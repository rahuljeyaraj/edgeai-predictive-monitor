#include <Arduino.h>

#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include "app_config.h"

#include "threads/accel_sampler_task.h"
#include "threads/fuser_task.h"
#include "threads/mic_sampler_task.h"
#include "threads/rgb_display_task.h"
#include "threads/transport_task.h"

/*
 * Entry point - the Arduino/FreeRTOS port of mcu/src/main.c. Same
 * "bring up every task, bail loudly if any fails, then blink a heartbeat
 * forever" shape; no matrix_display_task here since this node has no LED
 * matrix (user-confirmed peripheral scope: accel + mic + WS2812 ring
 * only, matching what Appendix B's MQTT command topic and mpu/tools/
 * satellite_node_sim.py actually model).
 *
 * Task start order mirrors mcu/'s: transport first (so the WiFi/MQTT link
 * is coming up in the background while the rest of setup() runs - the
 * sampler tasks don't depend on it, they just publish into queues that
 * fuser_task drains once it's ready), then the display, then the
 * sensors, then fuser last (it's the one consumer of everything else).
 */

/* Heartbeat: XIAO ESP32S3's onboard single-color LED (LED_BUILTIN =
 * GPIO21, pins_arduino.h) - the direct equivalent of mcu/src/main.c's
 * heartbeat_led (onboard LED3 green channel), independent of the WS2812
 * ring the same way mcu/'s heartbeat LED is independent of that board's
 * own ring. Polarity (active-high vs. active-low) isn't hardware-
 * confirmed in this port (no physical board on the bench here) - doesn't
 * matter functionally for a blink indicator either way. */
#define HEARTBEAT_PERIOD_MS 500

/* Reports which core each task of interest is pinned to. Core affinity is
 * load-bearing on this node rather than cosmetic (app_config.h's
 * CORE_RADIO/CORE_SENSING explains why), and "which core is WiFi actually
 * on" is a property of the arduino-esp32 build rather than something
 * worth assuming - so print it and read it off the console.
 *
 * Probes tasks by name via xTaskGetHandle() instead of enumerating with
 * uxTaskGetSystemState(): the latter is declared by this SDK's headers
 * but not present in its prebuilt libfreertos.a, so it fails at link.
 * "any" means tskNO_AFFINITY - the scheduler may migrate that task
 * between cores at will, which is what every task here used to do.
 */
static void log_task_affinities(void)
{
	/* The first four are arduino-esp32/ESP-IDF's own; the rest are ours. */
	static const char *const names[] = {
		"wifi", "tiT", "sys_evt", "loopTask",
		"transport", "rgb_display", "mic_sampler", "accel_sampler", "fuser",
	};

	Serial.printf("[cores] radio=core%d sensing=core%d\n", CORE_RADIO, CORE_SENSING);
	for (size_t i = 0; i < sizeof(names) / sizeof(names[0]); i++) {
		TaskHandle_t handle = xTaskGetHandle(names[i]);

		if (handle == NULL) {
			Serial.printf("[cores]   %-14s (not running)\n", names[i]);
			continue;
		}

		BaseType_t core = xTaskGetAffinity(handle);

		Serial.printf("[cores]   %-14s prio=%-2u core=%s\n", names[i],
			      (unsigned)uxTaskPriorityGet(handle),
			      core == tskNO_AFFINITY ? "any" : (core == 0 ? "0" : "1"));
	}
}

void setup(void)
{
	Serial.begin(115200);
	delay(3000); /* let the native-USB CDC console attach before the first log line */

	Serial.println("edgeai-predictive-monitor satellite node booting...");

	if (transport_task_start() < 0) {
		Serial.println("transport_task_start failed");
		return;
	}

	if (rgb_display_task_start() < 0) {
		Serial.println("rgb_display_task_start failed");
		return;
	}

	/* A sensor that fails to come up is NOT fatal to the node. It used to
	 * be (plain `return`), which meant one unresponsive sensor took the
	 * fuser down with it: transport and the ring were already up, so the
	 * node still joined WiFi/MQTT and still answered STATUS_LED - it was
	 * "discovered" and looked alive - while publishing no telemetry frame
	 * at all. Degrading to "publish the channels that do work" makes the
	 * failure visible as a flat channel on the dashboard instead of as a
	 * silent node, and keeps the working sensor usable meanwhile. The
	 * fuser already emits a zero-filled SPECTRUM section for a channel it
	 * has no data for (SENSOR_TELEMETRY_FRAME_PLAN.md S4 zero-fill), so
	 * the frame shape the base station commits to is unchanged either
	 * way. */
	if (mic_sampler_task_start() < 0) {
		Serial.println("mic_sampler_task_start failed - continuing without mic");
	}

	if (accel_sampler_task_start() < 0) {
		Serial.println("accel_sampler_task_start failed - continuing without accel");
	}

	if (fuser_task_start() < 0) {
		Serial.println("fuser_task_start failed");
		return;
	}

	log_task_affinities();

	Serial.println("edgeai-predictive-monitor satellite node booted");

	pinMode(LED_BUILTIN, OUTPUT);
}

void loop(void)
{
	digitalWrite(LED_BUILTIN, !digitalRead(LED_BUILTIN));
	delay(HEARTBEAT_PERIOD_MS);
	Serial.println("Heartbeat");
}

