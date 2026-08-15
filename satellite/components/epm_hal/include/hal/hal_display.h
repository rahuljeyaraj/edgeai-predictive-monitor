#pragma once

#include <stdint.h>

/*
 * Status-LED contract for the RGB indicator (matches the reference repo's
 * satellite/include/hal/hal_display_rgb.h role: one status enum, driven by
 * the DSP/WiFi tasks). components/epm_drivers/display_ledc.c is the only
 * implementation, using the LEDC hardware-fade engine (see its own header
 * comment); a future NeoPixel swap (due before Phase 9 closes) implements
 * this same contract.
 *
 * Function/enum names keep their original rgb_led_* spelling rather than
 * being renamed to hal_display_* — this is a pure move of the existing
 * public API into its HAL home, not a rename, to avoid rippling call-site
 * changes through dsp_task.c/wifi_task.c for no behavioural benefit.
 *
 * Zero ESP-IDF includes here (Part C.1): rgb_led_task's entry-point
 * signature is plain void* so it needs no FreeRTOS types, and it is passed
 * to xTaskCreatePinnedToCore by src/threads/led_task.c, which owns the
 * TaskHandle_t and its own led_task_get_handle() accessor — the FreeRTOS
 * task-handle API duplicated here previously was dead (no callers) and has
 * been removed.
 */

typedef enum {
    RGB_BOOT = 0,
    RGB_WIFI_CONN,
    RGB_TCP_CONN,
    RGB_MQTT_STALL,
    RGB_CALIBRATING,
    RGB_LEARNING,
    RGB_OK,
    RGB_WARN,
    RGB_FAULT,
    RGB_TRIPPED,
    RGB_STATE_MAX,
} rgb_led_state_t;

/* Call once from app_main before task creation. */
void rgb_led_task_init(void);

/* Set LED state. Safe from any task context. Non-blocking. */
void rgb_led_set_state(rgb_led_state_t state);

/* Drives the ring directly from an inbound STATUS_LED command's raw
 * (rgb, mode, period_ms) triple, bypassing the local rgb_led_state_t enum
 * table entirely. mode uses the same CONST=0/BREATHE=1/STROBE=2 encoding as
 * frame_codec/wire_protocol.h's display_rgb_payload — an out-of-range mode
 * value falls back to CONST rather than being treated as an error, since
 * the ring must still render *something* sane for a value this API cannot
 * itself reject earlier in the pipeline. Last write wins against
 * rgb_led_set_state() on the same underlying single-slot queue — see
 * docs/decisions/ADR-025-remote-status-led-priority.md for why that's
 * sufficient instead of a separate priority mechanism. Safe from any task
 * context. Non-blocking. */
void rgb_led_set_remote(uint32_t rgb, uint8_t mode, uint16_t period_ms);

/* Task function — pin to core 1, priority 3, stack 3072. */
void rgb_led_task(void *arg);

struct rgb_led_stats {
    uint32_t state_changes;  /* rgb_led_set_state() calls */
    uint32_t remote_updates; /* rgb_led_set_remote() calls */
    uint32_t hw_errors;      /* underlying LED hardware write failures */
};

/* Per Part I's one-<module>_get_stats()-per-module convention. Shared HAL
 * contract (not module-prefixed): both display_ledc.c and display_neopixel.c
 * implement this, matching the naming precedent of the other rgb_led_*
 * functions above. */
void rgb_led_get_stats(struct rgb_led_stats *out);
