"""15a-15f -- the code-structure figures for report S5.

Six small figures rather than two large ones, because these are embedded in a
Word document. Three rules follow from that, and they are why this script
looks different from the other d*.py:

  * Canvas width is held near 900 px. A figure placed at the page's full
    ~6.5 in text width renders its 13 pt body text at ~8.5 pt on paper; the
    earlier 1860 px-wide versions rendered the same text at under 4 pt.
  * Height is held under ~1.25x the width, so a figure can sit at full width
    without running off the page -- shrinking it to fit is what destroys the
    font size.
  * Straight edges only, or one bend. Every fan-out that needed a shared
    riser was split into its own figure instead.

Content is deliberately thinner than the code: each figure carries one claim.
Everything drawn was re-verified against main @ c6eb83b -- see the per-figure
comments for the exact lines.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from diagram_lib import Canvas, save, INK_SOFT, MONO  # noqa: E402

SCALE = 3.2          # ~307 dpi, for the Word report
TITLE = 16           # box title size
BODY = 13            # box body size

# ============================================================ 15a -- tiers

# Two firmware tiers, one Linux tier. Both firmware tiers run the same three
# stages and emit the same frame; only the transport differs.
c = Canvas(
    900, 1000,
    title="The three tiers",
    subtitle="Both firmware tiers run the same three stages and emit the same frame.",
    footnotes=[("Frame layout, both tiers: 128 spectrum bins each for mic and accel x/y/z, "
                "plus 6 scalars per channel — 536 numbers.", None)],
)

c.group(34, 150, 390, 530, "Satellite · ESP32-S3", role="neutral")
c.group(476, 150, 390, 530, "Base station MCU · STM32U585", role="neutral")

c.box(54, 195, 350, 140, "Sampler tasks",
      ["accel_sampler_task.cpp", "mic_sampler_task.cpp", "read sensors + per-axis FFT"],
      role="sense", title_size=TITLE, body_size=BODY)
c.box(54, 365, 350, 140, "fuser_task.cpp",
      ["pool to 128 bins per channel", "6 scalars per channel", "encode the frame"],
      role="sense", title_size=TITLE, body_size=BODY)
c.box(54, 535, 350, 140, "transport_task.cpp",
      ["MQTT publish", "epm/<node_id>/data"],
      role="sense", title_size=TITLE, body_size=BODY)

c.box(496, 195, 350, 140, "Sampler threads",
      ["accel_sampler.cpp", "mic_sampler.cpp", "read sensors + per-axis FFT"],
      role="sense", title_size=TITLE, body_size=BODY)
c.box(496, 365, 350, 140, "fuser.cpp",
      ["pool to 128 bins per channel", "6 scalars per channel", "identical frame layout"],
      role="sense", title_size=TITLE, body_size=BODY)
c.box(496, 535, 350, 140, "spi_link.cpp",
      ["bulk telemetry link", "to the Linux side"], role="sense", title_size=TITLE, body_size=BODY)

c.link([(229, 335), (229, 365)])
c.link([(229, 505), (229, 535)])
c.link([(671, 335), (671, 365)])
c.link([(671, 505), (671, 535)])

c.box(200, 780, 500, 140, "Base station MPU",
      ["QRB2210 · Debian · Python", "consumes both streams"],
      role="brain", title_size=TITLE + 2, body_size=BODY)

c.link([(229, 675), (229, 780)], label="MQTT", label_side="left")
c.link([(671, 675), (671, 780)], label="SPI", label_side="right")

save(c, "15a-tiers", scale=SCALE)

# ====================================================== 15b -- wire format

# gen_telemetry_schema.py:39-43 -- one JSON, three generated files, no build
# hook anywhere (no PlatformIO extra_script, no Makefile, no CI).
c = Canvas(
    900, 790,
    title="One schema, three generated files",
    subtitle="The wire format is generated, not kept in sync by hand.",
    footnotes=[
        ("It generates ID constants only — channel ids, scalar ids, name-to-id maps. "
         "The encode/decode logic on each side is hand-written against them.", None),
        ("Run by hand after editing the JSON: there is no build hook. Re-running it against "
         "main produces no diff — all three files are in sync.", None),
    ],
)

c.box(190, 150, 520, 100, "telemetry_schema.json",
      ["the single source of truth"],
      role="neutral", title_family=MONO, title_size=TITLE, body_size=BODY)

c.box(190, 310, 520, 130, "gen_telemetry_schema.py",
      ["base-station/python/tools/", "run by hand — no build hook"],
      role="ghost", title_family=MONO, title_size=TITLE, body_size=BODY)

c.box(150, 500, 600, 170, "Three generated files",
      ["sketch/telemetry_schema.h — MCU",
       "frame_codec/telemetry_schema.h — ESP32",
       "common/telemetry_schema.py — Python",
       "each headed GENERATED — DO NOT EDIT"],
      role="ghost", title_size=TITLE, body_size=BODY)

c.link([(450, 250), (450, 310)])
c.link([(450, 440), (450, 500)])

save(c, "15b-wire-format", scale=SCALE)

# ======================================================= 15c -- the fan-out

# main.py:540-546 -- one entry point, four consumers, plus a broadcast that
# never enters the pipeline at all.
c = Canvas(
    900, 980,
    title="Every frame goes to four places",
    subtitle="main.py's on_frame() is the single fan-in point, whichever transport it arrived on.",
    footnotes=[('The "spectrum" broadcast is not a consumer — it goes straight to the browser '
                "without touching the pipeline.", None)],
)

c.box(30, 359, 300, 140, "on_frame()",
      ["main.py:540", "MQTT subscriber", "and SpiConsumer", "both call it"],
      role="neutral", title_size=TITLE, body_size=BODY)

TARGETS = [
    (160, "manager.route(frame)", "main.py:541 — scoring", "brain"),
    (272, "commissioning.feed_frame()", "main.py:542", "neutral"),
    (384, "capture.feed_frame()", "main.py:543", "neutral"),
    (496, "stopped_baseline.feed_frame()", "main.py:544", "neutral"),
    (608, 'WS broadcast — "spectrum"', "main.py:546", "tell"),
]
for y, title, sub, role in TARGETS:
    c.box(420, y, 440, 90, title, [sub], role=role, title_size=TITLE, body_size=BODY)

# One shared exit and one shared riser, so this reads as a single fan.
c.link([(330, 429), (380, 429), (380, 653), (420, 653)], kind="arrowTell")
c.link([(330, 429), (380, 429), (380, 205), (420, 205)])
c.link([(330, 429), (380, 429), (380, 317), (420, 317)])
c.link([(330, 429), (380, 429), (380, 541), (420, 541)])
c.link([(330, 429), (420, 429)])

c.box(420, 760, 440, 100, "frontend/app.js",
      ["the dashboard's live spectrum"], role="tell", title_size=TITLE, body_size=BODY)
c.link([(640, 698), (640, 760)], kind="arrowTell")

save(c, "15c-frame-fanout", scale=SCALE)

# =================================================== 15d -- the scoring path

# The key figure. manager.py:190 runs the classifier BEFORE the model_path
# check at :192, and the two models sit behind two different gate instances
# (manager.py:151 and inference.py:136).
c = Canvas(
    900, 1080,
    title="One frame through the pipeline",
    subtitle="One thread, top to bottom. The two models sit behind two different gates.",
    footnotes=[
        ("The classifier runs first and unconditionally — a node that has never been "
         "commissioned is still classified, and then returns at step 3.", None),
        ("The two gates are separate instances. Neither one gates the other.", None),
    ],
)

SPINE = [
    (170, "1 · PAUSED guard", ["manager.py:175", "paused → nothing runs"]),
    (302, "2 · _maybe_classify()", ["manager.py:190", "the classifier — runs first"]),
    (434, "3 · model_path check", ["manager.py:192", "commissioned?"]),
    (566, "4 · InferencePipeline", ["manager.py:208", "the autoencoder"]),
    (698, "5 · _report_motor_state()", ["manager.py:233", "tells protection"]),
    (830, "6 · record the score", ["manager.py:235-248", "registry, history, dashboard"]),
]
for y, title, body in SPINE:
    c.box(230, y, 440, 110, title, body, role="brain",
          title_size=TITLE, body_size=BODY)

for y in (170, 302, 434, 566, 698):
    c.link([(450, y + 110), (450, y + 132)])

c.box(690, 312, 180, 90, "own gate",
      ["manager.py:151", "must read RUNNING"], role="brain",
      title_size=TITLE - 1, body_size=BODY - 1)
c.link([(670, 357), (690, 357)])

c.box(690, 576, 180, 90, "second gate",
      ["inference.py:136", "separate instance"], role="brain",
      title_size=TITLE - 1, body_size=BODY - 1)
c.link([(670, 621), (690, 621)])

c.box(30, 444, 180, 90, "return",
      ["not commissioned", "— stops here"], role="ghost",
      title_size=TITLE - 1, body_size=BODY - 1)
c.link([(230, 489), (210, 489)], kind="arrowSoft")

save(c, "15d-scoring-path", scale=SCALE)

# ======================================================= 15e -- the trip path

# Only two things reach protection.py: a confirmed FAULT status written by
# inference.py:164, and the gate's motor state. grep -c classif on
# protection/protection.py returns 0.
c = Canvas(
    900, 910,
    title="What can stop a machine",
    subtitle="Two inputs start a trip, and one operator control holds or clears it.",
    footnotes=[
        ("The classifier has no path in. It writes registry.last_classification, which is "
         "displayed and never read back into a trip decision.", None),
        ("protection/protection.py contains zero references to the classifier.", None),
    ],
)

c.box(60, 150, 380, 130, "Confirmed FAULT",
      ["inference.py:164 writes it", "on_status_change  main.py:506"],
      role="brain", title_size=TITLE, body_size=BODY)
c.box(470, 150, 380, 130, "Motor stopped or started",
      ["manager.py:233 reports it", "on_motor_state  main.py:512"],
      role="brain", title_size=TITLE, body_size=BODY)

c.box(200, 390, 500, 160, "protection/protection.py",
      ["the trip decision", "countdown, confirm, trip"],
      role="act", title_size=TITLE + 2, body_size=BODY)

c.link([(250, 280), (250, 390)])
c.link([(660, 280), (660, 390)])

c.box(470, 660, 380, 120, "frontend/app.js",
      ["POST protection/hold", "POST protection/acknowledge"],
      role="tell", title_size=TITLE, body_size=BODY)
c.link([(660, 660), (660, 550)], kind="arrowTell")

c.box(60, 660, 380, 120, "classifier result",
      ["registry.last_classification", "displayed only"],
      role="ghost", title_size=TITLE, body_size=BODY)
c.link([(250, 660), (250, 628)], kind="arrowSoft", dashed=True)
c.raw('<circle cx="250" cy="600" r="17" fill="#FFFFFF" stroke="#B03225" stroke-width="2.6"/>')
c.raw('<line x1="238" y1="588" x2="262" y2="612" stroke="#B03225" stroke-width="2.6"/>')
c.text(285, 606, "no path — the classifier never trips", size=BODY, fill="#8C271D")

save(c, "15e-trip-path", scale=SCALE)

# ====================================================== 15f -- guided setup

# setup_controller.py:72 STEPS, :79 SKIPPABLE_STEPS. Operator-facing titles
# from frontend/setup.js:40-47.
c = Canvas(
    900, 780,
    title="The guided setup",
    subtitle="Six steps, run once per machine. None of this is in the live scoring path.",
    footnotes=[("api/setup_controller.py owns the step order only — each step drives a "
                "controller that already existed.", None)],
)

STEPS = [
    (150, "1 · Name & class", "name the asset"),
    (238, "2 · Machine off", "learn what stopped looks like"),
    (326, "3 · Machine running", "collect healthy data"),
    (414, "4 · Train", "fit the model on the board"),
    (502, "5 · Stop output", "wire the trip — skippable"),
    (590, "6 · Done", "live and scoring"),
]
for y, title, sub in STEPS:
    role = "ghost" if "skippable" in sub else "neutral"
    c.box(250, y, 400, 70, title, [sub], role=role,
          title_size=TITLE - 1, body_size=BODY - 0.5)

for y in (150, 238, 326, 414, 502):
    c.link([(450, y + 70), (450, y + 88)], kind="arrowSoft")

c.text(450, 700, "step 5 is the only skippable one  (setup_controller.py:79)",
       size=BODY, anchor="middle", fill=INK_SOFT)

save(c, "15f-setup-steps", scale=SCALE)
