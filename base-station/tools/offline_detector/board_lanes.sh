#!/bin/sh
# Runs ON the UNO Q, started by node_isolate.sh. Two lanes side by side,
# neither of which crosses WiFi on its way to us:
#
#   nodes.csv   what the app believes  -- GET /nodes, per node, 1/s
#   broker.csv  what actually arrived  -- every epm/<node>/data message
#
# Together they split a stale node two ways: no broker traffic during the
# gap means the frames never got here (node or link), broker traffic during
# the gap means they got here and the app was late applying them.
#
# Caveat worth knowing before trusting a marginal result: the subscriber
# takes a full copy of every telemetry payload over loopback, so it is not
# free on a board already near its ingest ceiling.
DUR=${1:-120}
PORT=${2:-8080}
rm -f /tmp/nodes.csv /tmp/broker.csv
mosquitto_sub -h 127.0.0.1 -t 'epm/+/data' -F '%U %t' > /tmp/broker.csv 2>&1 &
SUB=$!
python3 /tmp/node_probe.py "$DUR" "$PORT" > /tmp/nodes.csv 2>&1
kill "$SUB" 2>/dev/null
