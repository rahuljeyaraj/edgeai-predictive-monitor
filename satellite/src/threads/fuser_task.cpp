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
 * carrying a SPECTRUM section per enabled channel, and publishes the raw
 * frame bytes as this node's MQTT data-topic body - no TYPE-byte envelope,
 * matching base-station/python/common/telemetry_frame.py's decoder and the
 * base station's own SPI link (SENSOR_TELEMETRY_FRAME_PLAN.md S3/S6).
 * Replaces this task's earlier fixed spectrum_fused_payload_header +
 * single-TYPE-byte MQTT wrapping.
 */

#define FUSER_TASK_STACK_WORDS 6144
#define FUSER_TASK_PRIORITY    5

/* Worst case: both channels present, each at its configured bin count. */
#define FUSER_FRAME_BUF_LEN                                                                      \
	(1 + 2 * SPECTRUM_SECTION_OVERHEAD + (MIC_FFT_BIN_COUNT + ACCEL_FFT_BIN_COUNT) * sizeof(float))

static void fuser_task_entry(void *arg)
{
	(void)arg;

	static float mic_bins[MIC_FFT_BIN_COUNT];
	static float accel_bins[ACCEL_FFT_BIN_COUNT];
	static uint8_t frame_buf[FUSER_FRAME_BUF_LEN];

	/* mic_fft_size/accel_fft_size are compile-time constant (driven by
	 * app_config.h); mic_fs/accel_fs come from each HAL's
	 * get_sample_rate() (also fixed at init time), so all four are read
	 * once here rather than every epoch. */
	float mic_fs = MIC_SENSOR_ENABLED ? (float)hal_audio_get_sample_rate() : 0.0f;
	uint16_t mic_fft_size = MIC_SENSOR_ENABLED ? (uint16_t)(MIC_FFT_BIN_COUNT * 2) : 0;
	float accel_fs = ACCEL_SENSOR_ENABLED ? (float)hal_accel_get_sample_rate() : 0.0f;
	uint16_t accel_fft_size = ACCEL_SENSOR_ENABLED ? (uint16_t)(ACCEL_FFT_BIN_COUNT * 2) : 0;

	Serial.printf("[fuser] mic fs=%u fft_size=%u bin_count=%u | accel fs=%u fft_size=%u "
		      "bin_count=%u\n",
		      (unsigned)mic_fs, mic_fft_size, MIC_SENSOR_ENABLED ? MIC_FFT_BIN_COUNT : 0,
		      (unsigned)accel_fs, accel_fft_size,
		      ACCEL_SENSOR_ENABLED ? ACCEL_FFT_BIN_COUNT : 0);

	while (1) {
		/* xQueueReceive() with a 0 timeout on an empty queue leaves
		 * mic_bins/accel_bins untouched - the sample-and-hold
		 * guarantee: nothing new since the last epoch just means
		 * "publish whatever we already had". */
		if (MIC_SENSOR_ENABLED) {
			xQueueReceive(mic_spectrum_queue, mic_bins, 0);
		}
		if (ACCEL_SENSOR_ENABLED) {
			xQueueReceive(accel_spectrum_queue, accel_bins, 0);
		}

		/* One SPECTRUM section per enabled channel - a sensor this
		 * node lacks entirely is left out of the array rather than
		 * sent as a zero-bin_count section (spectrum_codec.h). */
		struct spectrum_channel channels[2];
		size_t num_channels = 0;

		if (MIC_SENSOR_ENABLED) {
			channels[num_channels].channel_id = TELEM_CHANNEL_MIC;
			channels[num_channels].fs = mic_fs;
			channels[num_channels].fft_size = mic_fft_size;
			channels[num_channels].bin_count = MIC_FFT_BIN_COUNT;
			channels[num_channels].bins = mic_bins;
			num_channels++;
		}
		if (ACCEL_SENSOR_ENABLED) {
			channels[num_channels].channel_id = TELEM_CHANNEL_ACCEL;
			channels[num_channels].fs = accel_fs;
			channels[num_channels].fft_size = accel_fft_size;
			channels[num_channels].bin_count = ACCEL_FFT_BIN_COUNT;
			channels[num_channels].bins = accel_bins;
			num_channels++;
		}

		size_t frame_len = telemetry_build_spectrum_frame(channels, num_channels, frame_buf,
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
