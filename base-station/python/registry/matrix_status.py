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
# uncommissioned/commissioning_* are "New" (their own dashboard flow, no point
# duplicating on a hard-to-read display), and PAUSED is intentional/expected.
# Neither belongs in an at-a-glance fleet-health readout.
_UNCOUNTED = frozenset({
    NodeStatus.UNCOMMISSIONED,
    NodeStatus.COMMISSIONING_COLLECTING,
    NodeStatus.COMMISSIONING_TRAINING,
    NodeStatus.PAUSED,
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


def fleet_status_text(entries: Iterable[RegistryEntry],
                      now: Optional[float] = None) -> str:
    """Builds the rolling fleet-status string per LED_MATRIX_STATUS_PLAN.md §3
    from the current registry entries. `now` is the wall-clock time offline
    staleness is measured against (defaults to time.time(); injectable so the
    truth table is deterministically testable). Always uppercase and well
    under the firmware's 63-char cap -- counts-only never needs truncation."""
    if now is None:
        now = time.time()

    healthy = warning = fault = offline = 0
    for entry in entries:
        if entry.status in _UNCOUNTED:
            continue
        # Commissioned (HEALTHY/WARNING/FAULT). Staleness wins over the stored
        # status -- a node that went quiet is OFFLINE regardless of what it
        # last confirmed, matching frontend/app.js's bucketFor() (PAUSED is
        # already excluded above, preserving that function's precedence).
        # NodeStatus.OFFLINE is handled too, though nothing ever stores it.
        stale = entry.last_seen is not None and now - entry.last_seen > OFFLINE_AFTER_S
        if stale or entry.status == NodeStatus.OFFLINE:
            offline += 1
        elif entry.status == NodeStatus.HEALTHY:
            healthy += 1
        elif entry.status == NodeStatus.WARNING:
            warning += 1
        elif entry.status == NodeStatus.FAULT:
            fault += 1

    if healthy == 0 and warning == 0 and fault == 0 and offline == 0:
        # Empty fleet, or nothing commissioned yet -> blank display.
        return ""
    if warning == 0 and fault == 0 and offline == 0:
        # Everything healthy -> include the count (doubles as a fleet-size
        # readout), not a bare "ALL GOOD".
        return f"{healthy}{_HEALTHY_WORD}"
    # Anything wrong: only the nonzero buckets, fixed severity order
    # fault -> warning -> offline, healthy dropped entirely (§3).
    parts = []
    if fault:
        parts.append(f"{fault}{_FAULT_WORD}")
    if warning:
        parts.append(f"{warning}{_WARNING_WORD}")
    if offline:
        parts.append(f"{offline}{_OFFLINE_WORD}")
    return ",".join(parts)
