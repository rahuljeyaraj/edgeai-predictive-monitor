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

# ================================================== 15c -- the Linux packages

# The top-level map of base-station/python/. Package names and one line of
# responsibility each -- deliberately no line references, because this figure
# answers "where does the code live", not "what runs when" (that is 15d).
c = Canvas(
    900, 960,
    title="The Linux side, package by package",
    subtitle="A measurement travels down the left. Each box is a directory under base-station/python/.",
    footnotes=[("main.py holds no logic of its own. It constructs every object in dependency "
                "order and connects them, so reading it top to bottom is reading the architecture.", None)],
)

COLUMN = [
    (170, "ingestion/", ["mqtt_subscriber.py · spi_reader.py",
                         "turns both transports into one frame"], "sense"),
    (308, "pipeline/", ["one instance per machine",
                        "scoring · commissioning · capture · baseline",
                        "every frame goes to all four"], "brain"),
    (446, "registry/", ["what each machine is,",
                        "and the status it is in now"], "neutral"),
    (584, "api/", ["FastAPI routes and the WebSocket",
                   "one controller per flow"], "neutral"),
    (722, "frontend/", ["the dashboard — app.js",
                        "and one module per tab"], "tell"),
]
for y, title, body, role in COLUMN:
    c.box(34, y, 470, 110, title, body, role=role,
          title_family=MONO, title_size=TITLE, body_size=BODY)

c.link([(269, 280), (269, 308)], label="frames", label_side="right")
c.link([(269, 418), (269, 446)], label="score + status", label_side="right")
c.link([(269, 556), (269, 584)])
c.link([(269, 694), (269, 722)])

c.box(610, 446, 256, 110, "protection/", ["the trip decision", "and the stop output"],
      role="act", title_family=MONO, title_size=TITLE, body_size=BODY)
c.link([(504, 501), (610, 501)], label="fault", kind="arrowAct")

c.box(610, 170, 256, 110, "main.py", ["constructs every object", "and wires them together"],
      role="ghost", title_family=MONO, title_size=TITLE, body_size=BODY)

c.box(610, 584, 256, 248, "used throughout",
      ["common/ — the wire format", "history/ — score history",
       "alerts/ — Telegram", "monitoring/ — performance", "network/ — Wi-Fi"],
      role="ghost", title_size=TITLE, body_size=BODY - 1)

save(c, "15c-linux-packages", scale=SCALE)

# ================================================= 15d -- one frame's journey

# Two models, two different trainings, two independent sets of conditions:
#   * the fault classifier needs a model for the machine's *type* (one Edge
#     Impulse model shared by every machine of that type) -- manager.py's
#     _maybe_classify checks device_type, then the registry for a fetched
#     model, then its own gate.
#   * the health score needs *this machine's own* model, fitted during its
#     commissioning, and re-tests running through a second gate instance.
# Either side can be absent without blocking the other, which is why they are
# drawn as two lanes rather than one chain.
c = Canvas(
    900, 1080,
    title="What happens to one frame",
    subtitle="One machine at a time. Two models run in the same thread, under two different conditions.",
    footnotes=[
        ("Two models, trained two different ways. The fault classifier is trained once per machine "
         "type in Edge Impulse and shared by every machine of that type. The health model is trained "
         "on the board, from one machine's own healthy data.", None),
        ("Either side can be missing. No fault model for the type means no fault name; a machine that "
         "was never commissioned gets no score. Neither blocks the other.", None),
    ],
)

c.box(230, 170, 440, 90, "A frame arrives for one machine",
      ["pipeline/manager.py sends it to", "that machine's own pipeline"],
      role="neutral", title_size=TITLE, body_size=BODY)

c.box(230, 286, 440, 85, "Is the machine paused?",
      role="neutral", title_size=TITLE, rx=26)
c.box(40, 299, 110, 58, "stop", role="ghost", title_size=TITLE - 2)
c.link([(230, 328), (150, 328)], label="yes", kind="arrowSoft")

c.link([(450, 371), (450, 390), (234, 390), (234, 410)])
c.link([(450, 371), (450, 390), (666, 390), (666, 410)])

c.box(34, 410, 400, 250, "Name the likely fault",
      ["pipeline/classifier.py", "",
       "needs a fault model for this", "machine's type — one model",
       "covers every machine of that type", "",
       "and the machine must be running"],
      role="brain", title_size=TITLE, body_size=BODY)

c.box(466, 410, 400, 250, "Score how unusual it looks",
      ["pipeline/inference.py", "",
       "needs this machine's own model,", "fitted when it was commissioned", "",
       "and the machine must be running —", "tested separately from the left"],
      role="brain", title_size=TITLE, body_size=BODY)

c.box(64, 700, 340, 80, "a fault name",
      ["shown on the dashboard,", "never trips the machine"],
      role="neutral", title_size=TITLE, body_size=BODY)
c.box(496, 700, 340, 80, "healthy · warning · fault",
      ["against its own thresholds"],
      role="neutral", title_size=TITLE, body_size=BODY)

c.link([(234, 660), (234, 700)])
c.link([(666, 660), (666, 700)])

c.box(230, 830, 440, 90, "Publish the result",
      ["registry · history · dashboard"],
      role="tell", title_size=TITLE, body_size=BODY)

c.link([(234, 780), (234, 800), (450, 800), (450, 830)])
c.link([(666, 780), (666, 800), (450, 800), (450, 830)])

save(c, "15d-frame-journey", scale=SCALE)

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
