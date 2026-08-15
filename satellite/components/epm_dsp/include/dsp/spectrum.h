/*
 * spectrum.h — Pure-math Welch/power-accumulation helpers (ADR-013).
 *
 * FFT execution itself (dsps_fft2r_fc32) and the spectral-centroid dot
 * products (dsps_dotprod_f32) are ESP-DSP SIMD and stay in the task file;
 * this covers only the surrounding scalar C: hop sizing and the
 * accumulate/dB-convert loops.
 */

#pragma once

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Welch hop size in samples: how far the window advances between successive
 * FFTs given overlap_pct. Floored at 1 so an out-of-range overlap_pct can
 * never stall a drain loop.
 */
int epm_dsp_welch_hop_size(int fft_n, int overlap_pct);

/**
 * Accumulates linear power from one interleaved-complex FFT output
 * (re,im,re,im,...) into pwr_acc[0..half_n), applying norm_factor to each
 * component first. Adds to whatever is already in pwr_acc (caller clears it
 * via epm_dsp_power_to_db()).
 */
void epm_dsp_accumulate_power(const float *fft_interleaved, float *pwr_acc,
                               int half_n, float norm_factor);

/**
 * Converts an averaged linear-power accumulator to dBFS: mag_db_out[i] =
 * 10*log10(pwr_acc[i]*inv_n + 1e-12), then zeroes pwr_acc for the next
 * averaging cycle and overrides bin 0 (DC) to -120 dBFS.
 */
void epm_dsp_power_to_db(float *pwr_acc, float *mag_db_out, int half_n, float inv_n);

/**
 * Reduces a dB-scale spectrum from in_n bins to out_n bins (ADR-020):
 * each output bin's source band of in_n/out_n input bins is converted back
 * to linear power, averaged, and reconverted to dB with the same epsilon
 * convention as epm_dsp_power_to_db() (10*log10(avg_pwr + 1e-12)) -- power-
 * domain averaging, not a dB-domain mean, to stay energy-correct. No
 * special-casing of bin 0: the caller's upstream epm_dsp_power_to_db() has
 * already forced it to -120 dBFS before this function ever sees it, so it
 * reduces like any other bin.
 *
 * in_n must be an exact multiple of out_n. Returns 0 on success, -1 if
 * in_n or out_n is <= 0, out_n > in_n, or in_n % out_n != 0 (out_db is left
 * untouched on failure).
 */
int epm_dsp_reduce_bins(const float *in_db, int in_n, float *out_db, int out_n);

#ifdef __cplusplus
}
#endif
