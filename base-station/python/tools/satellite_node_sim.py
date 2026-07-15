#!/usr/bin/env python3
"""
Standalone MQTT satellite-node simulator: mimics one real ESP32 satellite
node (docs/Appendix_B_Wire_Protocol_Specification.md S3) closely enough to
exercise the real base station end-to-end (mpu/main.py --mqtt-host) without
real hardware -- replacing the old in-process `mpu/main.py --simulate`
fleet, which drove fake nodes directly through Registry/CommissioningController
calls instead of speaking the wire protocol.

Run one copy per fake node you want, each with its own --ui-port (or let it
auto-pick one -- printed on startup):
    python3 mpu/tools/satellite_node_sim.py --mqtt-host localhost --data-dir ~/kaggle_vibration --ui-port 9101
    python3 mpu/tools/satellite_node_sim.py --mqtt-host localhost --data-dir ~/kaggle_vibration --ui-port 9102
    ...

Then open http://localhost:<ui-port>/ per copy to:
  - flip it online/offline (offline = no MQTT traffic at all, so the node
    goes stale/"Offline" on the real dashboard after its existing 30s rule,
    frontend/app.js's OFFLINE_AFTER_S)
  - independently enable Mic / Accel and pick which file under --data-dir
    each one loops over
  - watch its status LED (const/breathing/strobing, colored per status)
    update live -- pushed by the base station over MQTT
    (STATUS_LED, epm/<node_id>/cmd) whenever this node's dashboard status
    changes, never polled from here.

Dataset files: download docs.kaggle.com/.../vibration-based-fault-diagnosis-
of-machines yourself and point --data-dir at the extracted folder. The
loader below (load_signal()) is a best-effort generic reader -- it flattens
every numeric value in a file into one 1-D signal, since the exact column
layout can't be verified without the files in hand. Adjust load_signal() if
your files need a specific column selected instead.

Wire format: binary, the same spectrum_fused_payload/display_rgb_payload
struct codec UART uses (mpu/common/wire_protocol.py), wrapped in a lean
[TYPE: 1B][PAYLOAD] envelope instead of UART's SYNC/LEN/CRC16 frame --
see mqtt_subscriber.py/mqtt_publisher.py's docstrings. This replaced an
earlier JSON envelope carrying sparse top-N FFT peaks, which both capped
the transmitted spectrum (a fixed peak count regardless of how much of
the spectrum actually mattered) and cost far more bytes per bin than a
packed float32 array -- there's no dual JSON/binary path here since no
real satellite-node firmware exists yet to require a transition.

Not a production ingestion service, same spirit as mpu/tools/
spectrum_server.py: no auth, no TLS, stdlib http.server rather than the
main app's FastAPI, each node an independent process. paho-mqtt and numpy
are the only third-party dependencies (both already used elsewhere in
mpu/), no extra install beyond what mqtt_subscriber.py already needs
(docs/appendix-mcu-mpu-channel.md S6: `sudo apt-get install -y
python3-paho-mqtt`).
"""
import argparse
import json
import os
import random
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import paho.mqtt.client as mqtt

# Self-contained sys.path insertion (mirrors mpu/tools/
# autoencoder_offline_eval.py) so `python3 mpu/tools/satellite_node_sim.py`
# works directly per this file's own run examples above, without the
# caller having to set PYTHONPATH the way the on-device scripts document.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "common"))
from wire_protocol import (  # noqa: E402
    ChannelSpectrum,
    LED_MODE_FROM_INT,
    MqttMsgType,
    decode_display_rgb_payload,
    decode_mqtt_message,
    encode_mqtt_message,
    encode_spectrum_fused_payload,
    rgb_int_to_hex,
)

DATA_TOPIC_FMT = "epm/{node_id}/data"
CMD_TOPIC_FMT = "epm/{node_id}/cmd"

CHANNEL_NAMES = ("accel", "mic")
DEFAULT_SAMPLE_RATE_HZ = 12800.0  # matches ACCEL_FS_HZ in spectrum_server.py
DEFAULT_WINDOW_SIZE = 1024
DEFAULT_PUBLISH_INTERVAL_S = 0.2  # matches the old simulate.py's cadence

_DEFAULT_LED = {"rgb": "#4d4d4d", "mode": "const", "period_ms": 0}


# ---------------------------------------------------------------------
# Dataset loading + FFT peak extraction
# ---------------------------------------------------------------------

_signal_cache = {}
_signal_cache_lock = threading.Lock()


def load_signal(path: str) -> np.ndarray:
    """Reader matched to the actual Kaggle vibration-fault-diagnosis
    dataset's file shape: a free-form metadata header (Com/Node/SN/
    Firmware/Time/Units -- the latter containing a non-UTF-8 "m/s²"
    byte) followed by "<index>,<amplitude>" data rows. np.genfromtxt can't
    handle this directly (choppy encoding, ragged header row lengths), so
    this reads line-by-line instead: a line's last comma-separated field
    is kept as one sample whenever it parses as a float, which naturally
    skips every header line (dates, serial numbers, channel names -- none
    of those parse as a bare float) while keeping just the amplitude
    column. Rare false positives from a header value that happens to
    parse as a float too (e.g. a calibration offset) are one-sample noise
    against ~4000 real samples per file -- negligible."""
    with _signal_cache_lock:
        cached = _signal_cache.get(path)
    if cached is not None:
        return cached

    samples = []
    with open(path, encoding="latin-1", errors="replace") as f:
        for line in f:
            field = line.rsplit(",", 1)[-1].strip()
            try:
                samples.append(float(field))
            except ValueError:
                continue

    arr = np.asarray(samples, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        raise ValueError(f"no numeric data found in {path!r}")

    with _signal_cache_lock:
        _signal_cache[path] = arr
    return arr


def compute_spectrum(window: np.ndarray, sample_rate_hz: float) -> ChannelSpectrum:
    """Full dense FFT magnitude spectrum -- one magnitude per bin, bin 0
    (DC) discarded (matches the UART path's convention: mic_fft_magnitude()/
    accel_fft_magnitude() on the MCU side never store the DC bin either).
    No top-N peak-picking: the wire format now carries the whole spectrum,
    not a bandwidth-motivated sparse subset."""
    fft_size = len(window)
    spectrum = np.abs(np.fft.rfft(window * np.hanning(fft_size)))
    bins = tuple(float(m) for m in spectrum[1:]) if spectrum.size > 1 else ()
    return ChannelSpectrum(fs=sample_rate_hz, fft_size=fft_size, bins=bins)


def spectrum_freqs(spectrum: ChannelSpectrum):
    """Frequency for each entry of a ChannelSpectrum's bins -- bin i is
    FFT bin (i+1) (DC discarded, see compute_spectrum), at frequency
    (i+1) * fs / fft_size. UI-only (drawing the local plot); not part of
    the wire payload, which carries fs/fft_size instead so the receiver
    computes this itself (mpu/tools/spectrum_server.py's bin_freqs())."""
    return [(i + 1) * spectrum.fs / spectrum.fft_size for i in range(len(spectrum.bins))]


def _spectrum_for_ui(spectrum) -> dict:
    """{"freq": [...], "mag": [...]} for the /state JSON response the web
    UI polls -- flat arrays rather than a list of {freq,mag} objects
    (cheaper to serialize for a ~500-bin dense spectrum than the old
    sparse peaks list ever needed to be)."""
    if spectrum is None or not spectrum.bins:
        return {"freq": [], "mag": []}
    return {"freq": spectrum_freqs(spectrum), "mag": list(spectrum.bins)}


class ChannelState:
    """One sensor channel's file selection + read position. A file shorter
    than window_size is tiled up front so next_window() never has to wrap
    more than once per call."""

    def __init__(self, name: str, sample_rate_hz: float, window_size: int):
        self.name = name
        self.sample_rate_hz = sample_rate_hz
        self.window_size = window_size
        self.enabled = False
        self.file_path = None
        self.selected_name = None  # the value the UI's <select> had picked, e.g. "Wear/Wear_Z/M(2).csv"
        self._signal = None
        self._pos = 0
        self._lock = threading.Lock()

    def set_file(self, path) -> None:
        if path is None:
            with self._lock:
                self.file_path = None
                self._signal = None
                self._pos = 0
            return
        signal = load_signal(path)
        if signal.size < self.window_size:
            reps = -(-self.window_size // signal.size)  # ceil division
            signal = np.tile(signal, reps)
        with self._lock:
            self.file_path = path
            self._signal = signal
            self._pos = 0

    def next_window(self):
        with self._lock:
            if self._signal is None:
                return None
            sig, start = self._signal, self._pos
            end = start + self.window_size
            if end <= sig.size:
                window = sig[start:end]
                self._pos = end % sig.size
            else:
                window = np.concatenate([sig[start:], sig[:end - sig.size]])
                self._pos = end - sig.size
            return window


# ---------------------------------------------------------------------
# Node: MQTT pub/sub + publish loop
# ---------------------------------------------------------------------

class SatelliteNode:
    def __init__(self, node_id: str, data_dir: str, mqtt_host: str, mqtt_port: int,
                 sample_rate_hz: float, window_size: int, publish_interval_s: float):
        self.node_id = node_id
        self.data_dir = data_dir

        self.online = False
        self.led = dict(_DEFAULT_LED)
        self.publish_interval_s = publish_interval_s
        self.channels = {name: ChannelState(name, sample_rate_hz, window_size)
                          for name in CHANNEL_NAMES}
        self.last_spectrum = {}  # name -> most recently published ChannelSpectrum, for the UI's FFT plot

        self._state_lock = threading.Lock()
        self._stop_event = threading.Event()

        self._mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"satellite-sim-{node_id}")
        self._mqtt.on_connect = self._on_connect
        self._mqtt.on_message = self._on_message
        self._mqtt.connect(mqtt_host, mqtt_port)
        self._mqtt.loop_start()

        self._publish_thread = threading.Thread(target=self._publish_loop, daemon=True)
        self._publish_thread.start()

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        client.subscribe(CMD_TOPIC_FMT.format(node_id=self.node_id), qos=1)

    def _on_message(self, client, userdata, msg) -> None:
        try:
            msg_type, body = decode_mqtt_message(msg.payload)
        except ValueError:
            return
        if msg_type != MqttMsgType.STATUS_LED:
            return
        try:
            rgb, mode, period_ms = decode_display_rgb_payload(body)
        except struct.error:
            return
        with self._state_lock:
            self.led = {
                "rgb": rgb_int_to_hex(rgb),
                "mode": LED_MODE_FROM_INT.get(mode, self.led["mode"]),
                "period_ms": period_ms,
            }
            led = dict(self.led)
        print(f"[{self.node_id}] STATUS_LED received: rgb={led['rgb']} mode={led['mode']} "
              f"period_ms={led['period_ms']}", flush=True)

    def list_files(self):
        """Recurses under data_dir (the Kaggle dataset extracts as
        <fault_type>/<fault_type>_<axis>/M(n).csv, not flat) and returns
        paths relative to data_dir, e.g. "Wear/Wear_Z/M(2).csv". Only
        ".csv" entries -- excludes the dataset's .mat files (unparsed by
        load_signal) and the stray "*.csv:Zone.Identifier" marker files
        Windows leaves behind when a folder is copied over from the
        Windows side of WSL."""
        matches = []
        for root, _dirs, files in os.walk(self.data_dir):
            for name in files:
                if name.lower().endswith(".csv"):
                    matches.append(os.path.relpath(os.path.join(root, name), self.data_dir))
        return sorted(matches)

    def set_online(self, online: bool) -> None:
        with self._state_lock:
            self.online = online

    def set_channel(self, name: str, enabled: bool, file_name) -> None:
        channel = self.channels[name]
        path = os.path.join(self.data_dir, file_name) if file_name else None
        channel.set_file(path)
        channel.selected_name = file_name
        channel.enabled = enabled

    def set_publish_interval(self, seconds: float) -> None:
        seconds = max(0.02, min(5.0, float(seconds)))
        with self._state_lock:
            self.publish_interval_s = seconds

    def _publish_loop(self) -> None:
        while True:
            with self._state_lock:
                interval = self.publish_interval_s
            if self._stop_event.wait(interval):
                return

            with self._state_lock:
                online = self.online
            if not online:
                continue

            channels_payload = {}
            for name, channel in self.channels.items():
                if not channel.enabled:
                    continue
                window = channel.next_window()
                if window is None:
                    continue
                spectrum = compute_spectrum(window, channel.sample_rate_hz)
                channels_payload[name] = spectrum
                with self._state_lock:
                    self.last_spectrum[name] = spectrum
            if not channels_payload:
                continue

            payload = encode_spectrum_fused_payload(
                mic=channels_payload.get("mic"), accel=channels_payload.get("accel"))
            message = encode_mqtt_message(MqttMsgType.SPECTRUM, payload)
            self._mqtt.publish(DATA_TOPIC_FMT.format(node_id=self.node_id), message, qos=0)

    def state(self) -> dict:
        with self._state_lock:
            online, led = self.online, dict(self.led)
            publish_interval_s = self.publish_interval_s
            last_spectrum = dict(self.last_spectrum)
        return {
            "node_id": self.node_id,
            "online": online,
            "led": led,
            "publish_interval_ms": round(publish_interval_s * 1000),
            "channels": {
                name: {"enabled": ch.enabled,
                       "file": ch.selected_name,
                       "sample_rate_hz": ch.sample_rate_hz,
                       **_spectrum_for_ui(last_spectrum.get(name))}
                for name, ch in self.channels.items()
            },
            "files": self.list_files(),
        }

    def stop(self) -> None:
        self._stop_event.set()
        self._mqtt.loop_stop()
        self._mqtt.disconnect()


# ---------------------------------------------------------------------
# Web UI: stdlib http.server, one inline page (no build step, matching
# spectrum_server.py's convention for a debug/test tool).
# ---------------------------------------------------------------------

PAGE_HTML = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>EPM Satellite Node Simulator</title>
<style>
  body { font-family: sans-serif; background: #111; color: #eee; margin: 0; padding: 1.5rem; max-width: 480px; }
  h1 { font-size: 1.1rem; color: #9cf; font-weight: normal; }
  h2 { font-size: 0.9rem; color: #9cf; font-weight: normal; margin-top: 1.5rem; }
  #node-id { font-family: monospace; color: #fff; }
  #led {
    width: 72px; height: 72px; border-radius: 50%; margin: 1rem auto;
    box-shadow: 0 0 16px currentColor;
  }
  #led.mode-const { animation: none; opacity: 1; }
  #led.mode-breathe { animation: breathe linear infinite; }
  #led.mode-strobe { animation: strobe steps(1) infinite; }
  @keyframes breathe { 0%, 100% { opacity: 0.2; } 50% { opacity: 1; } }
  @keyframes strobe { 0%, 49% { opacity: 1; } 50%, 100% { opacity: 0.1; } }
  button { font-size: 0.9rem; padding: 0.4rem 0.8rem; cursor: pointer; }
  .channel-row { display: flex; align-items: center; gap: 0.6rem; margin: 0.4rem 0; }
  select { flex: 1; }
  .file-label { font-size: 0.75rem; color: #9f9; font-family: monospace; word-break: break-all; margin: 0.1rem 0 0.4rem 0; min-height: 1em; }
  canvas.plot { display: block; width: 100%; height: 70px; background: #000; border: 1px solid #333; }
  .rate-row { display: flex; align-items: center; gap: 0.5rem; margin: 0.4rem 0; }
  .rate-row input { width: 5rem; }
</style>
</head>
<body>
  <h1>Satellite Node <span id="node-id">...</span></h1>
  <div id="led" class="mode-const"></div>
  <div style="text-align:center"><button id="online-btn" onclick="toggleOnline()">...</button></div>

  <h2>Publish Rate</h2>
  <div class="rate-row">
    <input type="number" id="interval-ms" min="20" max="5000" step="10" onchange="updateRate()">
    <span>ms per frame</span>
  </div>

  <h2>Accelerometer</h2>
  <div class="channel-row">
    <input type="checkbox" id="accel-enabled" onchange="updateChannel('accel')">
    <select id="accel-file" onchange="updateChannel('accel')"></select>
  </div>
  <div class="file-label" id="accel-file-label"></div>
  <canvas class="plot" id="accel-plot" width="440" height="70"></canvas>

  <h2>Microphone</h2>
  <div class="channel-row">
    <input type="checkbox" id="mic-enabled" onchange="updateChannel('mic')">
    <select id="mic-file" onchange="updateChannel('mic')"></select>
  </div>
  <div class="file-label" id="mic-file-label"></div>
  <canvas class="plot" id="mic-plot" width="440" height="70"></canvas>

<script>
let online = false;

function renderFileOptions(select, files, current) {
  const have = Array.from(select.options).map(o => o.value);
  if (have.join(",") !== files.join(",")) {
    select.innerHTML = '<option value="">(none)</option>' +
      files.map(f => `<option value="${f}">${f}</option>`).join("");
  }
  select.value = current || "";
}

function drawSpectrum(canvas, freq, mag, nyquistHz) {
  const ctx = canvas.getContext("2d");
  const w = canvas.width, h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  if (!freq || freq.length === 0 || !nyquistHz) return;
  const maxMag = Math.max(...mag, 1e-9);
  const barW = Math.max(2, w / freq.length - 1);
  ctx.fillStyle = "#4ade80";
  for (let i = 0; i < freq.length; i++) {
    const x = Math.min(w - barW, (freq[i] / nyquistHz) * w);
    const barH = Math.max(1, (mag[i] / maxMag) * (h - 4));
    ctx.fillRect(x, h - barH, barW, barH);
  }
}

async function poll() {
  try {
    const r = await fetch("/state");
    const s = await r.json();
    document.getElementById("node-id").textContent = s.node_id;

    online = s.online;
    document.getElementById("online-btn").textContent = online ? "Go Offline" : "Go Online";

    const led = document.getElementById("led");
    led.style.backgroundColor = s.led.rgb;
    led.style.color = s.led.rgb;
    led.className = "mode-" + s.led.mode;
    led.style.animationDuration = (s.led.period_ms || 1000) + "ms";

    const rateInput = document.getElementById("interval-ms");
    if (document.activeElement !== rateInput) {
      rateInput.value = s.publish_interval_ms;
    }

    for (const name of ["accel", "mic"]) {
      const ch = s.channels[name];
      document.getElementById(name + "-enabled").checked = ch.enabled;
      renderFileOptions(document.getElementById(name + "-file"), s.files, ch.file);
      document.getElementById(name + "-file-label").textContent = ch.file ? ch.file : "(no file selected)";
      drawSpectrum(document.getElementById(name + "-plot"), ch.freq, ch.mag, ch.sample_rate_hz / 2);
    }
  } catch (e) {
    document.getElementById("node-id").textContent = "(fetch error: " + e + ")";
  }
}

async function updateRate() {
  const ms = parseInt(document.getElementById("interval-ms").value, 10);
  if (!Number.isFinite(ms)) return;
  await fetch("/rate", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({interval_ms: ms}),
  });
  poll();
}

async function toggleOnline() {
  await fetch("/online", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({online: !online}),
  });
  poll();
}

async function updateChannel(name) {
  const enabled = document.getElementById(name + "-enabled").checked;
  const file = document.getElementById(name + "-file").value;
  await fetch("/channel", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({channel: name, enabled, file}),
  });
  poll();
}

setInterval(poll, 700);
poll();
</script>
</body>
</html>
"""


def build_httpd(ui_host: str, ui_port: int):
    """Binds immediately (so the caller can read the OS-assigned port when
    ui_port=0), before the SatelliteNode it'll serve even exists -- routes
    look the node up through `node_holder` at request time instead of
    capturing it at construction time."""
    node_holder = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # quiet by default, matches spectrum_server.py

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
            elif self.path == "/state":
                self._send_json(node_holder["node"].state())
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                self._send_json({"error": "bad json"}, status=400)
                return

            node = node_holder["node"]
            if self.path == "/online":
                node.set_online(bool(body.get("online")))
            elif self.path == "/channel":
                channel = body.get("channel")
                if channel not in CHANNEL_NAMES:
                    self._send_json({"error": f"unknown channel {channel!r}"}, status=400)
                    return
                node.set_channel(channel, bool(body.get("enabled")), body.get("file") or None)
            elif self.path == "/rate":
                interval_ms = body.get("interval_ms")
                if not isinstance(interval_ms, (int, float)):
                    self._send_json({"error": "missing/invalid interval_ms"}, status=400)
                    return
                node.set_publish_interval(interval_ms / 1000.0)
            else:
                self.send_response(404)
                self.end_headers()
                return
            self._send_json(node.state())

    httpd = ThreadingHTTPServer((ui_host, ui_port), Handler)
    return httpd, node_holder


def load_or_create_node_id(state_file: str) -> str:
    if os.path.exists(state_file):
        with open(state_file) as f:
            node_id = json.load(f).get("node_id")
        if node_id:
            return node_id
    # 6 lowercase hex chars, matching the MAC-derived id convention real
    # satellite nodes use (Appendix B S3, e.g. "a4cf12").
    node_id = "".join(random.choices("0123456789abcdef", k=6))
    with open(state_file, "w") as f:
        json.dump({"node_id": node_id}, f)
    return node_id


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mqtt-host", required=True)
    parser.add_argument("--mqtt-port", type=int, default=1883)
    parser.add_argument("--data-dir", required=True,
                         help="Folder of manually-downloaded Kaggle vibration files")
    parser.add_argument("--ui-host", default="0.0.0.0")
    parser.add_argument("--ui-port", type=int, default=0,
                         help="0 (default) = OS picks a free port, printed on startup")
    parser.add_argument("--node-id", default=None,
                         help="Override the persisted/random node id")
    parser.add_argument("--state-file", default=None,
                         help="Where to persist this copy's node id across restarts "
                              "(default: next to this script, keyed by the UI port)")
    parser.add_argument("--sample-rate-hz", type=float, default=DEFAULT_SAMPLE_RATE_HZ)
    parser.add_argument("--window-size", type=int, default=DEFAULT_WINDOW_SIZE)
    parser.add_argument("--publish-interval-s", type=float, default=DEFAULT_PUBLISH_INTERVAL_S)
    args = parser.parse_args()

    if not os.path.isdir(args.data_dir):
        parser.error(f"--data-dir {args.data_dir!r} is not a directory")

    httpd, node_holder = build_httpd(args.ui_host, args.ui_port)
    ui_port = httpd.server_port

    state_file = args.state_file or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), f".satellite_node_{ui_port}.json")
    node_id = args.node_id or load_or_create_node_id(state_file)

    node = SatelliteNode(node_id, args.data_dir, args.mqtt_host, args.mqtt_port,
                          args.sample_rate_hz, args.window_size, args.publish_interval_s)
    node_holder["node"] = node

    print(f"Node {node_id}: UI at http://localhost:{ui_port}/ "
          f"(publishing to MQTT {args.mqtt_host}:{args.mqtt_port} when online)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        node.stop()
        httpd.shutdown()


if __name__ == "__main__":
    main()
