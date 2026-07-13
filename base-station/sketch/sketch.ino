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
  /* mic_sampler_start() MUST be called last: it starts a priority-7,
   * never-yielding busy-wait capture thread (see mic_sampler.cpp), which is a
   * higher priority than this main/setup thread (CONFIG_MAIN_THREAD_PRIORITY=14
   * on this core). The instant that thread is created it preempts setup() and,
   * having no yield point on its success path, never hands the CPU back to a
   * lower-priority thread - so nothing sequenced after mic_sampler_start() here
   * (or in loop()) ever runs again. matrix/rgb/accel all start priority-3
   * threads that k_msleep()/block every iteration, so they coexist fine; they
   * just have to be brought up before mic monopolizes the CPU. This ordering
   * dependency is exactly what blocked the accel port for a whole session -
   * see docs/PROGRESS.md's accel_sampler_thread entry. */
  matrix_display_start();
  rgb_display_start();
  accel_sampler_start();
  mic_sampler_start();
}

void loop() {
  digitalWrite(LED_BUILTIN, HIGH);
  delay(HEARTBEAT_PERIOD_MS);
  digitalWrite(LED_BUILTIN, LOW);
  delay(HEARTBEAT_PERIOD_MS);
}
