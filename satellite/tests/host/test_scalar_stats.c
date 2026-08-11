/*
 * test_scalar_stats.c — host-native anchor tests for the scalar-stats math
 * computed on-device in src/mic_task.c (NOT src/dsp_task.c -- dsp_task.c
 * only owns the FFT/Hann/Welch pipeline; RMS/crest/kurtosis are computed
 * per-block in mic_task.c before a block is even handed off to dsp_task).
 *
 * src/mic_task.c is ESP-IDF-coupled (FreeRTOS, esp_timer, ESP-DSP SIMD
 * calls) and cannot be #include'd or linked here. Instead each function
 * below is a plain-C, scalar (non-SIMD) transcription of the exact formula
 * at the cited mic_task.c line range -- if mic_task.c's math changes, these
 * mirrors must be hand-resynced (see tests/host/README.md).
 *
 * Build/run: see tests/host/README.md.
 */
#include <math.h>
#include <stdint.h>
#include <stdio.h>

#include "test_util.h"

#define TEST_N 1024 /* mirrors FFT_MIC_N default, src/epm_config.h:23 */

/* Mirrors src/mic_task.c:112-117 (RMS on DC-removed signal via
 * dsps_dotprod_f32 then sqrtf(sum_sq/N)). */
static float mirror_rms(const float *x, int n)
{
    float sum_sq = 0.0f;
    for (int i = 0; i < n; i++) sum_sq += x[i] * x[i];
    return sqrtf(sum_sq / (float)n);
}

/* Mirrors src/mic_task.c:119-127 (crest = peak(|x|)/rms, fallback 0.0f when
 * rms <= 1e-8f). */
static float mirror_crest(const float *x, int n, float rms)
{
    float peak = 0.0f;
    for (int i = 0; i < n; i++) {
        float a = fabsf(x[i]);
        if (a > peak) peak = a;
    }
    return (rms > 1e-8f) ? (peak / rms) : 0.0f;
}

/* Mirrors src/mic_task.c's "3b'" step (signed max x.max(), NOT abs(x).max())
 * -- the wire "peak" scalar's convention, ADR-019. Deliberately separate from
 * mirror_crest's peak(|x|) above: they diverge whenever the largest-magnitude
 * excursion is negative-going. */
static float mirror_peak_signed(const float *x, int n)
{
    float peak = x[0];
    for (int i = 1; i < n; i++) {
        if (x[i] > peak) peak = x[i];
    }
    return peak;
}

/* Mirrors src/mic_task.c:129-138 (excess/Fisher kurtosis = (Sum(x^4)/N) /
 * (Sum(x^2)/N)^2 - 3, fallback 0.0f when var <= 1e-12f -- matches
 * mic_task.c:82's last_kurtosis init value). ADR-018 reversed ADR-014:
 * the reference's raw_features.py explicitly subtracts 3.0 ("# excess
 * kurtosis") and shares fuser.cpp's on-device math, so excess/Fisher
 * (Gaussian =~ 0) is the real wire convention, not RAW/Pearson. */
static float mirror_kurtosis(const float *x, int n)
{
    float sum_sq = 0.0f, sum4 = 0.0f;
    for (int i = 0; i < n; i++) sum_sq += x[i] * x[i];
    float var = sum_sq / (float)n;
    if (var <= 1e-12f) return 0.0f;
    for (int i = 0; i < n; i++) {
        float x2 = x[i] * x[i];
        sum4 += x2 * x2;
    }
    return (sum4 / (float)n) / (var * var) - 3.0f;
}

/* Mirrors components/epm_dsp/scalar_stats.c's epm_dsp_std_from_sums, generic
 * mean-centered form (not the sum=0.0f DC-removed-shortcut call site in
 * mic_task.c -- imu_task.c calls the same helper on non-DC-removed data, so
 * the mirror here computes sum/mean generically to match both). */
static float mirror_std(const float *x, int n)
{
    float sum = 0.0f, sum_sq = 0.0f;
    for (int i = 0; i < n; i++) { sum += x[i]; sum_sq += x[i] * x[i]; }
    float mean = sum / (float)n;
    float var  = sum_sq / (float)n - mean * mean;
    return (var > 0.0f) ? sqrtf(var) : 0.0f;
}

/* Mirrors components/epm_dsp/scalar_stats.c's epm_dsp_skewness_from_sums:
 * skewness = m3 / std^3, m3 = Sum(x^3)/N - 3*mean*Sum(x^2)/N + 2*mean^3,
 * fallback 0.0f when var <= 1e-12f. */
static float mirror_skewness(const float *x, int n)
{
    float sum = 0.0f, sum_sq = 0.0f, sum_cube = 0.0f;
    for (int i = 0; i < n; i++) {
        sum      += x[i];
        sum_sq   += x[i] * x[i];
        sum_cube += x[i] * x[i] * x[i];
    }
    float mean = sum / (float)n;
    float var  = sum_sq / (float)n - mean * mean;
    if (var <= 1e-12f) return 0.0f;
    float std = sqrtf(var);
    float m3  = sum_cube / (float)n - 3.0f * mean * (sum_sq / (float)n) + 2.0f * mean * mean * mean;
    return m3 / (std * std * std);
}

/* Deterministic splitmix64 PRNG -- no libc rand()/srand() dependency, so
 * results are identical across platforms/compilers. */
static uint64_t s_rng_state;

static uint64_t splitmix64_next(void)
{
    uint64_t z = (s_rng_state += 0x9E3779B97F4A7C15ULL);
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
    return z ^ (z >> 31);
}

static float rng_uniform_open01(void)
{
    /* (0,1) open interval -- avoids log(0.0) in Box-Muller below. */
    return ((float)(splitmix64_next() >> 11) + 0.5f) / (float)(1ULL << 53);
}

static void fill_gaussian(float *x, int n, uint64_t seed)
{
    s_rng_state = seed;
    for (int i = 0; i < n; i += 2) {
        float u1 = rng_uniform_open01();
        float u2 = rng_uniform_open01();
        float r = sqrtf(-2.0f * logf(u1));
        float g0 = r * cosf(2.0f * (float)M_PI * u2);
        float g1 = r * sinf(2.0f * (float)M_PI * u2);
        x[i] = g0;
        if (i + 1 < n) x[i + 1] = g1;
    }
}

static void test_sine_crest_factor(void)
{
    static float x[TEST_N];
    /* Bin-aligned: 8 whole cycles across 1024 samples so the discrete sum
     * Sum(cos^2) is exactly N/2 (orthogonality), no windowing-leakage error. */
    for (int i = 0; i < TEST_N; i++) {
        x[i] = cosf(2.0f * (float)M_PI * 8.0f * (float)i / (float)TEST_N);
    }
    float rms = mirror_rms(x, TEST_N);
    float crest = mirror_crest(x, TEST_N, rms);
    char detail[128];
    snprintf(detail, sizeof(detail), "rms=%.6f (want 0.707107) crest=%.6f (want 1.414214, sqrt(2))",
              rms, crest);
    int ok = fabsf(crest - 1.41421356f) < 1e-4f;
    test_report("sine_crest_factor", ok, EXPECT_PASS, detail);
}

static void test_square_crest_factor(void)
{
    static float x[TEST_N];
    /* +-1.0 square wave, period 128 samples (64 high, 64 low). */
    for (int i = 0; i < TEST_N; i++) {
        x[i] = ((i % 128) < 64) ? 1.0f : -1.0f;
    }
    float rms = mirror_rms(x, TEST_N);
    float crest = mirror_crest(x, TEST_N, rms);
    char detail[128];
    snprintf(detail, sizeof(detail), "rms=%.6f (want 1.000000) crest=%.6f (want 1.000000)", rms, crest);
    int ok = fabsf(crest - 1.0f) < 1e-6f;
    test_report("square_crest_factor", ok, EXPECT_PASS, detail);
}

static void test_gaussian_kurtosis_excess_convention(void)
{
    static float x[20000];
    fill_gaussian(x, 20000, 0xA53Cu);
    float k = mirror_kurtosis(x, 20000);
    /* Sampling std-error of excess kurtosis ~= sqrt(24/N) ~= 0.035 at N=20000;
     * +-0.15 is ~4x that margin, chosen during planning against an
     * independent PRNG run that converged to ~0.02 at both N=20000 and
     * N=100000, to avoid flakiness from PRNG-specific variance. */
    char detail[160];
    snprintf(detail, sizeof(detail),
              "kurtosis=%.4f (want ~0.0, excess/Fisher convention -- ADR-018 confirms this is "
              "the firmware's actual wire convention, reversing ADR-014's RAW/Pearson choice)", k);
    int ok = fabsf(k - 0.0f) < 0.15f;
    test_report("gaussian_kurtosis_excess_convention", ok, EXPECT_PASS, detail);
}

static void test_silence_fallback_defaults(void)
{
    static float x[TEST_N] = {0};
    float rms = mirror_rms(x, TEST_N);
    float crest = mirror_crest(x, TEST_N, rms);
    float k = mirror_kurtosis(x, TEST_N);
    char detail[128];
    snprintf(detail, sizeof(detail), "rms=%.6f crest=%.6f (want 0.0) kurtosis=%.6f (want 0.0)",
              rms, crest, k);
    int ok = (crest == 0.0f) && (k == 0.0f);
    test_report("silence_fallback_defaults", ok, EXPECT_PASS, detail);
}

static void test_std_known_variance(void)
{
    static float x[TEST_N];
    /* Zero-mean +-2.0 square wave: mean=0, Sum(x^2)/N=4 -> var=4, std=2.0 exactly. */
    for (int i = 0; i < TEST_N; i++) {
        x[i] = ((i % 128) < 64) ? 2.0f : -2.0f;
    }
    float std = mirror_std(x, TEST_N);
    char detail[128];
    snprintf(detail, sizeof(detail), "std=%.6f (want 2.000000, zero-mean square wave var=4)", std);
    int ok = fabsf(std - 2.0f) < 1e-5f;
    test_report("std_known_variance", ok, EXPECT_PASS, detail);
}

static void test_skewness_symmetric_gaussian(void)
{
    static float x[20000];
    fill_gaussian(x, 20000, 0xA53Cu);
    float s = mirror_skewness(x, 20000);
    /* Sampling std-error of skewness ~= sqrt(6/N) ~= 0.017 at N=20000; +-0.15
     * mirrors the margin used for the kurtosis Gaussian anchor above. */
    char detail[160];
    snprintf(detail, sizeof(detail), "skewness=%.4f (want ~0.0, symmetric Gaussian distribution)", s);
    int ok = fabsf(s - 0.0f) < 0.15f;
    test_report("skewness_symmetric_gaussian", ok, EXPECT_PASS, detail);
}

static void test_skewness_exponential_skewed(void)
{
    static float x[20000];
    s_rng_state = 0xE4700Eu;
    for (int i = 0; i < 20000; i++) {
        /* Exponential(rate=1) via inverse-CDF sampling: theoretical skewness
         * = 2.0 (right-skewed) -- a known-nonzero anchor distinct from the
         * symmetric Gaussian case above. */
        x[i] = -logf(rng_uniform_open01());
    }
    float s = mirror_skewness(x, 20000);
    char detail[128];
    snprintf(detail, sizeof(detail), "skewness=%.4f (want > 1.0, right-skewed Exponential(1), theory=2.0)", s);
    int ok = s > 1.0f;
    test_report("skewness_exponential_skewed", ok, EXPECT_PASS, detail);
}

static void test_peak_signed_diverges_from_abs_max(void)
{
    /* Deliberately asymmetric: largest-magnitude excursion (-0.9) is
     * negative-going, larger than the positive excursion (0.5). Signed max
     * (the wire "peak" scalar, ADR-019) misses it -- this is the exact
     * known failure mode ADR-019 documents, not a bug in this test. Crest
     * factor's independent peak(|x|)/rms still captures it via a high
     * crest value, which is why the failure mode is considered acceptable. */
    static const float x[6] = {0.1f, 0.5f, -0.9f, 0.2f, -0.3f, 0.0f};
    float rms          = mirror_rms(x, 6);
    float crest        = mirror_crest(x, 6, rms);
    float peak_signed  = mirror_peak_signed(x, 6);
    char detail[192];
    snprintf(detail, sizeof(detail),
              "peak_signed=%.4f (want 0.5, misses the -0.9 excursion by design) "
              "crest=%.4f (still reflects the 0.9 magnitude via peak(|x|)/rms)",
              peak_signed, crest);
    int ok = fabsf(peak_signed - 0.5f) < 1e-6f;
    test_report("peak_signed_diverges_from_abs_max", ok, EXPECT_PASS, detail);
}

int main(void)
{
    test_sine_crest_factor();
    test_square_crest_factor();
    test_gaussian_kurtosis_excess_convention();
    test_silence_fallback_defaults();
    test_std_known_variance();
    test_skewness_symmetric_gaussian();
    test_skewness_exponential_skewed();
    test_peak_signed_diverges_from_abs_max();
    return test_summary();
}
