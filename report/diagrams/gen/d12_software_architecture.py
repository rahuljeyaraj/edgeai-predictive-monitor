"""12 -- the Linux-side software architecture, module by module (Chapter 9)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from diagram_lib import Canvas, save, INK_SOFT  # noqa: E402

c = Canvas(
    1340, 960,
    title="Software architecture — what the Linux half is actually made of",
    subtitle="Five layers. Data flows up the left, decisions fan out to the right, and nothing skips a layer.",
    footnotes=[
        ("The registry is the only thing that fans out. Every other module talks to it, not to each other — "
         "so adding a new output means subscribing to the registry, not editing the pipeline.", None),
        ("Every module named here has a matching test module in base-station/tests/ — 34 of them.", None),
    ],
)

LX = 34
LW = 226           # layer-label column
BX = 282           # boxes start
BW = 1024

rows = [
    (150, "Transport", "sense",
     [("ingestion/spi_reader", "the UNO Q's own sensors"),
      ("ingestion/mqtt_subscriber", "every satellite node"),
      ("common/telemetry_frame", "one generated codec")]),
    (272, "Ingest & route", "sense",
     [("pipeline/manager", "frame → asset, shape-checked"),
      ("pipeline/features", "536-number feature vector"),
      ("common/wire_protocol", "framing, CRC, sync")]),
    (394, "Decide", "brain",
     [("pipeline/gate", "running or stopped?"),
      ("pipeline/autoencoder + inference", "per-machine anomaly score"),
      ("pipeline/classifier", "which fault (TFLite)")]),
    (516, "Remember", "brain",
     [("registry/registry", "one live record per asset"),
      ("history/store", "durable score history"),
      ("pipeline/capture", "labelled recordings")]),
    (638, "Act & tell", "act",
     [("protection/protection", "the trip — the only physical act"),
      ("api/app + frontend/", "dashboard over WebSocket"),
      ("alerts/telegram_alerts", "the phone")]),
]

for y, label, role, cells in rows:
    c.box(LX, y, LW, 96, label, role=role, title_size=15)
    cw = (BW - 2 * 18) / 3
    x = BX
    for name, blurb in cells:
        c.box(x, y, cw, 96, name, [blurb], role=role, title_size=12.5,
              body_size=10.5, title_family="DejaVu Sans Mono, Menlo, Consolas, monospace")
        x += cw + 18
    if y != 638:
        c.link([(LX + LW / 2, y + 96), (LX + LW / 2, y + 122)], width=1.6)

c.text(LX, 764, "Status pushes travel back down the same spine: a status change in the registry reaches the "
       "dashboard, the lights and the phone in one fan-out.",
       size=11.5, style="italic", fill=INK_SOFT)

c.box(LX, 786, 1272, 78, "The one rule that keeps this honest",
      ["Nothing writes an asset's status directly. Every status change goes through one explicit state machine in the registry,",
       "which is what closed a real bug where pausing a node mid-commissioning silently stole it out from under the session."],
      role="ghost", title_size=13.5, body_size=11)

save(c, "12-software-architecture")
