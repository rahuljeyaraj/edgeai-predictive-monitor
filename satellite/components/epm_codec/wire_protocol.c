#include <string.h>

#include "frame_codec/wire_protocol.h"

size_t mqtt_encode_message(enum mqtt_msg_type type, const uint8_t *payload, size_t payload_len,
			    uint8_t *out_buf, size_t out_buf_size)
{
	size_t total = 1 + payload_len;

	if (total > out_buf_size) {
		return 0;
	}

	out_buf[0] = (uint8_t)type;
	if (payload_len > 0) {
		memcpy(&out_buf[1], payload, payload_len);
	}

	return total;
}

bool mqtt_decode_message(const uint8_t *data, size_t len, uint8_t *out_type,
			  const uint8_t **out_payload, size_t *out_payload_len)
{
	if (len == 0) {
		return false;
	}

	*out_type = data[0];
	*out_payload = &data[1];
	*out_payload_len = len - 1;

	return true;
}

bool mqtt_decode_status_led(const uint8_t *payload, size_t payload_len,
			     struct display_rgb_payload *out)
{
	if (payload == NULL || out == NULL || payload_len < sizeof(*out)) {
		return false;
	}

	memcpy(out, payload, sizeof(*out));
	return true;
}
