#pragma once

#include <stdint.h>

#include "frame_codec/spectrum_codec.h"

/* One axis' 6 wire scalars, source-agnostic (net_task.c fills this from
 * mic_frame_t/imu_frame_t's per-axis fields). */
struct axis_scalars {
	float rms;
	float kurtosis;
	float std;
	float peak;
	float crest;
	float skewness;
};

/* Emits out[0..5] = {id_base+0=RMS, +1=KURTOSIS, +2=STD, +3=PEAK,
 * +4=CREST_FACTOR, +5=SKEWNESS} -- the per-axis id-block order
 * frame_codec/telemetry_schema.h's TELEM_SCALAR_*_X/_Y/_Z/_MIC blocks use.
 * Values are passed through unchanged. */
void scalar_map_build_axis(const struct axis_scalars *s, uint16_t id_base,
			    struct scalar_entry out[6]);
