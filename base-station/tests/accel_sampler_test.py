#!/usr/bin/env python3
"""
KX134-1211 SPI accelerometer verification. The old repo never had a standalone
accel test (edgeai-predictive-monitor-unoq/mpu/tests/ has none - accel data
only ever appeared as part of the not-yet-ported fuser_thread's fused
spectrum frame). This is a new script, following this repo's established
Bridge.call() pattern (see display_matrix_test.py/display_rgb_test.py/
mic_sampler_test.py).

Calls the sketch's "get_accel_info" once (self-describing ODR/FFT size/full vs.
exposed bin count, plus isr/read/timeout/fifo_full diagnostic counters - see
sketch/accel_sampler.cpp's own comment on why "exposed" is smaller than
"full": Bridge's 256-byte message ceiling), then polls "get_accel_spectrum"
repeatedly and prints a crude ASCII bar per bucket so you can eyeball whether
it reacts to real vibration (tap/shake the board) while this runs.

Run on the board while the app is running:
    adb shell "docker exec edgeai-predictive-monitor-base-station-main-1 python3 /app/tests/accel_sampler_test.py"
"""
import time

from arduino.app_utils import Bridge

POLL_INTERVAL_S = 0.5
POLL_COUNT = 20
BAR_MAX_WIDTH = 40
# Magnitude is a sum of three axes' 1024-sample FFT energy - resting-on-a-desk
# vibration alone tends to land in the low hundreds to low thousands. Not
# calibrated, just enough range to make a bar chart move visibly.
BAR_SCALE_DIVISOR = 200


def bar(value):
    width = min(BAR_MAX_WIDTH, int(value / BAR_SCALE_DIVISOR))
    return "#" * width


def main():
    info = Bridge.call("get_accel_info")
    fields = info.split(",")
    odr_hz, fft_len, full_bins, exposed_bins = (int(x) for x in fields[:4])
    print(f"accel: odr={odr_hz}Hz fft_len={fft_len} full_bins={full_bins} "
          f"exposed_bins={exposed_bins} (each exposed bucket averages "
          f"{full_bins // exposed_bins} full-resolution bins)")
    if len(fields) > 4:
        print(f"  diagnostics: {', '.join(fields[4:])}")
    print("Tap or shake the board and watch the bars move.")
    print()

    for i in range(POLL_COUNT):
        spectrum = [int(x) for x in Bridge.call("get_accel_spectrum").split(",")]
        peak_idx = max(range(len(spectrum)), key=lambda k: spectrum[k])
        print(f"[{i + 1:2d}/{POLL_COUNT}] peak bucket {peak_idx:2d} "
              f"(mag={spectrum[peak_idx]}): {bar(spectrum[peak_idx])}")
        time.sleep(POLL_INTERVAL_S)

    print()
    info = Bridge.call("get_accel_info")
    print(f"final diagnostics: {info.split(',', 4)[4] if ',' in info else info}")
    print("Done.")


if __name__ == "__main__":
    main()
