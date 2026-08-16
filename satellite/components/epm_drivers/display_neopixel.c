/*
 * display_neopixel.c — WS2812 (NeoPixel) status LED driver behind
 * hal_display.h, replacing display_ledc.c as the default (that file stays
 * in the tree as a Kconfig fallback, CONFIG_EPM_DISPLAY_USE_LEDC).
 *
 * Ports the reference repo's satellite/src/drivers/rgb_ws2812.cpp (Arduino
 * Adafruit_NeoPixel) + satellite/include/hal/hal_display_rgb.h contract —
 * same CONST/BREATHE/STROBE vocabulary and the same set()/tick() cosine
 * breathe / square-wave strobe math — re-expressed here as a single task
 * loop instead of an externally-polled tick(), because our hal_display.h
 * contract (unlike the reference's) owns the task loop itself: led_task.c
 * calls rgb_led_task_init() then hands rgb_led_task() straight to
 * xTaskCreatePinnedToCore, the same structure display_ledc.c already uses.
 *
 * Pixel push uses the espressif/led_strip managed component (RMT-backed),
 * the standard ESP-IDF 5.x driver for addressable LEDs — used the same way
 * mic_inmp441_i2s.c uses driver/i2s_std.h and accel_kx134_spi.c uses
 * driver/spi_master.h: a native ESP-IDF hardware-protocol driver behind our
 * own HAL contract, not a hand-rolled bit-bang implementation.
 *
 * Color/mode table matches the reference's base-station/python/registry/
 * status_color.py exactly for every rgb_led_state_t that has a NodeStatus
 * analog (OK/WARN/FAULT/CALIBRATING/LEARNING/TRIPPED), so status meaning is
 * identical on both sides of the wire. States with no NodeStatus analog
 * (BOOT/WIFI_CONN/TCP_CONN — connectivity bring-up before the node has
 * joined the network at all, a state status_color.py's NodeStatus enum has
 * no concept of since a node isn't in the registry until it's already
 * reporting) keep locally-chosen near-primary colors. See
 * docs/decisions/ADR-016 for the full rationale.
 */

#include <math.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"

#include "esp_log.h"
#include "esp_timer.h"
#include "led_strip.h"

#include "hal/hal_display.h"

static const char *TAG = "neopixel";

/* GPIO6 (D5) — reuses display_ledc.c's RGB_LED_B_GPIO. Safe: only one of
 * the two display drivers is ever compiled in (Kconfig-exclusive), so
 * there is no runtime pin conflict, and it keeps this pin's role as "the
 * display driver's pin" consistent regardless of which one is selected.
 * Does not conflict with the INMP441 mic (GPIO2/3/4, mic_inmp441_i2s.c). */
#define WS2812_GPIO 6

/* Physical hardware confirmed as an 8-LED WS2812 ring (Phase 7c hardware
 * validation session). All N pixels are always set identically below. */
#define WS2812_NUM_PIXELS 8

typedef enum {
    MODE_CONST = 0,
    MODE_BREATHE,
    MODE_STROBE,
} display_mode_t;

typedef struct {
    uint32_t       rgb;
    display_mode_t mode;
    uint16_t       period_ms;
} led_pattern_t;

/* rgb/mode/period_ms per state. OK/WARN/FAULT/CALIBRATING/LEARNING/TRIPPED
 * match status_color.py's _GREEN_HEALTHY / _YELLOW_WARNING_BREATHE /
 * _RED_FAULT_STROBE / _CYAN_NEW / _RED_TRIPPED_SLOW exactly (color, mode,
 * and period_ms). CALIBRATING and LEARNING both map to his single
 * "commissioning" cyan (_CYAN_NEW covers UNCOMMISSIONED +
 * COMMISSIONING_COLLECTING + COMMISSIONING_TRAINING) and both use CONST,
 * matching status_color.py exactly rather than distinguishing our two local
 * sub-phases on-device (see ADR-016's 2026-08-06 addendum for why exact
 * parity was chosen over that local distinction). BOOT/WIFI_CONN/TCP_CONN/
 * MQTT_STALL have no NodeStatus analog (status_color.py has nothing for
 * "not yet joined the network" -- a node isn't in his registry until it's
 * already reporting) -- colors chosen locally, kept in the blue family
 * (mirrors display_ledc.c's old blue-wifi/cyan-tcp pattern, recolored off
 * cyan since that's now reserved for CALIBRATING). MQTT_STALL (added after
 * the 2026-08-11 stress test found net_task.c's MQTT-disconnect revert was
 * silently reusing RGB_WIFI_CONN, making a broker-level stall
 * indistinguishable from a real WiFi-association drop -- see ADR-025's
 * 2026-08-11 addendum) uses violet, chosen specifically because it is
 * unused by both this table and status_color.py's own palette (checked
 * against the reference repo directly, not assumed). */
static const led_pattern_t k_pattern[RGB_STATE_MAX] = {
    [RGB_BOOT]        = { 0xFFFFFF, MODE_CONST,   0    },
    [RGB_WIFI_CONN]   = { 0x0000FF, MODE_BREATHE, 1200 },
    [RGB_TCP_CONN]    = { 0x0000FF, MODE_STROBE,  300  },
    [RGB_MQTT_STALL]  = { 0xBB00FF, MODE_BREATHE, 900  },
    [RGB_CALIBRATING] = { 0x22D3EE, MODE_CONST,   0    },
    [RGB_LEARNING]    = { 0x22D3EE, MODE_CONST,   0    },  /* unused: reserved for a
        future two-phase commissioning sequence, not a bug -- see comment above. */
    [RGB_OK]          = { 0x00FF00, MODE_CONST,   0    },
    [RGB_WARN]        = { 0xF59E0B, MODE_BREATHE, 1500 },
    [RGB_FAULT]       = { 0xFF0000, MODE_STROBE,  200  },
    [RGB_TRIPPED]     = { 0xFF0000, MODE_STROBE,  1000 },
};

#define BREATHE_PI 3.14159265f
#define ANIM_TICK_MS 30

/* Single-slot queue payload: either a local rgb_led_state_t (via
 * rgb_led_set_state()) or a remote (rgb, mode, period_ms) triple (via
 * rgb_led_set_remote()) — xQueueOverwrite() makes whichever call lands most
 * recently the one the task applies next (ADR-025's last-write-wins). */
typedef struct {
    bool            is_remote;
    rgb_led_state_t state;   /* valid when !is_remote */
    led_pattern_t   remote;  /* valid when is_remote */
} led_cmd_t;

static led_strip_handle_t s_strip = NULL;
static QueueHandle_t      s_state_queue = NULL;
static TaskHandle_t       s_task_handle = NULL;

/* ── Stats (Part I: one <module>_get_stats() accessor per module) ────────── */

static uint32_t s_state_changes  = 0;
static uint32_t s_remote_updates = 0;
static uint32_t s_hw_errors      = 0;
static bool     s_hw_fail_logged = false; /* log once per failure streak, not every tick */

void rgb_led_get_stats(struct rgb_led_stats *out)
{
    if (out == NULL) return;
    out->state_changes  = s_state_changes;
    out->remote_updates = s_remote_updates;
    out->hw_errors      = s_hw_errors;
}

static void set_ring(uint32_t rgb, uint8_t scale_pct)
{
    uint8_t r = (uint8_t)((((rgb >> 16) & 0xFF) * scale_pct) / 100);
    uint8_t g = (uint8_t)((((rgb >> 8)  & 0xFF) * scale_pct) / 100);
    uint8_t b = (uint8_t)(((rgb & 0xFF) * scale_pct) / 100);

    bool ok = true;
    for (int i = 0; i < WS2812_NUM_PIXELS; i++) {
        if (led_strip_set_pixel(s_strip, i, r, g, b) != ESP_OK) ok = false;
    }
    if (led_strip_refresh(s_strip) != ESP_OK) ok = false;

    if (!ok) {
        s_hw_errors++;
        if (!s_hw_fail_logged) {
            ESP_LOGW(TAG, "WS2812 hardware write failed (RMT/strip error)");
            s_hw_fail_logged = true;
        }
    } else {
        s_hw_fail_logged = false;
    }
}

void rgb_led_task_init(void)
{
    led_strip_config_t strip_config = {
        .strip_gpio_num = WS2812_GPIO,
        .max_leds = WS2812_NUM_PIXELS,
        .led_model = LED_MODEL_WS2812,
        .color_component_format = LED_STRIP_COLOR_COMPONENT_FMT_GRB,
        .flags = { .invert_out = false },
    };
    led_strip_rmt_config_t rmt_config = {
        .clk_src = RMT_CLK_SRC_DEFAULT,
        .resolution_hz = 10 * 1000 * 1000,
        .flags = { .with_dma = false },
    };
    ESP_ERROR_CHECK(led_strip_new_rmt_device(&strip_config, &rmt_config, &s_strip));

    /* Blank the ring: WS2812 pixels keep whatever they last latched until
     * told otherwise, unlike a simple PWM LED that resets to off. */
    led_strip_clear(s_strip);

    s_state_queue = xQueueCreate(1, sizeof(led_cmd_t));
    configASSERT(s_state_queue != NULL);

    ESP_LOGI(TAG, "WS2812 init: DIN=GPIO%d, %d pixel(s)", WS2812_GPIO, WS2812_NUM_PIXELS);
}

void rgb_led_set_state(rgb_led_state_t state)
{
    led_cmd_t cmd = { .is_remote = false, .state = state };
    s_state_changes++;

    if (s_state_queue) {
        xQueueOverwrite(s_state_queue, &cmd);
    }
    if (s_task_handle) {
        xTaskNotifyGive(s_task_handle);
    }
}

void rgb_led_set_remote(uint32_t rgb, uint8_t mode, uint16_t period_ms)
{
    led_cmd_t cmd = {
        .is_remote = true,
        .remote = {
            .rgb = rgb,
            .mode = (mode <= MODE_STROBE) ? (display_mode_t)mode : MODE_CONST,
            .period_ms = period_ms,
        },
    };
    s_remote_updates++;

    if (s_state_queue) {
        xQueueOverwrite(s_state_queue, &cmd);
    }
    if (s_task_handle) {
        xTaskNotifyGive(s_task_handle);
    }
}

void rgb_led_task(void *arg)
{
    (void)arg;
    s_task_handle = xTaskGetCurrentTaskHandle();

    led_pattern_t current_pattern = k_pattern[RGB_BOOT];
    led_cmd_t     queued_cmd;
    int64_t       state_start_ms = esp_timer_get_time() / 1000;

    set_ring(current_pattern.rgb, 100);

    while (1) {
        bool animated = (current_pattern.mode != MODE_CONST) && (current_pattern.period_ms > 0);

        TickType_t wait = animated ? pdMS_TO_TICKS(ANIM_TICK_MS) : portMAX_DELAY;
        ulTaskNotifyTake(pdFALSE, wait);

        if (xQueueReceive(s_state_queue, &queued_cmd, 0) == pdTRUE) {
            current_pattern = queued_cmd.is_remote ? queued_cmd.remote : k_pattern[queued_cmd.state];
            state_start_ms = esp_timer_get_time() / 1000;
            set_ring(current_pattern.rgb, 100);
            continue;
        }

        if (!animated) {
            continue;
        }

        int64_t elapsed = (esp_timer_get_time() / 1000) - state_start_ms;
        float   phase   = (float)(elapsed % current_pattern.period_ms) / (float)current_pattern.period_ms;
        uint8_t scale_pct;

        if (current_pattern.mode == MODE_BREATHE) {
            scale_pct = (uint8_t)((1.0f - cosf(phase * 2.0f * BREATHE_PI)) * 50.0f);
        } else {
            scale_pct = (phase < 0.5f) ? 100 : 0;
        }
        set_ring(current_pattern.rgb, scale_pct);
    }
}
