/*
 * led_task.c — thin task wrapper around the display_ledc.c HAL driver.
 *
 * Replicates exactly what main.c used to do inline: init the LEDC hardware,
 * pin the animation task to core 1 priority 3, then kick it to RGB_BOOT.
 */

#include "threads/led_task.h"

#include "hal/hal_display.h"

static TaskHandle_t s_task_handle = NULL;

void led_task_start(void)
{
    rgb_led_task_init();
    xTaskCreatePinnedToCore(rgb_led_task, "rgb_led", 3072, NULL, 3, &s_task_handle, 1);
    rgb_led_set_state(RGB_BOOT);
}

TaskHandle_t led_task_get_handle(void)
{
    return s_task_handle;
}

void led_task_get_stats(struct rgb_led_stats *out)
{
    rgb_led_get_stats(out);
}
