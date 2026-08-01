#!/usr/bin/env python3
from gen import Part, Schematic

sch = Schematic("EdgeAI Predictive Monitor - Satellite Node Wiring (Seeed XIAO ESP32S3)")

XIAO = Part(
    "epm:XIAO_ESP32S3", "U", "Seeed Studio XIAO ESP32S3",
    right=["SCK", "MISO", "MOSI", "CS", "INT", "I2S_WS", "I2S_CLK", "I2S_SD",
           "LED_DIN", "5V", "3V3", "GND"],
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

sch.place(XIAO, "U1", 60, 100, {
    "SCK": "SPI_SCK_D8", "MISO": "SPI_MISO_D9", "MOSI": "SPI_MOSI_D10",
    "CS": "ACC_CS_D3", "INT": "ACC_INT_D2",
    "I2S_WS": "MIC_WS_D0", "I2S_CLK": "MIC_SCK_D1", "I2S_SD": "MIC_SD_D4",
    "LED_DIN": "LED_DIN_D5",
    "5V": "PWR:+5V", "3V3": "PWR:+3V3", "GND": "PWR:GND",
})

sch.place(KX134, "U2", 170, 70, {
    "SCK": "SPI_SCK_D8", "SDO": "SPI_MISO_D9", "SDI": "SPI_MOSI_D10",
    "CS": "ACC_CS_D3", "INT1": "ACC_INT_D2",
    "VCC": "PWR:+3V3", "GND": "PWR:GND",
})

sch.place(INMP441, "U3", 170, 125, {
    "SCK": "MIC_SCK_D1", "WS": "MIC_WS_D0", "SD": "MIC_SD_D4",
    "L/R": "PWR:GND", "VDD": "PWR:+3V3", "GND": "PWR:GND",
})

sch.place(RING, "U4", 170, 170, {
    "DIN": "LED_DIN_D5", "VCC": "PWR:+5V", "GND": "PWR:GND",
})

sch.note("EdgeAI Predictive Monitor -- Satellite Node Wiring", 20, 25, size=3.0)
sch.note("Seeed Studio XIAO ESP32S3. Same sensor set as the base station, wired to the XIAO's 11 breakout GPIOs.", 20, 32, size=1.8)
sch.note("Report ref: Chapter 3.3 / Appendix B.2. No LED matrix on this node -- the ring alone carries status.", 20, 38, size=1.5)

open("satellite_node.kicad_sch", "w").write(sch.render())
print("wrote satellite_node.kicad_sch")
