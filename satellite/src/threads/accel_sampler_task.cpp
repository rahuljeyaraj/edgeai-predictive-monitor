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
/* Above mic/fuser (5), unlike mcu/ where this was 4. On core 1 those are
 * the only tasks that can pre-empt this one, and this is the one with the
 * hard deadline: the KX134 FIFO gives 6.7ms before it discards samples,
 * whereas the mic's I2S DMA ring (4 x 256 frames at 96kHz, mic_i2s.cpp)
 * absorbs ~10.7ms, and the fuser just pools whatever is in its queues. Of
 * the three, accel is the only one where being late destroys data rather
 * than merely delaying it. */
#define ACCEL_SAMPLER_TASK_PRIORITY    6

/* Frames requested per hal_accel_read_block() call: the FIFO's full
 * depth, i.e. drain it completely every time.
 *
 * This WAS 64, carried over from mcu/'s constant of the same name without
 * the characterization that justified it there ("revisit empirically once
 * real hardware is available" - now done, on node e36428). Taking 64 of
 * 86 leaves 22 frames behind, so the next Buffer Full interrupt arrives
 * after only 64/12800 = 5.0ms, and the task has to complete a whole
 * SPI read plus its share of the FFT inside that. Measured: the FIFO was
 * at its cap on 89.3% of reads with a mean inter-read gap of 5126us -
 * i.e. the average read was already past the point where BM_STREAM had
 * begun discarding the oldest samples. Each lost run splices the FFT
 * window, and with ACCEL_FFT_LEN/86 reads per window that reached most
 * windows, raising and destabilizing the whole broadband noise floor.
 *
 * Draining all 86 both empties the FIFO and stretches the budget to
 * 86/12800 = 6.7ms. 86 does not divide ACCEL_FFT_LEN evenly, so the read
 * that completes a window leaves a few frames unused (the loop below
 * stops at ACCEL_FFT_LEN); that costs nothing, because consecutive
 * windows were never contiguous anyway - fuser_task publishes one window
 * per FUSER_EPOCH_MS and drops the rest. What matters is that samples
 * WITHIN a window are contiguous, and that is exactly what this
 * restores. */
#define ACCEL_SAMPLER_READ_CHUNK_FRAMES HAL_ACCEL_FIFO_MAX_FRAMES

#define ACCEL_SAMPLER_MAX_RECOVERY_ATTEMPTS 5

/* How often to print the FIFO-drain stats line. One window is
 * ACCEL_FFT_LEN/ODR = 80ms, so 50 windows is roughly every 4s - frequent
 * enough to watch a trend during a bench run, sparse enough not to become
 * its own source of scheduling jitter on the very task it measures. */
#define ACCEL_SAMPLER_STATS_EVERY_WINDOWS 50

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
	uint32_t windows_since_stats = 0;

	while (1) {
		if (consecutive_failures >= ACCEL_SAMPLER_MAX_RECOVERY_ATTEMPTS) {
			/* Back off, then give it another run at the recovery attempts
			 * instead of parking here forever - a transient stretch of SPI/
			 * FIFO timeouts (e.g. WiFi-induced scheduling jitter) must not
			 * permanently strand the accel channel while mic keeps
			 * publishing, which is what happened before this reset: once
			 * this branch was entered, hal_accel_read_block() was never
			 * called again, so accel_spectrum_queue held one stale sample
			 * forever with no further log output to explain why. */
			vTaskDelay(pdMS_TO_TICKS(1000));
			consecutive_failures = 0;
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

		/* FIFO-drain health, every ACCEL_SAMPLER_STATS_EVERY_WINDOWS
		 * windows. Silent sample loss (hal_accel.h's struct
		 * hal_accel_stats) shows up in the spectrum as a raised, jittery
		 * broadband floor rather than as any error, so it needs its own
		 * readout to be diagnosable at all. */
		if (++windows_since_stats >= ACCEL_SAMPLER_STATS_EVERY_WINDOWS) {
			struct hal_accel_stats st;

			windows_since_stats = 0;
			hal_accel_get_stats(&st);
			Serial.printf("[accel] reads=%u fifo_full=%u (%.1f%%) overrun=%u (%.1f%%) "
				      "gap_us mean=%llu max=%u limit=%u frames=%u\n",
				      st.reads, st.fifo_full_reads,
				      st.reads ? 100.0f * st.fifo_full_reads / st.reads : 0.0f,
				      st.overrun_reads,
				      st.reads ? 100.0f * st.overrun_reads / st.reads : 0.0f,
				      st.reads > 1 ? st.total_gap_us / (st.reads - 1) : 0,
				      st.max_gap_us, st.span_us, st.frames_read);
		}
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
		xTaskCreatePinnedToCore(accel_sampler_task_entry, "accel_sampler",
					ACCEL_SAMPLER_TASK_STACK_WORDS, NULL,
					ACCEL_SAMPLER_TASK_PRIORITY, &handle, CORE_SENSING);

	return ok == pdPASS ? 0 : -ENOMEM;
}
