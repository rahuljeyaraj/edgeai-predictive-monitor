#!/usr/bin/env python3
from gen import Part, Schematic

sch = Schematic("EdgeAI Predictive Monitor - Base Station Wiring (Arduino UNO Q)")

UNOQ = Part(
    "epm:UNOQ", "U", "Arduino UNO Q (STM32U585 side)",
    right=["SCK", "MISO", "MOSI", "CS", "INT", "I2S_CLK", "I2S_WS", "I2S_SD",
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

sch.place(UNOQ, "U1", 60, 100, {
    "SCK": "SPI_SCK_D13", "MISO": "SPI_MISO_D12", "MOSI": "SPI_MOSI_D11",
    "CS": "ACC_CS_D8", "INT": "ACC_INT_D9",
    "I2S_CLK": "MIC_SCK_PB10", "I2S_WS": "MIC_WS_PB9", "I2S_SD": "MIC_SD_PC1",
    "LED_DIN": "LED_DIN_PB0",
    "5V": "PWR:+5V", "3V3": "PWR:+3V3", "GND": "PWR:GND",
})

sch.place(KX134, "U2", 170, 70, {
    "SCK": "SPI_SCK_D13", "SDO": "SPI_MISO_D12", "SDI": "SPI_MOSI_D11",
    "CS": "ACC_CS_D8", "INT1": "ACC_INT_D9",
    "VCC": "PWR:+3V3", "GND": "PWR:GND",
})

sch.place(INMP441, "U3", 170, 125, {
    "SCK": "MIC_SCK_PB10", "WS": "MIC_WS_PB9", "SD": "MIC_SD_PC1",
    "L/R": "PWR:GND", "VDD": "PWR:+3V3", "GND": "PWR:GND",
})

sch.place(RING, "U4", 170, 170, {
    "DIN": "LED_DIN_PB0", "VCC": "PWR:+5V", "GND": "PWR:GND",
})

sch.note("EdgeAI Predictive Monitor -- Base Station Wiring", 20, 25, size=3.0)
sch.note("Arduino UNO Q, STM32U585 (Zephyr) side. SPI bus + digital I2S + WS2812 status ring.", 20, 32, size=1.8)
sch.note("Report ref: Chapter 2.3 / Appendix B.1. Net labels carry the header pin used on the UNO Q main header.", 20, 38, size=1.5)

open("base_station.kicad_sch", "w").write(sch.render())
print("wrote base_station.kicad_sch")
