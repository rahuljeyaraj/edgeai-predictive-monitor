#!/usr/bin/env bash
#
# start_sim_node.sh
#
# Runs python/tools/satellite_node_sim.py on THIS machine and connects it,
# over MQTT, to the real base station dashboard already deployed on the
# UNO Q (the one ./start_dashboard.sh brings up) -- not the desktop
# dashboard (see start_desktop_dashboard.sh for that, no device involved).
#
# The MQTT broker lives ON THE UNO Q ITSELF (the hub), not on this dev
# machine -- matching how a real satellite node has to work: it's a dumb
# sensor with nowhere else to publish to. This machine's sim just connects
# out to the device's broker over plain WiFi/Ethernet LAN, exactly like a
# real satellite would (see docs/Running_Dashboard_And_Satellite_Sim.md's
# old "Variant B" for the pre-App-Lab version of this same setup). USB/adb
# is used only for the occasional/one-time steps below -- never for live
# MQTT traffic -- so a momentary USB blip can no longer flip a sim node
# "offline" on the dashboard (the old adb-reverse-tunnel version of this
# script was vulnerable to exactly that, since that port-forward dies with
# the adb transport session).
#
# One-time on-device setup this script does NOT do for you (needs a sudo
# password typed on-device; adb can't supply one non-interactively):
#   adb shell
#   sudo apt-get update && sudo apt-get install -y mosquitto mosquitto-clients
#   echo -e 'listener 1883 0.0.0.0\nallow_anonymous true' | sudo tee /etc/mosquitto/conf.d/lan.conf
#   sudo systemctl enable --now mosquitto
#   sudo systemctl restart mosquitto
# The broker-reachability check below will fail with this exact guidance
# if it isn't done yet.
#
# main.py picks its own --mqtt-host default at every startup (reads its
# container's docker-bridge gateway IP from /proc/net/route -- see
# main.py's _default_mqtt_host()) instead of needing an external script to
# patch that default and restart the app whenever the gateway IP changes.
# Guessing wrong (no broker actually there yet) is harmless: paho's
# connect_async() just retries quietly in the background. This script:
#   1. Confirms the device's app container is already up (run
#      ./start_dashboard.sh first if not) and running code recent enough
#      to have this auto-configure logic (redeploy via ./start_dashboard.sh
#      if not).
#   2. Starts satellite_node_sim.py here, pointed at the device's own LAN
#      IP:1883, streaming from --captures-dir.
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
MQTT_HOST=""
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
        --mqtt-host) MQTT_HOST="$2"; shift 2 ;;
        --mqtt-port) MQTT_PORT="$2"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

if [ -z "${CAPTURES_DIR}" ]; then
    echo "Usage: $0 --captures-dir <dir> [--nodes N] [--ui-port-base PORT] [--auto-online] [--mqtt-host HOST] [--mqtt-port PORT]" >&2
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

echo
echo "============================================================"
echo " Device dashboard: http://${DEVICE_LAN_IP}:${DASHBOARD_PORT}/index.html"
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
