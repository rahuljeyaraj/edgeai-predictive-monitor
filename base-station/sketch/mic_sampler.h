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

#endif /* MIC_SAMPLER_H_ */
