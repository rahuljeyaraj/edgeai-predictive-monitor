"""01 -- system at a glance (Chapter 1)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from diagram_lib import Canvas, save, ROLE_LEGEND  # noqa: E402

c = Canvas(
    1180, 660,
    title="EdgeAI Predictive Monitor — the whole loop, one picture",
    subtitle="Every sensing point reports to one base station; the base station is the only thing that decides.",
    legend=[(k, ROLE_LEGEND[k]) for k in ("sense", "brain", "tell", "act")],
    footnotes=[
        ("Sensing → deciding → acting closes without a human in it. Everything left of the "
         "base station is a sense organ; everything right of it is an output.", None),
        ("The STOP arrow is the one that makes this Physical AI rather than a very "
         "well-instrumented dashboard.", "#B03225"),
    ],
)

c.box(34, 168, 258, 92, "Sensor pod",
      ["accelerometer + microphone,", "wired to the base station itself"], role="sense")
c.box(34, 296, 258, 104, "Satellite nodes",
      ["same two sensors, one per", "extra machine, an ESP32-S3", "each, joined over Wi-Fi"], role="sense")

c.box(400, 176, 300, 216, "Base station",
      ["Arduino UNO Q", "", "asset registry", "per-machine anomaly model",
       "running/stopped gate", "fault classifier", "trip decision"],
      role="brain", title_size=17)

c.box(834, 150, 312, 66, "Live dashboard", role="tell")
c.box(834, 236, 312, 66, "Phone alert (Telegram)", role="tell")
c.box(834, 322, 312, 66, "Status ring + LED matrix", role="tell")
c.box(834, 432, 312, 96, "Motor power",
      ["stopped on a confirmed fault,", "stays refused until cleared by hand"],
      role="act", title_size=16)

c.link([(292, 214), (346, 214), (346, 226), (400, 226)], label="sensor frames")
c.link([(292, 348), (346, 348), (346, 340), (400, 340)], label="Wi-Fi / MQTT")

c.link([(700, 216), (768, 216), (768, 183), (834, 183)], label="status + scores")
c.link([(700, 269), (834, 269)], label="fault message")
c.link([(700, 322), (768, 322), (768, 355), (834, 355)], label="colour + mode")
c.link([(700, 372), (744, 372), (744, 480), (834, 480)],
       label="STOP  motor N", kind="arrowAct", width=2.4)

save(c, "01-system-at-a-glance")
