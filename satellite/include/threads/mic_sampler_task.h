#ifndef MIC_SAMPLER_TASK_H_
#define MIC_SAMPLER_TASK_H_

#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>

#include "app_config.h"

/*
 * Continuous I2S capture + FFT task - mirrors mcu/src/threads/
 * mic_sampler_thread.c/.h's structure (single-channel FFT, no summing
 * needed - that's accel-specific, see accel_sampler_task.h).
 */

#define MIC_FFT_LEN (MIC_FFT_BIN_COUNT * 2)

/* Spectrum plus the time-domain scalar tile computed on the same raw
 * window (rms/kurtosis/std/peak/crest_factor/skewness - dsp/
 * scalar_stats.h), model input alongside the spectrum, per
 * pipeline/features.py's SCALAR_NAMES order. */
struct mic_sample {
	float mag[MIC_FFT_BIN_COUNT];
	float rms;
	float kurtosis;
	float std;
	float peak;
	float crest_factor;
	float skewness;
};

/* 1-deep mailbox queue of struct mic_sample - see
 * accel_sampler_task.h's accel_spectrum_queue comment for the
 * xQueueOverwrite mailbox semantics this mirrors. */
extern QueueHandle_t mic_spectrum_queue;

int mic_sampler_task_start(void);

#endif /* MIC_SAMPLER_TASK_H_ */
