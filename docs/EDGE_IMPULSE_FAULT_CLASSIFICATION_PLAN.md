# Plan — Edge Impulse fault *classification* + AWS Greengrass

Status: **In progress — T1 done (2026-07-16), data uploaded to EI project 1060830,
first training runs done + a real train/test leakage bug found and fixed
(2026-07-17). Honest current accuracy is weak (~42%, see §3.3) — next session picks
up at feature dimensionality (band-power aggregation), not plumbing. All §9
decisions resolved (2026-07-15).** This doc
proposes how to add a second AI model — a supervised **fault-type classifier**
trained in Edge Impulse — on top of the existing per-node autoencoder, fed by the
simulated satellite node replaying the Kaggle vibration dataset, surfaced on the
dashboard, and (stretch) managed via AWS IoT Greengrass. It explains Edge Impulse
from scratch since we've never used it.

**Resolved decisions (see §9 for rationale):**
1. Feature representation → **spectrum-as-features + TFLite** (the recommended path).
2. Classes (from the dataset on disk) → **`Ideal` (healthy), `Cracking`,
   `Offset_Pulley`, `Wear`** — 4-class, accelerometer-only.
3. Greengrass → **MQTT-bridge + cloud telemetry committed; OTA model component is a
   time-permitting stretch.**
4. Classifier scope → **satellite (Kaggle) nodes only; local SPI motors stay
   anomaly-only.**

Companion to [PROGRESS.md](PROGRESS.md) / [progress2.md](progress2.md) (the base-station
port) and the MPU pipeline under [base-station/python/](../base-station/python/).

---

## 0. The core idea (read this first)

We already have **one** model. We're adding a **second**, and they do different jobs:

| | Model A — Autoencoder (exists) | Model B — Fault classifier (new) |
|---|---|---|
| Question it answers | "Does this motor look **abnormal**?" | "**Which fault** is this?" |
| Learning type | **Unsupervised** (trained only on *healthy* data) | **Supervised** (trained on *labeled* fault data) |
| Scope | **Per-node** — one model per motor, trained on that motor's own baseline | **Fleet-wide** — one model shared by all nodes running the same machine class |
| Trained where | **On the MPU**, during commissioning ([commissioning.py](../base-station/python/pipeline/commissioning.py)) | **In Edge Impulse cloud**, offline, once |
| Output | reconstruction error → healthy / warning / fault | class label + confidence, e.g. `bearing 0.92` |
| Lives in | [autoencoder.py](../base-station/python/pipeline/autoencoder.py) / [inference.py](../base-station/python/pipeline/inference.py) | new `pipeline/classifier.py` |

**Why you need both.** An autoencoder is an *anomaly detector*: it learns what
healthy looks like and flags anything that deviates. It fundamentally **cannot name
the fault**, because it was never shown any faults — it only knows "this isn't
healthy." To name a fault you must show the model **labeled examples of each fault
type**, which is *supervised learning*. That is exactly what the Kaggle dataset gives
us (each folder = a fault type) and exactly what Edge Impulse's Classification block
does.

**The cascade (how "the second AI kicks in").** They run in series:

```
frame ─▶ gate (running?) ─▶ features (FFT spectrum, peak-normalized)
                                   │
                    ┌──────────────┴───────────────┐
                    ▼                               ▼
          A. Autoencoder                   B. EI classifier
          reconstruction error             top class + confidence
                    │                               │
              healthy/warning/fault          "bearing / misalign / wear / ..."
                    │                               │
                    └──────────────┬───────────────┘
                                   ▼
              Dashboard shows:  "FAULT · Bearing (92%)"
```

Model B classifies continuously, but its label is only *surfaced as a diagnosis*
when Model A raises warning/fault — so the story is: **the autoencoder detects that
something is wrong, then the classifier names it.** The two share the *same feature
vector* ([features.py](../base-station/python/pipeline/features.py)'s normalized
spectrum), so there's no second feature-extraction path to build.

**Important honesty about scope (this matches what you already said).** The Kaggle
dataset's fault signatures won't match your *local* bench motors — so the classifier
is meaningful on the **simulated satellite nodes replaying Kaggle data**, where the
live spectra come from the same distribution the classifier was trained on. Local
SPI motors keep using **Model A only** (anomaly detection). The dashboard shows the
diagnosis panel only for classifier-enabled nodes. This is the correct, defensible
framing — we don't pretend the Kaggle-trained classifier generalizes to hardware it
never saw.

---

## 1. Edge Impulse in 5 minutes (we've never used it)

**What it is.** [Edge Impulse](https://studio.edgeimpulse.com) is a free (developer
tier) cloud platform for building small ML models that run on the edge. It handles
the whole ML lifecycle in a browser so you don't write training code: **upload
labeled data → design a pipeline ("impulse") → it extracts features → train a neural
net → see a confusion matrix → export a deployable model.** For the competition, the
value is that "we trained a real supervised classifier with proper train/test
splits, feature engineering, and a measured confusion matrix" — all reproducible and
demoable — not a hand-rolled script.

**Key vocabulary:**

- **Project** — one workspace (one dataset + one impulse + models).
- **Data acquisition** — your labeled samples, split into **Training** and **Test**
  sets. A sample carries a **label** (the fault type). You upload via the web UI, the
  `edge-impulse-uploader` CLI, or the ingestion API. Labels can come from the
  **filename prefix** (`bearing.001.csv` → label `bearing`) or a `--label` flag.
- **Impulse** — the pipeline, three blocks in a row:
  1. **Input block** — takes a *window* of your signal (window size + stride). For
     time-series vibration this is e.g. 1024 samples; for our "spectrum as features"
     route it's the 512-value spectrum treated as one sample.
  2. **Processing / DSP block** — turns the window into a feature vector. Options
     that matter to us: **Spectral Analysis** (purpose-built for vibration/motor
     data — FFT, RMS, spectral power in bands), **Raw**, and **Flatten** (pass values
     through, optionally with simple stats).
  3. **Learning block** — **Classification** (a Keras neural net → per-class
     probabilities). (There's also an unsupervised **Anomaly Detection** block, but
     that's the job Model A already does on-device.)
- **Feature explorer** — a 2D/3D scatter of your samples colored by label. If the
  classes visibly separate here, classification will work; if they're one blob, your
  DSP/window choice is wrong.
- **Training** — click Train, watch accuracy + **confusion matrix** (which classes
  get confused for which). Then **Model testing** runs the held-out Test set.
- **Deployment** — export the trained model as: an **Arduino library**, a **C++
  library**, **WebAssembly**, a **TFLite** file (float32/int8), or a **Linux `.eim`**
  self-contained runner for x86_64 / **AARCH64** / ARMv7. Our MPU (QRB2210) is
  **arm64**, so AARCH64 `.eim` and TFLite are both valid.

That's the whole loop. No training code, no GPU, browser-only.

---

## 2. The data: Kaggle dataset, "normal vs faulty", and how the sim sends it

**Dataset:** [Vibration-based Fault Diagnosis of Machines](https://www.kaggle.com/datasets/sumairaziz/vibration-based-fault-diagnosis-of-machines),
on disk at `~/workspace/vibration-based-fault-diagnosis-of-machines/`. Confirmed
structure: `<class>/<class>_<axis>/M(n).csv`, **top-level folder = the class label**:

| Class folder | Meaning | Files (X+Y+Z) |
|---|---|---|
| `Ideal` | **healthy / normal** (our baseline class) | ~30 |
| `Cracking` | fault | ~30 |
| `Offset_Pulley` | fault | ~30 |
| `Wear` | fault | ~60 |

~150 CSVs total, split by axis X/Y/Z, each ~3,490 samples after a metadata header
(then `<index>,<amplitude>` rows — [`load_signal()`](../base-station/python/tools/satellite_node_sim.py)
already parses exactly this format). Note the Windows `*.csv:Zone.Identifier` marker
files — the prep script must skip them (the sim's `list_files()` already does).

**Two facts that shape the design:**
- **`Ideal` is a real healthy class** → the classifier is a clean **4-way** problem
  (healthy + 3 faults), and can itself express "looks normal," which we cross-check
  against Model A.
- **The dataset is accelerometer-only (no audio).** So Kaggle satellite nodes stream
  the **accel channel only** → `sensor_config = {ACCEL}` → a **512-dim** feature
  vector, which is exactly what a single-axis 1024-sample FFT produces (513 bins − DC).
  We label **axis-agnostically**: pool X/Y/Z under each class so the classifier learns
  the fault signature regardless of mounting axis. Enabling the mic channel on a
  Kaggle node would be meaningless — leave it off.

**How the simulator already sends data.** [`satellite_node_sim.py`](../base-station/python/tools/satellite_node_sim.py)
is a standalone process that mimics one ESP32 satellite node. Its web UI (one per
sim instance, on its own `--ui-port`) lets you:
- flip the node **online/offline**,
- per channel (accel / mic) **pick which file** under `--data-dir` it loops over,
- watch its status LED (pushed back from the base station).

Each publish cycle it reads the next 1024-sample window from the selected file,
FFTs it ([`compute_spectrum()`](../base-station/python/tools/satellite_node_sim.py)),
and publishes the spectrum over MQTT (`epm/<node_id>/data`) — the *same*
`spectrum_fused_payload` the base station's own sensors produce, so everything
downstream (gate → features → autoencoder → **classifier**) is one code path.

So **"send normal data" = point the channel at a healthy/baseline file; "send faulty
data" = point it at a `Wear/…` (or other fault) file.** That's already possible by
hand today. For a clean live demo we'll add scripted switching (see §7).

**Two levels of "data" — don't conflate them:**
- **Training data (offline, one-time):** *all* Kaggle files, windowed + labeled by
  folder, uploaded to Edge Impulse. This is how Model B learns.
- **Live inference data (demo):** the sim streams *one file at a time* over MQTT; the
  deployed Model B classifies each window in real time on the MPU.

---

## 3. Training the classifier in Edge Impulse

### 3.1 The one real decision: what does the classifier eat?

This is the only choice that changes the code. Two coherent options:

**➤ RECOMMENDED — "spectrum as features" (Flatten/Raw block + TFLite export).**
Feed Model B the **same peak-normalized FFT magnitude spectrum** Model A already
uses. In EI: input = the 512-value spectrum as one sample, DSP = **Flatten/Raw**
(near-passthrough), learning = **Classification**. Export as **TFLite**.
- ✅ **Train/serve feature parity** — training features are computed by the exact
  same `np.abs(rfft(hanning·window))` the sim/pipeline already run, so there's no
  drift between what EI trained on and what flows live.
- ✅ **Identical for satellite and local nodes** — both already produce spectra; no
  new raw-window transport needed.
- ✅ **Self-contained inference in the App Lab container** — a TFLite file + a tiny
  interpreter call; no subprocess, no socket, no `.eim` runtime (the container is a
  locked-down non-root Docker sandbox — see progress2 §4.2 — so fewer moving parts is
  a real advantage).
- ✅ We already do the FFT — no DSP to replicate at serve time.
- ⚠️ We don't use EI's fancy Spectral DSP — but we don't need it; the autoencoder
  already proves these spectra separate healthy from faulty, and EI still does all
  the labeling/training/confusion-matrix/versioning work (that's the "we used Edge
  Impulse" story, fully intact).

**➤ ALTERNATIVE — "raw window + Spectral Analysis DSP" (`.eim` export).**
Feed EI **raw 1024-sample vibration windows**, let its **Spectral Analysis** block do
the DSP (RMS/FFT/band-power), export an AARCH64 **`.eim`**.
- ✅ More EI-native; Spectral Analysis is EI's flagship block for motor/bearing
  vibration and may squeeze out a few % more accuracy.
- ✅ `.eim` OTA-deploys cleanly as an AWS Greengrass ML component (§6) — nice for the
  cloud story.
- ⚠️ Requires the satellite payload to also carry the **raw window** (we only send
  spectra today), and running the `.eim` as a subprocess inside the container.

**DECISION (locked): the RECOMMENDED path** — spectrum-as-features + TFLite (fastest
to a working demo, no transport change, no container subprocess). The ALTERNATIVE is
a documented upgrade if we later want EI-native DSP + a Greengrass-managed `.eim`. The
rest of this doc assumes the recommended path unless noted.

Concretely for this dataset: each EI "sample" = the **512-value accel spectrum** of
one 1024-sample window; DSP = **Flatten/Raw**; learning = **4-class Classification**
(`Ideal`/`Cracking`/`Offset_Pulley`/`Wear`).

### 3.2 Step-by-step (recommended path)

1. **Prep script — [`tools/ei_dataset_prep.py`](../base-station/python/tools/ei_dataset_prep.py)
   (done 2026-07-16).** Walks `--data-dir`, and for each file: slides a 1024-sample
   window at stride = window size (non-overlapping, same as the sim reading a file
   sequentially), computes the **same** `compute_spectrum()` feature (imports it
   straight from `satellite_node_sim.py`, so it's byte-for-byte identical), peak-
   normalizes (imports [`normalize_bins()`](../base-station/python/pipeline/features.py)),
   and writes one `<label>/<label>.<n>.csv` sample per window (`timestamp,accel`
   time-series CSV, 512 rows — a single-row/512-column CSV gets rejected by EI's
   ingestion API with "need exactly one line with values (but found 512)"; a
   `timestamp` column is what tells EI this is one windowed sample, not 512
   separate ones) — label = top-level folder name. Verified run against the real dataset:
   **433 samples** (`Ideal` 88, `Cracking` 88, `Offset_Pulley` 90, `Wear` 167 — the
   class imbalance is inherited from the source file counts, see §2's table).
   Requires `numpy` + `paho-mqtt` + `python-statemachine` (the last two only because
   they're transitively imported via `satellite_node_sim`/`registry`) — deliberately
   **not** in [requirements.txt](../base-station/python/requirements.txt) (on the
   target App Lab container, `paho-mqtt` comes from apt and `numpy` ships with the
   image — see `satellite_node_sim.py`'s docstring), so a dev machine needs its own
   venv: [`tools/requirements-ei.txt`](../base-station/python/tools/requirements-ei.txt)
   pins the three packages; set up with
   `cd base-station/python && python3 -m venv .venv && .venv/bin/pip install -r tools/requirements-ei.txt`
   (`.venv/` is already gitignored). Run the script as
   `.venv/bin/python tools/ei_dataset_prep.py --data-dir ... --out-dir ...`.
   Prepared output for this run lives at
   `~/workspace/vibration-based-fault-diagnosis-of-machines-prepared/` (outside the
   repo — it's derived data, ~5MB of CSVs, not meant to be committed).
   If 433 samples trains poorly, `--stride` (e.g. half the window size) gives more
   overlapping-window samples per file at the cost of some correlation between them.
2. **Create an Edge Impulse project** (studio.edgeimpulse.com, free tier) and upload
   the prepared samples (project ID 1060830). This dev machine has **no `node`/`npm`**,
   so instead of the `edge-impulse-cli` uploader, use the curl-only
   [`tools/ei_upload.sh`](../base-station/python/tools/ei_upload.sh), which hits the
   [ingestion API](https://docs.edgeimpulse.com/reference/ingestion-api) directly,
   batching many files per request (see its docstring):
   `EI_API_KEY=ei_xxx tools/ei_upload.sh ~/workspace/vibration-based-fault-diagnosis-of-machines-prepared`
   (API key: project dashboard → **Keys** → **Add new API key**). **The train/test
   split is decided entirely by `ei_dataset_prep.py`, at the file level, before this
   script ever runs** — see §3.3 for why that matters. If you'd rather use the
   official CLI instead, install Node.js first, then
   `npm i -g edge-impulse-cli && edge-impulse-uploader --category split prepared/*/*.csv`
   (note: the CLI's own split wouldn't know about our file-level split either, so
   stick with `ei_upload.sh` unless you point the CLI at `prepared/training/*/*.csv`
   and `prepared/testing/*/*.csv` separately with explicit `--category`).
3. **Design the impulse:** input = spectrum window; processing = **Flatten/Raw**;
   learning = **Classification**. Generate features, open the **feature explorer** —
   confirm the classes separate.
4. **Train.** Small dense net (EI's default is fine for ~512 inputs). Check accuracy
   + **confusion matrix**. Iterate on window/normalization if a pair of classes
   collapses.
5. **Model testing** on the held-out set — this number is what we quote in the demo.
6. **Deploy → TFLite (float32 first, int8 if we want it tiny).** Download the `.tflite`
   + the class-label ordering. Also **note the exact input feature length/order** EI
   expects (it must match our live vector).
7. Commit the label list + a short model card (accuracy, classes, date, EI project
   link) to `docs/`. Drop the model into `base-station/.cache/models/`
   (that dir survives redeploys — see [main.py](../base-station/python/main.py)'s
   `DEFAULT_DATA_DIR`).

### 3.3 First training runs (2026-07-17): a leakage bug, and the honest number

**Attempt 1 — Flatten block (default 7 statistical features/axis).** 38.6% validation
accuracy; confusion matrix showed the model collapsed to predicting the majority
class (`Wear`) almost everywhere. Root cause: Flatten's default config computes 7
scalar summary stats (avg/min/max/RMS/std/skew/kurtosis) per axis, not a passthrough
of the 512-bin spectrum — the model never saw the spectrum shape at all.

**Attempt 2 — switched DSP block to Raw Data** (true 512-feature passthrough). 47.1%
validation accuracy — real signal, no longer collapsed, but still weak, and
concerningly `Ideal` (healthy) was getting confused with the fault classes.

**Attempt 3 — EON Tuner** (search over Raw Data + dense-net architectures/hyperparams).
Leaderboard's top trials showed **60%/54%/50% validation accuracy but only 24%/16%/9%
Test accuracy** — a massive validation-vs-test collapse, worse than random guessing on
Test for 2 of 3 trials. Root cause diagnosed: `ei_upload.sh` was splitting
train/test **at the window level** (every 5th window, globally), not the file level.
Since each source file produced only ~3 non-overlapping windows, windows from the
*same physical recording* could land on both sides of the split — so a model could
partly "recognize the file" rather than learn a fault signature that generalizes,
and EON Tuner's leaderboard (which ranks entirely by validation accuracy across dozens
of trials) rewarded exactly that.

**Fix — file-level split, done in `ei_dataset_prep.py` (see its docstring for the
full rationale):** files are now assigned to train or test *before* windowing (every
5th file per class → test, deterministic), so no window from a test file can ever
share a file with a training window. Training files get windowed at `--train-stride`
(default window-size/8, ~8x overlap → more samples); test files at a smaller
`--test-stride` (default window-size/4, ~4x) — deliberately less overlap than
training, so the test set isn't mostly near-duplicate crops of a handful of held-out
files (that would inflate its apparent size without adding real independent
evidence). Regenerated + re-uploaded: **2936 samples (2595 training / 341 testing)**,
up from the original 433 non-augmented ones. `ei_upload.sh` no longer does any
splitting itself — it just uploads whatever's under
`prepared/{training,testing}/<label>/` to the matching EI category.

**Attempt 4 — retrained on the file-split, augmented data.** Validation accuracy:
**97.7%**, suspiciously perfect (F1=1.00 on `Offset_Pulley`). **Model Testing (the
`testing` category, evaluated separately): 42.52%.** Another big validation-vs-test
gap — but this time it's a *different* leak: our training windows now overlap ~87.5%
with their neighbors (stride 128 of a 1024 window), and Edge Impulse's automatic
train/validation carve-out **inside** the `training` category splits at the window
level too, so near-duplicate overlapping windows from the same file can land on both
sides of *that* internal split. The model doesn't have to generalize to ace EI's
reported "validation accuracy" — it just has to recognize an 87.5%-identical twin of
something it trained on.

**The number that matters: 42.52%, and it's trustworthy.** Unlike validation, Model
Testing evaluates against the `testing` category, which was never part of `training`
in any capacity (not the weights, not EI's internal validation carve-out) — so this
figure has no leakage path available, regardless of how "training" gets carved up
internally. **From now on, ignore the Validation accuracy EI reports during
training/EON Tuner — it's inflated by the overlap-leakage above and not a useful
signal. Only trust the separate Model Testing page.**

**What 42.52% (barely above the 25% random baseline for 4 classes) actually means:**
this isn't a pipeline bug anymore — the split is now provably clean. It means a plain
dense net over the raw 512-value spectrum is a hard problem given how little
*independent* data really exists (~150 distinct physical recordings total, ~120 of
them in training, no matter how many correlated overlapping windows get generated
from them). Confusion matrix still leans toward `Wear`/uncertain for `Cracking` and
`Ideal` — the same majority-class-ish bias as attempt 2, just clearer now that
leakage isn't muddying the picture.

**Next session, not yet done:**
- **Band-power feature aggregation** (most promising next lever, not yet
  implemented): aggregate the 512 raw magnitude bins into ~32 band-power features
  (sum/mean magnitude per frequency band) computed ourselves in `ei_dataset_prep.py`,
  directly on the already-FFT'd spectrum. Keeps the physically meaningful "where is
  the energy concentrated" shape (unlike Flatten's whole-spectrum stats, attempt 1)
  while cutting dimensionality ~16x — a much better match for ~120 independent
  training files. This is *not* the same as re-enabling EI's Spectral Analysis DSP
  block, which would re-FFT an already-FFT'd spectrum (double-transform, meaningless)
  — the aggregation needs to happen in our own script, on data that's still
  conceptually "one spectrum snapshot."
- **Dial training `--stride` back down** (less overlap) purely so EI's own
  Validation accuracy becomes trustworthy again during quick iteration — right now
  it's unusable as a signal, so every change requires a full Model Testing run to
  judge. Cheap, orthogonal to the band-power change.
- Re-run Model Testing after each change; that page is the only number to trust.

---

## 4. Deploying Model B into the MPU pipeline

All new work is MPU-side Python; the MCU/SPI transport is untouched.

### 4.1 New: `pipeline/classifier.py`
A thin wrapper that loads the model once and scores a feature vector:

```python
# recommended path: TFLite, self-contained, no subprocess
class FaultClassifier:
    def __init__(self, model_path: str, labels: list[str]): ...
    def classify(self, vector: tuple[float, ...]) -> dict[str, float]:
        """feature vector -> {label: probability}, argmax = predicted fault"""
```
Backed by `tflite-runtime` (or `ai-edge-litert`). *Alternative path* would wrap
`edge_impulse_linux.runner.ImpulseRunner("fault_classifier.eim")` instead — same
`classify()` signature, so nothing else changes.

Add the dep to [requirements.txt](../base-station/python/requirements.txt)
(`tflite-runtime` **or** `edge_impulse_linux`).

### 4.2 Registry ([registry.py](../base-station/python/registry/registry.py))
Add two fields to `RegistryEntry`:
- `classifier_enabled: bool = False` — set true for satellite/MQTT nodes (or an
  explicit dashboard toggle). Keeps local SPI motors on anomaly-only.
- `last_classification: Optional[dict] = None` — `{label, confidence, ts}` for the
  Fleet card, mirroring how `last_anomaly_score` already works.

### 4.3 Pipeline ([manager.py](../base-station/python/pipeline/manager.py) / [inference.py](../base-station/python/pipeline/inference.py))
In `MotorPipeline.handle_frame`, after the autoencoder score, if
`classifier_enabled` and the gate says RUNNING, run `FaultClassifier.classify(vector)`
on the **same `vector`** the autoencoder scored. Record top label+confidence to the
registry + history, and fire an `on_classification(node_id, ts, label, confidence,
all_scores)` callback (parallel to the existing `on_score`). The classifier is a
process-wide singleton injected into `PipelineManager` (like `history_store`), not
per-node.

### 4.4 API + broadcast ([app.py](../base-station/python/api/app.py) / [main.py](../base-station/python/main.py))
Add a `classification` WebSocket message type next to the existing `anomaly` /
`spectrum` broadcasts:
```json
{"type":"classification","node_id":"a4cf12","timestamp":...,
 "label":"bearing","confidence":0.92,
 "scores":{"bearing":0.92,"misalign":0.05,"wear":0.03}}
```
`GET /nodes` already serializes the whole entry, so `last_classification` rides along
for free.

---

## 5. Dashboard changes ([frontend/](../base-station/python/frontend/))

The dashboard is vanilla JS (no build step), a fleet list of expandable node rows,
each with spectrum/waterfall/anomaly Plotly charts ([app.js](../base-station/python/frontend/app.js),
[charts.js](../base-station/python/frontend/charts.js)). Minimal, additive changes:

1. **Enriched status pill.** For classifier-enabled nodes in warning/fault, render
   `FAULT · Bearing (92%)` instead of bare `Faulty` (`statusLabelFor()` in app.js).
2. **New "Diagnosis" panel** in the expanded detail (next to Node ID / Anomaly
   score): the predicted fault **label + confidence**, a small **per-class
   probability bar chart** (reuse Plotly), and a short **recent-diagnosis history**.
3. **Live push.** Handle the new `classification` WS message the same way `anomaly` is
   handled — append to the node's diagnosis timeline without waiting for the 5s poll.
4. **Optional "actual vs predicted."** If the sim also sends the ground-truth label
   (§7), show it beside the prediction — a compelling, honest demo of the model being
   right (or occasionally wrong) in real time.

Local SPI motors simply don't get the Diagnosis panel (no `classifier_enabled`), so
the UI degrades cleanly.

---

## 6. AWS IoT Greengrass (stretch — extra competition points)

Greengrass v2 is AWS's **edge runtime**: it runs on the device, deploys/updates
**components** (code, containers, ML models) OTA from the cloud, and bridges local
MQTT to **AWS IoT Core**. Our system already speaks MQTT, which makes this additive
rather than a rewrite. Three angles, cheapest first:

1. **MQTT bridge + cloud telemetry (achievable core).** Run Greengrass on the base-
   station MPU (or a companion Linux box). Use the stock **MQTT bridge component** to
   forward our `epm/#` topics ↔ AWS IoT Core, and point
   [mqtt_subscriber.py](../base-station/python/ingestion/mqtt_subscriber.py) at
   Greengrass's local broker (Moquette) instead of bare Mosquitto — a config change,
   not code. Publish each node's **status + anomaly score + fault classification** up
   to IoT Core so a cloud view aggregates multiple base stations. Demoable with just
   the AWS IoT MQTT test client.
2. **EI model as a Greengrass ML component (flashy stretch).** Package the Edge
   Impulse model (the `.eim` from §3.1's alternative path deploys most cleanly here)
   as a **managed Greengrass component** and push model updates from the cloud to the
   edge OTA — "retrain in Edge Impulse → one-click redeploy to every base station."
   Edge Impulse documents a Greengrass deployment path; this is the highest-wow item.
3. **Cloud dashboard / fleet-of-fleets.** IoT Core rule → Timestream/DynamoDB → a
   small cloud dashboard, so judges see edge *and* cloud. Optional; #1 already gets
   the "cloud connected" points.

**DECISION (locked): scope = #1** (MQTT-bridge + cloud telemetry — real, low-risk,
reuses the MQTT we already have). **#2 (OTA model component) is a stretch to attempt
only if time allows**, and if we do it we'd switch to the §3.1 `.eim` alternative
since it packages more cleanly as a Greengrass ML component. #3 optional. Greengrass
is strictly additive — none of §§3–5 depend on it, so it can be cut entirely without
touching the core demo.

---

## 7. The live demo story (what judges see)

1. Base station MPU running `main.py --mqtt-host localhost`; dashboard open.
2. Start 2–3 `satellite_node_sim.py` instances (each its own UI). They appear on the
   dashboard automatically as **New** nodes.
3. **Commission** one on healthy data (Record → collect → Train) — Model A learns its
   baseline; node goes **Healthy**. (Shows on-device unsupervised training.)
4. Switch that node's file from healthy → a fault file. Model A's anomaly score
   climbs → node flips to **Warning/Fault**, and the **Diagnosis panel names the
   fault** (`Bearing 0.92`) — Model B kicking in. The status LED (pushed back over
   MQTT) turns red. This is the money moment: **detect + identify, live.**
5. Cycle through 2–3 fault files; the diagnosis label tracks each one.
6. (If Greengrass) show the same status/diagnosis arriving in the **AWS IoT** console.

**To script the healthy→fault switch cleanly:** add a small `--scenario` mode (or a
UI button) to `satellite_node_sim.py` that auto-advances files on a timer, and
optionally publishes the **ground-truth label** alongside the spectrum so the
dashboard can show *actual vs predicted*.

---

## 8. Task checklist / suggested order

- [x] **T0 — Kaggle labels enumerated** → `Ideal` (healthy) / `Cracking` /
      `Offset_Pulley` / `Wear`; accel-only, axis-agnostic. *(done 2026-07-15)*
- [x] **T1 — `tools/ei_dataset_prep.py`**: window + FFT (reuse sim's `compute_spectrum`)
      + normalize + emit labeled EI upload files. *(done 2026-07-16, reworked
      2026-07-17 to split at the file level + separate train/test strides — see §3.3.
      Currently 2936 samples: 2595 training / 341 testing.)*
- [~] **T2 — Edge Impulse**: create project (done, ID 1060830), upload (done via
      `ei_upload.sh`), design impulse (Raw Data→Classification, not Flatten — see
      §3.3 attempt 1), train (done, several iterations), read confusion matrix (done),
      **model-test: done, honest accuracy is 42.52%** (see §3.3) — not yet good enough
      to export/deploy. Next session: band-power feature aggregation (§3.3), not yet
      built. Export TFLite + model card come after accuracy is actually acceptable.
- [ ] **T3 — `pipeline/classifier.py`** (`FaultClassifier`, TFLite) + `requirements.txt`
      dep + drop model into `.cache/models/`.
- [ ] **T4 — Registry fields** `classifier_enabled` / `last_classification`.
- [ ] **T5 — Pipeline wiring**: run classifier on the same feature vector; `on_classification`
      callback; history record.
- [ ] **T6 — API/WS**: `classification` broadcast message.
- [ ] **T7 — Dashboard**: enriched status pill + Diagnosis panel + WS handling.
- [ ] **T8 — Sim `--scenario`** auto-switch + optional ground-truth label.
- [ ] **T9 — Demo dry-run** end-to-end on the real board.
- [ ] **T10 (stretch) — Greengrass** MQTT bridge + cloud telemetry (§6 #1).
- [ ] **T11 (stretch) — EI `.eim` as Greengrass ML component** (§6 #2), incl. the
      raw-window alternative path (§3.1) if we go that way.

---

## 9. Decisions — RESOLVED (2026-07-15)

All four were left to me to decide; here's the call and why.

1. **Feature representation → spectrum-as-features + TFLite (§3.1).** Least friction,
   train/serve feature parity with the existing pipeline, works identically for
   satellite and local nodes, and self-contained inference in the locked-down App Lab
   container. EI still does all the ML work, so the "we used Edge Impulse" story is
   intact.
2. **Class list → `Ideal` / `Cracking` / `Offset_Pulley` / `Wear` (§2).** Read
   directly off the dataset on disk. `Ideal` = healthy. 4-class, accel-only,
   axis-agnostic.
3. **Greengrass → MQTT-bridge + cloud telemetry committed; OTA component is a
   time-permitting stretch (§6).** The bridge is low-risk and reuses our MQTT; OTA is
   the flashy-but-optional extra.
4. **Classifier scope → satellite (Kaggle) nodes only.** Local SPI motors stay
   anomaly-only — the honest framing, since the Kaggle signatures don't match your
   bench hardware. This is why `classifier_enabled` is a per-node flag (§4.2).

Ready to start at **T1** (the prep script) whenever you want to proceed.

---

## Appendix — where each change lands in the existing code

| Concern | Existing file | Change |
|---|---|---|
| Feature vector (shared by A & B) | [pipeline/features.py](../base-station/python/pipeline/features.py) | reuse as-is |
| Anomaly model A | [pipeline/autoencoder.py](../base-station/python/pipeline/autoencoder.py), [inference.py](../base-station/python/pipeline/inference.py) | unchanged |
| **Classifier B** | — | **new** `pipeline/classifier.py` |
| Per-node routing | [pipeline/manager.py](../base-station/python/pipeline/manager.py) | call classifier after AE score |
| Node metadata | [registry/registry.py](../base-station/python/registry/registry.py) | `classifier_enabled`, `last_classification` |
| REST/WS | [api/app.py](../base-station/python/api/app.py), [main.py](../base-station/python/main.py) | `classification` broadcast |
| Dashboard | [frontend/app.js](../base-station/python/frontend/app.js), [charts.js](../base-station/python/frontend/charts.js) | status pill + Diagnosis panel |
| Satellite sim | [tools/satellite_node_sim.py](../base-station/python/tools/satellite_node_sim.py) | `--scenario` auto-switch + optional label |
| Data prep | — | **new** `tools/ei_dataset_prep.py` |
| MQTT ingestion (Greengrass) | [ingestion/mqtt_subscriber.py](../base-station/python/ingestion/mqtt_subscriber.py) | point at Greengrass broker (config) |
