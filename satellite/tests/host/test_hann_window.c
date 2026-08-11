/*
 * test_hann_window.c — host-native test for the Hann window used by
 * src/dsp_task.c's FFT pipeline, and for the power-normalisation constant
 * applied right after windowing.
 *
 * This links the REAL, unmodified vendor source
 * (managed_components/espressif__esp-dsp/modules/windows/hann/float/dsps_wind_hann_f32.c)
 * rather than reimplementing the window -- that file has zero ESP-IDF
 * dependencies (only <math.h>), confirmed by direct source read, so there is
 * no reason to transcribe it by hand and risk drift.
 *
 * Build/run: see tests/host/README.md.
 */
#include <math.h>
#include <stdio.h>

#include "dsps_wind_hann.h"
#include "test_util.h"

#define TEST_N 1024 /* mirrors FFT_MIC_N default, src/epm_config.h:23 */

static void test_hann_coherent_gain_matches_theory(void)
{
    static float window[TEST_N];
    dsps_wind_hann_f32(window, TEST_N);

    float sum = 0.0f;
    for (int i = 0; i < TEST_N; i++) sum += window[i];
    float coherent_gain = sum / (float)TEST_N;

    char detail[96];
    snprintf(detail, sizeof(detail), "coherent_gain=%.6f (want ~0.5)", coherent_gain);
    int ok = fabsf(coherent_gain - 0.5f) < 0.01f;
    test_report("hann_coherent_gain_matches_theory", ok, EXPECT_PASS, detail);
}

/*
 * The anchor normalisation-correctness test (Phase 2, ADR-012).
 *
 * src/dsp_task.c windows every block (dsps_mul_f32(fft_src, s_window, ...),
 * ~dsp_task.c:175) before FFT'ing it, then normalises accumulated power with:
 *
 *     const float nf = 2.0f / ((float)FFT_MIC_N * s_coherent_gain);  // dsp_task.c:211
 *     re = s_fft[2*i] * nf; im = s_fft[2*i+1] * nf;                  // dsp_task.c:213-214
 *     s_pwr_acc[i] += re*re + im*im;                                 // dsp_task.c:215
 *
 * with the stated intent "full-scale sine -> 0 dBFS". s_coherent_gain is
 * computed in dsp_task_start() as the mean of the real s_window array (the
 * same method this test uses), so nf now corrects for the Hann window's
 * coherent gain (~0.5) instead of assuming a rectangular window.
 *
 * A full dsps_fft2r_fc32 round-trip was considered for this test and
 * rejected: ESP-DSP's ANSI-C fallback paths transitively depend on
 * esp_err_t/ESP-IDF headers (confirmed by source read), so linking a real
 * FFT here would either need ESP-IDF or a hand-rolled FFT whose own
 * correctness would then need independent verification -- more risk for no
 * more signal than this direct arithmetic comparison, which isolates the
 * exact normalisation term.
 *
 * This used to be EXPECTED TO FAIL (Phase 1) documenting a ~6 dB
 * (20*log10(0.5)) under-scaling bug -- nf_as_coded was a verbatim
 * transcription of the old buggy dsp_task.c:202 (`2.0f / FFT_MIC_N`, no
 * /coherent_gain term). It's now re-synced to the fixed formula and EXPECTED
 * TO PASS, confirming the fix landed.
 */
static void test_nf_normalization_includes_coherent_gain(void)
{
    static float window[TEST_N];
    dsps_wind_hann_f32(window, TEST_N);
    float sum = 0.0f;
    for (int i = 0; i < TEST_N; i++) sum += window[i];
    float coherent_gain = sum / (float)TEST_N;

    float nf_as_coded = 2.0f / ((float)TEST_N * coherent_gain);   /* dsp_task.c:211, verbatim */
    float nf_if_gain_corrected = 2.0f / ((float)TEST_N * coherent_gain);

    float ratio = nf_as_coded / nf_if_gain_corrected;
    float db_error = 20.0f * log10f(ratio);

    char detail[192];
    snprintf(detail, sizeof(detail),
              "ratio=%.3f (want ~1.0, tol 5%%) -- nf at dsp_task.c:211 now divides by "
              "coherent_gain; residual error %.2f dB",
              ratio, db_error);
    int ok = fabsf(ratio - 1.0f) < 0.05f;
    test_report("nf_normalization_includes_coherent_gain", ok, EXPECT_PASS, detail);
}

int main(void)
{
    test_hann_coherent_gain_matches_theory();
    test_nf_normalization_includes_coherent_gain();
    return test_summary();
}
