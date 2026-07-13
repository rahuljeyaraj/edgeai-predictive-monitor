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

## Next up

Port the remaining threads from the old `main.c`, one at a time, onto the App Lab sketch/python split.
Old repo had these as separate Zephyr threads under `mcu/src/threads/`:

- [ ] `accel_sampler_thread` — accelerometer sampling
- [ ] `mic_sampler_thread` — microphone sampling
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
