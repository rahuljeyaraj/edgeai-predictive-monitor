# Handoff — Hackster.io write-up

Working notes for whoever picks this up next. Not part of the article.

Last updated: 2026-09-01

---

## 1. The task

Write the Hackster.io project write-up for EdgeAI Predictive Monitor.

- Draft lives in `hackster/ARTICLE.md`
- Images go in `hackster/assets/`
- **Deadline: 13 September 2026, 23:59 PDT** (extended from 30 Aug)
- Contest: Hackster "Invent the Future with Arduino UNO Q and App Lab",
  targeting **Best in Industrial IoT**
- Rubric: Documentation 30 · BOM 20 · Schematics 15 · Code 15 · Creativity 20.
  Judges also weigh Sustainability, User Experience, Scalability.

`report/REPORT.md` and the contest PDF are **source material, not truth**.
Cross-check every fact against the code before it goes in the article.

---

## 2. Where things stand

**Done**
- Researched Rahul's own prior Hackster project for house style (see §5)
- Agreed the BOM split, the length target and the tone
- Got the personal story for the hook (§6) and the demo transcript
- Wrote the full skeleton: `hackster/ARTICLE.md` — legend, Hackster-fields
  block, 11 sections / ~48 numbered subsections, every image placeholder
  with its caption written, per-section drafting notes, shot list

**Prose written**
- Section 1 complete: 1.1 hook, 1.2 the idea, 1.3 the rigs. ~890 words.
  Reviewed by Rahul over two rounds; his corrections are recorded in §11 and
  they apply to every section still to be written.
- Section 2 complete: 2.1 the ring, 2.2 fleet, 2.3 one machine, 2.4 setup,
  2.5 the trip, 2.6 Telegram. ~1,060 words. NOT yet reviewed by Rahul.
  Everything in it was read out of the code, see §7.

**Not started**
- Prose for sections 3 through 11
- The verification pass (§7)
- Photos and screenshots (§8)
- Two new diagrams

**Next action:** write section 3 (Running a fleet), 3.1 through 3.3. Verify
the perf-tab numbers for 3.3 against the real page before writing them (§7).
Read §11 first; it is the style contract now.

---

## 3. The Hackster editor rules (these govern the whole draft)

Hackster's editor has **no markdown**. It is a toolbar — select text, click a
button. So the article is drafted as a `.md` file and only mechanically
transcribed at the end. The Hackster session must be pure paste-and-click.

The translation legend at the top of `ARTICLE.md` maps each markdown form to
its toolbar button. Keep it there; it is marked as not part of the article.

**Two hard limits, already worked around in the source:**

1. **One heading level only.** Every heading carries a number prefix
   (1, 1.1, 1.1.1) and gets the same H button. The number carries the depth.
2. **Bullets cannot nest.** No nested bullets anywhere in the source.
   Hierarchy becomes numbered sub-headings instead.

Images/videos are marked `[IMAGE: ...]` / `[VIDEO: ...]` with an italic caption
line directly under. At transcription time that marker is the cue to upload and
embed, and the caption gets pasted and italicised.

---

## 4. The tier structure, and the one rule that governs it

Sections are ordered by how much the reader has committed to, and **each tier
must be a complete read on its own**:

- **Hook** (1) — personal narrative, then the one-line idea. No jargon, no parts
- **Experience** (2) — narrative walkthrough of using the finished thing
- **Operational** (3) — the admin surface behind it, still narrative
- **The fork** (4.3) — one explicit sentence: build track vs architecture track,
  safe to skip to either. The payoff gallery sits AT the fork, before both
- **Build track** (5, 6, 7) — procedure. Imperative, numbered, exact links and
  dimensions, arithmetic shown so a builder can catch their own mistake
- **Architecture track** (8) — mechanism. One diagram per subsection, prose
  points AT the diagram rather than re-describing it
- **Close** (10, 11) — honest future work incl. what was deliberately cut, then
  a conclusion that re-reads the opening hook

**Governing rule: no earlier tier may depend on anything from a later tier
having been read.** That is what makes the fork safe — two complete,
independent articles sharing an opening and a close.

---

## 5. House style, from Rahul's own Hackster project

Reference: [The Fire Pot](https://www.hackster.io/rahul-jeyaraj/the-fire-pot-reimagining-hotpot-7096d5)
(WebFetch 403s on Hackster; plain `curl` with a desktop browser UA returns 200.)

What it establishes:

- **6,196 words** and it reads fine — because no subsection runs past ~150
  words and there is a picture every screen. Target ~6,000 here.
- **Headings are plain and descriptive**, often with a colon:
  "1.1 The problem: guessing at the hotpot stand". Not clever, not tricky.
  The fun lives in the sentences.
- **BOM is split.** The Things widget carried only 4 headline hardware items
  plus 5 software/tools. The real 30-line BOM lived in the story as 5.1 with
  sub-sections and purchase links. Same split is planned here — the JST shells,
  crimp pins, bolts and filament would clutter the widget.
- **Arithmetic is shown**: "92 + 200 + 50 + 200 + 440 + 200 + 50 + 200 + 92 =
  1524 mm". Do the same for the 536-number frame (512 + 24).
- **The fork is one plain sentence**: "Everything past this point is the
  engineering. If you want to build one, follow sections 5, 6 and 7. If you
  want to understand how it works, section 8 covers the architecture."
- Prose is first person, narrative, and unhurried. Numbers sit inside
  sentences rather than in tables wherever possible.

---

## 6. The hook material (given in chat — preserve this)

Rahul's true story, in his words:

> I have a kitchen grinder like this [an Ultra Grind+ Gold table-top wet
> grinder], we have repaired it a ton of times, finally we gave up on it. When
> I planned to monitor the vibration of a machine, this machine came to my
> mind. I thought of taking it apart. It had many issues — the start switch was
> broken, I rewired it; the belt was loose and old, I replaced it; but still I
> was hearing a creaky sound. With the help of an electrician I took the motor
> and the rotating grinder apart, and I saw rusting ball bearings — 2 in the
> motor, 1 in the drum part. Luckily the electrician had the right tools for
> pulling the ball bearing. Once it was out, I could feel the cracks inside it
> when I rotated it in my hand. Then I decided that this ball bearing state is
> what I should prevent from happening. So the two motors, pump 1 and pump 2,
> are based on the motor from that grinder; the turbine, belt drive, is based
> on the ball bearing from the drum area.

**Licence to embellish (explicitly granted):** scenes may be invented and the
narrative extended for readability — the kitchen, the electrician's bench, the
sound on its last good day. **Hard line:** invention stays in atmosphere only.
Every technical fact, part, measurement and claim stays literally true.

This story is why section 1.3 exists and why section 11 works: the bearing that
killed the grinder is the exact fault the three rigs are trained to catch.

---

## 7. Verification checklist (run before each number ships)

Nothing from the PDF or REPORT.md goes in unverified. Specifically:

- `366 tests across 37 modules` — re-run pytest and recount
- `536` feature vector = 4 channels x 128 bins + 4 x 6 scalars — confirm in
  firmware, and show the arithmetic in 8.3
- Autoencoder `536 -> 134 -> 33 -> 134 -> 536`, ~153K params — read the model
- Classifier `536 -> 64 -> 32 -> 4`, ~37K params, int8, ~1 ms, <2 KB RAM
- Thresholds `0.144` warning / `0.288` fault, healthy `0.046`
- Running/stopped gate margin `2.09x` (was `1.18x`)
- `+38.5 sigma` per-axis vs `+1.8 sigma` combined
- Frame rate `~64 ms` base station / `~200 ms` satellite
- Dashboard latency `0.32 s`; trip time `~11 s` (10 s is the operator window).
  The 10 s is `DEFAULT_TRIP_DELAY_S` in `protection/protection.py`, CONFIRMED.
  Banner reads "<name> tripping in Ns" with a Hold button; Acknowledge appears
  after. Setup step order is CONFIRMED as name, stopped, conditions, train,
  trip_output, done (`api/setup_controller.py` STEPS): Stop output is step 5,
  after Train.
- SPI `~40 MHz`, LPUART1 `500000` baud, KX134 run at `12.8 kHz` of a possible
  `25.6 kHz`
- Perf tab claims from the demo: 13 assets, 4 cores idle, ~2% time budget,
  4-5 fps per satellite node
- ~~LED ring colours~~ VERIFIED 2026-09-01 against
  `base-station/python/registry/status_color.py`, and written into 2.1:
  green const healthy · amber (#f59e0b) strobe 1s warning · red strobe 200ms
  fault · red breathe 1s TRIPPED · white const IDLE · cyan const new
  (all three commissioning states) · amber const PAUSED · off for OFFLINE.
  Amber not yellow is a hardware finding, and the dashboard reuses the same
  hexes on purpose. Magenta is the satellite's OWN provisioning ring
  (`satellite/src/threads/transport_task.cpp`), not a fleet status: use it in
  3.2 if anywhere.
- **Mic is currently muted in the model input** (commit cb70b27) — section 10
  must say so. It is zeroed before the feature vector reaches either model.
- Every script name and flag in sections 6 and 7 — confirm it exists
- Pin names must use the **board's own header labels** (D3, D13, A4, SCL),
  never STM32 port names. Sensor pins keep their datasheet names.
- Prices go in marked "as of <date>", never as absolutes

---

## 8. Open items needing Rahul

1. ~~The bearings.~~ ANSWERED 2026-09-01: Rahul salvaged the old bearings AND
   bought matching new ones, so a bearing fault on the rigs is the real
   salvaged part swapped back in. 6201 x2 from the motor, 6004 x1 from the
   drum. Written into 1.3.
2. **Photos.** 3 of 7 now exist (grinder disassembled; salvaged 6004 + new;
   four 6201s). They were sent in chat and still need saving into
   `hackster/assets/`.
3. **Screenshots.** 5 needed.
4. Estimated build time, for the Hackster field.

Full shot list is at the bottom of `ARTICLE.md`.

---

## 9. Assets

**Ready** — `report/diagrams/` has 24 diagrams as PNG + SVG, generated from
Python in `report/diagrams/gen/`. Regenerate rather than hand-edit. The ones
the article uses: 01, 04, 05, 09, 11, 14, 15c, 15d, 15e, 15f, plus the three
KiCad schematic PNGs (02b, 03b, 06).

**Also ready** — 16 `.3mf` files in `3d-models/` for the CAD field; the demo
video on YouTube; `hardware/kicad/` for the schematics field.

**Missing** — most photographs. Three exist but were sent in chat and are NOT
yet in `hackster/assets/`: the grinder fully disassembled with its three
bearings, the salvaged 6004 beside its new replacement, and the four 6201s
(two salvaged, two new). Ask Rahul to drop those files in before transcription.
The three photos in the contest PDF are not in the repo either. Two new
diagrams needed (autoencoder shape for 8.4; possibly a trip-failure path for
8.7 if 15e does not cover it).

---

## 10. Standing preferences that shape this work

- Keep replies short. Bullets over prose. Rahul is dyslexic.
- Commit straight to `main`. Push only when asked.
- This project is effectively an ad for the UNO Q — hype the board, App Lab and
  Bricks wherever it is *naturally* load-bearing, never bolted on. The honest
  note (the dashboard deliberately does not use a Brick, and why) makes the
  praise credible; keep it.
- Never open with "what if your machine could...". No stock openers.
- **No em dashes anywhere in the article.** Use a comma, a full stop or a colon.
- Long paragraphs lose the reader. Make the diagrams talk instead.

---

## 11. Prose review notes from Rahul (from the section 1 rounds)

These are corrections he made to real draft text. They are not preferences,
they are the standard for every remaining section.

**Language**

- **No em dashes.** Comma, full stop or colon instead.
- **Prefer positive words.** He objected to "But it does not stop there" and to
  "but" generally. Use "and", or start a new sentence.
- **No self-congratulating filler.** "and that message is genuinely useful" was
  cut on sight. State what the thing does; do not editorialise about it.
- **No clever-but-empty closers.** "Stopping the machine is a different job"
  was rejected for meaning nothing concrete. Replace that instinct with a
  concrete consequence: "It protects the machine at three in the morning with
  the shop empty and every phone on silent."
- **Say what is actually moving.** "watches for the picture to drift" was
  unclear. Name the mechanism: it compares against the learned normal, wear or
  looseness moves the vibration and sound away from it, the score climbs, and
  it crosses a warning line and then a fault line.

**Vocabulary and facts**

- **Use the project's own nouns.** "pod" is not a word in this project.
  It is a **base station** (Arduino UNO Q) and a **satellite** (XIAO ESP32-S3).
  Introduce both the first time they are needed, which is now done in 1.2.
- **The base station monitors a machine itself.** It is not only a hub. Say
  both jobs whenever it is described.
- **Nodes attach magnetically.** Never "bolted to" a machine.
- **Unbalance is induced by REMOVING bolts from the flywheel**, not adding
  them. The 5.7 drafting note has been corrected to match.
- **No fault in this project is simulated.** Every one of the three is induced
  physically on a spinning rig. Do not single out the bearing as the real one.
- **The bearings are genuinely salvaged.** Rahul kept the worn bearings from
  the grinder and bought matching new ones, so a bearing fault is the actual
  worn part going back in. 6201 x2 (motor, so the pump rigs), 6004 x1 (drum, so
  the turbine rig).
- **The system names all three fault classes.** Do not write anything implying
  it was built only for bearing faults, however good the sentence sounds.
