"""Runs ON the UNO Q. Once a second, asks the app the same question the
dashboard asks -- GET /nodes -- and reports whether the *app* would have
shown anything offline. Nothing here crosses WiFi, so a clean run here
plus a dirty run from the PC localises the fault to the link."""
import json, sys, time, urllib.request

DUR = int(sys.argv[1]) if len(sys.argv) > 1 else 120
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8080
BASE = f"http://127.0.0.1:{PORT}"
OFFLINE_AFTER_S = 10  # mirror of frontend/app.js
# The one node that arrives over SPI and never crosses WiFi (mirror of
# ingestion/sensor_frame.py's BASE_STATION_NODE_ID). Reported separately
# because a max() over every node is dominated by the twelve that do cross
# WiFi -- see node_isolate.sh.
SPI_NODE = "base_station"

def get(path, timeout=3.0):
    t0 = time.time()
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        body = json.load(r)
    return r.status, (time.time() - t0) * 1000, body

print("epoch,http,ms,worst_age,offline,spi_age,frames_ok,arm_err,onframe_err")
end = time.time() + DUR
while time.time() < end:
    tick = time.time()
    try:
        code, ms, nodes = get("/nodes")
        now = time.time()
        ages = {k: now - v["last_seen"] for k, v in nodes.items()
                if v.get("last_seen") is not None}
        worst = max(ages.values()) if ages else -1
        offline = sum(1 for a in ages.values() if a > OFFLINE_AFTER_S)
        spi_age = ages.get(SPI_NODE, -1)
    except Exception as exc:
        print(f"{tick:.0f},ERR,,,,,,,{type(exc).__name__}", flush=True)
        time.sleep(max(0, 1 - (time.time() - tick)))
        continue
    try:
        _, _, perf = get("/perf")
        ing = perf.get("ingest", {})
        fok, aerr, oerr = ing.get("frames_ok"), ing.get("arm_errors"), ing.get("on_frame_errors")
    except Exception:
        fok = aerr = oerr = ""
    print(f"{tick:.0f},{code},{ms:.0f},{worst:.1f},{offline},{spi_age:.1f},{fok},{aerr},{oerr}", flush=True)
    time.sleep(max(0, 1 - (time.time() - tick)))
