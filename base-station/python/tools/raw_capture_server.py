#!/usr/bin/env python3
"""UI'd sibling of tools/raw_capture.py (docs/SENSOR_TELEMETRY_FRAME_PLAN.md):
same raw-mode capture (needs arduino.app_utils.Bridge, same as
spi_reader.py/main.py, against a sketch built with FUSER_RAW_CAPTURE_MODE=1),
but instead of a blind terminal loop this serves a live browser page --
accel x/y/z spectrum, mic spectrum, the raw time-domain windows, and 6
rolling scalar trends (rms/kurtosis per accel axis) -- so a labeled run can
be watched while it's in progress instead of only inspected afterward via
offline_experiment.py's plots.

Run this INSTEAD of raw_capture.py for an interactive session -- both hold
ingestion/spi_reader.py's cross-process exclusive lock (SPI_EXCLUSIVE_LOCK_PATH)
for their whole run, so only one can be active at a time (the other fails
fast with a clear error). main.py does NOT need to be stopped first: its own
SpiConsumer isn't exclusive, so it automatically steps aside (no Bridge
contention) while this holds the lock -- it just sees no new spectrum data.
The raw sensor stream only reaches Bridge/SPI from
inside the App Lab container, so this process runs there; "the host
machine" is just where you open the browser tab, same as the normal Fleet
dashboard (main.py) already works:

    PYTHONPATH=<repo>/base-station/python/common:<repo>/base-station/python/ingestion:\\
<repo>/base-station/python/registry:<repo>/base-station/python/api \\
        python3 tools/raw_capture_server.py --port 8080

Then open http://<device-ip>:8080/raw_capture.html, type a label, hit Start,
hold the rig in that state, hit Stop -- saves one .npz per Stop, same
filename scheme raw_capture.py uses, into /tmp/captures -- the directory
pull_captures.sh already expects, and one this container can actually write
to (raw_capture.py's own documented --out default, /data/captures, fails
with PermissionError on a real container -- /data isn't creatable there).
"""
import argparse
import asyncio
import logging
import os
import sys
import threading
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Optional

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_PYTHON_DIR = os.path.join(_HERE, "..")
for _subpackage in ("common", "ingestion", "registry", "api"):
    sys.path.insert(0, os.path.join(_PYTHON_DIR, _subpackage))

import uvicorn  # noqa: E402
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

import raw_features  # noqa: E402
import telemetry_schema as schema  # noqa: E402
from connection_manager import ConnectionManager  # noqa: E402
from spi_reader import SpiConsumer  # noqa: E402
from telemetry_frame import DecodedFrame  # noqa: E402

logger = logging.getLogger("raw_capture_server")

FRONTEND_DIR = os.path.join(_PYTHON_DIR, "frontend")

RAW_CHANNEL_NAMES = ("accel_x_raw", "accel_y_raw", "accel_z_raw", "mic_raw")
ACCEL_CHANNEL_NAMES = ("accel_x_raw", "accel_y_raw", "accel_z_raw")


class StartBody(BaseModel):
    label: str


def broadcast_threadsafe(app: FastAPI, message: dict) -> None:
    """Sync->async bridge (mirrors api/app.py's own) -- callable from the
    SpiConsumer ingestion thread, not just REST handlers."""
    loop = getattr(app.state, "loop", None)
    if loop is None:
        return
    asyncio.run_coroutine_threadsafe(app.state.connection_manager.broadcast(message), loop)


class RawCaptureRecorder:
    """One labeled run at a time: accumulates raw windows per channel while
    active, then saves them exactly like raw_capture.py's own save block --
    one .npz per label, `{label}_{unix_ts}.npz`, so pull_captures.sh and
    offline_experiment.py need no changes to keep reading these files."""

    def __init__(self, out_dir: str):
        self._out_dir = out_dir
        self._lock = threading.Lock()
        self._label: Optional[str] = None
        self._start_time: Optional[float] = None
        self._windows = defaultdict(list)
        self._fs_by_channel = {}

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._label is not None

    def start(self, label: str) -> None:
        with self._lock:
            if self._label is not None:
                raise ValueError(f"already recording label {self._label!r} -- stop it first")
            self._label = label
            self._start_time = time.time()
            self._windows = defaultdict(list)
            self._fs_by_channel = {}

    def record(self, channel_name: str, samples: np.ndarray, fs: float) -> None:
        with self._lock:
            if self._label is None:
                return
            self._windows[channel_name].append(samples)
            self._fs_by_channel[channel_name] = fs

    def counts(self) -> dict:
        with self._lock:
            return {name: len(arrs) for name, arrs in self._windows.items()}

    def status(self) -> dict:
        with self._lock:
            elapsed = time.time() - self._start_time if self._start_time is not None else None
            return {
                "recording": self._label is not None,
                "label": self._label,
                "elapsed_s": elapsed,
                "counts": {name: len(arrs) for name, arrs in self._windows.items()},
            }

    def stop(self) -> dict:
        with self._lock:
            if self._label is None:
                raise ValueError("not recording")
            label = self._label
            start_time = self._start_time
            windows = self._windows
            fs_by_channel = self._fs_by_channel
            self._label = None
            self._start_time = None

        os.makedirs(self._out_dir, exist_ok=True)
        out_path = os.path.join(self._out_dir, f"{label}_{int(start_time)}.npz")

        save_data = {"label": label}
        total_windows = 0
        for name in RAW_CHANNEL_NAMES:
            arrs = windows.get(name)
            if not arrs:
                continue
            save_data[name] = np.stack(arrs)
            save_data[f"{name}_fs"] = np.float32(fs_by_channel[name])
            total_windows += len(arrs)

        counts = {name: len(arrs) for name, arrs in windows.items()}
        if total_windows == 0:
            return {"label": label, "path": None, "total_windows": 0, "counts": counts,
                     "duration_s": time.time() - start_time}

        np.savez(out_path, **save_data)
        return {"label": label, "path": out_path, "total_windows": total_windows,
                 "counts": counts, "duration_s": time.time() - start_time}


def bin_count_for(channel_name: str, bin_count: int, mic_bin_count: int) -> int:
    return mic_bin_count if channel_name == "mic_raw" else bin_count


def create_app(recorder: RawCaptureRecorder, bin_count: int, mic_bin_count: int) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.loop = asyncio.get_running_loop()
        yield

    app = FastAPI(lifespan=lifespan)
    app.state.connection_manager = ConnectionManager()
    app.state.loop = None

    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
    )

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        manager: ConnectionManager = app.state.connection_manager
        await manager.connect(websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            manager.disconnect(websocket)

    @app.get("/capture/status")
    def get_status():
        return recorder.status()

    @app.post("/capture/start")
    def start_capture(body: StartBody):
        label = body.label.strip()
        if not label:
            raise HTTPException(400, "label must not be empty")
        try:
            recorder.start(label)
        except ValueError as e:
            raise HTTPException(409, str(e))
        status = recorder.status()
        broadcast_threadsafe(app, {"type": "capture_status", **status})
        return status

    @app.post("/capture/stop")
    def stop_capture():
        try:
            summary = recorder.stop()
        except ValueError as e:
            raise HTTPException(409, str(e))
        broadcast_threadsafe(app, {"type": "capture_status", **recorder.status()})
        return summary

    def on_decoded(decoded: DecodedFrame) -> None:
        accel_samples = {}
        accel_channels_payload = {}
        mic_payload = None
        recorded_any = False
        for channel_id, ts in decoded.time_series.items():
            name = schema.CHANNEL_NAME_BY_ID.get(channel_id)
            if name not in RAW_CHANNEL_NAMES:
                continue
            samples = np.array(ts.samples, dtype=np.float32)
            spectrum = raw_features.peak_normalize(
                raw_features.downsample(raw_features.fft_magnitude(samples),
                                         bin_count_for(name, bin_count, mic_bin_count)))
            if recorder.is_recording:
                recorder.record(name, samples, ts.fs)
                recorded_any = True
            payload = {"fs": ts.fs, "samples": samples.tolist(), "spectrum": spectrum.tolist()}
            if name in ACCEL_CHANNEL_NAMES:
                accel_samples[name] = samples
                accel_channels_payload[name] = payload
            elif name == "mic_raw":
                mic_payload = payload

        # One accel epoch ships all 3 axes together in the same decoded
        # frame (fuser.cpp's raw-mode alternation: one 3-axis accel frame,
        # then one mic frame) -- so all 3 are always present here together,
        # never partially, but the membership check stays explicit rather
        # than assumed. Broadcast as ONE message, not one per axis: the
        # frontend used to redraw both the accel spectrum and raw-signal
        # charts once per axis (3x the necessary Plotly.react calls per
        # epoch) since each of the 3 separate messages triggered its own
        # redraw -- batching lets it redraw exactly once per epoch instead.
        if accel_channels_payload:
            broadcast_threadsafe(app, {"type": "raw_accel_epoch", "channels": accel_channels_payload})
        if mic_payload is not None:
            broadcast_threadsafe(app, {"type": "raw_window", "channel": "mic_raw", **mic_payload})

        if all(name in accel_samples for name in ACCEL_CHANNEL_NAMES):
            # Same 6 scalar tiles + same combined-vector-magnitude input as
            # fuser.cpp's compute_scalars() (normal mode only) -- that
            # firmware path is compiled out under FUSER_RAW_CAPTURE_MODE, so
            # this is where the equivalent numbers come from while capturing.
            mag = raw_features.vector_magnitude(
                accel_samples["accel_x_raw"], accel_samples["accel_y_raw"],
                accel_samples["accel_z_raw"])
            scalars = {
                "rms": raw_features.rms(mag),
                "kurtosis": raw_features.kurtosis(mag),
                "crest_factor": raw_features.crest_factor(mag),
                "peak": raw_features.peak(mag),
                "std": raw_features.std(mag),
                "skewness": raw_features.skewness(mag),
            }
            broadcast_threadsafe(app, {"type": "raw_scalars", "t": time.time(), "scalars": scalars})

        # Live per-channel window counts (the toolbar's counts readout) --
        # broadcast once per epoch, not per channel, so a 3-axis accel
        # epoch doesn't push the same counts snapshot three times over.
        if recorded_any:
            broadcast_threadsafe(app, {"type": "capture_status", **recorder.status()})

    def ignore_sensor_frame(_frame):
        # No live pipeline running here (same as raw_capture.py) -- raw-mode
        # frames carry no spectrum bins for it to route anyway.
        pass

    spi_consumer = SpiConsumer(on_frame=ignore_sensor_frame, on_decoded=on_decoded, exclusive=True)
    try:
        spi_consumer.start()
    except RuntimeError as e:
        # main.py (or tools/raw_capture.py) already holds the SPI link --
        # starting anyway would silently show empty plots (2026-07-22: this
        # is exactly what used to happen with no error at all). Fail loud
        # instead so the operator knows to stop the other one first.
        logger.error("%s", e)
        raise SystemExit(1)

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080,
                         help="matches main.py's default -- never run alongside it, both want "
                              "exclusive Bridge/SPI access")
    parser.add_argument("--out", default="/tmp/captures",
                         help="output directory for .npz files (default: /tmp/captures, "
                              "matching pull_captures.sh's expected location -- NOT "
                              "raw_capture.py's own documented default of /data/captures, "
                              "which fails with PermissionError on a real container)")
    parser.add_argument("--bin-count", type=int, default=512,
                         help="live-preview accel spectrum resolution (default 512 -- matches "
                              "ACCEL_FFT_BIN_COUNT/app_config.h, i.e. accel's native FFT "
                              "resolution, factor=1/no pooling). Cosmetic only -- the full raw "
                              "window is always saved, so this doesn't limit later offline "
                              "analysis at a different bin count")
    parser.add_argument("--mic-bin-count", type=int, default=None,
                         help="live-preview mic spectrum resolution (default: same as --bin-count)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    mic_bin_count = args.mic_bin_count if args.mic_bin_count is not None else args.bin_count
    recorder = RawCaptureRecorder(args.out)
    app = create_app(recorder, args.bin_count, mic_bin_count)

    # Mounted after every REST/WebSocket route above is registered, so this
    # catch-all static handler can never shadow them (same ordering as
    # main.py). frontend/raw_capture.html lives alongside index.html so it
    # reuses style.css/vendor/plotly-cartesian.min.js with no extra plumbing.
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

    logger.info("Serving raw-capture UI on http://%s:%d/raw_capture.html", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
