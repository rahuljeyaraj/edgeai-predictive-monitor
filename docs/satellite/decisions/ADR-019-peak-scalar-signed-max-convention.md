---
id: ADR-019
title: Peak wire scalar uses signed max, not absolute max
status: accepted
date: 2026-08-05
deciders: Abhinav Krishna N
---

## Context

`docs/BASE_STATION_CONTRACT.md` finding 1 flagged an open decision: the
reference implementation's `peak` scalar is a signed maximum, not an
absolute-value maximum:

```python
def peak(x: np.ndarray) -> float:
    return float(x.max())
```

This is documented as matching `fuser.cpp`'s on-device computation — the real
wire convention, not an offline/display-only choice — and the contract doc
explicitly notes the known trade-off: `x.max()` has a "known failure mode on
negative-going impacts," i.e. it silently misses the true largest-magnitude
excursion whenever that excursion happens to be negative. Our own firmware
had no `peak` wire scalar at all before this ADR (Phase 6a's Task 2 added the
other missing scalars — `std`/`skewness` — but `peak` was left for this
decision).

Our existing code already computes an absolute-value peak
(`epm_dsp_peak_abs`) for crest factor (`peak(|x|)/rms`, an ISO-standard-style
impulsiveness metric) — that computation is independent of and unaffected by
this decision.

## Decision

The wire `peak` scalar uses **signed max** (`x.max()`), matching the
reference implementation, via a new `epm_dsp_peak_signed()` helper in
`components/epm_dsp/scalar_stats.c`. `epm_dsp_peak_abs()` is kept, unchanged,
as a distinct internal computation feeding crest factor only — the two are
not the same value and are not meant to converge.

Rationale: the `peak` scalar's consumer on the other end of the wire is a
base-station AI model trained on the reference implementation's
`raw_features.py` output. Diverging to `abs(x).max()` would fix the
negative-impact blind spot in isolation, but would introduce train/inference
skew for every downstream model that expects `peak` to mean "signed max" —
a worse failure mode than the one it would fix, and one that's silent (no
error, just a systematically wrong feature distribution). The negative-impact
blind spot itself is not left uncovered: crest factor's `peak(|x|)/rms` is a
separate wire scalar (id 3/9/15/21/27) that still spikes on a large
negative-going excursion even though `peak` itself won't reflect it — the
two scalars together give the base station what one scalar alone can't.

Implementation:
- `epm_dsp_peak_signed(const float *x, int n)` added to
  `components/epm_dsp/scalar_stats.{c,h}`.
- `src/threads/mic_task.c`: computes `last_peak` via
  `epm_dsp_peak_signed(s_norm, FFT_MIC_N)`, separate from the existing
  `epm_dsp_peak_abs()` call used only for crest. Added `peak` to
  `raw_mic_block_t` / `mic_frame_t` (`src/epm_config.h`), threaded through
  `src/threads/dsp_task.c`'s passthrough latch and frame build.
- `src/threads/imu_task.c`: `axis_stats_t.peak` renamed to `peak_abs` (crest
  factor's two call sites updated) plus a new `peak_signed` field, both
  computed in one pass inside `compute_axis_stats()`. Added `peak_x/y/z`
  (signed) to `imu_frame_t`.
- `tests/host/test_scalar_stats.c`: `mirror_peak_signed()` plus a test using
  a deliberately asymmetric signal (`-0.9` excursion larger in magnitude than
  the `0.5` positive excursion) that asserts `peak_signed` reports `0.5`
  (missing the `-0.9`) while crest factor still reflects the true `0.9`
  magnitude — this documents the accepted trade-off as a passing test, not
  just a comment.

## Consequences

- `peak` on our wire can under-report true impact magnitude whenever the
  largest excursion in a block is negative-going — this is an accepted,
  known, and now-tested characteristic, not a latent bug. Any future
  consumer of `peak` alone (without also checking crest factor) should be
  aware of this.
- Bit-for-bit parity with the reference implementation's `peak` values is
  preserved, which matters if/when a shared or transferred model is used.
- `epm_dsp_peak_abs` and crest factor's behavior are unchanged by this ADR —
  no regression risk to the existing crest-factor consumers.
