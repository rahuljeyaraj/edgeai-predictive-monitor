#include <string.h>

#include "frame_codec/spectrum_codec.h"
#include "frame_codec/telemetry_schema.h"

/* Little-endian appenders, matching base-station/sketch/fuser.cpp's
 * put_u8/put_u16/put_f32 - the Cortex-M base station and this ESP32S3 are
 * both LE, matching the documented wire byte order. */
static inline void put_u8(uint8_t *buf, size_t *pos, uint8_t v)
{
	buf[(*pos)++] = v;
}

static inline void put_u16(uint8_t *buf, size_t *pos, uint16_t v)
{
	memcpy(&buf[*pos], &v, sizeof(v));
	*pos += sizeof(v);
}

static inline void put_f32(uint8_t *buf, size_t *pos, float v)
{
	memcpy(&buf[*pos], &v, sizeof(v));
	*pos += sizeof(v);
}

size_t telemetry_build_frame(const struct spectrum_channel *channels, size_t num_channels,
			      const struct scalar_entry *scalars, size_t num_scalars,
			      uint8_t *out_buf, size_t out_buf_size)
{
	size_t num_sections = num_channels + (num_scalars > 0 ? 1 : 0);

	if (num_sections > 0xFF || num_scalars > 0xFF) {
		return 0;
	}

	size_t total = 1; /* num_sections byte */

	for (size_t i = 0; i < num_channels; i++) {
		total += SPECTRUM_SECTION_OVERHEAD + (size_t)channels[i].bin_count * sizeof(float);
	}
	if (num_scalars > 0) {
		total += SCALAR_SECTION_OVERHEAD + num_scalars * SCALAR_ENTRY_SIZE;
	}
	if (total > out_buf_size) {
		return 0;
	}

	size_t pos = 0;

	put_u8(out_buf, &pos, (uint8_t)num_sections);

	for (size_t i = 0; i < num_channels; i++) {
		const struct spectrum_channel *ch = &channels[i];
		uint16_t section_len = (uint16_t)(4 + 2 + 2 + (uint32_t)ch->bin_count * sizeof(float));

		put_u8(out_buf, &pos, TELEM_SOURCE_SATELLITE);
		put_u8(out_buf, &pos, ch->channel_id);
		put_u8(out_buf, &pos, TELEM_KIND_SPECTRUM);
		put_u16(out_buf, &pos, section_len);
		put_f32(out_buf, &pos, ch->fs);
		put_u16(out_buf, &pos, ch->fft_size);
		put_u16(out_buf, &pos, ch->bin_count);

		if (ch->bin_count > 0) {
			size_t bytes = (size_t)ch->bin_count * sizeof(float);

			memcpy(&out_buf[pos], ch->bins, bytes);
			pos += bytes;
		}
	}

	if (num_scalars > 0) {
		uint16_t section_len = (uint16_t)(1 + num_scalars * SCALAR_ENTRY_SIZE);

		put_u8(out_buf, &pos, TELEM_SOURCE_SATELLITE);
		put_u8(out_buf, &pos, TELEM_CHANNEL_PERF);
		put_u8(out_buf, &pos, TELEM_KIND_SCALAR_SET);
		put_u16(out_buf, &pos, section_len);
		put_u8(out_buf, &pos, (uint8_t)num_scalars);
		for (size_t i = 0; i < num_scalars; i++) {
			put_u16(out_buf, &pos, scalars[i].id);
		}
		for (size_t i = 0; i < num_scalars; i++) {
			put_f32(out_buf, &pos, scalars[i].value);
		}
	}

	return pos;
}
