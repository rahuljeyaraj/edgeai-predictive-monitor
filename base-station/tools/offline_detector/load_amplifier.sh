#!/usr/bin/env bash
# Raises the failure rate on demand so you don't have to sit and wait for a
# spontaneous stall. Runs N concurrent GET /nodes pollers from Windows, and
# -- in the same window -- the identical load against the board's own
# localhost, so server capacity is measured rather than argued about.
#
# READ THIS BEFORE TRUSTING A RESULT. This does NOT reproduce the reported
# bug. The real complaint happens with a single dashboard tab open; this
# makes an *overloaded* link fail, which may be a different mechanism. Use it
# to exercise a suspected fix quickly, not as evidence about normal-load
# behaviour. offline_probe.sh is the honest instrument.
#
# Measured (60s runs, N concurrent, board localhost never failed):
#   N=1  3/82 LAN failures, slowest ok 12.8s
#   N=4  varies wildly run to run: 8%-83% failures      <- too noisy to use
#   N=8  49/171 (29%), slowest ok 12.1s, board worst 1.3s
#
# Usage: ./load_amplifier.sh [concurrency=8] [seconds=60]   env: BOARD_IP, PORT, OUT
set -u
N=${1:-8}; DUR=${2:-60}
source "$(cd "$(dirname "$0")" && pwd)/_common.sh"

PS1W=$(stage_lan_probe) || exit 1
echo "== ${N} concurrent pollers, ${DUR}s, both lanes at once =="

adb shell "cat > /tmp/conc.sh" <<EOF
for n in \$(seq 1 $N); do
 ( ok=0; bad=0; slow=0; end=\$(( \$(date +%s) + $DUR ))
   while [ \$(date +%s) -lt \$end ]; do
     t=\$(curl -s -m 20 -o /dev/null -w "%{http_code} %{time_total}" http://127.0.0.1:$PORT/nodes)
     [ "\${t%% *}" = "200" ] && ok=\$((ok+1)) || bad=\$((bad+1))
     slow=\$(echo "\${t##* } \$slow" | awk '{print (\$1>\$2)?\$1:\$2}')
     sleep 1
   done
   echo "  local#\$n  ok=\$ok fail=\$bad slowest=\${slow}s" ) &
done
wait
EOF
adb shell 'bash /tmp/conc.sh' > "$OUT/local.out" 2>&1 &
CTRL=$!
for n in $(seq 1 "$N"); do
  ( cd /mnt/c && powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$PS1W" \
      -Dur "$DUR" -BoardIp "$BOARD_IP" -Port "$PORT" ) > "$OUT/load$n.csv" 2>&1 &
done
wait $CTRL; wait

lan_ok=0; lan_bad=0; lan_slow=0
for n in $(seq 1 "$N"); do
  lan_ok=$(( lan_ok + $(grep -c ',ok,'   "$OUT/load$n.csv") ))
  lan_bad=$(( lan_bad + $(grep -c ',FAIL,' "$OUT/load$n.csv") ))
  s=$(grep ',ok,' "$OUT/load$n.csv" | cut -d, -f3 | tr -d ' ' | sort -g | tail -1)
  lan_slow=$(echo "${s:-0} $lan_slow" | awk '{print ($1>$2)?$1:$2}')
done
loc_bad=$(awk -F'fail=' '{split($2,a," "); s+=a[1]} END{print s+0}' "$OUT/local.out")

echo; echo "BOARD localhost:"; cat "$OUT/local.out"
echo; echo "LAN over WiFi:   ok=$lan_ok fail=$lan_bad slowest_ok=${lan_slow}s"
echo
if [ "$lan_bad" -gt 0 ] && [ "$loc_bad" -eq 0 ]; then
  echo "LAN degraded, server did not. Consistent with a link fault."
elif [ "$loc_bad" -gt 0 ]; then
  echo "Server degraded too -- not the link. Debug the app."
else
  echo "Nothing caught at N=$N. Retry higher: ./load_amplifier.sh 16"
fi
