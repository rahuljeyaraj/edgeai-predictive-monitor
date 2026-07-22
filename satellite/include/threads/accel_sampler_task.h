#ifndef ACCEL_SAMPLER_TASK_H_
#define ACCEL_SAMPLER_TASK_H_

#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>

/*
 * Continuous SPI capture + FFT task - mirrors mcu/src/threads/
 * accel_sampler_thread.c/.h exactly in structure: three axes (X, Y, Z)
 * each FFT'd independently, then summed bin-by-bin into one combined
 * magnitude spectrum (see accel_sampler_task.cpp's header comment for why
 * summing, not max-picking - same vibration-directionality reasoning
 * mcu/'s version documents, carried over unchanged since it's a signal-
 * processing decision independent of MCU vs. ESP32).
 */

/* 1-deep mailbox queue (xQueueOverwrite/xQueueReceive) of
 * ACCEL_FFT_BIN_COUNT floats, holding the latest completed (summed,
 * 3-axis) FFT magnitude spectrum - the sampler -> Fuser handoff, the
 * FreeRTOS-queue equivalent of mcu/'s accel_spectrum_msgq. Always holds
 * the single latest pushed value: xQueueOverwrite() on a length-1 queue
 * replaces any unconsumed value rather than blocking or failing, the same
 * "never stale, never blocks the producer" guarantee mcu/'s purge-before-
 * put pattern provides explicitly. */
extern QueueHandle_t accel_spectrum_queue;

int accel_sampler_task_start(void);

#endif /* ACCEL_SAMPLER_TASK_H_ */
