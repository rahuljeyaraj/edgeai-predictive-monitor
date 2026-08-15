/*
 * dsp_task.c — ESP-DSP FFT pipeline task (core 1).
 *
 * Receives raw_mic_block_t items from mic_task via ring buffer, applies the
 * full Welch/Hann/FFT pipeline, and emits mic_frame_t after SPEC_AVG_N
 * blocks.
 *
 * Running exclusively on core 1 means the FFT never competes with I2S DMA
 * interrupts (core 0) or the WiFi driver (core 0).
 *
 * HW-OPT: ring buffer zero-copy — raw_mic_block_t is accessed directly from
 * the ring buffer storage (internal DRAM), no intermediate memcpy.  Item is
 * returned to the ring buffer immediately after samples are copied to the
 * sliding history buffer, typically within 2 µs of receipt.
 *
 * HW-OPT: ESP-DSP dsps_fft2r_fc32 uses the LX7 vectorisation unit (128-bit
 * SIMD, 4 floats/cycle).  Benchmark logged once at startup; expected ~1.1 ms
 * (≈264 k CPU cycles at 240 MHz) vs ~4.2 ms for a scalar Cooley-Tukey.
 *
 * Pipeline per block (ADR-013):
 *   1.  xRingbufferReceive — zero-copy pointer to raw_mic_block_t
 *   2.  Append block to sliding history buffer (s_hist)
 *   3.  vRingbufferReturnItem — release ring buffer slot
 *   [drain loop — one iteration per FFT_MIC_N-sample window the history
 *    buffer currently has enough samples for, advancing hop_n samples
 *    (= FFT_MIC_N * (1 - overlap_pct/100)) each time, so overlap_pct
 *    actually changes how often a window is emitted, not just its content]
 *   4.  Hann window (dsps_mul_f32, SIMD)
 *   5.  Pack interleaved complex, dsps_fft2r_fc32 + dsps_bit_rev2r_fc32
 *   6.  Accumulate linear power into s_pwr_acc (internal DRAM)
 *   7.  After SPEC_AVG_N windows:
 *       7a. Spectral centroid via dsps_dotprod_f32 on s_pwr_acc
 *       7b. Convert s_pwr_acc → dBFS → s_mag_db (PSRAM)
 *       7c. Build mic_frame_t (static internal DRAM) and post to queue
 *   8.  Compact history buffer, keeping the unconsumed (< FFT_MIC_N-sample) tail
 */

#include <math.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/ringbuf.h"

#include "esp_log.h"
#include "esp_timer.h"
#include "esp_attr.h"
#include "esp_cpu.h"         /* esp_cpu_get_cycle_count() — HW cycle counter (IDF 5.x) */

#include "dsps_fft2r.h"
#include "dsps_wind.h"
#include "dsps_math.h"
#include "dsps_dotprod.h"

#include "dsp/window.h"
#include "dsp/spectrum.h"

#include "epm_config.h"
#include "threads/dsp_task.h"
#include "hal/hal_display.h"  /* rgb_led_set_state, RGB_OK */

/* Set to true when 250 averaged frames have been processed (HST warm-up done). */
static bool s_hst_warmed_up = false;

static const char *TAG = "dsp_task";

#define FFT_HALF  (FFT_MIC_N / 2)

/* Task handle — exposed via getter for diagnostics_task stack HWM logging. */
static TaskHandle_t s_task_handle = NULL;
TaskHandle_t dsp_task_get_handle(void) { return s_task_handle; }

/* ── Stats (Part I: one <module>_get_stats() accessor per module) ────────── */

static uint32_t s_fft_count      = 0;
static uint32_t s_frames_emitted = 0;
static uint32_t s_rb_timeouts    = 0;
static uint32_t s_last_fft_us    = 0;

void dsp_task_get_stats(struct dsp_task_stats *out)
{
    if (out == NULL) return;
    out->fft_count      = s_fft_count;
    out->frames_emitted = s_frames_emitted;
    out->rb_timeouts    = s_rb_timeouts;
    out->last_fft_us    = s_last_fft_us;
}

/* ── FFT working buffers (internal DRAM — fast, SIMD-accessible) ─────────── */

/* HW-OPT: aligned(16) satisfies LX7 128-bit SIMD lane requirements. */
static float s_window    [FFT_MIC_N]     __attribute__((aligned(16)));
static float s_windowed  [FFT_MIC_N]     __attribute__((aligned(16)));
static float s_fft       [FFT_MIC_N * 2] __attribute__((aligned(16)));
static float s_pwr_acc   [FFT_HALF]      __attribute__((aligned(16)));  /* accumulator: fast DRAM */

/* Welch sliding-history buffer (ADR-013): raw samples accumulate here as
 * blocks arrive; FFT windows are drained out at a hop size that actually
 * responds to overlap_pct, instead of always advancing one block at a time.
 * Capacity 2*FFT_MIC_N is sufficient because the post-drain leftover is
 * always < FFT_MIC_N (proved in ADR-013), so one more full block appended
 * next cycle never overflows it. */
static float s_hist[2 * FFT_MIC_N] __attribute__((aligned(16)));
static int   s_hist_len  = 0;
static int   s_hist_read = 0;

/* ── Spectral centroid support — pre-computed frequency-bin table ─────────── */

/* HW-OPT: pre-computing freq_bins once avoids N multiplications inside the
 * averaging loop.  dsps_dotprod_f32 computes Σ(f_i × P_i) in a single SIMD
 * pass; the result divided by ΣP_i gives the spectral centroid in Hz. */
static float s_freq_bins[FFT_HALF] __attribute__((aligned(16)));  /* Hz per bin */
static float s_ones_half[FFT_HALF] __attribute__((aligned(16)));  /* all-1 for Σ P_i */

/* Hann window's coherent gain (mean of the window taps, ~0.5) — computed from
 * the real s_window array in dsp_task_start() rather than hardcoded, so it
 * tracks whatever window function is actually in use. The power-normalisation
 * factor below divides by this so a full-scale sine still reads 0 dBFS after
 * windowing (see tests/host/test_hann_window.c). */
static float s_coherent_gain = 1.0f;

/* ── FFT output buffer in PSRAM ──────────────────────────────────────────── */

/* s_mag_db in PSRAM: confirmed working (8 MB free, SESSION_7). */
static EXT_RAM_BSS_ATTR float s_mag_db[FFT_HALF];

/* ── Frame output buffer (static to keep 2 KB off the task stack) ─────────── */
static mic_frame_t s_out_frame;

static QueueHandle_t s_queue     = NULL;

/* ── DSP task ─────────────────────────────────────────────────────────────── */

static void dsp_task_fn(void *arg)
{
    RingbufHandle_t raw_rb = (RingbufHandle_t)arg;

    int      avg_cnt          = 0;
    const int local_spec_avg_n  = SPEC_AVG_N;
    const int local_overlap_pct = 0;
    uint32_t hst_frame_count   = 0;
    bool     fft_benchmarked   = false;

    float   last_rms      = 0.0f;
    float   last_crest    = 0.0f;
    float   last_kurtosis = 0.0f; /* excess/Fisher fallback, ADR-018 */
    float   last_std      = 0.0f;
    float   last_skewness = 0.0f;
    float   last_peak     = 0.0f; /* signed max, ADR-019 (not abs-max) */
    float   last_dc       = 0.0f;
    uint8_t last_clip     = 0;

    /* Pre-compute frequency-bin table and ones array (done once at startup). */
    const float hz_per_bin = (float)MIC_FS_HZ / FFT_MIC_N;
    for (int i = 0; i < FFT_HALF; i++) {
        s_freq_bins[i] = (float)i * hz_per_bin;
        s_ones_half[i] = 1.0f;
    }

    while (1) {
        /* --- 1. Zero-copy receive from ring buffer --- */
        size_t item_sz = 0;
        raw_mic_block_t *blk = (raw_mic_block_t *)
            xRingbufferReceive(raw_rb, &item_sz, pdMS_TO_TICKS(2000));

        if (blk == NULL) {
            s_rb_timeouts++;
            ESP_LOGW(TAG, "raw_rb timeout — no data from mic_task");
            continue;
        }

        /* Latch per-block stats from the ring buffer item. */
        last_rms      = blk->rms;
        last_crest    = blk->crest;
        last_kurtosis = blk->kurtosis;
        last_std      = blk->std;
        last_skewness = blk->skewness;
        last_peak     = blk->peak;
        last_dc       = blk->dc;
        last_clip     = blk->clip;

        /* --- 2. Append raw block to the sliding history buffer --- */
        memcpy(s_hist + s_hist_len, blk->samples, FFT_MIC_N * sizeof(float));
        s_hist_len += FFT_MIC_N;

        /* --- 3. Return ring buffer item — done reading blk->samples --- */
        vRingbufferReturnItem(raw_rb, blk);

        /* Welch hop (ADR-013): overlap_pct controls how far the window
         * advances between successive FFTs, not just what samples end up
         * inside a given window. hop_n is floored at 1 so an out-of-range
         * overlap_pct — already clamped above, guarded again here — can
         * never stall the drain loop below. */
        int hop_n = epm_dsp_welch_hop_size(FFT_MIC_N, local_overlap_pct);

        while (s_hist_read + FFT_MIC_N <= s_hist_len) {
            const float *fft_src = s_hist + s_hist_read;

            /* --- 4. Hann window (SIMD) --- */
            dsps_mul_f32(fft_src, s_window, s_windowed, FFT_MIC_N, 1, 1, 1);

            /* --- 5. FFT --- */
            for (int i = 0; i < FFT_MIC_N; i++) {
                s_fft[2 * i]     = s_windowed[i];
                s_fft[2 * i + 1] = 0.0f;
            }

            /* HW-OPT: esp_cpu_get_cycle_count() benchmark — logged once at startup.
             * dsps_fft2r_fc32 uses LX7 128-bit SIMD butterfly units.
             * Expected: ~264 k cycles (~1.1 ms at 240 MHz) vs ~1008 k (~4.2 ms) scalar. */
            if (!fft_benchmarked) {
                uint32_t t0 = esp_cpu_get_cycle_count();
                dsps_fft2r_fc32(s_fft, FFT_MIC_N);
                dsps_bit_rev2r_fc32(s_fft, FFT_MIC_N);
                uint32_t t1 = esp_cpu_get_cycle_count();
                ESP_LOGI(TAG, "FFT benchmark: %lu cycles (%.2f ms at 240 MHz) for %d-pt",
                         (unsigned long)(t1 - t0),
                         (float)(t1 - t0) / 240000.0f,
                         FFT_MIC_N);
                s_last_fft_us = (uint32_t)((t1 - t0) / 240);
                fft_benchmarked = true;
            } else {
                dsps_fft2r_fc32(s_fft, FFT_MIC_N);
                dsps_bit_rev2r_fc32(s_fft, FFT_MIC_N);
            }
            s_fft_count++;

            /* --- 6. Accumulate linear power (normalised so full-scale sine → 0 dBFS) ---
             * /s_coherent_gain corrects for the Hann window's amplitude loss
             * (ADR-012) — without it, windowed spectra read ~6 dB low.
             *
             * Welch/Bartlett caveat (ADR-013): once overlap_n > 0 these
             * segments are correlated, not independent, so the *effective*
             * independent-average count behind this accumulation is lower
             * than local_spec_avg_n. That inflates the variance of the
             * resulting estimate; it does not bias its mean, so the averaged
             * power below is still a correct (unbiased) spectrum estimate —
             * no output value needs correcting for the overlap. */
            const float nf = 2.0f / ((float)FFT_MIC_N * s_coherent_gain);
            epm_dsp_accumulate_power(s_fft, s_pwr_acc, FFT_HALF, nf);
            avg_cnt++;
            s_hist_read += hop_n;

            if (avg_cnt < local_spec_avg_n) {
                continue;
            }

            /* --- 7a. Spectral centroid from accumulated linear power (SIMD) --- */
            /* Σ(f_i × P_i) / Σ(P_i) — computed on raw accumulator so division by
             * local_spec_avg_n cancels in numerator and denominator. */
            float freq_weighted = 0.0f, power_total = 0.0f;
            dsps_dotprod_f32(s_pwr_acc, s_freq_bins, &freq_weighted, FFT_HALF);
            dsps_dotprod_f32(s_pwr_acc, s_ones_half, &power_total,   FFT_HALF);
            float spectral_centroid = (power_total > 1e-20f)
                                      ? freq_weighted / power_total : 0.0f;

            /* --- 7b. Convert averaged linear power → dBFS (PSRAM output) --- */
            const float inv_n = 1.0f / (float)local_spec_avg_n;
            epm_dsp_power_to_db(s_pwr_acc, s_mag_db, FFT_HALF, inv_n);
            avg_cnt = 0;

            /* --- 7c. Build frame and post to wifi_task queue --- */
            memcpy(s_out_frame.fft_db, s_mag_db, sizeof(s_out_frame.fft_db));
            s_out_frame.rms              = last_rms;
            s_out_frame.crest            = last_crest;
            s_out_frame.kurtosis         = last_kurtosis;
            s_out_frame.std              = last_std;
            s_out_frame.skewness         = last_skewness;
            s_out_frame.peak             = last_peak;
            s_out_frame.dc               = last_dc;
            s_out_frame.spectral_centroid = spectral_centroid;
            s_out_frame.clip             = last_clip;
            s_out_frame.timestamp_ms     = (uint32_t)(esp_timer_get_time() / 1000);

            xQueueOverwrite(s_queue, &s_out_frame);
            s_frames_emitted++;

            hst_frame_count++;
            if (!s_hst_warmed_up && hst_frame_count >= 250) {
                s_hst_warmed_up = true;
                rgb_led_set_state(RGB_OK);
                ESP_LOGI(TAG, "HST warmed up at frame %lu", (unsigned long)hst_frame_count);
            }
        }

        /* --- 8. Compact history buffer --- */
        /* Leftover is always < FFT_MIC_N (the drain loop's own condition
         * guarantees it stops as soon as less than one full window remains),
         * so appending the next full raw block never overflows s_hist's
         * 2*FFT_MIC_N capacity. */
        int leftover = s_hist_len - s_hist_read;
        if (s_hist_read > 0 && leftover > 0) {
            memmove(s_hist, s_hist + s_hist_read, (size_t)leftover * sizeof(float));
        }
        s_hist_len  = leftover;
        s_hist_read = 0;
    }
}

QueueHandle_t dsp_task_get_queue(void)
{
    return s_queue;
}

void dsp_task_start(RingbufHandle_t raw_rb)
{
    s_queue = xQueueCreate(1, sizeof(mic_frame_t));
    configASSERT(s_queue != NULL);

    dsps_wind_hann_f32(s_window, FFT_MIC_N);

    /* Coherent gain = mean of the window taps — derived from the actual
     * array dsps_wind_hann_f32() just produced, not a hardcoded constant. */
    s_coherent_gain = epm_dsp_coherent_gain(s_window, FFT_MIC_N);

    ESP_LOGI(TAG, "dsp_task starting (FFT core 1): %d-pt, avg=%d (adaptive), "
             "%.2f Hz/bin, coherent_gain=%.4f",
             FFT_MIC_N, SPEC_AVG_N, (float)MIC_FS_HZ / FFT_MIC_N, s_coherent_gain);

    xTaskCreatePinnedToCore(dsp_task_fn, "dsp_task", TASK_STACK_DSP, raw_rb,
                            TASK_PRIO_DSP, &s_task_handle, 1);
}
