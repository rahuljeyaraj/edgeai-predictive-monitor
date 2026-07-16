#!/usr/bin/env python3
"""Prep the Kaggle vibration-fault-diagnosis dataset for Edge Impulse
upload (docs/EDGE_IMPULSE_FAULT_CLASSIFICATION_PLAN.md S3.2 T1).

Reuses satellite_node_sim.py's load_signal()/compute_spectrum() (the exact
FFT the live sim/pipeline runs) and pipeline/features.py's normalize_bins(),
so the training features have zero drift from what flows through the real
pipeline at inference time.

Walks --data-dir (the dataset root -- top-level folder name is the class
label, e.g. Ideal/Cracking/Offset_Pulley/Wear), slides a --window-size
window (default 1024, matching the sim) over every .csv file at --stride
(default = --window-size, i.e. non-overlapping -- same as the sim reading a
file sequentially), computes the peak-normalized 512-bin accel spectrum per
window, and writes one Edge Impulse CSV sample per window to
--out-dir/<label>/<label>.<n>.csv: a "timestamp,accel" time-series CSV, one
bin value per row (see write_ei_sample()'s docstring for why it's not a
single-row/512-column CSV instead). Upload with the curl-only
tools/ei_upload.sh (no node/edge-impulse-cli needed), or the official CLI:

    npm i -g edge-impulse-cli
    edge-impulse-uploader --category split prepared/*/*.csv

--category split lets the uploader do the ~80/20 train/test split himself
per the plan; label comes from each file's "<label>." prefix.
"""
import argparse
import os
import sys

import numpy as np

# Flat-import bootstrap, same convention as main.py (no PYTHONPATH in the
# App Lab container, no __init__.py packages).
_PYTHON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
for _subpackage in ("tools", "common", "registry", "pipeline", "ingestion"):
    sys.path.insert(0, os.path.join(_PYTHON_DIR, _subpackage))

from satellite_node_sim import load_signal, compute_spectrum  # noqa: E402
from features import normalize_bins  # noqa: E402

DEFAULT_WINDOW_SIZE = 1024
DEFAULT_SAMPLE_RATE_HZ = 12800.0


def list_class_dirs(data_dir):
    return sorted(
        name for name in os.listdir(data_dir)
        if os.path.isdir(os.path.join(data_dir, name)))


def list_csv_files(class_dir):
    """Same convention as satellite_node_sim.list_files(): only ".csv"
    entries, which naturally excludes the dataset's .mat files and the
    stray "*.csv:Zone.Identifier" Windows marker files."""
    matches = []
    for root, _dirs, files in os.walk(class_dir):
        for name in files:
            if name.lower().endswith(".csv"):
                matches.append(os.path.join(root, name))
    return sorted(matches)


def sliding_windows(signal, window_size, stride):
    if signal.size < window_size:
        reps = -(-window_size // signal.size)  # ceil division
        signal = np.tile(signal, reps)
    start = 0
    while start + window_size <= signal.size:
        yield signal[start:start + window_size]
        start += stride


def write_ei_sample(path, bins):
    """EI's timestamp-less CSV format only accepts a single data row (one
    column per axis) -- our per-bin values need the time-series format
    instead (a monotonic "timestamp" column + one "accel" column, one row
    per bin), so EI treats the 512 bins as one windowed sample rather than
    512 separate one-row samples. The 1ms step is an arbitrary but
    consistent convention (implies a 1000Hz "sample rate" / 512ms window in
    the impulse's input block) -- these aren't real timestamps, just bin
    positions in a fixed order."""
    with open(path, "w") as f:
        f.write("timestamp,accel\n")
        f.writelines(f"{i},{value}\n" for i, value in enumerate(bins))


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", required=True,
                         help="Kaggle dataset root (contains Ideal/Cracking/Offset_Pulley/Wear)")
    parser.add_argument("--out-dir", required=True,
                         help="Where to write prepared/<label>/<label>.<n>.csv")
    parser.add_argument("--window-size", type=int, default=DEFAULT_WINDOW_SIZE)
    parser.add_argument("--stride", type=int, default=None,
                         help="default = --window-size (non-overlapping, matches the sim); "
                              "pass a smaller value for overlapping windows / more samples")
    parser.add_argument("--sample-rate-hz", type=float, default=DEFAULT_SAMPLE_RATE_HZ,
                         help="only affects the spectrum's fs metadata, not the FFT magnitudes")
    args = parser.parse_args()
    stride = args.stride or args.window_size

    classes = list_class_dirs(args.data_dir)
    if not classes:
        parser.error(f"no class folders found under {args.data_dir!r}")

    grand_total = 0
    for label in classes:
        files = list_csv_files(os.path.join(args.data_dir, label))
        out_class_dir = os.path.join(args.out_dir, label)
        os.makedirs(out_class_dir, exist_ok=True)

        count = 0
        for path in files:
            signal = load_signal(path)
            for window in sliding_windows(signal, args.window_size, stride):
                spectrum = compute_spectrum(window, args.sample_rate_hz)
                bins = normalize_bins(spectrum.bins)
                write_ei_sample(os.path.join(out_class_dir, f"{label}.{count}.csv"), bins)
                count += 1
        print(f"{label}: {len(files)} files -> {count} samples", flush=True)
        grand_total += count

    print(f"Total: {grand_total} samples written under {args.out_dir}", flush=True)


if __name__ == "__main__":
    main()
