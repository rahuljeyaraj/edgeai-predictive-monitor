#!/usr/bin/env python3
"""Prep the Kaggle vibration-fault-diagnosis dataset for Edge Impulse
upload (docs/EDGE_IMPULSE_FAULT_CLASSIFICATION_PLAN.md S3.2 T1).

Reuses satellite_node_sim.py's load_signal()/compute_spectrum() (the exact
FFT the live sim/pipeline runs) and pipeline/features.py's normalize_bins(),
so the training features have zero drift from what flows through the real
pipeline at inference time.

Walks --data-dir (the dataset root -- top-level folder name is the class
label, e.g. Ideal/Cracking/Offset_Pulley/Wear) and splits **files** (not
windows) into train/test up front -- every TEST_HOLDOUT_EVERY_Nth file (in
sorted order) per class is held out for test, the rest are training files.
This file-level split happens before any windowing, so no window from a
test file's signal can ever share a file with a training window -- windows
from the same physical recording run are highly correlated, so splitting
at the window level (the first version of this script did) let information
leak between train and test/validation, which is exactly what produced a
60%-validation/24%-test accuracy gap in EON Tuner runs.

Training files are windowed at --train-stride (default window-size/8, i.e.
~8x overlap -- more samples to learn from) and test files at --test-stride
(default window-size/4, i.e. ~4x overlap -- some more samples for a less
noisy metric, without manufacturing thousands of near-duplicate crops of a
handful of held-out files, which would inflate the test set's apparent size
without adding real independent evidence).

Computes the peak-normalized 512-bin accel spectrum per window and writes
one Edge Impulse CSV sample per window to
--out-dir/{training,testing}/<label>/<label>.<n>.csv: a "timestamp,accel"
time-series CSV, one bin value per row (see write_ei_sample()'s docstring
for why it's not a single-row/512-column CSV instead). Upload with the
curl-only tools/ei_upload.sh (no node/edge-impulse-cli needed), which reads
the training/testing category straight from this directory layout.
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
TEST_HOLDOUT_EVERY_N = 5  # every Nth file (~20%) held out for test, per class


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


def split_files(files):
    """Every TEST_HOLDOUT_EVERY_Nth file (by sorted order) -> test, rest ->
    train. Modulo (not a contiguous tail slice) so the split isn't skewed
    toward whichever axis subfolder happens to sort last (list_csv_files's
    sort clusters files by their <label>_<axis>/ subfolder)."""
    train_files, test_files = [], []
    for i, path in enumerate(files):
        if i % TEST_HOLDOUT_EVERY_N == TEST_HOLDOUT_EVERY_N - 1:
            test_files.append(path)
        else:
            train_files.append(path)
    return train_files, test_files


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


def prepare_category(files, stride, label, out_class_dir, window_size, sample_rate_hz):
    os.makedirs(out_class_dir, exist_ok=True)
    count = 0
    for path in files:
        signal = load_signal(path)
        for window in sliding_windows(signal, window_size, stride):
            spectrum = compute_spectrum(window, sample_rate_hz)
            bins = normalize_bins(spectrum.bins)
            write_ei_sample(os.path.join(out_class_dir, f"{label}.{count}.csv"), bins)
            count += 1
    return count


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", required=True,
                         help="Kaggle dataset root (contains Ideal/Cracking/Offset_Pulley/Wear)")
    parser.add_argument("--out-dir", required=True,
                         help="Where to write {training,testing}/<label>/<label>.<n>.csv")
    parser.add_argument("--window-size", type=int, default=DEFAULT_WINDOW_SIZE)
    parser.add_argument("--train-stride", type=int, default=None,
                         help="default = --window-size // 8 (~8x overlap for more training samples)")
    parser.add_argument("--test-stride", type=int, default=None,
                         help="default = --window-size // 4 (~4x overlap; kept lower than "
                              "--train-stride so the test set isn't mostly near-duplicate crops "
                              "of a handful of held-out files)")
    parser.add_argument("--sample-rate-hz", type=float, default=DEFAULT_SAMPLE_RATE_HZ,
                         help="only affects the spectrum's fs metadata, not the FFT magnitudes")
    args = parser.parse_args()
    train_stride = args.train_stride or max(1, args.window_size // 8)
    test_stride = args.test_stride or max(1, args.window_size // 4)

    classes = list_class_dirs(args.data_dir)
    if not classes:
        parser.error(f"no class folders found under {args.data_dir!r}")

    grand_totals = {"training": 0, "testing": 0}
    for label in classes:
        files = list_csv_files(os.path.join(args.data_dir, label))
        train_files, test_files = split_files(files)

        train_count = prepare_category(
            train_files, train_stride, label,
            os.path.join(args.out_dir, "training", label), args.window_size, args.sample_rate_hz)
        test_count = prepare_category(
            test_files, test_stride, label,
            os.path.join(args.out_dir, "testing", label), args.window_size, args.sample_rate_hz)

        print(f"{label}: {len(train_files)} train files -> {train_count} samples, "
              f"{len(test_files)} test files -> {test_count} samples", flush=True)
        grand_totals["training"] += train_count
        grand_totals["testing"] += test_count

    print(f"Total: {grand_totals['training']} training + {grand_totals['testing']} testing "
          f"samples written under {args.out_dir}", flush=True)


if __name__ == "__main__":
    main()
