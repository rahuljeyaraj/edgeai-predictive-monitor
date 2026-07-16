# Progress 3 — RGB ring: SPI+DMA landed, 1-pixel glitch FIXED via Timer+PWM+DMA (2026-07-16)

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

## 6. UPDATE 2026-07-16: plan's premise was wrong for PA12 - implemented on PB0/D3 instead

Before writing any code, checked `modules/hal/stm32/dts/st/u5/stm32u585aiix-pinctrl.dtsi`
for `pa12` pinmux entries as §4 said to. **PA12 has no `TIMx_CHy` alternate function at
all** on this exact package - only `spi1_mosi`, `fdcan1_tx`, `octospim_p2_ncs`,
`usart1_de`/`usart1_rts`, `usb_otg_fs_dp`, and analog. So the Timer+PWM+DMA design could
not be built on D4 in place; it requires moving the ring's physical DIN wire to a header
pin that has a timer channel.

Surveyed the rest of the Arduino header (`arduino_r3_connector.dtsi`) against both the
pinctrl dtsi and this sketch's existing pin usage: D0(PB7)/D1(PB6) are the board's console
UART (`usart1_rx_pb7`/`usart1_tx_pb6`, wired in `arduino_uno_q-common.dtsi`) - off limits.
D9(PB9) is already `mic_sampler.cpp`'s SAI1 FS/WS pin. D5(PA11)/D7(PB2) only expose
complementary-only timer channels (TIM1_CH4 is fine actually, but D2/D3/D6 were cleaner:
plain positive channels on simple general-purpose timers, no break-input complexity).
User chose **D3 (PB0, `tim3_ch3_pb0` = AF2)**.

**Implemented** in `base-station/sketch/rgb_display.cpp` (not yet flashed/verified - no
local build toolchain in this checkout, builds via `arduino-app-cli` on-device only):
- TIM3 CH3 PWM, ARR=255 (256-count period @ 160 MHz APB1 = 1.6 us/bit, matching the old
  SPI version's byte period exactly), OC preload enabled, DMA-on-UPDATE-event (not
  compare-match) - the standard glitch-free "DMA writes next period's CCR on each
  rollover" pattern.
- TIM3 is configured and enabled **once** at init and never disabled again - only the
  GPDMA1 channel is armed/disarmed per frame. This is the one non-negotiable design point
  from §4: no per-frame timer restart, so there's no analogue of SPI1's CSTART cold-start.
- Reused the SPI version's hardware-validated T0H/T1H verbatim as CCR fractions of 256:
  '0' -> CCR=64 (400 ns high, was `0xC0`), '1' -> CCR=192 (1200 ns high, was `0xFC` - this
  ring's confirmed-wider-than-datasheet T1H, see §1 bug 1). Buffer shape (192 data slots +
  200 zero-slot reset tail) carried over unchanged, just `uint16_t` CCR values instead of
  SPI bytes.
- GPDMA1 stays on channel 4 (same as before), now width HALFWORD (was BYTE), request line
  `LL_GPDMA1_REQUEST_TIM3_UP`, destination `&TIM3->CCR3` (was `&SPI1->TXDR`).
- `get_rgb_stats` DIAG provider updated to report TIM3/DMA state instead of SPI1/DMA state.

**CONFIRMED FIXED on hardware, same session:** wire moved D4->D3, redeployed via the §5
sequence (one flash attempt hit `Error: verify failed in bank at 0x08000000` + an extra
erase-range retry mid-flash - self-recovered, second full redeploy flashed clean with no
verify errors). `get_rgb_stats` showed `rem=0` (DMA completing every frame, TIM3 `cr1=0x1`
confirms CEN running) both before and after a `set_rgb` call. User confirmed on the
physical ring: **CONST red renders correctly on all 8 pixels, including pixel 0** - the
glitch is gone.

**Full `tests/display_rgb_test.py` re-run, same session, by the user:** all 6 steps
completed - CONST and STROBE look correct. **BREATHE is visibly glitchy** under the new
Timer+PWM+DMA transport. This is a NEW, still-open issue, distinct from the original
pixel-0 glitch (confirmed fixed) - full debugging handoff in §7 below.

The §3 white/high-brightness color-drift issue remains separately deferred (unrelated,
predates all of this work).

---

## 7. Handoff for next session: BREATHE glitch (opened 2026-07-16, right after §6's fix)

**Status:** unreproduced-in-detail. All the user has said so far is "the breathing was
glitchy" - no description yet of what that looks like (flicker? one pixel worse than
others, like the old pixel-0 bug? stutters in the fade speed/smoothness? a wrong color
appearing briefly?). **Getting that description (or a phone video) is the highest-value,
lowest-cost next step** - it may immediately rule in/out entire categories below.

**A same-session theory this file floated and later reasoning undercuts - don't start
here:** the first draft of §6 guessed the cause was `rgb_pwm_show()` fully resetting/
reconfiguring the GPDMA1 channel every tick while TIM3 free-runs underneath with no
SPI-CSTART-style gate, and that BREATHE's 50 Hz re-render rate was what exposed a race
other modes wouldn't hit. **That framing has a hole:** `rgb_display_thread_entry()` calls
`rgb_display_tick()` -> `rgb_render()` -> `rgb_pwm_show()` on *every* tick regardless of
mode (see the bottom of `rgb_display.cpp`) - CONST re-renders at the exact same 50 Hz/20ms
cadence as BREATHE, just with bit-identical `r,g,b` every time, and CONST tested clean. So
whatever's wrong is very unlikely to be "re-render frequency" alone; it's more likely tied
to *what changes between consecutive frames*, not *how often* frames are re-sent.

**Stronger candidate, not yet tested:** BREATHE is the only mode that continuously sweeps
`scale_pct` through a wide, ever-changing range (0-100, cosine-shaped), so `cmd.r/g/b`
scaled by it take on many different 8-bit values in sequence - each a structurally
different bit pattern (e.g. 0x7F = seven 1-bits then a 0; 0x80 = one 1-bit then seven 0s).
STROBE only ever sends two fixed frames (full color / all-zero); CONST sends one frame
forever. This is the same *shape* as the original SPI-era bug (§1 bug 1: a specific
bit-pattern - adjacent 0xFF bytes - broke, not every color), except that bug's specific
root cause (per-bit software re-sample jitter) is gone now that encoding is fully DMA/
timer-driven with no per-bit software timing at all - so if this new glitch is also
pattern-dependent, the mechanism has to be different (candidates: OC-preload/update-event
double-buffering behaving oddly for particular CCR sequences; something about how often
consecutive identical CCR values land vs. rapidly alternating ones; a DMA burst/priority
interaction). Worth testing directly: **hold CONST at a series of static, non-full/non-
zero colors that cover odd bit patterns** (e.g. `set_rgb` with `7F0000`, `800000`,
`550000`, `AA0000`, `330000`, `010000`, `FE0000`) and watch pixel-by-pixel for anything
wrong. If any static pattern alone glitches, this is a pure encoding bug, nothing to do
with BREATHE's motion or re-render rate - much narrower to chase than debugging BREATHE's
timing live.

**If no static pattern glitches on its own**, then it likely does need frame-to-frame
*change* (not just presence) of certain patterns, and the next step is probably logging:
poll `get_rgb_stats` (already exists, DIAG, reports `n`/`sr`/`cr1`/`ccr3`/`cnt`/`rem`) in a
tight loop (`while True: print(Bridge.call('get_rgb_stats')); time.sleep(0.02)`, faster
than the display's own 20ms tick) while BREATHE runs, and check whether `rem` ever fails to
read back 0 (would mean a frame's DMA hadn't actually finished when the next one got
armed - the race theory above, resurrected with actual evidence instead of speculation).

**Do not re-litigate the pixel-0/SPI-CSTART fix (§6) or the original two bugs (§1) as part
of this - those are confirmed fixed and unrelated.** The §3 color-drift issue is also a
separate, still-deferred item - don't conflate any of the three.

---

## 8. UPDATE 2026-07-16 (same day, later session): BREATHE glitch is NOT BREATHE-specific -
it's any non-saturated color byte, confirmed on hardware, root cause still unresolved

Live hardware access was available this session (`adb`/`docker exec ... Bridge.call(...)`),
so §7's proposed experiments were actually run instead of staying theoretical. Findings,
in order:

1. **DMA-stall race (§7's "if no static pattern glitches" fallback) - ruled out.** Polled
   `get_rgb_stats` in a tight loop (faster than the 20ms tick) while BREATHE ran: `rem`
   read back `0` on every single sample, both during BREATHE and later during the
   reproduced static-color glitch. The previous frame's DMA transfer always completes
   well before the next one is armed - this is not a stuck/incomplete transfer.

2. **Static odd-bit-pattern colors DO glitch on their own - §7's "stronger candidate"
   confirmed.** Held solid (CONST, never re-sent) `0x7F0000` indefinitely: user reported
   "flickering red" with no BREATHE involved at all. This means the bug was mischaracterized
   as a "BREATHE glitch" - it's a general encoding/transport bug that BREATHE merely
   exposes constantly (by sweeping through many intermediate byte values), which CONST/
   STROBE's original test pass never hit because it only ever used pure `0x00`/`0xFF`
   per-channel colors (red/green/blue/yellow/white/off).

3. **A digital race theory was tried and disproved.** Hypothesis: `rgb_pwm_show()` calls
   `LL_DMA_ResetChannel()` + full reconfigure every tick while TIM3 free-runs underneath;
   if a TIM3 UPDATE event (and its DMA request) landed while the channel was mid-reset, the
   request would be dropped, and buf[0] would only latch one full period late - shifting the
   whole frame by one slot. This is invisible for solid colors (shifting a run of identical
   duty values is undetectable) but would show up on transitions. **Fix tried:** clear
   `TIM3`'s UPDATE flag and busy-wait for a fresh one (bounded to <1.6us) immediately before
   `rgb_pwm_configure_dma()`, guaranteeing a near-full period of margin before the channel
   touches anything. Deployed, redeployed with full router-restart discipline, retested on
   the same `0x7F0000` static repro: **still flickered, no improvement.** Reverted in full
   (not left in the tree - see git history/diff for this file's session, since it did not
   help and this repo's convention is not to keep unverified speculative changes). This
   theory is now considered disproved, same status as the two fixes in §2.

4. **Power/current-draw theory - ruled out.** If the flicker were caused by supply
   sag/decoupling under load, a near-zero-current mixed pattern should be clean. Tested
   `0x010000` (value 1, i.e. minimal duty/current, but still a mixed 0/1 bit pattern):
   **still flickered.** So current draw is not the variable - the trigger is really "does
   this color's byte(s) contain both a 0-bit and a 1-bit," independent of magnitude.

5. **STROBE re-confirmed clean, consistent with the above.** STROBE only ever sends two
   frames per period - full saturated color or all-zero - both "uniform" bytes. Tested red
   STROBE, 2s period (1s on/1s off): user confirmed "clean blink, on/off crisp." This is the
   expected result under the pattern-dependence theory and rules out anything wrong with
   STROBE's own code path specifically.

**Net finding:** any 8-bit color/channel value that is *not* `0x00` or `0xFF` (i.e. contains
at least one 0-bit and one 1-bit) renders as visibly flickering on this transport, reliably
reproducible via CONST with zero BREATHE/motion/frame-to-frame-change involved. Pure `0x00`/
`0xFF` per channel (covering CONST red/green/blue/yellow/white/off and both STROBE frames)
is clean. Two plausible causes remain, and distinguishing them needs signal-level
visibility this session didn't have (no scope/logic analyzer, per §5's standing
constraint):
   - OC-preload/CCR-update-event pipeline behaving differently when the DMA-written value
     *changes* between consecutive periods vs. repeating (a digital/silicon timing
     interaction, not the "dropped request" variant already ruled out in point 3 above).
   - Genuine wire signal-integrity/inter-symbol-interference: a short high pulse (T0H,
     400ns/CCR=64) immediately adjacent to a long one (T1H, 1200ns/CCR=192) may not settle
     within this specific ring's actual (non-datasheet - see §6, T1H already had to be
     widened once) bit-threshold margin, only when duty *changes* between adjacent bits.

**Do not retry the two disproved theories (§8 points 3-4) or re-litigate §7's now-resolved
"is it BREATHE-specific" question** - it isn't; reproduce with static `0x7F0000` or `0x010000`
CONST, no BREATHE needed, much faster iteration loop. **Next open option, not yet decided:**
try further widening the CCR0/CCR1 margins (push T0H lower / T1H higher for more settling
margin) as a next blind experiment - but this repo's own history (§2: two register-level
timing tweaks tried and reverted, one of which made things *worse*) is a explicit reason to
be cautious about more blind timing changes without a scope to verify against.

---

## 9. UPDATE 2026-07-16 (same day, third session): found the real root cause, fix works but
has an unresolved cold-boot regression - reverted to HEAD, ring left working, NOT merged

**Critical procedural bug discovered first, fix it before trusting *any* of §7/§8's
conclusions that came from a redeploy:** `adb push <local>/sketch/ $R/sketch/` (the exact
command in §5 below) does **not** overwrite files in place when `$R/sketch/` already exists
- it creates a nested `$R/sketch/sketch/` and pushes there instead, silently leaving the
actual build directory (`$R/sketch/` top-level) untouched. Every code change "tested" in §8
(the UPDATE-event-sync fix, its revert, the `RGB_DISPLAY_TICK_MS=1000` change, the render-
skip-if-unchanged DIAG) was **never actually deployed** - the device ran the same unmodified
§6 baseline the whole time, so §8's "still flickering" results for those specific
experiments are void, not evidence against those theories. **Fix: push each file
individually and diff/md5-verify on-device content against the local file before every
build** (`§5`'s command sequence below has been updated to do this).

**With verified deploys, redid the key experiments properly:**
- A truly single, never-repeated render of `0x7F0000` (confirmed via live double-sampled
  `get_rgb_stats` showing the render counter genuinely flat and TIM3/DMA untouched) is
  **steady, no flicker.** The glitch needs *repeated* re-renders of the same color to
  appear at all - this only surfaced now because `rgb_display_tick()` unconditionally re-
  renders every tick regardless of mode (see file header comment), which §8 hadn't
  separated from "does the encoding/DMA path work" until this session added a real render-
  skip DIAG and genuinely deployed it.
- With repeated 50Hz re-renders restored, genuinely re-tested the §8-point-3
  UPDATE-event-sync fix (properly deployed this time): **still flickered - that theory really
  is dead, confirmed for real now, not just believed to be.**
- **Found the actual cause:** `rgb_pwm_show()` called a full `LL_DMA_ResetChannel()` +
  complete reconfigure of GPDMA1 channel 4 on *every single tick* (50Hz). Splitting this into
  a one-time `rgb_pwm_init_dma()` (direction/width/priority/request-line, configured once,
  mirroring how `rgb_pwm_configure_tim()` already configures TIM3 once) plus a per-frame
  `rgb_pwm_rearm_dma()` (only rewrites address/length - the two things a completed transfer
  actually leaves stale - and clears latched flags) **made `0x7F0000` steady at full 50Hz
  resend, verified deploy, confirmed by the user.**
- **But this fix has an unresolved regression: it intermittently breaks the ring entirely
  on a genuine cold power-cycle** (not just an app-container restart - a real unplug/replug
  of the board). Worked once right after a warm app-restart following the successful test
  above; after the user later did a real power-cycle, **nothing lit on either of two
  different physical LED rings**, even solid white, even though every register this file's
  DIAG can read (TIM3 `CR1`/`ARR`/`CCER`/`SR`, GPIOB `MODER`/`AFR[0]` for PB0, DMA `rem`) came
  back exactly as expected. Ruled out as *not* wiring/power: flashing HEAD's known-good
  `rgb_display.cpp` against the **exact same, untouched physical wiring** lit up immediately.
  So the regression is real and in the DMA-once-at-init refactor, not the physical setup.
- **First theory for the regression - also tried and disproved:** guessed a clock-enable
  synchronization race (RM: needs >=2 AHB cycles between enabling a peripheral's clock and
  touching its registers; the old per-frame full reset accidentally self-healed a glitched
  first attempt 20ms later, the once-only init has no such second chance). Added a dummy
  read-back of GPDMA1's clock-enable bit right after `LL_AHB1_GRP1_EnableClock(...)` to force
  synchronization before `rgb_pwm_init_dma()` touches the channel. **Tested across a real,
  user-performed power-cycle: still nothing lit.** This theory is disproved too - don't retry
  it.

**Current state, deliberately conservative:** reverted `base-station/sketch/rgb_display.cpp`
to exactly match `HEAD` (`git checkout -- base-station/sketch/rgb_display.cpp`) and
redeployed+verified it, because the priority at end-of-session was leaving the ring working,
not landing an unfinished fix with a known intermittent full-failure mode. **The
DMA-configure-once/rearm-per-frame fix is NOT in the tree** - it lived only in this session's
working copy and was discarded. Nothing about it is committed; there is no branch/stash to
recover it from - it would need to be re-derived from this section's description if picked
back up (see the `rgb_pwm_init_dma()`/`rgb_pwm_rearm_dma()` split described above; the
change is mechanical and small, maybe 20 lines, given this description).

**For the next session, in order:**
1. **Do not deploy anything without per-file push + md5 verification** (see updated §5
   command below) - this cost most of a session's worth of wasted redeploy cycles once
   already.
2. If picking the BREATHE-glitch fix back up: re-implement the `rgb_pwm_init_dma()` +
   `rgb_pwm_rearm_dma()` split described above (confirmed to fix the flicker under repeated
   50Hz resend of `0x7F0000`, verified deploy). Before trusting it again, it **must** survive
   several real, physical power-cycles (unplug/replug, not app-restarts) with a solid color
   test immediately after each one - the failure mode is intermittent and boot-timing-
   dependent, so one clean boot proves nothing.
3. The clock-enable-race theory is now also ruled out for the cold-boot regression - don't
   retry the dummy-read-back fix. The actual mechanism is still unknown. Candidates not yet
   tried: GPDMA1 or TIM3 register state depending on something else in the boot sequence
   that runs before `rgb_display_start()` (check exact call order in `sketch.ino` across
   `mic_sampler`/`spi_link`/`matrix_display`/`accel_sampler` inits - `rgb_display_start()` is
   documented to run *before* mic_sampler/spi_link, but what runs *before* rgb_display_start
   itself?); or a genuine silicon/LL-driver quirk specific to configuring this exact DMA
   channel only once versus every frame, that only an oscilloscope or a register-dump
   immediately post-cold-boot (before any render) could distinguish from the settled/working
   case.
4. **Do not re-litigate**: the pixel-0/SPI-CSTART fix (§6), the two original bugs (§1), the
   §3 color-drift issue, "is it BREATHE-specific" (§7/§8, resolved: no), the dropped-first-
   request DMA-arm race (§8 point 3, now properly disproved with a verified deploy in this
   section), or the clock-enable-race theory for the cold-boot regression (this section,
   also disproved with a verified deploy). All of these are closed; retrying any of them
   wastes a session.

---

## 10. UPDATE 2026-07-16 (fourth session): flicker bug FIXED for real - survived a genuine
physical power-cycle, which is the exact failure class that killed the previous attempt

**Starting point:** tree was at HEAD (`036274c`, matches §9's deliberate revert) - the
flicker/BREATHE bug from §7/§8 was still open, root-caused in §9 to
`rgb_pwm_show()`'s full `LL_DMA_ResetChannel()` + reconfigure of GPDMA1 channel 4 on every
single 50 Hz tick, but §9's fix (split into one-time `rgb_pwm_init_dma()` + per-frame
`rgb_pwm_rearm_dma()`, dropping the per-frame reset entirely) had an unresolved intermittent
full-ring-dead regression specifically on a genuine cold power-cycle, and was reverted
without being committed.

**New theory, not tried before:** rather than removing the per-frame `LL_DMA_ResetChannel()`
(a deviation from this codebase's own established, cold-boot-proven pattern -
`mic_sampler.cpp`'s `mic_dma_configure_channel()` and `spi_link.cpp`'s
`spi_link_configure_dma()` both do a full reset+reconfigure on *every* transfer too, and
neither has ever shown a cold-boot issue), keep the reset but ask why it only bites *this*
DMA channel. Answer: TIM3 free-runs forever (that's the whole point of the Timer+PWM+DMA
rewrite, §6) and its UPDATE event fires an actual GPDMA hardware request every 1.6us
unconditionally - unlike SPI3/SAI1 (`spi_link`/`mic_sampler`'s peripherals), which only
assert a DMA request while a transfer is actively being clocked by real data movement, i.e.
their request line goes quiet between transfers. Resetting the GPDMA channel while TIM3's
request line is continuously live means a request can land mid-reset against a
not-yet-consistent channel, and for a run of identical CCR values (pure `0x00`/`0xFF`
colors, and `0x7F0000`-repeated-unchanged single tests) that's invisible, but for a byte
with mixed 0/1 bits it can misalign which slot lands against which UPDATE event - matching
§8's "any color byte that isn't pure 0x00/0xFF flickers" finding exactly.

**Fix implemented (`base-station/sketch/rgb_display.cpp`, `rgb_pwm_show()`):**
`LL_TIM_DisableDMAReq_UPDATE(TIM3)` immediately before `rgb_pwm_configure_dma()` (the
existing full reset+reconfigure, left completely unchanged), then
`LL_TIM_EnableDMAReq_UPDATE(TIM3)` right after `LL_DMA_EnableChannel()`. This only gates
the DIER UDE bit (whether an UPDATE event *asserts a DMA request*) - it does **not** touch
TIM3's counter/CEN, so the "never disable the timer, no CSTART-equivalent" invariant from
§6/§9 is fully preserved. Net effect: the codebase's proven-safe full-reset-per-transfer
DMA pattern is kept byte-for-byte, just bracketed so it can never race the one thing that
makes this channel different from every other GPDMA1 user in the sketch (a request source
that never goes quiet).

**Verified on hardware, this session, per-file-push+md5-verified deploy (§5 discipline
followed, one `verify failed` transient on the first flash attempt, self-recovered on
retry same as previous sessions):**
- Held `0x7F0000` and `0x010000` (both confirmed flicker repros from §8) at the normal 50 Hz
  re-render rate: user confirmed **steady, no flicker** on both, `get_rgb_stats` showing
  `rem=0` throughout (transfers still completing cleanly every frame).
- Full `tests/display_rgb_test.py` re-run: user confirmed **BREATHE yellow is now smooth,
  no glitch** - this is the original complaint that opened §7, now resolved. CONST/STROBE
  still correct, pixel-0 still fine (§6's fix untouched).
- **Critical regression check:** user performed a genuine physical power-cycle
  (unplug/replug, not an app/container restart). Container came back `Exited (255)` as
  expected (needs `arduino-app-cli app start` after any real power-cycle, same as the USB
  hiccup noted in §5's footer) - redeployed (clean flash, no verify errors this time), then
  retested: **solid white lit correctly on all 8 pixels, then `0x7F0000` rendered steady
  with no flicker, pixel 0 confirmed fine.** This is the exact test that caught the §9
  fix's regression, and this fix passed it.

**Net result: the flicker/BREATHE bug (§7/§8/§9) is fixed and, unlike §9's attempt, does
not (so far) regress on a real cold boot.** The fix is small (4 lines: 2 LL_TIM calls
bracketing the existing `rgb_pwm_configure_dma()`/`LL_DMA_EnableChannel()` pair) and changes
nothing else about the DMA configuration itself.

**Caveat, be honest about it:** one clean power-cycle is encouraging but is exactly the
same single-data-point trap §9 warns about ("one clean boot proves nothing" - the previous
regression was itself intermittent, not every boot). If this is picked back up again and
something looks wrong after a future power-cycle, don't assume this fix is exonerated by
this one test - repeat the power-cycle test a few more times before fully trusting it,
same standing caution as §9 left behind.

**Not yet committed as of writing this section** - `base-station/sketch/rgb_display.cpp` has
this fix in the working tree; committing is a decision for whoever picks this back up (or
the user, this session, explicitly).

**Do not re-litigate:** the pixel-0/SPI-CSTART fix (§6), the two original SPI-era bugs (§1),
the §3 color-drift issue (still separately deferred), "is it BREATHE-specific" (§7/§8,
resolved: no), the dropped-first-request DMA-arm race and clock-enable-race theories for the
§9 regression (both already disproven there) - none of those are relevant to this fix or
need retesting because of it.

---

## 5. Deploy/debug discipline (reused verbatim this session, still load-bearing)

**UPDATE 2026-07-16 (§9): `adb push <local>/sketch/ $R/sketch/` (the directory-push form
this section used to show) is dangerous - if `$R/sketch/` already exists (it always does
after the first deploy), it silently creates a nested `$R/sketch/sketch/` and pushes there
instead, leaving the actual build directory untouched. An entire session's worth of "tested
this fix" this same day turned out to be retesting unmodified old firmware because of this.
Push per-file and verify md5 before every build - not optional, see §9 for the full story:**
```
R=/home/arduino/ArduinoApps/edgeai-predictive-monitor-base-station
LOCAL=/path/to/base-station/sketch
adb shell "arduino-app-cli app stop $R"
for f in "$LOCAL"/*; do
  adb push "$f" "$R/sketch/$(basename "$f")"
done
# verify every file before building - do not skip this
for f in "$LOCAL"/*; do
  b=$(basename "$f")
  [ "$(md5sum "$f" | awk '{print $1}')" == "$(adb shell "md5sum $R/sketch/$b" | awk '{print $1}')" ] || echo "$b: MISMATCH"
done
adb shell "arduino-app-cli app start $R"       # builds + flashes MCU + starts container
adb shell "echo 'help100S' | sudo -S systemctl restart arduino-router"
adb shell "arduino-app-cli app stop $R"
adb shell "arduino-app-cli app start $R"       # MCU re-registers providers against fresh router
```
Skipping the router-restart leaves Bridge providers *responding* to the first few calls,
then dying mid-session with the same `read_loop`/`invalid start byte` signature as bug #2
above - easy to mistake for a firmware regression when it's actually just a skipped deploy
step.

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
