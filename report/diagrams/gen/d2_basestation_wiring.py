"""02 -- base station block wiring (Chapter 2). Exact nets are in the KiCad
schematic; this shows which half of the board each part hangs off."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from diagram_lib import Canvas, save  # noqa: E402

c = Canvas(
    1180, 764,
    title="Base station — what connects where",
    subtitle="All three peripherals hang off the real-time half of the board. The Linux half never touches a sensor pin.",
    footnotes=[
        ("Two internal links, on purpose: small control messages on the UART, bulk spectra on the SPI, "
         "so a large diagnostic pull can never stall the live status loop.", None),
        ("Exact nets, net names and header pins: the KiCad schematic.", None),
    ],
)

c.box(34, 150, 268, 128, "KX134-1211",
      ["3-axis accelerometer", "±8/16/32/64 g, 16-bit", "512-byte hardware FIFO"], role="sense")
c.box(34, 296, 268, 112, "INMP441",
      ["I²S MEMS microphone", "24-bit, 61 dBA SNR"], role="sense")
c.box(34, 426, 268, 100, "WS2812B ring",
      ["8 addressable pixels", "local status light"], role="tell")

c.group(414, 128, 352, 498, "Arduino UNO Q — one board, two brains", role="brain")
c.box(444, 162, 292, 250, "STM32U585",
      ["Zephyr RTOS", "", "samples both sensors,", "runs the FFTs,",
       "computes scalar stats,", "drives both displays"], role="sense", title_size=16)
c.box(444, 462, 292, 142, "QRB2210",
      ["Debian Linux, quad-core", "", "models, registry, dashboard"], role="brain", title_size=16)

# Two of the three peripherals sit level with the MCU box and go in straight.
# The ring can't: three peripherals stacked are taller than the MCU box, so one
# of them has to climb. It is the ring, because it is the only output here.
c.link([(302, 214), (444, 214)], label="SPI1 + data-ready INT")
c.link([(302, 352), (444, 352)], label="SAI1 · I²S")
# Drawn MCU → ring, because the ring is the one peripheral here that is an
# output. label_dx keeps the plate out of the ring box's top-right corner.
c.link([(444, 392), (373, 392), (373, 476), (302, 476)], label="timer + DMA",
       label_seg=2, label_dx=28)

c.link([(534, 412), (534, 462)], label="LPUART1 · 500 k", both=True, label_side="left")
c.link([(652, 412), (652, 462)], label="SPI · ~40 MHz", both=True)

c.box(864, 182, 282, 84, "8×13 LED matrix", ["already on the UNO Q"], role="tell")
c.box(864, 300, 282, 84, "USB-UART console", ["debug only"], role="ghost")
c.box(864, 470, 282, 126, "Wi-Fi",
      ["dashboard on :8080", "MQTT broker for satellites", "epm-base.local"], role="brain")

c.link([(736, 224), (864, 224)])
c.link([(736, 342), (864, 342)], kind="arrowSoft", dashed=True)
c.link([(736, 533), (864, 533)], both=True)

save(c, "02-base-station-wiring")
