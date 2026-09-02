# Judge review: ARTICLE.md against the contest gates

Written 2026-09-02. A pass over `hackster/ARTICLE.md` from the position of a
Hackster judge screening ~93 entries down to 6 prizes, using the live rubric on
the contest `/rules` page.

**Status, updated 2026-09-02.** This was written as a findings document. Every
finding that did not need a camera has since been fixed in `ARTICLE.md` and
`PLAN.md`. What each section says still stands as the reasoning; the table in
section 1 now carries a status column. **Everything still open is media.**

Companion: the screening plan this was scored against is at
https://claude.ai/code/artifact/5be21add-0ef4-4a00-bd1e-9769e1a206d4

---

## 0. The headline

The writing is not the problem. The article would survive any judge's read on
prose, structure and technical depth. Two things put it at risk:

1. **There is no cover image.** It is a listed submission requirement. A judge
   screening on a binary checklist does not read the article to find out whether
   it deserved an exception.
2. **Not one photograph in this article shows the monitor.** Twenty-nine image
   markers: seventeen are drawings, three are a disassembled grinder, and nine
   do not exist yet. Every photograph that would prove the system is real is in
   the missing nine.

**So: PLAN.md section 7 has the next session doing another length pass. That is
the wrong next action.** Length is not a scored criterion. Images are named
inside the 30-point Documentation criterion, and their absence is what a
2-minute screen actually cuts on. Shoot the media first.

**PLAN.md section 7 has since been reordered to match**: cover image, photos and
screenshots, video, the Things-used widget, and length last and only if the rest
is done.

---

## 1. Priority order for the next session

Ranked by points at stake per hour of work.

| # | Action | Gate | Effort | Status |
|---|---|---|---|---|
| 1 | Produce a cover image | 0 | 1 hr | **OPEN, media.** The `[COVER IMAGE:]` marker is now in place at article 1.2, with a transcription note in the header legend. The shot itself is outstanding |
| 2 | Shoot the 4 missing photos | 1 | 2 hr | **OPEN, media** |
| 3 | Shoot the 4 missing screenshots | 1 | 1 hr | **OPEN, media** |
| 4 | Record + upload the trip video | 1 | 2 hr | **OPEN, media.** Requirements are restated in PLAN.md section 5 |
| 5 | Add sustainability, name the scalability | 3 | 30 min | **DONE.** New section 13.2, Closing renumbered to 13.3; scalability named at the end of 7.1 |
| 6 | State the classifier result, or why there isn't one | 1 | 15 min | **DONE**, option 2. New section 8.8, cross-linked from 1.5 and 12.4. No stale number quoted |
| 7 | Fill Hackster's "Things used" widget | 2 | 20 min | **PREPARED.** Ready-to-paste list is PLAN.md section 8. Still has to be typed into Hackster on submission night |
| 8 | Length pass | — | — | **Deferred, correctly.** Not scored. PLAN.md section 7 is reordered so it is last |

Two things this review missed, found while fixing the above and now also done:

- **The repository's 3D-printed parts appeared nowhere in the article.** `3d-models/` holds the base station and satellite housings plus the bracket, shaft, flywheel and ring sets for both rig types. They are now in 4.1, 4.5, 5.1 and 7.1. This matters twice: a builder cannot reproduce the rig without them, and it is what makes the CAD resource files visible to a judge who is not browsing the repo.
- **The microphone pin names disagreed three ways.** Article 4.4 used the STM32 peripheral name `SAI1`, article 7.2 used `BCLK` / `LRCLK`, and both KiCad schematics label the part `SCK` / `WS` / `SD`. All three now use the INMP441's own datasheet names, which is also what the project's pin-naming convention asks for.

---

## 2. Gate 0, binary disqualifiers

Run at 30 seconds per entry. Any single miss and the entry is out.

- **Uses the UNO Q, and the UNO Q matters** — PASS, and this is the entry's
  strongest single answer. Section 3.1's four jobs, 3.2's App Lab deploy of both
  halves, and on-device PyTorch training in 6.9 mean the ESP32 test fails
  loudly: this does not run on a cheaper board. Most entries cannot say that.
- **Fits a theme, powered by AI** — PASS. Industrial IoT, with a per-machine
  autoencoder and an Edge Impulse classifier. Not a threshold dressed up as AI.
- **English, original, not a prior winner** — PASS.
- **Resource files** — PASS. Repo is public (HTTP 200), MIT `LICENSE` present,
  `README.md` at 117 lines with a repository map, working tree in sync with
  `origin/main`. `3d-models/` covers the CAD field.
- **Cover image** — **FAIL at the time of writing; now half fixed.** There was no
  marker for one, nothing suitable in `hackster/assets/`, and no note in PLAN.md
  that one was owed. A `[COVER IMAGE:]` marker now sits at article 1.2, the
  header legend says to upload it as the cover *and* leave it inline, and PLAN.md
  section 5 lists it first. **The photograph itself is still outstanding.**

### Action 1: the cover image

It is the only thing every judge sees, and for most entries it is all they see.
It must be a photograph, not a diagram, and it must show the product rather than
the workbench.

Best candidate: **the sensor pod clipped to the running rig, status ring lit,
shot close and shallow.** That single frame carries the whole thesis. Second
choice: the base station and a satellite node side by side, both powered.

Note that PLAN.md section 5 already lists "the pod clipped to the rig, running"
as a needed photo, but **no `[IMAGE:]` marker in the article calls for it.** It
fell out between the plan and the draft. It is the cover shot.

**Reinstated 2026-09-02** as `[COVER IMAGE: ...]` at article 1.2, a distinct
marker type so transcription cannot confuse it with an inline figure.

---

## 3. Gate 1, did this thing physically exist?

The 2-minute screen, and the highest-yield filter a judge has. The article is
split: what is written is excellent evidence, what is shown is not.

### Already passing, and worth protecting in any future trim

These are the "advance on" signals, and the draft has more of them than most
entries ever will. Do not let a length pass touch them:

- **Admitted scars.** 6.5 and 6.6, the noise-floor mystery where stopped and
  running measured 1.18x apart. 6.10, "the dropdown was a lie". 9.4, refusing to
  claim a trip that failed. 12.4's limitations. A flawless narrative is the
  classic paper-project tell, and this draft is not that.
- **Numbers with conditions.** 12.2 gives 65 frames, energy reference 1,533.1,
  spread 1.39x, threshold 2,682.9. That is what a real measurement looks like.
- **An explicit not-built list.** Section 1.5, including the relay item and the
  open satellite captive-portal bug. Judges trust entries that do this.
- **The grinder photographs.** `IMG20260901093820.jpg`, one rusted 6004-2RS
  beside one new one, is the single most convincing image in the entry. Nothing
  fakes a real worn bearing.

### The problem: nine missing media items

Twenty-nine `[IMAGE:]` and two `[VIDEO:]` markers in the file. Resolution:

- **17** point at `report/diagrams/` — all present and all drawings
- **3** point at real photographs in `hackster/assets/` — all three are the
  grinder, at section 8.3, roughly two thirds of the way down
- **9** point at nothing that exists

The nine, with line numbers in ARTICLE.md:

**Photos (4)**
- 305 — base station wired on the bench
- 370 — the motor rig with its three steppers, labelled
- 735 — a satellite node powered on a second machine
- 1090 — the status ring in several colour states, LED matrix mid-scroll

**Screenshots (4)**
- 165 — desktop dashboard, one simulated node online and expanded
- 998 — the trip banner mid-countdown with Hold
- 1044 — an expanded asset row: anomaly chart, classifier bars, spectra
- 1108 — a real Telegram fault alert

**Video (1)**
- 424 — one real trip on the rig, motor stopping and the ring changing

### Why this is the top risk

Read the article the way a judge does, top down, stopping at two minutes. You
reach section 8 before you see a single photograph of anything that was built,
and the first photograph you do see is a machine in pieces on a floor. Up to
that point the entry is indistinguishable in form from a well-written project
that was never assembled.

The fix is not more words. It is four photographs above section 5.

### Two plan items that never became markers

PLAN.md section 5 asks for six photos. The article only has markers for four.
Missing entirely:

- **the pod clipped to the rig, running** — this is the cover image, see above
- **the printed fault fixtures** — **closed 2026-09-02: they do not exist.**
  `3d-models/` contains housings and rig parts only. Nothing is owed here. What
  the check did turn up is that none of those printed parts were mentioned in
  the article at all, which is now fixed in 4.1, 4.5, 5.1 and 7.1.

### Action 4: the video

The trip video at line 424 is the strongest artefact this project can produce
and it does not exist yet. It shows sensing, deciding and acting in one
unbroken shot, which is the entire claim. Requirements:

- **continuous, uncut** — a cut is where a judge assumes the trick is
- fault induced on camera, then countdown, then the motor physically stopping,
  then the ring changing colour
- the dashboard and the motor both visible, or the sequence shot in one pan
- **upload it and paste the URL into the article.** A `[VIDEO:]` marker with no
  link is worth nothing at judging time

---

## 4. Gate 2, scoring the rubric

Estimates as the article stands today, and after the media work.

### Project Documentation, 30 pts — currently 24 to 26, ceiling 30

The test is "could a beginner recreate this without asking a question?"
Sections 3 to 7 pass it: BOM, wiring, current-limit formulas, flashing,
provisioning, deploy, first light, all in order, all with real commands.
Section 2's no-hardware path is a genuine asset and should survive any trim,
because it lets a judge verify the software claim in ten minutes without
owning the board.

What holds it under 30 is images. The criterion names "images, screenshots,
and/or a video demonstration" in its own text. Eight of the nine missing items
sit inside this criterion.

### Complete BOM, 20 pts — currently 19 to 20

**The strongest part of the entry, and it should stay exactly as it is.**

Section 4.2 lists software and tools as their own section: App Lab, Zephyr,
PlatformIO, PyTorch, Edge Impulse, Mosquitto, Plotly, KiCad, and a multimeter.
The rules ask for "hardware, software and/or tools" and most entries list parts
only, dropping 8 to 10 points for nothing. This entry takes them.

Three BOMs, each with quantities, Robu.in links, prices and a subtotal.

**One transcription-time risk.** Hackster's "Things used" widget is a separate
UI from the article body, and judges read it as the BOM. PLAN.md section 2
already decided the split: headline hardware plus all software and tools go in
the widget, the full priced list stays in the body. Do not skip the widget on
submission night, and make sure every item in 4.2 appears in it. An empty or
hardware-only widget throws away the entry's best criterion.

### Schematics, 15 pts — currently 14 to 15

Two KiCad schematics, two wiring diagrams, pin tables in 4.4 and 7.2. This is
above what the criterion asks for. Two checks before submission:

- **Pin names, checked and fixed.** The article now has no `PBn`, `PAn`, `TIMn`,
  `GPIOn` or `USART` anywhere. The microphone lines in 4.4 and 7.2 were the one
  problem: 4.4 named the STM32 peripheral (`SAI1`), 7.2 used `BCLK` / `LRCLK`,
  and both KiCad schematics label the part `SCK` / `WS` / `SD`. All three agree
  now, on the INMP441's own datasheet names.
- **Inline legibility, checked and fine.** All three KiCad PNGs were rendered
  down to 800 px wide and read cleanly, including the 2343x3216 motor-driver
  schematic. No re-export needed.

### Code & Contribution, 15 pts — currently 14 to 15

Public repo, MIT licence, substantial README with a repository map, real commit
history, working tree in sync with origin. Linked twice in the article, once as
a clone command in section 2 and once in the closing.

Verified 2026-09-02: `HEAD` equals `origin/main` at `1b7dfd8`. Re-check it at
the moment the article goes up. A judge clicking through to a repo that is
behind the article is a credibility hit that costs more than the points.

### Creativity, 20 pts — currently 17 to 19

The differentiators are real and rare in this field: it **acts** rather than
alerts, it **trains on the device**, and it reports its own failures. Section
9's trip chain is the thing no comparable entry will have.

The risk is that creativity is judged on first impression, and the entry's first
impression is currently a title, a tagline and no cover image. Action 1 is
therefore a creativity fix as much as a compliance one.

---

## 5. Gate 3, the tie-break axes

The brief tells judges to weigh sustainability, user experience and scalability.
Two of the three are unclaimed, and the fixes are short.

### Scalability — present, unlabelled

Section 7.1 already ends with the whole argument, quantified: three of five
parts identical to the base station, deliberately, so growth stays near linear
at ₹8,115 for one machine, ₹12,605 for three, ₹28,320 for ten. Section 8.2's
one-model-per-machine-type is the software half of the same point.

**Action:** one sentence naming it. The numbers are already there and are
already true. Nothing needs inventing.

**Done.** A short paragraph at the end of 7.1 names it and adds the two reasons
growth stays linear that are not about price: no new part number and no per-unit
configuration, because a node's identity comes from its MAC address.

### Sustainability — absent

Zero occurrences in 12,400 words. Every "energy" hit in the file is signal
energy. This is the only tie-break axis with nothing to point at, and the
project has a straightforward true story:

- catching a bearing before it seizes means the motor is repaired, not scrapped
- a ₹2,245 node protects a machine worth several times that, indefinitely
- the fault dataset came from a machine that had already been abandoned: the
  family's dead Ultra wet grinder, taken apart for its bearings

**Action:** two or three sentences, and the honest place for them is section
13.2 or 1.2. Do not overclaim, and do not put a number on avoided waste that
was not measured.

**Done**, as its own heading, **13.2 Repairing instead of replacing**, with the
old 13.2 Closing renumbered to 13.3. A heading rather than a buried paragraph
because a judge scanning for this axis scans headings. Three bullets, and an
explicit line saying no number is put on avoided waste because none was
measured.

### User experience — present, unlabelled

The six-step wizard, the captive portal onboarding tested on real phones over
three rounds, the trip banner that is never behind a click, the status ring
readable from across a shop. All strong, none framed as UX. Lower priority than
sustainability: a judge will feel this one even unlabelled.

**Done anyway**, since it was one paragraph: the section 10 intro now names the
user the interface was designed for and lists the four decisions that follow
from it.

### Sponsor alignment — strong

App Lab has its own subsection at 3.2 and the contest is named for it. Edge
Impulse carries the whole of section 8. The UNO Q's dual-processor split, which
is the sponsor's central thesis, is the subject of 3.1 and 11.1. Nothing to fix.

---

## 6. One credibility gap worth closing

**Section 8 reports no result.** Eight subsections build the classifier: the
grinder faults, the labelled captures, the Edge Impulse link, the upload traps,
the fetch job, the CPU decision. Then section 12 measures the gate, the setup
run, the trip and per-axis features, and never returns to the classifier.

A judge who has just read 130 lines about a fault classifier will ask what it
scored. Reporting nothing reads as an omission even when it is caution.

Two acceptable fixes, in order of preference:

1. State the current number **with its conditions**: dataset size, class count,
   split method, and that the split is contiguous-tail. A modest accuracy
   honestly conditioned scores better than silence.
2. If no number is currently trustworthy, say so in one line and say why, in
   the same voice section 1.5 already uses for the relay and the captive-portal
   bug. That voice is one of this entry's real strengths.

Do not quote a stale figure. Verify against the current model before it goes in.

**Done, option 2**, as new section **8.8**. No trustworthy figure exists: the
report's own Appendix I.5 still carries a `[FILL IN]` for it, and the two
data-integrity bugs recorded there invalidate everything measured before them.
8.8 says that in section 1.5's voice, names both bugs, says where the time went
instead, and points at roadmap item 6 as the fix. It is cross-linked from 1.5
and from 12.4's limitations list. **If a number is ever added here, re-score the
current model first.**

---

## 7. On length

Currently ~12,400 words against PLAN.md's ~6,500 target.

**A judge does not score length.** The rubric has no length criterion, and the
Documentation criterion rewards completeness. Section 1.6's three-way fork
already gives a judge a legitimate path through the article.

Verdict: **the target was a house-style preference, not a rubric requirement.**
Do not spend the remaining days cutting words while nine media items are
outstanding. If time remains after the media is shot and placed, trim the
engineering asides PLAN.md section 7 already identified, and stop there.

The one length-adjacent change that does earn its place is **moving evidence
earlier**. A photograph above section 5 is worth more than a thousand words cut
from section 11.

---

## 8. What not to change

- The admitted-failure passages in 6.5, 6.6, 6.10, 9.4 and 12.4
- Section 4.2, the software and tools BOM
- Section 2, the no-hardware path
- Section 1.5, the built/not-built list
- The machine cast rules in PLAN.md section 3: no lathe or drill press as
  monitored machines, the grinder appears once in 8.3
- The prose rules: no em dashes, no nested bullets, no tables, numbered headings
