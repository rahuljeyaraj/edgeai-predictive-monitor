# MPU Software Architecture — EdgeAI Predictive Monitor (EPM)
**Team DragonWing — QRB2210 Linux-side Design**

Status: Design phase. No implementation yet. This document defines
features, architecture, and the milestone plan for all QRB2210-side
(MPU-side) code — multi-motor ingestion, per-motor autoencoder pipelines,
commissioning, dashboard backend + frontend, performance monitoring, and
(lower priority) physical actuation.

---

## 1. Scope

This document covers everything that runs on the QRB2210 Linux side of the
Arduino UNO Q base station, plus the browser-side dashboard it serves. It covers:

- Ingesting sensor data from the UNO Q base station (LPUART1) and ESP32 satellite
  nodes (WiFi/MQTT)
- Per-motor autoencoder pipelines (gating, features, training, inference)
- A registry of known motors/nodes and their configuration
- A dashboard backend (API) and frontend (UI) for technicians
- Performance monitoring of the MPU itself
- Physical motor cutoff via a separate control circuit (lower priority)

It does **not** cover:
- MCU-side firmware (accel/mic sampling, FFT, framing) — see
  `MCU_Software_Architecture.md`
- ESP32 satellite node firmware — owned by teammate, documented separately
- The exact byte-level satellite wire format — assumed to live in
  `edgeai-wire-protocol` (to be confirmed, see §8)

---

## 2. Design principles carried over from discussion

These are decisions already made; later sections build on them rather than
re-deriving them.

- **N independent pipelines, one per motor.** The UNO Q base station is just one of
  these N — not special-cased. Each pipeline has its own gate, model,
  weights, and inference state.
- **Two model input sizes.** 1024-dim (mic 512 + accel 512) for
  dual-sensor motors, 512-dim for single-sensor motors. Model architecture
  is shared code, parameterized by input dim.
- **Two transports, kept separate, each fit to its link:**
  - LPUART1 (base station↔MCU, same board, point-to-point) keeps the existing
    custom binary wire protocol (`wire_protocol.py`/`.c`). Already solved
    for throughput and correctness — not being replaced.
  - WiFi satellites (many nodes → one MPU) use MQTT, since pub/sub fits a
    many-to-one network topology.
  - Both are normalized into one common in-memory frame format before
    anything downstream touches them (§4).
- **Push, not poll, for live dashboard data.** WebSocket push from backend
  to frontend, replacing the current 300ms-poll pattern in
  `spectrum_server.py`, which is the direct cause of today's choppy
  spectrum updates.
- **Registry is the single source of truth.** Node ID → name → sensor
  config → model path → status → timestamps. Every other feature (add/
  rename/remove, commissioning, dashboard display) reads or writes this
  one place.
- **Actuation is a separate control path.** Sensing nodes (base station, satellites)
  are monitoring-only and cannot cut motor power. Cutoff commands go to a
  separate motor-control circuit, not to the sensing node.
- **Priority order:** monitoring stability first. Actuation is real but
  lower priority — build and prove the sensing/inference/dashboard loop
  before wiring up cutoff.

---

## 3. Feature list

### 3.1 Frame ingestion
- Read `SPECTRUM` frames off LPUART1 continuously (base station), reusing
  `FrameParser`
- Subscribe to MQTT topic(s) for satellite node data
- Normalize both sources into one common `SensorFrame` (§4.1)
- Handle disabled-sensor case (mic or accel bin count = 0)
- Track dropped/malformed frames per source

### 3.2 Motor state gating
- Detect running vs. stopped from frame energy (or an MCU-supplied flag —
  open question, §8)
- Suppress both training and inference during stopped/transient states
- Debounce start/stop transitions to avoid flapping at the boundary

### 3.3 Feature construction
- Convert raw mic+accel bins into a model input vector
- Normalize/scale bins
- Select 512-dim or 1024-dim path based on that node's sensor config

### 3.4 Data storage
- Persist raw frames or feature vectors during commissioning (debugging,
  reproducibility, demo evidence)
- Store trained model weights per motor, keyed by node ID
- Support re-commissioning (overwrite vs. versioned — open question, §8)
- Persist anomaly-score history per motor for graphing

### 3.5 Commissioning / training workflow
- Explicit start/stop trigger, per motor (not global)
- Collect N minutes/frames of gated "running, healthy" data
- Train that motor's autoencoder, save weights
- Update registry: status, last-commissioned timestamp
- Signal completion back to the dashboard

### 3.6 Inference
- Load trained weights, run reconstruction on live gated frames
- Compute anomaly score (reconstruction error)
- Threshold → healthy / warning / fault
- Smoothing/hysteresis so one noisy frame doesn't flip status

### 3.7 Output / feedback
- Map inference result → `DISPLAY_RGB` command for that node
- Map inference result → `DISPLAY_MATRIX` text for that node
- Push same result to the dashboard over WebSocket

### 3.8 Registry & module lifecycle
- **Add:** new node appears from ingestion → shows as unnamed with raw
  node ID, sensor config auto-detected from its frames → technician names
  it and confirms sensor config → commissioning triggered explicitly
- **Rename:** editable label mapped to node ID — pure registry edit, no
  protocol/hardware implication
- **Remove:** two distinct operations —
  - *Decommission* — delete registry entry + model (node gone for good)
  - *Pause/disconnect* — mark offline, keep model + history (node
    temporarily off WiFi/power)
- **Timestamps (auto-tracked, read-only):** last-seen (derived from
  incoming frames — doubles as online/offline indicator), last-commissioned
  (derived from training runs)
- **Not building:** manually-editable "last serviced" field — a facilities
  maintenance-log feature, disconnected from the sensing/AI pipeline being
  demonstrated. Deferred indefinitely, not architecturally blocked if
  wanted later (would just be another registry field).

### 3.9 Dashboard — technician view
- **Overview screen:** grid of all motors, name, color-coded status,
  last-seen; summary counts (healthy/warning/fault/offline)
- **Per-motor detail screen**, in priority order:
  1. **Anomaly score over time**, threshold line overlaid — primary
     go/no-go view, this is the direct output of the autoencoder
  2. **Waterfall (spectrogram over time)** — drill-down/diagnostic view,
     shows a fault frequency emerging and growing over days/weeks
  3. **Live instantaneous spectrum** (mic + accel) — tertiary/debug view,
     least useful for a technician's day-to-day judgment call
  - Sensor config, commissioning status, last-seen/last-commissioned
- MPU performance panel (§3.10), footer/secondary — not the main focus

### 3.10 MPU performance monitoring
- Per-pipeline: CPU%, memory, inference latency, frame processing rate
- System-wide: same, aggregated
- Detect pipelines falling behind (frames queuing/dropping)
- Shown in dashboard (technician-visible during demo)
- **Toggleable** — must be possible to disable entirely to save CPU once N
  pipelines are running concurrently

### 3.11 Physical actuation (lower priority — build after monitoring is stable)
- Separate motor-control circuit per motor (relay), reached from MPU —
  not through the sensing node, which has no control capability
- Cutoff threshold set strictly above the fault threshold (a warning zone
  exists before hard shutdown)
- Per-motor manual override / disable-auto-cutoff toggle in dashboard
- Fire-and-forget cutoff command — no confirmation/readback loop (explicit
  simplification, not an oversight)
- Registry carries the motor↔control-circuit mapping

---

## 4. Data model

### 4.1 `SensorFrame` (common, post-normalization)
Both ingestion paths (UART, MQTT) produce this same shape before anything
downstream touches them:

| Field | Type | Notes |
|---|---|---|
| `node_id` | string/int | Identifies which motor pipeline this belongs to |
| `source` | enum | `uart` or `mqtt` — for debugging/monitoring only |
| `timestamp` | float | Local receipt time |
| `mic_bins` | float[] or None | Empty/None if mic disabled on that node |
| `accel_bins` | float[] or None | Empty/None if accel disabled on that node |

### 4.2 Registry entry (one per motor/node)

| Field | Notes |
|---|---|
| `node_id` | Raw ID from hardware (MAC, UART node ID, etc.) |
| `display_name` | Technician-assigned, defaults to raw ID until renamed |
| `sensor_config` | mic / accel / both — drives 512 vs 1024 model dim |
| `input_dim` | Derived from `sensor_config` |
| `model_path` | Path to trained weights, or null if uncommissioned |
| `status` | uncommissioned / training / healthy / warning / fault / offline |
| `last_seen` | Auto-updated on every received frame |
| `last_commissioned` | Auto-updated when training completes |
| `control_circuit_id` | Nullable — for actuation mapping (§3.11), added later |
| `auto_cutoff_enabled` | Bool, default false until actuation is built |

### 4.3 History record (per motor, time series)
- `node_id`, `timestamp`, `anomaly_score`, `status_at_time`
- Enough to render the anomaly-score-over-time graph and reconstruct
  waterfall data if raw bins are also retained during that window

---

## 5. Architecture

```
                 Dashboard Frontend (browser)
                            |
              WebSocket (live push) + REST (control)
                            |
                    Backend API Layer
      - WebSocket: spectrum, scores, perf stats
      - REST: add/rename/remove node, trigger
        commissioning, toggle monitoring, cutoff override
                            |
                   Pipeline Manager
      - owns N per-motor pipelines
      - routes each SensorFrame by node_id
          /                              \
   Motor Pipeline 1                Motor Pipeline N
   (e.g. base station)                      (e.g. satellite)
   - state gate                    - state gate
   - feature builder                - feature builder
   - autoencoder (512/1024)          - autoencoder (512/1024)
   - anomaly score                   - anomaly score
          \                              /
                   Ingestion Layer
      - UART reader (base station, wire_protocol.py)
      - MQTT subscriber (satellites)
      - both normalize -> SensorFrame (§4.1)

  Cross-cutting, used by everything above:
  - Registry (§4.2) — persisted to disk
  - History Store (§4.3) — persisted to disk
  - Performance Monitor — toggleable

  Lower priority, added after monitoring is stable:
  - Actuation Module — cutoff to motor-control circuit,
    per registry mapping, threshold + manual override
```

### 5.1 Component responsibilities

- **Ingestion Layer** — only place that touches transport specifics (UART
  bytes, MQTT topics). Everything above it only ever sees `SensorFrame`.
- **Pipeline Manager** — routes by `node_id`, does no domain logic itself.
  Creates a new pipeline the first time an unknown `node_id` is seen.
- **Motor Pipeline** — fully self-contained per motor: gate → features →
  model → score. One pipeline misbehaving must not affect others.
- **Registry** — the only writable source of truth for node metadata.
  Add/rename/remove/commission all operate here.
- **History Store** — append-only time series per node, read by the
  dashboard's graphs.
- **Backend API** — the only thing the frontend talks to. Frontend never
  touches ingestion or pipelines directly. Implemented as a single
  FastAPI app (`api/app.py`) serving REST, the `/ws` WebSocket endpoint,
  and the static frontend from one port — see
  `docs/CLAUDE_CODE_START_PROMPT_fastapi_migration.md` for the migration
  from the original hand-rolled `http.server`/raw-socket implementation
  and its port-consolidation rationale.
- **Performance Monitor** — observes pipelines and system resources,
  independently toggleable so it can be switched off under load.
- **Actuation Module** — leaf, reads pipeline status + registry mapping,
  sends cutoff — does not sit inside the pipeline itself.

---

## 6. Open questions / deferred decisions

These need answers before or during the milestones that touch them —
flagged here rather than silently assumed.

| # | Question | Affects |
|---|---|---|
| 1 | ~~Framework: PyTorch, TF Lite, or ONNX Runtime on QRB2210?~~ Resolved at M6: PyTorch. Commissioning (S3.5, M7) trains a per-motor autoencoder on the QRB2210 itself, not just inference -- TF Lite has no real on-device training story and ONNX Runtime's training support is experimental, while PyTorch gives full training + inference on arm64 Linux with no meaningful latency concern at this model size. | M6 |
| 2 | Feature window: single frame per inference, or rolling buffer of N frames? | M5 |
| 3 | ~~Motor gate: MCU-supplied flag in the frame, or Python-side energy calc?~~ Resolved at M4: no MCU-supplied flag exists on the wire (`wire_protocol.py`'s SPECTRUM payload carries only mic/accel bins) or in `MCU_Software_Architecture.md`, so `pipeline/gate.py` uses an RMS-energy threshold over whichever bins are present, with debounce. | M4 |
| 4 | ~~For single-sensor motors: is the 512-dim case always accel-only, or can it also be mic-only? (Two different 512-dim variants if so — not interchangeable.)~~ Resolved at M5: yes, MIC-only and ACCEL-only are two distinct, non-interchangeable 512-dim variants -- `sensor_config` selects which raw bins feed the vector, not just its length. Doesn't affect M6: `pipeline/autoencoder.py`'s architecture is dim-agnostic, parameterized purely by `input_dim`. | M5, M6 |
| 5 | ~~Satellite wire format — confirmed to live in `edgeai-wire-protocol`? Byte-level spec needed before M11.~~ Resolved at M11: it does not live in a separate `edgeai-wire-protocol` package -- the spec is `docs/Appendix_B_Wire_Protocol_Specification.md` S3, in this repo. `ingestion/mqtt_subscriber.py` implements it: topic `epm/<node_id>/data`, a `[TYPE: 1B][PAYLOAD]` binary envelope (only `SPECTRUM` becomes a `SensorFrame`; other types are silently skipped, same as UART's non-SPECTRUM types). ~~At M11 this used a JSON envelope with sparse peak data (`{freq, mag}` pairs) reconstructed into dense bins.~~ Superseded post-M11: SPECTRUM now carries the same dense `spectrum_fused_payload` struct UART uses (exact float32 bins, no reconstruction) -- see Appendix B S3/S4 for why (bandwidth cost, capped spectrum) and open question #8 below, which this removes the reconstruction-fidelity half of but not the firmware `fft_size` convention half. | M11 |
| 6 | ~~Re-commissioning: overwrite existing model, or version/keep history?~~ Resolved at M7: overwrite. `model_path` is a single field, not a list, and nothing downstream reads more than the current model -- versioning would be dead weight with no consumer. `pipeline/commissioning.py` derives `model_path` deterministically from `node_id`, so re-commissioning naturally lands on the same file. | M7 |
| 7 | Actuation hardware: what does the motor-control circuit look like (relay board type, which node hosts it)? | M14 |
| 8 | Satellite `fft_size` vs. `input_dim_for(sensor_config)` (512): a real ESP32 node's wire `bin_count` (`fft_size // 2`, sent directly in the dense `spectrum_fused_payload` header since the post-M11 binary format change -- no longer peak-reconstructed) must match the fixed 512-dim `features.py` expects, or commissioning/inference (M7/M8) will raise on that node's frames. Needs either a fixed satellite-side `fft_size` convention (e.g. 1024, giving 512 bins) or a resampling step in `mqtt_subscriber.py`. Enforced (not resolved) as of the `SensorChannel` registry refactor: `pipeline/manager.py`'s `route()` now validates every frame's bin count against `input_dim_for(entry.sensor_config)` and raises `ValueError` on mismatch, so a wrong `fft_size` now fails loudly at ingest instead of silently reaching commissioning/inference -- the firmware-side convention itself is still undecided. | M7, M8, M11 |

---

## 7. Proposed repository layout

Lightweight proposal — revisit and lock at M1, same convention as
`edgeai-unoq`'s §3 (structure decided early, later milestones only fill
files in, not restructure folders).

```
edgeai-mpu/
├── ingestion/
│   ├── uart_reader.py        # wraps wire_protocol.py, base station source
│   ├── mqtt_subscriber.py    # satellite source
│   └── sensor_frame.py       # common SensorFrame type (§4.1)
├── pipeline/
│   ├── manager.py            # routes frames by node_id
│   ├── gate.py                # running/stopped detection
│   ├── features.py            # bin -> model input vector
│   └── autoencoder.py         # model def, train, infer (512/1024 dim)
├── registry/
│   └── registry.py            # node metadata store (§4.2), persisted
├── history/
│   └── store.py                # anomaly-score time series (§4.3)
├── monitoring/
│   └── perf.py                 # per-pipeline + system stats, toggleable
├── actuation/                  # added at M14, not before
│   └── cutoff.py
├── api/
│   ├── app.py                     # FastAPI app: REST routes + WebSocket
│   │                              #   endpoint (/ws), single port (see
│   │                              #   docs/CLAUDE_CODE_START_PROMPT_fastapi_migration.md)
│   ├── connection_manager.py     # tracks active WebSocket clients, broadcast()
│   └── commissioning_controller.py  # REST-layer lifecycle shim over
│                                  #   pipeline/commissioning.py sessions
├── frontend/                     # dashboard UI
└── main.py                        # wiring only, no business logic
```

---

## 8. Milestones

**Implementation proceeds one milestone at a time.** Each milestone is
built, run, and independently verified before the next begins — same
convention as the MCU-side doc. Milestones 1–3 establish the skeleton;
later milestones fill it in.

| # | Milestone | What's built | Verification method | Depends on |
|---|---|---|---|---|
| 1 | UART ingestion → `SensorFrame` | `ingestion/uart_reader.py`, `ingestion/sensor_frame.py`. Reads real `SPECTRUM` frames via existing `wire_protocol.py`, normalizes to `SensorFrame`. | Print normalized frames while base station streams; confirm bin counts/values match raw frame contents | — |
| 2 | Registry (CRUD, persisted) | `registry/registry.py`. Add/rename/remove/decommission/pause a node entry. Disk-backed. | Add, rename, pause, decommission a test entry; restart process; confirm state survives | — |
| 3 | Pipeline Manager skeleton | `pipeline/manager.py`. Routes frames by `node_id` to per-motor pipeline instances (stub pipelines that just log received frames). Auto-creates a pipeline + registry entry on first-seen `node_id`. | Feed frames from 2+ distinct `node_id`s (real base station + one synthetic); confirm each routes to its own pipeline instance, registry gains entries for both | 1, 2 |
| 4 | Motor state gate | `pipeline/gate.py`. Running/stopped detection with debounce (resolves open question #3 as part of this milestone). | Feed synthetic frames with varying energy across the stopped/running boundary; confirm gate output matches expected state transitions, no flapping | 3 |
| 5 | Feature builder | `pipeline/features.py`. Bins → normalized model input vector; 512 vs 1024 dim selected from that node's `sensor_config` (resolves open question #4). | Confirm correct vector shape/values for a dual-sensor node and a single-sensor node | 4 |
| 6 | Autoencoder model | `pipeline/autoencoder.py`. Architecture parameterized by input dim, train + save/load functions (resolves open question #1). | Train on synthetic "healthy" data; confirm low reconstruction error on similar data, high error on deliberately perturbed data | 5 |
| 7 | Commissioning workflow | Per-motor explicit trigger → collect gated healthy data → train → save weights → update registry (status, last-commissioned). | Trigger commissioning end-to-end on live or replayed data; confirm model file created, registry status/timestamp updated | 4, 6 |
| 8 | Inference loop | Load trained weights, score live gated frames, threshold + hysteresis → status. | Inject anomalous data mid-stream; confirm status transitions correctly and doesn't flap on single noisy frames | 6, 7 |
| 9 | History store | `history/store.py`. Persist anomaly score + status per node per timestamp. | Run inference for a period; query history; confirm records match observed scores | 8 |
| 10 | Backend API (WebSocket + REST) | `api/app.py` (FastAPI, single port), `api/connection_manager.py`, `api/commissioning_controller.py`. Push spectrum/scores/perf live over `/ws`; REST for registry ops + commissioning trigger. Originally built hand-rolled on stdlib `http.server`/raw sockets, migrated to FastAPI/uvicorn per `docs/CLAUDE_CODE_START_PROMPT_fastapi_migration.md` (same route contract, no frontend-visible change). | Connect a WebSocket test client, confirm live updates arrive; call REST endpoints, confirm registry changes reflected | 2, 8, 9 |
| 11 | Satellite (MQTT) ingestion | `ingestion/mqtt_subscriber.py`. Normalizes satellite data into the same `SensorFrame`, feeds Pipeline Manager identically to UART (resolves open question #5 as a prerequisite). | Publish synthetic satellite frames over MQTT; confirm a new pipeline is created and routed exactly as UART frames are | 3 |
| 12 | Performance monitor | `monitoring/perf.py`. Per-pipeline + system stats, exposed via API, toggle on/off. | Verify stats appear when enabled, disappear (and overhead drops) when disabled, values sane under multi-pipeline load | 3, 10 |
| 13 | Dashboard frontend | Overview screen, per-motor detail (score graph → waterfall → live spectrum, in that priority), add/rename/remove UI, commissioning trigger UI, perf panel. | Manual walkthrough against a running backend: add a node, rename it, commission it, watch live status/graphs update, remove it | 10, 12 |
| 14 | Physical actuation (lower priority) | `actuation/cutoff.py`. Threshold-triggered cutoff command to motor-control circuit, manual override, registry mapping. | Force a score past the cutoff threshold; confirm command is sent (mock/logged control circuit acceptable); confirm override suppresses it | 8, 2 |

**Parallelizable:** Milestones 1–2 are independent of each other. 11 can
be built in parallel with 4–9 once 3 exists (satellite ingestion doesn't
depend on the gate/model work being finished, only on the routing
skeleton). 12 can be built any time after 3, tested fully once 10 exists.
14 is intentionally last — monitoring must be stable first, per current
priority.

Each milestone produces a working, independently observable result before
the next begins.
