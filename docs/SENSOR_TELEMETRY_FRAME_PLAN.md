# Plan — Generic sensor telemetry frame format (MCU/satellite → MPU)

Status: **Design agreed 2026-07-17, not yet implemented.** This doc is the output of a
design discussion about generalizing the MCU→MPU (and future satellite→MPU) sensor
data frame so different sensor combinations and bin sizes can be experimented with
without hand-editing frame-parsing code on both ends every time. No code has changed
yet — this is what the next session implements.

Companion to [PROGRESS.md](PROGRESS.md) / [progress2.md](progress2.md) (documents the
current fixed frame format this plan replaces) and
[EDGE_IMPULSE_FAULT_CLASSIFICATION_PLAN.md](EDGE_IMPULSE_FAULT_CLASSIFICATION_PLAN.md)
(the accuracy-chasing work that motivated wanting to experiment with feature
representations more cheaply).

---

## 0. Why this exists

The EI classifier work (see the other plan doc, §3.3) is stuck at ~42% honest accuracy
and the next lever is feature-representation experiments: different bin counts,
per-axis vs combined accel spectra, added scalar features (RMS, kurtosis, ...). Today
that means editing the frame format by hand in three places — MCU firmware
([fuser.cpp](../base-station/sketch/fuser.cpp)), the SPI transport
([spi_link.cpp](../base-station/sketch/spi_link.cpp)), and the MPU parser
([features.py](../base-station/python/pipeline/features.py)) — for every combination
tried. This plan removes that cost for the experimentation phase, then collapses back
to something simple once a combination is chosen. It also anticipates satellite nodes
eventually sending real (non-simulated) sensor data over MQTT rather than SPI, using
the same payload shape.

**Explicitly not a production/deployed-system design** — this is for experimentation.
Several things a production version would need (staleness tracking for transient
missing data, a live channel-metadata registry) are deliberately left out; see §7.

---

## 1. Current state (baseline this replaces)

Today's frame (unchanged by this plan until implementation starts) is fixed and
positional:

- **Fuser payload** ([fuser.cpp:80-87](../base-station/sketch/fuser.cpp#L80-L87)):
  `fuser_frame_header` (`mic_fs, mic_fft_size, mic_bin_count, accel_fs, accel_fft_size,
  accel_bin_count` — 16 B) + `mic_bin_count` × float32 + `accel_bin_count` × float32.
  Accel bins are already X+Y+Z magnitude-summed on the MCU
  ([accel_sampler.cpp](../base-station/sketch/accel_sampler.cpp)) before this point —
  per-axis information is lost.
- **SPI link envelope** ([spi_link.cpp:87-105](../base-station/sketch/spi_link.cpp#L87-L105)):
  wraps the fuser payload with `[magic u32][seq u16][payload_len u16][payload][crc32
  u32]`, pulled in chunks via the MPU-initiated `spi_arm` RPC
  ([spi_link.cpp:479-528](../base-station/sketch/spi_link.cpp#L479-L528)).
- Bin count / fft size / sample rate are **already self-describing** (read off the
  header at runtime by `features.py`) — changing just a bin count today requires no MPU
  code change, only an MCU `#define` + rebuild. The cost is entirely in changing *which
  sensors/channels* are present, since the header hardcodes exactly "one mic block,
  one accel block."

---

## 2. Core idea: single source of truth schema

One schema file defines every channel a deployment uses, in a fixed order:

```yaml
# example — not final, edit freely during experimentation
- {channel: accel_x, kind: spectrum, bins: 128}
- {channel: accel_y, kind: spectrum, bins: 128}
- {channel: accel_z, kind: spectrum, bins: 128}
- {channel: mic,     kind: spectrum, bins: 512}
- {channel: scalars, kind: scalar_set, features: [rms, kurtosis]}
```

Both the MCU firmware build and the MPU Python pipeline are generated from (or at
minimum hand-edited *against*) this one file, so the two sides can't independently
drift out of sync the way `load_signal()` did before (see
[EDGE_IMPULSE_FAULT_CLASSIFICATION_PLAN.md](EDGE_IMPULSE_FAULT_CLASSIFICATION_PLAN.md)
§3.3 for that incident). Ideal implementation: a small script reads the schema and
emits a C header (consts for the MCU build) + a Python module (matching constants /
struct-format for the MPU parser). Minimum viable implementation: just the one file,
hand-copied into both, with a comment in each pointing back at it.

---

## 3. Two phases

### Phase A — experimentation (build this first)

A self-describing section-list payload, replacing the fixed `fuser_frame_header` +
two-block body:

```
[num_sections u8]
  repeated num_sections times:
  [source_id u8][channel_id u8][data_kind u8][section_len u16][body...]
```

- **`data_kind`** — one of three: `SPECTRUM`, `TIME_SERIES`, `SCALAR_SET`. Covers
  everything currently planned (spectra, raw windows if ever needed, RMS/kurtosis/etc
  scalars, and node performance/health metrics — perf data is just a `SCALAR_SET` with
  `channel_id = 0xFF`, no new data_kind needed). Not meant to be exhaustive forever —
  a 4th kind (e.g. spectrogram) can be added later if a real need shows up; not built
  now.
- **`source_id` / `channel_id`** — opaque numeric identity only. The protocol carries
  *no* semantic meaning ("mic" vs "accel", "X-axis" vs "Y-axis") — that meaning lives
  only in the schema file from §2, not in a runtime registry/handshake message (a live
  registry was considered and explicitly rejected as unnecessary complexity for this
  phase).
- **`SPECTRUM` body**: `fs f32, fft_size u16, bin_count u16, bins[bin_count] f32` —
  same shape as today's per-block header, just repeatable per channel instead of
  hardcoded to exactly one mic + one accel block.
- **`SCALAR_SET` body**: `count u8, ids[count] u16, values[count] f32`. `ids` are
  small integers keyed against the same schema file (e.g. `rms=1, kurtosis=2, ...`,
  perf metrics in a separate ID range e.g. `0x8000+` to avoid collision).
- **`TIME_SERIES` body**: `fs f32, sample_count u16, samples[sample_count] f32` —
  included in the enum now for completeness even though nothing emits it yet.

Effect during experimentation:
- Changing a `bin_count` → zero code changes anywhere (already true today, stays
  true).
- Changing which sensors/channels are sent (combined vs 3-axis accel, mic on/off,
  adding scalar features) → MCU side becomes a config toggle (still needs a firmware
  rebuild+reflash, but no new framing code, just choosing which section-writer calls
  run); MPU side needs **zero** code changes, since the parser loops over
  `num_sections` and dispatches purely on `data_kind` — it doesn't know or care what
  combination arrived.
- Model/training implication (separate from the transport question): a different
  sensor combination or bin count means a different feature-vector shape, which always
  requires retraining the autoencoder — no format change can make an already-trained
  model accept a new input shape. The self-describing frame only removes *parsing*
  code changes, not the retraining step, which is expected as part of the experiment.

### Phase B — finalize (do this once a combination is chosen)

Freeze the schema, then collapse the frame back to a **flat, rigid, positional
struct** sized for exactly the chosen combination — drop `num_sections` /
`section_type` / `section_len` entirely, since both sides now agree on the shape at
compile time and self-description is no longer needed. This is a return to something
like today's `fuser_frame_header` shape, just for whichever combination was decided.
Smaller frame, simpler parser, no per-section overhead — the right trade once
generality stops being needed.

---

## 4. Missing/absent channel data

- A node that **structurally lacks** a channel (e.g. a 2-axis accel node has no Z) —
  **send zero** for that slot. This is safe: it's a permanent, consistent fact true in
  both training and inference, so the model just learns that slot is always zero and
  effectively ignores it.
- A channel that **normally has data but is transiently missing one epoch**
  (bandwidth-driven skip, dropped packet) — **explicitly out of scope for this plan.**
  Zero-fill here is riskier (a zero magnitude spectrum is a real, meaningful value —
  "silence" — not a neutral "no data" marker, so it can be indistinguishable from a
  genuinely quiet reading and produce false anomaly signals or mask real ones). No
  stateful last-known-value cache or staleness tracking is being built now. Revisit
  only if this actually becomes a problem once nodes are sending real, non-simulated
  data.

---

## 5. Node/schema stability

Once a node's channel set is established, it does not change without being treated as
effectively a new node. A schema/dimension change (added sensor, reconfigured bin
count) always implies retraining — there is no mechanism, nor should there be one, that
auto-adapts an already-trained model to a new input shape. This assumption is what
makes zero-fill (§4) safe for structurally-absent channels: the "shape" of a given
node is fixed for its lifetime.

---

## 6. Transport

- **MCU (base station) → MPU**: existing SPI path stays as-is.
  [fuser.cpp](../base-station/sketch/fuser.cpp) changes to emit the new section-list
  payload instead of the fixed two-block one; [spi_link.cpp](../base-station/sketch/spi_link.cpp)'s
  outer envelope (`magic/seq/payload_len/crc32`, chunked pull via `spi_arm`) is
  unchanged — it's SPI-specific framing/integrity, orthogonal to the payload format it
  carries.
- **Satellite → MPU**: MQTT (not yet built — currently
  [satellite_node_sim.py](../base-station/python/tools/satellite_node_sim.py) simulates
  this). The **same section-list payload bytes** are published as the MQTT message
  body, with no additional custom envelope — MQTT already provides message framing and
  (via TCP) integrity, so a second magic/seq/CRC wrapper would be redundant. Topic
  structure (e.g. `nodes/{source_id}/telemetry`) can double as routing/filtering and
  allow per-data-kind QoS/retain policy (e.g. retain perf/health scalars, don't retain
  raw spectra) — a benefit unique to pub/sub that the point-to-point SPI link doesn't
  have or need.

---

## 7. Explicitly deferred (considered, not building now)

- Live channel-metadata registry message (sent at connect, mapping IDs → semantic
  meaning) — replaced by the static schema file (§2); a live version was judged
  unnecessary complexity.
- Stateful missing-data cache with per-channel staleness tracking — see §4.
- Additional `data_kind`s (spectrogram/waterfall, discrete event streams) — no current
  need; the enum is a `u8` so adding one later is cheap when a real need appears.
- Per-modality/per-axis separate autoencoders (fusing anomaly scores at decision time
  instead of concatenating a joint feature vector) — raised as a more robust
  alternative to a single monolithic autoencoder if satellite nodes end up with
  divergent arrival cadences, but not needed for the current experimentation-phase
  goal.

---

## 8. Task checklist / suggested order

- [ ] **T1 — Schema file.** Create the single source-of-truth channel schema (format:
      YAML or a shared header — pick whichever is less friction to hand-edit
      repeatedly during experiments). Start with today's actual combination (1 mic +
      1 combined-accel) as the first entry, to confirm the new format round-trips
      identically to what's currently working before changing anything.
- [ ] **T2 — MCU: section-list writer.** In `fuser.cpp`, replace the fixed
      `fuser_frame_header` + two-block body with the generic
      `num_sections`/`(source_id, channel_id, data_kind, section_len, body)` writer.
      Keep `SPI_LINK_MAX_PAYLOAD`/`FUSER_MAX_BINS` sized generously enough for
      whatever combination is being tried (check against the ~64 ms epoch's SPI wire
      time budget if payload grows a lot — a triaxial+mic+scalars frame is
      meaningfully bigger than today's 4112 B).
- [ ] **T3 — MPU: generic section parser.** In `features.py` (or a new module), write
      the section-list reader once: iterate sections by `(data_kind, len)`, dispatch to
      a per-kind decoder, skip unrecognized kinds by length. Build the feature vector
      by concatenating in a fixed sort order (e.g. by `channel_id`).
- [ ] **T4 — Zero-fill for structurally-absent channels.** Confirm the MCU sends
      explicit zero-valued sections (not omitted sections) for any channel a node
      declares in its schema but doesn't physically have — per §4, omission is not
      supported in this plan, only presence-with-zero.
- [ ] **T5 — Retrain harness.** Since every combination change requires a fresh
      autoencoder, make it cheap to retrain against whatever shape the current schema
      produces (this likely already mostly exists via
      [commissioning.py](../base-station/python/pipeline/commissioning.py) — confirm
      it doesn't hardcode `input_dim`).
- [ ] **T6 — Run the actual bin-size/combination experiments** this was all in service
      of (ties back into
      [EDGE_IMPULSE_FAULT_CLASSIFICATION_PLAN.md](EDGE_IMPULSE_FAULT_CLASSIFICATION_PLAN.md)
      §3.3's next-session item: band-power / dimensionality experiments).
- [ ] **T7 — Finalize.** Once a combination is chosen, freeze the schema and collapse
      to the flat, rigid Phase B struct (§3) — drop section headers, all fields
      required, positional layout.
- [ ] **T8 (later, not blocking) — Satellite MQTT path.** When satellite nodes send
      real (non-simulated) data, publish the same section-list payload bytes as the
      MQTT message body per §6, no additional envelope.

---

## 9. Appendix — where each change lands in existing code

| Concern | Existing file | Change |
|---|---|---|
| Frame payload writer | [sketch/fuser.cpp](../base-station/sketch/fuser.cpp) | fixed 2-block body → generic section-list writer (Phase A), then flat struct (Phase B) |
| SPI transport envelope | [sketch/spi_link.cpp](../base-station/sketch/spi_link.cpp) | unchanged — envelope is payload-agnostic |
| Accel per-axis FFT | [sketch/accel_sampler.cpp](../base-station/sketch/accel_sampler.cpp) | stop magnitude-summing X+Y+Z if/when experimenting with per-axis channels |
| Frame payload parser | [python/pipeline/features.py](../base-station/python/pipeline/features.py) | fixed mic+accel concat → generic section-list reader |
| Autoencoder input shape | [python/pipeline/autoencoder.py](../base-station/python/pipeline/autoencoder.py) | `input_dim` follows whatever the current schema produces; retrain per T5 |
| Commissioning/training | [python/pipeline/commissioning.py](../base-station/python/pipeline/commissioning.py) | confirm no hardcoded input_dim |
| Schema file (new) | — | **new**, single source of truth per §2 |
| Satellite sim | [python/tools/satellite_node_sim.py](../base-station/python/tools/satellite_node_sim.py) | eventually publish section-list payload over MQTT per §6/T8 |
| MQTT ingestion | [python/ingestion/mqtt_subscriber.py](../base-station/python/ingestion/mqtt_subscriber.py) | parse same section-list payload from real satellite nodes (T8) |
