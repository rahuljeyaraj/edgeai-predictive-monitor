/*
 * test_frame_encode.c — host-buildable encode test for components/epm_codec/.
 *
 * Links only the sources under components/epm_codec/ (zero ESP-IDF includes
 * there), builds
 * the same 5-section synthetic frame src/threads/net_task.c publishes on
 * real hardware (4 SPECTRUM channels + 1 SCALAR_SET), and writes the raw
 * bytes to tests/host/out/frame.bin for tests/host/decode_check.py to
 * verify against the reference repo's unmodified Python pipeline.
 *
 * Build (see docs/decisions/ADR-011-mqtt-transport-added.md's Verification
 * section):
 *   gcc -I components/epm_codec/include -o /tmp/tfe tests/host/test_frame_encode.c
 *       components/epm_codec/wire_protocol.c components/epm_codec/spectrum_codec.c
 *       -lm && /tmp/tfe
 */

#include <assert.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>

#include "frame_codec/spectrum_codec.h"
#include "frame_codec/telemetry_schema.h"

#define BIN_COUNT 128
#define FRAME_BUF_SIZE 4096
#define EXPECTED_FRAME_LEN 2251
#define EXPECTED_NUM_SECTIONS 5

static void fill_bins(float *bins, size_t n, uint32_t phase)
{
	float peak_pos = (float)(phase % n);

	for (size_t i = 0; i < n; i++) {
		float decay = expf(-(float)i / ((float)n * 0.25f));
		float dist = (float)i - peak_pos;
		float peak = expf(-(dist * dist) / (2.0f * 4.0f * 4.0f));

		bins[i] = 0.05f * decay + 0.5f * peak;
	}
}

static void fill_axis_scalars(struct scalar_entry *entries, uint16_t id_base)
{
	entries[0] = (struct scalar_entry){.id = (uint16_t)(id_base + 0), .value = 0.10f};
	entries[1] = (struct scalar_entry){.id = (uint16_t)(id_base + 1), .value = 3.00f};
	entries[2] = (struct scalar_entry){.id = (uint16_t)(id_base + 2), .value = 0.10f};
	entries[3] = (struct scalar_entry){.id = (uint16_t)(id_base + 3), .value = 0.40f};
	entries[4] = (struct scalar_entry){.id = (uint16_t)(id_base + 4), .value = 4.00f};
	entries[5] = (struct scalar_entry){.id = (uint16_t)(id_base + 5), .value = 0.00f};
}

int main(void)
{
	static float mic_bins[BIN_COUNT];
	static float accel_x_bins[BIN_COUNT];
	static float accel_y_bins[BIN_COUNT];
	static float accel_z_bins[BIN_COUNT];

	fill_bins(mic_bins, BIN_COUNT, 0);
	fill_bins(accel_x_bins, BIN_COUNT, 7);
	fill_bins(accel_y_bins, BIN_COUNT, 13);
	fill_bins(accel_z_bins, BIN_COUNT, 19);

	struct spectrum_channel channels[4] = {
		{.channel_id = TELEM_CHANNEL_MIC,
		 .fs = 48000.0f,
		 .fft_size = 2048,
		 .bin_count = BIN_COUNT,
		 .bins = mic_bins},
		{.channel_id = TELEM_CHANNEL_ACCEL_X,
		 .fs = 6400.0f,
		 .fft_size = 1024,
		 .bin_count = BIN_COUNT,
		 .bins = accel_x_bins},
		{.channel_id = TELEM_CHANNEL_ACCEL_Y,
		 .fs = 6400.0f,
		 .fft_size = 1024,
		 .bin_count = BIN_COUNT,
		 .bins = accel_y_bins},
		{.channel_id = TELEM_CHANNEL_ACCEL_Z,
		 .fs = 6400.0f,
		 .fft_size = 1024,
		 .bin_count = BIN_COUNT,
		 .bins = accel_z_bins},
	};

	struct scalar_entry scalars[24];

	fill_axis_scalars(&scalars[0], TELEM_SCALAR_RMS_X);
	fill_axis_scalars(&scalars[6], TELEM_SCALAR_RMS_Y);
	fill_axis_scalars(&scalars[12], TELEM_SCALAR_RMS_Z);
	fill_axis_scalars(&scalars[18], TELEM_SCALAR_RMS_MIC);

	static uint8_t out_buf[FRAME_BUF_SIZE];
	size_t len = telemetry_build_frame(channels, 4, scalars, 24, out_buf, sizeof(out_buf));

	assert(len == EXPECTED_FRAME_LEN);
	assert(out_buf[0] == EXPECTED_NUM_SECTIONS);

	FILE *f = fopen("tests/host/out/frame.bin", "wb");

	assert(f != NULL);
	assert(fwrite(out_buf, 1, len, f) == len);
	fclose(f);

	printf("RESULT: PASS (frame.bin: %zu bytes, %d sections)\n", len, out_buf[0]);
	return 0;
}
