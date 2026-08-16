#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include "hal/hal_display_rgb.h"
#include "threads/rgb_display_task.h"

/* Periodic hal_display_rgb_tick() driver - the Arduino/FreeRTOS port of
 * mcu/src/threads/rgb_display_thread.c. Same 20ms tick period. */

#define RGB_DISPLAY_TASK_STACK_WORDS 2048
#define RGB_DISPLAY_TASK_PRIORITY    3
#define RGB_DISPLAY_TICK_MS          20

static void rgb_display_task_entry(void *arg)
{
	(void)arg;

	while (1) {
		hal_display_rgb_tick();
		vTaskDelay(pdMS_TO_TICKS(RGB_DISPLAY_TICK_MS));
	}
}

int rgb_display_task_start(void)
{
	int ret = hal_display_rgb_init();

	if (ret < 0) {
		return ret;
	}

	TaskHandle_t handle = NULL;
	BaseType_t ok = xTaskCreate(rgb_display_task_entry, "rgb_display",
				    RGB_DISPLAY_TASK_STACK_WORDS, NULL, RGB_DISPLAY_TASK_PRIORITY,
				    &handle);

	return ok == pdPASS ? 0 : -1;
}
