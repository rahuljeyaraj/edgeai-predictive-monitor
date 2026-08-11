/*
 * display_ledc.c — Hardware-LEDC RGB LED animation engine.
 *
 * All timing (fades and holds) runs in the LEDC fade hardware with zero CPU
 * polling.  The fade-end ISR advances step state and notifies this task only
 * when it needs to program the next step or handle a state-change request.
 *
 * Hold phases are implemented as zero-delta LEDC fades (target == current
 * duty) so the hardware ISR fires at the end of the hold period with no
 * FreeRTOS timer involved.
 *
 * Pattern tables are in DRAM (DRAM_ATTR) so the ISR can read them even if
 * the flash instruction cache is disabled during a WiFi TX burst.
 */

#include <stdint.h>
#include <stdbool.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"

#include "esp_attr.h"
#include "esp_log.h"
#include "driver/ledc.h"

#include "hal/hal_display.h"

static const char *TAG = "rgb_led";

#ifndef ARRAY_SIZE
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#endif

/* GPIO assignments — confirmed free on XIAO ESP32-S3 */
#define RGB_LED_R_GPIO  1
#define RGB_LED_G_GPIO  5
#define RGB_LED_B_GPIO  6

/* LEDC configuration */
#define RGB_LEDC_TIMER      LEDC_TIMER_0
#define RGB_LEDC_MODE       LEDC_LOW_SPEED_MODE
#define RGB_LEDC_RESOLUTION LEDC_TIMER_13_BIT
#define RGB_LEDC_FREQ_HZ    5000
#define RGB_LEDC_CH_R       LEDC_CHANNEL_0
#define RGB_LEDC_CH_G       LEDC_CHANNEL_1
#define RGB_LEDC_CH_B       LEDC_CHANNEL_2

/* Duty levels (13-bit: 0..8191, common-cathode) */
#define RGB_DUTY_FULL    7000
#define RGB_DUTY_OFF     0

/* Colour macros (R, G, B duty) */
#define RGB_WHITE    RGB_DUTY_FULL, RGB_DUTY_FULL, RGB_DUTY_FULL
#define RGB_BLUE     RGB_DUTY_OFF,  RGB_DUTY_OFF,  RGB_DUTY_FULL
#define RGB_CYAN     RGB_DUTY_OFF,  RGB_DUTY_FULL, RGB_DUTY_FULL
#define RGB_YELLOW   RGB_DUTY_FULL, 5950,          RGB_DUTY_OFF
#define RGB_PURPLE   RGB_DUTY_FULL, RGB_DUTY_OFF,  RGB_DUTY_FULL
#define RGB_GREEN    RGB_DUTY_OFF,  RGB_DUTY_FULL, RGB_DUTY_OFF
#define RGB_AMBER    RGB_DUTY_FULL, 2800,          RGB_DUTY_OFF
#define RGB_RED      RGB_DUTY_FULL, RGB_DUTY_OFF,  RGB_DUTY_OFF
#define RGB_MAGENTA  RGB_DUTY_FULL, RGB_DUTY_OFF,  4900
#define RGB_VIOLET   3500,          RGB_DUTY_OFF,  RGB_DUTY_FULL
#define RGB_OFF      RGB_DUTY_OFF,  RGB_DUTY_OFF,  RGB_DUTY_OFF

/* ── Pattern step ───────────────────────────────────────────────────────── */

typedef struct {
    uint32_t r;
    uint32_t g;
    uint32_t b;
    uint32_t fade_ms;
    uint32_t hold_ms;
    bool     loop;
} led_step_t;

/* ── Pattern tables — must be in DRAM for ISR access ────────────────────── */

static const DRAM_ATTR led_step_t pat_boot[] = {
    {RGB_WHITE, 0, 60000, true},
};

static const DRAM_ATTR led_step_t pat_wifi[] = {
    {RGB_BLUE, 1200, 0, false},
    {RGB_OFF,  1200, 0, true},
};

static const DRAM_ATTR led_step_t pat_tcp[] = {
    {RGB_CYAN, 400, 100, false},
    {RGB_OFF,  400, 300, true},
};

/* Mirrors pat_wifi's fade-breathe shape (same 900ms fade-in/out this state
 * uses on the NeoPixel driver's cosine breathe, display_neopixel.c) but in
 * violet instead of blue -- keeps a broker-level MQTT stall visually
 * distinct from a real WiFi-association drop on this Kconfig fallback too,
 * not just on the default NeoPixel build. */
static const DRAM_ATTR led_step_t pat_mqtt_stall[] = {
    {RGB_VIOLET, 900, 0, false},
    {RGB_OFF,    900, 0, true},
};

static const DRAM_ATTR led_step_t pat_cal[] = {
    {RGB_YELLOW, 800, 0, false},
    {RGB_OFF,    800, 0, true},
};

static const DRAM_ATTR led_step_t pat_learn[] = {
    {RGB_PURPLE, 600, 0, false},
    {RGB_OFF,    600, 0, true},
};

static const DRAM_ATTR led_step_t pat_ok[] = {
    {RGB_GREEN, 0, 80,   false},
    {RGB_OFF,   0, 2920, true},
};

static const DRAM_ATTR led_step_t pat_warn[] = {
    {RGB_AMBER, 0, 100, false},
    {RGB_OFF,   0, 100, true},
};

static const DRAM_ATTR led_step_t pat_fault[] = {
    {RGB_RED, 0, 50,  false},
    {RGB_OFF, 0, 50,  false},
    {RGB_RED, 0, 50,  false},
    {RGB_OFF, 0, 50,  false},
    {RGB_RED, 0, 50,  false},
    {RGB_OFF, 0, 50,  false},
    {RGB_RED, 0, 50,  false},
    {RGB_OFF, 0, 50,  false},
    {RGB_RED, 0, 50,  false},
    {RGB_OFF, 0, 500, true},
};

static const DRAM_ATTR led_step_t pat_tripped[] = {
    /* S */
    {RGB_MAGENTA, 0, 100, false}, {RGB_OFF, 0, 100, false},
    {RGB_MAGENTA, 0, 100, false}, {RGB_OFF, 0, 100, false},
    {RGB_MAGENTA, 0, 100, false}, {RGB_OFF, 0, 100, false},
    /* O */
    {RGB_MAGENTA, 0, 400, false}, {RGB_OFF, 0, 100, false},
    {RGB_MAGENTA, 0, 400, false}, {RGB_OFF, 0, 100, false},
    {RGB_MAGENTA, 0, 400, false}, {RGB_OFF, 0, 100, false},
    /* S */
    {RGB_MAGENTA, 0, 100, false}, {RGB_OFF, 0, 100, false},
    {RGB_MAGENTA, 0, 100, false}, {RGB_OFF, 0, 100, false},
    {RGB_MAGENTA, 0, 100, false}, {RGB_OFF, 0, 100, false},
    /* pause */
    {RGB_OFF, 0, 2000, true},
};

/* ── Animation engine state — in DRAM for ISR access ────────────────────── */

typedef struct {
    const led_step_t *pattern;
    uint8_t           n_steps;
    volatile uint8_t  step;
    volatile uint8_t  phase;
} anim_state_t;

static DRAM_ATTR anim_state_t g_anim;
static TaskHandle_t           g_rgb_task_handle = NULL;
static QueueHandle_t          g_state_queue     = NULL;

/* ── Stats (Part I: one <module>_get_stats() accessor per module) ────────── */

static uint32_t s_state_changes  = 0;
static uint32_t s_hw_errors      = 0;
static bool     s_hw_fail_logged = false; /* log once per failure streak, not every call */

void rgb_led_get_stats(struct rgb_led_stats *out)
{
    if (out == NULL) return;
    out->state_changes  = s_state_changes;
    out->remote_updates = 0; /* this driver does not implement rgb_led_set_remote() */
    out->hw_errors      = s_hw_errors;
}

static void anim_note_result(bool ok)
{
    if (!ok) {
        s_hw_errors++;
        if (!s_hw_fail_logged) {
            ESP_LOGW(TAG, "LEDC hardware fade/stop call failed");
            s_hw_fail_logged = true;
        }
    } else {
        s_hw_fail_logged = false;
    }
}

/* ── LEDC helpers ────────────────────────────────────────────────────────── */

static void anim_program_step(anim_state_t *a)
{
    const led_step_t *s = &a->pattern[a->step];
    bool ok = true;
    ok &= (ledc_set_fade_with_time(RGB_LEDC_MODE, RGB_LEDC_CH_R, s->r, s->fade_ms) == ESP_OK);
    ok &= (ledc_set_fade_with_time(RGB_LEDC_MODE, RGB_LEDC_CH_G, s->g, s->fade_ms) == ESP_OK);
    ok &= (ledc_set_fade_with_time(RGB_LEDC_MODE, RGB_LEDC_CH_B, s->b, s->fade_ms) == ESP_OK);
    ok &= (ledc_fade_start(RGB_LEDC_MODE, RGB_LEDC_CH_R, LEDC_FADE_NO_WAIT) == ESP_OK);
    ok &= (ledc_fade_start(RGB_LEDC_MODE, RGB_LEDC_CH_G, LEDC_FADE_NO_WAIT) == ESP_OK);
    ok &= (ledc_fade_start(RGB_LEDC_MODE, RGB_LEDC_CH_B, LEDC_FADE_NO_WAIT) == ESP_OK);
    anim_note_result(ok);
}

static void anim_start(anim_state_t *a, const led_step_t *pat, uint8_t n)
{
    a->pattern = pat;
    a->n_steps = n;
    a->step    = 0;
    a->phase   = 0;
    anim_program_step(a);
}

static void anim_stop(void)
{
    bool ok = true;
    ok &= (ledc_stop(RGB_LEDC_MODE, RGB_LEDC_CH_R, 0) == ESP_OK);
    ok &= (ledc_stop(RGB_LEDC_MODE, RGB_LEDC_CH_G, 0) == ESP_OK);
    ok &= (ledc_stop(RGB_LEDC_MODE, RGB_LEDC_CH_B, 0) == ESP_OK);
    anim_note_result(ok);
}

static void anim_continue(anim_state_t *a)
{
    if (a->phase == 0) {
        anim_program_step(a);
    } else {
        /* phase == 1: ISR asked us to start the hold (zero-delta fade).
         * Must be done here in task context — ledc_set_fade_with_time
         * acquires a mutex and cannot be called from the fade-done ISR. */
        const led_step_t *s = &a->pattern[a->step];
        bool ok = true;
        ok &= (ledc_set_fade_with_time(RGB_LEDC_MODE, RGB_LEDC_CH_R, s->r, s->hold_ms) == ESP_OK);
        ok &= (ledc_set_fade_with_time(RGB_LEDC_MODE, RGB_LEDC_CH_G, s->g, s->hold_ms) == ESP_OK);
        ok &= (ledc_set_fade_with_time(RGB_LEDC_MODE, RGB_LEDC_CH_B, s->b, s->hold_ms) == ESP_OK);
        ok &= (ledc_fade_start(RGB_LEDC_MODE, RGB_LEDC_CH_R, LEDC_FADE_NO_WAIT) == ESP_OK);
        ok &= (ledc_fade_start(RGB_LEDC_MODE, RGB_LEDC_CH_G, LEDC_FADE_NO_WAIT) == ESP_OK);
        ok &= (ledc_fade_start(RGB_LEDC_MODE, RGB_LEDC_CH_B, LEDC_FADE_NO_WAIT) == ESP_OK);
        anim_note_result(ok);
    }
}

static void anim_start_for_state(rgb_led_state_t state)
{
    switch (state) {
    case RGB_BOOT:        anim_start(&g_anim, pat_boot,    ARRAY_SIZE(pat_boot));    break;
    case RGB_WIFI_CONN:   anim_start(&g_anim, pat_wifi,    ARRAY_SIZE(pat_wifi));    break;
    case RGB_TCP_CONN:    anim_start(&g_anim, pat_tcp,     ARRAY_SIZE(pat_tcp));     break;
    case RGB_MQTT_STALL:  anim_start(&g_anim, pat_mqtt_stall, ARRAY_SIZE(pat_mqtt_stall)); break;
    case RGB_CALIBRATING: anim_start(&g_anim, pat_cal,     ARRAY_SIZE(pat_cal));     break;
    case RGB_LEARNING:    anim_start(&g_anim, pat_learn,   ARRAY_SIZE(pat_learn));   break;
    case RGB_OK:          anim_start(&g_anim, pat_ok,      ARRAY_SIZE(pat_ok));      break;
    case RGB_WARN:        anim_start(&g_anim, pat_warn,    ARRAY_SIZE(pat_warn));    break;
    case RGB_FAULT:       anim_start(&g_anim, pat_fault,   ARRAY_SIZE(pat_fault));   break;
    case RGB_TRIPPED:     anim_start(&g_anim, pat_tripped, ARRAY_SIZE(pat_tripped)); break;
    default:              break;
    }
}

/* ── Fade-end ISR — called by LEDC hardware on every fade/hold completion ── */

static IRAM_ATTR bool rgb_fade_done_isr(const ledc_cb_param_t *param, void *user_arg)
{
    /* All three channels are registered; only CH_R drives state to avoid
     * triple-firing per step. */
    if (param->channel != RGB_LEDC_CH_R) return false;

    anim_state_t     *a = (anim_state_t *)user_arg;
    const led_step_t *s = &a->pattern[a->step];

    if (a->phase == 0 && s->hold_ms > 0) {
        /* Fade done; signal task to start hold phase.
         * ledc_set_fade_with_time() is NOT ISR-safe — it acquires a mutex
         * (_ledc_fade_hw_acquire → xQueueSemaphoreTake).  Calling it here
         * blocks CPU0 in vListInsert() and triggers the interrupt WDT. */
        a->phase = 1;
    } else {
        /* Hold done (or no hold): advance step. Task will program next. */
        a->phase = 0;
        a->step  = s->loop ? 0 : (uint8_t)(a->step + 1);
    }

    BaseType_t woken = pdFALSE;
    vTaskNotifyGiveFromISR(g_rgb_task_handle, &woken);
    return woken == pdTRUE;
}

/* ── Public API ──────────────────────────────────────────────────────────── */

void rgb_led_task_init(void)
{
    ledc_timer_config_t timer_cfg = {
        .speed_mode      = RGB_LEDC_MODE,
        .duty_resolution = RGB_LEDC_RESOLUTION,
        .timer_num       = RGB_LEDC_TIMER,
        .freq_hz         = RGB_LEDC_FREQ_HZ,
        .clk_cfg         = LEDC_AUTO_CLK,
    };
    ESP_ERROR_CHECK(ledc_timer_config(&timer_cfg));

    const int            gpios[3] = {RGB_LED_R_GPIO, RGB_LED_G_GPIO, RGB_LED_B_GPIO};
    const ledc_channel_t chs[3]   = {RGB_LEDC_CH_R,  RGB_LEDC_CH_G,  RGB_LEDC_CH_B};
    for (int i = 0; i < 3; i++) {
        ledc_channel_config_t ch_cfg = {
            .speed_mode = RGB_LEDC_MODE,
            .channel    = chs[i],
            .timer_sel  = RGB_LEDC_TIMER,
            .intr_type  = LEDC_INTR_DISABLE,
            .gpio_num   = gpios[i],
            .duty       = 0,
            .hpoint     = 0,
        };
        ESP_ERROR_CHECK(ledc_channel_config(&ch_cfg));
    }

    ESP_ERROR_CHECK(ledc_fade_func_install(0));

    ledc_cbs_t cbs = { .fade_cb = rgb_fade_done_isr };
    ledc_cb_register(RGB_LEDC_MODE, RGB_LEDC_CH_R, &cbs, &g_anim);
    ledc_cb_register(RGB_LEDC_MODE, RGB_LEDC_CH_G, &cbs, &g_anim);
    ledc_cb_register(RGB_LEDC_MODE, RGB_LEDC_CH_B, &cbs, &g_anim);

    g_state_queue = xQueueCreate(1, sizeof(rgb_led_state_t));
    configASSERT(g_state_queue != NULL);

    ESP_LOGI(TAG, "LEDC RGB init: R=GPIO%d G=GPIO%d B=GPIO%d  %u Hz 13-bit",
             RGB_LED_R_GPIO, RGB_LED_G_GPIO, RGB_LED_B_GPIO, (unsigned)RGB_LEDC_FREQ_HZ);
}

void rgb_led_set_state(rgb_led_state_t state)
{
    s_state_changes++;
    if (g_state_queue) {
        xQueueOverwrite(g_state_queue, &state);
    }
    if (g_rgb_task_handle) {
        xTaskNotifyGive(g_rgb_task_handle);
    }
}

void rgb_led_task(void *arg)
{
    (void)arg;
    g_rgb_task_handle = xTaskGetCurrentTaskHandle();

    rgb_led_state_t current_state = RGB_BOOT;
    rgb_led_state_t queued_state;

    anim_start(&g_anim, pat_boot, ARRAY_SIZE(pat_boot));

    while (1) {
        ulTaskNotifyTake(pdFALSE, pdMS_TO_TICKS(5000));

        if (xQueueReceive(g_state_queue, &queued_state, 0) == pdTRUE) {
            if (queued_state != current_state) {
                current_state = queued_state;
                anim_stop();
                anim_start_for_state(current_state);
            }
        } else {
            anim_continue(&g_anim);
        }
    }
}
