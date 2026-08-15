/*
 * net_task.h — Public API for the MQTT telemetry task.
 *
 * The satellite's sole transport (see
 * docs/decisions/ADR-023-transport-adrs-superseded.md). This task owns
 * nothing about WiFi itself — it blocks on wifi_task.h's
 * wifi_wait_connected(), then drives components/epm_drivers/
 * link_mqtt.c (behind components/epm_hal/include/hal/hal_transport.h) to
 * publish a section-list telemetry frame
 * (components/epm_codec/include/frame_codec/spectrum_codec.h) every
 * EPM_NET_PUBLISH_INTERVAL_MS, built from real mic/accel data delivered via
 * dsp_task_get_queue()/imu_task_get_queue() (ADR-021).
 */

#pragma once

#include <stdint.h>

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Call after wifi_rf_init(), dsp_task_start(), and imu_task_start().
 * Creates the net task (core 0, priority TASK_PRIO_NET) which blocks on
 * wifi_wait_connected(portMAX_DELAY), starts the MQTT link, and begins the
 * publish loop, reading mic_q/imu_q each tick. Returns 0, or -ENOMEM if
 * task creation fails.
 */
int net_task_start(QueueHandle_t mic_q, QueueHandle_t imu_q);

struct net_task_stats {
	uint32_t frames_built;      /* build_real_frame() calls that returned a nonzero length */
	uint32_t build_failures;    /* build_real_frame() calls that returned 0 */
	uint32_t publish_failures;  /* transport_publish_spectrum() calls that failed, excluding -ENOTCONN */
	uint32_t cmd_malformed;     /* net_task_cmd_handler() STATUS_LED payloads that failed to decode */
	uint32_t disconnect_reverts; /* MQTT-level disconnects that reverted the display to local state */
};

/* Per Part I's one-<module>_get_stats()-per-module convention. */
void net_task_get_stats(struct net_task_stats *out);

#ifdef __cplusplus
}
#endif
