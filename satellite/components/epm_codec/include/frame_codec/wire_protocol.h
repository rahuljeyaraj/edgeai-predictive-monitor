#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/*
 * MQTT control-plane envelope (docs/SENSOR_TELEMETRY_FRAME_PLAN.md S6,
 * base-station/python/common/wire_protocol.py) - covers only the Base
 * Station -> Node *command* direction (`epm/<node_id>/cmd`), the C mirror
 * of that module's MqttMsgType/encode_mqtt_message()/decode_mqtt_message()/
 * display_rgb_payload codec (must stay byte-for-byte compatible with it):
 *   [TYPE: 1B][PAYLOAD: N bytes]
 * No VER/NODE_ID/LEN/CRC16: node identity comes from the topic, not a wire
 * field - MQTT already provides framing, addressing, and delivery semantics.
 *
 * The Node -> Base Station *telemetry* direction (`epm/<node_id>/data`) no
 * longer uses this envelope at all: it publishes the raw generic
 * section-list telemetry frame bytes (frame_codec/spectrum_codec.h) as the
 * MQTT message body, with no TYPE byte or other wrapper.
 *
 * Ported from the reference repo's satellite/include/frame_codec/
 * wire_protocol.h (C++). Only porting change: explicit <stdbool.h> include
 * for `bool`, which the C++ original gets for free from the language.
 */

enum mqtt_msg_type {
	MQTT_MSG_TYPE_STATUS_LED = 0x08, /* Base Station -> Node, payload: display_rgb_payload */
};

/* display_rgb_payload's wire shape, reused here for MQTT's STATUS_LED -
 * same struct, same three-mode vocabulary, just carried over MQTT instead
 * of the base station's SPI link. Matches base-station/python/common/
 * wire_protocol.py's DISPLAY_RGB_PAYLOAD_FMT = "<IBH" (no padding, since
 * Python's struct module never pads and this is __packed). */
struct display_rgb_payload {
	uint32_t rgb;      /* packed 0xRRGGBB */
	uint8_t mode;       /* 0 = CONST, 1 = BREATHE, 2 = STROBE */
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
 * empty (can't even hold a TYPE byte) - callers should treat that as a
 * malformed message. */
bool mqtt_decode_message(const uint8_t *data, size_t len, uint8_t *out_type,
			  const uint8_t **out_payload, size_t *out_payload_len);

/* Decodes a STATUS_LED command's payload (the out_payload/out_payload_len
 * mqtt_decode_message() already split off) into a display_rgb_payload.
 * Returns false, leaving *out unwritten, if payload is too short to hold
 * one - the caller (link_mqtt.c's cmd-handler dispatch) must not trust a
 * remote message's declared length without this check before it ever
 * reaches the display driver. */
bool mqtt_decode_status_led(const uint8_t *payload, size_t payload_len,
			     struct display_rgb_payload *out);
