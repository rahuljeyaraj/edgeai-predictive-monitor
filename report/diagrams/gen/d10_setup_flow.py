"""10 -- the guided setup flow: six steps in one drawer (Chapter 5)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from diagram_lib import Canvas, save, INK_SOFT  # noqa: E402

c = Canvas(
    1340, 640,
    title="Commissioning a machine — one guided flow, six steps",
    subtitle="Before this there were four separate controls in four places, and nothing said what order they went in.",
    footnotes=[
        ("Step 2 is the only instruction in the whole system a computer cannot check for you, which is why "
         "the wording says so out loud: a baseline measured while the machine runs teaches it that its own "
         "vibration is silence.", None),
        ("Trip output sits after Train because its test needs a model to be able to answer “is it running?” "
         "at all. It used to be step 2, where its test could only ever fail.", "#B03225"),
    ],
)

steps = [
    ("1", "Name & class", ["Both required. The name", "is what the alert prints;", "the class is what", "recordings group by."],
     "Machine: either", "neutral"),
    ("2", "Off", ["Measure what this sensor", "reads with the machine", "switched off — its own", "noise floor."],
     "Machine: OFF", "sense"),
    ("3", "Running conditions", ["One or more named ways", "this machine normally", "runs: no load, full load.", "≥50 frames each."],
     "Machine: ON", "sense"),
    ("4", "Train", ["Fit this asset's own", "autoencoder and its", "own thresholds, here", "on the UNO Q."],
     "Machine: either", "brain"),
    ("5", "Trip output", ["Don't ask which motor —", "send a real stop and", "watch the machine go", "quiet. Skippable."],
     "Machine: ON → stopped", "act"),
    ("6", "Done", ["Summary, and the asset", "goes live: scored on", "every frame from here", "on."],
     "Machine: either", "tell"),
]

x = 34
w = 202
gap = 14
for badge, title, rows, machine, role in steps:
    c.box(x, 166, w, 172, title, rows, role=role, title_size=13.5,
          body_size=10, badge=badge)
    c.chip(x + 6, 350, machine, role="ghost", size=10, pad=8, h=21)
    if x + w + gap < 1300:
        c.link([(x + w, 252), (x + w + gap, 252)], width=1.5)
    x += w + gap

c.box(34, 412, 630, 84, "What it produces",
      ["stopped_spectrum_ref · running_energy_ref · a trained model ·",
       "warning + fault thresholds · a confirmed trip output"],
      role="ghost", title_size=13, body_size=11)

c.box(690, 412, 616, 84, "What it replaced",
      ["a Commission button on the tile · a baseline control buried in an expanded panel ·",
       "a motor dropdown hardcoded to three · a Record drawer nobody connected to any of it"],
      role="ghost", title_size=13, body_size=11)

c.text(34, 384, "Only step 5 can be skipped — most monitored points have no actuator wired to them at all.",
       size=11.5, style="italic", fill=INK_SOFT)

save(c, "10-setup-flow")
