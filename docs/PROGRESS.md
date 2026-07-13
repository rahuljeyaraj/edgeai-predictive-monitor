# Project Progress

Living status doc for `edgeai-predictive-monitor`. Edit this as work progresses; paste it into new
Claude Code sessions to bring them up to speed.

## Why this repo exists

Built for the [Arduino Physical AI Challenge India 2026](https://robu.in/arduino-physical-ai-challenge-india-2026/).

The original implementation, `edgeai-predictive-monitor-unoq`, drove the UNO Q's MCU directly with a
raw Zephyr/west application (`west build -b arduino_uno_q`, flashed over OpenOCD via adb). That
bypasses the Arduino App Lab framework the competition is built around, which risked disqualification.
This repo (`edgeai-predictive-monitor`) is a from-scratch port that sticks to the App Lab app
structure (`app.yaml` + `python/` + `sketch/`) so everything builds/deploys the way the competition
expects.

Porting is happening step by step, MCU side first.

## Architecture

- **`base-station/`** — the Arduino UNO Q App Lab app.
  - `python/main.py` — Linux (QRB2210/MPU) side, runs via the App framework.
  - `sketch/sketch.ino` — MCU (STM32U5) side, Arduino API sketch built against the `arduino:zephyr`
    core (FQBN `arduino:zephyr:unoq`). Kept to just `setup()`/`loop()` + heartbeat; each ported
    interface gets its own `<name>.h`/`<name>.cpp` pair in `sketch/` (standard Arduino multi-file
    sketch — the build picks up every `.h`/`.cpp`/`.ino` in the directory automatically), e.g.
    `matrix_display.h`/`matrix_display.cpp` for the LED matrix.
  - `tests/` — MPU-side verification scripts, ported one at a time from the old repo's `mpu/tests/`
    as each interface lands (e.g. `display_matrix_test.py`). Pushed automatically as part of
    `base-station/`, run manually on-device against the running app.
  - `deploy.sh` — pushes the app to the board over adb and (re)starts it via `arduino-app-cli`.
- **`satellite/`** — ESP32-S3 satellite node. Not started yet.

## Progress log

- **2026-07-13** — Ported the MCU heartbeat only from the old repo's
  [`mcu/src/main.c`](../../edgeai-predictive-monitor-unoq/mcu/src/main.c): `sketch/sketch.ino` blinks
  `LED_BUILTIN` on a 500ms period. Confirmed working on hardware via `adb push` +
  `arduino-app-cli app start`. Wrote `base-station/deploy.sh` to script that push/start/log cycle.
  Note: old code blinked a specific status LED (LED3 green, PH11) reached via a raw devicetree label —
  not accessible from a plain Arduino sketch, so this uses `LED_BUILTIN` (UNO Q's onboard RGB LED, red
  channel) as the equivalent "firmware is alive" indicator instead.

- **2026-07-13** — Ported `matrix_display_thread` (LED matrix), verified working on hardware. Lives in
  `base-station/sketch/matrix_display.h`/`matrix_display.cpp` (see that file's header comment for the
  full rationale — this entry is the summary). Behaviorally matches the old repo's
  [`threads/matrix_display_thread.c`](../../edgeai-predictive-monitor-unoq/mcu/src/threads/matrix_display_thread.c)
  + [`hal_display_matrix.h`](../../edgeai-predictive-monitor-unoq/mcu/src/hal/hal_display_matrix.h) +
  [`drivers/led_matrix.c`](../../edgeai-predictive-monitor-unoq/mcu/src/drivers/led_matrix.c) contract
  (settable text + scroll speed, rendered by a dedicated priority-3 tick thread, same
  `font_5x7`/scroll-cycle-with-trailing-blank-gap algorithm, copied verbatim), but two things
  underneath are different, specific to being back on App Lab:
  - **No hand-rolled charlieplex driver.** The old repo's `drivers/led_matrix.c` bit-banged the matrix's
    GPIOF pins directly and drove a TIM17 counter ISR itself, because that repo had replaced Arduino's own
    firmware/loader with a from-scratch Zephyr build, so the board's own matrix support wasn't linkable.
    This repo builds through the real `arduino:zephyr` App Lab toolchain, where that support *is*
    available: `Arduino_LED_Matrix` (bundled with the core), specifically its `loadPixels()` — confirmed
    same hardware via `Arduino_LED_Matrix.h`'s `canvasWidth`/`canvasHeight` (13/8) matching the old repo's
    `HAL_MATRIX_COLS`/`HAL_MATRIX_ROWS`. (`ArduinoGraphics`'s text API — `beginText()`/`endText()` — was
    tried first instead of a hand-rolled tick/font; reverted, see below.)
  - **No hand-rolled wire protocol either.** The old repo's not-yet-ported `transport_thread` was going to
    carry a custom binary framing over a raw UART (`mcu/src/frame_codec/`), because that repo had also
    masked out App Lab's `arduino-router`/Bridge service entirely. This repo keeps that service, so the
    MPU/Python side calls `Bridge.call("set_matrix_text", text)` /
    `Bridge.call("set_matrix_scroll_speed", str(scroll_speed_ms))` (`arduino.app_utils.Bridge`) straight
    into the sketch's `Bridge.provide(...)` handlers — no separate transport thread needed for this
    interface. **This changes the plan for the `transport_thread` item below**: it's not going to be one
    monolithic port — each interface will likely just register its own Bridge provider/caller directly,
    the way this one does.
  - Thread priority kept at **3**, per user instruction, to match the old repo's `rgb_display_thread`
    priority (`RGB_DISPLAY_THREAD_PRIORITY` in
    [`threads/rgb_display_thread.c`](../../edgeai-predictive-monitor-unoq/mcu/src/threads/rgb_display_thread.c)) —
    a visible-timing render thread should preempt Bridge's own background RPC thread (priority 5), same
    rationale as that file's own comment, since `rgb_display_thread` will land at the same priority when
    it's ported.

  **Two findings from hardware testing, both likely to recur when porting later interfaces:**
  - **Integer-typed Bridge/RPC parameters are broken on this board's `Arduino_RPClite` build.** Any
    integer argument to a `Bridge.provide()` handler — tried as part of a two-arg
    `(String, uint32_t)` provider, and again as a lone single-arg `uint32_t` provider — was rejected at
    runtime with `"Wrong type parameter in position: N"`, even though the exact wire bytes were
    confirmed correct by dumping `msgpack.packb()`'s raw output on-device and manually decoding them
    (fixarray headers, fixstr, positive-fixint all exactly as expected). String arguments decoded
    correctly every time. Workaround, applied here: send every RPC parameter as a `String` and parse
    with `.toInt()` on the sketch side (`matrix_display_set_scroll_speed` in `matrix_display.cpp`).
    **Worth checking again before assuming this is still true** — if a Bridge/RPClite version bump ever
    happens, retest with a plain integer parameter first.
  - **`ArduinoGraphics`'s bundled fonts (`Font_5x7`, `Font_4x6`) don't use the matrix's full 8-row
    height**, and neither does the alternative tried (`github.com/dhepper/font8x8`, a genuinely 8-byte-
    per-glyph font): in both, the bottom row is reserved for descenders (g/j/p/q/y) and is blank for
    ordinary text, and `Font_5x7`/the old repo's own `font_5x7` don't cover row 8 at all. Switching to
    the 8-wide `font8x8` font made scrolling text visibly wider for zero height gain, so it was
    reverted — `matrix_display.cpp` uses the old repo's exact original 5-wide `font_5x7` again. A
    display that visibly fills all 8 rows would need a hand-designed, non-standard font — not
    attempted.
  - Test script ported: `base-station/tests/display_matrix_test.py`, adapted from the old repo's
    [`mpu/tests/display_matrix_test.py`](../../edgeai-predictive-monitor-unoq/mpu/tests/display_matrix_test.py)
    to call `Bridge.call(...)` instead of hand-framing UART bytes. No separate `adb push` needed — it's
    part of `base-station/`, so `deploy.sh` already pushes it. `arduino.app_utils` is only importable
    inside the app's own container (`arduino-app-cli` runs each App Lab app in a Docker container named
    `<app-name>-main-1`), not the board's bare `python3` — run it with:
    `adb shell "docker exec edgeai-predictive-monitor-base-station-main-1 python3 /app/tests/display_matrix_test.py"`
    while the app is running. **Confirmed working on hardware** (2026-07-13): static text, scrolling
    text, and clear all render correctly at the expected size/speed.

- **2026-07-13** — Ported `rgb_display_thread` (external WS2812B 8-LED ring on D4/PA12), verified
  working on hardware (CONST/BREATHE/STROBE all confirmed by eye). Lives in
  `base-station/sketch/rgb_display.h`/`rgb_display.cpp` (see that file's header comment for the full
  rationale). Behaviorally matches the old repo's
  [`threads/rgb_display_thread.c`](../../edgeai-predictive-monitor-unoq/mcu/src/threads/rgb_display_thread.c)
  + [`hal_display_rgb.h`](../../edgeai-predictive-monitor-unoq/mcu/src/hal/hal_display_rgb.h) +
  [`drivers/rgb_ws2812.c`](../../edgeai-predictive-monitor-unoq/mcu/src/drivers/rgb_ws2812.c) contract
  (CONST/BREATHE/STROBE, packed 0xRRGGBB, same sine-breathe/square-strobe math, same tick-thread
  priority 3 / 20ms period), but the actual WS2812 transmission is fundamentally different, and hit one
  significant bug worth remembering:
  - **No `zephyr,led_strip` device.** The old repo drove the ring over SPI1 MOSI via Zephyr's
    `worldsemi,ws2812-spi` `led_strip` binding — a devicetree node its own board overlay added, which
    needed a from-scratch Zephyr build. App Lab's `arduino:zephyr` toolchain gives a sketch no way to
    add devicetree nodes, and unlike the LED matrix there's no bundled Arduino-native library for an
    external WS2812 strip either — so `rgb_display.cpp` bit-bangs the WS2812 protocol directly on
    D4/PA12 (the same pin the old repo's ring is wired to) instead of going through a `led_strip` device.
  - **First bit-bang attempt produced constant solid white regardless of any requested color/mode** —
    confirmed on hardware. Root cause: toggling the pin via Zephyr's `gpio_pin_set_dt()` (the same
    `arduino_pins[]` table `digitalWrite()` uses) compiles fine, but its driver-dispatch overhead alone
    exceeds a WS2812 "0" bit's entire ~0.4us high-time budget, so every bit's physical high pulse — 0 or
    1 — ends up longer than the decode threshold and reads back as 1, collapsing every byte to 0xFF.
    Fixed by toggling the pin with direct register writes instead: STM32Cube's
    `LL_GPIO_SetOutputPin()`/`LL_GPIO_ResetOutputPin()` (`stm32u5xx_ll_gpio.h`, `__STATIC_INLINE` — a
    single BSRR/BRR store, no driver-API call) — the same primitive Adafruit_NeoPixel's own STM32
    backend uses for exactly this reason. **Worth knowing for any future timing-critical GPIO work on
    this board**: the full STM32Cube HAL/LL tree (and CMSIS device headers) ships inside this core's
    `llext-edk` include path (`modules/hal/stm32/stm32cube/stm32u5xx/...`) and is directly includable
    from a sketch (`#define STM32U585xx` before `#include <stm32u5xx.h>` +
    `#include <stm32u5xx_ll_gpio.h>`) — confirmed by test-compiling against it — so direct
    register-level access is available without any raw hard-coded peripheral addresses. Bit timing
    itself uses `k_cycle_get_32()` (confirmed `CONFIG_SYS_CLOCK_HW_CYCLES_PER_SEC` == 160000000 on this
    board), with the whole per-frame transmission (192 bits @ 1.25us =~ 240us for 8 pixels) wrapped in
    `irq_lock()`/`irq_unlock()` so no ISR/reschedule can stretch a bit out of tolerance.
  - **Known current limitation**: this bit-bang blocks all interrupts (not just other threads) for the
    full ~240us of each frame, once per `RGB_DISPLAY_TICK_MS` (20ms) — a ~1.2% duty cycle of IRQs-off.
    Harmless today (nothing else on this board needs sub-millisecond response), but it's CPU time this
    thread is fully occupying rather than handing off to hardware — see the SPI1 idea below for the fix
    if a future thread ever needs tighter latency.
  - Bridge.provide() takes one combined String (`"RRGGBB,mode,period_ms"`), not
    `matrix_display.cpp`'s two-separate-single-String-providers split: color/mode/period should latch
    together atomically, and the known Arduino_RPClite integer-argument bug (see `matrix_display.cpp`'s
    own comment, still true) still rules out a native numeric argument.
  - Test script ported: `base-station/tests/display_rgb_test.py`, adapted from the old repo's
    [`mpu/tests/display_rgb_test.py`](../../edgeai-predictive-monitor-unoq/mpu/tests/display_rgb_test.py)
    to call `Bridge.call("set_rgb", ...)` instead of hand-framing UART bytes. Run the same way as the
    matrix test (see that entry above), swapping in `display_rgb_test.py`. **Confirmed working on
    hardware** (2026-07-13): CONST red/green/blue, BREATHE yellow, and STROBE magenta all rendered
    correctly.

- **2026-07-13** — Ported `mic_sampler_thread` (INMP441 I2S microphone), verified
  working on hardware: `get_mic_info`/`get_mic_spectrum` respond correctly and
  the returned spectrum visibly changes bucket-to-bucket, consistent with a
  real, live capture rather than stuck/garbage data. Lives in
  `base-station/sketch/mic_sampler.h`/`mic_sampler.cpp` (see that file's header
  comment for the full rationale - this entry is the summary). Behaviorally
  matches the old repo's
  [`threads/mic_sampler_thread.c`](../../edgeai-predictive-monitor-unoq/mcu/src/threads/mic_sampler_thread.c)
  + [`hal_audio.h`](../../edgeai-predictive-monitor-unoq/mcu/src/hal/hal_audio.h) +
  [`drivers/audio_i2s.c`](../../edgeai-predictive-monitor-unoq/mcu/src/drivers/audio_i2s.c)
  contract (2048-sample/96kHz mono blocks, 2048-pt FFT, drop DC/keep
  Nyquist/no-mirroring magnitude extraction, keep only the first
  MIC_FFT_BIN_COUNT=512 of the 1024 unique bins since without an external
  MCLK the INMP441 only has valid audio below Fs/4=24kHz - same hardware,
  same pins: SAI1_A, SCK=PB10/FS=PB9/SD=PC1, no MCLK), but everything
  underneath is different, specific to being back on App Lab:
  - **No Zephyr `i2s` driver/devicetree node.** Like the WS2812 ring, this
    board's shipped App Lab firmware has no `sai1_a` node compiled in and a
    sketch has no devicetree/pinctrl hook - confirmed via the installed
    core's bundled library list (Arduino_LED_Matrix/RTC/SocketWrapper/CAN/
    SPI/Wire/ea_malloc only, no I2S/PDM/audio library at all). So SAI1_A is
    driven directly via STM32Cube LL/CMSIS registers: RCC (PLL2 tuned
    exactly like the old repo's overlay - HSE/div-m=1/mul-n=24/div-p=5 ->
    76.8MHz, MCKDIV=25 for 96kHz/32-bit-frame), GPIO (AF13 on PB9/PB10/PC1,
    confirmed against the old repo's own generated `zephyr.dts` pinctrl
    resolution for this exact chip variant, `stm32u585aiixq`), then SAI1_A's
    CR1/CR2/FRCR/SLOTR by hand - there's no dedicated STM32U5 LL driver for
    SAI (only the full HAL, and only its headers ship in this core, not the
    linkable `.c`), so these are the same register values
    `HAL_SAI_InitProtocol(SAI_I2S_STANDARD, ...)` would compute, written
    directly. `SLOTEN` is set to slot 0 only (the old repo had to read and
    discard the right slot in software; telling the hardware to only ever
    push the left slot into the FIFO skips that step entirely).
  - **No DMA.** The old repo was GPDMA1-backed (channel 2, request/slot 36)
    and blocked on a mem-slab queue - free for the scheduler while waiting.
    Hand-programming GPDMA1's linked-list descriptors would have meant a
    second full register-level subsystem on top of SAI1 itself, so this
    polls SAI1's FIFO request flag (`SR.FREQ`) and reads one 16-bit sample
    from `DR` at a time instead - much less code, at the cost of the
    ~21.3ms/block capture loop being a genuine busy-wait (see "Known current
    limitation" in `mic_sampler.cpp`'s header comment, and the priority bug
    below).
  - **No CMSIS-DSP.** The old repo's `arm_rfft_fast_f32()` came from Zephyr's
    own module tree in a from-scratch build; no `arm_math.h` exists anywhere
    in this core. Replaced with a hand-rolled standard iterative radix-2
    in-place Cooley-Tukey complex FFT (real input, precomputed twiddle
    tables), same 2048-point size and magnitude extraction as the old
    repo's own `mic_fft_magnitude()`.
  - **Bridge's hard 256-byte round-trip message ceiling** (`DEFAULT_RPC_BUFFER_SIZE`
    in `Arduino_RPClite/src/request.h`, confirmed on-device) rules out
    sending the full 512-bin spectrum over Bridge at all, whether as binary
    (2048 bytes) or CSV text (~3KB) - nowhere close. `get_mic_spectrum`
    exposes a further average-pooled 32-bucket view instead (16 original
    bins per bucket) as one comma-separated integer-rounded `String`, well
    under the ceiling. The full 512-bin FFT is still computed every block;
    it just doesn't all fit back out over one Bridge call. Full-resolution
    transport would need chunking across multiple calls - not attempted,
    same "revisit once more interfaces exist" spirit as the
    `transport_thread` item below.

  **One bug found and fixed on hardware, likely to recur for any future
  continuously-running capture/render thread:**
  - **A busy-wait thread at the same priority as Bridge's own update thread
    (or higher) can permanently hang the entire Bridge link, not just its
    own provider.** First tried at priority 3 (matching
    `matrix_display_thread`/`rgb_display_thread`'s "preempt Bridge's
    priority-5 update thread" convention). On real hardware this made
    *every* Bridge call time out the instant the mic thread started -
    including unrelated `set_rgb`/`set_matrix_text` calls - confirmed by
    bisecting `mic_sampler_start()` step by step across five separate
    hardware deploys (clock init alone: fine; SAI register init alone: fine;
    `Bridge.provide()` registration alone: fine; only starting the capture
    thread broke it). Root cause: unlike the display threads, which
    `k_msleep()` every tick and so periodically let Bridge/each other run,
    the capture loop's success path never blocks or sleeps between
    2048-sample blocks (it can't, without losing samples - see "no DMA"
    above) - so once SAI1 is genuinely streaming data, a same-or-higher
    priority thread with no yield point never lets Zephyr's preemptive
    scheduler switch to a strictly-lower-priority thread like Bridge's,
    ever again. `k_yield()` doesn't fix this either - it only cedes to
    equal-priority peers, not lower-priority ones. Fixed by dropping
    `MIC_SAMPLER_THREAD_PRIORITY` to **7** - lower priority than both
    Bridge (5) and the display threads (3), so they always preempt mic on
    demand instead of the other way around; mic still gets effectively the
    whole CPU the rest of the time, since nothing else runs continuously.
    Trade-off versus the priority-3 attempt: a Bridge RPC or display tick
    can now preempt mid-capture-block and cost a dropped/skewed sample or
    two - same category of accepted cost as the busy-wait itself, not a new
    problem.
  - A second, unrelated hang was also found and fixed earlier in the same
    session: PLL2's VCO input range field (`PLL2RGE`) defaults to the
    4-8MHz range at reset, but this config's 16MHz HSE/div-m=1 input needs
    the 8-16MHz range - left unset, `LL_RCC_PLL2_IsReady()` never returned
    true, hanging `setup()` (and therefore every `Bridge.begin()` call after
    it) forever. Fixed by calling `LL_RCC_PLL2_SetVCOInputRange()` explicitly
    - something the old repo never needed, since Zephyr's `clock_control`
    driver derives this automatically from the devicetree-requested
    frequencies. Both this and the priority bug are why
    `mic_sampler_init_clocks()`/`mic_capture_next_block()` now use bounded
    wait/poll loops with a graceful bail-out instead of the unbounded ones
    first written - an unbounded wait on any future clock/register
    misconfiguration reproduces the exact same whole-board hang, and there's
    no on-device debugger access in this workflow to diagnose it beyond
    "redeploy with a different bisection point," which is slow (~2-3 minutes
    per attempt).
  - Test script: `base-station/tests/mic_sampler_test.py` - new, not ported
    from the old repo (which had no standalone mic test; mic data only ever
    appeared inside the not-yet-ported `fuser_thread`'s fused spectrum frame
    in `mpu/tests/sensor_frame_test.py`'s synthetic, non-hardware test data).
    Polls `get_mic_spectrum` and prints a crude ASCII bar per bucket so you
    can eyeball whether it reacts to real sound. Run the same way as the
    matrix/rgb tests (see those entries above), swapping in
    `mic_sampler_test.py`. **Confirmed working on hardware** (2026-07-13):
    `get_mic_info` reports `sr=0x1,timeouts=0` (SAI1's FIFO request flag
    read back as set, zero capture timeouts) and the spectrum's peak bucket/
    magnitude visibly changes across repeated polls.

- **2026-07-13 — `accel_sampler_thread` (KX134-1211 SPI accelerometer): DONE, verified working on
  hardware.** WHO_AM_I reads back `0x46`, the capture/FFT thread runs continuously (`timeouts=0`),
  and `get_accel_spectrum` returns a live, motion-reactive 3-axis-summed spectrum. Lives in
  `base-station/sketch/accel_sampler.h`/`.cpp` (see that file's header comment for the full port
  rationale — KX134 register map, SPI via this core's bundled `SPI` library resolving to spi2 via
  this board's `spis = <&spi2>, <&spi3>;`, software CS on D8/PB4, INT1 on D9/PB8 via
  `attachInterrupt()`, hand-rolled radix-2 FFT at `ACCEL_FFT_LEN=1024`, `get_accel_spectrum`/
  `get_accel_info` Bridge providers registered *before* the WHO_AM_I check so a mismatch stays
  observable). Wiring (confirmed with the user): CS=D8, INT1=D9, SCK=D13, MISO=D12, MOSI=D11 — the
  old repo's overlay *prose* comment describing CS on D10 is stale relative to its own applied
  `cs-gpios = <&gpiob 4 ...>` (=D8); trust the devicetree code, not that comment. The old repo's
  worry that SPI on PB13/14/15 never read WHO_AM_I correctly did **not** recur here — first real
  read returned `0x46`.

  **The real root cause of the prior session's "PORT BLOCKED" — a scheduling bug, NOT a toolchain
  bug.** The previous session spent hours concluding that `accel_sampler_start()`, called from
  `sketch.ino`'s `setup()`, "never executes" despite the call being correctly compiled/relocated
  (verified via objdump — that part was accurate), and suspected an immature-llext-loader bug worth
  filing upstream. It was none of that. The call *was* being reached-toward correctly; the CPU was
  simply being taken away before it got there:
  - `setup()`/`loop()` run in the Zephyr **main thread, priority 14** (`CONFIG_MAIN_THREAD_PRIORITY`
    in this core, confirmed on-device).
  - `mic_sampler_start()` creates a **priority-7, `K_NO_WAIT`, never-yielding busy-wait** capture
    thread (its whole priority-7 design, see the mic entry above, is *because* it can't yield). The
    instant `k_thread_create()` runs, that priority-7 thread preempts the priority-14 main thread and
    — having no yield point on its success path — **never hands the CPU back to a lower-priority
    thread**. So every statement sequenced *after* `mic_sampler_start()` in `setup()` (and every
    `loop()` iteration, i.e. the heartbeat) never runs again.
  - The accel call was placed *after* `mic_sampler_start()` → starved, never reached. matrix/rgb were
    *before* it → fine. The prior session's "decisive test" (calling accel from `rgb_display_start()`
    works) looked like it isolated the *caller*, but `rgb_display_start()` runs *before*
    `mic_sampler_start()` — so "nothing else changed" was wrong: the call's position **relative to
    mic's thread** changed. That, not the caller's identity, is what mattered.
  - **The fix is ordering, nothing more:** `setup()` now calls
    `matrix_display_start()` → `rgb_display_start()` → `accel_sampler_start()` → `mic_sampler_start()`
    (mic strictly last). accel's own thread is priority 3 and blocks on `accel_data_ready_sem` every
    read, so it yields cleanly and coexists with everything; it just has to be brought up before mic
    monopolizes the CPU. `sketch.ino`'s `setup()` and `accel_sampler.h` both carry a comment spelling
    out this "mic must be last" constraint so it isn't re-lost. The previous session's blind spot was
    a pure lens mismatch: its whole investigation was framed as "caller context vs. callee compiled
    form" (a relocation/ELF lens) and never considered that a correctly-compiled instruction was
    simply never getting scheduled — the mic priority-7 saga lives in a *separate* PROGRESS entry, so
    the two were never connected. The prior "tiny-body still fails from setup()" and
    "`__asm__ volatile("")` tail-call guard" findings are now explained/moot (the tiny body was after
    mic too; the tail-call guard was a genuine-but-irrelevant fix and has been removed — no
    `xxx_start()` is last in `setup()` anymore, mic is).

  **`fifo_full` tracks `reads`/`isr` ~1:1 on hardware — expected, not a defect.** `get_accel_info`
  reports `fifo_full` climbing in lockstep with `reads`, i.e. the KX134's 86-frame hardware FIFO sits
  at its cap on essentially every read. This is the **documented, accepted steady-state** of this
  exact configuration (ODR=1600Hz, `ACCEL_READ_CHUNK_FRAMES=64` drained off a Buffer-Full interrupt),
  reproduced faithfully from the old repo — see its
  [`accel_sampler_thread.c`](../../edgeai-predictive-monitor-unoq/mcu/src/threads/accel_sampler_thread.c)
  chunk-size comment (lines ~42-52) and `docs/Sensor_Throughput_Tuning_Plan.md`: 64 is the largest
  divisor of `ACCEL_FFT_LEN=1024` that fits under the 86-frame cap, and at 1600Hz the drain rate is
  slightly under ODR's production rate, so Stream mode discards the oldest raw samples before the
  thread sees them. This port deliberately keeps the old repo's original safe 1600Hz baseline (the
  header comment says so) rather than its later-tuned 12800Hz; closing the fifo_full gap is the same
  deferred throughput-tuning work, to revisit if bin-level spectral fidelity ever turns out to matter
  downstream. Not a bring-up blocker.

  **Side effect worth knowing:** because mic's priority-7 thread starves the priority-14 main thread
  (above), the `loop()` heartbeat LED effectively **stops blinking once the mic thread is streaming**
  — this is pre-existing (it started with the mic port, not accel) and currently just cosmetic (the
  "firmware alive" signal is now better read from any Bridge query succeeding). If a live heartbeat is
  wanted back, move it out of `loop()` into its own priority-3 (or lower) thread that `k_msleep()`s,
  the way the display threads do — not done here to keep this change scoped to the accel port.

  **Deploy/verify mechanics (unchanged, kept here for the next session):**
  - Deploy via `base-station/deploy.sh` (push+build+flash+restart). Incremental deploy 3-6 min on
    this board's slow CPU; from-scratch rebuild 10-15 min — don't assume a quiet deploy is stuck.
    `deploy.sh`'s final log-follow streams *historical* container logs, so it looks stale even on a
    good build; the real completion signal is a fresh low-uptime container
    (`adb shell "docker ps --filter name=edgeai-predictive-monitor-base-station"`).
  - Query Bridge from the host: `adb shell "docker exec edgeai-predictive-monitor-base-station-main-1
    timeout 25 python3 -c \"from arduino.app_utils import Bridge; print(Bridge.call('get_accel_info'))\""`
    (first call after a fresh flash often needs one retry).
  - Full test: `adb shell "docker exec edgeai-predictive-monitor-base-station-main-1 python3
    /app/tests/accel_sampler_test.py"` — prints ODR/FFT metadata + diagnostics, then polls
    `get_accel_spectrum` with a per-bucket ASCII bar so you can eyeball reactivity (tap/shake the
    board). **Confirmed working on hardware** (2026-07-13): `who_am_i=0x46,ok=1,timeouts=0`, peak
    bucket wanders across buckets 0-4, magnitudes vary poll-to-poll — live capture, not stuck data.
  - The recurring `Error: verify failed in bank at 0x08000000 ...` + `Warn : Adding extra erase
    range` pair in every `deploy.sh` OpenOCD run is **not a real error** — it's `flash_sketch.cfg`'s
    own intentional verify-then-reflash retry pattern. Don't chase it.

## Future improvements

- **`rgb_display.cpp`: move WS2812 transmission off the CPU onto SPI1, bypassing devicetree** — right
  now the ring is bit-banged (see the 2026-07-13 rgb_display_thread entry above), which busy-waits with
  `irq_lock()` held for ~240us per 8-pixel frame. The old repo avoided this entirely by pinmuxing
  PA12 as SPI1 MOSI and letting Zephyr's `worldsemi,ws2812-spi` `led_strip` driver clock bits out via
  DMA-backed SPI, freeing the CPU during transmission — not available to us since App Lab doesn't
  expose devicetree/pinctrl to a sketch, and Arduino's own `SPI` object doesn't map to D4 by default.
  Since confirming this core's full STM32Cube HAL/LL tree is reachable from a sketch (see that same
  entry), the same trick should be redoable by hand: enable SPI1's RCC clock, set PA12 to AF5 (SPI1
  MOSI) via direct GPIO AFR register writes, and configure/feed SPI1 directly via `stm32u5xx_ll_spi.h`
  — all bypassing devicetree, the same way `stm32u5xx_ll_gpio.h` bypassed the GPIO driver. Not attempted
  yet: real complexity (RCC/AF/SPI register setup) for a benefit (freeing ~240us/20ms of CPU time) that's
  currently invisible on this workload — revisit if a future thread needs tighter latency than the
  bit-bang's IRQs-off window allows.

- **`mic_sampler.cpp`: move I2S capture off the CPU onto GPDMA1, bypassing devicetree** — right
  now capture is a ~21.3ms/block busy-poll of `SR.FREQ` (see the 2026-07-13 mic_sampler_thread
  entry above), which is why the thread has to run at a below-Bridge priority (7) rather than
  preempt it like the display threads do. The old repo avoided this by having Zephyr's
  `i2s_stm32_sai.c` driver clock samples in via GPDMA1 channel 2 (request/slot 36, fixed SAI1_A RX
  hardware wiring) into a mem-slab queue, freeing the CPU between blocks entirely.

  **Attempted 2026-07-13, reverted the same day — left for whoever picks this up next with
  debugger/logic-analyzer access, since this session didn't have either.** Single-block (not
  circular/linked-list) GPDMA1 channel 2 register programming against `stm32u5xx_ll_dma.h`
  (there's no linkable `LL_DMA_Init()`/`LL_DMA_StructInit()` either — same missing-`.c` situation
  as `HAL_SAI_Init()` — so this called the individual `__STATIC_INLINE` setters by hand, same
  pattern as `mic_sampler_init_sai()`): direction=peripheral-to-memory, request=36 (`SAI1_A`),
  one-halfword-per-request single-burst, 16-bit widths both sides, source fixed at
  `SAI1_A->DR`/dest incrementing through `mic_capture_block[]`, re-armed (fresh `SrcAddress`/
  `DestAddress`/`BlkDataLength` + `EnableChannel`) from the sampler thread every block instead of
  the old repo's mem-slab-queue blocking wait. The channel could be proven correctly *configured*
  every time (`CTR2`/`CCR`/`PRIVCFGR`/`SECCFGR`/`RCFGLOCKR` all read back exactly as written, no
  `DTE`/`USE`/`ULE` error flags, `mic_capture_block[]`'s and `SAI1_A->DR`'s addresses both
  ordinary/expected) but never once *executed* a transfer —
  `LL_DMA_GetBlkDataLength()` always read back the full, untouched block length. Ten distinct
  fixes were tried on real hardware, each producing the identical failure signature:
  1. Clearing SAI1's `OVRUDR` unconditionally before every arm, not just after a successful
     block (in case an unacknowledged overrun — plausible, since `SAIEN` had already been on for
     a while by the time the sampler thread's first arm ran — was gating SAI1's own DMA request
     generation, mirroring the old repo's own `audio_i2s.c` "must restart, not just clear a flag"
     `I2S_STATE_ERROR` precedent).
  2. Writing `CR1.DMAEN` as a genuine `0→1` edge strictly after `SAIEN`, not in the same write or
     before it.
  3. `CR2.FTH` (FIFO threshold) at `1/4 FIFO` instead of `EMPTY`.
  4. `GPDMA1`'s per-channel destination port allocation (`CTR1.DAP`) explicitly set to Port1 (ST's
     own STM32U5 GPDMA training material: "Port0 ... direct hardware data path to APB
     peripherals ... Port1 ... for transfers to/from memory" — both default to Port0, and
     `mic_capture_block[]` is memory).
  5. A full `SAIEN` stop/restart cycle wrapped around every arm (disable, clear `OVRUDR`, arm+
     enable the DMA channel, re-enable `SAIEN`), so the FIFO starts empty and DMA is already
     draining before any new sample can arrive for that block.
  6. Reordering step 5 to exactly match ST's own `HAL_SAI_Receive_DMA()`
     (`stm32u5xx_hal_sai.c`, fetched from
     [github.com/STMicroelectronics/stm32u5xx-hal-driver](https://github.com/STMicroelectronics/stm32u5xx-hal-driver)
     for reference, since only that file's *headers* ship in this core): `DMAEN` set once,
     `SAIEN` enabled, and *only then* the DMA channel's addresses/length configured and started —
     the opposite order from attempt 5's "start DMA, then re-enable SAI" — still identical
     failure.
  7. `LL_DMA_IsActiveFlag_SUSP()` (channel status) confirmed set the whole time, with no
     corresponding `CCR.SUSP` *request* bit ever written — i.e. genuinely "armed and waiting,"
     not an error state.
  8. Ruled out trigger-gating entirely: `CTR2.TRIGPOL`'s reset default (`0`) is
     `LL_DMA_TRIG_POLARITY_MASKED` — "no trigger of the selected DMA request, masked trigger
     event" — confirmed by two independent sources (the LL header's own doc comment and an ST
     community thread), so the channel was never waiting on a phantom trigger despite `TRIGSEL`
     also defaulting to a *real* selector value (`EXTI_LINE0`, not a "disabled" sentinel) that
     looked suspicious at first.
  9. Ruled out privilege/security/config-lock gating: `GPDMA1->PRIVCFGR`/`SECCFGR`/`RCFGLOCKR`
     all read back `0` (unrestricted) throughout.
  10. Confirmed the sampler thread itself was alive and genuinely retrying the whole time (not
      stuck/faulted), by watching `mic_get_info()`'s `timeouts=` counter climb steadily across
      repeated queries — ruling out "the thread silently died after N attempts" as an explanation
      for the unchanging diagnostics.

  Live-searched ST's own community forums for this exact combination mid-session (a `STM32U599 SAI
  (PDM Mono) GPDMA got only 0's` thread reported the closely-related "polling mode worked, DMA
  received only zeros" symptom, unresolved/unanswered) — this SAI+GPDMA pairing is evidently a
  known rough edge even for people using full HAL on bare-metal firmware, not just hand-rolled
  registers. The most informative result here is arguably attempt 6: matching ST's own
  authoritative HAL sequence byte-for-byte changed nothing, which points away from a configuration
  mistake and toward something specific to this runtime environment that no bare-metal ST
  reference material would account for — the leading unconfirmed suspicion being Zephyr's own
  power management silently re-gating GPDMA1's execution clock sometime after this sketch enables
  it (register reads/writes keep working, on what would be a separate bus-clock domain, while the
  transfer engine itself stays dark) — but this session had no way to test that (no debugger, no
  logic analyzer on the actual GPDMA1↔SAI1 request line, no visibility into Zephyr's PM state from
  sketch code). Reverted to the FIFO-polling implementation described in the 2026-07-13
  mic_sampler_thread entry above, which is confirmed working. The `mic_sampler.cpp` header comment
  carries a shorter version of this same account; this entry is the full one, kept here since it's
  the natural place a future attempt would look first.

## Next up

Port the remaining threads from the old `main.c`, one at a time, onto the App Lab sketch/python split.
Old repo had these as separate Zephyr threads under `mcu/src/threads/`:

- [x] `accel_sampler_thread` — accelerometer sampling (2026-07-13, see progress log above — KX134
      over the bundled `SPI` (spi2) + INT1/`attachInterrupt()` + hand-rolled FFT + `Bridge`, not the
      old repo's Zephyr SPI/DMA/CMSIS-DSP stack. WHO_AM_I=0x46, live spectrum confirmed on hardware.
      The prior "PORT BLOCKED" was a scheduling bug, not a toolchain bug: mic's priority-7 thread
      starved the priority-14 `setup()` thread, so `mic_sampler_start()` must be called **last** in
      `setup()` — see the progress log entry for the full root-cause)
- [x] `mic_sampler_thread` — microphone sampling (2026-07-13, see progress log
      above — direct-register SAI1 I2S RX + hand-rolled FFT + `Bridge`, not
      the old repo's Zephyr `i2s`/DMA/CMSIS-DSP stack)
- [ ] `fuser_thread` — sensor fusion / inference
- [x] `rgb_display_thread` — external WS2812 ring (2026-07-13, see progress log above — direct
      register-level WS2812 bit-bang on D4/PA12 via STM32Cube's LL_GPIO driver + `Bridge`, not the old
      repo's `led_strip`/SPI1 approach)
- [x] `matrix_display_thread` — LED matrix display (2026-07-13, see progress log above — via
      `Arduino_LED_Matrix`/`ArduinoGraphics` + `Bridge`, not a hand-rolled driver/wire protocol)
- [ ] `transport_thread` — likely superseded per-interface by direct `Bridge.provide()`/`Bridge.call()`
      use (as `matrix_display_thread` now does) rather than one shared thread — revisit once more
      interfaces are ported and it's clear whether anything still needs a dedicated thread here (e.g. an
      MCU-initiated push channel Bridge doesn't already cover)

Satellite (ESP32-S3) side hasn't been started at all yet — no code has been ported or written there.

## Reference

- Old repo (for logic/behavior reference only, not to be built as-is): `../edgeai-predictive-monitor-unoq`
- Old MCU entry point: `edgeai-predictive-monitor-unoq/mcu/src/main.c`
- App Lab app layout / CLI: apps live on-device under `/home/arduino/ArduinoApps/<app-name>`;
  managed with `arduino-app-cli app start|stop|logs <path>` (see `base-station/deploy.sh`).
