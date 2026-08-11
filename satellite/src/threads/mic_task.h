/*
 * mic_task.h — Public API for the microphone capture task.
 */

#pragma once

#include <stdint.h>

#include "freertos/FreeRTOS.h"
#include "freertos/ringbuf.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Returns the RingbufHandle for raw_mic_block_t items (mic_task → dsp_task).
 * HW-OPT: esp_ringbuf zero-copy receive — dsp_task gets a pointer into the
 * ring buffer storage, avoiding a full 4-KB memcpy on every block receive.
 * Storage is 8192 bytes in static internal DRAM (DRAM_ATTR).
 * Call AFTER mic_task_start().
 */
RingbufHandle_t mic_task_get_raw_ringbuf(void);

/**
 * Initialises I2S and launches the capture FreeRTOS task on core 0.
 * Must be called once from app_main before dsp_task_start().
 */
void mic_task_start(void);

/** Returns the task handle (valid after mic_task_start()). Used by diagnostics_task. */
TaskHandle_t mic_task_get_handle(void);

struct mic_task_stats {
    uint32_t blocks_ok;         /* blocks successfully captured and posted */
    uint32_t capture_failures;  /* mic_capture_read_block() failures */
    uint32_t rb_drops;          /* blocks dropped: raw_rb full (dsp_task backlogged) */
};

/* Per Part I's one-<module>_get_stats()-per-module convention. */
void mic_task_get_stats(struct mic_task_stats *out);

#ifdef __cplusplus
}
#endif
