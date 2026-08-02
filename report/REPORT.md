<!-- Placeholder convention: [PHOTO: ...] [SCREENSHOT: ...] [VIDEO STILL: ...]
     [FILL IN: ...] marks a real value (name, date, receipt figure) not yet
     known. Every diagram referenced here is real and lives in diagrams/. -->

# EdgeAI Predictive Monitor

### Sensors that watch. An AI that decides. A hand that pulls the plug.

**Arduino Physical AI Challenge India 2026 — Industrial & Sustainability AI track**

| | |
|---|---|
| **Built on** | Arduino UNO Q (Qualcomm QRB2210 + STM32U585) |
| **Team / author** | `[FILL IN: team or author name]` |
| **Date** | `[FILL IN: submission date]` |
| **Source** | `[FILL IN: GitHub URL]` |
| **Demo video** | `[FILL IN: video URL]` |

---

## How to read this report

This document is written in layers. Every chapter opens with a plain-English
section that is a complete summary of that chapter on its own, and gets more
technical as it goes. **Reading only the first section of every chapter, front
to back, gives a correct and complete picture of the whole project** without a
single line of jargon.

Pick your depth:

| If you are… | Read | You will come away knowing |
|---|---|---|
| **Skimming** a stack of entries | [Chapter 1](#chapter-1-the-machine-that-never-complains-until-its-too-late) | What it is, what it does, why it is Physical AI, and that it genuinely works |
| **Judging the Arduino angle** | Chapters [1](#chapter-1-the-machine-that-never-complains-until-its-too-late) and [2](#chapter-2-why-the-arduino-uno-q) | Exactly which UNO Q capabilities this project leans on, and what it would take without them |
| **Going to build one** | Chapters [3](#chapter-3-building-the-base-station)–[4](#chapter-4-growing-the-fleet), then [Appendix A](#appendix-a-bill-of-materials), [Appendix B](#appendix-b-wiring-and-pinout-reference), [Appendix C](#appendix-c-build-one-yourself) | A parts list with buy links, a schematic, and a command-by-command build — including a path with **no hardware at all** |
| **Scoring the engineering** | Chapters [5](#chapter-5-teaching-it-what-normal-feels-like)–[11](#chapter-11-proof-not-promises) | The AI design, the protection logic, the architecture, and measured results from real hardware |
| **Curious about the hard parts** | Appendices [D](#appendix-d-sensor-selection-rationale)–[J](#appendix-j-test-suite-and-verification-record) | Why every major decision went the way it did, the dead ends, and the debugging that produced the numbers |

Two conventions used throughout, so nothing has to be taken on trust:

* Anything stated as **live-verified** was measured on the physical rig, not
  simulated and not estimated. Chapter 11 collects every one of those numbers
  in one place, and [Appendix J](#appendix-j-test-suite-and-verification-record)
  lists how each was checked.
* Anything **not** finished says so, in the same sentence, in the same voice.
  A report that only says "it works" is worth exactly as much as a machine that
  only says "I'm fine."

---

## Table of contents

**Part I — The system**

| | Chapter | What's in it |
|---|---|---|
| 1 | [The machine that never complains until it's too late](#chapter-1-the-machine-that-never-complains-until-its-too-late) | The problem · what the system does · honest scorecard · what makes this Physical AI |
| 2 | [Why the Arduino UNO Q](#chapter-2-why-the-arduino-uno-q) | Two brains, one board · the four jobs it does at once · what this would cost in parts and complexity without it |
| 3 | [Building the base station](#chapter-3-building-the-base-station) | One board, one machine · what gets wired where · first light |
| 4 | [Growing the fleet](#chapter-4-growing-the-fleet) | Satellite nodes · onboarding one from a phone · one wire format for every node |

**Part II — The intelligence**

| | Chapter | What's in it |
|---|---|---|
| 5 | [Teaching it what normal feels like](#chapter-5-teaching-it-what-normal-feels-like) | Commissioning · the feature vector · the autoencoder · thresholds · the sensor configuration envelope |
| 6 | [Naming the fault](#chapter-6-naming-the-fault) | The supervised classifier · one model per machine type · recording and labelling from the dashboard |
| 7 | [The day it stopped itself](#chapter-7-the-day-it-stopped-itself) | The trip chain · why it is delayed · why a failed trip is never reported as a trip · the calibration that made it trustworthy |

**Part III — The human interface**

| | Chapter | What's in it |
|---|---|---|
| 8 | [What the operator actually sees](#chapter-8-what-the-operator-actually-sees) | Every status an asset can hold · the Fleet page in detail · all five tabs · the light on the machine · the phone alert |

**Part IV — The engineering**

| | Chapter | What's in it |
|---|---|---|
| 9 | [Under the hood](#chapter-9-under-the-hood) | Full architecture · one frame's journey · the two chip-to-chip links · the threading pattern |
| 10 | [Why we built it this way](#chapter-10-why-we-built-it-this-way) | Nine decisions, the alternatives, and what each one cost — including why the anomaly model is per-machine but the classifier is not |
| 11 | [Proof, not promises](#chapter-11-proof-not-promises) | Measured results · a real commissioning run · known limitations · the status ledger |
| 12 | [What's next](#chapter-12-whats-next) | The near-term roadmap, in build order |

**Appendices**

| | Appendix | What's in it |
|---|---|---|
| A | [Bill of materials](#appendix-a-bill-of-materials) | Every part, every subsystem, with Robu.in links and indicative prices — the **only** parts list in this document |
| B | [Wiring and pinout reference](#appendix-b-wiring-and-pinout-reference) | Every pin on all three boards, plus the real KiCad schematics — the **only** wiring detail in this document |
| C | [Build one yourself](#appendix-c-build-one-yourself) | Command-by-command reproduction: base station, satellite, motor rig, and two paths that need no hardware at all |
| D | [Sensor selection rationale](#appendix-d-sensor-selection-rationale) | Why the KX134 beat both the cheaper and the more expensive options |
| E | [Network and transport selection rationale](#appendix-e-network-and-transport-selection-rationale) | BLE beacon vs BLE GATT vs Wi-Fi, and why the third won |
| F | [Wire protocol specification](#appendix-f-wire-protocol-specification) | Message types, both framings, QoS, and the schema that keeps three codebases in step |
| G | [Sensor configuration envelope](#appendix-g-sensor-configuration-envelope) | How fast and how finely this hardware can actually be pushed, what we run at, and why |
| H | [Motor-state gate calibration](#appendix-h-motor-state-gate-calibration) | The full investigation: three wrong layers, the real cause, and the fix that doubled the margin |
| I | [Edge Impulse classifier experiments](#appendix-i-edge-impulse-classifier-experiments) | The research history, including two data-integrity bugs caught before they could inflate a number |
| J | [Test suite and verification record](#appendix-j-test-suite-and-verification-record) | What is covered by automated tests, and how each live claim was checked |
| K | [3D-printed test rigs](#appendix-k-3d-printed-test-rigs) | Printed fixtures used to induce repeatable faults — *placeholder, to be filled in* |
| L | [Glossary](#appendix-l-glossary) | Every term used in this report, in one place |

---

# Part I — The system

# Chapter 1. The machine that never complains until it's too late

## 1.1 The problem, in one page

Ravi runs a small machine shop. Six months ago he signed a lease, and a week
later a brand-new CNC lathe came through the door — the biggest single thing he
has ever bought, and the thing his entire order book now depends on.

Machines don't send a text before they fail. They get a little louder, run a
little hotter, vibrate a little wrong — for days, sometimes weeks — and then one
Tuesday morning they don't start at all. By the time a person notices, it is
usually because something already broke. The repair is the small cost. The two
weeks of a dead machine and a slipped order book is the real one.

**EdgeAI Predictive Monitor** is a sensor pod that clips onto a machine, listens
to how it vibrates and sounds, and learns what normal is *for that specific
machine*. When it hears normal drifting away, it doesn't just put a sad icon on
a dashboard nobody is looking at. If it is confident enough, it reaches out and
**stops the motor** — before "a little wrong" becomes a seized bearing.

That last sentence is the point of this whole document. This system is allowed
to *act*, not only to observe. A dashboard that emails you is a respectable
product. A system that reaches out and stops the machine is a different category
of thing: sensing, deciding and *doing*, in one loop, with nobody required to be
watching at the moment it matters.

![System at a glance: sensor pods and satellite nodes feed one base station, which fans out to the dashboard, a phone, the status lights, and — on a confirmed fault — a motor-stop command](diagrams/01-system-at-a-glance.png)

> **[PHOTO: hero shot — the assembled base station clipped to the test rig, machine running, status ring lit]**

## 1.2 What it actually does

* **Watches.** An accelerometer and a microphone sample a machine's vibration
  and sound tens of thousands of times a second, right at the machine.
* **Reduces.** The sampling chip turns that firehose into a compact spectrum
  plus a handful of shape statistics, several times a second, so nothing large
  ever has to travel anywhere.
* **Learns.** During a short commissioning run, it trains a private model of
  what *this* machine's healthy state looks like. Training happens on the UNO Q
  itself. No two machines share a baseline, because no two machines vibrate the
  same.
* **Notices.** Every new frame is scored against that baseline in real time and
  lands on healthy, warning or fault.
* **Diagnoses.** A second, separate model names *which kind* of fault it is
  hearing — a bearing going, an imbalance, a loose mount. That one is trained
  per **machine type**, so five identical lathes share one model instead of
  five training runs.
* **Knows when the machine is off.** A dedicated gate tells running from
  stopped, so a switched-off machine reads as *stopped*, not as *broken*. This
  turned out to be the single hardest measurement in the project
  ([Chapter 7](#chapter-7-the-day-it-stopped-itself)).
* **Acts.** On a confirmed fault, it stops that machine's motor — and refuses to
  let it restart until a person clears it by hand.
* **Tells someone.** A light on the machine, a live dashboard, and a phone
  alert, so a human finds out without staring at a screen.
* **Scales.** The base station watches its own machine directly; **satellite
  nodes** watch other machines over Wi-Fi. Adding the tenth machine is a
  power-up-and-onboard job, not a cabling job.

## 1.3 Where the project actually stands

| Capability | Status |
|---|---|
| Vibration + audio sensing (base station) | Built · live-verified on hardware |
| Per-machine commissioning and anomaly model | Built · live-verified on hardware |
| Running/stopped gate with measured noise floor | Built · live-verified on hardware |
| Physical motor stop on confirmed fault, latched | Built · live-verified on hardware, both directions |
| Fault-type classifier (Edge Impulse, per machine type) | Built · runs on-device · trained on 541 real captures from this rig |
| Live dashboard (5 tabs, live charts, controls) | Built · live-verified on hardware |
| Status ring + on-board LED matrix | Built · live-verified on hardware |
| Wi-Fi onboarding via captive portal (base station) | Built · live-verified on real phones |
| Satellite sensor nodes over Wi-Fi/MQTT | Built |
| Phone alerts (Telegram) | Built · demonstrated against a real bot; currently switched off pending one config value |
| Per-motor relay (cutting electrical power, not just motion) | **Not built** — [Chapter 12](#chapter-12-whats-next) |

## 1.4 Why this counts as Physical AI

Plenty of monitoring products stop at "notify a human." That is a real product,
but the intelligence never touches the physical world — it only narrates it.

The bar this project set itself is stricter: the loop from *sensor reading* to
*motor stopping* has to close with no human in it, end to end, on real hardware,
repeatably. [Chapter 7](#chapter-7-the-day-it-stopped-itself) is that loop,
including the mistakes made getting it trustworthy enough to arm.

Everything else in this report — the sensors, the wireless nodes, the models,
the dashboard — exists to keep that one loop honest and fast. Ravi's shop is the
excuse. The trip is the point.

---

# Chapter 2. Why the Arduino UNO Q

## 2.1 One board doing four jobs at once

This system has to do four things that normally do not live on the same board:

1. Sample two sensors at tens of thousands of samples per second and never miss
   a window — that is a job for a microcontroller with real-time guarantees.
2. Run FFTs and statistics on those windows continuously, without stealing time
   from step 1.
3. **Train** a neural network, from scratch, in the field, while a technician
   waits — that is a job for a Linux machine with a real Python stack.
4. Serve a live dashboard, run an MQTT broker, hold a database of assets, and
   talk to Telegram — that is a job for a networked server.

The conventional answer is two or three boards: a microcontroller for 1–2, a
single-board computer for 3–4, and a bridge between them that somebody has to
design, debug and power. The UNO Q is that whole arrangement already built,
already routed, already sharing one power supply and one USB connector.

**What we actually use, and where:**

| UNO Q capability | What it does in this project |
|---|---|
| **STM32U585 (Zephyr RTOS)** | Samples the KX134 over SPI and the INMP441 over SAI, runs the FFTs, computes six statistics per channel, drives the WS2812 ring and the LED matrix |
| **QRB2210 (Debian Linux, quad-core)** | Trains and runs the per-machine autoencoder in PyTorch, runs the fault classifier, holds the asset registry, serves the dashboard, hosts the MQTT broker, publishes trips |
| **LPUART1 between the two** | Control-plane RPC between the halves, at 500 kbaud |
| **Dedicated MCU↔MPU SPI** | The bulk telemetry path — full-resolution spectra at ~40 MHz, so the control link never has to carry them |
| **On-board 8×13 LED matrix** | Fleet-wide health summary, readable across a workshop with no laptop open |
| **On-board Wi-Fi** | The shop network client, the MQTT broker's home, *and* a fallback access point for onboarding |
| **Arduino App Lab** | Packaging, deployment and secret management for the Linux-side application |
| **Adreno 702 GPU** | Evaluated for model inference. Confirmed working via Vulkan, and then deliberately **not** used — see [§2.3](#chapter-2-why-the-arduino-uno-q) |

## 2.2 The part that would be hardest to replace

Not the sensing. Not the dashboard. **The on-device training.**

Commissioning means a technician walks up to a machine, runs it for a few
minutes, and expects a trained, working monitor before they walk away. That is
only possible because the QRB2210 side is a genuine Linux computer running
genuine PyTorch — it fits and trains a small dense autoencoder locally, in
seconds, with no cloud round trip and no data ever leaving the building.

Take the Linux half away and the design changes shape completely: you are now
shipping every machine's vibration signature to a server, waiting on a training
job, and pushing a model back — which turns a five-minute walk-up task into a
workflow with a network dependency, a queue, and a data-governance conversation.
Take the microcontroller half away instead and you lose the deterministic
sampling that makes the spectra worth training on in the first place.

The UNO Q is one of the few boards where "sample it properly" and "train on it"
are the same purchase order.

## 2.3 What we pushed on, and what pushed back

An honest hardware section says where the board's limits are, not just its
features. Three things were tested to the edge:

* **Accelerometer output data rate.** The KX134 will run at 25.6 kHz. We run it
  at **12.8 kHz**. At the full rate, the sampling thread stopped yielding often
  enough and starved the inter-processor link outright — telemetry frames went
  to zero. 12.8 kHz is eight times the original baseline with real headroom
  left. Full numbers and the reasoning:
  [Appendix G](#appendix-g-sensor-configuration-envelope).
* **The internal UART.** Raised from the stock 115200 to **500000 baud** after
  root-causing why higher rates failed: the Linux side derives its baud from a
  32 MHz reference with 16× oversampling, so 1 Mbaud and 2 Mbaud land on
  divisors of 2 and 1 — right at the edge where the receiver loses sampling
  margin. They boot cleanly and then wedge minutes later. 500000 lands on a
  divisor of exactly 4 and survived every soak test, including one longer than
  the exposure that broke the next rate up.
* **The GPU.** The Adreno 702 was spiked properly rather than assumed. Two
  findings: the vendor TFLite GPU wheels are compiled for ARMv8.1 atomics this
  CPU does not have, so loading them kills the process outright — and via a
  Vulkan backend that *does* work, the measured speed-up stayed at ~1.0× from a
  single vector up to a 256-node batch. The models here are too small to fill
  the GPU. **Staying on CPU is a finding, not a shortcut.**

None of these are complaints. They are the kind of thing you only learn by
running a board hard for weeks, and all three are documented here so the next
person doesn't have to rediscover them.

---

# Chapter 3. Building the base station

## 3.1 One board, one machine

The simplest useful version of this system is one sensor pod bolted to one
machine. That is the **base station**: an Arduino UNO Q with an accelerometer
and a microphone wired to it, watching one motor, showing its own status on a
light, and serving the dashboard.

If you stopped right here you would already have something most small shops
don't: a machine that tells you it is getting sick before it collapses.
Everything else in this report is this same idea, repeated and connected.

> **[PHOTO: base station fully wired and clipped to the test rig — sensor, board and status ring in one wide shot]**

## 3.2 What you are building

Three peripherals hang off the real-time half of the board, and nothing hangs
off the Linux half:

![Base station block wiring: the KX134, INMP441 and WS2812 ring all connect to the STM32U585 side; the QRB2210 side handles Wi-Fi and the dashboard](diagrams/02-base-station-wiring.png)

* The **KX134-1211** accelerometer on SPI, with a chip-select and a
  buffer-full interrupt line. Its 512-byte hardware FIFO is doing real work
  here: without it the host would need servicing roughly every 39 microseconds
  at full rate, which is a hostile interrupt load for a chip that also has FFTs
  to run.
* The **INMP441** microphone on the STM32's SAI peripheral, giving 24-bit
  digital audio with no analogue front end to design.
* The **WS2812B ring** on a timer channel with DMA, so the strict bit-banged
  timing those LEDs need never depends on the scheduler being free.

The exact pin for every net is in
[Appendix B](#appendix-b-wiring-and-pinout-reference), which also carries the
real KiCad schematic. The parts, prices and buy links are in
[Appendix A](#appendix-a-bill-of-materials). Both appear exactly once in this
document, on purpose — there is one place to look for each.

## 3.3 First light

The full command-by-command build is [Appendix C](#appendix-c-build-one-yourself).
The short version is two steps: flash the sketch onto the STM32 side through
Arduino App Lab, then run one script that builds, pushes and starts the Linux
application and prints the board's own LAN URL.

Open that URL and the machine appears on its own. Nothing has been trained yet
— that is [Chapter 5](#chapter-5-teaching-it-what-normal-feels-like) — but the
sensing half of the loop is already complete and watchable: live vibration and
audio spectra, live time-domain traces, and a status ring that has gone solid
the moment real data started flowing.

## 3.4 Why there are two chips talking to each other

The STM32U585 side is close enough to the hardware to keep up with a sensor that
does not wait for anyone. The QRB2210 side has the memory and the operating
system to train a model and serve a web page at the same time. Between them run
two separate links, and the split between those two links is deliberate:

* **LPUART1** carries small control messages — remote-procedure calls, status
  pushes, statistics queries. A few numbers, often.
* **A dedicated SPI bus** carries the bulk telemetry — full-resolution spectra
  and raw diagnostic pulls. A lot of numbers, occasionally.

Keeping them apart means a large diagnostic capture can never stall the
real-time status loop that everything else depends on. That separation was not
free — it was the result of an earlier design where everything shared one link
and a bulk stream wedged the control channel. [Chapter 9](#chapter-9-under-the-hood)
has the full data path; [Appendix F](#appendix-f-wire-protocol-specification)
has the framing.

---

# Chapter 4. Growing the fleet

## 4.1 Machine number two

A year in, the shop isn't a one-machine operation. There is a second lathe, a
drill press, a compressor humming in the corner — and none of them within cable
reach of the first. Running wire to every new machine isn't a plan, it's a
standing chore.

**Satellite nodes** solve that. Each one is a small self-powered sensor pod —
same accelerometer, same microphone, same status ring as the base station — that
watches its own machine and reports over Wi-Fi. The dashboard does not
distinguish wired machines from wireless ones. A satellite just shows up as one
more asset in the list, next to the original.

![Satellite node block wiring: the same KX134 and INMP441 on a XIAO ESP32-S3, publishing over Wi-Fi to the base station's MQTT broker](diagrams/03-satellite-node-wiring.png)

> **[PHOTO: a satellite node mounted on a second machine, powered and running]**

## 4.2 Onboarding one, start to finish

This is the part most projects gloss over, so here it is in full — because it is
the difference between a demo and something a shop can actually adopt.

![Onboarding a node: power up, join its hotspot from a phone, fill three fields, it tests and switches over, and the asset appears on the dashboard](diagrams/09-onboarding.png)

**1. Power it up.** A node with no saved credentials does not sit there blinking
an error. It raises **its own Wi-Fi access point**, named from its own hardware
address — `EPM-SAT-a4cf12`. The name is unique per node, so ten unconfigured
nodes on a bench are ten distinguishable networks, not one collision.

**2. Join it from any phone.** No app. The moment the phone joins, the setup
page opens by itself. That is the captive-portal mechanism real airport Wi-Fi
uses: the node answers every DNS query with its own address, so whichever
connectivity-check URL the phone reaches for lands on the setup form instead,
and the operating system pops its browser open on it.

**3. Fill in three fields.** The shop's Wi-Fi name, its password, and the
address of the MQTT broker — which comes **pre-filled** with `epm-base.local`,
the base station's own mDNS name. It is a field rather than a constant because
mDNS is sometimes blocked on factory VLANs, and when it is, a technician needs
to be able to type an address rather than reflash a board.

**4. It tests before it commits.** Submitting does not blindly save. The node
tries the credentials first and only writes them to its non-volatile storage on
success, so a typo cannot strand a device on a machine you now need a ladder to
reach. On success its own access point drops and it joins the real network.

**5. It appears.** No pairing step, no ID to type in, no dashboard form. The
node derives its identity from its own Wi-Fi MAC and starts publishing; the
asset shows up on the Fleet page the moment the first frame lands, ready to be
named and commissioned.

> **[SCREENSHOT: the satellite setup portal on a phone, and the Fleet page with the new asset appearing]**

**The base station onboards itself the same way.** With no network saved, it
raises an `EPM-BaseStation` hotspot and runs the same DNS trick, redirecting any
request to its dashboard's Network tab — where the same "pick a network, type a
password" flow is waiting. That flow has been tested on real phones, and three
rounds of live testing went into details that only show up on real hardware: the
network list is a set of tappable buttons rather than a native autocomplete
(which mobile browsers render unreliably or not at all), and the warning that
*"this page may close when you tap Connect — that's normal"* appears **before**
the button rather than after it, because the device's own network switches out
from under the page too quickly to read a message that only appears afterwards.

## 4.3 One format, every node

A satellite node and the base station's own sensing half speak the identical
language on the wire: the same generic *here is a channel, here is its spectrum,
here are its statistics* frame, whether it arrives over the internal SPI bus or
over MQTT from across the floor.

That is deliberate, and it buys three things:

* The scoring pipeline never learns which kind of node a machine is behind. It
  routes, validates and scores one frame shape.
* Adding a satellite is a wiring-and-power task, not a software task.
* It is what makes "one trained classifier covers every lathe of this type"
  possible at all — a base-station-monitored lathe and a satellite-monitored
  lathe hand the model data shaped exactly the same way.

The frame layout, both framings and the schema that keeps the three codebases
from drifting apart are in
[Appendix F](#appendix-f-wire-protocol-specification).

---

# Part II — The intelligence

# Chapter 5. Teaching it what normal feels like

## 5.1 "Just let it run for a bit"

Before this system can say something is wrong, it has to know what *right*
sounds like — and every machine's right is different. A new lathe hums
differently from one that has run for a decade; a compressor's normal vibration
looks nothing like a drill press's.

So the first thing that happens with a new machine isn't detection, it's
listening. An operator presses **Commission**, lets the machine run its normal
cycle for a few minutes, and the system quietly builds a fingerprint of what
healthy looks like for *that one machine*. Then it presses **Train**, and about
a minute later that machine has its own model. No two machines on the fleet
share a baseline.

> **[SCREENSHOT: the Fleet row mid-commissioning, showing "Collecting 41/60" and then "Training 62%"]**

## 5.2 What "normal" means to a machine

Every incoming frame becomes a **feature vector** — a compact numerical
fingerprint of that moment, not the raw waveform.

![Feature pipeline: raw window to feature vector to autoencoder to anomaly score to status](diagrams/04-feature-pipeline.png)

For each sensing channel — vibration on X, Y and Z, plus audio — the system
keeps two very different kinds of information:

* **The spectrum.** How much energy sits at each frequency, peak-normalised so
  the model learns the *shape* of the spectrum rather than how hard the machine
  happens to be loaded at that instant.
* **Six shape statistics.** RMS, peak, crest factor, kurtosis, skewness and
  standard deviation, computed on the time-domain window. These describe things
  a spectrum alone hides — particularly impulsiveness, which is exactly what a
  failing bearing produces.

Those statistics are computed **per axis**, never on a combined tri-axial
magnitude. That is not a stylistic choice: combining X, Y and Z into one
magnitude erases the directional signature an imbalance produces, and an offline
sweep over real captures measured the difference as **+1.8σ fused versus +38.5σ
per-axis** on the same data. It is one of the highest-leverage decisions in the
whole pipeline, and it came out of measurement rather than intuition.

The result is a 536-number vector: 128 spectral bins × 4 channels, plus 6
statistics × 4 channels.

## 5.3 The autoencoder, and why an unsupervised model

An **autoencoder** is a small neural network whose only job is to squeeze its
input down through a bottleneck and rebuild it. Train one on nothing but a
machine's healthy data and it becomes very good at rebuilding that machine's
normal — and noticeably worse at rebuilding anything else. The size of that gap
is the **anomaly score**.

The reason for choosing this shape over a supervised classifier as the primary
detector is practical, not academic: **nobody has labelled fault data for a
machine that has not failed yet.** A supervised model needs examples of the
thing you are trying to prevent. An autoencoder needs only examples of the
machine behaving, which is the one thing every shop has an endless supply of.
That is what makes commissioning a five-minute job instead of a data-collection
campaign.

The network is deliberately small — a symmetric dense encoder/decoder whose
hidden and bottleneck widths scale from the input dimension rather than being
hardcoded, so the same code fits a mic-only node and a full four-channel one.

## 5.4 From a score to a status

A single number is not a status. Two thresholds turn it into one, and both are
derived from what this machine's own healthy data actually looked like:

* **Warning** at μ + 8σ of the healthy score distribution.
* **Fault** at μ + 15σ, with guards so that fault > warning > 0 always holds
  even for a degenerate near-zero-spread batch.

The margins are wide because healthy scores cluster very tightly right after
training on that same data, so a few sigma is a tiny absolute distance.

A fixed global threshold cannot work here, and this is worth being explicit
about because it is the most common way a project like this fails quietly: the
autoencoder's reconstruction error is measured in units set by that machine's
own spectrum. What reads as comfortably healthy on one motor reads as a fault on
another. Calibrating per machine is what makes a real fault actually cross the
line on the dashboard instead of sitting in the noise.

Crossing the fault line once is not enough either — a fault must persist across
consecutive frames before the status changes, so one noisy sample never cries
wolf.

## 5.5 Knowing when the machine is simply switched off

There is a third thing scoring a frame, alongside the model: a **running/stopped
gate**. Its whole job is to answer "is this machine even turning right now?"
from vibration energy alone.

It matters for two reasons. A machine switched off an hour ago should read as
*stopped*, not keep displaying whatever status it held while it was last
running. And when a trip fires, the only honest way to know it actually worked
is to watch the machine go quiet.

Getting that measurement right turned out to be the hardest single problem in
the project — the sensor's own noise floor is loud enough to look like a running
motor. That story is [Chapter 7](#chapter-7-the-day-it-stopped-itself), and the
full investigation is [Appendix H](#appendix-h-motor-state-gate-calibration).

## 5.6 How hard can you push the sensing?

A reasonable question from anyone planning to point this at a machine with a
different fault signature: what is the ceiling?

Short version — the accelerometer can run to **25.6 kHz**, giving usable
frequency content to about 12.8 kHz, and the FFT depth and how much of it goes
on the wire are both independent, tunable knobs. What we run today is one point
in that envelope, chosen for stability, not the maximum.

The full envelope — every rate, every bin count, what each costs in bandwidth
and in CPU, what happens at the extremes, and how to change it — is
[Appendix G](#appendix-g-sensor-configuration-envelope).

## 5.7 Why train here rather than in the cloud

Two reasons, in this order.

**Commissioning has to feel instant.** A technician standing at a machine will
wait a minute. They will not wait for a queued cloud job, and they certainly
will not come back tomorrow. Training locally keeps commissioning something you
start and finish in one visit.

**The data never has to leave.** A machine's vibration signature is a fairly
intimate record of how a business operates. Keeping training on-device means
"does our data leave the building?" has a clean answer — *only if you choose to
send it* — which stops being a technical question and starts being a
procurement one the moment a customer is larger than one shop.

---

# Chapter 6. Naming the fault

## 6.1 Beyond "something's wrong"

Healthy / warning / fault answers *whether* something is off. It does not
answer *what*, and "what" is the difference between "stop everything" and
"order a bearing for Thursday."

So there is a second model: a supervised classifier that names the fault
category it is hearing — bearing wear, imbalance, a loose mount. It runs
on-device, on the same feature vector the anomaly model sees, and its result
appears next to the machine's status the moment there is a fault to explain.

> **[SCREENSHOT: an expanded asset row showing the fault-classification confidence bars]**

## 6.2 One model per machine *type*, not per machine

This is the important structural difference from
[Chapter 5](#chapter-5-teaching-it-what-normal-feels-like), and it is the reason
the two models are built completely differently.

The anomaly model is **per machine**, because it models *this unit's normal* and
normal is a property of one physical installation. The classifier is **per
machine type**, because what distinguishes a bearing fault from an imbalance is
a property of the *fault*, not of the unit — and pooling every lathe's data
gives it far more to learn from than any one lathe could.

Concretely: every asset carries an **asset class** (`cnc lathe`, `conveyor
motor`, …). Recordings are grouped by that class, one Edge Impulse project is
linked per class, and the trained model that comes back applies to every asset
in it. Train once, cover the whole line.

The dashboard is built around that idea rather than bolting it on: the
Classifier tab shows **one card per asset class**, not one per node. The
normalisation baseline is fitted from every recording pooled across the class,
so units with slightly different mounting don't each drag the model in their own
direction.

## 6.3 Recording and labelling, without leaving the dashboard

The whole loop from "this machine is making a noise" to "there is a trained
model for it" happens in the dashboard:

1. **Record.** Open an asset's Record drawer, type a label (`healthy`,
   `bearing`, `unbalanced`, `loose`, with previous labels offered as
   suggestions), optionally set a frame count, and press Start. Capture runs
   **server-side**, so closing the drawer — or the browser — doesn't stop it;
   the row's record button just keeps pulsing until you come back.
2. **Select and upload.** The Classifier tab lists every recording for that
   asset class. Tick the ones you want and upload; they go to that class's Edge
   Impulse project as properly named feature columns.
3. **Train** in Edge Impulse Studio, which is genuinely better at that job than
   anything we would have written.
4. **Fetch.** One button pulls the trained model back down onto the device,
   with progress streaming live. From that moment on, every asset of that class
   is being classified.

An asset with no class assigned simply shows the anomaly score and no
classification. Nothing breaks, nothing shows an empty placeholder promising a
feature that isn't configured.

## 6.4 What the classifier is, and is not, allowed to do

The classifier **names** faults. It never decides whether to stop a motor.

That separation is structural, not a policy someone has to remember: the trip in
[Chapter 7](#chapter-7-the-day-it-stopped-itself) runs off the anomaly gate
alone and has no code path that reads a classification. If the classifier is
ever wrong about *which* fault it is, the machine still stops — the label on the
alert is just wrong, which is a bad afternoon rather than a bad outcome.

The research road to this model was long enough, and instructive enough, to be
worth its own appendix — including two data-integrity bugs that were caught
before they could quietly inflate a reported number, and one honest finding
where a classical method beat the neural one on the same data.
[Appendix I](#appendix-i-edge-impulse-classifier-experiments) has all of it.

---

# Chapter 7. The day it stopped itself

## 7.1 It doesn't just alert. It acts.

Eight months in, one of the machines starts drifting. Nothing a person would
catch by ear yet — but the anomaly score creeps past warning and keeps climbing.
Nobody is standing at the machine. The system doesn't wait for anyone to be.

The moment the fault is confirmed — a sustained reading, not one noisy frame —
a countdown starts on the dashboard, and when it runs out, a command goes out
that stops that one motor. Not a suggestion on a screen. The motor stops. And it
does not quietly start again: it refuses every later command until a person
clears it by hand.

This is the one chapter where the AI reaches past the screen and into the
physical world, and it is the reason this project can call itself Physical AI
rather than a very well-instrumented dashboard.

> **[PHOTO: the motor-driver rig — Arduino Uno, CNC Shield and the three stepper motors, labelled]**
>
> **[VIDEO STILL: a real trip on the rig — status light changing as the motor stops]**

## 7.2 The trip chain

![The trip chain: fault confirmed, countdown, trip published, motor stopped, then either confirmed tripped or reported as a failed trip](diagrams/07-trip-sequence.png)

Five steps, each deliberately boring:

1. **Fault confirmed.** The anomaly score has stayed over this machine's own
   fault threshold across consecutive frames, and this asset has a motor armed
   against it. Protection is armed **per asset**, not fleet-wide — most
   monitored points have no actuator at all, and arming one is an explicit
   choice an operator makes from the dashboard.
2. **A ten-second countdown**, visible and counting down on screen, cancellable
   with a **Hold** button. This is the operator's only chance to intervene, and
   it exists on purpose — see [§7.3](#chapter-7-the-day-it-stopped-itself).
3. **The trip is published** over MQTT, naming exactly which motor. One motor,
   one asset: the dashboard refuses to point two assets at the same motor,
   because a trip from either would then look like it came from both.
4. **The motor stops.** A listener on the rig halts that one axis and latches
   it. The other motors, if healthy, keep running.
5. **Confirmation, or an honest failure.** The vibration gate watches for the
   machine actually going quiet. If it does, the asset becomes **Tripped**. If
   it doesn't, the status stays **Fault** and is explicitly marked as a failed
   trip.

Captured off the real hardware the first time this ran end to end:

```
TRIP RECEIVED: stopping motor 1...
motor 1 stopped
```

## 7.3 Three design decisions that look wrong until you think about them

**The delay.** A protection trip with no delay is a nuisance trip: one transient
and the shop stops. Real machinery-protection relays delay their trips for
exactly this reason — a momentary excursion has to persist to be believed. Ten
seconds is longer than an industrial relay's one-to-three, and that is
deliberate: it is the window in which the decision becomes *legible* to a human,
counting down on a screen, with a button to stop it.

**Latching.** A system that re-arms itself a second later is not a safety
system. The stopped motor refuses every later speed command — including from the
rig's own control panel — until a person clears it from the dashboard. That is
what "protection" means as distinct from "control".

**Refusing to claim success.** If the trip is published but the machine keeps
turning, the system does **not** report it as tripped. It stays in Fault and
says the trip failed. Showing "stopped" for a machine that is still turning
would be the single most dangerous lie this dashboard could tell, and no amount
of "well, we sent the message" changes that.

There is also a deliberate absence: **there is no "reset protection" button.**
Restarting the machine is what clears things, and restarting makes frames score
again — so the score alone decides where the asset lands. Fix the fault and it
returns to healthy. Don't, and it goes back to fault and trips again. An
operator cannot restart their way out of a real fault, and nothing in this
system ever restarts a machine on its own.

## 7.4 The machine that cried wolf

Building the trip mechanism took a few days. Making it fire *reliably* took
considerably longer, and the reason is worth telling because it is the most
useful engineering in the project.

The first version measured "how energetic is this machine's vibration right
now" as a straight average across the whole spectrum. It worked in early testing
and then quietly stopped being trustworthy: stopped and running machines
measured almost the same — **1.18×** apart in the worst case. That is not a gap
you can safely threshold on, and a gate you cannot trust means a trip that can
never confirm.

The cause turned out to be the sensor, not the machine. An accelerometer
sensitive enough to catch a bearing starting to fail is also sensitive enough to
have a noise floor of its own — a broadband electrical hiss present whether the
machine is on or off — and that noise is spread across most of the spectrum,
while the motor's actual mechanical signature is a handful of narrow lines below
about 600 Hz. Averaging across everything was mostly measuring the
accelerometer.

The fix: teach each node what its own sensor reads with the machine
**deliberately off**, fit a per-bin noise floor from those frames, and count only
the *excess* over that floor as real signal.

| Method | Stopped | Running | Worst-case margin |
|---|---|---|---|
| Full-spectrum average (original) | 7,480 | 11,137 | 1.18× |
| Excess over measured baseline (current) | 1,414 | 6,194 | **2.09×** |

The difference between a threshold that is basically a coin flip and one you can
arm a motor stop against.

Two things make this more than a bug fix. First, the baseline is captured
separately from commissioning and never invalidates an existing model —
capturing one cannot force a retrain. Second, the principle generalises: **don't
hardcode a number that is supposed to mean "this machine is running". Measure
it, per machine, per sensor.** It is more setup than a constant in a config
file, and it is the difference between something that works on the bench and
something that works on the fortieth machine in a fleet.

The full investigation — including the two reasonable hypotheses that turned out
to be wrong, and two alternative approaches that measured *worse* — is
[Appendix H](#appendix-h-motor-state-gate-calibration).

---

# Part III — The human interface

# Chapter 8. What the operator actually sees

## 8.1 Three channels, none depending on the others

Nobody is standing at the machine when the trip happens — that is the entire
point. So the system talks back on three independent channels: a **light on the
machine** for whoever walks past, a **live dashboard** for whoever is checking,
and a **phone alert** for whoever needs to know right now without checking
anything. None of them requires another to be working.

## 8.2 Every status an asset can hold

Ten states, and it is worth walking all of them, because "what is this machine
doing right now" is the question the whole product exists to answer.

![Asset lifecycle: New, Collecting, Training, then the live-scored Healthy/Warning/Fault group, plus Idle, Tripped, Paused and Offline](diagrams/06-asset-lifecycle.png)

| Status | What it means | Who sets it |
|---|---|---|
| **New** | Streaming data, never commissioned. No model, nothing to score against. | The system, when an unknown node first appears |
| **Collecting** | Commissioning in progress; healthy frames are being gathered. The row shows live progress — *Collecting 41/60*. | An operator pressing Commission |
| **Training** | The batch is closed and the model is being fitted. The row shows a live percentage. | An operator pressing Train |
| **Healthy** | Scored, and comfortably below this machine's own warning line. | The anomaly model |
| **Warning** | Over the warning line. Something has changed; nothing has been decided. | The anomaly model |
| **Fault** | Over the fault line, sustained. If a motor is armed, this is what starts the countdown. | The anomaly model |
| **Idle** | The machine is not turning, and *a person* stopped it. Normal, not a problem. | The running/stopped gate |
| **Tripped** | The machine is not turning, and *we* stopped it. Latched until cleared. | Machinery protection, only after the gate confirms it actually went quiet |
| **Paused** | An operator has deliberately suspended monitoring — maintenance, a known noisy job. Staleness never demotes it to Offline, because it is a standing human intent. | An operator |
| **Offline** | Nothing heard for 30 seconds. **Never stored** — derived from the last frame's timestamp, so it can never get stuck on after a node comes back. | Derived, continuously |

Two of these deserve their own sentence.

**Idle and Tripped are separate on purpose.** Both mean "not turning". Collapsing
them into one *stopped* status would erase the only distinction an operator
actually cares about: whether this was expected. Idle even shows blue on the
status ring rather than a grey, because a machine somebody switched off is a
healthy condition, while the greys mean "you are not getting data from this
node."

**Idle also closed a real hole.** The gate could already detect a stopped motor
and inference already declined to score one — but nothing ever wrote that fact
anywhere, so a machine switched off an hour ago kept displaying whatever status
it held while it was last running.

Every legal transition between these states is enforced in one place by an
explicit state machine, rather than by each feature setting `.status` and hoping.
That is not tidiness for its own sake: it closed a real bug where pausing a node
mid-commissioning silently stole it out from under the commissioning session.

## 8.3 The Fleet page, in detail

![Anatomy of the Fleet page: tabs, status tiles that are also filters, one row per asset, and the expanded detail panel](diagrams/08-dashboard-anatomy.png)

**The status summary row.** One tile per status, each showing a count — and each
tile is also a **filter**. Click *Faulty* and the list below shows only faulty
machines; click again to bring them back; click several to combine. Two details
that came out of using it rather than designing it:

* Tiles with a count of zero **hide themselves**, so a healthy fleet shows a
  short, calm row instead of a wall of zeroes. They reappear in their original
  fixed position when the count comes back, never appended wherever they
  happened to change.
* The *Assets* (select-all) tile only appears when there are at least two
  non-empty buckets to select across. With one bucket it would just duplicate
  that bucket's own count and its own button.

**One row per asset.** Compact by design: nickname, node ID, asset class,
status. A machine that has never been given a nickname shows an inviting *Add
nickname* prompt rather than a technical ID masquerading as a chosen name — and
the node ID is always shown underneath either way, so identity is never
ambiguous. The asset-class pill is click-to-edit and colour-keyed, and it is the
*only* editor for that field anywhere in the UI; other places that need it link
back here rather than offering a second live editor for the same value.

When a machine is in warning or fault **and** a classifier has actually scored
it, a second chip appears next to the status carrying the fault name. It is
deliberately its own chip in its own colour rather than folded into the status
pill, because the classifier is an independent signal that is allowed to
disagree with the status.

The row's controls change with the status rather than greying out generically —
*Commission* becomes *Train* when enough frames are collected, becomes
*Training…* while fitting, becomes *Recommission* afterwards. On a stopped
machine it is disabled with a reason that says what to do about it: *"Start the
machine first to recommission."*

**Open a row and you get everything known about that machine**, in a deliberate
order:

1. **Protection**, at the top — which motor is armed, the trip status or live
   countdown, the Hold button, and the stopped-baseline control. It is first
   because during a countdown it is the most time-critical thing on the screen
   and the only part you can act on.
2. **Anomaly score**, live, with this machine's own threshold lines drawn on it
   and a scrubber for the last half hour. It is hidden entirely for a machine
   with no model yet — an empty chart is worse than no chart, and showing the
   *old* trend during a recommission would be stale data masquerading as
   current.
3. **Fault classification** — confidence per fault type, shown only once a model
   has actually scored this machine.
4. **Live spectra**, accelerometer per axis and microphone.
5. Three collapsed panels for going deeper: **Scalar values** (all 24
   statistics), **Raw signals** (time domain), and **Waterfall** — a spectrogram
   over time, in either a 2D heatmap or a 3D ridgeline.

The heavy panels are not just hidden when collapsed, they are **not rendered at
all** until first opened, which keeps opening a row cheap even on a phone.

> **[SCREENSHOT: an expanded asset row with the anomaly chart, classifier bars and spectra visible]**

## 8.4 The other four tabs

**Classifier.** One card per asset class: the recordings table, a
selection-driven action bar (upload / relabel / delete), the link to that class's
Edge Impulse project, and the *Fetch trained model* button. Asset classes that no
longer belong to any live node get their own de-emphasised delete-only card
rather than vanishing with their recordings.

**Network.** The base station's own Wi-Fi state — mode, network, address — and
the join-a-network flow described in
[Chapter 4](#chapter-4-growing-the-fleet). This is the page a phone lands on
when it joins the onboarding hotspot.

**Performance.** Two tiers of live charts, for when *the monitor itself* feels
slow rather than a machine feeling wrong:

* **QRB2210** — one chart **per CPU core** rather than one averaged number,
  because this pipeline is single-threaded and an average would happily hide one
  maxed-out core; plus memory, temperature where the board exposes it, and GPU
  where it is provisioned. Metrics that aren't really available are left out
  rather than faked.
* **Pipelines** — one row per live asset: frames arriving per second, and the
  percentage of its time budget each pipeline is using. That second number is
  the honest headroom signal, and it is deliberately not dressed up as a
  fabricated "you could add N more nodes" estimate.

**Alerts.** The Telegram connection (including a QR code, so a phone can join
without typing anything) and per-subscriber preferences — alert level, and
whether a subscriber wants the whole fleet or specific machines.

> **[SCREENSHOT: the Performance tab, per-core charts visible]**

## 8.5 The light on the machine

Every base station and every satellite node carries its own status ring, and the
colour alone tells the story from across the room:

| State | Ring |
|---|---|
| New | Cyan, steady |
| Healthy | Green, steady |
| Warning | Amber, slow breathing pulse |
| Fault | Red, fast strobe |
| Tripped | Red, slow strobe — deliberate rather than urgent: *I already acted* |
| Idle | Blue, steady |
| Paused | Mid grey |
| Offline | Dark grey |

The colours are hand-tuned for real WS2812 LEDs and are **not** copied from the
dashboard's palette, because that was tried and looked wrong: on an uncorrected
WS2812 any non-trivial secondary channel shows up disproportionately, so a
screen-friendly emerald rendered visibly bluish and a screen-friendly red
rendered pink. Near-primary values avoid that. It is a small thing that only
shows up when you put the real part on a real bench.

The base station adds a readout most sensor nodes don't have: the **8×13 LED
matrix already on the board**, scrolling a one-line fleet summary — counts only,
worst first. `FFLT,WWRN,OOFF,HOK` reads as *1 fault, 1 warning, 1 offline, the
rest healthy*. Idle and Paused are excluded from it entirely, because that
display exists to answer one question — *is anything wrong* — and a machine
somebody switched off is not.

> **[PHOTO: the status ring in each colour state, and the LED matrix mid-scroll]**

## 8.6 The phone alert

The dashboard runs a Telegram bot: link a phone once — by scanning the QR code
on the Alerts tab — and a confirmed fault arrives as a message carrying the
machine's nickname and, when there is one, the classifier's read. Nobody has to
go looking.

This was built and demonstrated working against a real Telegram bot and a real
phone. It is switched off in the current build for one reason only: the bot
token is a managed secret that has to be re-entered through App Lab after some
device-testing housekeeping. Nothing about the feature is unfinished; a value is
missing.

> **[SCREENSHOT: a real Telegram fault alert]**

---

# Part IV — The engineering

# Chapter 9. Under the hood

## 9.1 Three kinds of board, one brain

Everything in this report runs on three kinds of hardware: the base station, one
or more satellite nodes, and the motor-driver rig. **Only one of them thinks.**

![Full system architecture](diagrams/05-full-architecture.png)

The base station's Linux side is where the asset registry lives, where models
train and run, where the dashboard is served, and where the decision to stop a
motor is made. Every other board is a sense organ or a muscle. The motor-driver
rig in particular is not a peer — it accepts *stop*, and nothing else. There is
no code path in this system that can set a speed or start a machine.

## 9.2 One frame, start to finish

1. **Acquire.** The accelerometer and microphone are sampled at their native
   rates on whichever chip they are wired to. The accelerometer's hardware FIFO
   batches samples so the host gets one interrupt per block rather than one per
   sample.
2. **Reduce.** That same chip runs the FFTs and computes the six statistics per
   channel, then average-pools each spectrum down to its wire bin count. This
   step is what makes the whole architecture possible: shipping raw audio and
   vibration off-chip at native rate would saturate any link fast enough to be
   worth having.
3. **Arrive.** The frame reaches the Linux side — over the internal SPI bus for
   the base station's own sensors, or over Wi-Fi/MQTT from a satellite. Same
   frame either way.
4. **Route.** The pipeline manager matches the frame to an asset, and validates
   its shape against what that asset was commissioned with. A node whose channel
   set or bin count has changed is caught here rather than silently scored
   against a model that no longer fits it.
5. **Score.** The features feed both the running/stopped gate and the
   autoencoder, and — if this asset's class has a model — the fault classifier.
6. **Fan out.** A status change updates the registry, which pushes to
   everything downstream at once: the dashboard over a WebSocket, the status
   ring and matrix, a Telegram message if one is due, and — on a confirmed fault
   with a motor armed — the trip.

## 9.3 The two internal links, and why they are separate

Between the STM32U585 and the QRB2210 run two independent links:

| Link | Carries | Rate |
|---|---|---|
| **LPUART1** | Control-plane RPC: status pushes, statistics queries, display commands | 500 kbaud |
| **Dedicated SPI** | Bulk telemetry: full-resolution spectra, raw diagnostic pulls | ~40 MHz achieved |

They started as one link. Moving bulk data onto its own bus was not an
optimisation, it was a fix: at ~65 KB/s of continuous frames the shared serial
link's message framer wedged, taking the entire control channel with it. The
current split means a large raw capture — a research operation that pulls
un-decimated time-domain windows — cannot interfere with the live status loop,
because they are not on the same wire.

The UART's own speed has a story too, told in
[Chapter 2](#chapter-2-why-the-arduino-uno-q): 500000 baud is not a round number
picked for comfort, it is the highest rate whose clock divisor leaves enough
sampling margin to survive a real soak test.

## 9.4 One threading pattern, used everywhere

Every sampling thread in this codebase — accelerometer, microphone, on either
chip — follows the same shape: **sample continuously in its own thread, publish
only the latest result behind a lock, and let the consumer read that latest
value whenever it is ready.** No queues, no backlog.

It is a small, boring pattern applied consistently on purpose. A monitoring
system that falls behind should skip forward to *now*, not spend its time
catching up on a machine's past. There is nothing actionable in a spectrum from
four seconds ago.

Thread priorities on the real-time side are all declared in one file rather than
scattered across six, for a blunt reason: priority mistakes were this project's
two worst hardware bugs. A busy-polling microphone thread once starved the
inter-processor link so thoroughly that it died silently with no error anywhere,
and two threads sharing a priority level meant one could sit ready for a full
scheduler timeslice waiting out the other. Both are now impossible to
reintroduce by accident, because every value is in one place where they can be
read together.

## 9.5 The things that keep the dashboard honest

Three details that are invisible when they work and very visible when they don't:

* **The registry does not write to disk on every frame.** It used to. Every
  ingested frame rewrote the whole asset file while holding that asset's lock —
  the same lock every dashboard action waits on — so a single storage hiccup
  froze the entire UI: ingestion stalled inside the lock, requests piled up
  behind it, live pushes stopped, and the node eventually read as offline. Live
  values that are refreshed several times a second are now held in memory; the
  durable history is a separate database write.
* **Charts are re-parented, never rebuilt.** The asset list rebuilds itself on
  every update. If the chart elements lived inside that markup, every zoom or
  pan an operator applied would be wiped several times a minute. Instead the
  chart elements are created once and moved into whatever slot the newest render
  produced.
* **The inference pipeline is rebuilt when its inputs change.** Recommissioning
  a machine, or resuming a paused one, used to leave a cached pipeline holding
  the old thresholds and the old model — so a machine could sit reading
  *healthy* while its own graph was red. Both were real bugs, both are fixed,
  and both are now covered by tests.

---

# Chapter 10. Why we built it this way

Nine decisions, the alternative in each case, and what it cost.

## 10.1 A wired serial link between the two chips, not a second SPI bus

The chip-to-chip control link was nearly a second SPI bus with the Linux side as
master. The Qualcomm SPI hardware only really supports master mode on Linux, so
the STM32 would have had to be a slave — which meant fragile DMA timing above
modest clock rates, plus an extra signal wire purely to tell the master a frame
was ready. A bidirectional serial link needs none of that: either side talks
whenever it has something to say. Less clever, considerably more robust.

## 10.2 Train each machine's model on the machine

Covered in [§5.7](#chapter-5-teaching-it-what-normal-feels-like). The
alternative — capture, ship to a cloud service, wait, deploy back — works, and
turns a five-minute walk-up task into a workflow with a network dependency, a
queue and a data-governance question.

## 10.3 An unsupervised detector, with a supervised classifier alongside

The primary detector had to work on a machine that has never failed, because
that is every machine at the moment it is installed. That rules out a supervised
model as the *primary* signal. The classifier then adds the "what" once labelled
data exists, without ever becoming a dependency of the safety path
([§6.4](#chapter-6-naming-the-fault)).

## 10.4 Per-machine anomaly model, per-type classifier — and whether that has to stay true

This asymmetry is a fair thing to question, and it is worth answering properly:
*if identical machines can share a classifier, why can't they share an
autoencoder?*

**Why it is per-machine today.** The autoencoder models "normal for this unit",
and its output is a reconstruction error measured in units set by that unit's own
spectrum. Two nominally identical lathes differ in mounting stiffness, sensor
placement, foundation, load and wear. Sharing weights would mean sharing a score
scale, and the thresholds ([§5.4](#chapter-5-teaching-it-what-normal-feels-like))
are absolute distances in that scale.

**Why it probably doesn't have to stay that way.** The inputs are already
substantially unit-normalised before the model ever sees them: spectra are
peak-normalised per frame, and the six statistics per channel are standardised
using per-node mean and standard deviation fitted at commissioning. Much of the
unit-to-unit offset is therefore already removed. The remaining blockers are
narrower than they first look — the input dimension has to match across units
(same channel set and bin count), and the thresholds are per-node.

**The version that would work.** Pre-train one autoencoder per *asset class* on
pooled healthy data from every unit of that class, then, for each new unit,
collect a short healthy batch and use it only to fit that unit's normalisation
statistics and thresholds — not to retrain the weights. Commissioning a new
machine would drop from "collect and train" to "collect and calibrate", and the
shared model would be better than any single machine's, because it would have
seen more genuinely healthy variation.

This is **not built.** It is on the roadmap in
[Chapter 12](#chapter-12-whats-next) as a real, scoped item rather than a
hand-wave, and the reason it is not built is ordering, not doubt: per-machine
training already works and the trip depended on it, so the effort went into the
gate and the protection path instead.

## 10.5 A software-latched stop today, a relay later

The honest version: a relay per motor, wired to physically break the power line,
is the more bulletproof long-term design. What exists today is a per-motor
command that halts motion and refuses to re-arm until cleared. It was chosen
because it is real, testable on hardware already in hand, and does not require
sourcing new parts against a fixed deadline. It genuinely stops the motor from
turning; it is not yet the belt-and-braces version that also removes electrical
power at the source. [Chapter 12](#chapter-12-whats-next) has it first on the
list.

## 10.6 One wire format, wired or wireless

Base-station sensors and satellite nodes could easily have grown two data
shapes. They didn't — and the format is generated for all three codebases from a
single schema file, so the base station, the ingestion parser and the satellite
firmware cannot silently drift apart. Changing the format means regenerating,
not remembering. ([Appendix F](#appendix-f-wire-protocol-specification).)

## 10.7 Measure each machine's own quiet

[Chapter 7](#chapter-7-the-day-it-stopped-itself) tells the story. The principle
that came out of it is broader than the fix: don't hardcode a number that is
supposed to mean "this machine is running". Measure it, per machine, per sensor,
and compare against that.

## 10.8 A mature platform for the second model

Building a training pipeline for the fault classifier from scratch was on the
table. Edge Impulse — built for exactly this category of sensor ML — was chosen
instead, so effort could go into the parts nothing provides off the shelf: the
gate, the trip, the fleet dashboard. It also keeps that model structurally
independent of the safety path.

## 10.9 Statuses that distinguish *who* did something

Idle versus Tripped, Paused versus Offline. In each pair, both statuses mean
roughly the same thing physically and completely different things
operationally. Collapsing either pair would make the dashboard shorter and
strictly less useful. ([§8.2](#chapter-8-what-the-operator-actually-sees).)

---

# Chapter 11. Proof, not promises

## 11.1 What "verified" means here

Every number in this chapter was measured on the real rig — sensors reading a
spinning motor, a trip actually stopping that motor, a dashboard checked against
a live device in a real browser. Where a figure appears, it came out of that
hardware. [Appendix J](#appendix-j-test-suite-and-verification-record) records
how each was checked.

> **[SCREENSHOT: the dashboard mid-trip — status, countdown, anomaly chart and console log together]**

## 11.2 The gate, measured

Per-bin accelerometer energy, sensor stationary versus the rig spinning at
90 RPM:

| Frequency bin | Stopped | Running | Delta |
|---|---|---|---|
| ~131 Hz | 13,192 | 36,134 | **+22,942** |
| ~281 Hz | 12,680 | 44,798 | **+32,118** |
| ~381 Hz | 13,586 | 40,638 | **+27,052** |
| ~631 Hz | 13,453 | 13,545 | +92 |
| ~1,231 Hz | 11,217 | 11,482 | +265 |
| ~3,231 Hz | 5,525 | 5,483 | −42 |

The motor's entire mechanical signature is a handful of narrow lines below about
600 Hz — the stepper's own step rate, 90 RPM × 200 full steps = 300 Hz, landing
squarely in those bins. Everything above is the accelerometer's own broadband
noise, present identically whether the machine runs or not.

Which is why the metric had to change:

| Method | Stopped | Running | Worst-case margin |
|---|---|---|---|
| Full-spectrum average | 7,480 | 11,137 | 1.18× |
| Excess over measured baseline | 1,414 | 6,194 | **2.09×** |

## 11.3 A real commissioning run

Numbers straight off one session:

* Stopped baseline: **65 frames** captured with the rig confirmed physically
  off, fitted energy reference **1,533.1**, measured spread **1.39×**, gate
  threshold set at **2,682.9**.
* The node went from flapping between fault and warning at rest to settling
  cleanly on **Idle**.
* Spun the rig back up: it left Idle immediately.
* Re-commissioned against the running rig: healthy anomaly score **0.046**
  against a warning threshold of **0.144** and a fault threshold of **0.288** —
  a machine confidently reading as itself, with real daylight between normal and
  the line that means trouble.
* Ramped down again: returned cleanly to Idle, not to Fault.
* Dashboard checked against the live device in a real browser, zero console
  errors throughout.

## 11.4 The trip, both directions

Verified repeatedly on the rig, not once:

* Motor spinning → fault confirmed → countdown → **motor stops**, stays
  stopped, and refuses further speed commands until cleared from the dashboard.
* Cleared and spun back up → resumes normally, re-scored from scratch.

An earlier version had a genuine race in how a trip was confirmed, which could
produce a false negative — a trip that had actually worked being reported as
failed. It was found on hardware, fixed, and re-tested in both directions.

## 11.5 Feature-representation results

Not every result came from the rig; the offline experiment harness replays real
captures through the whole feature pipeline and sweeps its parameters. Two
findings from it changed the design:

* **Per-axis beats fused,** decisively: +38.5σ worst-case fault separation
  versus +1.8σ for a combined tri-axial magnitude, on the same captures. This is
  why the model consumes `accel_x/y/z` separately.
* **The six statistics carry more than expected.** Adding them took
  healthy-versus-imbalance separation from roughly 3σ to roughly 80σ. A spectrum
  alone was leaving a great deal on the table.

## 11.6 Known limitations, stated plainly

* **The test rig's three motors share one vibration sensor.** Trip one motor
  while the others keep running and that shared sensor still reads *running* —
  because it is honestly still feeling the other two. This is a property of one
  sensor covering three motors on a bench rig, not a software defect; a real
  deployment has one sensor per machine. It is called out here rather than
  quietly avoided. The rig therefore starts with a **single** motor installed,
  which is both the honest configuration for one sensor and the order a real
  floor grows in; the others are added on the control page when the point is
  fleet scale rather than trip fidelity.
* **The classifier is not the safety path**, by construction
  ([§6.4](#chapter-6-naming-the-fault)). If it names the wrong fault, the machine
  still stops — the label is just wrong.
* **The trip stops motion, not power.** See
  [§10.5](#chapter-10-why-we-built-it-this-way).
* **Faults above roughly bin 24 look alike on this rig.** A direct consequence
  of [§11.2](#chapter-11-proof-not-promises): above the motor's own signature,
  every class is looking at the same sensor noise. On a machine with genuine
  high-frequency fault content — which the sensor can see, see
  [Appendix G](#appendix-g-sensor-configuration-envelope) — this constraint
  lifts.

## 11.7 Status ledger

| Subsystem | Status |
|---|---|
| Base-station sensing (vibration + audio) | Live-verified on hardware |
| Per-machine commissioning and anomaly model | Live-verified on hardware |
| Running/stopped gate with measured baseline | Live-verified on hardware |
| Physical trip: per-motor stop + latch + confirm | Live-verified on hardware, both directions |
| Dashboard: fleet, detail, commissioning, protection | Live-verified on hardware |
| Status ring + LED matrix | Live-verified on hardware |
| Wi-Fi onboarding, base station (captive portal) | Live-verified on real phones, three rounds |
| Fault classifier, on-device | Built, running on-device, trained on 541 real captures |
| Satellite sensor nodes | Built |
| Telegram alerts | Built and demonstrated; off pending one config value |
| Per-motor relay | Not built — [Chapter 12](#chapter-12-whats-next) |
| Automated test suite | 33 test modules, run on every change — [Appendix J](#appendix-j-test-suite-and-verification-record) |

---

# Chapter 12. What's next

In the order they would be built.

**1. A relay per motor.** Today's trip stops a motor from moving; a relay would
remove its power at the source as well. Held back by a no-new-hardware
constraint during this build window, not by any design uncertainty — the trip
message, the latch and the confirmation logic are all already in place and would
not change.

**2. A shared anomaly model per asset class.** The scoped version from
[§10.4](#chapter-10-why-we-built-it-this-way): pre-train one autoencoder per
asset class on pooled healthy data, and reduce per-unit commissioning from
"collect and train" to "collect and calibrate". This makes commissioning the
fortieth machine faster than the first, which is the opposite of how it works
today.

**3. More labelled fault data per asset class.** The classifier's ceiling is set
by how much genuinely distinct fault data exists per class, and the recording
workflow in [§6.3](#chapter-6-naming-the-fault) is now good enough that
collecting it is a matter of time rather than tooling.

**4. Fault-severity trending, not just fault detection.** The anomaly score is
already stored durably per machine. The obvious next question after "something
is wrong" is "how fast is it getting worse", and the data to answer it is
already on disk.

**5. More nodes, more machine types.** The architecture was built for a fleet
from day one. Proving that on more than a handful of machines is the natural
next test of it.

## Closing

A year ago Ravi bought one machine and worried about it failing quietly. Today
his shop runs more machines than he can personally watch — and he doesn't have
to. A light tells him what is fine, a phone tells him what isn't, and once in a
while, before anyone picks up a phone, a motor just stops rather than grinding
itself into a repair bill.

Sensing, deciding, acting — in that order, with nobody standing over it. That was
the whole assignment.

---

# Appendices

# Appendix A. Bill of materials

**This is the only parts list in this document.** Every chapter that mentions a
component links here rather than repeating it.

Quantities assume one base station, one satellite node, and the three-motor
rig used to validate this report. Scale the satellite block by however many
machines a real deployment monitors.

Links are to **Robu.in**. Prices are indicative, checked at Indian retail in
August 2026, and include GST — confirm at the time of purchase, since this
category moves.

### Base station — one per site

| Part | Qty | What it does here | ≈ ₹ | Buy |
|---|---:|---|---:|---|
| Arduino UNO Q (2 GB) | 1 | The board. Real-time sensing on the STM32U585, models + dashboard on the QRB2210 Linux side | 6,800 | [Robu](https://robu.in/product/official-arduino-uno-q/) |
| SmartElex KX134-1211 breakout | 1 | Vibration sensing over SPI | 900 | [Robu](https://robu.in/product/smartelex-triple-axis-accelerometer-breakout-kx134/) |
| INMP441 I²S MEMS microphone | 1 | Audio sensing | 180 | [Robu](https://robu.in/product/inmp441-mems-high-precision-omnidirectional-microphone-module-i2s/) |
| WS2812B 8-pixel RGB ring | 1 | Local status light | 85 | [Robu](https://robu.in/product/8bit-ws2812-5050-rgb-led-built-full-color-driving-lights-circular-development-board/) |
| Jumper wires + rigid mount / magnet base | 1 set | Wiring, and rigidly coupling the accelerometer to the machine housing | 150 | [Robu](https://robu.in/product-category/connectors/jumper-wire/) |
| | | **Base station subtotal** | **≈ 8,115** | |

A 4 GB UNO Q variant exists ([Robu](https://robu.in/product/official-arduino-uno-q-4gb-single-board-computer-abx00173/))
and is a straight drop-in — nothing in this project needs it, but it is the one
to buy if you plan to train larger models on-device.

**The mount is not an accessory.** An accelerometer read through a soft or
loose mount is a low-pass filter you did not ask for, and it removes exactly the
high-frequency content early bearing faults live in. Rigid coupling to the
machine housing matters more than the price tag suggests.

### Satellite node — one per additional machine

| Part | Qty | What it does here | ≈ ₹ | Buy |
|---|---:|---|---:|---|
| Seeed Studio XIAO ESP32-S3 | 1 | The node's brain. Wi-Fi built in, no separate radio | 880 | [Robu](https://robu.in/product/seeed-studio-xiao-esp32s3-2-4ghz-wifi-ble-5-0/) |
| SmartElex KX134-1211 breakout | 1 | Vibration sensing — same part as the base station | 900 | [Robu](https://robu.in/product/smartelex-triple-axis-accelerometer-breakout-kx134/) |
| INMP441 I²S MEMS microphone | 1 | Audio sensing — same part as the base station | 180 | [Robu](https://robu.in/product/inmp441-mems-high-precision-omnidirectional-microphone-module-i2s/) |
| WS2812B 8-pixel RGB ring | 1 | Status light — same part as the base station | 85 | [Robu](https://robu.in/product/8bit-ws2812-5050-rgb-led-built-full-color-driving-lights-circular-development-board/) |
| USB 5 V supply + cable | 1 | The whole node runs off USB | 200 | [Robu](https://robu.in/product-category/electronic-instruments-and-tools/power-supply/) |
| | | **Per satellite node** | **≈ 2,245** | |

Same three sensing parts as the base station, deliberately: one line item to buy
in bulk, not a different parts list per machine. That is also what keeps the
per-machine cost of expanding the fleet close to linear.

### Validation rig — for reproducing this report's results

Not part of a deployment. This is the bench setup used to induce and measure
faults.

| Part | Qty | What it does here | ≈ ₹ | Buy |
|---|---:|---|---:|---|
| Arduino Uno R3 | 1 | Motor controller — receives stop commands, drives step pulses | 1,700 | [Robu](https://robu.in/product/original-arduino-uno-rev3/) |
| CNC Shield V3 | 1 | Driver carrier board | 200 | [Robu](https://robu.in/product/cnc-shield-v3-engraving-machine-3d-printer-a4988-drv8825-driver-expansion-board/) |
| A4988 stepper driver (or DRV8825) | 3 | One per motor axis | 100 ea | [Robu](https://robu.in/product/a4988-driver-stepper-motor-driver/) |
| NEMA-17 stepper motor (17HS4401) | 3 | The "machines" being monitored on the bench | 534 ea | [Robu](https://robu.in/product/nema17-4-2-kgcm-stepper-motor/) |
| 12–24 V DC supply, ≥3 A | 1 | Motor power | 700 | [Robu](https://robu.in/product-category/electronic-instruments-and-tools/power-supply/) |
| | | **Rig subtotal** | **≈ 4,502** | |

### What a real first deployment costs

| Scenario | Parts | ≈ ₹ |
|---|---|---:|
| One machine monitored | Base station only | 8,115 |
| Three machines monitored | Base station + 2 satellites | 12,605 |
| Ten machines monitored | Base station + 9 satellites | 28,320 |

**Only the UNO Q requires proof of purchase for this competition** — one board,
regardless of how many machines the architecture scales to. That constraint is
part of why the sensing node is an inexpensive, repeatable, identical block:
`[FILL IN: actual UNO Q purchase price and receipt reference]`.

For **why** the KX134 specifically, over both cheaper and far more expensive
alternatives, see [Appendix D](#appendix-d-sensor-selection-rationale). For
exactly how each part is wired, see
[Appendix B](#appendix-b-wiring-and-pinout-reference).

---

# Appendix B. Wiring and pinout reference

**This is the only wiring detail in this document.** Chapters
[3](#chapter-3-building-the-base-station) and
[4](#chapter-4-growing-the-fleet) show what connects to what in block form and
link here for the pins.

## B.1 Base station — Arduino UNO Q, STM32U585 side

| Peripheral | Signal | Pin |
|---|---|---|
| KX134 accelerometer | SPI SCK / MISO / MOSI | D13 / D12 / D11 (main header SPI) |
| | Chip select | D8 (PB4, software GPIO) |
| | INT1, buffer-full interrupt | D9 (PB8) |
| INMP441 microphone | SAI1 clock / frame-sync / data | PB10 / PB9 / PC1 |
| WS2812B ring | Data in | PB0 (TIM3 channel 3, DMA-driven) |

Nothing connects to the QRB2210 side. Debug logging runs on a separate physical
link — USART1 on the JDIGITAL D0/D1 header, straight to a host PC over a
USB-UART adapter — fully decoupled from the inter-processor link, so attaching a
log console can never compete with sensor or model traffic.

![Base station schematic (KiCad): UNO Q, KX134, INMP441 and the WS2812 ring, every net labelled by header pin](diagrams/02b-base-station-schematic-kicad.png)

## B.2 Satellite node — Seeed XIAO ESP32-S3

| Signal | Pin | Notes |
|---|---|---|
| KX134 SPI SCK / MISO / MOSI | D8 / D9 / D10 | The board's fixed hardware SPI pins |
| KX134 chip select | D3 | Software GPIO |
| KX134 INT1 (buffer-full) | D2 | |
| INMP441 WS / LRCLK | D0 | |
| INMP441 BCLK | D1 | |
| INMP441 SD (data in) | D4 | |
| WS2812B ring data in | D5 | |

The XIAO ESP32-S3 breaks out only 11 GPIOs, so every assignment above is chosen
to keep the fixed hardware SPI lines free for the accelerometer — the one
peripheral that genuinely needs them. Node identity is derived from the board's
own Wi-Fi MAC address; there is no per-unit ID to set, no jumper to solder, and
no build flag to change between units.

![Satellite node schematic (KiCad): XIAO ESP32-S3, KX134, INMP441 and the WS2812 ring, every net labelled by GPIO](diagrams/03b-satellite-node-schematic-kicad.png)

## B.3 Motor-driver rig — Arduino Uno + CNC Shield V3

| Signal | Pin | Notes |
|---|---|---|
| Shared driver enable (`~ENABLE`) | D8, active-LOW | **One line for all three driver sockets** — the shield has no per-motor hardware enable |
| Motor 1 (X) STEP / DIR | D2 / D5 | |
| Motor 2 (Y) STEP / DIR | D3 / D6 | |
| Motor 3 (Z) STEP / DIR | D4 / D7 | |

![Motor-driver rig schematic (KiCad): Arduino Uno + CNC Shield V3, three drivers on a shared ~ENABLE line, three NEMA-17 motors and the supply](diagrams/06-motor-driver-rig-schematic-kicad.png)

**The shared enable line is the reason the trip is implemented as a per-motor
step-pulse halt rather than a hardware disable.** Pulling `~ENABLE` high
de-energises all three drivers, which would stop two healthy machines to protect
one faulty one. Stopping step generation for one axis is the only per-motor
action this hardware supports — and it is exactly the constraint a per-motor
relay removes ([Chapter 12](#chapter-12-whats-next)).

**Set the driver current limit before running.** Each A4988/DRV8825 has a small
potentiometer setting its reference voltage; under-current skips steps and
over-current cooks the driver.

* A4988: `Vref ≈ Imax × 8 × Rsense`
* DRV8825: `Vref ≈ Imax / 2`

## B.4 Source files

The schematics above are real KiCad projects, not drawings — symbols and nets,
openable and editable — under `hardware/kicad/`. The block diagrams throughout
this report are generated from Python under `report/diagrams/gen/`, and both are
regenerated rather than hand-edited.

---

# Appendix C. Build one yourself

Five paths. **Two of them need no hardware at all**, which is the fastest way to
see the whole system working and the right place to start if you are evaluating
it before buying anything.

| Path | Hardware needed | Good for |
|---|---|---|
| [C.1 Desktop dashboard + simulated node](#appendix-c-build-one-yourself) | **None** | Seeing the entire dashboard, commissioning, scoring and classifier flow on a laptop |
| [C.2 Simulated node against a real base station](#appendix-c-build-one-yourself) | UNO Q only | Testing the real device's ingestion and fleet handling without building satellites |
| [C.3 Base station](#appendix-c-build-one-yourself) | UNO Q + sensors | The real thing, one machine |
| [C.4 Satellite node](#appendix-c-build-one-yourself) | XIAO ESP32-S3 + sensors | Adding machines |
| [C.5 Motor-driver rig](#appendix-c-build-one-yourself) | Uno + CNC shield + steppers | Reproducing the trip and this report's measurements |

Parts for C.3–C.5 are in [Appendix A](#appendix-a-bill-of-materials); pins are
in [Appendix B](#appendix-b-wiring-and-pinout-reference).

## C.1 No hardware at all — desktop dashboard + simulated node

This runs the **real** dashboard application on your own machine, fed by a
simulator that speaks the **real** wire protocol, replaying **real** captured
sensor data. It is not a mock: the registry, the feature pipeline, the
autoencoder, commissioning, the thresholds, the classifier and the whole
frontend are the same code that runs on the board.

You need an MQTT broker on `localhost:1883` — the script checks for one and will
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

That script creates a virtual environment, installs the dashboard and simulator
dependencies, starts the application on **port 8180** with an isolated data
directory (so it can never touch a real device's registry or history), starts one
simulated node, pre-configures it, and prints both URLs.

The simulated node starts **offline** on purpose — open its own control page and
press *Go Online* once you have looked at its configuration, or skip that with
`--auto-online`.

Useful flags:

| Flag | Effect |
|---|---|
| `--nodes N` | Run N independent simulated nodes, each with its own control page |
| `--captures-dir DIR` | Replay a different folder of `.npz` captures. Defaults to `base-station/captures/` |
| `--captures-dir ""` | Fall back to generated synthetic captures instead of real recordings |
| `--auto-online` | Bring the node online without a click |
| `--host 0.0.0.0` | Bind on every interface, so you can open the dashboard from a phone on the same network to check the mobile layout |

Each simulated node's own page lets you pick which capture file it streams,
toggle the accelerometer and microphone independently, switch between fused and
per-axis accelerometer output, choose which statistics ride along, adjust FFT
bin counts live, and watch its status LED change colour and mode as the base
station pushes status to it — exactly as a real node would.

**What you will not see:** the `base_station` asset itself. Its data comes from
the SPI-connected sampling chip, which does not exist on a laptop. Only
MQTT-driven nodes appear. That is expected, not a failure.

## C.2 A simulated node against a real base station

Once you have a UNO Q running (C.3), you can point the same simulator at the
real device over your LAN, which is how most of the fleet-level behaviour in
this report was exercised without building ten satellites.

One-time, on the device (this needs a password typed on-device, so no script can
do it for you):

```sh
adb shell
sudo apt-get update && sudo apt-get install -y mosquitto mosquitto-clients
echo -e 'listener 1883 0.0.0.0\nallow_anonymous true' | sudo tee /etc/mosquitto/conf.d/lan.conf
sudo systemctl enable --now mosquitto
```

The broker lives **on the UNO Q**, not on your laptop — matching how a real
satellite has to work, since it is a sensor with nowhere else to publish. Then,
from your machine:

```sh
cd base-station
./start_sim_node.sh --captures-dir captures --nodes 2
```

The simulator connects out over plain LAN, exactly as a real node would. USB is
used only for the occasional setup step, never for live traffic — so a momentary
USB blip cannot flip a node "offline" on the dashboard.

## C.3 The base station

**One-time device provisioning.** These configure things outside the application
container and only need running once per board:

```sh
cd base-station
./provision-spi.sh      # the MCU<->MPU SPI bulk link
./provision-baud.sh     # sets the serial link to 500000 baud on the Linux side
./provision-wifi.sh     # Wi-Fi onboarding: hotspot fallback + captive portal
```

`provision-baud.sh` matters more than it looks: the Linux-side router's baud
must match the firmware's, and a mismatch breaks the whole link silently, with
no error anywhere. See [§9.3](#chapter-9-under-the-hood).

**Flash the real-time side.** Open `base-station/sketch/` in Arduino App Lab and
flash it to the STM32U585.

**Build, deploy and run the Linux side:**

```sh
cd base-station
./start_dashboard.sh
```

This forces normal (non-raw-capture) mode, builds, flashes, pushes the
application, waits for its container, and prints **the board's own LAN IP URL**.
Use that link, not a localhost one — a real deployment has no port forwarding
available.

Open it, and assets appear as soon as real data flows. To go from there to a
working monitor: give the asset a nickname and an asset class, press
**Commission**, let the machine run, press **Train**. Then — with the machine
switched **off** — press *Measure with machine off* in the Protection section to
capture the stopped baseline ([Chapter 7](#chapter-7-the-day-it-stopped-itself)).
That step is the one thing no software can verify for you, which is why the
instruction lives in the button label itself.

**Telegram alerts** need one secret: set `TELEGRAM_BOT_TOKEN` as the
`arduino:telegram_bot` brick's variable through App Lab's interface, and re-add
that brick to `app.yaml`. The bot's username is resolved automatically at
startup, so there is no second value to configure. With the token unset, every
alert path no-ops cleanly.

## C.4 A satellite node

```sh
cd satellite
pio run                # build
pio run -t upload      # flash over USB
pio device monitor     # optional serial console, 115200 baud
```

Then follow [§4.2](#chapter-4-growing-the-fleet): power it up, join the
`EPM-SAT-<id>` network from a phone, fill in three fields. **There is no
credential to compile in.** For a bench board being reflashed constantly, a
dev-only shortcut exists — pass `WIFI_SSID` and `WIFI_PASSWORD` as build flags
in `platformio.ini` and the node seeds its storage on first boot and skips the
portal. A real build passes neither and the shortcut is a no-op.

## C.5 The motor-driver rig

1. Set each driver's current limit before applying power — see
   [Appendix B.3](#appendix-b-wiring-and-pinout-reference).
2. Flash `motor-driver/src/main.cpp` to the Arduino Uno with PlatformIO.
3. Start the rig host, which serves the control page and receives trips:

   ```sh
   cd motor-driver
   ./start_motor_driver.sh                              # broker on localhost
   ./start_motor_driver.sh --mqtt-host <base-station-ip>  # or over the LAN
   ```

   The Uno's port is autodetected; pass `--port` if two boards are attached.

   Open **http://localhost:8000/** in Chrome or Edge, click **Connect**, and
   pick the Uno's port. The rig starts with **one** motor installed; the empty
   slots add the others, and each one added is announced to the base station as
   a trip output straight away. Add `--profile` to run a scripted capture
   profile instead of driving by hand.
4. On the base station's dashboard, open the asset you want protected, and set
   its **Trip output** to a motor number. One motor may only be claimed by one
   asset; the dropdown shows already-claimed motors as disabled rather than
   failing after the fact. Back on the control page, that motor now carries a
   **PROTECTED** badge naming the asset — and if the trip ever fires, its card
   turns red and locks until a human presses **Reset & re-arm**.

## C.6 Changing the wire format

`base-station/telemetry_schema.json` is the single source of truth for the frame
format ([Appendix F](#appendix-f-wire-protocol-specification)). Any edit must be
followed by regenerating every generated side, so the base station, the
ingestion parser and the satellite firmware cannot drift apart:

```sh
python3 base-station/python/tools/gen_telemetry_schema.py
```

## C.7 Running the tests

Each test module is a standalone script rather than a pytest suite, and declares
the `PYTHONPATH` it needs in its own docstring. For example:

```sh
PYTHONPATH=base-station/python/ingestion:base-station/python/pipeline \
  python3 base-station/tests/gate_test.py
```

See [Appendix J](#appendix-j-test-suite-and-verification-record) for what is
covered.

---

# Appendix D. Sensor selection rationale

The KX134-1211 is the vibration transducer at every sensing point in this
architecture — base station and every satellite node alike. It was chosen over
both hobby-grade and industrial-grade alternatives on six criteria.

| Criterion | Hobby grade | **KX134 (selected)** | Industrial grade |
|---|---|---|---|
| Max output rate / usable bandwidth | ~1 kHz / 250–500 Hz | **25.6 kHz / 12.8 kHz** | continuous / ~11 kHz (needs an external ADC) |
| Dynamic range | fixed | **software-selectable ±8/16/32/64 g** | fixed, part-specific |
| Noise density | ~300 µg/√Hz | **~130 µg/√Hz** | 25–80 µg/√Hz |
| Interface | often analogue | **digital SPI, 16-bit ADC on-chip** | often analogue |
| Buffering | none | **512-byte hardware FIFO** | varies |
| Unit cost (India) | ₹150–350 | **≈ ₹900** | ₹3,800–7,200 |

Each line mattered for a specific reason:

* **Bandwidth was a hard filter, not a preference.** Early-stage fault
  signatures — micro-pitting, incipient bearing race damage — live in the
  2–10 kHz band. A sensor that physically cannot see that band cannot be
  compensated for downstream, no matter how good the processing is. Hobby
  sensors are eliminated by this line alone.
* **Dynamic range in software, not in the part number.** Selectable
  ±8/16/32/64 g means the same physical part works on a quiet bench rig and on a
  production motor with real startup transients — no hardware swap, no second
  part to stock.
* **Noise density sets the detection floor.** A noisy sensor raises the
  effective anomaly threshold before any software runs, hiding exactly the small
  early signals this system exists to catch. This is also the property that
  turned out to matter most in
  [Appendix H](#appendix-h-motor-state-gate-calibration) — in a way that was not
  fully appreciated until that debugging session.
* **The FIFO changes the real-time budget.** Without it the host must service
  the sensor roughly every 39 µs at full rate — an aggressive interrupt load for
  a chip that also runs FFTs and manages a link. With it, the sensor batches
  samples and raises one interrupt per block, turning a high-frequency servicing
  problem into a manageable batched one on both the STM32U585 and the ESP32-S3.
* **Cost matters at fleet scale, not per unit.** This architecture targets 20+
  sensing points. At ≈₹900 the sensor count stays close to linear; at industrial
  pricing the same fleet target becomes prohibitive.
* **Regional availability was a scheduling constraint.** The KX134 here sits on
  the SmartElex breakout, sourced through the Indian electronics market —
  avoiding the shipping and import lead time of industrial parts against a fixed
  deadline.

No single criterion justifies the KX134 alone. Hobby sensors fail on bandwidth;
industrial sensors fail on cost and lead time. It is the first point in the
market where every constraint is satisfied at once.

---

# Appendix E. Network and transport selection rationale

Satellite nodes needed a link that was both **real-time** (continuous spectrum
streaming, not periodic bursts) and **bidirectional** (the base station must be
able to send commands back). Three options were evaluated.

**BLE advertise-only (beacon pattern).** Attractive for scaling to 20+ nodes
with minimal per-connection overhead and low power. Rejected once bidirectional
control became non-negotiable: advertising is inherently one-way, and no
protocol cleverness fixes a fundamentally one-directional transport.

**BLE GATT (connection-based).** Natively bidirectional — notify from node,
write from base station — and would have kept the project inside the radio stack
originally planned. Rejected after investigation surfaced multiple documented
reliability issues in the Linux BlueZ stack with concurrent GATT connections to
more than one peripheral from a single central, including service resolution
hanging on the second concurrently-connected device, reproduced across several
BlueZ versions. For a link with no acceptable downtime, discovered late against
a fixed deadline, that was disqualifying on its own.

**Wi-Fi with an MQTT broker on the UNO Q — selected.** The board's wireless
module supports access-point mode, so the base station can host its own network
rather than depending on venue Wi-Fi, which cannot be trusted at a competition.
ESP32 nodes join as clients; an MQTT broker on the UNO Q mediates traffic in
both directions. This won on:

* **Reliability** — mature, well-understood Wi-Fi/TCP/MQTT, none of BLE's
  concurrency problems for this use case.
* **Throughput** — full-resolution spectra stream comfortably, with none of
  BLE's aggressive payload-size constraints. A satellite's ~4.1 KB frame every
  200 ms is ~164 kbps, which is nothing on Wi-Fi and impossible on BLE.
* **Infrastructure reuse** — the same broker and the same ingestion backend
  already used everywhere else in the system.

The accepted trade-off, stated plainly: Wi-Fi's continuous radio use is not
inherently lower-power than BLE. But sustained real-time streaming had already
negated BLE's main power advantage, so this was assessed as a wash rather than a
net loss.

BLE advertise-only remains a credible *production-scale* direction for a much
larger deployment — 20+ nodes sending periodic health summaries rather than live
spectra. It is noted here as a genuine future option, deliberately not the one
this build prioritised.

---

# Appendix F. Wire protocol specification

Two sensing paths — the base station's internal chip-to-chip link and every
satellite's Wi-Fi link — carry the same conceptual message types over different
framing, chosen to match what each transport already guarantees. A UART is a raw
byte stream with no message boundaries; MQTT already provides framing,
addressing and delivery semantics.

## F.1 Message types, shared by both transports

| Type | Name | Direction | Purpose |
|---|---|---|---|
| 0x01 | `SPECTRUM` | Node → Base | FFT bins + statistics from vibration/audio sensing |
| 0x02 | `HEALTH_ALERT` | Node → Base | Anomaly threshold crossing |
| 0x03 | `HEARTBEAT` | Node → Base | Liveness, current configuration echo |
| 0x04 | `COMMISSION_START` | Base → Node | Begin commissioning capture |
| 0x05 | `COMMISSION_DONE` | Base → Node | Commissioning complete; switch to inference |
| 0x06 | `CONFIG_SET` | Base → Node | Sample rate, FFT size, active channels |
| 0x07 | `ACK` | Either | Acknowledge a critical message |
| 0x08 | `STATUS_LED` | Base → Node | Drive the node's ring to match its dashboard status |

`SPECTRUM` and `STATUS_LED` are the two in production use today. The rest share
this numbering so ingestion treats a message uniformly no matter which link it
arrived on, as they are built out.

## F.2 The frame itself is a section list, not a fixed struct

The payload is deliberately generic: a list of sections, each declaring what it
is, what channel it belongs to, and how long it is. A node sends a spectrum
section per active channel, a statistics section, and — periodically — decimated
time-domain sections for the raw-signal charts.

That shape is what makes the rest of the architecture hold together. A node with
no microphone simply sends fewer sections. A node with a different bin count
sends different lengths, and the ingestion side commits that asset's model
dimension from its own first frame rather than assuming a fleet-wide constant.
Adding a channel does not break an existing node.

The layout is defined **once**, in `base-station/telemetry_schema.json`, and the
encoder/decoder for every side is generated from it — see
[Appendix C.6](#appendix-c-build-one-yourself). Three codebases in two languages
cannot drift apart, because none of them contains a hand-written copy of the
format.

## F.3 UART framing (internal to the base station)

```
[SYNC: 2B][VER: 1B][TYPE: 1B][NODE_ID: 1B][LEN: 2B][PAYLOAD: N][CRC16: 2B]
```

`SYNC` is a fixed `0xAA55` so a receiver can resynchronise after a dropped byte.
`NODE_ID` is always `0x00` — this link is point-to-point, and the field exists
only for symmetry with the Wi-Fi side. `CRC16` covers `VER..PAYLOAD`. The link is
full duplex: the sampling chip can stream on TX while receiving a control
message on RX.

## F.4 MQTT framing (satellite nodes)

Topics, per node, with `<node_id>` derived from the node's own Wi-Fi MAC:

```
epm/<node_id>/data    Node -> Base   (SPECTRUM, HEALTH_ALERT, HEARTBEAT)
epm/<node_id>/cmd     Base -> Node   (STATUS_LED, COMMISSION_START, CONFIG_SET, ...)
epm/<node_id>/ack     Either         (ACK)
```

Since MQTT already frames and addresses each message, the envelope is leaner —
just a type byte in front of the same type-specific payload:

```
[TYPE: 1B][PAYLOAD: N]
```

`STATUS_LED`'s payload — `[RGB: 4B][MODE: 1B][PERIOD_MS: 2B]` — is byte-identical
on both transports, so a satellite's ring and the base station's own ring always
mean the same thing by "breathing amber". One colour table, one mode enum, shared
everywhere a status light exists.

## F.5 Quality of service

| Message type | QoS | Why |
|---|---|---|
| `SPECTRUM`, `HEARTBEAT` | 0 | High frequency; the next one is along shortly |
| `HEALTH_ALERT`, `COMMISSION_*`, `CONFIG_SET`, `STATUS_LED` | 1 | Must arrive; duplicates are harmless because all are idempotent |
| `ACK` | 0 | Advisory only |

---

# Appendix G. Sensor configuration envelope

What this hardware can actually be pushed to, what we run at, and how to change
it. Referenced from [§5.6](#chapter-5-teaching-it-what-normal-feels-like).

## G.1 Accelerometer

| | Hardware ceiling | **What we run** | Why |
|---|---|---|---|
| Output data rate | 25,600 Hz | **12,800 Hz** | See below |
| Usable bandwidth (Nyquist) | 12.8 kHz | **6.4 kHz** | |
| Range | ±8 / 16 / 32 / 64 g, selectable in firmware | ±16 g | Bench rig; raise for machines with real startup transients |
| Resolution | 16-bit | 16-bit | |
| FIFO | 512 bytes | Used, interrupt per block | Removes a ~39 µs servicing deadline |

**Why not the full 25.6 kHz.** It was tried, on real hardware, as a controlled
A/B test. At the full rate the sampling thread stopped yielding often enough and
starved the inter-processor link outright — telemetry frames went to zero. The
sampling thread runs above the link's own thread by design, so it wins that
contest completely. 12,800 Hz is eight times the original 1,600 Hz baseline and
leaves real headroom.

If you need the top of the band — and a machine with genuine 6–12 kHz fault
content might — the fix is the thread priority relationship, not the rate: the
sampling thread has to yield inside its block loop. That is a known, scoped
piece of work, not an unknown.

## G.2 Microphone

| | Value |
|---|---|
| Interface | I²S / SAI, 24-bit |
| SNR | 61 dBA |
| Frequency response | ~60 Hz – 15 kHz |
| FFT window | 2048 samples |

## G.3 Spectral resolution — three independent knobs

This is the part most worth understanding, because the trade-off is not the one
people expect. There are **three separate bin counts**, and they are deliberately
not the same number:

| Knob | Base station | Satellite | What it controls |
|---|---|---|---|
| **Native FFT bins** | 512 per channel | 512 per channel | The real analysis resolution, and the length of the time-domain window the six statistics are computed over |
| **Model / wire bins** | 128 per channel | 128 per channel | How many buckets the spectrum is average-pooled to before transmission — what the model actually sees |
| **Legacy RPC view** | 32 per channel | — | A small polled view for diagnostics, constrained by a 256-byte round trip |

Keeping the native FFT dense at 512 while pooling to 128 on the wire is a
deliberate split. The pooling shrinks the payload; it does **not** shorten the
analysis window, so the statistics keep their full-length time-domain input.
Halving the wire bins would halve bandwidth without touching the quality of the
statistics.

**128 wire bins is not a guess.** It came out of an offline sweep over real
captures — axis handling, bin count, microphone inclusion and which statistics —
scored on worst-case fault separation. The winning configuration was per-axis
accelerometer, 128 bins, 128 microphone bins, all six statistics, giving a
536-dimension input vector at **+38.5σ** worst-case separation.

## G.4 Throughput

| | Base station | Satellite |
|---|---|---|
| Frame production period | 64 ms (~15.6 frames/s) | 200 ms (5 frames/s) |
| Transport | Dedicated SPI, ~40 MHz | Wi-Fi / MQTT |
| Approximate frame size | ~10–14.5 KB (with time-domain sections) | ~4.1 KB |
| Approximate sustained rate | well inside the SPI budget | ~20.5 KB/s (~164 kbps) |

Time-domain sections do not ride every frame. They piggyback on **every fourth**
frame, because the collapsed raw-signal charts do not need per-frame freshness
and carrying them every time would drag the whole frame's transfer — and
therefore the anomaly score and the spectra — down with them. The fast path stays
fast.

## G.5 Where to change all of this

Every knob above is a named constant in one header per board —
`base-station/sketch/app_config.h` and `satellite/include/app_config.h` — each
carrying the measurement or the failure that produced its current value. Nothing
in this list is a magic number without a paper trail.

A node's wire bin count does **not** have to match the base station's. The
ingestion side commits each asset's model dimension from that asset's own first
frame, so a satellite is free to run a different configuration from the board it
reports to.

---

# Appendix H. Motor-state gate calibration

[Chapter 7](#chapter-7-the-day-it-stopped-itself) told the short version. This is
the full one, because the process — three wrong layers before the real cause —
is worth more to a future reader than the fix alone.

## H.1 The question

Before the system can decide "this machine just stopped" — needed both to
suppress false scores at rest and to confirm a trip actually landed — it needs a
reliable way to tell running from stopped from vibration energy alone.

The first version used an absolute threshold on a single energy number. It failed
early and obviously: the default threshold was 0.05 while real running energy
measures around 19,000 — a 250,000× margin, meaning the stopped state was
literally unreachable on real hardware. It went unnoticed for a while because the
unit tests used single-digit synthetic values, a scale hardware never produces.

The fix was to make the threshold *relative* to each node's own commissioned
running-energy reference rather than a global constant. Correct in principle —
and it only pushed the real problem one layer down.

## H.2 Layer two: blaming the microphone

The relative gate initially summed *every* channel, microphone included, into one
energy number. Ambient shop noise has nothing to do with whether a motor is
turning, so excluding it looked like the obvious fix.

It was implemented, and it was right in principle. Measured live, it barely moved
the number: ~6,600–7,250 combined versus ~7,000–8,000 accelerometer-only. Same
order of magnitude. Whatever was keeping idle energy close to running energy was
coming from the accelerometer channels themselves.

## H.3 Layer three: a reasonable hypothesis that was wrong

Next hypothesis: a DC/gravity bias. If FFT bin 0 carries a large constant offset
from gravity, it would dominate an RMS regardless of whether the motor was
spinning — which would neatly explain idle and running looking like the same
order of magnitude.

It was a reasonable theory, and it was checked before anything was built on it.
It was wrong, twice over: the firmware's own magnitude routine already discards
bin 0 before this code ever sees it, and real captured windows confirmed it from
the other direction — the raw gravity offset sits at a mean of about 4,228
counts, and none of it appears anywhere in the bins software receives.

Worth recording as a *negative* result. The hour spent disproving it was cheaper
than the week that would have gone into building on it.

## H.4 The real cause: the sensor's own noise floor

The gate computed an RMS over *every* bin of every accelerometer channel — 384 of
them for a three-axis, 128-bin node. Measured live, per pooled bin, stopped
versus running at 90 RPM:

| Bin | ~Hz | Stopped | Running | Delta |
|---:|---:|---:|---:|---:|
| 2 | 131 | 13,192 | 36,134 | **+22,942** |
| 5 | 281 | 12,680 | 44,798 | **+32,118** |
| 7 | 381 | 13,586 | 40,638 | **+27,052** |
| 12 | 631 | 13,453 | 13,545 | +92 |
| 24 | 1,231 | 11,217 | 11,482 | +265 |
| 64 | 3,231 | 5,525 | 5,483 | −42 |

The motor's entire mechanical signature is a handful of narrow lines below
~600 Hz — the stepper's own step rate, 90 RPM × 200 full steps = 300 Hz, landing
squarely on bins 5–7. Every other bin, which is the overwhelming majority of the
spectrum, is the accelerometer's own broadband electrical noise, present
identically whether the machine runs or not.

Averaging across all of it was mostly a measurement of the accelerometer.

This also explains something otherwise puzzling in the classifier work: fault
classes captured from this rig look nearly identical to each other above roughly
bin 24, because above the motor's own signature every class is looking at the
same noise. See [Appendix I](#appendix-i-edge-impulse-classifier-experiments).

## H.5 The fix

Rather than trusting a formula to separate signal from noise, each node now
measures its own noise floor directly: it captures **≥30 frames with its machine
deliberately off**, fits a per-bin median floor, and the gate thereafter counts
only the *excess* over that floor.

| Method | Stopped | Running | Worst-case margin |
|---|---:|---:|---:|
| Full-spectrum average | 7,480 | 11,137 | 1.18× |
| Excess over measured baseline | 1,414 | 6,194 | **2.09×** |

Two properties of the fix matter as much as the number:

* It is **independent of the existing running-energy reference.** A node with no
  captured baseline keeps its previous behaviour exactly, so capturing one can
  never invalidate an existing model or force a retrain.
* Energy and threshold are always derived **together, on the same basis**, from
  the same measurement — so the two numbers are never compared across scales.

## H.6 Alternatives that measured worse

Both were implemented and measured rather than reasoned about:

* **Band-limiting to the motor's own frequency range** (bins 0–7 only) instead
  of subtracting a floor: separated **worse** — 1.09× against 2.09×. The noise
  floor turns out to be *tallest* in exactly the low bins where the real signal
  also lives, so narrowing the window does not escape it. This is the least
  intuitive result in the whole project.
* **Reference-free peakiness / spectral-flatness metrics:** every one of them
  overlapped between stopped and running in the worst case observed.

## H.7 Live verification

All of the above ran against real hardware, not simulation: baseline captured
with the rig confirmed physically off (65 frames, reference 1,533.1, spread
1.39×, threshold 2,682.9); the node observed going from flapping fault/warning at
rest to settling cleanly on idle; the rig spun back up and observed leaving idle
immediately; re-commissioning produced a healthy score of 0.046 against a warning
threshold of 0.144; ramping down returned cleanly to idle rather than fault; and
the dashboard was checked against the live device in a real browser with zero
console errors throughout.

## H.8 A known, accepted limitation

The test rig's three motors share one physical vibration sensor. With motor 1
tripped and motors 2 and 3 still spinning, that shared sensor still reads
*running*, because it genuinely is still feeling the other two. This is a
property of one sensor covering three motors on a bench rig, not a software
defect — a real deployment has one sensor per machine.

---

# Appendix I. Edge Impulse classifier experiments

The fault classifier ([Chapter 6](#chapter-6-naming-the-fault)) names *which*
fault is present. Getting there was a real research process, and two
data-integrity bugs were caught along the way that would otherwise have quietly
inflated a reported number.

**A note on the numbers below.** The accuracy figures in §I.2–§I.4 come from the
early phase, when the classifier was trained on a **public Kaggle dataset
replayed through a simulated node** — before any physical fault data from this
project existed. They are recorded here as method history, not as a statement
about the current model, which is trained on this rig's own captures
(§I.5). Do not quote them as this system's performance.

## I.1 Starting point: a public dataset, replayed

The first version trained against a public Kaggle vibration dataset — four
classes: healthy, cracking, offset pulley, wear — replayed through a simulated
satellite node, so the whole classifier path could be built and tested end to end
before any physical fault data existed. This established the workflow: upload
labelled spectra, design a feature pipeline, train, read a confusion matrix,
export a deployable model.

## I.2 Bug one: train/test leakage

An early run reported a low but plausible-looking accuracy. Before trusting it,
the split was checked — and it was leaking: windows from the same source file
were landing in both the training and test sets, so the model was partly being
tested on data adjacent to what it trained on.

The fix was a **file-level split**: an entire source file goes to either train or
test, never both. Any accuracy figure from before this fix is explicitly
considered stale and is not quoted anywhere in this report.

## I.3 Bug two: data corruption in the upload path

A second, deeper bug was found in the signal-loading step used to prepare every
upload — affecting every dataset uploaded to that point, not one run. This is the
kind of bug that does not announce itself: the pipeline ran, the numbers looked
plausible, and the data was wrong.

After fixing it, a raw triaxial re-run scored **59.82%** on Edge Impulse's own
trained model. A from-scratch local replication of a reference paper's classical
KNN method, on the same corrected data, scored **69.64%**.

A classical method beating the cloud-trained network on that specific dataset and
feature representation is a genuine result, and it is kept here rather than
quietly dropped. It is also what prompted the move to per-axis features and the
six statistics, which is where the real gains came from
([§11.5](#chapter-11-proof-not-promises)).

## I.4 A deliberate strategic pivot

At that point accuracy was still improving with tuning, but the marginal return
on more hyperparameter chasing was judged lower than the return on finishing the
rest of the system — the gate, the trip, the dashboard, satellite bring-up.

Stating that plainly matters, because the alternative is implying the number
above was a ceiling. It was not. It is the point at which effort was consciously
redirected.

## I.5 Moving to real hardware data

The classifier then moved off replayed public data onto **541 captures taken
directly from this project's own rig**, across bearing, healthy, loose-mount and
unbalanced conditions — converted to 536-dimension per-axis spectra plus
statistics and uploaded to a dedicated Edge Impulse project.

Because each fault condition exists as a single continuous capture rather than
many independent short samples, the file-level split from §I.2 is not available
here. A **contiguous-tail split** was used instead — the last portion of each
file reserved for test and never seen in training — the closest leakage-free
approximation available under that constraint. This is a real methodological
limitation and it is stated rather than papered over.

`[FILL IN: current model's accuracy / confusion matrix from Edge Impulse Studio]`

## I.6 One model per asset class

The dashboard's classifier workflow was reworked around one card per **asset
class** rather than per node, with a pooled per-class normalisation baseline
fitted from every recording of that class. That is what turns "train once, cover
every identical lathe" from an aspiration into a built capability
([§6.2](#chapter-6-naming-the-fault)).

## I.7 A bug only the real service could find

Testing against a real Edge Impulse account — rather than local tooling alone —
surfaced broken axis naming in the uploaded feature columns. It was fixed by
naming every column explicitly and correctly (`accel_x_bin0`, `rms_x`, and so on)
and wrapping them in a proper features input block.

A good reminder that a pipeline which passes every local test can still behave
differently the moment it meets the real external system it was built against.

---

# Appendix J. Test suite and verification record

## J.1 Automated tests

The backend carries **33 test modules**, exercised on every change. Each is a
standalone script declaring the import path it needs, rather than a framework
suite — see [Appendix C.7](#appendix-c-build-one-yourself) for how to run them.

Coverage, by area:

| Area | Modules |
|---|---|
| Registry, statuses and legal transitions | `registry_test` |
| Feature building and normalisation | `features_test`, `raw_features_test` |
| Model and commissioning | `autoencoder_test`, `commissioning_test`, `inference_test` |
| Running/stopped gate and stopped baseline | `gate_test`, `stopped_baseline_test` |
| Machinery protection and the trip chain | `protection_test` |
| Wire protocol, frame codec, schema | `wire_protocol_test`, `telemetry_frame_test`, `spi_link_test` |
| Ingestion and routing | `pipeline_manager_test`, `mqtt_subscriber_test` |
| Classifier and the Edge Impulse path | `classifier_test`, `ei_client_test`, `ei_controller_test`, `ei_projects_test`, `ei_scaling_test` |
| Capture and history | `capture_test`, `history_test` |
| Displays | `display_rgb_test`, `display_matrix_test`, `matrix_status_test`, `matrix_status_device_test`, `status_color_test` |
| Sampling | `accel_sampler_test`, `mic_sampler_test` |
| API, alerts, performance, simulator | `api_test`, `alert_store_test`, `telegram_alerts_test`, `perf_test`, `satellite_node_sim_test` |

Some of these are built from **real captured sensor data rather than synthetic
numbers**, and that is deliberate: a hand-written "quiet" spectrum was too clean
to have caught the noise-floor problem in
[Appendix H](#appendix-h-motor-state-gate-calibration). Synthetic test data that
is tidier than reality will confirm whatever you already believe.

A small, documented subset needs on-device-only libraries and is therefore
excluded from off-hardware runs. Those are expected gaps, not failures.

## J.2 How each live claim was checked

| Claim | How it was verified |
|---|---|
| 1.18× → 2.09× gate margin | Live capture on the rig, stopped and running, per-bin energy compared directly |
| 65-frame baseline, threshold 2,682.9 | One real commissioning session, values read from the device |
| Healthy 0.046 vs warning 0.144 | Same session, re-commissioned against the running rig |
| Trip stops the motor and latches | Repeated live runs; console output and physical motor observed |
| Trip clears and the machine resumes | Same runs, in the reverse direction |
| Failed trip is not reported as tripped | Induced deliberately; status confirmed to stay Fault |
| Per-axis +38.5σ vs fused +1.8σ | Offline sweep over real captures through the production feature pipeline |
| Dashboard behaviour under live data | Real browser against the live device; console checked for errors |
| Wi-Fi onboarding captive portal | Real phones, three rounds of live testing |
| GPU speed-up ≈ 1.0× | Live benchmark on the board, single vector through 256-node batch, output verified bit-exact against CPU |

---

# Appendix K. 3D-printed test rigs

> **[PLACEHOLDER — to be written]**
>
> This appendix will cover the 3D-printed fixtures used to hold sensors and to
> induce repeatable, known faults on the bench: what each part is, why it is
> shaped the way it is, print settings, and the source models.
>
> To fill in:
>
> * **[MODEL: sensor mounting bracket]** — the rigid coupling between the
>   accelerometer and the machine housing, and why rigidity here changes what
>   the sensor can see (see [Appendix A](#appendix-a-bill-of-materials)).
> * **[MODEL: motor mount / test bed]** — the frame holding the three NEMA-17
>   motors.
> * **[MODEL: fault-induction fixtures]** — the parts used to produce
>   repeatable imbalance and loose-mount conditions for the labelled captures in
>   [Appendix I](#appendix-i-edge-impulse-classifier-experiments).
> * **[PHOTO: printed parts, assembled and in use on the rig]**
> * Print settings: material, layer height, infill, orientation.
> * Source files and licence.

---

# Appendix L. Glossary

* **Anomaly score** — one number describing how far a live reading sits from
  what a machine's own model considers normal. The reconstruction error of the
  autoencoder.
* **Asset** — one monitored machine's entry in the system, whether sensed by the
  base station directly or by a satellite node.
* **Asset class** — what *kind* of machine an asset is (`cnc lathe`, `conveyor
  motor`). The grouping key for recordings, and the scope of one trained fault
  classifier.
* **Autoencoder** — a small neural network trained only on a machine's healthy
  data; the gap between what it rebuilds and what actually arrived is the
  anomaly score.
* **Base station** — the Arduino UNO Q running the models, the registry, the
  dashboard and the MQTT broker, plus one directly-wired sensor pod.
* **Commissioning** — the short "run the machine and let the system learn it"
  step that trains a new asset's anomaly model and calibrates its thresholds.
* **Crest factor, kurtosis, peak, RMS, skewness, standard deviation** — the six
  time-domain statistics computed per channel alongside the spectrum, describing
  a signal's shape rather than its frequency content.
* **Fleet** — every asset the dashboard currently tracks.
* **Gate (running/stopped gate)** — the mechanism deciding whether a machine is
  currently turning, from vibration energy alone.
* **Idle** — the machine is not turning, and a person stopped it.
* **LPUART1** — the control-plane serial link between the UNO Q's two chips.
* **MQTT** — the publish/subscribe protocol satellite nodes use to reach the
  base station, with the broker running on the base station itself.
* **Node** — a physical sensing device. A base station's own sensing half, or a
  satellite. Distinct from *asset*, which is the machine it watches.
* **Physical AI** — an AI system whose output is a real-world physical action,
  not only information: sensing, deciding and acting in one loop, with no human
  in that loop at the moment of action.
* **QRB2210** — the Qualcomm chip on the UNO Q running Linux, the models and the
  dashboard.
* **Registry** — the base station's live record of every known asset, its
  status, thresholds and configuration.
* **Satellite node** — a wireless sensing node (Seeed XIAO ESP32-S3) reporting
  over Wi-Fi/MQTT instead of a wire.
* **STM32U585** — the chip on the UNO Q running Zephyr and doing the real-time
  sampling, FFTs and display driving.
* **Stopped baseline** — a per-node measurement of what its sensor reads with the
  machine deliberately off, used to separate real signal from the sensor's own
  noise floor.
* **Trip** — stopping a specific motor in response to a confirmed fault. Latched
  until explicitly cleared by a person.
* **Tripped** — the machine is not turning, and *this system* stopped it.

