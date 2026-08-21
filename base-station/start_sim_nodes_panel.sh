#!/usr/bin/env bash
#
# start_sim_nodes_panel.sh
#
# Same as ./start_sim_node.sh (which it does NOT change -- run that instead
# if you want the old one-tab-per-node workflow) but adds ONE combined
# control page for all the sim nodes it starts, instead of N separate tabs:
# see python/tools/sim_node_panel.py. Built for demoing several sim nodes
# together -- status dot (the real status LED color/mode, same as each
# node's own page), capture file per node, and checkbox-select nodes to
# flip online/offline or change capture file for all of them in one go.
# Spectrum/scalar/bin-count config is NOT on the panel -- every node keeps
# the same accel-per-axis + mic + all-scalars config this script always
# pre-configures (open a node's own http://localhost:<port>/ page, printed
# below, if you need to change that for one node).
#
# Usage:
#   ./start_sim_nodes_panel.sh --captures-dir captures_3 [--nodes N] \
#       [--ui-port-base PORT] [--panel-port PORT] [--auto-online] \
#       [--mqtt-host HOST] [--mqtt-port PORT]

set -uo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_deploy_common.sh"

PY_DIR="${LOCAL_DIR}/python"
VENV="${PY_DIR}/.venv"
DASHBOARD_PORT=8080
MQTT_PORT=1883
MQTT_HOST=""
CAPTURES_DIR=""
NUM_NODES=10
UI_PORT_BASE=9101
PANEL_PORT=9100
AUTO_ONLINE=0

while [ $# -gt 0 ]; do
    case "$1" in
        --captures-dir) CAPTURES_DIR="$2"; shift 2 ;;
        --nodes) NUM_NODES="$2"; shift 2 ;;
        --ui-port-base) UI_PORT_BASE="$2"; shift 2 ;;
        --panel-port) PANEL_PORT="$2"; shift 2 ;;
        --auto-online) AUTO_ONLINE=1; shift ;;
        --mqtt-host) MQTT_HOST="$2"; shift 2 ;;
        --mqtt-port) MQTT_PORT="$2"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

if [ -z "${CAPTURES_DIR}" ]; then
    echo "Usage: $0 --captures-dir <dir> [--nodes N] [--ui-port-base PORT] [--panel-port PORT] [--auto-online] [--mqtt-host HOST] [--mqtt-port PORT]" >&2
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
if ss -tln 2>/dev/null | grep -q ":${PANEL_PORT} "; then
    echo "Panel port ${PANEL_PORT} is already in use. Pick a free --panel-port." >&2
    exit 1
fi

step "Setting up python/.venv (numpy + paho-mqtt for the sim)"
if [ ! -x "${VENV}/bin/python3" ]; then
    python3 -m venv "${VENV}"
fi
"${VENV}/bin/pip" install -q --upgrade pip
"${VENV}/bin/pip" install -q -r "${PY_DIR}/tools/requirements-desktop.txt"
echo "venv ready: ${VENV}"

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

step "Finding the device's own LAN IP (where this machine's sim will reach its broker)"
DEVICE_LAN_IP="$(find_lan_ip)"
if [ -z "${DEVICE_LAN_IP}" ]; then
    echo "Could not determine the device's LAN IP. Is it connected to WiFi/Ethernet? ('adb shell ip route get 1.1.1.1' to check)" >&2
    exit 1
fi
echo "Device LAN IP: ${DEVICE_LAN_IP}"

# The sims talk to the broker on the device by default. --mqtt-host exists for
# the case where this machine has no direct route to the device's LAN (e.g. WSL2
# behind NAT while the fleet sits on a Windows hotspot) and the traffic has to
# come in via a relay/portproxy address instead.
if [ -z "${MQTT_HOST}" ]; then
    MQTT_HOST="${DEVICE_LAN_IP}"
else
    echo "Using MQTT host override: ${MQTT_HOST}"
fi

step "Checking mosquitto broker is reachable at ${MQTT_HOST}:${MQTT_PORT}"
if ! "${VENV}/bin/python3" - "${MQTT_HOST}" "${MQTT_PORT}" <<'EOF'
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
    echo "No MQTT broker reachable at ${MQTT_HOST}:${MQTT_PORT}." >&2
    echo "First check which of the two causes this is:" >&2
    echo "  adb shell \"ss -tln | grep 1883\"   # broker listening on-device?" >&2
    echo "If it IS listening, the broker is fine and THIS machine just has no route" >&2
    echo "to ${MQTT_HOST} (e.g. WSL2 behind NAT, fleet on a Windows hotspot)." >&2
    echo "Fix the route, then re-run with --mqtt-host <relay-address>." >&2
    echo "If it is NOT listening, install mosquitto on the UNO Q (one-time, on-device):" >&2
    echo "  adb shell" >&2
    echo "  sudo apt-get update && sudo apt-get install -y mosquitto mosquitto-clients" >&2
    echo "  echo -e 'listener 1883 0.0.0.0\\nallow_anonymous true' | sudo tee /etc/mosquitto/conf.d/lan.conf" >&2
    echo "  sudo systemctl enable --now mosquitto && sudo systemctl restart mosquitto" >&2
    exit 1
fi
echo "Broker reachable."

step "Checking on-device app has MQTT auto-configure support"
has_autoconfig="$(adb_retry 15 shell "docker exec ${CONTAINER} grep -c _default_mqtt_host /app/python/main.py" 2>/dev/null | tr -d '\r\n')"
if [ "${has_autoconfig}" = "0" ] || [ -z "${has_autoconfig}" ]; then
    echo "On-device app predates MQTT auto-configure (main.py has no _default_mqtt_host)." >&2
    echo "Run ./start_dashboard.sh once to redeploy the latest code, then retry." >&2
    exit 1
fi
echo "Present -- main.py already self-points --mqtt-host at its own gateway IP on every boot, no patch/restart needed."

PIDS=()
CLEANED_UP=0
cleanup() {
    [ "${CLEANED_UP}" -eq 1 ] && return
    CLEANED_UP=1
    step "Stopping local sim node(s) + panel (device app keeps running)"
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
            --mqtt-host "${MQTT_HOST}" --mqtt-port "${MQTT_PORT}" \
            --captures-dir "${CAPTURES_DIR}" \
            --ui-host 127.0.0.1 --ui-port "${ui_port}"
    ) &
    PIDS+=("$!")
    SIM_UI_PORTS+=("${ui_port}")
done

step "Pre-configuring sim node(s) (first capture file found, per-axis accel, mic, all scalars)"
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
        -d "{\"file\": \"${first_file}\", \"accel\": true, \"accel_fused\": false, \"mic\": true, \"scalars\": [\"rms\", \"kurtosis\", \"crest_factor\", \"peak\", \"std\", \"skewness\"]}" \
        "http://127.0.0.1:${ui_port}/config" >/dev/null
    if [ "${AUTO_ONLINE}" -eq 1 ]; then
        curl -s -X POST --max-time 3 -H 'Content-Type: application/json' \
            -d '{"online": true}' "http://127.0.0.1:${ui_port}/online" >/dev/null
    fi
done

step "Starting combined control panel for ${#SIM_UI_PORTS[@]} node(s)"
(
    cd "${PY_DIR}/tools" && exec "${VENV}/bin/python3" sim_node_panel.py \
        --node-ports "${SIM_UI_PORTS[@]}" \
        --panel-host 0.0.0.0 --panel-port "${PANEL_PORT}"
) &
PIDS+=("$!")

echo
echo "============================================================"
echo " Device dashboard:  http://${DEVICE_LAN_IP}:${DASHBOARD_PORT}/index.html"
echo " Sim node panel:    http://127.0.0.1:${PANEL_PORT}/  (status, capture file, checkbox online/offline for all nodes)"
echo
echo " Individual node UIs (spectrum plots / scalars / bin counts, per node):"
for ui_port in "${SIM_UI_PORTS[@]}"; do
echo "   http://127.0.0.1:${ui_port}/"
done
echo
if [ "${AUTO_ONLINE}" -eq 1 ]; then
echo " Sim node(s) are online -- should show up on the device dashboard within a couple seconds."
else
echo " Sim node(s) are pre-configured but OFFLINE -- use the panel above (or rerun with"
echo " --auto-online) to bring them online before they'll appear on the dashboard."
fi
echo
echo " Press Ctrl+C to stop the local sim node(s) + panel. The device app keeps running either way."
echo "============================================================"

wait
