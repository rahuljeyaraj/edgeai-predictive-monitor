#ifndef ACCEL_SAMPLER_TASK_H_
#define ACCEL_SAMPLER_TASK_H_

#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>

#include "app_config.h"

/*
 * Continuous SPI capture + FFT task - mirrors mcu/src/threads/
 * accel_sampler_thread.c/.h's structure, but each axis (X, Y, Z) is now
 * published independently rather than summed into one combined magnitude
 * spectrum (see accel_sampler_task.cpp's header comment for why: the
 * fault-detection model needs the per-axis directional signature a summed
 * magnitude erases, matching base-station/sketch/fuser.cpp and
 * pipeline/features.py's per-axis SensorChannel design).
 */

#define ACCEL_FFT_LEN (ACCEL_FFT_BIN_COUNT * 2)

/* One axis's FFT result plus its time-domain scalar tile, computed on the
 * same raw window (rms/kurtosis/std/peak/crest_factor/skewness -
 * dsp/scalar_stats.h) - model input alongside the spectrum, per
 * pipeline/features.py's SCALAR_NAMES order. */
struct accel_axis_result {
	float mag[ACCEL_FFT_BIN_COUNT];
	float rms;
	float kurtosis;
	float std;
	float peak;
	float crest_factor;
	float skewness;
};

struct accel_sample {
	struct accel_axis_result x;
	struct accel_axis_result y;
	struct accel_axis_result z;
};

/* 1-deep mailbox queue (xQueueOverwrite/xQueueReceive) of struct
 * accel_sample, holding the latest completed per-axis FFT + scalar result -
 * the sampler -> Fuser handoff, the FreeRTOS-queue equivalent of mcu/'s
 * accel_spectrum_msgq. Always holds the single latest pushed value:
 * xQueueOverwrite() on a length-1 queue replaces any unconsumed value
 * rather than blocking or failing, the same "never stale, never blocks the
 * producer" guarantee mcu/'s purge-before-put pattern provides explicitly. */
extern QueueHandle_t accel_spectrum_queue;

int accel_sampler_task_start(void);

#endif /* ACCEL_SAMPLER_TASK_H_ */
