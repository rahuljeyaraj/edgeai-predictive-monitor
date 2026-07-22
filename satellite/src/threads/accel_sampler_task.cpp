#include <errno.h>
#include <string.h>

#include <arduinoFFT.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/task.h>

#include "app_config.h"
#include "hal/hal_accel.h"
#include "threads/accel_sampler_task.h"

/*
 * Continuous SPI capture + FFT task - the Arduino/FreeRTOS port of
 * mcu/src/threads/accel_sampler_thread.c. Same structural shape: read the
 * KX134's hardware FIFO in chunks (it's much smaller than one FFT window,
 * so multiple reads accumulate one window), FFT each axis independently,
 * sum bin-by-bin into one combined magnitude spectrum.
 *
 * Three axes, summed (not max-picked, not single-axis): carried over
 * unchanged from mcu/'s accel_sampler_thread.c - see that file's header
 * comment for the vibration-directionality/fault-signature reasoning
 * (a signal-processing decision, not MCU-specific, so it applies here
 * identically). Do not "simplify" this back to single-axis without
 * re-confirming that decision.
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

#define ACCEL_FFT_LEN (ACCEL_FFT_BIN_COUNT * 2)

QueueHandle_t accel_spectrum_queue;

static float fft_real[ACCEL_FFT_LEN];
static float fft_imag[ACCEL_FFT_LEN];
static ArduinoFFT<float> accel_fft;

/* Computes the magnitude spectrum of one axis's window into out_mag
 * (ACCEL_FFT_BIN_COUNT values, bins 1..ACCEL_FFT_BIN_COUNT - DC
 * discarded, same convention as mcu/'s accel_fft_magnitude()). Hann
 * windowing applied before the transform - matches mpu/tools/
 * satellite_node_sim.py's compute_spectrum() (np.hanning()), unlike
 * mcu/'s UART path (which windows nothing) - windowing measurably
 * reduces spectral leakage/smearing between adjacent bins, which still
 * matters even though every bin now goes out on the wire (frame_codec/
 * spectrum_codec.h), not just a selected top-N. accel_fft's array
 * pointers are bound once (accel_sampler_task_start()) and reused for
 * every call - calling setArrays() per-call would re-allocate its
 * internal windowing-factor scratch buffer on every FFT, needless heap
 * churn on a long-running embedded task. */
static void accel_fft_magnitude(const float *window, float *out_mag)
{
	memcpy(fft_real, window, ACCEL_FFT_LEN * sizeof(float));
	memset(fft_imag, 0, ACCEL_FFT_LEN * sizeof(float));

	accel_fft.windowing(FFTWindow::Hann, FFTDirection::Forward);
	accel_fft.compute(FFTDirection::Forward);
	accel_fft.complexToMagnitude();

	memcpy(out_mag, &fft_real[1], ACCEL_FFT_BIN_COUNT * sizeof(float));
}

static void accel_sampler_task_entry(void *arg)
{
	(void)arg;

	static int32_t block[ACCEL_SAMPLER_READ_CHUNK_FRAMES * 3];
	static float accel_window_x[ACCEL_FFT_LEN];
	static float accel_window_y[ACCEL_FFT_LEN];
	static float accel_window_z[ACCEL_FFT_LEN];
	static float accel_mag_x[ACCEL_FFT_BIN_COUNT];
	static float accel_mag_y[ACCEL_FFT_BIN_COUNT];
	static float accel_mag_z[ACCEL_FFT_BIN_COUNT];
	static float accel_mag_combined[ACCEL_FFT_BIN_COUNT];
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

		accel_fft_magnitude(accel_window_x, accel_mag_x);
		accel_fft_magnitude(accel_window_y, accel_mag_y);
		accel_fft_magnitude(accel_window_z, accel_mag_z);

		for (size_t i = 0; i < ACCEL_FFT_BIN_COUNT; i++) {
			accel_mag_combined[i] = accel_mag_x[i] + accel_mag_y[i] + accel_mag_z[i];
		}

		xQueueOverwrite(accel_spectrum_queue, accel_mag_combined);

		frames_accumulated = 0;
	}
}

int accel_sampler_task_start(void)
{
	if (!ACCEL_SENSOR_ENABLED) {
		Serial.println("[accel_sampler] accel sensor disabled (ACCEL_SENSOR_ENABLED == 0)");
		return 0;
	}

	accel_spectrum_queue = xQueueCreate(1, ACCEL_FFT_BIN_COUNT * sizeof(float));
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
