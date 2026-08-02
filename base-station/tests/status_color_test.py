#!/usr/bin/env python3
"""
Confirms status_color.color_for() covers every NodeStatus with the exact
rgb hex values the dashboard frontend uses for the same status
(base-station/python/frontend/style.css's --color-new/healthy/warning/fault), and that the
three confirmable/health statuses get the const/breathe/strobe modes the
user asked for (discovered=cyan const, healthy=green const,
warning=yellow breathe, fault=red strobe).

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

    assert color_for(NodeStatus.UNCOMMISSIONED) == ("#22d3ee", "const", 0)
    assert color_for(NodeStatus.COMMISSIONING_COLLECTING) == ("#22d3ee", "const", 0)
    assert color_for(NodeStatus.COMMISSIONING_TRAINING) == ("#22d3ee", "const", 0)
    print("discovered/in-progress statuses map to cyan const: PASS")

    # Near-primary, NOT the frontend's Tailwind palette: emerald-500 #10b981
    # and red-500 #ef4444 were confirmed on real hardware to render bluish
    # and pink respectively on an uncorrected WS2812. See status_color.py's
    # module docstring. This test asserted the old screen colors long after
    # the code was retuned, so it had been failing silently.
    assert color_for(NodeStatus.HEALTHY) == ("#00ff00", "const", 0)
    print("healthy maps to green const: PASS")

    warning = color_for(NodeStatus.WARNING)
    assert warning.rgb == "#f59e0b" and warning.mode == "breathe", warning
    print("warning maps to yellow breathe: PASS")

    fault = color_for(NodeStatus.FAULT)
    assert fault.rgb == "#ff0000" and fault.mode == "strobe", fault
    print("fault maps to red strobe: PASS")

    # Magenta since 2026-08-02, not blue: pure blue read the same as
    # _CYAN_NEW on the real ring. Asserted against _CYAN_NEW too, since
    # "tells those two apart" is the whole point of the value.
    idle = color_for(NodeStatus.IDLE)
    assert idle == ("#ff00ff", "const", 0), idle
    assert idle.rgb != color_for(NodeStatus.UNCOMMISSIONED).rgb, idle
    print("idle maps to magenta const, distinct from new: PASS")

    # Same red as FAULT, distinguished only by a slower strobe -- an urgent
    # alarm vs a latched "already acted". Asserting the period explicitly
    # because that difference IS the whole signal to an operator.
    tripped = color_for(NodeStatus.TRIPPED)
    assert tripped.rgb == "#ff0000" and tripped.mode == "strobe", tripped
    assert tripped.period_ms > fault.period_ms, (tripped, fault)
    print("tripped maps to red strobe, slower than fault: PASS")

    print("RESULT: PASS - status_color.color_for matches the hardware-tuned status colors")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"RESULT: FAIL - {e}")
        sys.exit(1)
