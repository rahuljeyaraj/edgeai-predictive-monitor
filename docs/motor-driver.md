# Motor Driver: 3 Stepper Motors as Vibration Source

Goal: drive three stepper motors as controllable "rotating machines" so the
predictive-monitor sensor (MPU) can capture vibration signatures, and the
base-station pipeline can classify baseline vs. anomaly.

This doc answers:
1. Which hardware path to use (CNC Shield + Uno vs. bare drivers + ESP).
2. Whether RPM/speed control is feasible in the current setup.
3. Why ramping lives on the host, not the firmware.

See [../motor-driver/README.md](../motor-driver/README.md) for the actual
build/upload/usage instructions — this doc is the design rationale.

---

## 1. Recommendation (short version)

- **Use the Arduino Uno + CNC Shield V3 + three stepper drivers**, one per
  X/Y/Z slot. Plug-and-play, mechanically rigid, needs almost no wiring.
- **RPM control is fully doable** and needs no extra hardware — speed is
  just the step-pulse frequency generated in firmware.
- **No potentiometer.** A pot only buys a physical "knob"; for
  predictive maintenance, **serial/software speed control is better**
  because runs are scripted, repeatable, and easy to label (baseline vs.
  fault).
- **The firmware does not ramp.** It applies whatever RPM it's told
  immediately. Re-flashing the Uno needs a WSL round-trip (upload, detach,
  reconnect over serial), so the firmware is kept as small and stable as
  possible; all ramp/acceleration logic lives on the host, in
  `dashboard.html` and `motor_driver.py`. See §3.

### Decoupled, with exactly one narrow coupling
Keep the motor rig's **control** path independent of the base-station/MQTT
stack — the motors are the thing being *measured*, not part of the
monitoring system, and the control page talks directly to the Uno over Web
Serial with nothing else in the loop.

The one deliberate exception is a **one-way safety stop**: predictive
monitoring detects FAULT on a motor's sensor → the rig host commands that
motor to 0 RPM, so an AI-detected fault actually stops physical hardware.
It is stop-only — nothing on the network can start a motor or set a speed —
and it is built; see [MOTOR_STOP_PLAN.md](MOTOR_STOP_PLAN.md).

### The rig host, and why the control page isn't a static file
`motor-driver/motor_driver.py` is the process that owns the rig's *network*
side: it listens for trips and **serves the control page**. That last part is
not incidental.

It deliberately does **not** hold the Uno's serial port by default. The
control page has always driven the motors over Web Serial, and on the normal
setup here the browser is the only thing that can reach the board at all —
the rig host runs in WSL while Chrome and the USB device are on Windows.
Forwarding the device into WSL with `usbipd` doesn't resolve that, it inverts
it: Windows loses the device and the control page has nothing to connect to.

So the split is: rig host = network and bookkeeping, control page = motors.
A trip is *recorded* by the rig host and *applied* by the page. That is a
real weakening — no page open, no protection — so the page says so, in the
header while it holds the link and on the trip itself when it couldn't act.
`--port` moves the port back to the rig host for browser-less protection or
a scripted `--profile` run.

The control page is a Web Serial page. It has no MQTT client, and this
repo's broker is TCP-only (no websockets listener), so it cannot publish the
rig's retained self-announce — which is what tells the base station how many
trip outputs exist. Serving the page from the rig host makes that a
same-origin HTTP hop instead: browser → `motor_driver.py` → retained
announce. No broker change, no websockets listener, no root on the base
station.

That hop is what lets **installing a motor be a UI action**. The rig starts
with one motor and empty `+ Add Motor` slots; filling one draws the card and
re-announces, so a new trip output shows up in the base station's setup
live. The same channel carries trips back the other way, so a tripped motor
turns red on its own card instead of being visible only in the rig host's
terminal.

The page still works served statically — it just says so, and loses the
install/protection/trip features it has nobody to ask about.

---

## 2. Hardware paths

### Option A — Arduino Uno + CNC Shield V3 ✅ recommended
- Drivers (A4988 or DRV8825) plug straight into the X / Y / Z slots — one
  motor per slot, no spare.
- Single 12–24 V supply into the shield screw terminal powers all three.
- Fixed pin mapping (standard Protoneer/GRBL):

  | Signal   | X (motor 1) | Y (motor 2) | Z (motor 3) | Notes                     |
  |----------|-------------|-------------|-------------|---------------------------|
  | STEP     | D2          | D3          | D4          | one pulse = one microstep |
  | DIR      | D5          | D6          | D7          | direction                 |
  | ~ENABLE  | D8          | D8          | D8          | shared, **active LOW**    |

- **One enable line for all three drivers.** The three motors are independent
  in *speed* only; there is no way to power down one driver and leave the
  others running. "Stopping" one motor = commanding it 0 RPM. Any host must
  therefore treat `d` as a coarse power cut, not as state: `e` re-applies each
  motor's last commanded speed, so a motor that was merely disabled (rather
  than zeroed) will restart the moment some *other* motor is started.

- Microstepping set by the **MS0/MS1/MS2 jumpers** under each driver socket.
  - No jumpers = full step (loudest / most vibration, and the most for a
    vibration monitor to see).
  - All three jumpers = 1/16 (A4988) or 1/32 (DRV8825) microstep (smooth/quiet).

### Option B — Bare drivers + ESP32/ESP8266
- Only worth it if you want the motor controller on WiFi (e.g. publish the
  commanded RPM over MQTT so it lands in the same timeline as sensor data).
- Downsides: hand-wire STEP/DIR/EN/VMOT/GND per driver, add 100 µF bulk caps
  across VMOT→GND, and 3.3 V logic is fine for A4988/DRV8825 STEP/DIR.
- **Skip this for now.** Add it later only if you want commanded-RPM labels
  auto-logged alongside the vibration stream.

---

## 3. RPM / speed control — how it works

Stepper speed is not analog; you control it by how fast you emit STEP pulses.

```
step_frequency (Hz) = RPM / 60 * steps_per_rev * microsteps
```

- Typical 1.8° motor = **200 full steps/rev**.
- Example: 120 RPM, full-step → 120/60 * 200 * 1 = **400 steps/s** (2.5 ms/step).
- Example: 120 RPM, 1/16 microstep → **6400 steps/s** (156 µs/step).

So "RPM control" = "change the pulse interval," sent as a serial command.

### Running THREE motors at independent speeds
Naive `delay()` stepping blocks and can't run multiple motors at different
RPMs. The firmware uses **AccelStepper**, but only for step-pulse generation
(`setSpeed()` + `runSpeed()`) — not its acceleration machinery. Each motor
jumps straight to whatever speed it's told; nothing on-device eases into it.

### Why ramping moved to the host
Jumping straight to a high step rate can stall a motor (no torque headroom
at that speed yet from a standstill). An earlier version of this firmware
handled that in AccelStepper's own accel/decel curve — but that meant tuning
the ramp rate required a re-upload, and re-uploading this Uno is the slow
step in the whole loop (WSL connect → upload → detach → reconnect over
serial to test). So the ramp moved entirely to the two things that talk to
the firmware:
- `motor-driver/dashboard.html` walks the commanded RPM toward the dialed-in
  target every ~100 ms, at a fixed RPM/s rate.
- `motor-driver/motor_driver.py` does the same for scripted runs.

Both default to 150 RPM/s, the rate the old on-device governor used
successfully. Tune it in either place, no re-flash needed.

It also gives richer vibration signatures once more than one motor is
installed:
- Motors at the same RPM → strong single-frequency vibration.
- Slightly different RPM → **beat frequency**.
- One steady + one changing → simulates a developing fault.

---

## 4. Capture speed profile (predictive-maintenance framing)

Script the run so the captured data maps to labels the pipeline can learn/flag:

1. **Warm-up / baseline** — every installed motor steady, moderate RPM
   (e.g. 90 RPM), 30–60 s. This is "healthy machine."
2. **Speed sweep** — ramp 60 → 180 RPM slowly. Shows signature shifting with
   speed (useful for feature/robustness discussion).
3. **Injected anomaly** — introduce an obvious change: an RPM step on one
   motor while any others hold, a brief disable, or mechanical imbalance
   (tape a small weight or an eccentric mass to one shaft). This is the
   "fault" the monitor should catch.

Serial control makes these runs push-button and repeatable:
`./start_motor_driver.sh --port <port> --profile` drives exactly this
sequence over whichever motors are installed.

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

## 6. Shopping / prep list

- [ ] Arduino Uno + USB cable
- [ ] CNC Shield V3
- [ ] 3× stepper drivers seated in X/Y/Z (A4988 or DRV8825)
- [ ] 3× NEMA-17 (or similar) steppers + a rigid mount so vibration couples to
      the MPU sensor mounting surface
- [ ] 12–24 V DC supply rated for all three motors' current
- [ ] Small heatsinks on the drivers
- [ ] (optional) small eccentric/imbalance mass for the "fault" run

## 7. Open questions to confirm before building
- How is the **MPU vibration sensor** mechanically coupled to the motors?
  (Same baseplate / shared bracket so vibration actually transfers.)
- Do you want the commanded RPM **logged into the pipeline** for labeled data?
  If yes, that's the one reason to consider the ESP32 (Option B) or to log the
  Uno serial stream on the base station.
