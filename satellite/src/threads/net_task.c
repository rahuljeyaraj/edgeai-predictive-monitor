/*
 * net_task.c — MQTT telemetry publish loop (Phase 0.5, real data since 6c).
 *
 * Blocks on wifi_task.h's wifi_wait_connected() (WiFi STA bring-up lives in
 * threads/wifi_task.c — see docs/decisions/ADR-022-wifi-task-revived.md and
 * docs/decisions/ADR-023-transport-adrs-superseded.md), then starts the
 * MQTT link (components/epm_drivers/link_mqtt.c) and
 * publishes one section-list telemetry frame
 * (components/epm_codec/include/frame_codec/spectrum_codec.h) every
 * EPM_NET_PUBLISH_INTERVAL_MS, built from real mic/IMU FFT output delivered
 * via dsp_task_get_queue()/imu_task_get_queue() — this task is each
 * producer's sole consumer (see docs/decisions/ADR-021-net-task-second-consumer-queue.md's
 * addendum: the second-queue split it introduced collapsed back to one
 * queue per producer once tcp_task.c, the other consumer, was deleted).
 *
 * No frame is published until both queues have delivered at least one real
 * frame (docs/BASE_STATION_CONTRACT.md line 24: a present, real-bin_count,
 * all-zero channel reads as genuine silence to the model, not "no data
 * yet" — zero-filling before real data exists would be indistinguishable
 * from that). Once both sides have delivered once, every tick publishes
 * whatever is currently cached, refreshed opportunistically each tick.
 *
 * This task also owns the inbound cmd-topic wiring: it registers the sole
 * transport_set_cmd_handler() caller in the tree (Phase 7c) so a decoded
 * STATUS_LED command reaches the display driver instead of only being
 * logged, and polls transport_is_connected() once per tick to revert the
 * display to a local state on MQTT disconnect (ADR-025).
 */

#include "threads/net_task.h"

#include <errno.h>
#include <string.h>

#include "esp_attr.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"

#include "dsp/spectrum.h"
#include "frame_codec/scalar_map.h"
#include "frame_codec/spectrum_codec.h"
#include "frame_codec/telemetry_schema.h"
#include "frame_codec/wire_protocol.h"
#include "hal/hal_display.h"
#include "hal/hal_transport.h"
#include "drivers/link_mqtt.h"

#include "epm_config.h"
#include "threads/wifi_task.h"

static const char *TAG = "net_task";

/* net_task_start()'s two queue args, handed to net_task_fn via
 * xTaskCreatePinnedToCore's arg pointer — ADR-021. */
typedef struct {
	QueueHandle_t mic_q;
	QueueHandle_t imu_q;
} net_task_args_t;

static net_task_args_t s_task_args;

/* Cached last-received frames. mic_frame_t (~2.1 KB) and imu_frame_t
 * (~12.4 KB) are both too large for TASK_STACK_NET (4096 B), so
 * xQueueReceive() always writes directly into these file-scope statics,
 * never a stack temporary. EXT_RAM_BSS_ATTR places them in PSRAM — the same placement
 * already used for this size class (dsp_task.c's s_mag_db, imu_task.c's
 * s_frame) — to protect the tight internal-DRAM heap margin
 * docs/decisions/ADR-020-bin-count-downsampled-not-buffer-enlarged.md
 * documents. xQueueReceive has no DMA constraint on this path (unlike the
 * KX134 FIFO buffer), so writing into PSRAM here is safe. */
static EXT_RAM_BSS_ATTR mic_frame_t s_last_mic;
static EXT_RAM_BSS_ATTR imu_frame_t s_last_imu;
static bool s_have_mic = false;
static bool s_have_imu = false;

/* Reduced (EPM_MODEL_SPECTRUM_BINS-wide) spectra, rebuilt each publish tick.
 * Small enough to stay plain internal DRAM. */
static float s_mic_bins[EPM_MODEL_SPECTRUM_BINS];
static float s_accel_x_bins[EPM_MODEL_SPECTRUM_BINS];
static float s_accel_y_bins[EPM_MODEL_SPECTRUM_BINS];
static float s_accel_z_bins[EPM_MODEL_SPECTRUM_BINS];

/* ADR-040 raised EPM_NET_FRAME_BUF_BYTES 4096->8192 (BINS 128->256), which no
 * longer fits comfortably in the tight internal-DRAM budget this file's own
 * s_last_mic/s_last_imu comment above documents. EXT_RAM_BSS_ATTR moves it to
 * PSRAM, same precedent and same "no DMA constraint on this path" reasoning
 * as those two statics (ADR-021 addendum) -- frame_buf is filled by
 * build_real_frame() (plain memory writes, no DMA) and handed to
 * transport_publish_spectrum() -> esp_mqtt_client_publish(), which is a TCP
 * payload copy, not a DMA-constrained peripheral transfer. */
static EXT_RAM_BSS_ATTR uint8_t frame_buf[EPM_NET_FRAME_BUF_BYTES];

static uint32_t s_frames_built = 0;
static uint32_t s_build_failures = 0;
static uint32_t s_publish_failures = 0;
static uint32_t s_cmd_malformed = 0;
static uint32_t s_disconnect_reverts = 0;

void net_task_get_stats(struct net_task_stats *out)
{
	if (out == NULL) return;
	out->frames_built       = s_frames_built;
	out->build_failures     = s_build_failures;
	out->publish_failures   = s_publish_failures;
	out->cmd_malformed      = s_cmd_malformed;
	out->disconnect_reverts = s_disconnect_reverts;
}

/* Builds the 8-section frame (7 SPECTRUM + 1 SCALAR_SET) from the currently
 * cached s_last_mic/s_last_imu. Returns the encoded length, or 0 if
 * out_buf_size is too small or a bin-reduction call fails
 * (telemetry_build_frame()'s / epm_dsp_reduce_bins()'s convention).
 *
 * The 3 envelope channels (Phase 11a / ADR-032) need no epm_dsp_reduce_bins()
 * call: imu_task.c's envelope pipeline decimates+FFTs down to
 * IMU_ENVELOPE_HALF bins directly, which epm_config.h's build-time check
 * guarantees equals EPM_MODEL_SPECTRUM_BINS. */
static size_t build_real_frame(uint8_t *out_buf, size_t out_buf_size)
{
	if (epm_dsp_reduce_bins(s_last_mic.fft_db, FFT_MIC_N / 2, s_mic_bins, EPM_MODEL_SPECTRUM_BINS) != 0 ||
	    epm_dsp_reduce_bins(s_last_imu.fft_x, FFT_IMU_N / 2, s_accel_x_bins, EPM_MODEL_SPECTRUM_BINS) != 0 ||
	    epm_dsp_reduce_bins(s_last_imu.fft_y, FFT_IMU_N / 2, s_accel_y_bins, EPM_MODEL_SPECTRUM_BINS) != 0 ||
	    epm_dsp_reduce_bins(s_last_imu.fft_z, FFT_IMU_N / 2, s_accel_z_bins, EPM_MODEL_SPECTRUM_BINS) != 0) {
		ESP_LOGE(TAG, "epm_dsp_reduce_bins failed");
		return 0;
	}

	const float envelope_fs = (float)IMU_FS_HZ / IMU_ENVELOPE_DECIM;

	struct spectrum_channel channels[7] = {
		{.channel_id = TELEM_CHANNEL_MIC,
		 .fs = (float)MIC_FS_HZ,
		 .fft_size = (uint16_t)EPM_MIC_WIRE_FFT_SIZE,
		 .bin_count = EPM_MODEL_SPECTRUM_BINS,
		 .bins = s_mic_bins},
		{.channel_id = TELEM_CHANNEL_ACCEL_X,
		 .fs = (float)IMU_FS_HZ,
		 .fft_size = (uint16_t)EPM_IMU_WIRE_FFT_SIZE,
		 .bin_count = EPM_MODEL_SPECTRUM_BINS,
		 .bins = s_accel_x_bins},
		{.channel_id = TELEM_CHANNEL_ACCEL_Y,
		 .fs = (float)IMU_FS_HZ,
		 .fft_size = (uint16_t)EPM_IMU_WIRE_FFT_SIZE,
		 .bin_count = EPM_MODEL_SPECTRUM_BINS,
		 .bins = s_accel_y_bins},
		{.channel_id = TELEM_CHANNEL_ACCEL_Z,
		 .fs = (float)IMU_FS_HZ,
		 .fft_size = (uint16_t)EPM_IMU_WIRE_FFT_SIZE,
		 .bin_count = EPM_MODEL_SPECTRUM_BINS,
		 .bins = s_accel_z_bins},
		{.channel_id = TELEM_CHANNEL_ACCEL_X_ENVELOPE,
		 .fs = envelope_fs,
		 .fft_size = (uint16_t)IMU_ENVELOPE_N,
		 .bin_count = EPM_MODEL_SPECTRUM_BINS,
		 .bins = s_last_imu.fft_x_env},
		{.channel_id = TELEM_CHANNEL_ACCEL_Y_ENVELOPE,
		 .fs = envelope_fs,
		 .fft_size = (uint16_t)IMU_ENVELOPE_N,
		 .bin_count = EPM_MODEL_SPECTRUM_BINS,
		 .bins = s_last_imu.fft_y_env},
		{.channel_id = TELEM_CHANNEL_ACCEL_Z_ENVELOPE,
		 .fs = envelope_fs,
		 .fft_size = (uint16_t)IMU_ENVELOPE_N,
		 .bin_count = EPM_MODEL_SPECTRUM_BINS,
		 .bins = s_last_imu.fft_z_env},
	};

	struct axis_scalars sc_x = {
		.rms = s_last_imu.rms_x, .kurtosis = s_last_imu.kurtosis_x, .std = s_last_imu.std_x,
		.peak = s_last_imu.peak_x, .crest = s_last_imu.crest_x, .skewness = s_last_imu.skewness_x,
	};
	struct axis_scalars sc_y = {
		.rms = s_last_imu.rms_y, .kurtosis = s_last_imu.kurtosis_y, .std = s_last_imu.std_y,
		.peak = s_last_imu.peak_y, .crest = s_last_imu.crest_y, .skewness = s_last_imu.skewness_y,
	};
	struct axis_scalars sc_z = {
		.rms = s_last_imu.rms_z, .kurtosis = s_last_imu.kurtosis_z, .std = s_last_imu.std_z,
		.peak = s_last_imu.peak_z, .crest = s_last_imu.crest_z, .skewness = s_last_imu.skewness_z,
	};
	struct axis_scalars sc_mic = {
		.rms = s_last_mic.rms, .kurtosis = s_last_mic.kurtosis, .std = s_last_mic.std,
		.peak = s_last_mic.peak, .crest = s_last_mic.crest, .skewness = s_last_mic.skewness,
	};

	struct scalar_entry scalars[24];

	scalar_map_build_axis(&sc_x, TELEM_SCALAR_RMS_X, &scalars[0]);
	scalar_map_build_axis(&sc_y, TELEM_SCALAR_RMS_Y, &scalars[6]);
	scalar_map_build_axis(&sc_z, TELEM_SCALAR_RMS_Z, &scalars[12]);
	scalar_map_build_axis(&sc_mic, TELEM_SCALAR_RMS_MIC, &scalars[18]);

	return telemetry_build_frame(channels, 7, scalars, 24, out_buf, out_buf_size);
}

/* transport_set_cmd_handler()'s registered callback (Phase 7c). Decodes a
 * STATUS_LED command and drives the display directly - the only defined
 * cmd type today (frame_codec/wire_protocol.h). */
static void net_task_cmd_handler(uint8_t type, const uint8_t *body, size_t len)
{
	if (type != MQTT_MSG_TYPE_STATUS_LED) {
		return;
	}

	struct display_rgb_payload rgb;

	if (!mqtt_decode_status_led(body, len, &rgb)) {
		s_cmd_malformed++;
		ESP_LOGW(TAG, "cmd handler: malformed STATUS_LED payload (%u bytes)",
			 (unsigned)len);
		return;
	}

	rgb_led_set_remote(rgb.rgb, rgb.mode, rgb.period_ms);
}

#if CONFIG_EPM_STATUS_LED_SELFTEST
/* TEMPORARY, test-only (Phase 7c hardware validation, see ADR-025). Fires a
 * manufactured STATUS_LED straight at the cmd handler net_task_fn() just
 * registered, so the decode-dispatch-LED-update path can be confirmed on
 * real hardware without a working MQTT broker connection (ADR-011's known
 * TCP-handshake stall). Blueviolet/BREATHE/500ms - deliberately not any
 * color in display_neopixel.c's k_pattern[] table, so a human watching the
 * board can tell this fired apart from any local state it might already be
 * showing.
 *
 * Runs from its own short-lived task, delayed ~90s past boot, instead of
 * firing inline in net_task_fn() at registration time: ADR-025's
 * last-write-wins single-slot queue means firing immediately at boot loses
 * the race against dsp_task.c's own one-shot rgb_led_set_state(RGB_OK) at
 * HST warm-up (~250 mic frames in, observed ~60-70s after boot), which
 * silently overwrites the remote color before a human has a chance to look
 * (confirmed on hardware: an immediate-fire version showed blueviolet only
 * in the first minute, then reverted to solid green with no further state
 * changes after). Firing after warm-up instead makes the self-test color
 * the last write, so it persists indefinitely for observation. */
static void net_task_selftest_task(void *arg)
{
	(void)arg;
	vTaskDelay(pdMS_TO_TICKS(90000));

	struct display_rgb_payload selftest_rgb = {
		.rgb = 0x8A2BE2, .mode = 1, .period_ms = 500,
	};
	uint8_t selftest_buf[sizeof(selftest_rgb)];

	memcpy(selftest_buf, &selftest_rgb, sizeof(selftest_rgb));
	ESP_LOGW(TAG, "EPM_STATUS_LED_SELFTEST: firing manufactured STATUS_LED at cmd handler");
	net_task_cmd_handler(MQTT_MSG_TYPE_STATUS_LED, selftest_buf, sizeof(selftest_buf));

	vTaskDelete(NULL);
}
#endif

#if CONFIG_EPM_MQTT_RECONNECT_STRESS_TEST
/* TEMPORARY, test-only. Accelerated leak-per-call test for
 * link_mqtt_start()'s retry-path fix (commit dab1064: destroying the stale
 * s_client handle before re-init). Normal operation calls link_mqtt_start()
 * exactly once per boot - net_task_fn()'s own retry loop below only repeats
 * if that first call fails outright, and once the publish loop starts,
 * mid-session drops are handled entirely inside esp-mqtt's own internal
 * reconnect state machine, which never touches link_mqtt_start() again.
 * That means ordinary running time, however long, can't exercise this
 * function's leak-fix path more than once. This task does, directly,
 * MQTT_STRESS_TEST_ITERATIONS times, logging free internal heap
 * immediately before/after each call so a per-call leak - if any -
 * shows up as a monotonic decline across the run. Delayed 30s past boot
 * so it doesn't pollute the steady-state DIAG baseline; safe to run
 * concurrently with the real publish loop only because link_mqtt_start()
 * now guards its client swap with s_mutex (see link_mqtt.c). */
#define MQTT_STRESS_TEST_ITERATIONS 50

static void net_task_mqtt_stress_task(void *arg)
{
	(void)arg;
	vTaskDelay(pdMS_TO_TICKS(30000));

	ESP_LOGW(TAG, "EPM_MQTT_RECONNECT_STRESS_TEST: starting %d x link_mqtt_start() calls",
		 MQTT_STRESS_TEST_ITERATIONS);

	size_t first_heap = 0, last_heap = 0, min_heap = SIZE_MAX;

	for (int i = 0; i < MQTT_STRESS_TEST_ITERATIONS; i++) {
		size_t before = heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
		int rc = link_mqtt_start();
		size_t after = heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);

		ESP_LOGW(TAG, "mqtt_stress[%d/%d]: rc=%d before=%u after=%u delta=%d",
			 i + 1, MQTT_STRESS_TEST_ITERATIONS, rc, (unsigned)before,
			 (unsigned)after, (int)after - (int)before);

		if (i == 0) {
			first_heap = after;
		}
		last_heap = after;
		if (after < min_heap) {
			min_heap = after;
		}

		vTaskDelay(pdMS_TO_TICKS(1000));
	}

	ESP_LOGW(TAG, "EPM_MQTT_RECONNECT_STRESS_TEST done: first=%u last=%u min=%u "
		 "net_delta=%d avg_per_call=%.1f",
		 (unsigned)first_heap, (unsigned)last_heap, (unsigned)min_heap,
		 (int)last_heap - (int)first_heap,
		 (double)((int)last_heap - (int)first_heap) / (MQTT_STRESS_TEST_ITERATIONS - 1));

	/* link_mqtt_start()'s transport_init() call unconditionally wipes the
	 * cmd handler each time - re-register it so the board is left in a
	 * normal working state after the test finishes. */
	transport_set_cmd_handler(net_task_cmd_handler);

	vTaskDelete(NULL);
}
#endif

static void net_task_fn(void *arg)
{
	net_task_args_t *args = (net_task_args_t *)arg;
	QueueHandle_t mic_q = args->mic_q;
	QueueHandle_t imu_q = args->imu_q;
	bool mqtt_was_connected = false;

	wifi_wait_connected(portMAX_DELAY);

#if CONFIG_EPM_LOW_HEAP_BOOT_STALL_TEST
	/* TEMPORARY, test-only. Deterministic repro/verify aid for the
	 * low-heap MQTT-init stall documented as an ADR-036 watchdog blind
	 * spot (docs/decisions/ADR-036-mqtt-reconnect-watchdog.md's dated
	 * addendum). The original finding hit this precondition (free heap
	 * below ADR-024's 32768-byte margin by the time link_mqtt_start()
	 * first runs, below) via an extended real WiFi boot-retry loop timed
	 * against a router power-cycle — not reproducible on demand. This
	 * reserves (and deliberately never frees) enough internal-DRAM heap
	 * right here, on an otherwise-normal WiFi connection, to land below
	 * the same margin directly.
	 *
	 * Arms exactly once, via an RTC_NOINIT_ATTR magic value — not an
	 * esp_reset_reason() check: an earlier version of this hook gated on
	 * esp_reset_reason() != ESP_RST_SW, but that never armed right after
	 * a fresh `pio run -t upload` on this board (esp-builtin/OpenOCD's
	 * post-flash reset apparently doesn't read back as ESP_RST_POWERON).
	 *
	 * IMPORTANT: RTC memory survives esp_restart() AND an esp-builtin/
	 * OpenOCD reflash — confirmed the hard way when a second flash (with
	 * the Gap 1 fix applied) inherited the magic value armed by the
	 * previous test run and silently skipped the heap reservation. Only
	 * an actual power-loss (VDD_RTC dropping — unplug/replug USB) clears
	 * it. So: first boot after a genuine power-on arms the test. Next
	 * boot, if the watchdog fix restarts the board via esp_restart():
	 * magic already set, does NOT re-arm — a fixed board recovers once
	 * and stays recovered instead of re-triggering the same low-heap
	 * condition and loop-restarting forever. But re-running this test
	 * after a reflash requires a real power cycle first, not just the
	 * reflash. */
	static RTC_NOINIT_ATTR uint32_t s_low_heap_test_armed;
#define LOW_HEAP_TEST_MAGIC 0xEA51EA51u
	if (s_low_heap_test_armed != LOW_HEAP_TEST_MAGIC) {
		s_low_heap_test_armed = LOW_HEAP_TEST_MAGIC;

		size_t free_now = heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
		size_t target_free = 20000;

		if (free_now > target_free) {
			size_t reserve = free_now - target_free;
			void *hog = heap_caps_malloc(reserve, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);

			ESP_LOGW(TAG, "EPM_LOW_HEAP_BOOT_STALL_TEST: reserved %u bytes "
				 "(leaked for this boot), free now %u vs ADR-024's "
				 "32768-byte margin",
				 (unsigned)reserve,
				 (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));
			(void)hog;
		}
	}
#endif

	/* Free internal-DRAM heap immediately before esp-mqtt's client init —
	 * the measurement Phase 7b's ADR-024 decision is based on (ADR-017:
	 * esp_mqtt_client_init() null-derefs later if esp_event_loop_create()
	 * fails here under tight margin). */
	ESP_LOGI(TAG, "free heap before link_mqtt_start(): internal=%lu",
		 (unsigned long)heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));

	/* link_mqtt_start() can transiently fail ADR-024's heap-margin guard
	 * right at boot: this task and wifi_provision_task.c both wake on the
	 * same "WiFi connected" event, and if the provisioning captive portal
	 * (AP+DNS+HTTP) hasn't finished tearing down yet it holds heap a few
	 * hundred bytes below the 32768-byte margin for a matter of seconds.
	 * Retry instead of giving up for the rest of the boot — there's
	 * nothing else useful this task can do without a transport (the
	 * publish loop below already treats "not connected" as a no-op, not
	 * an error), and both producer queues are depth-1 xQueueOverwrite
	 * (mic_task.c, imu_task.c), so delaying entry into that loop can't
	 * backpressure or stall them. */
	int rc = link_mqtt_start();

	while (rc != 0) {
		vTaskDelay(pdMS_TO_TICKS(2000));
		rc = link_mqtt_start();
	}

	/* Nothing else writes the LED between here and dsp_task.c's one-shot
	 * rgb_led_set_state(RGB_OK) at HST warm-up (~250 mic frames, observed
	 * ~60-70s after boot per net_task_selftest_task()'s comment above) —
	 * without this, the LED just sits on wifi_task.c's RGB_TCP_CONN for
	 * that whole gap. This has to be the write site rather than dsp_task.c
	 * itself: dsp_task_start() runs (main.c) before net_task_start(), so a
	 * CALIBRATING write placed at dsp_task_fn's warm-up-counting start
	 * loses ADR-025's last-write-wins race against wifi_task.c's later
	 * RGB_WIFI_CONN/RGB_TCP_CONN writes and is silently clobbered before
	 * ever being seen — confirmed on hardware (2026-08-16): a
	 * dsp_task.c-sited version never showed cyan at all. This site is
	 * causally after both of those (wifi_wait_connected() above already
	 * blocked on WiFi, and link_mqtt_start() just succeeded), so it can't
	 * lose that race. */
	rgb_led_set_state(RGB_CALIBRATING);

	/* Registered after link_mqtt_start() succeeds (not before): it calls
	 * transport_init() internally, which unconditionally resets the cmd
	 * handler to NULL, so registering any earlier — including between
	 * failed retries above — would just get wiped by the next attempt. */
	transport_set_cmd_handler(net_task_cmd_handler);

#if CONFIG_EPM_STATUS_LED_SELFTEST
	/* TEMPORARY, test-only (Phase 7c hardware validation, see ADR-025 and
	 * net_task_selftest_task()'s comment above for why this is deferred
	 * to a delayed background task instead of firing inline here). */
	xTaskCreate(net_task_selftest_task, "led_selftest", 2048, NULL, 3, NULL);
#endif

#if CONFIG_EPM_MQTT_RECONNECT_STRESS_TEST
	/* TEMPORARY, test-only (see net_task_mqtt_stress_task()'s comment
	 * above and Kconfig help). */
	xTaskCreate(net_task_mqtt_stress_task, "mqtt_stress", 4096, NULL, 3, NULL);
#endif

	while (1) {
		/* MQTT-level disconnect revert (ADR-025): a broker-session drop
		 * doesn't imply WiFi dropped too, so wifi_task.c's own
		 * WIFI_EVENT_STA_DISCONNECTED revert won't fire for this case -
		 * this is the path that covers it. */
		bool mqtt_connected = transport_is_connected();

		if (mqtt_was_connected && !mqtt_connected) {
			s_disconnect_reverts++;
			ESP_LOGW(TAG, "MQTT disconnected — reverting display to local state");
			/* RGB_MQTT_STALL, not RGB_WIFI_CONN: the two used to share a
			 * color, which made a broker-level stall indistinguishable
			 * from a real WiFi-association drop on the LED alone (see
			 * ADR-025's 2026-08-11 addendum and
			 * docs/NEW_NODE_SETUP_GUIDE.md §9). */
			rgb_led_set_state(RGB_MQTT_STALL);
		}
		mqtt_was_connected = mqtt_connected;

		if (xQueueReceive(mic_q, &s_last_mic, 0) == pdTRUE) {
			s_have_mic = true;
		}
		if (xQueueReceive(imu_q, &s_last_imu, 0) == pdTRUE) {
			s_have_imu = true;
		}

		if (!s_have_mic || !s_have_imu) {
			vTaskDelay(pdMS_TO_TICKS(EPM_NET_PUBLISH_INTERVAL_MS));
			continue;
		}

		size_t len = build_real_frame(frame_buf, sizeof(frame_buf));

		if (len == 0) {
			s_build_failures++;
			ESP_LOGE(TAG, "frame build failed (buffer too small?)");
		} else {
			s_frames_built++;
			int pub_rc = transport_publish_spectrum(frame_buf, len);

			if (pub_rc != 0 && pub_rc != -ENOTCONN) {
				s_publish_failures++;
				ESP_LOGW(TAG, "publish failed: %d", pub_rc);
			}
		}

		vTaskDelay(pdMS_TO_TICKS(EPM_NET_PUBLISH_INTERVAL_MS));
	}
}

int net_task_start(QueueHandle_t mic_q, QueueHandle_t imu_q)
{
	s_task_args.mic_q = mic_q;
	s_task_args.imu_q = imu_q;

	TaskHandle_t handle = NULL;
	BaseType_t ok = xTaskCreatePinnedToCore(net_task_fn, "net", TASK_STACK_NET, &s_task_args,
						 TASK_PRIO_NET, &handle, 0);

	return (ok == pdPASS) ? 0 : -ENOMEM;
}
