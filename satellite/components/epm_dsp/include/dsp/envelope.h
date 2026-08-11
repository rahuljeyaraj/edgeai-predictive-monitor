/*
 * envelope.h — Pure-math envelope-analysis (HFRT) pipeline for bearing-defect
 * detection (Part G Phase 11, ADR-032).
 *
 * Standard high-frequency resonance technique: band-pass around a structural
 * resonance -> full-wave rectify -> low-pass (extracts the amplitude
 * envelope) -> decimate. The caller windows + FFTs the decimated output the
 * same way it already does for the raw per-axis spectra (dsps_fft2r_fc32 is
 * ESP-DSP SIMD and stays in the task file, matching spectrum.h's existing
 * split) — bearing-defect frequencies (BPFO/BPFI/BSF/FTF) show up as a clear
 * fundamental + harmonics in that envelope spectrum, buried in the raw one.
 *
 * Filters are 2nd-order Butterworth biquads (RBJ cookbook, Q = 1/sqrt(2),
 * maximally flat passband) computed at init time from fs/fc — pure libm
 * (cosf/sinf), zero ESP-IDF includes, host-testable like the rest of
 * epm_dsp.
 */

#pragma once

#ifdef __cplusplus
extern "C" {
#endif

/** Direct Form II Transposed biquad: state + normalized (a0-divided) coeffs. */
typedef struct {
    float b0, b1, b2, a1, a2;
    float z1, z2;
} epm_dsp_biquad_t;

/** Second-order Butterworth low-pass, cutoff fc_hz at sample rate fs_hz. */
void epm_dsp_biquad_init_lowpass(epm_dsp_biquad_t *bq, float fs_hz, float fc_hz, float q);

/** Second-order Butterworth high-pass, cutoff fc_hz at sample rate fs_hz. */
void epm_dsp_biquad_init_highpass(epm_dsp_biquad_t *bq, float fs_hz, float fc_hz, float q);

/** Processes one sample through the filter, updating its internal state. */
float epm_dsp_biquad_process(epm_dsp_biquad_t *bq, float x);

/** Zeroes filter state (e.g. between unrelated signal blocks in a test). */
void epm_dsp_biquad_reset(epm_dsp_biquad_t *bq);

/**
 * Three cascaded biquads implementing the envelope pipeline's filter stage:
 * high-pass at the resonance band's lower edge, low-pass at its upper edge
 * (the two together form the band-pass), and a third low-pass, applied after
 * rectification, that extracts the envelope itself.
 */
typedef struct {
    epm_dsp_biquad_t band_hp;   /* resonance band lower edge */
    epm_dsp_biquad_t band_lp;   /* resonance band upper edge */
    epm_dsp_biquad_t env_lp;    /* post-rectify envelope low-pass */
} epm_dsp_envelope_t;

/**
 * Initializes the three-biquad cascade. band_lo_hz/band_hi_hz bound the
 * structural-resonance pass band (Q = 1/sqrt(2) on both edges); env_lp_hz is
 * the post-rectify envelope low-pass cutoff. All three run at fs_hz.
 */
void epm_dsp_envelope_init(epm_dsp_envelope_t *env, float fs_hz,
                            float band_lo_hz, float band_hi_hz, float env_lp_hz);

/**
 * Runs in[0..in_n) through band-pass -> full-wave rectify -> envelope
 * low-pass -> decimate-by-decim_m, writing the decimated output to
 * out[0..out_n). in_n must equal out_n * decim_m exactly (caller sizes out_n
 * itself, same contract style as epm_dsp_reduce_bins()). Filter state
 * persists across calls, matching imu_task.c's per-epoch streaming use.
 *
 * Returns 0 on success, -1 if any pointer is NULL, in_n/out_n/decim_m <= 0,
 * or in_n != out_n * decim_m (out is left untouched on failure).
 */
int epm_dsp_envelope_process(epm_dsp_envelope_t *env, const float *in, int in_n,
                              int decim_m, float *out, int out_n);

#ifdef __cplusplus
}
#endif
