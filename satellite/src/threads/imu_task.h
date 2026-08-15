/*
 * imu_task.h — Public API for the IMU capture + FFT task.
 */

#pragma once

#include <stdint.h>

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Returns the QueueHandle for imu_frame_t items (imu_task → net_task).
 * Queue depth is 1 — net_task reads via xQueueReceive;
 * the imu_task posts via xQueueOverwrite so it never blocks.
 * Call AFTER imu_task_start().
 */
QueueHandle_t imu_task_get_queue(void);

/**
 * Initialises the IMU (stub or real KX134 driver) and launches the
 * FreeRTOS task.  Must be called once from app_main.
 */
void imu_task_start(void);

/** Returns the task handle (valid after imu_task_start()). Used by diagnostics_task. */
TaskHandle_t imu_task_get_handle(void);

struct imu_task_stats {
    uint32_t epochs;           /* 3-axis capture epochs attempted since boot */
    uint32_t read_errors;      /* epochs with >= 1 failed hal_accel_read_block() call */
    uint32_t reinit_attempts;  /* hal_accel_reinit() calls after an IMU_FAIL_MAX streak */
    uint32_t reinit_successes; /* of the above, how many returned 0 */
};

/* Per Part I's one-<module>_get_stats()-per-module convention. */
void imu_task_get_stats(struct imu_task_stats *out);

#ifdef __cplusplus
}
#endif
