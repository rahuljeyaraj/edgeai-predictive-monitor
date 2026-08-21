import sys, os, re
d = sys.argv[1]

def rows(name):
    p = os.path.join(d, name)
    if not os.path.exists(p): return []
    out = []
    for line in open(p, errors="replace").read().splitlines():
        line = line.strip().lstrip("﻿")
        if not line or line.startswith("epoch"): continue
        out.append(line.split(","))
    return out

b = rows("board.csv")
ok_b = [r for r in b if len(r) > 4 and r[1] == "200"]
worst_age = max((float(r[3]) for r in ok_b if r[3]), default=-1)
off = max((int(r[4]) for r in ok_b if r[4]), default=0)
spi_age = max((float(r[5]) for r in ok_b if len(r) > 5 and r[5]), default=-1)
fok = [int(r[6]) for r in ok_b if len(r) > 6 and r[6].isdigit()]
aerr = [int(r[7]) for r in ok_b if len(r) > 7 and r[7].isdigit()]
oerr = [int(r[8]) for r in ok_b if len(r) > 8 and r[8].isdigit()]

l = rows("lan.csv")
ok_l = [r for r in l if len(r) > 2 and r[1] == "ok"]
bad_l = [r for r in l if len(r) > 2 and r[1] != "ok"]
slow = [float(r[2].replace(",", "")) for r in ok_l]

ping = open(os.path.join(d, "ping.txt"), errors="replace").read() if os.path.exists(os.path.join(d, "ping.txt")) else ""
loss = re.findall(r"\((\d+)% loss\)", ping)
rtt  = re.findall(r"Minimum = (\d+)ms, Maximum = (\d+)ms, Average = (\d+)ms", ping)

print()
print(f"BOARD  app itself   : {len(ok_b)}/{len(b)} ok   worst last_seen age {worst_age:.1f}s   "
      f"nodes past 10s rule: {off}")
# The SPI node is the control: it is the only one whose staleness cannot be
# blamed on WiFi, so it -- not the fleet max -- decides whether the app is at
# fault. frames_ok below is a further reason not to read the max as a verdict
# on the app: it counts the SPI lane only, so it can climb happily through a
# window in which every MQTT node went quiet.
print(f"                      base_station (SPI, no WiFi): worst age {spi_age:.1f}s")
if fok:
    print(f"                      frames_ok +{fok[-1]-fok[0]}  arm_errors +{(aerr[-1]-aerr[0]) if aerr else '?'}"
          f"  on_frame_errors +{(oerr[-1]-oerr[0]) if oerr else '?'}")
print(f"LAN    over WiFi    : {len(ok_l)}/{len(l)} ok   {len(bad_l)} fail/timeout   "
      f"slowest ok {max(slow) if slow else 0:.1f}s")
if loss:
    print(f"PING   PC->board    : {loss[0]}% loss" + (f"   rtt min/avg/max {rtt[0][0]}/{rtt[0][2]}/{rtt[0][1]} ms" if rtt else ""))
if len(loss) > 1:
    print(f"PING   PC->router   : {loss[1]}% loss" + (f"   rtt min/avg/max {rtt[1][0]}/{rtt[1][2]}/{rtt[1][1]} ms" if len(rtt) > 1 else ""))

print()
# Deliberately NOT `off == 0`: that counts twelve WiFi-borne nodes, and a
# WiFi outage trips it while the app is working perfectly. See node_isolate.sh
# -- a measured run had all 12 WiFi nodes stale, the SPI node never, and the
# app applying every frame within 0.7s of the broker delivering it.
board_clean = len(b) and len(ok_b) == len(b) and (spi_age < 0 or spi_age <= 10)
lan_dirty = len(l) and len(bad_l) > 0
if board_clean and lan_dirty:
    print("VERDICT: LINK. The app never lost a node; the PC could not reach it.")
elif not board_clean:
    print("VERDICT: BOARD. Even the SPI node went stale -- debug ingest, not WiFi.")
elif off:
    print(f"VERDICT: UPSTREAM. The SPI node stayed fresh while {off} WiFi-borne "
          "node(s) went stale.\n         Run ./node_isolate.sh to name them and "
          "to separate missing frames from late ones.")
else:
    print("VERDICT: nothing caught this run. Re-run longer.")
