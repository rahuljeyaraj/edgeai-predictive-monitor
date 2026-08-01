"""04 -- one frame's journey from raw samples to a status (Chapter 4)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from diagram_lib import Canvas, save, INK_SOFT  # noqa: E402

c = Canvas(
    1320, 520,
    title="From a shaking sensor to a status, once per frame",
    subtitle="The same four steps run for every monitored machine, on its own private model.",
    footnotes=[
        ("Thresholds are not constants. Each machine's warning and fault lines are placed above its own "
         "healthy spread at commissioning: warning = μ + 8σ, fault = μ + 15σ.", None),
        ("A fault has to persist to be believed — one noisy frame never flips the status on its own.", None),
    ],
)

c.box(34, 180, 224, 136, "Raw window",
      ["1024 accel samples", "per axis @ 12.8 kHz,", "2048 mic samples"], role="sense")
c.box(296, 180, 252, 136, "Feature vector",
      ["FFT spectrum, peak-", "normalised, plus six", "shape statistics per channel"], role="sense")
c.box(586, 180, 224, 136, "Autoencoder",
      ["squeeze it small,", "rebuild it, measure", "how badly that went"], role="brain")
c.box(848, 180, 204, 136, "Anomaly score",
      ["one number:", "how unlike normal", "this moment is"], role="brain")

c.link([(258, 248), (296, 248)])
c.link([(548, 248), (586, 248)])
c.link([(810, 248), (848, 248)])

c.box(1092, 150, 194, 58, "Healthy", role="tell")
c.box(1092, 226, 194, 58, "Warning", role="warn")
c.box(1092, 302, 194, 58, "Fault", role="act")

c.link([(1052, 220), (1072, 220), (1072, 179), (1092, 179)])
c.link([(1052, 255), (1092, 255)])
c.link([(1052, 282), (1072, 282), (1072, 331), (1092, 331)], kind="arrowAct")

c.lines(422, 348, ["128 bins × (accel x, y, z + mic)",
                   "+ 6 scalars each  =  536 numbers"], size=11.5, fill=INK_SOFT)
c.lines(698, 348, ["trained on this one machine's healthy",
                   "data, on the UNO Q itself"], size=11.5, fill=INK_SOFT)
c.lines(950, 348, ["reconstruction error —", "big gap means unfamiliar"], size=11.5, fill=INK_SOFT)

save(c, "04-feature-pipeline")
