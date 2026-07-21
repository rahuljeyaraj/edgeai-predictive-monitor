#!/usr/bin/env python3
"""Verifies common/raw_features.py's FFT/scalar math -- pure numpy, no
hardware/Bridge dependency, so this runs on a laptop same as features_test.py.

Run with PYTHONPATH covering base-station/python/common:
    PYTHONPATH=base-station/python/common python3 base-station/tests/raw_features_test.py
"""
import sys

import numpy as np

from raw_features import downsample, fft_magnitude, kurtosis, peak_normalize, rms


def test_fft_magnitude_drops_dc_keeps_half_spectrum():
    n = 64
    window = np.sin(2 * np.pi * 4 * np.arange(n) / n) + 10.0  # tone + DC offset
    mag = fft_magnitude(window)
    assert len(mag) == n // 2, len(mag)
    # bin 4 (the tone) should dominate the spectrum, DC (dropped) doesn't leak in
    assert np.argmax(mag) == 3, np.argmax(mag)  # bin 1..N/2 -> index 0 is bin 1
    print("test_fft_magnitude_drops_dc_keeps_half_spectrum: PASS")


def test_downsample_averages_evenly():
    mag = np.array([1.0, 3.0, 5.0, 7.0])
    pooled = downsample(mag, 2)
    assert list(pooled) == [2.0, 6.0], list(pooled)
    print("test_downsample_averages_evenly: PASS")


def test_downsample_uneven_raises():
    try:
        downsample(np.zeros(5), 2)
        assert False, "expected ValueError for a bin_count that doesn't divide evenly"
    except ValueError:
        pass
    print("test_downsample_uneven_raises: PASS")


def test_peak_normalize_rescales_to_own_peak():
    bins = np.array([1.0, 2.0, 4.0])
    normalized = peak_normalize(bins)
    assert list(normalized) == [0.25, 0.5, 1.0], list(normalized)
    print("test_peak_normalize_rescales_to_own_peak: PASS")


def test_peak_normalize_all_zero_no_crash():
    bins = np.zeros(4)
    normalized = peak_normalize(bins)
    assert list(normalized) == [0.0, 0.0, 0.0, 0.0], list(normalized)
    print("test_peak_normalize_all_zero_no_crash: PASS")


def test_rms_matches_known_value():
    x = np.array([3.0, 4.0])  # rms = sqrt((9+16)/2) = sqrt(12.5)
    assert abs(rms(x) - np.sqrt(12.5)) < 1e-9, rms(x)
    print("test_rms_matches_known_value: PASS")


def test_kurtosis_zero_for_constant_signal():
    x = np.full(16, 5.0)
    assert kurtosis(x) == 0.0, kurtosis(x)
    print("test_kurtosis_zero_for_constant_signal: PASS")


def main():
    test_fft_magnitude_drops_dc_keeps_half_spectrum()
    test_downsample_averages_evenly()
    test_downsample_uneven_raises()
    test_peak_normalize_rescales_to_own_peak()
    test_peak_normalize_all_zero_no_crash()
    test_rms_matches_known_value()
    test_kurtosis_zero_for_constant_signal()
    print("RESULT: PASS - raw_features FFT/scalar math verified")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"RESULT: FAIL - {e}")
        sys.exit(1)
