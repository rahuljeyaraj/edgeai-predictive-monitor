/*
 * test_spectrum.c — host-native tests for components/epm_dsp/spectrum.c's
 * epm_dsp_reduce_bins() (ADR-020). Unlike test_scalar_stats.c's hand-mirrored
 * approach, spectrum.c has zero ESP-IDF includes and is linked here directly
 * (same pattern as test_frame_encode.c linking epm_codec's real sources).
 *
 * Build/run: see tests/host/README.md.
 */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>

#include "dsp/spectrum.h"
#include "epm_config.h"
#include "test_util.h"

#define FLOOR_DB   -80.0f
#define STRONG_DB    0.0f

/* Fills n bins at FLOOR_DB except peak_idx, set to STRONG_DB. */
static void build_spectrum(float *db, int n, int peak_idx)
{
    for (int i = 0; i < n; i++) db[i] = FLOOR_DB;
    db[peak_idx] = STRONG_DB;
}

/* Analytically expected reduced value for a band of `band` bins containing
 * exactly one STRONG_DB bin and (band-1) FLOOR_DB bins -- mirrors
 * epm_dsp_reduce_bins()'s own power-domain-average formula so the test
 * doesn't hardcode a magic constant. */
static float expected_band_db(int band)
{
    float pwr_sum = powf(10.0f, STRONG_DB / 10.0f) + (float)(band - 1) * powf(10.0f, FLOOR_DB / 10.0f);
    float avg_pwr = pwr_sum / (float)band;
    return 10.0f * log10f(avg_pwr + 1e-12f);
}

static void test_divisibility_guard(void)
{
    float in_db[100], out_db[7];
    for (int i = 0; i < 100; i++) in_db[i] = 0.0f;

    int rc = epm_dsp_reduce_bins(in_db, 100, out_db, 7);
    test_report("divisibility_guard", rc == -1, EXPECT_PASS,
                "100 bins -> 7 bins (not a multiple) must be rejected");
}

/* Shared logic for the 512->128 and 1024->128 cases: build an in_n-bin
 * spectrum with a single strong bin, reduce to out_n, and check the reduced
 * spectrum's max lands in the expected output band, at the expected value,
 * and nowhere else. */
static void run_reduction_case(const char *name, int in_n, int out_n, int in_peak_idx)
{
    float *in_db  = malloc(sizeof(float) * (size_t)in_n);
    float *out_db = malloc(sizeof(float) * (size_t)out_n);
    int band = in_n / out_n;
    int expect_out_idx = in_peak_idx / band;
    char detail[128];

    build_spectrum(in_db, in_n, in_peak_idx);

    int rc = epm_dsp_reduce_bins(in_db, in_n, out_db, out_n);

    int argmax = 0;
    for (int i = 1; i < out_n; i++) {
        if (out_db[i] > out_db[argmax]) argmax = i;
    }

    float expected = expected_band_db(band);
    float got = out_db[expect_out_idx];
    int value_ok = fabsf(got - expected) < 0.01f;

    snprintf(detail, sizeof(detail),
             "rc=%d argmax=%d (want %d) out[%d]=%.4f (want %.4f)",
             rc, argmax, expect_out_idx, expect_out_idx, got, expected);
    test_report(name, rc == 0 && argmax == expect_out_idx && value_ok, EXPECT_PASS, detail);

    free(in_db);
    free(out_db);
}

static void test_reduce_512_to_128(void)
{
    /* Peak in input bin 37 -> expected output band 37/4 = 9. */
    run_reduction_case("reduce_512_to_128_peak", 512, 128, 37);
}

static void test_reduce_1024_to_128(void)
{
    /* Peak in input bin 601 -> expected output band 601/8 = 75. */
    run_reduction_case("reduce_1024_to_128_peak", 1024, 128, 601);
}

static void test_flat_spectrum(void)
{
    int in_n = 512, out_n = 128;
    float in_db[512], out_db[128];

    for (int i = 0; i < in_n; i++) in_db[i] = -42.0f;

    int rc = epm_dsp_reduce_bins(in_db, in_n, out_db, out_n);

    int all_flat = 1;
    for (int i = 0; i < out_n; i++) {
        if (fabsf(out_db[i] - (-42.0f)) > 0.01f) all_flat = 0;
    }

    test_report("flat_spectrum_reduces_to_same_value", rc == 0 && all_flat, EXPECT_PASS,
                "uniform -42 dBFS input must reduce to uniform -42 dBFS output");
}

/* net_task.c's wire-reported fft_size for the 4 pooled channels must be the
 * *effective*, post-pooling fft_size (src/epm_config.h's
 * EPM_MIC_WIRE_FFT_SIZE/EPM_IMU_WIRE_FFT_SIZE), not the native
 * FFT_MIC_N/FFT_IMU_N -- otherwise a consumer computing bin width the
 * standard way (fs / fft_size) recovers the native bin width instead of the
 * true pooled one (ADR-020 addendum: the mislabeling this guards against
 * capped the mic wire spectrum's apparent range at ~1984 Hz of a real
 * 0-8000 Hz Nyquist range). */
static void test_wire_fft_size_true_bin_width(void)
{
    float mic_bin_hz = (float)MIC_FS_HZ / (float)EPM_MIC_WIRE_FFT_SIZE;
    float imu_bin_hz = (float)IMU_FS_HZ / (float)EPM_IMU_WIRE_FFT_SIZE;
    /* ADR-040 raised EPM_MODEL_SPECTRUM_BINS 128->256, halving both pooling
     * bands and therefore halving the true bin width: mic 187.5->93.75 Hz,
     * accel 100.0->50.0 Hz. */
    int mic_ok = fabsf(mic_bin_hz - 93.75f) < 0.001f;
    int imu_ok = fabsf(imu_bin_hz - 50.0f) < 0.001f;
    char detail[160];

    snprintf(detail, sizeof(detail),
             "mic fs/fft_size=%.4f Hz (want 93.75), accel fs/fft_size=%.4f Hz (want 50.0)",
             (double)mic_bin_hz, (double)imu_bin_hz);
    test_report("wire_fft_size_true_bin_width", mic_ok && imu_ok, EXPECT_PASS, detail);
}

int main(void)
{
    test_divisibility_guard();
    test_reduce_512_to_128();
    test_reduce_1024_to_128();
    test_flat_spectrum();
    test_wire_fft_size_true_bin_width();

    return test_summary();
}
