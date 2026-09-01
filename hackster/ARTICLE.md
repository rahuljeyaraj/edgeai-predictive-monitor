<!-- ==========================================================================
     NOT PART OF THE ARTICLE — TRANSLATION LEGEND
     Hackster's editor has no markdown. It is a toolbar. When transcribing,
     paste the text, select it, and press the button named here.

       a line starting with #   ->  the H button (only one heading level)
       **bold**                 ->  the B button
       *italic*                 ->  the i button
       `text in backticks`      ->  the # button   (inline code)
       a ``` fenced block       ->  the </> button (block code)
       a line starting with >   ->  the quote button
       a line starting with -   ->  the bullet button (one level, never nested)
       [text](url)              ->  the link button
       [IMAGE: ...]             ->  the image embed
       [VIDEO: ...]             ->  the video embed

     TWO EDITOR LIMITS, ALREADY HANDLED IN THIS SOURCE
       1. One heading level only. Every heading carries a number prefix
          (1, 1.1, 1.1.1). Press the same H button regardless of depth.
       2. Bullets cannot nest. There are no nested bullets in this file.
          Hierarchy is carried by numbered sub-headings instead.

     Every [IMAGE:] / [VIDEO:] marker is followed by an italic caption line.
     At that marker, upload/embed, then paste the caption and italicise it.
     ========================================================================== -->

<!-- ==========================================================================
     NOT PART OF THE ARTICLE — HACKSTER FIELDS (filled outside the story body)

     THINGS USED IN THIS PROJECT  (widget, not story text)
       Hardware components
         - Arduino UNO Q (4GB, ABX00173)          x1
         - Seeed Studio XIAO ESP32-S3             x2
         - Kionix KX134-1211 accelerometer        x3
         - InvenSense INMP441 I2S microphone      x3
         - WS2812B addressable RGB LED            x3
         - Arduino Uno R3                         x1
         - Protoneer CNC Shield V3                x1
         - Allegro A4988 stepper driver           x3
         - NEMA-17 stepper motor                  x3
       Software apps and online services
         - Arduino App Lab
         - Zephyr RTOS
         - Edge Impulse Studio
         - PyTorch
         - TensorFlow Lite
         - PlatformIO IDE
         - KiCad
         - Autodesk Fusion / slicer
       Hand tools and fabrication machines
         - 3D printer
         - Crimping tool (JST-XH)
         - Soldering iron
       NOTE: the long-tail parts (JST shells, crimp pins, M6 bolts, bearings,
       filament, wire) stay OUT of the widget and live in story section 5.1.

     OTHER REQUIRED FIELDS
       - Cover image: [PENDING — hero shot, see shot list at end of file]
       - Difficulty: Advanced
       - Full instructions provided: yes
       - Estimated time: [PENDING]
       - Licence: MIT
       - Custom parts and enclosures: 3d-models/ (16 x .3mf)
       - Schematics: hardware/kicad/ + the three KiCad PNGs
       - Code: github.com/rahuljeyaraj/edgeai-predictive-monitor
     ========================================================================== -->

<!-- ==========================================================================
     NOT PART OF THE ARTICLE — DRAFTING NOTES

     TARGET: ~6,000 words, ~30 visuals. Every paragraph 2-4 lines.
     No subsection over ~150 words. Diagrams replace paragraphs, never
     illustrate them: prose points AT the picture.

     TONE: plain headings, interesting sentences. First person. No
     "imagine if your machine could..." openers anywhere.

     UNO Q / APP LAB / BRICKS: hyped where it is naturally load-bearing —
     4.2 (the pitch), 6.3 and 6.4 (one deploy, both halves), 6.5 (the
     Telegram brick holds the secret), 8.1-8.2 (two brains), 2.1 (the LED
     matrix is already on the board). Never bolted on.

     STATUS: skeleton only. Prose to be written after shape review.
     ========================================================================== -->

# 1 Introduction

## 1.1 The problem: the grinder we finally gave up on

There is a table-top wet grinder in our kitchen that ground batter every other morning for years. It was repaired more times than I can count. A switch one year, a belt another, once a whole afternoon lost at a repair shop across town.

Nobody ever announced that we were done with it. We simply stopped reaching for it, and it moved to the back of a shelf.

Months later I needed a real machine to monitor. Something with a motor, a load, and an honest way to fail. The grinder came to mind straight away, so I brought it down and opened it up.

The start switch was broken, so I rewired it. The belt was old and slack, so I replaced it. It ran. And it still made the same creaky sound it had been making on what I now think of as its last good day, the sound the whole house had quietly agreed was just how that machine sounded.

An electrician helped me take the motor and the rotating drum apart. He had the bearing puller I did not. Three ball bearings came out of it: two from the motor, one from the drum. Every one of them rusted.

I turned one over in my hand and felt it. Not heard it, felt it. Each rotation caught, released, and caught again, something small and broken dragging around inside the race. It had clearly been like that for a long time.

That is where this project comes from. The bearing never failed suddenly. It spent weeks telling us, in vibration nobody was listening to, and then it took the machine with it.

So I built the thing that would have been listening.

[IMAGE: the grinder fully disassembled on the floor, the three ball bearings in the middle]
*The grinder, opened up. Two bearings from the motor and one from the drum, all three rusted. You could feel the damage by turning them in your hand.*

## 1.2 The idea: a sensor on every machine, and one screen for all of them

What came out of that is a small sensor node that sits on a machine and pays attention to two things: how it shakes and how it sounds. It holds on with a magnet, so putting one on a machine takes seconds and leaves the machine exactly as it was.

There are two kinds of node. The base station is an Arduino UNO Q. It watches the machine it is sitting on, and it is also the brain for everything else: every model runs there, every decision is made there, and the dashboard is served from it. A satellite is a smaller and cheaper node built on a XIAO ESP32-S3. It watches its own machine and sends what it measures to the base station over Wi-Fi. One base station covers as many satellites as the shop needs. Thirteen machines report to a single board in the demo.

Each node arrives with no opinion about the machine it is on. You commission it, which takes a few minutes. You show it the machine stopped, then the machine running normally, and it learns what normal sounds and feels like for that machine specifically: that mount, that load, that bearing, that corner of the room. An identical machine on the next bench learns its own normal, because it will not shake the same way.

From then on it keeps comparing the machine against that learned normal. As something wears or loosens, the vibration and the sound move away from it, and the score the base station reports climbs. Cross the first line and it warns you. Cross the second and it calls the machine faulty, and for the faults it has been taught, names which one: unbalance, a loose mount, a failing bearing.

Then it acts on its own. A Telegram message goes to your phone, a countdown appears on the dashboard, and if nobody holds it for ten seconds the base station stops the motor. It protects the machine at three in the morning with the shop empty and every phone on silent.

[IMAGE: 01-system-at-a-glance.png]
*Satellites feed one base station. It decides, and on a confirmed fault it reaches back out and stops the machine.*

## 1.3 The machines in this project are that grinder

Everything here is tested on three motor rigs I built so that faults could be induced on them on purpose. They are not generic stand-ins for machinery. Their geometry came from the grinder.

The two "pump" rigs are direct drive, a motor turning a flywheel, the way the grinder's motor turned. The "turbine" rig is belt driven, and its bearing carries a side load the way the drum's bearing did.

The bearings are the real ones. When I rebuilt the grinder I kept every bearing I pulled out of it and bought new ones of the same size to go back in: 6201 for the motor, 6004 for the drum. So the worn bearing in these rigs is the bearing that killed the grinder, back in a running machine, felt by an accelerometer instead of by hand.

Every other fault is just as physical. Bolts come off the flywheel to unbalance it, mounting screws are slackened for a loose mount, and the worn bearing goes back in for a bearing fault. Nothing is injected in software, and the system is trained to name all three. The bearing is only the one that started it.

[IMAGE: the salvaged 6004 bearing beside its new replacement]
*Left, the bearing that killed the grinder. Right, the one that replaced it. Both go back into the rigs, one at a time.*

[IMAGE: the three motor rigs on the bench, sensor pods clipped on]
*Three rigs standing in for three machines. Their geometry came from the grinder that started this.*

---

# 2 Living with it

<!-- EXPERIENCE TIER. "What you would see and do", never a feature list.
     This tier must be a complete read on its own. No engineering. -->

## 2.1 The light on the machine

<!-- Start at the machine, not the screen — this is what an operator on the
     floor actually sees. The dome is a diffuser salvaged from a dead LED
     bulb. Colours: green healthy, amber warning, red fault, white idle,
     cyan not yet set up. VERIFY every colour against the LED code before
     writing — the ring palette was redesigned. ~150 words. -->

[IMAGE: three rigs running, domes lit green, amber and red]
*One glance across the room tells you which machine needs you.*

## 2.2 The fleet, at a glance

<!-- The Fleet tab. Counts along the top, one row per machine, colour-coded.
     13 assets in the demo. Filter tiles. Drag to reorder. ~130 words. -->

[IMAGE: fleet view screenshot, 13 assets]
*Thirteen machines, and the three that need attention are the three that are coloured.*

## 2.3 What one machine tells you

<!-- Expanding a row: anomaly score plotted live against the amber and red
     lines, the fault-name chip, the spectrum and the waterfall underneath.
     Keep it as "what you read", not "what it computes". ~140 words. -->

[IMAGE: expanded asset card — score plot, spectrum, waterfall]
*The score, the two lines it must stay under, and the spectrum underneath for anyone who wants to look closer.*

## 2.4 Setting up a new machine, in six steps

<!-- Narrative, not procedure — the procedure is section 7. Name and class,
     machine off, machine running, train, prove the stop output, done.
     The punch: training happens on the board and takes seconds. Four to
     six minutes and the machine is live. ~200 words. -->

[IMAGE: 15f-setup-steps.png]
*Six steps. The one that catches people out is step two, and the software says so.*

## 2.5 The ten seconds before it stops the motor

<!-- The best moment in the demo. A fault is confirmed. A banner appears
     with a countdown. An operator can hold it. Nobody does. The motor
     stops. Then: acknowledge, fix, reset. Write it as a scene. ~180 words. -->

[VIDEO: the trip — fault induced, countdown, motor stops]
*Unbalance induced by hand. Detected, named, and stopped, in about eleven seconds.*

## 2.6 The message on your phone

<!-- Scan a QR code once, and the alerts arrive. Show the real thread:
     bearing fault, loose mount, both tripped, then both recovered.
     ~110 words. -->

[IMAGE: Telegram thread on a phone — fault, trip, recovery]
*Set up once with a QR code. After that it just tells you.*

---

# 3 Running a fleet

<!-- OPERATIONAL TIER. The admin surface behind the operator-facing one.
     Still narrative, still no engineering detail. -->

## 3.1 Teaching it to name a fault

<!-- The Edge Impulse round trip, told as a workflow, not an API tour:
     record labelled data with a button, link an account, upload, train in
     Studio, then one Fetch button brings the model down. One model per
     asset class, not per machine. ~200 words. -->

[IMAGE: 11-edge-impulse-flow.png]
*Record, link, upload, train, fetch. Only one of those steps happens outside the dashboard.*

## 3.2 Getting it onto the shop Wi-Fi from a phone

<!-- Onboarding. The base station raises its own hotspot; a phone joins and
     is redirected. Satellites do the same, and the broker address fills
     itself in. If the shop has no Wi-Fi, the base station's hotspot IS the
     network. Nothing is compiled in. ~160 words. -->

[IMAGE: 09-onboarding.png]
*Three fields on a phone. There is no credential compiled into any firmware.*

## 3.3 Watching the monitor watch itself

<!-- The Performance tab, and the strongest scalability evidence in the
     project: 13 assets, four cores idle, ~2% of the frame time budget
     used. VERIFY the exact numbers against the perf page before writing.
     ~130 words. -->

[IMAGE: performance tab with 13 assets running]
*Thirteen machines reporting at once, and the board is barely awake.*

---

# 4 System overview

## 4.1 Hardware at a glance

<!-- One paragraph naming the three kinds of board and what each does.
     This is the last thing a non-builder needs. ~150 words. -->

[IMAGE: 05-full-architecture.png]
*Three kinds of board. Only one of them decides anything.*

## 4.2 Why the Arduino UNO Q, and not three boards

<!-- THE PITCH. Make it argued, not advertised:
     - a Zephyr STM32U585 sampling at 12.8 kHz, deterministically
     - a Debian QRB2210 running PyTorch, on the same board, same power,
       same USB cable
     - normally that is two or three boards and a hand-built bridge
     - because Linux is real, training happens in the field in seconds —
       no cloud round trip, no subscription
     - the models are small enough that one board holds 20 of them
     - the 8x13 LED matrix is already on the board; we added a Fresnel lens
     - App Lab flashes the sketch AND deploys the Linux app from one tool
     - closing line: one of the few boards where sampling a sensor properly
       and training a model on it are the same purchase order
     ~280 words. Longest subsection in the article, and earns it. -->

[IMAGE: 14-two-brains.png]
*Two chips, one board. The left half never stops sampling; the right half does the thinking.*

## 4.3 Two ways to read the rest

<!-- THE FORK. One short paragraph, and it must be explicit and permissive:
     "If you want to build one, sections 5, 6 and 7 are the build. If you
     want to know how it works, section 8 is the machine. Skip to whichever
     you came for." Then the payoff gallery, BEFORE either track. -->

[IMAGE: hero — base station and satellite, cases open, domes lit]
*The base station on the left, a satellite on the right. Same sensors, same frame on the wire.*

[VIDEO: full demo video]
*The whole system, start to finish, in one take.*

---

# 5 Building the hardware

## 5.1 Bill of materials

<!-- Flat bullets, "1 x [linked part]" style, matching the Fire Pot format.
     Purchase links where they exist. Prices marked "as of <date>". -->

## 5.1.1 The base station

## 5.1.2 A satellite node

## 5.1.3 The motor test rig

## 5.1.4 Printing, wiring and hardware

## 5.2 Printing the enclosures

<!-- The 16 .3mf plates in 3d-models/. Which to print, how many, what
     colour, what each one is. Show dimensions. VERIFY plate names and
     sizes by opening the model directory. -->

[IMAGE: printed enclosure parts laid out before assembly]
*Every printed part, before anything goes in it.*

## 5.3 The dome from a dead light bulb

<!-- Short, and the most charming thing in the build. A 9W LED bulb's
     diffuser, salvaged, becomes the status dome. How to crack one open
     without breaking it, and the magnet mount underneath. ~140 words. -->

[IMAGE: a dead bulb, its dome removed, and the dome fitted to a node]
*The diffuser is a dead bulb. It was already the right shape.*

## 5.4 Wiring the base station

<!-- Pin table + the KiCad schematic. Pins by the board's own header labels
     (D3, D13, A4, SCL) — never STM32 port names. VERIFY against firmware. -->

[IMAGE: 02b-base-station-schematic-kicad.png]
*Base station wiring. Everything hangs off the real-time half of the board.*

## 5.5 Wiring a satellite node

[IMAGE: 03b-satellite-node-schematic-kicad.png]
*Same three peripherals on a XIAO ESP32-S3, with its own pin map.*

## 5.6 Building the motor test rig

<!-- Optional, and say so up front — you only need it to reproduce the trip
     and the measurements. Uno + CNC shield + three drivers + three NEMA-17s.
     Set each driver's current limit BEFORE power. Flywheel bolts are how
     unbalance is induced. -->

[IMAGE: 06-motor-driver-rig-schematic-kicad.png]
*One driver per motor, a shared enable line, and a current limit to set before anything spins.*

## 5.7 Inducing faults on purpose

<!-- How the four classes are physically produced: bolts REMOVED from the
     flywheel (unbalanced), mounting screws slackened (loose
     mount), a good bearing swapped for a worn one (bearing fault). This
     is what makes the 100% number mean something. ~150 words. -->

[IMAGE: the flywheel with bolts, and the worn bearing beside a new one]
*Faults are induced by hand, on hardware that is actually spinning.*

---

# 6 Installing the software

## 6.1 Try it first, with no hardware at all

<!-- DELIBERATELY FIRST — it converts readers who have not bought anything.
     start_desktop_dashboard.sh, port 8180, real pipeline, real captures,
     ten minutes, needs only mosquitto. VERIFY the script, port and flags. -->

```sh
sudo apt-get install -y mosquitto mosquitto-clients
sudo systemctl enable --now mosquitto
cd base-station
./start_desktop_dashboard.sh
```

## 6.2 Getting the code

## 6.3 Flashing the real-time side with App Lab

<!-- Open base-station/sketch/ in App Lab, flash the STM32U585. Note that
     this is the same tool that will deploy the Linux half — one project,
     both chips. -->

## 6.4 Deploying the Linux side

<!-- ./provision-spi.sh, ./provision-baud.sh, ./provision-wifi.sh once per
     board, then ./start_dashboard.sh. Call out the baud trap: a mismatch
     breaks the link silently with no error anywhere. VERIFY every script
     name and flag. -->

## 6.5 The one secret, and where App Lab keeps it

<!-- The arduino:telegram_bot brick holds TELEGRAM_BOT_TOKEN. Typed into
     App Lab's own interface, never committed, never printed. With it unset
     every alert path no-ops cleanly instead of crashing. This is the
     honest Bricks story and it is a genuinely good one. ~140 words. -->

## 6.6 Flashing a satellite node

```sh
cd satellite
pio run
pio run -t upload
```

---

# 7 Calibration and first run

## 7.1 The step that carries all the weight: machine off

<!-- Step 2 of setup. The software cannot verify the machine is off, so it
     asks and means it. A baseline captured while the machine runs teaches
     the system that its own vibration is silence, and the running/stopped
     gate never works again until you re-measure. ~130 words. -->

## 7.2 Training on the board

<!-- Collect a running batch, press Train, wait seconds. What "seconds"
     actually was on the real board — VERIFY. What thresholds come out. -->

## 7.3 Proving which motor is yours

<!-- Do not ask the operator which motor; make them prove it. Press Test,
     watch the machine stop. One motor may only be claimed by one asset.
     ~120 words. -->

## 7.4 Checking it works

<!-- Flat checklist of observable outcomes, in order. This is the section a
     builder holds their phone against while standing at the rig. -->

---

# 8 How it works

<!-- ARCHITECTURE TRACK. Diagram-led. One diagram per subsection, and the
     prose points at it rather than restating it. This is where length
     goes to die, so keep every subsection under 150 words. -->

## 8.1 Three kinds of board, one brain

[IMAGE: 05-full-architecture.png]
*Every sensing path delivers the identical frame, so the scoring pipeline never learns which kind of node a machine is behind.*

## 8.2 Two chips, two links, on purpose

<!-- LPUART1 for small control messages, SPI at ~40 MHz for bulk spectra,
     so a large diagnostic pull can never stall the live status loop.
     VERIFY both rates. -->

[IMAGE: 14-two-brains.png]
*Small messages on the serial link, bulk data on SPI. A big transfer can never block a status update.*

## 8.3 From a shaking bearing to 536 numbers

<!-- Sample, FFT per axis, pool to 128 bins per channel, six scalars per
     channel. 4 channels x 128 = 512, plus 4 x 6 = 24, total 536. Show the
     arithmetic — a reader should be able to check it. VERIFY in firmware. -->

[IMAGE: 04-feature-pipeline.png]
*Four channels, 128 bins each, six scalars each. 512 + 24 = 536 numbers, every frame.*

## 8.4 The model that learns "normal"

<!-- 536 -> 134 -> 33 -> 134 -> 536, ~153K params, trained per machine on
     that machine's own healthy data. Reconstruction error is the score.
     Two thresholds turn it into a status. VERIFY shape and param count. -->

[IMAGE: autoencoder shape + score-to-status diagram]
*It learns to rebuild "healthy". How badly it fails at rebuilding is the score.*

## 8.5 The model that names the fault

<!-- 536 -> 64 -> 32 -> 4, ~37K params, int8, one per asset class, trained
     in Edge Impulse. Runs in about a millisecond in under 2 KB of RAM.
     It names; it never trips. That separation is the design. VERIFY. -->

[IMAGE: 15d-frame-journey.png]
*Two models, two different jobs, and only one of them is allowed to stop a machine.*

## 8.6 Knowing the machine is simply switched off

<!-- The running/stopped gate. Why it exists: an idle machine must not read
     as a healthy machine. Measured against each machine's own noise floor.
     2.09x margin, up from 1.18x. VERIFY. -->

## 8.7 The trip chain

<!-- Fault confirmed -> countdown, operator can hold -> Telegram -> MQTT
     stop naming the exact motor -> motor stops -> the gate confirms it
     actually went quiet -> TRIPPED. And if it did not go quiet, it says
     the trip FAILED rather than claiming success. That last part is the
     most important sentence in the section. -->

[IMAGE: 15e-trip-path.png]
*It does not trust its own stop command. It listens for the machine going quiet.*

## 8.8 The code, folder by folder

[IMAGE: 15c-linux-packages.png]
*A measurement travels down the left. main.py holds no logic — it just constructs everything in order.*

---

# 9 What we measured

<!-- Short and dense. A results table, then three lines on how faults were
     induced. Every number VERIFIED before it goes in. Candidates:
     100% classifier accuracy on held-out data; healthy 0.046 vs 0.144 and
     0.288; 2.09x running/stopped margin; +38.5 sigma per-axis vs +1.8
     combined; ~11 s to stop, 10 of which is the operator's window;
     0.32 s dashboard latency; 1 ms inference in under 2 KB;
     366 tests across 37 modules. RE-RUN pytest for the last one. -->

## 9.1 The three edge cases we pushed it into

<!-- The stop pointed at the wrong motor (it reported failure, and testing
     it found a real bug). 13 nodes at once. A score sitting exactly on the
     threshold, flickering — honest, unfixed, and section 10 says so. -->

---

# 10 Future improvements

<!-- Honest first, aspirational second — the rule is to call out what is
     deliberately cut, not just what would be nice:
     - hysteresis on the fault threshold (the flicker above, known, unfixed)
     - the microphone is currently muted in the model, and why
     - per-condition thresholds (multi-speed costs 5.1x sensitivity today)
     - a relay instead of a software-latched stop
     - temperature as a third sense
     - the GPU is a dead end, and that is a tested finding, not a shortcut -->

---

# 11 Conclusion

<!-- Loop back to 1.1 and say what is different now. The close writes
     itself: the grinder is still in pieces, and the bearing that killed it
     is now the exact thing three rigs are trained to catch. Somebody with
     that grinder and this pod would have been told in week one, and the
     machine would have stopped itself rather than waiting for a hand on
     the housing. ~200 words. -->

---

<!-- ==========================================================================
     NOT PART OF THE ARTICLE — SHOT LIST

     HAVE ALREADY (report/diagrams/):
       01-system-at-a-glance · 04-feature-pipeline · 05-full-architecture
       09-onboarding · 11-edge-impulse-flow · 14-two-brains
       15c-linux-packages · 15d-frame-journey · 15e-trip-path
       15f-setup-steps · 02b/03b/06 KiCad schematics

     NEED TO SHOOT — PHOTOS
       1. The grinder disassembled, three bearings in shot   HAVE  (1.1) ***
       1b. Salvaged 6004 beside its new replacement          HAVE  (1.3)
       2. The three rigs on the bench, pods clipped on             (1.3)
       3. Three domes lit green / amber / red                      (2.1)
       4. Printed parts laid out before assembly                   (5.2)
       5. A dead bulb, its dome off, and the dome on a node        (5.3)
       6. Flywheel bolts (the bearing pair is now used at 1.3)     (5.7)
          The four 6201s (2 salvaged, 2 new) are shot and could go here.
       7. Hero: base station and satellite, cases open, lit        (4.3 + COVER)

     NEED TO CAPTURE — SCREENSHOTS
       8.  Fleet view, 13 assets                                   (2.2)
       9.  Expanded asset card: score, spectrum, waterfall         (2.3)
      10.  Trip banner mid-countdown                               (2.5)
      11.  Telegram thread: fault, trip, recovery                  (2.6)
      12.  Performance tab under 13 assets                         (3.3)

     NEED TO MAKE — NEW DIAGRAMS (2)
      13.  Autoencoder shape + score-to-status                     (8.4)
      14.  (optional) trip-failure path, if 15e does not cover it  (8.7)

     ALREADY HAVE — VIDEO
       The demo video, embedded at 4.3. A trimmed trip clip at 2.5
       would be better than a still if one can be cut from it.
     ========================================================================== -->
