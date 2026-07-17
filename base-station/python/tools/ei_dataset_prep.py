#!/usr/bin/env python3
"""Prep the Kaggle vibration-fault-diagnosis dataset for Edge Impulse
upload (docs/EDGE_IMPULSE_FAULT_CLASSIFICATION_PLAN.md S3.2 T1).

Three feature modes, picked with --features:
- "spectrum" (default): the same peak-normalized 512-bin accel FFT
  magnitude spectrum the live pipeline uses. Reuses satellite_node_sim.py's
  load_signal()/compute_spectrum() (the exact FFT the live sim/pipeline
  runs) and pipeline/features.py's normalize_bins(), so these features have
  zero drift from what flows through the real pipeline at inference time.
  Single-axis: X/Y/Z files are pooled as independent samples under the
  class label (axis-agnostic -- matches a real satellite node's single
  accelerometer, see docs/EDGE_IMPULSE_FAULT_CLASSIFICATION_PLAN.md S2).
- "raw": the peak-normalized 1024-sample raw time-domain window, no FFT,
  still single-axis/pooled like spectrum mode. An experiment to see whether
  Edge Impulse's own DSP (e.g. Spectral Analysis) does better than our own
  dense FFT + flat passthrough (S3.3's 42.52% honest baseline).
- "raw-triaxial": like "raw", but pairs each physical recording's three
  per-axis files (<label>_X/<label>_Y/<label>_Z, matched by filename, e.g.
  M(3).csv in all three) into one 3-channel window instead of pooling axes
  as unrelated samples. This is NOT arbitrary pairing -- the source paper
  (Khan et al., "System Design for Early Fault Diagnosis of Machines using
  Vibration Features", IEEE PGSRET 2019, the dataset's origin) confirms a
  single SG-Link *tri-axial* accelerometer was used, and sorting each
  axis's files by their CSV header "Time" field gives the identical label
  order across all three axes (e.g. Ideal: M5,M4,M3,M2,M1,M10,M9,M8,M7,M6
  in every one of X/Y/Z) with small (~1-9min) within-triple gaps vs large
  (~30min) between-triple gaps -- strong evidence M(n) is the same physical
  run across axes, just exported to file a few minutes apart per channel,
  not three independent recordings. Each axis's channel is peak-normalized
  jointly (one shared peak across all 3 channels, not per-axis) since X/Y/Z
  are the same physical unit (m/s^2) from one sensor, unlike the mic-vs-accel
  channels elsewhere in the pipeline where independent normalization is
  correct. Per-triple axis lengths differ slightly (e.g. Ideal M(1): X=1598,
  Y=3811, Z=4002 samples) so each triple is truncated to its shortest axis
  before windowing, keeping the three channels genuinely time-aligned rather
  than padding a fabricated tail onto the shorter axis.

  Also note: the paper states the true sensor sample rate is 679Hz, not the
  12800Hz --sample-rate-hz defaults to (that constant is borrowed from the
  *live* board's real accelerometer ODR, see satellite_node_sim.py, and only
  matters for spectrum mode's fs *metadata* label since compute_spectrum()'s
  FFT math doesn't depend on it). Both raw modes default --sample-rate-hz to
  679Hz instead, since raw mode's CSV timestamp step is what tells EI's own
  DSP blocks the real sample rate -- getting it wrong would make Spectral
  Analysis compute physically meaningless frequencies. Override with
  --sample-rate-hz if you want something else.

  There is no live-serving counterpart for either raw mode yet (T3-T7 in the
  plan doc are all spectrum-path and unstarted) -- this only affects offline
  training data. Upload raw/raw-triaxial output to a *separate* EI project so
  it doesn't clobber the spectrum baseline in project 1060830.

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
DEFAULT_SAMPLE_RATE_HZ = 12800.0  # spectrum mode: borrowed live-board ODR, fs metadata only
DEFAULT_RAW_SAMPLE_RATE_HZ = 679.0  # raw/raw-triaxial: the dataset's real rate per Khan et al. 2019
TEST_HOLDOUT_EVERY_N = 5  # every Nth file (~20%) held out for test, per class
AXES = ("X", "Y", "Z")


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


def list_axis_triples(class_dir, label):
    """raw-triaxial mode: pairs M(n).csv files across <label>_X/_Y/_Z by
    filename -- confirmed to be the same physical run per axis (see module
    docstring for the "Time" header evidence), not independent recordings.
    Returns a sorted list of (x_path, y_path, z_path) triples."""
    axis_dirs = {axis: os.path.join(class_dir, f"{label}_{axis}") for axis in AXES}
    for axis, d in axis_dirs.items():
        if not os.path.isdir(d):
            raise SystemExit(f"expected axis folder {d!r} not found")
    x_files = {os.path.basename(p): p for p in list_csv_files(axis_dirs["X"])}
    triples = []
    for name in sorted(x_files):
        y_path = os.path.join(axis_dirs["Y"], name)
        z_path = os.path.join(axis_dirs["Z"], name)
        if not (os.path.isfile(y_path) and os.path.isfile(z_path)):
            raise SystemExit(f"axis triple mismatch for {name!r} under {class_dir!r}")
        triples.append((x_files[name], y_path, z_path))
    return triples


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


def write_ei_sample(path, values, timestamp_step_ms=1.0):
    """EI's timestamp-less CSV format only accepts a single data row (one
    column per axis) -- our per-value series needs the time-series format
    instead (a monotonic "timestamp" column + one "accel" column, one row
    per value), so EI treats the whole window as one windowed sample rather
    than N separate one-row samples. timestamp_step_ms defaults to an
    arbitrary-but-consistent 1ms-per-row convention (spectrum mode: these
    aren't real timestamps, just bin positions in a fixed order); raw mode
    passes the true 1/sample_rate step instead, since raw samples do have a
    real time axis and EI's DSP blocks need it to compute correct
    frequencies."""
    with open(path, "w") as f:
        f.write("timestamp,accel\n")
        f.writelines(f"{i * timestamp_step_ms:.6f},{value}\n" for i, value in enumerate(values))


def peak_normalize_signal(window):
    """Peak-normalize a raw time-domain window by its own max absolute
    amplitude, mapping to [-1, 1] -- same "shape over absolute level"
    rationale as pipeline/features.py's normalize_bins() for spectra, but
    signed (raw accel samples can be negative; magnitude spectra can't)."""
    peak = float(np.max(np.abs(window)))
    if peak <= 0:
        return tuple(0.0 for _ in window)
    return tuple(float(v) / peak for v in window)


def peak_normalize_multi(channels):
    """Like peak_normalize_signal(), but one peak shared across all
    channels rather than per-channel -- X/Y/Z are the same physical unit
    (m/s^2) from one accelerometer, so their relative magnitude is real
    signal (unlike mic-vs-accel, which are unrelated units elsewhere in the
    pipeline and are normalized independently)."""
    peak = max(float(np.max(np.abs(c))) for c in channels)
    if peak <= 0:
        return tuple(tuple(0.0 for _ in c) for c in channels)
    return tuple(tuple(float(v) / peak for v in c) for c in channels)


def load_axis_triple(paths):
    """Loads the 3 axis signals for one physical run and truncates all to
    the shortest one's length -- per-axis export lengths differ slightly
    (see module docstring), so this keeps the channels genuinely
    time-aligned instead of fabricating a tail for the shorter axes."""
    signals = [load_signal(p) for p in paths]
    min_len = min(s.size for s in signals)
    return tuple(s[:min_len] for s in signals)


def sliding_windows_multi(signals, window_size, stride):
    length = signals[0].size
    if length < window_size:
        reps = -(-window_size // length)  # ceil division
        signals = tuple(np.tile(s, reps) for s in signals)
        length = signals[0].size
    start = 0
    while start + window_size <= length:
        yield tuple(s[start:start + window_size] for s in signals)
        start += stride


def write_ei_sample_multi(path, channel_values, column_names, timestamp_step_ms):
    """Multi-column variant of write_ei_sample() -- one row per sample
    index, one column per axis, e.g. "timestamp,accel_x,accel_y,accel_z".
    This is EI's native tri-axial-accelerometer CSV shape."""
    with open(path, "w") as f:
        f.write("timestamp," + ",".join(column_names) + "\n")
        for i in range(len(channel_values[0])):
            row = ",".join(str(channel_values[c][i]) for c in range(len(channel_values)))
            f.write(f"{i * timestamp_step_ms:.6f},{row}\n")


def prepare_category_triaxial(triples, stride, label, out_class_dir, window_size, sample_rate_hz):
    os.makedirs(out_class_dir, exist_ok=True)
    timestamp_step_ms = 1000.0 / sample_rate_hz
    count = 0
    for paths in triples:
        signals = load_axis_triple(paths)
        for window in sliding_windows_multi(signals, window_size, stride):
            values = peak_normalize_multi(window)
            write_ei_sample_multi(
                os.path.join(out_class_dir, f"{label}.{count}.csv"), values,
                ("accel_x", "accel_y", "accel_z"), timestamp_step_ms)
            count += 1
    return count


def prepare_category(files, stride, label, out_class_dir, window_size, sample_rate_hz, feature_mode):
    os.makedirs(out_class_dir, exist_ok=True)
    count = 0
    for path in files:
        signal = load_signal(path)
        for window in sliding_windows(signal, window_size, stride):
            if feature_mode == "spectrum":
                spectrum = compute_spectrum(window, sample_rate_hz)
                values = normalize_bins(spectrum.bins)
                timestamp_step_ms = 1.0
            else:
                values = peak_normalize_signal(window)
                timestamp_step_ms = 1000.0 / sample_rate_hz
            write_ei_sample(os.path.join(out_class_dir, f"{label}.{count}.csv"), values, timestamp_step_ms)
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
    parser.add_argument("--sample-rate-hz", type=float, default=None,
                         help="default: 12800Hz for --features spectrum (fs metadata only, doesn't "
                              "affect FFT magnitudes), 679Hz for raw/raw-triaxial (the dataset's real "
                              "rate per Khan et al. 2019 -- sets the CSV timestamp step EI's own DSP "
                              "blocks use as the real sample rate)")
    parser.add_argument("--features", choices=("spectrum", "raw", "raw-triaxial"), default="spectrum",
                         help="'spectrum' (default): peak-normalized 512-bin accel FFT magnitude, "
                              "matching the live pipeline, single-axis/pooled. 'raw': peak-normalized "
                              "1024-sample raw time-domain window, no FFT, still single-axis/pooled. "
                              "'raw-triaxial': like raw, but pairs each run's X/Y/Z files into one "
                              "3-channel window (see module docstring for why this pairing is valid). "
                              "Upload raw/raw-triaxial output to a separate EI project, don't mix it "
                              "into the spectrum project.")
    args = parser.parse_args()
    train_stride = args.train_stride or max(1, args.window_size // 8)
    test_stride = args.test_stride or max(1, args.window_size // 4)
    if args.sample_rate_hz is not None:
        sample_rate_hz = args.sample_rate_hz
    else:
        sample_rate_hz = DEFAULT_SAMPLE_RATE_HZ if args.features == "spectrum" else DEFAULT_RAW_SAMPLE_RATE_HZ

    classes = list_class_dirs(args.data_dir)
    if not classes:
        parser.error(f"no class folders found under {args.data_dir!r}")

    grand_totals = {"training": 0, "testing": 0}
    for label in classes:
        class_dir = os.path.join(args.data_dir, label)
        if args.features == "raw-triaxial":
            items = list_axis_triples(class_dir, label)
        else:
            items = list_csv_files(class_dir)
        train_items, test_items = split_files(items)

        if args.features == "raw-triaxial":
            train_count = prepare_category_triaxial(
                train_items, train_stride, label,
                os.path.join(args.out_dir, "training", label), args.window_size, sample_rate_hz)
            test_count = prepare_category_triaxial(
                test_items, test_stride, label,
                os.path.join(args.out_dir, "testing", label), args.window_size, sample_rate_hz)
        else:
            train_count = prepare_category(
                train_items, train_stride, label,
                os.path.join(args.out_dir, "training", label), args.window_size, sample_rate_hz,
                args.features)
            test_count = prepare_category(
                test_items, test_stride, label,
                os.path.join(args.out_dir, "testing", label), args.window_size, sample_rate_hz,
                args.features)

        print(f"{label}: {len(train_items)} train files -> {train_count} samples, "
              f"{len(test_items)} test files -> {test_count} samples", flush=True)
        grand_totals["training"] += train_count
        grand_totals["testing"] += test_count

    print(f"Total: {grand_totals['training']} training + {grand_totals['testing']} testing "
          f"samples written under {args.out_dir}", flush=True)


if __name__ == "__main__":
    main()
