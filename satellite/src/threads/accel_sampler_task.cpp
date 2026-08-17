#include <errno.h>
#include <string.h>

#include <arduinoFFT.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/task.h>

#include "app_config.h"
#include "dsp/scalar_stats.h"
#include "hal/hal_accel.h"
#include "threads/accel_sampler_task.h"

/*
 * Continuous SPI capture + FFT task - the Arduino/FreeRTOS port of
 * mcu/src/threads/accel_sampler_thread.c. Same structural shape: read the
 * KX134's hardware FIFO in chunks (it's much smaller than one FFT window,
 * so multiple reads accumulate one window), FFT each axis independently.
 *
 * Each axis is published on its own (accel_x/y/z, registry.SensorChannel
 * members) rather than summed into one combined magnitude spectrum -
 * base-station/sketch/fuser.cpp's own migration away from a combined
 * `accel` channel found that summing x/y/z erases the directional
 * signature an imbalance fault produces (+1.8 sigma combined vs. +38.5
 * sigma per-axis on real captures, tools/offline_experiment.py). This
 * satellite firmware had drifted behind that change (last synced when the
 * base station still summed axes) - per-axis is now the only shape the
 * base station's ingestion (pipeline/features.py's build_feature_vector())
 * accepts. Each axis's own raw window also feeds compute_scalars()
 * (dsp/scalar_stats.h) for the model's scalar tail - fuser_task.cpp reads
 * both the spectrum and the scalars out of the queue below.
 */

#define ACCEL_SAMPLER_TASK_STACK_WORDS 6144
#define ACCEL_SAMPLER_TASK_PRIORITY    4

/* Frames requested per hal_accel_read_block() call. Unlike mcu/'s
 * ACCEL_SAMPLER_READ_CHUNK_FRAMES (64 - empirically tuned on real STM32
 * hardware, see that constant's own long comment), this value is *not*
 * hardware-tuned - there's no physical XIAO ESP32S3 + KX134 on the bench
 * in this port to run the same characterization against. 64 is carried
 * over as a reasonable starting point (divides ACCEL_FFT_LEN evenly,
 * fits under the 86-frame hardware FIFO cap) - revisit empirically once
 * real hardware is available, exactly as mcu/'s own comment describes
 * doing for its platform. */
#define ACCEL_SAMPLER_READ_CHUNK_FRAMES 64

#define ACCEL_SAMPLER_MAX_RECOVERY_ATTEMPTS 5

QueueHandle_t accel_spectrum_queue;

static float fft_real[ACCEL_FFT_LEN];
static float fft_imag[ACCEL_FFT_LEN];
static ArduinoFFT<float> accel_fft;

/* Computes the magnitude spectrum of one axis's window into out_mag
 * (ACCEL_FFT_BIN_COUNT values, bins 1..ACCEL_FFT_BIN_COUNT - DC
 * discarded, same convention as mcu/'s accel_fft_magnitude()). No
 * windowing (rectangular) - matches mcu/'s UART path and base-station/
 * python/common/raw_features.py's fft_magnitude() (np.fft.rfft() with no
 * window), the shared reference the simulator and offline training
 * harness both use. An earlier revision windowed with Hann to match a
 * since-reverted version of the sim that used np.hanning() (see git
 * history around 4e3f285) - that sim code no longer windows either, so
 * this doesn't. A window tapers samples near the block edges, so a short
 * transient's reported magnitude ends up depending on where in the block
 * it happened to land rather than tracking actual amplitude; rectangular
 * avoids that. accel_fft's array pointers are bound once
 * (accel_sampler_task_start()) and reused for every call. `window` is
 * left untouched (fft_real is a scratch copy), since compute_scalars()
 * below still needs the original raw window after this returns. */
static void accel_fft_magnitude(const float *window, float *out_mag)
{
	memcpy(fft_real, window, ACCEL_FFT_LEN * sizeof(float));
	memset(fft_imag, 0, ACCEL_FFT_LEN * sizeof(float));

	accel_fft.compute(FFTDirection::Forward);
	accel_fft.complexToMagnitude();

	memcpy(out_mag, &fft_real[1], ACCEL_FFT_BIN_COUNT * sizeof(float));
}

static void accel_axis_process(const float *window, struct accel_axis_result *out)
{
	accel_fft_magnitude(window, out->mag);
	compute_scalars(window, ACCEL_FFT_LEN, &out->rms, &out->kurtosis, &out->std, &out->peak,
			&out->crest_factor, &out->skewness);
}

static void accel_sampler_task_entry(void *arg)
{
	(void)arg;

	static int32_t block[ACCEL_SAMPLER_READ_CHUNK_FRAMES * 3];
	static float accel_window_x[ACCEL_FFT_LEN];
	static float accel_window_y[ACCEL_FFT_LEN];
	static float accel_window_z[ACCEL_FFT_LEN];
	static struct accel_sample sample;
	size_t frames_accumulated = 0;
	uint32_t consecutive_failures = 0;

	while (1) {
		if (consecutive_failures >= ACCEL_SAMPLER_MAX_RECOVERY_ATTEMPTS) {
			vTaskDelay(pdMS_TO_TICKS(1000));
			continue;
		}

		int n = hal_accel_read_block(block, ACCEL_SAMPLER_READ_CHUNK_FRAMES);

		if (n < 0) {
			hal_accel_stop();
			hal_accel_start();
			consecutive_failures++;
			vTaskDelay(pdMS_TO_TICKS(100));
			continue;
		}

		consecutive_failures = 0;

		for (int i = 0; i < n && frames_accumulated < ACCEL_FFT_LEN; i++) {
			accel_window_x[frames_accumulated] = (float)block[i * 3 + 0];
			accel_window_y[frames_accumulated] = (float)block[i * 3 + 1];
			accel_window_z[frames_accumulated] = (float)block[i * 3 + 2];
			frames_accumulated++;
		}

		if (frames_accumulated < ACCEL_FFT_LEN) {
			continue;
		}

		accel_axis_process(accel_window_x, &sample.x);
		accel_axis_process(accel_window_y, &sample.y);
		accel_axis_process(accel_window_z, &sample.z);

		xQueueOverwrite(accel_spectrum_queue, &sample);

		frames_accumulated = 0;
	}
}

int accel_sampler_task_start(void)
{
	if (!ACCEL_SENSOR_ENABLED) {
		Serial.println("[accel_sampler] accel sensor disabled (ACCEL_SENSOR_ENABLED == 0)");
		return 0;
	}

	accel_spectrum_queue = xQueueCreate(1, sizeof(struct accel_sample));
	if (accel_spectrum_queue == NULL) {
		return -ENOMEM;
	}

	accel_fft.setArrays(fft_real, fft_imag, ACCEL_FFT_LEN);

	int ret = hal_accel_init();

	if (ret < 0) {
		return ret;
	}

	ret = hal_accel_start();
	if (ret < 0) {
		return ret;
	}

	TaskHandle_t handle = NULL;
	BaseType_t ok =
		xTaskCreate(accel_sampler_task_entry, "accel_sampler", ACCEL_SAMPLER_TASK_STACK_WORDS,
			    NULL, ACCEL_SAMPLER_TASK_PRIORITY, &handle);

	return ok == pdPASS ? 0 : -ENOMEM;
}
