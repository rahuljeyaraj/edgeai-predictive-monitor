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

     Two hard limits, already solved in this source:
       - Only one heading level exists. Every heading is numbered (1, 1.1) and
         gets the same H button. The number carries the hierarchy.
       - Bullets cannot nest. There is not one nested bullet in this file.

     TITLE:    EdgeAI Predictive Monitor
     TAGLINE:  Sensors that watch. An AI that decides. A hand that pulls the plug.
     WRITING STATUS: sections 1-5 drafted. Sections 6-13 pending.
     ========================================================================== -->

# 1 What this is, and what it decides

# 1.1 The problem: the machines nobody stands next to

Ravi runs a machine shop off a highway outside town. The lathe cost him nine
lakhs, and there is a person standing at it all day. If it makes a new noise,
someone hears it within the hour.

The air compressor in the corner cost nine thousand rupees. It sits outside,
cycles on and off by itself on a pressure switch, and nobody has looked at it
since the day it was bolted down. When its bearing seized, every air tool in the
shop stopped with it and the shop lost two weeks.

That is the pattern worth building for. The expensive machine has a human
attached to it. The cheap ones run alone: outdoors, overnight, behind a shed, on
a roof. Machines get louder, hotter and a little wrong for days before they
stop. Somebody has to be standing there to notice, and on those machines nobody
ever is.

# 1.2 What the monitor does

**EdgeAI Predictive Monitor** is a sensor pod that clips to a machine, learns
what normal feels like for that specific machine, and stops it if normal goes
far enough wrong.

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

Most monitoring products stop at telling a human. That is a real product, but
the intelligence never touches the physical world, it only narrates it.

The bar this project set itself is stricter: the loop from sensor reading to
motor stopping has to close with no human in it, end to end, on real hardware,
repeatably. On a confirmed fault the base station counts down ten seconds in a
banner on every tab, publishes a stop naming exactly that machine's motor, and
latches it. Every later speed command for that motor is refused until a person
clears it at the machine. The countdown is the operator's one chance to press
**Hold**. Nobody has to be there for it to fire.

Everything else here, the sensors, the wireless nodes, the models, the
dashboard, exists to keep that one loop honest and fast. Ravi's shop is the
excuse. The trip is the point.

# 1.4 A monitor, not a safety interlock

This needs saying before anything else, because the software turns things off.

This is a condition-monitoring system with a protective trip. It is **not** a
certified functional-safety system and it is not a substitute for one. It has no
safety integrity level, no redundant channel, no independent watchdog on the
trip path, and no fail-safe behaviour if the base station loses power. If the
Linux side dies, nothing trips and the machine keeps running exactly as it would
have without this installed. That is the correct failure mode for a monitoring
device and the wrong one for a guard interlock.

Every safety function a machine already has, emergency stop, guarding, overload
protection, stays where it is and answers to nothing in this article. And
nothing in this system ever *starts* a machine. That is a hard invariant,
enforced by there being no code path that could.

# 1.5 What is built, and what is not

Anything below described as **live-verified** was measured on physical hardware.
Anything unfinished says so in the same sentence, in the same voice.

- **Vibration and audio sensing, base station.** Built, live-verified.
- **Guided six-step setup and the per-machine anomaly model.** Built, live-verified.
- **Multi-condition training, no load and full load.** Built, live-verified, with a measured sensitivity cost that section 6.6 states rather than buries.
- **Running/stopped gate with a measured noise floor.** Built, live-verified.
- **Trip-output mapping, confirmed by actually stopping the machine.** Built, live-verified.
- **Physical motor stop on a confirmed fault, latched.** Built, live-verified in both directions.
- **Fault-type classifier, Edge Impulse, running on-device.** Built, trained on 541 real captures from this rig.
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

# 2.1 What the desktop path actually runs

Before you buy anything, run it.

The desktop path is not a demo mode and not a mock. It starts the **real**
dashboard application on your own laptop, fed by a simulator that speaks the
**real** wire protocol, replaying **real** captured sensor data from the rig.
The asset registry, the feature pipeline, the autoencoder, the setup flow, the
thresholds, the classifier and the entire frontend are the same code that runs
on the board.

What is simulated is one thing only: the sensor hardware at the far end of the
wire. Everything downstream of the frame is genuine, which is why this is the
honest way to evaluate the system and also how a large part of the fleet
behaviour in this project was developed.

# 2.2 Installing and starting it

You need an MQTT broker on `localhost:1883`. The script checks for one and will
deliberately not start one for you:

```sh
sudo apt-get install -y mosquitto mosquitto-clients
sudo systemctl enable --now mosquitto
```

Then:

```sh
cd base-station
./start_desktop_dashboard.sh
```

That creates a virtual environment, installs the dependencies, starts the
application on **port 8180** with an isolated data directory so it can never
touch a real device's registry or history, starts one simulated node,
pre-configures it, and prints both URLs.

Useful flags: `--nodes N` for several independent nodes, `--captures-dir DIR` to
replay a different folder of captures, `--auto-online` to skip the first click,
and `--host 0.0.0.0` to open the dashboard from a phone on the same network.

# 2.3 What to look at first, and what you will not see

The simulated node starts **offline** on purpose. Open its own control page,
look at its configuration, then press *Go Online*. That page lets you choose
which capture it streams, toggle the accelerometer and microphone
independently, switch between fused and per-axis output, change FFT bin counts
live, and watch its status LED change colour as the base station pushes status
down to it. Exactly what a real node does.

Then set the node up on the dashboard, the same six steps section 6 describes,
and watch it score live.

One thing will be missing: the `base_station` asset itself. Its data arrives
over the SPI link from the sampling chip, which does not exist on a laptop. Only
MQTT-driven nodes appear. That is expected, not a failure.

[IMAGE: screenshot of the desktop dashboard with one simulated node online and expanded]
*The whole dashboard, running on a laptop, fed by real recorded vibration.*

---

# 3 The board: two processors on one card

# 3.1 Four jobs that do not normally share a board

Ravi's shed has one spare socket, a lot of aluminium dust and no room for a
rack. Anything needing three boards, two power bricks and a fan is not going to
survive its first week.

This system has to do four things that do not normally live together:

- **Sample two sensors** tens of thousands of times a second and never miss a window. A microcontroller job, with real-time guarantees.
- **Run FFTs and statistics** on those windows continuously, without stealing time from the sampling.
- **Train a neural network** from scratch, in the field, while a technician waits. A Linux job, with a real Python stack.
- **Serve a live dashboard**, run an MQTT broker, hold a database of assets and talk to Telegram. A networked server job.

The usual answer is two or three boards and a bridge somebody has to design,
debug and power. The UNO Q is that arrangement already built, already routed,
sharing one power supply and one USB connector.

[IMAGE: report/diagrams/14-two-brains.png]
*What runs on the STM32U585 side, what runs on the QRB2210 Linux side, and the two links between them.*

# 3.2 Training on the device is the part that would be hardest to replace

Not the sensing. Not the dashboard. The on-device training.

Commissioning means a technician walks up to a machine, runs it for a few
minutes and expects a trained, working monitor before they walk away. That is
only possible because the QRB2210 side is a genuine Linux computer running
genuine PyTorch. It fits and trains a small dense autoencoder locally, in
seconds, with no cloud round trip and no data leaving the building.

Take the Linux half away and the design changes shape entirely: you are shipping
every machine's vibration signature to a server, waiting on a training job and
pushing a model back, which turns a five-minute walk-up task into a workflow
with a network dependency, a queue and a data-governance conversation. Take the
microcontroller half away and you lose the deterministic sampling that made the
spectra worth training on.

# 3.3 What Arduino App Lab handles

App Lab is not a nicety here. It is what makes the Linux half shippable rather
than a pile of scripts somebody has to remember to start.

- **One application, one deploy.** `base-station/app.yaml` declares the app's name, its icon and the ports it exposes, 8080 being the dashboard. App Lab builds it, ships it to the board and runs it in its own container.
- **Secrets stay out of the repository.** The Telegram bot token is an App Lab brick variable, `arduino:telegram_bot`, typed into App Lab's own interface, never committed and never printed. With it unset, every alert path no-ops cleanly instead of crashing.
- **Both halves in one project.** The Zephyr sketch under `base-station/sketch/` and the Python application under `base-station/python/` deploy from the same tool, which matters when a wire-format change has to land on both sides at once.

The honest note: the frontend is deliberately **not** a brick. It is plain HTML,
CSS and five self-contained JavaScript modules, no framework, no bundler, no
build step, because a live 15 frames-per-second Plotly view with per-asset state
was easier to keep correct as explicit code. Bricks earn their keep where the
integration is the hard part, which here is the bot token, not the charts.

# 3.4 Three limits we found by pushing

An honest hardware section says where a board's limits are, not just its
features. Three things were tested to the edge.

**The accelerometer's output rate.** The KX134 will run at 25.6 kHz. We run it
at **12.8 kHz**. At the full rate the sampling thread stopped yielding often
enough and starved the inter-processor link outright: telemetry frames went to
zero. 12.8 kHz is still eight times the original 1,600 Hz baseline, with
headroom left.

**The internal UART.** Raised from the stock 115200 to **500000 baud**, after
root-causing why higher rates failed. The Linux side derives its baud from a
32 MHz reference with 16x oversampling, so 1 Mbaud and 2 Mbaud land on divisors
of 2 and 1, right where the receiver loses sampling margin. They boot
beautifully and wedge twenty minutes later, which is the worst kind of working.
500000 lands on a divisor of exactly 4 and survived every soak test.

**The GPU.** The Adreno 702 was spiked properly rather than assumed. The vendor
TFLite GPU wheels are built for ARMv8.1 atomics this CPU does not have, so
loading them takes the whole process down with an illegal instruction, not an
exception you can catch. Through a Vulkan backend that does work, bit-exact
against CPU, the speed-up measured roughly 1.0x from a single vector up to a
256-node batch. These models are 536 numbers wide. Sending them to a GPU is
chartering a cargo ship to post a letter. **Staying on CPU is a finding, not a
shortcut.**

None of these are complaints. They are what you learn running a board hard for
weeks, written down so the next person does not have to.

---

# 4 Building the sensor pod

The simplest useful version of this system is one pod bolted to one machine.
That is the **base station**: an Arduino UNO Q with an accelerometer and a
microphone wired to it, watching one motor, showing its status on a light and
serving the dashboard. Everything later in this article is this same thing,
repeated and connected.

Budget about two hours.

# 4.1 Bill of materials: base station

Prices are Indian retail, checked in August 2026, GST included. Links go to
Robu.in. This block is one per site.

- 1 x [Arduino UNO Q, 2 GB](https://robu.in/product/official-arduino-uno-q/), the board itself. Real-time sensing on the STM32U585, models and dashboard on the QRB2210 Linux side. ~ ₹6,800
- 1 x [SmartElex KX134-1211 breakout](https://robu.in/product/smartelex-triple-axis-accelerometer-breakout-kx134/), vibration sensing over SPI. ~ ₹900
- 1 x [INMP441 I2S MEMS microphone](https://robu.in/product/inmp441-mems-high-precision-omnidirectional-microphone-module-i2s/), audio sensing. ~ ₹180
- 1 x [WS2812B 8-pixel RGB ring](https://robu.in/product/8bit-ws2812-5050-rgb-led-built-full-color-driving-lights-circular-development-board/), the local status light. ~ ₹85
- 1 set of [jumper wires](https://robu.in/product-category/connectors/jumper-wire/) plus a rigid mount or magnet base. ~ ₹150
- **Base station subtotal: about ₹8,115.**

A [4 GB UNO Q](https://robu.in/product/official-arduino-uno-q-4gb-single-board-computer-abx00173/) is a
straight drop-in. Nothing here needs it, but it is the one to buy if you plan to
train larger models on-device.

# 4.2 Choosing the accelerometer

The KX134 is the vibration sensor at every sensing point here, base station and
satellite alike. It was picked over both cheaper and far more expensive parts,
and three lines decided it.

- **Bandwidth was a hard filter, not a preference.** Early fault signatures, micro-pitting and incipient bearing race damage, live in the 2 to 10 kHz band. The KX134 reaches 25.6 kHz output, 12.8 kHz usable. Hobby parts top out near 1 kHz output and 250 to 500 Hz usable, and a band a sensor cannot see cannot be recovered downstream.
- **Noise density sets the detection floor.** Roughly 130 ug per root Hz, against roughly 300 for hobby parts. A noisy sensor raises the effective anomaly threshold before any software runs, hiding the small early signals this system exists to catch. It is also the property that ended up mattering most in section 6.4.
- **The 512-byte FIFO changes the real-time budget.** Without it the host must service the sensor about every 39 microseconds at full rate, a hostile interrupt load for a chip that is also running FFTs. With it, the sensor batches and raises one interrupt per block.

Cost is the fourth line. At about ₹900 a twenty-point fleet stays affordable,
where industrial parts at ₹3,800 to ₹7,200 each do not, and those mostly
arrive as analogue signals needing an external ADC. The KX134 is the first point
in the market where every constraint is met at once.

# 4.3 Wiring

Three peripherals hang off the real-time half of the board. Nothing at all hangs
off the Linux half.

[IMAGE: report/diagrams/02-base-station-wiring.png]
*The KX134, INMP441 and WS2812 ring all connect to the STM32U585 side. The QRB2210 side handles Wi-Fi and the dashboard.*

Pins are the UNO Q's own header labels, which is what the board is silkscreened
with and what you plug a wire into.

- **KX134, SPI SCK / MISO / MOSI:** D13 / D12 / D11, the main header SPI.
- **KX134, chip select:** D8.
- **KX134, INT1 buffer-full interrupt:** D9.
- **INMP441, SAI1 clock / frame sync / data:** SCL / D10 / A4.
- **WS2812B ring, data in:** D3, a timer channel driven by DMA so the strict bit-banged timing those LEDs need never depends on the scheduler being free.

The microphone's bit clock is the one signal without a D-number. SAI1's clock
line is brought out on this board as the dedicated **SCL** pin, so the I2C
peripheral is disabled to free it and nothing here uses I2C.

[IMAGE: report/diagrams/02b-base-station-schematic-kicad.png]
*The real schematic. It is a KiCad project under hardware/kicad/, generated from Python, not a drawing.*

# 4.4 Mounting, and why it changes what the sensor can see

Where you put the accelerometer matters as much as which one you bought.

An accelerometer read through a soft or loose mount is a low-pass filter you did
not ask for, and it removes exactly the high-frequency content early bearing
faults live in. You can pay for a 12.8 kHz sensor and throw the useful half of
its band away with a cable tie and a rubber pad.

Couple it rigidly to the housing, as close to the bearing as the geometry
allows: a bolted bracket on the bench rig, a magnet base on clean flat metal on
a real motor. This is the cheapest line in the bill of materials to get wrong.

[IMAGE: photo of the base station wired on the bench, sensor, board and status ring in one wide shot]
*One pod: UNO Q, accelerometer, microphone and status ring.*

# 4.5 Flashing the real-time side

Open `base-station/sketch/` in Arduino App Lab and flash it to the STM32U585.
That is the whole step. The sketch is the sampling, the FFTs, the six statistics
per channel, the status ring and the LED matrix.

Every sensing knob it uses is a named constant in one file,
`base-station/sketch/app_config.h`, and each one carries the measurement or the
failure that produced its current value. There are no magic numbers in there
without a paper trail, which is deliberate: section 3.4 is what happens when you
change them without one.

# 4.6 Provisioning the Linux side

Three one-time scripts configure things that live outside the application
container. They only need running once per board:

```sh
cd base-station
./provision-spi.sh      # the MCU-to-MPU SPI bulk link
./provision-baud.sh     # sets the Linux side's serial link to 500000 baud
./provision-wifi.sh     # Wi-Fi onboarding: hotspot fallback + captive portal
```

`provision-baud.sh` matters more than it looks. The Linux-side router's baud
must match the firmware's, and a mismatch breaks the entire link **silently**,
with no error printed anywhere. Skip it and nothing complains, the board simply
never says anything.

# 4.7 Deploying, and first light

```sh
cd base-station
./start_dashboard.sh
```

That builds, pushes the application, waits for its container and prints **the
board's own LAN IP URL**. Use that link, not a localhost one. A real deployment
has no port forwarding available, and testing over a forwarded USB port hides
exactly the network problems you want to find early.

Open it and the machine appears on its own. Nothing has been trained yet, that
is section 6, but the sensing half of the loop is already complete and
watchable: live vibration and audio spectra, live time-domain traces, and a
status ring that went solid the moment real data started flowing.

If you only ever build this much, you already have something most small shops do
not: a machine that can tell you it is getting sick.

---

# 5 The bench rig: something to watch, something to stop

Ravi has a compressor and a pump. I have three stepper motors, two direct-drive
and one belt-driven, and the mapping is deliberate: the direct-drive pair stand
in for pumps, the belted one for the compressor.

You do not need this rig to run the monitor. You need it to reproduce the trip,
because a trip needs a motor that a piece of software is genuinely allowed to
stop. Budget about two hours.

# 5.1 Bill of materials: bench rig

Not part of a deployment. This is the setup used to induce and measure faults,
and to prove the trip.

- 1 x [Arduino Uno R3](https://robu.in/product/original-arduino-uno-rev3/), the motor controller. Receives stop commands, drives step pulses. ~ ₹1,700
- 1 x [CNC Shield V3](https://robu.in/product/cnc-shield-v3-engraving-machine-3d-printer-a4988-drv8825-driver-expansion-board/), the driver carrier board. ~ ₹200
- 3 x [A4988 stepper driver](https://robu.in/product/a4988-driver-stepper-motor-driver/), or DRV8825, one per axis. ~ ₹100 each
- 3 x [NEMA-17 stepper, 17HS4401](https://robu.in/product/nema17-4-2-kgcm-stepper-motor/), the machines being monitored on the bench. ~ ₹534 each
- 1 x [12 to 24 V DC supply, 3 A or better](https://robu.in/product-category/electronic-instruments-and-tools/power-supply/), motor power. ~ ₹700
- **Rig subtotal: about ₹4,502.**

[IMAGE: photo of the motor rig with its three steppers, labelled]
*Three motors, one shared enable line, and one sensor pod watching them.*

# 5.2 Wiring, and the shared enable line

[IMAGE: report/diagrams/06-motor-driver-rig-schematic-kicad.png]
*Arduino Uno and CNC Shield V3, three drivers on a shared enable line, three NEMA-17 motors and the supply.*

- **Shared driver enable, `~ENABLE`:** D8, active LOW. One line for all three driver sockets. The shield has no per-motor hardware enable.
- **Motor 1 (X), STEP / DIR:** D2 / D5.
- **Motor 2 (Y), STEP / DIR:** D3 / D6.
- **Motor 3 (Z), STEP / DIR:** D4 / D7.

That first line is the reason the trip is implemented as a per-motor step-pulse
halt rather than a hardware disable. Pulling `~ENABLE` high de-energises all
three drivers, which is wonderfully efficient right up until you want to stop
exactly one of them: it would stop two healthy machines to protect one faulty
one. Halting step generation for a single axis is the only per-motor action this
hardware supports, and it is exactly the constraint that a relay per motor
removes.

# 5.3 Setting the driver current limit, before power

Do this before the motors ever see current. Each A4988 or DRV8825 has a small
potentiometer setting its reference voltage. Under-current skips steps.
Over-current cooks the driver, and an over-current driver is a fire risk, not
just a dead part.

- **A4988:** `Vref = Imax x 8 x Rsense`
- **DRV8825:** `Vref = Imax / 2`

Measure it between the potentiometer wiper and ground with a multimeter, motors
disconnected, and set it before a motor is ever attached. The rig runs at 12 to
24 V DC and the drivers get genuinely hot in normal operation, which is
expected. Smell is not.

# 5.4 Flashing and the control page

Flash `motor-driver/src/main.cpp` to the Arduino Uno with PlatformIO, then start
the rig host, which serves the control page and receives trips:

```sh
cd motor-driver
./start_motor_driver.sh                                # broker on localhost
./start_motor_driver.sh --mqtt-host <base-station-ip>  # or over the LAN
```

The Uno's port is autodetected. Open **http://localhost:8000/** in Chrome or
Edge, click **Connect**, and pick the Uno's port.

The rig starts with **one** motor installed, which is both the honest
configuration for one shared vibration sensor and the order a real floor grows
in. The empty slots add the others, and each one added is announced to the base
station as an available trip output straight away. That is what section 6.8
needs: a real motor, on the far end of a real message, that the monitor is
allowed to stop.

[VIDEO: one real trip on the rig, motor stopping and the status ring changing]
*The whole point of the rig: a motor that software is allowed to stop.*

---

<!-- SECTIONS 6-13 PENDING. Next session continues at section 6, "Commissioning:
     teaching it one machine's normal", per hackster/PLAN.md section 4. -->
