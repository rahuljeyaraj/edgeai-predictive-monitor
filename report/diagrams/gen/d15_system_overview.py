"""15 -- code structure, tier by tier (Report S5, figure 1 of 2).

Every box is a real file. Line references cited in the report text were
re-verified against main @ 34f8e52:

  satellite/src/threads/accel_sampler_task.cpp:4   arduinoFFT.h
  satellite/src/threads/mic_sampler_task.cpp:4     arduinoFFT.h
  satellite/src/threads/fuser_task.cpp:8,215       spectrum_codec.h, publish
  satellite/src/threads/transport_task.cpp:199     epm/<node_id>/data
  satellite/include/app_config.h:86                MODEL_SPECTRUM_BINS 128
  base-station/python/tools/gen_telemetry_schema.py:39-43  the four paths

The schema panel is deliberately drawn *below the rule*, detached from the
tier flow: it is a build-time step, not a stage a frame passes through. The
distinction matters because the generator emits ID constants only -- the
encode/decode logic on each of the three sides is hand-written.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from diagram_lib import Canvas, save, INK_SOFT, MONO  # noqa: E402

c = Canvas(
    1580, 1150,
    title="Code structure, tier by tier",
    subtitle="Two firmware tiers produce the same self-describing telemetry frame; the Linux tier consumes both. "
             "Each block names the file that does the work.",
    legend=[("sense", "Senses"), ("brain", "Decides"),
            ("ghost", "Build-time only — not a runtime stage")],
    footnotes=[
        ("Both firmware tiers emit an identical frame layout — 128 spectrum bins per channel for mic and accel x/y/z, "
         "plus six scalar statistics per channel: 4 x 128 + 4 x 6 = 536 numbers.", None),
        ("The generator emits ID constants only (channel ids, scalar ids, name-to-id maps). The encode/decode logic "
         "on each of the three sides is hand-written against those constants.", None),
    ],
)

# ---------------------------------------------------------------- tier 1

c.group(34, 190, 1074, 196,
        "Tier 1 — Satellite node  ·  ESP32-S3 (PlatformIO / Arduino)", role="neutral")

t1a = c.box(60, 232, 300, 128, "Sampler tasks",
            ["accel_sampler_task.cpp", "mic_sampler_task.cpp",
             "SPI (KX134) / I²S read,", "per-axis FFT"],
            role="sense")
t1b = c.box(420, 232, 300, 128, "fuser_task.cpp",
            ["pool to 128 bins/channel,", "6 scalars per channel,",
             "encode via spectrum_codec.cpp"], role="sense")
t1c = c.box(780, 232, 300, 128, "transport_task.cpp",
            ["MQTT publish over Wi-Fi,", "topic  epm/<node_id>/data"], role="sense")

c.link([t1a.right, t1b.left])
c.link([t1b.right, t1c.left])

# ---------------------------------------------------------------- tier 2

c.group(34, 440, 1074, 196,
        "Tier 2 — Base station MCU  ·  STM32U585 (Zephyr, via App Lab)", role="neutral")

t2a = c.box(60, 482, 300, 128, "Sampler threads",
            ["accel_sampler.cpp/.h", "mic_sampler.cpp/.h",
             "KX134 over SPI (FIFO),", "INMP441 over SAI"], role="sense")
t2b = c.box(420, 482, 300, 128, "fuser.cpp/.h",
            ["FFT, statistics, pooling,", "frame assembly —", "same layout as Tier 1"],
            role="sense")
t2c = c.box(780, 482, 300, 128, "spi_link.cpp/.h",
            ["bulk telemetry link", "to the Linux side"], role="sense")

c.link([t2a.right, t2b.left])
c.link([t2b.right, t2c.left])

# ---------------------------------------------------------------- tier 3

mpu = c.box(1188, 318, 358, 190, "Tier 3 — Base station MPU",
            ["QRB2210  ·  Debian  ·  Python", "",
             "MQTT subscriber + SPI reader,", "pipeline, protection, dashboard"],
            role="brain", badge="next figure")

# Two transports converge on one box. They leave their own tier level and
# share the riser at x=1140, so this reads as one fan-in rather than two
# unrelated jogs. Labels hang off the riser, not off the short stub, so
# neither lands on the tier group's dashed border.
c.link([(1080, 296), (1140, 296), (1140, 380), (1188, 380)],
       label="MQTT / Wi-Fi", label_seg=1, label_side="right")
c.link([(1080, 546), (1140, 546), (1140, 452), (1188, 452)],
       label="SPI", label_seg=1, label_side="right")

# ------------------------------------------------- cross-cutting schema

# Below the rule, in ghost tones: this is a build-time step run by hand, not
# a stage any frame passes through.
c.rule(34, 690, 1546)
c.text(34, 722, "Cross-cutting — the wire format is generated, not hand-synced",
       size=14, weight="bold")

c.group(34, 760, 1512, 250, "Build-time code generation  ·  run by hand", role="ghost")

# The one hand-edited file in this panel, so it keeps a solid slate box while
# everything downstream of the generator stays ghosted.
src = c.box(60, 812, 280, 146, "telemetry_schema.json",
            ["base-station/", "single source of truth", "for the wire format"],
            role="neutral", title_family=MONO, title_size=13)

gen = c.box(400, 812, 380, 146, "gen_telemetry_schema.py",
            ["base-station/python/tools/", "", "no build hook — no PlatformIO",
             "extra_script, no Makefile, no CI"],
            role="ghost", title_family=MONO, title_size=12.5)

c.link([src.right, gen.left])

# Middle output sits on the generator's own centre line, so its edge is
# straight and the other two share one riser off that same exit point.
c.box(840, 781, 340, 64, "sketch/telemetry_schema.h",
      ["Tier 2 — STM32U585"], role="ghost", title_family=MONO, title_size=12.5)
c.box(840, 853, 340, 64, "frame_codec/telemetry_schema.h",
      ["Tier 1 — ESP32-S3"], role="ghost", title_family=MONO, title_size=12.5)
c.box(840, 925, 340, 64, "common/telemetry_schema.py",
      ["Tier 3 — Python"], role="ghost", title_family=MONO, title_size=12.5)

c.link([(780, 885), (808, 885), (808, 813), (840, 813)], kind="arrowSoft")
c.link([(780, 885), (808, 885), (808, 957), (840, 957)], kind="arrowSoft")
c.link([(780, 885), (840, 885)], kind="arrowSoft")

c.lines(1373, 812, ["Every generated file carries", "the same first line:"],
        size=11.5, anchor="middle", fill=INK_SOFT)
c.chip(1216, 848, "GENERATED — DO NOT EDIT BY HAND", role="warn", size=10.5)
c.lines(1373, 912,
        ["Re-running the generator against main", "produces zero diff — all three",
         "checked-in files are in sync."],
        size=11.5, anchor="middle", fill=INK_SOFT)

save(c, "15-system-overview", scale=3.2)
