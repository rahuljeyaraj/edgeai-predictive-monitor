#ifndef MIC_SAMPLER_TASK_H_
#define MIC_SAMPLER_TASK_H_

#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>

/*
 * Continuous I2S capture + FFT task - mirrors mcu/src/threads/
 * mic_sampler_thread.c/.h's structure (single-channel FFT, no summing
 * needed - that's accel-specific, see accel_sampler_task.h).
 */

/* 1-deep mailbox queue of MIC_FFT_BIN_COUNT floats - see
 * accel_sampler_task.h's accel_spectrum_queue comment for the
 * xQueueOverwrite mailbox semantics this mirrors. */
extern QueueHandle_t mic_spectrum_queue;

int mic_sampler_task_start(void);

#endif /* MIC_SAMPLER_TASK_H_ */
