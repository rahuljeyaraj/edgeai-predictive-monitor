#ifndef SPECTRUM_CODEC_H_
#define SPECTRUM_CODEC_H_

#include <stddef.h>
#include <stdint.h>

/*
 * Builds the generic section-list telemetry frame this node publishes as its
 * MQTT data-topic body -- byte-identical to what the base station's own SPI
 * link now sends (base-station/sketch/fuser.cpp's write_spectrum_section()/
 * write_scalar_section() + num_sections writer) and what
 * base-station/python/common/telemetry_frame.py decodes
 * (docs/SENSOR_TELEMETRY_FRAME_PLAN.md S3/S6): one codec, two transports,
 * driven off the single schema base-station/telemetry_schema.json
 * (frame_codec/telemetry_schema.h, also generated from it -- see
 * python/tools/gen_telemetry_schema.py). This node emits one SPECTRUM
 * section per enabled channel (mic, accel_x, accel_y, accel_z -- the
 * per-axis SensorChannel members, matching
 * base-station/python/tools/satellite_node_sim.py's per-axis mode) plus one
 * SCALAR_SET section carrying that same channel set's rms/kurtosis/std/
 * peak/crest_factor/skewness tiles (pipeline/features.py requires all 6 per
 * live channel or it rejects the frame entirely).
 *
 * Replaces this file's earlier fixed spectrum_fused_payload_header codec
 * (byte-identical to the base station's own now-retired frame_types.h
 * struct): the base station's wire format moved to this generic
 * section-list shape first (SENSOR_TELEMETRY_FRAME_PLAN.md T2/T8), and this
 * satellite firmware now matches its current per-axis+scalar shape instead
 * of the earlier combined-channel, spectrum-only one.
 *
 * Wire layout (little-endian, matches telemetry_frame.py's encode_frame()):
 *   [num_sections u8] then, per SPECTRUM channel:
 *     [source_id u8][channel_id u8][data_kind u8][section_len u16]
 *     [fs f32][fft_size u16][bin_count u16][bins f32...]
 *   then, if any scalars are present, one SCALAR_SET section:
 *     [source_id u8][channel_id u8][data_kind u8][section_len u16]
 *     [count u8][ids u16...][values f32...]
 */

/* Per-channel wire overhead: the 5-byte section header (source/channel/kind +
 * section_len u16) plus a SPECTRUM body's fs/fft_size/bin_count preamble
 * (4 + 2 + 2) -- mirrors base-station/sketch/fuser.cpp's
 * FUSER_SECTION_OVERHEAD. */
#define SPECTRUM_SECTION_OVERHEAD (5 + 4 + 2 + 2)
/* SCALAR_SET section overhead: the same 5-byte section header plus the
 * count u8; each entry then costs (id u16 + value f32) = 6 bytes, sized by
 * the caller via num_scalars. */
#define SCALAR_SECTION_OVERHEAD (5 + 1)
#define SCALAR_ENTRY_SIZE       (2 + 4)

struct spectrum_channel {
	uint8_t channel_id; /* TELEM_CHANNEL_MIC / _ACCEL_X / _ACCEL_Y / _ACCEL_Z (frame_codec/telemetry_schema.h) */
	float fs;
	uint16_t fft_size;
	uint16_t bin_count;
	const float *bins; /* bin_count entries; ignored (may be NULL) when bin_count == 0 */
};

struct scalar_entry {
	uint16_t id; /* TELEM_SCALAR_* (frame_codec/telemetry_schema.h) */
	float value;
};

/* Encodes a section-list frame carrying one SPECTRUM section per entry of
 * `channels` (num_channels of them, any order -- the decoder keys sections by
 * channel_id, not position), followed by one SCALAR_SET section carrying all
 * of `scalars` (skipped entirely when num_scalars == 0). A sensor this node
 * lacks entirely (app_config.h's MIC_SENSOR_ENABLED/ACCEL_SENSOR_ENABLED) is
 * simply left out of both arrays rather than sent as a zero-bin_count/
 * zero-value placeholder -- unlike satellite_node_sim.py, this firmware's
 * enable flags are fixed at compile time, so a node's first-ever frame
 * already reflects its final, permanent channel set. source_id is always
 * TELEM_SOURCE_SATELLITE. Returns the encoded length, or 0 if out_buf_size
 * is too small or num_channels/num_scalars/total sections exceeds a u8. */
size_t telemetry_build_frame(const struct spectrum_channel *channels, size_t num_channels,
			      const struct scalar_entry *scalars, size_t num_scalars,
			      uint8_t *out_buf, size_t out_buf_size);

#endif /* SPECTRUM_CODEC_H_ */
