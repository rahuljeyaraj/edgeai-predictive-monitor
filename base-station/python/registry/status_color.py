"""NodeStatus -> STATUS_LED color mapping (docs/Appendix_B_Wire_Protocol_
Specification.md S3, S1 message type 0x08), for main.py to push whenever a
node's status changes (see Registry.on_status_change) -- over MQTT to a
satellite node, or over the local Bridge RPC link for this board's own ring
(main.py's wire_local_status_led).

rgb values are hand-tuned for raw WS2812 rendering, NOT copied from the
dashboard frontend's palette (mpu/frontend/style.css's
--color-new/healthy/warning/fault) as they used to be -- confirmed on real
hardware 2026-07-16 that a screen-friendly, desaturated color (Tailwind
emerald-500 #10b981, R16/G185/B129; red-500 #ef4444, R239/G68/B68) reads
wrong on an uncorrected WS2812: any non-trivial secondary channel shows up
disproportionately strongly on these LEDs, so emerald green (B=129, ~51% of
G) rendered visibly bluish and red-500 (G=B=68, ~28% of R) rendered as light
pink. Near-primary values with negligible secondary channels avoid that.
mode/period_ms reuse the exact const/breathe/strobe vocabulary and periods
already established for the UART-side MCU display
(mpu/tests/display_rgb_test.py's BREATHE 1500ms / STROBE 200ms steps).
"""
from typing import NamedTuple

from registry import NodeStatus


class LedCommand(NamedTuple):
    rgb: str
    mode: str
    period_ms: int


_CYAN_NEW = LedCommand(rgb="#22d3ee", mode="const", period_ms=0)
_GREEN_HEALTHY = LedCommand(rgb="#00ff00", mode="const", period_ms=0)
_YELLOW_WARNING_BREATHE = LedCommand(rgb="#f59e0b", mode="breathe", period_ms=1500)
_RED_FAULT_STROBE = LedCommand(rgb="#ff0000", mode="strobe", period_ms=200)
_GREY_PAUSED = LedCommand(rgb="#717171", mode="const", period_ms=0)
_GREY_OFFLINE = LedCommand(rgb="#4d4d4d", mode="const", period_ms=0)
# Machinery-protection states (docs/MOTOR_STOP_PLAN.md).
#
# IDLE is magenta -- not a grey, because "switched off by an operator" is a
# normal condition while the greys above both mean "you are not getting data
# from this node", and not blue either: it was pure blue #0000ff until
# 2026-08-02, when it was reported on real hardware as indistinguishable from
# _CYAN_NEW's blue-dominant cast. Magenta is still a full-strength two-channel
# mix (R=B=255, G=0) so it obeys this file's near-primary rule -- the
# dashboard's matching fuchsia (--color-idle #d946ef) is deliberately NOT
# reused here, since its G=70 is exactly the weak-secondary case the module
# docstring says washes out on an uncorrected WS2812.
_MAGENTA_IDLE = LedCommand(rgb="#ff00ff", mode="const", period_ms=0)
# TRIPPED reuses FAULT's red and differs only in strobe period: 200ms reads
# as an urgent alarm, 1000ms as a deliberate, latched "I already acted".
# Deliberately NOT a new mode -- const/breathe/strobe is the whole vocabulary
# both the MQTT payload (wire_protocol.LED_MODE_TO_INT) and the MCU display
# firmware understand, so a slow blink has to be a slow strobe or it would
# mean re-flashing every node.
_RED_TRIPPED_SLOW = LedCommand(rgb="#ff0000", mode="strobe", period_ms=1000)

# Every NodeStatus a node can actually be pushed while still reachable over
# MQTT to receive it. OFFLINE is included defensively even though nothing
# server-side ever sets it today (registry.py's own docstring: it's a
# frontend-computed staleness label) -- a node that's actually offline can't
# receive this command anyway, so it's harmless dead code if never hit.
_LED_BY_STATUS = {
    NodeStatus.UNCOMMISSIONED: _CYAN_NEW,
    NodeStatus.COMMISSIONING_COLLECTING: _CYAN_NEW,
    NodeStatus.COMMISSIONING_TRAINING: _CYAN_NEW,
    NodeStatus.HEALTHY: _GREEN_HEALTHY,
    NodeStatus.WARNING: _YELLOW_WARNING_BREATHE,
    NodeStatus.FAULT: _RED_FAULT_STROBE,
    NodeStatus.PAUSED: _GREY_PAUSED,
    NodeStatus.OFFLINE: _GREY_OFFLINE,
    NodeStatus.IDLE: _MAGENTA_IDLE,
    NodeStatus.TRIPPED: _RED_TRIPPED_SLOW,
}


def color_for(status: NodeStatus) -> LedCommand:
    return _LED_BY_STATUS[status]
