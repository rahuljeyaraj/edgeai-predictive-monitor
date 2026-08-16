#ifndef HAL_TRANSPORT_H_
#define HAL_TRANSPORT_H_

#include <stddef.h>
#include <stdint.h>

/*
 * Transport contract for the WiFi/MQTT link (docs/Appendix_B_Wire_
 * Protocol_Specification.md S3) - the counterpart to mcu/src/hal/
 * hal_transport.h's LPUART1 contract. Any code that needs to publish a
 * SPECTRUM message includes this header; src/threads/transport_task.cpp
 * is the only implementation and owns the actual WiFi/PubSubClient
 * connection, exactly as uart_link.c is the only implementation of mcu/'s
 * hal_transport.h and owns the LPUART1 peripheral.
 *
 * Shaped differently from mcu/'s hal_transport.h because the underlying
 * transports differ structurally, not just in physical layer: UART needs
 * an explicit SYNC/CRC framing layer this code invents nothing analogous
 * to (MQTT already frames and addresses messages - Appendix B S3's own
 * "Unlike UART, MQTT already provides message framing" note) and this
 * node only has one inbound command type (STATUS_LED, handled internally
 * by transport_task.cpp's MQTT callback - there's no DISPLAY_MATRIX
 * equivalent over MQTT, so no dispatch-by-type surface is needed here the
 * way mcu/'s transport_thread.c has).
 */

/* One-time setup (currently: allocates the mutex guarding the MQTT
 * client). Called by threads/transport_task.h's transport_task_start()
 * before it creates the background task that owns the provision/connect/
 * recover state machine (docs/WIFI_ONBOARDING_PLAN.md S2) and the MQTT
 * client, subscribes this node's cmd topic, and services incoming
 * STATUS_LED commands - mirrors mcu/'s own split, where main.c calls
 * transport_thread_start() (which calls hal_transport.h's transport_init()
 * internally), not transport_init() directly. Returns 0, or a negative
 * errno. */
int transport_init(void);

/* This node's MAC-derived id (6 lowercase hex chars, Appendix B S3) -
 * valid from the first loop iteration of the background task
 * (transport_task_start()): derived from the factory WiFi MAC, which
 * needs no prior WiFi connection to read. Used to build the outgoing
 * envelope's "node_id" field, this node's provisioning AP SSID, and to
 * log its identity. */
const char *transport_node_id(void);

/* Publishes a complete generic section-list telemetry frame (built by
 * threads/fuser_task.cpp via frame_codec/spectrum_codec.h) as the raw
 * message body - no TYPE byte or other envelope, matching
 * base-station/python/common/telemetry_frame.py's decoder - to this node's
 * epm/<node_id>/data topic at QoS 0 (SENSOR_TELEMETRY_FRAME_PLAN.md S6:
 * "High frequency; occasional loss acceptable"). Returns 0 on success, or a
 * negative errno - most commonly -ENOTCONN if the MQTT client isn't
 * currently connected (transport_task.cpp's reconnect loop will restore it;
 * this call does not block waiting for that). */
int transport_publish_spectrum(const uint8_t *message, size_t len);

#endif /* HAL_TRANSPORT_H_ */
