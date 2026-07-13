#!/usr/bin/env python3
"""
LED matrix verification, ported from the old repo's
mpu/tests/display_matrix_test.py (edgeai-predictive-monitor-unoq). Drives the
onboard 8x13 LED matrix through a sequence of commands with varying text, so
you can confirm on hardware that sketch/sketch.ino's matrix_display_thread
(priority 3) is alive and rendering correctly.

This script only sends; it can't see the matrix itself. Watch the board
while it runs.

The old repo drove this over a hand-rolled binary framing on a raw UART
(mpu/common/wire_protocol.py), because that repo had masked out App Lab's
own arduino-router/Bridge service entirely. This repo stays inside App Lab,
so the same "what text/scroll speed to show" contract is reached over
App Lab's real RPC mechanism instead - arduino.app_utils.Bridge.call(),
routed to two of the sketch's Bridge.provide() handlers. No serial
port/baud arguments needed - Bridge already owns that link, and no adb
push of a separate tree either: base-station/deploy.sh pushes this whole
app directory (tests/ included) as part of the normal deploy.

Two separate calls, not one "set_matrix_text(text, scroll_speed_ms)": a
real two-argument provider was tried first and reliably failed
Arduino_RPClite's argument type-check on the second (integer) parameter,
confirmed by dumping the exact msgpack bytes sent (which were correct) -
see sketch/sketch.ino's matrix_display_set_text() comment for the
findings. Every official RouterBridge example only ever binds zero- or
one-argument free functions, so the sketch now exposes
"set_matrix_scroll_speed" and "set_matrix_text" separately, and this
script always sends scroll speed first so it's already in effect by the
time the (non-blank) text lands.

Run on the board while the app is running:
    adb shell "python3 /home/arduino/ArduinoApps/edgeai-predictive-monitor-base-station/tests/display_matrix_test.py"
"""
import time

from arduino.app_utils import Bridge

MATRIX_COLS = 13
GLYPH_STRIDE = 6  # 5 glyph columns + 1 inter-character gap (5x7 font)


def scroll_cycle_seconds(text: str, scroll_speed_ms: int) -> float:
    """One full scroll cycle: the text's columns plus a trailing
    screen-width blank gap, before matrix_display_tick()'s scroll_col
    wraps back to the start - same column math as sketch/sketch.ino's
    matrix_display_tick(), since it's the same physical font/matrix."""
    cycle_cols = len(text) * GLYPH_STRIDE + MATRIX_COLS
    return cycle_cols * scroll_speed_ms / 1000


# (label, text, scroll_speed_ms, hold_seconds)
_SCROLL_TEXT = "HELLO EPM 123"
_SCROLL_SPEED_MS = 150
STEPS = [
    ("static \"HI\"", "HI", 0, 4),
    ("static \"OK 8x13\"", "OK 8x13", 0, 4),
    (f"scrolling \"{_SCROLL_TEXT}\" @{_SCROLL_SPEED_MS}ms/col",
     _SCROLL_TEXT, _SCROLL_SPEED_MS,
     # Hold for one full scroll cycle (text fully exits left) plus a few
     # seconds into the next loop, so the wrap-around is visible too.
     scroll_cycle_seconds(_SCROLL_TEXT, _SCROLL_SPEED_MS) + 5),
    ("static blank (clear)", "", 0, 1),
]


def main():
    print("Connected via RouterBridge. Watch the onboard LED matrix on the "
          "board now.")

    for label, text, scroll_speed_ms, hold_s in STEPS:
        # Sent as a string, not an int: the sketch's Bridge providers take
        # String for both args, not just text - see sketch/sketch.ino's
        # matrix_display_set_text() comment for why (integer RPC params
        # reliably failed Arduino_RPClite's type-check on hardware).
        Bridge.call("set_matrix_scroll_speed", str(scroll_speed_ms))
        Bridge.call("set_matrix_text", text)
        print(f"  TX {label} (text={text!r} "
              f"scroll_speed_ms={scroll_speed_ms}) - holding {hold_s}s")
        time.sleep(hold_s)

    print()
    print("Done.")


if __name__ == "__main__":
    main()
