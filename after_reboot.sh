#!/usr/bin/env bash
#
# after_reboot.sh
#
# Brings the link between this machine and the UNO Q back up after a reboot,
# replug, or usbipd dance, and checks every layer that has to be working
# before the motor rig and the base station can see each other.
#
# The only two things it changes are adb port forwards (which do not survive a
# disconnect) and starting the app container if it has exited. It never
# deploys, never decommissions, and never touches the registry -- so it is
# safe to run repeatedly, and safe to run when you don't know what is wrong.
#
# Usage:
#   ./after_reboot.sh
#
# Each check prints OK or FAIL with the exact thing to do about it, and the
# script keeps going so you get the whole picture in one pass rather than
# fixing one thing at a time.

set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DASH_PORT=8080        # base station dashboard + REST API, forwarded 1:1
MQTT_PORT=11883       # local port -> the board's 1883, deliberately not 1883
                      # so it can't be confused with a broker on this machine
FAILED=0

ok()   { printf '  \033[32mOK\033[0m    %s\n' "$1"; }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAILED=1; }
warn() { printf '  \033[33mWARN\033[0m  %s\n' "$1"; }
fix()  { printf '        → %s\n' "$1"; }
step() { printf '\n\033[1m%s\033[0m\n' "$1"; }

# --- 1. Is the board attached to WSL at all? --------------------------------
step "1. UNO Q over adb"
DEVICES="$(adb devices 2>/dev/null | awk 'NR>1 && $2=="device" {print $1}')"
if [ -z "${DEVICES}" ]; then
    bad "adb sees no board"
    fix "In an ADMIN PowerShell on Windows:  usbipd list"
    fix "then:  usbipd attach --wsl --busid <the UNO Q's busid>"
    fix "Attach ONLY the UNO Q. The rig's Arduino Uno must stay on Windows,"
    fix "or Chrome can't reach it and the control page has nothing to Connect to."
    echo
    echo "Nothing else can be checked until the board is attached. Stopping."
    exit 1
fi
ok "board attached ($(echo "${DEVICES}" | tr '\n' ' '))"

# --- 2. Port forwards -------------------------------------------------------
# These are per-connection. A reboot, a replug, or a usbipd detach silently
# drops them, and every symptom after that looks like "the base station is
# down" rather than "there is no tunnel".
step "2. Port forwards"
adb forward tcp:${DASH_PORT} tcp:8080 >/dev/null 2>&1 \
    && ok "localhost:${DASH_PORT} -> board :8080 (dashboard)" \
    || bad "could not forward ${DASH_PORT}"
adb forward tcp:${MQTT_PORT} tcp:1883 >/dev/null 2>&1 \
    && ok "localhost:${MQTT_PORT} -> board :1883 (broker)" \
    || bad "could not forward ${MQTT_PORT}"

# --- 3. Clock ---------------------------------------------------------------
# The board has no NTP while it's in AP mode. A skewed clock makes every node
# look OFFLINE on the dashboard, because staleness is measured against the
# board's own idea of now -- a confusing failure that looks like dead sensors.
step "3. Board clock"
BOARD_EPOCH="$(adb shell date +%s 2>/dev/null | tr -d '\r')"
if ! [[ "${BOARD_EPOCH}" =~ ^[0-9]+$ ]]; then
    warn "couldn't read the board's clock"
else
    SKEW=$(( $(date +%s) - BOARD_EPOCH ))
    ABS_SKEW=${SKEW#-}
    if [ "${ABS_SKEW}" -lt 120 ]; then
        ok "within ${ABS_SKEW}s of this machine"
    else
        bad "off by ${ABS_SKEW}s ($(( ABS_SKEW / 3600 ))h) — nodes will read OFFLINE"
        fix "adb shell su -c \"date -s @$(date +%s)\"   (needs root; may not work)"
        fix "Otherwise expect OFFLINE badges and ignore them."
    fi
fi

# --- 4. The dashboard itself ------------------------------------------------
# The app container is restart=no, so it does NOT come back by itself after a
# reboot or a replug -- it just sits there Exited(0) while every symptom looks
# like a network problem. Starting the existing container is much faster than
# a redeploy (~3 s vs a full push) and is all that's needed when the code
# hasn't changed, so try that before telling anyone to redeploy.
step "4. Base station app"
CONTAINER="edgeai-predictive-monitor-base-station-main-1"
if curl -fs -m 5 "http://localhost:${DASH_PORT}/trip_outputs" >/dev/null 2>&1; then
    ok "answering on localhost:${DASH_PORT}"
else
    warn "no answer on localhost:${DASH_PORT} — trying to start the container"
    if adb shell "docker start ${CONTAINER}" >/dev/null 2>&1; then
        for _ in $(seq 1 20); do
            curl -fs -m 3 "http://localhost:${DASH_PORT}/trip_outputs" >/dev/null 2>&1 && break
            sleep 2
        done
        if curl -fs -m 5 "http://localhost:${DASH_PORT}/trip_outputs" >/dev/null 2>&1; then
            ok "started the existing container — answering now"
        else
            bad "container started but the app isn't answering"
            fix "cd base-station && ./start_dashboard.sh   (full redeploy)"
            fix "(Use that, never deploy.sh — its tar step truncates, repeatedly.)"
        fi
    else
        bad "no container to start"
        fix "cd base-station && ./start_dashboard.sh   (full redeploy)"
        fix "(Use that, never deploy.sh — its tar step truncates, repeatedly.)"
    fi
fi

# --- 5. The broker ----------------------------------------------------------
# A real MQTT connect, not a TCP probe: a forwarded port answers the TCP
# handshake even when nothing is listening on the far side, so a bare
# connect() test would pass against a dead broker.
step "5. MQTT broker on the board"
PYTHON="${DIR}/motor-driver/.venv/bin/python"
[ -x "${PYTHON}" ] || PYTHON="python3"
BROKER_OUT="$("${PYTHON}" - "${MQTT_PORT}" <<'PY' 2>&1
import sys
try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("NOPAHO"); raise SystemExit
c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
try:
    c.connect("localhost", int(sys.argv[1]), keepalive=5)
    c.disconnect()
    print("UP")
except Exception as e:
    print(f"DOWN {e}")
PY
)"
case "${BROKER_OUT}" in
    UP)     ok "broker reachable via localhost:${MQTT_PORT}" ;;
    NOPAHO) warn "paho-mqtt not installed, skipped"
            fix "cd motor-driver && python3 -m venv .venv && ./.venv/bin/pip install pyserial paho-mqtt" ;;
    *)      bad "broker not reachable (${BROKER_OUT})"
            fix "adb shell 'sudo systemctl start mosquitto'  — it may not be enabled at boot" ;;
esac

# --- 6. What the base station currently believes about the rig --------------
# The announce is RETAINED. Whatever the rig last published stays on the
# broker forever, rig switched off or not -- so a stale list here is the
# normal way this looks wrong, and it is not a bug in either end.
step "6. Trip outputs the base station is offering"
OUTPUTS="$(curl -fs -m 5 "http://localhost:${DASH_PORT}/trip_outputs" 2>/dev/null)"
if [ -z "${OUTPUTS}" ]; then
    warn "couldn't read them (see step 4)"
else
    echo "${OUTPUTS}" | "${PYTHON}" -c '
import json, sys, time
outs = json.load(sys.stdin).get("outputs", [])
if not outs:
    print("        (none announced -- setup falls back to a typed motor number)")
for o in outs:
    age = time.time() - o.get("announced_at", 0)
    claim = o.get("claimed_by") or "unclaimed"
    idx = o.get("idx")
    print("        motor %s  %-16s announced %.1fh ago" % (idx, claim, age / 3600))
'
    fix "Stale or too many? Start the rig host — its announce replaces this:"
    fix "cd motor-driver && ./start_motor_driver.sh"
fi

# --- Summary ----------------------------------------------------------------
step "Next"
if [ "${FAILED}" -eq 0 ]; then
    echo "  Everything is up. Start the rig host:"
    echo
    echo "    cd motor-driver && ./start_motor_driver.sh"
    echo
    echo "  Wait for:  ANNOUNCED outputs [1] on epm/motor_rig/outputs"
    echo "  Then open  http://localhost:8000/       (control page — click Connect)"
    echo "        and  http://localhost:${DASH_PORT}/       (base station)"
else
    echo "  Fix the FAILs above, then run this again."
fi
exit "${FAILED}"
