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

    assert color_for(NodeStatus.HEALTHY) == ("#10b981", "const", 0)
    print("healthy maps to green const: PASS")

    warning = color_for(NodeStatus.WARNING)
    assert warning.rgb == "#f59e0b" and warning.mode == "breathe", warning
    print("warning maps to yellow breathe: PASS")

    fault = color_for(NodeStatus.FAULT)
    assert fault.rgb == "#ef4444" and fault.mode == "strobe", fault
    print("fault maps to red strobe: PASS")

    print("RESULT: PASS - status_color.color_for matches the dashboard's own status colors")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"RESULT: FAIL - {e}")
        sys.exit(1)
