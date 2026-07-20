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
 * fuser (6): one band below Bridge, NOT equal to it (5) - back on the old UART
 * transport, at equal priority the continuous ~15.8fps notify stream starved
 * Bridge's own update thread badly enough that every Bridge.provide() provider
 * went "method not available" for as long as the fuser streamed (2026-07-14,
 * see docs/PROGRESS.md). One band below keeps Bridge able to preempt it. Since
 * 2026-07-15 the fuser no longer streams over Bridge at all - it stages frames
 * for the SPI transport (fuser.cpp) - so the flood is gone, but 6 (below Bridge,
 * above mic) is still the right slot for a steady per-epoch producer.
 * spi_link (6): BELOW Bridge (5), ABOVE mic (7). Two constraints, both learned
 * the hard way on hardware (docs/progress2.md 4.8):
 * - Must be below Bridge: it ran at 3 (the matrix/rgb/accel tier) through both
 *   failed SPI3-slave bring-up attempts, where any non-yielding path through its
 *   loop starved Bridge (5) and main/setup() (14) forever - total silent Bridge
 *   death with zero router-log errors, the exact hang signature of
 *   docs/progress2.md 4.3/4.7. Nothing about the slave path needs to preempt
 *   Bridge (GPDMA feeds the SPI FIFO regardless of the scheduler), and staying
 *   below it keeps get_spi_link_stats readable through any spi_link misbehaviour.
 * - Must be above mic: at 8 (below mic's 7) it was starved outright - mic
 *   remains the heaviest continuous compute in the system and anything below it
 *   inherits whatever CPU mic leaves over.
 * fuser and spi_link share priority 6 and coexist fine: both are cooperative
 * (each k_msleep()s regularly and yields), fuser only stages (sub-1ms) while
 * spi_link only waits on DMA, and the one lock they share (the pending-frame
 * mutex) is priority-inheriting so Bridge's spi_arm can never be blocked on it
 * for long. */
#define MATRIX_DISPLAY_THREAD_PRIORITY 3
#define RGB_DISPLAY_THREAD_PRIORITY 3
#define ACCEL_SAMPLER_THREAD_PRIORITY 3
#define MIC_SAMPLER_THREAD_PRIORITY 7
#define FUSER_THREAD_PRIORITY 6
#define SPI_LINK_THREAD_PRIORITY 6

/* --- Tick / epoch periods ------------------------------------------------ */
#define HEARTBEAT_PERIOD_MS 500     /* loop()'s LED_BUILTIN blink period */
#define MATRIX_DISPLAY_TICK_MS 20
#define RGB_DISPLAY_TICK_MS 20
#define FUSER_EPOCH_MS 64           /* ~15.6 fused frames/s - see fuser.cpp. This is
                                     * now the frame *production* rate (how often the
                                     * fuser stages a fresh frame for the SPI
                                     * transport); the MPU's pull rate over SPI is
                                     * independent. The old concern here - that this
                                     * rate's ~65KB/s stream over the shared Bridge
                                     * UART wedged the link's msgpack framer
                                     * (root-caused 2026-07-14) - no longer applies:
                                     * the bulk stream moved off the UART onto the
                                     * dedicated MCU<->MPU SPI (spi_link.{h,cpp}),
                                     * which removes the byte-pressure entirely and
                                     * keeps full float32. */

/* Data-collection build toggle: 0 (default) is the normal fused SPECTRUM
 * stream fuser.cpp has always sent. 1 rebuilds fuser.cpp to instead stream
 * raw, un-FFT'd time-series windows (3 accel axes kept separate + raw mic)
 * for offline experimentation (docs/SENSOR_TELEMETRY_FRAME_PLAN.md) - not
 * meant to run permanently, flip back to 0 and reflash once a rig capture
 * session is done. See fuser.cpp's raw-mode block for the frame layout. */
#define FUSER_RAW_CAPTURE_MODE 0

/* Raw-mode-only epoch. One accel raw window (1024 samples @ 1600Hz ODR)
 * takes ~640ms to fill; a faster epoch than that would resend the same
 * window twice (a byte-for-byte duplicate landing in two different labeled
 * capture files - worse than window overlap, straightforward leakage if the
 * dupe crosses a train/test split). 1000ms gives margin and keeps the raw
 * frame's data rate (~20KB every other epoch, accel/mic alternate - see
 * fuser.cpp) far under the SPI link's budget; no reason to rush capture. */
#define FUSER_RAW_EPOCH_MS 1000

/* Normal-mode (FUSER_RAW_CAPTURE_MODE=0) time-domain piggyback: sending the
 * accel x/y/z + mic decimated time-series sections on every fused frame
 * would drag the whole frame's SPI pull time down with them (RPC-round-trip-
 * per-chunk dominated, not raw bit rate - see spi_reader.py's own reasoning),
 * which would also slow the anomaly score and spectrum charts riding the
 * same frame. Instead they ride only every Nth frame
 * (docs/CHART_CLUTTER_PLAN.md S1's collapsed "Raw signals" panel doesn't
 * need every-frame freshness); the fast path (spectra + scalar tiles) stays
 * on every frame. See fuser.cpp's fuser_epoch_count gating. */
#define FUSER_TIME_SERIES_EVERY_N 4

/* Time-domain sections are decimated (simple stride) to this many samples
 * before transmission - a chart line doesn't need the full FFT window
 * length (1024 accel / 2048 mic) to read as smooth, and this keeps the
 * piggybacked frame's size (and therefore its SPI pull time) bounded
 * regardless of which sensor's native window is longer. */
#define FUSER_TS_DECIMATED_SAMPLES 256

/* --- Bridge link ----------------------------------------------------------
 * MCU<->MPU serial baud (Serial1 <-> /dev/ttyHS1). MUST match the router's
 * --serial-baudrate on the Linux side (the stock per-board systemd generator
 * drop-in, /var/lib/arduino-router/config/10-imola.conf) - a mismatch
 * silently breaks the whole link.
 *
 * Was reverted to the library default 115200 on 2026-07-14 after the bulk
 * fuser stream moved off this link onto the dedicated SPI bus (see
 * docs/progress2.md section 2-3) - the earlier 1M/2M raise had been chasing
 * a msgpack framing-desync wedge that was root-cause unrelated to baud.
 * With only RPC/control traffic left on the UART, 115200 was ample, but
 * this link is otherwise capable of much more.
 *
 * Root-caused and raised to 500000 on 2026-07-20 (was 115200). Root cause
 * has two layers - a fully-explained one and a partially-characterized one:
 *
 * 1) THE BIG ONE, fully explained: /dev/ttyHS1's Linux driver (`ttyHS1...
 *    is a MSM`, `/sys/class/tty/ttyHS1/uartclk` = 32000000) derives baud
 *    from a 32 MHz reference with classic 16x-oversampling, i.e.
 *    divisor = 32e6 / (16 * baud) = 2e6 / baud, ROUNDED TO AN INTEGER.
 *    That divisor collapses at exactly the rates that failed hardest:
 *      - 4000000 -> divisor 0.5, can't round down to a real divisor without
 *        halving the actual baud to 2000000 against an MCU genuinely
 *        driving LPUART1 at 4M - a straight 2x mismatch, so the router
 *        fails to decode a single byte from MCU boot onward (confirmed:
 *        2/2 clean reflash+recovery cycles, zero providers ever registered).
 *      - 2000000 -> divisor exactly 1.0, the lowest a 16x-oversampled UART
 *        can run - right at/beyond where these designs lose sampling
 *        margin. Boots looking clean (one-off Bridge.call succeeds!) but a
 *        real back-to-back soak wedges it solid (0/50, every call eating
 *        the full 10s timeout) - a single ad-hoc call after reflash is NOT
 *        a valid stability check on this link, it will lie to you.
 *      - 1000000 -> divisor exactly 2.0, still on the losing side of the
 *        margin: passes a clean 20-call burst, then wedges on the next
 *        burst after just ~90s. Reconfirmed independently later the same
 *        session (fresh reflash, board reboot + USB reconnect in between):
 *        identical signature both times - clean burst1, dead on the very
 *        first call of burst2. Fails faster/harder than 666667 (divisor 3),
 *        consistent with the monotonic margin-vs-divisor pattern.
 *      - 666667 -> divisor exactly 3.0 (2e6/3): passed a 20+90s-idle+20
 *        soak clean, but WEDGED on a longer 100+180s-idle+100 exposure -
 *        divisor 3 has more margin than 1-2 but still not enough.
 *      - 500000 -> divisor exactly 4.0: clean on every test run, including
 *        one LONGER than the exposure that broke 666667 (50+300s-idle+50,
 *        zero failures). This is the value in use now.
 *    (STM32U585 LPUART1 is comfortably not the bottleneck at any of these -
 *    kernel clock SYSCLK/1=160MHz, BRR divides evenly at every rate tested,
 *    and its own baud ceiling per RM0456 (ck_lpuart/3) is ~53 Mbaud.)
 *
 * 2) THE RESIDUAL RISK, NOT fully characterized: divisor precision alone
 *    doesn't explain everything - 666667 (a mathematically clean divisor)
 *    still wedged given enough elapsed time, meaning failure likelihood
 *    scales with baud even away from the worst divisor edge cases, not
 *    just jumps to zero above divisor 2. Ruled out this session: mic_sampler
 *    (isolation test - commented out mic_sampler_start(), wedge still
 *    reproduced at 1M with mic fully disabled); any application-level
 *    irq_lock()/interrupt-masking (grepped the whole sketch/ tree, zero
 *    hits - rgb_display.cpp's PWM+DMA path explicitly documents "no
 *    irq_lock, no busy-wait"); STM32-side hardware RX errors (SWD-read
 *    LPUART1's ISR register live mid-wedge - PE/FE/NE/ORE all clear,
 *    though the read landed after the fact so this isn't conclusive for a
 *    transient error). Leading unconfirmed hypothesis: physical
 *    signal-integrity marginality on this specific link (this board has a
 *    prior loose-connector incident, see mic/SAI capture bug notes) -
 *    higher baud means less voltage/timing margin per bit against the same
 *    absolute noise, and the failures pattern like a rare, roughly
 *    time-distributed event rather than anything tied to Bridge call load
 *    specifically (matrix/rgb/accel/spi_link/fuser all run continuously
 *    regardless of Bridge traffic, so "idle" bursts in testing were never
 *    actually idle from the MCU's perspective). Not resolved further this
 *    session - would need a scope on the physical UART lines to confirm.
 *
 * Don't raise this past 500000 without a REAL soak test first: burst of
 * calls, idle gap of several minutes (not seconds), another burst - a
 * clean boot or a single successful call proves nothing on this link.
 *
 * Bridge.begin(BRIDGE_BAUD) is idempotent - only the first caller's baud
 * actually takes effect - but every module passes BRIDGE_BAUD so the
 * effective baud doesn't depend on setup() ordering. */
#define BRIDGE_BAUD 500000

#endif /* APP_CONFIG_H_ */
