# Motor Driver: 3-Motor Industrial Control Rig

Arduino firmware + browser dashboard that drives three stepper motors as the
"rotating machine under test" for the predictive-monitor demo. This mirrors a
real factory's story:

1. A factory sets up its first motor with its own control page —
   `dashboard.html` here, talking straight to the Uno over serial. No AI, no
   base station, no dependency on anything else in this repo.
2. Predictive monitoring gets added later, completely decoupled: the MPU
   vibration sensor watches the motors, but nothing about motor *control*
   changes.
3. As more motors/satellite nodes get added, the base station gets an
   optional path to physically stop a motor on FAULT — see
   [../docs/MOTOR_STOP_PLAN.md](../docs/MOTOR_STOP_PLAN.md). That coupling is
   one-way (stop only) and still hasn't been built; today this folder is
   still the standalone control rig from step 1–2.

Design rationale and the demo speed profile live in
[../docs/motor-driver.md](../docs/motor-driver.md).

## Contents
- `src/main.cpp` — Arduino Uno firmware (PlatformIO project, needs
  AccelStepper). Deliberately simple: it applies whatever RPM it's told
  immediately, no ramp logic on the device — see "Why the firmware has no
  ramp" below.
- `dashboard.html` — browser control panel for all 3 motors (slider +
  type-in speed, on/off, sync) over Web Serial. Ramping lives here.
- `run_demo.py` — host-side helper to script baseline / sweep / fault runs.
  Ramping lives here too, for the same reason.

## Hardware
Arduino Uno + CNC Shield V3 + 3× A4988/DRV8825 drivers in the **X, Y, and Z**
slots. Single 12–24 V supply into the shield terminal.

| Signal  | Motor 1 (X) | Motor 2 (Y) | Motor 3 (Z) | Notes              |
|---------|-------------|-------------|-------------|--------------------|
| STEP    | D2          | D3          | D4          |                    |
| DIR     | D5          | D6          | D7          |                    |
| ~ENABLE | D8          | D8          | D8          | shared, active LOW |

> **There is no per-motor enable.** One `~ENABLE` line feeds all three driver
> sockets, so `e`/`d` energize or coast *everything*. Stopping a single motor
> means commanding it to 0 RPM. This matters: `e` re-applies whatever speed
> each motor was last told, so anything that stops a motor must send `N 0`
> for it — sending only `d` leaves a stale speed armed, and the next `e`
> (triggered by starting some *other* motor) will spin it back up.

> Set each driver's **Vref (current limit)** before running, and never
> insert/remove a driver with power applied. See the docs file for details.

## Why the firmware has no ramp
Re-flashing this Uno means connecting through WSL, uploading, then
disconnecting again to test over serial — slow enough that firmware changes
should be rare. So `src/main.cpp` only does the minimum: parse a command, set
a motor's speed immediately (`setSpeed()`/`runSpeed()`, no acceleration
curve). Jumping straight to a high RPM can stall a motor, so **all ramping
happens on the host** — `dashboard.html` and `run_demo.py` both walk the
commanded speed toward the target in small steps before sending it. Tuning or
fixing ramp behavior is now a dashboard/script edit, not a re-upload.

## Build & upload (PlatformIO)
```bash
cd motor-driver
pio run                 # compile-check
pio run -t upload       # flash the Uno (pick the right --upload-port if prompted)
pio device monitor -b 115200   # optional: watch the serial console
```
Confirm `MICROSTEP` in `src/main.cpp` matches the **MSx jumpers** on the
shield (default `1` = no jumpers = full step) before uploading.

## Serial control (115200 baud, newline-terminated)
| Command     | Effect                                       | Replies |
|-------------|----------------------------------------------|---------|
| `1 <rpm>`   | set motor 1 speed (RPM), applied immediately  | silent  |
| `2 <rpm>`   | set motor 2 speed (RPM), applied immediately  | silent  |
| `3 <rpm>`   | set motor 3 speed (RPM), applied immediately  | silent  |
| `b <rpm>`   | set ALL THREE motors, applied immediately     | silent  |
| `e` / `d`   | energize / coast **all three** drivers        | status  |
| `s`         | print status                                  | status  |
| `h`         | print help                                    | help    |

Speed commands answer with nothing on purpose. A status line overflows the
Uno's 64-byte serial TX buffer, which makes `Serial.print()` block and stalls
the step-pulse loop for *all three* motors — during a host ramp that's ~30
stalls/second, and it was audible as every motor stuttering whenever any one
of them changed speed. Poll `s` when you want state.

Negative RPM reverses direction; RPM is clamped to ±1200 (full step).

Example (Arduino Serial Monitor or any terminal) — note the firmware won't
ramp for you, so jumping straight to a high RPM this way can stall a motor:
```
1 60        # motor 1 to 60 RPM (small enough to not need a ramp)
d           # coast to silence
```
For anything faster or bigger, use the dashboard or `run_demo.py` instead —
they do the ramping.

## Browser dashboard (live control)
Open `dashboard.html` in desktop **Chrome or Edge** (Web Serial isn't
supported in Firefox/Safari, or inside sandboxed iframes):

```bash
# either just double-click the file, or serve it locally:
python3 -m http.server -d motor-driver 8000   # then visit http://localhost:8000/dashboard.html
```

Click **Connect** and pick the Uno's port. Each motor card has two independent
controls:

- **Speed** — the RPM setting (slider or type-in). Stopping a motor never
  changes this, so starting it again ramps back to the same speed.
- **Run / Stop** — whether that speed is currently applied.

The card also shows a RUNNING/IDLE/STOPPED pill and the currently-commanded
RPM as it ramps toward the setting. **Sync** makes motors 2 and 3 follow motor
1; changing 2 or 3 directly turns Sync back off. **Stop All** stops all three
immediately, skipping the ramp, and preserves their speed settings.

> Because `~ENABLE` is shared, the dashboard only de-energizes once **all**
> motors are stopped, and it always commands a stopped motor to 0 RPM rather
> than relying on `d`.

> The port opens with a ~2 s Uno reset; wait for the `=== motor-driver ready
> ===` line in the console before driving.

## Scripted runs
```bash
python3 motor-driver/run_demo.py --port /dev/ttyACM0
```
Runs baseline → speed sweep → injected fault (motor 1 steps up, motors 2/3
held) so the captured vibration maps to labels. See `run_demo.py --help`.
