#!/usr/bin/env python3
"""
RGB LED ring verification, ported from the old repo's
mpu/tests/display_rgb_test.py (edgeai-predictive-monitor-unoq). Drives the
external WS2812B 8-LED ring through CONST/BREATHE/STROBE commands with
varying color/period, so you can confirm on hardware that sketch/sketch.ino's
rgb_display_thread (priority 3) is alive and rendering correctly.

This script only sends; it can't see the ring itself. Watch the board while
it runs.

The old repo drove this over a hand-rolled binary framing on a raw UART
(mpu/common/wire_protocol.py). This repo stays inside App Lab, so the same
"what color/mode/period to show" contract is reached over App Lab's real RPC
mechanism instead - arduino.app_utils.Bridge.call(), routed to one of the
sketch's Bridge.provide() handlers. No serial port/baud arguments needed -
Bridge already owns that link.

One combined "RRGGBB,mode,period_ms" string, not three separate calls (unlike
display_matrix_test.py's two calls): color/mode/period should latch together
atomically on the sketch side (see sketch/rgb_display.cpp's header comment),
and a bare String argument sidesteps the same Arduino_RPClite integer-RPC-
argument bug documented there.

Run on the board while the app is running:
    adb shell "docker exec edgeai-predictive-monitor-base-station-main-1 python3 /app/tests/display_rgb_test.py"
"""
import time

from arduino.app_utils import Bridge

MODE_CONST = 0
MODE_BREATHE = 1
MODE_STROBE = 2

# (label, rgb, mode, period_ms, hold_seconds)
STEPS = [
    ("CONST red",           0xFF0000, MODE_CONST,   0,    3),
    ("CONST green",         0x00FF00, MODE_CONST,   0,    3),
    ("CONST blue",          0x0000FF, MODE_CONST,   0,    3),
    ("BREATHE yellow 1.5s", 0xFFFF00, MODE_BREATHE, 1500, 6),
    ("STROBE magenta 0.2s", 0xFF00FF, MODE_STROBE,  200,  4),
    ("CONST off",           0x000000, MODE_CONST,   0,    1),
]


def main():
    print("Connected via RouterBridge. Watch the external RGB LED ring on "
          "the board now.")

    for label, rgb, mode, period_ms, hold_s in STEPS:
        Bridge.call("set_rgb", f"{rgb:06X},{mode},{period_ms}")
        print(f"  TX {label} (rgb=0x{rgb:06x} mode={mode} "
              f"period_ms={period_ms}) - holding {hold_s}s")
        time.sleep(hold_s)

    print()
    print("Done.")


if __name__ == "__main__":
    main()
