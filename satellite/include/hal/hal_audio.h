#ifndef HAL_AUDIO_H_
#define HAL_AUDIO_H_

#include <stddef.h>
#include <stdint.h>

/*
 * Microphone contract - mirrors mcu/src/hal/hal_audio.h's role (INMP441
 * I2S MEMS mic, mono/left-channel capture), ported to the ESP32's I2S
 * peripheral via the driver/i2s.h API arduino-esp32 exposes.
 * src/drivers/mic_i2s.cpp is the implementation.
 *
 * Unlike mcu/'s STM32U585 path (which is forced to word_size=16 by a
 * vendored DMA driver bug - see mcu/src/drivers/audio_i2s.c's header
 * comment - not a mic limitation), the ESP32 I2S peripheral has no such
 * constraint: this reads the INMP441's full 24-bit output (left-justified
 * in a 32-bit I2S slot per its datasheet), so samples handed back here
 * carry more precision than mcu/'s equivalent, not less.
 */

/* Mono samples per hal_audio_read_block() call - mic_i2s.cpp's internal
 * block size. Must equal MIC_FFT_BIN_COUNT * 4 (app_config.h) so one
 * block is exactly one FFT window - see mic_sampler_task.cpp's
 * static_assert, and mic_sampler_task.h's MIC_FFT_LEN comment for why
 * the multiplier is 4 and not 2 (mirrors base-station/sketch/
 * mic_sampler.cpp's MIC_FFT_LEN exactly - same INMP441, same no-MCLK
 * fold-back reasoning). */
#define AUDIO_BLOCK_SAMPLES 2048

int hal_audio_init(void);
int hal_audio_start(void);

/* Blocks until one full block of samples is available, then copies up to
 * max_samples into out_samples (mono). Returns the number of samples
 * written, or a negative errno. */
int hal_audio_read_block(int32_t *out_samples, size_t max_samples);

uint32_t hal_audio_get_sample_rate(void);
void hal_audio_stop(void);

#endif /* HAL_AUDIO_H_ */
