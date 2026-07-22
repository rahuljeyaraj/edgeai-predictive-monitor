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
#   3. Generates synthetic vibration CSVs for the sim node to stream, if
#      not already present (python/tools/gen_synthetic_vibration_csv.py).
#   4. Starts main.py (--mqtt-host localhost, isolated --data-dir so this
#      never touches a real device's registry/history).
#   5. Starts one satellite_node_sim.py copy, waits for its HTTP control
#      API to come up, then flips it online with accel+mic enabled so a
#      node is already live the moment the dashboard opens.
#   6. Prints both URLs. Ctrl+C stops both processes.
#
# Usage:
#   ./start_desktop_dashboard.sh [--nodes N]

set -uo pipefail

LOCAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY_DIR="${LOCAL_DIR}/python"
VENV="${PY_DIR}/.venv"
DATA_DIR="${LOCAL_DIR}/.cache/data-desktop"
SIM_DATA_DIR="${LOCAL_DIR}/.cache/sim-data"
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

step "Preparing synthetic vibration data for the sim node(s)"
mkdir -p "${SIM_DATA_DIR}"
"${VENV}/bin/python3" "${PY_DIR}/tools/gen_synthetic_vibration_csv.py" --out-dir "${SIM_DATA_DIR}"

step "Starting dashboard (python/main.py) on port ${DASHBOARD_PORT}"
mkdir -p "${DATA_DIR}"
(
    cd "${PY_DIR}" && exec "${VENV}/bin/python3" main.py \
        --host 127.0.0.1 --port "${DASHBOARD_PORT}" \
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
            --data-dir "${SIM_DATA_DIR}" \
            --ui-host 127.0.0.1 --ui-port "${ui_port}" \
            --state-file "${state_file}"
    ) &
    PIDS+=("$!")
    SIM_UI_PORTS+=("${ui_port}")
done

step "Bringing sim node(s) online (accel channel -> healthy.csv)"
for ui_port in "${SIM_UI_PORTS[@]}"; do
    up=0
    for _ in $(seq 1 20); do
        if curl -s --max-time 2 "http://127.0.0.1:${ui_port}/state" >/dev/null 2>&1; then
            up=1
            break
        fi
        sleep 0.5
    done
    if [ "${up}" -ne 1 ]; then
        echo "Sim node on port ${ui_port} never came up -- skipping auto-enable." >&2
        continue
    fi
    curl -s -X POST --max-time 3 -H 'Content-Type: application/json' \
        -d '{"online": true}' "http://127.0.0.1:${ui_port}/online" >/dev/null
    curl -s -X POST --max-time 3 -H 'Content-Type: application/json' \
        -d '{"channel": "accel", "enabled": true, "file": "healthy.csv"}' \
        "http://127.0.0.1:${ui_port}/channel" >/dev/null
done

echo
echo "============================================================"
echo " Dashboard:  http://127.0.0.1:${DASHBOARD_PORT}/index.html"
for ui_port in "${SIM_UI_PORTS[@]}"; do
echo " Sim node UI: http://127.0.0.1:${ui_port}/  (toggle online/channel/fault file here)"
done
echo
echo " base_station node will NOT appear (no MCU on this machine) --"
echo " that's expected. Sim node(s) above should show up in the fleet"
echo " view within a couple seconds."
echo
echo " Press Ctrl+C to stop everything."
echo "============================================================"

wait "${DASHBOARD_PID}"
