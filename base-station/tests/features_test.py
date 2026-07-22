#!/usr/bin/env python3
"""
Milestone 5 verification, updated for the per-axis-spectra + scalar-tail
feature vector (docs/SENSOR_TELEMETRY_FRAME_PLAN.md): confirm correct
vector shape/values for a dual-channel node and single-channel nodes,
that MIC/ACCEL_X aren't interchangeable (open question #4), that the
scalar tail is appended in a fixed order and raises loudly if missing
(rather than silently zero-filling), and that standardize_scalars()
z-scores only the scalar tail, leaving spectral bins untouched.

Run with PYTHONPATH covering base-station/python/ingestion, base-station/python/registry, base-station/python/pipeline:
    PYTHONPATH=base-station/python/ingestion:base-station/python/registry:base-station/python/pipeline \\
        python3 base-station/tests/features_test.py
"""
import sys

from sensor_frame import FrameSource, SensorFrame
from registry import SensorChannel
from features import SCALAR_NAMES, build_feature_vector, normalize_bins, standardize_scalars

BINS = 128  # SensorChannel.MIC/ACCEL_X/Y/Z's spectral bin count (registry._DIM_BY_CHANNEL)

MIC_SCALARS = {f"{name}_mic": 10.0 + i for i, name in enumerate(SCALAR_NAMES)}
ACCEL_X_SCALARS = {f"{name}_x": 20.0 + i for i, name in enumerate(SCALAR_NAMES)}


def frame(mic_bins=None, accel_x_bins=None, scalars=None) -> SensorFrame:
    bins = {}
    if mic_bins is not None:
        bins["mic"] = mic_bins
    if accel_x_bins is not None:
        bins["accel_x"] = accel_x_bins
    return SensorFrame(node_id="node-1", source=FrameSource.SPI, timestamp=0.0,
                        bins=bins, scalars=scalars or {})


def test_dual_sensor_both():
    mic_bins = tuple(float(i) for i in range(1, BINS + 1))        # peak 128.0
    accel_x_bins = tuple(float(2 * i) for i in range(1, BINS + 1))  # peak 256.0
    both_scalars = {**MIC_SCALARS, **ACCEL_X_SCALARS}
    vector, spectral_dim = build_feature_vector(
        frame(mic_bins, accel_x_bins, both_scalars),
        frozenset({SensorChannel.MIC, SensorChannel.ACCEL_X}), 268)

    assert len(vector) == 268, len(vector)
    assert spectral_dim == 256, spectral_dim
    # mic half normalized against its own peak (128.0)
    assert vector[0] == 1.0 / 128.0, vector[0]
    assert vector[127] == 1.0, vector[127]
    # accel_x half normalized against its own peak (256.0), independent of mic's
    assert vector[128] == 2.0 / 256.0, vector[128]
    assert vector[255] == 1.0, vector[255]
    # scalar tail: SensorChannel order (MIC then ACCEL_X), SCALAR_NAMES order within each
    expected_tail = tuple(MIC_SCALARS[f"{n}_mic"] for n in SCALAR_NAMES) + \
        tuple(ACCEL_X_SCALARS[f"{n}_x"] for n in SCALAR_NAMES)
    assert vector[256:] == expected_tail, vector[256:]
    print("test_dual_sensor_both: PASS")


def test_single_sensor_accel_only():
    accel_x_bins = tuple(float(i) for i in range(1, BINS + 1))
    vector, spectral_dim = build_feature_vector(
        frame(accel_x_bins=accel_x_bins, scalars=ACCEL_X_SCALARS),
        frozenset({SensorChannel.ACCEL_X}), 134)

    assert len(vector) == 134, len(vector)
    assert spectral_dim == 128, spectral_dim
    assert vector[0] == 1.0 / 128.0, vector[0]
    assert vector[127] == 1.0, vector[127]
    assert vector[128:] == tuple(ACCEL_X_SCALARS[f"{n}_x"] for n in SCALAR_NAMES), vector[128:]
    print("test_single_sensor_accel_only: PASS")


def test_single_sensor_mic_only():
    mic_bins = tuple(float(i) for i in range(1, BINS + 1))
    vector, spectral_dim = build_feature_vector(
        frame(mic_bins=mic_bins, scalars=MIC_SCALARS),
        frozenset({SensorChannel.MIC}), 134)

    assert len(vector) == 134, len(vector)
    assert spectral_dim == 128, spectral_dim
    assert vector[0] == 1.0 / 128.0, vector[0]
    assert vector[127] == 1.0, vector[127]
    assert vector[128:] == tuple(MIC_SCALARS[f"{n}_mic"] for n in SCALAR_NAMES), vector[128:]
    print("test_single_sensor_mic_only: PASS")


def test_mic_only_and_accel_only_are_not_interchangeable():
    # Same values, wrong sensor_config for what's actually present -- each
    # channel has its own required bins key, not just a length. Supplies
    # accel_x's scalars (matching the requested sensor_config) so the
    # ValueError this raises is specifically the spectral bin-count
    # mismatch (0 accel_x bins present, mic's 128 don't count), not a
    # missing-scalar complaint.
    bins = tuple(float(i) for i in range(1, BINS + 1))
    try:
        build_feature_vector(frame(mic_bins=bins, scalars=ACCEL_X_SCALARS),
                              frozenset({SensorChannel.ACCEL_X}), 134)
        assert False, "expected ValueError: mic bins present but ACCEL_X config requires accel_x bins"
    except ValueError:
        pass
    print("test_mic_only_and_accel_only_are_not_interchangeable: PASS")


def test_bin_count_mismatch_raises():
    try:
        build_feature_vector(frame(accel_x_bins=(1.0, 2.0, 3.0), scalars=ACCEL_X_SCALARS),
                              frozenset({SensorChannel.ACCEL_X}), 134)
        assert False, "expected ValueError for wrong bin count"
    except ValueError:
        pass
    print("test_bin_count_mismatch_raises: PASS")


def test_missing_scalar_key_raises():
    # Spectral bins present and correctly sized, but the scalar tail is
    # missing entirely -- must raise, not silently zero-fill (a version-
    # skewed firmware that hasn't started sending scalars yet is a real
    # misconfiguration, not something to paper over).
    accel_x_bins = tuple(float(i) for i in range(1, BINS + 1))
    try:
        build_feature_vector(frame(accel_x_bins=accel_x_bins),
                              frozenset({SensorChannel.ACCEL_X}), 134)
        assert False, "expected ValueError for missing scalar tail"
    except ValueError:
        pass
    print("test_missing_scalar_key_raises: PASS")


def test_all_zero_bins_normalize_to_zero_without_crash():
    zero_bins = tuple(0.0 for _ in range(BINS))
    assert normalize_bins(zero_bins) == zero_bins
    vector, spectral_dim = build_feature_vector(
        frame(accel_x_bins=zero_bins, scalars=ACCEL_X_SCALARS),
        frozenset({SensorChannel.ACCEL_X}), 134)
    assert spectral_dim == BINS, spectral_dim
    assert vector[:BINS] == zero_bins, vector[:BINS]
    assert vector[BINS:] == tuple(ACCEL_X_SCALARS[f"{n}_x"] for n in SCALAR_NAMES), vector[BINS:]
    print("test_all_zero_bins_normalize_to_zero_without_crash: PASS")


def test_standardize_scalars_round_trip():
    # spectral_dim=2 spectral columns (untouched) + 3 scalar columns.
    vector = (0.1, 0.9, 10.0, 20.0, 30.0)
    mu = (10.0, 15.0, 30.0)
    sigma = (2.0, 5.0, 0.0)  # last column degenerate -- near-zero-sigma guard
    standardized = standardize_scalars(vector, spectral_dim=2, mu=mu, sigma=sigma)

    assert standardized[:2] == (0.1, 0.9), standardized[:2]  # spectral untouched
    assert standardized[2] == (10.0 - 10.0) / 2.0 == 0.0, standardized[2]
    assert standardized[3] == (20.0 - 15.0) / 5.0 == 1.0, standardized[3]
    # sigma <= 1e-9 falls back to dividing by 1.0, not a ZeroDivisionError/inf
    assert standardized[4] == 30.0 - 30.0 == 0.0, standardized[4]

    # No scalar tail at all (spectral_dim == len(vector)) is a no-op.
    spectral_only = (0.1, 0.9)
    assert standardize_scalars(spectral_only, spectral_dim=2, mu=(), sigma=()) == spectral_only
    print("test_standardize_scalars_round_trip: PASS")


def main():
    test_dual_sensor_both()
    test_single_sensor_accel_only()
    test_single_sensor_mic_only()
    test_mic_only_and_accel_only_are_not_interchangeable()
    test_bin_count_mismatch_raises()
    test_missing_scalar_key_raises()
    test_all_zero_bins_normalize_to_zero_without_crash()
    test_standardize_scalars_round_trip()
    print("RESULT: PASS - feature vectors have correct shape/values for dual- and single-sensor "
          "nodes, and the scalar tail is standardized correctly")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"RESULT: FAIL - {e}")
        sys.exit(1)
