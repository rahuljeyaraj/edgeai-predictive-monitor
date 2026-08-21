"""Runs ON the UNO Q. Same question as board_probe.py, but per node instead
of collapsed to a max().

board_probe.py reports max(age) over every node, which is only useful if the
fleet shares one failure mode. It doesn't: base_station arrives over SPI and
never touches WiFi, the two real satellites cross the air once (node->AP->
board), and the sim nodes cross it from the dev PC. One sim node going quiet
therefore produces a "the app's own view went stale" verdict that says
nothing about the app.

Output is one row per node per tick, so a stale episode can be attributed to
a node rather than to the fleet.
"""
import json, sys, time, urllib.request

DUR = int(sys.argv[1]) if len(sys.argv) > 1 else 120
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8080
BASE = f"http://127.0.0.1:{PORT}"

print("epoch,node,age,status")
end = time.time() + DUR
while time.time() < end:
    tick = time.time()
    try:
        with urllib.request.urlopen(BASE + "/nodes", timeout=3.0) as r:
            nodes = json.load(r)
        now = time.time()
        for node_id, v in nodes.items():
            ls = v.get("last_seen")
            age = f"{now - ls:.1f}" if ls is not None else ""
            print(f"{tick:.0f},{node_id},{age},{v.get('status','')}", flush=True)
    except Exception as exc:
        print(f"{tick:.0f},,,ERR:{type(exc).__name__}", flush=True)
    time.sleep(max(0, 1 - (time.time() - tick)))
