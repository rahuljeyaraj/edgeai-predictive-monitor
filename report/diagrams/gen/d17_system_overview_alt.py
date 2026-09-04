"""17 -- system overview, variant B (experiment for the Hackster article).

Same content as d16, one structural difference: every output belongs to the
node that drives it. The status dome and LED matrix hang off the
microcontroller, the dashboard and Telegram off the Linux processor, and all
four sit *inside* the base station group; the satellite's own dome sits inside
the satellite group. Only the motor driver and Edge Impulse are outside, being
the two things that are not part of a node.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from diagram_lib import Canvas, ROLES, save  # noqa: E402

c = Canvas(
    1660, 1080,
    legend=[("sense", "Senses"), ("brain", "Decides"),
            ("tell", "Tells a human"), ("act", "Acts (physical)"),
            ("ghost", "Off the device")],
)

TITLE = 17.5
BODY = 13.5

# ----------------------------------------------------------- base station
c.group(34, 110, 1560, 560, "Base station node", role="sense")

c.box(70, 175, 300, 90, "Accelerometer", ["vibration up to 6 kHz"],
      role="sense", title_size=TITLE, body_size=BODY)
c.box(70, 275, 300, 90, "Microphone", ["sound up to 24 kHz"],
      role="sense", title_size=TITLE, body_size=BODY)

c.group(430, 140, 660, 500, "Arduino UNO Q", role="brain")
c.box(460, 180, 600, 170, "Microcontroller",
      ["reads the sensors", "FFT + statistics"],
      role="sense", title_size=TITLE, body_size=BODY)
c.box(460, 400, 600, 200, "Linux processor",
      ["detection model, one per asset",
       "identification model, one per asset class",
       "dashboard web server", "alerts and trip logic"],
      role="brain", title_size=TITLE, body_size=BODY)

c.link([(370, 220), (460, 220)], label="SPI", label_size=12)
c.link([(370, 320), (460, 320)], label="I²S", label_size=12)
c.link([(760, 350), (760, 400)], label="UART · SPI", label_size=12, both=True)

# Each output hangs off the processor that actually drives it, so the two
# halves of the board are told apart by what they own rather than by a caption.
c.box(1150, 175, 400, 80, "Status dome", role="tell", title_size=TITLE)
c.box(1150, 270, 400, 80, "LED matrix", role="tell", title_size=TITLE)
c.box(1150, 420, 400, 80, "Dashboard in any browser", role="tell",
      title_size=TITLE)
c.box(1150, 515, 400, 80, "Telegram on a phone", role="tell", title_size=TITLE)

for y in (215, 310):
    c.link([(1060, y), (1150, y)], kind="arrowTell")
for y in (460, 555):
    c.link([(1060, y), (1150, y)], kind="arrowTell")

# --------------------------------------------------------- satellite node
# Narrower than the base station so both the stop and the Edge Impulse lanes
# have clear air to run down, well inside the Linux box rather than along its
# right-hand edge.
for off in (32, 16):
    c.raw(f'<rect x="{34 + off}" y="{730 + off}" width="760" height="270" rx="13" '
          f'fill="#FFFFFF" stroke="#8C9AA8" stroke-width="1.2" '
          f'stroke-dasharray="8,5"/>')
c.group(34, 730, 760, 270, "Satellite node × N", role="sense")

c.box(70, 770, 210, 80, "Accelerometer", role="sense", title_size=TITLE)
c.box(70, 880, 210, 80, "Microphone", role="sense", title_size=TITLE)
c.box(350, 780, 250, 170, "XIAO ESP32-S3",
      ["reads the sensors", "FFT + statistics"],
      role="sense", title_size=TITLE, body_size=BODY)

# Two lines of title, so a narrow box does not have to carry one long word
# pair across its whole width.
c.box(640, 815, 140, 100, "", role="tell")
for i, word in enumerate(("Status", "dome")):
    c.text(710, 855 + i * 26, word, size=TITLE, weight="bold", anchor="middle",
           fill=ROLES["tell"][2])

c.link([(280, 810), (350, 810)], label="SPI", label_size=12)
c.link([(280, 920), (350, 920)], label="I²S", label_size=12)
c.link([(600, 865), (640, 865)], kind="arrowTell")
c.link([(475, 780), (475, 600)], label="Wi-Fi · MQTT", label_size=12)

# ------------------------------------------------- what is not a node yet
# Both boxes share the output column's x, so every consequence of a frame,
# green or red or grey, sits on one vertical line.
c.box(1150, 760, 400, 130, "Motor driver",
      ["10 second countdown, then the motor trips",
       "it stays tripped until someone clears it"],
      role="act", title_size=TITLE, body_size=BODY)
c.link([(930, 600), (930, 825), (1150, 825)], label="stop command",
       kind="arrowAct", width=2.4, label_size=12, label_seg=0)

c.box(1150, 930, 400, 110, "Edge Impulse",
      ["training the fault classifier", "the one step needing internet"],
      role="ghost", title_size=TITLE, body_size=BODY, dashed=True)
c.link([(870, 600), (870, 985), (1150, 985)], label="fault recordings",
       kind="arrowSoft", dashed=True, label_size=12, label_seg=0, label_at=0.8)

save(c, "17-system-overview-alt")
