#ifndef MIC_SAMPLER_H_
#define MIC_SAMPLER_H_

#include <stdint.h>

#include "app_config.h"

/*
 * INMP441 I2S microphone sampler - see mic_sampler.cpp's header comment for
 * the full port rationale. Mirrors the old repo's threads/mic_sampler_thread.h
 * contract: one entry point that brings up the hardware and starts the
 * capture/FFT thread, plus a Bridge RPC surface for the MPU side to pull the
 * latest spectrum.
 */

/* Initializes SAI1_A for I2S RX, registers the Bridge providers
 * ("get_mic_spectrum", "get_mic_info"), and starts the capture/FFT thread
 * (priority MIC_SAMPLER_THREAD_PRIORITY, see mic_sampler.cpp). Call once
 * from setup(). */
void mic_sampler_start(void);

/* Full-resolution spectrum access for the fuser (fuser.cpp): the latest
 * mic_full_bin_count() float32 magnitudes (the useful <Fs/4 half of the FFT),
 * plus self-describing metadata. mic_copy_full_spectrum() is mutex-guarded and
 * safe to call from another thread; it copies exactly mic_full_bin_count()
 * floats into out[]. These are the un-downsampled bins, distinct from the
 * 32-bucket "get_mic_spectrum" Bridge view. */
int mic_full_bin_count(void);
int mic_fft_size(void);
float mic_sample_rate_hz(void);
void mic_copy_full_spectrum(float *out);

#if FUSER_RAW_CAPTURE_MODE
/* Raw, un-FFT'd window access for fuser.cpp's raw-capture mode (see
 * app_config.h's FUSER_RAW_CAPTURE_MODE). mic_copy_raw_window() copies
 * mic_fft_size() float32 samples into out[] - same mutex-guarded
 * latest-window handoff as mic_copy_full_spectrum(). */
void mic_copy_raw_window(float *out);
#endif

#if BENCHMARK_STATS_ENABLED
/* Cumulative-since-boot capture-stage counters (docs/Sensor_Throughput_
 * Tuning_Plan.md Phase 0 in the old repo, ported here), read by fuser.cpp's
 * periodic "get_bench_stats" Bridge report. Compiled out entirely when
 * BENCHMARK_STATS_ENABLED is 0 (app_config.h). */
struct mic_bench_stats {
  uint32_t windows_completed; /* FFT windows produced (mic_dma_capture_block()
                                * succeeded + FFT ran) */
  uint32_t timeouts;          /* DMA blocks that didn't complete in time -
                                * same counter get_mic_info's timeouts= field
                                * reports */
};

/* Snapshot of the counters above. No locking: each field is a single
 * uint32_t (word-atomic read/write on Cortex-M), same "approximate rate/
 * logging data, not correctness-critical" reasoning as the old repo's
 * hal_accel_get_stats(). */
void mic_sampler_get_stats(struct mic_bench_stats *out);
#endif /* BENCHMARK_STATS_ENABLED */

#endif /* MIC_SAMPLER_H_ */
