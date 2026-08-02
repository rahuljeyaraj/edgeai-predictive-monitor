"""08 -- annotated map of the dashboard (Chapter 6). A wireframe, not a
screenshot: it labels what each region is for. Real screenshots sit
alongside it in the chapter."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from diagram_lib import Canvas, save, INK, INK_SOFT, HAIRLINE, BAND, ROLES  # noqa: E402

c = Canvas(
    1240, 880,
    title="Anatomy of the Fleet page",
    subtitle="One page, five tabs, and a row that opens into everything known about one machine.",
    footnotes=[
        ("Nothing on this page polls for a chart. One WebSocket carries spectra, scores, classifier "
         "results, training progress and status changes to every open browser.", None),
    ],
)

WX, WY, WW, WH = 34, 150, 796, 656
c.raw(f'<rect x="{WX}" y="{WY}" width="{WW}" height="{WH}" rx="10" fill="#FFFFFF" '
      f'stroke="{HAIRLINE}" stroke-width="1.6" filter="url(#softshadow)"/>')

# ---- top bar -------------------------------------------------------------
c.raw(f'<path d="M{WX},{WY+54} L{WX},{WY+10} Q{WX},{WY} {WX+10},{WY} '
      f'L{WX+WW-10},{WY} Q{WX+WW},{WY} {WX+WW},{WY+10} L{WX+WW},{WY+54} Z" fill="{BAND}"/>')
c.rule(WX, WY + 54, WX + WW)
c.text(WX + 18, WY + 26, "EdgeAI", size=15, weight="bold", fill=INK)
c.text(WX + 18, WY + 42, "PREDICTIVE MONITOR", size=8, fill=INK_SOFT)

tabs = ["Fleet", "Classifier", "Network", "Performance", "Alerts"]
tx = WX + 210
for i, t in enumerate(tabs):
    active = i == 0
    w = 22 + len(t) * 6.6
    if active:
        c.raw(f'<rect x="{tx-8:.1f}" y="{WY+13}" width="{w:.1f}" height="28" rx="6" '
              f'fill="#DCEAFB" stroke="#1B5FA8" stroke-width="1.1"/>')
    c.text(tx + w / 2 - 8, WY + 32, t, size=11.5, anchor="middle",
           weight="bold" if active else "normal", fill="#154A85" if active else INK_SOFT)
    tx += w + 10

# ---- summary tiles -------------------------------------------------------
c.text(WX + 18, WY + 82, "STATUS SUMMARY  ·  click a tile to filter the list below",
       size=9.5, weight="bold", fill=INK_SOFT)
tiles = [("6", "Assets", "neutral"), ("1", "Tripped", "act"), ("1", "Faulty", "act"),
         ("1", "Warning", "warn"), ("2", "Healthy", "tell"), ("1", "Idle", "sense")]
tx = WX + 18
for count, label, role in tiles:
    fill, stroke, tcol = ROLES[role]
    c.raw(f'<rect x="{tx}" y="{WY+92}" width="118" height="62" rx="8" fill="{fill}" '
          f'stroke="{stroke}" stroke-width="1.3"/>')
    c.text(tx + 59, WY + 124, count, size=21, weight="bold", anchor="middle", fill=tcol)
    c.text(tx + 59, WY + 142, label, size=10, anchor="middle", fill=INK_SOFT)
    tx += 128

# ---- asset rows ----------------------------------------------------------
def row(y, name, node_id, cls, status, role, expanded=False):
    fill, stroke, tcol = ROLES[role]
    c.raw(f'<rect x="{WX+18}" y="{y}" width="{WW-36}" height="52" rx="8" fill="#FCFDFE" '
          f'stroke="{HAIRLINE}" stroke-width="1.2"/>')
    c.raw(f'<rect x="{WX+18}" y="{y}" width="5" height="52" rx="2.5" fill="{stroke}"/>')
    c.text(WX + 38, y + 22, name, size=12.5, weight="bold", fill=INK)
    c.text(WX + 38, y + 38, node_id, size=9.5, fill=INK_SOFT)
    c.raw(f'<rect x="{WX+250}" y="{y+15}" width="120" height="22" rx="11" fill="#F1EDFB" '
          f'stroke="#7C5FC4" stroke-width="1.1"/>')
    c.text(WX + 310, y + 30, cls, size=10, anchor="middle", fill="#4E3A8C")
    c.text(WX + 430, y + 30, status, size=11.5, weight="bold", fill=tcol)
    for i, lbl in enumerate(["Recommission", "rec", "II", "bin"]):
        bw = 78 if i == 0 else 26
        bx = WX + WW - 54 - sum(78 if j == 0 else 26 for j in range(i)) - i * 8 - bw
        c.raw(f'<rect x="{bx}" y="{y+14}" width="{bw}" height="24" rx="6" fill="#F4F7FA" '
              f'stroke="{HAIRLINE}" stroke-width="1"/>')
        if i == 0:
            c.text(bx + bw / 2, y + 30, lbl, size=9.5, anchor="middle", fill=INK_SOFT)


c.text(WX + 18, WY + 182, "ASSETS", size=9.5, weight="bold", fill=INK_SOFT)
row(WY + 194, "Lathe 1", "d0bcd7", "cnc lathe", "Healthy", "tell")
row(WY + 256, "Lathe 2", "a4cf12", "cnc lathe", "Faulty", "act")

# ---- expanded detail panel ----------------------------------------------
PX, PY, PW, PH = WX + 18, WY + 316, WW - 36, 316
c.raw(f'<rect x="{PX}" y="{PY}" width="{PW}" height="{PH}" rx="8" fill="#F8FAFC" '
      f'stroke="#1B5FA8" stroke-width="1.4"/>')

def block(x, y, w, h, label, sub=None, role="neutral", chart=False):
    fill, stroke, tcol = ROLES[role]
    c.raw(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="#FFFFFF" '
          f'stroke="{HAIRLINE}" stroke-width="1.1"/>')
    c.text(x + 10, y + 17, label, size=10.5, weight="bold", fill=tcol)
    if sub:
        c.text(x + 10, y + 31, sub, size=9, fill=INK_SOFT)
    if chart:
        pts = [(x + 12 + i * (w - 24) / 24,
                y + h - 12 - (h - 44) * v)
               for i, v in enumerate([.12, .15, .11, .18, .14, .2, .17, .22, .19, .26,
                                      .3, .27, .34, .41, .38, .5, .47, .58, .66, .62,
                                      .74, .81, .78, .9, .95])]
        d = "M" + " L".join(f"{px:.1f},{py:.1f}" for px, py in pts)
        c.raw(f'<path d="{d}" fill="none" stroke="#1B5FA8" stroke-width="1.6"/>')
        c.raw(f'<line x1="{x+12}" y1="{y+h-12-(h-44)*0.85:.1f}" x2="{x+w-12}" '
              f'y2="{y+h-12-(h-44)*0.85:.1f}" stroke="#B03225" stroke-width="1.1" '
              f'stroke-dasharray="5,3"/>')


block(PX + 12, PY + 12, PW - 24, 56, "Protection",
      "trip output · status · stopped baseline", role="act")
block(PX + 12, PY + 80, PW - 24, 108, "Anomaly score",
      "live, with this machine's own threshold lines", role="brain", chart=True)
block(PX + 12, PY + 200, (PW - 36) / 2, 48, "Fault classification",
      "confidence per fault type", role="brain")
block(PX + 24 + (PW - 36) / 2, PY + 200, (PW - 36) / 2, 48, "Accel + mic spectrum",
      "per axis, live", role="sense")
for i, lbl in enumerate(["Scalar values", "Raw signals", "Waterfall"]):
    bw = (PW - 48) / 3
    bx = PX + 12 + i * (bw + 12)
    c.raw(f'<rect x="{bx:.1f}" y="{PY+260}" width="{bw:.1f}" height="36" rx="6" '
          f'fill="#FFFFFF" stroke="{HAIRLINE}" stroke-width="1.1"/>')
    c.text(bx + 12, PY + 282, "▸  " + lbl, size=10, fill=INK_SOFT)

# ---- callouts ------------------------------------------------------------
NX, NW = 872, 334


def note(target_y, h, title, rows, role="neutral"):
    """A callout is *centred* on the thing it points at, so its leader is a
    straight line. Placing the note first and bending the leader to reach it
    is what left the earlier version looking like a staircase."""
    y = target_y - h / 2
    c.box(NX, y, NW, h, title, rows, role=role, title_size=12.5, body_size=10.5, rx=7)
    c.link([(WX + WW, target_y), (NX, target_y)], kind="arrowSoft", width=1.3)


note(WY + 15, 74, "Five tabs, one page",
     ["Fleet · Classifier · Network ·", "Performance · Alerts"])
note(WY + 125, 96, "Counts that are also filters",
     ["Every tile is a toggle. Empty", "buckets hide themselves, so the", "row never fills with zeroes."])
note(WY + 240, 96, "One row per machine",
     ["Nickname, node ID, asset class,", "status — and the classifier's", "read when there is a fault."])
note(WY + 420, 152, "Open a row for everything else",
     ["Protection controls first (the only", "time-critical thing on screen),",
      "then the anomaly trend, the fault", "classification, live spectra, and",
      "three collapsed deep-dive panels."])
note(WY + 580, 96, "Charts survive the list",
     ["Plotly elements are re-parented,", "never rebuilt — a zoom you set", "outlives every live update."])

save(c, "08-dashboard-anatomy")
