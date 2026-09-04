"""08 -- the Fleet page (Chapter 9).

Not a block diagram: a faithful mock of the real dashboard, drawn at 1:1 in
CSS pixels straight from base-station/python/frontend/style.css. No title
band, no footnote band, and no callout column -- the anatomy is described in
the chapter's prose, and a mock that argues with the screen it is mocking is
worse than no mock at all.

Every colour, radius, padding and font size below is copied from a named
rule in style.css / charts.js / app.js, cited in the comments. If the UI
changes, this file is the thing to re-read it against.

Deliberately NOT drawn with diagram_lib's box()/chip() helpers: those carry
the report's own light semantic palette (ROLES) and a 1.3x font scale, and
both are exactly what this diagram must not use.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from diagram_lib import Canvas, save, esc, text_width, MONO  # noqa: E402

W, H = 1000, 1042
c = Canvas(W, H)

UI = "system-ui, -apple-system, Segoe UI, DejaVu Sans, Helvetica, Arial, sans-serif"

# --- style.css palette, verbatim ---------------------------------------
BG        = "#0f172a"   # body
CARD      = "#1e293b"   # .topbar, .tile, .motor-row, .perf-tier
LINE      = "#334155"   # borders
LINE2     = "#475569"   # .classification-bar__fill
PROT_BG   = "#131c2b"   # .protection
TXT       = "#f1f5f9"   # .motor-row__name
TXT2      = "#e2e8f0"   # .protection__state
MUTED     = "#94a3b8"   # .tile__label, .chart-section__title
DIM       = "#64748b"   # .motor-row__node-id
HEAD      = "#cbd5e1"   # .fleet__title
CYAN      = "#22d3ee"   # logo "AI"
LOGO_FG   = "#f8fafc"   # logo "Edge"
VIOLET    = "#818cf8"   # .motor-row__classification-chip
PINK      = "#f472b6"   # assetClassColor("cnc lathe") -> ASSET_CLASS_PALETTE[?]

# :root status colours -- kept identical to registry/status_color.py's LEDs.
HEALTHY, WARNING, FAULT = "#00ff00", "#f59e0b", "#ff0000"
TRIPPED, IDLE, ALL = "#b91c1c", "#ffffff", "#e2e8f0"

# charts.js: PAPER_BG/PLOT_BG #0f172a, GRID_COLOR #1e293b, AXIS_COLOR
# #334155, ANOMALY_LINE_COLOR #94a3b8.
GRID, AXIS, TRACE = "#1e293b", "#334155", "#94a3b8"


# --- raw primitives -----------------------------------------------------
# diagram_lib.text() multiplies every size by FONT_SCALE (1.3) for the
# report's own diagrams; these write true CSS pixels instead.

def rect(x, y, w, h, fill, stroke=None, sw=1, rx=0, extra=""):
    st = f' stroke="{stroke}" stroke-width="{sw}"' if stroke else ""
    c.raw(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
          f'rx="{rx}" fill="{fill}"{st}{extra}/>')


def txt(x, y, s, size, fill, weight="normal", anchor="start", family=UI, ls=None):
    sp = f' letter-spacing="{ls}"' if ls else ""
    c.raw(f'<text x="{x:.1f}" y="{y:.1f}" font-family="{family}" '
          f'font-size="{size:.1f}" font-weight="{weight}" text-anchor="{anchor}" '
          f'fill="{fill}"{sp}>{esc(s)}</text>')


def tw(s, size, weight="normal", family=UI):
    """text_width() at true pixels -- it bakes in the same 1.3x scale."""
    fam = MONO if family == MONO else None
    return text_width(s, size, weight, family=fam or "x") / 1.3


def mix(hex_a, hex_b, pct):
    """CSS color-mix(in srgb, a pct%, b) -- the tinted fills the UI uses."""
    a = [int(hex_a[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(hex_b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x * pct + y * (1 - pct)):02x}" for x, y in zip(a, b))


rect(0, 0, W, H, BG)

# --- .topbar: 72px, #1e293b, 1px #334155 bottom, padding 0 24px ---------
rect(0, 0, W, 72, CARD)
c.raw(f'<line x1="0" y1="71.5" x2="{W}" y2="71.5" stroke="{LINE}" stroke-width="1"/>')

# .topbar__logo -- the inline SVG in index.html, viewBox 0 0 220 66 drawn
# at height:60, so everything in it scales by 60/66 and sits 6px down.
S = 60 / 66
c.raw(f'<text x="24" y="{6 + 34 * S:.1f}" font-family="Arial, Helvetica, sans-serif" '
      f'font-size="{30 * S:.1f}" font-weight="800" letter-spacing="0.27">'
      f'<tspan fill="{LOGO_FG}">Edge</tspan><tspan fill="{CYAN}">AI</tspan></text>')
txt(24 + S, 6 + 52 * S, "PREDICTIVE MONITOR", 11 * S, MUTED, "500", ls="1.36")

# .topbar__nav -- 17px/600, #94a3b8, gap 28. The active tab is #f1f5f9 with
# a 2px --color-healthy underline on its 70px-tall button.
TABS = ["Fleet", "Classifier", "Network", "Performance", "Alerts"]
tx = 24 + 220 * S + 16
for i, tab in enumerate(TABS):
    w = tw(tab, 17, "600")
    txt(tx, 42, tab, 17, TXT if i == 0 else MUTED, "600")
    if i == 0:
        rect(tx, 70, w, 2, HEALTHY)
    tx += w + 28

# --- .summary: padding 24, .tile 140x87, 3px accent border, radius 10 ---
# SUMMARY_TILES order from app.js; zero-count buckets are never rendered.
TILES = [("6", "Assets", ALL), ("1", "Tripped", TRIPPED), ("1", "Faulty", FAULT),
         ("1", "Warning", WARNING), ("2", "Healthy", HEALTHY), ("1", "Idle", IDLE)]
for i, (n, label, accent) in enumerate(TILES):
    x = 24 + i * 154
    # Every bucket starts selected (app.js: selectedBuckets = REAL_BUCKETS),
    # so the default view shows .tile.is-selected's tinted fill, not bare
    # #1e293b.
    rect(x, 96, 140, 87, mix(accent, CARD, 0.18), accent, 3, 10)
    txt(x + 16, 141, n, 38, accent, "700", family=MONO)   # .tile__count
    txt(x + 16, 163, label, 14, MUTED)                    # .tile__label

# --- .fleet__title: 16px/700, uppercase, #cbd5e1 ------------------------
txt(24, 223, "ASSETS", 16, HEAD, "700", ls="0.64")


def grip(x, cy):
    """.motor-row__grip -- app.js ICON_GRIP, #64748b at rest."""
    for r in range(3):
        for col in range(2):
            c.raw(f'<circle cx="{x + col * 5}" cy="{cy - 5 + r * 5}" r="1.6" fill="{DIM}"/>')


def icon_btn(x, y, glyph, danger=False):
    """.btn-icon -- 30x30, #334155, radius 6, #e2e8f0 glyph."""
    rect(x, y, 30, 30, LINE, rx=6)
    cx, cy = x + 15, y + 15
    if glyph == "record":
        c.raw(f'<circle cx="{cx}" cy="{cy}" r="5.5" fill="{TXT2}"/>')
    elif glyph == "pause":
        rect(cx - 4.5, cy - 5.5, 3, 11, TXT2, rx=1)
        rect(cx + 1.5, cy - 5.5, 3, 11, TXT2, rx=1)
    else:  # trash
        rect(cx - 5, cy - 3.5, 10, 9, TXT2, rx=1.5)
        rect(cx - 6.5, cy - 6, 13, 2, TXT2, rx=1)


def row(y, name, node_id, status, accent, expanded=False, classification=None):
    """.motor-row -- 2px accent border, radius 10, padding 14px 18px."""
    rx = "10" if not expanded else "10"
    rect(24, y, 952, 66, CARD, accent, 2, rx)
    if expanded:
        # .motor-row--expanded squares the bottom corners off against the
        # detail panel; overdraw the two lower rounds.
        rect(24, y + 46, 952, 20, CARD)
        c.raw(f'<line x1="25" y1="{y + 46}" x2="25" y2="{y + 66}" stroke="{accent}" stroke-width="2"/>')
        c.raw(f'<line x1="975" y1="{y + 46}" x2="975" y2="{y + 66}" stroke="{accent}" stroke-width="2"/>')
    cy = y + 33
    grip(34, cy)
    txt(58, cy - 2, name, 17, TXT, "600")                    # .motor-row__name
    txt(58, cy + 15, node_id, 11, DIM, family=MONO)          # .motor-row__node-id

    # .device-type-pill, set state: 20% accent fill, 45% accent border.
    pw = tw("cnc lathe", 13, "600") + 20
    rect(236, cy - 12, pw, 24, mix(PINK, CARD, 0.20), mix(PINK, CARD, 0.45), 1, 12)
    txt(246, cy + 5, "cnc lathe", 13, PINK, "600")
    c.raw(f'<circle cx="{236 + pw + 13.5}" cy="{cy}" r="7.5" fill="{LINE}"/>')
    txt(236 + pw + 13.5, cy + 3.5, "?", 10, MUTED, "700", anchor="middle")

    # .motor-row__status -- mono/700, uppercase, --accent.
    txt(419, cy + 5, status.upper(), 14, accent, "700", family=MONO, ls="0.42")
    if classification:
        cw = tw(classification, 12, "600") + 18
        rect(419 + tw(status.upper(), 14, "700", MONO) + 12, cy - 10, cw, 20,
             mix(VIOLET, CARD, 0.18), mix(VIOLET, CARD, 0.40), 1, 10)
        txt(419 + tw(status.upper(), 14, "700", MONO) + 21, cy + 4, classification, 12, VIOLET, "600")

    # .motor-row__actions -- record, pause, remove. A commissioned asset has
    # no setup button at all (app.js: "Re-run setup lives in the drawer").
    for i, g in enumerate(("trash", "pause", "record")):
        icon_btn(928 - i * 38, cy - 15, g)


row(238, "Lathe 1", "d0bcd7", "Healthy", HEALTHY)
row(316, "Lathe 2", "a4cf12", "Faulty", FAULT, expanded=True, classification="Bearing")

# --- .motor-row__detail: #0f172a, 2px accent border, no top, padding 12/18
DY = 382
rect(24, DY, 952, 636, BG, FAULT, 2, 10)
rect(24, DY, 952, 12, BG)   # square the top off against the row above
c.raw(f'<line x1="25" y1="{DY}" x2="25" y2="{DY + 12}" stroke="{FAULT}" stroke-width="2"/>')
c.raw(f'<line x1="975" y1="{DY}" x2="975" y2="{DY + 12}" stroke="{FAULT}" stroke-width="2"/>')

# .protection -- #131c2b on #1e293b border, full-width band above the charts.
rect(42, 394, 916, 124, PROT_BG, CARD, 1, 10)
txt(58, 425, "PROTECTION", 13, MUTED, "600", ls="0.52")
for i, (label, state, col) in enumerate([
        ("Trip output", "MQTT  ·  motor/lathe2/stop", TXT2),
        ("Status", "Armed", TXT2),
        ("Stopped baseline", "Captured", TXT2)]):
    ly = 452 + i * 24
    txt(58, ly, label, 14, MUTED)          # .protection__label, 132px column
    txt(190, ly, state, 14, col)           # .protection__state

# .chart-section__title -- 13px/600, uppercase, #94a3b8.
txt(42, 549, "ANOMALY SCORE", 13, MUTED, "600", ls="0.52")

# The anomaly plot: charts.js's dark Plotly theme, per-point STATUS_COLOR
# markers, and this machine's own warning/fault threshold lines.
PX, PY, PW, PH = 42, 557, 916, 160
rect(PX, PY, PW, PH, BG)
for i in range(1, 5):
    gy = PY + i * PH / 5
    c.raw(f'<line x1="{PX}" y1="{gy:.1f}" x2="{PX + PW}" y2="{gy:.1f}" stroke="{GRID}" stroke-width="1"/>')
c.raw(f'<line x1="{PX}" y1="{PY + PH}" x2="{PX + PW}" y2="{PY + PH}" stroke="{AXIS}" stroke-width="1"/>')
c.raw(f'<line x1="{PX}" y1="{PY}" x2="{PX}" y2="{PY + PH}" stroke="{AXIS}" stroke-width="1"/>')

WARN_Y, FAULT_Y = PY + PH * 0.46, PY + PH * 0.22
c.raw(f'<line x1="{PX}" y1="{WARN_Y:.1f}" x2="{PX + PW}" y2="{WARN_Y:.1f}" '
      f'stroke="{WARNING}" stroke-width="1.2" stroke-dasharray="6,4"/>')
c.raw(f'<line x1="{PX}" y1="{FAULT_Y:.1f}" x2="{PX + PW}" y2="{FAULT_Y:.1f}" '
      f'stroke="{FAULT}" stroke-width="1.2" stroke-dasharray="6,4"/>')

# A run that drifts up out of healthy, through warning, into fault.
SERIES = [0.10, 0.13, 0.11, 0.16, 0.14, 0.19, 0.17, 0.22, 0.26, 0.24, 0.30,
          0.34, 0.31, 0.39, 0.45, 0.52, 0.49, 0.58, 0.66, 0.63, 0.71, 0.78,
          0.75, 0.83, 0.88]
pts = [(PX + 14 + i * (PW - 28) / (len(SERIES) - 1), PY + PH - 12 - v * (PH - 30))
       for i, v in enumerate(SERIES)]
c.raw('<path d="M' + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts) +
      f'" fill="none" stroke="{TRACE}" stroke-width="1.6"/>')
for (x, y), v in zip(pts, SERIES):
    col = FAULT if y < FAULT_Y else (WARNING if y < WARN_Y else HEALTHY)
    c.raw(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.6" fill="{col}"/>')

# --- two half-width chart sections -------------------------------------
txt(42, 745, "FAULT CLASSIFICATION", 13, MUTED, "600", ls="0.52")
# .classification-bar: 100px label / track / 44px value. The top class is
# #818cf8, every other one #475569 -- deliberately not the status palette.
BARS = [("Bearing", 0.81, True), ("Unbalanced", 0.11, False),
        ("Loose mount", 0.05, False), ("Healthy", 0.03, False)]
for i, (label, v, top) in enumerate(BARS):
    by = 768 + i * 26
    txt(42, by + 4, label, 13, TXT2 if top else MUTED, "600" if top else "normal")
    rect(150, by - 4, 260, 8, CARD, LINE, 1, 4)
    rect(150, by - 4, 260 * v, 8, VIOLET if top else LINE2, rx=4)
    txt(454, by + 4, f"{v * 100:.0f}%", 13, TXT2 if top else MUTED,
        "600" if top else "normal", anchor="end", family=MONO)

txt(514, 745, "ACCEL SPECTRUM", 13, MUTED, "600", ls="0.52")
SX, SY, SW, SH = 514, 757, 444, 116
rect(SX, SY, SW, SH, BG)
for i in range(1, 4):
    gy = SY + i * SH / 4
    c.raw(f'<line x1="{SX}" y1="{gy:.1f}" x2="{SX + SW}" y2="{gy:.1f}" stroke="{GRID}" stroke-width="1"/>')
c.raw(f'<line x1="{SX}" y1="{SY + SH}" x2="{SX + SW}" y2="{SY + SH}" stroke="{AXIS}" stroke-width="1"/>')
# charts.js CHANNEL_COLOR: accel_x #3987e5, accel_y #9085e9, accel_z #d55181.
import math  # noqa: E402
for colour, phase, amp in (("#3987e5", 0.0, 1.00), ("#9085e9", 1.7, 0.72),
                           ("#d55181", 3.1, 0.55)):
    d = []
    for i in range(90):
        x = SX + 6 + i * (SW - 12) / 89
        t = i / 89
        v = (math.exp(-((t - 0.10) ** 2) / 0.0022) * 0.85
             + math.exp(-((t - 0.34) ** 2) / 0.0016) * 0.55
             + math.exp(-((t - 0.62) ** 2) / 0.0012) * 0.35
             + 0.06 + 0.05 * math.sin(t * 47 + phase))
        d.append(f"{x:.1f},{SY + SH - 6 - min(v, 1.0) * amp * (SH - 16):.1f}")
    c.raw(f'<path d="M{" L".join(d)}" fill="none" stroke="{colour}" stroke-width="1.3"/>')

# --- .perf-tier collapsibles: #1e293b, 1px #334155, radius 10 -----------
# Only two of these exist -- "Raw signals" was removed from charts.js.
for i, label in enumerate(("Scalar values", "Waterfall")):
    py = 897 + i * 66
    rect(42, py, 916, 50, CARD, LINE, 1, 10)
    txt(62, py + 31, "▸", 14, DIM)
    txt(84, py + 31, label, 17, TXT, "700", family=MONO)

save(c, "08-dashboard-anatomy")
