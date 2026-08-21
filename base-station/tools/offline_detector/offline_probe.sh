#!/usr/bin/env bash
# Answers one question about an "everything went Offline" event: was it the
# app, or was it the link?
#
# "Offline" is computed entirely in the browser (frontend/app.js
# OFFLINE_AFTER_S) from last_seen staleness -- nothing server-side ever sets
# it. So a node reads Offline either because the app really stopped seeing
# it, or because the browser could not get fresh data in time. Those need
# opposite fixes, and they look identical on screen.
#
# Three lanes, one window:
#   BOARD  GET /nodes from inside the UNO Q      -- never crosses WiFi
#   LAN    the same GET from the Windows host    -- the browser's own stack
#   PING   PC->board vs PC->router               -- separates the two hops
#
# Board clean + LAN dirty => the link. Board dirty => the app.
#
# Usage: ./offline_probe.sh [seconds=120]      env: BOARD_IP, PORT, OUT
set -u
DUR=${1:-120}
source "$(cd "$(dirname "$0")" && pwd)/_common.sh"
ROUTER_IP=${ROUTER_IP:-192.168.1.1}

PS1W=$(stage_lan_probe) || exit 1
adb push "$HERE/board_probe.py" /tmp/board_probe.py >/dev/null || exit 1

echo "== probing ${DUR}s (board ${BOARD_IP}:${PORT}) =="

adb shell "python3 /tmp/board_probe.py $DUR $PORT" > "$OUT/board.csv" 2>&1 &
B=$!
( cd /mnt/c && powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$PS1W" \
    -Dur "$DUR" -BoardIp "$BOARD_IP" -Port "$PORT" ) > "$OUT/lan.csv" 2>&1 &
L=$!
# One ping per second, so -n matches the run length. Router is sampled only
# briefly -- it is the reference hop, not the thing under test.
( cd /mnt/c && powershell.exe -NoProfile -Command \
    "ping.exe -n $DUR $BOARD_IP | Select-Object -Last 5; ping.exe -n 6 $ROUTER_IP | Select-Object -Last 4" ) \
    > "$OUT/ping.txt" 2>&1 &
P=$!
wait $B $L $P

python3 "$HERE/summarize.py" "$OUT"
echo; echo "raw csvs: $OUT"
