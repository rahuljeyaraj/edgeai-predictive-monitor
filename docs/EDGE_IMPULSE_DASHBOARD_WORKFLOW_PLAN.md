# Plan — Edge Impulse dashboard-driven workflow (capture → label → group → train → deploy)

Status: **Brainstorm complete (2026-07-23). Round A ("Upload") of S4 shipped
2026-07-24: connect (creates a per-device-type EI project) + upload
(pushes selected recordings) are live from the Classifier tab, tests
passing, live smoke test against the real EI account pending. Train/
poll/build/fetch (S4 steps 5-9) are Round B, not started. Record/train
button UX (for the capture/commission flow, not this panel) is still an
open item (ideas captured, not decided).**

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
than blocking the upload). Steps 5-9 (train/poll/build/download) are
**Round B**, not started — different mechanism (async job + WebSocket log
streaming vs. this round's plain request/response calls).

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
2. Does capture+label require a commissioned node first, or can it run on an
   uncommissioned one? (§2, still open.)
3. NN architecture specifics (layer sizes) for the shared/fixed template —
   not chosen yet, just confirmed to be configurable via the Keras settings
   endpoint.
