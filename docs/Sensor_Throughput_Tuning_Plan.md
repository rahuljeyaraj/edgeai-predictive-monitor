# Sensor Throughput Tuning Plan (KX134 Accel + INMP441 Mic)

## Goal

Push both sensor pipelines (accel: ODR, FFT rate; mic: sample rate, FFT
rate; both: fuser epoch rate, UART rate) toward max bandwidth
incrementally, with enough instrumentation at each pipeline stage to see
exactly where loss/corruption/staleness happens before pushing the next
knob. Each step below is a small, isolated change; build/flash, capture
the stats log, get a go/no-ahead, then move to the next step. Accel is the
primary driver since its data path has more open headroom (see below);
mic is tuned in parallel where its own hard constraints allow.

## Current pipelines (as of this plan)

```
KX134 HW buffer (86 frames, Stream mode)
  -> BFI (buffer-full) -> INT1 -> kx134_int1_isr() -> kx134_data_ready_sem
  -> hal_accel_read_block() [drivers/kx134.c] - SPI2 DMA burst read, 4MHz
  -> accel_sampler_thread [threads/accel_sampler_thread.c] - accumulates
     ACCEL_FFT_LEN=128 samples/axis, FFT x3, sum -> accel_spectrum_msgq
     (depth 1, purge-before-put)
                                                                    \
INMP441 (I2S, 16kHz, 16-bit slot) -> SAI1_A DMA                     \
  -> hal_audio_read_block() [drivers/audio_i2s.c] - 1024-sample       -> fuser_thread
     blocks (~64ms/block)                                             [threads/fuser_thread.c]
  -> mic_sampler_thread [threads/mic_sampler_thread.c] - FFT over      every FUSER_EPOCH_MS=250ms,
     the full 1024-sample block -> mic_spectrum_msgq                  non-blocking get per sensor
     (depth 1, purge-before-put)                                      (sample-and-hold) -> transport_send()
                                                                    /
                                                                   /
  -> uart_link.c - tx_busy CAS, drops frame outright if a previous TX is
     still in flight -> LPUART1 DMA @ 4Mbps
```

Known-suspect bottleneck, from the numbers alone (no hardware run needed
to see this): **both** sensors already produce spectra faster than
`fuser_thread` drains them.

- Accel: at ODR=1600Hz, one FFT window (128 samples/axis) completes every
  ~80ms (~12.5 windows/sec), but `fuser_thread` only drains
  `accel_spectrum_msgq` every 250ms (4Hz).
- Mic: at 16kHz with `AUDIO_BLOCK_SAMPLES`=1024, one block/FFT window
  completes every 64ms (~15.6 windows/sec) - even faster than accel - but
  the same 250ms/4Hz fuser epoch drains it.

Since both msgqs are 1-deep with purge-before-put
(`accel_sampler_thread.c:239-243`, `mic_sampler_thread.c:178`), roughly
2-of-3 accel windows and 3-of-4 mic windows are likely already being
silently discarded today, before any rate increase. This needs to be
*measured*, not assumed, hence Phase 0 below.

## Hard constraints already known (not fixable by this plan)

These bound how far the mic side can go regardless of tuning, and are
called out up front so a step doesn't get spent "discovering" a problem
that's already documented:

- **I2S word_size=16, not the INMP441's native 24-bit.**
  `drivers/audio_i2s.c`'s file header comment: the vendored
  `i2s_stm32_sai.c` hardcodes 16-bit DMA transfer width regardless of
  configured I2S word_size, so word_size=24 corrupts data (confirmed on
  hardware). Fixing this means patching vendored `zephyr/` code, which
  this project deliberately avoids. Out of scope here - the mic stays at
  16-bit precision.
- **Vendored `i2s_stm32_sai.c` mem-slab leak on I2S_STATE_ERROR
  recovery** (`docs/MCU_Software_Architecture.md` S8 item M5,
  `mic_sampler_thread.c:25-36`). Confirmed real (slab-size experiment).
  `MIC_SAMPLER_MAX_RECOVERY_ATTEMPTS`=5 bounds the damage per outage
  instead of fixing the root cause. Raising the mic sample rate increases
  DMA/interrupt frequency, which may make this recovery path trigger more
  often - watch its counter closely in every mic step below, since it's
  an existing unpatched ceiling, not something a rate change can push
  through.
- **PB9 CS vs mic SAI1_A FS pin conflict** (`project_kx134_i2c_migration`
  memory, `boards/arduino_uno_q.overlay`) - open, unresolved, orthogonal
  to throughput. Left alone here.

## Phase 0 — Instrumentation only (no rate changes)

Add free-running counters at each stage, plus one periodic summary log
line that reports counts-per-interval (not cumulative totals) so the
numbers directly answer "how much am I losing *right now*". No behavior
changes.

1. **`drivers/kx134.c` / `hal/hal_accel.h`**
   - Count INT1/BFI pulses (in `kx134_int1_isr()`).
   - Count `hal_accel_read_block()` calls, and frames returned per call.
   - Count calls where `frames == KX134_FIFO_MAX_FRAMES` (86) - the
     hardware buffer was already full/at-cap when read, meaning Stream
     mode may have been silently discarding older samples before this
     read ever happened.
   - Count SPI errors/timeouts (`-ETIMEDOUT` path already exists, just
     needs a counter instead of/alongside the existing `LOG_ERR`).
   - Expose via a small `hal_accel_get_stats(...)` (or similar) accessor
     rather than logging from inside the ISR/driver directly.

2. **`threads/accel_sampler_thread.c`**
   - Count completed FFT windows (rate = actual window throughput).
   - Count *dropped* windows: check `k_msgq_num_used_get(&accel_spectrum_msgq)
     > 0` immediately before `k_msgq_purge()` - if true, the purge just
     discarded a window `fuser_thread` never consumed.
   - Count `hal_accel_read_block()` failures (recovery-retry path).

3. **`drivers/audio_i2s.c` / `hal/hal_audio.h`**
   - Count `hal_audio_read_block()` calls and blocks returned.
   - Count `i2s_read()` failures (the path that currently just logs and
     returns a negative errno).
   - Expose via a small `hal_audio_get_stats(...)` accessor, same pattern
     as `hal_accel`.

4. **`threads/mic_sampler_thread.c`**
   - Count completed FFT windows (rate = actual window throughput).
   - Count *dropped* windows, same pattern as accel: check
     `k_msgq_num_used_get(&mic_spectrum_msgq) > 0` immediately before
     `k_msgq_purge()`.
   - Count recovery attempts (`consecutive_failures` increments) and
     "gave up" events (hitting `MIC_SAMPLER_MAX_RECOVERY_ATTEMPTS`) - this
     is the mem-slab-leak-adjacent path called out above, so it needs its
     own visibility, not just a generic error count.

5. **`threads/fuser_thread.c`**
   - Count epochs where `k_msgq_get(&accel_spectrum_msgq, ...)` /
     `k_msgq_get(&mic_spectrum_msgq, ...)` returned `-ENOMSG` (stale -
     resent the previous window, sample-and-hold) vs. hit (fresh data
     this epoch), separately for each sensor.
   - Count `transport_send()` failures (non-zero return).

6. **`drivers/uart_link.c`**
   - Turn the existing "TX busy, dropping" `LOG_WRN` into a counter as
     well (per-event logging at high rates would itself become a
     bandwidth problem); same for `UART_TX_ABORTED`.
   - Expose via a small accessor, same pattern as `hal_accel`/`hal_audio`.

7. **Stats reporting**
   - One `LOG_INF` line every ~1s (piggybacked on a counter inside
     `fuser_thread`'s existing loop, or a dedicated lightweight timer),
     printing all counters above as *rate over the last interval*, then
     resetting them. Something like:
     `accel: isr=N read=N frames_avg=N fifo_full=N spi_err=N | mic: read=N
     i2s_err=N recover=N giveup=N | sampler: a_windows=N a_drops=N
     m_windows=N m_drops=N | fuser: accel_fresh=N accel_stale=N
     mic_fresh=N mic_stale=N tx_fail=N | uart: tx_drop=N tx_abort=N`

This phase alone should already confirm/refute the epoch-mismatch
hypothesis above for *both* sensors, and gives a real baseline for every
later step.

## Phase 1 — Baseline capture

Build + flash with Phase 0 instrumentation only, current rates unchanged
(accel: ODR 1600Hz, `ACCEL_FFT_BIN_COUNT`=64, SPI 4MHz; mic: 16kHz,
`MIC_FFT_BIN_COUNT`=512, word_size=16; shared: `FUSER_EPOCH_MS`=250, UART
4Mbps). Capture a minute or two of the stats log as the reference point
every later step is compared against.

## Phase 2 — Incremental rate increases

One knob per step. Rebuild, flash, run, capture the stats log, stop for
confirmation before the next step. `FUSER_EPOCH_MS` is listed first since
it's shared and Phase 1 will likely show it as the dominant existing loss
source for *both* sensors, independent of either sensor's own rate.

1. **`FUSER_EPOCH_MS`** (`app_config.h:39`) - tighten to match/exceed the
   faster of the two producers (mic's ~64ms today) first. Try 100ms, then
   80ms, then ~64ms; watch `sampler: a_drops`/`m_drops` trend toward zero
   and `uart: tx_drop`/`tx_abort` for any new regression. Since this
   affects both sensors' drain rate simultaneously, a single step here
   may resolve most of the loss measured in Phase 1 before any per-sensor
   rate is even touched.

2. **KX134 ODR** (`KX134_ODR_HZ` / `KX134_ODCNTL_OSA_ACTIVE`,
   `drivers/kx134.c`) - step through the datasheet's supported OSA
   rates: 1600 -> 3200 -> 6400 -> 12800 -> 25600 Hz (now a one-line
   change - both constants live together with the full OSA table right
   above them). At each step, watch `accel: spi_err` (SPI read-out
   falling behind) and the *drained-throughput ratio* - `isr_count x
   frames_avg` from the stats line vs. the configured ODR - rather than
   `fifo_full` alone: `accel_sampler_thread` only wakes on a
   buffer-full interrupt, so `fifo_full` reads ~100% in any healthy
   steady-state cycle, not just a lossy one (confirmed empirically -
   see [[project_sensor_throughput_tuning]] memory for the full
   reasoning). A falling drained-throughput ratio is the real signal
   that this step has found the ceiling.

   **Every ODR change here must also update `ACCEL_FS_HZ` in
   `mpu/tools/spectrum_server.py`** to the same value - the wire protocol's
   `spectrum_fused_payload_header` carries no sample-rate field, so the
   MPU-side frequency-axis labels are computed from that hardcoded
   Python constant independently of whatever the MCU is actually
   sampling at. Missing this update doesn't break anything
   numerically, but it silently mislabels the displayed spectrum's
   frequency axis (the FFT bin *math* on the MCU scales correctly with
   ODR on its own - only the MPU-side display needs the manual nudge).

3. **SPI clock** (`spi-max-frequency`, `boards/arduino_uno_q.overlay`,
   currently 4MHz, chip max 10MHz) - only if step 2 shows SPI read-out
   itself falling behind (not just the ODR being high) - raise toward
   10MHz.

4. **Mic sample rate** (`AUDIO_SAMPLE_RATE_HZ`, `drivers/audio_i2s.c:44`)
   - step up from 16kHz (e.g. 22050 -> 32000 -> 44100/48000 Hz, whatever
     the SAI1 clock tree and the INMP441's own datasheet actually
     support - check both before picking the next value, this hasn't
     been verified yet). At each step, watch `mic: i2s_err`/`recover`/
     `giveup` closely - per the hard-constraints section above, the
     vendored driver's mem-slab leak on I2S_STATE_ERROR is an existing
     unpatched bug, and a higher DMA/interrupt rate is a plausible way to
     trigger it more often. A rising `giveup` count at a given rate is a
     hard stop for that rate, not something to push through.

5. **UART/transport** - recheck `uart: tx_drop`/`tx_abort` at each step
   above. At 4Mbps LPUART1 this is expected to have the most headroom of
   any stage (current SPECTRUM frame is well under a KB even with both
   sensors' bins), but Phase 0's counters will confirm rather than
   assume this. If this becomes the actual limiter once both sensors are
   pushed up, the fused frame's total size (mic + accel bins together) is
   the thing to trim first, before dropping either sensor's own rate.

6. **`ACCEL_FFT_BIN_COUNT`** (`app_config.h:26`) - only after ODR is at
   its ceiling from step 2 - larger windows trade FFT rate/latency for
   frequency resolution; smaller windows do the opposite. Adjust based on
   what the fault-frequency analysis actually needs, using the now-real
   throughput numbers instead of the current placeholder value.

7. **`MIC_FFT_BIN_COUNT`** (`app_config.h:16`, currently 512, i.e.
   `AUDIO_BLOCK_SAMPLES`=1024) - only after mic sample rate is at its
   ceiling from step 4 - same window-size/frequency-resolution trade-off
   as accel's step 6. Note `MIC_FFT_LEN` is asserted equal to
   `AUDIO_BLOCK_SAMPLES` at build time (`mic_sampler_thread.c:48`), so
   changing this also changes the I2S block size and needs both
   constants updated together.

## Phase 3 — Settle

Once a step shows sustained non-zero loss that doesn't clear on its own
(not just a brief startup transient), back off to the last "clean"
(near-zero drop/stale/error count) configuration for that stage, lock
those values in as the new defaults in `app_config.h` / `drivers/kx134.c`
/ `drivers/audio_i2s.c` / the overlay, and record the final numbers +
identified limiting stage (per sensor) in this doc or
`docs/MCU_Software_Architecture.md`.

## Open items this plan does not touch

- The PB9 CS vs. mic SAI1_A FS pin conflict (`project_kx134_i2c_migration`
  memory / `boards/arduino_uno_q.overlay`'s `&spi2` comment) is orthogonal
  to throughput and is left alone here.
- The I2S 16-bit word-size limit and the vendored driver's mem-slab leak
  on I2S_STATE_ERROR recovery (see "Hard constraints" above) are known,
  unpatched, and out of scope - this plan works around them (via
  instrumentation and recovery-attempt caps), not through them.
- `mcu/boards/arduino_uno_q.overlay` and `mcu/src/drivers/rgb_pwm.c`
  currently have unrelated uncommitted changes (RGB LED pin remap) - not
  touched by this plan.
