/*
 * main.c — EPM (EdgeAI Predictive Monitor) entry point.
 *
 * Initialises system services and starts the FreeRTOS tasks:
 *
 *   Core 0 — Radio and peripheral capture (time-critical I/O)
 *   ┌────────────────────────────────────────────────────────┐
 *   │ net_task         priority 4   stack 4096  (MQTT publish)│
 *   │ mic_task         priority 5   stack 8192  (I2S DMA)    │
 *   │ imu_task         priority 3   stack 4096  (SPI DMA)    │
 *   │ diagnostics_task priority 1   stack 3072  (health mon) │
 *   └────────────────────────────────────────────────────────┘
 *
 * WiFi STA lifecycle (wifi_rf_init()/wifi_wait_connected()) is event-driven,
 * not a task of its own — see src/threads/wifi_task.h.
 *
 *   Core 1 — Compute (no radio interference)
 *   ┌────────────────────────────────────────────────────────┐
 *   │ dsp_task       priority 6   stack 6144   (FFT compute) │
 *   │ rgb_led_task   priority 3   stack 3072   (LEDC HW)     │
 *   └────────────────────────────────────────────────────────┘
 *
 * FFT table note: dsps_fft2r_init_fc32 with NULL uses a shared static table
 * and is NOT thread-safe.  We initialise once here with the larger FFT_IMU_N
 * (2048) before any tasks start.  A 2048-pt table is a superset of 1024-pt —
 * both tasks can use it concurrently without re-initialising.
 */

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_err.h"
#include "esp_system.h"
#include "nvs_flash.h"
#include "esp_netif.h"
#include "esp_event.h"
#include "esp_wifi.h"
#include "esp_heap_caps.h"

#include "dsps_fft2r.h"

#include "epm_config.h"
#include "threads/led_task.h"
#include "threads/mic_task.h"
#include "threads/dsp_task.h"
#include "threads/imu_task.h"
#include "threads/wifi_task.h"
#include "threads/wifi_provision_task.h"
#include "drivers/mic_inmp441_i2s.h"
#include "threads/net_task.h"
#include "hal/hal_accel.h"
#include "drivers/link_mqtt.h"

static const char *TAG = "main";

/* ── Diagnostics task ─────────────────────────────────────────────────────── */

typedef struct {
    TaskHandle_t h_mic;
    TaskHandle_t h_dsp;
    TaskHandle_t h_rgb;
    TaskHandle_t h_imu;
} diag_args_t;

static diag_args_t s_diag_args;

static void diagnostics_task_fn(void *arg)
{
    diag_args_t  *a     = (diag_args_t *)arg;
    TaskHandle_t  h_diag = xTaskGetCurrentTaskHandle();

    while (1) {
        vTaskDelay(pdMS_TO_TICKS(30000));   /* 30 s health interval */

        /* Stack watermarks — minimum ever-free bytes (IDF 5.x returns bytes).
         * A value approaching 0 signals an imminent stack overflow. */
        ESP_LOGI("DIAG", "Stack HWM (bytes free): mic=%lu dsp=%lu rgb=%lu imu=%lu diag=%lu",
            (unsigned long)uxTaskGetStackHighWaterMark(a->h_mic),
            (unsigned long)uxTaskGetStackHighWaterMark(a->h_dsp),
            (unsigned long)uxTaskGetStackHighWaterMark(a->h_rgb),
            (unsigned long)uxTaskGetStackHighWaterMark(a->h_imu),
            (unsigned long)uxTaskGetStackHighWaterMark(h_diag));

        /* CPU runtime statistics — only compiled when the SMP FreeRTOS scheduler
         * exposes configGENERATE_RUN_TIME_STATS.  On ESP32-S3 with SMP FreeRTOS
         * (IDF 5.x default), this Kconfig option is unavailable; the block is
         * omitted at compile time to keep the rest of diagnostics functional. */
#ifdef CONFIG_FREERTOS_GENERATE_RUN_TIME_STATS
        /* Static keeps the 1024-byte text table off the 3072-byte task stack. */
        static char s_stats[1024];
        vTaskGetRunTimeStats(s_stats);
        ESP_LOGI("DIAG", "CPU runtime:\n%s", s_stats);
#else
        ESP_LOGI("DIAG", "CPU runtime stats: not available (SMP FreeRTOS)");
#endif

        /* Heap health. largest_free tells fragmentation (falling free with
         * stable largest_free) apart from real exhaustion (both falling
         * together) — stress-soak instrumentation, see
         * docs/performance/SATELLITE_STRESS_STABILITY_TEST.md. */
        ESP_LOGI("DIAG", "Heap free: internal=%lu largest_free=%lu PSRAM=%lu IRAM=%lu",
            (unsigned long)heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT),
            (unsigned long)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT),
            (unsigned long)heap_caps_get_free_size(MALLOC_CAP_SPIRAM),
            (unsigned long)heap_caps_get_free_size(MALLOC_CAP_EXEC));

        /* Per-module health counters (Part I's <module>_get_stats() convention).
         * Logged every cycle so a field failure shows up here well before it
         * escalates to a crash/reboot, even on units nobody is watching live. */
        struct wifi_task_stats wifi_st;
        wifi_task_get_stats(&wifi_st);
        ESP_LOGI("DIAG", "wifi: connects=%lu disconnects=%lu retry_cnt=%lu",
            (unsigned long)wifi_st.connects, (unsigned long)wifi_st.disconnects,
            (unsigned long)wifi_st.retry_cnt);

        /* MQTT-level connects/disconnects are esp-mqtt's own internal
         * reconnect state machine (link_mqtt.c's network.reconnect_timeout_ms),
         * distinct from wifi_st above — logged separately so a heap dip can be
         * correlated against MQTT-only churn (broker restarts, keepalive
         * timeouts) vs. WiFi-level drops. */
        struct link_mqtt_stats mqtt_st;
        link_mqtt_get_stats(&mqtt_st);
        ESP_LOGI("DIAG", "mqtt: connects=%lu disconnects=%lu publishes=%lu "
                 "publish_failures=%lu cmds_received=%lu",
            (unsigned long)mqtt_st.connects, (unsigned long)mqtt_st.disconnects,
            (unsigned long)mqtt_st.publishes, (unsigned long)mqtt_st.publish_failures,
            (unsigned long)mqtt_st.cmds_received);

        /* Self-heal watchdog: esp-mqtt/esp-tls's own internal reconnect
         * loop (network.reconnect_timeout_ms in link_mqtt.c) leaks a small
         * amount of heap per failed attempt against a dead/unreachable
         * broker. Given enough uninterrupted retries this leak runs the
         * board into ADR-024's heap guard, which then blocks
         * link_mqtt_start() from ever being able to re-init - a permanent,
         * non-recovering failure observed on real hardware after ~7.6h
         * unattended (see docs/decisions/ADR-036-mqtt-reconnect-watchdog.md).
         * esp_restart() doesn't depend on heap availability, unlike
         * re-running link_mqtt_start(), so it's the only self-heal that
         * still works once heap is this far gone.
         *
         * Threshold shortened 30 -> 10 (ADR-036's 2026-08-11 addendum):
         * at the observed ~13s real retry cadence, 30 meant ~6.5 minutes
         * stuck before self-heal, which was directly observed to be
         * confusing/alarming during independent testing on someone else's
         * hardware/network with no context on this failure mode loaded.
         * 10 is ~130s (~2.2 min) - still an order of magnitude longer than
         * any broker restart/blip this project has actually observed (the
         * WiFi-AP power-cycle trials and the live 2026-08-11 repro both
         * involved outages that ran the full original ~6.5 min without
         * clearing on their own, i.e. every transient this project has
         * hard evidence for either clears in a handful of seconds or
         * doesn't clear within the watchdog's window at all - there is no
         * observed case that would sit in between and get false-triggered
         * by dropping to 10), so it keeps the same "won't fire on an
         * ordinary blip" property while cutting the stuck window roughly
         * 3x. This still resets to 0 on the very next successful
         * reconnect, same as before. */
#define MQTT_WATCHDOG_RESTART_THRESHOLD 10
        if (mqtt_st.consecutive_disconnects >= MQTT_WATCHDOG_RESTART_THRESHOLD) {
            ESP_LOGE("DIAG", "mqtt stuck: %lu consecutive disconnects with no "
                     "successful reconnect - restarting to recover (ADR-036)",
                (unsigned long)mqtt_st.consecutive_disconnects);
            esp_restart();
        }

        struct wifi_provision_stats prov_st;
        wifi_provision_task_get_stats(&prov_st);
        ESP_LOGI("DIAG", "wifi_prov: state=%lu provisioning_entries=%lu "
                 "recovery_entries=%lu recovery_successes=%lu",
            (unsigned long)g_wifi_provision_state,
            (unsigned long)prov_st.provisioning_entries,
            (unsigned long)prov_st.recovery_entries,
            (unsigned long)prov_st.recovery_successes);

        struct mic_task_stats mic_st;
        mic_task_get_stats(&mic_st);
        ESP_LOGI("DIAG", "mic: blocks_ok=%lu capture_failures=%lu rb_drops=%lu",
            (unsigned long)mic_st.blocks_ok, (unsigned long)mic_st.capture_failures,
            (unsigned long)mic_st.rb_drops);

        /* I2S DMA overflow health — non-zero means audio gaps occurred */
        struct mic_inmp441_i2s_stats mic_i2s_st;
        mic_inmp441_i2s_get_stats(&mic_i2s_st);
        if (mic_i2s_st.overflow_count > 0) {
            ESP_LOGW("DIAG", "mic i2s: overflow_count=%lu (cumulative) read_errors=%lu "
                     "short_reads=%lu — check CPU load",
                (unsigned long)mic_i2s_st.overflow_count,
                (unsigned long)mic_i2s_st.read_errors,
                (unsigned long)mic_i2s_st.short_reads);
        } else {
            ESP_LOGI("DIAG", "mic i2s: overflow_count=0 (clean) read_errors=%lu short_reads=%lu",
                (unsigned long)mic_i2s_st.read_errors, (unsigned long)mic_i2s_st.short_reads);
        }

        struct dsp_task_stats dsp_st;
        dsp_task_get_stats(&dsp_st);
        ESP_LOGI("DIAG", "dsp: fft_count=%lu frames_emitted=%lu rb_timeouts=%lu last_fft_us=%lu",
            (unsigned long)dsp_st.fft_count, (unsigned long)dsp_st.frames_emitted,
            (unsigned long)dsp_st.rb_timeouts, (unsigned long)dsp_st.last_fft_us);

        struct imu_task_stats imu_st;
        imu_task_get_stats(&imu_st);
        ESP_LOGI("DIAG", "imu: epochs=%lu read_errors=%lu reinit_attempts=%lu reinit_successes=%lu",
            (unsigned long)imu_st.epochs, (unsigned long)imu_st.read_errors,
            (unsigned long)imu_st.reinit_attempts, (unsigned long)imu_st.reinit_successes);

        struct hal_accel_stats accel_st;
        hal_accel_get_stats(&accel_st);
        ESP_LOGI("DIAG", "accel: reads_ok=%lu read_errors=%lu fifo_max_hits=%lu",
            (unsigned long)accel_st.reads_ok, (unsigned long)accel_st.read_errors,
            (unsigned long)accel_st.fifo_max_hits);

        struct net_task_stats net_st;
        net_task_get_stats(&net_st);
        ESP_LOGI("DIAG", "net: frames_built=%lu build_failures=%lu publish_failures=%lu "
                 "cmd_malformed=%lu disconnect_reverts=%lu",
            (unsigned long)net_st.frames_built, (unsigned long)net_st.build_failures,
            (unsigned long)net_st.publish_failures, (unsigned long)net_st.cmd_malformed,
            (unsigned long)net_st.disconnect_reverts);

        struct rgb_led_stats led_st;
        led_task_get_stats(&led_st);
        ESP_LOGI("DIAG", "led: state_changes=%lu remote_updates=%lu hw_errors=%lu",
            (unsigned long)led_st.state_changes, (unsigned long)led_st.remote_updates,
            (unsigned long)led_st.hw_errors);
    }
}

/* ── app_main ─────────────────────────────────────────────────────────────── */

void app_main(void)
{
    /* --- System services --- */
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES ||
        ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    /* HW-OPT: Boot memory map — logged before any tasks allocate heap.
     * Use these numbers in HARDWARE_AUDIT_RESULTS.md baseline table. */
    ESP_LOGI(TAG, "Boot memory (before tasks): "
             "DRAM free=%lu PSRAM free=%lu IRAM free=%lu",
        (unsigned long)heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT),
        (unsigned long)heap_caps_get_free_size(MALLOC_CAP_SPIRAM),
        (unsigned long)heap_caps_get_free_size(MALLOC_CAP_EXEC));

    /* Initialise FFT twiddle-factor table once with the largest needed size.
     * FFT_IMU_N >= FFT_MIC_N, so a single init covers both tasks.
     * NULL → use the built-in static twiddle table in DROM (flash, read-only). */
#if FFT_IMU_N >= FFT_MIC_N
    ESP_ERROR_CHECK(dsps_fft2r_init_fc32(NULL, FFT_IMU_N));
#else
    ESP_ERROR_CHECK(dsps_fft2r_init_fc32(NULL, FFT_MIC_N));
#endif

    /* --- RGB LED: init hardware and start task on core 1, priority 3 --- */
    led_task_start();

    /* --- Start WiFi RF before any I2S/DMA ---
     * I2S DMA interrupts disrupt the WiFi firmware RF state-machine timing
     * if armed concurrently, causing TG1WDT_SYS_RST during the initial scan.
     * wifi_rf_init() itself is still synchronous and still completes (incl.
     * esp_wifi_start(), which begins the RF scan) before any DMA task
     * starts below — that ordering constraint is unchanged.
     *
     * What changed (Phase 12a): the old code then blocked here for up to
     * 30 s waiting for a connection before continuing regardless. The
     * provisioning state machine (src/threads/wifi_provision_task.c) now
     * owns that wait in its own task instead — it still attempts saved/
     * seeded credentials with a bounded join (15 s) but does not block
     * app_main(), so the tasks below start immediately either way, same
     * as the old "connect or warn and continue anyway" behavior. */
    wifi_rf_init();
    wifi_provision_task_start();

    /* --- Start application tasks --- */
    mic_task_start();
    dsp_task_start(mic_task_get_raw_ringbuf());
    imu_task_start();

    /* --- MQTT telemetry to the base station ---
     * See docs/decisions/ADR-023-transport-adrs-superseded.md. net_task
     * blocks on WiFi itself, so it's safe to start before the 30 s wait
     * above resolves. */
    if (net_task_start(dsp_task_get_queue(), imu_task_get_queue()) != 0) {
        ESP_LOGE(TAG, "net_task_start failed");
    }

    /* --- diagnostics_task (core 0, priority 1) ---
     * Collects task handles after all *_task_start() calls complete.
     * Priority 1 ensures it never preempts any application task. */
    s_diag_args.h_mic  = mic_task_get_handle();
    s_diag_args.h_dsp  = dsp_task_get_handle();
    s_diag_args.h_rgb  = led_task_get_handle();
    s_diag_args.h_imu  = imu_task_get_handle();

    static TaskHandle_t h_diag = NULL;
    xTaskCreatePinnedToCore(diagnostics_task_fn, "diag", TASK_STACK_DIAG,
                            &s_diag_args, TASK_PRIO_DIAG, &h_diag, 0);

    ESP_LOGI(TAG, "EPM: mic=%d-pt imu=%d-pt avg=%d",
             FFT_MIC_N, FFT_IMU_N, SPEC_AVG_N);

    while (1) {
        vTaskDelay(portMAX_DELAY);
    }
}
