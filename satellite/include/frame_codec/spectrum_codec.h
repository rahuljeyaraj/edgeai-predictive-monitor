#ifndef SPECTRUM_CODEC_H_
#define SPECTRUM_CODEC_H_

#include <stddef.h>
#include <stdint.h>

/*
 * Builds the generic section-list telemetry frame this node publishes as its
 * MQTT data-topic body -- byte-identical to what the base station's own SPI
 * link now sends (base-station/sketch/fuser.cpp's write_spectrum_section() +
 * num_sections writer) and what base-station/python/common/telemetry_frame.py
 * decodes (docs/SENSOR_TELEMETRY_FRAME_PLAN.md S3/S6): one codec, two
 * transports, driven off the single schema
 * base-station/telemetry_schema.json (frame_codec/telemetry_schema.h, also
 * generated from it -- see python/tools/gen_telemetry_schema.py). Unlike the
 * base station this node only ever emits SPECTRUM sections (mic, accel) -
 * no per-axis/scalar/time-series sections, matching
 * base-station/python/tools/satellite_node_sim.py's channel set exactly, so
 * a real node and the simulator stay interchangeable from the dashboard's
 * point of view.
 *
 * Replaces this file's earlier fixed spectrum_fused_payload_header codec
 * (byte-identical to the base station's own now-retired frame_types.h
 * struct): the base station's wire format moved to this generic
 * section-list shape first (SENSOR_TELEMETRY_FRAME_PLAN.md T2/T8), and this
 * satellite firmware -- the last piece T8 left outstanding -- now matches
 * it instead of the superseded fixed-struct shape.
 *
 * Wire layout (little-endian, matches telemetry_frame.py's encode_frame() /
 * encode_spectrum_frame()):
 *   [num_sections u8] then, per channel:
 *     [source_id u8][channel_id u8][data_kind u8][section_len u16]
 *     [fs f32][fft_size u16][bin_count u16][bins f32...]
 */

/* Per-channel wire overhead: the 5-byte section header (source/channel/kind +
 * section_len u16) plus a SPECTRUM body's fs/fft_size/bin_count preamble
 * (4 + 2 + 2) -- mirrors base-station/sketch/fuser.cpp's
 * FUSER_SECTION_OVERHEAD. Used by callers (fuser_task.cpp, transport_task.cpp)
 * to size their static buffers without duplicating this arithmetic. */
#define SPECTRUM_SECTION_OVERHEAD (5 + 4 + 2 + 2)

struct spectrum_channel {
	uint8_t channel_id; /* TELEM_CHANNEL_MIC / TELEM_CHANNEL_ACCEL (frame_codec/telemetry_schema.h) */
	float fs;
	uint16_t fft_size;
	uint16_t bin_count;
	const float *bins; /* bin_count entries; ignored (may be NULL) when bin_count == 0 */
};

/* Encodes a section-list frame carrying one SPECTRUM section per entry of
 * `channels` (num_channels of them, any order -- the decoder keys sections by
 * channel_id, not position). A sensor this node lacks entirely (app_config.h's
 * MIC_SENSOR_ENABLED/ACCEL_SENSOR_ENABLED) is simply left out of the array
 * rather than sent as a zero-bin_count placeholder section: bin_count=0 is
 * the schema's own "channel not part of this frame" signal
 * (telemetry_frame.py's decode_frame() docstring), which omission already
 * conveys with no extra bytes on the wire. source_id is always
 * TELEM_SOURCE_SATELLITE. Returns the encoded length, or 0 if out_buf_size is
 * too small or num_channels exceeds a u8. */
size_t telemetry_build_spectrum_frame(const struct spectrum_channel *channels, size_t num_channels,
				       uint8_t *out_buf, size_t out_buf_size);

#endif /* SPECTRUM_CODEC_H_ */
