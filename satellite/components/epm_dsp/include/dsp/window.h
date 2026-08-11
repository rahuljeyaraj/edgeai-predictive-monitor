/*
 * window.h — Pure-math window helpers (zero ESP-IDF includes, host-testable).
 *
 * The window generation itself (dsps_wind_hann_f32) is ESP-DSP SIMD and stays
 * in the task file; only the coherent-gain reduction over the resulting taps
 * is pure C and lives here.
 */

#pragma once

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Coherent gain = mean of the window taps (see ADR-012). Used to normalise
 * windowed spectra so a full-scale sine still reads 0 dBFS after windowing.
 */
float epm_dsp_coherent_gain(const float *window, int n);

#ifdef __cplusplus
}
#endif
