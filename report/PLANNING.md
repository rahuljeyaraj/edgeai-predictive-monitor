# Report planning — EdgeAI Predictive Monitor

Internal working doc. **Not** part of the submitted PDF. `REPORT.md` must stand
completely on its own — everything it needs, it contains. Nothing in `docs/` can
be cited by reference, because `docs/` is deleted before submission.

---

## 1. Facts this plan is built on

- **Deadline:** 23 Aug 2026 (user-confirmed; the public page's 31 Jul is stale).
- **Rubric, 100 pts:** Functionality & Execution 40 · Innovation & Originality 25
  · Technical Documentation (BOM, schematics, code quality) 20 · Presentation &
  Creativity 15.
- **Required deliverables:** demo video, GitHub source, report PDF,
  circuit/system diagram, proof of purchase of the UNO Q. No page limit found.
- **"Physical AI"** means sensing + computation ending in a real physical
  action. Ch. 7 (the trip) is the chapter that makes this a valid entry at all.
- **No new hardware** is being bought. The report documents what exists.
- **Robu.in is the contest sponsor**, so the BOM links there (user instruction,
  2026-08-01). Robu blocks automated fetching (403), so listed prices are
  indicative Indian-retail figures checked Aug 2026 — confirm before submission.

---

## 2. Structure (rewritten 2026-08-01)

Four parts, twelve chapters, twelve appendices. The v1 draft was rewritten end to
end after review; §7 lists what was wrong with it.

**Part I — The system.** 1 Overview · 2 Why the UNO Q · 3 Base station ·
4 Fleet + onboarding
**Part II — The intelligence.** 5 Commissioning + anomaly model · 6 Classifier ·
7 The trip
**Part III — The human interface.** 8 Statuses, dashboard, lights, alerts
**Part IV — The engineering.** 9 Architecture · 10 Design decisions · 11 Results
· 12 Roadmap

**Appendices.** A BOM · B Wiring + schematics · C Build one yourself ·
D Sensor selection · E Network selection · F Wire protocol · G Sensor
configuration envelope · H Gate calibration · I Classifier experiments ·
J Tests + verification record · K 3D-printed rigs *(placeholder)* · L Glossary

### Layering rule (held throughout)

Every chapter opens with a plain-English section that is a complete summary of
that chapter. Reading only those, front to back, gives the whole project with no
jargon. Everything after goes progressively deeper.

### Single-source rule

Each fact lives in exactly one place and is linked to from everywhere else.
Specifically: **the BOM only exists in Appendix A**, **pin-level wiring only in
Appendix B**, **build commands only in Appendix C**. The v1 draft duplicated all
three between chapters and appendices.

---

## 3. Reader map

| Reader | Reads | Walks away with |
|---|---|---|
| Judge skimming many entries | Ch. 1 | What it is, that it works, why it's Physical AI |
| Judge on the Arduino angle | Ch. 1–2 | Exactly which UNO Q capabilities are load-bearing |
| Someone building one | Ch. 3–4, App. A/B/C | Parts with buy links, schematics, commands — plus two no-hardware paths |
| Reviewer scoring engineering | Ch. 5–11 | AI design, protection logic, architecture, measured results |
| Curious engineer | App. D–J | Why each decision went the way it did, and the dead ends |

---

## 4. Cross-reference / anchor policy

Links are **chapter- and appendix-level only**, never to subsections. Reason:
GitHub and Pandoc generate heading anchors differently for headings that *start
with a digit* — Pandoc strips everything up to the first letter, so `### 3.2 Foo`
becomes `#foo` in Pandoc and `#32-foo` on GitHub. Chapter and appendix headings
all start with a letter (`Chapter 3. …`, `Appendix C. …`), so their slugs are
identical in both renderers.

Subsection references in prose therefore read `[§5.7](#chapter-5-…)` — they name
the subsection but land on the chapter. Verified: 106 internal links, 0 broken.

---

## 5. Diagrams

Nine hand-built block diagrams (`diagrams/gen/`, see `diagrams/README.md`) plus
three real KiCad schematics from `hardware/kicad/`. The v1 diagrams were rebuilt
because they had caption text overlapping arrowheads, misaligned legend swatches,
group borders cutting through their own labels, crossed edges with colliding
labels, and large dead areas. `diagram_lib.py` now enforces framed layout,
measured text widths and orthogonal routing so those can't come back.

Four diagrams are new and exist to answer specific review gaps: `06-asset-lifecycle`
(all ten statuses), `07-trip-sequence`, `08-dashboard-anatomy`, `09-onboarding`.

---

## 6. Decisions

1. **PDF pipeline: Pandoc.** All diagram references are real PNGs now — no
   placeholders, no Mermaid filter needed.
2. **Protagonist "Ravi"**, generic small Indian machine shop. Confined to the
   opening of Ch. 1 and the close of Ch. 12 — the v1 draft carried the persona
   into technical chapters, which diluted them.
3. **Classifier is described as working** (user instruction, 2026-08-01). The
   42% / 59.82% / 69.64% figures were from the earlier Kaggle-replay research
   phase; they now live in Appendix I explicitly labelled as method history, with
   a note not to quote them as current performance.
4. **Satellite nodes are written as built and working** (standing user
   instruction). Deliberately, no *specific live-verified measurements* are
   claimed for them — see the open items below.
5. **"Physical AI" is argued once, properly** (Ch. 1 §1.4 + Ch. 7) rather than
   asserted in every chapter, which is how the v1 draft read.

---

## 7. What the v1 draft got wrong (kept so it isn't repeated)

- Surface-level throughout; no real depth on the dashboard, the statuses, or the
  sensor's configurable envelope.
- Repeated "Physical AI" many times without ever explaining it properly.
- **Factually wrong on satellite Wi-Fi setup**: claimed SSID/password are
  compiled in before power-on and that the base station hosts the network.
  Reality is a per-node captive portal (`EPM-SAT-<id>`), three fields, tested
  before saving to NVS; the base station's own hotspot is a *fallback* for
  onboarding itself.
- Said the classifier wasn't working.
- Onboarding experience was absent; the ten asset statuses were absent.
- Replication instructions were thin, and never mentioned the simulator or the
  desktop dashboard — the two paths that need no hardware.
- Gate-calibration detail leaked into the main chapters instead of staying in its
  appendix.
- No cross-reference links; BOM had no buy links; BOM and wiring were duplicated
  between chapters and appendices; table of contents was a bare list with the
  appendices on one line.
- The UNO Q itself was barely praised, in a contest whose purpose is to showcase
  it. Ch. 2 now exists for that, and is honest about the three limits found.

---

## 8. Open items before submission

1. `[FILL IN]` — team/author name, submission date, GitHub URL, demo video URL.
2. `[FILL IN]` — actual UNO Q purchase price + receipt reference (Appendix A).
3. `[FILL IN]` — current classifier accuracy / confusion matrix from Edge Impulse
   Studio (Appendix I §I.5).
4. **Appendix K is a placeholder** — the 3D-printed test rigs section needs
   writing, with models, print settings and photos (user instruction).
5. **Photos and screenshots.** Search `REPORT.md` for `[PHOTO`, `[SCREENSHOT` and
   `[VIDEO STILL` — every one is a real shot that still needs taking.
6. **Verify Robu prices and links** are still live and current.
7. **Satellite hardware bring-up.** The report presents satellite nodes as
   built and working, per standing instruction, but `satellite/README.md` and
   `docs/WIFI_ONBOARDING_PLAN.md` §2 both record that the firmware port and its
   captive-portal onboarding have not yet been run on a physical XIAO ESP32-S3.
   The demo video is meant to be a real, unedited demo, so this gap should be
   closed with an actual bring-up before judging.
8. **Telegram token.** Re-add the `arduino:telegram_bot` brick to
   `base-station/app.yaml` and set `TELEGRAM_BOT_TOKEN` via App Lab, so the
   feature is live rather than "built and demonstrated".
