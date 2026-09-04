"""Animated status-ring legend (report/diagrams/18-status-light.gif).

Sibling of the SVG diagrams in this directory, but raster + animated on
purpose: the whole point of the ring is the *blink pattern*, which a still
frame cannot carry (a WARNING strobe and a PAUSED const are the same amber).

Every colour, mode and period here is read off the two firmware sources of
truth, not invented:

  * base-station/python/registry/status_color.py -- the NodeStatus -> LED
    command map pushed to a satellite over MQTT and to this board's own ring
    over the local Bridge. Same table drives both node types.
  * satellite/src/threads/transport_task.cpp -- the satellite-only
    provisioning/connectivity language (magenta = setup family, blue =
    connectivity-trouble family) that pre-empts the map above until MQTT is
    up.

Pattern maths is copied from the two ring drivers so the animation blinks at
the rate the hardware actually blinks (base-station/sketch/rgb_display.cpp
and satellite/src/drivers/rgb_ws2812.cpp are line-for-line identical here):

    BREATHE: scale = (1 - cos(2*pi*phase)) / 2
    STROBE:  scale = 1 if phase < 0.5 else 0

OFFLINE (#000000 const) is deliberately absent: status_color.py's own
comment records it as dead code -- a node that is genuinely offline has no
channel left to receive the command.

Labels are the dashboard's STATUS_LABEL strings (frontend/app.js), so the
ring and the browser name the same state the same way.
"""
import math
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

OUT_DIR = os.path.join(os.path.dirname(__file__), "..")
OUT_GIF = os.path.join(OUT_DIR, "18-status-light.gif")
OUT_PNG = os.path.join(OUT_DIR, "18-status-light.png")

# ---------------------------------------------------------------------------
# House style, borrowed from diagram_lib.py so this sits with the other 17.
INK = (0x16, 0x20, 0x2B)
INK_SOFT = (0x4A, 0x5A, 0x6A)
INK_FAINT = (0x6B, 0x78, 0x87)
PAPER = (0xFF, 0xFF, 0xFF)
BAND = (0xF4, 0xF7, 0xFA)
HAIRLINE = (0xD6, 0xDE, 0xE6)
# The ring sits in a dark bezel on top of the node. Drawing it that way is
# not decoration: on white paper an unlit LED and the IDLE white LED are the
# same pixel, and a strobe's off-half has nothing to read as "off".
BEZEL = (0x30, 0x3C, 0x49)
BEZEL_EDGE = (0x1E, 0x27, 0x31)
UNLIT_LED = (0x1B, 0x22, 0x2A)

FONT_DIR = "/usr/share/fonts/truetype/dejavu"
S = 2  # supersample factor; everything below is in 1x units


def font(size, bold=False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return ImageFont.truetype(os.path.join(FONT_DIR, name), int(round(size * S)))


# ---------------------------------------------------------------------------
# The states. (label, one-line meaning, "#rrggbb", mode, period_ms)
CONST, BREATHE, STROBE = "const", "breathe", "strobe"

SHARED = [
    ("New",      "Not set up yet",         "#00ffff", CONST,  0),
    ("Healthy",  "Running normally",       "#00ff00", CONST,  0),
    ("Warning",  "Drifting from healthy",  "#f59e0b", STROBE, 1000),
    ("Faulty",   "Fault detected",         "#ff0000", STROBE, 200),
    ("Tripped",  "Stopped by EPM",         "#ff0000", BREATHE, 1000),
    ("Idle",     "Machine not running",    "#ffffff", CONST,  0),
    ("Paused",   "Monitoring switched off", "#f59e0b", CONST, 0),
]

SATELLITE = [
    ("Needs setup",     "Waiting for WiFi details", "#ff00ff", CONST,   0),
    ("Testing WiFi",    "Trying the details given", "#ff00ff", BREATHE, 1000),
    ("No base station", "On WiFi, cannot reach it", "#0000ff", CONST,   0),
    ("Reconnecting",    "WiFi dropped, retrying",   "#0000ff", BREATHE, 1000),
]

PATTERN_WORDS = {
    (CONST, 0): "Steady",
    (STROBE, 1000): "Blinks once a second",
    (STROBE, 200): "Blinks fast",
    (BREATHE, 1000): "Fades in and out",
}

# ---------------------------------------------------------------------------
# Layout (1x units).
W = 1000
MARGIN = 40
TITLE_H = 96
LAMP_R = 33          # lit disc
DOME_R = 41          # diffuser housing around it
GLOW = 26            # halo reach beyond the disc
CELL_H = 196
LAMP_DY = 60         # lamp centre below the cell top

ROW_A1, ROW_A2 = SHARED[:4], SHARED[4:]
COLS = 4
COL_W = (W - 2 * MARGIN) // COLS

SEC_A_Y = TITLE_H + 30
ROW_A1_Y = SEC_A_Y + 30
ROW_A2_Y = ROW_A1_Y + CELL_H
DIV_Y = ROW_A2_Y + CELL_H + 4
SEC_B_Y = DIV_Y + 26
ROW_B_Y = SEC_B_Y + 30
H = ROW_B_Y + CELL_H + 30


def hex_rgb(s):
    s = s.lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def scale_at(mode, period_ms, t_ms):
    """Exactly the two ring drivers' rendering maths."""
    if mode == CONST or not period_ms:
        return 1.0
    phase = (t_ms % period_ms) / float(period_ms)
    if mode == BREATHE:
        return (1.0 - math.cos(phase * 2.0 * math.pi)) / 2.0
    return 1.0 if phase < 0.5 else 0.0


def cell_positions(items, row_y):
    """Centre a short row instead of left-ragging it against a 4-wide row."""
    n = len(items)
    left = MARGIN + (W - 2 * MARGIN - n * COL_W) // 2
    return [(left + i * COL_W + COL_W // 2, row_y) for i in range(n)]


# ---------------------------------------------------------------------------
# Static layer: everything that does not change frame to frame.
def build_background():
    img = Image.new("RGB", (W * S, H * S), PAPER)
    d = ImageDraw.Draw(img)

    d.rectangle([0, 0, W * S, TITLE_H * S], fill=BAND)
    d.line([MARGIN * S, TITLE_H * S, (W - MARGIN) * S, TITLE_H * S], fill=HAIRLINE, width=S)
    d.text((MARGIN * S, 30 * S), "What the light on a node means",
           font=font(27, bold=True), fill=INK, anchor="ls")
    d.text((MARGIN * S, 56 * S), "One ring on the base station, one on every satellite. Same colours as the dashboard.",
           font=font(15.5), fill=INK_SOFT, anchor="ls")

    d.text((MARGIN * S, SEC_A_Y * S), "Machine health  ·  every node",
           font=font(18, bold=True), fill=INK, anchor="ls")
    d.line([MARGIN * S, DIV_Y * S, (W - MARGIN) * S, DIV_Y * S], fill=HAIRLINE, width=S)
    d.text((MARGIN * S, SEC_B_Y * S), "Getting connected  ·  satellite only",
           font=font(18, bold=True), fill=INK, anchor="ls")

    rows = [(ROW_A1, ROW_A1_Y), (ROW_A2, ROW_A2_Y), (SATELLITE, ROW_B_Y)]
    lamps = []
    f_name, f_desc, f_pat = font(19, bold=True), font(15), font(13.5)
    for items, row_y in rows:
        for (cx, cy), (label, desc, rgb, mode, period) in zip(cell_positions(items, row_y), items):
            ly = cy + LAMP_DY
            d.ellipse([(cx - DOME_R) * S, (ly - DOME_R) * S, (cx + DOME_R) * S, (ly + DOME_R) * S],
                      fill=BEZEL, outline=BEZEL_EDGE, width=int(1.6 * S))
            d.text((cx * S, (cy + 128) * S), label, font=f_name, fill=INK, anchor="ms")
            d.text((cx * S, (cy + 151) * S), desc, font=f_desc, fill=INK_SOFT, anchor="ms")
            d.text((cx * S, (cy + 173) * S), PATTERN_WORDS[(mode, period)],
                   font=f_pat, fill=INK_FAINT, anchor="ms")
            lamps.append((cx, ly, hex_rgb(rgb), mode, period))
    return img, lamps


# ---------------------------------------------------------------------------
# Lamp compositing. One distance field, reused by every lamp.
PATCH = (DOME_R + GLOW) * S
_yy, _xx = np.mgrid[-PATCH:PATCH, -PATCH:PATCH].astype(np.float32)
_dist = np.hypot(_xx, _yy)
_glow_mask = np.clip(1.0 - (_dist - LAMP_R * S) / (GLOW * S), 0.0, 1.0) ** 2.2
_disc_mask = np.clip((LAMP_R * S - _dist) + 1.0, 0.0, 1.0)
# Soft specular smear near the top-left, so a lit dome reads as a dome.
_spec = np.clip(1.0 - np.hypot(_xx + LAMP_R * S * 0.34, _yy + LAMP_R * S * 0.40) / (LAMP_R * S * 0.62), 0, 1) ** 2
_spec = _spec * _disc_mask

UNLIT = np.array(UNLIT_LED, dtype=np.float32)


def draw_lamp(frame, cx, cy, rgb, s):
    x0, y0 = cx * S - PATCH, cy * S - PATCH
    box = (x0, y0, x0 + 2 * PATCH, y0 + 2 * PATCH)
    px = np.asarray(frame.crop(box), dtype=np.float32)
    colour = np.array(rgb, dtype=np.float32)

    a = (_glow_mask * (0.62 * s))[..., None]
    px = px * (1 - a) + colour * a

    lit = UNLIT + (colour - UNLIT) * (s ** 0.75)
    a = _disc_mask[..., None]
    px = px * (1 - a) + lit * a

    a = (_spec * (0.06 + 0.24 * s))[..., None]
    px = px * (1 - a) + 255.0 * a

    frame.paste(Image.fromarray(np.clip(px, 0, 255).astype(np.uint8)), box[:2])


# ---------------------------------------------------------------------------
FRAMES = 25          # 25 x 40 ms = one 1000 ms loop, an exact multiple of the
FRAME_MS = 40        # 200 ms FAULT strobe as well as the 1000 ms patterns.


def main():
    bg, lamps = build_background()

    frames = []
    for i in range(FRAMES):
        t = i * FRAME_MS
        f = bg.copy()
        for cx, cy, rgb, mode, period in lamps:
            draw_lamp(f, cx, cy, rgb, scale_at(mode, period, t))
        frames.append(f.resize((W, H), Image.LANCZOS))

    # A still for print/PDF, where a blink cannot survive: every ring lit.
    still = bg.copy()
    for cx, cy, rgb, _mode, _period in lamps:
        draw_lamp(still, cx, cy, rgb, 1.0)
    still.resize((W, H), Image.LANCZOS).save(OUT_PNG)

    # One shared palette across all frames, so GIF delta-frame optimisation
    # actually kicks in (a per-frame adaptive palette makes every pixel differ).
    strip = Image.new("RGB", (W, H * len(frames)))
    for i, f in enumerate(frames):
        strip.paste(f, (0, i * H))
    pal = strip.quantize(colors=255, method=Image.MEDIANCUT)
    quant = [f.quantize(palette=pal, dither=Image.NONE) for f in frames]

    quant[0].save(OUT_GIF, save_all=True, append_images=quant[1:],
                  duration=FRAME_MS, loop=0, optimize=True, disposal=1)
    print(f"{OUT_GIF}  {os.path.getsize(OUT_GIF) / 1024:.0f} KB  {W}x{H}  {len(frames)} frames")
    print(f"{OUT_PNG}  {os.path.getsize(OUT_PNG) / 1024:.0f} KB")


if __name__ == "__main__":
    main()
