"""07 -- the machinery-protection trip, step by step (Chapter 5)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from diagram_lib import Canvas, save, INK_SOFT  # noqa: E402

c = Canvas(
    1320, 520,
    title="The trip chain — what actually happens between a bad reading and a stopped motor",
    subtitle="Five boring, checkable steps. Boring is the point: this is the only path in the system allowed to touch the physical world.",
    footnotes=[
        ("The delay is deliberate. A protection trip with no delay is a nuisance trip — one transient "
         "and the shop stops. Real protection relays use 1–3 s; ten makes the decision legible on screen.", None),
        ("A trip that was published but never confirmed is NOT reported as tripped. Showing “stopped” for "
         "a machine that is still turning would be the most dangerous lie this system could tell.", "#B03225"),
        ("Latched means latched: that motor refuses every later speed command, including from its own "
         "control panel, until a person clears it from the dashboard.", None),
    ],
)

c.box(34, 196, 220, 96, "1 · Fault confirmed",
      ["the score stays over the", "fault line, not just one frame"], role="warn")
c.box(290, 196, 200, 96, "2 · Countdown",
      ["10 s, visible on the", "dashboard · Hold cancels"], role="warn")
c.box(526, 196, 210, 96, "3 · Trip published",
      ["MQTT, naming exactly", "which motor — only that one"], role="act")
c.box(772, 196, 200, 96, "4 · Motor stopped",
      ["the rig's listener halts", "that axis and latches it"], role="act")

c.link([(254, 244), (290, 244)])
c.link([(490, 244), (526, 244)])
c.link([(736, 244), (772, 244)])

c.box(1060, 150, 226, 84, "5a · Tripped",
      ["the vibration gate", "confirms it went quiet"], role="act")
c.box(1060, 282, 226, 84, "5b · Trip failed",
      ["still turning — status", "stays Fault, and says so"], role="warn")

c.link([(972, 226), (1016, 226), (1016, 192), (1060, 192)], kind="arrowAct")
c.link([(972, 262), (1016, 262), (1016, 324), (1060, 324)], kind="arrowSoft")

c.text(390, 320, "operator's only chance to intervene", size=11, anchor="middle",
       fill=INK_SOFT, style="italic")
c.text(849, 320, "no human in this step", size=11, anchor="middle",
       fill=INK_SOFT, style="italic")

save(c, "07-trip-sequence")
