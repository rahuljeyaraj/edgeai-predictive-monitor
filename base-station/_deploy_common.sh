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

# The device's app.yaml is the SOURCE OF TRUTH, and the repo's copy is only a
# seed for a board that has never been deployed to. See preserve_device_app_yaml().
DEVICE_APP_YAML="${REMOTE_DIR}/app.yaml"

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

# App Lab stores brick secrets (the Telegram bot token) by writing them INLINE
# into the deployed app.yaml on the device. There is no separate secret store --
# that has been checked: /var/lib/arduino-app-cli, ~/.arduino15,
# ~/.arduino-bricks, /etc/arduino-app-cli all hold nothing.
#
# So a deploy that ships the repo's app.yaml over the device's copy DELETES the
# token, and the next `arduino-app-cli app start` hard-fails with
#   Variable "Telegram_bot_token" Is Required By Brick "Arduino:telegram_bot"
# before main.py ever runs. The container then never comes up with fresh
# firmware, the MCU sketch never re-registers spi_arm_stream, and the board's
# own base_station node silently vanishes from the dashboard. That bug has cost
# multiple sessions, each time diagnosed as an SPI/firmware fault when it was a
# config fault.
#
# The fix is to treat the DEVICE's app.yaml as authoritative and never ship the
# repo's copy over it. The repo's copy is a seed, used only when the device has
# none. Real app.yaml changes (a new port, a new brick) are therefore deliberate:
# this warns when the two have drifted instead of silently clobbering secrets.
#
# Call BEFORE the wipe/extract. Pairs with the -not -name 'app.yaml' guard in
# the wipe and the --exclude='./app.yaml' in the tar.
preserve_device_app_yaml() {
    step "Protecting the device's app.yaml (holds the Telegram brick token)"

    if ! adb_retry 15 shell "test -f '${DEVICE_APP_YAML}'" 2>/dev/null; then
        echo "Device has no app.yaml yet -- seeding it from the repo's copy."
        adb_retry 20 shell "mkdir -p '${REMOTE_DIR}'" >/dev/null || true
        adb_retry 20 push "${LOCAL_DIR}/app.yaml" "${DEVICE_APP_YAML}" >/dev/null \
            || { echo "Could not seed app.yaml onto the device." >&2; exit 1; }
        if grep -qE '^\s*bricks:\s*\[?\s*arduino:' "${LOCAL_DIR}/app.yaml"; then
            echo
            echo "WARNING: the seeded app.yaml declares a brick but carries no token." >&2
            echo "  The build will fail until you set the secret in App Lab's GUI," >&2
            echo "  or restore /home/arduino/app.yaml.telegram-backup over it." >&2
        fi
        return 0
    fi

    echo "Keeping the device's copy (the repo's app.yaml will NOT be shipped)."

    # Drift check, ignoring the secret-bearing lines so the token never reaches
    # this machine's terminal, logs, or scrollback.
    local device_shape repo_shape
    device_shape="$(adb_retry 15 shell "grep -vE '^\s*(#|TELEGRAM_BOT_TOKEN|variables:|-?\s*arduino:)' '${DEVICE_APP_YAML}' | grep -vE '^\s*$' | tr -d ' \r' | sort" 2>/dev/null)"
    repo_shape="$(grep -vE '^\s*(#|TELEGRAM_BOT_TOKEN|variables:|-?\s*arduino:)' "${LOCAL_DIR}/app.yaml" | grep -vE '^\s*$' | tr -d ' ' | sort)"

    if [ "${device_shape}" != "${repo_shape}" ]; then
        echo
        echo "NOTE: the repo's app.yaml and the device's differ (ignoring secrets)."
        echo "  The device's copy wins, so any new port/brick you added in the repo"
        echo "  is NOT being deployed. To adopt the repo's version deliberately:"
        echo "    1. adb shell \"cp ${DEVICE_APP_YAML} /home/arduino/app.yaml.telegram-backup\""
        echo "    2. adb push base-station/app.yaml ${DEVICE_APP_YAML}"
        echo "    3. re-add the token via App Lab's GUI, then re-run this script."
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

    preserve_device_app_yaml

    step "Building local payload (compressed, excludes .venv/__pycache__/.pytest_cache)"
    local local_tarball
    local_tarball="$(mktemp "/tmp/${APP_NAME}_payload.XXXXXX.tar.gz")"
    # app.yaml is deliberately excluded -- see preserve_device_app_yaml().
    tar -C "${LOCAL_DIR}" \
        --exclude='.venv' \
        --exclude='__pycache__' \
        --exclude='.pytest_cache' \
        --exclude='./app.yaml' \
        -czf "${local_tarball}" .
    echo "Payload: $(du -h "${local_tarball}" | cut -f1)"

    step "Pushing payload to device (single adb push, not a long-lived pipe)"
    if ! adb_retry 30 push "${local_tarball}" "${REMOTE_TMP_TARBALL}"; then
        echo "Push never succeeded despite retries -- link may be down for longer than 30s at a time. Check dmesg for vhci_hcd activity and retry." >&2
        rm -f "${local_tarball}"
        exit 1
    fi
    rm -f "${local_tarball}"

    step "Clearing previous app source (preserving .cache and app.yaml) and extracting payload"
    # app.yaml is spared by name here as well as excluded from the tar -- both
    # halves are needed, or the wipe deletes the token before the extract runs.
    local extract_cmd="mkdir -p '${REMOTE_DIR}' && \
find '${REMOTE_DIR}' -mindepth 1 -maxdepth 1 -not -name '.cache' -not -name 'app.yaml' -exec rm -rf {} + ; \
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

# Starts whatever app code is already on the device -- no local build, no
# push, no extract. Just stop (if running) + start the existing REMOTE_DIR.
# Use this when nothing has changed since the last real deploy_app() run and
# you just want the container back up (e.g. after a reboot or a manual stop).
start_existing_app() {
    step "Checking adb device connection"
    if ! wait_for_device 15; then
        echo "Board not visible to adb after waiting. Run 'adb devices' to check." >&2
        exit 1
    fi

    step "Stopping existing app (if running) -- best effort"
    adb_retry 20 shell "arduino-app-cli app stop ${REMOTE_DIR}" || true

    step "Starting existing app on-device (no push -- using code already there)"
    local kickoff_cmd="rm -f '${BUILD_LOG}'; nohup arduino-app-cli app start '${REMOTE_DIR}' > '${BUILD_LOG}' 2>&1 < /dev/null & disown; echo KICKED_OFF"
    if ! adb_retry 20 shell "${kickoff_cmd}"; then
        echo "Could not even kick off the start -- link too unstable right now. Retry once it settles (watch dmesg)." >&2
        exit 1
    fi

    step "Waiting for start to finish (usually faster than a full deploy; polling, tolerant of link drops)"
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
        echo "If REMOTE_DIR has no app pushed yet, this will always fail -- run without --existing first." >&2
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
