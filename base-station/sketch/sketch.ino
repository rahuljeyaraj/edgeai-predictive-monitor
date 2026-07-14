/*
 * Heartbeat: onboard LED_BUILTIN, blinked forever to confirm the sketch
 * flashed and is actually running. Mirrors the old Zephyr app's heartbeat
 * thread (edgeai-predictive-monitor-unoq/mcu/src/main.c), before any of the
 * sensor/fuser/transport threads are ported over. HEARTBEAT_PERIOD_MS lives
 * in app_config.h.
 */

#include "accel_sampler.h"
#include "app_config.h"
#include "bench.h"
#include "fuser.h"
#include "matrix_display.h"
#include "mic_sampler.h"
#include "rgb_display.h"

void setup() {
  pinMode(LED_BUILTIN, OUTPUT);
  /* mic no longer has to run last. It used to: its capture thread was a
   * never-yielding busy-wait at a priority above this main/setup thread
   * (CONFIG_MAIN_THREAD_PRIORITY=14), so it starved setup()/loop() the instant
   * it was created. Capture moved to GPDMA1 (mic_sampler.cpp) - the thread now
   * k_msleep()s between blocks and yields, so the constraint is gone. */
  matrix_display_start();
  rgb_display_start();
  accel_sampler_start();
  mic_sampler_start();
  bench_start();
  /* fuser_start() is intentionally LAST: it starts the continuous notify stream,
   * and every other module's Bridge.provide() registration is a round-trip the
   * stream can crowd out - so all providers register first. NOTE: this ordering
   * only avoids losing provider registrations; it does NOT cure the deeper wedge
   * where the continuous stream desyncs the shared UART's msgpack framer within
   * minutes (root-caused 2026-07-14 - see docs/progress2.md). The real fix is
   * moving this stream off the UART onto the dedicated MCU<->MPU SPI. */
  fuser_start();
}

void loop() {
  digitalWrite(LED_BUILTIN, HIGH);
  delay(HEARTBEAT_PERIOD_MS);
  digitalWrite(LED_BUILTIN, LOW);
  delay(HEARTBEAT_PERIOD_MS);
}
