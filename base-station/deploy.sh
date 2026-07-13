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

step "Removing previous copy on device"
adb shell "rm -rf ${REMOTE_DIR}"

step "Pushing app to ${REMOTE_DIR}"
adb push "${LOCAL_DIR}" "${REMOTE_DIR}"

step "Starting app"
adb shell "arduino-app-cli app start ${REMOTE_DIR}"

step "Streaming logs (Ctrl+C to stop watching; app keeps running)"
adb shell "arduino-app-cli app logs ${REMOTE_DIR}"
