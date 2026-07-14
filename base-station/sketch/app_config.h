#ifndef APP_CONFIG_H_
#define APP_CONFIG_H_

/*
 * Single place for every module's tunable "policy" knobs - sample rates,
 * bin counts, thread priorities, tick/epoch periods, the Bridge baud, and
 * the benchmark toggle. Mirrors the old repo's mcu/src/app_config.h intent
 * (edgeai-predictive-monitor-unoq), extended here to also centralize thread
 * priorities: priority mistakes (mic's busy-poll starving Bridge, the
 * fuser/Bridge-update-thread tie) were this port's two worst hardware bugs
 * (see docs/PROGRESS.md), so every priority now lives in one file where they
 * can be eyeballed together instead of six.
 *
 * Deliberately NOT moved here: pin assignments, register maps, wire-format
 * constants (chunk size/magic byte), stack sizes, and derived math (e.g.
 * MIC_FFT_LEN = MIC_FFT_BIN_COUNT * 4) - those are implementation details of
 * one file, not cross-module policy, and stay defined locally (computed off
 * this header's base constants where they depend on one).
 */

/* Compile-time switch for the per-stage throughput counters + periodic
 * "get_bench_stats" Bridge summary (mic_sampler.cpp/accel_sampler.cpp/
 * fuser.cpp), ported from the old repo's BENCHMARK_STATS_ENABLED /
 * docs/Sensor_Throughput_Tuning_Plan.md Phase 0. Left on (1) here, unlike
 * the old repo's default-off: this project wants these numbers pollable
 * from the MPU dashboard on an ongoing basis (and eventually from satellite
 * nodes too), not just during a one-off tuning pass, so there's no "done
 * tuning, flip it off" moment the way the old repo had. Flip to 0 to compile
 * all of it out (no counters, no get_bench_stats provider) if that changes. */
#define BENCHMARK_STATS_ENABLED 1

/* --- Sample rates / bin counts ------------------------------------------
 * Both are unique-bin counts (excl. DC), not FFT length - each file derives
 * its own *_FFT_LEN locally (mic: *4, accel: *2 - see mic_sampler.cpp's
 * MIC_FFT_BIN_COUNT comment for why the multiplier differs). */
#define MIC_FFT_BIN_COUNT 512
#define ACCEL_FFT_BIN_COUNT 512
#define ACCEL_ODR_HZ 1600 /* KX134 output data rate - see accel_sampler.cpp's KX134_ODCNTL_OSA_1600HZ */

/* Bridge's 256-byte round-trip ceiling forces both get_*_spectrum views down
 * to an average-pooled bucket count - same value/reasoning in both samplers. */
#define MIC_SPECTRUM_BINS 32
#define ACCEL_SPECTRUM_BINS 32

/* --- Thread priorities ---------------------------------------------------
 * Lower number = higher priority (Zephyr convention). Bridge's own update
 * thread runs at priority 5 (UPDATE_THREAD_PRIORITY in
 * Arduino_RouterBridge's bridge.h) - not ours to change, but the fixed point
 * every value below is chosen relative to.
 *
 * matrix/rgb (3): visible-timing render threads, preempt Bridge's RPC
 * thread so on-screen timing doesn't inherit its scheduling jitter.
 * accel (3): matches matrix/rgb - accel_read_block() blocks on a semaphore
 * every call, yielding the CPU each time, same as their k_msleep() tick.
 * mic (7): BELOW Bridge, not above. Historical/no-longer-load-bearing: back
 * when mic capture was a never-yielding busy-poll, priority 3 (or anything
 * >= Bridge's 5) hung the whole Bridge link outright (see docs/PROGRESS.md's
 * mic_sampler_thread entry). Capture moved to GPDMA1 + k_msleep() between
 * blocks since then, so mic now yields regularly like accel/matrix/rgb and
 * priority 3 would very likely be safe - left at 7 pending the hardware
 * verification called out in that same doc entry, not because it's still
 * required.
 * fuser (6): one band below Bridge, NOT equal to it (5) - at equal priority
 * the continuous ~15.8fps notify stream starved Bridge's own update thread
 * badly enough that every Bridge.provide() provider went "method not
 * available" for as long as the fuser streamed (2026-07-14, see
 * docs/PROGRESS.md). One band below lets Bridge always preempt the stream
 * to service a pending register/call.
 * spi_link (3): the SPI3-slave bring-up spike (docs/progress2.md) - matches
 * matrix/rgb/accel, not Bridge-relative like fuser/mic: it blocks on
 * spi_transceive() waiting for the MPU's clock (SPI_PERIPHERAL mode), the
 * same yield-every-call shape as accel's semaphore wait, and doesn't touch
 * the UART at all so it has no Bridge-starvation risk to budget against. */
#define MATRIX_DISPLAY_THREAD_PRIORITY 3
#define RGB_DISPLAY_THREAD_PRIORITY 3
#define ACCEL_SAMPLER_THREAD_PRIORITY 3
#define MIC_SAMPLER_THREAD_PRIORITY 7
#define FUSER_THREAD_PRIORITY 6
#define SPI_LINK_THREAD_PRIORITY 3

/* --- Tick / epoch periods ------------------------------------------------ */
#define HEARTBEAT_PERIOD_MS 500     /* loop()'s LED_BUILTIN blink period */
#define MATRIX_DISPLAY_TICK_MS 20
#define RGB_DISPLAY_TICK_MS 20
#define FUSER_EPOCH_MS 64           /* ~15.6 fused frames/s - see fuser.cpp.
                                     * NOTE: this rate's continuous ~65KB/s notify
                                     * stream over the shared 1Mbaud UART is what
                                     * recurringly wedges the Bridge link (msgpack
                                     * framer desync; root-caused 2026-07-14, see
                                     * docs/progress2.md). Lowering it to ~10fps
                                     * only extended time-to-wedge (~5->~15min),
                                     * it did NOT fix it - so the rate is NOT the
                                     * mitigation lever and is left at the native
                                     * 64ms. The real fix is moving this stream off
                                     * the UART onto the dedicated MCU<->MPU SPI
                                     * (docs/progress2.md); that removes the byte-
                                     * pressure entirely and keeps full float32. */

/* --- Bridge link ----------------------------------------------------------
 * MCU<->MPU serial baud (Serial1 <-> /dev/ttyHS1). MUST match the router's
 * --serial-baudrate on the Linux side, set in the systemd drop-in (see
 * base-station/provision-baud.sh + docs/PROGRESS.md) - a mismatch silently
 * breaks the whole link. Raised from the library default 115200 because the
 * fuser pushes the full-resolution float32 spectrum (~64 KB/s at 15.6 Hz),
 * which 115200 (~11.5 KB/s) cannot carry.
 *
 * Why 1000000 specifically - it satisfies two independent constraints:
 *   1. EXACT divisor. 1000000 = 16MHz Serial1 kernel clock / 16 exactly, so
 *      the STM32 UART baud is precise. 921600 is NOT an exact divisor here -
 *      tried, the MCU went completely silent even on a clean reboot (the
 *      core's Serial1 doesn't realize the needed fractional divider
 *      accurately). Stick to exact-divisor rates (1000000, 2000000, ...); do
 *      NOT assume a "standard" rate like 921600/115200 is safe on the MCU
 *      side.
 *   2. OVER16 RX margin. At <=1MHz the STM32 USART uses 16x oversampling; at
 *      ~2MHz it drops to OVER8, whose thinner RX margin made the
 *      router->MCU direction unreliable - at 2000000, MCU->MPU notifies
 *      streamed perfectly but round-trip Bridge.provide() registrations and
 *      MPU->MCU calls intermittently failed ("method not available"),
 *      breaking the LED matrix/RGB providers and the sampler info calls.
 *      1000000 keeps OVER16 and fixed it, while still carrying the full-res
 *      frame at 15.6 Hz (~64% link util).
 *
 * Bridge.begin(BRIDGE_BAUD) is idempotent - only the first caller's baud
 * actually takes effect - but every module passes BRIDGE_BAUD so the
 * effective baud doesn't depend on setup() ordering. */
#define BRIDGE_BAUD 1000000

#endif /* APP_CONFIG_H_ */
