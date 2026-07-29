#!/usr/bin/env bash
#
# provision-wifi.sh
#
# Gives the app's Python code (running inside the arduino-app-cli-managed
# Docker container) control over WiFi onboarding (docs/WIFI_ONBOARDING_PLAN.md
# S1) -- joining the factory network, falling back to a self-hosted AP + a
# captive-portal-style form, with no manual factory reset ever required.
#
# A plain udev/group fix is NOT enough here, same reasoning as
# provision-spi.sh/provision-gpu.sh: the app container isn't privileged (no
# CAP_NET_ADMIN, no host network namespace), so it can't drive wlan0 itself
# even though /dev is bind-mounted into it. The fix is the same shape: a
# small root-owned systemd service (host/wifi_bridge.py + host/wifi-bridge.
# service) that drives wlan0 via nmcli from outside the container -- where
# that restriction doesn't apply -- and re-exposes control over a Unix
# domain socket at /dev/wifi-link.sock. That path is under /dev, which IS
# already bind-mounted into the container, so it needs no new compose/
# bind-mount plumbing and survives every app redeploy. App-side Python code
# (python/network/wifi.py) talks to that socket, not to wlan0/nmcli directly.
#
# NetworkManager was confirmed already active on this board (`nmcli`,
# `systemctl is-active NetworkManager`) -- this script does NOT install
# hostapd/dnsmasq; NM's own "shared" ipv4 method handles AP-mode DHCP.
#
# This also does two other one-time, board-level things the onboarding
# flow depends on:
#   - Creates the reusable, OPEN (no password) "Hotspot" NM connection
#     profile (ssid EPM-BaseStation) that wifi_bridge.py activates/
#     deactivates but never creates itself. Re-running this script deletes
#     and recreates it, so it's safe to re-run.
#   - Renames the host from its current hostname to `epm-base` so avahi's
#     already-active default mDNS publishing (<hostname>.local) matches
#     what docs/WIFI_ONBOARDING_PLAN.md calls the base station
#     (`epm-base.local`), instead of writing new mDNS code.
#
# This is a SYSTEM-LEVEL, ONE-TIME board provisioning step, OUTSIDE the App
# Lab app: it is NOT applied by deploy.sh and is wiped by an OS reflash --
# re-run it after any base-OS reflash. It needs the board's sudo password
# (prompted once on-device via sudo -S).
#
# Usage:
#   ./provision-wifi.sh
#
# You will be prompted for the board's sudo password.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DAEMON_SRC="${SCRIPT_DIR}/host/wifi_bridge.py"
UNIT_SRC="${SCRIPT_DIR}/host/wifi-bridge.service"
DAEMON_DST="/usr/local/sbin/wifi_bridge.py"
UNIT_DST="/etc/systemd/system/wifi-bridge.service"
HOTSPOT_SSID="EPM-BaseStation"
NEW_HOSTNAME="epm-base"

step() { echo; echo "==> $1"; }

ADB_STATE="$(adb get-state 2>/dev/null || true)"
if [ "${ADB_STATE}" != "device" ]; then
    echo "Board not visible to adb (state: '${ADB_STATE:-none}'). Run 'adb devices'." >&2
    exit 1
fi

read -r -s -p "Board sudo password: " SUDO_PW
echo

step "Creating the open Hotspot NM connection profile (ssid ${HOTSPOT_SSID})"
# Idempotent: delete any previous profile of this name first (harmless if
# it doesn't exist yet), then create fresh. No 802-11-wireless-security
# section at all == an unencrypted/open network (deliberate -- decided
# 2026-07-29 as the simplest onboarding-time tradeoff: this AP only exists
# transiently, while a technician sets up the real factory credentials).
# ipv4.method shared makes NM hand out DHCP leases itself on whatever
# private subnet it picks (commonly 10.42.0.1 -- matching the address
# already referenced for this SSID across docs/satellite/README.md).
adb shell "echo '${SUDO_PW}' | sudo -S -p '' nmcli connection delete Hotspot 2>/dev/null; true"
adb shell "echo '${SUDO_PW}' | sudo -S -p '' nmcli connection add type wifi ifname wlan0 con-name Hotspot autoconnect no ssid ${HOTSPOT_SSID} mode ap"
adb shell "echo '${SUDO_PW}' | sudo -S -p '' nmcli connection modify Hotspot 802-11-wireless.band bg ipv4.method shared"

step "Renaming host to ${NEW_HOSTNAME} (so epm-base.local matches the onboarding docs)"
adb shell "echo '${SUDO_PW}' | sudo -S -p '' hostnamectl set-hostname ${NEW_HOSTNAME}"
adb shell "echo '${SUDO_PW}' | sudo -S -p '' systemctl restart avahi-daemon"

step "Pushing ${DAEMON_DST} and ${UNIT_DST}"
adb push "${DAEMON_SRC}" /tmp/wifi_bridge.py >/dev/null
adb push "${UNIT_SRC}" /tmp/wifi-bridge.service >/dev/null
adb shell "echo '${SUDO_PW}' | sudo -S -p '' sh -c '
  cp /tmp/wifi_bridge.py ${DAEMON_DST} && chmod 755 ${DAEMON_DST} &&
  cp /tmp/wifi-bridge.service ${UNIT_DST} &&
  rm -f /tmp/wifi_bridge.py /tmp/wifi-bridge.service
'"

step "Enabling + (re)starting wifi-bridge.service"
adb shell "echo '${SUDO_PW}' | sudo -S -p '' systemctl daemon-reload"
adb shell "echo '${SUDO_PW}' | sudo -S -p '' systemctl enable --now wifi-bridge.service"
adb shell "echo '${SUDO_PW}' | sudo -S -p '' systemctl restart wifi-bridge.service"

step "Verifying"
adb shell "echo '${SUDO_PW}' | sudo -S -p '' systemctl is-active wifi-bridge.service"
adb shell "ls -la /dev/wifi-link.sock"
adb shell "hostname"

echo
echo "wifi-bridge.service is up, exposing WiFi control at /dev/wifi-link.sock."
echo "Host is now ${NEW_HOSTNAME} -- reachable at ${NEW_HOSTNAME}.local once joined to a real network."
echo "App-side Python connects to the socket via python/network/wifi.py."
