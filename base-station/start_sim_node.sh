#!/usr/bin/env bash
#
# start_sim_node.sh
#
# Runs python/tools/satellite_node_sim.py on THIS machine and connects it,
# over MQTT, to the real base station dashboard already deployed on the
# UNO Q (the one ./start_dashboard.sh brings up) -- not the desktop
# dashboard (see start_desktop_dashboard.sh for that, no device involved).
#
# The problem this works around: the deployed app's run.sh always launches
# main.py with zero args (`exec python "$PYTHON_SCRIPT"`), and app.yaml has
# no field to inject one -- so the on-device app never has --mqtt-host set
# on its own. This script:
#   1. Confirms the device's app container is already up (run
#      ./start_dashboard.sh first if not).
#   2. `adb reverse tcp:1883 tcp:1883` -- lets the *device's* connections to
#      127.0.0.1:1883 tunnel back over USB to this machine's own mosquitto
#      broker (must already be running here).
#   3. Finds the app container's docker-bridge gateway IP (reachable from
#      *inside* the container's network namespace, unlike the adb-reverse
#      target, which only listens in the device's host namespace) and, if
#      the on-device main.py isn't already defaulting --mqtt-host to it,
#      patches that default and `arduino-app-cli app restart`s the app so
#      it takes effect. NOT committed to git -- edits the on-device copy
#      only; a future ./start_dashboard.sh run overwrites it back to the
#      clean default (MQTT off) automatically. Skipped entirely (no
#      restart) if already pointed at the right gateway IP.
#   4. Starts satellite_node_sim.py here, streaming from --captures-dir.
#
# Usage:
#   ./start_sim_node.sh --captures-dir captures_3 [--nodes N] [--ui-port-base PORT] [--auto-online]
#
# Ctrl+C stops the local sim node(s) only -- the device app keeps running
# with MQTT ingestion enabled.

set -uo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_deploy_common.sh"

PY_DIR="${LOCAL_DIR}/python"
VENV="${PY_DIR}/.venv"
DASHBOARD_PORT=8080
MQTT_PORT=1883
CAPTURES_DIR=""
NUM_NODES=1
UI_PORT_BASE=9101
AUTO_ONLINE=0

while [ $# -gt 0 ]; do
    case "$1" in
        --captures-dir) CAPTURES_DIR="$2"; shift 2 ;;
        --nodes) NUM_NODES="$2"; shift 2 ;;
        --ui-port-base) UI_PORT_BASE="$2"; shift 2 ;;
        --auto-online) AUTO_ONLINE=1; shift ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

if [ -z "${CAPTURES_DIR}" ]; then
    echo "Usage: $0 --captures-dir <dir> [--nodes N] [--ui-port-base PORT] [--auto-online]" >&2
    exit 1
fi
case "${CAPTURES_DIR}" in
    /*) : ;;
    *) CAPTURES_DIR="$(pwd)/${CAPTURES_DIR}" ;;
esac
if [ ! -d "${CAPTURES_DIR}" ]; then
    echo "--captures-dir ${CAPTURES_DIR} is not a directory" >&2
    exit 1
fi
if ! find "${CAPTURES_DIR}" -iname '*.npz' -print -quit | grep -q .; then
    echo "No .npz files found under ${CAPTURES_DIR} -- satellite_node_sim.py needs at least one." >&2
    exit 1
fi

for i in $(seq 0 $((NUM_NODES - 1))); do
    port=$((UI_PORT_BASE + i))
    if ss -tln 2>/dev/null | grep -q ":${port} "; then
        echo "Port ${port} is already in use (maybe another sim node already running?). Pick a free --ui-port-base." >&2
        exit 1
    fi
done

step "Setting up python/.venv (numpy + paho-mqtt for the sim)"
if [ ! -x "${VENV}/bin/python3" ]; then
    python3 -m venv "${VENV}"
fi
"${VENV}/bin/pip" install -q --upgrade pip
"${VENV}/bin/pip" install -q -r "${PY_DIR}/tools/requirements-desktop.txt"
echo "venv ready: ${VENV}"

step "Checking local mosquitto broker at 127.0.0.1:${MQTT_PORT}"
if ! "${VENV}/bin/python3" - "127.0.0.1" "${MQTT_PORT}" <<'EOF'
import socket, sys
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
    echo "No MQTT broker reachable at 127.0.0.1:${MQTT_PORT}." >&2
    echo "Install/start one, e.g.: sudo apt-get install -y mosquitto && sudo systemctl start mosquitto" >&2
    exit 1
fi
echo "Broker reachable."

step "Checking adb device connection"
if ! wait_for_device 15; then
    echo "Board not visible to adb after waiting. Run 'adb devices' to check." >&2
    exit 1
fi

step "Checking app container is up"
running="$(adb_retry 15 shell "docker inspect -f '{{.State.Running}}' ${CONTAINER}" 2>/dev/null | tr -d '\r\n')"
if [ "${running}" != "true" ]; then
    echo "App container ${CONTAINER} isn't running. Run ./start_dashboard.sh first." >&2
    exit 1
fi

step "Setting up adb reverse tcp:${MQTT_PORT} (device host namespace -> this machine's broker)"
if adb reverse --list 2>/dev/null | grep -q "tcp:${MQTT_PORT} tcp:${MQTT_PORT}"; then
    echo "Already set up."
else
    adb_retry 15 reverse "tcp:${MQTT_PORT}" "tcp:${MQTT_PORT}"
    echo "Set up."
fi

step "Finding app container's docker-bridge gateway IP"
route_hex="$(adb_retry 15 shell "docker exec ${CONTAINER} awk '\$2==\"00000000\" {print \$3}' /proc/net/route" 2>/dev/null | tr -d '\r\n')"
if [ -z "${route_hex}" ] || [ "${#route_hex}" -ne 8 ]; then
    echo "Could not read the container's default route from /proc/net/route." >&2
    exit 1
fi
GATEWAY_IP="$(printf '%d.%d.%d.%d' \
    "0x${route_hex:6:2}" "0x${route_hex:4:2}" "0x${route_hex:2:2}" "0x${route_hex:0:2}")"
echo "Gateway IP (container -> device host, where the adb-reverse tunnel listens): ${GATEWAY_IP}"

step "Checking on-device main.py's --mqtt-host default"
current_default="$(adb_retry 15 shell "docker exec ${CONTAINER} grep -oP '(?<=--mqtt-host\", default=)[^,]*' /app/python/main.py" 2>/dev/null | tr -d '\r\n')"
desired_default="\"${GATEWAY_IP}\""
if [ "${current_default}" = "${desired_default}" ]; then
    echo "Already pointed at ${GATEWAY_IP} -- no patch/restart needed."
else
    step "Patching --mqtt-host default to ${GATEWAY_IP} (on-device copy only, not committed to git)"
    adb_retry 15 shell "docker exec ${CONTAINER} sed -i 's/\"--mqtt-host\", default=[^,]*,/\"--mqtt-host\", default=${desired_default},/' /app/python/main.py"

    step "Restarting the app so the new default takes effect"
    adb_retry 60 shell "arduino-app-cli app restart ${REMOTE_DIR}"

    step "Waiting for the app container to come back up"
    up=0
    for i in $(seq 1 60); do
        running="$(adb_retry 15 shell "docker inspect -f '{{.State.Running}}' ${CONTAINER}" 2>/dev/null | tr -d '\r\n')"
        if [ "${running}" = "true" ]; then
            up=1
            break
        fi
        sleep 2
    done
    if [ "${up}" -ne 1 ]; then
        echo "Container never came back up after restart. Check 'adb shell docker logs ${CONTAINER}'." >&2
        exit 1
    fi

    step "Waiting for dashboard HTTP to respond again"
    LAN_IP="$(find_lan_ip)"
    VERIFY_BASE="http://${LAN_IP:-localhost}:${DASHBOARD_PORT}"
    dashboard_up=0
    for i in $(seq 1 30); do
        code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "${VERIFY_BASE}/index.html" 2>/dev/null || true)"
        if [ "${code}" = "200" ]; then
            dashboard_up=1
            break
        fi
        sleep 2
    done
    [ "${dashboard_up}" -eq 1 ] && echo "Dashboard responding (HTTP 200)." || echo "Dashboard hasn't responded yet -- give it a few more seconds." >&2
fi

PIDS=()
CLEANED_UP=0
cleanup() {
    [ "${CLEANED_UP}" -eq 1 ] && return
    CLEANED_UP=1
    step "Stopping local sim node(s) (device app keeps running)"
    for pid in "${PIDS[@]:-}"; do
        kill "${pid}" 2>/dev/null || true
    done
    wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

step "Starting ${NUM_NODES} sim node(s) against ${CAPTURES_DIR}"
SIM_UI_PORTS=()
for i in $(seq 0 $((NUM_NODES - 1))); do
    ui_port=$((UI_PORT_BASE + i))
    (
        cd "${PY_DIR}/tools" && exec "${VENV}/bin/python3" satellite_node_sim.py \
            --mqtt-host 127.0.0.1 --mqtt-port "${MQTT_PORT}" \
            --captures-dir "${CAPTURES_DIR}" \
            --ui-host 127.0.0.1 --ui-port "${ui_port}"
    ) &
    PIDS+=("$!")
    SIM_UI_PORTS+=("${ui_port}")
done

step "Pre-configuring sim node(s) (first capture file found, fused+per-axis accel, mic, all scalars)"
for ui_port in "${SIM_UI_PORTS[@]}"; do
    up=0
    state=""
    for i in $(seq 1 20); do
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
print(files[0] if files else "")
')"
    if [ -z "${first_file}" ]; then
        echo "No .npz capture files found under ${CAPTURES_DIR} -- skipping auto-configure for port ${ui_port}." >&2
        continue
    fi
    curl -s -X POST --max-time 3 -H 'Content-Type: application/json' \
        -d "{\"file\": \"${first_file}\", \"accel\": true, \"accel_fused\": true, \"mic\": true, \"scalars\": [\"rms\", \"kurtosis\", \"crest_factor\", \"peak\", \"std\", \"skewness\"]}" \
        "http://127.0.0.1:${ui_port}/config" >/dev/null
    if [ "${AUTO_ONLINE}" -eq 1 ]; then
        curl -s -X POST --max-time 3 -H 'Content-Type: application/json' \
            -d '{"online": true}' "http://127.0.0.1:${ui_port}/online" >/dev/null
    fi
done

LAN_IP="$(find_lan_ip)"

echo
echo "============================================================"
if [ -n "${LAN_IP}" ]; then
echo " Device dashboard: http://${LAN_IP}:${DASHBOARD_PORT}/index.html"
else
echo " Device has no network route right now -- reconnect it to WiFi/Ethernet."
fi
for ui_port in "${SIM_UI_PORTS[@]}"; do
echo " Sim node UI: http://127.0.0.1:${ui_port}/  (toggle online/capture file/channels here)"
done
echo
if [ "${AUTO_ONLINE}" -eq 1 ]; then
echo " Sim node(s) are online -- should show up on the device dashboard within a couple seconds."
else
echo " Sim node(s) are pre-configured but OFFLINE -- open a Sim node UI above and"
echo " click \"Go Online\" (or rerun with --auto-online) before they'll appear on the dashboard."
fi
echo
echo " Press Ctrl+C to stop the local sim node(s). The device app keeps running either way."
echo "============================================================"

wait
