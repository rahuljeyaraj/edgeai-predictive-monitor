"""04 -- one frame's journey from raw samples to a status (Chapter 4).

Bare vertical flowchart: no title band, no footnote band, and no text
outside the boxes. What used to hang beside each box as a grey caption is
either folded into the box in short form or dropped -- the thresholds and
the persistence rule are prose in Chapter 4, not part of the picture.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from diagram_lib import Canvas, save  # noqa: E402

c = Canvas(566, 614)

CX = 283          # every box shares one centre line, so the chain is straight
W, H = 498, 84

c.box(34, 34, W, H, "Raw window",
      ["1024 accel samples per axis @ 12.8 kHz,",
       "2048 mic samples @ 96 kHz"], role="sense")
c.box(34, 148, W, H, "Feature vector",
      ["FFT spectrum + 6 shape stats per channel  =  536 numbers"], role="sense")
c.box(34, 262, W, H, "Autoencoder",
      ["squeeze it small, rebuild it, measure the error",
       "trained on this machine's healthy data, on the UNO Q"], role="brain")
c.box(34, 376, W, H, "Anomaly score",
      ["one number: how unlike normal this moment is"], role="brain")

c.link([(CX, 118), (CX, 148)])
c.link([(CX, 232), (CX, 262)])
c.link([(CX, 346), (CX, 376)])

# Left to right in severity order, the middle one sitting on the chain's own
# centre line so the fan is symmetric about the trunk.
c.box(34, 522, 150, 58, "Healthy", role="tell")
c.box(208, 522, 150, 58, "Warning", role="warn")
c.box(382, 522, 150, 58, "Fault", role="act")

# Fault drawn first so the two neutral branches overdraw the shared stub --
# otherwise the trunk picks up the red of whichever branch was drawn last.
c.link([(CX, 460), (CX, 492), (457, 492), (457, 522)], kind="arrowAct")
c.link([(CX, 460), (CX, 492), (109, 492), (109, 522)])
c.link([(CX, 460), (CX, 522)])

save(c, "04-feature-pipeline")
