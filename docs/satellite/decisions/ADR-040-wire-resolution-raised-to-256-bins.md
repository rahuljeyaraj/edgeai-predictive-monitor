---
id: ADR-040
title: Wire spectral resolution raised 128 -> 256 bins (mic + accel + envelope)
status: accepted
date: 2026-08-11
deciders: Abhinav Krishna N
---

## Context

`tools/accuracy_harness/PHASE_B_REPORT.md`'s 2026-08-10 addendum characterized why
Mechanical Looseness's gate (`gateway/pipeline/alerting.py`: `mic_kurtosis >= 6.0 and
hi_r < 0.30 and lo_r < 0.55 and mid_r > 0.20`) could not be satisfied on real
hardware even with carrier placement chosen for the best available closed-form
margin: at the real 128-bin/48 kHz resolution (`hz_per` = 375 Hz), a short-tau
(`4 ms`) burst's Lorentzian ringdown linewidth spreads real acoustic energy across
several 375 Hz-wide bins near the 1875 Hz mid/hi edge, pushing measured `hi_r` to
0.45-0.48 versus a 0.29 closed-form prediction — nearly double, and well over the
0.30 gate on every frame of a 21-frame real-rig capture. The mechanism identified
was spectral leakage relative to bin width, not signal strength — coarser bins mean
a fixed-linewidth burst's energy smears across more of the gate-relevant band
regardless of carrier placement.

The direct fix for a leakage-vs-bin-width problem is finer bins: halving bin width
roughly halves how much of a fixed-linewidth burst's energy crosses a fixed band
edge. `EPM_MODEL_SPECTRUM_BINS` (`src/epm_config.h`) was 128, chosen by ADR-020 to
fit the then-existing 4096 B `EPM_NET_FRAME_BUF_BYTES`/esp-mqtt buffer budget, not
for any acoustic-accuracy reason — ADR-020 explicitly reserved raising it as a
future option once DRAM headroom improved (which it has: Phase 7's TCP+AES
retirement and PSRAM enablement, `project_hardware_hst.md`/`project_wifi_debug`
memories, both landed since ADR-020 was written).

## Options considered

### Option A: 512 bins
Would cut leakage the most, but `EPM_MODEL_SPECTRUM_BINS` must exactly divide
`FFT_MIC_N/2` (512) and `FFT_IMU_N/2` (1024) for `epm_dsp_reduce_bins()`'s integer
band-pooling (`in_n % out_n == 0`, both compile-time `#error`-guarded in
`epm_config.h`). 512 divides `FFT_IMU_N/2` but not `FFT_MIC_N/2` (512/512 = 1, i.e.
no mic pooling at all — the mic channel would report its full native resolution
while accel still pools 2:1), and more importantly exceeds `IMU_ENVELOPE_HALF`
(`FFT_IMU_N/IMU_ENVELOPE_DECIM/2`, currently 256 at `IMU_ENVELOPE_DECIM=8`), which
is build-time-asserted equal to `EPM_MODEL_SPECTRUM_BINS` since envelope channels
are wire-encoded directly without pooling. Reaching 512 there would need
`IMU_ENVELOPE_DECIM=2`, decimating to a 6400 Hz→12800 Hz effective rate that starts
to erode the aliasing margin below the 1000 Hz envelope lowpass rather than sitting
comfortably above it. Rejected for this pass: real-hardware timeline pressure (Task
3's re-test needs to happen against real firmware, not a redesign) favors the
smaller, safer step; 512 remains a valid future option if 256 turns out
insufficient.

### Option B: 384 bins
Not buildable. `FFT_MIC_N/2` = 512 and `FFT_IMU_N/2` = 1024 are both powers of two;
384 = 128*3 does not evenly divide either (512/384 and 1024/384 are both
non-integer), so `epm_dsp_reduce_bins()`'s divisibility requirement — and the
compile-time `#error` guards enforcing it — reject it outright. Not a tuning
choice, a hard constraint.

### Option C: 256 bins (chosen)
Divides both `FFT_MIC_N/2` (512/256 = 2) and `FFT_IMU_N/2` (1024/256 = 4) exactly,
halves bin width for every pooled channel (mic 375→187.5 Hz native-pooled width
before wire-fft_size accounting; wire-reported `hz_per` goes from 93.75 Hz... see
Decision below for the actual wire numbers), and reaches `IMU_ENVELOPE_HALF=256`
at `IMU_ENVELOPE_DECIM=4` instead of needing `=2` — comfortably preserving the
aliasing margin (`IMU_FS_HZ/4` = 6400 Hz decimated rate, 3200 Hz Nyquist, versus a
1000 Hz pre-decimation lowpass, i.e. over 3x margin instead of Option A's tighter
one). Directly halves the leakage-vs-bin-width ratio identified as the Looseness
gate's blocker, without requiring a frame-buffer redesign beyond a straightforward
byte-count recompute.

## Decision

**Option C — raise `EPM_MODEL_SPECTRUM_BINS` 128 -> 256.**

`IMU_ENVELOPE_DECIM` drops 8 -> 4 to keep `IMU_ENVELOPE_HALF` (`FFT_IMU_N /
IMU_ENVELOPE_DECIM / 2`) equal to the new 256-bin count, per the existing
build-time `#error` guard. Aliasing check for the smaller decimation factor: the
decimated rate becomes `IMU_FS_HZ/4` = 6400 Hz (Nyquist 3200 Hz), still far above
the `IMU_ENVELOPE_LP_HZ=1000` Hz lowpass that runs *before* decimation in the
band-pass -> rectify -> low-pass -> decimate pipeline, so nothing above ~1000 Hz
reaches the decimator — no new aliasing risk from halving the decimation factor.

`EPM_NET_FRAME_BUF_BYTES` recomputed from real arithmetic, not rounding: an 8-section
frame (7 SPECTRUM + 1 SCALAR_SET) at `EPM_MODEL_SPECTRUM_BINS=256` is
`1 + 7*(13 + 256*4) + (6 + 24*6) = 1 + 7*1037 + 150 = 7410 B` minimum. The prior
4096 B buffer no longer fits (128-bin minimum was 3826 B, 270 B/~7% headroom); the
buffer is raised to 8192 B, giving 782 B (~10.6%) headroom — a wider margin than
the prior buffer's. This exceeds `link_mqtt.c`'s 4096 B esp-mqtt `buffer.out_size`,
but esp-mqtt's publish path fragments payloads larger than its own buffer across
multiple transport writes (verified against `esp_mqtt_client_publish()`'s
`fragmented_msg_total_length` handling in `mqtt_client.c`) rather than rejecting
them, so no esp-mqtt buffer change is needed. The buffer remains PSRAM-backed
(`EXT_RAM_BSS_ATTR`, `src/threads/net_task.c` — moved there from internal DRAM by
the immediately-preceding commit `64afb29`, since ADR-020's Option A rejection
specifically flagged internal-DRAM margin as the risk, and PSRAM sidesteps that
risk entirely at this buffer's size).

No model retrain is needed: the AI model's `input_dim` is inferred per-frame from
whatever the node's actual first frame contains (`Registry.add()`, cited in
ADR-020's own Context), not hardcoded to a nominal 128 — this is the same
self-describing-wire-format property ADR-020 already established, now exercised in
the other direction (bins going up, not down).

Two hardcoded `128` bin-count literals were audited for and found this session,
both now bugs fixed as part of this same change (not pre-existing, since they
previously matched the old 128 value correctly):
- `tools/mqtt_fleet_sim.py` — `SPECTRUM_BINS` constant, manually duplicated from
  firmware rather than derived; now `256` with a comment flagging it as a
  manually-synced literal tied to `epm_config.h`'s `EPM_MODEL_SPECTRUM_BINS`.
- `tests/host/test_spectrum.c` — `test_wire_fft_size_true_bin_width()`'s hardcoded
  expected bin widths; now expects `93.75f` (mic) / `50.0f` (accel), matching the
  new `EPM_MIC_WIRE_FFT_SIZE`/`EPM_IMU_WIRE_FFT_SIZE` values at 256 bins.

No `mic_tools/` literal was found referencing the bin count directly (it consumes
decoded frames generically, same as `gateway/common/telemetry_frame.py`).

## Consequences

- Every pooled spectrum channel (mic, accel x/y/z) and every envelope channel
  (accel x/y/z envelope) now reports 256 bins instead of 128, at half the
  previous bin width — mic `hz_per` 93.75 Hz (was 187.5 Hz effective at 128
  bins... see Validation for the real wire-confirmed number), accel raw 50.0 Hz,
  envelope 12.5 Hz.
- `EPM_NET_FRAME_BUF_BYTES` roughly doubled (4096 -> 8192 B), all in PSRAM — no
  internal-DRAM cost, consistent with ADR-020's Option A rejection rationale no
  longer applying at this buffer's placement.
- `IMU_ENVELOPE_DECIM` dropped 8 -> 4; envelope aliasing margin narrows from
  `IMU_FS_HZ/8=3200 Hz` Nyquist (6.4x over the 1000 Hz lowpass) to `IMU_FS_HZ/4=
  6400 Hz` Nyquist (3.2x over) — still comfortable, but consumed some of the
  margin `IMU_ENVELOPE_DECIM=2` would have needed anyway if a future pass wants
  512 bins (Option A).
- Real-hardware Looseness leakage genuinely dropped (~35-40% reduction in mean
  `hi_r`, see Validation) but the gate does not yet reliably clear on this rig —
  a real, partial win, not a fix. Mechanical Looseness's status in
  `PHASE_B_REPORT.md` is unchanged: real-hardware attempted, gate not yet
  satisfied, remains injected-data-only. The mid-band edge itself also shifted
  (finer resolution quantizes band edges differently — real mid band is now
  468.8-1968.8 Hz versus the old 375-1875 Hz), a confound noted but not
  separated from the leakage-reduction effect in this pass.
- 512 bins (Option A) remains available as a future step if 256 proves
  insufficient, at the cost of re-deriving `IMU_ENVELOPE_DECIM` (would need `=2`,
  narrowing envelope aliasing margin further) and the mic channel losing pooling
  entirely (512 = `FFT_MIC_N/2`, i.e. native resolution, no `epm_dsp_reduce_bins()`
  call for mic at all).

## Validation

**Task 1 (build)**: `#error` divisibility guards in `epm_config.h` pass at
`EPM_MODEL_SPECTRUM_BINS=256` for both `FFT_MIC_N/2` and `FFT_IMU_N/2`;
`IMU_ENVELOPE_HALF != EPM_MODEL_SPECTRUM_BINS` guard passes at
`IMU_ENVELOPE_DECIM=4`. Host test suite (`tests/host/`, CMake+MinGW+CTest): 8/8
pass, including the updated `test_wire_fft_size_true_bin_width()`. Python
`pytest` suite: 174 passed / 1 skipped.

**Task 2 (real hardware)**: flashed and confirmed clean boot, WiFi/MQTT connect.
Live MQTT capture (`node_id=5ab004`) confirmed the new resolution is actually on
the wire, not just in firmware source:

```
channel                    fs   fft_size  bin_count     hz_per
mic                   48000.0        512        256    93.7500
accel_x               25600.0        512        256    50.0000
accel_y               25600.0        512        256    50.0000
accel_z               25600.0        512        256    50.0000
accel_x_envelope       6400.0        512        256    12.5000
accel_y_envelope       6400.0        512        256    12.5000
accel_z_envelope       6400.0        512        256    12.5000
```

`diagnostics_task` heap logging watched continuously for ~10.5 minutes real
device uptime (t~272s to t~634s in device-log time): `wifi: disconnects=0`,
`mqtt: disconnects=0` throughout, heap flat the entire window (internal 24520 B,
largest_free 16384 B, PSRAM 7942716 B unchanged) — no regression versus the
2026-08-09 stress-test baseline, internal DRAM margin not newly tight.

**Task 3 (Looseness re-test, real rig, apples-to-apples)**: same manifest
parameters as the original 2026-08-10 test (carriers 1650/1750/1850 Hz,
`tau_ms=4`, `burst_ms=20`, `period_hz=30`, `amplitude=0.8`, `sample_rate=48000`,
6 s duration), no parameter changes. Two independent live captures (97 total mic
frames; 73 frames coincide with the actual burst, identified the same way prior
addenda did — `mid_r` overtaking `lo_r` marks a ~2.5 s post-playback tail that is
excluded from the burst statistics below as a likely click/silence artifact, not
part of the injected fault signal).

Real result: `hi_r` during the burst now measures **mean 0.3010, std 0.0284,
range 0.2377-0.3802** across 73 frames — down from the prior firmware's
consistent 0.45-0.48. This is a real, reproducible ~35-40% reduction, and the
mean now sits almost exactly on the 0.30 gate rather than nearly 2x over it. It
still straddles rather than clears the gate: only 33/73 burst frames (45%)
individually satisfy `hi_r < 0.30`, and the full AND-gate
(`mic_kurtosis >= 6.0 and hi_r < 0.30 and lo_r < 0.55 and mid_r > 0.20`) fires on
0 of the 73 burst frames — `mic_kurtosis` stays under 1 in magnitude throughout
the real burst, nowhere near the 6.0 threshold. The gate does fire on 9 of the 24
excluded tail frames (kurtosis spiking to 6.0-32.98, `mid_r`~0.80-0.84,
`lo_r`~0.005-0.017), but this pattern — a sharp post-playback kurtosis spike with
near-silent `lo_r` — is far more consistent with a speaker/output click artifact
at playback stop than with the synthesized Looseness signal, and is excluded
from the verdict on that basis.

**Verdict: real, measured improvement in the identified leakage mechanism, but
Mechanical Looseness's gate is still not reliably satisfied by the actual fault
signal on this rig.** `tools/accuracy_harness/PHASE_B_REPORT.md` gains a dated
addendum recording this; the fault-category status table there is unchanged
(Looseness remains "real-hardware attempted, gate not yet satisfied,
injected-data-only") since the gate-pass conclusion itself did not change, even
though the underlying numbers did.
