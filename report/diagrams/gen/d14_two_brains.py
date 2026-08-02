"""14 -- how the two chips on one UNO Q divide the work (Chapter 2)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from diagram_lib import Canvas, save, INK_SOFT  # noqa: E402

c = Canvas(
    1340, 700,
    title="Two brains, one board, one power supply",
    subtitle="The split is not cosmetic. Each side does the job the other physically cannot.",
    footnotes=[
        ("Take the Linux half away and commissioning becomes a cloud round trip. Take the real-time half "
         "away and the spectra are no longer worth training on. This project needs both, on one board.", None),
        ("Both links run between the two chips inside the UNO Q. Neither leaves the board, and neither "
         "needs a wire a builder has to solder.", None),
    ],
)

# ---- MCU side ----------------------------------------------------------
c.box(34, 168, 570, 92, "STM32U585 · Cortex-M33 @ 160 MHz",
      ["Zephyr RTOS · 2 MB flash · 786 kB SRAM"], role="sense", title_size=16)
mcu_rows = [
    ("Sample without missing a window", "KX134 over SPI at 12.8 kHz, INMP441 over SAI, both continuous"),
    ("Reduce the firehose", "512-point FFT per channel, six statistics, average-pooled to 128 wire bins"),
    ("Drive the lights", "WS2812 ring on a DMA timer channel, and the board's own 8×13 LED matrix"),
]
y = 278
for title, blurb in mcu_rows:
    c.box(34, y, 570, 78, title, [blurb], role="sense", title_size=13, body_size=10.5)
    y += 90

# ---- MPU side ----------------------------------------------------------
c.box(736, 168, 570, 92, "QRB2210 · 4 × Cortex-A53 @ 2.0 GHz",
      ["Debian Linux · 2 GB LPDDR4X · Wi-Fi 5"], role="brain", title_size=16)
mpu_rows = [
    ("Train a neural network in the field", "PyTorch, on the board, while a technician waits — no cloud, no queue"),
    ("Score and decide", "anomaly model, running/stopped gate, TFLite classifier, the trip decision"),
    ("Be a server", "dashboard, WebSocket, MQTT broker, asset registry, history, Telegram"),
]
y = 278
for title, blurb in mpu_rows:
    c.box(736, y, 570, 78, title, [blurb], role="brain", title_size=13, body_size=10.5)
    y += 90

# ---- the two links between them ---------------------------------------
c.box(628, 300, 84, 236, "", role="ghost", title_size=1)
c.text(670, 330, "LPUART1", size=12, anchor="middle", weight="bold")
c.text(670, 348, "500 kbaud", size=10.5, anchor="middle", fill=INK_SOFT)
c.text(670, 366, "control RPC", size=10.5, anchor="middle", fill=INK_SOFT)
c.text(670, 452, "SPI", size=12, anchor="middle", weight="bold")
c.text(670, 470, "~40 MHz", size=10.5, anchor="middle", fill=INK_SOFT)
c.text(670, 488, "bulk telemetry", size=10.5, anchor="middle", fill=INK_SOFT)

c.link([(604, 320), (628, 320)], both=True, width=1.6)
c.link([(712, 320), (736, 320)], both=True, width=1.6)
c.link([(604, 442), (628, 442)], both=True, width=2.2)
c.link([(712, 442), (736, 442)], both=True, width=2.2)

c.text(670, 566, "Two links, on purpose:", size=11, anchor="middle", fill=INK_SOFT)
c.text(670, 583, "a big diagnostic capture", size=11, anchor="middle", fill=INK_SOFT)
c.text(670, 600, "cannot stall the status loop.", size=11, anchor="middle", fill=INK_SOFT)

save(c, "14-two-brains")
