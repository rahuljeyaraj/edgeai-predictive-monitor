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

step "Waiting for adb to settle (app stop briefly re-enumerates the board's USB)"
for _ in $(seq 1 10); do
    [ "$(adb get-state 2>/dev/null || true)" = "device" ] && break
    sleep 1
done
if [ "$(adb get-state 2>/dev/null || true)" != "device" ]; then
    echo "Board didn't come back after app stop. Check 'adb devices' and re-run ./deploy.sh." >&2
    exit 1
fi

step "Protecting the device's app.yaml (holds the Telegram brick token)"
# App Lab writes brick secrets INLINE into the device's app.yaml, and there is
# no separate secret store. Shipping the repo's copy over it deletes the token,
# and the next `app start` hard-fails on the brick check before main.py runs --
# which leaves the MCU sketch un-reflashed and makes the board's own
# base_station node disappear from the dashboard. So the device's copy is
# authoritative; the repo's is only a seed for a fresh board.
if adb shell "test -f '${REMOTE_DIR}/app.yaml'" 2>/dev/null; then
    echo "Keeping the device's copy (the repo's app.yaml will NOT be shipped)."
else
    echo "Device has no app.yaml yet -- seeding it from the repo's copy."
    adb shell "mkdir -p '${REMOTE_DIR}'"
    adb push "${LOCAL_DIR}/app.yaml" "${REMOTE_DIR}/app.yaml"
    echo "NOTE: if app.yaml declares a brick, set its secret in App Lab's GUI"
    echo "      before the build, or it will fail on the brick check."
fi

step "Removing previous copy on device (preserving build/venv cache and app.yaml)"
# Only clear app source files, not ${REMOTE_DIR}/.cache — that's where the
# device keeps the Python venv (uv) and sketch build cache. Wiping it every
# deploy forces a full venv rebuild + package re-download and a full sketch
# recompile even when nothing changed, which is what made deploys slow.
# app.yaml is spared for the reason above.
adb shell "test -d '${REMOTE_DIR}' && find '${REMOTE_DIR}' -mindepth 1 -maxdepth 1 -not -name '.cache' -not -name 'app.yaml' -exec rm -rf {} + || true"

step "Pushing app to ${REMOTE_DIR}"
# Build the tar locally, then `adb push` it as a real file and extract it
# remotely -- NOT `tar -cf - . | adb shell "tar -xf -"`. That piped form
# repeatedly (5+ times across past sessions) truncated mid-stream with NO
# non-zero exit code (the pipeline's exit status is adb shell's, not the
# local tar's), leaving the device with a half-extracted app and no
# app.yaml. A real `adb push` of a finished file doesn't have that failure
# mode. /tmp is used (not /data/local/tmp -- "secure_mkdirs failed:
# Permission denied" on this board).
#
# Excludes: .venv/__pycache__/.pytest_cache are local dev-only cruft, same
# reasoning as before. captures/ and captures_*/ (matching .gitignore) are
# this dev machine's own local capture recordings for offline experiments/EI
# upload tooling (base-station/python/tools/*.py's _DEFAULT_CAPTURES_DIR) --
# unrelated to the device's own captures, which live under
# ${REMOTE_DIR}/.cache/data/captures and are never touched by this script.
# Shipping tens of MB of them on every deploy was dead weight and a likely
# contributor to the pipe-truncation failures above.
TAR_PATH="$(mktemp -t deploy-XXXXXX.tar)"
trap 'rm -f "${TAR_PATH}"' EXIT
tar -C "${LOCAL_DIR}" \
    --exclude='.venv' \
    --exclude='__pycache__' \
    --exclude='.pytest_cache' \
    --exclude='captures' \
    --exclude='captures_*' \
    --exclude='./app.yaml' \
    -cf "${TAR_PATH}" .
adb shell "mkdir -p '${REMOTE_DIR}'"
adb push "${TAR_PATH}" /tmp/deploy.tar
adb shell "tar -C '${REMOTE_DIR}' -xf /tmp/deploy.tar && rm /tmp/deploy.tar"

step "Starting app"
adb shell "arduino-app-cli app start ${REMOTE_DIR}"

step "Streaming logs (Ctrl+C to stop watching; app keeps running)"
adb shell "arduino-app-cli app logs ${REMOTE_DIR}"
