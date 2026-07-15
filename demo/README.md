# Demo: Stepper Vibration Rig

Arduino firmware that drives two stepper motors as the "rotating machine under
test" for the predictive-monitor demo. The MPU vibration sensor measures the
motors; speed (RPM) is controlled over USB serial so runs are repeatable and
easy to label (baseline vs. fault).

Design rationale and the demo speed profile live in
[../docs/demo-stepper-vibration-rig.md](../docs/demo-stepper-vibration-rig.md).

## Contents
- `stepper_rig/stepper_rig.ino` — Arduino Uno firmware (needs AccelStepper).
- `dashboard.html` — browser control panel (sliders + presets) over Web Serial.
- `run_demo.py` — host-side helper to script baseline / sweep / fault runs.

## Hardware
Arduino Uno + CNC Shield V3 + 2× A4988/DRV8825 drivers in the **X and Y** slots
(3rd driver = spare). Single 12–24 V supply into the shield terminal.

| Signal  | Motor 1 (X) | Motor 2 (Y) | Notes                |
|---------|-------------|-------------|----------------------|
| STEP    | D2          | D3          |                      |
| DIR     | D5          | D6          |                      |
| ~ENABLE | D8          | D8          | shared, active LOW   |

> Set each driver's **Vref (current limit)** before running, and never
> insert/remove a driver with power applied. See the docs file for details.

## Build & upload
1. Arduino IDE → **Library Manager** → install **AccelStepper**.
2. Open `stepper_rig/stepper_rig.ino`.
3. Board: **Arduino Uno**, select the correct serial port.
4. Confirm `MICROSTEP` in the sketch matches the **MSx jumpers** on the shield
   (default `1` = no jumpers = full step).
5. Upload.

CLI alternative (arduino-cli):
```bash
arduino-cli lib install AccelStepper
arduino-cli compile --fqbn arduino:avr:uno demo/stepper_rig
arduino-cli upload  --fqbn arduino:avr:uno -p /dev/ttyACM0 demo/stepper_rig
```

## Serial control (115200 baud, newline-terminated)
| Command     | Effect                                   |
|-------------|------------------------------------------|
| `1 <rpm>`   | set motor 1 speed (RPM)                  |
| `2 <rpm>`   | set motor 2 speed (RPM)                  |
| `b <rpm>`   | set both motors                          |
| `e` / `d`   | enable / disable (energize / coast)      |
| `s`         | print status                             |
| `h`         | print help                               |

Negative RPM reverses direction; RPM is clamped to ±300.

Example (Arduino Serial Monitor or any terminal):
```
b 90        # both at 90 RPM (baseline)
1 120       # motor 1 up to 120, motor 2 stays 90 -> beat frequency
d           # coast to silence
```

## Browser dashboard (live control)
Open `dashboard.html` in desktop **Chrome or Edge** (Web Serial isn't supported
in Firefox/Safari, or inside sandboxed iframes):

```bash
# either just double-click the file, or serve it locally:
python3 -m http.server -d demo 8000   # then visit http://localhost:8000/dashboard.html
```

Click **Connect**, pick the Uno's port, then use the per-motor sliders,
quick-set buttons, and demo presets (Baseline / Beat / Fault). A live serial
console shows what's sent and the Uno's status replies. **Stop** disables the
drivers instantly. Closing/disconnecting auto-sends `d` so motors coast down.

> The port opens with a ~2 s Uno reset; wait for the `=== stepper_rig ready ===`
> line in the console before driving.

## Scripted runs
```bash
python3 demo/run_demo.py --port /dev/ttyACM0
```
Runs baseline → speed sweep → injected fault so the captured vibration maps to
labels. See `run_demo.py --help`.
