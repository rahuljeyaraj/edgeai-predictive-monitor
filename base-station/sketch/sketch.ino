/*
 * Heartbeat: onboard LED_BUILTIN, blinked forever to confirm the sketch
 * flashed and is actually running. Mirrors the old Zephyr app's heartbeat
 * thread (edgeai-predictive-monitor-unoq/mcu/src/main.c), before any of the
 * sensor/fuser/transport threads are ported over.
 */
#define HEARTBEAT_PERIOD_MS 500

#include "accel_sampler.h"
#include "matrix_display.h"
#include "mic_sampler.h"
#include "rgb_display.h"

void setup() {
  pinMode(LED_BUILTIN, OUTPUT);
  matrix_display_start();
  rgb_display_start();
  mic_sampler_start();
  /* TEMPORARY: accel_sampler_start() call moved into rgb_display_start()
   * itself, to test whether the caller context (sketch.ino's setup()) is
   * what matters, vs. the callee's own compiled form. */
  __asm__ volatile("");
}

void loop() {
  digitalWrite(LED_BUILTIN, HIGH);
  delay(HEARTBEAT_PERIOD_MS);
  digitalWrite(LED_BUILTIN, LOW);
  delay(HEARTBEAT_PERIOD_MS);
}
