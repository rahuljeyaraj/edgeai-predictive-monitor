#!/usr/bin/env python3
"""Prep tools/raw_capture.py .npz captures (base-station/captures/) for Edge
Impulse upload, as flat feature vectors instead of raw time-series -- unlike
ei_dataset_prep.py's Kaggle-CSV pipeline, these captures carry accel (x/y/z,
1024 samples/window @ 12800Hz) and mic (2048 samples/window @ 96000Hz)
channels that can't share one time-series sample (different window
durations: 80ms vs ~21ms). Converting both to fixed-length spectra sidesteps
that -- every channel becomes the same bin_count regardless of its raw
window length.

Per-window feature vector, in SensorChannel declaration order (mic, accel_x,
accel_y, accel_z -- matches pipeline/features.py's build_feature_vector so
column order isn't arbitrary): each channel's peak-normalized bin_count-bin
FFT magnitude spectrum (mic trimmed to its useful sub-Fs/4 half first, same
as raw_features.mic_useful_magnitude -- see that function's docstring),
followed by each channel's 6 raw time-domain scalars (rms/kurtosis/std/peak/
crest_factor/skewness, common/raw_features.py -- same functions
tools/offline_experiment.py uses, so there's no drift from the project's own
validated feature engineering). Axes are kept separate (never summed) per
offline_experiment.py's own finding: summing x/y/z erases the directional
signature an imbalance fault produces (+1.8sigma vs +38.5sigma per-axis on
real captures).

One .npz per label (raw_capture.py's convention: one continuous rig-state
recording per file) means there's no file-level train/test split available
the way the leakage-safe Kaggle pipeline does it (see
edge-impulse-classifier-leakage-and-results). Instead each label's windows
are split as one contiguous block: the first (1 - test_fraction) windows ->
train, the last test_fraction windows -> test. A contiguous tail, not
interleaved samples -- adjacent windows in one continuous recording are
highly correlated, so interleaving would leak train information into test
almost as badly as a window-level split from a single file did before.

The scalar tail is NOT naturally in [0, 1] the way peak-normalized spectrum
bins are (e.g. a raw accel "peak" scalar can be in the thousands) -- left
raw, it swamps the spectral columns in any distance- or gradient-based
model. Standardized the same way production does it
(pipeline/commissioning.py, pipeline/features.py's standardize_scalars()):
z-score using mean/std fit on the healthy label's TRAINING windows only
(the commissioning-batch role), then apply that fixed mu/sigma to every
vector -- every label, both train and test. This mirrors production exactly
(one fixed baseline computed once, then applied to all future frames
whatever their true label) rather than being test-set leakage.

Each sample is written the same "timestamp,feature" one-column-per-row CSV
convention as ei_dataset_prep.py's write_ei_sample() (EI's timestamp-less
CSV only accepts a single data row; this format is what tells EI's parser
"the whole window is one sample" instead of len(vector) separate samples).
The timestamp step is an arbitrary-but-consistent 1ms/row -- these rows are
feature-vector positions, not a real time axis, same as the Kaggle
pipeline's spectrum mode.

Usage:
  cd base-station/python
  .venv/bin/python3 tools/ei_capture_upload.py --out-dir /tmp/ei_capture_prepared
  EI_API_KEY=ei_xxx tools/ei_upload.sh /tmp/ei_capture_prepared
"""
import argparse
import glob
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "common"))

from raw_features import (  # noqa: E402
    fft_magnitude, mic_useful_magnitude, downsample, peak_normalize,
    rms, kurtosis, std, peak, crest_factor, skewness,
)

# alongside pull_captures.sh, resolved relative to this file so the default
# just works regardless of caller cwd (same convention as offline_experiment.py)
_DEFAULT_CAPTURES_DIR = os.path.join(_HERE, "..", "..", "captures")

ACCEL_AXES = ("accel_x_raw", "accel_y_raw", "accel_z_raw")
MIC_CHANNEL = "mic_raw"
# mic first, matching registry.SensorChannel declaration order (features.py's
# build_feature_vector iterates channels in this order) -- not alphabetical.
CHANNEL_ORDER = (MIC_CHANNEL,) + ACCEL_AXES

SCALAR_FUNCS = (
    ("rms", rms), ("kurtosis", kurtosis), ("std", std),
    ("peak", peak), ("crest_factor", crest_factor), ("skewness", skewness),
)

DEFAULT_BIN_COUNT = 128
DEFAULT_TEST_FRACTION = 0.2


def channel_spectrum(window: np.ndarray, bin_count: int, is_mic: bool) -> np.ndarray:
    mag = fft_magnitude(window)
    if is_mic:
        mag = mic_useful_magnitude(mag)
    return peak_normalize(downsample(mag, bin_count))


def build_window_vectors(run: dict, bin_count: int) -> np.ndarray:
    """run: channel name -> (num_windows, samples) float64 array (all
    CHANNEL_ORDER entries must be present -- raw_capture.py always writes
    all 4 raw channels together). Returns (n, dim) float32, n = min window
    count across channels (index-paired within this one run only -- fair
    "same rig state" pairing, not necessarily the same instant, since
    fuser.cpp alternates one accel frame / one mic frame per epoch; see
    offline_experiment.py's load_captures() docstring for the same
    reasoning already validated in this project)."""
    missing = [c for c in CHANNEL_ORDER if c not in run]
    if missing:
        raise SystemExit(f"capture is missing channel(s) {missing!r} -- run: {sorted(run)}")
    n = min(run[c].shape[0] for c in CHANNEL_ORDER)

    spectral_cols = []
    for channel in CHANNEL_ORDER:
        is_mic = channel == MIC_CHANNEL
        spectral_cols.append(np.stack(
            [channel_spectrum(run[channel][i], bin_count, is_mic) for i in range(n)]))
    spectral = np.concatenate(spectral_cols, axis=1)

    scalar_cols = []
    for channel in CHANNEL_ORDER:
        for _name, fn in SCALAR_FUNCS:
            scalar_cols.append(np.array([fn(run[channel][i]) for i in range(n)]))
    scalars = np.stack(scalar_cols, axis=1)

    return np.concatenate([spectral, scalars], axis=1).astype(np.float32)


def split_contiguous(vectors: np.ndarray, test_fraction: float):
    n = vectors.shape[0]
    n_test = max(1, round(n * test_fraction)) if n > 1 else 0
    n_train = n - n_test
    return vectors[:n_train], vectors[n_train:]


def standardize_scalars(vectors: np.ndarray, spectral_dim: int,
                         mu: np.ndarray, sigma: np.ndarray) -> np.ndarray:
    """z-score the scalar tail columns (spectral_dim: onward) using mu/sigma
    fit elsewhere (the healthy label's training windows) -- same formula as
    pipeline/features.py's standardize_scalars(), just batched over an (n,
    dim) array instead of one row at a time."""
    out = vectors.copy()
    tail = out[:, spectral_dim:]
    safe_sigma = np.where(sigma > 1e-9, sigma, 1.0)
    out[:, spectral_dim:] = (tail - mu) / safe_sigma
    return out


def write_ei_sample(path: str, values: np.ndarray, timestamp_step_ms: float = 1.0):
    """Same convention as ei_dataset_prep.py's write_ei_sample(): a
    "timestamp,feature" time-series CSV, one row per vector value -- tells
    EI's ingestion parser this is one windowed sample, not len(values)
    separate one-row samples."""
    with open(path, "w") as f:
        f.write("timestamp,feature\n")
        f.writelines(f"{i * timestamp_step_ms:.6f},{v}\n" for i, v in enumerate(values))


def load_label_vectors(path: str, bin_count: int):
    data = np.load(path)
    label = str(data["label"])
    run = {name: data[name].astype(np.float64) for name in CHANNEL_ORDER if name in data}
    return label, build_window_vectors(run, bin_count)


def write_category(vectors: np.ndarray, label: str, category: str, out_dir: str) -> int:
    out_class_dir = os.path.join(out_dir, category, label)
    os.makedirs(out_class_dir, exist_ok=True)
    for i, vec in enumerate(vectors):
        write_ei_sample(os.path.join(out_class_dir, f"{label}.{i}.csv"), vec)
    return len(vectors)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--captures-dir", default=_DEFAULT_CAPTURES_DIR,
                         help=f"default: {os.path.relpath(_DEFAULT_CAPTURES_DIR, _HERE)}")
    parser.add_argument("--out-dir", required=True,
                         help="where to write {training,testing}/<label>/<label>.<n>.csv, "
                              "for tools/ei_upload.sh to upload")
    parser.add_argument("--bin-count", type=int, default=DEFAULT_BIN_COUNT,
                         help="peak-normalized FFT bins per channel (mic + each accel axis), "
                              f"default {DEFAULT_BIN_COUNT} -- must evenly divide 512 "
                              "(the accel window's unique FFT bin count, and the mic's "
                              "useful sub-Fs/4 half)")
    parser.add_argument("--test-fraction", type=float, default=DEFAULT_TEST_FRACTION,
                         help=f"fraction of each label's windows (contiguous tail) held out "
                              f"for test, default {DEFAULT_TEST_FRACTION}")
    parser.add_argument("--healthy-label", default="healthy",
                         help="label whose TRAINING windows fit the scalar-tail mu/sigma "
                              "(the commissioning-batch role), applied to every label's "
                              "vectors -- default 'healthy'")
    args = parser.parse_args()

    paths = sorted(glob.glob(os.path.join(args.captures_dir, "*.npz")))
    if not paths:
        parser.error(f"no .npz captures found in {args.captures_dir!r}")

    spectral_dim = args.bin_count * len(CHANNEL_ORDER)
    per_label = {}
    for path in paths:
        label, vectors = load_label_vectors(path, args.bin_count)
        per_label[label] = split_contiguous(vectors, args.test_fraction)

    if args.healthy_label not in per_label:
        parser.error(f"--healthy-label {args.healthy_label!r} not found among captured "
                      f"labels: {sorted(per_label)}")
    healthy_train_tail = per_label[args.healthy_label][0][:, spectral_dim:]
    mu = healthy_train_tail.mean(axis=0)
    sigma = healthy_train_tail.std(axis=0)

    grand_totals = {"training": 0, "testing": 0}
    for label, (train_vectors, test_vectors) in per_label.items():
        train_std = standardize_scalars(train_vectors, spectral_dim, mu, sigma)
        test_std = standardize_scalars(test_vectors, spectral_dim, mu, sigma)
        n_train = write_category(train_std, label, "training", args.out_dir)
        n_test = write_category(test_std, label, "testing", args.out_dir)
        print(f"{label}: {n_train} training + {n_test} testing samples "
              f"(dim={spectral_dim + len(SCALAR_FUNCS) * len(CHANNEL_ORDER)})", flush=True)
        grand_totals["training"] += n_train
        grand_totals["testing"] += n_test

    print(f"Total: {grand_totals['training']} training + {grand_totals['testing']} testing "
          f"samples written under {args.out_dir}, scalar tail z-scored against "
          f"{args.healthy_label!r}'s training windows", flush=True)


if __name__ == "__main__":
    main()
