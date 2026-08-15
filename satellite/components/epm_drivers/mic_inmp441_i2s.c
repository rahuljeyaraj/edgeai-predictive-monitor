/*
 * mic_inmp441_i2s.c — Stage 1: raw I2S DMA capture + signal verification.
 *
 * No FFT, no esp-dsp, no BLE here on purpose. The goal of this stage is
 * just to prove the mic is wired correctly and the I2S/DMA path is
 * gap-free, before any compute or radio work is layered on top.
 *
 * Uses the new ESP-IDF v5.x I2S driver (driver/i2s_std.h), which is what
 * PlatformIO's `framework = espidf` builds against for IDF 5.x. If your
 * platformio.ini pins an IDF 4.x release this header won't exist — check
 * `platform_packages`/`framework` versions if the build complains about
 * missing i2s_std.h.
 *
 * HARDWARE NOTE: This driver targets EXTERNAL I2S microphones (INMP441,
 * ICS-43434).  The built-in PDM microphone on the XIAO ESP32-S3 Sense
 * board requires i2s_pdm_rx_config_t instead of i2s_std_config_t.
 * If using the onboard PDM mic, replace i2s_channel_init_std_mode with
 * i2s_channel_init_pdm_rx_mode and set the appropriate PDM GPIO pins.
 */

#include "drivers/mic_inmp441_i2s.h"
#include "freertos/FreeRTOS.h"
#include "freertos/portmacro.h"
#include "driver/i2s_std.h"
#include "driver/gpio.h"
#include "esp_heap_caps.h"
#include "esp_attr.h"
#include "esp_log.h"
#include <math.h>
#include <string.h>

static const char *TAG = "mic_capture";

static i2s_chan_handle_t s_rx_chan = NULL;

/* ── I2S DMA overflow tracking ───────────────────────────────────────────────
 * HW-OPT: CONFIG_I2S_ISR_IRAM_SAFE=y keeps the ISR in IRAM so it stays
 * reachable when the flash cache is disabled during WiFi TX bursts.
 * Without it an 800 µs cache-miss window can cause the overflow ISR to miss
 * a DMA underrun event, silently dropping an audio block. */
static volatile uint32_t DRAM_ATTR s_i2s_overflow_count = 0;

static IRAM_ATTR bool i2s_overflow_cb(i2s_chan_handle_t handle,
                                       i2s_event_data_t *event, void *ctx)
{
    (void)handle; (void)event; (void)ctx;
    s_i2s_overflow_count++;
    return false; /* no higher-priority task wake needed */
}

uint32_t mic_capture_get_overflow_count(void)
{
    return s_i2s_overflow_count;
}

/* ── Stats (Part I: one <module>_get_stats() accessor per module) ────────── */

static uint32_t s_read_errors = 0;
static uint32_t s_short_reads = 0;

void mic_inmp441_i2s_get_stats(struct mic_inmp441_i2s_stats *out)
{
    if (out == NULL) return;
    out->overflow_count = s_i2s_overflow_count;
    out->read_errors    = s_read_errors;
    out->short_reads    = s_short_reads;
}

/* ── PSRAM pre-trigger ring buffer ──────────────────────────────────────────
 * 4 seconds × 16000 Hz × 2 bytes = 128 KB.  int16_t truncation (raw >> 16)
 * keeps the 16 most-significant bits — equivalent to 96 dB dynamic range.
 */
#define SNAPSHOT_SAMPLES  (4u * MIC_SAMPLE_RATE_HZ)

static int16_t         *s_ring_buf   = NULL;
static volatile size_t  s_ring_head  = 0;   /* next write position (mod SNAPSHOT_SAMPLES) */
static volatile size_t  s_ring_count = 0;   /* valid samples, capped at SNAPSHOT_SAMPLES */
static portMUX_TYPE     s_ring_mux   = portMUX_INITIALIZER_UNLOCKED;

void snapshot_init(void)
{
    s_ring_buf = heap_caps_malloc(SNAPSHOT_SAMPLES * sizeof(int16_t), MALLOC_CAP_SPIRAM);
    if (!s_ring_buf) {
        ESP_LOGW(TAG, "PSRAM unavailable — pre-trigger ring buffer disabled");
    } else {
        ESP_LOGI(TAG, "PSRAM ring buffer: %u samples (%.0f s)",
                 (unsigned)SNAPSHOT_SAMPLES,
                 (float)SNAPSHOT_SAMPLES / MIC_SAMPLE_RATE_HZ);
    }
}

static void snapshot_push_block(const int32_t *dma_raw, size_t n)
{
    if (!s_ring_buf || n == 0) return;
    if (n > SNAPSHOT_SAMPLES) n = SNAPSHOT_SAMPLES;

    size_t head = s_ring_head;  /* local snapshot before writing */
    for (size_t i = 0; i < n; i++) {
        s_ring_buf[(head + i) % SNAPSHOT_SAMPLES] = (int16_t)(dma_raw[i] >> 16);
    }

    portENTER_CRITICAL(&s_ring_mux);
    s_ring_head = (s_ring_head + n) % SNAPSHOT_SAMPLES;
    if (s_ring_count < SNAPSHOT_SAMPLES) {
        s_ring_count += n;
        if (s_ring_count > SNAPSHOT_SAMPLES) s_ring_count = SNAPSHOT_SAMPLES;
    }
    portEXIT_CRITICAL(&s_ring_mux);
}

size_t snapshot_count(void)
{
    portENTER_CRITICAL(&s_ring_mux);
    size_t c = s_ring_count;
    portEXIT_CRITICAL(&s_ring_mux);
    return c;
}

size_t snapshot_read_chunk(size_t chunk_byte_offset, void *dst, size_t nbytes)
{
    if (!s_ring_buf || !dst || nbytes == 0) return 0;

    portENTER_CRITICAL(&s_ring_mux);
    size_t count = s_ring_count;
    size_t head  = s_ring_head;
    portEXIT_CRITICAL(&s_ring_mux);

    size_t total_bytes = count * sizeof(int16_t);
    if (chunk_byte_offset >= total_bytes) return 0;

    size_t avail = total_bytes - chunk_byte_offset;
    if (nbytes > avail) nbytes = avail;

    /* Oldest sample sits at (head - count) mod SNAPSHOT_SAMPLES */
    size_t oldest_idx    = (head + SNAPSHOT_SAMPLES - count) % SNAPSHOT_SAMPLES;
    size_t sample_offset = chunk_byte_offset / sizeof(int16_t);
    size_t start_idx     = (oldest_idx + sample_offset) % SNAPSHOT_SAMPLES;
    size_t samples_total = nbytes / sizeof(int16_t);

    int16_t *d = (int16_t *)dst;
    size_t   copied = 0;
    while (copied < samples_total) {
        size_t pos          = (start_idx + copied) % SNAPSHOT_SAMPLES;
        size_t avail_to_end = SNAPSHOT_SAMPLES - pos;
        size_t remaining    = samples_total - copied;
        size_t seg          = (remaining < avail_to_end) ? remaining : avail_to_end;
        memcpy(d + copied, s_ring_buf + pos, seg * sizeof(int16_t));
        copied += seg;
    }
    return samples_total * sizeof(int16_t);
}

esp_err_t mic_capture_init(void)
{
    snapshot_init();
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(MIC_I2S_PORT, I2S_ROLE_MASTER);

    // ESP32-S3 DMA descriptor max is 4092 bytes = 1023 samples at 32-bit.
    // MIC_RAW_BLOCK_SAMPLES=1024 would hit that limit and trigger a driver
    // warning. Using half the block size (512) keeps each descriptor within
    // the hardware limit; i2s_channel_read() spans multiple descriptors
    // transparently. Total ring = dma_desc_num × 512 = 2048 samples = 2 blocks.
    chan_cfg.dma_desc_num  = MIC_DMA_DESC_NUM;
    chan_cfg.dma_frame_num = MIC_RAW_BLOCK_SAMPLES / 2;
    chan_cfg.auto_clear    = true;

    esp_err_t err = i2s_new_channel(&chan_cfg, NULL, &s_rx_chan);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "i2s_new_channel failed: %s", esp_err_to_name(err));
        return err;
    }

    i2s_std_config_t std_cfg = {
        .clk_cfg  = I2S_STD_CLK_DEFAULT_CONFIG(MIC_SAMPLE_RATE_HZ),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT,
                                                         I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = MIC_I2S_BCLK_PIN,
            .ws   = MIC_I2S_WS_PIN,
            .dout = I2S_GPIO_UNUSED,
            .din  = MIC_I2S_DATA_IN_PIN,
            .invert_flags = {
                .mclk_inv = false,
                .bclk_inv = false,
                .ws_inv   = false,
            },
        },
    };

    // INMP441/ICS-43434 output on the LEFT slot when L/R is tied to
    // GND. If you've wired SEL to VDD instead, flip this to SLOT_RIGHT —
    // otherwise you'll capture silence (the unselected channel).
    std_cfg.slot_cfg.slot_mask = I2S_STD_SLOT_LEFT;

    err = i2s_channel_init_std_mode(s_rx_chan, &std_cfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "i2s_channel_init_std_mode failed: %s", esp_err_to_name(err));
        return err;
    }

    /* HW-OPT: Register I2S overflow event callback so DMA underruns are
     * counted rather than silently dropped.  Requires CONFIG_I2S_ISR_IRAM_SAFE=y
     * in sdkconfig.defaults for IRAM placement of the ISR dispatch path. */
    i2s_event_callbacks_t evt_cbs = {
        .on_recv        = NULL,
        .on_recv_q_ovf  = i2s_overflow_cb,
        .on_sent        = NULL,
        .on_send_q_ovf  = NULL,
    };
    err = i2s_channel_register_event_callback(s_rx_chan, &evt_cbs, NULL);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "I2S event callback register failed: %s (overflow counting disabled)",
                 esp_err_to_name(err));
    }

    ESP_LOGI(TAG, "mic_capture init: %d Hz, block=%d samples, dma_desc=%d",
              MIC_SAMPLE_RATE_HZ, MIC_RAW_BLOCK_SAMPLES, MIC_DMA_DESC_NUM);
    return ESP_OK;
}

/* Call from mic_task_fn() running on CPU0. */
esp_err_t mic_capture_enable(void)
{
    esp_err_t err = i2s_channel_enable(s_rx_chan);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "i2s_channel_enable failed: %s", esp_err_to_name(err));
    }
    return err;
}

esp_err_t mic_capture_read_block(int32_t *out_raw, float *out_normalized,
                                  size_t block_len)
{
    if (s_rx_chan == NULL) {
        return ESP_ERR_INVALID_STATE;
    }

    /* HW-OPT: DMA_ATTR places raw_buf in internal DRAM with 4-byte alignment.
     * DMA controller on ESP32-S3 cannot read from PSRAM directly — the buffer
     * must be in internal DRAM to avoid a silent DMA corruption when PSRAM
     * cache lines are being evicted during concurrent WiFi GDMA operations.
     *
     * DMA buffer size check (must not exceed 4092 bytes per descriptor):
     *   dma_frame_num × slot_num × bit_width/8
     *   = 512 × 1 × 32/8 = 2048 bytes ≤ 4092 ✓
     * (MIC_RAW_BLOCK_SAMPLES/2 = 512 is set in mic_capture_init.) */
    static DMA_ATTR int32_t raw_buf[MIC_RAW_BLOCK_SAMPLES];
    size_t want_bytes = block_len * sizeof(int32_t);
    size_t got_bytes  = 0;

    /* 500 ms timeout: at 16 kHz and 1024-sample blocks, one block takes 64 ms.
     * portMAX_DELAY would block mic_task forever if DMA stalls — use a finite
     * timeout so mic_task_fn can log the failure and dsp_task can report it. */
    esp_err_t err = i2s_channel_read(s_rx_chan, raw_buf, want_bytes, &got_bytes,
                                      pdMS_TO_TICKS(500));
    if (err != ESP_OK) {
        s_read_errors++;
        ESP_LOGW(TAG, "i2s_channel_read failed: %s", esp_err_to_name(err));
        return err;
    }
    size_t n = got_bytes / sizeof(int32_t);

    snapshot_push_block(raw_buf, n);

    if (got_bytes != want_bytes) {
        s_short_reads++;
        ESP_LOGW(TAG, "short read: got %u of %u bytes — zero-padding remainder",
                 (unsigned)got_bytes, (unsigned)want_bytes);
        /* Zero-pad so the FFT never sees stale samples from the previous call. */
        if (out_normalized && n < block_len) {
            memset(out_normalized + n, 0, (block_len - n) * sizeof(float));
        }
        if (out_raw && n < block_len) {
            memset(out_raw + n, 0, (block_len - n) * sizeof(int32_t));
        }
    }

    for (size_t i = 0; i < n; i++) {
        // INMP441/ICS-43434 send 24-bit samples left-justified in the
        // 32-bit slot. Shift right by 8 to land the 24-bit value in the
        // low bits, sign-extending correctly since this is a signed
        // right shift on a signed type.
        int32_t sample24 = raw_buf[i] >> 8;

        if (out_raw) {
            out_raw[i] = sample24;
        }
        if (out_normalized) {
            out_normalized[i] = (float)sample24 / 8388608.0f; // 2^23
        }
    }

    return ESP_OK;
}

void mic_capture_compute_stats(const float *normalized_block, size_t len,
                                mic_block_stats_t *out_stats)
{
    if (len == 0 || normalized_block == NULL || out_stats == NULL) {
        return;
    }

    float sum = 0.0f;
    float sum_sq = 0.0f;
    float minv = normalized_block[0];
    float maxv = normalized_block[0];
    uint32_t clipped = 0;

    // Treat anything within ~1 LSB of full scale as clipped — at 24-bit
    // depth that's an extremely tight margin, so this only fires on
    // genuine clipping (gain too high, or mic overloaded by a loud
    // transient), not on normal signal swings.
    const float clip_thresh = 1.0f - (1.0f / 8388608.0f) * 2.0f;

    for (size_t i = 0; i < len; i++) {
        float v = normalized_block[i];
        sum    += v;
        sum_sq += v * v;
        if (v < minv) minv = v;
        if (v > maxv) maxv = v;
        if (fabsf(v) >= clip_thresh) clipped++;
    }

    out_stats->dc_offset      = sum / (float)len;
    out_stats->rms            = sqrtf(sum_sq / (float)len);
    out_stats->min_sample      = (int32_t)(minv * 8388608.0f);
    out_stats->max_sample      = (int32_t)(maxv * 8388608.0f);
    out_stats->clipped_count   = clipped;
}

void mic_capture_deinit(void)
{
    if (s_rx_chan) {
        i2s_channel_disable(s_rx_chan);
        i2s_del_channel(s_rx_chan);
        s_rx_chan = NULL;
    }
}
