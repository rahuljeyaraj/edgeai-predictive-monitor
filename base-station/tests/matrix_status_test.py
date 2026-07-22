#!/usr/bin/env python3
"""
Exercises matrix_status.fleet_status_text() against the truth table in
docs/LED_MATRIX_STATUS_PLAN.md §3: blank when nothing's commissioned, a
count-bearing "NOK" when all good, and the nonzero buckets in fixed
fault->warning->offline order (healthy dropped) when anything's wrong. Words
are the matrix-only shorthand (OK/FLT/WRN/OFF, not the NodeStatus vocabulary
used elsewhere) and every separator is dropped (no space after a count, no
space after a comma) -- each character, including a space, costs a fixed
6-column glyph slot on the firmware's 5x7 font, ~46% of the 13-column-wide
matrix's visible window at a time.
Also covers the two exclusions (§2/§3 -- New/commissioning and PAUSED never
counted) and offline-by-last_seen-staleness, since NodeStatus.OFFLINE is
never stored server-side.

Run with PYTHONPATH covering base-station/python/registry:
    PYTHONPATH=base-station/python/registry python3 base-station/tests/matrix_status_test.py
"""
import sys

from registry import NodeStatus, RegistryEntry, SensorChannel
from matrix_status import OFFLINE_AFTER_S, fleet_status_text

_NOW = 1_000_000.0
_FRESH = _NOW - 1  # seen a second ago -> online
_STALE = _NOW - (OFFLINE_AFTER_S + 5)  # past the staleness cutoff -> offline


def entry(status: NodeStatus, last_seen=_FRESH) -> RegistryEntry:
    """A RegistryEntry with only the fields fleet_status_text reads
    (status/last_seen) that matter; the rest are filler."""
    return RegistryEntry(
        node_id="n",
        display_name="n",
        sensor_config=frozenset({SensorChannel.ACCEL}),
        input_dim=512,
        status=status,
        last_seen=last_seen,
    )


def check(label: str, entries, expected: str) -> None:
    got = fleet_status_text(entries, now=_NOW)
    assert got == expected, f"{label}: expected {expected!r}, got {got!r}"
    # Firmware constraints (§1): uppercase-only font, 63-char cap.
    assert got == got.upper(), f"{label}: not uppercase: {got!r}"
    assert len(got) <= 63, f"{label}: over 63 chars: {got!r}"
    print(f"  {label}: PASS ({got!r})")


def main():
    # Empty fleet, and a fleet with nothing counted (New/commissioning/paused
    # only) -> blank display.
    check("empty fleet", [], "")
    check("nothing commissioned", [
        entry(NodeStatus.UNCOMMISSIONED),
        entry(NodeStatus.COMMISSIONING_COLLECTING),
        entry(NodeStatus.COMMISSIONING_TRAINING),
        entry(NodeStatus.PAUSED),
    ], "")

    # Everything healthy -> count-bearing "NOK".
    check("all healthy", [entry(NodeStatus.HEALTHY)] * 3, "3OK")
    # Excluded statuses don't inflate the healthy count.
    check("healthy plus excluded", [
        entry(NodeStatus.HEALTHY),
        entry(NodeStatus.HEALTHY),
        entry(NodeStatus.PAUSED),
        entry(NodeStatus.UNCOMMISSIONED),
    ], "2OK")

    # Anything wrong -> nonzero buckets only, fault->warning->offline order,
    # healthy dropped entirely.
    check("all three severities", [
        entry(NodeStatus.HEALTHY),
        entry(NodeStatus.FAULT),
        entry(NodeStatus.WARNING),
        entry(NodeStatus.WARNING),
        entry(NodeStatus.HEALTHY, last_seen=_STALE),  # -> offline
    ], "1FLT,2WRN,1OFF")
    check("fault only", [entry(NodeStatus.FAULT), entry(NodeStatus.HEALTHY)], "1FLT")
    check("warning only", [entry(NodeStatus.WARNING)], "1WRN")

    # Offline is derived from last_seen staleness, not a stored status:
    # a HEALTHY node gone quiet counts as offline, and healthy is dropped
    # once any bucket is nonzero.
    check("stale healthy is offline", [
        entry(NodeStatus.HEALTHY),
        entry(NodeStatus.HEALTHY, last_seen=_STALE),
    ], "1OFF")
    # A node that never streamed a frame (last_seen None) is New, not offline.
    check("null last_seen not offline", [
        entry(NodeStatus.HEALTHY),
        entry(NodeStatus.HEALTHY, last_seen=None),
    ], "2OK")
    # A stale node in an excluded status is still excluded (staleness never
    # resurrects a New/paused node into the counts).
    check("stale but excluded", [
        entry(NodeStatus.HEALTHY),
        entry(NodeStatus.PAUSED, last_seen=_STALE),
        entry(NodeStatus.UNCOMMISSIONED, last_seen=_STALE),
    ], "1OK")

    # Large fleet still fits the 63-char cap (§2's claim that counts-only
    # never needs truncation).
    check("large fleet fits", (
        [entry(NodeStatus.FAULT)] * 9999
        + [entry(NodeStatus.WARNING)] * 9999
        + [entry(NodeStatus.HEALTHY, last_seen=_STALE)] * 9999
    ), "9999FLT,9999WRN,9999OFF")

    print("RESULT: PASS - fleet_status_text matches LED_MATRIX_STATUS_PLAN.md §3")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"RESULT: FAIL - {e}")
        sys.exit(1)
