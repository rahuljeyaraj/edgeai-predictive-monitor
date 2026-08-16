#ifndef DSP_SCALAR_STATS_H_
#define DSP_SCALAR_STATS_H_

#include <math.h>

/*
 * Per-channel time-domain scalars (rms/kurtosis/std/peak/crest_factor/
 * skewness), byte-for-byte the same formulas as base-station/sketch/
 * fuser.cpp's compute_scalars() and base-station/python/common/
 * raw_features.py -- all three must agree since pipeline/features.py's
 * build_feature_vector() concatenates whichever node's scalar tail onto
 * the same model input vector. `out_peak` is "max signed value," not "max
 * magnitude" (raw_features.py's peak() convention) -- don't fabsf() it.
 * `out_std` is population std (ddof=0); `out_kurtosis` is excess kurtosis.
 */
static inline void compute_scalars(const float *x, int len, float *out_rms, float *out_kurtosis,
				    float *out_std, float *out_peak, float *out_crest,
				    float *out_skew)
{
	float sum = 0.0f, sumsq = 0.0f, peak = x[0];

	for (int i = 0; i < len; i++) {
		sum += x[i];
		sumsq += x[i] * x[i];
		if (x[i] > peak) {
			peak = x[i];
		}
	}
	float mean = sum / len;
	float rms = sqrtf(sumsq / len);
	float variance = sumsq / len - mean * mean;

	if (variance < 0.0f) {
		variance = 0.0f; /* fp rounding guard */
	}
	float std = sqrtf(variance);

	float m3 = 0.0f, m4 = 0.0f;

	for (int i = 0; i < len; i++) {
		float d = x[i] - mean;
		float d2 = d * d;

		m3 += d2 * d;
		m4 += d2 * d2;
	}
	m3 /= len;
	m4 /= len;

	*out_rms = rms;
	*out_peak = peak;
	*out_std = std;
	*out_crest = (rms > 0.0f) ? (peak / rms) : 0.0f;
	*out_kurtosis = (std > 0.0f) ? (m4 / (std * std * std * std) - 3.0f) : 0.0f;
	*out_skew = (std > 0.0f) ? (m3 / (std * std * std)) : 0.0f;
}

#endif /* DSP_SCALAR_STATS_H_ */
