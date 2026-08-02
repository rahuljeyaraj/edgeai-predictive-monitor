"""Fleet-health -> LED matrix message (docs/LED_MATRIX_STATUS_PLAN.md).

Pure counts->string builder (fleet_status_text), kept independent of the
Bridge RPC call so it's unit-testable on its own (tests/matrix_status_test.py)
-- the same split as status_color.py's color_for() vs main.py's
wire_local_status_led() Bridge push. main.py's wire_local_matrix_text()
subscribes to Registry.on_status_change, calls this to build the string, and
pushes it to the board's own 8x13 matrix via `set_matrix_text`.
"""
import time
from typing import Iterable, Optional

from registry import NodeStatus, RegistryEntry

# Mirror of frontend/app.js's OFFLINE_AFTER_S: a commissioned node counts as
# offline once nothing's been heard from it for this long. NodeStatus.OFFLINE
# is never stored server-side (registry.py -- it's a last_seen staleness label
# the frontend computes), so the offline bucket is recomputed here the same
# way, keeping the matrix's OFFLINE count in step with the dashboard's Offline
# tile. Keep this value in sync with that frontend constant.
OFFLINE_AFTER_S = 30

# Statuses excluded from every count (LED_MATRIX_STATUS_PLAN.md §2/§3):
# uncommissioned/commissioning_* are "New" -- a node mid-setup is already in
# front of whoever is setting it up, on the dashboard, so duplicating it on a
# hard-to-read display buys nothing.
# IDLE and PAUSED used to be excluded on the same reasoning, since neither is
# a fleet-health problem and this display was scoped to "is anything wrong".
# They count now (2026-08-02): a stopped or paused machine is still a machine
# not producing, so "nothing wrong" and "nothing running" are different
# answers and the board was silent about the difference. They rank last,
# after healthy -- see the severity order below. TRIPPED is the opposite end
# of that scale: it means this system stopped a machine, the single most
# important thing the fleet can be doing, so it ranks above FAULT.
_UNCOUNTED = frozenset({
    NodeStatus.UNCOMMISSIONED,
    NodeStatus.COMMISSIONING_COLLECTING,
    NodeStatus.COMMISSIONING_TRAINING,
})

# Shorthand words for the matrix (not the NodeStatus vocabulary used
# everywhere else -- "fault" stays "fault" in the registry/frontend/alerts,
# this is a display-only trim). Every glyph, including a space, costs a
# fixed 6-column slot on the firmware's 5x7 font (FONT_GLYPH_STRIDE,
# matrix_display.cpp) -- on a 13-column-wide matrix, a single space blanks
# ~46% of the visible window at any scroll position, so separators are
# dropped entirely (no space after the count, no space after a comma)
# rather than just shortened.
_HEALTHY_WORD = "OK"
_FAULT_WORD = "FLT"
_WARNING_WORD = "WRN"
_OFFLINE_WORD = "OFF"
_TRIPPED_WORD = "TRP"
_IDLE_WORD = "IDL"
_PAUSED_WORD = "PSE"


def fleet_status_text(entries: Iterable[RegistryEntry],
                      now: Optional[float] = None) -> str:
    """Builds the rolling fleet-status string per LED_MATRIX_STATUS_PLAN.md §3
    from the current registry entries. `now` is the wall-clock time offline
    staleness is measured against (defaults to time.time(); injectable so the
    truth table is deterministically testable). Always uppercase and well
    under the firmware's 63-char cap -- counts-only never needs truncation."""
    if now is None:
        now = time.time()

    healthy = warning = fault = offline = tripped = idle = paused = 0
    for entry in entries:
        if entry.status in _UNCOUNTED:
            continue
        # PAUSED is checked before staleness, deliberately, and IDLE after --
        # exactly the precedence frontend/app.js's bucketFor() uses. Pausing is
        # a standing operator intent that outranks the node going quiet (a
        # paused node is *expected* to stop reporting), while a machine merely
        # switched off at the rig is offline first and idle second, the same
        # way a faulted node that goes quiet reads offline.
        if entry.status == NodeStatus.PAUSED:
            paused += 1
            continue
        # Staleness wins over the stored status -- a node that went quiet is
        # OFFLINE regardless of what it last confirmed. NodeStatus.OFFLINE is
        # handled too, though nothing ever stores it.
        stale = entry.last_seen is not None and now - entry.last_seen > OFFLINE_AFTER_S
        if stale or entry.status == NodeStatus.OFFLINE:
            offline += 1
        elif entry.status == NodeStatus.HEALTHY:
            healthy += 1
        elif entry.status == NodeStatus.WARNING:
            warning += 1
        elif entry.status == NodeStatus.FAULT:
            fault += 1
        elif entry.status == NodeStatus.TRIPPED:
            tripped += 1
        elif entry.status == NodeStatus.IDLE:
            idle += 1

    if (healthy == 0 and warning == 0 and fault == 0 and offline == 0
            and tripped == 0 and idle == 0 and paused == 0):
        # Empty fleet, or nothing commissioned yet -> blank display.
        return ""
    if (warning == 0 and fault == 0 and offline == 0 and tripped == 0
            and idle == 0 and paused == 0):
        # Everything healthy -> just the count (doubles as a fleet-size
        # readout), not a bare "ALL GOOD".
        return f"{healthy}{_HEALTHY_WORD}"
    # Anything else: nonzero buckets, fixed severity order tripped -> fault ->
    # warning -> offline, then healthy, then idle -> paused last. The word
    # shortening (OK/TRP/FLT/WRN/OFF/IDL/PSE) made room to keep healthy in the
    # message even here, instead of dropping it (§3 revision). TRIPPED leads
    # because a machine this system has physically stopped outranks one that is
    # merely faulted -- it's the one state that already had a real-world
    # consequence. IDLE and PAUSED trail healthy because neither is a problem
    # at all; they're here to say "not running", not "not well".
    parts = []
    if tripped:
        parts.append(f"{tripped}{_TRIPPED_WORD}")
    if fault:
        parts.append(f"{fault}{_FAULT_WORD}")
    if warning:
        parts.append(f"{warning}{_WARNING_WORD}")
    if offline:
        parts.append(f"{offline}{_OFFLINE_WORD}")
    if healthy:
        parts.append(f"{healthy}{_HEALTHY_WORD}")
    if idle:
        parts.append(f"{idle}{_IDLE_WORD}")
    if paused:
        parts.append(f"{paused}{_PAUSED_WORD}")
    return ",".join(parts)
