#include <string.h>

#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include "app_config.h"
#include "frame_codec/spectrum_codec.h"
#include "frame_codec/telemetry_schema.h"
#include "hal/hal_accel.h"
#include "hal/hal_audio.h"
#include "hal/hal_transport.h"
#include "threads/accel_sampler_task.h"
#include "threads/fuser_task.h"
#include "threads/mic_sampler_task.h"

/*
 * Periodic sample-and-hold + publish task (see fuser_task.h). Builds one
 * generic section-list telemetry frame (frame_codec/spectrum_codec.h)
 * carrying a SPECTRUM section per enabled channel (mic, accel_x, accel_y,
 * accel_z - per-axis, not the retired combined `accel` channel) plus one
 * SCALAR_SET section of that same channel set's rms/kurtosis/std/peak/
 * crest_factor/skewness tiles, and publishes the raw frame bytes as this
 * node's MQTT data-topic body - no TYPE-byte envelope, matching
 * base-station/python/common/telemetry_frame.py's decoder and the base
 * station's own SPI link (SENSOR_TELEMETRY_FRAME_PLAN.md S3/S6).
 *
 * Matches base-station/sketch/fuser.cpp's current per-axis+scalar shape
 * (pipeline/features.py's build_feature_vector() requires exactly this: a
 * SensorChannel's full 6-scalar tile or the frame is rejected outright) -
 * this task's earlier combined mic+accel, spectrum-only shape predated that
 * migration and was no longer accepted by the base station's ingestion.
 *
 * Each channel's native MIC_FFT_BIN_COUNT/ACCEL_FFT_BIN_COUNT spectrum
 * (app_config.h) is average-pooled down to MODEL_SPECTRUM_BINS before going
 * on the wire - same reasoning as base-station/sketch/fuser.cpp's own
 * fuser_pool_spectrum(): keeps a real node's per-frame payload size (and
 * therefore MQTT bandwidth) bounded regardless of how fine the on-device FFT
 * resolution is. fft_size travels on the wire already scaled down to match
 * (dashboard frequency-axis math divides by it directly, charts.js) - this
 * bit the base station itself once (an unscaled fft_size compressed its
 * displayed frequency range 4x), so it's applied here from the start.
 */

#define FUSER_TASK_STACK_WORDS 6144
#define FUSER_TASK_PRIORITY    5

#define ACCEL_POOL_FACTOR (ACCEL_FFT_BIN_COUNT / MODEL_SPECTRUM_BINS)
#define MIC_POOL_FACTOR   (MIC_FFT_BIN_COUNT / MODEL_SPECTRUM_BINS)

/* Worst case: mic + all 3 accel axes present, all 24 scalars present. */
#define FUSER_NUM_SPECTRUM_CHANNELS 4
#define FUSER_NUM_SCALARS           24
#define FUSER_FRAME_BUF_LEN                                                                      \
	(1 +                                                                                      \
	 FUSER_NUM_SPECTRUM_CHANNELS *                                                            \
		 (SPECTRUM_SECTION_OVERHEAD + MODEL_SPECTRUM_BINS * sizeof(float)) +              \
	 SCALAR_SECTION_OVERHEAD + FUSER_NUM_SCALARS * SCALAR_ENTRY_SIZE)

/* Average-pool a MIC_FFT_BIN_COUNT/ACCEL_FFT_BIN_COUNT-resolution spectrum
 * down to MODEL_SPECTRUM_BINS buckets for the wire - same scheme as
 * base-station/sketch/fuser.cpp's fuser_pool_spectrum(). */
static void pool_spectrum(const float *in, int factor, float *out)
{
	for (int b = 0; b < MODEL_SPECTRUM_BINS; b++) {
		float sum = 0.0f;

		for (int i = 0; i < factor; i++) {
			sum += in[b * factor + i];
		}
		out[b] = sum / (float)factor;
	}
}

static void append_axis_scalars(struct scalar_entry *scalars, size_t *count,
				 const struct accel_axis_result *axis, uint16_t id_rms,
				 uint16_t id_kurtosis, uint16_t id_std, uint16_t id_peak,
				 uint16_t id_crest, uint16_t id_skew)
{
	scalars[(*count)++] = {id_rms, axis->rms};
	scalars[(*count)++] = {id_kurtosis, axis->kurtosis};
	scalars[(*count)++] = {id_std, axis->std};
	scalars[(*count)++] = {id_peak, axis->peak};
	scalars[(*count)++] = {id_crest, axis->crest_factor};
	scalars[(*count)++] = {id_skew, axis->skewness};
}

static void fuser_task_entry(void *arg)
{
	(void)arg;

	/* xQueueReceive() with a 0 timeout on an empty queue leaves its
	 * destination untouched - the sample-and-hold guarantee: nothing new
	 * since the last epoch just means "publish whatever we already had".
	 * Held here (not a local temp) for exactly that reason. */
	static struct accel_sample held_accel;
	static struct mic_sample held_mic;

	static float pooled_mic[MODEL_SPECTRUM_BINS];
	static float pooled_accel_x[MODEL_SPECTRUM_BINS];
	static float pooled_accel_y[MODEL_SPECTRUM_BINS];
	static float pooled_accel_z[MODEL_SPECTRUM_BINS];

	static uint8_t frame_buf[FUSER_FRAME_BUF_LEN];

	/* fs/native fft_size are compile-time-fixed (driven by app_config.h)
	 * or read once from each HAL's get_sample_rate() (also fixed at init
	 * time), so all are read once here rather than every epoch. fft_size
	 * on the wire is the pooled value (ACCEL_FFT_LEN/MIC_FFT_LEN divided
	 * by the same factor the spectrum itself is pooled by), matching
	 * base-station/sketch/fuser.cpp's own mic_fft_pooled/accel_fft_pooled. */
	float mic_fs = MIC_SENSOR_ENABLED ? (float)hal_audio_get_sample_rate() : 0.0f;
	uint16_t mic_fft_size = MIC_SENSOR_ENABLED ? (uint16_t)(MIC_FFT_LEN / MIC_POOL_FACTOR) : 0;
	float accel_fs = ACCEL_SENSOR_ENABLED ? (float)hal_accel_get_sample_rate() : 0.0f;
	uint16_t accel_fft_size =
		ACCEL_SENSOR_ENABLED ? (uint16_t)(ACCEL_FFT_LEN / ACCEL_POOL_FACTOR) : 0;

	Serial.printf("[fuser] mic fs=%u fft_size=%u bin_count=%u | accel fs=%u fft_size=%u "
		      "bin_count=%u\n",
		      (unsigned)mic_fs, mic_fft_size, MIC_SENSOR_ENABLED ? MODEL_SPECTRUM_BINS : 0,
		      (unsigned)accel_fs, accel_fft_size,
		      ACCEL_SENSOR_ENABLED ? MODEL_SPECTRUM_BINS : 0);

	while (1) {
		if (MIC_SENSOR_ENABLED) {
			xQueueReceive(mic_spectrum_queue, &held_mic, 0);
			pool_spectrum(held_mic.mag, MIC_POOL_FACTOR, pooled_mic);
		}
		if (ACCEL_SENSOR_ENABLED) {
			xQueueReceive(accel_spectrum_queue, &held_accel, 0);
			pool_spectrum(held_accel.x.mag, ACCEL_POOL_FACTOR, pooled_accel_x);
			pool_spectrum(held_accel.y.mag, ACCEL_POOL_FACTOR, pooled_accel_y);
			pool_spectrum(held_accel.z.mag, ACCEL_POOL_FACTOR, pooled_accel_z);
		}

		/* One SPECTRUM section per enabled channel - a sensor this
		 * node lacks entirely is left out of the array rather than
		 * sent as a zero-bin_count section (spectrum_codec.h). */
		struct spectrum_channel channels[FUSER_NUM_SPECTRUM_CHANNELS];
		size_t num_channels = 0;

		if (MIC_SENSOR_ENABLED) {
			channels[num_channels++] = {TELEM_CHANNEL_MIC, mic_fs, mic_fft_size,
						     MODEL_SPECTRUM_BINS, pooled_mic};
		}
		if (ACCEL_SENSOR_ENABLED) {
			channels[num_channels++] = {TELEM_CHANNEL_ACCEL_X, accel_fs, accel_fft_size,
						     MODEL_SPECTRUM_BINS, pooled_accel_x};
			channels[num_channels++] = {TELEM_CHANNEL_ACCEL_Y, accel_fs, accel_fft_size,
						     MODEL_SPECTRUM_BINS, pooled_accel_y};
			channels[num_channels++] = {TELEM_CHANNEL_ACCEL_Z, accel_fs, accel_fft_size,
						     MODEL_SPECTRUM_BINS, pooled_accel_z};
		}

		/* Same channel set's scalar tiles, one SCALAR_SET section
		 * (pipeline/features.py requires all 6 per live channel or
		 * none at all - never a partial tile). */
		struct scalar_entry scalars[FUSER_NUM_SCALARS];
		size_t num_scalars = 0;

		if (ACCEL_SENSOR_ENABLED) {
			append_axis_scalars(scalars, &num_scalars, &held_accel.x,
					     TELEM_SCALAR_RMS_X, TELEM_SCALAR_KURTOSIS_X,
					     TELEM_SCALAR_STD_X, TELEM_SCALAR_PEAK_X,
					     TELEM_SCALAR_CREST_FACTOR_X, TELEM_SCALAR_SKEWNESS_X);
			append_axis_scalars(scalars, &num_scalars, &held_accel.y,
					     TELEM_SCALAR_RMS_Y, TELEM_SCALAR_KURTOSIS_Y,
					     TELEM_SCALAR_STD_Y, TELEM_SCALAR_PEAK_Y,
					     TELEM_SCALAR_CREST_FACTOR_Y, TELEM_SCALAR_SKEWNESS_Y);
			append_axis_scalars(scalars, &num_scalars, &held_accel.z,
					     TELEM_SCALAR_RMS_Z, TELEM_SCALAR_KURTOSIS_Z,
					     TELEM_SCALAR_STD_Z, TELEM_SCALAR_PEAK_Z,
					     TELEM_SCALAR_CREST_FACTOR_Z, TELEM_SCALAR_SKEWNESS_Z);
		}
		if (MIC_SENSOR_ENABLED) {
			scalars[num_scalars++] = {TELEM_SCALAR_RMS_MIC, held_mic.rms};
			scalars[num_scalars++] = {TELEM_SCALAR_KURTOSIS_MIC, held_mic.kurtosis};
			scalars[num_scalars++] = {TELEM_SCALAR_STD_MIC, held_mic.std};
			scalars[num_scalars++] = {TELEM_SCALAR_PEAK_MIC, held_mic.peak};
			scalars[num_scalars++] = {TELEM_SCALAR_CREST_FACTOR_MIC,
						   held_mic.crest_factor};
			scalars[num_scalars++] = {TELEM_SCALAR_SKEWNESS_MIC, held_mic.skewness};
		}

		size_t frame_len = telemetry_build_frame(channels, num_channels, scalars,
							  num_scalars, frame_buf,
							  sizeof(frame_buf));

		if (frame_len > 0) {
			transport_publish_spectrum(frame_buf, frame_len);
		}

		vTaskDelay(pdMS_TO_TICKS(FUSER_EPOCH_MS));
	}
}

int fuser_task_start(void)
{
	TaskHandle_t handle = NULL;
	BaseType_t ok = xTaskCreate(fuser_task_entry, "fuser", FUSER_TASK_STACK_WORDS, NULL,
				    FUSER_TASK_PRIORITY, &handle);

	return ok == pdPASS ? 0 : -1;
}
