#include <errno.h>

#include <Arduino.h>
#include <driver/i2s.h>

#include "board_pins.h"
#include "hal/hal_audio.h"

/*
 * Implements hal_audio.h for the INMP441 I2S MEMS microphone using the
 * ESP32 legacy driver/i2s.h API - the Arduino/ESP32 port of mcu/src/
 * drivers/audio_i2s.c. Function-for-function analog of that file:
 * i2s_driver_install()/i2s_set_pin() ~ hal_audio_init()'s i2s_configure(),
 * i2s_start() ~ hal_audio_start()'s i2s_trigger(START), i2s_read() ~
 * hal_audio_read_block()'s own i2s_read() call (same function name in
 * both Zephyr's and ESP-IDF's I2S APIs).
 *
 * Unlike mcu/'s STM32U585 path, which is forced to a 16-bit I2S slot by a
 * confirmed DMA-width bug in a vendored STM32 driver (see audio_i2s.c's
 * header comment - a real, hardware-verified STM32-specific finding),
 * the ESP32 I2S peripheral has no equivalent documented constraint, so
 * this reads the INMP441's full-width slot: I2S_BITS_PER_SAMPLE_32BIT,
 * channel_format=ONLY_LEFT (mono - matches the INMP441's L/R=GND pin
 * strap mcu/'s mic wiring also relies on, so the right slot is never
 * even clocked into a sample here, unlike mcu/'s "discard slot 1 in
 * software" approach). Per the INMP441 datasheet, the 24-bit sample sits
 * left-justified (MSB-first) in that 32-bit word with 8 zero-padded LSBs
 * - hal_audio_read_block() below right-shifts by 8 to recover it as a
 * signed 24-bit value in an int32_t, matching common INMP441/ESP32
 * reference-driver convention.
 *
 * Sample rate is 96kHz, same as mcu/'s SAI1_A path and for the identical
 * reason (base-station/sketch/mic_sampler.cpp's header comment): this is
 * the same INMP441, wired the same way (BCLK+WS+SD only, no MCLK pin -
 * the part doesn't have one), so the same datasheet-level constraint
 * applies regardless of host MCU - without an external MCLK the part's
 * own natural rate is Fs/2, and the unavoidable 2x upsampling to the I2S
 * frame rate Fs folds an image in above Fs/4. Sampling at 96kHz keeps
 * that clean band at 0-24kHz; mic_sampler_task.cpp then keeps only the
 * first MIC_FFT_BIN_COUNT of the FFT's unique bins (up to 24kHz) and
 * drops the rest, exactly mirroring mic_sampler.cpp's own
 * MIC_FFT_LEN/bin-keeping split. Not hardware-verified in this port yet
 * (no physical ESP32S3 + INMP441 bring-up prior to this stage) - this is
 * the config to verify against real hardware, not a placeholder to
 * revisit later.
 */

#define AUDIO_SAMPLE_RATE_HZ 96000
#define AUDIO_I2S_PORT        I2S_NUM_0
#define AUDIO_DMA_BUF_COUNT   4
#define AUDIO_DMA_BUF_LEN     256 /* frames per DMA buffer */

int hal_audio_init(void)
{
	i2s_config_t cfg = {};

	cfg.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX);
	cfg.sample_rate = AUDIO_SAMPLE_RATE_HZ;
	cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT;
	cfg.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;
	cfg.communication_format = I2S_COMM_FORMAT_STAND_I2S;
	cfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
	cfg.dma_buf_count = AUDIO_DMA_BUF_COUNT;
	cfg.dma_buf_len = AUDIO_DMA_BUF_LEN;
	cfg.use_apll = false;

	esp_err_t err = i2s_driver_install(AUDIO_I2S_PORT, &cfg, 0, NULL);

	if (err != ESP_OK) {
		Serial.printf("[mic_i2s] i2s_driver_install failed: %d\n", (int)err);
		return -EIO;
	}

	i2s_pin_config_t pins = {};

	pins.mck_io_num = I2S_PIN_NO_CHANGE;
	pins.bck_io_num = PIN_MIC_I2S_BCLK;
	pins.ws_io_num = PIN_MIC_I2S_WS;
	pins.data_out_num = I2S_PIN_NO_CHANGE;
	pins.data_in_num = PIN_MIC_I2S_SD;

	err = i2s_set_pin(AUDIO_I2S_PORT, &pins);
	if (err != ESP_OK) {
		Serial.printf("[mic_i2s] i2s_set_pin failed: %d\n", (int)err);
		return -EIO;
	}

	/* Driver starts running immediately after install - stop it here so
	 * hal_audio_start() (called separately, mirroring mcu/'s configure-
	 * then-start split) is the one true "go" signal. */
	i2s_stop(AUDIO_I2S_PORT);

	return 0;
}

int hal_audio_start(void)
{
	esp_err_t err = i2s_start(AUDIO_I2S_PORT);

	if (err != ESP_OK) {
		Serial.printf("[mic_i2s] i2s_start failed: %d\n", (int)err);
		return -EIO;
	}

	return 0;
}

int hal_audio_read_block(int32_t *out_samples, size_t max_samples)
{
	static int32_t raw[AUDIO_BLOCK_SAMPLES];
	size_t want_bytes = min(max_samples, (size_t)AUDIO_BLOCK_SAMPLES) * sizeof(int32_t);
	size_t bytes_read = 0;

	esp_err_t err =
		i2s_read(AUDIO_I2S_PORT, raw, want_bytes, &bytes_read, pdMS_TO_TICKS(1000));

	if (err != ESP_OK) {
		Serial.printf("[mic_i2s] i2s_read failed: %d\n", (int)err);
		return -EIO;
	}

	size_t n = bytes_read / sizeof(int32_t);

	for (size_t i = 0; i < n; i++) {
		out_samples[i] = raw[i] >> 8;
	}

	return (int)n;
}

uint32_t hal_audio_get_sample_rate(void)
{
	return AUDIO_SAMPLE_RATE_HZ;
}

void hal_audio_stop(void)
{
	i2s_stop(AUDIO_I2S_PORT);
}
