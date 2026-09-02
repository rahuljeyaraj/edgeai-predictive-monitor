# Hackster.io article — plan

Status: **plan agreed, no prose written.** Next session writes sections 1 onward
into `hackster/ARTICLE.md`.

Source of truth for all technical content: `report/REPORT.md` (3,385 lines).
Every section below carries the report chapter it draws from.

Deadline: **13 Sep 2026, 23:59 PDT** (Hackster, "Invent the Future with Arduino
UNO Q and App Lab", Industrial IoT category).

---

## 1. How this gets written

Draft in `hackster/ARTICLE.md`. Hackster's editor has **no markdown**: it is a
toolbar you click after selecting text. The Hackster session at the end is pure
paste-and-click, never drafting.

Put this legend at the top of `ARTICLE.md`, marked as not part of the article:

```
line starting with #     ->  the H button (only one heading level exists)
**bold**                 ->  the B button
*italic*                 ->  the i button
`backticks`              ->  the # button   (inline code)
``` fenced block         ->  the </> button (block code)
line starting with >     ->  the quote button
line starting with -     ->  the bullet button (one level, never nested)
[text](url)              ->  the link button
[IMAGE: ...]             ->  the image embed
[VIDEO: ...]             ->  the video embed
```

### Two hard editor limits, solved in the source

- **Only one heading level.** Number every heading (1, 1.1) and press the same H
  button at every depth. The number carries the hierarchy.
- **Bullets cannot nest.** Never write a nested bullet. Restructure hierarchy
  into flat bullets or into a numbered sub-heading.

### Media placeholders

`[IMAGE: short description]` or `[VIDEO: short description]`, followed by one
italic caption line. At transcription time the marker is the cue to upload and
embed, and the caption gets pasted and italicised.

---

## 2. Prose rules

- **No em dashes.** Rahul flagged them in the first draft. Use a comma, a full
  stop or a colon.
- No stock openers ("what if your machine could...").
- First person, narrative, plain descriptive headings.
- No subsection over ~150 words. Target ~6,500 words total.
- House-style reference: Rahul's own
  [The Fire Pot](https://www.hackster.io/rahul-jeyaraj/the-fire-pot-reimagining-hotpot-7096d5).
  Hackster 403s WebFetch, use plain `curl` with a desktop browser User-Agent.
- **Headings name the subject, never the story beat.** No "Month 7", no "The day
  it stopped itself". The story lives *inside* the section: one to three
  sentences of Ravi's shop at the top, then straight into the technical content.
- **BOM split.** Hackster's "Things used" widget gets only the headline hardware
  plus all software and tools. The full parts list with Robu.in links and prices
  lives in the article body. Hackster's rubric is dominated by documentation
  (30 pts) and BOM (20 pts), judged as "could a beginner recreate this?".

---

## 3. The story, and the machine cast

The report's narrative is **Ravi's year** running a small workshop. Keep it.

The report's original cast was wrong for the premise and is corrected here. The
whole system only earns its keep on machines that run with **nobody standing
there**. A CNC lathe has an operator at it; if it screams, someone hears it.

| Machine | Runs unattended? | Role |
|---|---|---|
| CNC lathe | No, an operator is at it | Stays in the story, **not monitored**. The counter-example |
| Air compressor | Yes, cycles on a pressure switch, lives outside | **Machine 1.** Belt driven, feeds every air tool |
| Borewell / tank pump | Yes, night timer or float switch | **The 02:40 machine.** Direct drive |
| Exhaust / dust blower | Yes, all shift, on the roof | Fleet machine, out of cable reach |
| Coolant pump | Yes, whenever the lathe runs | Fleet machine |
| Conveyor motor | Yes, nobody at the far end | Fleet machine, and already an asset class in the code |
| Drill press | No, hand fed | **Cut.** REPORT.md has it at month 7; it fails the test |

This is grounded in the code, not invented: the repo's own asset names are
`pump002`, `conveyor001`, `conveyor_motor`, `compressor a`. No lathe anywhere.

It also fixes the bench-rig mapping: the two direct-drive stepper rigs stand in
for pumps, the belt-driven one for the compressor, matching the wet grinder's
motor and its belt-driven drum.

**The grinder is not the hook.** It appears once, in section 8.3, as the answer
to "where do you get real fault data?". You cannot buy a broken machine, so the
labelled faults came from the family's dead Ultra wet grinder: repaired
repeatedly, then abandoned, taken apart until an electrician pulled three rusted
ball bearings out of it, two from the motor and one from the drum, and you could
feel the cracks by turning one in your hand.

**Licence to invent:** atmosphere only. Every technical fact, number and
measurement stays literally true to `report/REPORT.md`.

**Divergence to carry back:** REPORT.md still uses the lathe and drill press in
its month timeline and its asset-class examples (`cnc lathe`). Once the article
is settled, the report should be brought into line so the two agree.

---

## 4. Section skeleton

Story beats in *italics* are drafting notes, not headings.

### 1. What this is, and what it decides
Source: REPORT.md Ch 1.
- 1.1 The problem: the machines nobody stands next to
  *The lathe cost nine lakhs and someone stands at it all day. The compressor in
  the corner cost nine thousand, runs on a pressure switch, and when it seized
  the whole shop stopped for two weeks.*
- 1.2 What the monitor does (the watch/reduce/learn/notice/diagnose/act list)
- 1.3 Acting, not just alerting (why this is Physical AI, the closed loop)
- 1.4 What it is not: a monitor, not a safety interlock (pull Ch 8.4 forward)
- 1.5 What is built and what isn't (the status table from 1.3)

### 2. Trying it without hardware
Source: Appendix C.1.
- 2.1 What the desktop path actually runs (real app, real wire protocol, real
  replayed captures, not a mock)
- 2.2 Installing and starting it (mosquitto, `./start_desktop_dashboard.sh`)
- 2.3 What to look at first, and what you will not see (`base_station` needs SPI)

### 3. The board: two processors on one card
Source: Ch 2.
- 3.1 The four jobs that don't normally share a board
- 3.2 Training on the device (the part that would be hardest to replace)
- 3.3 What Arduino App Lab handles (one deploy, brick secrets, both halves)
- 3.4 Three limits we found by pushing (12.8 kHz not 25.6, 500000 baud, the GPU)

### 4. Building the sensor pod
Source: Ch 3 + Appendix A.1 + Appendix B.1 + Appendix C.3.
- 4.1 Bill of materials (base station, ~₹8,115, with Robu links)
- 4.2 Choosing the accelerometer (Appendix D, condensed to bandwidth, FIFO, cost)
- 4.3 Wiring (the B.1 pin table, UNO Q header labels)
- 4.4 Mounting, and why it changes what the sensor can see
- 4.5 Flashing the real-time side (App Lab, `base-station/sketch/`)
- 4.6 Provisioning the Linux side (provision-spi / -baud / -wifi)
- 4.7 Deploying, and first light (`./start_dashboard.sh`, use the LAN IP URL)

### 5. The bench rig: something to watch, something to stop
Source: Appendix A.3 + B.3 + C.5.
*Ravi has a compressor and a pump. I have three steppers, two direct drive and
one belted.*
- 5.1 Bill of materials (rig, ~₹4,502)
- 5.2 Wiring, and the shared enable line (why the trip is a per-axis step halt)
- 5.3 Setting the driver current limit (Vref formulas, before power)
- 5.4 Flashing and the control page (`./start_motor_driver.sh`, port 8000)

### 6. Commissioning: teaching it one machine's normal
Source: Ch 5 (+ Ch 8.5 for 6.4).
- 6.1 Why every machine needs its own baseline
- 6.2 Step 1: name and asset class (both mandatory, and why)
- 6.3 Step 2: measuring the machine switched off (the one instruction no
  computer can check)
- 6.4 Why step 2 exists: the sensor's own noise floor
  *The mystery beat. Stopped and running measured 1.18x apart. We had built a
  very sophisticated way of measuring an accelerometer. Per-bin baseline took it
  to 2.09x.*
- 6.5 Step 3: running conditions (named, >=50 frames each, three destinations)
- 6.6 What a second condition costs (the measured 5.1x, stated not buried)
- 6.7 Step 4: training on the board (autoencoder, why unsupervised, seconds)
- 6.8 Step 5: proving which motor stops this machine (the dropdown was a lie;
  send a real stop and watch the gate; unconfirmed is honest)
- 6.9 Step 6: live
- 6.10 From a score to a status (mu+8sigma, mu+15sigma, why no global threshold)

### 7. Adding machines over Wi-Fi
Source: Ch 4 + A.2 + B.2 + C.4 (+ Appendix E for one line on why Wi-Fi).
*The borewell pump behind the shed, the blower on the roof. No cable reach, and
nobody running conduit across a yard for a monitoring system.*
- 7.1 Bill of materials (satellite, ~₹2,245)
- 7.2 Wiring (B.2 pin table, 11 GPIOs, identity from the Wi-Fi MAC)
- 7.3 Building and flashing (`pio run -t upload`)
- 7.4 Onboarding from a phone (the five steps, the captive portal, the two
  real-hardware fixes: tappable buttons not `<datalist>`, warning above the
  button)
- 7.5 One frame format for every node, and the three things it buys

### 8. Naming the fault
Source: Ch 6 + Ch 7 (+ Appendix K for the fixtures).
- 8.1 Why a second model (healthy/warning/fault says whether, not what)
- 8.2 One model per machine type, not per machine
- 8.3 Making real faults: the grinder's bearings and printed fixtures
  *You cannot buy a broken machine. The grinder story goes here, once.*
- 8.4 Recording labelled captures (server side; real labels are `healthy`,
  `bearing`, `unbalanced`, `loose`, plus `idle`)
- 8.5 Linking a class to Edge Impulse (three REST calls, the 0600 key)
- 8.6 Uploading, and four ways to get it wrong (scalar tail standardised,
  baseline pooled per class, fitted on train split only, contiguous split)
- 8.7 Training in Studio (and why the Train button was deliberately removed)
- 8.8 Fetching the model (background job, streams stages, survives refresh)
- 8.9 Running it on the CPU (no NPU on this part, GPU is ~1.0x, vendor wheels
  are an illegal instruction; staying on CPU is a finding)

### 9. The trip: stopping a motor
Source: Ch 8.
*02:40. The tank pump is on its night timer. Nobody is in the building.*
- 9.1 The trip chain (five steps, and the real console capture)
- 9.2 The ten-second countdown (longer than an industrial relay, on purpose)
- 9.3 Latching (refuses every later speed command until a human clears it)
- 9.4 Refusing to claim a trip that failed (the most dangerous lie available)
- 9.5 Idle versus tripped (`target = TRIPPED if was_ours else IDLE`)

### 10. The operator's view
Source: Ch 9.
- 10.1 The trip banner (above the tab bar, four states, what can be dismissed)
- 10.2 Fleet, and the ten statuses (tiles that are filters, the expanded row)
- 10.3 Classifier, Network, Performance, Alerts (one question each, brief)
- 10.4 The status ring on the machine (the colour table, why not the screen
  palette, the LED matrix summary)
- 10.5 Phone alerts (built and demonstrated, off pending one config value)

### 11. How it works inside
Source: Ch 10 (+ the best of Ch 11).
- 11.1 Three boards, one brain (only one of them thinks)
- 11.2 One frame's journey (acquire, reduce, arrive, route, score, fan out)
- 11.3 Two links between the processors, and why they are separate
- 11.4 The registry is the only thing that fans out

### 12. Measured results
Source: Ch 12.
- 12.1 The running/stopped gate (the per-bin table, 1.18x vs 2.09x)
- 12.2 A full setup run (65 frames, 1533.1, 0.046 vs 0.144 / 0.288)
- 12.3 The trip, both directions (including the flapping countdown, stated)
- 12.4 Per-axis versus fused features (+38.5 sigma vs +1.8 sigma)
- 12.5 Known limitations (shared sensor on the rig, multi-condition cost,
  hysteresis, classifier is not the safety path, motion not power, satellite
  hardware not yet run)

### 13. What's next
Source: Ch 13 + Closing. Roadmap in build order, then a close that returns to
the compressor from 1.1.

---

## 5. Media

### Already in `hackster/assets/`
- `IMG20260901093223.jpg` — the wet grinder fully disassembled on the floor,
  belt pulley, stator, rotor, drum shaft, and three bearings laid out. **Use in
  8.3.**
- `IMG20260901093820.jpg` — one rusted 6004-2RS beside one new one. The single
  clearest "this is what a dying bearing looks like" shot. **Use in 8.3.**
- `IMG20260901093909.jpg` — four 6201 bearings, two worn, two new. **Use in 8.3
  or as the fault-class illustration.**

### Reusable, already generated, in `report/diagrams/`
- `01-system-at-a-glance.png` -> 1.2
- `14-two-brains.png` -> 3.1
- `02-base-station-wiring.png` + `02b-base-station-schematic-kicad.png` -> 4.3
- `06-motor-driver-rig-schematic-kicad.png` -> 5.2
- `10-setup-flow.png` -> 6.1
- `04-feature-pipeline.png` -> 6.7
- `03-satellite-node-wiring.png` + `03b-...-kicad.png` -> 7.2
- `09-onboarding.png` -> 7.4
- `11-edge-impulse-flow.png` -> 8.1
- `07-trip-sequence.png` -> 9.1
- `06-asset-lifecycle.png` -> 10.2
- `08-dashboard-anatomy.png` -> 10.2
- `13-dashboard-tabs.png` -> 10.3
- `05-full-architecture.png` -> 11.1
- `12-software-architecture.png` -> 11.1

### Still needed, and not in the repo
Photos: base station wired on the bench · the pod clipped to the rig, running ·
a satellite node powered on a second machine · the motor rig with its three
steppers, labelled · the status ring in several colour states and the LED matrix
mid-scroll · the printed fault fixtures, if 8.3 is to show them.

Screenshots: the setup drawer on step 3 with two conditions collecting · an
expanded asset row (anomaly chart, classifier bars, spectra) · the trip banner
mid-countdown with Hold · the Classifier tab with two class cards · a real
Telegram fault alert.

Video: one real trip on the rig, motor stopping and the ring changing.

---

## 6. Open questions for Rahul

1. Section 10.5: state plainly that Telegram is built and demonstrated but
   switched off pending one config value, or drop the subsection?
2. Section 8.3: are there printed fault fixtures to photograph, or does the
   grinder-bearing set carry that subsection alone?
3. The `[FILL IN]` fields the report still carries: GitHub URL, demo video URL,
   submission date, UNO Q purchase price and receipt reference.

---

## 7. Next session

1. Create `hackster/ARTICLE.md` with the legend, then write sections 1 to 5.
2. Verify every number against `report/REPORT.md` or the code as you write it.
   Do not restate a figure from memory.
3. Keep each subsection under ~150 words. Keep the story inside the sections,
   never in a heading.
