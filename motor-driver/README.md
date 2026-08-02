# Motor Driver: the rotating machine under test

Arduino firmware + browser control page + a host-side rig controller. The Uno
is wired for three stepper motors, but **the rig starts with one installed**,
and the rest are added on the control page — which is the same order a real
factory grows in:

1. A factory sets up its first motor with its own control page —
   `dashboard.html` here, talking straight to the Uno over serial. No AI, no
   base station, no dependency on anything else in this repo.
2. Predictive monitoring gets added later, completely decoupled: the MPU
   vibration sensor watches the motor, but nothing about motor *control*
   changes. The motor's card gains a **PROTECTED** badge naming the asset
   that watches it.
3. More motors are added on the control page as the floor grows. Each one
   installed is announced to the base station as a trip output, so it can be
   mapped to a monitored asset without touching a config file — see
   [../docs/MOTOR_STOP_PLAN.md](../docs/MOTOR_STOP_PLAN.md). That coupling is
   one-way: the base station can stop a motor, never start one.

Design rationale and the capture speed profile live in
[../docs/motor-driver.md](../docs/motor-driver.md).

## Contents
- `src/main.cpp` — Arduino Uno firmware (PlatformIO project, needs
  AccelStepper). Deliberately simple: it applies whatever RPM it's told
  immediately, no ramp logic on the device — see "Why the firmware has no
  ramp" below.
- `dashboard.html` — browser control page: one card per installed motor
  (slider + type-in speed, on/off, sync) over Web Serial. Ramping lives here.
- `motor_driver.py` — the **rig host**. Serves the control page, listens for
  protection trips over MQTT, and can run a scripted capture profile. It does
  *not* hold the Uno's serial port by default — the control page does.
- `start_motor_driver.sh` — starts the rig host (use this, not
  `python3 -m http.server`).
- `fake_uno.py` — runs the rig host against a pseudo-terminal, so the control
  page can be clicked through with no hardware at all.
- `rig_trip_test.py`, `control_page_test.py` — tests; see below.

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
happens on the host** — `dashboard.html` and `motor_driver.py` both walk the
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
For anything faster or bigger, use the dashboard or `motor_driver.py` instead —
they do the ramping.

## Running the rig
```bash
cd motor-driver
./start_motor_driver.sh                          # everything defaulted
./start_motor_driver.sh --mqtt-host uno-q.local  # broker over the LAN
./start_motor_driver.sh --mqtt-host ''           # no broker at all
```

Nothing needs to be typed in the normal case. The broker defaults to
`localhost`, and the port to **11883** when something is listening there (the
`adb forward tcp:11883 tcp:1883` case, which is most of the time on this
bench) and **1883** otherwise.

> Watch for `ANNOUNCED outputs [1] on epm/motor_rig/outputs`. If the broker
> can't be reached, the rig says so after 6 seconds and tells you nothing was
> announced — because a silent non-connection leaves the base station showing
> whatever a *previous* run left retained, which looks exactly like a bug in
> the dashboard and isn't one.

> After a reboot or a replug, run [`../after_reboot.sh`](../after_reboot.sh)
> first. adb port forwards and the base station's container do not come back
> on their own, and every symptom of that looks like a network fault.

Then open **http://localhost:8000/** in desktop **Chrome or Edge** (Web Serial
isn't supported in Firefox/Safari, or inside sandboxed iframes), click
**Connect**, and pick the Uno's port.

### Who holds the serial port
By default **the control page does**, over Web Serial — and the rig host never
opens it. That is the only arrangement that works on the usual split setup:

> The rig host runs in **WSL**. Chrome and the Uno are on **Windows**.
> `usbipd`-forwarding the Uno into WSL doesn't fix it, it just moves the
> problem — Windows then loses the device and the page has nothing to
> **Connect** to.

So the rig host does the network half (serves the page, holds MQTT, records
trips) and the page does the motor half. When a trip arrives, the rig host
records it and **the page sends the stop**. The header shows `trip via this
page` while that's the arrangement, and turns red with `— not connected` when
the page isn't attached to a port, because protection genuinely is off then.

Pass `--port /dev/ttyACM0` (or `--port auto`) to have the rig host hold the
port instead — for browser-less protection, or a scripted run. `--port auto`
never picks the UNO Q base station, which is also an Arduino-VID device on
`/dev/ttyACM*`; that would write stepper commands into the monitoring board.

`start_motor_driver.sh` passes every argument through to `motor_driver.py`
(`--help` for the full list). It uses `./.venv/bin/python` when that exists,
because `pyserial`/`paho-mqtt` can't be installed system-wide on a PEP 668
host:

```bash
python3 -m venv .venv && ./.venv/bin/pip install pyserial paho-mqtt
```

> **Don't serve this page with `python3 -m http.server`.** It works — you can
> still drive motors — but the page then has nobody to ask which motors are
> installed or whether one has been tripped, and it says so in a banner.

### Motor cards
Each motor card has two independent controls:

- **Speed** — the RPM setting (slider or type-in). Stopping a motor never
  changes this, so starting it again ramps back to the same speed.
- **Run / Stop** — whether that speed is currently applied.

The card also shows a RUNNING/IDLE/STOPPED pill and the currently-commanded
RPM as it ramps toward the setting. **Sync** makes the other motors follow the
first one, and only appears once there are two; changing a follower directly
turns Sync back off. **Stop All** stops everything immediately, skipping the
ramp, and preserves the speed settings.

> Because `~ENABLE` is shared, the page only de-energizes once **all** motors
> are stopped, and it always commands a stopped motor to 0 RPM rather than
> relying on `d`.

> The port opens with a ~2 s Uno reset; wait for the `=== motor-driver ready
> ===` line in the console before driving.

### Adding and removing motors
The rig starts with **one** motor (`--motors` sets the startup set). The empty
slots are `+ Add Motor N` — clicking one installs that motor *and* re-announces
the rig's trip outputs over MQTT, so the new output appears in the base
station's setup live, with nothing restarted. `✕` removes a motor again
(disabled while it's running).

This is the one thing the rig host exists for that a static file server can't
do: the control page has no MQTT client, and the broker is TCP-only, so the
page posts to `motor_driver.py` and *it* publishes the announce.

### Protection status
With `--mqtt-host` set, the page also reads the base station's
`GET /trip_outputs` directly (its API sends `Access-Control-Allow-Origin: *`).
A motor the base station has claimed shows a **PROTECTED · &lt;asset&gt;** badge,
and the header counts how many are protected. Point it elsewhere with
`--base-station-url`; the default is `http://<mqtt-host>:8080`.

### When a trip lands
A trip turns the motor's card red, sets the pill to **TRIPPED**, locks the run
switch, and names the asset that faulted. Only **Reset & re-arm** brings it
back — a trip is sticky until a human clears it, enforced in
`Rig.motor()` and not merely promised.

## Scripted capture runs
```bash
./start_motor_driver.sh --profile --motors 1,2,3   # needs the port; autodetects it
```
Runs baseline → speed sweep → injected fault (the first installed motor steps
up, the rest are held) so the captured vibration maps to labels, then exits.
Without `--profile` the rig host just idles with the control page served and
the trip listener armed.

> Don't expect to observe a live trip during `--profile`: its fixed timing
> races the base station's variable fault-detection timing, and the profile
> has ended before the trip lands (`docs/progress4.md`).

## Tests
```bash
python3 rig_trip_test.py                                   # no hardware needed
../base-station/python/.venv/bin/python control_page_test.py   # needs playwright
```
`rig_trip_test.py` pins the trip's exact serial bytes and the runtime-editable
output set. `control_page_test.py` drives `dashboard.html` in a real headless
Chromium against a real `ControlServer` and a stand-in base station.

## Clicking through it with no hardware
`fake_uno.py` runs the rig host against a pseudo-terminal instead of a real
Uno. Everything except the motors is real — same page, same control server,
same MQTT trip listener:

```bash
python3 fake_uno.py                        # open http://localhost:8000/
python3 fake_uno.py --mqtt-host <uno-q>    # against a real base station
python3 fake_uno.py --mqtt-host <uno-q> --trip-after 20   # trip motor 1 at 20 s
```

`--trip-after` publishes the base station's exact `MOTOR_STOP` bytes, so the
trip arrives through the real listener — the way to see the tripped card and
**Reset & re-arm** without waiting for a genuine fault. Web Serial will fail
to find a port; ignore it, none of the above needs it.
