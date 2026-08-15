/*
 * test_scalar_map.c — host-native test for components/epm_codec/scalar_map.c's
 * scalar_map_build_axis() (ADR-021 / Phase 6c). Links the real source, same
 * pattern as test_spectrum.c linking epm_dsp/spectrum.c.
 *
 * Build/run: see tests/host/README.md.
 */
#include <math.h>
#include <stdio.h>

#include "frame_codec/scalar_map.h"
#include "test_util.h"

static void test_id_sequence_and_values(void)
{
	struct axis_scalars s = {
		.rms      = 0.11f,
		.kurtosis = 2.22f,
		.std      = 0.33f,
		.peak     = 4.44f,
		.crest    = 5.55f,
		.skewness = 0.66f,
	};
	struct scalar_entry out[6];
	uint16_t id_base = 7; /* TELEM_SCALAR_RMS_X */

	scalar_map_build_axis(&s, id_base, out);

	uint16_t expect_ids[6] = {
		(uint16_t)(id_base + 0), (uint16_t)(id_base + 1), (uint16_t)(id_base + 2),
		(uint16_t)(id_base + 3), (uint16_t)(id_base + 4), (uint16_t)(id_base + 5),
	};
	float expect_values[6] = {s.rms, s.kurtosis, s.std, s.peak, s.crest, s.skewness};

	int ok = 1;
	char detail[256];
	int off = 0;

	for (int i = 0; i < 6; i++) {
		int id_ok  = out[i].id == expect_ids[i];
		int val_ok = fabsf(out[i].value - expect_values[i]) < 1e-6f;

		if (!id_ok || !val_ok) ok = 0;
		off += snprintf(detail + off, sizeof(detail) - (size_t)off,
				 "[%d] id=%u(want %u) val=%.4f(want %.4f) ",
				 i, out[i].id, expect_ids[i], (double)out[i].value,
				 (double)expect_values[i]);
	}

	test_report("scalar_map_id_sequence_and_values", ok, EXPECT_PASS, detail);
}

int main(void)
{
	test_id_sequence_and_values();

	return test_summary();
}
