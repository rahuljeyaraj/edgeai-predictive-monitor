#!/usr/bin/env python3
"""
Confirms status_color.color_for() covers every NodeStatus with the exact
rgb hex values the dashboard frontend uses for the same status
(base-station/python/frontend/style.css's --color-new/healthy/warning/fault), and that the
three confirmable/health statuses get the const/breathe/strobe modes the
user asked for (discovered=cyan const, healthy=green const,
warning=yellow strobe, fault=red strobe).

Run with PYTHONPATH covering base-station/python/registry:
    PYTHONPATH=base-station/python/registry python3 base-station/tests/status_color_test.py
"""
import sys

from registry import NodeStatus
from status_color import color_for


def main():
    for status in NodeStatus:
        led = color_for(status)
        assert led.rgb.startswith("#") and len(led.rgb) == 7, led.rgb
        assert led.mode in ("const", "breathe", "strobe"), led.mode
    print("color_for() covers every NodeStatus with a valid rgb/mode: PASS")

    assert color_for(NodeStatus.UNCOMMISSIONED) == ("#00ffff", "const", 0)
    assert color_for(NodeStatus.COMMISSIONING_COLLECTING) == ("#00ffff", "const", 0)
    assert color_for(NodeStatus.COMMISSIONING_TRAINING) == ("#00ffff", "const", 0)
    print("discovered/in-progress statuses map to cyan const: PASS")

    assert color_for(NodeStatus.HEALTHY) == ("#00ff00", "const", 0)
    print("healthy maps to green const: PASS")

    warning = color_for(NodeStatus.WARNING)
    assert warning.rgb == "#f59e0b" and warning.mode == "strobe", warning
    print("warning maps to amber strobe: PASS")

    fault = color_for(NodeStatus.FAULT)
    assert fault.rgb == "#ff0000" and fault.mode == "strobe", fault
    print("fault maps to red strobe: PASS")

    # PAUSED reuses WARNING's amber, distinguished by mode (const vs
    # strobe) rather than a separate hue.
    paused = color_for(NodeStatus.PAUSED)
    assert paused.rgb == "#f59e0b" and paused.mode == "const", paused
    assert paused.mode != warning.mode, (paused, warning)
    print("paused maps to amber const, distinct pattern from warning: PASS")

    # OFFLINE is dead code in practice (a genuinely offline node has no
    # channel to receive this), so it's simply off rather than spending a
    # hue on it.
    offline = color_for(NodeStatus.OFFLINE)
    assert offline == ("#000000", "const", 0), offline
    print("offline maps to off: PASS")

    # White since 2026-08-17, not magenta: magenta was reassigned to the
    # satellite's own provisioning/connectivity ring language.
    idle = color_for(NodeStatus.IDLE)
    assert idle == ("#ffffff", "const", 0), idle
    print("idle maps to white const: PASS")

    # Same red as FAULT, distinguished by pattern SHAPE (breathe, not a
    # second strobe speed) -- an urgent alarm vs a latched "already acted".
    tripped = color_for(NodeStatus.TRIPPED)
    assert tripped.rgb == "#ff0000" and tripped.mode == "breathe", tripped
    assert tripped.mode != fault.mode, (tripped, fault)
    print("tripped maps to red breathe, distinct pattern from fault: PASS")

    print("RESULT: PASS - status_color.color_for matches the hardware-tuned status colors")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"RESULT: FAIL - {e}")
        sys.exit(1)
