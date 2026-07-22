# Satellite Node Bring-Up Guide (XIAO ESP32-S3 → Desktop Dashboard)

A step-by-step guide for bringing up a **satellite node** on a Seeed Studio
XIAO ESP32-S3, talking to the dashboard running **on your desktop** — no UNO Q
in the picture. Written for someone who has never seen this repo before.

You'll bring the board up **one module at a time**: heartbeat LED → WiFi/MQTT
link → status ring → microphone → accelerometer → both together. Each stage
tells you exactly what to flash, what to watch in the serial log, and what to
check on the dashboard.

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

- **`epm/<node_id>/data`** — node → desktop. The binary telemetry frame
  (mic + accel FFT spectra). This is what makes the node appear on the dashboard.
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
  `pio` CLI. Install the CLI with: `pip install -U platformio`
- `mosquitto` + `mosquitto-clients` (the MQTT broker + test tools)

---

## 2. Wiring (do this once)

Pin map for the XIAO ESP32-S3. Full rationale is in
[satellite/include/board_pins.h](../satellite/include/board_pins.h).

| Peripheral | Signal | XIAO pin | Notes |
|---|---|---|---|
| **KX134 accel** | SCK  | **D8** | hardware SPI (fixed) |
| | MISO | **D9** | hardware SPI (fixed) |
| | MOSI | **D10** | hardware SPI (fixed) |
| | CS   | **D3** | software chip-select |
| | INT1 | **D2** | buffer-full interrupt |
| **INMP441 mic** | WS / LRCLK | **D0** | |
| | BCLK | **D1** | |
| | SD (data) | **D4** | |
| | **L/R** | **GND** | **must tie to GND** → left channel (firmware reads left only) |
| **WS2812 ring** | DIN | **D5** | 8 pixels |

> ⚠️ **Power is 3.3 V, not 5 V.** Feed all three peripherals from the XIAO's
> **3V3** pin and share **GND**. The WS2812 ring runs fine off 3V3 at 8 pixels.

> ℹ️ The **onboard user LED** (the little LED on the XIAO itself, GPIO21) is a
> separate "heartbeat" — nothing to wire. It's your first sign of life.

---

## 3. Codebase tour (what you're flashing)

Everything for the node lives under [satellite/](../satellite/). It's an
Arduino/PlatformIO project. Full detail in
[satellite/README.md](../satellite/README.md).

**Config you'll actually touch** — [satellite/include/app_config.h](../satellite/include/app_config.h):

| Setting | What it does |
|---|---|
| `WIFI_SSID` / `WIFI_PASSWORD` | which WiFi to join |
| `MQTT_BROKER_HOST` / `MQTT_BROKER_PORT` | where the desktop broker is |
| `MIC_SENSOR_ENABLED` (0/1) | turn the mic task on/off |
| `ACCEL_SENSOR_ENABLED` (0/1) | turn the accel task on/off |

The two `*_ENABLED` flags are how we bring sensors up one at a time.

**The firmware = 5 FreeRTOS tasks**, started in this order by
[satellite/src/main.cpp](../satellite/src/main.cpp):

1. **transport** — owns WiFi + MQTT. Publishes telemetry, receives LED commands.
   → [transport_task.cpp](../satellite/src/threads/transport_task.cpp)
2. **rgb_display** — drives the WS2812 ring from `STATUS_LED` commands.
   → [rgb_display_task.cpp](../satellite/src/threads/rgb_display_task.cpp) / [rgb_ws2812.cpp](../satellite/src/drivers/rgb_ws2812.cpp)
3. **mic_sampler** — reads the INMP441 over I2S, runs an FFT.
   → [mic_sampler_task.cpp](../satellite/src/threads/mic_sampler_task.cpp) / [mic_i2s.cpp](../satellite/src/drivers/mic_i2s.cpp)
4. **accel_sampler** — reads the KX134 over SPI, FFTs each axis, sums them.
   → [accel_sampler_task.cpp](../satellite/src/threads/accel_sampler_task.cpp) / [kx134.cpp](../satellite/src/drivers/kx134.cpp)
5. **fuser** — every 200 ms, grabs the latest mic + accel spectra and publishes
   one telemetry frame over MQTT.
   → [fuser_task.cpp](../satellite/src/threads/fuser_task.cpp)

Plus the **heartbeat**: `loop()` in `main.cpp` blinks the onboard LED every
500 ms once all five tasks start successfully.

> 🔑 **Two rules that will save you an hour:**
>
> 1. **A sensor whose flag is ON but is broken/unwired will halt the entire
>    boot.** If `hal_accel_init()` can't find the KX134 (bad wiring), the node
>    prints `accel_sampler_task_start failed` and stops — **no heartbeat, no
>    publishing at all**. So enable a sensor *only* once it's wired. That's the
>    whole reason we bring them up one at a time.
> 2. **A node locks in its sensor set on its very first telemetry frame.** If
>    the node's first frame carried only mic, the dashboard commits it to
>    "mic-only" and will then *reject* any later frame that also has accel. So
>    when you change which sensors are enabled, you must **reset the node in the
>    dashboard's registry** (Section 6, Stage 5 shows how).

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

Write this down — it goes into `MQTT_BROKER_HOST` in Section 5. Example:
`192.168.1.50`.

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

Edit [satellite/include/app_config.h](../satellite/include/app_config.h):

```c
#define WIFI_SSID         "YourWiFiName"
#define WIFI_PASSWORD     "YourWiFiPassword"
#define MQTT_BROKER_HOST  "192.168.1.50"   // the desktop IP from Section 4b
```

The defaults (`EPM-BaseStation` / `10.42.0.1`) assume the real UNO Q — replace
them.

> 🔒 Don't commit your WiFi password. If you'd rather not touch the file, you can
> pass these as build flags in `platformio.ini` instead (see the README) — but
> editing `app_config.h` is the simplest for bring-up.

### 5b. USB / flashing basics

From the [satellite/](../satellite/) folder:

```sh
cd satellite
pio run                 # build
pio run -t upload       # flash over USB-C
pio device monitor      # serial log @ 115200 baud
```

- **Serial permission error (Linux):** add yourself to the `dialout` group once:
  `sudo usermod -aG dialout $USER`, then log out/in.
- **Upload can't find / open the port:** put the board in bootloader mode — hold
  **BOOT**, tap **RESET**, release **BOOT**, then re-run upload.
- The XIAO uses native USB — the **same port** does both flashing and the serial
  log.

---

## 6. Bring-up, one module at a time

Keep two terminals open on the desktop the whole time:
- `pio device monitor` (from `satellite/`) — the board's serial log
- `mosquitto_sub -h localhost -t 'epm/#' -v` — raw MQTT traffic

---

### Stage 1 — Heartbeat LED (is the board alive?)

**Goal:** prove the flash worked and the board boots.

**Config** — sensors OFF so nothing can block the boot:

```c
#define MIC_SENSOR_ENABLED   0
#define ACCEL_SENSOR_ENABLED 0
```

Flash it: `pio run -t upload && pio device monitor`

**✅ You should see** in the serial log:

```
edgeai-predictive-monitor satellite node booting...
...
edgeai-predictive-monitor satellite node booted
```

…and the **onboard LED blinking ~once per second**.

- **No heartbeat / no "booted" line?** Read the last log line — it names which
  `*_task_start failed`. With both sensors off, only `transport` or `rgb` could
  fail (rare). Re-check the flash succeeded.

---

### Stage 2 — WiFi + MQTT link (does it reach the desktop?)

**Goal:** the board joins WiFi and connects to the broker. (Still sensors-off.)

Nothing to change — same build. Just watch the serial log after boot:

**✅ You should see:**

```
[transport] connecting to WiFi SSID "YourWiFiName"...
[transport] WiFi connected, IP=192.168.1.xx
[transport] connecting to MQTT broker 192.168.1.50:1883 as "a1b2c3"...
[transport] MQTT connected, subscribed to epm/a1b2c3/cmd
```

**That `a1b2c3` is your node_id — note it down.**

Troubleshooting:

| Symptom | Cause / fix |
|---|---|
| `still waiting for WiFi...` repeating | Wrong SSID/password, or board not on 2.4 GHz. The ESP32-S3 is 2.4 GHz only. |
| WiFi connects, but no "MQTT connected" | Wrong `MQTT_BROKER_HOST` IP, broker not listening on `0.0.0.0`, or firewall. Recheck Section 4a. From the desktop try `mosquitto_sub -h <desktop-ip> -t test` to prove the broker is reachable by IP, not just localhost. |
| `MQTT connect failed, rc=...` | Broker reachable but rejecting. Confirm `allow_anonymous true`. |

> The node won't appear on the dashboard yet — with both sensors off it only
> sends empty "heartbeat" frames (no spectrum data), which the dashboard skips.
> That's expected. The next stages give it real data.

---

### Stage 3 — Status ring (the round trip)

**Goal:** confirm the WS2812 ring works. The ring is **driven by the dashboard**,
so we test it with a manual command for now.

The ring starts **blank** at boot (normal). Send it a color by hand from the
desktop — solid **red**, full brightness — replacing `a1b2c3` with your node_id:

```sh
printf '\x08\x00\x00\xff\x00\x00\x00\x00' \
  | mosquitto_pub -h localhost -t 'epm/a1b2c3/cmd' -s
```

**✅ You should see** the ring turn solid red, and in the serial log:

```
[transport] RX STATUS_LED rgb=0xff0000 mode=0 period_ms=0
```

Try green: `\x08\x00\xff\x00\x00\x00\x00\x00`. Try off: all-zero rgb.

> This proves the full command round trip: desktop → broker → node → ring. Once
> sensors are on, the dashboard sends these automatically based on node health
> (e.g. green = healthy). You won't need `mosquitto_pub` after this stage.

- **Ring stays dark / wrong colors?** Check DIN on **D5**, 3V3 power, shared GND.
  If serial shows the `RX STATUS_LED` line but the ring is dark, it's wiring/power,
  not firmware.

---

### Stage 4 — Microphone

**Goal:** first real telemetry — mic spectrum on the dashboard.

**Config:**

```c
#define MIC_SENSOR_ENABLED   1
#define ACCEL_SENSOR_ENABLED 0
```

Flash it. **✅ You should see** in the serial log after boot:

```
[accel_sampler] accel sensor disabled (ACCEL_SENSOR_ENABLED == 0)
[fuser] mic fs=48000 fft_size=1024 bin_count=512 | accel fs=0 fft_size=0 bin_count=0
```

…the heartbeat still blinking, and in `mosquitto_sub` a steady stream of frames
on `epm/<node_id>/data`.

**On the dashboard (http://localhost:8180):** your node appears (by its node_id),
with a **live mic spectrum / waterfall**. Make noise near the mic — tap it,
whistle — and the spectrum should react.

Troubleshooting:

| Symptom | Cause / fix |
|---|---|
| Boot halts, last line `mic_sampler_task_start failed` / `[mic_i2s] ... failed` | I2S init failed. Recheck WS=D0, BCLK=D1, SD=D4, and 3V3/GND. |
| Node appears but spectrum is flat/dead | Mic wired but silent — confirm **L/R tied to GND**. Without it the left slot is never clocked. |
| Node never appears on dashboard | No data frames — recheck Stage 2 (MQTT). Confirm frames in `mosquitto_sub`. |

---

### Stage 5 — Accelerometer (+ the registry reset)

**Goal:** add the accelerometer so the node reports **both** channels.

> 🔑 **You must reset the node first.** In Stage 4 the node committed to
> "mic-only" on its first frame. If you just add accel, the dashboard will reject
> the new mic+accel frames (bin-count mismatch) and the node will look frozen.
> Clear it:
>
> ```sh
> # in the dashboard terminal: Ctrl+C to stop it, then:
> rm -rf base-station/.cache/data-desktop
> cd base-station && ./start_desktop_dashboard.sh
> ```
>
> This wipes the remembered node set (and the sim node's history) so your board
> can re-register with its new sensor set. Do this **every time** you change
> which sensors are enabled.

**Config** — both on:

```c
#define MIC_SENSOR_ENABLED   1
#define ACCEL_SENSOR_ENABLED 1
```

Flash it. **✅ You should see:**

```
[fuser] mic fs=48000 fft_size=1024 bin_count=512 | accel fs=12800 fft_size=1024 bin_count=512
```

Heartbeat blinking, frames flowing. On the dashboard the node now shows **both a
mic and an accel spectrum**. Tap the accelerometer / the surface it's on — the
accel spectrum should react.

Troubleshooting:

| Symptom | Cause / fix |
|---|---|
| Boot halts, `[kx134] WHO_AM_I mismatch: got 0xNN, expected 0x46` | The board can't talk to the KX134 over SPI. Check CS=D3, INT1=D2, and SCK/MISO/MOSI=D8/D9/D10, plus 3V3/GND. A wrong `got` value = wiring; `got 0x00`/`0xff` usually = no connection at all. |
| `[kx134] no accel frame after 1000ms` repeating | SPI reads OK but no data-ready interrupt — check **INT1 → D2**. |
| Node stuck / dashboard not updating after adding accel | You skipped the registry reset above. |

---

### Stage 6 — Everything together

With both sensors live, the node runs the full pipeline. Leave it publishing and
watch the dashboard:

- The node's **status ring** now updates on its own (the dashboard pushes
  `STATUS_LED` based on health — no more `mosquitto_pub`).
- After ~50 frames (~10 s) the node gets **commissioned** and starts producing
  **anomaly scores** — the dashboard's health/anomaly view comes alive.

🎉 That's a fully working satellite node talking to your desktop.

---

## 7. Troubleshooting quick reference

| What you see | Where | Likely cause |
|---|---|---|
| No onboard heartbeat LED | board | boot halted — read the last serial line for which `*_start failed` |
| `still waiting for WiFi...` | serial | wrong WiFi creds / not 2.4 GHz |
| WiFi OK, no MQTT | serial | wrong desktop IP, broker not on `0.0.0.0`, firewall |
| Node absent from dashboard | dashboard | no *data* frames — check `mosquitto_sub`, check a sensor is enabled |
| `WHO_AM_I mismatch` | serial | KX134 SPI wiring (CS/INT1/SCK/MISO/MOSI) |
| `[mic_i2s] ... failed` | serial | INMP441 I2S wiring (WS/BCLK/SD) |
| Flat mic spectrum | dashboard | INMP441 L/R not tied to GND |
| Node freezes after enabling a new sensor | dashboard | forgot the registry reset (Stage 5) |

---

## 8. Reference card

**Build / flash / log** (from `satellite/`):

```sh
pio run                 # build
pio run -t upload       # flash
pio device monitor      # serial log @ 115200
```

**Desktop:**

```sh
cd base-station && ./start_desktop_dashboard.sh     # dashboard @ :8180 + broker check
mosquitto_sub -h localhost -t 'epm/#' -v            # watch all MQTT traffic
rm -rf base-station/.cache/data-desktop             # reset the node registry
hostname -I | awk '{print $1}'                      # desktop IP for the board
```

**MQTT topics:**

| Topic | Direction | Payload |
|---|---|---|
| `epm/<node_id>/data` | node → desktop | telemetry frame (mic/accel spectra) |
| `epm/<node_id>/cmd`  | desktop → node | `STATUS_LED` (`[0x08][rgb u32 LE][mode u8][period u16 LE]`) |

**Config knobs** — [satellite/include/app_config.h](../satellite/include/app_config.h):
`WIFI_SSID`, `WIFI_PASSWORD`, `MQTT_BROKER_HOST`, `MQTT_BROKER_PORT`,
`MIC_SENSOR_ENABLED`, `ACCEL_SENSOR_ENABLED`.

**Two rules to remember:**
1. Enable a sensor only when it's wired — a broken enabled sensor halts boot.
2. Reset the registry whenever you change the enabled-sensor set.

**Deeper reading:** [satellite/README.md](../satellite/README.md),
[docs/SENSOR_TELEMETRY_FRAME_PLAN.md](SENSOR_TELEMETRY_FRAME_PLAN.md).
</content>
</invoke>
