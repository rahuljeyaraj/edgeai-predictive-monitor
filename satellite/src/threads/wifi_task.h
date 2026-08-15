/*
 * wifi_task.h — Public API for WiFi STA lifecycle + power management.
 *
 * Despite the "task" name (kept for continuity with the log tag and the
 * priority/stack naming convention elsewhere in this tree) there is no
 * FreeRTOS task here: WiFi STA bring-up is entirely event-driven
 * (ESP-IDF's own event loop task drives it), and the two calls below are
 * meant to be made once, in sequence, from app_main() before any other
 * task starts. See docs/decisions/ADR-022-wifi-task-revived.md.
 */

#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "freertos/FreeRTOS.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Phase 1 — call BEFORE any I2S/DMA tasks are started.
 * Initialises WiFi hardware, registers event handlers, calls
 * esp_wifi_start() so the RF scan begins with no I2S interrupt load, caps
 * TX power, and configures dynamic CPU frequency scaling.
 */
void wifi_rf_init(void);

/**
 * Phase 2 — call after wifi_rf_init(), still before I2S tasks.
 * Blocks until WIFI_CONNECTED_BIT is set or ticks_to_wait expires.
 * Returns true if connected, false on timeout. ticks_to_wait=0 is a
 * non-blocking poll of the bit's current state.
 */
bool wifi_wait_connected(TickType_t ticks_to_wait);

/*
 * Reconfigures STA SSID/password and triggers a fresh connection attempt.
 * Safe to call any time after wifi_rf_init(), including while already
 * connected or mid-retry — reuses the same event-driven reconnect/backoff
 * path wifi_rf_init()'s initial config joins through (see wifi_task.c's
 * on_wifi_disconnected()). Used by wifi_provision_task.c's STA_TESTING
 * state (Phase 12a) to test a freshly-submitted credential without a
 * reboot. ssid/password are copied; the caller's buffers need not outlive
 * the call.
 */
void wifi_sta_reconfigure(const char *ssid, const char *password);

/* JTAG-readable WiFi state machine step: 0=init 1=rf_init_done 2=sta_start
 * 3=connecting 4=got_ip. */
extern volatile uint32_t g_wifi_debug_state;

struct wifi_task_stats {
    uint32_t connects;    /* got_ip events since boot */
    uint32_t disconnects; /* STA_DISCONNECTED events since boot */
    uint32_t retry_cnt;   /* consecutive failures since the last successful connect */
};

/* Per Part I's one-<module>_get_stats()-per-module convention. */
void wifi_task_get_stats(struct wifi_task_stats *out);

#ifdef __cplusplus
}
#endif
