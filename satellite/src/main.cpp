#include <Arduino.h>

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

void setup(void)
{
	Serial.begin(115200);
	delay(1000); /* let the native-USB CDC console attach before the first log line */

	Serial.println("edgeai-predictive-monitor satellite node booting...");

	if (transport_task_start() < 0) {
		Serial.println("transport_task_start failed");
		return;
	}

	if (rgb_display_task_start() < 0) {
		Serial.println("rgb_display_task_start failed");
		return;
	}

	if (mic_sampler_task_start() < 0) {
		Serial.println("mic_sampler_task_start failed");
		return;
	}

	if (accel_sampler_task_start() < 0) {
		Serial.println("accel_sampler_task_start failed");
		return;
	}

	if (fuser_task_start() < 0) {
		Serial.println("fuser_task_start failed");
		return;
	}

	Serial.println("edgeai-predictive-monitor satellite node booted");

	pinMode(LED_BUILTIN, OUTPUT);
}

void loop(void)
{
	digitalWrite(LED_BUILTIN, !digitalRead(LED_BUILTIN));
	delay(HEARTBEAT_PERIOD_MS);
}
