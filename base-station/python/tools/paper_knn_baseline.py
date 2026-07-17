#!/usr/bin/env python3
"""Standalone replication of the dataset's source paper's feature+classifier
method (Khan et al., "System Design for Early Fault Diagnosis of Machines
using Vibration Features", IEEE PGSRET 2019), bypassing Edge Impulse
entirely: EI has no EMD DSP block and no supervised KNN learning block, so
this can't be set up as an EI impulse.

Reads ei_dataset_prep.py --features raw-triaxial's already-prepared
windows (accel_x,accel_y,accel_z CSVs under
--prepared-dir/{training,testing}/<label>/), so this shares the exact same
leak-free file-level train/test split as the raw-triaxial EI upload --
apples-to-apples against that 59.82% Model Testing result, varying only the
features/classifier.

Computes the paper's 5 features (Mean, Standard Deviation, RMS, Energy,
Spectral Mean Value) per axis, concatenated across accel_x/y/z into one
15-dim vector per window -- the paper doesn't state whether tri-axial
features are concatenated or fused another way; concatenation is the
standard convention and is assumed here. "Spectral Mean Value" per the
paper's own description ("the value that divides the whole dataset into
two equal parts") is actually a median, not a mean -- taken literally as
the median of the window's FFT magnitude spectrum.

Scope gap vs. the paper: this does NOT implement EMD (Empirical Mode
Decomposition) segmentation -- no off-the-shelf implementation in this
codebase's dependencies, and the paper doesn't specify sifting-stop
parameters precisely enough to reimplement faithfully. Features are
computed directly on the raw window instead, so this is "paper's features
+ KNN, no EMD" rather than a full replication -- keep that in mind next to
the paper's reported 91.5%.

Feature scales differ by orders of magnitude (Energy vs. Mean), which
would dominate KNN's distance metric if left unnormalized -- not mentioned
in the paper, but necessary in practice, so each feature is z-score
standardized using training-set statistics only (applied unchanged to
test).

Usage:
  .venv/bin/python tools/paper_knn_baseline.py \\
    --prepared-dir ~/workspace/vibration-based-fault-diagnosis-of-machines-prepared-raw-triaxial
"""
import argparse
import csv
import glob
import os

import numpy as np
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.neighbors import KNeighborsClassifier
from sklearn.preprocessing import StandardScaler

AXES = ("accel_x", "accel_y", "accel_z")


def read_sample(path):
    with open(path) as f:
        rows = list(csv.reader(f))
    header, data = rows[0], rows[1:]
    return {
        name: np.array([float(row[i]) for row in data], dtype=float)
        for i, name in enumerate(header) if name != "timestamp"
    }


def spectral_median(x):
    """Paper's "Spectral Mean Value" is defined as "the value that divides
    the whole dataset into two equal parts" -- that's a median, applied
    here to the window's FFT magnitude spectrum (not the raw signal), per
    the "Spectral" half of the feature's name."""
    spectrum = np.abs(np.fft.rfft(x))
    return float(np.median(spectrum))


def five_features(x):
    """Paper's exact 5 features (Section III.C): Mean, Standard Deviation,
    RMS, Energy, Spectral Mean Value."""
    mean = float(np.mean(x))
    std = float(np.std(x))
    rms = float(np.sqrt(np.mean(x ** 2)))
    energy = float(np.sum(x ** 2))
    return [mean, std, rms, energy, spectral_median(x)]


def featurize_sample(path):
    cols = read_sample(path)
    features = []
    for axis in AXES:
        features.extend(five_features(cols[axis]))
    return features


def load_category(category_dir):
    features, labels = [], []
    for label in sorted(os.listdir(category_dir)):
        label_dir = os.path.join(category_dir, label)
        if not os.path.isdir(label_dir):
            continue
        for path in sorted(glob.glob(os.path.join(label_dir, "*.csv"))):
            features.append(featurize_sample(path))
            labels.append(label)
    return np.array(features), np.array(labels)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--prepared-dir", required=True,
                         help="ei_dataset_prep.py --features raw-triaxial output dir "
                              "(contains training/ and testing/)")
    parser.add_argument("--k", type=int, default=5,
                         help="KNN neighbor count -- the paper doesn't state its k, default is "
                              "scikit-learn's own default")
    args = parser.parse_args()

    x_train, y_train = load_category(os.path.join(args.prepared_dir, "training"))
    x_test, y_test = load_category(os.path.join(args.prepared_dir, "testing"))
    print(f"train: {x_train.shape[0]} samples, test: {x_test.shape[0]} samples, "
          f"{x_train.shape[1]} features/sample", flush=True)

    scaler = StandardScaler().fit(x_train)
    x_train_scaled = scaler.transform(x_train)
    x_test_scaled = scaler.transform(x_test)

    clf = KNeighborsClassifier(n_neighbors=args.k)
    clf.fit(x_train_scaled, y_train)
    y_pred = clf.predict(x_test_scaled)

    accuracy = accuracy_score(y_test, y_pred)
    print(f"\nTest accuracy: {accuracy * 100:.2f}%\n", flush=True)

    labels = sorted(set(y_train) | set(y_test))
    print("Confusion matrix (rows=true, cols=predicted):")
    print(labels)
    print(confusion_matrix(y_test, y_pred, labels=labels))
    print()
    print(classification_report(y_test, y_pred, labels=labels))


if __name__ == "__main__":
    main()
