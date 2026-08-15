---
id: ADR-037
title: KX134 ODR raised from 12800Hz to 25600Hz to match IMU_FS_HZ
status: accepted
date: 2026-08-09
deciders: Abhinav Krishna N
---

## Context

An accel-side audit (same class of check as the `MIC_SAMPLE_RATE_HZ`/`MIC_FS_HZ`
drift found and fixed on the mic path) turned up a real, already-known-but-
unfixed mismatch: `src/epm_config.h`'s `IMU_FS_HZ` (25600) is used throughout
the firmware and gateway — FFT bin-width math, `epm_dsp_envelope_init()`'s
band-pass filter coefficient design, the wire-protocol `.fs` field for both
raw and envelope accel spectrum channels (`net_task.c`), and the gateway's
bearing-frequency marker math (`bf.markers(rv.IMU_FS_HZ)`) — but the real
`accel_kx134_spi.c` driver programmed `OSA<3:0> = 0x0E` (12800Hz), not
25600Hz. `hal_accel_get_sample_rate()` correctly returned the true 12800Hz
rate, but nothing in the firmware or gateway calls it — every consumer uses
the compile-time `IMU_FS_HZ` macro instead.

`docs/decisions/ADR-017` already named this ("ODR mismatch, out of scope to
fix here") but under-stated the impact, reasoning only about `vTaskDelay`
pacing and stale doc comments and concluding "this does not corrupt data."
That conclusion missed two real consequences:

1. **Envelope filter validity.** `epm_dsp_envelope_init()` uses `fs_hz`
   directly to compute biquad coefficients for the 2–8kHz band-pass. At the
   real 12800Hz rate, Nyquist is 6400Hz — the 8kHz upper band edge the
   firmware was designing for sat *above* Nyquist, an invalid filter design,
   not merely a stale comment.
2. **Wire-protocol semantics.** `net_task.c` publishes `.fs = IMU_FS_HZ` for
   both raw and envelope accel channels, and the gateway interprets bin
   index → physical frequency using that same constant. Every bearing-fault
   frequency marker computed downstream would have been off by exactly 2×
   versus what the sensor was actually doing.

Separately, `IMU_FS_HZ=25600` was always chosen to give a 12800Hz Nyquist,
comfortably clearing the reference project's reported 8kHz accel ceiling.
The chip silently running at 12800Hz ODR (6400Hz Nyquist) fell *short* of
that ceiling instead of clearing it.

## Options considered

### Option A: Lower `IMU_FS_HZ` to match the real 12800Hz ODR
Cheapest fix, but locks in falling short of the reference project's 8kHz
accel ceiling instead of meeting it — rejected on those grounds.

### Option B: Raise the KX134's programmed ODR to 25600Hz to match `IMU_FS_HZ` (chosen)
Per the KX134-1211 TRM Table 13 ODR table, `OSA<3:0> = 1111` (0x0F) is
25600Hz — one step above the previously-programmed 0x0E (12800Hz). Requires
empirical re-validation: doubling the ODR halves the time between BFI FIFO-
full interrupts, which ADR-017's sustained run never exercised.

## Decision

**Option B.** `accel_kx134_spi.c`'s `KX134_ODCNTL_OSA_12800HZ` (0x0E) is
replaced with `KX134_ODCNTL_OSA_25600HZ` (0x0F); `KX134_ODR_HZ` updated to
25600 to match (`hal_accel_get_sample_rate()`'s return value is now
consistent with `IMU_FS_HZ`, though still unconsulted by any caller).

No other file needed to change. Every downstream consumer (`epm_dsp_envelope_
init()`'s filter design, `net_task.c`'s wire `.fs` fields, the gateway's
bearing-marker math) was already written against `IMU_FS_HZ=25600` — they
were wrong only because the hardware disagreed with that assumption. Now
that the hardware matches, the envelope filter's 8kHz upper edge sits safely
under the new 12800Hz Nyquist, and every wire-reported frequency is correct
without further changes.

## Consequences

**Positive:**
- Real accel Nyquist (12800Hz) now clears the reference project's 8kHz
  ceiling, as `IMU_FS_HZ=25600` always intended.
- Envelope band-pass filter design (2–8kHz) is now valid — previously
  computed against an fs where the upper band edge exceeded Nyquist.
- Wire-reported `.fs` fields and gateway bearing-frequency markers now match
  the sensor's real sample rate; no more silent 2× error in fault-frequency
  identification.
- No changes needed outside `accel_kx134_spi.c` — every downstream consumer
  was already coded for 25600Hz.

**Negative / trade-offs:**
- Epoch cadence roughly doubles in FIFO/BFI-interrupt frequency (BFI now
  fires every ~3.36ms instead of ~6.72ms, since the 86-frame physical FIFO
  capacity is unchanged); SPI/CPU overhead per unit time increases
  correspondingly. Validated as tolerable below (see Validation).
- `ADR-017`'s "ODR mismatch, out of scope to fix here" bullet is now stale;
  this ADR supersedes that specific point (rest of ADR-017 stands).

**Metrics to watch:**
- `accel: read_errors`, `imu: reinit_attempts` (diagnostics_task log) —
  any sustained nonzero rate at 25600Hz would indicate the SPI/FIFO path
  can't keep up and the ODR bump should be reverted.

## Validation

Hardware was available (XIAO ESP32-S3 + KX134, COM15) and reflashed with the
0x0F ODR value. 150s continuous serial capture (board already ~44s into
uptime when capture attached, so ~194s of post-boot runtime is reflected in
the diagnostics counters below):

- **965 accel epochs** (`FFT_IMU_N=2048` samples each, 3 axes) — **2895
  `hal_accel_read_block()` calls, 0 `read_errors`, 0 `reinit_attempts`, 0
  `reinit_successes`** (diagnostics_task's periodic summary, 5 snapshots
  across the run, monotonically increasing with no resets — confirms no
  mid-run reboot).
- **Zero `-ETIMEDOUT`, zero SPI errors, zero crashes/panics/watchdog
  resets** — grepped for `ETIMEDOUT`, `Guru Meditation`, `abort`,
  `LoadProhibited`, `Backtrace` across the full capture; none found.
- **`FIFO seen at max capacity (86/86 frames)` on every epoch** — same
  benign BFI-only-interrupt pattern ADR-017 already documented as expected
  at 12800Hz (the chip's INC4/BUF_CNTL1 config triggers BFI only once the
  buffer is genuinely full, not at a watermark); `fifo_max_hits` tracked
  epoch count 1:1 across all 5 diagnostic snapshots (321/321, 482/482,
  642/642, 804/804, 965/965) — no change in this pattern's meaning from the
  ODR bump.
- **At-rest g-values stable across the full run**: x≈1.01–1.02g,
  y≈0.07–0.10g, z≈0.07–0.08g, holding steady epoch-to-epoch — no clipping,
  no noise blowup, no sign of corrupted decode at the higher data rate.
- **Epoch cadence stable**: ~180–190ms/epoch throughout (FFT compute +
  SPI + task scheduling overhead on top of the 80ms physical fill time),
  no drift or degradation over the 150s window.
- `mic i2s: overflow_count` flat at 26 across every diagnostic snapshot
  during this run (no new mic overflows) — the ODR bump has no observable
  cross-task effect on the mic path in this isolated test; combined-load
  behavior is a separate test (mic+accel simultaneous stimulus, tracked in
  the same characterization session as this ADR).

Conclusion: 25600Hz ODR is confirmed working on real hardware with the
existing FIFO/interrupt/SPI design — no FIFO overrun evidence, no error-rate
increase, no instability. Kept at 25600Hz; Option A (falling back to
12800Hz) was not needed.
