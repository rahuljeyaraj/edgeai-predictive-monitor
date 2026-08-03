#!/usr/bin/env python3
from gen import Part, Schematic

sch = Schematic("EdgeAI Predictive Monitor - Base Station Wiring (Arduino UNO Q)")

# Pins are named the way the UNO Q itself names them -- the header labels
# printed on the board (D0-D13, A0-A5, SDA/SCL), not the STM32U585 function
# or port name. The port each one lands on is in the comment below and in
# REPORT.md Appendix B.1; what you need at wiring time is the header label.
# Mapping is from the board's own gpio-map (zephyr/boards/arduino/uno_q/
# arduino_r3_connector.dtsi): D13=PB13, D12=PB14, D11=PB15, D8=PB4, D9=PB8,
# D10=PB9, D3=PB0, A4=PC1. The mic's bit clock is the odd one out -- SAI1_A
# SCK is PB10, which this board brings out as the dedicated SCL header pin,
# not as a D-number (I2C2 is disabled to free it, see the MCU overlay).
UNOQ = Part(
    "epm:UNOQ", "U", "Arduino UNO Q (STM32U585 side)",
    right=["D13", "D12", "D11", "D8", "D9", "SCL", "D10", "A4",
           "D3", "5V", "3V3", "GND"],
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
    "D13": "SPI_SCK", "D12": "SPI_MISO", "D11": "SPI_MOSI",
    "D8": "ACC_CS", "D9": "ACC_INT",
    "SCL": "MIC_SCK", "D10": "MIC_WS", "A4": "MIC_SD",
    "D3": "LED_DIN",
    "5V": "PWR:+5V", "3V3": "PWR:+3V3", "GND": "PWR:GND",
})

sch.place(KX134, "U2", 170, 70, {
    "SCK": "SPI_SCK", "SDO": "SPI_MISO", "SDI": "SPI_MOSI",
    "CS": "ACC_CS", "INT1": "ACC_INT",
    "VCC": "PWR:+3V3", "GND": "PWR:GND",
})

sch.place(INMP441, "U3", 170, 125, {
    "SCK": "MIC_SCK", "WS": "MIC_WS", "SD": "MIC_SD",
    "L/R": "PWR:GND", "VDD": "PWR:+3V3", "GND": "PWR:GND",
})

sch.place(RING, "U4", 170, 170, {
    "DIN": "LED_DIN", "VCC": "PWR:+5V", "GND": "PWR:GND",
})

sch.note("EdgeAI Predictive Monitor -- Base Station Wiring", 20, 25, size=3.0)
sch.note("Arduino UNO Q, STM32U585 (Zephyr) side. SPI bus + digital I2S + WS2812 status ring.", 20, 32, size=1.8)
sch.note("Report ref: Chapter 2.3 / Appendix B.1. UNO Q pins are named by its own header labels (D3, D13, A4, SCL) -- not by STM32 port.", 20, 38, size=1.5)

open("base_station.kicad_sch", "w").write(sch.render())
print("wrote base_station.kicad_sch")
