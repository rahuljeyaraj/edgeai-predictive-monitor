"""09 -- what onboarding a new sensor node actually feels like (Chapter 3)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from diagram_lib import Canvas, save, INK_SOFT  # noqa: E402

c = Canvas(
    1320, 430,
    title="Onboarding a node — no laptop, no rebuild, no config file",
    subtitle="The same five steps for the first node and for the fortieth.",
    footnotes=[
        ("The base station onboards itself exactly the same way: with no network saved, it raises its own "
         "EPM-BaseStation hotspot and hijacks DNS so the phone's browser opens the Network tab by itself.", None),
        ("Nothing here is typed twice. The node's identity comes from its own Wi-Fi MAC, and the asset "
         "appears on the dashboard the moment its first frame lands.", None),
    ],
)

steps = [
    ("1", "Power it up", ["No saved network, so it", "raises its own hotspot:", "EPM-SAT-a4cf12"], "sense"),
    ("2", "Join from a phone", ["Any phone. The setup page", "opens on its own — the", "captive-portal trick"], "sense"),
    ("3", "Fill three fields", ["Shop Wi-Fi name, password,", "broker address (already", "pre-filled)"], "brain"),
    ("4", "It tests, then switches", ["Credentials are tried before", "they're saved, so a typo", "can't strand the node"], "brain"),
    ("5", "It appears", ["A new asset shows up on", "the Fleet page, waiting", "to be commissioned"], "tell"),
]

x = 34
w = 228
gap = 26
for badge, title, rows, role in steps:
    c.box(x, 146, w, 148, title, rows, role=role, title_size=14, body_size=10.5, badge=badge)
    if x + w + gap < 1286:
        c.link([(x + w, 220), (x + w + gap, 220)], width=1.5)
    x += w + gap

c.text(34, 326, "Elapsed: about a minute. Nothing was rebuilt, reflashed or edited.",
       size=12, style="italic", fill=INK_SOFT)

save(c, "09-onboarding")
