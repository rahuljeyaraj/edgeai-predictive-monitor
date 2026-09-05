#!/usr/bin/env bash
#
# provision-baud.sh
#
# Raises the arduino-router MCU<->MPU serial link baud on the UNO Q to match the
# sketch's BRIDGE_BAUD (base-station/sketch/app_config.h). The default router
# baud is 115200 (~11.5 KB/s), which cannot carry the fuser's full-resolution
# float32 spectrum push (~64 KB/s); this installs a persistent systemd drop-in
# that overrides the router's --serial-baudrate.
#
# This is a SYSTEM-LEVEL, ONE-TIME board provisioning step, OUTSIDE the App Lab
# app: it is NOT applied by deploy.sh and is wiped by an OS reflash - re-run it
# after any base-OS reflash. It edits a root-owned systemd unit, so it needs the
# board's sudo password (prompted once on-device via sudo -S).
#
# The MCU side must be flashed at the SAME baud (deploy.sh, with app_config.h
# already set) - a mismatch silently breaks the whole Bridge link. Recommended
# order: run this first, then deploy.sh (so the MCU resets into the new baud last
# and the handshake is clean).
#
# Usage:
#   ./provision-baud.sh                             # defaults to 500000
#   BRIDGE_BAUD=2000000 ./provision-baud.sh          # baud must match app_config.h
#
# You will be prompted for the board's sudo password.

set -euo pipefail

BAUD="${BRIDGE_BAUD:-500000}"   # matches app_config.h BRIDGE_BAUD
SERIAL_PORT="/dev/ttyHS1"   # UNO Q ("Imola"): MCU <-> Linux high-speed UART
DROPIN_DIR="/etc/systemd/system/arduino-router.service.d"
DROPIN="${DROPIN_DIR}/99-baud.conf"

step() { echo; echo "==> $1"; }

ADB_STATE="$(adb get-state 2>/dev/null || true)"
if [ "${ADB_STATE}" != "device" ]; then
    echo "Board not visible to adb (state: '${ADB_STATE:-none}'). Run 'adb devices'." >&2
    exit 1
fi

read -r -s -p "Board sudo password: " SUDO_PW
echo

# The ExecStart mirrors the stock generator drop-in
# (/var/lib/arduino-router/config/10-imola.conf) exactly, changing only the
# baudrate. The leading empty 'ExecStart=' resets the generator's command so
# ours (in a higher-sorted drop-in) is the one that runs; ExecStartPre/StopPost
# from the generator (the micro-ready gpioset) are left untouched.
DROPIN_CONTENT="[Service]
ExecStart=
ExecStart=/usr/bin/arduino-router --unix-port /var/run/arduino-router.sock --serial-port ${SERIAL_PORT} --serial-baudrate ${BAUD} --after-ready '/usr/bin/gpioset -c /dev/gpiochip1 -t0 70=1'
"

step "Installing router drop-in at ${DROPIN} (baud ${BAUD})"
printf '%s' "${DROPIN_CONTENT}" | adb shell "cat > /tmp/99-baud.conf"
adb shell "echo '${SUDO_PW}' | sudo -S -p '' sh -c 'mkdir -p ${DROPIN_DIR} && cp /tmp/99-baud.conf ${DROPIN} && rm -f /tmp/99-baud.conf'"

step "Reloading systemd and restarting arduino-router"
adb shell "echo '${SUDO_PW}' | sudo -S -p '' systemctl daemon-reload"
adb shell "echo '${SUDO_PW}' | sudo -S -p '' systemctl restart arduino-router"

step "Verifying active baud"
adb shell "systemctl cat arduino-router 2>/dev/null | grep -m1 serial-baudrate | sed 's/^/  /'"
echo
echo "Router now at baud ${BAUD}. Flash the MCU at the same baud (deploy.sh with"
echo "app_config.h BRIDGE_BAUD=${BAUD}) if you haven't already."
