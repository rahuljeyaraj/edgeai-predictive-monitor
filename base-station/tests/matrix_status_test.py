#!/usr/bin/env python3
"""
Exercises matrix_status.fleet_status_text() against the truth table in
docs/LED_MATRIX_STATUS_PLAN.md §3: blank when nothing's commissioned, a
count-bearing "NOK" when all good, and the nonzero buckets in fixed
tripped->fault->warning->offline->healthy->idle->paused order when anything
else is true. Words are the matrix-only shorthand (OK/TRP/FLT/WRN/OFF/IDL/PSE,
not the NodeStatus vocabulary used elsewhere) and every separator is dropped
(no space after a count, no space after a comma) -- each character, including
a space, costs a fixed 6-column glyph slot on the firmware's 5x7 font, ~46% of
the 13-column-wide matrix's visible window at a time.
Also covers the one remaining exclusion (§2/§3 -- New/commissioning is never
counted; IDLE and PAUSED were exclusions too until 2026-08-02) and
offline-by-last_seen-staleness, since NodeStatus.OFFLINE is never stored
server-side.

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
        device_name="n",
        sensor_config=frozenset({SensorChannel.MIC}),
        input_dim=134,
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
    # Empty fleet, and a fleet with nothing counted (New/commissioning only)
    # -> blank display.
    check("empty fleet", [], "")
    check("nothing commissioned", [
        entry(NodeStatus.UNCOMMISSIONED),
        entry(NodeStatus.COMMISSIONING_COLLECTING),
        entry(NodeStatus.COMMISSIONING_TRAINING),
    ], "")

    # Everything healthy -> count-bearing "NOK".
    check("all healthy", [entry(NodeStatus.HEALTHY)] * 3, "3OK")
    # New/commissioning doesn't inflate the healthy count.
    check("healthy plus excluded", [
        entry(NodeStatus.HEALTHY),
        entry(NodeStatus.HEALTHY),
        entry(NodeStatus.UNCOMMISSIONED),
    ], "2OK")

    # Anything wrong -> nonzero buckets, tripped->fault->warning->offline->
    # healthy order, healthy last (not dropped) if any are still healthy.
    check("all three severities plus healthy", [
        entry(NodeStatus.HEALTHY),
        entry(NodeStatus.FAULT),
        entry(NodeStatus.WARNING),
        entry(NodeStatus.WARNING),
        entry(NodeStatus.HEALTHY, last_seen=_STALE),  # -> offline
    ], "1FLT,2WRN,1OFF,1OK")
    check("fault only", [entry(NodeStatus.FAULT)], "1FLT")
    check("fault plus healthy", [entry(NodeStatus.FAULT), entry(NodeStatus.HEALTHY)], "1FLT,1OK")
    check("warning only", [entry(NodeStatus.WARNING)], "1WRN")
    check("tripped leads", [
        entry(NodeStatus.FAULT),
        entry(NodeStatus.TRIPPED),
    ], "1TRP,1FLT")

    # IDLE/PAUSED count since 2026-08-02, last of all: "not running" is worth
    # showing even when nothing is wrong, but it never outranks a real fault.
    check("idle only", [entry(NodeStatus.IDLE)], "1IDL")
    check("paused only", [entry(NodeStatus.PAUSED)], "1PSE")
    check("idle and paused trail healthy", [
        entry(NodeStatus.HEALTHY),
        entry(NodeStatus.IDLE),
        entry(NodeStatus.PAUSED),
    ], "1OK,1IDL,1PSE")
    check("full severity ladder", [
        entry(NodeStatus.TRIPPED),
        entry(NodeStatus.FAULT),
        entry(NodeStatus.WARNING),
        entry(NodeStatus.HEALTHY, last_seen=_STALE),  # -> offline
        entry(NodeStatus.HEALTHY),
        entry(NodeStatus.IDLE),
        entry(NodeStatus.PAUSED),
    ], "1TRP,1FLT,1WRN,1OFF,1OK,1IDL,1PSE")

    # Offline is derived from last_seen staleness, not a stored status:
    # a HEALTHY node gone quiet counts as offline, and any still-healthy
    # nodes are appended last once any bucket is nonzero.
    check("stale healthy is offline", [
        entry(NodeStatus.HEALTHY),
        entry(NodeStatus.HEALTHY, last_seen=_STALE),
    ], "1OFF,1OK")
    # A node that never streamed a frame (last_seen None) is New, not offline.
    check("null last_seen not offline", [
        entry(NodeStatus.HEALTHY),
        entry(NodeStatus.HEALTHY, last_seen=None),
    ], "2OK")
    # A stale node in an excluded status is still excluded (staleness never
    # resurrects a New node into the counts).
    check("stale but excluded", [
        entry(NodeStatus.HEALTHY),
        entry(NodeStatus.UNCOMMISSIONED, last_seen=_STALE),
    ], "1OK")
    # Staleness precedence, mirroring frontend/app.js's bucketFor(): PAUSED is
    # a standing operator intent and stays paused however long it's been quiet,
    # while a stale IDLE node reads offline first, idle second.
    check("stale paused stays paused", [
        entry(NodeStatus.HEALTHY),
        entry(NodeStatus.PAUSED, last_seen=_STALE),
    ], "1OK,1PSE")
    check("stale idle is offline", [
        entry(NodeStatus.HEALTHY),
        entry(NodeStatus.IDLE, last_seen=_STALE),
    ], "1OFF,1OK")

    # Large fleet still fits the 63-char cap (§2's claim that counts-only
    # never needs truncation) -- every bucket nonzero at 4 digits is the
    # worst case the display can be asked to show.
    check("large fleet fits", (
        [entry(NodeStatus.TRIPPED)] * 9999
        + [entry(NodeStatus.FAULT)] * 9999
        + [entry(NodeStatus.WARNING)] * 9999
        + [entry(NodeStatus.HEALTHY, last_seen=_STALE)] * 9999
        + [entry(NodeStatus.HEALTHY)] * 9999
        + [entry(NodeStatus.IDLE)] * 9999
        + [entry(NodeStatus.PAUSED)] * 9999
    ), "9999TRP,9999FLT,9999WRN,9999OFF,9999OK,9999IDL,9999PSE")

    print("RESULT: PASS - fleet_status_text matches LED_MATRIX_STATUS_PLAN.md §3")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"RESULT: FAIL - {e}")
        sys.exit(1)
