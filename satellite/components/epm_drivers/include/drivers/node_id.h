#pragma once

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/*
 * node_id — shared node-id derivation, used by both link_mqtt.c (MQTT
 * client id / topic prefix) and provisioning.c (AP SSID). Same convention
 * as the reference satellite's transport_task.cpp: last 3 STA MAC octets,
 * lowercase hex, no separators.
 *
 * esp_wifi_get_mac(WIFI_IF_STA, ...) reads the efuse-programmed MAC
 * regardless of connection state, so this is valid to call during
 * provisioning too, before the STA has ever associated — unlike
 * hal_transport.h's transport_node_id(), which is only populated once
 * link_mqtt_start() has run (after a WiFi connection already exists).
 */

#define NODE_ID_LEN 6 /* last 3 MAC octets, lowercase hex, no separators */

/* Writes the derived node id into out (NUL-terminated). out must be at
 * least NODE_ID_LEN+1 bytes. */
void node_id_derive(char *out, size_t out_size);

#ifdef __cplusplus
}
#endif
