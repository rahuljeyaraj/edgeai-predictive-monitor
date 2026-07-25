# Plan — Edge Impulse dashboard-driven workflow (capture → label → group → train → deploy)

Status: **Brainstorm complete (2026-07-23). Round A ("Upload") of S4 shipped
2026-07-24: connect (creates a per-device-type EI project) + upload
(pushes selected recordings) are live from the Classifier tab, tests
passing, live smoke test against the real EI account pending. Round B
("Train/Build/Fetch", S4 steps 5-9) built 2026-07-24: Train (generate
features + train) and Fetch trained model (build + download) buttons per
device type, background job + WS "ei_progress" streaming (mirrors
commission/stop's training_progress pattern), model saved to
`<data_dir>/ei_models/<device_type>.tflite`. Tests passing; **not yet
smoke-tested against a real EI account** (same gap Round A still has --
the job endpoint shapes in `pipeline/ei_client.py` are built from EI's
documented API, unverified live). Record/train button UX (for the
capture/commission flow, not this panel) is still an open item (ideas
captured, not decided). Actually wiring the fetched model into live
per-frame classification (§6 "Runtime behavior") is a further round, not
started.**

**2026-07-25: real usage of Round A/B surfaced enough UX and correctness
problems (silent upload failures, no per-project clarity, commissioning-
dependent normalization producing inconsistent data) that the Classifier
tab UI and the upload/normalization approach got fully re-brainstormed —
see §8. §8 supersedes §3's UI shape and §4's normalization approach; §3-4
are kept as-is for history.**

**§8 implemented same day (2026-07-25):** one-card-per-asset-class UI
(`frontend/classifier.js`/`style.css`/`index.html`) with a Delete(N)/
Edit-label(N) action bar, `Upload all` (async job + WS `deleting`/
`uploading N/M` progress readout), Studio link/Unlink, and Fetch-only
model row. Backend: `EIController.upload()` rewritten around a
device_type-scoped gather + pooled per-device-type scalar-tail baseline
(train-split-only, persisted in new `pipeline/ei_scaling.py`), a new
`ei_client.delete_all_samples()` (S8.5) wipes the project before every
upload, `POST /classifier/ei/upload` moved to the background-job/WS
pattern train/fetch already used, and `POST /captures/rename_bulk` backs
the new Edit-label action. Round B's Train route/button dropped per §8.2
(`EIController.train()` itself left in place, unused, per that section's
explicit call). Model-staleness indicator (§8.4/§8.7.2) skipped per
explicit user answer this session. All Python tests green (`ei_client_test.py`,
`ei_controller_test.py`, `ei_scaling_test.py` (new), `api_test.py`) and the
UI was exercised live in a headless browser against a faked Edge Impulse
client (no real account in this environment) — selection/action-bar
counts, the deleting→uploading progress readout, and Fetch all verified
working with no console errors. **Still not verified against a real Edge
Impulse account** — same gap §3/§4 already had; the `delete-all` endpoint
shape especially should be smoke-tested early per §8.7.4. Not yet committed.

Companion to [EDGE_IMPULSE_FAULT_CLASSIFICATION_PLAN.md](EDGE_IMPULSE_FAULT_CLASSIFICATION_PLAN.md)
— that doc is the pipeline-wiring task list (T1-T11: `pipeline/classifier.py`,
registry fields, WS broadcast, dashboard panel). **This doc is the human-facing
lifecycle around it** — how a user goes from "an anomaly happened" to "the fleet
now recognizes this fault type," entirely from the dashboard, for the rig-capture
project line (not the Kaggle dataset line — see the other doc for that history).

---

## 0. The story

1. A node alerts with an anomaly score (exists today).
2. User investigates, decides the anomaly is worth capturing + labeling.
3. Data can be grouped across multiple nodes that monitor the **same kind of
   machine** — not all nodes are the same machine.
4. User pushes selected labeled captures to Edge Impulse from the dashboard.
5. User trains a classifier — also from the dashboard, no Studio visit.
6. The trained model comes straight back down onto the device (not a manual
   upload) and auto-applies to every node monitoring that machine kind.
7. Next anomaly: the classifier runs **alongside** the anomaly detector (not
   gated behind it) and names the fault type, for every node that has a model.

---

## 1. Device identity on the existing registry entry (no separate Device table)

Originally drafted as a separate Node/Device split with its own table — revised
after checking how `display_name` is actually used today. It's already a
free-text, user-editable field (`registry.py:rename()`, double-click-to-rename
in the Fleet row, defaults to `node_id` if unset) — in practice it already
functions as "which device this node is monitoring" (e.g. rename it to
`motor001`). No new field needed for that.

So the actual change is smaller than first drafted:
- **Rename `display_name` → `device_name`** — same field, same behavior,
  clearer name for what it's actually used for. Mechanical rename, call sites
  confirmed: `registry/registry.py` (`RegistryEntry.display_name`, `add()`,
  `rename()`), `api/app.py` (rename endpoint body/handler), `frontend/app.js`
  (row render + rename input + rename POST), `frontend/alerts.js` (name
  lookup for alert display).
- **Add a new `device_type: Optional[str]` field** — this is the only
  genuinely new field. No separate Device dataclass/table; both fields live
  directly on `RegistryEntry` since node:device is 1:1 in practice.

**`device_type` is the scoping key for everything downstream**: data
grouping, which Edge Impulse project data goes to, and which model gets
auto-applied at runtime. Nodes with no `device_type` assigned (or whose type
has no model) just show the anomaly score — no classification attempted.

**Cleanup while adding this:** `RegistryEntry.control_circuit_id` and
`auto_cutoff_enabled` are declared but have **zero references anywhere else in
the codebase** (confirmed via grep) — dead fields, safe to remove in the same
change.

---

## 2. Capture + label (new, separate from commissioning)

- **Manual only** — a button, not an automatic capture triggered by the
  anomaly. Distinct from the existing commission/train flow (which trains the
  autoencoder baseline and discards its input frames) — this path actually
  **persists** a labeled sample for later use.
- **Captures the already-computed feature vector**, not raw time-domain data.
  MPU already assembles spectrum bins + scalar tail
  (`pipeline/features.py:build_feature_vector()`, called live in
  `inference.py:120`) — that's exactly what gets serialized + labeled, no
  conversion step needed. (`raw_capture.py`/`raw_capture_server.py` stays a
  separate research tool for feature-selection experiments — not reused for
  this live path.)
- Has its own state: start capturing a stream, stop when done, then prompt
  for a label. A **save action** (floppy-disk icon) persists the labeled
  stream for later upload — distinct from any "recording in progress"
  indicator.
- **Decided 2026-07-24: capture does NOT require commissioning.** It has
  nothing to do with the autoencoder/thresholds -- can be started on any
  node in any registry status. Still gated on the motor-state gate
  (`MotorState.RUNNING`, own `MotorStateGate` instance) the same way
  commissioning is, since a captured sample while the motor is stopped
  carries no signature worth labeling.

---

## 3. Dashboard UI shape

- New **"Classifier" tab** (alongside Fleet/Network/Performance/Alerts —
  Network/Alerts are already placeholder stubs, so adding a tab is an
  established pattern here).
- Contents: a table of captured samples (device, device type, label,
  timestamp), checkboxes to select which to push, an "Upload selected"
  button, and a model-fetch panel sitting right next to it.
- **Revised 2026-07-24 (Round A build):** the "one static API key field"
  design above assumed a project already exists. Real EI project
  *creation* needs account-level auth, not a project key — a project
  doesn't exist yet to scope a key to. Replaced with **one row per device
  type**, each with a connected/not-connected pill and, if not connected, a
  one-time username+password(+TOTP) login form (`POST
  /classifier/ei/connect`) that creates that type's project, impulse, and
  NN config, then stores only the resulting **per-project API key**
  server-side (`<data_dir>/ei_projects.json`, 0600) — the login credentials
  themselves are held in memory for that one request and never persisted.
  This resolved cleanly because the user's EI account (Google-SSO only) had
  no native password to begin with; they set one via EI's own account
  settings first. See `pipeline/ei_client.py`/`pipeline/ei_projects.py`/
  `api/ei_controller.py` for the implementation.
- Device name / device type editing lives **near the existing Fleet
  record/capture controls**, not on the Classifier tab — it's node/device
  identity, not a classifier-workflow action.
- **One EI project per device type**, even though the model architecture is
  identical across types (only learned weights differ). Reasoning: fault
  taxonomies likely aren't actually identical across physically different
  machines even when label names look similar, and this project has already
  been burned twice by subtle data-leakage bugs from mixed/filtered data (see
  the fault-classification plan doc, §3.3). Cost of separate projects is just
  re-applying one fixed impulse template per new device type — cheap and safe
  versus a live-demo data-mixing risk. Reversible later if it becomes
  annoying at scale.

---

## 4. Full Edge Impulse automation — zero Studio visits

Confirmed via Edge Impulse's own API docs: every step below is a plain REST
call. The "pre-featured vector" decision already locked in the other plan doc
(spectrum-as-features, not raw time-series) turns out to be a great fit here
— it sidesteps the one part of impulse config (DSP block *parameters*, e.g.
frequency ranges) that's hardest to drive blindly from an API, since EI's
`"features"` input block type exists specifically for "don't reprocess this."

| Step | Endpoint |
|---|---|
| 1. Create project (per device type) | `POST /v1/api/projects/create` — can return a scoped API key in the same call |
| 2. Create impulse | `POST /v1/api/{projectId}/impulse` — fixed template: `"features"` input block + passthrough DSP + `"keras"` learn block, same JSON reused for every device type |
| 3. Set NN architecture/training config | `POST /v1/api/{projectId}/training/keras/{learnId}` — layers, epochs, learning rate, batch size; same fixed template reused per device type |
| 4. Upload labeled samples | Ingestion API (already used by `tools/ei_capture_upload.py`/`ei_upload.sh`) |
| 5. Generate features (DSP) job | `generate_features_job` (Python SDK) → returns job id |
| 6. Train | `POST /v1/api/{projectId}/jobs/train/keras/{learnId}` — progress streamed over WebSocket (the one legitimate use of EI's WS in this whole flow: live training logs, not data — EI's own docs say the WS "remote management protocol" is explicitly **not** for data ingestion) |
| 7. Poll job | `GET /v1/api/{projectId}/jobs/{job_id}/status` (or `/stdout`) |
| 8. Build deployable model | `POST /v1/api/{projectId}/jobs/build-ondevice-model`, `engine: tflite` (confirms the already-locked TFLite decision — not EON compiler, not `.eim`) |
| 9. Download it | Deployment download endpoint → ZIP containing `trained.tflite` |

Steps 1-3 are the same fixed JSON body every time a new device type is added
— only the project id changes. This means "add a new device type" could
itself become a single dashboard action later, though the first version of
this can just script it manually per new type.

**Round A ("Upload," steps 1-4) shipped 2026-07-24** — `POST
/classifier/ei/connect` (steps 1-3, idempotent per device type) and `POST
/classifier/ei/upload` (step 4). One correctness issue found and fixed
during the build: `pipeline/capture.py` stores the *raw*
`build_feature_vector()` output, but live inference
(`pipeline/inference.py`) always standardizes the scalar tail against that
node's own commissioned baseline before scoring. Uploading raw vectors
would have trained the classifier on a different distribution than it
sees at runtime — the same class of leakage bug this project has hit
twice before. Fixed by standardizing at upload time using each capture's
own node's `scalar_mu`/`scalar_sigma` (falls back to raw, with a surfaced
warning, for a capture from a node that was never commissioned, rather
than blocking the upload).

**Round B ("Train/Build/Fetch," steps 5-9) shipped 2026-07-24.** Two new
per-device-type buttons on the Classifier tab's Edge Impulse panel:
- **Train** — `POST /classifier/ei/train {device_type}`. Runs step 5
  (`generate-features` job) then step 6 (`train/keras` job),
  `EIController.train()` blocking the calling thread while it polls each
  job via `ei_client.wait_for_job()`.
- **Fetch trained model** — `POST /classifier/ei/fetch_model
  {device_type}`. Runs step 8 (`build-ondevice-model` job, `engine:
  tflite`) then step 9 (download the deployment ZIP,
  `ei_client.extract_tflite()` pulls out the one `.tflite` entry), saving
  to `<data_dir>/ei_models/<device_type>.tflite` (overwrites any
  previous fetch for that type — same no-versioning call as
  commissioning.py's per-node `model_path`).

Both routes return `{"started": true}` immediately (409 synchronously
first if the device_type isn't linked yet, or a job's already running for
it) and run the actual work on a background `Thread`, exactly mirroring
`commission/stop`'s `stop_collecting()`/`train()` split — an EI job is
real minutes, not a request/response. Progress streams over the
dashboard's own `/ws` as `{"type": "ei_progress", "device_type", "action":
"train"|"fetch", "stage", ...}` messages (stages: `generating_features`,
`training`, `building`, `downloading`, then `done`/`error`) — **not**
Edge Impulse's own WebSocket "remote management protocol" for job logs,
which this deliberately doesn't use (would need a second WS client just
for a nicer progress string; a plain REST status poll was judged good
enough, see `pipeline/ei_client.py`'s module docstring). `GET
/classifier/ei/status` now also returns `"models"` (device_type → fetched
model's mtime, or null) and `"jobs"` (device_type → `"train"`/`"fetch"`
for whichever are currently running) so the panel reflects the right
state after a page refresh, not just while its WS connection is open.

Same live-verification gap as Round A: built and unit-tested against a
faked `ei_client` (`ei_client_test.py`, `ei_controller_test.py`,
`api_test.py` all green), but the real job endpoints
(`generate-features`/`train/keras`/`build-ondevice-model`/`jobs/{id}/status`/
`deployment/download`) and their exact response shapes are unverified
against a live EI account — expect to adjust `ei_client.py` if a real
run disagrees with the documented API. Actually loading the fetched
`.tflite` into live per-frame classification (§6) is a further round, not
started.

**Model deployment is a fetch, not an upload.** No file picker in the
dashboard — the base station calls Edge Impulse's API directly and pulls
`trained.tflite` onto the device. Since assignment is "the file exists for
this device_type," fetching **is** assigning — no separate per-node
assignment step, no picking which nodes get it. A device type with no
fetched model just means its nodes get anomaly score only, no
classification.

**Explicitly dropped:** no Model Testing accuracy number surfaced anywhere in
the dashboard. The classifier is a best-effort hint that can be wrong — that
was an explicit call, not an oversight.

**Revised 2026-07-24 (later same day):** two follow-ups from live use.
1. Project naming changed from `"EdgeAI - {device_type}"` to
   `"edgeai-predictive-monitor-{device_type}"` (matches the repo name).
2. Renamed the whole connect/connected vocabulary to **link/linked**
   throughout (`connect()` → `link()`, `POST /classifier/ei/connect` →
   `/classifier/ei/link`, the `{"connected": true}` response key → `{"linked":
   true}`, the dashboard pill/button text) — in Edge Impulse's own
   terminology "connected" specifically means a device is live on the
   ingestion WebSocket streaming data for inference, which this feature
   never does; reusing that word for "a Studio project exists for this
   device type" was a false claim about a live data connection.
3. Added **unlink** (`POST /classifier/ei/unlink`, `EIController.unlink()`,
   `ei_projects.remove_project()`): drops the locally-saved project_id/
   api_key for a device type without calling EI's API. Covers the case
   where the Studio project was deleted by hand — previously there was no
   way to recover, since `link()` treats any saved mapping as already-done
   and silently no-ops. A later `link()` after unlinking creates a brand
   new project. The dashboard's "Linked" pill now also links out to the
   real Studio project (`https://studio.edgeimpulse.com/studio/<project_id>`,
   from the new `GET /classifier/ei/status`'s `project_ids` field) and
   shows an "Unlink" button next to it.

---

## 5. Clustering / feature-explorer visualization — bring the data in

`GET /v1/api/{projectId}/training/keras/{learnId}/data-explorer/features`
returns the raw UMAP-projected coordinates (x/y per sample + label + sample
id/name) as plain JSON — **not** a rendered image. That makes "pull it into
our dashboard" the easier option, not the harder one: it's just another data
source to scatter-plot with the charting stack already used elsewhere
(Plotly, same family as the anomaly chart / 3D ridgeline work) — no
iframe/screenshot of Studio needed, and it keeps the zero-Studio-visit story
intact. Recommendation: fetch this after each training job, render as a
native chart on the Classifier tab.

---

## 6. Runtime behavior

- Classifier runs **every frame, in parallel with the autoencoder** — both
  always-on independently whenever a model exists for that node's device
  type. **Not** gated behind an anomaly trigger (an earlier draft of this
  plan assumed cascade-on-anomaly; corrected — parallel execution is the
  locked behavior). Continuous load also serves the "show the whole hardware
  in use" demo goal better than occasional spikes would.
- **GPU note:** the existing autoencoder runs on CPU-only PyTorch
  (`requirements.txt` pins the `cpu` wheel explicitly) — nothing in this app
  currently drives the GPU; `monitoring/gpu_perf.py` only *reads* a busy%
  counter. TFLite's GPU delegate is a real lever to actually light that
  counter up, since the classifier is new code free to pick a different
  runtime than the CPU-locked autoencoder — but **unverified** whether the
  GPU delegate actually works on this board's OS image. Worth a quick spike
  before betting the demo story on it.

---

## 7. Open items

1. **Record/train button UX** — the existing two-icon commissioning control
   (`commission_start` / `commission_stop`, `frontend/app.js`) is "not
   intuitive": pressing the "train" icon actually means "stop collecting AND
   train," but the two icons read as independent buttons, and readiness today
   is only a disabled→enabled flip (easy to miss). Ideas captured, not
   decided:
   - **Leaning option:** collapse into a single morphing button — record icon
     idle → stop-square with a progress ring (filling toward `min_frames`)
     while collecting → spinner while training → checkmark, fading to a small
     "Recommission" affordance. Removes "which button, is it ready" entirely
     instead of just decorating the current two-button layout.
   - Alternative, less disruptive: keep both icons but add a visible 2-step
     sequence indicator (① Record → ② Train, current step lit) so it reads
     as one flow instead of two independent controls.
2. ~~Does capture+label require a commissioned node first, or can it run on an
   uncommissioned one?~~ **Resolved by §8**: capture never needed
   commissioning (confirmed by reading `capture.py` — it only needs
   sensor_config/input_dim, never touches standardize_scalars()), and after
   §8's normalization rework, upload doesn't need it either. Nothing in this
   flow depends on commissioning status anymore.
3. NN architecture specifics (layer sizes) for the shared/fixed template —
   not chosen yet, just confirmed to be configurable via the Keras settings
   endpoint.

---

## 8. Classifier tab UI redesign + upload/normalization rework (2026-07-25 — spec for next session, not yet implemented)

Brainstormed live after real usage of Round A/B surfaced several problems:
upload fails with no logs/progress, no way to tell which EI project a
selection uploads to, no filter on the recordings table, meaningless box
labels, awkward select-all placement, and the EI panel reads as clutter.
Also re-examined scalar-tail normalization (§4's "standardize at upload
time using each capture's own node's `scalar_mu`/`scalar_sigma`") and found
a real design gap: it silently produces inconsistent data across nodes and
depends on commissioning, which capture/upload were never supposed to
require. This section supersedes §3's UI shape and §4's upload/
normalization approach; §3-4 are left as-is above for history (what Round
A/B actually shipped).

### 8.1 Why — the guiding reframe

This tab's only job: get recorded data into the right Edge Impulse project.
Everything after that (DSP tuning, training, deploying) happens in Edge
Impulse Studio, not here. Confirmed several times over the course of this
brainstorm that trying to make EI Studio do part of our job (its
"Normalize features" DSP toggle, multi-axis input restructuring) costs more
than it saves — see 8.4.

### 8.2 UI shape — one card per asset class, table lives inside it

No more global recordings table + separate disconnected EI status panel.
One card per `device_type`, each a self-contained unit:

```
┌─ Bearing ──────────────────────────────── Linked ✓ ─┐
│ [ Delete (3) ]  [ Edit label (3) ]                    │
│ ┌──┬────────┬─────────────┬────────┬──────────┐      │
│ │☐ │ Node   │ Label       │ Frames │ Recorded │      │
│ ├──┼────────┼─────────────┼────────┼──────────┤      │
│ │☑ │ node-3 │ healthy     │  128   │ Jul 20   │      │
│ │☑ │ node-3 │ bearing_flt │  128   │ Jul 21   │      │
│ │☑ │ node-5 │ healthy     │  128   │ Jul 22   │      │
│ └──┴────────┴─────────────┴────────┴──────────┘      │
│                                                        │
│ [ Upload all (42) ]                                   │
│                                                        │
│ [ Open in Edge Impulse Studio ↗ ]        Unlink       │
│ Model: fetched Jul 24, 3:12pm  [ Fetch again ]        │
└────────────────────────────────────────────────────────┘
```

Not-linked card: same table (rename/delete are local-only, work with no EI
project), `[ Link to Edge Impulse ]` button in place of Upload, no
Open-in-Studio/Model row.

Orphaned device types (a capture's `device_type` no longer exists on any
fleet node) keep their own de-emphasized, delete-only card — unchanged from
§3's original design.

Specific decisions baked into this layout:
- No separate "select all" control — the table header's own `☐` covers it.
- Per-row action icons (pencil/trash) are gone. One selection mechanism for
  everything: check rows (any count, including 1), then use the action bar.
  Mirrors Edge Impulse's own sample-table UX (Delete/Edit labels buttons
  with a live count badge) rather than inventing a different pattern.
- An earlier version of this design showed an aggregate label breakdown
  (`healthy ▓▓▓▓▓▓▓▓░░ 120 recorded` with a bulk-rename pencil per label)
  instead of a row-level table. **Reversed** — went back to a plain
  per-capture table so the existing rename/delete-selected/upload
  machinery didn't need two separate code paths (label-level and
  capture-level). Don't reintroduce the bars.
- No filter UI added anywhere — each card is already scoped to one asset
  class, which was the only filter axis that mattered.
- Model row keeps only **Fetch trained model** — no Train button. Training
  is exactly the kind of EI-internal tuning work that stays in Studio;
  fetch is necessary glue (nothing else can pull the compiled artifact back
  down). Round B's Train button/route can be removed from the tab (backend
  `EIController.train()` can stay dead code or be removed — implementer's
  call).

### 8.3 Selection + Upload semantics (the big behavior change)

- Table checkboxes are **only** for `Delete (N)` / `Edit label (N)` — they
  no longer decide what gets uploaded.
- **Upload always uploads every local recording for that asset class**,
  selection state irrelevant. Reason: the new normalization (8.4) needs to
  fit mean/stdev across the whole local population for that class — a
  partial/selected upload would fit against an incomplete, inconsistent
  slice.
- **Upload always wipes the EI project first.** No separate "Replace all
  data" button (considered, then dropped in favor of folding it into the
  one Upload action) — every Upload is: delete all existing samples in the
  project (`8.5`'s new endpoint), then push every local recording fresh.
  This matches the actual expected usage pattern (confirmed by the user:
  "mostly what he will do is delete everything in remote and reupload it")
  and, as a side effect, eliminates any need to track what's already been
  sent — there's never a partial/stale remote state to reconcile against.
- Clicking Upload replaces the button with a two-stage inline progress
  readout (reuse the WS `ei_progress` job pattern already built for Train/
  Fetch — background thread, broadcast per stage):
  1. `"Deleting existing project data…"`
  2. `"Uploading… 22 / 60"` with a running ✓/✗ count, failures listed inline
     (capture id + reason) rather than one `alert()` at the end.

### 8.4 Scalar-tail normalization rework

**Dropping** the current per-node commissioning-baseline approach
(`EIController._standardize()`, §4's "standardize at upload time using each
capture's own node's `scalar_mu`/`scalar_sigma`, fallback to raw with a
warning") for the **EI upload path only** — live inference's own per-node
normalization (`pipeline/inference.py`, `pipeline/commissioning.py`) is
untouched, separate model, separate concern, do not conflate.

Two alternatives investigated and rejected first, so they aren't
re-proposed later:
- **Let EI's own "Normalize features" DSP toggle do it** — confirmed
  against docs and a live project screenshot that this normalizes the
  *entire* DSP block output, not a selectable subset of columns. Our
  impulse's "features" input block exposes exactly one axis (confirmed
  live: Studio's own "Input axes (1)" list), so there's no way to scope it
  to just the scalar tail without restructuring to a "time-series" input +
  EI's own Spectral Analysis DSP block — a much bigger change that reopens
  the "configure EI's DSP blind" risk the original `"features"` block
  choice deliberately avoided (see §4's intro paragraph).
- **Split the vector into multiple named axes at upload** (e.g. separate
  x/y/z/mic/scalar axes) to route just the scalar axis through its own
  normalized DSP block — genuinely uncertain whether the `"features"`
  input type supports multi-axis samples at all (EI's multi-axis JSON
  ingestion format is built around real time-series semantics —
  `interval_ms`, sampled-over-time — not a bag of independent precomputed
  vectors). Worth a cheap live test before ever revisiting this, but
  shelved for now in favor of the local approach below, which is known to
  work with zero uncertainty.

**New local scheme** — same shape as before (z-score the scalar tail,
spectral bins untouched, per-column mean/stdev, `spectral_dim` marks where
the tail starts, all reusing `features.py`'s existing
`standardize_scalars()`), different baseline:

1. Population: **every local capture for that device_type, every label**
   (not "healthy" — no such label is guaranteed to exist; labels are
   free-typed, `capture.py:normalize_label()` has no fixed vocabulary).
   Since Upload now always uploads everything (8.3), this population is
   just "everything about to be uploaded."
2. Pool raw vectors per label, run the existing contiguous-tail `_split()`
   per label (unchanged) → train_vectors/test_vectors per label.
3. Union **only the train_vectors, across every label** for this device
   type → the fit set. (Union the *test* vectors in too and you leak
   validation data into the scaling stats — the mistake caught mid-brainstorm.)
4. Compute mean + stdev **independently per scalar column** (~24 columns:
   6 scalars × up to 4 channels) from the fit set only — same
   `statistics.fmean`/`pstdev` pattern `commissioning.py:train()` already
   uses, just pooled across nodes/labels instead of one node's healthy
   batch.
5. Apply that one fitted mu/sigma to standardize the scalar tail of every
   vector — train and test, every label — before uploading.
6. No commissioning dependency anywhere in this path anymore. The
   "uploaded with a raw non-standardized tail, node was never commissioned"
   warning path in the current `upload()` goes away entirely — every
   upload is now standardized, always.

**Persistence**: this per-device-type mu/sigma is needed by anything that
later runs this classifier for real (train/serve skew otherwise — same bug
class as the original reason `_standardize()` existed at all). Save it
alongside the project mapping — a small store sibling to `ei_projects.json`
(same read/write-then-rename shape, no need for 0600 since it's not a
secret), written every time Upload runs. **Do not** write it into
`RegistryEntry.scalar_mu`/`scalar_sigma` — wrong scope (those are per-node,
fit at commissioning, owned by the autoencoder; this is per-device-type,
fit at upload, owned by the EI classifier — conflating them risks one
silently overwriting the other).

**Known gap, not solved here, note for whoever wires the model into real
inference later**: because Upload always wipes + fully re-fits (8.3), the
stored mu/sigma is only guaranteed to match whatever was in the *last*
Upload. If a model gets trained/fetched, then a later Upload adds more data
(recomputing mu/sigma), the already-fetched model and the freshly-stored
mu/sigma have drifted apart. Same underlying issue as "delete-all
invalidates EI's DSP/learn blocks, so a previously-fetched model is stale
the moment you Upload again" — both point at the same fix: a staleness
indicator on the Model row (e.g. "fetched Jul 24 — data has changed since,
may be stale") once Upload has run again after a fetch. Proposed, not
confirmed with the user — flag for a quick check before building it.

### 8.5 New Edge Impulse API call needed

`ei_client.py` needs one more function, same shape as everything else in
that module (plain `x-api-key` POST, no new dependency):

```
POST {STUDIO_BASE}/api/{project_id}/raw-data/delete-all
headers: {"x-api-key": api_key}
body: none
response: {"success": bool, "error": str (optional)}
```

Deletes all samples across all categories for the project and invalidates
its DSP/learn block state (features + trained model) — not deleted from
EI's cold storage, but a clean wipe from Studio's perspective. Unverified
against a live account, like every other job endpoint in this module (see
its module docstring) — same "expect to adjust if a real run disagrees"
caveat applies.

### 8.6 Explicitly ruled out (so these don't get quietly reintroduced)

- Selection-scoped upload, single or multi-asset-class — replaced by
  always-upload-everything (8.3).
- Tracking "sent vs not sent" per capture — no tracking at all; local disk
  is the source of truth, remote is disposable (8.3).
- A separate "Replace all data" button distinct from "Upload" — merged
  into one Upload action (8.3).
- Per-node commissioning baseline for EI upload normalization — replaced
  by the pooled per-device-type baseline (8.4).
- EI's own "Normalize features" DSP toggle — investigated, doesn't fit our
  vector shape (8.4).
- A "healthy"-labeled baseline for normalization — no such label is
  guaranteed to exist; replaced by whole-population pooling (8.4).
- Train button on this tab — dropped, only Fetch stays (8.2).
- Aggregate per-label bars view with bulk-rename-by-label — reversed back
  to a plain per-capture table (8.2).
- A collapsed "▸ manage recordings" drill-down separate from the main
  card view — reversed; the table is inline/always-visible now, there's no
  separate summary-vs-detail split anymore.

### 8.7 Open items for next session

1. Bulk "Edit label (N)" needs either a real bulk-rename endpoint
   (multiple capture ids + one new label in one call) or a client-side
   loop calling the existing single-id `POST /captures/rename` per
   selected row — not decided, implementer's call.
2. Model-staleness indicator (end of 8.4) — proposed during the brainstorm,
   never explicitly confirmed with the user. Check before building.
3. Whether the Link (username/password/TOTP) form should move into a modal
   instead of the inline expansion it uses today — mentioned once in
   passing, not settled.
4. Nothing in 8.3-8.5 has been tested against a live EI account — same gap
   Round A/B already had (see §4). The `delete-all` endpoint shape
   especially should be smoke-tested early, since every Upload now depends
   on it working correctly (a failed delete followed by a successful
   upload would double up data in the project).
