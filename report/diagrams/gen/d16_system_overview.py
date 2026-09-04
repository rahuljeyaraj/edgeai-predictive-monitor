"""16 -- system overview for the Hackster write-up, section 2.2.

Deliberately different from d5_full_architecture: no title band and no footnote
band, because the article's own caption carries that text and the image is read
on a phone. Fonts run two points larger for the same reason.

The two dashed groups are the point of the picture: a node is sensors *plus* a
board, so the UNO Q is drawn inside the base station rather than beside it.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from diagram_lib import Canvas, save  # noqa: E402

c = Canvas(
    1834, 1000,
    legend=[("sense", "Senses"), ("brain", "Decides"),
            ("tell", "Tells a human"), ("act", "Acts (physical)"),
            ("ghost", "Off the device")],
)

TITLE = 17.5
BODY = 13.5

# ----------------------------------------------------------- base station
# Sensors, then the board they are wired to, all inside one group: the base
# station is a node like any other, it just happens to hold the brain too.
c.group(34, 110, 1150, 520, "Base station node", role="sense")

c.box(70, 195, 300, 90, "Accelerometer", ["vibration up to 6 kHz"],
      role="sense", title_size=TITLE, body_size=BODY)
c.box(70, 295, 300, 90, "Microphone", ["sound up to 24 kHz"],
      role="sense", title_size=TITLE, body_size=BODY)

c.group(430, 140, 710, 460, "Arduino UNO Q", role="brain")
c.box(460, 180, 650, 180, "Microcontroller",
      ["reads the sensors", "FFT + statistics"],
      role="sense", title_size=TITLE, body_size=BODY)
c.box(460, 400, 650, 170, "Linux processor",
      ["detection model, one per asset",
       "identification model, one per asset class",
       "dashboard web server", "alerts and trip logic"],
      role="brain", title_size=TITLE, body_size=BODY)

c.link([(370, 240), (460, 240)], label="SPI", label_size=12)
c.link([(370, 340), (460, 340)], label="I²S", label_size=12)
c.link([(785, 360), (785, 400)], label="UART · SPI", label_size=12, both=True)

# --------------------------------------------------------- satellite node
# Same two sensors, a different board. The only thing that changes is how the
# numbers reach the Linux processor.
for off in (32, 16):
    c.raw(f'<rect x="{34 + off}" y="{670 + off}" width="740" height="250" rx="13" '
          f'fill="#FFFFFF" stroke="#8C9AA8" stroke-width="1.2" '
          f'stroke-dasharray="8,5"/>')
c.group(34, 670, 740, 250, "Satellite node × N", role="sense")

c.box(70, 710, 260, 80, "Accelerometer", role="sense", title_size=TITLE)
c.box(70, 810, 260, 80, "Microphone", role="sense", title_size=TITLE)
c.box(430, 720, 310, 160, "XIAO ESP32-S3",
      ["reads the sensors", "FFT + statistics"],
      role="sense", title_size=TITLE, body_size=BODY)

c.link([(330, 750), (430, 750)], label="SPI", label_size=12)
c.link([(330, 850), (430, 850)], label="I²S", label_size=12)
c.link([(585, 720), (585, 570)], label="Wi-Fi · MQTT", label_size=12)

# ------------------------------------------------------- tells and action
c.box(1300, 140, 500, 92, "Status dome on every node", role="tell",
      title_size=TITLE)
c.box(1300, 240, 500, 92, "LED matrix on the base station", role="tell",
      title_size=TITLE)
c.box(1300, 340, 500, 92, "Dashboard in any browser", role="tell",
      title_size=TITLE)
c.box(1300, 440, 500, 92, "Telegram on a phone", role="tell",
      title_size=TITLE)

# The four outputs share one exit point and one riser, so they read as a
# single fan-out. The stop gets its own riser lower down, clear of that bus.
for entry in (186, 286, 386, 486):
    c.link([(1110, 460), (1220, 460), (1220, entry), (1300, entry)],
           kind="arrowTell")

c.box(1300, 720, 500, 130, "Stop the machine",
      ["10 second countdown, then the motor stops",
       "stays stopped until someone clears it"],
      role="act", title_size=TITLE, body_size=BODY)
c.link([(1110, 545), (1270, 545), (1270, 785), (1300, 785)],
       kind="arrowAct", width=2.4)

# ----------------------------------------------------------- the one link
# that leaves the building, drawn dashed and grey so it reads as optional.
c.box(844, 740, 340, 110, "Edge Impulse",
      ["training the fault classifier", "the one step needing internet"],
      role="ghost", title_size=TITLE, body_size=BODY, dashed=True)
c.link([(1014, 570), (1014, 740)], label="fault recordings",
       kind="arrowSoft", dashed=True, label_size=12)

save(c, "16-system-overview")
