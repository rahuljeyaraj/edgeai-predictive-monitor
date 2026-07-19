#!/usr/bin/env bash
#
# provision-gpu.sh
#
# Gives the app's Python code (running inside the arduino-app-cli-managed
# Docker container) a real GPU utilization reading, for the Dev/perf page's
# QRB2210 tier (docs/DEV_PERF_PAGE_PLAN.md).
#
# The GPU's live busy% comes from a root-only debugfs stream
# (/sys/kernel/debug/dri/<N>/perf) -- /sys/kernel/debug isn't bind-mounted
# into the app container (same class of restriction as spidev0.0, see
# provision-spi.sh), so a small root-owned systemd service (host/gpu_bridge.py
# + host/gpu-bridge.service) reads it from outside the container and
# re-exposes the latest value over a Unix domain socket at
# /dev/gpu-perf.sock. That path is under /dev, which IS already bind-mounted
# into the container, so the socket needs no new compose/bind-mount plumbing
# and survives every app redeploy untouched. App-side Python code
# (monitoring/gpu_perf.py) talks to that socket, not to debugfs directly.
#
# This is a SYSTEM-LEVEL, ONE-TIME board provisioning step, OUTSIDE the App
# Lab app: it is NOT applied by deploy.sh and is wiped by an OS reflash -
# re-run it after any base-OS reflash. It installs a root-owned systemd
# unit, so it needs the board's sudo password (prompted once on-device via
# sudo -S).
#
# Usage:
#   ./provision-gpu.sh
#
# You will be prompted for the board's sudo password.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DAEMON_SRC="${SCRIPT_DIR}/host/gpu_bridge.py"
UNIT_SRC="${SCRIPT_DIR}/host/gpu-bridge.service"
DAEMON_DST="/usr/local/sbin/gpu_bridge.py"
UNIT_DST="/etc/systemd/system/gpu-bridge.service"

step() { echo; echo "==> $1"; }

ADB_STATE="$(adb get-state 2>/dev/null || true)"
if [ "${ADB_STATE}" != "device" ]; then
    echo "Board not visible to adb (state: '${ADB_STATE:-none}'). Run 'adb devices'." >&2
    exit 1
fi

read -r -s -p "Board sudo password: " SUDO_PW
echo

step "Pushing ${DAEMON_DST} and ${UNIT_DST}"
adb push "${DAEMON_SRC}" /tmp/gpu_bridge.py >/dev/null
adb push "${UNIT_SRC}" /tmp/gpu-bridge.service >/dev/null
adb shell "echo '${SUDO_PW}' | sudo -S -p '' sh -c '
  cp /tmp/gpu_bridge.py ${DAEMON_DST} && chmod 755 ${DAEMON_DST} &&
  cp /tmp/gpu-bridge.service ${UNIT_DST} &&
  rm -f /tmp/gpu_bridge.py /tmp/gpu-bridge.service
'"

step "Enabling + (re)starting gpu-bridge.service"
adb shell "echo '${SUDO_PW}' | sudo -S -p '' systemctl daemon-reload"
adb shell "echo '${SUDO_PW}' | sudo -S -p '' systemctl enable --now gpu-bridge.service"
adb shell "echo '${SUDO_PW}' | sudo -S -p '' systemctl restart gpu-bridge.service"

step "Verifying"
adb shell "echo '${SUDO_PW}' | sudo -S -p '' systemctl is-active gpu-bridge.service"
adb shell "ls -la /dev/gpu-perf.sock"
echo
echo "gpu-bridge.service is up, exposing the GPU's live busy% at /dev/gpu-perf.sock."
echo "App-side Python connects to that socket (see monitoring/gpu_perf.py)."
