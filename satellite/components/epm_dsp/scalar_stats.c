#include "dsp/scalar_stats.h"

#include <math.h>

float epm_dsp_rms_from_sum_sq(float sum_sq, int n)
{
    return sqrtf(sum_sq / (float)n);
}

float epm_dsp_peak_abs(const float *x, int n)
{
    float peak = 0.0f;
    for (int i = 0; i < n; i++) {
        float a = fabsf(x[i]);
        if (a > peak) peak = a;
    }
    return peak;
}

float epm_dsp_peak_signed(const float *x, int n)
{
    float peak = x[0];
    for (int i = 1; i < n; i++) {
        if (x[i] > peak) peak = x[i];
    }
    return peak;
}

float epm_dsp_crest_factor(float peak, float rms)
{
    return (rms > 1e-8f) ? (peak / rms) : 0.0f;
}

float epm_dsp_kurtosis_from_sums(float sum_sq, float sum4, int n, float fallback)
{
    float var = sum_sq / (float)n;
    if (var > 1e-12f) {
        return (sum4 / (float)n) / (var * var) - 3.0f; /* excess/Fisher, ADR-018 */
    }
    return fallback;
}

float epm_dsp_std_from_sums(float sum, float sum_sq, int n)
{
    float mean = sum / (float)n;
    float var  = sum_sq / (float)n - mean * mean;
    return (var > 0.0f) ? sqrtf(var) : 0.0f;
}

float epm_dsp_skewness_from_sums(float sum, float sum_sq, float sum_cube, int n, float fallback)
{
    float mean = sum / (float)n;
    float var  = sum_sq / (float)n - mean * mean;
    if (var <= 1e-12f) {
        return fallback;
    }
    float std = sqrtf(var);
    float m3  = sum_cube / (float)n - 3.0f * mean * (sum_sq / (float)n) + 2.0f * mean * mean * mean;
    return m3 / (std * std * std);
}
