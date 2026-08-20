#ifndef WIRE_PROTOCOL_H_
#define WIRE_PROTOCOL_H_

#include <stddef.h>
#include <stdint.h>

/*
 * MQTT control-plane envelope (docs/SENSOR_TELEMETRY_FRAME_PLAN.md S6,
 * base-station/python/common/wire_protocol.py) - covers only the Base
 * Station -> Node *command* direction (`epm/<node_id>/cmd`), the C++ mirror
 * of that module's MqttMsgType/encode_mqtt_message()/decode_mqtt_message()/
 * display_rgb_payload codec (must stay byte-for-byte compatible with it):
 *   [TYPE: 1B][PAYLOAD: N bytes]
 * No VER/NODE_ID/LEN/CRC16: node identity comes from the topic, not a wire
 * field - MQTT already provides framing, addressing, and delivery semantics.
 *
 * The Node -> Base Station *telemetry* direction (`epm/<node_id>/data`) no
 * longer uses this envelope at all: it publishes the raw generic
 * section-list telemetry frame bytes (frame_codec/spectrum_codec.h) as the
 * MQTT message body, with no TYPE byte or other wrapper - the same shape the
 * base station's own SPI link and base-station/python/common/
 * telemetry_frame.py's decoder use, one payload format for both transports.
 * A dedicated MQTT_MSG_TYPE_SPECTRUM used to live here; it's gone now that
 * data direction dropped the envelope entirely (SENSOR_TELEMETRY_FRAME_PLAN.md
 * T8).
 */

enum mqtt_msg_type {
	MQTT_MSG_TYPE_STATUS_LED = 0x08, /* Base Station -> Node, payload: display_rgb_payload */
	MQTT_MSG_TYPE_WIFI_PROVISION = 0x0a, /* Base Station -> Node, payload: wifi_provision_payload */
	MQTT_MSG_TYPE_WIFI_PROVISION_ACK = 0x0b, /* Node -> Base Station (epm/<node_id>/evt), payload: wifi_provision_ack_payload */
};

/* Fleet WiFi roaming (docs/WIFI_ONBOARDING_PLAN.md S6): the base station
 * hands its whole fleet the network it is about to join itself, so one form
 * in its dashboard onboards every node instead of one captive portal per
 * device. This is the only command whose *reply* travels back over MQTT
 * (MQTT_MSG_TYPE_WIFI_PROVISION_ACK below, on epm/<node_id>/evt - a separate
 * topic from /data precisely so the telemetry decoder never sees a byte that
 * isn't a section-list frame).
 *
 * Field widths are hal/hal_credentials.h's CREDS_*_MAX_LEN + 1 exactly, so
 * threads/transport_task.cpp can copy these straight onto a struct
 * node_credentials. Matches base-station/python/common/wire_protocol.py's
 * WIFI_PROVISION_PAYLOAD_FMT ("<I33s65s65sH", 169 bytes) byte for byte. */
struct wifi_provision_payload {
	uint32_t roam_id;       /* echoed in the ack - see wifi_provision_ack_payload */
	char wifi_ssid[33];      /* CREDS_SSID_MAX_LEN + 1, NUL-padded */
	char wifi_password[65];  /* CREDS_PASS_MAX_LEN + 1, NUL-padded */
	char mqtt_broker_host[65]; /* CREDS_BROKER_MAX_LEN + 1, NUL-padded */
	uint16_t mqtt_broker_port;
} __attribute__((packed));

/* A node acks when it has TAKEN the credentials, not when it has joined:
 * the join tears down the very link this ack travels on, so "joined" can
 * never be reported. roam_id is echoed so the base station can tell this
 * ack apart from one belonging to an earlier push (a retry after a timeout,
 * or a duplicate delivery). Matches WIFI_PROVISION_ACK_PAYLOAD_FMT ("<IB").
 *
 * status: 0 = accepted (switching now), 1 = simulated node (no radio),
 * 2 = rejected (unusable push) - base-station/python/common/
 * wire_protocol.py's WifiProvisionAckStatus. */
struct wifi_provision_ack_payload {
	uint32_t roam_id;
	uint8_t status;
} __attribute__((packed));

#define WIFI_PROVISION_ACK_ACCEPTED 0
#define WIFI_PROVISION_ACK_REJECTED 2

/* display_rgb_payload's wire shape, reused here for MQTT's STATUS_LED -
 * same struct, same three-mode vocabulary (hal/hal_display_rgb.h's enum
 * rgb_display_mode), just carried over MQTT instead of the base station's
 * SPI link. Matches base-station/python/common/wire_protocol.py's
 * DISPLAY_RGB_PAYLOAD_FMT = "<IBH" (no padding, since Python's struct
 * module never pads and this is __packed). */
struct display_rgb_payload {
	uint32_t rgb;      /* packed 0xRRGGBB */
	uint8_t mode;       /* 0 = CONST, 1 = BREATHE, 2 = STROBE - hal_display_rgb.h's enum rgb_display_mode */
	uint16_t period_ms; /* ignored when mode == CONST */
} __attribute__((packed));

/* Writes [TYPE: 1B][payload[0..payload_len)] into out_buf. Returns the
 * total length written (1 + payload_len), or 0 if out_buf_size is too
 * small - mirrors spectrum_codec.h's telemetry_build_frame()'s
 * "0 means didn't fit" convention. */
size_t mqtt_encode_message(enum mqtt_msg_type type, const uint8_t *payload, size_t payload_len,
			    uint8_t *out_buf, size_t out_buf_size);

/* Inverse of mqtt_encode_message(): splits data into its TYPE byte and
 * payload, writing them to out_type, out_payload and out_payload_len
 * (out_payload points into data, no copy). Returns false if data is
 * empty (can't even hold a TYPE byte) - the C++ equivalent of
 * decode_mqtt_message()'s ValueError-on-empty-payload in
 * base-station/python/common/wire_protocol.py; callers should treat that as
 * a malformed message. */
bool mqtt_decode_message(const uint8_t *data, size_t len, uint8_t *out_type,
			  const uint8_t **out_payload, size_t *out_payload_len);

#endif /* WIRE_PROTOCOL_H_ */
