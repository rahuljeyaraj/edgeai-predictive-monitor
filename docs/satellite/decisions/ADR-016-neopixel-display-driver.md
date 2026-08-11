---
id: ADR-016
title: WS2812 (NeoPixel) status LED replaces plain-LEDC RGB as the default display driver
status: accepted
date: 2026-08-04
deciders: Abhinav Krishna N
---

## Context

Interop parity with the reference repo calls for adopting its NeoPixel (WS2812) status ring in place of our plain-LEDC RGB LED (ADR-006), so status meaning is identical on both sides of the interop wire: the reference's gateway (`base-station/python/registry/status_color.py`) pushes `(rgb, mode, period_ms)` STATUS_LED commands driven by `NodeStatus`, and its satellite firmware (`satellite/src/drivers/rgb_ws2812.cpp` behind `satellite/include/hal/hal_display_rgb.h`) renders them on a WS2812 ring using the exact color values status_color.py's own docstring says were hand-tuned for raw (uncorrected) WS2812 rendering — a screen-friendly desaturated color (e.g. Tailwind emerald-500) reads visibly wrong on this hardware, so matching his exact `0xRRGGBB` values, not just his intent, is what makes status meaning actually identical.

`display_ledc.c` (ADR-006) has no addressable-LED analog to any of this — it drives three separate PWM channels with a hardware fade engine, which cannot be driven by an `(rgb, mode, period_ms)` command in the first place. It is kept in the tree as a Kconfig fallback (`CONFIG_EPM_DISPLAY_USE_LEDC`) for boards without WS2812 hardware wired up, mirroring `accel_stub.c`'s `EPM_ACCEL_USE_STUB` pattern.

Our `hal_display.h` contract (post Task 0 fix, this same session) is task-owning — `led_task.c` calls `rgb_led_task_init()` then hands `rgb_led_task()` directly to `xTaskCreatePinnedToCore` — unlike the reference's `hal_display_rgb.h`, which exposes a `set()`/`tick()` pair driven by an external caller (its MQTT command handler calls `set()`, a periodic caller elsewhere calls `tick()`). The reference's CONST/BREATHE/STROBE color math (cosine breathe, square-wave strobe) is chip-agnostic and worth porting verbatim; the set()/tick() split is not, since it doesn't fit our contract shape.

## Options considered

### Option A: Keep display_ledc.c as the default, do not port NeoPixel
**Pros:** Zero new work, zero new managed-component dependency, already proven on hardware (ADR-006).
**Cons:** Directly contradicts the interop-parity decision (already made, not to be re-litigated) and leaves status colors meaning different things on the two sides of the wire — the entire point of the interop merge is that a node's status is legible the same way regardless of which firmware produced it.

### Option B: Hand-roll WS2812 bit-timing via driver/rmt_tx.h directly
**Evidence:** ESP-IDF 5.x's RMT TX driver supports a custom `rmt_bytes_encoder_config_t` (bit0/bit1 symbol durations) plus a second encoder for the >50µs reset code, which is how Espressif's own `led_strip` component is implemented internally.
**Pros:** No new managed-component dependency; consistent with this project's general preference for hand-written drivers against native `driver/*.h` headers (mic, LEDC, and this phase's own SPI accel driver all do this).
**Cons:** WS2812 reset-code sequencing (a composite encoder that switches from bytes-encoding to a fixed low-duration copy-encoder mid-transmission) is a nontrivial state machine to get right, and any subtle timing bug is very hard to diagnose without a logic analyzer or working hardware in the loop during this session (see Validation). Reimplementing it by hand adds real risk for no behavioral gain over Option C, which is the same underlying RMT mechanism Espressif already validated.

### Option C: espressif/led_strip managed component (RMT-backed)
**Evidence:** `espressif/led_strip` is Espressif's own IDF-Component-Registry package, the documented modern replacement for hand-rolled WS2812 RMT bit-banging on IDF 5.x. This project already depends on one Espressif managed component the same way (`espressif/esp-dsp`, declared in `src/idf_component.yml`, consumed via `REQUIRES espressif__esp-dsp` — see `src/CMakeLists.txt`), so adding a second is not a new pattern.
**Pros:** The RMT encoder state machine (byte encoding + reset-code sequencing) is Espressif-maintained and already correct; this driver only has to call `led_strip_set_pixel()`/`led_strip_refresh()`/`led_strip_clear()`, which is the same abstraction level as calling `driver/i2s_std.h` for the mic or `driver/spi_master.h` for the accelerometer — a native ESP-IDF hardware-protocol driver behind our own HAL contract, not a hand-bit-banged implementation either way.
**Cons:** One more managed-component version to track (`>=2.5.0` in `src/idf_component.yml`); its API surface (`led_strip_new_rmt_device`, `led_strip_config_t`/`led_strip_rmt_config_t`) is a small amount of vendor-controlled surface this driver now depends on.

## Decision
**Chosen: Option C — espressif/led_strip, single-pixel default**

**Color/mode table** (`components/epm_drivers/display_neopixel.c`), matching `status_color.py`'s `_LED_BY_STATUS` values exactly where a `NodeStatus` analog exists:

| `rgb_led_state_t` | rgb | mode | period_ms | Reference analog |
|---|---|---|---|---|
| `RGB_OK` | `0x00FF00` | CONST | 0 | `_GREEN_HEALTHY` (HEALTHY) |
| `RGB_WARN` | `0xF59E0B` | BREATHE | 1500 | `_YELLOW_WARNING_BREATHE` (WARNING) |
| `RGB_FAULT` | `0xFF0000` | STROBE | 200 | `_RED_FAULT_STROBE` (FAULT) |
| `RGB_CALIBRATING` | `0x22D3EE` | CONST | 0 | `_CYAN_NEW` (UNCOMMISSIONED / COMMISSIONING_COLLECTING) |
| `RGB_LEARNING` | `0x22D3EE` | BREATHE | 800 | `_CYAN_NEW` (COMMISSIONING_TRAINING) |
| `RGB_BOOT` | `0xFFFFFF` | CONST | 0 | none |
| `RGB_WIFI_CONN` | `0x0000FF` | BREATHE | 1200 | none |
| `RGB_TCP_CONN` | `0x0000FF` | STROBE | 300 | none |
| `RGB_TRIPPED` | `0xFF00FF` | STROBE | 150 | none |

`status_color.py`'s `NodeStatus` enum has no member for "not yet reachable at all" (BOOT/WIFI_CONN/TCP_CONN — the node can't receive a STATUS_LED command until it has a TCP/MQTT link) or for a local safety-alarm condition (TRIPPED — a firmware-local vibration/threshold trip, not a registry status). These four keep locally-chosen colors: the two connectivity states share the blue family with CONST-vs-mode differentiation (mirrors `display_ledc.c`'s retired blue-wifi/cyan-tcp pattern, recolored off cyan since that value is now reserved for `RGB_CALIBRATING`), and `RGB_TRIPPED` uses a fast magenta strobe deliberately distinct from `RGB_FAULT`'s red strobe so an operator cannot confuse "sensor fault" with "alarm tripped" at a glance.

`RGB_CALIBRATING` and `RGB_LEARNING` both collapse onto the reference's single `_CYAN_NEW` on the wire (his registry doesn't distinguish commissioning sub-phases either), but locally use CONST vs BREATHE to still give on-device visual feedback that the two phases differ, without deviating from the shared color.

**Pin:** WS2812 DIN = GPIO6 (D5) — reuses `display_ledc.c`'s `RGB_LED_B_GPIO`. Safe because the two display drivers are Kconfig-exclusive (`CONFIG_EPM_DISPLAY_USE_LEDC`), never compiled in together, so there is no runtime conflict; it does not collide with the INMP441 mic's permanent GPIO2/3/4 claim.

**Pixel count:** defaulted to 1, not the reference's 8. The reference's `RING_NUM_PIXELS 8` is justified in its own source comment as a *user-confirmed* match to that specific board's ring hardware — that confirmation is specific to their board and does not transfer to ours. Our physical WS2812 hardware (single LED vs. ring, and if a ring, how many pixels) has not been confirmed against real hardware in this session (see Validation). A single pixel is the conservative default: correct either way, since every pixel in the chain is always set identically, and it cannot overrun a shorter real chain the way guessing 8 could.

## Consequences
**Positive:**
- Status colors are now byte-identical to the reference's `status_color.py` for every state with a wire meaning — a dashboard operator sees the same color for the same status regardless of which firmware produced it.
- `display_ledc.c` remains available (`CONFIG_EPM_DISPLAY_USE_LEDC=y`) with zero changes to its own logic, for boards without WS2812 hardware.

**Negative / trade-offs:**
- New managed-component dependency (`espressif/led_strip`), resolved by the IDF Component Manager at build time — requires network access on first build (same as the existing `espressif/esp-dsp` dependency already does).
- `WS2812_NUM_PIXELS` is a placeholder pending physical hardware confirmation; if the real board has more than one pixel, only pixel 0 will be driven correctly today (all pixels are set identically, so this degrades to "only the first physical LED lights up" rather than a wrong color, but is still a known gap).
- Animation now polls at a fixed 30 ms tick (`ANIM_TICK_MS`) during BREATHE/STROBE rather than being purely interrupt-driven like `display_ledc.c`'s LEDC hardware fade engine — a small, bounded CPU cost (task wakes every 30 ms only while in a non-CONST state) that the LEDC design avoided entirely via hardware fade completion callbacks.

**Metrics to watch:**
- Actual WS2812 pixel count and product, once physical hardware is confirmed — update `WS2812_NUM_PIXELS` accordingly.
- `rgb_led_task` stack HWM and CPU time in `vTaskGetRunTimeStats`, now that the task self-paces via a 30 ms timeout instead of blocking indefinitely on ISR notification during animated states.

## Validation
Hardware was available and flashed. Confirmed on-device: `led_strip_new_rmt_device()` claims the RMT channel and initialises cleanly every boot (`WS2812 init: DIN=GPIO6, 1 pixel(s)`, zero errors) across every capture in this session, including a 357-epoch / 90 s sustained run with the real KX134 driver active alongside it — no RMT/DMA contention observed. `rgb_led_set_state()` call sites and the color table were checked line-by-line against `status_color.py`'s convention and match.

Not confirmed: the actual physical LED color output. No camera/visual channel was available in this session to verify the RMT-driven WS2812 actually renders the intended colors (vs., e.g., a channel-order or gamma mismatch that would compile and run cleanly but look wrong) — this still needs a human to look at the board. `tests/host/` does not cover this driver (ESP-IDF/RMT-dependent, not host-testable).

## Addendum (2026-08-05, Phase 7c)

Physical hardware confirmed by the user: an 8-LED WS2812 ring, not a single
pixel. `WS2812_NUM_PIXELS` updated from 1 to 8
(`components/epm_drivers/display_neopixel.c`) — the "Metrics to watch" item
this ADR flagged for exactly this update. All 8 pixels are driven
identically (`display_neopixel.c`'s per-pixel loop was already written for
this, per this ADR's Decision section), so the change is a one-line count
update, not a logic change.

## Addendum (2026-08-06)

Found by re-checking against the reference repo's actual
`base-station/python/registry/status_color.py` (fetched directly, not
assumed): the Decision table above
was wrong about `RGB_TRIPPED` having "no NodeStatus analog." The reference
file does define a `TRIPPED` entry:

```python
_RED_TRIPPED_SLOW = ("#ff0000", strobe, 1000)  # TRIPPED
```

(the reference implementation's own comment on this line: "1000ms as a
deliberate, latched 'I already
acted'"). Our `RGB_TRIPPED` was `{0xFF00FF, MODE_STROBE, 150}` — a
different color (magenta) *and* a different, much faster period. Worse,
that magenta was byte-identical to the reference's `_MAGENTA_IDLE` (`"#ff00ff", const,
0`, for `IDLE`), so it was a genuine cross-status color collision, not
merely "no equivalent chosen locally." Corrected to
`{0xFF0000, MODE_STROBE, 1000}` — exact match to `_RED_TRIPPED_SLOW`.

Separately, and by Abhinav's explicit instruction to prioritize exact
color-scheme parity: `RGB_LEARNING` (previously `{0x22D3EE, MODE_BREATHE,
800}`) is collapsed to `{0x22D3EE, MODE_CONST, 0}`, identical to
`RGB_CALIBRATING`. `status_color.py` uses `_CYAN_NEW` (CONST) for every
commissioning sub-phase (`UNCOMMISSIONED` / `COMMISSIONING_COLLECTING` /
`COMMISSIONING_TRAINING`) with no BREATHE distinction anywhere in it. The
original Decision section's CONST-vs-BREATHE split was a deliberate choice
to give the two local commissioning sub-phases distinct on-device visual
feedback without deviating from the shared wire color — that tradeoff is
now deliberately reversed in favor of byte-identical parity with
`status_color.py`. This is a reversal of a considered decision, not a bug
fix: BREATHE at CALIBRATING-vs-LEARNING boundaries was working as designed.

The Decision table's `| Reference analog |` column is corrected
accordingly: `RGB_TRIPPED` now reads `_RED_TRIPPED_SLOW (TRIPPED)` instead
of `none`, and `RGB_LEARNING`'s mode is CONST instead of BREATHE. (Per this
project's append-only ADR convention, the original table text is left as
written above; this addendum is the record of what changed and why.)

No new `rgb_led_state_t` entries were added for `status_color.py`'s
`PAUSED`, `OFFLINE`, or `IDLE` (`_GREY_PAUSED`, `_GREY_OFFLINE`,
`_MAGENTA_IDLE`). These are gateway-decided statuses our satellite firmware
has no local trigger condition for — nothing in our own state machine ever
decides "I am paused" or "I am offline" from the inside. The existing
`rgb_led_set_remote()` path (arbitrary `(rgb, mode, period_ms)` pushed over
MQTT, wired since Phase 7c) already renders any of these correctly if a
real base station ever sends one; this is not a gap needing a pre-emptive
local entry, just three statuses this side never originates on its own.

Hardware note: current physical hardware is a temporary WS2812B-64 (8x8
matrix) stand-in for the real 8-LED ring — `WS2812_NUM_PIXELS` stays at 8,
matching the reference's `satellite/src/drivers/rgb_ws2812.cpp`
(`RING_NUM_PIXELS 8`) exactly, and is not changed by this addendum. Driving
8-pixel data into the 64-LED stand-in lighting up only a subset of its
physical LEDs is expected given that mismatch, not a bug to chase. No
visual/hardware re-validation of this color and timing change was possible
in this session (no real ring available yet) — the "Not confirmed" gap this
ADR's own Validation section already flagged for physical LED color output
remains open, now also covering `RGB_TRIPPED`'s and `RGB_LEARNING`'s new
values specifically.
