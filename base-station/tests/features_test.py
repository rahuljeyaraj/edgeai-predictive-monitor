#!/usr/bin/env python3
"""
Milestone 5 verification: confirm
correct vector shape/values for a dual-sensor node and single-sensor
nodes -- including both 512-dim variants (mic-only, accel-only) per
open question #4, which this milestone resolves.

Run with PYTHONPATH covering base-station/python/ingestion, base-station/python/registry, base-station/python/pipeline:
    PYTHONPATH=base-station/python/ingestion:base-station/python/registry:base-station/python/pipeline \\
        python3 base-station/tests/features_test.py
"""
import sys

from sensor_frame import FrameSource, SensorFrame
from registry import SensorChannel
from features import build_feature_vector, normalize_bins


def frame(mic_bins=None, accel_bins=None) -> SensorFrame:
    bins = {}
    if mic_bins is not None:
        bins["mic"] = mic_bins
    if accel_bins is not None:
        bins["accel"] = accel_bins
    return SensorFrame(node_id="node-1", source=FrameSource.SPI, timestamp=0.0, bins=bins)


def test_dual_sensor_both():
    mic_bins = tuple(float(i) for i in range(1, 513))      # peak 512.0
    accel_bins = tuple(float(2 * i) for i in range(1, 513))  # peak 1024.0
    vector = build_feature_vector(frame(mic_bins, accel_bins),
                                   frozenset({SensorChannel.MIC, SensorChannel.ACCEL}))

    assert len(vector) == 1024, len(vector)
    # mic half normalized against its own peak (512.0)
    assert vector[0] == 1.0 / 512.0, vector[0]
    assert vector[511] == 1.0, vector[511]
    # accel half normalized against its own peak (1024.0), independent of mic's
    assert vector[512] == 2.0 / 1024.0, vector[512]
    assert vector[1023] == 1.0, vector[1023]
    print("test_dual_sensor_both: PASS")


def test_single_sensor_accel_only():
    accel_bins = tuple(float(i) for i in range(1, 513))
    vector = build_feature_vector(frame(accel_bins=accel_bins), frozenset({SensorChannel.ACCEL}))

    assert len(vector) == 512, len(vector)
    assert vector[0] == 1.0 / 512.0, vector[0]
    assert vector[-1] == 1.0, vector[-1]
    print("test_single_sensor_accel_only: PASS")


def test_single_sensor_mic_only():
    mic_bins = tuple(float(i) for i in range(1, 513))
    vector = build_feature_vector(frame(mic_bins=mic_bins), frozenset({SensorChannel.MIC}))

    assert len(vector) == 512, len(vector)
    assert vector[0] == 1.0 / 512.0, vector[0]
    assert vector[-1] == 1.0, vector[-1]
    print("test_single_sensor_mic_only: PASS")


def test_mic_only_and_accel_only_are_not_interchangeable():
    # Same values, wrong sensor_config for what's actually present --
    # each 512-dim variant has its own required bins, not just a length.
    bins = tuple(float(i) for i in range(1, 513))
    try:
        build_feature_vector(frame(mic_bins=bins), frozenset({SensorChannel.ACCEL}))
        assert False, "expected ValueError: mic bins present but ACCEL config requires accel_bins"
    except ValueError:
        pass
    print("test_mic_only_and_accel_only_are_not_interchangeable: PASS")


def test_bin_count_mismatch_raises():
    try:
        build_feature_vector(frame(accel_bins=(1.0, 2.0, 3.0)), frozenset({SensorChannel.ACCEL}))
        assert False, "expected ValueError for wrong bin count"
    except ValueError:
        pass
    print("test_bin_count_mismatch_raises: PASS")


def test_all_zero_bins_normalize_to_zero_without_crash():
    zero_bins = tuple(0.0 for _ in range(512))
    assert normalize_bins(zero_bins) == zero_bins
    vector = build_feature_vector(frame(accel_bins=zero_bins), frozenset({SensorChannel.ACCEL}))
    assert vector == zero_bins
    print("test_all_zero_bins_normalize_to_zero_without_crash: PASS")


def main():
    test_dual_sensor_both()
    test_single_sensor_accel_only()
    test_single_sensor_mic_only()
    test_mic_only_and_accel_only_are_not_interchangeable()
    test_bin_count_mismatch_raises()
    test_all_zero_bins_normalize_to_zero_without_crash()
    print("RESULT: PASS - feature vectors have correct shape/values for dual- and single-sensor nodes")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"RESULT: FAIL - {e}")
        sys.exit(1)
