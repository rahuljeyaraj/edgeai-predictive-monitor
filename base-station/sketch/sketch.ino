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
#include "spi_link.h"

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
  /* spi_link_start() registers a "get_spi_link_stats" Bridge provider
   * (register-level SPI3-slave + GPDMA1, see spi_link.cpp) - belongs here,
   * before fuser_start(), same as the other providers.
   * TEMP: disabled again. Re-tested 2026-07-14 on a confirmed-clean link
   * (3 clean Bridge.call()s, zero framing-desync errors right before
   * enabling) and it reproduced a total Bridge hang - same signature as the
   * earlier SPI1/Zephyr-API attempt (4.3): zero "invalid packet" desync
   * errors in the router log, every provider (including ones registered
   * earlier in setup(), e.g. get_mic_info) stops responding, not just
   * get_spi_link_stats. So the register-level rewrite (4.5) does NOT fix the
   * hang either - it is not confined to the Zephyr SPI1 path as hoped. Root
   * cause still open; see docs/progress2.md 4.7. Do not re-enable without a
   * new diagnostic angle (get_spi_link_stats' checkpoint counter cannot be
   * read once the hang happens, since Bridge itself is dead by then). */
  // spi_link_start();
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
