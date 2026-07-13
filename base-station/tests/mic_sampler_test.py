#!/usr/bin/env python3
"""
INMP441 I2S microphone verification. The old repo never had a standalone mic
test (edgeai-predictive-monitor-unoq/mpu/tests/ has none - mic data was only
ever exercised as part of the not-yet-ported fuser_thread's fused spectrum
frame, via mpu/tests/sensor_frame_test.py's synthetic ChannelSpectrum, not a
real capture). This is a new script, following this repo's established
Bridge.call() pattern (see display_matrix_test.py/display_rgb_test.py)
instead of that old hand-framed-UART wire format.

Calls the sketch's "get_mic_info" once (self-describing sample rate/FFT
size/full vs. exposed bin count - see sketch/mic_sampler.cpp's own comment on
why "exposed" is smaller than "full": Bridge's 256-byte message ceiling),
then polls "get_mic_spectrum" repeatedly and prints a crude ASCII bar per
bucket so you can eyeball whether the spectrum reacts to sound (clap, tap the
mic, speak near it) while this runs.

Run on the board while the app is running:
    adb shell "docker exec edgeai-predictive-monitor-base-station-main-1 python3 /app/tests/mic_sampler_test.py"
"""
import time

from arduino.app_utils import Bridge

POLL_INTERVAL_S = 0.5
POLL_COUNT = 20
BAR_MAX_WIDTH = 40
# Magnitude is a sum of 2048 raw int16 samples' FFT energy - ambient noise
# alone tends to land in the low hundreds to low thousands. Not calibrated,
# just enough range to make a bar chart move visibly.
BAR_SCALE_DIVISOR = 200


def bar(value):
    width = min(BAR_MAX_WIDTH, int(value / BAR_SCALE_DIVISOR))
    return "#" * width


def main():
    info = Bridge.call("get_mic_info")
    fields = info.split(",")
    fs_hz, fft_len, full_bins, exposed_bins = (int(x) for x in fields[:4])
    print(f"mic: fs={fs_hz}Hz fft_len={fft_len} full_bins={full_bins} "
          f"exposed_bins={exposed_bins} (each exposed bucket averages "
          f"{full_bins // exposed_bins} full-resolution bins)")
    if len(fields) > 4:
        print(f"  diagnostics: {', '.join(fields[4:])}")
    print("Make some noise near the mic (clap/tap/speak) and watch the bars move.")
    print()

    for i in range(POLL_COUNT):
        spectrum = [int(x) for x in Bridge.call("get_mic_spectrum").split(",")]
        peak_idx = max(range(len(spectrum)), key=lambda k: spectrum[k])
        print(f"[{i + 1:2d}/{POLL_COUNT}] peak bucket {peak_idx:2d} "
              f"(mag={spectrum[peak_idx]}): {bar(spectrum[peak_idx])}")
        time.sleep(POLL_INTERVAL_S)

    print()
    print("Done.")


if __name__ == "__main__":
    main()
