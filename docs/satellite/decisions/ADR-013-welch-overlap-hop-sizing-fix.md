---
id: ADR-013
title: Welch overlap now controls FFT hop size, not just window content
status: accepted
date: 2026-08-04
deciders: Abhinav Krishna N
---

## Context

Auditing the Welch-overlap implementation in `src/dsp_task.c` — treating it
as unverified rather than assuming correctness — found no host test exists
for it, because `dsp_task.c` has ESP-IDF/ESP-DSP dependencies and cannot be
linked into `tests/host/` (the same reason `mic_task.c`'s stats are
hand-mirrored there instead of linked; confirmed by reading
`tests/host/CMakeLists.txt`).

Reading the pre-fix code (old `dsp_task.c:161-176`) showed every incoming
raw block produced **exactly one FFT**, regardless of `overlap_pct`:

```c
const float *fft_src = blk->samples;
if (local_overlap_pct > 0 && s_overlap_valid) {
    int overlap_n = (local_overlap_pct * FFT_MIC_N) / 100;
    if (overlap_n > 0 && overlap_n < FFT_MIC_N) {
        memcpy(s_merged, s_overlap_buf + (FFT_MIC_N - overlap_n), overlap_n * sizeof(float));
        memcpy(s_merged + overlap_n, blk->samples, (FFT_MIC_N - overlap_n) * sizeof(float));
        fft_src = s_merged;
    }
}
memcpy(s_overlap_buf, blk->samples, FFT_MIC_N * sizeof(float));
s_overlap_valid = 1;
```

`overlap_pct` changed *which* samples populated the window (splicing in the
previous block's tail) but never changed *how often* a window was emitted —
the stride between successive FFT windows, measured in raw samples, was
always `FFT_MIC_N`. This directly contradicts `src/epm_protocol.h:143-146`'s
documented wire-contract intent: *"At 75%, effective FFT rate quadruples
(step = FFT_MIC_N/4 samples)"*. That quadrupling never happened. **Confirmed
broken**, not a documentation mismatch.

Before touching real source, the proposed replacement's hop arithmetic was
verified with a throwaway scratch harness (not committed) tracing window
start offsets for `overlap_pct ∈ {0, 25, 50, 75}` — the only values
`src/wifi_task.h:46` documents as valid — over 12 synthetic raw blocks:

```
CURRENT  overlap=  0%: hop 1024 x11              -> 12 windows / 12 blocks (1.00x)
PROPOSED overlap=  0%: hop 1024 x11              -> 12 windows / 12 blocks (1.00x)

CURRENT  overlap= 25%: hop 1024 x11              -> 12 windows / 12 blocks (1.00x)
PROPOSED overlap= 25%: hop 768  x15               -> 16 windows / 12 blocks (1.33x)

CURRENT  overlap= 50%: hop 1024 x11              -> 12 windows / 12 blocks (1.00x)
PROPOSED overlap= 50%: hop 512  x23               -> 24 windows / 12 blocks (2.00x)

CURRENT  overlap= 75%: hop 1024 x11              -> 12 windows / 12 blocks (1.00x)
PROPOSED overlap= 75%: hop 256  x47               -> 48 windows / 12 blocks (4.00x)
```

The proposed design's steady-state window density (1x / 1.33x / 2x / 4x for
0/25/50/75%) matches `epm_protocol.h`'s documented rate multipliers exactly,
including the quadrupling claim at 75%.

## Decision

Replace block-granular Welch merging with a real sliding-history buffer that
decouples "how many raw samples have arrived" from "how many FFT windows
have been extracted":

- `s_overlap_buf` / `s_merged` / `s_overlap_valid` are replaced with
  `static float s_hist[2 * FFT_MIC_N]`, `static int s_hist_len`,
  `static int s_hist_read`.
- Each raw block is appended to `s_hist` at `s_hist_len` (`dsp_task.c`,
  step 2); the ring-buffer item is still returned immediately after
  (step 3), unchanged from before.
- `hop_n = FFT_MIC_N - overlap_n` (floored at 1) is computed once per raw
  block from the currently-latched `local_overlap_pct`.
- A drain loop, `while (s_hist_read + FFT_MIC_N <= s_hist_len)`, extracts
  and processes one window per iteration (Hann → FFT → power accumulate,
  unchanged steps 4-6), advancing `s_hist_read += hop_n` each time — so a
  single raw block can now yield zero, one, or several FFT windows,
  whichever `hop_n` actually implies. When `avg_cnt` reaches
  `local_spec_avg_n` inside the drain loop, the frame is built and emitted
  (steps 7a-7c, unchanged) exactly as before.
- After the drain loop empties out (less than one full window's worth of
  history remains), the buffer is compacted: the unconsumed tail
  (`s_hist_len - s_hist_read`, always `< FFT_MIC_N` because the drain loop's
  own condition guarantees it stops before that) is `memmove`d to the front
  and `s_hist_read` reset to 0. This keeps `s_hist`'s `2*FFT_MIC_N` capacity
  from ever overflowing: at most `FFT_MIC_N - 1` leftover samples plus one
  freshly appended `FFT_MIC_N`-sample block.

**New clamp, now load-bearing rather than cosmetic:** `new_overlap` (latched
from `g_adapt_overlap_pct`) previously had no bounds check — harmless, since
overlap only ever affected window *content*. It now directly controls loop
advancement via `hop_n`, so an out-of-protocol value (e.g. a wire glitch
delivering >100) could drive `hop_n` toward 0 and stall the task in the
drain loop. Two independent guards were added: `new_overlap` is clamped to
`[0, 90]` at the same latch site as the existing `new_avg` clamp, and
`hop_n` itself is floored at 1 regardless.

**Variance-reduction caveat (documented, not corrected in code):**
`epm_protocol.h:148`'s "Variance ∝ 1/N" comment assumes independent
segments. Once `overlap_n > 0`, successive windows share samples and are no
longer independent, so the *effective* independent-average count behind an
`local_spec_avg_n`-window accumulation is lower than `local_spec_avg_n`
itself — standard Welch/Bartlett behavior. This inflates the variance of the
resulting power estimate; it does **not** bias its mean, so the averaged
`s_pwr_acc` value emitted in `fft_db` remains a correct (unbiased) estimate
of the true spectrum regardless of overlap. No output-value correction is
needed. `epm_protocol.h` itself documents the noise-floor claim and is
outside this phase's file scope to edit; the caveat is recorded as a code
comment at the accumulation site in `dsp_task.c` and here, for whoever next
touches `epm_protocol.h`'s comments to reconcile.

## Consequence

- `overlap_pct` (0/25/50/75, from `g_adapt_overlap_pct`) now genuinely
  changes both spectral update rate and per-window variance, matching the
  wire contract's documented behavior for the first time. At a fixed
  `spec_avg_n`, higher overlap means more frequent (but more correlated)
  FFT windows feed each emitted frame — frame *emission* rate is unchanged
  (still gated on `avg_cnt >= local_spec_avg_n` windows), but the wall-clock
  time to accumulate those windows shrinks roughly in proportion to the hop
  reduction.
- No test in `tests/host/` exercises this path (same linking limitation
  noted above); regression coverage for this fix is
  the scratch-harness hop-arithmetic trace above plus a full `pio run`
  compile across all five PlatformIO environments (`xiao_esp32s3`,
  `mic_char_16k`, `mic_char_22050`, `mic_char_32k`, `mic_char_48k`), all of
  which built clean with the rewritten `dsp_task.c`.
- `tests/host/` itself is unaffected (`dsp_task.c` isn't linked into any of
  its three targets) — its unchanged green run after this fix is a
  build-safety check, not evidence this fix is correct.
