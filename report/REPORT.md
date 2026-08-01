<!-- Placeholder convention: [PHOTO: ...] [DIAGRAM: ...] [SCREENSHOT: ...]
     [FILL IN: ...] marks a real value (cost, name, date) not yet known. -->

# EdgeAI Predictive Monitor
### Sensors that watch, an AI that decides, a hand that pulls the plug.

**Arduino Physical AI Challenge India 2026 — Industrial & Sustainability AI Track**

[Team / author name placeholder]
[Date placeholder]

---

## How to Read This Report

*(One page. Tells the reader where to stop, per the reader's-map in PLANNING.md §2.)*

- In a hurry? Read Chapter 1. That's the whole idea in a few minutes.
- Want to build one? Chapters 1–3 have the parts list and wiring.
- Reviewing the engineering? Chapters 1–9 cover the full design and real
  test results.
- Want the deep reasoning, the dead ends, and the debugging war stories?
  That's the Appendices.
- Every chapter opens with a short plain-English section before it goes
  deeper — reading just those, chapter by chapter, gives the full picture
  without any of the technical weeds.

---

## Table of Contents

1. The Machine That Never Complains Until It's Too Late
2. Wiring Up the First Machine — the Base Station
3. The Factory Grows — Satellite Nodes
4. Teaching It What Normal Feels Like — the AI Pipeline
5. The Day It Stopped Itself — Machinery Protection Trip
6. Ravi's Phone Buzzes — Dashboard, Alerts, and Lights
7. Under the Hood — Full System Architecture
8. Why We Built It This Way — Design Decisions
9. Proof, Not Promises — Results and Live Validation
10. What's Next for Ravi's Factory

Appendix A — Full Bill of Materials
Appendix B — Wiring & Pinout Reference
Appendix C — Sensor Selection Rationale
Appendix D — Network & Transport Selection Rationale
Appendix E — Wire Protocol Specification
Appendix F — Motor-State Gate Calibration: the Full Investigation
Appendix G — Edge Impulse Classifier Experiments
Appendix H — Software Setup & Reproduction Guide
Appendix I — Glossary

---

# Chapter 1 — The Machine That Never Complains Until It's Too Late

## 1.1 Ravi Buys a Machine

Ravi runs a small machine shop. Six months ago he signed a lease, and a
week later a brand-new CNC lathe rolled through the door — the single
biggest thing he's ever bought, and the thing his entire order book now
depends on.

Machines don't send a text before they fail. They just get a little
louder, run a little hotter, vibrate a little wrong — for days, sometimes
weeks — and then one Tuesday morning they don't start at all. By the time
a human notices, it's usually because something already broke.

**EdgeAI Predictive Monitor** is a small sensor pod that clips onto a
machine, listens to how it vibrates and sounds, and learns what "normal"
is for *that specific machine*. When it hears something drift away from
normal, it doesn't just put a sad icon on a dashboard somewhere Ravi isn't
looking. If it's confident enough, it reaches out and **cuts power to the
motor itself** — before "a little wrong" becomes a seized bearing and a
two-week wait for a replacement part.

That last sentence is the whole point of this report, so it's worth
saying twice: this system is allowed to *act*, not just *observe*. A
dashboard that emails you is a nice tool. A system that reaches out and
stops the machine is a different category of thing — sensing, deciding,
and *doing*, in one loop, with no human required to be watching at the
moment it matters. That's what "Physical AI" means here, and every
chapter after this one is really just an explanation of how that loop is
built.

> **[PHOTO: hero shot — the assembled base station clipped onto the demo
> rig, machine running, LED ring visible and lit]**

## 1.2 What It Actually Does, at a Glance

- **Watches** — an accelerometer and a microphone sample a machine's
  vibration and sound tens of thousands of times a second.
- **Learns** — during a short "commissioning" run, it builds a private
  model of what *this* machine's normal looks like. No two machines get
  the same baseline, because no two machines vibrate the same.
- **Notices** — an on-device AI model scores every new sample against
  that baseline and flags healthy / warning / fault in real time.
- **Diagnoses** — a second model, trained on the cloud platform Edge
  Impulse and then pulled back down onto the device, goes further than
  healthy-or-not: it names *which kind* of fault it's hearing — a
  bearing going, an imbalance, a loose mount. Train it once per
  *machine type*, not once per machine — five identical lathes share one
  trained model instead of five separate training runs.
- **Acts** — on a confirmed fault, it cuts power to the motor. This is
  the physical action, and Chapter 5 is its full story.
- **Tells someone** — a status light on the machine, a live dashboard,
  and a phone alert, so a human finds out without staring at a screen
  all day.
- **Scales** — one base station watches its own machine directly; extra
  "satellite" sensor nodes watch other machines over Wi-Fi, so a growing
  shop doesn't mean running cable across the floor.

A quick, honest scorecard — because a report that only says "it works"
is worth exactly as much as a machine that only says "I'm fine":

| Capability | Status |
|---|---|
| Vibration + audio sensing, base station | Built, live-verified on real hardware |
| Per-machine AI commissioning (autoencoder) | Built, live-verified on real hardware |
| Physical motor shutoff on confirmed fault | Built, live-verified on real hardware |
| Satellite sensor nodes (Wi-Fi) | Built, live-verified |
| Live dashboard | Built, live-verified |
| Phone alerts (Telegram) | Built and demonstrated; currently switched off pending one config value |
| Fault-type classifier (Edge Impulse, shared per machine type) | Built, accuracy still improving — an active research track, not the safety path |
| Per-motor relay (independent shutoff per machine) | Planned, not yet built |

![System at a glance: sensor pod and satellite nodes feed the base station, which fans out to dashboard, phone, status light, and — on a confirmed fault — a motor-stop command](diagrams/01-system-at-a-glance.png)

## 1.3 Why This Counts as Physical AI, Not Just a Smart Dashboard

Plenty of monitoring products stop at "notify a human." That's a
respectable product, but it's not physical AI — the intelligence never
touches the physical world, it just narrates it. The bar this project
holds itself to is stricter: the loop from *sensor reading* to *motor
losing power* has to run with no human in it, end to end, on real
hardware, more than once. Chapter 5 shows exactly that loop, including
the mistakes made getting it reliable enough to trust.

Everything else in this report — the sensors, the wireless nodes, the AI
model, the dashboard — exists in service of keeping that one loop honest
and fast. Ravi's shop is the excuse. The trip is the point.

# Chapter 2 — Wiring Up the First Machine

## 2.1 One Board, One Machine

The simplest possible version of this system is one sensor pod bolted to
one machine. That's the **base station**: an Arduino UNO Q with an
accelerometer and a microphone wired to it, watching one motor, showing
its own status on a light, and running the dashboard Ravi checks from his
phone or laptop. Everything else in this report is this same idea,
repeated and connected.

If Ravi stopped right here — one machine, one board — he'd already have
something most small shops don't: a machine that tells him it's getting
sick before it collapses.

> **[PHOTO: base station fully wired and clipped to the demo rig, wide
> shot showing sensor, board, and LED ring together]**

## 2.2 What You'll Need

| Part | Role | Approx. cost (₹) |
|---|---|---|
| Arduino UNO Q | the board — dual-brain: runs the AI *and* the real-time sensor sampling | `[FILL IN from purchase receipt]` |
| SmartElex KX134-1211 breakout | vibration sensing (accelerometer), SPI | ~913 |
| INMP441 breakout | sound sensing (I2S MEMS microphone) | `[FILL IN]` |
| WS2812B 8-pixel RGB ring | local status light | `[FILL IN]` |
| Jumper wires, mounting bracket/magnet | wiring + rigidly attaching the sensor to the machine housing | `[FILL IN]` |

The UNO Q's own onboard 8×13 LED matrix is used too, for a second, more
detailed status readout — nothing extra to buy there, it ships on the
board.

*(The KX134 is the one part worth explaining the choice of — Appendix C
walks through why it beat both hobby-grade and industrial-grade
alternatives on bandwidth, noise floor, and cost-at-scale.)*

## 2.3 Wiring It Up

Three things get wired to the UNO Q's STM32U585 side:

| Sensor | Signal | Pin |
|---|---|---|
| KX134 accelerometer | SPI bus (SCK / MISO / MOSI) | D13 / D12 / D11 (main header SPI) |
| | Chip-select | D8 (PB4, software GPIO) |
| | Interrupt (buffer-full) | D9 (PB8) |
| INMP441 microphone | SAI1 clock / frame-sync / data | PB10 / PB9 / PC1 |
| WS2812 status ring | Data in | PB0 (TIM3 channel 3) |

![Base station wiring: KX134, INMP441, and the WS2812 ring, each labeled with the STM32U585 pins they connect to](diagrams/02-base-station-wiring.png)

## 2.4 Seeing It Work

Flash the STM32U585 side with the sketch in `base-station/sketch/`
through Arduino App Lab, then start the dashboard:

```sh
cd base-station
./start_dashboard.sh
```

Open the dashboard in a browser, and the machine shows up: live
vibration/audio charts, and the status ring goes solid color for
"healthy" the moment real sensor data is flowing. Nothing is trained
yet at this point — that's Chapter 4 — but the sensing half of the loop
is already complete and watchable.

## 2.5 Two Brains, One Board

The UNO Q isn't one chip, it's two, glued together on one PCB: a
**Qualcomm QRB2210** running Linux, and an **STM32U585** running Zephyr —
and this project uses both, for different jobs.

The STM32U585 side is the one wired to the sensors above. It samples the
accelerometer and microphone at tens of thousands of samples a second,
runs an FFT on each window, and hands off compact spectra — it's the
part of the board close enough to real-time hardware to keep up with a
sensor that doesn't wait for anyone.

The QRB2210 side is the one running Linux, Python, the AI models, and
the dashboard's web server — the part of the board with enough memory and
a real operating system to train an autoencoder and serve a web page at
the same time.

The two talk to each other over a wired serial link (**LPUART1**) inside
the board itself — a question the STM32U585 side answers a few hundred
times a second: *"here's what I just heard and felt, make sense of it."*
Chapter 7 has the full frame format for anyone who wants to speak that
protocol themselves.

# Chapter 3 — The Factory Grows

## 3.1 Machine Number Two

A year in, Ravi's shop isn't a one-machine operation anymore. There's a
second lathe, then a drill press, then a compressor humming in the
corner — and not one of them is within cable reach of the first. Running
wire to every new machine every time the shop grows isn't a plan, it's a
standing chore.

That's what **satellite nodes** solve. Each one is a small, self-powered
sensor pod — same accelerometer, same microphone, same status ring as
the base station — that watches its own machine and reports in over
Wi-Fi instead of a wire. Bolt it on, power it up, and it finds its way
onto the network on its own. The base station's dashboard doesn't
distinguish between "wired" and "wireless" machines — a satellite node
just shows up as one more tile on the fleet view, right alongside the
original.

> **[PHOTO: a satellite node mounted on a second machine, powered and
> running, distinct from the base station]**

## 3.2 What You'll Need (per satellite)

| Part | Role | Approx. cost (₹) |
|---|---|---|
| Seeed Studio XIAO ESP32S3 | the node's brain — Wi-Fi built in, no separate radio module | `[FILL IN]` |
| SmartElex KX134-1211 breakout | vibration sensing, same part as the base station | ~913 |
| INMP441 breakout | sound sensing | `[FILL IN]` |
| WS2812B 8-pixel RGB ring | status light, same product as the base station's | `[FILL IN]` |
| USB power (cable + 5V source) | the whole node runs off USB power | `[FILL IN]` |

Same sensors as Chapter 2, on purpose — one bill of materials to buy in
bulk, not a different parts list per machine.

## 3.3 Wiring It Up

The XIAO ESP32S3 only breaks out 11 GPIO pins, so the wiring is tighter
than the base station's, but it's exactly this, every time:

| Signal | Pin | Notes |
|---|---|---|
| KX134 SPI SCK / MISO / MOSI | D8 / D9 / D10 | the board's fixed hardware SPI pins |
| KX134 chip-select | D3 | software GPIO |
| KX134 INT1 (buffer-full) | D2 | |
| INMP441 WS / LRCLK | D0 | |
| INMP441 BCLK | D1 | |
| INMP441 SD (data in) | D4 | |
| WS2812 ring data in | D5 | |

![Satellite node wiring: KX134, INMP441, and the WS2812 ring, each labeled with the XIAO ESP32S3 pins they connect to](diagrams/03-satellite-node-wiring.png)

One thing a satellite node *doesn't* have: the base station's LED matrix.
The ring alone carries its status — one light, not two, is plenty for a
sensor pod that isn't also running the dashboard.

## 3.4 Joining the Network

A satellite node is told two things before it's powered on: the shop
Wi-Fi network's name and password. The base station itself hosts that
network — it runs as its own access point, with a small message broker
(MQTT) listening for any node that joins. From there, a satellite finds
its way in on its own: it connects to the network, announces itself
using an ID derived automatically from its own hardware address, and
starts publishing telemetry. No per-device setup screen, no pairing
ritual — the tenth node onboards exactly the same way the first one did.

Once joined, a satellite behaves like any other machine on the fleet
view: commission it (Chapter 4), watch its status light, get an alert if
it trips (Chapters 5–6). The dashboard doesn't ask whether a machine is
wired or wireless before it starts protecting it.

> **[SCREENSHOT: dashboard fleet view with both a base-station machine
> and a satellite-node machine visible side by side]**

## 3.5 One Format, Every Node

A satellite node and the base station's own sensing half speak the exact
same language on the wire — the same generic "here's a channel, here's
its spectrum, here's its scalar stats" frame format, whether it arrives
over the base station's internal serial link or over MQTT from across
the shop floor. That's deliberate: the AI pipeline downstream never
needs to know or care which transport a frame arrived over, only what's
inside it. It's also what makes the "share one trained model across
every machine of the same type" idea from Chapter 1 possible at all — a
fleet of identical satellite-monitored lathes and a base-station-monitored
lathe all hand the AI pipeline data shaped exactly the same way.

# Chapter 4 — Teaching It What Normal Feels Like

## 4.1 "Just Let It Run for a Bit"

Before this system can tell Ravi something's wrong, it has to know what
*right* sounds like — and every machine's "right" is different. A brand
new lathe hums differently from one that's run for a decade; a
compressor's normal vibration looks nothing like a drill press's. So the
first thing that happens with any new machine isn't detection, it's
listening: Ravi hits "Commission" on the dashboard, lets the machine run
its normal cycle for a few minutes, and the system quietly builds a
private fingerprint of what healthy looks like for *that one machine*.
No two machines on the fleet ever share a baseline.

> **[SCREENSHOT: dashboard commissioning panel, in progress]**

## 4.2 What "Normal" Actually Means to a Machine

Under the hood, every incoming sample gets turned into a **feature
vector** — a compact numerical fingerprint of that moment, not the raw
waveform. For each sensing channel (vibration on X/Y/Z, plus audio), the
system keeps:

- an FFT spectrum — how much energy is present at each frequency, and
- six summary numbers — RMS, peak, crest factor, kurtosis, skewness,
  standard deviation — that describe the *shape* of the signal beyond
  just its frequency content.

During commissioning, dozens of these fingerprints get collected while
the machine runs normally, and an **autoencoder** — a small neural
network whose only job is to compress a fingerprint down and
reconstruct it back — is trained on nothing but that healthy data. A
network that's only ever seen "normal" gets good at rebuilding normal
and noticeably worse at rebuilding anything else. That gap between
"what came in" and "what the network thinks normal should look like" is
the **anomaly score**.

![Feature pipeline: sensor to feature vector to autoencoder to anomaly score to healthy/warning/fault status](diagrams/04-feature-pipeline.png)

## 4.3 From Score to Status

A single number isn't a status on its own, so the system watches the
score against two lines drawn automatically from the spread it saw
during commissioning: cross the lower one and the machine is
**warning**; cross the higher one, held for more than an instant (so
one noisy sample doesn't cry wolf), and it's **fault**. Below both, it's
just **healthy**. This score, plotted live, is the single most-watched
chart on the whole dashboard — Chapter 9 shows what it actually looks
like on a real machine, both healthy and mid-fault.

## 4.4 Beyond "Something's Wrong" — Naming It

Healthy/warning/fault answers *whether* something's off. A second,
separate model answers *what*: a supervised classifier — trained on the
cloud platform **Edge Impulse**, then fetched back down to run on-device
— that names the fault category it's hearing (a bearing starting to go,
an imbalance, a loose mount). Because it's trained per **machine type**
rather than per individual machine, one training run — done once,
against pooled data from every node of that type — covers every lathe
in the shop, not just the one it happened to be trained on.

This one is honestly still a work in progress. It's built, it runs
on-device, and it's meaningfully better than guessing — but its accuracy
isn't yet where the healthy/warning/fault gate's is, and it doesn't
drive the physical trip in Chapter 5. It's a second opinion, not the
safety mechanism. Appendix G has the full experiment history, including
a data-leakage bug that inflated an early accuracy number and how it was
caught and fixed.

## 4.5 Why Train Here, Not in the Cloud

The autoencoder trains on the UNO Q itself — on the QRB2210's Linux
side, in PyTorch — rather than shipping data off to a cloud service and
waiting for a model to come back. That choice mostly comes down to what
"commissioning" needs to feel like: a technician walks up, runs the
machine for a few minutes, and expects a trained, working monitor before
they walk away. A round trip to a cloud training job doesn't fit that
moment. It also means a machine's vibration data never has to leave the
shop floor unless someone chooses to send it there — which matters once
"data leaving the building" is a factory-floor policy question, not
just a technical one.

# Chapter 5 — The Day It Stopped Itself

## 5.1 It Doesn't Just Alert. It Acts.

Eight months in, one of Ravi's machines starts drifting — nothing a
person would catch by ear yet, but the anomaly score creeps past
warning and keeps climbing. This time, nobody's standing at the machine
when it happens. The system doesn't wait for someone to be.

The moment the fault is confirmed — not a single noisy reading, a
sustained one — it sends a command that stops that one motor from
turning. Not a suggestion on a screen. The motor stops. And it doesn't
quietly start again on its own: it stays refused until a person looks
at the dashboard and clears it by hand. This is the one chapter in this
report where the AI reaches past the screen and into the physical
world, and it's the reason this project can call itself Physical AI at
all rather than a very well-instrumented dashboard.

> **[PHOTO: the motor-driver rig — Arduino Uno, CNC Shield, and the
> stepper motor with its wiring, labeled]**

## 5.2 How the Command Actually Gets There

The trip is a chain of small, boring, reliable steps — which is exactly
what you want from a safety path:

1. The AI pipeline confirms **fault** status for a machine that has a
   motor armed against it.
2. The registry publishes a stop command over the network (MQTT) to the
   rig, naming exactly which motor.
3. A listener process on the machine controlling the rig receives it and
   tells that one motor's driver: stop moving. Not the whole rig — just
   that axis. The other motors, if they're healthy, keep running.
4. That motor becomes **sticky**: it refuses every further "spin up"
   command it receives, even from an operator's own control panel,
   until someone explicitly clears the trip from the dashboard. This
   matters more than it sounds like it should — a system that
   re-arms itself automatically a second later isn't actually a safety
   system.

Real log line, captured off the actual hardware the first time this ran
end to end:

```
TRIP RECEIVED: stopping motor 1...
motor 1 stopped
```

> **[VIDEO STILL: frame captured from a real trip event on the demo
> rig — status light changing color as the motor stops]**

## 5.3 The Machine That Cried Wolf

Getting the trip to fire *reliably* — not too eager, not too slow — took
longer than building the trip mechanism itself. The first version
measured "how energetic is this machine's vibration right now" as a
straight average across the whole spectrum. It worked in early testing
and then quietly stopped being trustworthy: stopped and running
machines measured almost the same, a 1.18× difference in the worst
case — not a gap you can safely threshold on.

The cause turned out to be the sensor, not the machine. An accelerometer
sensitive enough to catch a bearing starting to fail is also sensitive
enough to have its own noise floor — a background hiss present whether
the machine is on or off — and that noise was spread across most of the
spectrum, drowning out the actual, narrow band where the motor's real
signature lives. Averaging across everything mostly just measured the
sensor's own hardware.

The fix: teach each machine what its sensor reads with the motor
*deliberately off*, and only count what's left over that baseline as
real signal. That one change took the same worst-case gap from 1.18× to
**2.09×** — the difference between a threshold that's basically a coin
flip and one you can actually rely on. Appendix F has the full
investigation, including the two wrong turns taken before landing on
this — the DC/gravity theory that wasn't it, and a race condition in
how a trip got confirmed that briefly caused false negatives, both
worth reading if anyone reproducing this hits the same wall.

# Chapter 6 — Ravi's Phone Buzzes

## 6.1 Three Ways the System Talks to a Human

Ravi isn't standing at the machine when the trip happens — nobody is,
that's the whole point. So the system talks back on three channels at
once: a light on the machine itself for whoever walks past, a live
dashboard for whoever's checking, and a phone alert for whoever needs to
know right now without checking anything. None of them depend on the
others being open.

## 6.2 The Dashboard

One page, five tabs, and Ravi never needs more than a glance at most of
them:

- **Fleet** — every machine, one tile each, colored by status. This is
  the tab left open on a shop office monitor all day.
- **Classifier** — one card per machine type, where fault-type models
  (Chapter 4) get uploaded, trained, and reviewed.
- **Network** — Wi-Fi setup for the base station and a live view of
  which satellite nodes are connected.
- **Performance** — CPU, memory, and frame-rate health of the base
  station itself, for when something feels slow rather than a machine
  feeling wrong.
- **Alerts** — the Telegram connection and alert history.

Clicking into any one machine opens its detail view: the anomaly score
over time with its threshold line, the raw vibration/audio spectrum, and
the commissioning/protection controls from Chapters 4 and 5.

> **[SCREENSHOT: dashboard fleet view, several machines at a mix of
> statuses]**

## 6.3 The Light on the Machine

Every base station and satellite node carries its own status ring, and
the color alone tells the story from across the room: **solid green**
for healthy, a slow **amber breathing pulse** for warning, and a fast
**red strobe** the moment a fault is confirmed — impossible to mistake
for "everything's fine" even out of the corner of an eye. A dim grey
means the node hasn't reported in.

The base station adds a second readout most sensor nodes don't have: an
8×13 LED matrix scrolling a one-line fleet summary — just counts, ordered
worst-first (`FFLT,WWRN,OOFF,HOK` reads as "1 fault, 1 warning, 1
offline, the rest healthy"). No need to walk up to a laptop to know the
shape of trouble across the whole floor.

> **[PHOTO: status ring showing each color state — green / amber /
> red / grey, four small photos or one composite]**

## 6.4 The Phone Alert

The dashboard also runs a Telegram bot: link a phone once, and a fault
anywhere in the fleet arrives as a message, not something anyone has to
go looking for. This one's built and was demonstrated working against a
real Telegram account and a real device — it's switched off in the
current build only because the bot's access token needs re-entering
after some device-testing housekeeping, not because of anything wrong
with the feature itself.

> **[SCREENSHOT: a real Telegram fault alert message]**

# Chapter 7 — Under the Hood

## 7.1 Three Kinds of Boards, One Brain

Everything in this report runs on three kinds of hardware: the base
station (Chapter 2), satellite nodes (Chapter 3), and the motor-driver
rig (Chapter 5). Only one of them thinks. The base station's Linux side
is where the registry of machines lives, where models train and run
inference, where the dashboard is served, and where a decision to trip
a motor gets made — every other board is either a sense organ or a
muscle, not a decision-maker.

![Full architecture: satellite nodes and the base station's own STM32U585 feed the QRB2210 Linux brain, which drives the dashboard, phone alerts, status lights, and — on a confirmed fault — the motor-driver rig](diagrams/05-full-architecture.png)

## 7.2 Sensor Sample to Dashboard Pixel

One trip through the whole system, start to finish:

1. **Acquire** — the accelerometer and microphone are sampled at their
   native rate on whichever chip they're wired to (STM32U585 for the
   base station, ESP32S3 for a satellite).
2. **Reduce** — that chip runs an FFT and computes summary statistics
   locally, turning a firehose of raw samples into one compact frame a
   few times a second. This step matters: shipping raw audio/vibration
   data off-chip at its native rate would saturate any link fast enough
   to matter.
3. **Arrive** — the frame reaches the base station's Linux side, either
   over the internal chip-to-chip link (base station's own sensors) or
   over Wi-Fi/MQTT (satellite nodes) — Appendix E has the wire format,
   shared by both paths.
4. **Route** — the pipeline manager matches the frame to the right
   machine's registry entry and validates its shape against what that
   machine was commissioned with.
5. **Score** — features feed both the motor-state gate (is this machine
   even running right now?) and the autoencoder (how far from normal is
   this?), producing a status.
6. **Act & tell** — a status change updates the registry, which fans out
   to everything downstream at once: the dashboard over a live
   WebSocket, the status ring and matrix, a Telegram message if one's
   due, and — if the status is a confirmed fault on an armed
   machine — the trip command from Chapter 5.

## 7.3 The Chip-to-Chip Link

Inside the base station, the two chips talk over a wired serial link
(LPUART1) that never leaves the board. Regular telemetry — spectra and
summary stats — rides this link as lightweight remote-procedure calls,
kept deliberately small per message; a separate high-throughput path
exists alongside it for pulling a full-resolution raw waveform on
demand, used by the offline research tooling in Appendix G rather than
by the live monitoring loop. Keeping "a few numbers, often" and "a lot
of numbers, rarely" on separate paths means a large diagnostic pull
never has a chance to stall the real-time status loop everything else
depends on.

## 7.4 One Pattern, Reused Everywhere

Every sampling thread in this codebase — accelerometer, microphone,
whichever chip it's on — follows the same shape: sample continuously in
its own thread, publish only the *latest* result behind a lock, and let
a consumer read that latest value whenever it's ready rather than
queuing up a backlog of stale frames. It's a small, boring pattern, used
consistently on purpose: a monitoring system that falls behind should
skip forward to "now," not spend its time catching up on data about a
machine's past that's no longer actionable.

# Chapter 8 — Why We Built It This Way

## 8.1 The Big Calls, in One Line Each

- **A wired serial link between the two chips, not a second SPI bus.**
- **Train each machine's model on the machine, not in the cloud.**
- **A software-latched per-motor stop today, a hardware relay later.**
- **One wire format for every sensor, wired or wireless.**
- **Measure each machine's own quiet, don't assume everyone's is the same.**
- **A mature classifier platform for the second AI model, not a
  from-scratch training pipeline.**

## 8.2 A Wired Serial Link, Not a Second SPI Bus

The chip-to-chip link was nearly a second SPI bus running the QRB2210 as
master, the STM32U585 as slave. It was investigated seriously — the
Qualcomm SPI hardware only really supports master mode on Linux, so the
STM32 side would have had to run as a slave, which meant fragile
DMA timing above modest clock speeds and a separate signal wire just to
tell the master a frame was ready. A single bidirectional serial link
needed none of that: either side can talk whenever it has something to
say, no master/slave asymmetry, no extra wire. Less clever, more robust.

## 8.3 Train Each Machine's Model on the Machine

The alternative was straightforward: capture data, ship it to a cloud
service, wait for a trained model, deploy it back. That works, but it
turns "commissioning" from a five-minute walk-up task into a workflow
with a network dependency and a wait. Training locally, on the QRB2210
itself, keeps commissioning something a technician can start and finish
in one visit — and means a machine's raw vibration signature never has
to leave the building unless someone chooses to send it there.

## 8.4 A Software-Latched Stop Today, a Relay Later

The honest version of this decision: a relay per motor, wired to
physically break the power line, is the more bulletproof long-term
design — Chapter 10 has it on the roadmap. What's built today, a
per-motor command that halts step pulses and refuses to re-arm until
cleared, was chosen because it's real, testable on the hardware already
in hand, and doesn't require sourcing or wiring new parts against a
fixed deadline. It genuinely stops the motor from turning; it just isn't
yet the belt-and-suspenders version that also removes electrical power
at the source.

## 8.5 One Wire Format, Wired or Wireless

Base-station sensors and satellite nodes could easily have grown two
different data shapes — one for a link inside the board, one for a link
across Wi-Fi. They don't: both send the same generic
channel-plus-spectrum-plus-scalars frame (Appendix E). The AI pipeline
that scores a machine's health never has to know or care which kind of
node a frame came from, which is also what makes "add another satellite
node" a wiring-and-power task rather than a software one.

## 8.6 Measure Each Machine's Own Quiet

Chapter 5 told the story of a fixed vibration threshold quietly failing
because every accelerometer has its own noise floor. The design
principle that came out of it goes further than that one fix: don't
hardcode a number that's supposed to mean "this machine is running."
Measure it, per machine, per sensor, and compare against that. It's more
setup than a constant in a config file, and it's the difference between
a threshold that works on the bench and one that works on the fortieth
machine in a fleet with slightly different mounting, slightly different
sensors, slightly different everything.

## 8.7 A Mature Platform for the Second Opinion

Building a training pipeline for the fault-type classifier from scratch
was on the table. Edge Impulse — a platform built for exactly this
category of embedded/sensor ML — was chosen instead, so effort could go
into the parts of this project nothing else provides off the shelf: the
gate, the trip, the fleet dashboard. It also keeps that model
independent of the safety path in Chapter 5 by construction — the
classifier's job is to name a fault, never to decide whether to stop a
motor.

# Chapter 9 — Proof, Not Promises

## 9.1 What's Actually Been Run

Every claim in this chapter was measured on the real rig, not simulated
or estimated — sensors reading a spinning motor, a trip actually cutting
that motor's motion, a dashboard checked against a live device in a real
browser. Where a number appears below, it's a number that came out of
that hardware.

> **[SCREENSHOT: dashboard mid-trip — status pill, anomaly score chart,
> and console log all visible together]**

## 9.2 The Trip, Measured

The headline number from Chapter 5's calibration work, with the data
behind it. Per-bin energy, sensor stationary vs. the rig spinning at
90 RPM:

| Frequency bin | Stopped | Running | Delta |
|---|---|---|---|
| ~131 Hz | 13,192 | 36,134 | +22,942 |
| ~281 Hz | 12,680 | 44,798 | +32,118 |
| ~381 Hz | 13,586 | 40,638 | +27,052 |
| ~631 Hz | 13,453 | 13,545 | +92 |
| ~3,231 Hz | 5,525 | 5,483 | −42 |

The motor's actual signature lives in a handful of low-frequency bins;
everything past ~600 Hz is sensor noise, present whether or not anything
is moving. That's the whole reason a full-spectrum average was the wrong
metric:

| Method | Stopped reading | Running reading | Worst-case margin |
|---|---|---|---|
| Full-spectrum average (old) | 7,480 | 11,137 | 1.18× |
| Excess over measured baseline (current) | 1,414 | 6,194 | **2.09×** |

And the trip chain itself, verified in both directions on the real rig:
motor spinning → fault confirmed → **stops**, stays stopped, ignores
further speed commands until cleared from the dashboard; motor cleared
and spun back up → resumes normally. Both directions, repeatable, not a
one-time fluke.

## 9.3 A Real Commissioning Run

One actual commissioning result, numbers straight from the run: 65
frames captured with the rig confirmed off, a fitted baseline energy of
1,533.1, a measured spread of 1.39×, gate threshold set at 2,682.9.
Spinning the rig back up and re-commissioning against it produced a
healthy anomaly score of 0.046 against a warning threshold of 0.144 (and
a fault threshold of 0.288) — a machine confidently reading as itself,
with real daylight between "normal" and "the line that means trouble."

## 9.4 Known Limitations, Stated Plainly

- **Cross-talk on the demo rig.** The physical demo motors share a
  single vibration sensor. Tripping one motor while the others keep
  running still shows that shared sensor reading "running," because
  it's honestly still feeling the other two spin. This is a property of
  one sensor covering three physical motors on a shared test rig, not a
  software bug — a real deployment has one sensor per real machine, which
  doesn't have this problem.
- **The fault-type classifier isn't the safety path.** It names what
  kind of fault is likely, and it's useful, but the trip in Chapter 5
  runs off the healthy/warning/fault gate alone. If the classifier is
  ever wrong about *which* fault it is, the motor still stops — it just
  might be labeled wrong when it does.

## 9.5 Automated Tests

The backend carries an automated test suite exercised on every change,
not just manual spot-checks — including tests built directly from real
captured sensor data rather than synthetic numbers, specifically because
a hand-written "quiet" spectrum turned out too clean to have caught the
noise-floor bug in §9.2 the first time around. As of this report the
suite passes in full except for a small, known set that needs physical
on-device libraries unavailable off-hardware — expected gaps, not
regressions.

## 9.6 Honest Status Ledger

| Subsystem | Status |
|---|---|
| Base-station sensing (vibration + audio) | Live-verified on real hardware |
| Satellite sensor nodes | Live-verified on real hardware |
| Per-machine autoencoder commissioning | Live-verified on real hardware |
| Motor-state gate (stopped-baseline calibration) | Live-verified on real hardware |
| Physical trip (per-motor stop + latch) | Live-verified on real hardware, both directions |
| Dashboard (fleet, detail, commissioning, protection controls) | Live-verified on real hardware |
| Status ring + LED matrix | Live-verified on real hardware |
| Telegram alerts | Built and demonstrated; off pending one config value |
| Fault-type classifier (Edge Impulse) | Built, running on-device; accuracy still improving |
| Per-motor relay (independent power cutoff) | Future work — see Chapter 10 |

# Chapter 10 — What's Next for Ravi's Factory

## 10.1 The Near-Term List

Three things sit at the top, in the order they'd get built:

1. **A relay per motor.** Today's trip stops a motor from moving;
   a relay would remove its power at the source too — the more
   bulletproof version, held back only by "no new hardware" during this
   build window, not by any design uncertainty about how to do it.
2. **A sharper fault-type classifier.** More labeled data per machine
   type, and closing the remaining gap between "meaningfully better than
   guessing" and "trustworthy enough to act on."
3. **More satellite nodes, more machine types.** The architecture was
   built to scale to a real fleet from day one — proving that out on more
   than a handful of machines is the natural next test of it.

## 10.2 Back to Ravi

A year ago Ravi bought one machine and worried about it failing quietly.
Today his shop runs on more machines than he can watch personally, and
he doesn't have to — a light tells him what's fine, a phone tells him
what isn't, and once in a while, before anyone even picks up the phone,
a motor just stops on its own rather than grinding itself into a repair
bill. That's not a hypothetical story bolted onto a spec sheet — every
piece of it in this report was run on real hardware, more than once.

Sensing, deciding, acting — in that order, with nobody standing over it.
That was the whole assignment. Thanks for reading this far — Ravi
appreciates it, even if he'll never know your name.

---

# Appendix A — Full Bill of Materials

One combined list across all three subsystems. Quantities assume one base
station, one satellite node, and the three-motor demo rig used to validate
this report — scale the satellite row by however many additional machines
a real deployment monitors.

| Subsystem | Part | Qty | Role | Approx. cost (₹) |
|---|---|---|---|---|
| Base station | Arduino UNO Q | 1 | dual-brain board: real-time sensing (STM32U585) + AI/dashboard (QRB2210 Linux) | `[FILL IN]` |
| Base station | SmartElex KX134-1211 breakout | 1 | vibration sensing | ~913 |
| Base station | INMP441 breakout | 1 | audio sensing | `[FILL IN]` |
| Base station | WS2812B 8-pixel RGB ring | 1 | local status light | `[FILL IN]` |
| Base station | Jumper wires + mounting bracket/magnet | 1 set | wiring + rigid attachment to machine housing | `[FILL IN]` |
| Satellite node (×N) | Seeed Studio XIAO ESP32S3 | 1 per machine | wireless sensing node | `[FILL IN]` |
| Satellite node (×N) | SmartElex KX134-1211 breakout | 1 per machine | vibration sensing, same part as base station | ~913 |
| Satellite node (×N) | INMP441 breakout | 1 per machine | audio sensing | `[FILL IN]` |
| Satellite node (×N) | WS2812B 8-pixel RGB ring | 1 per machine | status light | `[FILL IN]` |
| Demo/validation rig | Arduino Uno | 1 | motor controller | `[FILL IN]` |
| Demo/validation rig | CNC Shield V3 | 1 | driver carrier board | `[FILL IN]` |
| Demo/validation rig | A4988 or DRV8825 stepper driver | 3 | one per motor axis | `[FILL IN]` |
| Demo/validation rig | NEMA-17 stepper motor | 3 | the "machines" being monitored on the bench | `[FILL IN]` |
| Demo/validation rig | 12–24V DC power supply | 1 | motor power | `[FILL IN]` |

Only the UNO Q requires proof of purchase for this competition; everything
else scales as the fleet grows. See Appendix C for why the KX134 was
chosen over cheaper and more expensive alternatives, and Appendix B for
exactly how each part is wired.

---

# Appendix B — Wiring & Pinout Reference

## B.1 Base Station (Arduino UNO Q, STM32U585 side)

| Sensor | Signal | Pin |
|---|---|---|
| KX134 accelerometer | SPI SCK / MISO / MOSI | D13 / D12 / D11 |
| | Chip-select | D8 (PB4, software GPIO) |
| | Interrupt (buffer-full) | D9 (PB8) |
| INMP441 microphone | SAI1 clock / frame-sync / data | PB10 / PB9 / PC1 |
| WS2812 status ring | Data in | PB0 (TIM3 channel 3) |

Debug logging runs on a separate physical link (USART1, JDIGITAL D0/D1)
straight to a host PC over USB-UART — fully decoupled from the QRB2210
side, so a log dongle never competes with the sensor/AI link.

## B.2 Satellite Node (Seeed XIAO ESP32S3)

| Signal | Pin | Notes |
|---|---|---|
| KX134 SPI SCK / MISO / MOSI | D8 / D9 / D10 | fixed hardware SPI pins on this board |
| KX134 chip-select | D3 | software GPIO |
| KX134 INT1 (buffer-full) | D2 | |
| INMP441 WS / LRCLK | D0 | |
| INMP441 BCLK | D1 | |
| INMP441 SD (data in) | D4 | |
| WS2812 ring data in | D5 | |

The XIAO ESP32S3 only breaks out 11 GPIOs; every pin above was chosen
specifically to avoid the board's fixed hardware SPI lines. Node identity
is derived automatically from the board's own WiFi MAC address — no
per-unit pin or ID configuration needed beyond the wiring itself.

## B.3 Motor-Driver Rig (Arduino Uno + CNC Shield V3)

| Signal | Pin | Notes |
|---|---|---|
| Shared driver enable (`~ENABLE`) | D8, active-LOW | one line for all three driver sockets — no per-motor hardware enable |
| Motor 1 (X) STEP / DIR | D2 / D5 | |
| Motor 2 (Y) STEP / DIR | D3 / D6 | |
| Motor 3 (Z) STEP / DIR | D4 / D7 | |

Driver current limit (the small potentiometer on each A4988/DRV8825) must
be set before running — under-current skips steps, over-current
overheats the driver. A4988: `Vref ≈ Imax × 8 × Rsense`; DRV8825:
`Vref ≈ Imax / 2`.

---

# Appendix C — Sensor Selection Rationale

The KX134-1211 is used as the vibration transducer at every sensing point
in this architecture — base station and every satellite node alike. It
was chosen over both hobby-grade and industrial-grade alternatives on
eight criteria:

| Criterion | Hobby grade | **KX134 (selected)** | Industrial grade |
|---|---|---|---|
| Max ODR / usable Nyquist | ~1 kHz / 250–500 Hz | **25.6 kHz / 12.8 kHz** | continuous / ~11 kHz (needs external ADC) |
| Dynamic range | fixed | **software-selectable ±8/16/32/64g** | fixed, part-specific |
| Noise density | ~300 µg/√Hz | **~130 µg/√Hz** | 25–80 µg/√Hz |
| Interface | often analog | **digital SPI, on-chip 16-bit ADC** | often analog |
| FIFO | none | **512-byte hardware FIFO** | varies |
| Unit cost (India) | ₹150–350 | **~₹913** | ₹3,800–7,200 |

Each criterion mattered for a specific reason:

- **Bandwidth was a hard filter, not a preference.** Early-stage fault
  signatures — micro-pitting, incipient bearing race damage — live in the
  2–10 kHz band. A sensor that physically can't see that band can't be
  compensated for downstream, no matter how good the signal processing
  is. Hobby sensors are eliminated by this alone.
- **Dynamic range as software, not hardware.** ±8/16/32/64g selectable in
  firmware means the same physical part works on a quiet bench rig and on
  a production motor with real startup transients, without a hardware
  swap or a second SKU to stock.
- **Noise density sets the detection floor.** A noisy sensor effectively
  raises the anomaly threshold before any software ever runs, hiding
  exactly the small, early signals this system exists to catch — this
  is also the property that turned out to matter most in Appendix F's
  investigation, in a way not fully appreciated until that debugging
  session.
- **The FIFO changes the real-time budget.** Without it, the host has to
  service the sensor roughly every 39 microseconds at full rate — an
  aggressive interrupt load for a chip that also has to run FFTs and
  manage a network link. With it, the sensor batches samples and fires
  one interrupt per batch, turning a high-frequency servicing problem
  into a manageable, batched one on both the STM32U585 and the ESP32S3.
- **Cost matters at fleet scale, not per unit.** This architecture is
  built to scale toward 20+ sensor nodes, and the competition rules only
  require proof of purchase for one UNO Q, not one per node. At ~₹913,
  scaling sensor count stays close to linear; at industrial pricing, the
  same fleet target becomes cost-prohibitive.
- **India availability was a hard scheduling constraint.** The KX134
  evaluated here sits on the SmartElex breakout platform, sourced through
  the regional Indian electronics market — avoiding the shipping and
  import-duty lead time that comes with sourcing industrial-grade parts
  from overseas against a fixed competition deadline.

No single criterion justified the KX134 alone — hobby sensors fail on
bandwidth, industrial sensors fail on cost and lead time. It's the first
point in the market where all of these constraints are satisfied at once.

---

# Appendix D — Network & Transport Selection Rationale

Satellite nodes needed a link to the base station that was both
**real-time** (continuous spectrum streaming, not periodic bursts) and
**bidirectional** (the base station must be able to send commands back,
not just receive data). Three options were evaluated.

**BLE advertise-only (beacon pattern).** Attractive for scaling to 20+
nodes with minimal per-connection overhead and low power. Rejected
outright once bidirectional control became non-negotiable — advertising
is inherently one-way, and no protocol cleverness fixes a fundamentally
one-directional transport.

**BLE GATT (connection-based).** Natively bidirectional (Notify from node
to base station, Write from base station to node), and would have kept
the project inside the radio stack originally planned. Rejected after
investigation surfaced multiple documented reliability issues in the
Linux BlueZ stack with concurrent GATT connections to more than one
peripheral from a single central — including cases where service
resolution hangs on the second concurrently-connected device, reproduced
across several BlueZ versions. For a link with no acceptable downtime,
that risk — discovered late in the build cycle, against a fixed
competition deadline — was disqualifying on its own.

**WiFi (UNO Q-hosted access point + MQTT) — selected.** The UNO Q's
onboard wireless module was confirmed to support access-point mode, so
the base station hosts its own network rather than depending on venue
WiFi (which can't be trusted at a competition venue). ESP32 nodes join as
clients; an MQTT broker running on the UNO Q mediates pub/sub traffic in
both directions. This won on reliability (mature, well-understood
WiFi/TCP/MQTT, none of BLE's concurrency issues for this use case),
throughput (full-resolution spectrum data streams without BLE's
aggressive payload-size constraints), and infrastructure reuse (the same
MQTT broker and FastAPI ingestion backend already used everywhere else in
the system). The accepted trade-off: WiFi's continuous radio use isn't
inherently lower-power than BLE for a sustained streaming workload — but
since continuous real-time streaming already negated BLE's main power
advantage, this was assessed as a wash, not a net loss.

BLE advertise-only remains a credible *production-scale* direction for a
much larger deployment (20+ nodes, periodic health summaries rather than
live streaming) — noted here as a real future option, deliberately not
the one this build prioritizes.

---

# Appendix E — Wire Protocol Specification

Two sensing paths — the base station's own internal MCU↔MPU link and
every satellite node's WiFi link — carry the same conceptual message
types over different framing, chosen to match what each transport
already guarantees: UART is a raw byte stream with no message
boundaries of its own; MQTT already provides framing, addressing
(topics), and delivery semantics.

## E.1 Shared Message Types

| TYPE (hex) | Name | Direction | Purpose |
|---|---|---|---|
| 0x01 | SPECTRUM | Node → Base Station | FFT bins from vibration/audio sensing |
| 0x02 | HEALTH_ALERT | Node → Base Station | Anomaly threshold crossing |
| 0x03 | HEARTBEAT | Node → Base Station | Liveness signal, current config echo |
| 0x04 | COMMISSION_START | Base Station → Node | Begin commissioning capture |
| 0x05 | COMMISSION_DONE | Base Station → Node | Commissioning complete; switch to inference |
| 0x06 | CONFIG_SET | Base Station → Node | Sample rate, FFT size, active-channel config |
| 0x07 | ACK | Either direction | Acknowledge a critical message |
| 0x08 | STATUS_LED | Base Station → Node | Drive the node's status LED to match its dashboard status |

`SPECTRUM` and `STATUS_LED` are the two types in production use today; the
rest share this same numbering so ingestion code treats a message
uniformly no matter which link it arrived on, once built out further.

## E.2 UART Framing (STM32U585 ↔ QRB2210, internal to the base station)

```
[SYNC: 2B][VER: 1B][TYPE: 1B][NODE_ID: 1B][LEN: 2B][PAYLOAD: N bytes][CRC16: 2B]
```

`SYNC` is a fixed `0xAA55` marker so a receiver can resynchronize after a
dropped byte. `NODE_ID` is a constant `0x00` — this link is point-to-point,
exactly one MCU, so the field exists only for symmetry with the WiFi side.
`CRC16` covers `VER..PAYLOAD` and catches wire-level bit errors. The link
is full duplex: the MCU can stream `SPECTRUM` on TX while simultaneously
receiving a control message on RX.

`SPECTRUM`'s payload carries both channels' full FFT bins, not just peaks:

```
[MIC_FS: 4B float][MIC_FFT_SIZE: 2B][MIC_BIN_COUNT: 2B]
[ACCEL_FS: 4B float][ACCEL_FFT_SIZE: 2B][ACCEL_BIN_COUNT: 2B]
[MIC_BINS: MIC_BIN_COUNT × 4B float][ACCEL_BINS: ACCEL_BIN_COUNT × 4B float]
```

Sample rate and FFT size travel on the wire per frame rather than being
fixed knowledge baked into the receiver — a disabled channel simply
carries `BIN_COUNT = 0` and contributes no bin bytes.

## E.3 WiFi/MQTT Framing (ESP32 satellite ↔ base station)

Topics, per node (`<node_id>` derived from the node's own WiFi MAC
address, e.g. `a4cf12`):

```
epm/<node_id>/data     - Node -> Base Station (SPECTRUM, HEALTH_ALERT, HEARTBEAT)
epm/<node_id>/cmd      - Base Station -> Node (COMMISSION_START, CONFIG_SET, STATUS_LED, ...)
epm/<node_id>/ack      - Either direction (ACK)
```

Since MQTT already frames and addresses each message, the payload
envelope is leaner than UART's — just a type byte in front of the same
type-specific payload:

```
[TYPE: 1B][PAYLOAD: N bytes]
```

`STATUS_LED`'s payload (`[RGB: 4B][MODE: 1B][PERIOD_MS: 2B]`) is the same
struct on both transports, so a satellite ring and the base station's own
ring always mean the same thing by "breathing amber" or "strobing red" —
one color table, one mode enum, shared everywhere a status light exists.

## E.4 QoS (MQTT side)

| Message type | QoS | Why |
|---|---|---|
| SPECTRUM, HEARTBEAT | 0 | High frequency; occasional loss acceptable |
| HEALTH_ALERT, COMMISSION_START/DONE, CONFIG_SET, STATUS_LED | 1 | Must arrive; duplicates are harmless (idempotent) |
| ACK | 0 | Advisory only |

---

# Appendix F — Motor-State Gate Calibration: the Full Investigation

Chapter 5 told the short version. This is the full one, because the
process — three wrong layers before the real cause — is worth more to a
future reader than the fix alone.

## F.1 The Question

Before the system can decide "this machine just stopped" (needed both to
suppress false anomaly scores at rest and to confirm a trip actually
landed), it needs a reliable way to tell running from stopped off vibration
energy alone. The naive approach — an absolute threshold on a single
energy number — was the very first version, and it failed early and
obviously (readings around 0.05 against real running energy around
19,000): fixed by moving to a *relative* threshold, a fraction of each
node's own commissioned running-energy reference, rather than a global
constant. That fix is what's referred to elsewhere as the original
`running_energy_ref` design — solid in principle, but it only pushed the
real problem one layer down.

## F.2 Layer Two: Blaming the Microphone

The relative-threshold gate initially summed *every* channel present —
mic included — into one energy number. Ambient shop noise picked up by
the microphone has nothing to do with whether a motor is turning, so
excluding it looked like the obvious fix. It was implemented, and it
was correct in principle — but measured live, it barely moved the idle
number (~6,600–7,250 combined vs. ~7,000–8,000 accelerometer-only, the
same order of magnitude). Whatever was keeping "idle" energy close to
"running" energy was coming from the accelerometer channels themselves,
not the mic.

## F.3 Layer Three: A Reasonable Guess That Was Wrong

The next hypothesis was a DC/gravity bias: if FFT bin 0 carries a large,
roughly-constant offset from gravity, it would dominate an RMS regardless
of whether the motor was spinning, explaining why idle and running looked
like the same order of magnitude instead of differing by orders of
magnitude. It was a reasonable theory and it was checked properly before
building anything on top of it — and it turned out to be wrong. The
firmware's own FFT magnitude routine already discards bin 0 before any of
this code ever sees it. Real captured accelerometer windows confirmed it
from the other direction too: the raw gravity offset sits at a mean of
about 4,228 counts, and none of that offset appears anywhere in the bins
the software actually receives.

## F.4 The Real Cause: the Sensor's Own Noise Floor

The actual gate computed an RMS over *every* bin of every accelerometer
channel — 384 of them for a three-axis, 128-bin node. Measured live, per
pooled bin (stopped vs. running at 90 RPM):

| Bin | ~Hz | Stopped | Running | Delta |
|---|---|---|---|---|
| 2 | 131 | 13,192 | 36,134 | **+22,942** |
| 5 | 281 | 12,680 | 44,798 | **+32,118** |
| 7 | 381 | 13,586 | 40,638 | **+27,052** |
| 12 | 631 | 13,453 | 13,545 | +92 |
| 24 | 1,231 | 11,217 | 11,482 | +265 |
| 64 | 3,231 | 5,525 | 5,483 | −42 |

The motor's entire mechanical signature is a handful of narrow lines
below ~600 Hz — the stepper's own step rate, 90 RPM × 200 full steps =
300 Hz, landing squarely on bins 5–7. Every other bin — the vast majority
of the spectrum — is the KX134's own broadband electrical noise, present
identically whether the machine runs or not. Averaging across all of it
was mostly a measurement of the accelerometer, not the machine, which is
also why fault classes captured from this rig look nearly identical to
each other above roughly bin 24 — worth remembering the next time
classifier accuracy (Appendix G) is the question.

## F.5 The Fix: Subtract a Measured Baseline

Rather than trust a formula to separate signal from noise, the system now
measures each node's own noise floor directly: a node captures ≥30 frames
with its machine deliberately off, fits a per-bin median floor from them,
and the gate thereafter measures only the *excess* over that floor.
Measured effect, same rig, same session:

| Method | Stopped reading | Running reading | Worst-case margin |
|---|---|---|---|
| Full-spectrum average (old) | 7,480 | 11,137 | 1.18× |
| Excess over measured baseline (new) | 1,414 | 6,194 | **2.09×** |

This is deliberately independent of the existing commissioned
running-energy reference: a node with no captured baseline keeps today's
behavior unchanged, so capturing one can never invalidate an existing
model, and the two numbers are never compared across scales — energy and
threshold are always picked together, on the same basis, for the same
measurement.

## F.6 Alternatives Considered and Rejected

- **Band-limiting energy to the motor's own frequency range** (bins 0–7
  only) instead of subtracting a floor: measured, and it separated
  *worse* (1.09× vs. 2.09× subtracted) — the noise floor turns out to be
  *tallest* in exactly the low bins where the real signal also lives, so
  narrowing the window doesn't escape it.
- **Reference-free "peakiness" / spectral-flatness metrics**: measured,
  and all of them overlapped between stopped and running in the
  worst case observed.

## F.7 Live Verification

All of the above was run against real hardware, not simulated: a
baseline captured with the rig confirmed physically off (65 frames,
energy reference 1,533.1, spread 1.39×, threshold 2,682.9); the node
observed going from flapping fault/warning at rest to settling cleanly
on idle; the rig spun back up and observed leaving idle immediately;
re-commissioning produced a healthy score of 0.046 against a warning
threshold of 0.144; ramping down returned cleanly to idle, not fault; and
the dashboard was checked against the live device in a real browser with
zero console errors throughout.

## F.8 Known, Accepted Limitation

The demo rig's three motors share one physical vibration sensor. With
motor 1 tripped and motors 2/3 still spinning, that shared sensor still
reads "running," because it's genuinely still feeling the other two. This
is a property of one sensor covering three physical test motors on a
single bench rig, not a software defect — a real deployment has one
sensor per real machine and doesn't have this problem.

---

# Appendix G — Edge Impulse Classifier Experiments

The fault-type classifier's job (Chapter 4) is to name *which* fault is
present, not just whether one exists. Getting there involved a real
research process, including two data-integrity bugs caught before they
could quietly inflate a reported accuracy number.

## G.1 Starting Point: a Public Dataset, Replayed

The first version trained against a public Kaggle vibration dataset
(four classes: healthy, cracking, offset pulley, wear), replayed through
a simulated satellite node so the classifier could be built and tested
end-to-end before any physical fault data existed. This established the
Edge Impulse workflow: upload labeled spectra, design a feature pipeline,
train, read back a confusion matrix, export a deployable model.

## G.2 Bug One: Train/Test Leakage

An early run reported a low but plausible-looking accuracy. Before
trusting it, the split was checked — and it was leaking: windows from the
same source file were landing in both the training and test sets, so the
model was partly being tested on data adjacent to what it trained on. The
fix was a file-level split (an entire source file goes to either train or
test, never both), and the honest number after the fix was materially
different from the leaky one. Any accuracy figure from before this fix is
explicitly considered stale and is not quoted anywhere else in this
report.

## G.3 Bug Two: a Data-Corruption Bug in the Upload Path

A second, deeper bug was found in the signal-loading step used to
prepare every upload — affecting every dataset uploaded to that point,
not just one run. After fixing it, a raw triaxial re-run scored 59.82%
on Edge Impulse's own trained model. A local from-scratch replication of
a reference paper's classical (KNN-based) method, run on the same
corrected data, scored 69.64% — a real, honest finding that a simpler
classical method outperformed the cloud-trained neural network on this
specific dataset and feature representation. Worth keeping rather than
hiding: it's a genuine result, not a failure.

## G.4 A Deliberate Strategic Pivot

At this point, accuracy was still improving with more tuning, but the
marginal return on more hyperparameter/feature-representation chasing
was assessed as lower than the return on finishing the rest of the
pipeline (dashboard integration, the physical trip, satellite bring-up).
That's a call worth stating plainly rather than implying the number
above was the ceiling of what's possible — it's the point at which effort
was consciously redirected, not a wall that was hit.

## G.5 Moving to Real Hardware Data

The classifier later moved from Kaggle-replayed data to real captures
taken directly off the project's own demo rig — 541 captures across
bearing, healthy, loose-mount, and unbalanced conditions, converted to
536-dimensional per-axis spectra plus scalar features and uploaded to a
dedicated Edge Impulse project. Because each fault condition exists as a
single continuous capture file rather than many independent short
samples, the usual file-level train/test split from §G.2 isn't available
here — a contiguous-tail split (the last portion of each file reserved
for test, never seen in training) was used instead, the closest
available approximation to a leakage-free split under that constraint.

## G.6 Sharing One Model Per Machine Type

The dashboard's classifier UI was reworked around one card per **asset
class** (machine type) rather than per individual node, with a pooled,
per-device-type baseline normalization — so uploading and training
happens once for every machine of the same type, not once per physical
unit. This is what makes the "train once, cover every identical lathe in
the shop" claim in Chapter 1 a real, built capability rather than an
aspiration.

## G.7 A Bug Only the Real Service Could Find

Testing against a real Edge Impulse account (rather than local tooling
alone) surfaced a broken axis-naming issue in the uploaded feature
columns. It was fixed by naming columns explicitly and correctly
(`accel_x_bin0`, `rms_x`, and so on) and wrapping them in a proper
"features" input block — a good reminder that a pipeline which passes
every local test can still behave differently the moment it meets the
real external system it was built against.

---

# Appendix H — Software Setup & Reproduction Guide

## H.1 Base Station

1. Flash `base-station/sketch/` onto the UNO Q's STM32U585 side through
   Arduino App Lab.
2. From `base-station/`, run:
   ```sh
   ./start_dashboard.sh
   ```
   This builds/flashes/pushes the app, brings the container up, and
   prints the board's own LAN-IP dashboard URL — use that link, not a
   localhost address, since a real deployment has no port-forwarding
   available.
3. Open the printed URL in a browser. Sensor tiles appear as soon as
   real data starts flowing.

## H.2 Satellite Node

1. Set `WIFI_SSID` / `WIFI_PASSWORD` for the shop's base-station-hosted
   network in `satellite/include/app_config.h` (or override at build
   time via `platformio.ini`'s `build_flags`).
2. From `satellite/`:
   ```sh
   pio run                # build
   pio run -t upload      # flash over USB
   pio device monitor     # optional: serial log console, 115200 baud
   ```
3. No further per-device setup — the node identifies itself
   automatically from its own hardware MAC address and appears on the
   dashboard once it starts publishing.

## H.3 Motor-Driver Rig

1. Flash `motor-driver/src/main.cpp` to the Arduino Uno via PlatformIO.
2. Set each driver's current limit (Vref) before powering the rig —
   see Appendix B.3.
3. Run the rig's own local control page via its `start_dashboard.sh`, or
   drive it directly over serial for scripted test profiles.

## H.4 After Changing the Wire Format

Any edit to `base-station/telemetry_schema.json` (the single source of
truth for the shared frame format in Appendix E) must be followed by
regenerating every generated side, so the base station, MPU parser, and
satellite firmware can never silently drift apart:

```sh
python3 base-station/python/tools/gen_telemetry_schema.py
```

## H.5 Tests

The backend's automated test suite lives under `base-station/tests/` and
`base-station/python/`; most of it runs standalone, with a small,
documented subset requiring on-device-only libraries and therefore
excluded from off-hardware runs (see Chapter 9 §9.5).

---

# Appendix I — Glossary

- **Autoencoder** — a neural network trained only on a machine's healthy
  data; the gap between what it reconstructs and what actually came in
  is the anomaly score.
- **Anomaly score** — a single number describing how far a live reading
  sits from what a machine's own autoencoder considers normal.
- **Base station** — the Arduino UNO Q running the AI pipeline, dashboard,
  and (usually) one directly-wired sensor.
- **Commissioning** — the short "run the machine, let the system learn
  it" step that trains a new machine's autoencoder.
- **Crest factor, kurtosis, skewness, RMS** — statistical descriptors of
  a signal's shape, computed alongside its FFT spectrum as part of the
  feature vector fed to both AI models.
- **Fleet** — every machine (base-station-attached or satellite) the
  dashboard currently tracks.
- **Gate (motor-state gate)** — the mechanism that decides whether a
  machine is currently running or stopped from vibration energy alone.
- **LPUART1** — the wired serial link inside the base station connecting
  its two internal chips.
- **MQTT** — the publish/subscribe messaging protocol satellite nodes use
  to talk to the base station over WiFi.
- **Node** — any one monitored machine's entry in the system, whether
  sensed by the base station directly or by a satellite.
- **Physical AI** — an AI system whose output is a real-world physical
  action, not just information — sensing, deciding, and acting in one
  loop, without a human in that loop at the moment of action.
- **QRB2210** — the Qualcomm chip on the UNO Q running Linux, the AI
  models, and the dashboard.
- **Registry** — the base station's live record of every known machine,
  its status, and its configuration.
- **Satellite node** — a wireless sensing node (Seeed XIAO ESP32S3) that
  reports to the base station over WiFi/MQTT instead of a wire.
- **STM32U585** — the chip on the UNO Q running Zephyr RTOS and doing the
  real-time sensor sampling.
- **Stopped baseline** — a per-node measurement of what its sensor reads
  with the machine deliberately off, used to separate real signal from
  the sensor's own noise floor.
- **Trip** — the act of stopping a specific motor in response to a
  confirmed fault; sticky until explicitly cleared.
