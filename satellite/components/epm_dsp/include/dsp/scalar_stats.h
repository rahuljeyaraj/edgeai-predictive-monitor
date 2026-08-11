/*
 * scalar_stats.h — Pure-math time-domain block statistics.
 *
 * The SIMD reductions feeding these (dsps_dotprod_f32, dsps_mul_f32) stay in
 * the task file; this covers only the pure-C ratio/peak math built on top of
 * their outputs.
 */

#pragma once

#ifdef __cplusplus
extern "C" {
#endif

/** RMS from a precomputed sum-of-squares (e.g. dsps_dotprod_f32(x,x)). */
float epm_dsp_rms_from_sum_sq(float sum_sq, int n);

/** Peak absolute value over x[0..n). Used for crest factor (ISO-standard
 * peak/rms), independent of the wire "peak" scalar's signed-max convention. */
float epm_dsp_peak_abs(const float *x, int n);

/**
 * Signed maximum over x[0..n) (i.e. x.max(), not abs(x).max()) — the wire
 * "peak" scalar's convention, matching the reference implementation
 * (ADR-019). Undefined for n == 0.
 */
float epm_dsp_peak_signed(const float *x, int n);

/** Crest factor = peak/rms, or 0 if rms is too small to divide by. */
float epm_dsp_crest_factor(float peak, float rms);

/**
 * Excess/Fisher kurtosis (Gaussian ≈ 0.0, ADR-018) = (sum4/n)/(sum_sq/n)^2 - 3,
 * or `fallback` if the variance is too small to divide by (matches the
 * mic_task.c convention of leaving the previous value in place rather than
 * dividing by ~0 — `fallback` must itself already be in excess terms).
 */
float epm_dsp_kurtosis_from_sums(float sum_sq, float sum4, int n, float fallback);

/**
 * Population standard deviation from Σx and Σx² (mean subtracted internally,
 * so it's correct whether or not the caller pre-removed DC).
 */
float epm_dsp_std_from_sums(float sum, float sum_sq, int n);

/**
 * Skewness = mean(((x-mean(x))/std(x))^3), from Σx, Σx², Σx³ (mean subtracted
 * internally), or `fallback` if the variance is too small to divide by.
 */
float epm_dsp_skewness_from_sums(float sum, float sum_sq, float sum_cube, int n, float fallback);

#ifdef __cplusplus
}
#endif
