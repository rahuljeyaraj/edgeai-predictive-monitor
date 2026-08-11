# Satellite Node Bring-Up Guide (XIAO ESP32-S3 → Desktop Dashboard)

A step-by-step guide for bringing up a **satellite node** on a Seeed Studio
XIAO ESP32-S3, talking to the dashboard running **on your desktop** — no UNO Q
in the picture. Written for someone who has never seen this repo before.

You'll bring the board up **one module at a time**: boot → WiFi/MQTT link →
status ring → microphone → accelerometer → both together. Each stage tells
you exactly what to flash, what to watch in the serial log, and what to check
on the dashboard.

---

## 0. The 30-second picture

Normally a satellite node talks to the **UNO Q** base station over WiFi/MQTT.
For bring-up we cut the UNO Q out entirely and let your **desktop play the base
station's role**: the desktop runs the MQTT broker *and* the dashboard. The
ESP32 just needs to join the same network and publish to that broker.

```
  ┌───────────────────┐        WiFi / MQTT         ┌──────────────────────────┐
  │  XIAO ESP32-S3     │  ─────────────────────────▶│  Your desktop            │
  │  (satellite node)  │   epm/<node_id>/data       │                          │
  │                    │                            │  • Mosquitto broker :1883│
  │  KX134 accel (SPI) │◀───────────────────────────│  • Dashboard  :8180      │
  │  INMP441 mic (I2S) │   epm/<node_id>/cmd         │    (base-station/python) │
  │  WS2812 ring       │   (STATUS_LED)             │                          │
  └───────────────────┘                            └──────────────────────────┘
```

- **`epm/<node_id>/data`** — node → desktop. The binary telemetry frame: one
  mic spectrum, three accelerometer axis spectra (X/Y/Z), and three
  bearing-envelope spectra derived from those same axes, each followed by its
  own six-scalar set (`rms`/`kurtosis`/`crest_factor`/`peak`/`std`/`skewness`).
  This is what makes the node appear on the dashboard.
- **`epm/<node_id>/cmd`** — desktop → node. `STATUS_LED` commands that drive the
  WS2812 ring color.
- **`node_id`** — the last 6 hex digits of the board's WiFi MAC, lowercase
  (e.g. `a1b2c3`). Automatic — no per-board config. You'll read it off the
  serial log.

---

## 1. What you need

**Hardware**

- Seeed Studio XIAO ESP32-S3
- KX134-1211 accelerometer breakout
- INMP441 I2S MEMS microphone
- WS2812B 8-pixel ring (same ring the base station uses)
- USB-C cable (data, not charge-only)
- Jumper wires

**Software**

- A desktop on the **same WiFi/LAN** as the board will join (Linux assumed
  below; the repo's own tooling targets Linux).
- [PlatformIO](https://platformio.org/) — either the VS Code extension or the
  `pio` CLI. Install the CLI with: `pip install -U platformio`. The firmware
  targets the ESP-IDF framework (not Arduino) — PlatformIO downloads the
  ESP-IDF toolchain automatically on first build; expect a larger first
  `pio run` than an Arduino project.
- `mosquitto` + `mosquitto-clients` (the MQTT broker + test tools)

---

## 2. Wiring (do this once)

Pin map for the XIAO ESP32-S3. Full rationale is in
[docs/satellite/PIN_ALLOCATION.md](satellite/PIN_ALLOCATION.md).

| Signal | XIAO pin | GPIO | Notes |
|---|---|---|---|
| INMP441 SCK (BCLK) | **D1** | 2 | I2S clock |
| INMP441 WS (LRCLK) | **D2** | 3 | I2S word-select |
| INMP441 SD (data out) | **D3** | 4 | mic → MCU |
| WS2812 ring DIN | **D5** | 6 | 8-pixel ring |
| KX134 CS | **D6** | 43 | SPI chip-select, active LOW |
| KX134 INT1 | **D7** | 44 | wired but not currently read by firmware |
| KX134 SPI SCLK | **D8** | 7 | 10 MHz |
| KX134 SPI MISO | **D9** | 8 | |
| KX134 SPI MOSI | **D10** | 9 | |

> ⚠️ **Power is 3.3 V, not 5 V.** Feed both peripherals and the ring from the
> XIAO's **3V3** pin and share **GND**. The WS2812 ring runs fine off 3V3 at 8
> pixels — no level shifting needed anywhere on this board.

> ℹ️ GPIO43/44 (D6/D7) double as UART0 TX/RX on this board, but that's safe:
> the debug console runs over USB-JTAG (`esp-builtin`), not physical UART0
> pins, so those two GPIOs are free for the KX134's CS/INT1.

> ℹ️ There is no separate onboard "heartbeat" LED used by this firmware — the
> WS2812 ring itself is your first sign of life (Stage 1 below).

---

## 3. Codebase tour (what you're flashing)

Everything for the node lives under [satellite/](../satellite/). It's a real
ESP-IDF (C, not Arduino/C++) project. Full detail in
[satellite/README.md](../satellite/README.md).

**WiFi credentials are not compiled in.** The node stores them at runtime in
NVS flash, seeded one of two ways:

- **Dev-bench shortcut (what this guide uses)** — drop a gitignored
  `src/wifi_creds.h` next to
  [satellite/src/epm_config.h](../satellite/src/epm_config.h) defining
  `WIFI_SSID`/`WIFI_PASS`; `epm_config.h` pulls it in automatically
  (`#if __has_include("wifi_creds.h")`) and the node seeds NVS with it on its
  first boot. A header, not a build flag, specifically so a password
  containing spaces or symbols doesn't need shell-escaping.
- **The real captive portal** — no `wifi_creds.h` at all: on first boot (or
  whenever NVS has no saved credentials) the node opens its own AP,
  `EPM-SAT-<node_id>`, with a form at `http://192.168.4.1`. See §5a.

**The MQTT broker address is compile-time only**, via
`EPM_MQTT_BROKER_HOST` / `EPM_MQTT_BROKER_PORT` `build_flags` in
[satellite/platformio.ini](../satellite/platformio.ini) (default
`10.42.0.1:1883` — the UNO Q's own address, not your desktop's). The captive
portal's form also has broker host/port fields, and submitted values persist
to NVS alongside the WiFi credentials — but nothing in the runtime MQTT-connect
path reads them back (`components/epm_drivers/link_mqtt.c` always uses the
compile-time macros). Treat the portal's broker fields as inert for now; for
bring-up you must point the board at your desktop with a `build_flags`
override instead (§5a).

**There are no per-sensor compile-time enable flags.** Unlike the stub this
guide used to describe, mic and accelerometer sampling both always run — the
firmware has no `MIC_SENSOR_ENABLED`/`ACCEL_SENSOR_ENABLED`-style switches, and
a missing or broken sensor **does not halt boot**. `imu_task_start()`'s own
comment states this explicitly: a failed accelerometer init is "non-fatal...
the task still starts", logs the error, and keeps retrying — mic and net
follow the same convention. Practically: bring sensors up in stages by wiring
them incrementally and watching the log, not by flipping a build-time switch.

**The firmware = 6 FreeRTOS tasks**, started in this order by
[satellite/src/main.c](../satellite/src/main.c) (full boot-order rationale is
in that file's own header comment):

1. **led_task** (core 1, priority 3) — drives the WS2812 ring from local state
   changes and `STATUS_LED` commands.
   → [led_task.c/h](../satellite/src/threads/led_task.c), [display_neopixel.c](../satellite/components/epm_drivers/display_neopixel.c)
2. WiFi RF is brought up next (`wifi_rf_init()`) — event-driven, not a task of
   its own — followed by **wifi_provision_task** (core 0, priority 2), which
   owns the join/recover/re-provision state machine.
   → [wifi_task.c/h](../satellite/src/threads/wifi_task.c), [wifi_provision_task.c/h](../satellite/src/threads/wifi_provision_task.c), [provisioning.c](../satellite/components/epm_drivers/provisioning.c)
3. **mic_task** (core 0, priority 5) — reads the INMP441 over I2S DMA, computes
   time-domain stats.
   → [mic_task.c/h](../satellite/src/threads/mic_task.c), [mic_inmp441_i2s.c](../satellite/components/epm_drivers/mic_inmp441_i2s.c)
4. **dsp_task** (core 1, priority 6) — Welch-averaged FFT of the mic stream.
   → [dsp_task.c/h](../satellite/src/threads/dsp_task.c)
5. **imu_task** (core 0, priority 3) — reads the KX134 over SPI, FFTs and
   envelope-demodulates each of the 3 axes.
   → [imu_task.c/h](../satellite/src/threads/imu_task.c), [accel_kx134_spi.c](../satellite/components/epm_drivers/accel_kx134_spi.c)
6. **net_task** (core 0, priority 4) — every 200 ms, builds one telemetry frame
   from the latest mic + accel data and publishes it over MQTT; also handles
   `STATUS_LED` commands.
   → [net_task.c/h](../satellite/src/threads/net_task.c), [link_mqtt.c](../satellite/components/epm_drivers/link_mqtt.c)

Plus **diagnostics_task** (core 0, priority 1), started last once every other
task's handle exists — every 30 s it logs stack high-water marks, heap free
(internal/largest-free/PSRAM/IRAM), and per-module counters (WiFi
connects/disconnects, MQTT connects/publishes/failures, mic/accel/DSP
error counts). It also carries a self-heal watchdog: 10 consecutive MQTT
reconnect failures triggers `esp_restart()` — measured real self-heal time is
~152s (`ADR-036`), not just a calculated retry-count × interval estimate —
see
[docs/satellite/decisions/ADR-036-mqtt-reconnect-watchdog.md](satellite/decisions/ADR-036-mqtt-reconnect-watchdog.md).
This is your primary "is it healthy" tool once everything is running (Stage 6).

---

## 4. Desktop setup (the "base station" side)

Do this once on the desktop. Everything here runs from the repo root.

### 4a. Install and configure the MQTT broker

```sh
sudo apt-get install -y mosquitto mosquitto-clients
```

By default Mosquitto only listens on `localhost`, which the ESP32 can't reach.
Open it to the LAN with a small config file:

```sh
sudo tee /etc/mosquitto/conf.d/epm.conf >/dev/null <<'EOF'
listener 1883 0.0.0.0
allow_anonymous true
EOF
sudo systemctl restart mosquitto
```

> ⚠️ This makes the broker reachable by anything on your network. Fine for a lab
> LAN; don't do it on an untrusted network.

If you run a firewall, allow the port: `sudo ufw allow 1883/tcp`

### 4b. Find the desktop's IP (the board needs it)

```sh
hostname -I | awk '{print $1}'
```

Write this down — it goes into the `EPM_MQTT_BROKER_HOST` build flag in
Section 5. Example: `192.168.1.50`.

### 4c. Start the dashboard

```sh
cd base-station
./start_desktop_dashboard.sh
```

This sets up a Python venv, checks the broker is up, and launches the dashboard
at **http://localhost:8180**. It also starts **one simulated node** — that's on
purpose: it's a known-good reference so you can confirm the dashboard works
*before* your real board is talking. Leave it running; Ctrl+C stops everything.

> The `base_station` node will never appear (that's the UNO Q's own sensors,
> which aren't here). Only MQTT satellite nodes show up. Expected.

**✅ Checkpoint:** Open http://localhost:8180 — you should see the simulated
satellite node. In another terminal, watch the raw MQTT traffic:

```sh
mosquitto_sub -h localhost -t 'epm/#' -v
```

You'll see frames flowing from the sim node's topic. This is your ground truth
for the rest of the guide — when your real board publishes, it shows up here too.

---

## 5. Firmware setup (dev machine)

### 5a. Point the board at your desktop

**WiFi credentials** — create `satellite/src/wifi_creds.h` (gitignored, never
committed):

```c
#define WIFI_SSID  "YourWiFiName"
#define WIFI_PASS  "YourWiFiPassword"
```

This seeds NVS on the node's first boot after flashing and is never touched
again unless you erase flash (§5b).

**MQTT broker** — add a `build_flags` entry to
[satellite/platformio.ini](../satellite/platformio.ini) pointing at your
desktop's IP from §4b:

```ini
build_flags =
    -DFFT_MIC_N=1024
    -DFFT_IMU_N=2048
    -DSPEC_AVG_N=8
    -DEPM_MQTT_BROKER_HOST='"192.168.1.50"'
    -fno-ira-loop-pressure
```

This is the only way to point a bring-up board at your desktop — the captive
portal's own broker-address field does not currently take effect (§3). Don't
commit your real WiFi credentials or LAN IP if you're sharing this checkout;
`wifi_creds.h` is already gitignored, but a `platformio.ini` edit is not — undo
the `EPM_MQTT_BROKER_HOST` line (or move it to a personal, gitignored
`platformio.local.ini` include) before committing anything else in this
folder.

**If you'd rather exercise the real onboarding flow instead** (no
`wifi_creds.h`, no build-flag edit): flash as-is, the node finds no saved
credentials and opens its own AP. Join it from your phone/laptop:

1. The node's serial log prints its AP SSID (`EPM-SAT-<node_id>`) and a
   freshly generated WPA2 password — write the password down, you'll need it
   to join.
2. Join that WiFi network, then browse to `http://192.168.4.1` (auto-open
   behavior depends on the OS's captive-portal probe).
3. Submit your desktop's WiFi SSID/password. The broker address field is on
   the form too, but as noted above it has no effect yet — the node will still
   try to reach the compile-time default (`10.42.0.1:1883`) unless you've
   also done the `build_flags` override above before this flash.

For bring-up, the `wifi_creds.h` + `build_flags` path is simpler and is what
the rest of this guide assumes. Use the portal path if you specifically want
to test onboarding itself.

### 5b. USB / flashing basics

From the [satellite/](../satellite/) folder:

```sh
cd satellite
pio run                                     # build
pio run --target upload -e xiao_esp32s3     # flash
pio device monitor                          # serial log @ 115200 baud
```

- Flashing uses the board's built-in USB-JTAG (`upload_protocol = esp-builtin`
  in `platformio.ini`), not the CDC serial port — **no BOOT/RESET button dance
  needed**, and no separate bootloader-mode step.
- `monitor_port` is matched by USB VID:PID (`hwgrep://303A:1001`), not a fixed
  COM/tty number, so plugging into a different USB port doesn't break the
  build.
- **Serial permission error (Linux):** add yourself to the `dialout` group once:
  `sudo usermod -aG dialout $USER`, then log out/in.
- **`pio run --target erase`** wipes the whole flash, NVS included — use it to
  force a clean re-provision (drop saved WiFi creds, go back to the portal).
  A plain `upload` never touches NVS, so re-flashing after a config change
  (e.g. a new `build_flags` broker IP) keeps whatever WiFi credentials were
  already saved.

---

## 6. Bring-up, one module at a time

Keep two terminals open on the desktop the whole time:
- `pio device monitor` (from `satellite/`) — the board's serial log
- `mosquitto_sub -h localhost -t 'epm/#' -v` — raw MQTT traffic

Unlike a firmware with per-sensor build flags, this one always starts every
task on every boot — "bring up one module at a time" here means **wire
incrementally and watch the log react**, not recompile with a different flag
each stage.

---

### Stage 1 — Boot (is the board alive?)

**Goal:** prove the flash worked and the board boots, before anything else is
wired.

Flash it: `pio run --target upload -e xiao_esp32s3 && pio device monitor`

**✅ You should see**, near the top of the log:

```
main: Boot memory (before tasks): DRAM free=... PSRAM free=... IRAM free=...
```

and, once every task has started (the last line before app_main goes idle):

```
main: EPM: mic=1024-pt imu=2048-pt avg=8
```

The WS2812 ring goes **solid white** — that's `led_task`'s boot state, your
visual "board is alive" signal (there's no separate onboard blinking LED in
this firmware).

- **No white ring / no "EPM: mic=..." line?** Boot didn't complete — check the
  last log line before it stopped. `led_task_start()` runs first, so a dark
  ring after flashing usually means the flash itself failed, not a firmware
  bug; retry the upload.

---

### Stage 2 — WiFi + MQTT link (does it reach the desktop?)

**Goal:** the board joins WiFi and connects to the broker.

Nothing to change from Stage 1 — same build. Watch the serial log after boot:

```
wifi_task: WiFi RF init — SSID: "YourWiFiName"
wifi_task: STA started — connecting to "YourWiFiName"...
wifi_task: Got IP: 192.168.1.xx (after 1 attempt(s))
link_mqtt: node_id=a1b2c3 broker=192.168.1.50:1883 data_topic=epm/a1b2c3/data
link_mqtt: connected, subscribed to epm/a1b2c3/cmd
```

**That `a1b2c3` is your node_id** — note it down, you'll need it for Stage 3.

**Ring color while this is happening:** blue, slow breathe (1.2 s) from the
moment WiFi starts connecting, through any reconnect attempts; blue, fast
strobe (300 ms) once an IP address is obtained (this state is named
`RGB_TCP_CONN` in the driver — a holdover from a since-removed TCP transport —
it now just means "IP acquired, about to reach the broker"); if MQTT itself
later drops while WiFi stays up, the ring switches to **violet breathe**
(0.9 s) instead of reusing the blue WiFi color, specifically so a
broker-level stall doesn't read as a WiFi problem.

If the node instead has no saved credentials and opens its own AP (portal
path from §5a), **the ring has no dedicated color for that** — it stays
whatever it was in `led_task`'s last state (typically still boot-white) until
a credential attempt actually starts. Don't rely on the ring to tell you
"provisioning is active"; check the serial log instead:

```
provisioning: provisioning AP up: ssid="EPM-SAT-a1b2c3" password="..." (WPA2-PSK, http://192.168.4.1)
```

Troubleshooting:

| Symptom | Cause / fix |
|---|---|
| Ring stuck blue breathe, log repeats `Disconnect reason: ... attempt=N` | Wrong SSID/password in `wifi_creds.h`, or not 2.4 GHz (ESP32-S3 STA is 2.4 GHz only). Fix `wifi_creds.h` and reflash — a plain flash doesn't touch NVS, but a *first-boot* seed only happens once, so if a bad credential already got seeded, `pio run --target erase` first. |
| Got IP, but no `connected, subscribed to ...` line | Wrong `EPM_MQTT_BROKER_HOST` build flag, broker not listening on `0.0.0.0`, or firewall. Recheck §4a. From the desktop try `mosquitto_sub -h <desktop-ip> -t test` to prove the broker is reachable by IP, not just localhost. |
| `esp_mqtt_client_start failed: 0x...` | Broker unreachable at the configured host/port — recheck the IP is current (desktop IPs can change on DHCP renewal) and that you rebuilt after editing `build_flags`. |
| Ring goes violet breathe after previously connecting | MQTT-level disconnect (broker restarted, network blip). Self-heals; if it doesn't clear within ~152s the watchdog restarts the board automatically (§3). |
| Portal AP never appears | The node already has saved credentials from a previous boot — `pio run --target erase` first if you want to force provisioning. |

---

### Stage 3 — Status ring (the round trip)

**Goal:** confirm the WS2812 ring works and the command path is wired up. The
ring is normally **driven by the dashboard**, so we test it here with a manual
command.

Send a solid **red**, full-brightness, constant command by hand from the
desktop — replace `a1b2c3` with your node_id:

```sh
printf '\x08\x00\x00\xff\x00\x00\x00\x00' \
  | mosquitto_pub -h localhost -t 'epm/a1b2c3/cmd' -s
```

**✅ You should see** the ring turn solid red, and in the serial log:

```
link_mqtt: STATUS_LED rgb=0xff0000 mode=0 period_ms=0
```

Try green: `\x08\x00\xff\x00\x00\x00\x00\x00`. Try off: all-zero rgb.

> This proves the full command round trip: desktop → broker → node → ring.
> Once the dashboard sees healthy telemetry it drives this automatically; you
> won't need `mosquitto_pub` after this stage.

- **Ring stays dark / wrong colors, but the log shows the `STATUS_LED` line?**
  Wiring/power, not firmware — check DIN on **D5**, 3V3 power, shared GND.
- **No `STATUS_LED` line at all?** Command didn't reach the node — recheck
  Stage 2 (MQTT), and double-check the node_id in the topic matches what the
  log printed.

---

### Stage 4 — Microphone

**Goal:** first real telemetry — mic spectrum on the dashboard.

Nothing to enable — the mic task starts automatically on every boot. Wire the
INMP441 (D1/D2/D3, 3V3, GND) and reflash if you haven't yet; **✅ you should
see**, near boot:

```
mic_inmp441_i2s: mic_capture init: 48000 Hz, block=1024 samples, dma_desc=6
mic_task: mic_task starting (capture core 0, SIMD stats): ...
dsp_task: dsp_task starting (FFT core 1): 1024-pt, avg=8 (adaptive), ...
```

…and in `mosquitto_sub`, a steady stream of frames on `epm/<node_id>/data`
that now carry a real (not silent) mic spectrum section.

**On the dashboard (http://localhost:8180):** your node appears (by its
node_id), with a **live mic spectrum / waterfall**. Make noise near the mic —
tap it, whistle — and the spectrum should react.

Troubleshooting:

| Symptom | Cause / fix |
|---|---|
| `i2s_new_channel failed` / `i2s_channel_init_std_mode failed` | I2S driver couldn't claim the peripheral — rare, usually a build/IDF issue rather than wiring; check the exact `esp_err_to_name(...)` string in the log. |
| No init errors, but spectrum stays flat/silent | Check WS=D2, BCLK=D1, SD=D3, and 3V3/GND. `DIAG: mic i2s: overflow_count=... read_errors=... short_reads=...` in the 30 s diagnostics log tells you if the driver is even getting samples. |
| Node never appears on dashboard | No data frames at all — recheck Stage 2 (MQTT), confirm frames in `mosquitto_sub`. |

---

### Stage 5 — Accelerometer

**Goal:** confirm the KX134 is wired and producing real 3-axis spectra +
bearing-envelope channels.

Same as Stage 4 — nothing to enable, the accel task always starts. Wire the
KX134 (SPI D8/D9/D10, CS D6, INT1 D7, 3V3, GND) and reflash if you haven't
yet; **✅ you should see**:

```
accel_kx134_spi: WHO_AM_I OK (0x46)
imu_task: imu_task starting (3-axis): 2048-pt × 3 axes, avg=8, ...
```

On the dashboard the node now shows accelerometer spectra alongside the mic
one. Tap the accelerometer / the surface it's on — the spectra should react.

Because this firmware always publishes the full mic + 3-axis-accel +
envelope section set from its very first frame, there's no dashboard registry
step to worry about here — unlike a firmware with per-sensor enable flags,
there's no "the node changed its reported channel set mid-life" case to
reset.

Troubleshooting:

| Symptom | Cause / fix |
|---|---|
| `WHO_AM_I mismatch: got 0x.., expected 0x46` | The board can't talk to the KX134 correctly over SPI — check CS=D6, SCK/MISO/MOSI=D8/D9/D10, and 3V3/GND. `got 0x00`/`0xff` usually means no connection at all. |
| `no accel frame after ...ms` repeating | SPI reads OK but the FIFO never fills / INT1 isn't behaving as expected — check **INT1 → D7**. |
| `hal_accel_read_block: N consecutive failed epochs` then `hal_accel_reinit OK` | Sensor dropped out and self-recovered — transient (loose wiring, brief power glitch), not necessarily still broken; watch `DIAG: accel: ...` in the 30 s diagnostics log for a recurring pattern. |

---

### Stage 6 — Everything together

With both sensors wired, the node runs the full pipeline continuously from
boot — there's no separate "combine" step. Leave it publishing and watch:

- The status ring updates automatically once the dashboard is driving it
  (no more manual `mosquitto_pub` needed).
- Every 30 s, `diagnostics_task` logs a full health snapshot (`DIAG:` lines) —
  stack watermarks, heap free, and per-module counters for WiFi, MQTT, mic,
  DSP, accel, net, and LED. This is your ongoing "is it actually healthy"
  signal once the board is left running unattended; a slowly falling
  `largest_free` with `internal` roughly stable points at fragmentation, both
  falling together points at real exhaustion.
- If MQTT gets stuck (10 consecutive reconnect failures), the board
  self-restarts (`DIAG: mqtt stuck: ... restarting to recover (ADR-036)`) —
  measured real self-heal time is ~152s, expected behavior, not a crash.

🎉 That's a fully working satellite node talking to your desktop.

---

## 7. Troubleshooting quick reference

| What you see | Where | Likely cause |
|---|---|---|
| Ring stays dark / no `EPM: mic=...` boot line | board | flash failed — retry `pio run --target upload -e xiao_esp32s3` |
| Ring blue breathe, `Disconnect reason: ...` repeating | serial / ring | wrong WiFi creds in `wifi_creds.h`, or not 2.4 GHz |
| Ring violet breathe | serial / ring | MQTT-level disconnect (WiFi still up) — self-heals within ~152s or the board restarts |
| WiFi OK (`Got IP: ...`), no `connected, subscribed to ...` | serial | wrong `EPM_MQTT_BROKER_HOST` build flag, broker not on `0.0.0.0`, or firewall |
| Node absent from dashboard | dashboard | no *data* frames — check `mosquitto_sub` |
| `WHO_AM_I mismatch` | serial | KX134 SPI wiring (CS/SCK/MISO/MOSI = D6/D8/D9/D10) |
| `i2s_new_channel failed` / silent mic spectrum | serial / dashboard | INMP441 wiring (WS/BCLK/SD = D2/D1/D3) or driver init issue |
| Portal's MQTT broker field seems to do nothing | firmware | it currently doesn't (§3, §5a) — use the `EPM_MQTT_BROKER_HOST` build flag instead |
| Reflash didn't pick up a new `wifi_creds.h` | serial | NVS already has creds from an earlier boot — the seed step only fires once. `pio run --target erase` for a clean slate. |

---

## 8. Reference card

**Build / flash / log** (from `satellite/`):

```sh
pio run                                     # build
pio run --target upload -e xiao_esp32s3     # flash (USB-JTAG, no button dance)
pio run --target erase                      # wipe flash incl. NVS - forces re-provisioning
pio device monitor                          # serial log @ 115200
```

**Desktop:**

```sh
cd base-station && ./start_desktop_dashboard.sh     # dashboard @ :8180 + broker check
mosquitto_sub -h localhost -t 'epm/#' -v            # watch all MQTT traffic
hostname -I | awk '{print $1}'                      # desktop IP for the board
```

**MQTT topics:**

| Topic | Direction | Payload |
|---|---|---|
| `epm/<node_id>/data` | node → desktop | telemetry frame (mic + 3-axis accel spectra + envelopes) |
| `epm/<node_id>/cmd`  | desktop → node | `STATUS_LED` (`[0x08][rgb u32 LE][mode u8][period u16 LE]`) — the only command type currently handled |

**Config knobs:**

| Setting | Where | Notes |
|---|---|---|
| `WIFI_SSID` / `WIFI_PASS` | gitignored `satellite/src/wifi_creds.h` | first-boot NVS seed only; portal submission overrides it afterward |
| `EPM_MQTT_BROKER_HOST` / `EPM_MQTT_BROKER_PORT` | `build_flags` in `satellite/platformio.ini` | compile-time only — the portal's broker fields are not consumed at runtime |
| `FFT_MIC_N` / `FFT_IMU_N` / `SPEC_AVG_N` | `build_flags` in `satellite/platformio.ini` | already set to this firmware's defaults (1024 / 2048 / 8); rarely need changing for bring-up |

**Three rules to remember:**
1. There are no per-sensor enable flags — mic and accelerometer sampling
   always run. A missing/broken sensor logs errors and keeps retrying; it
   doesn't block boot or the other sensor.
2. WiFi credentials live in NVS, seeded from `wifi_creds.h` on first boot or
   from the captive portal. The MQTT broker address is compile-time only
   (`build_flags`) — the portal's broker fields don't take effect yet.
3. A plain `upload` never touches NVS. `pio run --target erase` wipes it for
   a clean re-provision.

**Deeper reading:** [satellite/README.md](../satellite/README.md),
[docs/satellite/PIN_ALLOCATION.md](satellite/PIN_ALLOCATION.md),
[docs/satellite/decisions/ADR-036-mqtt-reconnect-watchdog.md](satellite/decisions/ADR-036-mqtt-reconnect-watchdog.md)
(the self-heal watchdog referenced in Stage 6),
[docs/satellite/decisions/ADR-016-neopixel-display-driver.md](satellite/decisions/ADR-016-neopixel-display-driver.md)
(full ring-color state table).
