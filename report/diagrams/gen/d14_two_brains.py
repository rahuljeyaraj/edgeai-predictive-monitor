"""14 -- how the two chips on one UNO Q divide the work (Chapter 2)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from diagram_lib import (  # noqa: E402
    Canvas, save, text_width, ROLES, INK, INK_SOFT, PAPER,
)

c = Canvas(1340, 536)

# ---- the board itself, drawn around both halves ------------------------
BX, BY, BW, BH = 34, 56, 1272, 444
c.raw(f'<rect x="{BX}" y="{BY}" width="{BW}" height="{BH}" rx="18" '
      f'fill="#FBFCFD" stroke="#5B6B7A" stroke-width="1.8"/>')
label = "Arduino UNO Q"
lw = text_width(label, 15, "bold") + 48
c.raw(f'<rect x="{BX + 30:.1f}" y="{BY - 18:.1f}" width="{lw:.1f}" height="36" rx="9" '
      f'fill="{PAPER}" stroke="#5B6B7A" stroke-width="1.5"/>')
c.text(BX + 30 + lw / 2, BY + 7, label, size=15, weight="bold", anchor="middle", fill=INK)

COL_W, COL_H = 520, 322
COL_Y = BY + 30
LX, RX = 56, 764
PAD, ROW_H, ROW_GAP = 16, 66, 14
ROW_TOP = COL_Y + 78


def half(x, chip, spec, rows, role):
    """One processor: a big box carrying its own three jobs."""
    fill, stroke, tcol = ROLES[role]
    c.raw(f'<rect x="{x}" y="{COL_Y}" width="{COL_W}" height="{COL_H}" rx="14" '
          f'fill="{fill}" stroke="{stroke}" stroke-width="2" filter="url(#softshadow)"/>')
    c.text(x + COL_W / 2, COL_Y + 34, chip, size=15, weight="bold",
           anchor="middle", fill=tcol)
    c.text(x + COL_W / 2, COL_Y + 57, spec, size=11, anchor="middle", fill=INK_SOFT)
    for i, (head, blurb) in enumerate(rows):
        y = ROW_TOP + i * (ROW_H + ROW_GAP)
        c.raw(f'<rect x="{x + PAD}" y="{y}" width="{COL_W - 2 * PAD}" height="{ROW_H}" '
              f'rx="8" fill="{PAPER}" stroke="{stroke}" stroke-width="1.3"/>')
        c.text(x + COL_W / 2, y + 27, head, size=12.5, weight="bold",
               anchor="middle", fill=tcol)
        c.text(x + COL_W / 2, y + 48, blurb, size=10.5, anchor="middle", fill=INK_SOFT)


half(LX, "STM32U585 · Cortex-M33 @ 160 MHz", "Zephyr RTOS · 2 MB flash · 786 kB SRAM", [
    ("Samples both sensors", "KX134 at 12.8 kHz and INMP441, never missing a window"),
    ("Reduces the firehose", "512-point FFT and six statistics, per channel"),
    ("Drives the lights", "the status ring, and the board's own 8×13 LED matrix"),
], "sense")

half(RX, "QRB2210 · 4 × Cortex-A53 @ 2.0 GHz", "Debian Linux · 4 GB LPDDR4X · Wi-Fi 5", [
    ("Trains a model per machine", "PyTorch on the board, in seconds, with no cloud"),
    ("Scores and decides", "anomaly model, running gate, classifier, the trip"),
    ("Is the server", "dashboard, MQTT broker, asset registry, Telegram"),
], "brain")

# ---- the two links between them ---------------------------------------
GAP_L, GAP_R = LX + COL_W, RX
MID = (GAP_L + GAP_R) / 2
for i, (name, sub, width) in enumerate([
    ("LPUART1 · 500 kbaud", "commands and status", 1.7),
    (None, None, 0),
    ("SPI · about 40 MHz", "the bulk spectra", 2.6),
]):
    y = ROW_TOP + i * (ROW_H + ROW_GAP) + ROW_H / 2
    if name is None:
        continue
    c.link([(GAP_L + 6, y), (GAP_R - 6, y)], label=name, both=True, width=width,
           label_size=11)
    c.text(MID, y + 22, sub, size=10.5, anchor="middle", fill=INK_SOFT)

# ---- what else came on the board --------------------------------------
chips = ["One power supply", "One USB-C cable", "On-board Wi-Fi", "8×13 LED matrix"]
widths = [text_width(t, 14, "bold") + 48 for t in chips]
gap = 30
x = (BX + BW / 2) - (sum(widths) + gap * (len(chips) - 1)) / 2
for label_, w in zip(chips, widths):
    c.chip(x, COL_Y + COL_H + 20, label_, role="ghost", size=14, pad=24, h=44)
    x += w + gap

save(c, "14-two-brains")
