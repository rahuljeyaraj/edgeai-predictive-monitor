#!/usr/bin/env python3
"""Offline experiment harness for raw sensor captures
(docs/SENSOR_TELEMETRY_FRAME_PLAN.md, tools/raw_capture.py).

Loads the .npz files raw_capture.py produces (one file per rig-state label,
pulled off the device with adb) and tries feature-engineering choices --
FFT bin count, accel axis fusion (summed vs 3 separate axes), with/without
mic, +rms/+kurtosis scalars -- entirely in numpy on a laptop, training the
project's actual autoencoder (pipeline/autoencoder.py) on each config's
healthy windows and scoring every label's reconstruction error against it.
This is the point of capturing raw instead of spectra: every combination
below is tried from ONE recording, no firmware rebuild/reflash per combo.

Mirrors pipeline/commissioning.py's own calibration exactly (population
mu/sigma over the healthy batch the model trained on, same warning=8sigma/
fault=15sigma reference points) so a config's separation number here means
the same thing it would after real commissioning.

Usage:
  .venv/bin/python3 tools/offline_experiment.py --captures-dir ../../captures

  # try one specific config
  .venv/bin/python3 tools/offline_experiment.py --captures-dir ../../captures \\
      --axis-mode separate --bin-count 64 --scalars rms kurtosis

  # sweep a small grid of configs and rank by separation
  .venv/bin/python3 tools/offline_experiment.py --captures-dir ../../captures --sweep
"""
import argparse
import glob
import os
import statistics
import sys

import numpy as np
import torch

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "pipeline"))

# tools/ -> python/ -> base-station/ -> base-station/captures/, alongside
# pull_captures.sh - resolved relative to this file, not the caller's cwd, so
# `python3 offline_experiment.py --sweep` just works with no path argument
# regardless of where it's run from.
_DEFAULT_CAPTURES_DIR = os.path.join(_HERE, "..", "..", "captures")

from autoencoder import build_autoencoder, reconstruction_error, train_autoencoder  # noqa: E402

ACCEL_AXES = ("accel_x_raw", "accel_y_raw", "accel_z_raw")
MIC_CHANNEL = "mic_raw"
ALL_CHANNELS = ACCEL_AXES + (MIC_CHANNEL,)

# Same reference points as pipeline/commissioning.py's _WARNING_SIGMA/_FAULT_SIGMA
# -- a config's separation is reported against these so the number here means
# the same thing production commissioning would compute.
_WARNING_SIGMA = 8.0
_FAULT_SIGMA = 15.0


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_captures(captures_dir):
    """{label: [run, ...]}, one `run` dict per .npz file (channel name ->
    (num_windows, samples) float64 array). Runs are kept separate, never
    merged across files, so windows are only ever index-paired across
    channels *within* one capture session -- raw_capture.py's channels
    arrive at different cadences (accel every ~640ms, mic separately), so
    the i-th accel window and i-th mic window are only a fair "same rig
    state" pairing inside a single run, not across concatenated files."""
    by_label = {}
    paths = sorted(glob.glob(os.path.join(captures_dir, "*.npz")))
    if not paths:
        raise SystemExit(f"no .npz captures found in {captures_dir!r}")
    for path in paths:
        data = np.load(path)
        label = str(data["label"])
        run = {name: data[name].astype(np.float64)
               for name in ALL_CHANNELS if name in data}
        by_label.setdefault(label, []).append(run)
    return by_label


# ---------------------------------------------------------------------------
# Feature engineering (mirrors accel_sampler.cpp/mic_sampler.cpp/features.py)
# ---------------------------------------------------------------------------

def fft_magnitude(window: np.ndarray) -> np.ndarray:
    """Same convention as the firmware's *_fft_magnitude(): DC (bin 0)
    dropped, bins 1..N/2 kept -- N/2 bins total for an N-sample window."""
    return np.abs(np.fft.rfft(window))[1:]


def downsample(mag: np.ndarray, bin_count: int) -> np.ndarray:
    """Average-pool down to bin_count buckets, same scheme as the firmware's
    accel_spectrum_downsample()/get_mic_spectrum(). len(mag) must divide
    evenly by bin_count."""
    if len(mag) % bin_count != 0:
        raise ValueError(f"{len(mag)} FFT bins doesn't divide evenly by "
                          f"--bin-count={bin_count} (divisors of {len(mag)}: "
                          f"{[d for d in range(1, len(mag) + 1) if len(mag) % d == 0]})")
    factor = len(mag) // bin_count
    return mag.reshape(bin_count, factor).mean(axis=1)


def peak_normalize(bins: np.ndarray) -> np.ndarray:
    """Same as pipeline/features.py's normalize_bins -- rescale to this
    block's own peak so absolute amplitude (motor load, placement, mic gain)
    doesn't swamp the shape the autoencoder needs to learn."""
    peak = bins.max()
    if peak <= 0:
        return np.zeros_like(bins)
    return bins / peak


def kurtosis(x: np.ndarray) -> float:
    std = x.std()
    if std <= 0:
        return 0.0
    return float(np.mean(((x - x.mean()) / std) ** 4) - 3.0)  # excess kurtosis


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x ** 2)))


_SCALAR_FUNCS = {"rms": rms, "kurtosis": kurtosis}


class FeatureConfig:
    def __init__(self, axis_mode="summed", bin_count=32, mic_bin_count=None,
                 include_mic=True, scalars=()):
        if axis_mode not in ("summed", "separate", "none"):
            raise ValueError("axis_mode must be 'summed', 'separate', or 'none'")
        if axis_mode == "none" and not include_mic:
            raise ValueError("axis_mode='none' with include_mic=False leaves no "
                              "channels at all")
        self.axis_mode = axis_mode
        self.include_accel = axis_mode != "none"
        self.bin_count = bin_count
        self.mic_bin_count = mic_bin_count if mic_bin_count is not None else bin_count
        self.include_mic = include_mic
        self.scalars = tuple(scalars)

    def __str__(self):
        bits = ["axis=none-mic-only"] if not self.include_accel else \
            [f"axis={self.axis_mode}", f"bins={self.bin_count}"]
        bits.append(f"mic_bins={self.mic_bin_count}" if self.include_mic else "no-mic")
        bits.append("scalars=" + "+".join(self.scalars) if self.scalars else "no-scalars")
        return " ".join(bits)


def _spectrum_block(raw: np.ndarray, bin_count: int) -> np.ndarray:
    """raw: (num_windows, samples) -> (num_windows, bin_count) peak-normalized
    per window."""
    spectra = np.stack([downsample(fft_magnitude(raw[i]), bin_count)
                         for i in range(raw.shape[0])])
    return np.stack([peak_normalize(spectra[i]) for i in range(spectra.shape[0])])


def build_run_vectors(run: dict, cfg: FeatureConfig):
    """One run's channel arrays -> (num_windows, dim) float32 feature
    vectors, num_windows truncated to the shortest channel this config
    actually uses (accel axes should already match each other; mic can
    differ in count, see load_captures's docstring)."""
    needed = (list(ACCEL_AXES) if cfg.include_accel else []) + \
        ([MIC_CHANNEL] if cfg.include_mic else [])
    missing = [c for c in needed if c not in run]
    if missing:
        return None  # this run doesn't have a channel this config needs
    n = min(run[c].shape[0] for c in needed)
    if n == 0:
        return None

    accel = {axis: run[axis][:n] for axis in ACCEL_AXES} if cfg.include_accel else {}

    spectral_parts = []
    if cfg.include_accel:
        if cfg.axis_mode == "summed":
            combined_mag = sum(
                np.stack([downsample(fft_magnitude(accel[axis][i]), cfg.bin_count)
                          for i in range(n)])
                for axis in ACCEL_AXES)
            accel_part = np.stack([peak_normalize(combined_mag[i]) for i in range(n)])
        else:  # separate
            accel_part = np.concatenate(
                [_spectrum_block(accel[axis], cfg.bin_count) for axis in ACCEL_AXES], axis=1)
        spectral_parts.append(accel_part)
    if cfg.include_mic:
        spectral_parts.append(_spectrum_block(run[MIC_CHANNEL][:n], cfg.mic_bin_count))

    scalar_parts = []
    if cfg.scalars:
        if cfg.include_accel:
            for axis in ACCEL_AXES:
                for name in cfg.scalars:
                    fn = _SCALAR_FUNCS[name]
                    scalar_parts.append(np.array([fn(accel[axis][i]) for i in range(n)]))
        if cfg.include_mic:
            mic = run[MIC_CHANNEL][:n]
            for name in cfg.scalars:
                fn = _SCALAR_FUNCS[name]
                scalar_parts.append(np.array([fn(mic[i]) for i in range(n)]))

    spectral_dim = sum(p.shape[1] for p in spectral_parts)
    if scalar_parts:
        scalar_block = np.stack(scalar_parts, axis=1)
        vectors = np.concatenate(spectral_parts + [scalar_block], axis=1)
    else:
        vectors = np.concatenate(spectral_parts, axis=1)
    return vectors.astype(np.float32), spectral_dim


def build_label_vectors(runs, cfg: FeatureConfig):
    """runs: list of per-file run dicts for one label -> (num_windows, dim)
    float32 array (concatenated across runs), plus the spectral/scalar
    column split (same for every run of a given config)."""
    per_run = []
    spectral_dim = None
    for run in runs:
        result = build_run_vectors(run, cfg)
        if result is None:
            continue
        vectors, dim = result
        spectral_dim = dim
        per_run.append(vectors)
    if not per_run:
        return None, None
    return np.concatenate(per_run, axis=0), spectral_dim


def standardize_scalars(vectors: np.ndarray, spectral_dim: int, mu, sigma) -> np.ndarray:
    """z-score the scalar tail columns (rms/kurtosis are not naturally in
    [0,1] the way peak-normalized spectrum bins are) using stats fit on the
    healthy training set only; spectral columns are left untouched."""
    if spectral_dim >= vectors.shape[1]:
        return vectors
    out = vectors.copy()
    tail = out[:, spectral_dim:]
    safe_sigma = np.where(sigma > 1e-9, sigma, 1.0)
    out[:, spectral_dim:] = (tail - mu) / safe_sigma
    return out


# ---------------------------------------------------------------------------
# Train + score one config
# ---------------------------------------------------------------------------

def run_config(by_label, cfg: FeatureConfig, healthy_label, epochs, seed):
    vectors_by_label = {}
    spectral_dim = None
    for label, runs in by_label.items():
        vectors, dim = build_label_vectors(runs, cfg)
        if vectors is None:
            continue
        spectral_dim = dim
        vectors_by_label[label] = vectors

    if healthy_label not in vectors_by_label:
        raise SystemExit(f"no usable windows for healthy label {healthy_label!r} "
                          f"under config ({cfg}) -- available labels: "
                          f"{sorted(vectors_by_label)}")

    healthy_raw = vectors_by_label[healthy_label]
    if cfg.scalars:
        tail = healthy_raw[:, spectral_dim:]
        mu, sigma = tail.mean(axis=0), tail.std(axis=0)
    else:
        mu = sigma = None

    scaled = {}
    for label, vectors in vectors_by_label.items():
        scaled[label] = (standardize_scalars(vectors, spectral_dim, mu, sigma)
                          if cfg.scalars else vectors)

    torch.manual_seed(seed)
    input_dim = scaled[healthy_label].shape[1]
    model = build_autoencoder(input_dim)
    healthy_vectors = [tuple(row) for row in scaled[healthy_label]]
    train_autoencoder(model, healthy_vectors, epochs=epochs)

    scores_by_label = {
        label: [reconstruction_error(model, tuple(row)) for row in vectors]
        for label, vectors in scaled.items()
    }
    return scores_by_label, input_dim


def summarize(scores_by_label, healthy_label):
    healthy_scores = scores_by_label[healthy_label]
    mu = statistics.fmean(healthy_scores)
    sigma = statistics.pstdev(healthy_scores)  # population: this batch IS the baseline

    per_label = {}
    for label, scores in scores_by_label.items():
        label_mu = statistics.fmean(scores)
        label_sigma = statistics.pstdev(scores) if len(scores) > 1 else 0.0
        sigmas_above = (label_mu - mu) / sigma if sigma > 0 else float("inf")
        per_label[label] = {
            "n": len(scores), "mean": label_mu, "std": label_sigma,
            "min": min(scores), "max": max(scores), "sigmas_above_healthy": sigmas_above,
        }

    fault_labels = [l for l in per_label if l != healthy_label]
    worst_sigma = (min(per_label[l]["sigmas_above_healthy"] for l in fault_labels)
                   if fault_labels else None)
    return {"healthy_mu": mu, "healthy_sigma": sigma, "per_label": per_label,
            "worst_sigma": worst_sigma}


def save_diagnostic_plot(path, by_label, cfg: FeatureConfig, scores_by_label, summary):
    """Three panels, sharing one color per label: (1) one example raw window
    per label for a representative channel, (2) that window's spectrum as
    the model actually sees it (binned + peak-normalized), (3) the
    reconstruction-error (anomaly score) distribution per label with the
    healthy mean and the 8sigma/15sigma warning/fault reference lines
    (summarize()'s same thresholds). Point is to see input -> features ->
    score in one place, not just the score table print_report() gives."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    channel = ACCEL_AXES[0] if cfg.include_accel else MIC_CHANNEL
    bin_count = cfg.bin_count if cfg.include_accel else cfg.mic_bin_count

    labels = sorted(by_label)
    colors = {label: c for label, c in zip(labels, plt.cm.tab10.colors)}

    fig, (ax_raw, ax_spec, ax_score) = plt.subplots(3, 1, figsize=(10, 12))

    for label in labels:
        run = next((r for r in by_label[label] if channel in r and r[channel].shape[0] > 0), None)
        if run is None:
            continue
        window = run[channel][0]
        ax_raw.plot(window, label=label, color=colors[label], alpha=0.8)
        mag = peak_normalize(downsample(fft_magnitude(window), bin_count))
        ax_spec.plot(mag, label=label, color=colors[label], alpha=0.8)

    ax_raw.set_title(f"Example raw window - channel: {channel}")
    ax_raw.set_xlabel("sample")
    ax_raw.set_ylabel("raw value")
    ax_raw.legend(fontsize="small")

    ax_spec.set_title(f"Same window's spectrum ({bin_count} bins, peak-normalized) - what the model sees")
    ax_spec.set_xlabel("bin")
    ax_spec.set_ylabel("normalized magnitude")
    ax_spec.legend(fontsize="small")

    all_scores = [s for scores in scores_by_label.values() for s in scores]
    use_log = all(s > 0 for s in all_scores)
    for label in labels:
        scores = scores_by_label.get(label)
        if not scores:
            continue
        ax_score.hist(scores, bins=20, alpha=0.5, label=f"{label} (n={len(scores)})",
                      color=colors[label])
    mu, sigma = summary["healthy_mu"], summary["healthy_sigma"]
    ax_score.axvline(mu, color="black", linestyle="--", linewidth=1, label="healthy mean")
    if sigma > 0:
        ax_score.axvline(mu + _WARNING_SIGMA * sigma, color="orange", linestyle="--",
                         linewidth=1, label=f"{_WARNING_SIGMA:.0f}sigma warning")
        ax_score.axvline(mu + _FAULT_SIGMA * sigma, color="red", linestyle="--",
                         linewidth=1, label=f"{_FAULT_SIGMA:.0f}sigma fault")
    if use_log:
        ax_score.set_xscale("log")
    ax_score.set_title("Anomaly score (reconstruction error) by label")
    ax_score.set_xlabel("score" + (" (log scale)" if use_log else ""))
    ax_score.set_ylabel("count")
    ax_score.legend(fontsize="small")

    fig.suptitle(str(cfg))
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def print_report(cfg, input_dim, summary, healthy_label):
    print(f"\n=== {cfg}  (input_dim={input_dim}) ===")
    print(f"healthy baseline: mu={summary['healthy_mu']:.6g} sigma={summary['healthy_sigma']:.3g}")
    for label, s in sorted(summary["per_label"].items()):
        marker = " (healthy)" if label == healthy_label else \
            f"  {s['sigmas_above_healthy']:+.1f}sigma"
        print(f"  {label:<16} n={s['n']:<4} mean={s['mean']:.6g} std={s['std']:.3g} "
              f"range=[{s['min']:.6g}, {s['max']:.6g}]{marker}")
    if summary["worst_sigma"] is not None:
        ws = summary["worst_sigma"]
        verdict = ("clears FAULT (15sigma)" if ws >= _FAULT_SIGMA else
                   "clears WARNING (8sigma) only" if ws >= _WARNING_SIGMA else
                   "below WARNING threshold -- would not be flagged")
        print(f"  worst-case separation: {ws:+.1f}sigma -- {verdict}")
    else:
        print("  (no non-healthy label captured yet -- can't compute separation)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _sweep_configs():
    for axis_mode in ("summed", "separate", "none"):
        for bin_count in (16, 32, 64):
            for include_mic in (True, False):
                if axis_mode == "none" and not include_mic:
                    continue  # no channels left at all - not a valid config
                for scalars in ((), ("rms", "kurtosis")):
                    yield FeatureConfig(axis_mode=axis_mode, bin_count=bin_count,
                                        include_mic=include_mic, scalars=scalars)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--captures-dir", default=_DEFAULT_CAPTURES_DIR,
                         help="directory of raw_capture.py .npz files (adb-pulled), "
                              f"default: {os.path.relpath(_DEFAULT_CAPTURES_DIR, _HERE)} "
                              "(base-station/captures/, alongside pull_captures.sh)")
    parser.add_argument("--healthy-label", default="healthy",
                         help="label whose windows train the autoencoder (default: healthy)")
    parser.add_argument("--axis-mode", choices=("summed", "separate", "none"), default="summed",
                         help="accel 3-axis fusion: bin-sum (current firmware behavior), "
                              "keep axes as separate concatenated blocks, or 'none' to "
                              "exclude accel entirely (mic-only; the accel-side symmetric "
                              "counterpart to --exclude-mic)")
    parser.add_argument("--bin-count", type=int, default=32,
                         help="downsampled accel spectrum bins (must evenly divide 512 "
                              "for the current 1024-sample accel window)")
    parser.add_argument("--mic-bin-count", type=int, default=None,
                         help="downsampled mic spectrum bins (default: same as --bin-count; "
                              "must evenly divide 1024 for the current 2048-sample mic window)")
    parser.add_argument("--exclude-mic", action="store_true",
                         help="accel-only feature vector")
    parser.add_argument("--scalars", nargs="*", choices=("rms", "kurtosis"), default=(),
                         help="append per-axis time-domain scalar(s) to the feature vector")
    parser.add_argument("--epochs", type=int, default=300,
                         help="autoencoder training epochs (default matches "
                              "pipeline/commissioning.py's own default)")
    parser.add_argument("--seed", type=int, default=0, help="torch seed, for repeatable comparisons")
    parser.add_argument("--sweep", action="store_true",
                         help="ignore --axis-mode/--bin-count/etc. and try a small grid of "
                              "configs, ranked by worst-case healthy/fault separation")
    parser.add_argument("--plot-out",
                         help="save a PNG with 3 panels (example raw window, its spectrum, "
                              "and the healthy-vs-fault score distribution with the 8sigma/"
                              "15sigma reference lines) for the config that ran. With "
                              "--sweep, plots the best-ranked config instead of every one.")
    args = parser.parse_args()

    by_label = load_captures(args.captures_dir)
    window_counts = {label: sum(len(next(iter(r.values()))) for r in runs)
                      for label, runs in by_label.items()}
    print(f"loaded labels: {window_counts}")

    if args.sweep:
        results = []
        for cfg in _sweep_configs():
            try:
                scores_by_label, input_dim = run_config(
                    by_label, cfg, args.healthy_label, args.epochs, args.seed)
            except SystemExit as e:
                print(f"skip {cfg}: {e}")
                continue
            summary = summarize(scores_by_label, args.healthy_label)
            print_report(cfg, input_dim, summary, args.healthy_label)
            results.append((cfg, input_dim, summary, scores_by_label))

        ranked = [r for r in results if r[2]["worst_sigma"] is not None]
        if ranked:
            ranked.sort(key=lambda r: r[2]["worst_sigma"], reverse=True)
            print("\n=== ranked by worst-case separation (higher = better) ===")
            for cfg, input_dim, summary, _ in ranked:
                print(f"  {summary['worst_sigma']:+7.1f}sigma  dim={input_dim:<5} {cfg}")
            if args.plot_out:
                best_cfg, _, best_summary, best_scores = ranked[0]
                save_diagnostic_plot(args.plot_out, by_label, best_cfg, best_scores, best_summary)
                print(f"\nSaved best-config plot ({best_cfg}) -> {args.plot_out}")
        return

    cfg = FeatureConfig(axis_mode=args.axis_mode, bin_count=args.bin_count,
                         mic_bin_count=args.mic_bin_count, include_mic=not args.exclude_mic,
                         scalars=args.scalars)
    scores_by_label, input_dim = run_config(by_label, cfg, args.healthy_label, args.epochs, args.seed)
    summary = summarize(scores_by_label, args.healthy_label)
    print_report(cfg, input_dim, summary, args.healthy_label)
    if args.plot_out:
        save_diagnostic_plot(args.plot_out, by_label, cfg, scores_by_label, summary)
        print(f"\nSaved plot -> {args.plot_out}")


if __name__ == "__main__":
    main()
