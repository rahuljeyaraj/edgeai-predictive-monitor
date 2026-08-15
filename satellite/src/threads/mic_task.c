/*
 * mic_task.c — I2S microphone capture task (core 0).
 *
 * Pipeline per block:
 *   1. mic_capture_read_block() — I2S DMA → normalised float block
 *   2. DC removal (mean subtracted in-place from s_norm)
 *   3. Time-domain stats via ESP-DSP SIMD:
 *      RMS    : dsps_dotprod_f32(s_norm, s_norm)  → sqrt(·/N)
 *      Crest  : fabsf() scalar loop → peak(|x|)/RMS  (dsps_abs_f32 absent in this ESP-DSP release)
 *      Kurtosis: dsps_mul_f32(s_norm,s_norm) → dsps_dotprod_f32 → (Σx⁴/N)/(var²) - 3 (excess, ADR-018)
 *      Std/Skew: reuses the Kurtosis step's x² scratch → dot(x²,x) = Σx³
 *      Peak (wire scalar): signed max x.max(), NOT abs(x).max() — ADR-019.
 *      Crest factor's internal peak(|x|) above is a separate, unrelated value.
 *   4. Post raw_mic_block_t to ring buffer for dsp_task (core 1)
 *
 * HW-OPT: esp_ringbuf zero-copy handoff — dsp_task receives a pointer into
 * the ring buffer storage (s_rb_storage, 8192 bytes, DRAM_ATTR internal DRAM),
 * reads the data directly, then returns the item.  Eliminates the 4-KB memcpy
 * that a depth-1 xQueueOverwrite would perform on every block.
 */

#include <math.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/ringbuf.h"

#include "esp_attr.h"
#include "esp_log.h"
#include "esp_timer.h"

#include "dsps_fft2r.h"
#include "dsps_math.h"
#include "dsps_dotprod.h"

#include "dsp/scalar_stats.h"

#include "drivers/mic_inmp441_i2s.h"
#include "epm_config.h"
#include "threads/mic_task.h"

static const char *TAG = "mic_task";

/* ── Capture + compute buffers (static — never on the task stack) ────────── */

/* HW-OPT: aligned(16) satisfies LX7 128-bit SIMD lane requirements for
 * dsps_dotprod_f32 / dsps_mul_f32 / fabsf. */
static float s_norm   [FFT_MIC_N] __attribute__((aligned(16)));
static float s_scratch[FFT_MIC_N] __attribute__((aligned(16)));  /* temp for SIMD */

/* Task handle — exposed via getter for diagnostics_task stack HWM logging. */
static TaskHandle_t s_task_handle = NULL;
TaskHandle_t mic_task_get_handle(void) { return s_task_handle; }

/* ── Stats (Part I: one <module>_get_stats() accessor per module) ────────── */

static uint32_t s_blocks_ok        = 0;
static uint32_t s_capture_failures = 0;
static uint32_t s_rb_drops         = 0;

void mic_task_get_stats(struct mic_task_stats *out)
{
    if (out == NULL) return;
    out->blocks_ok        = s_blocks_ok;
    out->capture_failures = s_capture_failures;
    out->rb_drops         = s_rb_drops;
}

/* ── Ring buffer for mic_task → dsp_task handoff ─────────────────────────── */

/* HW-OPT: DRAM_ATTR guarantees internal DRAM placement.  PSRAM cannot be used
 * as ring buffer storage — the esp_ringbuf implementation accesses header bytes
 * inside the ISR-driven xRingbufferReceive path, which must be in DRAM when
 * the flash cache is off during WiFi TX bursts.
 *
 * Size rationale for RINGBUF_TYPE_NOSPLIT:
 *   FreeRTOS NOSPLIT limits usable space to (buf_len/2 - header) per item.
 *   sizeof(raw_mic_block_t) = 4120 bytes; item header = 8 bytes.
 *   Minimum buf_len = (4120 + 8) × 2 = 8256 bytes.
 *   Using 10240 (10 KB) gives ~5112 bytes/slot → 2 items in flight (headroom
 *   for one dsp_task processing cycle while mic_task fills the next block). */
static DRAM_ATTR uint8_t       s_rb_storage[10240];
static StaticRingbuffer_t      s_rb_mem;
static RingbufHandle_t         s_raw_rb = NULL;

RingbufHandle_t mic_task_get_raw_ringbuf(void) { return s_raw_rb; }

/* ── Task function ───────────────────────────────────────────────────────── */

static void mic_task_fn(void *arg)
{
    (void)arg;

    int fail_cnt = 0;

    ESP_ERROR_CHECK(mic_capture_enable());

    float   last_rms      = 0.0f;
    float   last_crest    = 0.0f;
    float   last_kurtosis = 0.0f; /* excess/Fisher fallback, ADR-018 */
    float   last_std      = 0.0f;
    float   last_skewness = 0.0f;
    float   last_peak     = 0.0f; /* signed max, ADR-019 (not abs-max) */
    float   last_dc       = 0.0f;
    uint8_t last_clip     = 0;

    while (1) {
        /* --- 1. Capture --- */
        if (mic_capture_read_block(NULL, s_norm, FFT_MIC_N) != ESP_OK) {
            fail_cnt++;
            s_capture_failures++;
            if (fail_cnt >= MIC_FAIL_MAX) {
                ESP_LOGE(TAG, "mic_capture_read_block: %d consecutive failures — "
                         "check I2S wiring / clock", fail_cnt);
            } else {
                ESP_LOGW(TAG, "mic_capture_read_block failed (%d/%d) — retrying",
                         fail_cnt, MIC_FAIL_MAX);
            }
            vTaskDelay(pdMS_TO_TICKS(10));
            continue;
        }
        fail_cnt = 0;

        /* --- 2. DC offset (kept for removal and telemetry) --- */
        mic_block_stats_t st;
        mic_capture_compute_stats(s_norm, FFT_MIC_N, &st);
        last_dc   = st.dc_offset;
        last_clip = (uint8_t)(st.clipped_count > 0 ? 1 : 0);

        /* DC removal in-place */
        float dc = last_dc;
        for (int i = 0; i < FFT_MIC_N; i++) s_norm[i] -= dc;

        /* --- 3a. RMS on DC-removed signal (SIMD) --- */
        /* HW-OPT: dsps_dotprod_f32 uses LX7 vectorised multiply-accumulate;
         * ~4× throughput vs a scalar loop (128-bit SIMD = 4 float/cycle). */
        float sum_sq = 0.0f;
        dsps_dotprod_f32(s_norm, s_norm, &sum_sq, FFT_MIC_N);
        last_rms = epm_dsp_rms_from_sum_sq(sum_sq, FFT_MIC_N);

        /* --- 3b. Crest factor: peak(|x|) / RMS (scalar abs + scan) --- */
        /* dsps_abs_f32 is not present in this ESP-DSP release; scalar fabsf is
         * fast enough (1 cycle/element on LX7, 512 iterations ≈ 0.2 µs). */
        float peak = epm_dsp_peak_abs(s_norm, FFT_MIC_N);
        last_crest = epm_dsp_crest_factor(peak, last_rms);

        /* --- 3b'. Wire "peak" scalar: signed max, not abs-max (ADR-019) --- */
        last_peak = epm_dsp_peak_signed(s_norm, FFT_MIC_N);

        /* --- 3c. Kurtosis: (Σx⁴/N) / (Σx²/N)² (two SIMD dotprods) --- */
        /* Step 1: s_scratch = x²  (element-wise, SIMD) */
        dsps_mul_f32(s_norm, s_norm, s_scratch, FFT_MIC_N, 1, 1, 1);
        /* Step 2: sum4 = Σx⁴ = dot(s_scratch, s_scratch)  (SIMD) */
        float sum4 = 0.0f;
        dsps_dotprod_f32(s_scratch, s_scratch, &sum4, FFT_MIC_N);
        last_kurtosis = epm_dsp_kurtosis_from_sums(sum_sq, sum4, FFT_MIC_N, last_kurtosis);

        /* --- 3d. Std + skewness: s_norm is already DC-removed (mean ≈ 0), so
         * sum=0.0f is passed rather than an extra SIMD reduction; the helpers
         * still mean-center generically so this is correct, not a shortcut
         * specific to this call site. Step 3: sum3 = Σx³ = dot(s_scratch, s_norm)
         * reuses s_scratch = x² from the kurtosis step above (SIMD). */
        float sum3 = 0.0f;
        dsps_dotprod_f32(s_scratch, s_norm, &sum3, FFT_MIC_N);
        last_std      = epm_dsp_std_from_sums(0.0f, sum_sq, FFT_MIC_N);
        last_skewness = epm_dsp_skewness_from_sums(0.0f, sum_sq, sum3, FFT_MIC_N, last_skewness);

        /* --- 4. Post to dsp_task via ring buffer --- */
        static raw_mic_block_t s_blk;
        memcpy(s_blk.samples, s_norm, FFT_MIC_N * sizeof(float));
        s_blk.rms          = last_rms;
        s_blk.crest        = last_crest;
        s_blk.kurtosis     = last_kurtosis;
        s_blk.std          = last_std;
        s_blk.skewness     = last_skewness;
        s_blk.peak         = last_peak;
        s_blk.dc           = last_dc;
        s_blk.clip         = last_clip;
        s_blk.timestamp_ms = (uint32_t)(esp_timer_get_time() / 1000);

        /* Non-blocking send: if ring buffer is full (dsp_task backlogged),
         * drop the oldest data path — identical behaviour to xQueueOverwrite
         * but avoids an extra memcpy on the receive side. */
        if (xRingbufferSend(s_raw_rb, &s_blk, sizeof(s_blk), 0) != pdTRUE) {
            s_rb_drops++;
            ESP_LOGD(TAG, "raw_rb full — dropping block (dsp_task backlogged)");
        } else {
            s_blocks_ok++;
        }
    }
}

/* ── Public API ──────────────────────────────────────────────────────────── */

void mic_task_start(void)
{
    /* HW-OPT: xRingbufferCreateStatic — ring buffer storage in s_rb_storage
     * (DRAM_ATTR, internal DRAM).  No heap allocation at runtime. */
    s_raw_rb = xRingbufferCreateStatic(sizeof(s_rb_storage),
                                        RINGBUF_TYPE_NOSPLIT,
                                        s_rb_storage, &s_rb_mem);
    configASSERT(s_raw_rb != NULL);

    ESP_ERROR_CHECK(mic_capture_init());

    ESP_LOGI(TAG, "mic_task starting (capture core 0, SIMD stats): "
             "block=%d samples, Fs=%d Hz, ringbuf=%u bytes, blk_sz=%u",
             FFT_MIC_N, MIC_FS_HZ, (unsigned)sizeof(s_rb_storage),
             (unsigned)sizeof(raw_mic_block_t));

    xTaskCreatePinnedToCore(mic_task_fn, "mic_task", TASK_STACK_MIC, NULL,
                            TASK_PRIO_MIC, &s_task_handle, 0);
}
