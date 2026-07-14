#ifndef ACCEL_SAMPLER_H_
#define ACCEL_SAMPLER_H_

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
 * from setup(), BEFORE mic_sampler_start() - mic's priority-7 never-yielding
 * capture thread starves this (lower-priority) setup() thread the instant it
 * starts, so anything sequenced after mic_sampler_start() in setup() never
 * runs. See docs/PROGRESS.md's accel_sampler_thread entry for the full story. */
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

#endif /* ACCEL_SAMPLER_H_ */
