"""16 -- what the Linux tier actually does with a frame (Report S5, figure 2 of 2).

The point of this figure is that the two models are NOT two always-on parallel
branches. handle_frame() is one sequential thread with two branch points, each
guarded by its own independent gate instance:

  * the classifier runs FIRST and unconditionally (manager.py:190), gated only
    by its own classification_gate (manager.py:151, :300). It works on a node
    that has never been commissioned.
  * the autoencoder runs only if entry.model_path is set (manager.py:192) and
    only through InferencePipeline's own second gate (inference.py:136).

Re-verified against main @ 34f8e52:

  manager.py:175-186  frame_count++, PAUSED guard
  manager.py:190      _maybe_classify() -- before the model_path check
  manager.py:192      not commissioned -> return
  manager.py:208-232  build/rebuild InferencePipeline, then score
  manager.py:233      _report_motor_state()
  manager.py:235-248  record_anomaly_score / history_store / on_score
  manager.py:300      classification_gate.update()
  manager.py:333,335  record_classification, on_classification
  inference.py:136    second gate -- must read RUNNING
  inference.py:164    registry.set_status()
  main.py:506,512-513,517  the protection wiring
  main.py:540-546     on_frame fan-out + the direct spectrum broadcast
  setup_controller.py:72,79   STEPS, SKIPPABLE_STEPS

`grep -c classif protection/protection.py` -> 0. The only writers of
registry.set_status() are inference.py:164 and protection.py:673. That absence
is drawn explicitly, because it is the safety claim the report makes.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from diagram_lib import Canvas, save, INK_SOFT, MONO  # noqa: E402

c = Canvas(
    1860, 1700,
    title="One frame through the Linux tier",
    subtitle="Every arrow is a real call. The classifier and the autoencoder are sequential, not parallel — "
             "and each is held back by its own separate gate.",
    legend=[("sense", "Senses"), ("brain", "Decides"), ("act", "Acts (physical)"),
            ("tell", "Tells a human"), ("ghost", "Not in the live scoring path")],
    footnotes=[
        ("The classifier has no path into protection.py. It writes one field — registry.last_classification — and "
         "nothing reads it back into a trip decision; only the autoencoder's confirmed status can start a countdown.", None),
        ("A node that has never been commissioned still gets classified: step 2 runs before the model_path check at "
         "step 3, which is where such a node returns.", None),
    ],
)

# ============================================================ band 1: ingest

c.box(40, 292, 250, 86, "mqtt_subscriber.py",
      ["satellite frames"], role="sense", title_size=13)
c.box(40, 422, 250, 86, "SpiConsumer",
      ["the board's own sensors"], role="sense", title_size=13)

onframe = c.box(350, 332, 300, 136, "main.py — on_frame()",
                ["line 540", "one fan-in point for every",
                 "frame, whichever transport", "it arrived on"], role="neutral")

# Two transports, one shared entry point.
c.link([(290, 335), (320, 335), (320, 400), (350, 400)])
c.link([(290, 465), (320, 465), (320, 400), (350, 400)])

FAN = [
    (202, "commissioning.feed_frame()", "main.py:542", "neutral", None),
    (284, "capture.feed_frame()", "main.py:543", "neutral", None),
    (366, "stopped_baseline.feed_frame()", "main.py:544", "neutral", None),
    (448, 'WS broadcast — "spectrum"', "main.py:546 — bypasses the pipeline", "tell", None),
    (530, "manager.route(frame)", "main.py:541 — the first of the four", "brain", None),
]
for y, title, sub, role, badge in FAN:
    c.box(710, y, 330, 68, title, [sub], role=role, title_size=13, badge=badge)

# Odd-coloured branch drawn first so the neutral ones overdraw the shared
# trunk instead of picking up its colour.
c.link([(650, 400), (680, 400), (680, 482), (710, 482)], kind="arrowTell")
c.link([(650, 400), (680, 400), (680, 236), (710, 236)])
c.link([(650, 400), (680, 400), (680, 318), (710, 318)])
c.link([(650, 400), (680, 400), (680, 564), (710, 564)])
c.link([(650, 400), (710, 400)])

c.lines(500, 528, ["four consumers of every frame,", "plus one broadcast that",
                   "never enters the pipeline"], size=11.5, anchor="middle", fill=INK_SOFT)

# ====================================================== band 2: the manager

c.group(34, 640, 1000, 720,
        "pipeline/manager.py — MotorPipeline.handle_frame()   ·   one thread, top to bottom",
        role="neutral")

SPINE = [
    (680, "1 · PAUSED guard", ["manager.py:175-186", "paused → return, nothing runs"]),
    (784, "2 · _maybe_classify()", ["manager.py:190", "runs first, unconditionally"]),
    (888, "3 · model_path check", ["manager.py:192", "not commissioned → return"]),
    (992, "4 · InferencePipeline", ["manager.py:208-232", "build/rebuild, then score"]),
    (1096, "5 · _report_motor_state()", ["manager.py:233", "edge-triggered, once per change"]),
    (1200, "6 · record the score", ["manager.py:235-248"]),
]
for y, title, body in SPINE:
    c.box(64, y, 340, 80, title, body, role="brain", title_size=13.5)

for y in (680, 784, 888, 992, 1096):
    c.link([(234, y + 80), (234, y + 104)])

# -- step 2's branch: the classifier. Its own gate, its own preconditions.
c.box(440, 784, 270, 80, "classification_gate",
      ["manager.py:151 · :300", "own gate — must read RUNNING"], role="brain", title_size=13)
c.box(740, 784, 250, 80, "classifier.classify()",
      ["pipeline/classifier.py", "→ registry :333 · WS push :335"], role="brain", title_size=13)
c.link([(404, 824), (440, 824)])
c.link([(710, 824), (740, 824)])

# -- step 3's branch: where an uncommissioned node stops.
c.box(440, 888, 270, 80, "return",
      ["the classifier already ran;", "no anomaly score for this node"],
      role="ghost", title_size=13)
c.link([(404, 928), (440, 928)], kind="arrowSoft")

# -- step 4's branch: the autoencoder, behind a second, separate gate.
c.box(440, 992, 270, 80, "inference.handle_frame()",
      ["inference.py:136 — a second,", "separate gate; must read RUNNING"],
      role="brain", title_size=13)
c.box(740, 992, 250, 80, "threshold + hysteresis",
      ["inference.py:164", "→ registry.set_status()"], role="brain", title_size=13)
c.link([(404, 1032), (440, 1032)])
c.link([(710, 1032), (740, 1032)])

# -- step 6's branch.
c.box(440, 1200, 270, 80, "record + persist",
      ["record_anomaly_score()  :235", "history_store.record()  :237"],
      role="neutral", title_size=13)
c.box(740, 1200, 250, 80, 'WS push — "anomaly"',
      ["manager.py:248"], role="tell", title_size=13)
c.link([(404, 1240), (440, 1240)])
c.link([(710, 1240), (740, 1240)], kind="arrowTell")

# -- the absence, drawn on purpose.
c.link([(865, 864), (865, 902)], kind="arrowSoft", dashed=True)
c.raw('<circle cx="865" cy="920" r="15" fill="#FFFFFF" stroke="#B03225" stroke-width="2.4"/>')
c.raw('<line x1="854.4" y1="909.4" x2="875.6" y2="930.6" stroke="#B03225" stroke-width="2.4"/>')
c.lines(865, 958, ["no path from here to protection.py —", "the classifier can never trip a machine"],
        size=11.5, anchor="middle", fill="#8C271D")

# ================================================= band 3: state and safety

registry = c.box(1100, 700, 380, 400, "registry/registry.py",
                 ["the asset registry and its state machine", "",
                  "record_classification()  ← step 2, :333", "set_status()  ← step 4, inference.py:164",
                  "record_anomaly_score()  ← step 6, :235", "",
                  "last_classification is written and", "displayed — nothing reads it back"],
                 role="neutral")

protection = c.box(1560, 1160, 300, 260, "protection/protection.py",
                   ["the trip decision", "",
                    "on_status_change()  :304", "on_motor_state()  :322",
                    "both wired at main.py:506, :512", "",
                    "0 references to the classifier"],
                   role="act")

c.link([(990, 824), (1100, 824)])
c.link([(990, 1032), (1100, 1032)])
c.link([(575, 1200), (575, 1160), (1050, 1160), (1050, 1075), (1100, 1075)])

# on_motor_state: leaves step 5, crosses to protection well below the registry.
c.link([(404, 1136), (1495, 1136), (1495, 1220), (1560, 1220)],
       label="on_motor_state  (main.py:512)", label_seg=0, label_at=0.42)

# FAULT confirmed in the registry is what starts the countdown.
c.link([(1480, 1000), (1530, 1000), (1530, 1300), (1560, 1300)],
       label="on_status_change · FAULT",
       label_seg=1, label_side="right", label_at=0.33)

# The one arrow back into the manager.
c.link([(1560, 1340), (1034, 1340)],
       label="is_running query  (main.py:517)", kind="arrowSoft", label_at=0.55)

# ============================================== band 4: dashboard, two ways

ws = c.box(1100, 250, 330, 200, "WebSocket push",
           ["main.py broadcast_threadsafe", "", '"spectrum"  :546',
            '"anomaly"  :450', '"classification"  :462'], role="tell")

appjs = c.box(1490, 250, 340, 200, "frontend/app.js",
              ["the dashboard", "", "renders what it is pushed,", "and posts commands back"],
              role="tell")

c.link([(1430, 350), (1490, 350)], kind="arrowTell")

# the direct spectrum broadcast, straight from on_frame
c.link([(1040, 482), (1070, 482), (1070, 380), (1100, 380)], kind="arrowTell")

# anomaly + classification, out of the pipeline
c.link([(1034, 680), (1060, 680), (1060, 615), (1330, 615), (1330, 450)],
       kind="arrowTell", label="from steps 2 and 6", label_seg=2, label_at=0.62)

# outbound POSTs -- two different targets.
c.link([(1660, 450), (1660, 660), (1200, 660), (1200, 700)],
       label="POST pause · resume · decommission", label_seg=2, label_at=0.5)
c.link([(1800, 450), (1800, 1160)],
       label="POST protection/hold · acknowledge", label_side="left", label_at=0.72)

# ================================================ band 5: commissioning flow

c.rule(34, 1440, 1826)
c.text(34, 1474, "Off the live path — the guided setup that produces a model in the first place",
       size=14, weight="bold")

c.box(34, 1500, 300, 78, "api/setup_controller.py",
      ["sequences the other controllers;", "owns step order only"],
      role="ghost", title_family=MONO, title_size=12.5)

STEPS = ["1  Name & class", "2  Machine off", "3  Machine running",
         "4  Train", "5  Stop output", "6  Done"]
x = 400
prev = None
for i, label in enumerate(STEPS):
    node = c.chip(x, 1527, label, role="ghost", size=11.5, h=26)
    if prev is not None:
        c.link([(prev, 1540), (x, 1540)], kind="arrowSoft", width=1.4)
    prev = x + node.w
    x = prev + 42

c.text(1082, 1596, "step 5 is the only skippable one  (setup_controller.py:79)",
       size=11.5, anchor="middle", fill=INK_SOFT)
c.link([(360, 1539), (400, 1539)], kind="arrowSoft")

save(c, "16-mpu-pipeline-detail", scale=3.2)
