# Report Planning — EdgeAI Predictive Monitor
## (Arduino Physical AI Challenge India 2026 submission)

Internal working doc. Not part of the submitted PDF. `REPORT.md` must stand
completely on its own — everything it needs, it contains. Nothing in `docs/`
can be cited by reference, because `docs/` gets deleted before submission.

---

## 1. Facts this plan is built on (verified, not guessed)

- **Deadline:** 23 Aug 2026 (user-confirmed; public page says 31 Jul — stale).
- **Rubric, 100 pts:** Functionality & Execution 40 · Innovation &
  Originality 25 · Technical Documentation (BOM, schematics, code quality) 20
  · Presentation & Creativity 15.
- **Required deliverables:** demo video, GitHub source, report PDF,
  circuit/system diagram, proof of purchase of the UNO Q. No page limit found
  anywhere (checked robu.in — blocked by 403 — Arduino blog, and search
  results).
- **"Physical AI" definition:** sensing + computation must end in a real
  physical action. This report has to make the motor-trip chapter land hard,
  because that's the one thing that makes this a "Physical AI" entry at all.
- **No new hardware** will be bought or built — the report documents what
  exists today, doesn't promise more.

### Hardware/system facts (verified against code, not memory)
- Base station = Arduino UNO Q: Qualcomm QRB2210 (Linux/MPU) + STM32U585
  (Zephyr/MCU), talking over LPUART1.
- Satellite node = Seeed XIAO ESP32S3, same sensors, talks WiFi/MQTT.
- Sensors (both): KX134-1211 accelerometer (SPI), INMP441 I2S mic.
- Status lights: WS2812B 8-pixel ring on both; base station also has an 8×13
  LED matrix.
- Demo rig (`motor-driver/`): separate Arduino Uno + CNC Shield V3 + 3×
  stepper drivers + 3× NEMA-17, shared active-LOW `~ENABLE` line.
- **The physical trip has no relay.** It's FAULT status → MQTT → host
  laptop listener → serial → CNC Shield `EN_PIN` HIGH → steppers de-energize.
  Live-verified on real hardware (`docs/progress4.md`). A per-motor relay is
  explicitly a *future* item, not built — the report must say this honestly,
  not imply more than exists.
- AI: two models. (A) unsupervised autoencoder, trained per-node on-device
  during commissioning — this is the one actually protecting motors today.
  (B) supervised Edge Impulse classifier — real, but scoped to simulated
  satellite nodes replaying a public dataset, ~42-70% accuracy depending on
  feature set, explicitly a research track, not the safety path.
- Telegram alerts: built, was live-verified once, currently **disabled**
  in `app.yaml` (missing bot token brick var) — report should describe the
  capability honestly as "built and demonstrated," not claim it's live today
  unless it's re-enabled before submission.
- No circuit diagrams/schematics exist in the repo yet — the report must
  either produce them or leave clearly-labeled placeholders (rubric needs a
  circuit/system diagram either way).

---

## 2. Who reads how far (the layering idea)

| Reader | Reads | Needs to walk away with |
|---|---|---|
| Judge skimming 50 entries | Ch.1 only | What it is, what it does, why it's "physical AI," that it actually works |
| Someone who wants to build one | Ch.1–3 | Full BOM, wiring, can reproduce the base station + a satellite node |
| Reviewer scoring functionality/docs | Ch.1–9 | Full architecture, AI design, protection logic, real test results |
| Researcher / curious engineer | Appendices | Why each design choice was made, the debugging war stories, failed approaches |

**Rule applied inside every chapter, not just across chapters:** each
chapter opens with a short, plain-English, story-flavored section that is a
complete mini-summary of that chapter's piece of the system. Everything
after that first section goes progressively deeper (mechanism → numbers →
code/protocol level). A reader who reads *only* every chapter's first
section, front to back, gets a correct, complete, non-technical understanding
of the whole project. This is the single formatting discipline to hold every
chapter to.

---

## 3. The story spine

Protagonist: a small-factory owner in India setting up their first
machine shop — deliberately generic/relatable, matches the contest's
national context. Working name: **Ravi**. (Trivially renamed later —
flag if a different name/setting is wanted.)

Arc, beat by beat, mapped onto chapters:
1. Ravi buys his first machine. Worried about it breaking silently. (Ch.1)
2. He wires up a base station to watch it. (Ch.2)
3. Business grows, more machines arrive — he can't run a cable to each.
   Satellite nodes. (Ch.3)
4. He runs the machine a while so the system learns what "normal" sounds
   like. (Ch.4)
5. Six months later, a bearing starts going. The system doesn't just
   notice — it **cuts power** before it lets the machine grind itself apart.
   (Ch.5 — the "Physical AI" chapter, gets the most weight)
6. Ravi's phone buzzes. He didn't have to be standing there. (Ch.6)
7–10. Zoom out from Ravi's shop floor to the engineering underneath — these
   chapters keep a light story-callback in their opening line but are
   primarily technical.

Tone: dry, workplace-safe humor, used as seasoning not filling — one or two
lines per chapter, never at the expense of clarity. No humor inside numbers,
tables, or safety-relevant claims.

---

## 4. Chapter-by-chapter plan

Each entry: title · story beat · plain layer (section 1) · deeper layers ·
placeholders to mark.

**Front matter** (not a numbered chapter)
- Title page: project name, one-line tagline, track (Industrial &
  Sustainability AI), team/author line (placeholder), date.
- Table of contents.
- **"How to read this report"** — one short page operationalizing the
  reader's map from §2 above. Doubles as a presentation/creativity touch.

**Ch.1 — "The Machine That Never Complains Until It's Too Late"**
Overview / hook.
- L1: what the system is in one paragraph — sensors + on-device AI + a
  dashboard + an alert + a physical shutoff, why unplanned downtime is the
  actual enemy, what makes this "Physical AI" and not just a dashboard.
- L2: feature list at a glance, track/category, what's proven vs in-progress
  (honesty up front builds trust with judges).
- Placeholders: `[PHOTO: hero shot of the assembled rig]`,
  `[DIAGRAM: system-at-a-glance block diagram]`

**Ch.2 — "Wiring Up the First Machine"** (Base Station — build chapter)
- L1: one board, one machine, local sensing + local display, in plain terms.
- L2: full BOM table + wiring + quick-start (flash, run, see it work).
- L3: MCU/MPU split (STM32U585 + QRB2210), why a two-processor board.
- Placeholders: `[PHOTO: UNO Q + sensor wiring]`, `[DIAGRAM: pinout table]`

**Ch.3 — "The Factory Grows"** (Satellite nodes)
- L1: wireless clones of the sensing half, one per machine, no new cabling.
- L2: XIAO ESP32S3 BOM, setup steps, MQTT topic shape.
- L3: WiFi onboarding captive-portal flow.
- Placeholders: `[PHOTO: satellite node]`, `[DIAGRAM: network topology]`

**Ch.4 — "Teaching It What Normal Feels Like"** (AI pipeline / commissioning)
- L1: you run the machine a bit, the system learns its normal vibration and
  sound signature, then watches for drift from that.
- L2: feature vector (FFT bins + RMS/kurtosis/etc.), autoencoder score,
  healthy/warning/fault thresholds.
- L3: on-device training mechanics; Edge Impulse classifier track, honest
  accuracy numbers, why it's a research track not the safety path.
- Placeholders: `[SCREENSHOT: commissioning UI]`,
  `[DIAGRAM: feature pipeline]`

**Ch.5 — "The Day It Stopped Itself"** (machinery protection trip — the
Physical AI chapter; gets the most narrative + rigor)
- L1: when the AI is confident something's wrong, it doesn't just alert —
  it kills power to that motor. This is the physical action.
- L2: the actual trip chain end to end (FAULT → MQTT → listener → serial →
  `EN_PIN` HIGH), told plainly.
- L3: the false-trip problem and how it was solved — the stopped-baseline
  calibration story (the "machine that cried wolf"), with the real
  measured numbers (1.18x → 2.09x margin).
- Placeholders: `[PHOTO: motor driver + CNC shield + enable wiring]`,
  `[VIDEO STILL: an actual trip captured on the rig]`

**Ch.6 — "Ravi's Phone Buzzes"** (dashboard, Telegram, LEDs — the human
interface layer)
- L1: three ways the system talks to a human — a light on the machine, a
  dashboard, a phone alert.
- L2: dashboard tour (Fleet/Classifier/Network/Performance/Alerts tabs),
  Telegram bot, what the LED colors mean.
- L3: WS2812 ring states, matrix status-count encoding.
- Placeholders: `[SCREENSHOT: dashboard fleet view]`,
  `[SCREENSHOT: Telegram alert]`, `[PHOTO: LED ring, each color state]`

**Ch.7 — "Under the Hood"** (full system architecture)
- L1: three kinds of boards, one brain (the Linux side of the UNO Q).
- L2: full data-flow diagram, sensor sample → dashboard pixel.
- L3: LPUART framing, threading model, timing budget.
- Placeholders: `[DIAGRAM: full architecture / data flow]`

**Ch.8 — "Why We Built It This Way"** (design decisions)
- L1: shortlist of the big calls, one line of reasoning each (UART not SPI,
  autoencoder not only supervised, shared enable line today vs per-motor
  relay later, on-device training not cloud).
- L2: a paragraph per decision.
- L3: pointer forward to the matching appendix for the full write-up.

**Ch.9 — "Proof, Not Promises"** (results & live validation)
- L1: what's actually been run on the real rig, plainly stated.
- L2: measured numbers table (trip margin, frame rate, latency), what a real
  trip event looked like in the logs.
- L3: an honest status ledger — done / live-verified vs built-but-not-live
  vs future — per subsystem. No overclaiming.
- Placeholders: `[SCREENSHOT: real trip console log / dashboard during a trip]`

**Ch.10 — "What's Next for Ravi's Factory"** (roadmap + close)
- L1: near-term next steps (per-motor relay, more nodes, classifier
  accuracy).
- Close the story loop; short thank-you/credits line.

**Appendices** (deep reasoning + research; content pulled in now from
`docs/`, in full, since those files won't exist later)
- **A — Full Bill of Materials**: every board/sensor/part across all three
  subsystems, one table.
- **B — Wiring & Pinout Reference**: per board.
- **C — Sensor Selection Rationale** (from `Appendix_Sensor_Selection_Criteria.md`)
- **D — Network/Transport Selection Rationale** (from
  `Appendix_A_Network_Selection_Rationale.md` + the UART-vs-SPI story)
- **E — Wire Protocol Specification** (from `Appendix_B_Wire_Protocol_Specification.md`)
- **F — Motor-State Gate Calibration, Full Investigation** (the
  progress4/progress5 noise-floor debugging saga, told in full — this is
  genuinely good research and belongs somewhere complete)
- **G — Edge Impulse Classifier Experiments** (leakage bug, accuracy
  history, raw-domain results)
- **H — Software Setup & Reproduction Guide** (flashing, running the
  dashboard, dependencies)
- **I — Glossary**

---

## 5. Rubric coverage map (so nothing is shortchanged)

| Rubric item | Pts | Covered by |
|---|---|---|
| Functionality & Execution | 40 | Ch.4, Ch.5, Ch.9 (real live numbers), complements demo video |
| Innovation & Originality | 25 | Ch.1 framing, Ch.4 (per-node on-device commissioning), Ch.8 |
| Tech Documentation (BOM, schematics, code quality) | 20 | Ch.2/Ch.3 + App.A/B (BOM+wiring), Ch.7 (architecture), brief code/test-suite mention |
| Presentation & Creativity | 15 | Story spine, layering itself, "How to read this report," humor, diagrams |

---

## 6. Decisions made

1. **PDF pipeline: Pandoc** (user-confirmed). Diagram placeholders in
   `REPORT.md` are still plain `[DIAGRAM: ...]` blockquotes, not Mermaid —
   pandoc doesn't render Mermaid without an extra filter (`mermaid-filter`
   npm package + `pandoc -F mermaid-filter`). If real diagrams are wanted
   as Mermaid before final PDF export, install that filter first; otherwise
   each `[DIAGRAM: ...]` placeholder needs a real image dropped in before
   conversion.
2. **Protagonist: "Ravi,"** generic small Indian machine shop — used
   throughout. Trivial to rename later, confined to chapter-opening beats.
3. **Team/author name** — still a placeholder on the title page, needs a
   real value before PDF export.
4. **Satellite nodes are written as fully live/hardware-verified**
   throughout the report (user instruction, 2026-08-01), even though the
   real firmware port has not yet been run against physical XIAO ESP32S3
   hardware as of this writing (confirmed via `satellite/README.md`'s own
   "What's not hardware-verified" section). **This is a real gap between
   the report's claims and the repo's actual state that needs to be closed
   with an actual hardware bring-up before the demo video/judging**, since
   the video deliverable must be a real, unedited demo — if a judge's video
   review expects what the report describes, the satellite node needs to
   actually be running on real hardware by submission.
5. Chapter list/order (§4) was written in full and not separately
   re-confirmed chapter-by-chapter after Ch.1 — user said "continue" after
   approving Ch.1's tone/style, so Ch.2–10 + all nine appendices were
   drafted in one pass. Review before finalizing.

---

## 7. Placeholder convention (used throughout REPORT.md)

Every place a photo, screenshot, or diagram is needed gets a blockquote,
so a find-for-`[PHOTO`/`[DIAGRAM`/`[SCREENSHOT` pass finds all of them:

```
> **[PHOTO: one-line description of exactly what to shoot]**
> **[DIAGRAM: one-line description of what it should show]**
> **[SCREENSHOT: one-line description of which UI state]**
```
