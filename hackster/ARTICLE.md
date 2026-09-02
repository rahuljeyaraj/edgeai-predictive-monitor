<!-- ==========================================================================
     NOT PART OF THE ARTICLE. Transcription legend for Hackster's editor.

     Hackster's editor has no markdown. It is a toolbar you click after
     selecting text. Translate this file as follows:

       line starting with #     ->  the H button (only one heading level exists)
       **bold**                 ->  the B button
       *italic*                 ->  the i button
       `backticks`              ->  the # button   (inline code)
       ``` fenced block         ->  the </> button (block code)
       line starting with >     ->  the quote button
       line starting with -     ->  the bullet button (one level, never nested)
       [text](url)              ->  the link button
       [IMAGE: ...]             ->  the image embed, caption pasted + italicised
       [VIDEO: ...]             ->  the video embed, caption pasted + italicised
       [COVER IMAGE: ...]       ->  the cover upload, AND embedded inline as well

     Two hard limits, already solved in this source:
       - Only one heading level exists. Every heading is numbered (1, 1.1) and
         gets the same H button. The number carries the hierarchy.
       - Bullets cannot nest. There is not one nested bullet in this file.

     TITLE:    EdgeAI Predictive Monitor
     TAGLINE:  Sensors that watch. An AI that decides. A hand that pulls the plug.

     COVER IMAGE: Hackster's cover image is a separate upload from the article
     body and is a listed submission requirement. Use the [COVER IMAGE] shot
     marked in section 1.2. Upload it as the cover AND leave it in place at 1.2.

     THINGS USED: Hackster's "Things used" widget is a separate UI from this
     file. Fill it from section 4.1's headline hardware plus every item in 4.2.
     Judges read the widget as the BOM. Do not leave it empty.

     WRITING STATUS: sections 1-13 drafted, two length passes done, and the
     2026-09-02 judge-review fixes applied (cover marker, 8.8, 13.2, the printed
     parts, scalability and UX named, mic pin names). Media is the only work
     left before transcription. See hackster/JUDGE-REVIEW.md and PLAN.md sec 7.
     ========================================================================== -->

# 1 What this is, and what it decides

# 1.1 The problem: the machines nobody stands next to

Ravi runs a machine shop off a highway outside town. The lathe cost him nine
lakhs, and there is a person standing at it all day. If it makes a new noise,
someone hears it within the hour.

The air compressor in the corner cost nine thousand rupees. It sits outside,
cycles on a pressure switch, and nobody has looked at it since the day it was
bolted down. When its bearing seized, every air tool in the shop stopped with it
and the shop lost two weeks.

That is the pattern worth building for. The expensive machine has a human
attached to it. The cheap ones run alone: outdoors, overnight, behind a shed, on
a roof. Machines get louder, hotter and a little wrong for days before they
stop, and somebody has to be standing there to notice.

# 1.2 What the monitor does

**EdgeAI Predictive Monitor** is a sensor pod that clips to a machine, learns
what normal feels like for that specific machine, and stops it if normal goes
far enough wrong.

[COVER IMAGE: photo of the sensor pod clipped to the running rig, status ring lit, shot close and shallow]
*One pod, clipped to one machine, watching it.*

[IMAGE: report/diagrams/01-system-at-a-glance.png]
*Sensor pods and satellite nodes feed one base station, which fans out to the dashboard, a phone, the status lights, and, on a confirmed fault, a motor-stop command.*

- **Watches.** An accelerometer and a microphone sample vibration and sound tens of thousands of times a second, at the machine.
- **Reduces.** The real-time chip turns that firehose into a compact spectrum plus a few shape statistics, several times a second, so nothing large travels anywhere.
- **Learns.** A short guided setup trains a private model of *this* machine's healthy state, on the board itself.
- **Notices.** Every new frame is scored against that baseline and lands on healthy, warning or fault.
- **Diagnoses.** A second model, trained in Edge Impulse and running on-device, names which *kind* of fault: a bearing, an imbalance, a loose mount.
- **Knows when the machine is off.** A dedicated gate separates running from stopped, so a switched-off machine reads as idle, not broken. That turned out to be the hardest measurement in the project.
- **Acts.** On a confirmed fault it stops that machine's motor, and refuses to restart it until a person clears it by hand.
- **Tells someone.** A light on the machine, a live dashboard, a phone alert.
- **Scales.** The base station watches its own machine over wires. Satellite nodes watch other machines over Wi-Fi.

# 1.3 Acting, not alerting

Most monitoring products stop at telling a human. The intelligence never touches
the physical world, it only narrates it.

The bar here is stricter: the loop from sensor reading to motor stopping closes
with no human in it, end to end, on real hardware, repeatably. On a confirmed
fault the base station counts down ten seconds in a banner on every tab,
publishes a stop naming exactly that machine's motor, and latches it. The
countdown is the operator's one chance to press **Hold**. Nobody has to be there
for it to fire.

Everything else here exists to keep that one loop honest and fast. Ravi's shop
is the excuse. The trip is the point.

# 1.4 A monitor, not a safety interlock

This needs saying before anything else, because the software turns things off.

This is condition monitoring with a protective trip. It is **not** a certified
functional-safety system:

- **No safety integrity level, no redundant channel**, and no independent watchdog on the trip path.
- **No fail-safe if the base station loses power.** If the Linux side dies, nothing trips and the machine keeps running exactly as it would have without this installed. That is correct for a monitoring device and wrong for a guard interlock.
- **Every safety function the machine already has stays where it is.** Emergency stop, guarding, overload protection: none of them answer to anything in this article.
- **Nothing here ever *starts* a machine.** There is no code path that could.

# 1.5 What is built, and what is not

**Live-verified** below means measured on physical hardware. Anything unfinished
says so in the same voice.

- **Vibration and audio sensing, base station.** Built, live-verified.
- **Guided six-step setup and the per-machine anomaly model.** Built, live-verified.
- **Multi-condition training, no load and full load.** Built, live-verified, with a measured sensitivity cost that section 6.8 states rather than buries.
- **Running/stopped gate with a measured noise floor.** Built, live-verified.
- **Trip-output mapping, confirmed by actually stopping the machine.** Built, live-verified.
- **Physical motor stop on a confirmed fault, latched.** Built, live-verified in both directions.
- **Fault-type classifier, Edge Impulse, running on-device.** Built, trained on 541 real captures from this rig. No accuracy figure is claimed, and section 8.8 says why.
- **Live dashboard: five tabs, live charts, controls, global trip banner.** Built, live-verified.
- **Status ring and on-board LED matrix.** Built, live-verified.
- **Wi-Fi onboarding via captive portal.** Built, live-verified on real phones over three rounds.
- **Satellite sensor nodes over Wi-Fi and MQTT.** Built and running on a physical XIAO ESP32-S3: Wi-Fi, MQTT, status ring, microphone and accelerometer all verified on hardware. One open bug remains in the node's own captive portal.
- **Phone alerts over Telegram.** Built and demonstrated against a real bot, currently switched off pending one config value.
- **A relay per motor, cutting electrical power rather than motion.** Not built. It is item one in section 13.

# 1.6 Three ways to read this

- **You want to see it work.** Section 2 runs the whole thing on a laptop in about ten minutes, with no hardware and nothing to buy.
- **You want to build one.** Sections 3 to 7 are the build, in order: the board, the sensor pod, the bench rig, commissioning a machine, and adding more machines over Wi-Fi. Every part has a price and a link.
- **You want to know how it works.** Sections 8 to 12 are the engineering: the fault classifier, the trip, the operator's view, the architecture, and the measured results with their limitations.

---

# 2 Trying it without hardware

Before you buy anything, run it. This is not a demo mode and not a mock.

- **Real application.** The same dashboard that runs on the board, on your laptop.
- **Real wire protocol**, spoken by the simulator.
- **Real sensor data**, replayed from captures taken on the rig.
- **Simulated: one thing only.** The sensor hardware at the far end of the wire. The registry, feature pipeline, autoencoder, setup flow, thresholds, classifier and entire frontend are the shipping code.

# 2.1 Ten minutes, start to finish

You need an MQTT broker on `localhost:1883`. The script checks for one and
deliberately will not start one for you.

```sh
sudo apt-get install -y mosquitto mosquitto-clients
sudo systemctl enable --now mosquitto

git clone https://github.com/rahuljeyaraj/edgeai-predictive-monitor
cd edgeai-predictive-monitor/base-station
./start_desktop_dashboard.sh
```

That creates a virtual environment, installs dependencies, starts the application on
**port 8180** with an isolated data directory so it can never touch a real device's
registry, starts one simulated node, and prints both URLs. Useful flags:

- `--nodes N` for several independent nodes.
- `--captures-dir DIR` to replay a different folder of captures.
- `--auto-online` to skip the first click.
- `--host 0.0.0.0` to open the dashboard from a phone on the same network.

# 2.2 What to look at, and what will be missing

The simulated node starts **offline** on purpose. Open its own control page and
press *Go Online*. That page does everything a real node does:

- Pick which capture it streams.
- Toggle the accelerometer and microphone independently.
- Switch between fused and per-axis output, and change FFT bin counts live.
- Watch its status LED change colour as the base station pushes status down to it.

Then set it up on the dashboard, the same six steps section 6 describes, and watch
it score. One thing will be missing: the `base_station` asset itself, whose data
arrives over the SPI link from the sampling chip, which does not exist on a laptop.

[IMAGE: screenshot of the desktop dashboard with one simulated node online and expanded]
*The whole dashboard, running on a laptop, fed by real recorded vibration.*

---

# 3 The board: two processors on one card

# 3.1 Four jobs that do not normally share a board

Ravi's shed has one spare socket, a lot of aluminium dust and no room for a rack.
This system has to do four things that do not normally live together:

- **Sample two sensors** tens of thousands of times a second and never miss a window. A microcontroller job, with real-time guarantees.
- **Run FFTs and statistics** on those windows continuously, without stealing time from the sampling.
- **Train a neural network** from scratch, in the field, while a technician waits. A Linux job, with a real Python stack.
- **Serve a live dashboard**, run an MQTT broker, hold a database of assets and talk to Telegram. A networked server job.

The usual answer is two or three boards and a bridge somebody has to design, debug
and power. The UNO Q is that arrangement already built and already routed, on one
power supply and one USB connector.

[IMAGE: report/diagrams/14-two-brains.png]
*What runs on the STM32U585 side, what runs on the QRB2210 Linux side, and the two links between them.*

The on-device training is the half that would be hardest to replace. Commissioning
means a technician walks up to a machine, runs it for a few minutes and expects a
trained monitor before they walk away, and that only works because the QRB2210 side
is a genuine Linux computer running genuine PyTorch. Take it away and a five-minute
walk-up task becomes a workflow with a network dependency, a queue and a
data-governance conversation.

# 3.2 What Arduino App Lab handles

App Lab is what makes the Linux half shippable rather than a pile of scripts
somebody has to remember to start.

- **One application, one deploy.** `base-station/app.yaml` declares the app's name, its icon and the ports it exposes, 8080 being the dashboard. App Lab builds it, ships it to the board and runs it in its own container.
- **Secrets stay out of the repository.** The Telegram bot token is an App Lab brick variable, `arduino:telegram_bot`, typed into App Lab's own interface, never committed and never printed. With it unset, every alert path no-ops cleanly instead of crashing.
- **Both halves in one project.** The Zephyr sketch under `base-station/sketch/` and the Python application under `base-station/python/` deploy from the same tool, which matters when a wire-format change has to land on both sides at once.

The frontend is deliberately **not** a brick: plain HTML, CSS and five
self-contained JavaScript modules, no framework and no build step, because a live
15 frames-per-second Plotly view with per-asset state was easier to keep correct
as explicit code. Bricks earn their keep where the integration is the hard part,
which here is the bot token, not the charts.

# 3.3 Three limits we found by pushing

- **The accelerometer: 12.8 kHz, not the 25.6 kHz it will do.** At full rate the sampling thread stopped yielding often enough and starved the inter-processor link outright: telemetry frames went to zero. 12.8 kHz is still eight times the original 1,600 Hz baseline.
- **The internal UART: 500000 baud, not the stock 115200.** The Linux side derives baud from a 32 MHz reference with 16x oversampling, so 1 and 2 Mbaud land on divisors of 2 and 1, where the receiver loses sampling margin. They boot beautifully and wedge twenty minutes later, the worst kind of working. 500000 lands on a divisor of exactly 4 and survived every soak test.
- **The GPU: measured, then declined.** The vendor TFLite GPU wheels need ARMv8.1 atomics this CPU lacks, so loading them kills the process with an illegal instruction, not a catchable exception. Through a Vulkan backend that does work, bit-exact against CPU, the speed-up measured roughly 1.0x from one vector up to a 256-node batch. These models are 536 numbers wide. **Staying on CPU is a finding, not a shortcut.**

---

# 4 Building the sensor pod

The simplest useful version of this system is one pod bolted to one machine. That
is the **base station**: an Arduino UNO Q with an accelerometer and a microphone
wired to it, watching one motor, showing status on a light and serving the
dashboard. Everything later in this article is this same thing, repeated. Budget
about two hours.

# 4.1 Bill of materials: base station

Prices are Indian retail, checked in August 2026, GST included. Links go to
Robu.in. This block is one per site.

- 1 x [Arduino UNO Q, 2 GB](https://robu.in/product/official-arduino-uno-q/), the board itself. Real-time sensing on the STM32U585, models and dashboard on the QRB2210 Linux side. ~ ₹6,800
- 1 x [SmartElex KX134-1211 breakout](https://robu.in/product/smartelex-triple-axis-accelerometer-breakout-kx134/), vibration sensing over SPI. ~ ₹900
- 1 x [INMP441 I2S MEMS microphone](https://robu.in/product/inmp441-mems-high-precision-omnidirectional-microphone-module-i2s/), audio sensing. ~ ₹180
- 1 x [WS2812B 8-pixel RGB ring](https://robu.in/product/8bit-ws2812-5050-rgb-led-built-full-color-driving-lights-circular-development-board/), the local status light. ~ ₹85
- 1 set of [jumper wires](https://robu.in/product-category/connectors/jumper-wire/) plus a rigid mount or magnet base. ~ ₹150
- **Base station subtotal: about ₹8,115.**

One part is printed rather than bought and is therefore not in that subtotal: the
pod's housing, which is what turns the loose parts above into something you can
bolt to a machine. Its three `.3mf` files are in the repository under
`3d-models/base_station/`.

A [4 GB UNO Q](https://robu.in/product/official-arduino-uno-q-4gb-single-board-computer-abx00173/) is a
straight drop-in. Nothing here needs it, but it is the one to buy if you plan to
train larger models on-device.

# 4.2 Software and tools

All free, and nothing here needs a paid tier.

- **Arduino App Lab.** Builds and deploys both halves of the UNO Q, and holds the Telegram bot token as a brick variable.
- **Zephyr RTOS**, via App Lab, for the STM32U585 sketch.
- **PlatformIO** for the satellite node and the motor rig's Arduino Uno.
- **Python 3 with PyTorch** on the QRB2210 side, for on-device autoencoder training.
- **Edge Impulse Studio**, a free account, for the fault classifier. Linked from inside the dashboard.
- **Mosquitto**, the MQTT broker, running on the UNO Q itself.
- **Plotly** for the dashboard charts. **KiCad** for the schematics, generated from Python under `hardware/kicad/`.
- A multimeter, for setting the stepper driver current limit in section 5.3.

# 4.3 Choosing the accelerometer

The KX134 is the vibration sensor at every sensing point here, base station and
satellite alike. Three lines decided it over both cheaper and far more expensive
parts.

- **Bandwidth, the hard filter.** Early fault signatures, micro-pitting and incipient race damage, live in the 2 to 10 kHz band. The KX134 does 25.6 kHz output; we run 12.8 kHz per section 3.3, so usable bandwidth is 6.4 kHz. That reaches into the band. Hobby parts, 1 kHz output and 250 to 500 Hz usable, do not reach it at all.
- **Noise density sets the detection floor.** Roughly 130 ug per root Hz, against roughly 300 for hobby parts. A noisy sensor raises the effective anomaly threshold before any software runs, and it is the property that mattered most in section 6.5.
- **The 512-byte FIFO changes the real-time budget.** Without it the host must service the sensor about every 39 microseconds at full rate, a hostile interrupt load for a chip also running FFTs. With it, the sensor batches and raises one interrupt per block.

Cost is the fourth line. At about ₹900 a twenty-point fleet stays affordable, where
industrial parts at ₹3,800 to ₹7,200 each do not, and those mostly arrive as
analogue signals needing an external ADC.

# 4.4 Wiring

Three peripherals hang off the real-time half of the board. Nothing at all hangs
off the Linux half.

[IMAGE: report/diagrams/02-base-station-wiring.png]
*The KX134, INMP441 and WS2812 ring all connect to the STM32U585 side. The QRB2210 side handles Wi-Fi and the dashboard.*

Pins are the UNO Q's own header labels, which is what the board is silkscreened
with and what you plug a wire into.

- **KX134, SPI SCK / MISO / MOSI:** D13 / D12 / D11, the main header SPI.
- **KX134, chip select:** D8.
- **KX134, INT1 buffer-full interrupt:** D9.
- **INMP441, SCK / WS / SD:** SCL / D10 / A4.
- **WS2812B ring, data in:** D3, a timer channel driven by DMA so the strict bit-banged timing those LEDs need never depends on the scheduler being free.

The microphone's bit clock, `SCK`, is the one signal without a D-number. The
STM32's audio peripheral brings its clock out on this board as the dedicated
**SCL** pin, so the I2C peripheral is disabled to free it and nothing here uses
I2C.

[IMAGE: report/diagrams/02b-base-station-schematic-kicad.png]
*The real schematic. It is a KiCad project under hardware/kicad/, generated from Python, not a drawing.*

# 4.5 Mounting, and why it changes what the sensor can see

An accelerometer read through a soft or loose mount is a low-pass filter you did
not ask for, and it removes exactly the high-frequency content early bearing
faults live in. You can pay for a 12.8 kHz sensor and throw the useful half of its
band away with a cable tie and a rubber pad.

Couple it rigidly to the housing, as close to the bearing as the geometry allows:
a bolted bracket on the bench rig, a magnet base on clean flat metal on a real
motor. This is the cheapest line in the bill of materials to get wrong.

The printed housing from 4.1 exists for this reason: a rigid body that carries the
sensor with the machine, rather than a board and a sensor loose on the ends of
jumper wires. The satellite node has its own equivalent under
`3d-models/satellite/`.

[IMAGE: photo of the base station wired on the bench, sensor, board and status ring in one wide shot]
*One pod: UNO Q, accelerometer, microphone and status ring.*

# 4.6 Flashing and provisioning

Open `base-station/sketch/` in Arduino App Lab and flash it to the STM32U585.
That is the whole real-time side: sampling, FFTs, the six statistics per channel,
the status ring and the LED matrix. Every sensing knob it uses is a named constant
in `base-station/sketch/app_config.h`, each one carrying the measurement or the
failure that produced its value.

Three one-time scripts then configure what lives outside the application
container. They run once per board:

```sh
cd base-station
./provision-spi.sh      # the MCU-to-MPU SPI bulk link
./provision-baud.sh     # sets the Linux side's serial link to 500000 baud
./provision-wifi.sh     # Wi-Fi onboarding: hotspot fallback + captive portal
```

`provision-baud.sh` matters more than it looks. The Linux-side router's baud must
match the firmware's, and a mismatch breaks the entire link **silently**, with no
error printed anywhere. Skip it and the board simply never says anything.

# 4.7 Deploying, and first light

```sh
cd base-station
./start_dashboard.sh
```

That builds, pushes the application, waits for its container and prints **the
board's own LAN IP URL**. Use that link, not a localhost one: a real deployment
has no port forwarding available, and testing over a forwarded USB port hides
exactly the network problems you want to find early.

Open it and the machine appears on its own. Nothing has been trained yet, that is
section 6, but the sensing half of the loop is already watchable: live vibration
and audio spectra, live time-domain traces, and a status ring that went solid the
moment real data started flowing.

---

# 5 The bench rig: something to watch, something to stop

Ravi has a compressor and a pump. I have three stepper motors, two direct-drive
and one belt-driven, and the mapping is deliberate: the direct-drive pair stand in
for pumps, the belted one for the compressor.

You do not need this rig to run the monitor. You need it to reproduce the trip,
because a trip needs a motor that software is genuinely allowed to stop.

# 5.1 Bill of materials: bench rig

Not part of a deployment. This is the setup used to induce and measure faults, and
to prove the trip.

- 1 x [Arduino Uno R3](https://robu.in/product/original-arduino-uno-rev3/), the motor controller. Receives stop commands, drives step pulses. ~ ₹1,700
- 1 x [CNC Shield V3](https://robu.in/product/cnc-shield-v3-engraving-machine-3d-printer-a4988-drv8825-driver-expansion-board/), the driver carrier board. ~ ₹200
- 3 x [A4988 stepper driver](https://robu.in/product/a4988-driver-stepper-motor-driver/), or DRV8825, one per axis. ~ ₹100 each
- 3 x [NEMA-17 stepper, 17HS4401](https://robu.in/product/nema17-4-2-kgcm-stepper-motor/), the machines being monitored on the bench. ~ ₹534 each
- 1 x [12 to 24 V DC supply, 3 A or better](https://robu.in/product-category/electronic-instruments-and-tools/power-supply/), motor power. ~ ₹700
- **Rig subtotal: about ₹4,502.**

The parts that turn three loose motors into three machines are printed rather than
bought, so they are not in that subtotal. A bare stepper has almost nothing to
measure: no rotating mass, no belt, and a vibration signature that is mostly its
own step pulses.

- **`3d-models/direct_drive_motor_rig/`**, the two pump stand-ins: a motor bracket, a shaft, a flywheel and a set of flywheel rings.
- **`3d-models/belt_drive_motor_rig/`**, the compressor stand-in: the same bracket and flywheel, plus a shaft assembly driven through a belt.

The flywheel is what gives each motor a rotating mass and therefore a signature
worth learning. It is also what the induced faults in section 8.3 act on: an added
mass off-centre makes `unbalanced`, an under-torqued bracket makes `loose`. Both
are reversible, so one rig produces those classes without destroying anything.

[IMAGE: photo of the motor rig with its three steppers, labelled]
*Three motors, one shared enable line, and one sensor pod watching them.*

# 5.2 Wiring, and the shared enable line

[IMAGE: report/diagrams/06-motor-driver-rig-schematic-kicad.png]
*Arduino Uno and CNC Shield V3, three drivers on a shared enable line, three NEMA-17 motors and the supply.*

- **Shared driver enable, `~ENABLE`:** D8, active LOW. One line for all three driver sockets. The shield has no per-motor hardware enable.
- **Motor 1 (X), STEP / DIR:** D2 / D5.
- **Motor 2 (Y), STEP / DIR:** D3 / D6.
- **Motor 3 (Z), STEP / DIR:** D4 / D7.

That first line is why the trip is a per-motor step-pulse halt rather than a
hardware disable. Pulling `~ENABLE` high de-energises all three drivers, which
would stop two healthy machines to protect one faulty one. Halting step generation
for a single axis is the only per-motor action this hardware supports, and it is
exactly the constraint a relay per motor removes.

# 5.3 Setting the driver current limit, before power

Do this before the motors ever see current. Each A4988 or DRV8825 has a small
potentiometer setting its reference voltage. Under-current skips steps.
Over-current cooks the driver, and an over-current driver is a fire risk, not just
a dead part.

- **A4988:** `Vref = Imax x 8 x Rsense`
- **DRV8825:** `Vref = Imax / 2`

Measure between the potentiometer wiper and ground with a multimeter, motors
disconnected, and set it before a motor is ever attached. The rig runs at 12 to
24 V DC and the drivers get genuinely hot in normal operation, which is expected.
Smell is not.

# 5.4 Flashing and the control page

Flash `motor-driver/src/main.cpp` to the Arduino Uno with PlatformIO, then start
the rig host, which serves the control page and receives trips:

```sh
cd motor-driver
./start_motor_driver.sh                                # broker on localhost
./start_motor_driver.sh --mqtt-host <base-station-ip>  # or over the LAN
```

The Uno's port is autodetected. Open **http://localhost:8000/** in Chrome or Edge,
click **Connect**, and pick the Uno's port.

The rig starts with **one** motor installed, which is both the honest
configuration for one shared vibration sensor and the order a real floor grows in.
Each motor added is announced to the base station as an available trip output
straight away. That is what section 6.10 needs: a real motor, on the far end of a
real message, that the monitor is allowed to stop.

[VIDEO: one real trip on the rig, motor stopping and the status ring changing]
*The whole point of the rig: a motor that software is allowed to stop.*

---

# 6 Commissioning: teaching it one machine's normal

# 6.1 Why every machine needs its own baseline

The pod is on the machine and the dashboard is up. The obvious question is *so what
does it think?*, and the honest answer is: nothing yet. It has never met this
machine.

Every frame is reduced to a fingerprint of that moment, for each of four channels,
vibration on X, Y and Z plus audio. A **spectrum** is how much energy sits at each
frequency, and a **bin** is one slice of it. Each channel gives:

- **A 128-bin spectrum**, peak-normalised, so the model learns the *shape* of the spectrum rather than how hard the machine happened to be loaded at that instant.
- **Six shape statistics** from the time-domain window: RMS, peak, crest factor, kurtosis, skewness and standard deviation. They describe what a spectrum hides, particularly impulsiveness, meaning sharp repeated knocks, which is what a failing bearing produces.

That is 536 numbers per frame, and every machine's 536 look different. There is no
universal picture of healthy to ship in the firmware, so the first thing that happens
with a new machine is not detection. It is listening.

# 6.2 The six steps, and why they are in this order

[IMAGE: report/diagrams/10-setup-flow.png]
*Name and class, measure with the machine off, collect one or more running conditions, train, confirm the trip output by really stopping the machine.*

An operator opens the machine's setup drawer and works down a short list. Four to
six minutes later that machine has its own model, its own thresholds and a tested
emergency stop.

- **1. Name and class.** Required. Machine in either state.
- **2. Off.** Machine **switched off**. Required. Produces this sensor's own noise floor, per frequency bin.
- **3. Running conditions.** Machine **running**. At least one required. Produces the training batch, the gate's running reference and labelled healthy recordings.
- **4. Train.** Required. Produces this asset's model, its normalisation statistics and its two thresholds.
- **5. Trip output.** Machine running, then stopped by us. Optional. Produces a *confirmed* answer to "which motor stops this machine".
- **6. Done.** A summary. The asset goes live.

Two words used throughout: an **asset** is one monitored machine, and the **gate** is
the logic that decides whether a machine is turning at all.

The order is not cosmetic. Step 3 relies on the gate to know the machine is really
running while it collects, and the gate can barely tell running from stopped until
step 2 has measured this sensor's floor. Collect step 3 first and you have trained on
frames selected by a gate that does not work.

# 6.3 Step 1: name and class, both mandatory

**The name** is what a Telegram alert and the trip banner print. *"Tripped:
esp32-a4cf12, 02:40"* is the wrong thing to read at 02:40.

**The class** is what recordings are grouped by. Making it optional would mean a
silent conditional branch through the rest of setup: recordings quietly not saved,
discovered weeks later by somebody trying to train a classifier and finding nothing
there. On day one the class is free text; after that it is a pick-list of the classes
that exist, which stops one shop accumulating `pump`, `Pump` and `water pump`.

# 6.4 Step 2: measuring the machine switched off

This is the one instruction no computer can check. Nothing in the software can
confirm a machine is actually off, so the screen says so rather than pretending
otherwise: *"Switch the machine off. Confirm it has stopped moving, then Start.
Nothing here can check that for you. A measurement taken while the machine runs
teaches the system that its own vibration is silence."*

It captures at least 30 frames of whatever the sensor reads with nothing turning, and
fits a median floor **per frequency bin**. Not one number for the whole spectrum. One
number for each bin, on each channel.

Get this wrong and nothing downstream announces it. The machine simply never reads as
stopped again, and the trip can never confirm, until somebody re-measures. It takes
thirty seconds and it is the step people are most tempted to skip.

# 6.5 Why step 2 exists: the sensor's own noise floor

The first gate did the obvious thing: average vibration energy across the whole
spectrum, call the machine running if the number is high. It worked in early testing
and then quietly stopped being trustworthy. Stopped and running measured **1.18x**
apart in the worst case. You cannot threshold on a 1.18x gap, and a gate you cannot
trust means a trip that can never confirm.

The cause was the sensor, not the machine. An accelerometer sensitive enough to catch
a bearing starting to fail also has a broadband electrical hiss of its own, present
whether the machine is on or off. The motor's actual mechanical signature is a
handful of narrow lines below about 600 Hz: at 90 RPM, 200 full steps per revolution
puts the step rate at 300 Hz, landing on bins 5 to 7 and almost nowhere else.

So the gate was averaging over 384 bins, 128 on each of three axes, of which maybe
three carried the motor. The other 381 were the sensor listening to itself. We had
built a very sophisticated and expensive way of measuring an accelerometer.

# 6.6 The fix, and the result that made no sense

The fix is step 2. Measure the floor with the machine deliberately off, per bin, and
count only the **excess** over that floor as real signal. The floor is a median
rather than a mean, which stops one stray frame lifting it.

Measured live, in raw sensor energy units. The two energy figures are session means;
the margin is the worst case across that session, which is the number a threshold
actually has to survive.

- **Full-spectrum average:** stopped 7,480, running 11,137. Worst-case margin **1.18x**.
- **Excess over a measured baseline:** stopped 1,414, running 6,194. Worst-case margin **2.09x**.

The obvious alternative was measured too, and it is the least intuitive result in the
project. **Band-limiting** the gate to bins 0 to 7, the motor's own frequency range,
separated **worse**: 1.09x against 2.09x. The noise floor is *tallest* in exactly the
low bins where the real signal also lives, so narrowing the window does not escape it.

The transferable principle: do not hardcode a number that is supposed to mean "this
machine is running". Measure it, per machine, per sensor.

# 6.7 Step 3: running conditions, and where the frames go

A machine does not have one healthy state. A pump idling and a pump at full head
vibrate differently, and both are fine. Show the model only one and the other reads
as faulty every time the shift changes what the machine is doing.

So step 3 collects **named conditions**, not one batch. *Running* is the default and
the only mandatory one; an operator can add *No load*, *Full load* or anything they
type, each collecting its own 50 frames or more. Those frames go three places at once.

- **Pooled into one training batch.** All conditions together, one model, one picture of healthy that spans the machine's real duty range.
- **The gate's running reference, taken from the quietest condition**, not the pooled median. A median dragged upward by a loud full-load condition would push that line above the machine's own no-load level, and a machine idling normally would read as stopped.
- **Saved as recordings**, one file per condition, all under the same label `healthy`, with the condition name alongside. Separate `healthy_no_load` and `healthy_full_load` labels would hand the classifier in section 8 two classes that both mean "fine".

# 6.8 What a second condition costs, measured

Pooling conditions widens the spread of healthy scores, and the thresholds sit
relative to that spread. So they rise and sensitivity drops. That much was expected.
How much was not. Measured on the rig, same frame counts, same everything else:

- **`slow_90rpm` alone:** warning threshold 0.146, fault threshold 0.292.
- **`slow_90rpm` plus `fast_150rpm`:** warning 0.745, fault 1.490.

**5.1x wider**, and there is a consequence you can watch happen. A 220 RPM overspeed,
2.4x the commissioned speed and an unambiguous fault, scored 1.851 and tripped in
about eleven seconds under one condition. Under two conditions it never crossed 1.490
at all.

It is still the right trade for a machine with a real duty cycle: a higher line that
never false-alarms at the start of every shift beats a tight line that cries wolf.
But it should be measured per machine, and the honest fix, per-condition thresholds,
is on the roadmap in section 13 rather than claimed as done here.

# 6.9 Step 4: training on the board

[IMAGE: report/diagrams/04-feature-pipeline.png]
*Raw window to feature vector to autoencoder to anomaly score to status.*

An **autoencoder** is a small neural network whose only job is to squeeze its input
through a narrow bottleneck and rebuild it on the other side. Train one on nothing
but a machine's healthy data and it becomes very good at rebuilding that machine's
normal, and worse at rebuilding anything else. The size of that gap is the **anomaly
score**.

Choosing that over a supervised classifier as the primary detector is practical, not
academic: **nobody has labelled fault data for a machine that has not failed yet.** A
supervised model needs examples of the thing you are trying to prevent. An
autoencoder needs only examples of the machine behaving.

The network is deliberately small, and its widths scale from the input dimension
rather than being hardcoded, so the same code fits a microphone-only node and a full
four-channel one. Training takes seconds on the board, with live progress pushed to
the browser, and the operator is told they can walk away.

# 6.10 Step 5: proving which motor stops this machine

This is the step an operator remembers, and the design decision this project is
proudest of.

The old version was a dropdown with three motors in it, because three was
hardcoded to match the bench rig. A shop with one motor saw three options, two of
them fiction, and whichever one the operator picked was never checked. You found
out whether "Motor 2" really stopped *this* machine during the first real fault,
which is the worst imaginable moment to learn it was wired to the machine next
door.

What happens instead:

- **The rig announces its own outputs** on connect, as a *retained* MQTT message, so the dashboard offers exactly the outputs that exist. One motor on day one, five when there are five, with no dashboard change.
- **The operator leaves the machine running** and presses **Test** beside a candidate.
- **The system sends a real stop.** Same command, same code path, same payload as a genuine trip. Then it watches this node's own vibration gate.
- **The machine goes quiet inside the confirm window** and the mapping is **confirmed**, stamped with the date. **It keeps running** and the test fails. Try the next one.

Three things follow, beyond deleting a dropdown:

- **The mapping is verified against physics**, not against somebody's memory of how the panel was wired.
- **It exercises the whole trip path** in daylight, MQTT topic to rig subscription to motor halt to gate confirmation, instead of during the first emergency.
- **It is honest about what it does not know.** A machine that cannot be cycled right now has a *use without testing* fallback, recorded as **unconfirmed** and labelled so on the tile. Unconfirmed is honest. A confirmed-looking guess is not.

The safety invariant survives all of it: **the only command this system can ever send
is stop.** No code path anywhere sets a speed or starts a machine.

# 6.11 From a score to a status

A single number is not a status. Two thresholds turn it into one, both from this
machine's own healthy data. Take the mean of the healthy scores, call it mu, and
their spread, call it sigma:

- **Warning** at mu plus 8 sigma.
- **Fault** at mu plus 15 sigma, with guards so fault above warning above zero always holds, even for a batch with almost no spread at all.

Those margins look enormous and are not. Healthy scores cluster very tightly right
after training on that same data, so eight sigma is a small absolute distance. From
one real session: healthy score **0.046**, warning **0.144**, fault **0.288**.

A fixed global threshold cannot work here, and this is the most common way a project
like this fails quietly. Reconstruction error is measured in units set by that
machine's own spectrum, so what reads as healthy on one motor reads as a fault on
another. Crossing the line once is not enough either: a fault has to persist across
consecutive frames before the status changes.

---

# 7 Adding machines over Wi-Fi

The borewell pump sits behind the shed. The dust blower is on the roof. Neither is
within cable reach, and nobody is running conduit across a yard for a monitoring
system.

**Satellite nodes** are the answer: a small self-powered pod with the same
accelerometer, microphone and status ring as the base station, watching its own
machine and reporting over Wi-Fi. The dashboard does not distinguish wired machines
from wireless ones.

Wi-Fi rather than Bluetooth, and both BLE options were looked at. Advertise-only BLE
is one-way, disqualifying the moment the base station has to send a stop *back*, and
BLE GATT was the original plan until BlueZ turned out to have documented problems
holding concurrent connections to more than one peripheral. Throughput settled it
anyway: 4.1 KB every 200 ms, roughly 164 kbps, is nothing on Wi-Fi and not realistic
on BLE.

# 7.1 Bill of materials: satellite node

One of these per additional machine.

- 1 x [Seeed Studio XIAO ESP32-S3](https://robu.in/product/seeed-studio-xiao-esp32s3-2-4ghz-wifi-ble-5-0/), the node's brain. Wi-Fi built in, no separate radio. ~ ₹880
- 1 x [SmartElex KX134-1211 breakout](https://robu.in/product/smartelex-triple-axis-accelerometer-breakout-kx134/), vibration sensing. The same part as the base station. ~ ₹900
- 1 x [INMP441 I2S MEMS microphone](https://robu.in/product/inmp441-mems-high-precision-omnidirectional-microphone-module-i2s/), audio sensing. The same part again. ~ ₹180
- 1 x [WS2812B 8-pixel RGB ring](https://robu.in/product/8bit-ws2812-5050-rgb-led-built-full-color-driving-lights-circular-development-board/), status light. The same part again. ~ ₹85
- 1 x [USB 5 V supply and cable](https://robu.in/product-category/electronic-instruments-and-tools/power-supply/). The whole node runs off USB. ~ ₹200
- **Per satellite node: about ₹2,245.**

Three of those five parts are identical to the base station's, deliberately. It is
one line item to buy in bulk rather than a different parts list per machine, and it
keeps the cost of growing close to linear: one machine ₹8,115, three machines
₹12,605, ten machines ₹28,320.

**That linearity is the scalability argument, and it is a hardware property, not a
promise.** Adding the tenth machine costs the same ₹2,245 as adding the second,
needs no new part number, no new firmware build and no per-unit configuration,
because a node's identity comes from its own MAC address. The software half of the
same argument is in section 8.2: models are trained per machine *type*, so the
tenth pump inherits a classifier the first nine paid for. The node's own enclosure
is printed, under `3d-models/satellite/`.

# 7.2 Wiring, and where a node's name comes from

[IMAGE: report/diagrams/03-satellite-node-wiring.png]
*The same KX134 and INMP441, on a XIAO ESP32-S3, publishing over Wi-Fi to the base station's MQTT broker.*

- **KX134, SPI SCK / MISO / MOSI:** D8 / D9 / D10, the board's fixed hardware SPI pins.
- **KX134, chip select:** D3.
- **KX134, INT1 buffer-full interrupt:** D2.
- **INMP441, WS word select:** D0.
- **INMP441, SCK bit clock:** D1.
- **INMP441, SD data out:** D4.
- **WS2812B ring, data in:** D5.

The XIAO breaks out only 11 GPIOs, so every assignment above exists to keep the
fixed hardware SPI lines free for the accelerometer, the one peripheral that
genuinely needs them.

A node's identity comes from its own Wi-Fi MAC address. There is no per-unit ID to
set, no jumper to solder and no build flag to change between units, which means
twenty nodes are twenty copies of one build.

[IMAGE: report/diagrams/03b-satellite-node-schematic-kicad.png]
*The satellite schematic, also a real KiCad project rather than a drawing.*

# 7.3 Building and flashing

```sh
cd satellite
pio run                # build
pio run -t upload      # flash over USB
pio device monitor     # optional serial console, 115200 baud
```

**There is no credential to compile in**, which is the point of the next section.
For a bench board being reflashed twenty times an afternoon there is a
development-only shortcut: pass `WIFI_SSID` and `WIFI_PASSWORD` as build flags in
`platformio.ini` and the node seeds its storage on first boot, skipping the portal.
A real build passes neither, and the shortcut is then a no-op.

# 7.4 Onboarding from a phone

This is the part most projects gloss over, and it is the difference between a demo
and something a shop can adopt.

[IMAGE: report/diagrams/09-onboarding.png]
*Power up, join its hotspot from a phone, fill three fields, it tests and switches over, and the asset appears.*

- **1. Power it up.** A node with no saved credentials raises **its own Wi-Fi access point**, named from its hardware address: `EPM-SAT-a4cf12`. Ten unconfigured nodes on a bench are ten distinguishable networks, not one collision.
- **2. Join it from any phone.** No app. The node answers every DNS query with its own address, the captive-portal trick airport Wi-Fi uses, so the phone opens the setup form by itself.
- **3. Fill in three fields.** Wi-Fi name, password, and the MQTT broker address, pre-filled with `epm-base.local`. It is a field rather than a constant because mDNS is sometimes blocked on factory networks.
- **4. It tests before it commits.** The node tries the credentials first and only writes them to storage on success, so a typo cannot strand a device on a machine you now need a ladder to reach.
- **5. It appears.** No pairing step, no ID to type. The asset shows up on the Fleet page the moment the first frame lands.

The base station onboards itself the same way, raising an `EPM-BaseStation` hotspot
and redirecting any request to its Network tab. Three rounds on real phones produced
two fixes desk testing never would have:

- **The network list is tappable buttons, not an autocomplete field.** An earlier `<datalist>` version rendered unreliably or not at all on mobile, leaving the operator typing an SSID from memory beside the machine.
- **The warning that the page may close sits above the Connect button.** The device's own network switches out from under the page too fast to read a message that appears afterwards.

[IMAGE: photo of a satellite node powered and running on a second machine]
*One node, one machine, no cable back to the base station.*

# 7.5 One frame format for every node

A satellite and the base station's own sensing half speak an identical language on
the wire: the same generic *here is a channel, here is its spectrum, here are its
statistics* frame, whether it arrives over the internal SPI bus or over MQTT from
across the yard. That buys three things.

- **The scoring pipeline never learns which kind of node a machine is behind.** It routes, validates and scores one frame shape, so there is no wired path and wireless path to keep in step.
- **Adding a satellite is a wiring-and-power task, not a software task.** Nothing on the base station changes when the eleventh machine arrives.
- **It is what makes one classifier per machine type possible at all.** A pump watched by the base station and a pump watched by a satellite hand the model data shaped exactly the same way, which is the premise of section 8.

---

# 8 Naming the fault

The shop has two pumps now, the borewell one behind the shed and the coolant pump
inside. Different units, bought years apart, failing the same ways.

# 8.1 Why there is a second model

Healthy, warning and fault answer *whether* something is off. They do not answer
*what*, and "what" is the difference between stopping everything and ordering a
bearing for Thursday.

So there is a second model alongside the per-machine autoencoder: a supervised
classifier that names the fault category it is hearing, bearing wear, imbalance, a
loose mount. It runs on-device, on the same 536 numbers the anomaly model sees, and
its result appears next to the machine's status as its own chip.

One boundary, structural rather than a policy someone has to remember. **The
classifier names faults. It never decides whether to stop a motor.** The trip in
section 9 runs off the anomaly gate alone and has no code path that reads a
classification. If the classifier is wrong, the machine still stops and only the
label is wrong: a bad afternoon rather than a bad outcome.

# 8.2 One model per machine type, not per machine

[IMAGE: report/diagrams/11-edge-impulse-flow.png]
*Record on the machine, upload from the dashboard, train in Studio, fetch the built model back onto the board.*

The two models are built in opposite directions, deliberately.

- **The anomaly model is per machine**, because it models *this unit's normal*, and normal is a property of one physical installation: this pump, this mount, this bearing at this age.
- **The classifier is per machine type**, because what separates a bearing fault from an imbalance is a property of the *fault*, not of the unit. Pooling every pump's fault data gives it far more to learn from than any one pump could.

The asset class typed in step 1 of setup is what recordings are grouped by. One
Edge Impulse project is linked per class, and the model that comes back applies to
every asset in it: train once, cover every pump in the shop. The normalisation
baseline is fitted from every recording pooled across the class, so five identical
pumps with slightly different mounts do not each drag the model their own way.

# 8.3 You cannot buy a broken machine

Getting labelled fault data is the part no amount of software solves. Nobody sells
a compressor with a failing bearing, and destroying a working machine to record one
is expensive and slow.

What we had was the family's old Ultra wet grinder: repaired repeatedly over the
years, then given up on and left in a corner. Taking it apart, an electrician
pulled three rusted ball bearings out of it, two from the motor and one from the
drum. You can feel the cracks by turning one in your hand.

[IMAGE: hackster/assets/IMG20260901093223.jpg]
*The grinder fully apart: belt pulley, stator, rotor, drum shaft, and the three bearings that came out of it.*

[IMAGE: hackster/assets/IMG20260901093820.jpg]
*One 6004-2RS out of the motor beside a new one. This is what a dying bearing looks like.*

It is also the right shape of machine: a motor driving a belted drum, which is
Ravi's compressor in miniature, and real wear from something that actually died of
it rather than damage manufactured for a demo. The other two fault classes are
induced on the rig and repeatable on purpose: `unbalanced` is a known mass mounted
off-centre, `loose` is a deliberately under-torqued mount.

[IMAGE: hackster/assets/IMG20260901093909.jpg]
*Four 6201 bearings, two worn and two new. A fault class you can hold.*

# 8.4 Recording a labelled capture

Every machine's row has a **Record** drawer: type a label, optionally set a frame
count, press Start. Previously used labels are offered as suggestions, which stops
a fleet accumulating `bearing`, `Bearing` and `bearing2`.

**Capture runs server-side.** Closing the drawer does not stop it. Closing the
browser does not stop it. The row's record button keeps pulsing until you come
back. That matters because the useful captures are the long ones, and nobody wants
to babysit a browser tab for four minutes beside a running machine.

The healthy class fills itself: every running condition collected in step 3 of
setup is also saved as a `healthy` recording, so commissioning the fleet builds the
classifier's largest class for free. The fault classes are the ones a human has to
go and produce. The current model is trained on **541 real captures** from this rig.

# 8.5 Linking a class to Edge Impulse

Writing a training pipeline from scratch was the wrong use of the time, and keeping
the classifier's training entirely outside this codebase is what makes it
structurally independent of the safety path.

An unlinked class card has one button, **Link to Edge Impulse**. It asks for a
username and password, plus a TOTP code if the account has two-factor turned on.
Submitting runs three REST calls rather than a redirect to Studio.

- **Create the project** for this asset class. The response carries a scoped API key, the only server-side secret this application persists, written owner-only, `0600`. The credentials used to create the project are never written anywhere.
- **Create the impulse**, from a fixed template: a `features` input block, a passthrough DSP block, a Keras learn block.
- **Set the training configuration**, layers, epochs, learning rate, batch size, from that same template every time.

The last two are identical JSON for every class and only the project ID changes, so
adding a machine type is one button rather than a Studio session.

That `features` block took two wrong shapes to find, and every local test passed
through all three. The bug existed only against the real service, which is this
project's strongest argument for testing against the thing itself.

# 8.6 Uploading, and four ways to get it wrong

Tick the recordings on a class's card and press **Upload**. Four things happen
underneath that the button does not suggest, and each was a real mistake first.

- **The scalar tail is standardised before upload**, because live inference standardises it before scoring. Uploading raw vectors would train the classifier on a different distribution than it meets at runtime: train/serve skew, a model that tests beautifully and behaves oddly on the machine.
- **The baseline is pooled across the class, not per node.** An earlier version standardised each capture against its own node's commissioning statistics, which silently made five identical pumps inconsistent with each other.
- **It is fitted on the train split only.** Fitting normalisation statistics over the test rows too is a small, respectable-looking way to leak.
- **The train/test split is contiguous.** Each fault condition here is one continuous capture, so a random split would put near-identical adjacent windows on both sides of the line. The last portion of each file is reserved for test. A real limitation of one-capture-per-class data, stated rather than papered over.

# 8.7 Training in Studio, and fetching the model back

The dashboard used to have a Train button. It was removed on purpose. Everything
after "the data is in the right project", DSP tuning, model architecture, reading a
confusion matrix, is work Studio is better at than a button in somebody else's
dashboard, and automating it would mean freezing one architecture forever. The card
links straight to that class's project.

**Fetch trained model** is the one piece of glue that has to exist. It runs a build
job in Edge Impulse, downloads the deployment archive, pulls the single `.tflite`
out and saves it under the asset class's name. It is a background job, because an
Edge Impulse build is real minutes, and it streams named stages to the browser:
*building*, *downloading*, *done*. Refresh mid-job and the card still shows it
running, because job state lives on the server. From the moment that file lands,
**every asset of that class is being classified**, with no restart and no per-node
action.

Two notes on where that model actually runs:

- **TFLite on the CPU via XNNPACK, deliberately.** Section 3.3 has the GPU measurement; the other half is that there is no NPU to target, because this board exposes only the audio DSP's FastRPC channel and Qualcomm's own product brief gives CPU and GPU as the sanctioned AI path for this part.
- **No SDK, no HTTP library beyond `urllib`.** The device side is plain REST over Python's standard library, which on a board where an unbuildable wheel already killed the GPU path is not a small consideration.

# 8.8 What it scores, and why no number is printed here

Section 12 puts a number on the gate, the setup run, the trip and the per-axis
features. It does not put one on the classifier, and that omission is deliberate.

Two data-integrity bugs were found while building this pipeline, and both invalidate
everything measured before them.

- **The train/test split was leaking.** Windows cut from the same source capture were landing on both sides of it, so the model was partly being tested on data adjacent to what it trained on. The fix is the file-level, contiguous-tail split in section 8.6.
- **The signal-loading step was corrupting data**, in the code path used to prepare every upload. Not one run: every dataset uploaded up to that point.

When those were fixed, accuracy was still improving with tuning. Another round of
tuning was not what the project needed most, though: the gate, the trip, the
dashboard and the satellite bring-up were, and that is where the remaining time
went. So the honest position is that the pipeline is built and the model runs
on-device on real captures, **and the current model has not been re-scored since
those fixes landed.** Every figure from before them is discarded rather than
quietly reused, and stating that is better than printing a number this project
cannot stand behind.

What can be said without a number: the classifier's ceiling here is set by data,
not architecture. Each fault class is close to one continuous capture, which is why
the split has to be contiguous, and that is a real limitation of one rig rather
than a tuning problem. Item 6 in section 13.1 is the fix, and it is more recording,
not more epochs.

The boundary in 8.1 is what makes this safe to say out loud rather than paper
over. A classifier with no current score still cannot fail dangerously, because it
has no path to the trip.

---

# 9 The trip: stopping a motor

02:40 on a Tuesday. The tank pump is running on its night timer and nobody is in
the building. Its anomaly score has been drifting up for two days, nothing a person
would have caught by ear, and it crosses the line. The system does not send an
email and hope.

# 9.1 The trip chain

[IMAGE: report/diagrams/07-trip-sequence.png]
*Fault confirmed, countdown, trip published, motor stopped, then either confirmed tripped or reported as a failed trip.*

Five steps, each deliberately boring.

- **1. Fault confirmed.** The score has stayed over this machine's fault threshold across consecutive frames, and this asset has a motor armed against it. Protection is armed **per asset**, never fleet-wide: most monitored points have no actuator, and arming one is the explicit choice made in step 5.
- **2. A ten-second countdown**, in a banner at the top of every tab, with a **Hold** button. This is the operator's only chance to intervene.
- **3. The trip is published** over MQTT, naming exactly which motor. One motor, one asset: the dashboard refuses to point two assets at the same motor, because a trip from either would then look like it came from both.
- **4. The motor stops.** A listener on the rig halts that one axis and latches it. The other motors, if healthy, keep running.
- **5. Confirmation, or an honest failure.** The vibration gate watches for the machine actually going quiet. If it does, the asset becomes **Tripped**. If it does not, the status stays **Fault** and is explicitly marked as a failed trip.

The first time this ran end to end on real hardware, the rig's console said all of
it:

```
TRIP RECEIVED: stopping motor 1...
motor 1 stopped
```

# 9.2 Ten seconds, which is longer than an industrial relay

A protection trip with no delay is a nuisance trip: one transient and the shop
stops. Real machinery-protection relays delay for exactly this reason, typically
one to three seconds, because a momentary excursion has to persist to be believed.

Ten is longer than that on purpose. The delay is not only there to filter noise, it
is the window in which the decision becomes *legible to a human*: counting down on
a screen, naming the machine, with a button that stops it. An automatic action
nobody can see coming is a worse product than one that announces itself for ten
seconds first.

# 9.3 Latching, and the button that does not exist

A system that re-arms itself a second later is not a safety system, it is a very
anxious light switch. The stopped motor refuses every later speed command,
including from the rig's own control panel, until a person clears it from the
dashboard. That is what separates protection from control.

There is also a deliberate absence: **there is no reset protection button.**
Restarting the machine is what clears things, and restarting makes frames score
again, so the score alone decides where the asset lands.

- **Fix the fault** and it returns to healthy.
- **Do not**, and it goes back to fault and trips again.

An operator cannot restart their way out of a real fault, and nothing here ever
restarts a machine on its own. One note on scope, from section 1.4: **the trip stops
motion, not power.** A stopped stepper is still an energised stepper.

# 9.4 Refusing to claim a trip that failed

If the trip is published and the machine keeps turning, the system does **not**
report it as tripped. The asset stays in Fault, and the banner says the trip
failed, in red, and cannot be dismissed.

Showing "stopped" for a machine that is still turning is the single most dangerous
lie this dashboard could tell, and no amount of "well, we sent the message" changes
that. It is the one status here derived from physics rather than from having sent
something.

# 9.5 Idle versus tripped, and one line of code

Both mean the machine is not turning. Collapsing them into a single *stopped*
status would erase the only distinction an operator cares about: whether this was
expected.

A machine stopped by the setup test in step 5 lands on **Idle**. We stopped a
healthy machine on purpose, and recording that as a trip would leave a fake trip in
that machine's history forever. A fault-driven stop lands on **Tripped**. The whole
distinction is `target = TRIPPED if was_ours else IDLE`, and it is the difference
between a maintenance record you can trust and one you cannot.

---

# 10 The operator's view

06:15. Ravi opens the shed. Before his jacket is off he already knows something
happened: the ring on the pump is blinking slow red, and there is a message on his
phone from the middle of the night.

Nobody is standing at the machine when a trip happens, which is the entire point,
so the system talks back on three independent channels: a **light on the machine**
for whoever walks past, a **live dashboard** for whoever is checking, and a **phone
alert** for whoever needs to know without checking anything. None of them requires
another to be working.

The user this was designed for is not an engineer at a desk. It is whoever is in
the shed at 06:15, and every interface decision in this section follows from that:
status readable at a glance from across a room, the one urgent thing never behind a
click, setup as a guided wizard rather than a configuration file, and onboarding
done from a phone with no app to install.

# 10.1 The trip banner, the one thing never behind a click

Everything else in this section is somewhere you navigate to. The banner is not: it
sits **above the tab bar**, on screen on Fleet, Classifier, Network, Performance
and Alerts alike, one line per affected asset.

- **Counting down.** *Pump 1, tripping in 8s*, with a **Hold** button. Not dismissible: it is still true and still needs a decision.
- **Trip failed.** *Pump 1, trip failed, machine still running*. Not dismissible, and the most severe thing this system can say.
- **Tripped.** *Tripped, Pump 1 at 02:40, confirmed stopped*. Dismissible, because the event is settled.
- **Faulty but unarmed.** *Blower, faulty, no trip output wired*. Dismissible, quieter, and no Hold button, because there is nothing to hold.

The countdown used to live inside an expanded asset row. Ten seconds is not enough
time to remember which asset it was, find its row, expand it and scroll. The rule
now is **cold configuration in the drawer, hot state out front**: a trip countdown
is an alarm, not a setting. A dismissed line comes back if that asset's situation
changes again, so one acknowledgement never silences a machine permanently.

[IMAGE: screenshot of the trip banner mid-countdown with Hold, on the Performance tab]
*It follows you across tabs, which is the whole reason it lives above them.*

# 10.2 Ten statuses an asset can hold

[IMAGE: report/diagrams/06-asset-lifecycle.png]
*New, Collecting and Training, then the live-scored Healthy, Warning and Fault, plus Idle, Tripped, Paused and Offline.*

- **New.** Streaming data, never set up. Nothing to score against.
- **Collecting.** Setup in progress, with live progress on the row: *Running 41/50*.
- **Training.** The batch is closed and the model is being fitted, with a live percentage.
- **Healthy.** Scored, comfortably below this machine's own warning line.
- **Warning.** Over the warning line. Something changed, nothing has been decided.
- **Fault.** Over the fault line, sustained. With a motor armed, this starts the countdown.
- **Idle.** Not turning, and *a person* stopped it. Normal, and set by the gate.
- **Tripped.** Not turning, and *we* stopped it. Latched until cleared, and only set once the gate confirms it went quiet.
- **Paused.** Deliberately suspended for maintenance or a known noisy job. Staleness never demotes it to Offline, because it is a standing human intent.
- **Offline.** Nothing heard for 30 seconds. **Never stored**, always derived from the last frame's timestamp, so it cannot get stuck on after a node comes back.

Every legal transition is enforced in one explicit state machine, rather than by each
feature setting a status field and hoping. That closed a bug where pausing a node
mid-setup silently stole it out from under the setup session.

# 10.3 Fleet, and what an open row shows

[IMAGE: report/diagrams/08-dashboard-anatomy.png]
*Tabs, status tiles that are also filters, one row per asset, and the expanded detail panel.*

The status tiles across the top each carry a count, and each is also a **filter**.
Click *Faulty* and the list shows only faulty machines, click several to combine.
Tiles with a count of zero hide themselves, so a healthy fleet shows a short calm
row instead of a wall of zeroes.

A row is compact: nickname, node ID underneath so identity is never ambiguous after
somebody names two machines "Pump", asset class, status. Its controls change with
the status rather than greying out generically, *Set up* becoming *Train* becoming
*Training…* becoming *Re-run setup*, and on a stopped machine the disabled button
says what to do about it: *"Start the machine first."*

Open the row and the order is deliberate, top to bottom:

- **Protection.** First, because during an incident it is the most important thing on screen.
- **The live anomaly score**, with this machine's threshold lines and a half-hour scrubber. Hidden entirely for a machine with no model yet, because an empty chart is worse than no chart.
- **Fault classification**, then **live spectra** per axis and for the microphone.
- **Three collapsed panels**: all 24 scalar statistics, raw time-domain signals, and a waterfall spectrogram in 2D or 3D. None render until first opened, which keeps opening a row cheap on a phone.

[IMAGE: screenshot of an expanded asset row with the anomaly chart, classifier bars and spectra]
*Everything known about one machine, in the order an incident needs it.*

# 10.4 The other four tabs

[IMAGE: report/diagrams/13-dashboard-tabs.png]
*Five tabs, five questions, with the trip banner sitting above all of them.*

Each tab answers one question completely, under one rule: **no fact is editable in
two places.** The asset class is edited in setup and nowhere else. The trip output
is configured in setup and only read back elsewhere.

- **Classifier: what kind of fault is it?** One card per asset class: the Edge Impulse link row, the recordings table with checkboxes, an action bar driven by that selection, *Upload (N)*, *Edit label (N)*, *Delete (N)*, and the model row. A fully decommissioned class gets a de-emphasised delete-only card rather than vanishing, because silently taking four hours of labelled captures with it is how people stop trusting a tool.
- **Network: which network is the base station on?** Mode, network name and address, plus a scan and a password field. This is the page a phone lands on through the captive portal.
- **Performance: is the monitor keeping up?** One chart per CPU core, not one average, which would hide a core pinned at 100% behind three idle ones. Then memory, temperature and GPU where the board exposes them, and per asset: frames per second and percentage of the frame's time budget used. Metrics that are not available are left out, not faked and not zeroed.
- **Alerts: who gets told, and about what?** A QR code to connect a phone, then one row per subscriber with two preferences: level, warnings upward or faults only, and scope, the whole fleet or a named set of machines.

# 10.5 The light on the machine

Every base station and every satellite carries its own status ring, and the colour
alone tells the story from across the room.

- **New:** cyan, steady.
- **Healthy:** green, steady.
- **Warning:** amber, slow breathing pulse.
- **Fault:** red, fast strobe at 200 ms.
- **Tripped:** red, **slow** strobe at 1000 ms. Deliberate rather than urgent: *I already acted.*
- **Idle:** magenta, steady.
- **Paused:** mid grey. **Offline:** dark grey.

These are hand-tuned for real WS2812 LEDs and deliberately **not** the dashboard's
palette, which was tried and looked wrong: on an uncorrected WS2812 any weak
secondary channel shows up disproportionately, so a screen-friendly emerald
rendered visibly bluish and a screen-friendly red rendered pink. Idle is the one
status where ring and screen share an exact value, `#ff00ff`; it was pure blue
until a bench test showed it was indistinguishable from the cyan used for new.

Tripped reuses fault's red and differs only in strobe period, deliberately: const,
breathe and strobe is the entire vocabulary every node's firmware understands, so a
fourth blink mode would mean reflashing every node in the shop to change one light.

The base station adds the **8x13 LED matrix already on the board**, scrolling a
one-line fleet summary, counts only, worst first. `FFLT,WWRN,OOFF,HOK` reads as one
fault, one warning, one offline, the rest healthy. Idle and Paused are excluded,
because that display answers one question, *is anything wrong*.

[IMAGE: photo of the status ring in several colour states and the LED matrix mid-scroll]
*Colour from across the room, counts up close.*

# 10.6 The phone alert

Scan the QR code on the Alerts tab once and a confirmed fault arrives as a Telegram
message carrying the machine's nickname and, when there is one, the classifier's
read.

- **No account, no invite code, no bot username to remember.**
- **The link expires.** A one-time token with a fifteen-minute life, so an old screenshot of the QR code is not a permanent key to the fleet's alerts.

This was built and demonstrated against a real bot and a real phone. It is switched
off in the current build for exactly one reason: the bot token is a managed App Lab
secret that has to be re-entered through App Lab's interface, and the on-device build
fails if the secret is declared with no value behind it. Nothing about the feature is
unfinished. A value is missing.

[IMAGE: screenshot of a real Telegram fault alert]
*The channel that reaches somebody who is not looking at anything.*

---

# 11 How it works inside

This section is the short version. The full architecture, layer by layer, is in
`report/REPORT.md` in the repository.

# 11.1 Three kinds of board, and only one of them thinks

[IMAGE: report/diagrams/05-full-architecture.png]
*One base station, any number of satellites, one motor rig. The thinking happens in one place.*

The base station's Linux side holds the asset registry, trains and runs the models,
serves the dashboard and decides to stop a motor. Every other board is a sense organ
or a muscle. The motor rig is not a peer: it accepts *stop* and nothing else.

[IMAGE: report/diagrams/12-software-architecture.png]
*Five layers on the Linux side. Nothing skips a layer.*

Roughly 15,700 lines of Python, alongside 9,100 of frontend, 4,300 of Zephyr
firmware, 3,600 of ESP32 firmware and 11,300 of tests, in five layers: **transport**
(bytes off a wire into a frame), **ingest and route** (match a frame to an asset,
validate, build the 536-number vector), **decide** (running or stopped, how unlike
normal, which fault), **remember** (live record, durable history, recordings), and
**act and tell** (the trip, the dashboard, the phone).

# 11.2 Three decisions that shaped it

- **One frame format, two transports.** The reducing chip runs a 512-point FFT per channel and average-pools to 128 bins before anything leaves it, because raw audio and vibration at native rate would saturate any link worth having. It arrives over internal SPI at 10 to 14.5 KB every 64 ms, or over MQTT at 4.1 KB every 200 ms. The scoring pipeline never learns which.
- **Two links, not one.** **LPUART1 at 500 kbaud** carries the control plane; a **dedicated SPI bus at about 40 MHz** carries bulk telemetry. Splitting them was a fix, not an optimisation: at around 65 KB/s of continuous frames the shared link's framer wedged and took the control channel with it.
- **The registry is the only thing that fans out.** Nothing writes a status directly and nothing subscribes to the pipeline. One state machine feeds the registry, which pushes to the dashboard, status ring, LED matrix, Telegram and protection at once. A new output subscribes to the registry rather than editing the scoring path, the one place a mistake produces a wrong answer about a machine.

The MQTT broker runs **on the UNO Q itself**. A satellite bolted to a compressor has
nowhere else to publish, and a broker on somebody's laptop means the fleet stops when
that laptop closes.

---

# 12 Measured results

# 12.1 What "verified" means here, and the gate measured

Every number in this section was measured on the real rig: sensors reading a
spinning motor, a trip actually stopping that motor, a dashboard checked against a
live device in a real browser.

Per-bin accelerometer energy, sensor stationary versus the rig spinning at 90 RPM,
in the sensor's own raw units:

- **~131 Hz:** stopped 13,192, running 36,134. Delta **+22,942**.
- **~281 Hz:** stopped 12,680, running 44,798. Delta **+32,118**.
- **~381 Hz:** stopped 13,586, running 40,638. Delta **+27,052**.
- **~631 Hz:** stopped 13,453, running 13,545. Delta +92.
- **~1,231 Hz:** stopped 11,217, running 11,482. Delta +265.
- **~3,231 Hz:** stopped 5,525, running 5,483. Delta −42.

The motor's entire mechanical signature is those first three rows. Everything above
about 600 Hz is the accelerometer's own broadband noise, present identically whether
the machine runs or not. That is the whole argument of section 6.6, in numbers: a
full-spectrum average gives a **1.18x** margin between stopped and running, excess
over a measured baseline gives **2.09x**.

# 12.2 A full setup run, and the trip

Straight off one session on the real UNO Q and rig:

- **Stopped baseline:** 65 frames with the rig confirmed physically off. Fitted energy reference **1,533.1**, measured spread **1.39x**, gate threshold **2,682.9**. The node went from flapping between fault and warning at rest to settling cleanly on **Idle**, and left Idle the moment the rig spun up.
- **Trained against the running rig:** healthy anomaly score **0.046**, warning threshold **0.144**, fault threshold **0.288**. Ramped down again, it returned to Idle, not Fault. Zero browser console errors throughout.
- **The trip, verified repeatedly, not once.** Motor spinning, fault confirmed, countdown, **motor stops**, stays stopped, refuses further speed commands until cleared. Cleared and spun back up, it resumes and is re-scored from scratch.
- **Both directions in one session.** The setup confirmation test and a genuine fault-driven trip ran against the same output minutes apart, the first landing on Idle and the second on Tripped, exactly as intended.
- **A second session ran all six steps end to end**, including the confirm-by-stopping test passing against the right output, correctly failing against a wrong one, and correctly refusing to run against an already-stopped machine, plus all four trip-banner states on all five tabs.

Two honest notes from those runs:

- **The countdown started and cancelled three times** before the trip finally fired, because the score was bouncing right at the fault threshold. Correct behaviour, since a fault has to persist to be believed, but a visibly twitchy banner.
- **An earlier version had a genuine race** that could report a working trip as failed. Found on hardware, fixed, and re-tested both ways.

# 12.3 Per-axis beats fused, decisively

An offline harness replays real captures through the feature pipeline and sweeps its
parameters. Two findings changed the design.

- **Per-axis versus fused:** **+38.5 sigma** worst-case fault separation, against **+1.8 sigma** for a combined tri-axial magnitude, on the same captures. That is why the model consumes `accel_x`, `accel_y` and `accel_z` separately.
- **The six statistics carry more than expected:** adding them took healthy-versus-imbalance separation from roughly **3 sigma to roughly 80 sigma**. A spectrum alone was leaving a great deal on the table.

# 12.4 Known limitations

- **The bench rig's three motors share one vibration sensor.** Trip one while the others run and that sensor still honestly reads *running*. A property of one sensor covering three motors, not a software defect, and why the rig starts with one motor. A real deployment has one sensor per machine.
- **Multi-condition training costs sensitivity**, by a measured **5.1x** on this rig. Section 6.8 has the numbers; per-condition thresholds are item 3 in section 13.
- **A score sitting exactly on the fault threshold makes the countdown flap.** Correct behaviour, unpleasant to watch, fixable with hysteresis.
- **The classifier is not the safety path**, by construction, per section 8.1.
- **The classifier carries no current accuracy figure.** Two data-integrity bugs invalidated every earlier measurement and the model has not been re-scored since they were fixed. Section 8.8 states this rather than quoting a stale number.
- **The trip stops motion, not power**, per section 9.3.
- **Fault classes blur above roughly 1.2 kHz on this rig.** A direct consequence of section 12.1: above the motor's own signature, every class is looking at the same sensor noise. On a machine with genuine high-frequency fault content, which this sensor can see, the constraint lifts.
- **The satellite's own captive portal has one open bug.** Wi-Fi, MQTT, the status ring, microphone and accelerometer are all hardware-verified on a physical XIAO ESP32-S3. The setup page it serves does not reliably load; the live hypothesis is that its own Wi-Fi scan kicks clients off its access point. The base station's portal is a separate implementation, verified on real phones.

---

# 13 What's next

# 13.1 The roadmap, in the order it would be built

- **1. A relay per motor.** Today's trip stops motion; a relay would remove power at the source too. Held back by a no-new-hardware constraint, not design uncertainty: the trip message, the latch and the confirmation logic would not change.
- **2. Hysteresis on the fault threshold.** Separating the enter-fault and leave-fault levels fixes the flapping countdown in section 12.2 without weakening the trip.
- **3. Per-condition thresholds.** The 5.1x sensitivity cost is the largest known weakness in the detection path. The hard part is not the thresholds, it is knowing which condition a machine is currently in, which nothing detects today and the same gate machinery is well placed to answer.
- **4. Closing the satellite portal bug**, the last gap between the satellite firmware and the base station's verification record.
- **5. A shared anomaly model per asset class.** Pre-train one autoencoder per class on pooled healthy data, and per-unit setup drops from collect-and-train to collect-and-calibrate. That would make commissioning the fortieth machine faster than the first, which is the opposite of how it works today.
- **6. More labelled fault data per class.** The classifier's ceiling is set by how much genuinely distinct fault data exists per class, and the recording workflow is now good enough that collecting it is a matter of time rather than tooling.
- **7. Fault-severity trending, not just detection.** The anomaly score is already stored durably per machine. The obvious next question after *something is wrong* is *how fast is it getting worse*, and the data to answer it is already on disk.

# 13.2 Repairing instead of replacing

There is a sustainability argument in this project that is worth stating plainly,
without inflating it.

- **A bearing caught early is a repair. A bearing caught late is a replacement.** The compressor in section 1.1 did not need a new motor when it started sounding wrong. It needed one after it seized. Everything between those two moments is the window this system exists to open, and the difference is one part against a whole machine.
- **The economics point the same way as the waste does.** A ₹2,245 satellite node is a fraction of what it watches, and it has no moving parts to wear out. The cheap unattended machines in section 1.1 are exactly the ones nobody currently instruments, and exactly the ones that get scrapped rather than diagnosed.
- **The fault data came from a machine that had already been thrown away.** The Ultra wet grinder in section 8.3 was repaired for years, then abandoned in a corner. Its three rusted bearings are the only fault data here that came from real wear rather than from a fault induced on purpose. Nothing working was broken to build this.

No number is put on avoided waste, because none was measured. The mechanism is
real, the arithmetic is not something this project is in a position to claim.

# 13.3 Closing

The compressor from section 1.1 is the machine that started this. Nine thousand
rupees, bolted down outside, running on a pressure switch, seized on a Tuesday and
took two weeks of the shop with it. Nobody was standing next to it when it began to
go, and on that machine nobody ever was.

A year on, Ravi runs more machines than he can personally watch and does not have
to. A light tells him what is fine, a phone tells him what is not, and once in a
while, at 02:40 in the morning, a motor just stops instead of grinding itself into a
repair bill.

Sensing, deciding, acting, in that order, with nobody standing over it. That was the
whole assignment.

All the code, the wiring, the firmware and the full engineering report are at
[github.com/rahuljeyaraj/edgeai-predictive-monitor](https://github.com/rahuljeyaraj/edgeai-predictive-monitor).
