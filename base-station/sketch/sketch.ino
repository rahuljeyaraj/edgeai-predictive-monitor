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
   * Re-enabled 2026-07-14 after the starvation fix: both earlier total-hang
   * reproductions (docs/progress2.md 4.3/4.7) are explained by the spi_link
   * thread running at priority 3 - above Bridge's update thread (5) - where
   * any non-yielding path through its loop (e.g. a latched DMA error flag
   * making the bounded wait break out instantly on every re-arm) starves
   * Bridge and setup() forever: total silent Bridge death, zero router-log
   * errors, exactly the observed signature. Now priority 8 (below Bridge/
   * fuser/mic), with per-arm flag clearing, an error back-off, and error
   * counters in get_spi_link_stats - a worst-case spin can no longer take
   * Bridge down, so the link stays diagnosable either way. */
  spi_link_start();
  /* fuser_start() is intentionally LAST: it starts the continuous notify stream,
   * and every other module's Bridge.provide() registration is a round-trip the
   * stream can crowd out - so all providers register first. NOTE: this ordering
   * only avoids losing provider registrations; it does NOT cure the deeper wedge
   * where the continuous stream desyncs the shared UART's msgpack framer within
   * minutes (root-caused 2026-07-14 - see docs/progress2.md). The real fix is
   * moving this stream off the UART onto the dedicated MCU<->MPU SPI.
   *
   * TEMP: disabled during the SPI bring-up (2026-07-14). After the baud revert
   * to 115200, one ~4.4KB fuser frame takes ~400ms of UART time vs the 64ms
   * epoch (hardware-measured: fus_ovr == fus_frm, fus_avg=407.5ms), so the
   * notify flood outruns the link 6x; once the TX path backs up, fuser spins
   * inside Bridge.notify at priority 6 and starves mic (7), spi_link (8) and
   * loop() (14) permanently (SWD forensics: _kernel.current == fuser, all
   * pipeline fps == 0, mic/spi_link QUEUED-ready forever - docs/progress2.md
   * 4.8). Re-enable when this stream rides the SPI link (tasks 4-5) instead
   * of the UART - do NOT re-enable on UART at 115200. */
  // fuser_start();
}

void loop() {
  digitalWrite(LED_BUILTIN, HIGH);
  delay(HEARTBEAT_PERIOD_MS);
  digitalWrite(LED_BUILTIN, LOW);
  delay(HEARTBEAT_PERIOD_MS);
}
