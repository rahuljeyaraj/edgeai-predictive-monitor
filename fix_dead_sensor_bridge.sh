#!/usr/bin/env bash
#
# fix_dead_sensor_bridge.sh
#
# Fixes the "base station asset never shows up in the dashboard" symptom
# caused by a desynced UART/RPC link to the board's STM32 co-processor.
#
# How to recognize this is the problem:
#   - GET /nodes on the dashboard returns {} (empty), even after a while.
#   - GET /perf shows ingest.frames_ok stuck at 0 and frames_dropped climbing.
#   - Container logs (docker logs <base-station container>) show:
#       "Bridge.read_loop] Bridge:  Unexpected error in read loop:
#        'utf-8' codec can't decode byte 0x.. in position .., invalid start byte"
#       "Invalid RPC message type received: None"
#
# Why a plain restart doesn't fix it:
#   `docker restart` only restarts the Linux-side Python process. The
#   desync lives on the STM32 co-processor side of the UART link, which a
#   container restart never touches. See docs/progress2.md and
#   docs/progress4.md for the original diagnosis.
#
# What this script does instead (in order -- order matters):
#   1. Restarts the arduino-router systemd service on the board, to get a
#      fresh serial file descriptor and a fresh msgpack decoder.
#   2. Stops and restarts the app via arduino-app-cli, which re-enumerates
#      USB and actually resets the STM32 side (~1-2 minutes).
#   3. Checks /perf afterwards to confirm frames_ok is moving again.
#
# Usage:
#   ./fix_dead_sensor_bridge.sh
#
# You will be prompted for the board's sudo password.

set -uo pipefail

DASH_PORT=8080
APP_PATH="/home/arduino/ArduinoApps/edgeai-predictive-monitor-base-station"

ok()   { printf '  \033[32mOK\033[0m    %s\n' "$1"; }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; }
step() { printf '\n\033[1m%s\033[0m\n' "$1"; }

step "0. Board reachable over adb"
ADB_STATE="$(adb get-state 2>/dev/null || true)"
if [ "${ADB_STATE}" != "device" ]; then
    bad "adb sees no board (state: '${ADB_STATE:-none}')"
    echo "Run 'adb devices' / re-attach via usbipd, then try again."
    exit 1
fi
ok "board attached"

read -r -s -p "Board sudo password: " SUDO_PW
echo

step "1. Restarting arduino-router (fresh serial fd + msgpack decoder)"
adb shell "echo '${SUDO_PW}' | sudo -S -p '' systemctl restart arduino-router"
ok "arduino-router restarted"

step "2. Restarting the app via arduino-app-cli (resets the STM32 side too)"
echo "  this rebuilds/reflashes -- takes ~1-2 minutes, be patient"
adb shell "arduino-app-cli app stop  ${APP_PATH}"
adb shell "arduino-app-cli app start ${APP_PATH}"
ok "app restarted"

step "3. Waiting for the dashboard to answer again"
for _ in $(seq 1 30); do
    curl -fs -m 3 "http://localhost:${DASH_PORT}/trip_outputs" >/dev/null 2>&1 && break
    sleep 2
done
if curl -fs -m 5 "http://localhost:${DASH_PORT}/trip_outputs" >/dev/null 2>&1; then
    ok "dashboard answering on localhost:${DASH_PORT}"
else
    bad "dashboard still not answering -- give it more time, or re-run ./after_reboot.sh"
    exit 1
fi

step "4. Checking the sensor link actually recovered"
sleep 5
PERF="$(curl -fs -m 5 "http://localhost:${DASH_PORT}/perf" 2>/dev/null)"
FRAMES_OK="$(echo "${PERF}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["ingest"]["frames_ok"])' 2>/dev/null)"
if [ -n "${FRAMES_OK}" ] && [ "${FRAMES_OK}" != "0" ]; then
    ok "frames_ok = ${FRAMES_OK} -- sensor link is alive"
    echo
    echo "  Give it a few more seconds, then check the dashboard --"
    echo "  base_station should now appear under /nodes."
else
    bad "frames_ok still 0 -- link did not recover"
    echo "  Try a genuine physical power cycle of the board next."
    echo "  (A bare 'reboot' does not reliably clear this either.)"
fi
