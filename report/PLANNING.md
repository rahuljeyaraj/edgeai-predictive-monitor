# Report planning — EdgeAI Predictive Monitor

Internal working doc. **Not** part of the submitted PDF. `REPORT.md` must stand
completely on its own — everything it needs, it contains. Nothing in `docs/` can
be cited by reference, because `docs/` is deleted before submission.

---

## 1. The two contests this report serves

### Arduino Physical AI Challenge India 2026 (robu.in + Arduino + Qualcomm)

- **Deadline:** 23 Aug 2026 (user-confirmed; the public page's 31 Jul is stale).
- **Track:** Industrial & Sustainability AI.
- **Rubric, 100 pts:** Functionality & Execution 40 · Innovation & Originality 25
  · Technical Documentation (BOM, schematics, code quality) 20 · Presentation &
  Creativity 15. **Open source earns bonus points.**
- **Deliverables:** demo video (continuous, unedited, publicly accessible) ·
  GitHub source · report PDF · circuit/system diagram · **proof of purchase of
  the UNO Q (mandatory)**.

### Invent the Future with Arduino UNO Q and App Lab (Hackster.io)

Fetched from the live rules page 2026-08-02. Sponsors: Arduino, Qualcomm and
**Edge Impulse**; also Foundries, HuggingFace, SparkFun, Seeed, Farnell, ST.

- **Submissions close:** 30 Aug 2026, 23:59 PT. Winners by 25 Sep 2026.
- **Category to target:** Best in Industrial IoT ($3,000) — and Best in Show.
- **Rubric, 100 pts, verbatim headings:**
  - **Project Documentation (30)** — "Story/Instructions … *If I were a beginner
    reading this project, would I understand how to recreate it?*"
  - **Complete BOM (20)** — "Detail the hardware, **software and/or tools** used."
  - **Schematics (15)** — circuit diagrams and/or detailed photographs.
  - **Code & Contribution (15)** — "working code with helpful comments."
  - **Creativity (20)** — a fresh take counts as much as a new idea.
- **Required project contents:** name, short description, **cover image**, BOM,
  full instructions, images, resource files (schematics, code, **CAD**).
- **Additional considerations judges are told to weigh:** Sustainability, User
  Experience, Scalability.
- The contest's own example brief for Industrial IoT is, almost word for word,
  this project: *"Smart Predictive Maintenance System: monitors vibration …
  using sensors connected to UNO Q. Use AI models (via Edge Impulse) to predict
  failures before they happen … add a dashboard in App Lab for real-time alerts
  and analytics."*

### What both share

- **"Physical AI"** means sensing + computation ending in a real physical
  action. Ch. 8 (the trip) is what makes this a valid entry at all.
- **No new hardware** is being bought. The report documents what exists.
- **Robu.in is the Indian contest's sponsor**, so the BOM links there. Robu
  blocks automated fetching (403), so listed prices are indicative Indian-retail
  figures checked Aug 2026 — confirm before submission.

---

## 2. Structure (rev 3, rewritten 2026-08-02)

Four parts, thirteen chapters, fourteen appendices.

**Part I — The system.** 1 Overview · 2 Why the UNO Q (+ App Lab) ·
3 Base station · 4 Fleet + onboarding
**Part II — The intelligence.** 5 Guided setup + anomaly model · 6 Classifier
concept · **7 The Edge Impulse workflow (new)** · 8 The trip
**Part III — The human interface.** 9 Statuses, trip banner, all five tabs,
lights, alerts
**Part IV — The engineering.** 10 Architecture · 11 Design decisions ·
12 Results · 13 Roadmap

**Appendices.** A BOM (+ software/tools) · B Wiring + schematics · C Build one
yourself · D Sensor selection · E Network selection · F Wire protocol ·
G Sensor configuration envelope · H Gate calibration · I Classifier research
history · J Tests + verification record · K 3D-printed rigs *(placeholder)* ·
**L Reading the source (new)** · **M Sustainability, scale, running cost (new)**
· N Glossary

### What changed in rev 3

1. **"How to read this report" is gone.** It described the writing brief rather
   than the project. The depth guidance now lives implicitly in the ToC, and the
   two conventions worth keeping (live-verified; unfinished things say so) are
   one italic paragraph under it.
2. **Commissioning rewritten** for the six-step guided setup, multi-condition
   collection, mandatory name + class, and confirm-by-stopping. Includes the
   **measured 5.1× sensitivity cost** of pooled conditions and the 2.4×
   overspeed that goes undetected under two conditions — the most important
   honest number added in this pass.
3. **Edge Impulse gets its own chapter** (7). It is a named sponsor of the
   Hackster contest and the workflow was previously invisible outside an
   appendix.
4. **The four non-Fleet tabs** went from four paragraphs to a section each.
5. **Architecture deepened** — a layer-by-layer software map, what runs as what
   (container + three host bridges + broker), and what is stored where.
6. **Safety scope stated** (§8.4): monitoring system with a protective trip, not
   a certified functional-safety system, with its failure mode named.
7. **Story spine.** Ravi now opens most chapters as a short blockquote vignette
   with a month marker, plus a timeline table in §1.5. Kept in labelled frames
   so the technical body stays undiluted — the v1 problem.
8. **Five new diagrams** (10–14): setup flow, Edge Impulse round trip, software
   architecture, tab map, two-brains split. Now 17 images total.
9. **Judge-facing gaps closed:** software/tools BOM (A.4), Appendix L for Code &
   Contribution, Appendix M for sustainability + scalability, beginner time
   estimates and a "start here" in Appendix C.

### Layering rule (held throughout)

Every chapter opens with a plain-English section that is a complete summary of
that chapter. Everything after goes progressively deeper.

### Single-source rule

Each fact lives in exactly one place and is linked to from everywhere else.
**The BOM only exists in Appendix A**, **pin-level wiring only in Appendix B**,
**build commands only in Appendix C**.

---

## 3. Cross-reference / anchor policy

Links are **chapter- and appendix-level only**, never to subsections. Reason:
GitHub and Pandoc generate heading anchors differently for headings that *start
with a digit* — Pandoc strips everything up to the first letter, so `### 3.2 Foo`
becomes `#foo` in Pandoc and `#32-foo` on GitHub. Chapter and appendix headings
all start with a letter, so their slugs are identical in both renderers.

Subsection references in prose therefore read `[§5.7](#chapter-5-…)` — they name
the subsection but land on the chapter. **Verified 2026-08-02: 148 internal
links, 0 broken, 0 duplicate anchors, 17 images all present.**

---

## 4. Diagrams

Fourteen generated block diagrams (`diagrams/gen/`, see `diagrams/README.md`)
plus three real KiCad schematics from `hardware/kicad/`. `diagram_lib.py`
enforces framed layout, measured text widths and orthogonal routing so the
first-generation defects (captions over arrowheads, borders through labels)
can't come back.

---

## 5. Decisions

1. **PDF pipeline: Pandoc.** All diagram references are real PNGs.
2. **Protagonist "Ravi"**, generic small Indian machine shop. Rev 3 extends him
   across chapter openings as *labelled vignettes*, on user instruction —
   distinct from v1, which wove the persona into technical prose.
3. **Classifier is described as working** (user instruction, 2026-08-01). The
   42% / 59.82% / 69.64% figures live in Appendix I labelled as method history,
   with a note not to quote them as current performance.
4. **Satellite nodes are written as built and working** in the chapters
   (standing user instruction). Rev 3 adds one honest line in §12.7 and §J.3
   that they have not yet been run on physical XIAO hardware — **flagged to the
   user; remove if the standing instruction should override the verification
   record.** Closing it with a real bring-up is the better fix.
5. **"Physical AI" is argued once, properly** (§1.4 + Ch. 8).

---

## 6. Open items before submission

1. `[FILL IN]` ×7 — team/author name, submission date, GitHub URL, demo video
   URL, UNO Q purchase price + receipt reference, current classifier accuracy /
   confusion matrix.
2. **Photos and screenshots — the single biggest remaining gap.** 8 `[PHOTO`,
   12 `[SCREENSHOT`, 2 `[VIDEO STILL` placeholders. Hackster's Project
   Documentation is 30 pts and explicitly asks for images and screenshots; a
   cover image is a *required* project field.
3. ~~No `LICENSE`~~ — **done 2026-08-02: MIT**, copyright "Rahul Jeyaraj".
   Confirm that name is how you want to be credited.
4. ~~No root `README.md`~~ — **done 2026-08-02.** Judge-facing entry point:
   hero diagram, the ten-minute no-hardware path, repo map, status table,
   safety scope. Its links into `REPORT.md` are anchor-checked; re-run the
   checker if report headings change.
5. **Appendix K is a placeholder** — 3D-printed test rigs, with models, print
   settings and photos. Hackster's required resource files list names CAD
   explicitly.
6. **Verify Robu prices and links** are still live and current.
7. **Satellite hardware bring-up** on a physical XIAO ESP32-S3 — closes the one
   gap in §J.3 and makes the demo video honest.
8. **Telegram token.** Re-add the `arduino:telegram_bot` brick to
   `base-station/app.yaml` and set `TELEGRAM_BOT_TOKEN` via App Lab, so the
   feature is live rather than "built and demonstrated".
9. **Decide whether `docs/` really is deleted before submission.** It is strong
   evidence of engineering rigour for both "code quality" and "Code &
   Contribution", and Appendix L currently mentions it in one neutral sentence
   that is trivial to remove if the deletion stands.
10. **Measure power draw** — Appendix M explicitly declines to give a figure.
    One measurement closes it.
