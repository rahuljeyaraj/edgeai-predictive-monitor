#!/usr/bin/env python3
"""
Raw sensor data collection tool for offline experimentation
(docs/SENSOR_TELEMETRY_FRAME_PLAN.md). Runs INSIDE the App Lab container
(needs arduino.app_utils.Bridge, same as spi_reader.py/main.py) against a
sketch built with FUSER_RAW_CAPTURE_MODE=1 (app_config.h) -- fuser.cpp in
that mode alternates one 3-axis-accel-raw frame / one mic-raw frame per
epoch instead of the normal fused SPECTRUM stream. This tool does not start
PipelineManager/the autoencoder -- it just pulls frames over SPI (reusing
spi_reader.SpiConsumer's existing chunked-pull/CRC transport) and dumps every
raw TIME_SERIES window that arrives to one labeled .npz file.

One file per run == one label (rig state, e.g. "healthy" or "imbalance") --
deliberately never mixed, so a later train/test split can be file-level and
leakage-free by construction (this project already hit window-level leakage
once, see docs/EDGE_IMPULSE_FAULT_CLASSIFICATION_PLAN.md S3.3 -- one dupe or
near-dupe window landing in both splits is enough to fake a good score).

Usage (inside the container, motor already in the labeled state):
    PYTHONPATH=<repo>/base-station/python/common:<repo>/base-station/python/ingestion \\
        python3 tools/raw_capture.py --label healthy --duration 180 --out /data/captures

Then pull the .npz files off the device (adb pull) for offline work on a
laptop -- np.load() gives back one array per channel, shape
(num_windows, samples_per_window).
"""
import argparse
import os
import sys
import time
from collections import defaultdict

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_PYTHON_DIR = os.path.join(_HERE, "..")
for _subpackage in ("common", "ingestion"):
    sys.path.insert(0, os.path.join(_PYTHON_DIR, _subpackage))

from spi_reader import SpiConsumer  # noqa: E402
import telemetry_schema as schema  # noqa: E402

RAW_CHANNEL_NAMES = ("accel_x_raw", "accel_y_raw", "accel_z_raw", "mic_raw")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", required=True,
                         help="rig state this run captures, e.g. healthy / imbalance -- "
                              "becomes part of the output filename and is stored inside it")
    parser.add_argument("--duration", type=float, default=180.0,
                         help="seconds to capture (default 180 = 3 min)")
    parser.add_argument("--out", default="/data/captures",
                         help="output directory for the .npz file (default /data/captures)")
    args = parser.parse_args()

    windows = defaultdict(list)   # channel name -> list of 1-D float32 sample arrays
    fs_by_channel = {}            # channel name -> fs (constant per channel, last write wins)
    frame_count = 0

    def on_decoded(decoded):
        nonlocal frame_count
        if not decoded.time_series:
            return  # a non-raw-mode frame (or a heartbeat) -- nothing to capture
        frame_count += 1
        for channel_id, ts in decoded.time_series.items():
            name = schema.CHANNEL_NAME_BY_ID.get(channel_id, f"channel_{channel_id}")
            windows[name].append(np.array(ts.samples, dtype=np.float32))
            fs_by_channel[name] = ts.fs

    def ignore_sensor_frame(_frame):
        # SpiConsumer's normal on_frame callback would hand this to
        # PipelineManager.route -- raw-mode frames carry no spectrum bins
        # (decoded.bins is empty), and this tool isn't running the live
        # pipeline anyway, so there's nothing to do with it.
        pass

    consumer = SpiConsumer(on_frame=ignore_sensor_frame, on_decoded=on_decoded)
    consumer.start()

    start = time.time()
    print(f"Capturing label={args.label!r} for {args.duration:.0f}s -- "
          f"keep the rig in that state now.", flush=True)
    while time.time() - start < args.duration:
        time.sleep(1)
        counts = {name: len(arrs) for name, arrs in windows.items()}
        print(f"  t={time.time() - start:5.0f}s  frames={frame_count}  windows={counts}",
              flush=True)

    os.makedirs(args.out, exist_ok=True)
    out_path = os.path.join(args.out, f"{args.label}_{int(start)}.npz")

    save_data = {"label": args.label}
    total_windows = 0
    for name in RAW_CHANNEL_NAMES:
        arrs = windows.get(name)
        if not arrs:
            continue
        save_data[name] = np.stack(arrs)              # (num_windows, samples_per_window)
        save_data[f"{name}_fs"] = np.float32(fs_by_channel[name])
        total_windows += len(arrs)

    if total_windows == 0:
        print("No raw windows captured -- is the sketch built with "
              "FUSER_RAW_CAPTURE_MODE=1 and running?", file=sys.stderr)
        sys.exit(1)

    np.savez(out_path, **save_data)
    print(f"Saved {total_windows} windows across {len(save_data) - 1} arrays -> {out_path}")


if __name__ == "__main__":
    main()
