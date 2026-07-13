#ifndef MIC_SAMPLER_H_
#define MIC_SAMPLER_H_

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

#endif /* MIC_SAMPLER_H_ */
