#!/usr/bin/env python3
"""
Standalone MQTT satellite-node simulator: mimics one real ESP32 satellite
node (docs/Appendix_B_Wire_Protocol_Specification.md S3) closely enough to
exercise the real base station end-to-end (mpu/main.py --mqtt-host) without
real hardware.

Streams from `.npz` raw-capture files -- the same format tools/raw_capture.py
/ tools/raw_capture_server.py write into base-station/captures/, and
tools/offline_experiment.py already reads for offline analysis: one file per
recording session, holding `accel_x_raw`/`accel_y_raw`/`accel_z_raw`
(num_windows, 1024) and `mic_raw` (num_windows, 2048) float32 arrays (each
with an `<channel>_fs` sample-rate companion) plus a `label` string. Point
--captures-dir at base-station/captures/ (or any other folder of such files,
e.g. one pulled from a different rig) to replay real recordings, or at
synthetic data from tools/gen_synthetic_captures.py.

Run one copy per fake node you want, each with its own --ui-port (or let it
auto-pick one -- printed on startup):
    python3 tools/satellite_node_sim.py --mqtt-host localhost --captures-dir ../captures --ui-port 9101
    python3 tools/satellite_node_sim.py --mqtt-host localhost --captures-dir ../captures --ui-port 9102
    ...

Then open http://localhost:<ui-port>/ per copy to:
  - flip it online/offline (offline = no MQTT traffic at all, so the node
    goes stale/"Offline" on the real dashboard after its existing 30s rule,
    frontend/app.js's OFFLINE_AFTER_S) -- starts OFFLINE, never auto-enabled
    by this file itself (see ../start_desktop_dashboard.sh if you want a
    one-shot script that flips it online for you)
  - pick which capture file under --captures-dir this node streams (all 4
    raw channels of that one recording session), toggle accel on/off (and
    fused vs per-axis when on -- see "Accel: fused vs per-axis" below) and
    mic on/off independently, and which scalar modules ride along in the
    SCALAR_SET section
  - adjust accel FFT bin count (used as-is for the fused channel, or per
    axis -- x3 total across accel_x/y/z -- in per-axis mode) and mic FFT
    bin count, both freely adjustable at any time, never locked
  - watch its status LED (const/breathing/strobing, colored per status)
    update live -- pushed by the base station over MQTT
    (STATUS_LED, epm/<node_id>/cmd) whenever this node's dashboard status
    changes, never polled from here.

Wire format (data direction): the generic section-list telemetry frame
(common/telemetry_frame.py, docs/SENSOR_TELEMETRY_FRAME_PLAN.md S3/S6) -- the
exact same payload the base station's own SPI fuser emits, published as the raw
MQTT message body with no extra envelope. By default this mirrors real
firmware's normal-mode shape (base-station/sketch/fuser.cpp) for accel + mic
+ scalars, with one deliberate simplification: this sim emits fused accel OR
per-axis accel per node, never both together the way real firmware always
does (see "Accel: fused vs per-axis" below) -- plus one SCALAR_SET section,
6 scalars computed separately per live channel (accel_x/accel_y/accel_z/mic,
never a combined tri-axial magnitude), exactly mirroring fuser.cpp's own
per-channel compute_scalars() calls. The command direction (STATUS_LED,
epm/<node_id>/cmd) still uses the lean [TYPE: 1B][display_rgb_payload]
envelope.

Accel: fused vs per-axis -- a node's accel output is controlled by two
settings: --accel (emit accel spectrum data at all) and --accel-fused
(while --accel is on: fused single-channel if set, per-axis accel_x/y/z if
not; ignored while --accel is off). They're mutually exclusive by
construction, never both at once.

Zero-fill, not omit (fused accel / mic): PipelineManager commits to a node's
bin-count shape from its first-ever frame (pipeline/manager.py's
_infer_sensor_config_and_dim) and raises loudly if a later frame's shape
drifts -- and that raise, uncaught, has previously taken down MQTT ingestion
for the *entire* fleet, not just the one node that changed (fixed separately
in ingestion/mqtt_subscriber.py, but this sim shouldn't provoke it either).
So the "Accel"/"Mic" toggles never remove the fused-slot/mic sections once
online -- they switch each between real computed values and an all-zero
spectrum at the same bin count (common/telemetry_frame.py's own documented
"zero-fill" convention), simulating a sensor that's still wired up but
reporting nothing useful (e.g. a loose connector, docs/mic-sai-capture-bug
territory) rather than one that was never there. Mic's scalar block follows
the same zero-fill rule (build_frame() sends all-zero rms_mic/etc. rather
than omitting them when mic's off). Per-axis accel_x/y/z ARE
registry.SensorChannel members now (model-relevant, validated) -- toggling
--accel-per-axis off after a node's first frame committed it would drift
that node's shape exactly like the fused/mic case above, so treat it as a
fixed-for-the-session choice, same caveat.

Accel/mic bin count are never locked: unlike a real node, nothing here
freezes --accel-bin-count/--mic-bin-count (or the UI's matching fields)
after this node's first published frame. A later change just shows up on
the next published frame; if that drifts this node's already-committed
input_dim (registry.py's per-node input_dim, derived dynamically from the
first frame), only ingestion for this one node is affected --
ingestion/mqtt_subscriber.py's fix above already isolates a bad frame to
its own node rather than taking down the whole fleet.

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
    rgb_int_to_hex,
)
import telemetry_schema as schema  # noqa: E402
from telemetry_frame import (  # noqa: E402
    encode_frame,
    encode_scalar_body,
    encode_section,
    encode_spectrum_body,
)
from raw_features import (  # noqa: E402
    crest_factor,
    downsample,
    fft_magnitude,
    kurtosis,
    mic_useful_magnitude,
    peak,
    peak_normalize,
    rms,
    skewness,
    std,
)

DATA_TOPIC_FMT = "epm/{node_id}/data"
CMD_TOPIC_FMT = "epm/{node_id}/cmd"

# tools/ -> python/ -> base-station/ -> base-station/captures/, alongside
# pull_captures.sh and tools/offline_experiment.py's own default -- resolved
# relative to this file, not the caller's cwd.
_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_CAPTURES_DIR = os.path.join(_HERE, "..", "..", "captures")

ACCEL_AXES = ("accel_x_raw", "accel_y_raw", "accel_z_raw")
MIC_CHANNEL = "mic_raw"
RAW_CHANNEL_NAMES = ACCEL_AXES + (MIC_CHANNEL,)
_AXIS_SPECTRUM_NAME = {"accel_x_raw": "accel_x", "accel_y_raw": "accel_y", "accel_z_raw": "accel_z"}
# Scalar wire-key suffix per raw channel (telemetry_schema.json's rms_x/.../
# rms_mic naming) -- matches fuser.cpp's per-channel scalar computation.
_SCALAR_SUFFIX = {"accel_x_raw": "x", "accel_y_raw": "y", "accel_z_raw": "z", "mic_raw": "mic"}

_SCALAR_FUNCS = {
    "rms": rms, "kurtosis": kurtosis, "std": std,
    "peak": peak, "crest_factor": crest_factor, "skewness": skewness,
}

_KIND_SPECTRUM = schema.DATA_KIND["SPECTRUM"]
_KIND_SCALAR_SET = schema.DATA_KIND["SCALAR_SET"]

DEFAULT_BIN_COUNT = 128
DEFAULT_PUBLISH_INTERVAL_S = 0.2  # matches the old simulate.py's cadence

# Metadata for a zero-filled accel/mic section when the currently selected
# capture lacks that raw channel entirely -- matches gen_synthetic_captures.py
# and raw_capture.py's real window sizes, used only for the fs/fft_size wire
# fields (display/frequency-axis math), never for bin_count itself.
NOMINAL_ACCEL_FS_HZ = 6400.0
NOMINAL_ACCEL_FFT_SIZE = 1024
NOMINAL_MIC_FS_HZ = 48000.0
NOMINAL_MIC_FFT_SIZE = 2048

_DEFAULT_LED = {"rgb": "#4d4d4d", "mode": "const", "period_ms": 0}


# ---------------------------------------------------------------------
# Capture loading
# ---------------------------------------------------------------------

_capture_cache = {}
_capture_cache_lock = threading.Lock()


def load_capture(path: str) -> dict:
    """One raw_capture.py-shaped .npz file -> {"label": str,
    "accel_x_raw": (num_windows, 1024) f32, "accel_x_raw_fs": float, ...,
    "mic_raw": (num_windows, 2048) f32, "mic_raw_fs": float}, keeping only
    whichever of the 4 raw channels the file actually has (a file recorded
    with e.g. no mic is tolerated, not an error)."""
    with _capture_cache_lock:
        cached = _capture_cache.get(path)
    if cached is not None:
        return cached

    data = np.load(path)
    capture = {"label": str(data["label"]) if "label" in data.files else None}
    for name in RAW_CHANNEL_NAMES:
        if name in data.files:
            capture[name] = data[name].astype(np.float32)
            capture[f"{name}_fs"] = float(data[f"{name}_fs"])

    with _capture_cache_lock:
        _capture_cache[path] = capture
    return capture


class CaptureState:
    """One node's selected capture file + a per-raw-channel read position.
    A single .npz bundles all 4 raw channels from one recording session (a
    node's rig state at capture time), so selection is per-node, not
    per-channel -- unlike the old CSV-per-channel model. Each channel's
    position is advanced/wrapped independently mod its own window count:
    accel and mic windows aren't index-paired 1:1 (see
    tools/offline_experiment.py's load_captures() docstring)."""

    def __init__(self):
        self.file_path = None
        self.selected_name = None
        self._capture = None
        self._pos = {}
        self._lock = threading.Lock()

    def set_file(self, file_name, captures_dir) -> None:
        path = os.path.join(captures_dir, file_name) if file_name else None
        capture = load_capture(path) if path else None
        with self._lock:
            self.file_path = path
            self.selected_name = file_name
            self._capture = capture
            self._pos = {name: 0 for name in RAW_CHANNEL_NAMES if capture and name in capture}

    def label(self):
        with self._lock:
            return self._capture.get("label") if self._capture else None

    def next_windows(self) -> dict:
        """{channel_name: (window ndarray, fs)} for each raw channel present
        in the selected file, one window popped per channel and that
        channel's own position advanced/wrapped independently."""
        with self._lock:
            if self._capture is None:
                return {}
            out = {}
            for name in RAW_CHANNEL_NAMES:
                arr = self._capture.get(name)
                if arr is None or arr.shape[0] == 0:
                    continue
                pos = self._pos[name]
                out[name] = (arr[pos], self._capture[f"{name}_fs"])
                self._pos[name] = (pos + 1) % arr.shape[0]
            return out


# ---------------------------------------------------------------------
# Spectrum/scalar computation -> wire sections
# ---------------------------------------------------------------------

def build_frame(windows: dict, *, accel_fused: bool, accel_per_axis: bool, mic: bool,
                 scalars, bin_count: int, mic_bin_count: int, axis_bin_count: int,
                 source_id: int):
    """windows: CaptureState.next_windows()'s output. Returns
    (sections, preview, scalar_values):
      sections       -- encoded bytes ready for encode_frame()
      preview        -- ChannelSpectrum per built channel ("accel", "mic",
                         and/or "accel_x"/"accel_y"/"accel_z"), for the local
                         debug UI's FFT plots
      scalar_values  -- {scalar_key: float} for whichever scalars were
                         computed ("rms_x", "kurtosis_mic", etc.), for the
                         local debug UI's readout

    Fused accel ("accel") and mic are model-relevant (SensorFrame.bins,
    registry.SensorChannel) -- ALWAYS emitted at the given bin_count/
    mic_bin_count once this is called at all, real values when enabled and
    the capture has that data, zero-filled otherwise (module docstring's
    "zero-fill, not omit"). Per-axis accel_x/y/z are ALSO model-relevant now
    (registry.SensorChannel includes them), so the same "don't let a
    committed channel disappear mid-session" caveat now applies to
    --accel-per-axis too, not just --accel/--mic.

    Scalars are computed per-channel (accel_x/accel_y/accel_z/mic), mirroring
    fuser.cpp's compute_scalars() -- never a combined tri-axial magnitude, and
    the wire key is channel-suffixed (rms_x, kurtosis_mic, ...) to match
    telemetry_schema.json. This is no longer "display-only, never validated"
    like it used to be: pipeline/features.py's build_feature_vector() now
    raises if ANY of a live channel's 6 scalar keys is missing from a frame,
    so unchecking a single scalar checkbox in the local debug UI while
    accel-per-axis/mic are on will break ingestion for this node just as
    hard as unchecking all of them -- there's no such thing as "some
    scalars" anymore, only "all 6 per channel" or "channel not live"."""
    sections = []
    preview = {}
    scalar_values = {}

    if accel_per_axis:
        for name in ACCEL_AXES:
            if name not in windows:
                continue
            window, fs = windows[name]
            mag = downsample(fft_magnitude(window), axis_bin_count)
            bins = tuple(float(v) for v in peak_normalize(mag))
            axis_name = _AXIS_SPECTRUM_NAME[name]
            channel_id = schema.CHANNEL_ID_BY_NAME[axis_name]
            sections.append(encode_section(source_id, channel_id, _KIND_SPECTRUM,
                                            encode_spectrum_body(fs, len(window), bins)))
            preview[axis_name] = ChannelSpectrum(fs=fs, fft_size=len(window), bins=bins)
            if scalars:
                suffix = _SCALAR_SUFFIX[name]
                for scalar_name in scalars:
                    scalar_values[f"{scalar_name}_{suffix}"] = _SCALAR_FUNCS[scalar_name](window)

    present_axes = [name for name in ACCEL_AXES if name in windows]
    if accel_fused and present_axes:
        fs = windows[present_axes[0]][1]
        combined = sum(downsample(fft_magnitude(windows[name][0]), bin_count) for name in present_axes)
        accel_bins = tuple(float(v) for v in peak_normalize(combined))
    else:
        fs = NOMINAL_ACCEL_FS_HZ
        accel_bins = tuple(0.0 for _ in range(bin_count))
    sections.append(encode_section(source_id, schema.CHANNEL_ID_BY_NAME["accel"], _KIND_SPECTRUM,
                                    encode_spectrum_body(fs, NOMINAL_ACCEL_FFT_SIZE, accel_bins)))
    preview["accel"] = ChannelSpectrum(fs=fs, fft_size=NOMINAL_ACCEL_FFT_SIZE, bins=accel_bins)

    if mic and MIC_CHANNEL in windows:
        window, fs = windows[MIC_CHANNEL]
        # Trim to the first quarter-FFT before downsampling -- mic's own
        # Fs/4 aliasing limit (mic_sampler.cpp), same as raw_capture_server.py
        # and offline_experiment.py apply; otherwise this simulated node
        # publishes an alias image as if it were real audio above 24kHz.
        mag = downsample(mic_useful_magnitude(fft_magnitude(window)), mic_bin_count)
        mic_bins = tuple(float(v) for v in peak_normalize(mag))
        if scalars:
            for scalar_name in scalars:
                scalar_values[f"{scalar_name}_mic"] = _SCALAR_FUNCS[scalar_name](window)
    else:
        fs = NOMINAL_MIC_FS_HZ
        mic_bins = tuple(0.0 for _ in range(mic_bin_count))
        # mic's spectrum section is zero-filled rather than omitted when
        # mic's off/unavailable (above) -- its scalar block must match, or a
        # node with mic committed as a model channel would suddenly be
        # missing rms_mic/etc. the moment the capture ran out of mic data.
        if scalars:
            for scalar_name in scalars:
                scalar_values[f"{scalar_name}_mic"] = 0.0
    sections.append(encode_section(source_id, schema.CHANNEL_ID_BY_NAME["mic"], _KIND_SPECTRUM,
                                    encode_spectrum_body(fs, NOMINAL_MIC_FFT_SIZE, mic_bins)))
    preview["mic"] = ChannelSpectrum(fs=fs, fft_size=NOMINAL_MIC_FFT_SIZE, bins=mic_bins)

    if scalar_values:
        values = {schema.SCALAR_ID_BY_NAME[key]: value for key, value in scalar_values.items()}
        sections.append(encode_section(source_id, schema.PERF_CHANNEL_ID, _KIND_SCALAR_SET,
                                        encode_scalar_body(values)))

    return sections, preview, scalar_values


def spectrum_freqs(spectrum: ChannelSpectrum, bandwidth_hz: float):
    """Frequency for each bin of a ChannelSpectrum, evenly spanning
    0..bandwidth_hz across however many bins are actually present.

    Bin i covers the band (i, i+1) * fs/fft_size and is plotted at its
    centre, matching the dashboard's own charts.js binFreqsFor() (see
    common/telemetry_frame.py's SPECTRUM frequency convention -- DC is
    never on the wire, so a bin's lower edge is not its frequency). The
    fs/fft_size form breaks once bin_count is
    downsampled below the natural resolution, which only this sim's own
    --accel-bin-count/--mic-bin-count knobs can do: it silently reports a
    frequency span 1/downsample-factor too narrow, squeezing the real
    content into a fraction of the plot (a mic capture 4x-downsampled
    below its natural 512-bin resolution showed as squashed into the
    first quarter of this UI's plot -- the whole real 0..fs/4 band, not
    just that first quarter). Computing evenly off the known real
    bandwidth (fs/2 for accel, fs/4 for mic -- see mic_useful_magnitude())
    instead of fft_size sidesteps that regardless of pooling. UI-only
    (drawing the local plot); not part of the wire payload itself, which
    still sends fs/fft_size/bin_count faithfully."""
    n = len(spectrum.bins)
    return [(i + 0.5) * bandwidth_hz / n for i in range(n)]


def _spectrum_for_ui(spectrum, bandwidth_hz) -> dict:
    """{"freq": [...], "mag": [...]} for the /state JSON response the web
    UI polls -- flat arrays rather than a list of {freq,mag} objects."""
    if spectrum is None or not spectrum.bins:
        return {"freq": [], "mag": []}
    return {"freq": spectrum_freqs(spectrum, bandwidth_hz), "mag": list(spectrum.bins)}


# ---------------------------------------------------------------------
# Node: MQTT pub/sub + publish loop
# ---------------------------------------------------------------------

class SatelliteNode:
    def __init__(self, node_id: str, captures_dir: str, mqtt_host: str, mqtt_port: int,
                 accel_bin_count: int, mic_bin_count: int,
                 accel: bool, accel_fused: bool, mic: bool, scalars,
                 publish_interval_s: float):
        self.node_id = node_id
        self.captures_dir = captures_dir
        self.accel_bin_count = accel_bin_count
        self.mic_bin_count = mic_bin_count

        self.online = False
        self.led = dict(_DEFAULT_LED)
        self.publish_interval_s = publish_interval_s
        # accel: on/off. accel_fused: fused vs per-axis when accel is on
        # (meaningless, ignored, while accel is off) -- never both at once,
        # see module docstring's "Accel: fused vs per-axis".
        self.accel = accel
        self.accel_fused = accel_fused
        self.mic = mic
        self.scalars = tuple(scalars)
        self.capture = CaptureState()
        self.last_preview = {}   # name -> most recently published ChannelSpectrum, for the UI's FFT plot
        self.last_scalars = {}   # scalar name -> most recently published value, for the UI's readout
        self.last_error = None   # str, most recent build_frame()/publish failure, for the UI

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
        """Recurses under captures_dir and returns paths relative to it,
        e.g. "healthy_1784692804.npz" or "session2/fault.npz" -- only
        ".npz" entries."""
        matches = []
        for root, _dirs, files in os.walk(self.captures_dir):
            for name in files:
                if name.lower().endswith(".npz"):
                    matches.append(os.path.relpath(os.path.join(root, name), self.captures_dir))
        return sorted(matches)

    def set_online(self, online: bool) -> None:
        with self._state_lock:
            self.online = online

    def set_config(self, file_name, accel: bool, accel_fused: bool, mic: bool, scalars,
                    accel_bin_count: int, mic_bin_count: int) -> None:
        self.capture.set_file(file_name, self.captures_dir)
        with self._state_lock:
            self.accel = accel
            self.accel_fused = accel_fused
            self.mic = mic
            self.scalars = tuple(name for name in scalars if name in _SCALAR_FUNCS)
            self.accel_bin_count = max(1, int(accel_bin_count))
            self.mic_bin_count = max(1, int(mic_bin_count))

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

            windows = self.capture.next_windows()
            if not windows:
                continue

            with self._state_lock:
                accel, accel_fused_choice, mic, scalars = (
                    self.accel, self.accel_fused, self.mic, self.scalars)
                accel_bin_count, mic_bin_count = self.accel_bin_count, self.mic_bin_count

            # Fused and per-axis are mutually exclusive from this node's
            # config -- accel_fused_choice only matters while accel is on.
            accel_fused = accel and accel_fused_choice
            accel_per_axis = accel and not accel_fused_choice

            try:
                sections, preview, scalar_values = build_frame(
                    windows, accel_fused=accel_fused, accel_per_axis=accel_per_axis, mic=mic,
                    scalars=scalars, bin_count=accel_bin_count, mic_bin_count=mic_bin_count,
                    axis_bin_count=accel_bin_count, source_id=schema.SOURCE_ID["satellite"])
            except ValueError as e:
                # e.g. a bin count that doesn't evenly divide this capture's
                # raw FFT size (raw_features.downsample()'s own check) --
                # log + skip this tick rather than letting a bad config
                # silently kill the publish thread forever.
                with self._state_lock:
                    self.last_error = str(e)
                print(f"[{self.node_id}] build_frame failed, skipping this tick: {e}", flush=True)
                continue

            with self._state_lock:
                self.last_preview.update(preview)
                self.last_scalars = scalar_values
                self.last_error = None

            frame = encode_frame(sections)
            self._mqtt.publish(DATA_TOPIC_FMT.format(node_id=self.node_id), frame, qos=0)

    def state(self) -> dict:
        with self._state_lock:
            online, led = self.online, dict(self.led)
            publish_interval_s = self.publish_interval_s
            accel, accel_fused, mic = self.accel, self.accel_fused, self.mic
            scalars = self.scalars
            accel_bin_count, mic_bin_count = self.accel_bin_count, self.mic_bin_count
            last_preview = dict(self.last_preview)
            last_scalars = dict(self.last_scalars)
            last_error = self.last_error
        return {
            "node_id": self.node_id,
            "online": online,
            "led": led,
            "publish_interval_ms": round(publish_interval_s * 1000),
            "file": self.capture.selected_name,
            "label": self.capture.label(),
            "accel": accel,
            "accel_fused": accel_fused,
            "mic": mic,
            "scalars": list(scalars),
            "all_scalars": list(_SCALAR_FUNCS),
            "accel_bin_count": accel_bin_count,
            "mic_bin_count": mic_bin_count,
            "preview": {
                name: _spectrum_for_ui(spec, spec.fs / 4 if name == "mic" else spec.fs / 2)
                for name, spec in last_preview.items()
            },
            "scalar_values": last_scalars,
            "last_error": last_error,
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
  select { width: 100%; }
  .file-label { font-size: 0.75rem; color: #9f9; font-family: monospace; word-break: break-all; margin: 0.3rem 0 0.6rem 0; min-height: 1em; }
  .hint { font-size: 0.72rem; color: #778; margin: 0.2rem 0 0.5rem 0; }
  .error { font-size: 0.75rem; color: #f87171; font-family: monospace; margin: 0.3rem 0; }
  .toggle-row { display: flex; flex-wrap: wrap; gap: 0.2rem 1rem; margin: 0.4rem 0; }
  .toggle-row label { display: flex; align-items: center; gap: 0.3rem; font-size: 0.85rem; }
  .toggle-row label:has(input:disabled) { opacity: 0.4; }
  .bin-row { display: flex; align-items: center; gap: 0.5rem; margin: 0.3rem 0; font-size: 0.82rem; }
  .bin-row label { flex: 1; }
  .bin-row input { width: 5.5rem; }
  .bin-row input:disabled { opacity: 0.4; }
  .legend { display: flex; gap: 0.8rem; font-size: 0.72rem; margin-top: 0.3rem; }
  .legend span { display: flex; align-items: center; gap: 0.3rem; }
  .legend i { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }
  canvas.plot { display: block; width: 100%; height: 90px; background: #000; border: 1px solid #333; margin-top: 0.3rem; }
  .rate-row { display: flex; align-items: center; gap: 0.5rem; margin: 0.4rem 0; }
  .rate-row input { width: 5rem; }
  #scalar-table { font-size: 0.8rem; font-family: monospace; color: #9f9; }
  #scalar-table div { display: flex; justify-content: space-between; }
</style>
</head>
<body>
  <h1>Satellite Node <span id="node-id">...</span></h1>
  <div id="led" class="mode-const"></div>
  <div style="text-align:center"><button id="online-btn" onclick="toggleOnline()">...</button></div>
  <div class="error" id="last-error"></div>

  <h2>Publish Rate</h2>
  <div class="rate-row">
    <input type="number" id="interval-ms" min="20" max="5000" step="10" onchange="updateConfig()">
    <span>ms per frame</span>
  </div>

  <h2>Capture File</h2>
  <select id="capture-file" onchange="updateConfig()"></select>
  <div class="file-label" id="capture-file-label"></div>

  <h2>Spectrum Channels</h2>
  <div class="hint">Accel is fused or per-axis, never both -- "Fused" only
    applies while Accel is on.</div>
  <div class="toggle-row">
    <label><input type="checkbox" id="accel" onchange="updateConfig()"> Accel</label>
    <label><input type="checkbox" id="accel-fused" onchange="updateConfig()"> Fused</label>
    <label><input type="checkbox" id="mic" onchange="updateConfig()"> Mic</label>
  </div>
  <div class="bin-row">
    <label for="accel-bin-count">Accel bin count</label>
    <input type="number" id="accel-bin-count" min="1" onchange="updateConfig()">
  </div>
  <div class="hint">Fused: total bins. Per-axis: bins per axis (x3 across accel_x/y/z).</div>
  <div class="bin-row">
    <label for="mic-bin-count">Mic bin count</label>
    <input type="number" id="mic-bin-count" min="1" onchange="updateConfig()">
  </div>

  <div class="legend" id="accel-legend"></div>
  <canvas class="plot" id="accel-plot" width="440" height="90"></canvas>
  <canvas class="plot" id="mic-plot" width="440" height="90"></canvas>

  <h2>Scalars</h2>
  <div class="toggle-row" id="scalar-toggles"></div>
  <div id="scalar-table"></div>

<script>
let online = false;
const ALL_SCALARS_FALLBACK = ["rms", "kurtosis", "crest_factor", "peak", "std", "skewness"];
let scalarTogglesBuilt = false;

// Matches base-station/python/frontend/charts.js's AXIS_COLORS exactly, so
// this preview reads the same way the real dashboard's spectrum chart does.
const AXIS_COLORS = {
  accel_x: "#3987e5", accel_y: "#9085e9", accel_z: "#d55181",
  mic: "#d95926", accel: "#3987e5",
};

function renderFileOptions(select, files, current) {
  const have = Array.from(select.options).map(o => o.value);
  if (have.join(",") !== files.join(",")) {
    select.innerHTML = '<option value="">(none)</option>' +
      files.map(f => `<option value="${f}">${f}</option>`).join("");
  }
  select.value = current || "";
}

function buildScalarToggles(names) {
  const container = document.getElementById("scalar-toggles");
  container.innerHTML = names.map(name =>
    `<label><input type="checkbox" id="scalar-${name}" onchange="updateConfig()"> ${name}</label>`
  ).join("");
  scalarTogglesBuilt = true;
}

// series: [{freq, mag, color, fill, name}]. Mirrors charts.js's spline+
// tozeroy-fill look closely enough for a debug tool, without pulling in a
// charting library. x-axis scale is taken from the data's own max freq
// (spectrum_freqs()'s last bin, server-side) rather than a hardcoded
// guess -- the real bandwidth varies with each capture file's own fs.
function drawLines(canvas, series) {
  const ctx = canvas.getContext("2d");
  const w = canvas.width, h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  if (!series.length) return;
  const allFreq = series.flatMap(s => s.freq && s.freq.length ? s.freq : [0]);
  const nyquistHz = Math.max(...allFreq, 1e-9);
  const allMag = series.flatMap(s => s.mag && s.mag.length ? s.mag : [0]);
  const maxMag = Math.max(...allMag, 1e-9);
  for (const s of series) {
    if (!s.freq || !s.freq.length) continue;
    const points = s.freq.map((f, i) => [
      Math.min(w, (f / nyquistHz) * w),
      h - Math.max(0, (s.mag[i] / maxMag) * (h - 4)),
    ]);
    if (s.fill) {
      ctx.beginPath();
      ctx.moveTo(points[0][0], h);
      for (const [x, y] of points) ctx.lineTo(x, y);
      ctx.lineTo(points[points.length - 1][0], h);
      ctx.closePath();
      ctx.fillStyle = s.color + "26";  // ~15% alpha, matches charts.js's fill opacity
      ctx.fill();
    }
    ctx.beginPath();
    points.forEach(([x, y], i) => (i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)));
    ctx.strokeStyle = s.color;
    ctx.lineWidth = 1.5;
    ctx.stroke();
  }
}

function renderLegend(container, series) {
  container.innerHTML = series.length <= 1 ? "" : series.map(s =>
    `<span><i style="background:${s.color}"></i>${s.name}</span>`
  ).join("");
}

async function poll() {
  try {
    const r = await fetch("/state");
    const s = await r.json();
    document.getElementById("node-id").textContent = s.node_id;

    online = s.online;
    document.getElementById("online-btn").textContent = online ? "Go Offline" : "Go Online";
    document.getElementById("last-error").textContent = s.last_error ? ("error: " + s.last_error) : "";

    const led = document.getElementById("led");
    led.style.backgroundColor = s.led.rgb;
    led.style.color = s.led.rgb;
    led.className = "mode-" + s.led.mode;
    led.style.animationDuration = (s.led.period_ms || 1000) + "ms";

    const rateInput = document.getElementById("interval-ms");
    if (document.activeElement !== rateInput) {
      rateInput.value = s.publish_interval_ms;
    }

    if (!scalarTogglesBuilt) {
      buildScalarToggles(s.all_scalars && s.all_scalars.length ? s.all_scalars : ALL_SCALARS_FALLBACK);
    }

    renderFileOptions(document.getElementById("capture-file"), s.files, s.file);
    document.getElementById("capture-file-label").textContent =
      s.file ? (s.file + (s.label ? "  (label: " + s.label + ")" : "")) : "(no file selected)";

    document.getElementById("accel").checked = s.accel;
    const accelFusedBox = document.getElementById("accel-fused");
    accelFusedBox.checked = s.accel_fused;
    accelFusedBox.disabled = !s.accel;
    document.getElementById("mic").checked = s.mic;
    for (const name of (s.all_scalars || ALL_SCALARS_FALLBACK)) {
      const box = document.getElementById("scalar-" + name);
      if (box) box.checked = s.scalars.includes(name);
    }

    const accelBinCountInput = document.getElementById("accel-bin-count");
    const micBinCountInput = document.getElementById("mic-bin-count");
    if (document.activeElement !== accelBinCountInput) accelBinCountInput.value = s.accel_bin_count;
    if (document.activeElement !== micBinCountInput) micBinCountInput.value = s.mic_bin_count;

    // Dashboard's own precedence (charts.js's buildAccelSpectrumFigure):
    // per-axis traces win over the fused fallback whenever per-axis data
    // exists at all, never both at once in the same panel.
    const axisNames = ["accel_x", "accel_y", "accel_z"].filter(n => s.preview[n] && s.preview[n].freq.length);
    let accelSeries;
    if (axisNames.length) {
      accelSeries = axisNames.map(n => ({
        freq: s.preview[n].freq, mag: s.preview[n].mag, color: AXIS_COLORS[n], fill: false, name: n,
      }));
    } else if (s.preview.accel) {
      accelSeries = [{
        freq: s.preview.accel.freq, mag: s.preview.accel.mag, color: AXIS_COLORS.accel, fill: true, name: "accel",
      }];
    } else {
      accelSeries = [];
    }
    renderLegend(document.getElementById("accel-legend"), accelSeries);
    drawLines(document.getElementById("accel-plot"), accelSeries);

    const micPreview = s.preview.mic;
    const micSeries = micPreview && micPreview.freq.length
      ? [{ freq: micPreview.freq, mag: micPreview.mag, color: AXIS_COLORS.mic, fill: true, name: "mic" }]
      : [];
    drawLines(document.getElementById("mic-plot"), micSeries);

    const table = document.getElementById("scalar-table");
    table.innerHTML = Object.entries(s.scalar_values || {})
      .map(([name, value]) => `<div><span>${name}</span><span>${value.toFixed(4)}</span></div>`)
      .join("");
  } catch (e) {
    document.getElementById("node-id").textContent = "(fetch error: " + e + ")";
  }
}

async function toggleOnline() {
  await fetch("/online", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({online: !online}),
  });
  poll();
}

async function updateConfig() {
  const ms = parseInt(document.getElementById("interval-ms").value, 10);
  if (Number.isFinite(ms)) {
    await fetch("/rate", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({interval_ms: ms}),
    });
  }
  const scalars = Array.from(document.querySelectorAll('[id^="scalar-"]'))
    .filter(box => box.checked)
    .map(box => box.id.replace("scalar-", ""));
  await fetch("/config", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      file: document.getElementById("capture-file").value,
      accel: document.getElementById("accel").checked,
      accel_fused: document.getElementById("accel-fused").checked,
      mic: document.getElementById("mic").checked,
      scalars: scalars,
      accel_bin_count: parseInt(document.getElementById("accel-bin-count").value, 10),
      mic_bin_count: parseInt(document.getElementById("mic-bin-count").value, 10),
    }),
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
            elif self.path == "/config":
                scalars = body.get("scalars") or []
                if not isinstance(scalars, list):
                    self._send_json({"error": "scalars must be a list"}, status=400)
                    return
                current = node.state()
                node.set_config(
                    body.get("file") or None,
                    bool(body.get("accel")),
                    bool(body.get("accel_fused")),
                    bool(body.get("mic")),
                    scalars,
                    body.get("accel_bin_count") or current["accel_bin_count"],
                    body.get("mic_bin_count") or current["mic_bin_count"],
                )
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
    parser.add_argument("--captures-dir", default=_DEFAULT_CAPTURES_DIR,
                         help="directory of raw_capture.py .npz files, recursed, default: "
                              f"{os.path.relpath(_DEFAULT_CAPTURES_DIR, _HERE)} "
                              "(base-station/captures/, alongside pull_captures.sh)")
    parser.add_argument("--ui-host", default="0.0.0.0")
    parser.add_argument("--ui-port", type=int, default=0,
                         help="0 (default) = OS picks a free port, printed on startup")
    parser.add_argument("--node-id", default=None,
                         help="Override the persisted/random node id")
    parser.add_argument("--state-file", default=None,
                         help="Where to persist this copy's node id across restarts "
                              "(default: next to this script, keyed by the UI port)")
    parser.add_argument("--accel-bin-count", type=int, default=DEFAULT_BIN_COUNT,
                         help="downsampled accel spectrum bins: used as-is for the fused channel, "
                              "or per-axis (x3 total across accel_x/y/z) in per-axis mode -- see "
                              "--accel-fused. Must evenly divide 512 FFT bins from the 1024-sample "
                              "accel window (firmware's ACCEL_FFT_BIN_COUNT). "
                              "Freely adjustable at any time, never locked")
    parser.add_argument("--mic-bin-count", type=int, default=None,
                         help="downsampled mic spectrum bins (default: same as --accel-bin-count; "
                              "must evenly divide 512 -- of the 1024 unique FFT bins from the "
                              "2048-sample mic window, only the first 512 (below Fs/4=24kHz) are "
                              "real audio, see mic_useful_magnitude()). Freely adjustable at any "
                              "time, never locked")
    parser.add_argument("--accel", action=argparse.BooleanOptionalAction, default=True,
                         help="emit accel spectrum data at all (fused or per-axis, see --accel-fused)")
    parser.add_argument("--accel-fused", action=argparse.BooleanOptionalAction, default=False,
                         help="while --accel is on: fused (summed 3-axis) channel if set, "
                              "accel_x/y/z per-axis channels if not. Ignored while --accel is off")
    parser.add_argument("--mic", action=argparse.BooleanOptionalAction, default=True,
                         help="emit the mic SPECTRUM channel")
    parser.add_argument("--scalars", nargs="*", choices=tuple(_SCALAR_FUNCS), default=list(_SCALAR_FUNCS),
                         help="scalar modules to include in the SCALAR_SET section, computed on "
                              "the combined tri-axial accel magnitude (default: all 6)")
    parser.add_argument("--publish-interval-s", type=float, default=DEFAULT_PUBLISH_INTERVAL_S)
    args = parser.parse_args()

    if not os.path.isdir(args.captures_dir):
        parser.error(f"--captures-dir {args.captures_dir!r} is not a directory")

    httpd, node_holder = build_httpd(args.ui_host, args.ui_port)
    ui_port = httpd.server_port

    state_file = args.state_file or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), f".satellite_node_{ui_port}.json")
    node_id = args.node_id or load_or_create_node_id(state_file)

    mic_bin_count = args.mic_bin_count if args.mic_bin_count is not None else args.accel_bin_count
    node = SatelliteNode(node_id, args.captures_dir, args.mqtt_host, args.mqtt_port,
                          args.accel_bin_count, mic_bin_count,
                          args.accel, args.accel_fused, args.mic, args.scalars,
                          args.publish_interval_s)
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
