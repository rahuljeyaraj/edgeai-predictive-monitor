# EdgeAI Predictive Monitor — Dashboard Redesign Spec

Status: draft, ready for Phase 0 discovery review before implementation.
Supersedes the Gemini-generated PRD in places — see Section 9 for the diff.

---

## 1. Terminology

- The Arduino UNO Q node is called **base station**, not "hub." Use this term everywhere — UI copy, code, docs.
- Satellite nodes remain **satellite** (ESP32-C3 / ESP32-S3 XIAO).

---

## 2. Core visual rules (ISA-101, unchanged from original spec)

- Dark charcoal/slate base palette (`#020617` to `#0f172a`).
- 3-color rule, reserved strictly for health and lifecycle meaning:
  - Emerald green — healthy / optimal.
  - Amber — warning / degrading.
  - Crimson red — critical fault / active alert (pulse or flash).
  - Blueprint blue — calibration / training lifecycle phase.
- All telemetry (scores, frame counts, raw metrics) uses a monospace font with tabular numerals — no layout shift on rapid updates.

---

## 3. Navigation & layout

**Structure: sidebar nav**, not tabs. Chosen for room to grow and familiarity with other SCADA-style tools.

Sidebar sections:
- **Fleet** — asset grid overview (replaces "Fleet Matrix" from the original spec).
- **Diagnostics** — per-asset deep dive, including commissioning progress for whichever node is being calibrated.
- **Settings** — placeholder for now. Future home for retention window, MQTT broker config, etc. No functionality required at launch.

**Global footer** (not tied to any sidebar section): MPU system performance monitor, always visible.

---

## 4. Data ingestion layer (unchanged from original spec)

- Primary: WebSocket (`/ws`) for continuous real-time push.
- Fallback: REST polling `GET /nodes` every 5000ms, for zero-touch discovery of newly auto-registered nodes.
- Note: routes are bare paths (`/nodes`, `/ws`, `/perf`, `/config`), not prefixed with `/api/`. The original spec's `/api/` wording was documentation shorthand and does not reflect the real routes — corrected here, see Section 9.

---

## 5. Component specs

### 5.1 Fleet card

Each asset card shows:

| Element | Spec |
|---|---|
| Asset identity | Hardware ID, editable custom tag, platform (base station / ESP32-S3 satellite) |
| Anomaly index | Single float — the combined autoencoder reconstruction loss. Per-channel breakdown is **not** shown here; it lives in Diagnostics. |
| Operational state | Color-coded per Section 2. Must render all states including the two commissioning sub-states (see Section 6) — both blueprint blue, with a text label distinguishing "Collecting" vs "Training." |
| Control console | Context-sensitive action button (Start Calibration → Stop & Train, depending on sub-state), rename, pause, jump to Diagnostics for this asset. |

Note: `OFFLINE` continues to be frontend-computed (via a heartbeat-style check), not pushed by the server — consistent with current MPU behavior. No change needed here.

### 5.2 Diagnostics view

Two tabs within the Diagnostics section: **Live** and **History**.

**Live tab:**
- Stacked FFT subplots, one row per active sensor channel, shared frequency x-axis.
- Channel count is **dynamic**, not hardcoded to mic + accel. Pulls from `sensor_config`, which is already generic on the backend and already exposed in `RegistryEntry.to_dict()` — no new endpoint needed for this part.
- For how channel data flows end-to-end (including the satellite-side gap), see Section 7.

**History tab:**
- Waterfall spectrogram, heatmap style: frequency on x-axis, time on y-axis, color intensity = amplitude.
- All channels stacked, one heatmap per channel (same channel list as Live tab).
- Shared color legend (single amplitude scale computed across all channels' buffered rows).
- Stays client-side buffered — no new backend storage for raw spectrum bins.

**During `COMMISSIONING_TRAINING`:** the Live tab's FFT view is replaced entirely by a training progress panel (see Section 6). There's no new live data to show during training — it's fitting on an already-collected batch — so showing a "live" graph at that point would be misleading.

**Asset calibration profile** (unchanged from original spec): inline rename panel, `POST /nodes/{id}/rename`.

### 5.3 Footer — MPU system performance monitor (unchanged from original spec)

Fixed, collapsible. Tracks the QRB2210 host, not any individual node:
- Gateway compute load (CPU %, per-core).
- Storage memory pool (RAM used / total, MB).
- Active stream ingestion rate (FPS) — separate figures for **MQTT** (satellites) and **LPUART1** (base station). (Original spec said "BLE or serial" — corrected, see Section 9.)
- Inter-core latency (running average, ms) — labeled honestly as a proxy figure (reuses existing `avg_latency_ms`; no new instrumentation added for this).

---

## 6. Node lifecycle & state machine

Three top-level phases, matching the original spec's intent, with commissioning now split into two sub-states:

```
Pairing → Collecting → Training → Monitoring (Healthy / Warning / Fault)
```

- **Pairing** — satellite connects to WiFi, publishes to the MQTT broker on the QRB2210. Node ID is derived from the WiFi MAC address (last 6 hex chars) — no BLE, no manual flashing. Neutral/gray in the UI.
- **Collecting** (`COMMISSIONING_COLLECTING`) — technician walks the motor through real operating conditions. Live FFT feed visible. No auto-stop at a frame count — requires an explicit "Stop & Train" action. Blueprint blue.
- **Training** (`COMMISSIONING_TRAINING`) — autoencoder fits on the collected batch, fixed-epoch full-batch. Shows **real progress** (epoch X of N), since the epoch count is known ahead of time. Blueprint blue.
- **Monitoring** — active inference. The node moves freely between Healthy (green), Warning (amber), and Fault (red) based on live anomaly score — this is not a one-way progression like the earlier stages.

`COMMISSIONING_COLLECTING` and `COMMISSIONING_TRAINING` are locked as the final state names. `NodeStatus.TRAINING` is renamed (not aliased) to these two states, with all references (registry, commissioning, tests, frontend, CSS) updated together. A compatibility shim maps any legacy persisted `"training"` string to `COMMISSIONING_COLLECTING` — the safer of the two, since it forces a re-stop rather than silently claiming mid-training.

Epoch progress streams over the existing `/ws` connection as a new `training_progress` message type (no new transport needed), throttled to avoid flooding the socket on long training runs.

---

## 7. Sensor data extensibility — SensorFrame & wire protocol

Full fix, in scope for this pass. Two parts, with different levels of risk.

**MPU side (self-contained):**
- `SensorFrame` moves from fixed `mic_bins` / `accel_bins` fields to a channel-keyed structure, mirroring the `SensorChannel` config pattern already used elsewhere in the registry.
- This ripples through everywhere a frame is consumed: the spectrum WS broadcast (already channel-keyed per the implementation plan), autoencoder input concatenation, commissioning batch buffering, and history/waterfall buffering. All of these move from hardcoded two-field access to iterating over whatever channels are present.
- This part is contained entirely within `edgeai-unoq` and is low-risk.

**Cross-repo (the real work):**
- The satellite MQTT path has a documented, pre-existing gap: incoming messages carry no channel discriminator, so `normalize_spectrum_message` currently assigns everything to `accel_bins` regardless of what it actually is. A generic `SensorFrame` on the MPU side does not fix this — the data arrives already ambiguous.
- The actual fix requires adding a channel field to the wire contract itself. This is a breaking change and needs a version bump (the protocol already carries a version byte in the packet header).
- Three repos are touched: `edgeai-wire-protocol` (the contract), `edgeai-esp32` (satellite firmware — teammate-owned), `edgeai-unoq` (MPU-side parsing).
- **Required before implementation:** a Phase 0 discovery pass confirming the exact current MQTT message shape from satellites, and exactly where the version byte lives in the existing contract. This is a shared, hand-maintained contract with no codegen tooling — an incorrect assumption here means hand-syncing the wrong design across three repos.
- **Coordination note:** this touches your teammate's repo directly. Their firmware needs to know the discriminator field's design before they can emit it — this isn't something that can be finished by editing `edgeai-unoq` alone.

Recommended next step: a separate Phase 0 discovery task doc scoped specifically to the satellite MQTT message shape and the wire-protocol version byte, before any implementation work here begins.

---

## 8. Out of scope for this rewrite

- Any change to the autoencoder model type. It's autoencoder-only, fixed-epoch, full-batch — not up for reconsideration here.
- Any change to satellite pairing transport (WiFi + MQTT is confirmed, not BLE).
- Settings section functionality — placeholder only.

---

## 9. Corrections vs. the original Gemini-generated PRD

For traceability — these are the specific points where the uploaded PRD didn't match the actual system, and what replaced them:

| Original PRD said | Corrected to |
|---|---|
| Satellite nodes pair over BLE | WiFi + MQTT, node ID from WiFi MAC |
| Model: "Autoencoder or One-Class SVM" | Autoencoder only, locked decision |
| Calibration auto-stops after "a set window" | Explicit Stop & Train button, technician-controlled |
| Dual-channel FFT (mic + accel) hardcoded | N dynamic channels, driven by backend `SensorChannel` config, down to `SensorFrame` itself (Section 7) |
| Footer ingestion rate "over BLE or serial" | MQTT (satellites) / LPUART1 (base station) |
| "Hub" terminology throughout | "Base station" |
| Two-tab top-level layout (Fleet Matrix / Diagnostics) | Sidebar nav (Fleet / Diagnostics / Settings) |
| No waterfall/history view | Added: History tab with heatmap-style waterfall, all channels stacked |
| Single commissioning phase | Split into `COMMISSIONING_COLLECTING` → `COMMISSIONING_TRAINING`, with real training progress |
| Routes prefixed with `/api/` | Bare paths already in use: `/nodes`, `/ws`, `/perf`, `/config` — `/api/` was documentation shorthand, not real |

---

## 10. Open items requiring confirmation before Phase 1 implementation

Resolved since the last draft:
- ~~Which endpoint/WS field exposes a node's active `SensorChannel` set~~ — resolved: `sensor_config` is already generic and exposed in `RegistryEntry.to_dict()`.
- ~~Exact naming for the two new `NodeStatus` states~~ — resolved: `COMMISSIONING_COLLECTING`, `COMMISSIONING_TRAINING`.
- ~~Whether epoch progress needs a new transport~~ — resolved: streams over the existing `/ws` connection as a `training_progress` message.

Still open:
1. Exact current satellite MQTT message shape and wire-protocol version byte location — blocks the Section 7 channel discriminator design. Needs a Phase 0 discovery pass across `edgeai-wire-protocol` and `edgeai-esp32` before that work can start.
2. Coordination with teammate on the `edgeai-esp32` side timeline, since the discriminator field change requires their firmware to be updated too.
