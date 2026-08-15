/*
 * dsp_task.h — Public API for the DSP compute task (core 1).
 */

#pragma once

#include <stdint.h>

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/ringbuf.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Returns the QueueHandle for mic_frame_t items (dsp_task → net_task).
 * Queue depth is 1 — net_task reads; dsp_task posts via xQueueOverwrite.
 * Call AFTER dsp_task_start().
 */
QueueHandle_t dsp_task_get_queue(void);

/**
 * Initialises the Hann window table and launches the DSP FreeRTOS task on
 * core 1.  raw_rb must be the ring buffer returned by mic_task_get_raw_ringbuf().
 * Call AFTER mic_task_start().
 */
void dsp_task_start(RingbufHandle_t raw_rb);

/** Returns the task handle (valid after dsp_task_start()). Used by diagnostics_task. */
TaskHandle_t dsp_task_get_handle(void);

struct dsp_task_stats {
    uint32_t fft_count;       /* FFT windows computed since boot */
    uint32_t frames_emitted;  /* averaged mic_frame_t frames posted to net_task */
    uint32_t rb_timeouts;     /* raw_rb receive timeouts (no data from mic_task) */
    uint32_t last_fft_us;     /* one-shot FFT benchmark duration, microseconds */
};

/* Per Part I's one-<module>_get_stats()-per-module convention. */
void dsp_task_get_stats(struct dsp_task_stats *out);

#ifdef __cplusplus
}
#endif
