#!/usr/bin/env python3
"""Generates a couple of synthetic CSVs in the exact shape satellite_node_sim.py's
load_signal() expects (free-text metadata lines, then a "Sweeps,Channel N" marker
line, then "<idx>,<amplitude>" rows) -- for smoke-testing the desktop dashboard
(../../start_desktop_dashboard.sh) when the real Kaggle vibration-fault-diagnosis
dataset isn't downloaded on this machine. Not scientifically meaningful data, just
enough shape/variance for the dashboard's pipeline (gate/features/autoencoder) to
have something non-degenerate to chew on.

    python3 gen_synthetic_vibration_csv.py --out-dir ~/.cache/epm-sim-data
"""
import argparse
import os
import random

N_ROWS = 20000  # >> satellite_node_sim.py's DEFAULT_WINDOW_SIZE (1024)

_HEADER = """Com,SIM
Node,000000
SN,0
Firmware,synthetic
Time,synthetic
Units,m/s^2
Sweeps,Channel 1
"""


def _write_csv(path: str, rows) -> None:
    with open(path, "w") as f:
        f.write(_HEADER)
        for i, value in enumerate(rows):
            f.write(f"{i},{value:.6f}\n")


def gen_healthy(n: int, rng: random.Random):
    return [rng.gauss(0.0, 1.0) for _ in range(n)]


def gen_fault(n: int, rng: random.Random, period: int = 37, spike: float = 8.0):
    return [rng.gauss(0.0, 1.0) + (spike if i % period == 0 else 0.0) for i in range(n)]


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--rows", type=int, default=N_ROWS)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--force", action="store_true",
                         help="Regenerate even if the target files already exist")
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    rng = random.Random(args.seed)

    targets = {
        "healthy.csv": gen_healthy,
        "fault.csv": gen_fault,
    }
    for name, gen_fn in targets.items():
        path = os.path.join(args.out_dir, name)
        if os.path.exists(path) and not args.force:
            print(f"{path} already exists -- skipping (--force to regenerate)")
            continue
        _write_csv(path, gen_fn(args.rows, rng))
        print(f"wrote {path} ({args.rows} rows)")


if __name__ == "__main__":
    main()
