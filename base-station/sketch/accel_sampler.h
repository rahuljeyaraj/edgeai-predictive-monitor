#ifndef ACCEL_SAMPLER_H_
#define ACCEL_SAMPLER_H_

#include <stdint.h>

#include "app_config.h"

/*
 * KX134-1211 SPI accelerometer sampler - see accel_sampler.cpp's header comment for
 * the full port rationale. Mirrors the old repo's threads/accel_sampler_thread.h +
 * hal/hal_accel.h contract: one entry point that brings up the hardware and starts
 * the capture/FFT thread, plus a Bridge RPC surface for the MPU side to pull the
 * latest 3-axis-summed spectrum.
 */

/* Initializes the KX134 over SPI, registers the Bridge providers
 * ("get_accel_spectrum", "get_accel_info"), and starts the capture/FFT thread
 * (priority ACCEL_SAMPLER_THREAD_PRIORITY, see accel_sampler.cpp). Call once
 * from setup(). No ordering constraint relative to mic_sampler_start() -
 * mic's capture thread used to never yield and would starve setup() if
 * anything was sequenced after it, but that was fixed by moving mic capture
 * onto GPDMA1 (see mic_sampler.cpp and docs/PROGRESS.md's 2026-07-14 entry). */
void accel_sampler_start(void);

/* Full-resolution spectrum access for the fuser (fuser.cpp): the latest
 * accel_full_bin_count() float32 magnitudes (3-axis-summed, all unique bins),
 * plus self-describing metadata. accel_copy_full_spectrum() is mutex-guarded
 * and safe to call from another thread; it copies exactly
 * accel_full_bin_count() floats into out[]. These are the un-downsampled bins,
 * distinct from the 32-bucket "get_accel_spectrum" Bridge view. */
int accel_full_bin_count(void);
int accel_fft_size(void);
float accel_sample_rate_hz(void);
void accel_copy_full_spectrum(float *out);

/* Per-axis (not summed) full-resolution spectra, same bin count/fs/fft_size
 * as accel_copy_full_spectrum() since it's the same FFT just not summed
 * across axes -- feeds the per-axis SPECTRUM sections (fuser.cpp normal
 * mode) that let the dashboard overlay 1-3 accel axes on one chart
 * (docs/CHART_CLUTTER_PLAN.md S1). Mutex-guarded, same latest-window
 * handoff as accel_copy_full_spectrum(). */
void accel_copy_axis_spectra(float *out_x, float *out_y, float *out_z);

/* Raw, un-FFT'd per-axis window access. Originally fuser.cpp's raw-capture-
 * mode-only accessor; now also used unconditionally by normal mode to
 * compute the accel-derived scalar tiles (rms/kurtosis/...) and the
 * decimated time-domain sections (docs/CHART_CLUTTER_PLAN.md S1) - so this
 * is no longer gated behind FUSER_RAW_CAPTURE_MODE. accel_copy_raw_window()
 * copies accel_fft_size() float32 samples into each of out_x/y/z - same
 * mutex-guarded latest-window handoff as accel_copy_full_spectrum(). */
void accel_copy_raw_window(float *out_x, float *out_y, float *out_z);

#if BENCHMARK_STATS_ENABLED
/* Cumulative-since-boot pipeline-stage counters, read by fuser.cpp's
 * periodic "get_bench_stats" Bridge report - ported from the old repo's
 * docs/Sensor_Throughput_Tuning_Plan.md Phase 0 (isr/read/fifo_full/timeout
 * are the same underlying counters accel_get_info()'s string already
 * exposes; windows_completed is new). Compiled out entirely when
 * BENCHMARK_STATS_ENABLED is 0 (app_config.h). */
struct accel_bench_stats {
  uint32_t windows_completed; /* 3-axis FFT windows produced */
  uint32_t isr_count;         /* INT1/BFI pulses */
  uint32_t read_count;        /* accel_read_block() calls */
  uint32_t timeout_count;     /* accel_data_ready_sem timeouts */
  uint32_t fifo_full_count;   /* reads where the HW FIFO was already at its
                                * 86-frame cap */
};

/* Snapshot of the counters above - same no-locking rationale as
 * mic_sampler_get_stats() (mic_sampler.h). */
void accel_sampler_get_stats(struct accel_bench_stats *out);
#endif /* BENCHMARK_STATS_ENABLED */

#endif /* ACCEL_SAMPLER_H_ */
