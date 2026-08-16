#ifndef RGB_DISPLAY_TASK_H_
#define RGB_DISPLAY_TASK_H_

/*
 * Periodic hal_display_rgb_tick() driver - mirrors mcu/src/threads/
 * rgb_display_thread.c/.h exactly (same 20ms tick period, same reason:
 * BREATHE/STROBE need a steady tick independent of MQTT/sampler load).
 */

int rgb_display_task_start(void);

#endif /* RGB_DISPLAY_TASK_H_ */
