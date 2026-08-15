#include "threads/wifi_provision_task.h"

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "epm_config.h"
#include "threads/wifi_task.h"
#include "drivers/net_credentials.h"
#include "hal/hal_provisioning.h"

/*
 * wifi_provision_task.c — provisioning state machine. See
 * wifi_provision_task.h for why this file (unlike wifi_task.c) owns a
 * real FreeRTOS task.
 *
 * Design reference: this phase's own design notes (ported from the
 * reference satellite's satellite/src/threads/transport_task.cpp state
 * machine — that file lives in a different, non-ESP-IDF codebase and was
 * read directly, not copied).
 */

static const char *TAG = "wifi_provision";

#define BOOT_JOIN_TIMEOUT_MS 15000 /* bounded saved-creds join at boot */
#define STA_TEST_TIMEOUT_MS  15000 /* bounded join when testing a submitted credential */
#define RECOVERY_WINDOW_MS   60000 /* silent-reconnect window before re-provisioning */
#define POLL_INTERVAL_MS     1000  /* CONNECTED/PROVISIONING polling cadence */

volatile uint32_t g_wifi_provision_state = WIFI_PROV_BOOT_STA_ATTEMPT;

static struct wifi_provision_stats s_stats;

static void enter_state(enum wifi_provision_state s)
{
    g_wifi_provision_state = (uint32_t)s;
}

static void provision_task_fn(void *arg)
{
    (void)arg;

    /* wifi_rf_init() (called synchronously from app_main(), before this
     * task was spawned) already loaded/seeded NVS credentials and issued
     * esp_wifi_connect() against them — src/threads/wifi_task.c. This just
     * waits, bounded, for that attempt to resolve. */
    enter_state(WIFI_PROV_BOOT_STA_ATTEMPT);
    s_stats.boot_attempts++;
    bool connected = wifi_wait_connected(pdMS_TO_TICKS(BOOT_JOIN_TIMEOUT_MS));
    if (!connected) {
        ESP_LOGW(TAG, "no WiFi after %d s boot attempt", BOOT_JOIN_TIMEOUT_MS / 1000);
    }

    while (1) {
        if (connected) {
            enter_state(WIFI_PROV_CONNECTED);
            ESP_LOGI(TAG, "CONNECTED");

            /* Poll for loss. wifi_wait_connected(0) is a non-blocking read
             * of WIFI_CONNECTED_BIT's current state — this reuses
             * wifi_task.c's existing event-driven reconnect/backoff
             * (s_retry_cnt, docs/decisions/ADR-022) as the join-attempt
             * primitive rather than this task driving esp_wifi_connect()
             * itself. */
            while (wifi_wait_connected(0)) {
                vTaskDelay(pdMS_TO_TICKS(POLL_INTERVAL_MS));
            }

            ESP_LOGW(TAG, "WiFi link lost — RECOVERING (%d s window)",
                     RECOVERY_WINDOW_MS / 1000);
            enter_state(WIFI_PROV_RECOVERING);
            s_stats.recovery_entries++;

            TickType_t deadline = xTaskGetTickCount() + pdMS_TO_TICKS(RECOVERY_WINDOW_MS);
            bool recovered = false;
            while ((int32_t)(deadline - xTaskGetTickCount()) > 0) {
                if (wifi_wait_connected(pdMS_TO_TICKS(POLL_INTERVAL_MS))) {
                    recovered = true;
                    break;
                }
            }

            if (recovered) {
                ESP_LOGI(TAG, "WiFi self-healed within recovery window");
                s_stats.recovery_successes++;
                connected = true;
                continue;
            }

            ESP_LOGW(TAG, "recovery window expired — PROVISIONING (AP reopens, no reboot)");
            connected = false;
        }

        enter_state(WIFI_PROV_PROVISIONING);
        s_stats.provisioning_entries++;
        hal_provisioning_start();

        struct net_credentials submitted;
        while (!hal_provisioning_take_submission(&submitted)) {
            /* Polls without spinning (POLL_INTERVAL_MS between checks)
             * until a form submission arrives via components/epm_drivers/
             * provisioning.c's captive portal (Phase 12b) — negligible CPU
             * while nobody has submitted anything yet. */
            vTaskDelay(pdMS_TO_TICKS(POLL_INTERVAL_MS));
        }

        enter_state(WIFI_PROV_STA_TESTING);
        ESP_LOGI(TAG, "testing submitted credential for \"%s\"...", submitted.wifi_ssid);
        wifi_sta_reconfigure(submitted.wifi_ssid, submitted.wifi_password);
        bool test_ok = wifi_wait_connected(pdMS_TO_TICKS(STA_TEST_TIMEOUT_MS));
        hal_provisioning_report_result(test_ok);

        if (test_ok) {
            /* Only persist a credential once it has actually joined —
             * never on an unverified submission (matches the reference
             * design's rule). */
            net_credentials_save(&submitted);

            /* Grace period before tearing down the portal:
             * s_submitted_html's meta-refresh polls /status every 2s, and
             * hal_provisioning_report_result() just above flipped it to
             * STATUS_SUCCESS. Without this delay, hal_provisioning_stop()
             * can tear down the AP before that poll ever lands, so the
             * phone just sees an abrupt disconnect instead of the
             * "Connected" confirmation page. */
            vTaskDelay(pdMS_TO_TICKS(5000));

            hal_provisioning_stop();
            connected = true;
        } else {
            ESP_LOGW(TAG, "submitted credential failed to join — back to PROVISIONING");
            connected = false;
        }
    }
}

void wifi_provision_task_start(void)
{
    TaskHandle_t h = NULL;
    xTaskCreatePinnedToCore(provision_task_fn, "wifi_prov", TASK_STACK_WIFI_PROV,
                             NULL, TASK_PRIO_WIFI_PROV, &h, 0);
    configASSERT(h != NULL);
}

void wifi_provision_task_get_stats(struct wifi_provision_stats *out)
{
    if (out == NULL) return;
    *out = s_stats;
}
