"""13 -- the five dashboard tabs and what each one owns (Chapter 8)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from diagram_lib import Canvas, save, INK_SOFT  # noqa: E402

c = Canvas(
    1340, 620,
    title="Five tabs, five questions",
    subtitle="Each tab answers one question completely, and no fact is editable in two of them.",
    footnotes=[
        ("The trip banner sits above the tabs, not inside one, so a countdown and its Hold button are on "
         "screen no matter which tab is open. Ten seconds is not enough time to go looking.", "#B03225"),
        ("Every tab is one self-contained JavaScript module with its own data, sharing exactly one "
         "WebSocket. No framework, no build step, no bundler.", None),
    ],
)

# The always-present banner strip, drawn above the tab row.
c.box(34, 150, 1272, 52, "Trip banner — countdown · Hold · “trip failed” · “tripped at 14:22”",
      role="act", title_size=14)

tabs = [
    ("Fleet", "Is anything wrong right now?",
     ["status tiles that are also filters", "one row per machine",
      "expand for score, spectra,", "classifier, waterfall",
      "guided setup lives here"], "brain"),
    ("Classifier", "What kind of fault is it?",
     ["one card per asset class",
      "recordings table + select-driven", "upload / relabel / delete",
      "link to Edge Impulse", "fetch the trained model"], "brain"),
    ("Network", "Which network is the base station on?",
     ["mode, SSID, address",
      "scan and join a network", "the page a phone lands on",
      "when it joins the onboarding", "hotspot"], "sense"),
    ("Performance", "Is the monitor itself keeping up?",
     ["one chart per CPU core",
      "memory, temperature, GPU", "per-pipeline frames/s",
      "and time-budget used", "no fabricated headroom number"], "sense"),
    ("Alerts", "Who gets told, and about what?",
     ["Telegram connection + QR code",
      "one row per subscriber", "alert level per subscriber",
      "whole fleet or named machines"], "tell"),
]

x = 34
w = 244
gap = 12
for title, question, rows, role in tabs:
    c.box(x, 232, w, 46, title, role=role, title_size=15)
    c.text(x + w / 2, 300, question, size=11, anchor="middle", fill=INK_SOFT)
    c.box(x, 316, w, 176, "", rows, role="ghost", title_size=1, body_size=10.5)
    c.link([(x + w / 2, 202), (x + w / 2, 232)], width=1.5)
    x += w + gap

c.text(34, 534, "Nothing is shown twice. The asset class is editable in setup and nowhere else; the trip output "
       "is configured in setup and only read back on the tile.",
       size=11.5, style="italic", fill=INK_SOFT)

save(c, "13-dashboard-tabs")
