#!/usr/bin/env python3
"""
On-hardware check for the LED matrix fleet-status feature
(docs/LED_MATRIX_STATUS_PLAN.md). Companion to matrix_status_test.py: that
one unit-tests fleet_status_text()'s string logic off-device; this one drives
the *real* matrix through the exact Bridge push wire_local_matrix_text() uses
(set_matrix_scroll_speed 150 then set_matrix_text), for representative fleet
states, so you can confirm on hardware that the §3 strings actually render and
scroll correctly. Like display_matrix_test.py it only sends -- watch the
board's 8x13 matrix while it runs.

Unlike display_matrix_test.py this imports the production builder
(matrix_status.fleet_status_text) rather than hardcoding strings, so what you
see on the matrix is exactly what the live app would push for each fleet
shape -- no risk of the test and the feature drifting apart.

Because it imports `registry` (needs the `statemachine` dep) and
`arduino.app_utils` (the App Lab runtime), it must run inside the app
container with that venv's python, not the host's, e.g.:

    C=edgeai-predictive-monitor-base-station-main-1
    PY=$(adb shell "docker exec $C find /app/.cache -maxdepth 4 -name python3" | tr -d '\r' | head -1)
    adb shell "docker exec -e PYTHONPATH=/app/python/registry $C \
        $PY /app/tests/matrix_status_device_test.py"
"""
import sys
import time

sys.path.insert(0, "/app/python/registry")

from arduino.app_utils import Bridge

from registry import NodeStatus, RegistryEntry, SensorChannel
from matrix_status import fleet_status_text

# Must match main.py's MATRIX_SCROLL_SPEED_MS -- the speed wire_local_matrix_text
# pushes, so the on-screen scroll here matches production exactly.
SCROLL_SPEED_MS = 150
MATRIX_COLS = 13
GLYPH_STRIDE = 6  # 5 glyph columns + 1 inter-character gap (5x7 font)

_NOW = time.time()
_FRESH = _NOW - 1  # seen a second ago -> online
_STALE = _NOW - 3600  # long past the 30s offline cutoff -> offline


def entry(status: NodeStatus, last_seen=_FRESH) -> RegistryEntry:
    return RegistryEntry(
        node_id="n", device_name="n",
        sensor_config=frozenset({SensorChannel.MIC}), input_dim=134,
        status=status, last_seen=last_seen)


def scroll_cycle_seconds(text: str) -> float:
    """One full scroll cycle (text columns + a trailing screen-width blank
    gap), same column math as matrix_display.cpp's tick, so each hold lasts
    long enough to see the whole message scroll past once."""
    if not text:
        return 1.0
    cycle_cols = len(text) * GLYPH_STRIDE + MATRIX_COLS
    return cycle_cols * SCROLL_SPEED_MS / 1000


# (label, fleet entries) -- one per branch of the §3 truth table.
STEPS = [
    ("all healthy", [entry(NodeStatus.HEALTHY)] * 3),
    ("all three severities", [
        entry(NodeStatus.FAULT),
        entry(NodeStatus.WARNING), entry(NodeStatus.WARNING),
        entry(NodeStatus.HEALTHY, last_seen=_STALE),  # -> offline
    ]),
    ("offline only", [entry(NodeStatus.HEALTHY, last_seen=_STALE)] * 2),
    ("nothing commissioned (blank)", [
        entry(NodeStatus.UNCOMMISSIONED), entry(NodeStatus.PAUSED),
    ]),
]


def main():
    print("Connected via RouterBridge. Watch the onboard LED matrix now.")
    Bridge.call("set_matrix_scroll_speed", str(SCROLL_SPEED_MS))

    for label, entries in STEPS:
        text = fleet_status_text(entries, now=_NOW)
        Bridge.call("set_matrix_text", text)
        hold = scroll_cycle_seconds(text) + 3
        print(f"  {label}: text={text!r} -- holding {hold:.1f}s")
        time.sleep(hold)

    Bridge.call("set_matrix_text", "")  # leave it blank
    print()
    print("Done.")


if __name__ == "__main__":
    main()
