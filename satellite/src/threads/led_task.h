/*
 * led_task.h — Public API for the RGB status-LED task wrapper.
 */

#pragma once

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "hal/hal_display.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Initialises the LEDC hardware (hal_display.h) and launches the RGB
 * animation task on core 1, priority 3. Must be called once from app_main.
 */
void led_task_start(void);

/** Returns the task handle (valid after led_task_start()). Used by diagnostics_task. */
TaskHandle_t led_task_get_handle(void);

/* Per Part I's one-<module>_get_stats()-per-module convention. Forwards to
 * the underlying display driver's rgb_led_get_stats() — led_task.c itself
 * is a thin task wrapper with no counters of its own. */
void led_task_get_stats(struct rgb_led_stats *out);

#ifdef __cplusplus
}
#endif
