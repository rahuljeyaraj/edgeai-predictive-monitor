# Demo Rig: Stepper Motors as Vibration Source

Goal: drive two stepper motors as a controllable "rotating machine" so the
predictive-monitor sensor (MPU) can capture vibration signatures, and the
base-station pipeline can classify baseline vs. anomaly.

This doc answers three things:
1. Which hardware path to use (CNC Shield + Uno vs. bare drivers + ESP).
2. Whether RPM/speed control is feasible in the current setup.
3. Whether a potentiometer is needed for speed control.

---

## 1. Recommendation (short version)

- **Use the Arduino Uno + CNC Shield V3 + two stepper drivers.** It is the
  plug-and-play path, mechanically rigid (drivers seat directly on the shield),
  and needs almost no wiring. Keep the third driver as a spare.
- **RPM control is fully doable** and does not require any extra hardware —
  speed is just the step-pulse frequency you generate in firmware.
- **You do NOT need a pot.** A pot is optional and only buys you a physical
  "knob" for live demos. For a predictive-maintenance demo, **serial/software
  speed control is better** because you can script repeatable speed profiles
  (baseline run → fault run) and label the captured data.
- Add a pot **only if** you want an interactive "turn the knob, watch the
  vibration change" moment on stage.

Keep the motor rig **independent** of the base-station/MQTT stack. The steppers
are the thing being *measured*, not part of the monitoring system. Don't couple
them — it just adds failure points during a live demo.

---

## 2. Hardware paths

### Option A — Arduino Uno + CNC Shield V3  ✅ recommended
- Drivers (A4988 or DRV8825) plug straight into the X / Y / Z slots.
- Use **X and Y slots** for the two motors; leave Z empty (spare driver).
- Single 12–24 V supply into the shield screw terminal powers both motors.
- Fixed pin mapping (GRBL-style), so firmware is trivial:

  | Signal   | X (motor 1) | Y (motor 2) | Notes                     |
  |----------|-------------|-------------|---------------------------|
  | STEP     | D2          | D3          | one pulse = one microstep |
  | DIR      | D5          | D6          | direction                 |
  | ~ENABLE  | D8          | D8          | shared, **active LOW**    |

- Microstepping set by the **MS0/MS1/MS2 jumpers** under each driver socket.
  - No jumpers = full step (loudest / most vibration, good for a demo).
  - All three jumpers = 1/16 (A4988) or 1/32 (DRV8825) microstep (smooth/quiet).

### Option B — Bare drivers + ESP32/ESP8266
- Only worth it if you want the motor controller on WiFi (e.g. publish the
  commanded RPM over MQTT so it lands in the same timeline as sensor data).
- Downsides: hand-wire STEP/DIR/EN/VMOT/GND per driver, add 100 µF bulk caps
  across VMOT→GND, and 3.3 V logic is fine for A4988/DRV8825 STEP/DIR.
- **Skip this for the first demo.** Add it later only if you want commanded-RPM
  labels auto-logged alongside the vibration stream.

---

## 3. RPM / speed control — how it works

Stepper speed is not analog; you control it by how fast you emit STEP pulses.

```
step_frequency (Hz) = RPM / 60 * steps_per_rev * microsteps
```

- Typical 1.8° motor = **200 full steps/rev**.
- Example: 120 RPM, full-step → 120/60 * 200 * 1 = **400 steps/s** (2.5 ms/step).
- Example: 120 RPM, 1/16 microstep → **6400 steps/s** (156 µs/step).

So "RPM control" = "change the pulse interval." Three ways to set it:

| Method              | Extra HW | Best for                                   |
|---------------------|----------|--------------------------------------------|
| Hard-coded speed    | none     | quickest bring-up / smoke test             |
| **Serial command**  | none     | **scripted, repeatable, labeled demo runs**|
| Potentiometer (A0)  | 1 pot    | interactive live "knob" demo               |

### Running TWO motors at independent speeds
Naive `delay()` stepping blocks and can't run two motors at different RPMs.
Use the **AccelStepper** library (non-blocking, handles multiple motors and
accel ramps). One `setSpeed()` per motor, call `runSpeed()` in the loop.

This also lets you create richer vibration signatures for the demo:
- Both motors same RPM → strong single-frequency vibration.
- Slightly different RPM → **beat frequency** (great "interesting signal" demo).
- One steady + one changing → simulate a developing fault.

---

## 4. Demo speed profile (predictive-maintenance framing)

Script the run so the captured data maps to labels the pipeline can learn/flag:

1. **Warm-up / baseline** — both motors steady, moderate RPM (e.g. 90 RPM),
   30–60 s. This is "healthy machine."
2. **Speed sweep** — ramp 60 → 180 RPM slowly. Shows signature shifting with
   speed (useful for feature/robustness discussion).
3. **Injected anomaly** — introduce an obvious change: sudden RPM step, one
   motor stall/brief disable, or add mechanical imbalance (tape a small weight
   or an eccentric mass to one shaft). This is the "fault" the monitor should
   catch.

Serial control makes these runs push-button and repeatable; a pot does not.

---

## 5. Bring-up checklist (Option A)

1. **Set driver current limit (Vref)** BEFORE running — turn the tiny pot on
   each A4988/DRV8825. Under-current = missed steps; over-current = overheating.
   - A4988: `Vref ≈ Imax * 8 * Rsense` (Rsense often 0.1 Ω → Vref ≈ 0.8·Imax).
   - DRV8825: `Vref ≈ Imax / 2`.
   - Start low (~0.4–0.6 V), confirm motion, raise only if it stalls.
2. Insert drivers **correct orientation** (EN/GND pin marking lines up). Wrong
   way = instant driver death.
3. Motor coil pairs: A4988/DRV8825 expect **1A/1B, 2A/2B**. If a motor buzzes
   but won't turn, swap one coil pair.
4. Power the shield from **12–24 V** (NOT just USB — USB only powers logic).
   Bulk cap across the terminal is recommended; the shield usually has one.
5. Never insert/remove a driver with power on.
6. Pull **~ENABLE (D8) LOW** to energize the drivers.

---

## 6. Reference firmware sketch (Uno + CNC Shield + AccelStepper)

Two motors, optional pot on A0. Remove the pot block for pure software/serial.

```cpp
#include <AccelStepper.h>

// CNC Shield V3 pin map
#define EN_PIN   8            // shared, active LOW
AccelStepper m1(AccelStepper::DRIVER, 2, 5);   // X: STEP=D2, DIR=D5
AccelStepper m2(AccelStepper::DRIVER, 3, 6);   // Y: STEP=D3, DIR=D6

const int   STEPS_PER_REV = 200;   // 1.8° motor, full step
const int   MICROSTEP     = 1;     // match your MS jumpers
const float RPM1_DEFAULT  = 90.0;
const float RPM2_DEFAULT  = 90.0;

float rpmToSps(float rpm) {         // RPM -> steps per second
  return rpm / 60.0 * STEPS_PER_REV * MICROSTEP;
}

void setup() {
  Serial.begin(115200);
  pinMode(EN_PIN, OUTPUT);
  digitalWrite(EN_PIN, LOW);        // enable drivers
  m1.setMaxSpeed(4000);
  m2.setMaxSpeed(4000);
  m1.setSpeed(rpmToSps(RPM1_DEFAULT));
  m2.setSpeed(rpmToSps(RPM2_DEFAULT));
  Serial.println("Send: '1 120' or '2 60' to set motor RPM");
}

void loop() {
  // --- Serial speed control: "<motor> <rpm>" ---
  if (Serial.available()) {
    int motor = Serial.parseInt();
    float rpm = Serial.parseFloat();
    if (motor == 1) m1.setSpeed(rpmToSps(rpm));
    if (motor == 2) m2.setSpeed(rpmToSps(rpm));
    Serial.print("m"); Serial.print(motor);
    Serial.print(" -> "); Serial.print(rpm); Serial.println(" RPM");
  }

  // --- Optional pot on A0 controls motor 1 (0..180 RPM) ---
  // float rpm = analogRead(A0) / 1023.0 * 180.0;
  // m1.setSpeed(rpmToSps(rpm));

  m1.runSpeed();
  m2.runSpeed();
}
```

Install AccelStepper via Arduino IDE Library Manager (search "AccelStepper").

---

## 7. Shopping / prep list

- [ ] Arduino Uno + USB cable
- [ ] CNC Shield V3
- [ ] 2× stepper drivers seated in X/Y (A4988 or DRV8825) — 3rd = spare
- [ ] 2× NEMA-17 (or similar) steppers + a rigid mount so vibration couples to
      the MPU sensor mounting surface
- [ ] 12–24 V DC supply rated for both motors' current
- [ ] Small heatsinks on the drivers
- [ ] (optional) 10 kΩ pot for live knob control
- [ ] (optional) small eccentric/imbalance mass for the "fault" run

## 8. Open questions to confirm before building
- How is the **MPU vibration sensor** mechanically coupled to the motors?
  (Same baseplate / shared bracket so vibration actually transfers.)
- Do you want the commanded RPM **logged into the pipeline** for labeled data?
  If yes, that's the one reason to consider the ESP32 (Option B) or to log the
  Uno serial stream on the base station.
