/*
 * wifi_provision_task.h — Public API for the WiFi provisioning state machine.
 *
 * Added in Phase 12a. Unlike src/threads/
 * wifi_task.c — which stays deliberately event-driven with no FreeRTOS
 * task of its own, per docs/decisions/ADR-022-wifi-task-revived.md — this
 * file DOES own a real task. ADR-022's reasoning covered WiFi STA
 * lifecycle itself: bring up the radio, join, retry on disconnect, all of
 * which ESP-IDF's own event loop already drives with no loop of ours
 * needed. Provisioning is a different concern layered on top: it needs
 * bounded join timeouts (15 s, not "however long esp_wifi takes"), a 60 s
 * recovery window before giving up on a lost connection, and — once Phase
 * 12b's captive portal exists — a poll loop over hal_provisioning's
 * submission queue. None of that maps onto "call once from app_main()"; it
 * needs an actual loop, so it gets one, in its own file, without touching
 * wifi_task.c's existing event-driven shape.
 */

#pragma once

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

enum wifi_provision_state {
    WIFI_PROV_BOOT_STA_ATTEMPT = 0, /* saved creds, bounded 15 s join */
    WIFI_PROV_CONNECTED        = 1,
    WIFI_PROV_PROVISIONING     = 2, /* AP+portal up (stub in 12a — see hal/hal_provisioning.h) */
    WIFI_PROV_STA_TESTING      = 3, /* testing a submitted credential, AP stays up */
    WIFI_PROV_RECOVERING       = 4, /* link lost while CONNECTED, silent retry window */
};

/*
 * Starts the provisioning state machine task (core 0, priority
 * TASK_PRIO_WIFI_PROV). Call once from app_main(), after wifi_rf_init(),
 * in place of the old blocking wifi_wait_connected(30000) call — this
 * does not block: WiFi join/recovery/provisioning all happen in the
 * background so mic_task/dsp_task/imu_task/net_task can start immediately
 * regardless of WiFi's outcome, same as the old code's "connect or warn
 * and continue anyway" behavior, just no longer bounded to one 30 s
 * window at boot only.
 */
void wifi_provision_task_start(void);

/* JTAG-readable current state — an enum wifi_provision_state value. */
extern volatile uint32_t g_wifi_provision_state;

struct wifi_provision_stats {
    uint32_t boot_attempts;        /* BOOT_STA_ATTEMPT entries since boot (always 1) */
    uint32_t provisioning_entries; /* PROVISIONING entries since boot */
    uint32_t recovery_entries;     /* RECOVERING entries since boot */
    uint32_t recovery_successes;   /* RECOVERING -> CONNECTED (self-healed) transitions */
};

/* Per Part I's one-<module>_get_stats()-per-module convention. */
void wifi_provision_task_get_stats(struct wifi_provision_stats *out);

#ifdef __cplusplus
}
#endif
