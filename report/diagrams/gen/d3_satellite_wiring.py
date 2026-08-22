"""03 -- satellite node block wiring (Chapter 3)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from diagram_lib import Canvas, save  # noqa: E402

c = Canvas(
    1180, 706,
    title="Satellite node — the sensing half of the base station, on Wi-Fi",
    subtitle="Same two sensors, same status ring, same frame on the wire. Only the transport differs.",
    footnotes=[
        ("The XIAO ESP32-S3 breaks out 11 GPIOs. The pin map keeps the board's fixed hardware SPI "
         "lines free for the accelerometer; exact pins are in the KiCad schematic.", None),
        ("No per-unit configuration: the node's ID comes from its own Wi-Fi MAC, and its credentials "
         "come from the setup portal, not from a rebuild.", None),
    ],
)

c.box(34, 158, 268, 128, "KX134-1211",
      ["same part as the base station —", "one line item to buy in bulk"], role="sense")
c.box(34, 320, 268, 104, "INMP441", ["same part as the base station"], role="sense")
c.box(34, 458, 268, 100, "WS2812B ring",
      ["8 pixels — this node's only", "display, no LED matrix here"], role="tell")

c.box(430, 158, 320, 400, "Seeed XIAO ESP32-S3",
      ["", "dual-core, 240 MHz", "Wi-Fi 2.4 GHz + BLE", "8 MB PSRAM, 8 MB flash",
       "", "samples, FFTs and pools", "the spectrum locally, then", "publishes one frame"],
      role="sense", title_size=17)

# The MCU box is tall enough to meet all three peripherals head-on, so none of
# these needs a jog.
c.link([(302, 222), (430, 222)], label="SPI + data-ready INT")
c.link([(302, 372), (430, 372)], label="I²S")
c.link([(430, 508), (302, 508)], label="1-wire")  # the ring is an output

c.box(846, 208, 300, 122, "Base station",
      ["MQTT broker", "epm-base.local : 1883"], role="brain")
c.box(846, 392, 300, 118, "On-board NVS",
      ["SSID · password · broker,", "written once at setup"], role="ghost")

c.link([(750, 269), (846, 269)], label="MQTT", both=True)
c.link([(750, 451), (846, 451)], kind="arrowSoft", both=True)

save(c, "03-satellite-node-wiring")
