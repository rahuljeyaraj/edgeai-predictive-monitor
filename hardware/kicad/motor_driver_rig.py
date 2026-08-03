#!/usr/bin/env python3
from gen import Part, Schematic

sch = Schematic("EdgeAI Predictive Monitor - Motor-Driver Rig Wiring (Arduino Uno + CNC Shield V3)")

# Pins are named the way the Arduino Uno underneath names them (D2-D8) --
# the CNC Shield's own X/Y/Z STEP+DIR silkscreen is just a relabelling of
# these header pins, and the header number is what you probe. Assignment is
# from motor-driver/src/main.cpp: EN=8, X=2/5, Y=3/6, Z=4/7.
HUB = Part(
    "epm:ARDUINO_UNO_CNC", "U", "Arduino Uno + CNC Shield V3",
    right=["D8", "D2", "D5", "D3", "D6", "D4", "D7", "GND"],
    width=45.72,
)
DRIVER = Part(
    "epm:STEPPER_DRIVER", "A", "A4988 / DRV8825 stepper driver",
    left=["STEP", "DIR", "EN", "GND"],
    right=["VMOT", "1A", "1B", "2A", "2B"],
    width=33.02,
)
MOTOR = Part(
    "epm:NEMA17", "M", "NEMA-17 stepper motor",
    left=["A1", "A2", "B1", "B2"],
    width=25.4,
)
PSU = Part(
    "epm:PSU_DC", "PS", "12-24V DC power supply",
    right=["V+", "GND"],
    width=25.4,
)

sch.place(HUB, "U1", 60, 100, {
    "D8": "ENABLE_D8", "GND": "PWR:GND",
    "D2": "M1_STEP_D2", "D5": "M1_DIR_D5",
    "D3": "M2_STEP_D3", "D6": "M2_DIR_D6",
    "D4": "M3_STEP_D4", "D7": "M3_DIR_D7",
})

motor_y = [70, 125, 180]
for idx, y in zip((1, 2, 3), motor_y):
    ref_d = chr(ord("A") + idx - 1)
    sch.place(DRIVER, f"A{idx}", 175, y, {
        "STEP": f"M{idx}_STEP_D{ {1:2,2:3,3:4}[idx] }",
        "DIR": f"M{idx}_DIR_D{ {1:5,2:6,3:7}[idx] }",
        "EN": "ENABLE_D8",
        "GND": "PWR:GND",
        "VMOT": "VMOT",
        "1A": f"M{idx}_A1", "1B": f"M{idx}_A2",
        "2A": f"M{idx}_B1", "2B": f"M{idx}_B2",
    })
    sch.place(MOTOR, f"M{idx}", 260, y, {
        "A1": f"M{idx}_A1", "A2": f"M{idx}_A2",
        "B1": f"M{idx}_B1", "B2": f"M{idx}_B2",
    })

sch.place(PSU, "PS1", 60, 210, {
    "V+": "VMOT", "GND": "PWR:GND",
})

sch.note("EdgeAI Predictive Monitor -- Motor-Driver Rig Wiring", 20, 25, size=3.0)
sch.note("Arduino Uno + CNC Shield V3, one driver socket per motor axis. Shared ~ENABLE line on D8, active-LOW. Uno pins named by header number.", 20, 32, size=1.8)
sch.note("Report ref: Chapter 5 / Appendix B.3. Set each driver's current-limit trimpot before running: A4988 Vref = Imax x 8 x Rsense, DRV8825 Vref = Imax / 2.", 20, 38, size=1.5)

open("motor_driver_rig.kicad_sch", "w").write(sch.render())
print("wrote motor_driver_rig.kicad_sch")
