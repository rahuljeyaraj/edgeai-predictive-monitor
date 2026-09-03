#!/usr/bin/env python3
from gen import Part, Schematic, stack_column, write_pro

sch = Schematic("EdgeAI Predictive Monitor - Satellite Node Wiring (Seeed XIAO ESP32S3)")

# Pins are named the way the XIAO itself names them -- the D0-D10 breakout
# labels on the board, not the ESP32-S3 function or GPIO number. Mapping is
# from framework-arduinoespressif32's variants/XIAO_ESP32S3/pins_arduino.h
# (D0=GPIO1 .. D5=GPIO6, D6=GPIO43, D7=GPIO44, D8=GPIO7, D9=GPIO8, D10=GPIO9),
# the same source satellite/include/board_pins.h cites -- and the assignment
# below is transcribed straight from that header's #defines. D8/D9/D10 are the
# variant's fixed hardware SPI pins; everything else is a free GPIO choice.
# D0 and D4 are left unused by this pin map.
XIAO = Part(
    "epm:XIAO_ESP32S3", "U", "Seeed Studio XIAO ESP32S3",
    right=["D8", "D9", "D10", "D6", "D7", "D2", "D1", "D3",
           "D5", "5V", "3V3", "GND"],
    width=45.72,
)
KX134 = Part(
    "epm:KX134", "U", "SmartElex KX134-1211 (accelerometer)",
    left=["SCK", "SDO", "SDI", "CS", "INT1", "VCC", "GND"],
    width=30.48,
)
INMP441 = Part(
    "epm:INMP441", "U", "INMP441 (I2S microphone)",
    left=["SCK", "WS", "SD", "L/R", "VDD", "GND"],
    width=27.94,
)
RING = Part(
    "epm:WS2812_RING", "U", "WS2812B x8 status ring",
    left=["DIN", "VCC", "GND"],
    width=27.94,
)

xiao_y, = stack_column(48, [XIAO])
kx134_y, inmp441_y, ring_y = stack_column(48, [KX134, INMP441, RING])

sch.place(XIAO, "U1", 60, xiao_y, {
    "D8": "ACC_SCK", "D9": "ACC_MISO", "D10": "ACC_MOSI",
    "D6": "ACC_CS", "D7": "ACC_INT",
    "D2": "MIC_WS", "D1": "MIC_SCK", "D3": "MIC_SD",
    "D5": "LED_DIN",
    "5V": "PWR:+5V", "3V3": "PWR:+3V3", "GND": "PWR:GND",
})

sch.place(KX134, "U2", 170, kx134_y, {
    "SCK": "ACC_SCK", "SDO": "ACC_MISO", "SDI": "ACC_MOSI",
    "CS": "ACC_CS", "INT1": "ACC_INT",
    "VCC": "PWR:+3V3", "GND": "PWR:GND",
})

sch.place(INMP441, "U3", 170, inmp441_y, {
    "SCK": "MIC_SCK", "WS": "MIC_WS", "SD": "MIC_SD",
    "L/R": "PWR:GND", "VDD": "PWR:+3V3", "GND": "PWR:GND",
})

sch.place(RING, "U4", 170, ring_y, {
    "DIN": "LED_DIN", "VCC": "PWR:+5V", "GND": "PWR:GND",
})

sch.note("EdgeAI Predictive Monitor -- Satellite Node Wiring", 20, 25, size=4.2)
sch.note("Seeed Studio XIAO ESP32S3. Same sensor set as the base station; status ring only, no LED matrix.", 20, 34, size=2.4)
sch.note("XIAO pins are named by its own D0-D10 breakout labels -- not by GPIO number.", 20, 41, size=2.0)

open("satellite_node.kicad_sch", "w").write(sch.render())
write_pro("satellite_node.kicad_pro", sch)
print("wrote satellite_node.kicad_sch + satellite_node.kicad_pro")
