"""06 -- every status an asset can hold, and what moves it (Chapter 6)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from diagram_lib import Canvas, save  # noqa: E402

c = Canvas(
    1340, 830,
    title="Every status an asset can be in",
    subtitle="Ten states. The only things that move between them are a measurement or a deliberate human action.",
    footnotes=[
        ("Idle and Tripped both mean the machine is not turning. They differ only in who stopped it — "
         "which is exactly the distinction an operator needs, and which one “stopped” status would erase.", None),
        ("There is no “reset protection” button, by design. Restart the machine and it is scored from "
         "scratch: fix the fault and it returns to Healthy; don't, and it trips again.", None),
        ("Offline is never stored. It is derived from how long ago the last frame arrived, so it can "
         "never get stuck on after a node comes back.", None),
    ],
)

# ---- setup spine ---------------------------------------------------------
c.box(34, 150, 180, 64, "New", role="ghost", title_size=16)
c.box(300, 150, 190, 64, "Collecting", role="neutral", title_size=16)
c.box(580, 150, 180, 64, "Training", role="neutral", title_size=16)

c.link([(214, 182), (300, 182)], label="Commission")
c.link([(490, 182), (580, 182)], label="Stop & train")

# ---- the live, model-confirmed statuses ---------------------------------
c.group(470, 280, 380, 300, "Live — re-scored on every frame", role="brain")
c.box(500, 322, 320, 62, "Healthy", role="tell", title_size=16)
c.box(500, 404, 320, 62, "Warning", role="warn", title_size=16)
c.box(500, 486, 320, 62, "Fault", role="act", title_size=16)

c.link([(670, 214), (800, 214), (800, 280)], label="first model saved", label_seg=1)
c.link([(500, 353), (420, 353), (420, 238), (395, 238), (395, 214)], label="Recommission", label_side="left", label_dy=-20)

# ---- machine not turning, and the two ways that happens ------------------
c.box(930, 322, 200, 62, "Idle", role="sense", title_size=16)
c.box(930, 486, 200, 62, "Tripped", role="act", title_size=16)
c.box(930, 640, 200, 62, "Offline", role="ghost", title_size=16)
c.box(500, 640, 320, 62, "Paused", role="ghost", title_size=16)

c.link([(820, 353), (930, 353)], label="a person stopped it",
       both=True, label_dy=-30, label_size=10.5)
c.link([(820, 517), (930, 517)], label="we stopped it",
       kind="arrowAct", width=2.2, label_dy=-30, label_size=10.5)
c.link([(660, 580), (660, 640)], label="Pause / Resume", both=True)
c.link([(850, 580), (884, 580), (884, 671), (930, 671)],
       label="silent 30 s", kind="arrowSoft", dashed=True, label_size=10.5)

# ---- reading key (also keeps the left half from going empty) ------------
c.box(34, 300, 384, 262, "Reading this diagram",
      ["Blue group — the three statuses the anomaly",
       "model is allowed to confirm by itself.",
       "",
       "Grey boxes — set by a person, or derived from",
       "silence. Never chosen by a model.",
       "",
       "Red edge — the one transition in the entire",
       "system that moves something physical."],
      role="ghost", title_size=14, body_size=11)

save(c, "06-asset-lifecycle")
