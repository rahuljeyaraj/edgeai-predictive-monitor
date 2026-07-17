#!/usr/bin/env bash
#
# pull_captures.sh
#
# Copies labeled raw-capture .npz files (tools/raw_capture.py output) off
# the UNO Q to a local directory, without nesting folders, then deletes the
# on-device originals once every file is verified safely on the laptop -
# the board has limited storage and shouldn't be the archive; this local
# directory is.
#
# `adb pull <remote_dir> <local_dir>` nests the remote dir's basename inside
# an already-existing local_dir, same as `cp -r` would - so a second run
# produces captures/captures/*.npz, a third captures/captures/captures/*.npz,
# and so on (the exact problem this script exists to avoid). Fix: pull each
# file individually into local_dir instead of pulling the directory itself -
# placing a file into an existing directory never nests.
#
# The capture files live inside the app's docker container (/tmp/captures on
# raw_capture.py's default --out), not on the board's own host filesystem
# that `adb shell`/`adb pull` normally see - `docker cp` bridges that first,
# same two-hop shape as deploy.sh's own adb/docker split.
#
# Every file is size-verified after pulling (and re-pulled if an existing
# local copy's size doesn't match, though raw_capture.py's
# {label}_{unix-timestamp}.npz naming means a name collision with different
# content shouldn't happen in practice). The device's originals are only
# deleted once every single file has verified clean - if anything fails to
# verify, nothing is deleted and the script exits non-zero so you can just
# rerun it.
#
# Usage:
#   ./pull_captures.sh [local_dir]        (default: ./captures)

set -euo pipefail

CONTAINER_NAME="edgeai-predictive-monitor-base-station-main-1"
REMOTE_CONTAINER_DIR="/tmp/captures"
REMOTE_HOST_STAGING="/tmp/captures-pull-staging"
LOCAL_DIR="${1:-./captures}"

step() { echo; echo "==> $1"; }

step "Checking adb device connection"
ADB_STATE="$(adb get-state 2>/dev/null || true)"
if [ "${ADB_STATE}" != "device" ]; then
    echo "Board not visible to adb (got state: '${ADB_STATE:-none}'). Run 'adb devices' to check." >&2
    exit 1
fi

step "Copying captures out of the container onto the board's host filesystem"
adb shell "rm -rf '${REMOTE_HOST_STAGING}' && docker cp '${CONTAINER_NAME}:${REMOTE_CONTAINER_DIR}' '${REMOTE_HOST_STAGING}'" \
    || { echo "No captures found in the container (has raw_capture.py been run yet?)" >&2; exit 1; }

mkdir -p "${LOCAL_DIR}"

step "Pulling + verifying files into ${LOCAL_DIR} (never nests)"
FILES="$(adb shell "ls '${REMOTE_HOST_STAGING}'" | tr -d '\r')"
if [ -z "${FILES}" ]; then
    echo "No capture files found on the device." >&2
    adb shell "rm -rf '${REMOTE_HOST_STAGING}'"
    exit 1
fi

# Read into an array rather than looping over stdin (`while read <<< ...`):
# adb shell/adb pull inside the loop body would otherwise silently consume
# the loop's own remaining input on their own stdin, truncating the file
# list mid-loop with no error - exactly the kind of bug that must not exist
# in a script whose last step deletes the device's only other copy.
mapfile -t FILE_LIST <<< "${FILES}"

pulled=0
already_local=0
failed=0
for f in "${FILE_LIST[@]}"; do
    [ -z "${f}" ] && continue
    remote_size="$(adb shell "stat -c%s '${REMOTE_HOST_STAGING}/${f}'" </dev/null | tr -d '\r')"

    if [ -f "${LOCAL_DIR}/${f}" ]; then
        local_size="$(stat -c%s "${LOCAL_DIR}/${f}" 2>/dev/null || echo -1)"
        if [ "${local_size}" = "${remote_size}" ]; then
            already_local=$((already_local + 1))
            continue
        fi
        echo "  ${f}: local copy size differs (local=${local_size} remote=${remote_size}) - re-pulling" >&2
    fi

    if adb pull "${REMOTE_HOST_STAGING}/${f}" "${LOCAL_DIR}/${f}" </dev/null >/dev/null 2>&1; then
        pulled_size="$(stat -c%s "${LOCAL_DIR}/${f}" 2>/dev/null || echo -1)"
        if [ "${pulled_size}" = "${remote_size}" ]; then
            pulled=$((pulled + 1))
        else
            echo "  ${f}: size mismatch after pull (got ${pulled_size}, expected ${remote_size})" >&2
            failed=$((failed + 1))
        fi
    else
        echo "  ${f}: adb pull failed" >&2
        failed=$((failed + 1))
    fi
done

if [ $((pulled + already_local + failed)) -ne ${#FILE_LIST[@]} ]; then
    echo "Internal check failed: processed $((pulled + already_local + failed)) of ${#FILE_LIST[@]} listed files - not deleting anything from the device." >&2
    exit 1
fi

step "Cleaning up staging copy on the board"
adb shell "rm -rf '${REMOTE_HOST_STAGING}'"

echo
echo "Pulled ${pulled} new file(s), ${already_local} already local (verified), ${failed} failed -> ${LOCAL_DIR}/"

if [ "${failed}" -gt 0 ]; then
    echo "Not deleting anything from the device (some files failed to verify) - rerun this script to retry." >&2
    exit 1
fi

step "All files verified locally -- deleting the originals from the container"
adb shell "docker exec '${CONTAINER_NAME}' rm -rf '${REMOTE_CONTAINER_DIR}'"

ls -la "${LOCAL_DIR}"
