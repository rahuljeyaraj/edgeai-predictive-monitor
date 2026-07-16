# Progress 3 — RGB ring: SPI+DMA landed, 1-pixel glitch open, next = Timer+PWM+DMA

Condensed handoff for the next session. Predecessor: [progress2.md](progress2.md) (fuser/SPI
transport + mic/SAI bring-up - all DONE, unrelated to this file). This file is scoped to
the external WS2812B 8-LED ring (`base-station/sketch/rgb_display.cpp`) only.

---

## 1. Where this stands right now

Commit `74ca11e` (branch `main`) replaced the ring's `irq_lock()`+busy-wait bit-bang with
SPI1-master-TX (MOSI-only, PA12/AF5) + GPDMA1, one SPI byte per WS2812 bit at 5 MHz. This
was a full architecture change, not a tune - see rgb_display.cpp's own header comment for
the deep rationale. It fixed both bugs that motivated it, confirmed on hardware:

1. **CONST yellow (or any color with adjacent 0xFF wire bytes) rendered green.** Root
   cause: the old bit-bang's per-bit `k_cycle_get_32()` re-sample jitter cost the byte
   *after* a run of 1-bits its high-pulse margin. Single-channel colors (pure red/green/
   blue) never exposed it - only multi-channel-saturated colors did. Fixed by construction:
   every SPI-clocked bit is an identical, jitter-free hardware-timed pulse.
2. **BREATHE/STROBE hung Bridge.** Root cause: `ws2812_show()` held `irq_lock()` for the
   whole ~240us frame, and BREATHE/STROBE re-render every `RGB_DISPLAY_TICK_MS` (20ms)
   from this priority-3 thread (**above** Bridge's priority-5 update thread) - so a 240us
   IRQs-off window recurring 50x/s overran the 115200 RPC UART, desyncing Bridge's msgpack
   framer. Symptom on the MPU side: `[Bridge.read_loop] ... 'utf-8' codec can't decode
   byte 0xNN ... invalid start byte`, then every `Bridge.call` times out. Fixed by
   construction: GPDMA1 streams the frame with **zero CPU/IRQ involvement** - the thread
   just arms the DMA and returns, no lock of any kind.

Verified via `tests/display_rgb_test.py` (all 6 steps incl. BREATHE/STROBE) completing
end-to-end with a clean Bridge (`docker logs` free of `read_loop`/`decode` errors) across
multiple repeat runs.

**Open issue (not a regression of either bug above - new, narrower, still unresolved):**
the LED **closest to DIN (pixel 0, first in the chain)** shows a wrong color, consistently,
regardless of which color is requested (confirmed on red/yellow/white). LEDs 2-8 are
correct. This is new - the old bit-bang never had a per-pixel positional glitch, only the
two bugs above.

---

## 2. What was tried on the pixel-0 glitch, and ruled out

Two targeted register-level fixes were tried and reverted (neither is in the current
commit - `74ca11e` is the clean baseline described above):

- **A short `k_busy_wait()` between `LL_SPI_Enable()` and `LL_SPI_StartMasterTransfer()`
  (CSTART)**, on the theory that DMA hadn't yet pushed the first byte into the TX FIFO
  before the clock started. **Zero measurable effect** at 5us. This is actually useful
  negative evidence: if the FIFO were genuinely racing CSTART, *some* delay should have
  helped. That it didn't suggests the first byte's *data* is very likely already correct
  in the FIFO by the time CSTART fires - i.e. this is probably not a data-availability
  race at all.
- **Manually pre-loading the first `RGB_SPI_PRELOAD_BYTES` (4) bytes into `SPI1->TXDR` via
  `LL_SPI_TransmitData8()`** before arming DMA/CSTART, with DMA reconfigured to only move
  the remaining tail (`rgb_spi_buf + RGB_SPI_PRELOAD_BYTES`, `RGB_SPI_BUF_LEN -
  RGB_SPI_PRELOAD_BYTES`). This **made things worse** - the *last* LED also started
  glitching, which it hadn't before. Most likely a split-transfer alignment/off-by-one
  bug in the DMA length math introduced by the split itself, not evidence about the
  underlying cause. Reverted in full.

**Working theory, unconfirmed:** this SPI IP (STM32U585's SPIv3-style FIFO+CSTART master
mode) may have a first-clock-edge timing quirk specific to the transition out of CSTART -
i.e. a hardware characteristic of the peripheral's own clock generator, not a data-timing
race - which no "get the data there sooner" fix can address. This can't be confirmed
without an oscilloscope or logic analyzer on PA12 during that first pulse; SWD register/
RAM inspection (this project's usual go-to, see [[rpc-transport-and-push-primitive]] memory
/ progress2.md §4.8) only shows *digital register state*, not analog pulse-width defects,
so it's not useful for this specific problem. **No scope is available this session** (per
the user) - diagnosis was 100% by eye on the physical ring.

---

## 3. Also found, explicitly OUT OF SCOPE for this file

**White/high-brightness colors slowly drift toward losing red** (white -> blue, yellow ->
green) over several seconds while holding a static CONST color. User confirmed this
**predates the SPI+DMA rewrite** (same symptom seen on the old bit-bang) - so it is NOT
introduced by this work and is NOT a firmware timing bug (firmware sends the identical
24-bit color every 20ms tick regardless; nothing about the render path changes over time).
Classic signature of either power-supply sag (8 LEDs at full white ~480mA can brown out a
weak 5V rail, and WS2812 chips commonly lose red first under sag) or thermal droop (red
LED dies lose efficiency faster than green/blue under sustained heat). User explicitly
deferred this to a later, separate investigation (power budget / current measurement) -
don't conflate it with the pixel-0 SPI glitch above when picking this back up.

---

## 4. THE NEXT CHANGE — Timer+PWM+DMA rewrite

User's explicit choice (over "accept as-is" and "one more surgical SPI try") for the
pixel-0 glitch: **replace the SPI-as-WS2812-encoder trick entirely with the standard
professional approach** - a hardware timer's PWM channel, DMA-driven, updating the
duty-cycle compare register (CCR) once per WS2812 bit. This is the same technique
Adafruit_NeoPixel and most serious STM32 WS2812 drivers use, specifically because it
sidesteps SPI's CSTART/master-mode machinery entirely - there's no analogous "clock
generator startup transition" for a free-running PWM timer already ticking before DMA
ever engages the ring's data.

**Shape of the design** (not yet built - this is the plan, informed by what's already
proven in this codebase):
- One timer channel (e.g. TIM1 or TIM8 - **not currently used anywhere in this sketch**,
  confirmed via `grep -rn "LL_TIM" base-station/sketch/*.cpp` at the start of this
  session - free choice, no conflict) in PWM mode, driving PA12 via its own alternate
  function (**not** AF5/SPI1_MOSI - PA12's timer AF will be different; needs the same
  `STM32_PINMUX` decode from `modules/hal/stm32/dts/st/u5/stm32u585aiix-pinctrl.dtsi`
  used to find AF5 this session - grep that file for `tim.*_ch.*_pa12` variants).
- ARR (auto-reload) set for a ~800kHz-1.25MHz PWM period (WS2812 bit period), CCR updated
  per-bit by DMA to encode a WS2812 '0' (~33% duty) vs '1' (~66% duty) - the timer's own
  free-running counter provides the bit-period timing in hardware; DMA only ever touches
  the duty-cycle value, never the clock/gating logic SPI's CSTART owns.
- GPDMA1 TX request line will be a `LL_GPDMA1_REQUEST_TIMx_CHy`-style constant (check
  `stm32u5xx_ll_dma.h` for the exact one matching whichever timer/channel is chosen).
- **DMA channel: use `LL_DMA_CHANNEL_4`** (what RGB currently owns) or higher - confirmed
  channel map across the whole sketch: `mic_sampler.cpp`=2, `spi_link.cpp`=3, `rgb_display.
  cpp`=4 (this file). Don't collide with 2 or 3.
- Buffer/encoding structure (192 data bytes for 8 GRB pixels + reset/latch tail) and the
  overall command/mutex/thread-tick architecture (`rgb_display_set_command` Bridge
  provider, `rgb_display_tick()`'s CONST/BREATHE/STROBE math, `RGB_DISPLAY_THREAD_PRIORITY`
  = 3, `RGB_DISPLAY_TICK_MS` = 20) all stay as-is from the current SPI version - only the
  peripheral driving the physical bit stream changes. `rgb_spi_fill()`'s bit-encoding
  logic can likely be adapted almost directly (same MSB-first-per-color-byte loop), just
  writing CCR-sized duty values instead of SPI-byte pulse-width bytes.
- The 2026-07-16 SPI attempt's confirmed-good pieces to carry forward unchanged: the
  `RGB_WS_SPI_BIT1`-family bit-width tuning work (0xF0 -> 0xF8 -> 0xFC, i.e. T1H needed
  ~1200ns not the WS2812B datasheet's nominal 800ns on *this* physical ring - re-derive
  the equivalent CCR fraction empirically the same way, don't assume the datasheet number
  will just work) and the MODF-mode-fault self-clearing pattern (**N/A for a pure PWM
  timer** - that was SPI-master-mode-specific, won't be needed here).

**First thing to verify on hardware once built:** hold CONST white and CONST red, check
whether pixel 0 now matches the rest. If yes, run the full `tests/display_rgb_test.py`
end-to-end (incl. BREATHE/STROBE) to reconfirm neither original bug regressed, then decide
whether to also tackle the drift issue (§3) as a separate, later investigation.

---

## 5. Deploy/debug discipline (reused verbatim this session, still load-bearing)

Full sequence confirmed necessary every reflash this session (matches [[rpc-transport-and-push-primitive]]
memory, re-confirmed 2026-07-16):
```
adb shell "arduino-app-cli app stop $R"
adb push <local sketch/> $R/sketch/            # or just edit in place on-device
adb shell "arduino-app-cli app start $R"       # builds + flashes MCU + starts container
adb shell "echo 'help100S' | sudo -S systemctl restart arduino-router"
adb shell "arduino-app-cli app stop $R"
adb shell "arduino-app-cli app start $R"       # MCU re-registers providers against fresh router
```
`R=/home/arduino/ArduinoApps/edgeai-predictive-monitor-base-station`. Skipping the
router-restart leaves Bridge providers *responding* to the first few calls, then dying
mid-session with the same `read_loop`/`invalid start byte` signature as bug #2 above - easy
to mistake for a firmware regression when it's actually just a skipped deploy step. A
working copy of this whole sequence is at
`/tmp/claude-1000/.../scratchpad/redeploy.sh` from this session (may not persist -
recreate from the sequence above if gone).

`docker exec edgeai-predictive-monitor-base-station-main-1 python3 -c "from
arduino.app_utils import Bridge; print(Bridge.call('set_rgb','FF0000,0,0'))"` is the
fastest one-off way to drive a color without the full test script. **No serial monitor or
oscilloscope available** - all verification this session was Bridge-health-via-logs
(`docker logs ... | grep read_loop`) plus the user's own eyes on the physical ring; plan
accordingly (can't verify pulse-width claims independently, only end-to-end color/hang
behavior).

One board-session USB hiccup happened mid-session (`adb: no devices/emulators found`,
self-resolved by `adb kill-server && adb start-server`, then the container was found
`Exited (255)` and needed a full redeploy) - if a redeploy command mysteriously fails,
check `adb devices` before assuming a firmware problem.
