# Bill of Materials

Everything you need to buy to build an EdgeAI Predictive Monitor.

**This is the canonical parts list for the project.** Anywhere else a part is
mentioned, including the Hackster write-up, this file is the one that is right.

Build steps that use these parts are in [`BUILD_GUIDE.md`](BUILD_GUIDE.md).
Pin-by-pin wiring is in [`BUILD_GUIDE.md` §4.1 and §5.1](BUILD_GUIDE.md).

---

## 0. How much to buy

The system is one **base station** plus one **satellite node** per additional
machine. Buy in blocks:

| You want to monitor | Buy | Section |
|---|---|---|
| 1 machine | Base station | [§1](#1-base-station-one-per-site) |
| N machines | Base station + (N−1) satellites | [§1](#1-base-station-one-per-site) + [§2](#2-satellite-node-one-per-extra-machine) |
| Nothing yet, just want to see it run | Nothing at all | [`BUILD_GUIDE.md` §8](BUILD_GUIDE.md) |

The motor test rig in [§3](#3-motor-test-rig-validation-only) is **not part of a
deployment**. It is the bench setup used to induce and measure faults, and you
only need it if you are reproducing the project's results.

Every block also draws on the shared consumables in
[§4](#4-shared-consumables) and the printed parts in
[§5](#5-3d-printed-parts).

---

## 1. Base station, one per site

The central node. It senses its own machine, runs the models for every node in
the fleet, serves the dashboard and sends the alerts.

| Part | Qty | What it does here | Buy |
|---|---:|---|---|
| Arduino UNO Q, 4 GB (ABX00173) | 1 | The board. Real-time sensing on the STM32U585, models and dashboard on the Linux side | [Robu][unoq] |
| KX134-1211 SPI accelerometer | 1 | Vibration sensing to 6 kHz | [Robu][kx134] |
| INMP441 I2S MEMS microphone | 1 | Audio sensing to 24 kHz | [Robu][inmp441] |
| WS2812B 8-pixel RGB ring | 1 | The status light on top of the pod | [Robu][ws2812] |
| 9 W LED bulb | 1 | Bought for its diffuser cap, which becomes the status dome over the ring | [Amazon][bulb] |
| Fresnel lens, credit-card size | 1 | Magnifies the UNO Q's onboard LED matrix so the fleet summary is readable across a room | [Amazon][fresnel] |
| Neodymium ring magnet, N51, OD15 × ID7 × 5 mm | 1 | Couples the pod to the machine housing | [Patel Magnets][magnet] |
| JST-XH 2.54 connector, 8-pin, male and female | 2 | Board-to-harness connectors | [Maker Bazar][jst8] |
| JST-XH 2.54 connector, 6-pin, male and female | 1 | Accelerometer harness | [Maker Bazar][jst6] |
| JST-XH 2.54 connector, 4-pin, male and female | 1 | Microphone harness | [Maker Bazar][jst4] |
| JST-XH 2.54 connector, 3-pin, male and female | 3 | Status ring and power harnesses | [Maker Bazar][jst3] |
| JST-XH 2.54 connector, 2-pin, male and female | 1 | Power harness | [Maker Bazar][jst2] |
| USB-C cable | 1 | Power and the App Lab link | [Amazon][usbc] |

**The mount is not an accessory.** An accelerometer read through a soft or loose
mount is a low-pass filter you did not ask for, and it removes exactly the
high-frequency content that early bearing faults live in. Rigid coupling to the
machine housing, as close to the bearing as the geometry allows, matters more
than the price tag suggests.

A 2 GB UNO Q variant exists and is a straight drop-in. Nothing in this project
needs 4 GB, but 4 GB is the one to buy if you plan to train larger models
on-device.

---

## 2. Satellite node, one per extra machine

Same three sensing parts as the base station, on a XIAO ESP32-S3 instead. It
watches its own machine and reports to the base station over Wi-Fi.

Quantities below are **per node**.

| Part | Qty | What it does here | Buy |
|---|---:|---|---|
| Seeed Studio XIAO ESP32-S3 | 1 | The node's brain. Wi-Fi built in, no separate radio | [Robu][xiao] |
| KX134-1211 SPI accelerometer | 1 | Vibration sensing, same part as the base station | [Robu][kx134] |
| INMP441 I2S MEMS microphone | 1 | Audio sensing, same part as the base station | [Robu][inmp441] |
| WS2812B 8-pixel RGB ring | 1 | Status light, same part as the base station | [Robu][ws2812] |
| 9 W LED bulb | 1 | Diffuser cap for the status dome | [Amazon][bulb] |
| Neodymium ring magnet, N51, OD15 × ID7 × 5 mm | 1 | Couples the pod to the machine housing | [Patel Magnets][magnet] |
| JST-XH 2.54 connector, 6-pin, male and female | 3 | Sensor harnesses | [Maker Bazar][jst6] |
| JST-XH 2.54 connector, 4-pin, male and female | 1 | Microphone harness | [Maker Bazar][jst4] |
| JST-XH 2.54 connector, 3-pin, male and female | 2 | Status ring and power harnesses | [Maker Bazar][jst3] |
| USB-C cable | 1 | Power, and flashing over USB | [Amazon][usbc] |

Three of the four sensing parts are identical to the base station's on purpose:
one line item to buy in bulk, not a different parts list per machine. That is
also what keeps the cost of expanding the fleet close to linear.

A satellite has no Fresnel lens, because it has no LED matrix. Its printed bezel
has no lens window either.

---

## 3. Motor test rig, validation only

Not part of a deployment. This is the bench setup used to induce repeatable
faults and measure the trip.

| Part | Qty | What it does here | Buy |
|---|---:|---|---|
| Arduino Uno R3 | 1 | Motor controller. Receives stop commands, drives step pulses | [Robu][uno] |
| CNC Shield V3 | 1 | Driver carrier board | [Robu][cncshield] |
| A4988 stepper driver (or DRV8825) | 3 | One per motor axis | [Robu][a4988] |
| NEMA-17 stepper motor, JK42HS48 | 3 | The "machines" being monitored on the bench | [Robu][nema17] |
| 12 to 24 V DC power supply, at least 3 A | 1 | Motor power | [Robu][psu] |
| Bearing, 6201 | 2 | Pump rig | [Misumi][bearing6201] |
| Bearing, 6004 | 1 | Turbine rig | [Misumi][bearing6004] |
| M6 × 18 mm nut and bolt | 51 | Flywheel mass, and magnetic mounting of sensor nodes to the rig | any hardware shop |
| Wooden block, 200 × 100 × 20 mm | 3 | Motor rig base | any hardware shop |

The bolts are the fault-injection mechanism as much as they are fasteners: they
bolt into the flywheel's bolt circle, and moving or removing one produces a
repeatable imbalance.

---

## 4. Shared consumables

Bought once, used across every block above.

| Part | Qty | What it does here | Buy |
|---|---:|---|---|
| eSUN PLA+ Silver, 1.75 mm spool | 1 | Node enclosure shells and mount kits | [Robu][pla-silver] |
| eSUN PLA+ Orange, 1.75 mm spool | 1 | Node front bezels | [Robu][pla-orange] |
| eSUN PLA+ Grey, 1.75 mm spool | 2 | Motor rig brackets and flywheel | [Robu][pla-grey] |
| eSUN PLA+ Gold, 1.75 mm spool | 1 | Motor rig rings, pulley and shaft | [Robu][pla-gold] |
| Multicolor flat ribbon cable, 10-wire, 1 m | 1 | Every sensor harness | [Robu][ribbon] |
| 2515 JST-XH crimp terminal, female pins | ~100 | The harness ends | [Maker Bazar][crimp] |

Colours are what this build used, not a requirement. Any PLA works.

---

## 5. 3D-printed parts

All models are in [`3d-models/`](../3d-models/), each supplied as both **3MF**
(plated and ready to slice, colour already assigned) and **STL** (plain mesh).
Thirteen parts in total. `a` is the base station, `b` is the satellite, `c` is
the validation rig.

| File | Contains | Colour | Needed for |
|---|---|---|---|
| `a1_base_station_silver` | Shell, front and back halves | Silver | Base station |
| `a2_base_station_silver` | Mount kit: left and right plates, leg, two stem connectors | Silver | Base station |
| `a3_base_station_orange` | Front bezel: left and right rims, lens insert, foot | Orange | Base station |
| `b1_satellite_silver` | Shell, front and back halves | Silver | Each satellite |
| `b2_satellite_silver` | Mount kit, same pattern as the base station's | Silver | Each satellite |
| `b3_satellite_orange` | Front bezel: left and right rims, foot. No lens insert | Orange | Each satellite |
| `c1_belt_drive_rig_grey` | Upright L-bracket with bearing bore | Grey | Belt-drive rig |
| `c2_belt_drive_rig_gold` | Two bearing rings and a stepper ring | Gold | Belt-drive rig |
| `c3_belt_drive_rig_gold` | Toothed pulley, hex shaft, bearing holder | Gold | Belt-drive rig |
| `c6_direct_drive_rig_grey` | U-shaped pillow-block bracket | Grey | Direct-drive rig |
| `c7_direct_drive_rig_gold` | Two bearing rings, stepper ring, coupling shaft | Gold | Direct-drive rig |
| `c4_fly_wheel_grey` | Rotor disc | Grey | Both rigs |
| `c5_fly_wheel_ring_gold` | Bolt-on ring | Gold | Both rigs |

**For a deployment you only print `a1`–`a3` once, and `b1`–`b3` per satellite.**
The `c` parts are the bench rig. Pick belt-drive or direct-drive, then add the
shared flywheel either way.

The optional embossed wordmark for the shell faces is in
[`hardware/enclosure-logo/`](../hardware/enclosure-logo/), with its own README
covering slicer settings.

Print settings, the per-plate part breakdown and assembly order are in
[`3d-models/README.md`](../3d-models/README.md) and
[`BUILD_GUIDE.md` §2](BUILD_GUIDE.md).

---

## 6. Software and tools

The other half of a bill of materials. Everything here is free unless marked.

| | What | Used for | Cost |
|---|---|---|---|
| **On the board** | Debian Linux (QRB2210) and Zephyr RTOS (STM32U585) | Both operating systems, both shipped with the UNO Q | free |
| | Python 3, FastAPI, `websockets` | The application and the dashboard's live feed | free |
| | PyTorch | Training and running the per-machine autoencoder, on-device | free |
| | `ai-edge-litert` (TFLite) | Running the fetched fault classifier on CPU | free |
| | Mosquitto | The MQTT broker, hosted on the UNO Q itself | free |
| **Cloud** | Edge Impulse | Training the fault classifier, one project per asset class | free tier is enough |
| | Telegram Bot API | Phone alerts | free |
| **Development** | Arduino App Lab | Building, deploying and running both halves. Also the only place to set brick secrets | free |
| | PlatformIO | Satellite (ESP32-S3) and motor-rig (Uno) firmware | free |
| | KiCad 9 | The three schematics in [`hardware/kicad/`](../hardware/kicad/) | free |
| | A slicer (PrusaSlicer, OrcaSlicer, Bambu Studio) | The printed parts | free |
| | `adb`, `usbipd` | Getting onto the board from a Windows or WSL host | free |
| **Hardware tools** | Crimping tool for JST-XH, wire cutters, small screwdriver set | Harnesses, and setting each stepper driver's current limit | — |
| | Multimeter | Setting the driver reference voltage on the rig | — |
| | Soldering iron | Headers on the sensor breakouts | — |
| | 3D printer, 0.4 mm nozzle | The enclosures and rig parts | — |
| | USB-UART adapter | Optional: the STM32's separate debug console | low |

**Nothing in the production path needs a paid service.** Edge Impulse's free
tier covers the classifier work, and the anomaly detection, which is the part
that decides whether a machine stops, runs entirely on the board with no account
of any kind.

---

## 7. What it costs

| Scenario | Parts | Roughly |
|---|---|---|
| 1 machine monitored | Base station | $100 |
| 3 machines monitored | Base station + 2 satellites | $150 |
| 10 machines monitored | Base station + 9 satellites | $325 |

Electronics only. Filament, printer time and the validation rig are not counted,
and neither is the crimp and connector stock, which is bought in bulk and lasts
many nodes.

For context, a single unplanned bearing failure on a small CNC lathe, counting
parts, labour and two weeks of lost capacity, comfortably exceeds the ten-machine
figure.

---

## 8. Notes on substitutions

- **The accelerometer is the one part not to swap casually.** The KX134-1211 was
  chosen for two reasons: it reaches 6 kHz, which is where early bearing faults
  show up, and it has a 512-byte hardware FIFO. Without that FIFO the
  microcontroller needs servicing roughly every 39 microseconds at full rate,
  which is a hostile interrupt load for a chip that also has FFTs to run.
  Cheaper parts fail the first test; far more expensive ones buy accuracy this
  application cannot use.
- **The microphone can be any I2S MEMS part** with a 24-bit output, but the
  INMP441's pinout is what the printed shell's cutout matches.
- **DRV8825 drivers work in place of A4988** on the rig, with a different
  current-limit formula. See [`BUILD_GUIDE.md` §7](BUILD_GUIDE.md).
- **The 9 W bulb is a diffuser, not a light.** Any frosted dome of about the
  same diameter does the same job. Nothing electrical in the bulb is used.
- Links are to Indian retailers, checked in August 2026. Prices in this category
  move, so confirm at the time of purchase.

---

<!-- Purchase links. Kept out of the tables so the tables stay readable. -->

[unoq]: https://robu.in/product/official-arduino-uno-q-4gb-single-board-computer-abx00173/
[kx134]: https://robu.in/product/smartelex-triple-axis-accelerometer-breakout-kx134/
[inmp441]: https://robu.in/product/inmp441-mems-high-precision-omnidirectional-microphone-module-i2s/
[ws2812]: https://robu.in/product/8bit-ws2812-5050-rgb-led-built-full-color-driving-lights-circular-development-board/
[bulb]: https://www.amazon.in/Crompton-Dyna-Round-Cool-Light/dp/B0B6FJNH97/
[fresnel]: https://www.amazon.in/oddpodTM-Fresnel-Flexible-Plastic-Magnifying/dp/B09MDH1LLH/
[magnet]: https://patelmagnets.com/shop/od15-x-id7-x-5mm-neodymium-magnet/
[usbc]: https://www.amazon.in/Ambrane-Unbreakable-Charging-Braided-Cable/dp/B098NS6PVG/
[xiao]: https://robu.in/product/seeed-studio-xiao-esp32s3-2-4ghz-wifi-ble-5-0/
[jst8]: https://makerbazar.in/products/male-female-connector-straight?variant=46140555493616
[jst6]: https://makerbazar.in/products/male-female-connector-straight?variant=46137886703856
[jst4]: https://makerbazar.in/products/male-female-connector-straight?variant=46134456320240
[jst3]: https://makerbazar.in/products/male-female-connector-straight?variant=46134287663344
[jst2]: https://makerbazar.in/products/male-female-connector-straight?variant=46134287630576
[crimp]: https://makerbazar.in/products/2515-jst-xh-crimp-terminal-female-pins
[ribbon]: https://robu.in/product/multicolor-flat-ribbon-cable-10-cond-1meter/
[uno]: https://robu.in/product/arduino-uno-r3/
[cncshield]: https://robu.in/product/cnc-shield-v3-engraving-machine-3d-printer-a4988-drv8825-driver-expansion-board/
[a4988]: https://robu.in/product/a4988-driver-stepper-motor-driver/
[nema17]: https://robu.in/product/nema17-4-2-kgcm-stepper-motor/
[psu]: https://robu.in/product/mean-well-lrs-150-12-12v-12-5a-150w-smps/
[bearing6201]: https://in.misumi-ec.com/vona2/detail/110310367019/?HissuCode=C-E6201ZZ
[bearing6004]: https://in.misumi-ec.com/vona2/detail/110310367019/?HissuCode=C-E6004ZZ
[pla-silver]: https://robu.in/product/esun-pla-1-75mm-3d-printing-filament-1kg-silver/
[pla-orange]: https://robu.in/product/esun-pla-1-75mm-3d-printing-filament-1kg-orange/
[pla-grey]: https://robu.in/product/esun-pla-1-75mm-3d-printing-filament-1kg-grey/
[pla-gold]: https://robu.in/product/esun-pla-1-75mm-3d-printing-filament-1kg-gold/
