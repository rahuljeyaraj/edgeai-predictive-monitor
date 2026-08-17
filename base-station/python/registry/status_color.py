"""NodeStatus -> STATUS_LED color mapping (docs/Appendix_B_Wire_Protocol_
Specification.md S3, S1 message type 0x08), for main.py to push whenever a
node's status changes (see Registry.on_status_change) -- over MQTT to a
satellite node, or over the local Bridge RPC link for this board's own ring
(main.py's wire_local_status_led).

rgb values are pure/near-primary (single or dual full-strength channels)
where that actually reads true on an uncorrected WS2812: confirmed on real
hardware 2026-07-16 that any non-trivial secondary channel shows up
disproportionately strongly on these LEDs (a desaturated Tailwind
emerald-500 rendered bluish, a desaturated red-500 rendered pink).

WARNING/PAUSED are the one deliberate exception to "pure hue": full-strength
yellow (#ffff00, R=G=255) was tried 2026-08-17 and reported on real hardware
as reading light green, not yellow -- this ring's green channel is
perceptually/physically brighter than its red at equal PWM duty, so an
equal-intensity R/G mix skews green despite being "near-primary" by the
letter of the rule above. Amber (#f59e0b, G at ~65% of R) is the
hardware-confirmed correction for this specific hue, not a regression back
to the old screen-tuned palette.

2026-08-17: these are also now the dashboard frontend's palette
(style.css's --color-new/healthy/warning/fault/idle) -- deliberately kept
identical on both sides (user's call) rather than screen-tuned separately,
so an operator who has learned the ring's colors reads the same colors on
the dashboard tiles. Change one, change both. Two states that reuse another
status's hue (PAUSED reuses WARNING's amber, TRIPPED reuses FAULT's red --
disambiguated by mode/period on the ring) get a *distinguishable shade* of
that hue on the dashboard instead of the identical hex, since a static tile
has no blink to fall back on.

mode/period_ms use the const/breathe/strobe vocabulary and periods the
MCU display firmware and MQTT payload (wire_protocol.LED_MODE_TO_INT) both
understand.
"""
from typing import NamedTuple

from registry import NodeStatus


class LedCommand(NamedTuple):
    rgb: str
    mode: str
    period_ms: int


_CYAN_NEW = LedCommand(rgb="#00ffff", mode="const", period_ms=0)
_GREEN_HEALTHY = LedCommand(rgb="#00ff00", mode="const", period_ms=0)
_AMBER_WARNING = LedCommand(rgb="#f59e0b", mode="strobe", period_ms=1000)
_RED_FAULT_STROBE = LedCommand(rgb="#ff0000", mode="strobe", period_ms=200)
# PAUSED reuses WARNING's amber -- "operator switched it off" is a normal,
# not-urgent condition, so it stays solid (const) where WARNING strobes.
_AMBER_PAUSED = LedCommand(rgb="#f59e0b", mode="const", period_ms=0)
# OFFLINE is dead code in practice (registry.py's own docstring: it's a
# frontend-computed staleness label; a node that's actually offline has no
# channel to receive this command anyway), so its command is simply "off"
# rather than spending one of the 8 hardware-safe hues on an unreachable
# state.
_OFF_OFFLINE = LedCommand(rgb="#000000", mode="const", period_ms=0)
# Machinery-protection states (docs/MOTOR_STOP_PLAN.md).
#
# IDLE is white -- not a grey (the greys used to mean "no data from this
# node" before OFFLINE/PAUSED moved off grey too), and not magenta anymore
# either: magenta was reassigned 2026-08-17 to the satellite's own
# provisioning/connectivity ring language (transport_task.cpp), and reusing
# it here would have made three unrelated states share one hue with only a
# blink pattern telling them apart.
_WHITE_IDLE = LedCommand(rgb="#ffffff", mode="const", period_ms=0)
# TRIPPED reuses FAULT's red but as a BREATHE, not a second strobe speed:
# 200ms strobe reads as an urgent alarm, a slow breathe reads as a
# deliberate, latched "I already acted" -- distinguishable by pattern SHAPE
# now, not just by timing two strobes apart.
_RED_TRIPPED_BREATHE = LedCommand(rgb="#ff0000", mode="breathe", period_ms=1000)

# Every NodeStatus a node can actually be pushed while still reachable over
# MQTT to receive it. OFFLINE is included defensively even though nothing
# server-side ever sets it today -- see _OFF_OFFLINE above.
_LED_BY_STATUS = {
    NodeStatus.UNCOMMISSIONED: _CYAN_NEW,
    NodeStatus.COMMISSIONING_COLLECTING: _CYAN_NEW,
    NodeStatus.COMMISSIONING_TRAINING: _CYAN_NEW,
    NodeStatus.HEALTHY: _GREEN_HEALTHY,
    NodeStatus.WARNING: _AMBER_WARNING,
    NodeStatus.FAULT: _RED_FAULT_STROBE,
    NodeStatus.PAUSED: _AMBER_PAUSED,
    NodeStatus.OFFLINE: _OFF_OFFLINE,
    NodeStatus.IDLE: _WHITE_IDLE,
    NodeStatus.TRIPPED: _RED_TRIPPED_BREATHE,
}


def color_for(status: NodeStatus) -> LedCommand:
    return _LED_BY_STATUS[status]
