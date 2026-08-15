#pragma once

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/*
 * MQTT link driver - implements hal/hal_transport.h behind ESP-IDF's
 * native WiFi STA (owned by src/wifi_task.c on this board, see
 * docs/decisions/ADR-011-mqtt-transport-added.md) + the esp-mqtt
 * component. Analog of the reference repo's satellite/src/threads/
 * transport_task.cpp, split into a HAL-conforming driver + a thin
 * task, per this repo's HAL layering convention.
 */

struct link_mqtt_stats {
	uint32_t connects;
	uint32_t disconnects;
	uint32_t publishes;
	uint32_t publish_failures;
	uint32_t cmds_received;
	/* Disconnects since the last successful MQTT_EVENT_CONNECTED, reset to
	 * 0 on every reconnect. Distinct from the cumulative `disconnects`
	 * above - lets a caller detect "stuck retrying, never reconnecting"
	 * (this climbs unbounded) vs. ordinary churn (this stays near 0). See
	 * docs/decisions/ADR-036-mqtt-reconnect-watchdog.md. */
	uint32_t consecutive_disconnects;
};

/* Starts the MQTT link: derives node_id from the STA MAC (must be called
 * after WiFi is connected - derive_node_id() reads the MAC via
 * esp_wifi_get_mac()), builds data/cmd topics, and starts the esp-mqtt
 * client with auto-reconnect. Returns 0, or a negative errno. */
int link_mqtt_start(void);

/* Per Part I's one-<module>_get_stats()-per-module convention. */
void link_mqtt_get_stats(struct link_mqtt_stats *out);

#ifdef __cplusplus
}
#endif
