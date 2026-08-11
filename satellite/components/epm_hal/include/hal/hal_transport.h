#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/*
 * Transport contract for the WiFi/MQTT link (docs/Appendix_B_Wire_
 * Protocol_Specification.md S3). Any code that needs to publish a
 * telemetry frame includes this header; components/epm_drivers/
 * link_mqtt.c is the only implementation and owns the actual MQTT client
 * (WiFi STA bring-up itself stays owned by src/wifi_task.c on this board -
 * see docs/decisions/ADR-011-mqtt-transport-added.md for why: one radio,
 * one AP, and wifi_task.c already brings it up before this component's
 * task starts).
 *
 * Pure C, zero ESP-IDF includes, per this repo's HAL layering/naming conventions.
 */

/* One-time setup (allocates the mutex guarding the MQTT client, resets
 * stats). Called by link_mqtt_start() before it creates the background
 * task that connects MQTT and services incoming commands. Returns 0, or
 * a negative errno. */
int transport_init(void);

/* This node's MAC-derived id (6 lowercase hex chars) - empty ("") until
 * WiFi is up and derive_node_id() has run; valid from then on. */
const char *transport_node_id(void);

/* Publishes a complete generic section-list telemetry frame (built by
 * epm_codec/spectrum_codec.h) as the raw message body - no TYPE byte or
 * other envelope - to this node's epm/<node_id>/data topic at QoS 0
 * (SENSOR_TELEMETRY_FRAME_PLAN.md S6: "High frequency; occasional loss
 * acceptable"). Returns 0 on success, or a negative errno - most commonly
 * -ENOTCONN if the MQTT client isn't currently connected (the reconnect
 * loop will restore it; this call does not block waiting for that). */
int transport_publish_spectrum(const uint8_t *message, size_t len);

/* True once the MQTT client has an active broker connection. */
bool transport_is_connected(void);

/* Callback invoked for every decoded inbound cmd-topic message
 * (frame_codec/wire_protocol.h's [TYPE][body] envelope, already split).
 * Registered once via transport_set_cmd_handler(); NULL clears it. */
typedef void (*transport_cmd_fn)(uint8_t type, const uint8_t *body, size_t len);

/* Registers fn as the inbound cmd-topic handler. Returns 0, or -EINVAL if
 * fn is NULL and no handler was previously set (clearing an unset handler
 * is a no-op, not an error, so that case still returns 0). */
int transport_set_cmd_handler(transport_cmd_fn fn);
