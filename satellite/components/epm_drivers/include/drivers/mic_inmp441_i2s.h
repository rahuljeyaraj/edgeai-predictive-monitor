#pragma once
/*
 * mic_inmp441_i2s.h — Stage 1: raw I2S microphone capture only.
 *
 * Scope (intentionally): get clean, gap-free raw PCM out of the mic and
 * verify it's sane (no clipping, reasonable noise floor, no stuck DC
 * offset) BEFORE any windowing/FFT/BLE is added. Those come in later
 * stages once this is confirmed working on real hardware.
 *
 * Moved from components/mic_capture/ (file rename + relocation only —
 * the mic_capture_* API is unchanged).
 */

#include <stdint.h>
#include <stddef.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ---------------- Hardware / capture tunables ---------------- */

// I2S pins. Defaults below match the wiring suggested in the README:
// XIAO ESP32-S3 pin D1 -> GPIO2 (BCLK), D2 -> GPIO3 (WS/LRCLK),
// D3 -> GPIO4 (DOUT from mic). Verify against the silkscreen on your
// specific board and change these if you wired it differently.
#define MIC_I2S_PORT          I2S_NUM_0
#define MIC_I2S_BCLK_PIN      GPIO_NUM_2   // XIAO pin D1
#define MIC_I2S_WS_PIN        GPIO_NUM_3   // XIAO pin D2
#define MIC_I2S_DATA_IN_PIN   GPIO_NUM_4   // XIAO pin D3

// Sample rate -- mirrors src/epm_config.h's MIC_FS_HZ (48000, raised from
// the 16 kHz placeholder after a real-hardware SNR sweep confirmed strong
// 15-20kHz bearing-fault signal at 48 kHz). Not #include-d directly:
// epm_drivers must not depend back on the main component's config, same
// rule accel_stub.c documents for IMU_FS_HZ and accel_kx134_spi.c documents
// for FFT_IMU_N. This is the value that actually configures the I2S clock
// (see I2S_STD_CLK_DEFAULT_CONFIG below) -- if this drifts from MIC_FS_HZ,
// the wire protocol's advertised sample rate no longer matches what the
// mic hardware is physically doing.
// Wrapped in #ifndef so test builds can override via -DMIC_SAMPLE_RATE_HZ=X.
#ifndef MIC_SAMPLE_RATE_HZ
#define MIC_SAMPLE_RATE_HZ    48000
#endif

// Raw read block size in samples. This is independent of the eventual
// FFT window size (that's a Stage-2 concern) — for Stage 1 just pick a
// size that's convenient to log/inspect, e.g. one I2S DMA frame.
#ifndef MIC_RAW_BLOCK_SAMPLES
#define MIC_RAW_BLOCK_SAMPLES 1024
#endif

// DMA ring depth. More descriptors = more capture headroom before any
// chance of an overrun if your consumer task is briefly slow (printf,
// logging, etc.). 4 is generous for Stage 1 where you're just verifying
// signal integrity, not yet racing a tight compute budget.
#define MIC_DMA_DESC_NUM      4

/* --------------------------------------------------------------- */

typedef struct {
    int32_t  min_sample;     // raw 24-bit-in-32 sample, sign-extended
    int32_t  max_sample;
    float    rms;            // RMS over the block, in normalized [-1,1] units
    float    dc_offset;      // mean value, normalized — should be ~0 for a good mic/wiring
    uint32_t clipped_count;  // samples that hit full-scale — wiring/gain problem if nonzero
} mic_block_stats_t;

/**
 * Brings up I2S in double-buffered DMA RX mode for the configured mic.
 * Also allocates the PSRAM pre-trigger ring buffer via snapshot_init().
 * Does NOT enable the channel — call mic_capture_enable() from mic_task.
 */
esp_err_t mic_capture_init(void);

/**
 * Enables the I2S channel and arms DMA.  Call from mic_task running on
 * CPU0.  I2S, WiFi, and IMU all share CPU0; FFT runs uninterrupted on
 * CPU1 (dsp_task).
 */
esp_err_t mic_capture_enable(void);

/**
 * Blocks until one full raw block is captured. `out_raw` receives the
 * sign-extended 24-bit samples (as int32_t, NOT normalized) so you can
 * inspect true raw values while debugging wiring/gain. `out_normalized`
 * (optional, pass NULL to skip) receives the same data scaled to
 * [-1.0, 1.0) float — this is the form Stage 2 (FFT) will actually want,
 * so the conversion is already here and ready to reuse.
 */
esp_err_t mic_capture_read_block(int32_t *out_raw, float *out_normalized,
                                  size_t block_len);

/**
 * Convenience helper: computes min/max/RMS/DC-offset/clip-count over a
 * normalized block. Use this from your verification task to sanity-check
 * the signal before moving on to Stage 2.
 */
void mic_capture_compute_stats(const float *normalized_block, size_t len,
                                mic_block_stats_t *out_stats);

void mic_capture_deinit(void);

/* ── PSRAM pre-trigger ring buffer ──────────────────────────────────────────
 *
 * 4-second circular buffer of int16_t samples, allocated in PSRAM.
 * snapshot_init() is called by mic_capture_init() — no explicit init needed.
 * Data is pushed automatically on every successful mic_capture_read_block().
 *
 * snapshot_read_chunk() iterates the ring buffer in chronological order.
 * Call with chunk_byte_offset=0, advancing by the returned byte count each
 * call, until the return value is 0.
 */
void   snapshot_init(void);
size_t snapshot_count(void);
size_t snapshot_read_chunk(size_t chunk_byte_offset, void *dst, size_t nbytes);

/* ── I2S DMA overflow counter ────────────────────────────────────────────────
 * Incremented inside the I2S event ISR each time a DMA overrun is detected.
 * Call this from wifi_task / diagnostics_task; NOT from an ISR.
 * Returns the total cumulative count since boot (never wraps in normal use). */
uint32_t mic_capture_get_overflow_count(void);

struct mic_inmp441_i2s_stats {
    uint32_t overflow_count; /* same value as mic_capture_get_overflow_count() */
    uint32_t read_errors;    /* i2s_channel_read() calls that returned != ESP_OK */
    uint32_t short_reads;    /* successful reads that returned fewer bytes than requested */
};

/* Per Part I's one-<module>_get_stats()-per-module convention. */
void mic_inmp441_i2s_get_stats(struct mic_inmp441_i2s_stats *out);

#ifdef __cplusplus
}
#endif
