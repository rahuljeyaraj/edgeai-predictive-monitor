---
id: ADR-032
title: Three accel envelope-spectrum channels added as permanent schema entries, no coordination with the reference repository needed
status: accepted
date: 2026-08-06
deciders: Abhinav Krishna N
---

## Context

Part G Phase 11 (optional differentiator) adds
envelope analysis (`components/epm_dsp/envelope.c`): band-pass around a
structural resonance -> full-wave rectify -> low-pass -> decimate, the
standard high-frequency resonance technique for bearing-defect detection.
Its output needs a wire channel to reach the base station. Two questions:
which axes, and whether adding channels needs sign-off from the maintainer of
the reference/base-station side.

**Channel selection.** Part G's own stated purpose for this feature is
BPFO/BPFI/BSF/FTF bearing-defect-frequency markers (`bearing_math.py`) — these
are vibration/mechanical frequencies, not acoustic. `mic_tools/fault_models.py`
(lines 32-33, 183-185, 229-236) documents the project's existing "structural
resonance excited in the 2-8 kHz band" convention, itself built for the
*microphone* fault model — but the resonance mechanism it describes (impacts
exciting a structural resonance, detected via HFRT) is generic to *any*
vibration/acoustic sensor's own resonance, not specific to the mic's transfer
function, and the KX134 driver work (`docs/decisions/ADR-017`) documents no
sensor-specific resonance frequency to derive an alternative from. The
accelerometer channels are the direct fit for bearing-defect frequencies;
mic envelope analysis is a separate, real signal but out of scope here.

**Decision: `accel_x_envelope`/`accel_y_envelope`/`accel_z_envelope` only, not
mic.** Same three radial/axial axes as the existing raw `accel_x/y/z`
channels (`src/threads/imu_task.c`'s axis convention).

## Schema coordination — resolved, no need to contact the reference repository's maintainer

Two independent pieces of evidence, both readable directly in this repo
(not assumed, not requiring a fresh live fetch of the reference repo):

1. **`schema/telemetry_schema.json`'s own doc comment (lines 14-16 of this
   file, ported verbatim from the reference repo's `base-station/telemetry_schema.json`
   when Phase 5 built this generator)**: *"This is Phase A (experimentation) --
   add/remove channels or change bins freely; the MPU parser needs zero code
   changes (it loops over sections and dispatches on data_kind)."* This is
   the reference repo's own stated contract for this exact file, ported into
   ours unchanged.

2. **`docs/BASE_STATION_CONTRACT.md` finding 4** (verified live against the
   reference repo on 2026-08-04, sources listed at that document's end): its
   `registry.py`'s `SensorChannel` enum is `{MIC, ACCEL_X, ACCEL_Y, ACCEL_Z}`
   only — nothing else. The existing combined `accel` channel (`id=1`, still
   on our own schema for legacy reasons) is confirmed to fall through to
   `SensorFrame.display_bins`, not `.bins`, precisely *because* it isn't in
   that enum (`mqtt_subscriber_test.py::test_combined_accel_channel_lands_in_display_bins_not_bins`,
   cited in that finding). A channel outside that enum decodes generically and
   displays if the reference UI shows it, but never touches
   `.bins`/`input_dim`/its model — the same fallthrough the existing `accel`
   channel already exercises today, with zero reported issues.

A new envelope channel doesn't need to be in the reference `SensorChannel`
enum to decode cleanly — it only needs a valid
`[source_id][channel_id][data_kind]` section header, which any addition to
`schema/telemetry_schema.json` guarantees by construction
(`schema/gen_schema.py`'s `_validate()` rejects duplicate/out-of-range ids
before either side is generated).

**Conclusion:** these three channels ship as normal, permanent schema
entries — not "provisional pending review." No message to the reference
repo's maintainer is needed for this addition specifically (unlike a
wire-format or scalar-convention change, which would require coordination
since those *are* shared contract, per `docs/BASE_STATION_CONTRACT.md`'s
open items).

## Decision

Added to `schema/telemetry_schema.json`'s `channels` array, next available
ids after the existing `accel_x/y/z` (6/7/8):

| name | id | kind |
|---|---|---|
| `accel_x_envelope` | 9 | SPECTRUM |
| `accel_y_envelope` | 10 | SPECTRUM |
| `accel_z_envelope` | 11 | SPECTRUM |

Regenerated via `python schema/gen_schema.py`:
`components/epm_codec/include/frame_codec/telemetry_schema.h` and
`gateway/common/telemetry_schema.py`.

**Incidental fix required to make regeneration actually work:**
`schema/gen_schema.py`'s `PYMOD_PATH` still pointed at `mic_tools/telemetry_schema.py`,
a path retired by Phase 8b3's gateway restructure (the real generated file
has lived at `gateway/common/telemetry_schema.py` since that phase, confirmed
identical to what the generator produces once the path is corrected — it was
hand-copied during the restructure and the generator's own path constant was
never updated to match). Running the generator unmodified would have silently
recreated a stale file at the old, now-untracked `mic_tools/` location and
left `gateway/common/telemetry_schema.py` un-regenerated. Fixed `PYMOD_PATH`
(and the one doc-comment line naming it) to point at the real current
location before regenerating.

## Consequences

- Three new SPECTRUM channels on the wire; `bin_count` will match whatever
  `net_task.c` encodes them at (Phase 11a Task 3 — same
  `EPM_MODEL_SPECTRUM_BINS`-wide convention as the raw spectra,
  `docs/decisions/ADR-020`, unless that phase's RAM check says otherwise).
- No action needed on the reference repo's side; its generic section-loop
  decode already handles an unrecognized channel id safely (finding 4 above).
- `docs/decisions/ADR-032` (this file) is the record for "why no coordination
  needed" so a future session doesn't have to re-derive it from scratch.
- `schema/gen_schema.py`'s path fix is a one-line correctness fix bundled
  into this commit (it blocks "regenerate both outputs" from being true
  otherwise) — not a new decision of its own.

## Validation

`schema/gen_schema.py`'s own `_validate()` passed (no duplicate/out-of-range
ids). Diff of both regenerated files reviewed: only the three new channel
entries added, everything else byte-identical. No gateway-side code changes
in this phase (11b, deferred) — the three channels exist on the wire and
decode generically but nothing consumes them yet.
