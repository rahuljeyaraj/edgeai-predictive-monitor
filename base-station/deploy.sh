#!/usr/bin/env bash
#
# deploy.sh
#
# Pushes this App Lab app to the Arduino UNO Q over adb and (re)starts it.
# arduino-app-cli handles building/flashing the sketch to the MCU and
# starting the Python app together.
#
# Requires: board connected and visible via `adb devices` as "device".
#
# Usage:
#   ./deploy.sh

set -euo pipefail

APP_NAME="edgeai-predictive-monitor-base-station"
LOCAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REMOTE_DIR="/home/arduino/ArduinoApps/${APP_NAME}"

step() { echo; echo "==> $1"; }

step "Checking adb device connection"
ADB_STATE="$(adb get-state 2>/dev/null || true)"
if [ "${ADB_STATE}" != "device" ]; then
    echo "Board not visible to adb (got state: '${ADB_STATE:-none}'). Run 'adb devices' to check." >&2
    exit 1
fi

step "Stopping existing app (if running)"
adb shell "arduino-app-cli app stop ${REMOTE_DIR}" || true

step "Removing previous copy on device (preserving build/venv cache)"
# Only clear app source files, not ${REMOTE_DIR}/.cache — that's where the
# device keeps the Python venv (uv) and sketch build cache. Wiping it every
# deploy forces a full venv rebuild + package re-download and a full sketch
# recompile even when nothing changed, which is what made deploys slow.
adb shell "test -d '${REMOTE_DIR}' && find '${REMOTE_DIR}' -mindepth 1 -maxdepth 1 -not -name '.cache' -exec rm -rf {} + || true"

step "Pushing app to ${REMOTE_DIR}"
# Push each top-level item individually rather than pushing LOCAL_DIR itself.
# ${REMOTE_DIR} now persists across deploys (to preserve .cache above), and
# `adb push` nests the source under an existing destination dir like `cp -r`
# would — pushing LOCAL_DIR straight to an existing REMOTE_DIR would land
# everything one level too deep at ${REMOTE_DIR}/$(basename LOCAL_DIR)/...
adb shell "mkdir -p '${REMOTE_DIR}'"
shopt -s dotglob
for entry in "${LOCAL_DIR}"/*; do
    adb push "${entry}" "${REMOTE_DIR}"
done
shopt -u dotglob

step "Starting app"
adb shell "arduino-app-cli app start ${REMOTE_DIR}"

step "Streaming logs (Ctrl+C to stop watching; app keeps running)"
adb shell "arduino-app-cli app logs ${REMOTE_DIR}"
