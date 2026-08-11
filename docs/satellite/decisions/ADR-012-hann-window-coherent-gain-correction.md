---
id: ADR-012
title: Hann window power normalisation corrected for coherent gain
status: accepted
date: 2026-08-04
deciders: Abhinav Krishna N
---

## Context

A host-side regression test (`tests/host/test_hann_window.c`) proved a real
bug in `src/dsp_task.c`'s FFT power-normalisation constant.

The pipeline windows every block with a Hann window
(`dsps_mul_f32(fft_src, s_window, s_windowed, FFT_MIC_N, 1, 1, 1)`,
`dsp_task.c:175`) before FFT'ing it, then converted accumulated power to
dBFS using:

```c
const float nf = 2.0f / FFT_MIC_N;                 // old dsp_task.c:202
```

with the stated intent "full-scale sine → 0 dBFS". That formula is correct
for a **rectangular** (unwindowed) full-scale sine — it has no term
correcting for the Hann window's coherent gain (mean of the window taps,
≈0.5), even though the signal is windowed first. The result: every
Hann-windowed spectrum read ~6.03 dB (`20*log10(0.5)`) low. The host test
`nf_normalization_ignores_coherent_gain` documented this
(`EXPECT_FAIL`) by comparing the as-coded formula against a gain-corrected
one and showing a ratio of ~0.5 instead of ~1.0.

## Decision

Derive the coherent-gain correction from the actual window array computed
on-device, rather than assuming a hardcoded constant (e.g. `0.5f`):

- `dsp_task_start()` computes `s_coherent_gain` as the mean of `s_window`
  right after `dsps_wind_hann_f32(s_window, FFT_MIC_N)` populates it.
- The power-normalisation factor becomes:
  `nf = 2.0f / ((float)FFT_MIC_N * s_coherent_gain)` (`dsp_task.c:211`).

This is deliberately not `nf = 2.0f / (FFT_MIC_N * 0.5f)`: computing the
gain from the real array means the normalisation stays correct if the
window function ever changes (e.g. to Blackman-Harris for better sidelobe
rejection) without anyone needing to remember to update a second hardcoded
constant in a different function.

## Consequence

Hann-windowed spectra now read ~6.03 dB higher than before this fix — this
is the corrected value, not drift. Anything downstream that consumed the
old (low-by-6dB) `fft_db` values (thresholds, baselines, trained models)
will see a level shift and should be recalibrated; flagged for whoever
owns those downstream consumers, not addressed here (out of this phase's
scope — `mic_tools/`, ONNX autoencoder thresholds, etc. are untouched by
this ADR).

`tests/host/test_hann_window.c`'s `nf_normalization_ignores_coherent_gain`
is renamed to `nf_normalization_includes_coherent_gain` and its expectation
flipped `EXPECT_FAIL → EXPECT_PASS`, with its hardcoded `nf_as_coded`
literal re-synced to mirror the new `dsp_task.c:211` formula exactly (it
doesn't link `dsp_task.c` — ESP-DSP's non-ANSI-C paths pull in ESP-IDF
headers — so it transcribes the formula by hand, same convention
`test_scalar_stats.c` already documents for `mic_task.c`'s mirrors).
