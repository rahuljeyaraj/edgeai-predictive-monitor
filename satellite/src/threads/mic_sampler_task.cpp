#include <errno.h>
#include <string.h>

#include <arduinoFFT.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/task.h>

#include "app_config.h"
#include "hal/hal_audio.h"
#include "threads/mic_sampler_task.h"

/*
 * Continuous I2S capture + FFT task - the Arduino/FreeRTOS port of
 * mcu/src/threads/mic_sampler_thread.c. Single channel, no axis-summing
 * (that's accel-specific - see accel_sampler_task.cpp).
 */

#define MIC_SAMPLER_TASK_STACK_WORDS 6144
#define MIC_SAMPLER_TASK_PRIORITY    5
#define MIC_SAMPLER_MAX_RECOVERY_ATTEMPTS 5

#define MIC_FFT_LEN (MIC_FFT_BIN_COUNT * 2)

static_assert(MIC_FFT_LEN == AUDIO_BLOCK_SAMPLES,
	      "MIC_FFT_LEN must equal AUDIO_BLOCK_SAMPLES (hal/hal_audio.h) - the FFT window "
	      "is the full block, no partial/leftover samples.");

QueueHandle_t mic_spectrum_queue;

static float fft_real[MIC_FFT_LEN];
static float fft_imag[MIC_FFT_LEN];
static ArduinoFFT<float> mic_fft;

/* See accel_sampler_task.cpp's accel_fft_magnitude() comment - identical
 * reasoning (Hann windowing to match mpu/tools/satellite_node_sim.py's
 * compute_spectrum(), arrays bound once not per-call). */
static void mic_fft_magnitude(const float *window, float *out_mag)
{
	memcpy(fft_real, window, MIC_FFT_LEN * sizeof(float));
	memset(fft_imag, 0, MIC_FFT_LEN * sizeof(float));

	mic_fft.windowing(FFTWindow::Hann, FFTDirection::Forward);
	mic_fft.compute(FFTDirection::Forward);
	mic_fft.complexToMagnitude();

	memcpy(out_mag, &fft_real[1], MIC_FFT_BIN_COUNT * sizeof(float));
}

static void mic_sampler_task_entry(void *arg)
{
	(void)arg;

	static int32_t block[AUDIO_BLOCK_SAMPLES];
	static float mic_window[MIC_FFT_LEN];
	static float mic_mag[MIC_FFT_BIN_COUNT];
	uint32_t consecutive_failures = 0;

	while (1) {
		if (consecutive_failures >= MIC_SAMPLER_MAX_RECOVERY_ATTEMPTS) {
			vTaskDelay(pdMS_TO_TICKS(1000));
			continue;
		}

		int n = hal_audio_read_block(block, AUDIO_BLOCK_SAMPLES);

		if (n < 0) {
			hal_audio_stop();
			hal_audio_start();
			consecutive_failures++;
			vTaskDelay(pdMS_TO_TICKS(100));
			continue;
		}

		consecutive_failures = 0;

		for (size_t i = 0; i < (size_t)n; i++) {
			mic_window[i] = (float)block[i];
		}

		mic_fft_magnitude(mic_window, mic_mag);
		xQueueOverwrite(mic_spectrum_queue, mic_mag);
	}
}

int mic_sampler_task_start(void)
{
	if (!MIC_SENSOR_ENABLED) {
		Serial.println("[mic_sampler] mic sensor disabled (MIC_SENSOR_ENABLED == 0)");
		return 0;
	}

	mic_spectrum_queue = xQueueCreate(1, MIC_FFT_BIN_COUNT * sizeof(float));
	if (mic_spectrum_queue == NULL) {
		return -ENOMEM;
	}

	mic_fft.setArrays(fft_real, fft_imag, MIC_FFT_LEN);

	int ret = hal_audio_init();

	if (ret < 0) {
		return ret;
	}

	ret = hal_audio_start();
	if (ret < 0) {
		return ret;
	}

	TaskHandle_t handle = NULL;
	BaseType_t ok =
		xTaskCreate(mic_sampler_task_entry, "mic_sampler", MIC_SAMPLER_TASK_STACK_WORDS,
			    NULL, MIC_SAMPLER_TASK_PRIORITY, &handle);

	return ok == pdPASS ? 0 : -ENOMEM;
}
