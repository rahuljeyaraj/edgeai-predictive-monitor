#!/usr/bin/env bash
#
# reset-base-station.sh
#
# Recovers the base station from the recurring "base_station node is missing /
# spi_arm_stream not available" failure, without losing the Telegram token.
#
# Why this script exists
# ----------------------
# Three separate things have to be true for the UNO Q's own onboard node to
# appear in the dashboard, and each one has bitten us on its own:
#
#   1. The board is attached to WSL over usbip and visible to adb. An
#      `arduino-app-cli app stop` re-enumerates USB and can drop the board out
#      of WSL entirely, mid-deploy.
#   2. The device's app.yaml carries the Telegram brick token *inline*. There
#      is no separate secret store. start_dashboard.sh wipes REMOTE_DIR and
#      extracts the repo's copy over it -- and the repo's copy has no
#      `variables:` block -- so every deploy deletes the token, and the next
#      `app start` hard-fails with
#         Variable "Telegram_bot_token" Is Required By Brick "Arduino:telegram_bot"
#      before main.py ever runs. The container then never comes up with fresh
#      firmware, so the MCU sketch never re-registers its spi_arm_stream RPC,
#      and base_station goes stale in /nodes. That is the failure this script
#      is named after: it looks like an SPI/firmware problem and is actually a
#      config problem.
#   3. The board is reachable at its LAN IP -- it is on WiFi with no PC
#      attached, so that, not an adb forward, is the path that must work.
#
# So the fix is an ordering fix: push code, restore app.yaml, THEN start.
# start_dashboard.sh cannot do that -- its extract and start steps are welded
# together with no hook between them, which is exactly why the token keeps
# getting wiped.
#
# Usage:
#   ./reset-base-station.sh              # restart the app already on the device
#   ./reset-base-station.sh --deploy     # push this repo's code first, then restart
#
# Use --deploy only when the local code has actually changed. The no-arg form
# is the right one for "it was working, now the node is gone" -- it is faster
# and it cannot ship a half-finished working tree to the board.
#
# The script is safe to re-run. It never decommissions a node, never touches
# the registry, and refuses to start the app at all if it cannot prove the
# token is in place first.

set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${DIR}/base-station/_deploy_common.sh"

# Where the token-bearing app.yaml is kept. Deliberately OUTSIDE REMOTE_DIR so
# the deploy's own "wipe everything except .cache" step cannot eat it.
TOKEN_BACKUP="/home/arduino/app.yaml.telegram-backup"

USBIPD="/mnt/c/Program Files/usbipd-win/usbipd.exe"
UNOQ_VIDPID="2341:0078"   # ADB Interface. The rig's Uno is 2341:0043 -- never attach that one.

DASH_PORT=8080

DEPLOY=0
[ "${1:-}" = "--deploy" ] && DEPLOY=1

ok()  { printf '  \033[32mOK\033[0m    %s\n' "$1"; }
bad() { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; }
fix() { printf '        → %s\n' "$1"; }

die() { echo; bad "$1"; shift; for l in "$@"; do fix "$l"; done; echo; exit 1; }

# --- 1. Board attached to WSL ----------------------------------------------
step "1. Board attached to WSL"
if ! wait_for_device 3; then
    echo "  adb sees no board -- trying to re-attach it over usbipd."
    if [ ! -x "${USBIPD}" ]; then
        die "usbipd.exe not found at ${USBIPD}" \
            "Attach manually from an ADMIN PowerShell:  usbipd attach --wsl --busid <busid>"
    fi
    BUSID="$("${USBIPD}" list 2>/dev/null | awk -v vp="${UNOQ_VIDPID}" '$2 == vp {print $1; exit}')"
    if [ -z "${BUSID}" ]; then
        die "No device with VID:PID ${UNOQ_VIDPID} in usbipd's list" \
            "The board is not plugged in, or Windows lost it. Replug it and re-run." \
            "Check with:  '${USBIPD}' list"
    fi
    echo "  Found UNO Q at busid ${BUSID} -- attaching..."
    "${USBIPD}" attach --wsl --busid "${BUSID}" 2>&1 | sed 's/^/  /'
    if ! wait_for_device 15; then
        die "Attach ran but adb still sees no board" \
            "Watch 'dmesg -w' for vhci_hcd activity while re-running this."
    fi
fi
ok "board attached ($(adb devices | awk 'NR>1 && $2=="device" {printf "%s ", $1}'))"

# --- 2. The Telegram token backup must exist BEFORE we touch anything ------
# Checked up front, not just before the restore: if the token is gone there is
# no point stopping a working app, because we would not be able to start it
# again without a trip through App Lab's Windows-only GUI.
step "2. Telegram token backup"
if ! adb_retry 15 shell "test -f '${TOKEN_BACKUP}' && grep -qE '${TOKEN_ASSIGN_RE}' '${TOKEN_BACKUP}'" 2>/dev/null; then
    die "No usable token backup at ${TOKEN_BACKUP} on the device" \
        "Without it, starting the app will hard-fail on the telegram_bot brick." \
        "Recover it by setting Telegram_bot_token in App Lab's GUI (Windows only)," \
        "then back the result up on-device:" \
        "  adb shell \"cp ${REMOTE_DIR}/app.yaml ${TOKEN_BACKUP}\"" \
        "Or, to proceed without Telegram, set 'bricks: []' in base-station/app.yaml and --deploy."
fi
ok "token backup present on device"

# --- 3. Heal the device's app.yaml if a past deploy wiped its token --------
# Deploys no longer clobber it (_deploy_common.sh's preserve_device_app_yaml
# keeps the device's copy and excludes the repo's from the payload), but a
# board left broken by an OLDER deploy still needs healing -- that is the state
# this script most often runs in.
# Matched with TOKEN_ASSIGN_RE, not a bare `grep TELEGRAM_BOT_TOKEN`: app.yaml
# documents the variable by name in its comments, so an unanchored match reports
# a token-less `bricks: []` file as healthy and skips the restore below.
step "3. Checking the device's app.yaml still has its token"
if adb_retry 15 shell "grep -qE '${TOKEN_ASSIGN_RE}' '${REMOTE_DIR}/app.yaml'" 2>/dev/null; then
    ok "token already in place on the device"
else
    echo "  Token missing (a pre-fix deploy wiped it) -- restoring from the backup."
    adb_retry 15 shell "cp '${TOKEN_BACKUP}' '${REMOTE_DIR}/app.yaml'" >/dev/null \
        || die "Could not restore app.yaml from the backup"
    adb_retry 15 shell "grep -qE '${TOKEN_ASSIGN_RE}' '${REMOTE_DIR}/app.yaml'" 2>/dev/null \
        || die "Restore ran but the token is still not in ${REMOTE_DIR}/app.yaml" \
               "Refusing to start -- the brick check would fail the build."
    ok "token restored"
fi

# --- 4. Build + flash + start ----------------------------------------------
# Both paths go through _deploy_common.sh rather than repeating its logic, so
# the app.yaml protection lives in exactly one place and cannot drift.
if [ "${DEPLOY}" -eq 1 ]; then
    step "4. Full deploy (push this repo's code, then build + flash + start)"
    deploy_app
else
    step "4. Restarting the code already on the device (no --deploy)"
    start_existing_app
fi
ok "container is up"

# --- 5. Find the board on the LAN ------------------------------------------
# Deliberately NOT `adb forward` + localhost. In real use the board is on WiFi
# with no PC attached, so a forward tests a path that will not exist. Worse, it
# tests a path that CANNOT fail the way the real one does: on 2026-08-20 the
# dashboard answered perfectly over the forward while being completely
# unreachable over WiFi, which is exactly the outage the user was seeing.
# Everything below therefore checks the board at its LAN address.
step "5. Locating the board on the LAN"
BOARD_IP="$(find_lan_ip | tr -d '\r')"
if [ -z "${BOARD_IP}" ]; then
    bad "Could not determine the board's LAN IP"
    fix "Check WiFi on the board:  adb shell nmcli dev status"
else
    ok "board is ${BOARD_IP} -- dashboard at http://${BOARD_IP}:${DASH_PORT}/"
fi

# --- 6. Prove the actual symptom is gone -----------------------------------
# The container being up is NOT proof: last time the app answered on :8080 for
# hours while spi_arm_stream stayed dead. Check the RPC itself.
step "6. Verifying the MCU registered spi_arm_stream"
PROBE="$(mktemp)"
cat > "${PROBE}" <<'PY'
from arduino.app_utils import Bridge
try:
    Bridge.call('spi_arm_stream', '512')
    print('SPI_OK')
except Exception as e:
    print('SPI_FAIL', e)
PY
adb_retry 15 push "${PROBE}" /tmp/_epm_probe.py >/dev/null 2>&1
rm -f "${PROBE}"
adb_retry 15 shell "docker cp /tmp/_epm_probe.py ${CONTAINER}:/tmp/_epm_probe.py" >/dev/null 2>&1

SPI=""
for i in $(seq 1 12); do   # the sketch needs a moment after flash to register
    SPI="$(timeout 30 adb shell "docker exec ${CONTAINER} python3 /tmp/_epm_probe.py" 2>/dev/null | tr -d '\r')"
    case "${SPI}" in
        *SPI_OK*) break ;;
        # 'busy' means the stream is already armed by the running app -- healthy.
        *busy*)   break ;;
    esac
    sleep 5
done
case "${SPI}" in
    *SPI_OK*|*busy*) ok "spi_arm_stream answering" ;;
    *) bad "spi_arm_stream still not answering: ${SPI}"
       fix "This is the failure the script exists to fix, so something new is wrong."
       fix "Check the sketch build:  adb shell tail -50 ${BUILD_LOG}" ;;
esac

# Asked over the LAN, so a pass here means a phone or laptop can actually load
# the dashboard -- not merely that the app is alive inside the board.
step "7. Verifying base_station is back in /nodes (over the LAN)"
NODE=0
NET_FAIL=0
for i in $(seq 1 20); do   # needs one valid frame to re-register in the registry
    RESP="$(curl -s --max-time 5 "http://${BOARD_IP}:${DASH_PORT}/nodes" 2>/dev/null)"
    if [ -z "${RESP}" ]; then
        NET_FAIL=$((NET_FAIL + 1))
    else
        NODE="$(printf '%s' "${RESP}" | grep -c base_station)"
        [ "${NODE:-0}" -gt 0 ] && break
    fi
    sleep 3
done
if [ "${NODE:-0}" -gt 0 ]; then
    ok "base_station present in /nodes"
    [ "${NET_FAIL}" -gt 0 ] && {
        bad "but ${NET_FAIL} of the LAN requests timed out -- the WiFi link is degraded"
        fix "The app is fine; the radio is not. Check signal:  adb shell nmcli dev wifi list"
        fix "A weak/congested 2.4GHz link makes the dashboard 'come on and off'."
    }
    echo
    echo "Done. Open http://${BOARD_IP}:${DASH_PORT}/"
else
    bad "base_station not reachable in /nodes over the LAN after ~60s"
    fix "If every request timed out, this is a WiFi problem, not an app problem."
    fix "Confirm from the board's side:  adb shell \"docker exec ${CONTAINER} curl -s localhost:8080/nodes\""
    fix "If spi_arm_stream above was OK, it just needs a valid frame -- give it a minute."
    fix "Otherwise check:  curl -s localhost:${DASH_PORT}/perf | python3 -m json.tool | head -30"
    fix "frames_ok climbing = healthy; all-dropped with arm_gap 0 = the RPC is dead again."
fi
