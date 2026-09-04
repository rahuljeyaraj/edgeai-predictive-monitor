"""11 -- the Edge Impulse round trip, driven from the dashboard (Chapter 6).

Bare vertical flowchart, same treatment as 04: no title band, no footnote
band, no text outside the boxes, one short line of detail per step. The
three things that used to sit in the footnotes and in the amber caveat box
are prose in the chapters -- pooled-not-per-node at REPORT.md's "The
baseline is pooled across the whole class", plain-REST-no-SDK just after
it, and "the classifier names faults, it never decides whether to stop a
motor" in Chapter 5.

Two columns rather than one, because *where* each step runs is the point of
the diagram: the flow snakes down the device column, crosses to Edge
Impulse for the one step that lives there, and comes back.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from diagram_lib import Canvas, save  # noqa: E402

c = Canvas(874, 524)

LX, RX = 228, 646     # the two column centre lines
BW, BH = 356, 76

# --- on the device ------------------------------------------------------
c.group(34, 56, 388, 434, "On the UNO Q", role="brain")

c.box(50, 76, BW, BH, "1 · Record",
      ["record labelled clips on the machine"], role="sense")
c.box(50, 182, BW, BH, "2 · Select & Upload",
      ["tick the clips, push them to the project"], role="brain")
c.box(50, 288, BW, BH, "4 · Fetch",
      ["pull the built .tflite back to the board"], role="brain")
c.box(50, 394, BW, BH, "5 · Classify live",
      ["TFLite on the CPU, same feature vector"], role="brain")

# --- Edge Impulse -------------------------------------------------------
c.group(452, 162, 388, 222, "Edge Impulse", role="neutral")

c.box(468, 182, BW, BH, "Project",
      ["one project per asset class"], role="neutral")
c.box(468, 288, BW, BH, "3 · Train in Studio",
      ["the one step left to Edge Impulse"], role="neutral")

# --- the round trip -----------------------------------------------------
c.link([(LX, 152), (LX, 182)])
c.link([(406, 220), (468, 220)])
c.link([(RX, 258), (RX, 288)])
# Fetch sits level with Train, so the return leg is one straight line back
# across rather than a detour around the group.
c.link([(468, 326), (406, 326)])
c.link([(LX, 364), (LX, 394)])

save(c, "11-edge-impulse-flow")
