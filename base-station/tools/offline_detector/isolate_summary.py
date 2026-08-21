"""Merges node_isolate.sh's lanes into a per-node table.

The question each row answers is not "was this node offline" -- the app
already says that -- but "did its frames stop arriving, or did they arrive
and get applied late". Those are the same symptom on screen and the same
symptom in board_probe.py's max(), and they are different bugs.
"""
import json, os, re, sys

d = sys.argv[1]
STALE_S = 10          # mirror of frontend/app.js OFFLINE_AFTER_S
TOL_S = 3             # registry ages are sampled at 1Hz; don't split hairs


def read(name):
    p = os.path.join(d, name)
    return open(p, errors="replace").read() if os.path.exists(p) else ""


pc_nodes = {n.strip() for n in read("pc_nodes.txt").splitlines() if n.strip()}

# What the app believed, per node per tick.
ages = {}
for line in read("nodes.csv").splitlines():
    line = line.strip().lstrip("﻿")
    if not line or line.startswith("epoch"):
        continue
    parts = line.split(",")
    if len(parts) < 3 or not parts[1] or not parts[2]:
        continue
    ages.setdefault(parts[1], []).append((int(parts[0]), float(parts[2])))

# What actually reached the broker, per node.
arrivals = {}
for line in read("broker.csv").splitlines():
    m = re.match(r"([\d.]+)\s+epm/([^/]+)/data", line.strip())
    if m:
        arrivals.setdefault(m.group(2), []).append(float(m.group(1)))


def worst_gap(ts, t0, t1):
    """Longest silence, counting the edges of the observation window.

    Consecutive differences alone miss the case that matters most here: a
    node that delivers a short burst and is otherwise silent for the whole
    run scores a tiny gap, because the silence sits before its first message
    or after its last one rather than between two of them. Two real
    satellites scored 2.3s and 20.8s that way on 10 and 20 messages in 120s,
    which read as "the frames arrived" when almost none of them had.
    """
    if not ts:
        return t1 - t0
    marks = [t0] + ts + [t1]
    return max(b - a for a, b in zip(marks, marks[1:]))


def worst_lag(series, ts):
    """How far behind the broker the app's own view ever got, in seconds.

    Both lanes subscribe to the same broker on the same box, so at any tick
    the newest message the subscriber has seen is the newest the app could
    possibly have applied. `tick - age` is when the app thinks it last heard
    from the node; the difference between the two is time the frame spent
    inside the app rather than on the air.

    This is what the worst-age-vs-worst-gap comparison was reaching for, but
    it holds at the edges of the window too: it never asks about silence it
    could not observe, only about messages it watched arrive.
    """
    lag, i = 0.0, 0
    for tick, age in series:
        while i < len(ts) and ts[i] <= tick:
            i += 1
        if i == 0:                     # nothing seen yet this run
            continue
        lag = max(lag, ts[i - 1] - (tick - age))
    return lag


def stale_runs(series):
    """(count, longest) of consecutive ticks over the offline threshold."""
    runs, cur = [], 0
    for _, age in series:
        if age > STALE_S:
            cur += 1
        elif cur:
            runs.append(cur)
            cur = 0
    if cur:
        runs.append(cur)
    return len(runs), max(runs, default=0)


# Observation window, taken from the app lane (the broker lane starts and
# stops with it).
ticks = [t for series in ages.values() for t, _ in series]
T0, T1 = (min(ticks), max(ticks)) if ticks else (0, 0)

rows = []
for node, series in ages.items():
    ts = arrivals.get(node, [])
    max_age = max(a for _, a in series)
    n_runs, longest = stale_runs(series)
    gap = worst_gap(ts, T0, T1) if ts else 0.0
    lag = worst_lag(series, ts)
    if not ts:
        kind = "spi"            # never appeared on the broker at all
    elif node in pc_nodes:
        kind = "sim/PC-WiFi"
    else:
        kind = "satellite"
    if max_age <= STALE_S:
        why = "-"
    elif not ts:
        why = "no broker traffic (not an MQTT node)"
    elif lag > TOL_S:
        why = f"FRAMES ARRIVED, APP LATE by {lag:.0f}s"
    else:
        why = "FRAMES MISSING -- nothing arrived either"
    rows.append((node, kind, max_age, n_runs, longest, len(ts), gap, lag, why))

rows.sort(key=lambda r: -r[2])

print()
win = max(T1 - T0, 1)
print(f"{'node':<14}{'path':<14}{'worst age':>10}{'stale runs':>12}{'longest':>9}"
      f"{'msgs':>7}{'fps':>7}{'worst gap':>11}{'app lag':>9}  attribution")
for node, kind, max_age, n_runs, longest, n_msgs, gap, lag, why in rows:
    print(f"{node:<14}{kind:<14}{max_age:>9.1f}s{n_runs:>12}{longest:>8}s"
          f"{n_msgs:>7}{n_msgs / win:>7.1f}{gap:>10.1f}s{lag:>8.1f}s  {why}")

# The control: the one node that cannot be blamed on WiFi.
ctl = [r for r in rows if r[1] == "spi"]
wifi_stale = [r for r in rows if r[1] != "spi" and r[2] > STALE_S]
missing = [r for r in rows if r[8].startswith("FRAMES MISSING")]
late = [r for r in rows if r[8].startswith("FRAMES ARRIVED")]

for tag, name in (("before", "perf_before.json"), ("after", "perf_after.json")):
    try:
        p = json.loads(read(name))
    except Exception:
        continue
    s = p.get("system", {})
    by = s.get("ingest_fps_by_transport", {})
    print(f"\nperf {tag:<7}: {s.get('frames_per_sec', 0):.1f} fps total "
          f"(mqtt {by.get('mqtt', 0):.1f}, spi {by.get('spi_link', 0):.1f})  "
          f"cpu {s.get('process_cpu_percent', 0):.0f}%  "
          f"falling_behind {s.get('falling_behind_count', '?')}")

loss = re.findall(r"\((\d+)% loss\)", read("ping.txt"))
if loss:
    print(f"ping PC->board : {loss[0]}% loss")

print()
if ctl and ctl[0][2] > STALE_S:
    print("VERDICT: APP. The SPI node went stale too, and its frames never "
          "touch WiFi.")
elif late:
    print("VERDICT: APP LAG. Frames reached the broker during the stale "
          "window and the app applied them late: "
          + ", ".join(r[0] for r in late))
elif wifi_stale:
    kinds = {r[1] for r in missing}
    print("VERDICT: UPSTREAM. Only WiFi-borne nodes went stale and their "
          "frames never reached the broker" + (f" ({', '.join(sorted(kinds))})" if kinds else "")
          + "; the SPI node stayed fresh throughout, so the app is not the fault.")
else:
    print("VERDICT: nothing caught this run. Re-run longer.")
