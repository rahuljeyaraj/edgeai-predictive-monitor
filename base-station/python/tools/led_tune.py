#!/usr/bin/env python3
"""Live RGB/brightness tuner for the STATUS_LED rings, pushed straight to
real hardware, bypassing registry/led_keeper.py entirely -- for eyeballing
a color on both rings side by side while picking new palette values
(registry/status_color.py), since the base station's ring and a
satellite's ring are known to render the same hex differently (see that
module's docstring: full-strength yellow read light green on this
hardware).

Two independent pushes, same as the real system uses:
  - Base station's own ring: arduino.app_utils.Bridge's `set_rgb` RPC
    (main.py's wire_local_status_led). Only reachable from inside the App
    Lab container, so run this tool there -- same requirement
    tools/raw_capture.py and tools/raw_capture_server.py already have. Safe
    to run alongside main.py: set_rgb is an infrequent, non-exclusive
    Bridge call, not the SPI streaming raw_capture_server.py's docstring
    warns about.
  - A satellite's ring: MQTT STATUS_LED command (epm/<node_id>/cmd),
    reusing ingestion/mqtt_publisher.py's real publisher. The broker lives
    on the base station itself (--mqtt-host localhost works from inside
    the container).

led_keeper.py won't fight a color pushed from here unless the node's
*registry status* actually changes while you're testing -- it only
re-pushes when the color computed from current status drifts from what it
last pushed, and it doesn't know this tool touched the ring at all.

Usage (inside the App Lab container):
    python3 tools/led_tune.py --mqtt-host localhost

Then open http://<device-ip>:<port>/ (port printed on startup) and drag.
"""
import argparse
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_HERE = os.path.dirname(os.path.abspath(__file__))
_PYTHON_DIR = os.path.join(_HERE, "..")
for _subpackage in ("common", "ingestion"):
    sys.path.insert(0, os.path.join(_PYTHON_DIR, _subpackage))

from wire_protocol import LED_MODE_TO_INT  # noqa: E402
from mqtt_publisher import MqttPublisher  # noqa: E402

# The 8 corners of the RGB cube -- every full-brightness on/off combination
# of the 3 channels -- rather than the shipped status palette, so each
# primary/secondary can be eyeballed on both rings independently of any
# particular status color.
PRESETS = [
    {"name": "Black", "rgb": "#000000"},
    {"name": "Red", "rgb": "#ff0000"},
    {"name": "Green", "rgb": "#00ff00"},
    {"name": "Blue", "rgb": "#0000ff"},
    {"name": "Yellow", "rgb": "#ffff00"},
    {"name": "Magenta", "rgb": "#ff00ff"},
    {"name": "Cyan", "rgb": "#00ffff"},
    {"name": "White", "rgb": "#ffffff"},
]


def _default_mqtt_host():
    """Mirrors main.py's _default_mqtt_host(): inside the App Lab container,
    mosquitto runs on the HOST, not in-container, so "localhost" reaches
    nothing -- the docker-bridge gateway IP (read from /proc/net/route) is
    what actually gets there. Returns None outside the container (e.g. no
    default route readable), in which case --mqtt-host must be passed
    explicitly."""
    try:
        with open("/proc/net/route") as f:
            next(f)
            for line in f:
                fields = line.split()
                if len(fields) >= 3 and fields[1] == "00000000":
                    gateway_hex = fields[2]
                    return ".".join(str(int(gateway_hex[i:i + 2], 16)) for i in (6, 4, 2, 0))
    except OSError:
        pass
    return None


def push_base_station(rgb_hex: str) -> None:
    try:
        from arduino.app_utils import Bridge
    except ImportError as exc:
        raise RuntimeError(
            "arduino.app_utils.Bridge unavailable -- run this tool inside "
            "the App Lab container, same as main.py") from exc
    Bridge.call("set_rgb", f"{rgb_hex.lstrip('#')},{LED_MODE_TO_INT['const']},0")


PAGE_HTML = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>LED Tune</title>
<style>
  body { font-family: sans-serif; background: #111; color: #eee; margin: 0;
         padding: 1.5rem; max-width: 460px; font-size: 1.05rem; }
  h1 { font-size: 1.3rem; color: #9cf; font-weight: normal; margin: 0 0 1rem 0; }
  #swatch { width: 100%; height: 96px; border-radius: 10px; border: 2px solid #444; margin-bottom: 1rem; }
  .row { display: flex; align-items: center; gap: 0.7rem; margin: 0.6rem 0; }
  .row label { width: 4.5rem; font-size: 1.05rem; }
  .row input[type=range] { flex: 1; height: 1.6rem; }
  .row input[type=number] { width: 4.5rem; font-size: 1.05rem; padding: 0.2rem; }
  #hex { font-size: 1.1rem; font-family: monospace; width: 8rem; padding: 0.3rem; }
  .presets { display: flex; flex-wrap: wrap; gap: 0.6rem; margin: 0.8rem 0 1.2rem 0; }
  .presets button { width: 2.6rem; height: 2.6rem; border-radius: 50%; border: 2px solid #444; cursor: pointer; }
  .targets { display: flex; gap: 1.5rem; margin: 1rem 0; font-size: 1.1rem; }
  .targets label { display: flex; align-items: center; gap: 0.4rem; }
  .targets input[type=checkbox] { width: 1.3rem; height: 1.3rem; }
  #node-id { font-size: 1.05rem; padding: 0.3rem; width: 100%; box-sizing: border-box; margin-top: 0.3rem; }
  .status { display: flex; gap: 1.5rem; margin-top: 1rem; font-size: 1.05rem; }
  .ok { color: #4ade80; } .err { color: #f87171; }
</style>
</head>
<body>
  <h1>LED Tune</h1>
  <div id="swatch"></div>

  <div class="presets" id="presets"></div>

  <div class="row"><label>R</label><input type="range" id="r-s" min="0" max="255"><input type="number" id="r-n" min="0" max="255"></div>
  <div class="row"><label>G</label><input type="range" id="g-s" min="0" max="255"><input type="number" id="g-n" min="0" max="255"></div>
  <div class="row"><label>B</label><input type="range" id="b-s" min="0" max="255"><input type="number" id="b-n" min="0" max="255"></div>
  <div class="row"><label>Hex</label><input type="text" id="hex"></div>
  <div class="row"><label>Bright %</label><input type="range" id="br-s" min="0" max="100"><input type="number" id="br-n" min="0" max="100"></div>

  <div class="targets">
    <label><input type="checkbox" id="t-base" checked> Base station</label>
    <label><input type="checkbox" id="t-sat" checked> Satellite</label>
  </div>
  <input type="text" id="node-id" placeholder="satellite node id (e.g. a4cf12)">

  <div class="status" id="status"></div>

<script>
const ids = ["r", "g", "b"];
let sending = null, pending = false;

function clamp255(v) { return Math.max(0, Math.min(255, Math.round(v || 0))); }
function toHex(r, g, b) { return "#" + [r, g, b].map(v => v.toString(16).padStart(2, "0")).join(""); }

function syncFromRgb() {
  const r = clamp255(+document.getElementById("r-n").value);
  const g = clamp255(+document.getElementById("g-n").value);
  const b = clamp255(+document.getElementById("b-n").value);
  document.getElementById("hex").value = toHex(r, g, b);
  render();
}

function syncFromHex() {
  const m = /^#?([0-9a-fA-F]{6})$/.exec(document.getElementById("hex").value.trim());
  if (!m) return;
  const n = parseInt(m[1], 16);
  for (const [id, shift] of [["r", 16], ["g", 8], ["b", 0]]) {
    const v = (n >> shift) & 0xff;
    document.getElementById(id + "-s").value = v;
    document.getElementById(id + "-n").value = v;
  }
  render();
}

function render() {
  const r = clamp255(+document.getElementById("r-n").value);
  const g = clamp255(+document.getElementById("g-n").value);
  const b = clamp255(+document.getElementById("b-n").value);
  const pct = Math.max(0, Math.min(100, +document.getElementById("br-n").value || 0));
  const scaled = [r, g, b].map(v => clamp255(v * pct / 100));
  document.getElementById("swatch").style.background = toHex(...scaled);
  sendDebounced(toHex(r, g, b), pct);
}

function bindPair(id) {
  const s = document.getElementById(id + "-s"), n = document.getElementById(id + "-n");
  s.oninput = () => { n.value = s.value; syncFromRgb(); };
  n.oninput = () => { s.value = n.value; syncFromRgb(); };
}
["r", "g", "b"].forEach(bindPair);
bindPair("br");
document.getElementById("hex").oninput = syncFromHex;

function sendDebounced(hex, pct) {
  pending = { hex, pct };
  if (sending) return;
  sending = setTimeout(flush, 80);
}

async function flush() {
  sending = null;
  const { hex, pct } = pending;
  const targets = [];
  if (document.getElementById("t-base").checked) targets.push("base_station");
  if (document.getElementById("t-sat").checked) targets.push("satellite");
  if (!targets.length) return;
  try {
    const resp = await fetch("/set", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        hex, brightness_pct: pct, targets,
        satellite_node_id: document.getElementById("node-id").value.trim(),
      }),
    });
    const data = await resp.json();
    document.getElementById("status").innerHTML = Object.entries(data.results || {})
      .map(([t, r]) => `<span class="${r === "ok" ? "ok" : "err"}">${t}: ${r}</span>`).join("");
  } catch (e) {
    document.getElementById("status").innerHTML = `<span class="err">${e}</span>`;
  }
}

async function loadPresets() {
  const r = await fetch("/presets");
  const presets = await r.json();
  document.getElementById("presets").innerHTML = presets.map(p =>
    `<button title="${p.name}" style="background:${p.rgb}" onclick="applyPreset('${p.rgb}')"></button>`
  ).join("");
}

function applyPreset(hex) {
  document.getElementById("hex").value = hex;
  syncFromHex();
}

document.getElementById("node-id").value = localStorage.getItem("led_tune_node_id") || "";
document.getElementById("node-id").onchange = e => localStorage.setItem("led_tune_node_id", e.target.value.trim());

document.getElementById("r-n").value = 0; document.getElementById("r-s").value = 0;
document.getElementById("g-n").value = 255; document.getElementById("g-s").value = 255;
document.getElementById("b-n").value = 0; document.getElementById("b-s").value = 0;
document.getElementById("br-n").value = 100; document.getElementById("br-s").value = 100;
loadPresets();
syncFromRgb();
</script>
</body>
</html>
"""


def build_httpd(ui_host: str, ui_port: int, mqtt_host: str, mqtt_port: int):
    mqtt_pub = MqttPublisher(mqtt_host, mqtt_port, client_id="led_tune")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _send_json(self, obj, status=200):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/":
                body = PAGE_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/presets":
                self._send_json(PRESETS)
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            if self.path != "/set":
                self.send_response(404)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                self._send_json({"error": "bad json"}, status=400)
                return

            m = re.match(r"^#?([0-9a-fA-F]{6})$", str(body.get("hex", "")))
            if not m:
                self._send_json({"error": "bad hex"}, status=400)
                return
            pct = max(0, min(100, float(body.get("brightness_pct", 100))))
            n = int(m.group(1), 16)
            scaled = tuple(round(((n >> shift) & 0xff) * pct / 100) for shift in (16, 8, 0))
            rgb_hex = "#%02x%02x%02x" % scaled

            results = {}
            targets = body.get("targets") or []
            if "base_station" in targets:
                try:
                    push_base_station(rgb_hex)
                    results["base_station"] = "ok"
                except Exception as exc:  # noqa: BLE001 - reported to the UI, not raised
                    results["base_station"] = str(exc)
            if "satellite" in targets:
                node_id = str(body.get("satellite_node_id") or "").strip()
                if not node_id:
                    results["satellite"] = "no node id"
                else:
                    try:
                        mqtt_pub.publish_status(node_id, rgb_hex, "const", 0)
                        results["satellite"] = "ok"
                    except Exception as exc:  # noqa: BLE001
                        results["satellite"] = str(exc)
            self._send_json({"rgb": rgb_hex, "results": results})

    httpd = ThreadingHTTPServer((ui_host, ui_port), Handler)
    return httpd, mqtt_pub


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mqtt-host", default=_default_mqtt_host(),
                         help="default: auto-detected docker-bridge gateway IP, matching "
                              "main.py's own default (mosquitto runs on the host, not "
                              "in-container)")
    parser.add_argument("--mqtt-port", type=int, default=1883)
    parser.add_argument("--ui-host", default="0.0.0.0")
    parser.add_argument("--ui-port", type=int, default=0,
                         help="0 (default) = OS picks a free port, printed on startup")
    args = parser.parse_args()

    if not args.mqtt_host:
        parser.error("--mqtt-host could not be auto-detected -- pass it explicitly "
                      "(e.g. --mqtt-host localhost if mosquitto is co-located)")

    httpd, mqtt_pub = build_httpd(args.ui_host, args.ui_port, args.mqtt_host, args.mqtt_port)
    ui_port = httpd.server_port
    print(f"LED tune UI at http://localhost:{ui_port}/ (Ctrl+C to stop), MQTT via {args.mqtt_host}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        mqtt_pub.stop()


if __name__ == "__main__":
    main()
