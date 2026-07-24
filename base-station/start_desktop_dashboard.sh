#!/usr/bin/env bash
#
# start_desktop_dashboard.sh
#
# Runs the dashboard (python/main.py) directly on THIS machine -- no MCU,
# no App Lab container, no adb/device at all -- for exercising the
# registry/pipeline/dashboard UI against simulated satellite nodes
# (python/tools/satellite_node_sim.py) instead of real hardware.
#
# The base_station node itself will never appear (its data comes from the
# SPI-connected fuser MCU, which doesn't exist on this machine) -- that's
# expected. Only MQTT-driven satellite nodes show up.
#
# What this does:
#   1. Creates/reuses python/.venv, installs python/requirements.txt +
#      python/tools/requirements-desktop.txt into it (fastapi/uvicorn/
#      websockets for the dashboard, numpy/paho-mqtt for the sim).
#   2. Checks a MQTT broker is reachable on localhost:1883 (does not start
#      one -- see the error message below if none is running).
#   3. Streams real capture .npz files for the sim node from
#      base-station/captures/ by default. Pass --captures-dir DIR to replay a
#      different folder, or --captures-dir "" to fall back to generated
#      synthetic captures (python/tools/gen_synthetic_captures.py).
#   4. Starts main.py (--mqtt-host localhost, isolated --data-dir so this
#      never touches a real device's registry/history).
#   5. Starts one satellite_node_sim.py copy, waits for its HTTP control API
#      to come up, then pre-configures it (a capture file picked, everything
#      enabled -- fused+per-axis accel, mic, all 6 scalars) but leaves it
#      OFFLINE: open its UI and click "Go Online" once you've reviewed the
#      config, or pass --auto-online to have this script flip it online too.
#   6. Prints both URLs. Ctrl+C stops both processes.
#
# Usage:
#   ./start_desktop_dashboard.sh [--nodes N] [--captures-dir DIR] [--auto-online] [--host HOST]
#
#   --host 0.0.0.0   binds on every interface instead of just localhost, so
#                     the dashboard is reachable from another device (e.g. a
#                     phone, to check mobile view) on the same LAN/Wi-Fi --
#                     the script prints the LAN URL to open once it's up.
#                     Default (127.0.0.1) stays loopback-only/safe.

set -uo pipefail

LOCAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY_DIR="${LOCAL_DIR}/python"
VENV="${PY_DIR}/.venv"
DATA_DIR="${LOCAL_DIR}/.cache/data-desktop"
SIM_DATA_DIR="${LOCAL_DIR}/.cache/sim-data"
CAPTURES_DIR="${LOCAL_DIR}/captures"  # default: real captures; pass --captures-dir "" for synthetic SIM_DATA_DIR
AUTO_ONLINE=0    # 0 = leave the sim node offline for you to review/click "Go Online"; --auto-online flips it for you
DASHBOARD_HOST=127.0.0.1  # --host 0.0.0.0 to expose on the LAN (e.g. for checking mobile view)
# Deliberately NOT 8080 -- that's app.yaml's real on-device dashboard port,
# routinely reachable here too via `adb forward tcp:8080 tcp:8080` during
# device testing. Using a different port means this script can never bind
# on top of (or be confused with) a live device session.
DASHBOARD_PORT=8180
MQTT_HOST=localhost
MQTT_PORT=1883
NUM_NODES=1

step() { echo; echo "==> $1"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --nodes) NUM_NODES="$2"; shift 2 ;;
        --port) DASHBOARD_PORT="$2"; shift 2 ;;
        --captures-dir) CAPTURES_DIR="$2"; shift 2 ;;
        --auto-online) AUTO_ONLINE=1; shift ;;
        --host) DASHBOARD_HOST="$2"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

if ss -tln 2>/dev/null | grep -q ":${DASHBOARD_PORT} "; then
    echo "Port ${DASHBOARD_PORT} is already in use (maybe an adb forward to a real device?). Pick another with --port." >&2
    exit 1
fi

PIDS=()
CLEANED_UP=0
cleanup() {
    [ "${CLEANED_UP}" -eq 1 ] && return
    CLEANED_UP=1
    step "Stopping dashboard + sim node(s)"
    for pid in "${PIDS[@]:-}"; do
        kill "${pid}" 2>/dev/null || true
    done
    wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

step "Setting up python/.venv"
if [ ! -x "${VENV}/bin/python3" ]; then
    python3 -m venv "${VENV}"
fi
"${VENV}/bin/pip" install -q --upgrade pip
"${VENV}/bin/pip" install -q \
    -r "${PY_DIR}/requirements.txt" \
    -r "${PY_DIR}/tools/requirements-desktop.txt"
echo "venv ready: ${VENV}"

step "Checking MQTT broker at ${MQTT_HOST}:${MQTT_PORT}"
if ! "${VENV}/bin/python3" - "${MQTT_HOST}" "${MQTT_PORT}" <<'EOF'
import socket
import sys
host, port = sys.argv[1], int(sys.argv[2])
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(3)
try:
    s.connect((host, port))
except OSError:
    sys.exit(1)
finally:
    s.close()
EOF
then
    echo "No MQTT broker reachable at ${MQTT_HOST}:${MQTT_PORT}." >&2
    echo "Install/start one, e.g.:" >&2
    echo "  sudo apt-get install -y mosquitto && sudo systemctl start mosquitto" >&2
    exit 1
fi
echo "Broker reachable."

if [ -z "${CAPTURES_DIR}" ]; then
    step "Preparing synthetic capture data for the sim node(s)"
    mkdir -p "${SIM_DATA_DIR}"
    "${VENV}/bin/python3" "${PY_DIR}/tools/gen_synthetic_captures.py" --out-dir "${SIM_DATA_DIR}"
    CAPTURES_DIR="${SIM_DATA_DIR}"
else
    step "Using captures dir ${CAPTURES_DIR}"
    if [ ! -d "${CAPTURES_DIR}" ]; then
        echo "--captures-dir ${CAPTURES_DIR} is not a directory" >&2
        exit 1
    fi
fi

step "Starting dashboard (python/main.py) on ${DASHBOARD_HOST}:${DASHBOARD_PORT}"
mkdir -p "${DATA_DIR}"
(
    cd "${PY_DIR}" && exec "${VENV}/bin/python3" main.py \
        --host "${DASHBOARD_HOST}" --port "${DASHBOARD_PORT}" \
        --data-dir "${DATA_DIR}" \
        --mqtt-host "${MQTT_HOST}" --mqtt-port "${MQTT_PORT}"
) &
DASHBOARD_PID=$!
PIDS+=("${DASHBOARD_PID}")

step "Waiting for dashboard HTTP to respond"
dashboard_up=0
for _ in $(seq 1 30); do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "http://127.0.0.1:${DASHBOARD_PORT}/index.html" 2>/dev/null || true)"
    if [ "${code}" = "200" ]; then
        dashboard_up=1
        break
    fi
    sleep 1
done
if [ "${dashboard_up}" -ne 1 ]; then
    echo "Dashboard never responded on port ${DASHBOARD_PORT}. It may still be starting -- check the output above for a traceback." >&2
    exit 1
fi
echo "Dashboard responding (HTTP 200)."

step "Starting ${NUM_NODES} simulated satellite node(s)"
SIM_UI_PORTS=()
for i in $(seq 1 "${NUM_NODES}"); do
    ui_port=$((9100 + i))
    state_file="${SIM_DATA_DIR}/.node_${ui_port}.json"
    (
        cd "${PY_DIR}/tools" && exec "${VENV}/bin/python3" satellite_node_sim.py \
            --mqtt-host "${MQTT_HOST}" --mqtt-port "${MQTT_PORT}" \
            --captures-dir "${CAPTURES_DIR}" \
            --ui-host 127.0.0.1 --ui-port "${ui_port}" \
            --state-file "${state_file}"
    ) &
    PIDS+=("$!")
    SIM_UI_PORTS+=("${ui_port}")
done

step "Pre-configuring sim node(s) (per-axis accel, mic, all scalars -- left OFFLINE for you to review)"
for ui_port in "${SIM_UI_PORTS[@]}"; do
    up=0
    state=""
    for _ in $(seq 1 20); do
        state="$(curl -s --max-time 2 "http://127.0.0.1:${ui_port}/state" 2>/dev/null || true)"
        if [ -n "${state}" ]; then
            up=1
            break
        fi
        sleep 0.5
    done
    if [ "${up}" -ne 1 ]; then
        echo "Sim node on port ${ui_port} never came up -- skipping auto-configure." >&2
        continue
    fi
    first_file="$(echo "${state}" | "${VENV}/bin/python3" -c '
import json, sys
files = json.load(sys.stdin).get("files") or []
print(next((f for f in files if f == "healthy.npz"), files[0] if files else ""))
')"
    if [ -z "${first_file}" ]; then
        echo "No .npz capture files found under ${CAPTURES_DIR} -- skipping auto-configure for port ${ui_port}." >&2
        continue
    fi
    curl -s -X POST --max-time 3 -H 'Content-Type: application/json' \
        -d "{\"file\": \"${first_file}\", \"accel\": true, \"accel_fused\": false, \"mic\": true, \"scalars\": [\"rms\", \"kurtosis\", \"crest_factor\", \"peak\", \"std\", \"skewness\"]}" \
        "http://127.0.0.1:${ui_port}/config" >/dev/null
    if [ "${AUTO_ONLINE}" -eq 1 ]; then
        curl -s -X POST --max-time 3 -H 'Content-Type: application/json' \
            -d '{"online": true}' "http://127.0.0.1:${ui_port}/online" >/dev/null
    fi
done

echo
echo "============================================================"
echo " Dashboard:  http://127.0.0.1:${DASHBOARD_PORT}/index.html"
if [ "${DASHBOARD_HOST}" = "0.0.0.0" ]; then
    lan_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
    if [ -n "${lan_ip}" ]; then
        echo " From your phone (same Wi-Fi): http://${lan_ip}:${DASHBOARD_PORT}/index.html"
    else
        echo " Bound on 0.0.0.0 but couldn't detect a LAN IP -- run \`hostname -I\` or \`ip addr\` to find yours."
    fi
fi
for ui_port in "${SIM_UI_PORTS[@]}"; do
echo " Sim node UI: http://127.0.0.1:${ui_port}/  (toggle online/capture file/spectrum+scalar channels here)"
done
echo
if [ "${AUTO_ONLINE}" -eq 1 ]; then
echo " Sim node(s) are online -- should show up in the fleet view within a couple seconds."
else
echo " Sim node(s) are pre-configured but OFFLINE -- open a Sim node UI above and"
echo " click \"Go Online\" (or rerun with --auto-online) before they'll appear on the dashboard."
fi
echo
echo " base_station node will NOT appear (no MCU on this machine) -- that's expected."
echo
echo " Press Ctrl+C to stop everything."
echo "============================================================"

wait "${DASHBOARD_PID}"
