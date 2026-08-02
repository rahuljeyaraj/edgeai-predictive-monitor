"""11 -- the Edge Impulse round trip, driven from the dashboard (Chapter 6)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from diagram_lib import Canvas, save, INK_SOFT  # noqa: E402

c = Canvas(
    1340, 764,
    title="The Edge Impulse round trip",
    subtitle="Record on the machine · upload from the dashboard · train in Studio · fetch the model back. One project per asset class.",
    footnotes=[
        ("Everything on the device side is plain REST from the standard library — no Edge Impulse SDK is "
         "installed on the board, so nothing here can be broken by a dependency the UNO Q can't build.", None),
        ("The classifier names the fault. It is never allowed to stop a motor — that path reads the anomaly "
         "gate alone and has no code path that can see a classification.", "#B03225"),
    ],
)

# --- on the device ------------------------------------------------------
c.group(34, 150, 604, 372, "On the UNO Q", role="brain")

c.box(58, 194, 264, 108, "1 · Record",
      ["Open an asset's Record drawer,", "type a label, press Start.",
       "Runs server-side — close the tab."], role="sense", title_size=14, body_size=10.5)
c.box(350, 194, 264, 108, "2 · Select & Upload",
      ["Tick recordings on that class's", "card. Standardised, named,",
       "batched 25 × 8 in flight."], role="brain", title_size=14, body_size=10.5)
c.box(58, 350, 264, 108, "5 · Classify live",
      ["TFLite on the CPU, on the same", "feature vector the anomaly",
       "model already sees."], role="brain", title_size=14, body_size=10.5)
c.box(350, 350, 264, 108, "4 · Fetch",
      ["One button pulls the built", ".tflite back down and applies it",
       "to every asset of that class."], role="brain", title_size=14, body_size=10.5)

c.link([(322, 248), (350, 248)])
c.link([(350, 404), (322, 404)])

# --- Edge Impulse -------------------------------------------------------
c.group(706, 150, 600, 372, "Edge Impulse", role="neutral")
c.box(730, 194, 552, 108, "Project — one per asset class",
      ["created from the dashboard: project + impulse + training config,",
       "features input block with real column names (accel_x_bin0, rms_x, …)"],
      role="neutral", title_size=14, body_size=10.5)
c.box(730, 350, 552, 108, "3 · Train in Studio",
      ["The one step deliberately left in Edge Impulse's own hands.",
       "DSP tuning and model architecture are what Studio is good at."],
      role="neutral", title_size=14, body_size=10.5)

c.link([(614, 248), (730, 248)], label="ingestion API")
c.link([(1006, 302), (1006, 350)])
c.link([(730, 404), (672, 404), (672, 404), (614, 404)], label="build + download")

# --- the pooled baseline ------------------------------------------------
c.box(34, 556, 1272, 92, "The part that is easy to get wrong",
      ["The scalar tail is standardised against a baseline pooled across every recording of that asset class — fitted on the",
       "train split only, and saved to disk — so the data the model is trained on and the data it is served at runtime are on the",
       "same scale. Fit it per node instead and five identical lathes quietly pull the model five different ways."],
      role="warn", title_size=14, body_size=11)

c.text(34, 538, "Train once, cover every machine of that type. The anomaly model is per machine; this one is not.",
       size=11.5, style="italic", fill=INK_SOFT)

save(c, "11-edge-impulse-flow")
