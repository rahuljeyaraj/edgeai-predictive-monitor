"""05 -- full system architecture (Chapter 7)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from diagram_lib import Canvas, save, ROLE_LEGEND  # noqa: E402

c = Canvas(
    1320, 740,
    title="Full system architecture",
    subtitle="Three kinds of board. Only one of them decides anything.",
    legend=[(k, ROLE_LEGEND[k]) for k in ("sense", "brain", "tell", "act")],
    footnotes=[
        ("Every sensing path — internal SPI or Wi-Fi — delivers the identical frame, so the scoring "
         "pipeline never learns which kind of node a machine is behind.", None),
        ("The motor-driver rig is an actuator, not a peer: it accepts stop, and nothing else.", "#B03225"),
    ],
)

# Ordered to match the chips they feed (STM32 on top, QRB2210 below), so the
# two ingest paths never have to cross each other or share a label lane.
c.box(34, 180, 250, 106, "Base station's own",
      ["accel + mic + ring", "+ 8×13 LED matrix"], role="sense")
c.box(34, 318, 250, 106, "Satellite node × N",
      ["XIAO ESP32-S3", "accel + mic + ring"], role="sense")

c.group(400, 150, 372, 476, "Arduino UNO Q", role="brain")
c.box(428, 190, 316, 118, "STM32U585 · Zephyr",
      ["sample → FFT → pool → frame"], role="sense", title_size=15)
c.box(428, 366, 316, 244, "QRB2210 · Debian Linux",
      ["", "ingestion + frame routing", "running/stopped gate",
       "per-machine autoencoder", "fault classifier (TFLite)",
       "asset registry + history", "protection / trip logic", "dashboard web server"],
      role="brain", title_size=15)

c.link([(284, 233), (356, 233), (356, 250), (428, 250)], label="SPI · I²S")
c.link([(284, 371), (356, 371), (356, 404), (428, 404)], label="Wi-Fi / MQTT")
c.link([(536, 308), (536, 366)], label="LPUART1", both=True)
c.link([(654, 308), (654, 366)], label="SPI", both=True)

c.box(880, 168, 406, 76, "Live dashboard", ["browser on the shop LAN"], role="tell")
c.box(880, 262, 406, 76, "Telegram", ["one message per confirmed fault"], role="tell")
c.box(880, 356, 406, 76, "Status ring + LED matrix", role="tell")
c.box(880, 470, 406, 130, "Motor-driver rig",
      ["Arduino Uno + CNC Shield V3", "3 × A4988 · 3 × NEMA-17",
       "per-motor stop, latched until cleared"], role="act", title_size=16)

c.link([(744, 400), (812, 400), (812, 206), (880, 206)], label="WebSocket",
       label_seg=0, label_dx=8)
c.link([(744, 430), (830, 430), (830, 300), (880, 300)], label="Bot API",
       label_seg=0, label_dx=8)
c.link([(744, 462), (846, 462), (846, 394), (880, 394)], label="STATUS_LED",
       label_seg=0, label_dx=8)
c.link([(744, 522), (812, 522), (812, 535), (880, 535)],
       label="STOP motor N", kind="arrowAct", width=2.4, label_seg=0,
       label_dx=42, label_dy=-13)

save(c, "05-full-architecture")
