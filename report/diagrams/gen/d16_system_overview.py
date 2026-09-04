"""16 -- system overview for the Hackster write-up, section 2.2.

Deliberately different from d5_full_architecture: no title band, no legend and
no footnote band, because the article's own caption carries that text and the
image is read on a phone. Everything the picture has to say is inside a box.
Fonts run two points larger than the report diagrams for the same reason.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from diagram_lib import Canvas, save  # noqa: E402

c = Canvas(1790, 900)

TITLE = 17.5
BODY = 13.5

# ------------------------------------------------------------------ senses
# One box per kind of node, each centred on the exact y it enters the UNO Q so
# both feeds are straight lines. The 206 px gap to the group is what keeps the
# wire labels off both boxes.
c.box(34, 160, 430, 150, "Base station node",
      ["accelerometer + microphone", "the machine standing beside it"],
      role="sense", title_size=TITLE, body_size=BODY)
c.box(34, 385, 430, 150, "Satellite node",
      ["accelerometer + microphone", "one on every other machine"],
      role="sense", title_size=TITLE, body_size=BODY)

# ------------------------------------------------------------------- brain
c.group(640, 110, 540, 620, "Arduino UNO Q", role="brain")
c.box(670, 155, 480, 160, "Microcontroller",
      ["reads the sensors", "FFT + statistics"],
      role="sense", title_size=TITLE, body_size=BODY)
c.box(670, 370, 480, 330, "Linux processor",
      ["", "detection model, one per machine",
       "identification model, one per machine type",
       "dashboard web server", "alerts and trip logic"],
      role="brain", title_size=TITLE, body_size=BODY)

c.link([(464, 235), (670, 235)], label="SPI \u00b7 I\u00b2S", label_size=12)
c.link([(464, 460), (670, 460)], label="Wi-Fi \u00b7 MQTT", label_size=12)
c.link([(910, 315), (910, 370)], label="536 numbers, 5 times a second",
       label_size=12)

# -------------------------------------------------------- tells and action
c.box(1250, 120, 500, 92, "Status dome on every node", role="tell",
      title_size=TITLE)
c.box(1250, 232, 500, 92, "LED matrix on the base station", role="tell",
      title_size=TITLE)
c.box(1250, 344, 500, 92, "Dashboard in any browser", role="tell",
      title_size=TITLE)
c.box(1250, 456, 500, 92, "Telegram on a phone", role="tell",
      title_size=TITLE)

# The four outputs share one exit point and one riser, so they read as a
# single fan-out. The stop is deliberately not on that bus: it leaves the
# Linux box dead level with its own target.
for entry in (166, 278, 390, 502):
    c.link([(1150, 535), (1205, 535), (1205, entry), (1250, entry)],
           kind="arrowTell")

c.box(1250, 600, 500, 130, "Stop the machine",
      ["10 second countdown, then the motor stops",
       "stays stopped until someone clears it"],
      role="act", title_size=TITLE, body_size=BODY)
c.link([(1150, 665), (1250, 665)], kind="arrowAct", width=2.4)

# ------------------------------------------------------------ the one link
# that leaves the building, drawn dashed and grey so it reads as optional.
c.box(670, 780, 480, 76, "Edge Impulse",
      ["the only step that needs internet"],
      role="ghost", title_size=TITLE, body_size=BODY, dashed=True)
c.link([(910, 700), (910, 780)], label="labelled fault recordings",
       kind="arrowSoft", dashed=True, label_size=12)

save(c, "16-system-overview")
