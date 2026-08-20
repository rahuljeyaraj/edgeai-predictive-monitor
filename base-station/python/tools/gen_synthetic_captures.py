#!/usr/bin/env python3
"""Generates a couple of synthetic .npz files in the exact shape
tools/raw_capture.py writes (accel_x_raw/accel_y_raw/accel_z_raw:
(num_windows, 1024) float32, mic_raw: (num_windows, 2048) float32, each with
an "<channel>_fs" scalar companion, plus a "label" scalar string) -- for
smoke-testing tools/satellite_node_sim.py / ../start_desktop_dashboard.sh
when no real captures are available under base-station/captures/. Not
scientifically meaningful data, just enough shape/variance for the
dashboard's pipeline (gate/features/autoencoder) to have something
non-degenerate to chew on.

    python3 gen_synthetic_captures.py --out-dir ~/.cache/epm-sim-data
"""
import argparse
import os

import numpy as np

NUM_WINDOWS = 200  # enough to loop through without an obvious short cycle
ACCEL_WINDOW_SAMPLES = 1024
MIC_WINDOW_SAMPLES = 2048
ACCEL_FS_HZ = 6400.0
MIC_FS_HZ = 48000.0


def gen_healthy(rng: np.random.Generator, num_windows: int, samples: int) -> np.ndarray:
    return rng.normal(0.0, 1.0, size=(num_windows, samples)).astype(np.float32)


def gen_idle(rng: np.random.Generator, num_windows: int, samples: int) -> np.ndarray:
    """Motor stopped -- just sensor noise floor, ~20x lower amplitude than
    gen_healthy's running signal. For the commissioning wizard's stopped-
    baseline step (api/stopped_baseline_controller.py), which needs a
    capture file that reads as genuinely stopped, not just "healthy"."""
    return rng.normal(0.0, 0.05, size=(num_windows, samples)).astype(np.float32)


def gen_fault(rng: np.random.Generator, num_windows: int, samples: int,
              period: int = 37, spike: float = 8.0) -> np.ndarray:
    signal = rng.normal(0.0, 1.0, size=(num_windows, samples)).astype(np.float32)
    signal[:, ::period] += spike
    return signal


def _write_capture(path: str, gen_fn, label: str, rng: np.random.Generator, num_windows: int) -> None:
    data = {"label": label}
    for name in ("accel_x_raw", "accel_y_raw", "accel_z_raw"):
        data[name] = gen_fn(rng, num_windows, ACCEL_WINDOW_SAMPLES)
        data[f"{name}_fs"] = np.float32(ACCEL_FS_HZ)
    data["mic_raw"] = gen_fn(rng, num_windows, MIC_WINDOW_SAMPLES)
    data["mic_raw_fs"] = np.float32(MIC_FS_HZ)
    np.savez(path, **data)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--num-windows", type=int, default=NUM_WINDOWS)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--force", action="store_true",
                         help="Regenerate even if the target files already exist")
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    rng = np.random.default_rng(args.seed)

    targets = {
        "healthy.npz": (gen_healthy, "healthy"),
        "fault.npz": (gen_fault, "fault"),
        "idle.npz": (gen_idle, "idle"),
    }
    for name, (gen_fn, label) in targets.items():
        path = os.path.join(args.out_dir, name)
        if os.path.exists(path) and not args.force:
            print(f"{path} already exists -- skipping (--force to regenerate)")
            continue
        _write_capture(path, gen_fn, label, rng, args.num_windows)
        print(f"wrote {path} ({args.num_windows} windows)")


if __name__ == "__main__":
    main()
