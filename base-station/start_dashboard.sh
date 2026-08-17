#!/usr/bin/env bash
#
# start_dashboard.sh
#
# One-shot setup to get the normal live-model dashboard running on a fresh
# build (docs/RAW_CAPTURE_WORKFLOW.md step 6 + the main workflow), automated
# end to end:
#   1. Force FUSER_RAW_CAPTURE_MODE to 0, build + flash + push (see
#      _deploy_common.sh for the flaky-USB-link resilience details).
#   2. Wait for the app container to come up -- main.py (port 8080) is the
#      container's own entrypoint, so no separate process to launch here,
#      unlike raw-capture mode's raw_capture_server.py.
#   3. Print the board's own LAN-IP URL -- not a localhost/adb-forward link.
#      The real deployment has no adb port-forwarding available at all, and
#      even here in dev, WSL2's localhost-forwarding isn't reliably reaching
#      the Windows browser -- the IP-address link is the one that actually
#      works in both cases. adb forward is still used internally as a
#      verification fallback for when the board itself has no network route
#      (dev-only situation), never as the link shown to you.
#   4. If the base_station node was left over from a prior raw-capture
#      session (registered as a 0-channel sensor while that mode was
#      active), decommission it via the dashboard's own API so the UI
#      doesn't error out once real spectral data starts arriving again --
#      this is the "click the trash/Remove icon" step from
#      docs/RAW_CAPTURE_WORKFLOW.md step 6, done here via curl instead of a
#      browser click.
#
# Usage:
#   ./start_dashboard.sh             -- full push + build/flash + start
#   ./start_dashboard.sh --existing  -- skip push/build, just (re)start the
#                                        app code already on the device

set -uo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_deploy_common.sh"

PORT=8080
BASE_STATION_NODE_ID="base_station"
USE_EXISTING=0

for arg in "$@"; do
    case "${arg}" in
        --existing|-e)
            USE_EXISTING=1
            ;;
        *)
            echo "Unknown argument: ${arg}" >&2
            echo "Usage: $0 [--existing]" >&2
            exit 1
            ;;
    esac
done

require_port "${PORT}"
if [ "${USE_EXISTING}" -eq 1 ]; then
    start_existing_app
else
    set_raw_capture_mode 0
    deploy_app
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

step "Waiting for dashboard HTTP to respond"
dashboard_up=0
for i in $(seq 1 30); do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "${VERIFY_BASE}/index.html" 2>/dev/null || true)"
    if [ "${code}" = "200" ]; then
        dashboard_up=1
        break
    fi
    sleep 2
done
if [ "${dashboard_up}" -ne 1 ]; then
    echo "Dashboard never responded. Check 'adb shell docker logs ${CONTAINER}'." >&2
    exit 1
fi
echo "Dashboard responding (HTTP 200)."

step "Clearing any stale base_station node left over from raw-capture mode"
# A raw-capture-mode leftover registers with sensor_config == [] (its first
# frame carries no spectral channel bins at all -- manager.py's
# _infer_sensor_config_and_dim commits sensor_config from whatever the very
# first frame actually had, and never changes it after). A real normal-mode
# base_station always has 4 (accel_x/y/z + mic). Only clear the former --
# decommissioning deletes the node's registry entry (calibration/thresholds)
# AND its entire history.db row set, so blindly clearing whatever's there
# unconditionally, as this used to, wipes a real commissioned node's history
# with no way to get it back.
node_json="$(curl -s --max-time 5 "${VERIFY_BASE}/nodes/${BASE_STATION_NODE_ID}" 2>/dev/null || true)"
if echo "${node_json}" | grep -q '"node_id"'; then
    channel_count="$(echo "${node_json}" | python3 -c 'import json,sys
print(len(json.load(sys.stdin).get("sensor_config") or []))' 2>/dev/null || echo -1)"
    if [ "${channel_count}" = "0" ]; then
        curl -s -X POST --max-time 5 "${VERIFY_BASE}/nodes/${BASE_STATION_NODE_ID}/decommission" >/dev/null
        echo "Cleared a stale 0-channel ${BASE_STATION_NODE_ID} entry (raw-capture-mode leftover) -- it'll re-register fresh from the next real frame."
    else
        echo "Existing ${BASE_STATION_NODE_ID} entry has ${channel_count} sensor channel(s) -- that's a real commissioned node, leaving it alone."
    fi
else
    echo "No existing ${BASE_STATION_NODE_ID} entry -- nothing to clear."
fi

step "Verifying live sensor frames are actually arriving (waits up to 20s)"
frames_ok=0
for i in $(seq 1 10); do
    resp="$(curl -s --max-time 5 "${VERIFY_BASE}/nodes/${BASE_STATION_NODE_ID}" 2>/dev/null || true)"
    if echo "${resp}" | grep -q '"node_id"'; then
        frames_ok=1
        break
    fi
    sleep 2
done
if [ "${frames_ok}" -eq 1 ]; then
    echo "base_station node is reporting to the registry -- live data flowing."
else
    echo "base_station hasn't reported yet -- give it a few more seconds in the browser; this only means the very first frame is still in flight, not necessarily a problem."
fi

echo
echo "============================================================"
if [ -n "${LAN_IP}" ]; then
echo " Dashboard is up, running the normal live model."
echo
echo " Open in a browser:"
echo "   http://${LAN_IP}:${PORT}/index.html"
else
echo " Dashboard is up and verified working (checked via adb forward),"
echo " but the board has NO network route right now, so there is no"
echo " reachable URL to give you. Connect the board to WiFi/Ethernet,"
echo " then re-run this script to get its LAN-IP link."
fi
echo "============================================================"
