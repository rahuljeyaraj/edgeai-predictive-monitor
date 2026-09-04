# Build Guide

How to build an EdgeAI Predictive Monitor, from an empty bench to a machine that
is being watched.

Parts and buy links are in [`BILL_OF_MATERIALS.md`](BILL_OF_MATERIALS.md). This
guide assumes you have them.

---

## 0. Pick your path

There are five ways in, and **two of them need no hardware at all**. Those two
are the fastest way to see the whole system working, and the right place to
start if you are still deciding whether to buy anything.

| Path | Hardware needed | Time | Good for |
|---|---|---|---|
| [§8.1 Desktop dashboard + simulated node](#81-no-hardware-at-all) | none | ~10 min | Seeing the entire dashboard, setup, scoring and classifier flow on a laptop |
| [§8.2 Simulated node against a real base station](#82-a-simulated-node-against-a-real-base-station) | UNO Q only | ~30 min | Testing the real device's ingestion and fleet handling without building satellites |
| [§4 Base station](#4-build-the-base-station) | UNO Q + sensors | ~2 hours | The real thing, one machine |
| [§5 Satellite node](#5-build-a-satellite-node) | XIAO ESP32-S3 + sensors | ~1 hour | Adding machines |
| [§7 Motor test rig](#7-optional-the-motor-test-rig) | Uno + CNC shield + steppers | ~2 hours | Reproducing the project's measurements and the trip |

**If you are starting from nothing, do §8.1 first.** It takes ten minutes, needs
no purchase, and everything you learn about the dashboard there is true of the
real device.

Add the printed enclosure ([§2](#2-print-the-enclosure)) and harnesses
([§3](#3-make-the-harnesses)) to the times above if you are building the pods
properly rather than working on a bench with jumper wires.

---

## 1. Before you start

Get the repo:

```sh
git clone https://github.com/rahuljeyaraj/edgeai-predictive-monitor.git
cd edgeai-predictive-monitor
```

Install, in whatever order suits you:

- **Arduino App Lab**, for the UNO Q. This is the only tool that can flash the
  STM32 side and the only place brick secrets can be set.
- **PlatformIO**, for the satellite and the motor rig.
- **A slicer**, for the printed parts.

Tools on the bench: a JST-XH crimping tool, wire cutters, a soldering iron for
the breakout headers, and a multimeter if you are building the rig.

---

## 2. Print the enclosure

Models are in [`3d-models/`](../3d-models/), one 3MF per plate per colour. The
file-by-file map is in
[`BILL_OF_MATERIALS.md` §5](BILL_OF_MATERIALS.md#5-3d-printed-parts).

For one base station, print `base_station_1`, `_2` and `_3`. For each satellite,
print `satellite_1`, `_2` and `_3`.

Settings that worked here:

| | |
|---|---|
| Material | PLA+ |
| Nozzle | 0.4 mm |
| Layer height | 0.2 mm |
| Infill | 20% for shells and bezels, 40% or more for the rig brackets and flywheel |
| Supports | none needed; the plates are already oriented |

Each pod is three sub-assemblies:

- **Shell**, front and back halves that snap together around the board.
- **Mount kit**, two backplates plus a leg and two stem connectors, which is what
  actually holds the pod on the machine.
- **Front bezel**, two rims and a foot. The base station's bezel also has a lens
  insert, which holds the Fresnel lens over the UNO Q's LED matrix. The
  satellite's does not, because a satellite has no matrix.

Optional: emboss the wordmark on the shell face before slicing. The SVGs and the
slicer settings are in [`hardware/enclosure-logo/`](../hardware/enclosure-logo/).
Engrave it recessed at 0.3 to 0.4 mm, not raised.

---

## 3. Make the harnesses

Every sensor connects through a JST-XH harness rather than direct jumper wires,
so a pod can be opened, a sensor swapped, and nothing resoldered.

1. Cut the 10-wire ribbon into lengths, keeping the colour order consistent
   across every harness you make. Getting this wrong once and being consistent
   about it is better than getting it right sometimes.
2. Crimp a 2515 female pin onto each end and seat it in the matching housing.
   The connector sizes per harness are listed in the BOM tables.
3. Tug-test every crimp before it goes in the housing. A crimp that pulls out in
   your fingers will pull out under vibration, which is the one environment this
   whole project lives in.

Solder the pin headers onto the KX134 and INMP441 breakouts now, if the boards
did not ship with them fitted.

---

## 4. Build the base station

### 4.1 Wire it

Three peripherals hang off the real-time half of the board. Nothing hangs off
the Linux half.

| Peripheral | Signal | Pin |
|---|---|---|
| KX134 accelerometer | SPI SCK / MISO / MOSI | D13 / D12 / D11 |
| | Chip select | D8 |
| | INT1, buffer-full interrupt | D9 |
| INMP441 microphone | SAI1 clock / frame-sync / data | SCL / D10 / A4 |
| WS2812B ring | Data in | D3 |

Pins are the UNO Q's own header labels, which is what the board is silkscreened
with. The microphone's bit clock is the one signal without a D-number: it comes
out on the dedicated **SCL** pin. The I2C peripheral is disabled to free it, and
nothing in this project uses I2C.

The editable schematic is
[`hardware/kicad/base_station.kicad_sch`](../hardware/kicad/base_station.kicad_sch),
with a one-page PDF next to it.

### 4.2 Assemble the pod

1. Seat the UNO Q in the back half of the shell.
2. Fit the Fresnel lens into the bezel's lens insert, over the LED matrix.
3. Pop the diffuser cap off the 9 W bulb and fit it over the WS2812B ring. That
   cap is the status dome, and it is the only reason the bulb is on the parts
   list.
4. Plug the three harnesses in and close the shell.
5. Bolt the ring magnet into the mount foot with an M6 bolt through its 7 mm
   bore, then attach the mount kit.

**Where you put the finished pod matters as much as which sensor you bought.**
Mount it rigidly, as close to the bearing as the geometry allows. A soft or loose
mount is a low-pass filter you did not ask for, and it removes exactly the
high-frequency content that early bearing faults live in.

### 4.3 Provision the board, once

These configure things outside the application container and only need running
once per board:

```sh
cd base-station
./provision-spi.sh      # the MCU-to-Linux SPI bulk link
./provision-baud.sh     # sets the serial link to 500000 baud on the Linux side
./provision-wifi.sh     # Wi-Fi onboarding: hotspot fallback + captive portal
```

`provision-baud.sh` matters more than it looks. The Linux-side router's baud must
match the firmware's, and a mismatch breaks the whole link silently, with no
error anywhere.

### 4.4 Flash the real-time side

Open [`base-station/sketch/`](../base-station/sketch/) in Arduino App Lab and
flash it to the STM32U585.

### 4.5 Build, deploy and run the Linux side

```sh
cd base-station
./start_dashboard.sh
```

This forces normal (non-raw-capture) mode, builds, flashes, pushes the
application, waits for its container, and prints **the board's own LAN IP URL**.

Use that link, not a localhost one. A real deployment has no port forwarding
available, and testing over one hides problems you will meet later.

Open it and the base station's own machine appears on its own. Nothing has been
trained yet, but the sensing half of the loop is already live and watchable:
vibration and audio spectra, time-domain traces, and a status ring that goes
solid the moment real data starts flowing.

### 4.6 Put it on the shop network

If there is Wi-Fi on the floor, join it from the dashboard's **Network** tab.

If there is none, the base station raises its own `EPM-BaseStation` hotspot and
runs a captive portal, so a phone that joins it lands straight on the same
"pick a network, type a password" flow. Satellites and the dashboard then connect
to the base station directly, and the whole system works with no network
infrastructure at all.

### 4.7 Telegram alerts, optional

One secret. Set `TELEGRAM_BOT_TOKEN` as the `arduino:telegram_bot` brick's
variable through App Lab's interface, then re-add that brick to `app.yaml`. The
bot's username is resolved automatically at startup, so there is no second value
to configure. With the token unset, every alert path no-ops cleanly.

---

## 5. Build a satellite node

### 5.1 Wire it

| Signal | Pin | Notes |
|---|---|---|
| KX134 SPI SCK / MISO / MOSI | D8 / D9 / D10 | The board's fixed hardware SPI pins |
| KX134 chip select | D3 | |
| KX134 INT1, buffer-full | D2 | |
| INMP441 WS / LRCLK | D0 | |
| INMP441 BCLK | D1 | |
| INMP441 SD, data in | D4 | |
| WS2812B ring data in | D5 | |

The XIAO ESP32-S3 breaks out only 11 GPIOs, so every assignment above is chosen
to keep the fixed hardware SPI lines free for the accelerometer, the one
peripheral that genuinely needs them.

**There is no per-unit ID to set.** Node identity is derived from the board's own
Wi-Fi MAC address. No jumper to solder, no build flag to change between units.

Schematic:
[`hardware/kicad/satellite_node.kicad_sch`](../hardware/kicad/satellite_node.kicad_sch).

### 5.2 Assemble the pod

Same as [§4.2](#42-assemble-the-pod), minus the Fresnel lens. The satellite bezel
has no lens window.

### 5.3 Flash it

```sh
cd satellite
pio run                # build
pio run -t upload      # flash over USB
pio device monitor     # optional serial console, 115200 baud
```

**No credential is compiled in.** For a bench board being reflashed constantly, a
dev-only shortcut exists: pass `WIFI_SSID` and `WIFI_PASSWORD` as build flags in
`platformio.ini` and the node seeds its storage on first boot and skips the
portal. A real build passes neither, and the shortcut is a no-op.

If this is your first satellite and something does not come up, the stage-by-
stage bring-up guide is
[`SATELLITE_BRINGUP_GUIDE.md`](SATELLITE_BRINGUP_GUIDE.md).

### 5.4 Onboard it

1. **Power it up.** A node with no saved credentials raises **its own access
   point**, named from its own hardware address, for example `EPM-SAT-a4cf12`.
   Ten unconfigured nodes on a bench are ten distinguishable networks.
2. **Join it from any phone.** No app. The setup page opens by itself, through
   the same captive-portal mechanism airport Wi-Fi uses.
3. **Fill in three fields.** The shop's Wi-Fi name, its password, and the MQTT
   broker address, which comes pre-filled with `epm-base.local`. It is a field
   rather than a constant because mDNS is sometimes blocked on factory networks,
   and when it is, a technician needs to type an address rather than reflash a
   board.
4. **It tests before it commits.** Submitting does not blindly save. The node
   tries the credentials first and writes them to storage only on success, so a
   typo cannot strand a device on a machine you now need a ladder to reach.
5. **It appears.** No pairing step, no ID to type in. The asset shows up on the
   Fleet page the moment its first frame lands, ready to be set up.

---

## 6. Commission your first machine

A node that is wired and online is not yet monitoring anything. Every asset is
taught its own normal once, from the dashboard, in four to six minutes.

Find the asset in the list and press **Set up**. Six steps:

| # | Step | Machine | Required | What it produces |
|---|---|---|---|---|
| 1 | Name and class | either | yes | The name alerts print, and the class recordings group by |
| 2 | Off | **switched off** | yes | This sensor's own noise floor, per frequency bin |
| 3 | Running conditions | **running** | yes, at least one | The training batch, the running reference, and labelled healthy recordings |
| 4 | Train | either | yes | This asset's own model, its normalisation statistics and its two thresholds |
| 5 | Trip output | running, then stopped by the system | no | Which motor stops this machine, confirmed rather than guessed |
| 6 | Done | either | — | A summary. The asset goes live |

**Step 2 is the one instruction no computer can check.** Nothing in the software
can confirm the machine is actually switched off. A baseline captured while the
machine runs teaches the system that its own vibration is silence, and the
running/stopped gate will never work correctly until you re-measure it.

**Step 5 needs the motor rig, or a real machine wired to a trip output.** Skip it
if you have neither. The outputs that have announced themselves appear as real
candidates; press **Test** and watch the machine actually stop. One motor may
only be claimed by one asset.

Setup state lives in memory only. Restart the dashboard mid-setup and the current
step restarts, because a batch resumed across a restart is worse data than a
fresh one.

Naming the fault type, rather than just detecting one, needs a second model
trained through Edge Impulse. That flow is entirely dashboard-driven and is
covered in [`report/REPORT.md`](../report/REPORT.md) Chapter 7.

---

## 7. Optional: the motor test rig

Only needed to reproduce the project's measurements or to see the trip fire
end to end.

**1. Set each driver's current limit before applying power.** Each A4988 or
DRV8825 has a small potentiometer setting its reference voltage. Under-current
skips steps, over-current cooks the driver.

- A4988: `Vref ≈ Imax × 8 × Rsense`
- DRV8825: `Vref ≈ Imax / 2`

**2. Wire it.**

| Signal | Pin | Notes |
|---|---|---|
| Shared driver enable (`~ENABLE`) | D8, active-LOW | **One line for all three driver sockets.** The shield has no per-motor hardware enable |
| Motor 1 (X) STEP / DIR | D2 / D5 | |
| Motor 2 (Y) STEP / DIR | D3 / D6 | |
| Motor 3 (Z) STEP / DIR | D4 / D7 | |

The shared enable line is why the trip is implemented as a per-motor step-pulse
halt rather than a hardware disable. Pulling `~ENABLE` high de-energises all
three drivers, which would stop two healthy machines to protect one faulty one.

**3. Flash** [`motor-driver/src/main.cpp`](../motor-driver/src/main.cpp) to the
Arduino Uno with PlatformIO.

**4. Start the rig host,** which serves the control page and receives trips:

```sh
cd motor-driver
./start_motor_driver.sh                                # broker on localhost
./start_motor_driver.sh --mqtt-host <base-station-ip>  # or over the LAN
```

The Uno's port is autodetected; pass `--port` if two boards are attached. Open
**http://localhost:8000/** in Chrome or Edge, click **Connect**, and pick the
Uno's port. The rig starts with **one** motor installed; the empty slots add the
others, and each one added is announced to the base station as a trip output
straight away. Add `--profile` to run a scripted capture profile instead of
driving by hand.

**5. Claim it** at step 5 of that asset's setup ([§6](#6-commission-your-first-machine)).
Back on the control page, the motor now carries a **PROTECTED** badge naming the
asset. If the trip ever fires, its card turns red and locks until a human presses
**Reset & re-arm**.

The printed rig parts, including the flywheel used for imbalance injection, are
in [`3d-models/`](../3d-models/). Moving or removing bolts from the flywheel's
bolt circle is the fault-injection mechanism.

---

## 8. Building with no hardware

### 8.1 No hardware at all

This runs the **real** dashboard application on your own machine, fed by a
simulator that speaks the **real** wire protocol, replaying **real** captured
sensor data. It is not a mock: the registry, the feature pipeline, the
autoencoder, the setup flow, the thresholds, the classifier and the whole
frontend are the same code that runs on the board.

You need an MQTT broker on `localhost:1883`. The script checks for one and will
not start one for you:

```sh
sudo apt-get install -y mosquitto mosquitto-clients
sudo systemctl enable --now mosquitto
```

Then:

```sh
cd base-station
./start_desktop_dashboard.sh
```

That creates a virtual environment, installs dependencies, starts the application
on **port 8180** with an isolated data directory so it can never touch a real
device's registry or history, starts one simulated node, pre-configures it, and
prints both URLs.

The simulated node starts **offline** on purpose. Open its own control page and
press *Go Online*, or skip that with `--auto-online`.

| Flag | Effect |
|---|---|
| `--nodes N` | Run N independent simulated nodes, each with its own control page |
| `--captures-dir DIR` | Replay a different folder of `.npz` captures. Defaults to `base-station/captures/` |
| `--captures-dir ""` | Fall back to generated synthetic captures instead of real recordings |
| `--auto-online` | Bring the node online without a click |
| `--host 0.0.0.0` | Bind on every interface, so you can open the dashboard from a phone to check the mobile layout |

Each simulated node's page lets you pick which capture file it streams, toggle
the accelerometer and microphone independently, adjust FFT bin counts live, and
watch its status LED change as the base station pushes status to it, exactly as a
real node would.

**What you will not see:** the `base_station` asset itself. Its data comes from
the SPI-connected sampling chip, which does not exist on a laptop. Only
MQTT-driven nodes appear. That is expected, not a failure.

### 8.2 A simulated node against a real base station

Once you have a UNO Q running ([§4](#4-build-the-base-station)), point the same
simulator at the real device over your LAN. This is how most of the fleet-level
behaviour in this project was exercised without building ten satellites.

One-time, on the device. This needs a password typed on-device, so no script can
do it for you:

```sh
adb shell
sudo apt-get update && sudo apt-get install -y mosquitto mosquitto-clients
echo -e 'listener 1883 0.0.0.0\nallow_anonymous true' | sudo tee /etc/mosquitto/conf.d/lan.conf
sudo systemctl enable --now mosquitto
```

The broker lives **on the UNO Q**, not on your laptop, matching how a real
satellite has to work since it is a sensor with nowhere else to publish. Then,
from your machine:

```sh
cd base-station
./start_sim_node.sh --captures-dir captures --nodes 2
```

The simulator connects out over plain LAN, exactly as a real node would. USB is
used only for occasional setup steps, never for live traffic, so a momentary USB
blip cannot flip a node offline on the dashboard.

---

## 9. Changing the wire format

[`base-station/telemetry_schema.json`](../base-station/telemetry_schema.json) is
the single source of truth for the frame format. Any edit must be followed by
regenerating every generated side, so the base station, the ingestion parser and
the satellite firmware cannot drift apart:

```sh
python3 base-station/python/tools/gen_telemetry_schema.py
```

---

## Where to go next

- [`BILL_OF_MATERIALS.md`](BILL_OF_MATERIALS.md) — what to buy
- [`SATELLITE_BRINGUP_GUIDE.md`](SATELLITE_BRINGUP_GUIDE.md) — stage-by-stage
  satellite debugging when a module does not come up
- [`Dashboard_LAN_Access_Guide.md`](Dashboard_LAN_Access_Guide.md) — reaching the
  dashboard from other machines
- [`report/REPORT.md`](../report/REPORT.md) — why every one of these decisions was
  made the way it was
