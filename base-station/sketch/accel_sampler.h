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
 * from setup(). */
void accel_sampler_start(void);

#endif /* ACCEL_SAMPLER_H_ */
