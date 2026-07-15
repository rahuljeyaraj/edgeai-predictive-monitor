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
  /* spi_link_start() registers the "spi_arm"/"get_spi_link_stats" Bridge
   * providers and brings up SPI3-slave + GPDMA1 (spi_link.cpp) - MUST come
   * before fuser_start() so the transport is ready when the fuser begins staging
   * frames. Priority 6 (below Bridge's update thread at 5): both earlier
   * total-hang reproductions (docs/progress2.md 4.3/4.7) were the spi_link thread
   * running at priority 3, above Bridge, where any non-yielding path through its
   * loop starved Bridge and setup() forever (total silent Bridge death, zero
   * router-log errors). Now below Bridge, with per-arm flag clearing and an
   * error back-off, a worst-case spin can no longer take Bridge down. */
  spi_link_start();
  /* fuser_start() is intentionally LAST: every other module's Bridge.provide()
   * registration is a round-trip, so all providers register first.
   *
   * Re-enabled 2026-07-15: the fuser no longer streams over the shared Bridge
   * UART. It now hands each frame to spi_link_stage_frame() and the bulk data
   * rides the dedicated MCU<->MPU SPI bus (tasks 4-5, docs/progress2.md) - which
   * removes both failure modes that forced it off: the msgpack framer desync
   * that wedged the UART (section 2), and the priority-6 busy-spin inside
   * Bridge.notify at 115200 baud that starved mic/spi_link/loop() (4.8). Staging
   * is a sub-1ms non-blocking memcpy+CRC, so nothing here floods anything. */
  fuser_start();
}

void loop() {
  digitalWrite(LED_BUILTIN, HIGH);
  delay(HEARTBEAT_PERIOD_MS);
  digitalWrite(LED_BUILTIN, LOW);
  delay(HEARTBEAT_PERIOD_MS);
}
