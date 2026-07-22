#!/usr/bin/env bash
#
# _deploy_common.sh -- shared build/deploy plumbing for start_raw_capture.sh
# and start_dashboard.sh. Not standalone; sourced by both.
#
# Resilient to a flaky USB/IP link that can drop for a few seconds at any
# moment (observed directly via dmesg on this dev box -- vhci_hcd cycling
# disconnect/reconnect unpredictably, not just the "occasional" flake
# deploy.sh's own comments describe). Every adb call is short, timeout-
# wrapped, and retried -- no single step depends on the link staying up for
# more than ~30s except the actual build+flash, which is kicked off detached
# (nohup) on the device so a mid-build USB flap doesn't kill it; we just
# reconnect and keep polling for it to finish.

APP_NAME="edgeai-predictive-monitor-base-station"
LOCAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REMOTE_DIR="/home/arduino/ArduinoApps/${APP_NAME}"
CONTAINER="${APP_NAME}-main-1"
APP_CONFIG="${LOCAL_DIR}/sketch/app_config.h"
REMOTE_TMP_TARBALL="/tmp/${APP_NAME}_deploy_payload.tar.gz"
BUILD_LOG="/tmp/${APP_NAME}_app_start.log"

step() { echo; echo "==> $1"; }

wait_for_device() {
    local tries="${1:-15}"
    local i
    for ((i = 1; i <= tries; i++)); do
        if [ "$(adb get-state 2>/dev/null || true)" = "device" ]; then
            return 0
        fi
        sleep 2
    done
    return 1
}

# Runs a short-lived adb command, retrying through USB/IP link flaps.
# Usage: adb_retry <per-attempt-timeout-seconds> <adb-subcommand...>
adb_retry() {
    local per_try_timeout="$1"; shift
    local tries=8
    local i
    for ((i = 1; i <= tries; i++)); do
        if timeout "${per_try_timeout}" adb "$@"; then
            return 0
        fi
        echo "  (attempt ${i}/${tries} failed/timed out -- reconnecting...)" >&2
        wait_for_device 15 >/dev/null || true
    done
    return 1
}

# Sets sketch/app_config.h's FUSER_RAW_CAPTURE_MODE to the given value (0 or 1).
set_raw_capture_mode() {
    local value="$1"
    step "Setting FUSER_RAW_CAPTURE_MODE to ${value}"
    if grep -q "^#define FUSER_RAW_CAPTURE_MODE ${value}\$" "${APP_CONFIG}"; then
        echo "Already ${value} -- no change needed."
    else
        sed -i "s/^#define FUSER_RAW_CAPTURE_MODE .*/#define FUSER_RAW_CAPTURE_MODE ${value}/" "${APP_CONFIG}"
        echo "Set to ${value}."
    fi
}

# Checks app.yaml lists the given port under ports:.
require_port() {
    local port="$1"
    step "Checking app.yaml exposes port ${port}"
    if ! grep -q "${port}" "${LOCAL_DIR}/app.yaml"; then
        echo "app.yaml does not list port ${port} -- add it under 'ports:' and re-run." >&2
        exit 1
    fi
}

# Full build+flash+push+start cycle. On success, the app container is
# running with fresh firmware+code. Exits the script on unrecoverable
# failure (push/extract/kickoff never succeeding, or the build never
# finishing within the wait window).
deploy_app() {
    step "Checking adb device connection"
    if ! wait_for_device 15; then
        echo "Board not visible to adb after waiting. Run 'adb devices' to check." >&2
        exit 1
    fi

    step "Stopping existing app (if running) -- best effort"
    adb_retry 20 shell "arduino-app-cli app stop ${REMOTE_DIR}" || true

    step "Building local payload (compressed, excludes .venv/__pycache__/.pytest_cache)"
    local local_tarball
    local_tarball="$(mktemp "/tmp/${APP_NAME}_payload.XXXXXX.tar.gz")"
    tar -C "${LOCAL_DIR}" \
        --exclude='.venv' \
        --exclude='__pycache__' \
        --exclude='.pytest_cache' \
        -czf "${local_tarball}" .
    echo "Payload: $(du -h "${local_tarball}" | cut -f1)"

    step "Pushing payload to device (single adb push, not a long-lived pipe)"
    if ! adb_retry 30 push "${local_tarball}" "${REMOTE_TMP_TARBALL}"; then
        echo "Push never succeeded despite retries -- link may be down for longer than 30s at a time. Check dmesg for vhci_hcd activity and retry." >&2
        rm -f "${local_tarball}"
        exit 1
    fi
    rm -f "${local_tarball}"

    step "Clearing previous app source (preserving .cache) and extracting payload"
    local extract_cmd="mkdir -p '${REMOTE_DIR}' && \
find '${REMOTE_DIR}' -mindepth 1 -maxdepth 1 -not -name '.cache' -exec rm -rf {} + ; \
tar -xzf '${REMOTE_TMP_TARBALL}' -C '${REMOTE_DIR}' && \
rm -f '${REMOTE_TMP_TARBALL}'"
    if ! adb_retry 30 shell "${extract_cmd}"; then
        echo "Extraction never succeeded despite retries." >&2
        exit 1
    fi

    step "Kicking off build + flash + start (detached on-device, so a USB flap mid-build won't kill it)"
    local kickoff_cmd="rm -f '${BUILD_LOG}'; nohup arduino-app-cli app start '${REMOTE_DIR}' > '${BUILD_LOG}' 2>&1 < /dev/null & disown; echo KICKED_OFF"
    if ! adb_retry 20 shell "${kickoff_cmd}"; then
        echo "Could not even kick off the build -- link too unstable right now. Retry once it settles (watch dmesg)." >&2
        exit 1
    fi

    step "Waiting for build+flash+start to finish (can take 3-15 min; polling, tolerant of link drops)"
    local build_done=0
    local i
    for i in $(seq 1 200); do   # ~200 * 5s = ~16.5 min ceiling
        local state
        state="$(timeout 15 adb shell "docker inspect -f '{{.State.Running}}' ${CONTAINER} 2>/dev/null" 2>/dev/null | tr -d '\r\n')"
        if [ "${state}" = "true" ]; then
            build_done=1
            break
        fi
        sleep 5
    done

    if [ "${build_done}" -ne 1 ]; then
        echo "Container never came up within the wait window. Tail of the on-device build log:" >&2
        adb_retry 15 shell "tail -n 40 '${BUILD_LOG}'" || true
        exit 1
    fi
    echo "Container is up."
}

# Finds the app's uv-managed venv python3 path inside the container.
# Echoes the path on success, exits the script on failure.
find_venv_python() {
    local venv_py=""
    local i
    for i in $(seq 1 15); do
        venv_py="$(adb_retry 15 shell "docker exec ${CONTAINER} find /app/.cache -maxdepth 4 -name python3" 2>/dev/null | tr -d '\r' | head -1)"
        [ -n "${venv_py}" ] && break
        sleep 3
    done
    if [ -z "${venv_py}" ]; then
        echo "Could not find the venv python3 under /app/.cache -- app may still be installing deps. Check 'adb shell docker logs ${CONTAINER}'." >&2
        exit 1
    fi
    echo "${venv_py}"
}

# Echoes the board's LAN IP, or empty if it can't be determined.
find_lan_ip() {
    adb_retry 15 shell "ip route get 1.1.1.1" 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p'
}
