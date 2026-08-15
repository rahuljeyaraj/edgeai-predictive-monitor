/*
 * test_envelope.c — host-native tests for components/epm_dsp/envelope.c
 * (Part G Phase 11 / ADR-032). Links the real source directly, same pattern
 * as test_spectrum.c.
 *
 * This is the test Part G's own text calls for: a carrier amplitude-
 * modulated at a known low frequency must produce a clear peak (+ harmonic)
 * in the envelope spectrum; a plain unmodulated tone must not. No FFT is
 * available host-side (dsps_fft2r_fc32 is ESP-DSP SIMD and stays in the task
 * file, per spectrum.h's established split) — a small reference Goertzel
 * detector stands in for "is there a spectral peak at this exact bin"
 * without needing a full FFT.
 *
 * Parameters mirror what Phase 11a wires into imu_task.c: fs=IMU_FS_HZ
 * (25600 Hz), band 2-8 kHz (fault_models.py's structural-resonance
 * convention), envelope low-pass 1 kHz, decimate by 8 -> 3200 Hz decimated
 * rate, block 2048 -> 256 samples (matches FFT_IMU_N and its /8 envelope
 * FFT size).
 *
 * Build/run: see tests/host/README.md.
 */
#include <math.h>
#include <stdio.h>

#include "dsp/envelope.h"
#include "test_util.h"

#define PI            3.14159265358979323846f
#define FS_HZ         25600.0f
#define BAND_LO_HZ    2000.0f
#define BAND_HI_HZ    8000.0f
#define ENV_LP_HZ     1000.0f
#define DECIM_M       8
#define IN_N          2048
#define OUT_N         (IN_N / DECIM_M)   /* 256 */

#define CARRIER_HZ    4000.0f   /* mid resonance band */
#define MOD_HZ        100.0f    /* stand-in bearing-defect fundamental */
#define MOD_DEPTH     0.8f
#define MOD_DEPTH_2ND 0.3f      /* 2nd harmonic: real defect envelopes are a
                                 * periodic impact train, not a pure sinusoid,
                                 * so they carry defect-frequency harmonics —
                                 * a single-tone AM envelope wouldn't (linear
                                 * rectification demodulates a pure sinusoid
                                 * cleanly, with no harmonic distortion) */

/* Reference single-bin DFT (Goertzel) magnitude at exact bin k of an
 * n-sample block. MOD_HZ*OUT_N/decimated_fs is an exact integer (8) at
 * these parameters, so there is no spectral leakage to account for. */
static float goertzel_mag(const float *x, int n, int k)
{
    float w      = 2.0f * PI * (float)k / (float)n;
    float coeff  = 2.0f * cosf(w);
    float s1 = 0.0f, s2 = 0.0f;

    for (int i = 0; i < n; i++) {
        float s0 = x[i] + coeff * s1 - s2;
        s2 = s1;
        s1 = s0;
    }

    float real = s1 - s2 * cosf(w);
    float imag = s2 * sinf(w);
    return sqrtf(real * real + imag * imag);
}

static void build_carrier(float *x, int n, int modulated)
{
    for (int i = 0; i < n; i++) {
        float t = (float)i / FS_HZ;
        float carrier = cosf(2.0f * PI * CARRIER_HZ * t);
        if (modulated) {
            float envelope = 1.0f + MOD_DEPTH * cosf(2.0f * PI * MOD_HZ * t)
                                  + MOD_DEPTH_2ND * cosf(2.0f * PI * 2.0f * MOD_HZ * t);
            x[i] = envelope * carrier;
        } else {
            x[i] = carrier;
        }
    }
}

static void test_invalid_args_rejected(void)
{
    epm_dsp_envelope_t env;
    float in[IN_N] = {0};
    float out[OUT_N];

    epm_dsp_envelope_init(&env, FS_HZ, BAND_LO_HZ, BAND_HI_HZ, ENV_LP_HZ);

    int rc_bad_len   = epm_dsp_envelope_process(&env, in, IN_N, DECIM_M, out, OUT_N - 1);
    int rc_bad_decim = epm_dsp_envelope_process(&env, in, IN_N, 0, out, OUT_N);
    int rc_null      = epm_dsp_envelope_process(&env, NULL, IN_N, DECIM_M, out, OUT_N);

    test_report("invalid_args_rejected",
                rc_bad_len == -1 && rc_bad_decim == -1 && rc_null == -1, EXPECT_PASS,
                "mismatched out_n, decim_m<=0, and NULL input must all return -1");
}

static void test_am_carrier_produces_envelope_peak(void)
{
    epm_dsp_envelope_t env;
    float in[IN_N], out[OUT_N];
    char detail[192];

    epm_dsp_envelope_init(&env, FS_HZ, BAND_LO_HZ, BAND_HI_HZ, ENV_LP_HZ);
    build_carrier(in, IN_N, /*modulated=*/1);

    int rc = epm_dsp_envelope_process(&env, in, IN_N, DECIM_M, out, OUT_N);

    /* bin = MOD_HZ * OUT_N / decimated_fs = 100 * 256 / 3200 = 8 */
    int   bin = (int)(MOD_HZ * OUT_N / (FS_HZ / DECIM_M) + 0.5f);
    int   bin2 = 2 * bin; /* 2nd harmonic, per Part G's "peak + harmonics" */
    float mag  = goertzel_mag(out, OUT_N, bin);
    float mag2 = goertzel_mag(out, OUT_N, bin2);

    snprintf(detail, sizeof(detail),
             "rc=%d bin=%d mag=%.3f (want > 5.0), 2nd-harmonic bin=%d mag2=%.3f (want > 5.0)",
             rc, bin, mag, bin2, mag2);
    test_report("am_carrier_produces_envelope_peak",
                rc == 0 && mag > 5.0f && mag2 > 5.0f, EXPECT_PASS, detail);
}

static void test_plain_tone_produces_no_envelope_peak(void)
{
    epm_dsp_envelope_t env;
    float in[IN_N], out[OUT_N];
    char detail[128];

    epm_dsp_envelope_init(&env, FS_HZ, BAND_LO_HZ, BAND_HI_HZ, ENV_LP_HZ);
    build_carrier(in, IN_N, /*modulated=*/0);

    int rc = epm_dsp_envelope_process(&env, in, IN_N, DECIM_M, out, OUT_N);

    int   bin = (int)(MOD_HZ * OUT_N / (FS_HZ / DECIM_M) + 0.5f);
    float mag = goertzel_mag(out, OUT_N, bin);

    snprintf(detail, sizeof(detail), "rc=%d bin=%d mag=%.3f (want < 1.0)", rc, bin, mag);
    test_report("plain_tone_produces_no_envelope_peak",
                rc == 0 && mag < 1.0f, EXPECT_PASS, detail);
}

static void test_modulated_dwarfs_unmodulated(void)
{
    epm_dsp_envelope_t env_mod, env_plain;
    float in_mod[IN_N], in_plain[IN_N];
    float out_mod[OUT_N], out_plain[OUT_N];
    char detail[128];

    epm_dsp_envelope_init(&env_mod, FS_HZ, BAND_LO_HZ, BAND_HI_HZ, ENV_LP_HZ);
    epm_dsp_envelope_init(&env_plain, FS_HZ, BAND_LO_HZ, BAND_HI_HZ, ENV_LP_HZ);
    build_carrier(in_mod, IN_N, 1);
    build_carrier(in_plain, IN_N, 0);

    epm_dsp_envelope_process(&env_mod, in_mod, IN_N, DECIM_M, out_mod, OUT_N);
    epm_dsp_envelope_process(&env_plain, in_plain, IN_N, DECIM_M, out_plain, OUT_N);

    int bin = (int)(MOD_HZ * OUT_N / (FS_HZ / DECIM_M) + 0.5f);
    float mag_mod   = goertzel_mag(out_mod, OUT_N, bin);
    float mag_plain = goertzel_mag(out_plain, OUT_N, bin);

    snprintf(detail, sizeof(detail), "mag_mod=%.3f mag_plain=%.3f (want mod > 10x plain)",
             mag_mod, mag_plain);
    test_report("modulated_dwarfs_unmodulated", mag_mod > 10.0f * (mag_plain + 0.01f),
                EXPECT_PASS, detail);
}

int main(void)
{
    test_invalid_args_rejected();
    test_am_carrier_produces_envelope_peak();
    test_plain_tone_produces_no_envelope_peak();
    test_modulated_dwarfs_unmodulated();

    return test_summary();
}
