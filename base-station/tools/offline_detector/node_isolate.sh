#!/usr/bin/env bash
# Second stage of the offline detector. Run this when offline_probe.sh says
# BOARD.
#
# That verdict is a max() over every node, and the fleet does not share one
# path: base_station comes in over SPI and never touches the air, the real
# satellites cross it once, and the sim nodes are processes on the dev PC
# publishing across the very link the PING lane is measuring. So a BOARD
# verdict can be produced entirely by WiFi, as long as the node it happened
# to hit was one of the twelve that ride WiFi rather than the one that
# doesn't.
#
# This stage answers, per node: were the frames missing, or merely late?
#
#   nodes.csv   what the app believes -- GET /nodes per node, 1/s, on-board
#   broker.csv  what actually arrived -- every epm/<node>/data, on-board
#   ping.txt    PC->board, for context only
#
# base_station is the control: it is the one node whose staleness cannot be
# WiFi. If it stays fresh while WiFi-borne nodes go stale, the app is fine.
#
# Usage: ./node_isolate.sh [seconds=120]   env: BOARD_IP, PORT, OUT, SIM_PANEL
set -u
DUR=${1:-120}
source "$(cd "$(dirname "$0")" && pwd)/_common.sh"
SIM_PANEL=${SIM_PANEL:-http://127.0.0.1:9100/api/nodes}

# Which node ids are processes on this PC rather than hardware. Best effort:
# without it every node is just "mqtt" and the sim/real split has to be read
# off the node ids by hand.
curl -s --max-time 5 "$SIM_PANEL" 2>/dev/null \
  | python3 -c 'import json,sys;print("\n".join(n["node_id"] for n in json.load(sys.stdin)))' \
  > "$OUT/pc_nodes.txt" 2>/dev/null || : > "$OUT/pc_nodes.txt"

echo "== isolating ${DUR}s (board ${BOARD_IP}:${PORT}, $(wc -l < "$OUT/pc_nodes.txt") sim nodes on this PC) =="

adb push "$HERE/node_probe.py" /tmp/node_probe.py >/dev/null || exit 1
adb push "$HERE/board_lanes.sh" /tmp/board_lanes.sh >/dev/null || exit 1

perf_snap() {
  adb shell "python3 -c \"import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:$PORT/perf',timeout=5).read().decode())\"" \
    > "$1" 2>&1
}
perf_snap "$OUT/perf_before.json"

adb shell "sh /tmp/board_lanes.sh $DUR $PORT" >/dev/null 2>&1 &
B=$!
( cd /mnt/c && powershell.exe -NoProfile -Command "ping.exe -n $DUR $BOARD_IP | Select-Object -Last 5" ) \
  > "$OUT/ping.txt" 2>&1 &
P=$!
wait $B $P

perf_snap "$OUT/perf_after.json"
adb pull /tmp/nodes.csv "$OUT/nodes.csv" >/dev/null 2>&1
adb pull /tmp/broker.csv "$OUT/broker.csv" >/dev/null 2>&1

python3 "$HERE/isolate_summary.py" "$OUT"
echo; echo "raw csvs: $OUT"
