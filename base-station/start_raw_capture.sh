#!/usr/bin/env bash
#
# start_raw_capture.sh
#
# One-shot setup for a raw-capture session (docs/RAW_CAPTURE_WORKFLOW.md,
# steps 0-4 Option A), automated end to end:
#   1. Force FUSER_RAW_CAPTURE_MODE to 1, build + flash + push (see
#      _deploy_common.sh for the flaky-USB-link resilience details).
#   2. Wait for the app container to come up, find its venv python.
#   3. Launch raw_capture_server.py --port 8081 inside it.
#   4. Print the board's own LAN-IP URL -- not a localhost/adb-forward link.
#      The real deployment has no adb port-forwarding available at all, and
#      even here in dev, WSL2's localhost-forwarding isn't reliably reaching
#      the Windows browser -- the IP-address link is the one that actually
#      works in both cases. adb forward is still used internally as a
#      verification fallback for when the board itself has no network route
#      (dev-only situation), never as the link shown to you.
#
# Usage:
#   ./start_raw_capture.sh

set -uo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_deploy_common.sh"

PORT=8081

set_raw_capture_mode 1
require_port "${PORT}"
deploy_app

step "Finding app's venv python interpreter"
VENV_PY="$(find_venv_python)"
echo "Using ${VENV_PY}"

step "Starting raw_capture_server.py on port ${PORT}"
if adb_retry 15 shell "docker exec ${CONTAINER} pgrep -af raw_capture_server" 2>/dev/null | grep -q raw_capture_server; then
    echo "Already running -- leaving it as-is."
else
    adb_retry 15 shell "docker exec -d ${CONTAINER} ${VENV_PY} /app/python/tools/raw_capture_server.py --port ${PORT}"
    sleep 2
    if ! adb_retry 15 shell "docker exec ${CONTAINER} pgrep -af raw_capture_server" 2>/dev/null | grep -q raw_capture_server; then
        echo "raw_capture_server.py did not start -- check 'adb shell docker logs ${CONTAINER}'." >&2
        exit 1
    fi
fi

step "Finding board's LAN IP"
LAN_IP="$(find_lan_ip)"
if [ -n "${LAN_IP}" ]; then
    echo "Board's LAN IP: ${LAN_IP}"
    VERIFY_BASE="http://${LAN_IP}:${PORT}"
else
    echo "Board has no network route right now (WiFi/Ethernet not connected)." >&2
    echo "Falling back to adb forward for verification only -- the final URL below will still be IP-based and won't work until the board joins a network." >&2
    adb_retry 15 forward "tcp:${PORT}" "tcp:${PORT}"
    VERIFY_BASE="http://localhost:${PORT}"
fi

step "Verifying raw_capture.html is reachable"
page_up=0
for i in $(seq 1 15); do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "${VERIFY_BASE}/raw_capture.html" 2>/dev/null || true)"
    if [ "${code}" = "200" ]; then
        page_up=1
        break
    fi
    sleep 2
done
if [ "${page_up}" -ne 1 ]; then
    echo "raw_capture.html never responded. Check 'adb shell docker exec ${CONTAINER} pgrep -af raw_capture_server'." >&2
    exit 1
fi
echo "raw_capture.html responding (HTTP 200)."

echo
echo "============================================================"
if [ -n "${LAN_IP}" ]; then
echo " Raw capture server is up."
echo
echo " Open in a browser:"
echo "   http://${LAN_IP}:${PORT}/raw_capture.html"
echo
echo " When done: type a label, hit Start, hold the rig state, hit Stop."
echo " Then run ./pull_captures.sh to pull the .npz files to this laptop."
else
echo " Raw capture server is up and verified working (checked via adb"
echo " forward), but the board has NO network route right now, so there"
echo " is no reachable URL to give you. Connect the board to WiFi/"
echo " Ethernet, then re-run this script to get its LAN-IP link."
fi
echo "============================================================"
