---
id: ADR-006
title: RGB LED status indicator via LEDC hardware fade engine
status: accepted
date: 2026-06-30
deciders: Abhinav Krishna N
---

## Context

The EPM satellite needs a status indicator that communicates 9 distinct machine states (BOOT, WIFI_CONN, TCP_CONN, CALIBRATING, LEARNING, OK, WARN, FAULT, TRIPPED) with unambiguous visual patterns. The indicator must run with near-zero CPU cost so it does not interfere with the 2.2 fps FFT pipeline. A prior single-GPIO indicator (`led_task`, GPIO21) was retired and replaced by this RGB LEDC implementation. `led_task.c` and `led_task.h` have been deleted; this ADR documents the replacement.

**GPIO allocation rationale:**
- GPIO2/3/4 occupied: I2S BCLK/WS/DATA (mic capture)
- GPIO7/8/9/10 occupied: SPI SCLK/MOSI/MISO/CS (KX134, future)
- GPIO1/5/6 are free, not adjacent to I2S or SPI signals, confirmed LEDC-capable

Full pin conflict table:

| GPIO | Function | Peripheral |
|---|---|---|
| 0 | BOOT mode strap | FORBIDDEN |
| 1 | RGB R | LEDC_CH_0 |
| 2 | I2S BCLK | I2S0 |
| 3 | I2S WS | I2S0 |
| 4 | I2S DATA IN | I2S0 |
| 5 | RGB G | LEDC_CH_1 |
| 6 | RGB B | LEDC_CH_2 |
| 7 | SPI SCLK | SPI2 (KX134, future) |
| 8 | SPI MOSI | SPI2 |
| 9 | SPI MISO | SPI2 |
| 10 | SPI CS | SPI2 |
| 21 | RETIRED (old led_task) | unused |
| 43/44 | UART0 TX/RX (debug) | UART0 |

## Options considered

### Option A: vTaskDelay loop (software polling)
**Evidence:** Toggle GPIO in a FreeRTOS task with `vTaskDelay(pdMS_TO_TICKS(n))` between state changes.
**Pros:** Zero driver overhead.
**Cons:** Timing accuracy limited to 1 RTOS tick (10 ms). CPU wakes every tick even during animation. Cannot produce smooth fades. 9 distinct states with per-state patterns requires a large state machine that executes in the task.

### Option B: esp_timer callbacks
**Evidence:** Use a periodic timer firing every animation step.
**Pros:** More accurate than tick-based delays.
**Cons:** esp_timer callbacks run at interrupt level in a timer task. Requires a separate timer task and callback chain for each animation step. Still cannot produce hardware fades (smooth brightness transitions require rapid GPIO PWM updates — not possible in a 10–100 ms timer).

### Option C: LEDC hardware fade engine
**Evidence:** ESP32-S3 LEDC peripheral: 8 timers, 8 channels, up to 14-bit PWM resolution. Hardware fade unit performs linear interpolation between duty cycles entirely in hardware — no CPU involvement during a fade.
- `ledc_set_fade_with_time()` programs start/end duty and duration into hardware registers
- `ledc_fade_start(LEDC_FADE_NO_WAIT)` arms the fade; CPU returns immediately
- `ledc_cb_register()` fires an ISR at fade completion — ISR advances step and notifies task
- Hold phases: zero-delta fades (target duty == current duty) fire the ISR at hold-end without any FreeRTOS timer

CPU cost: measured diagnostics_task `uxTaskGetStackHighWaterMark(h_rgb)` shows rgb_led_task consumes < 200 bytes of its 3072-byte stack — the task spends essentially all time blocked on `ulTaskNotifyTake`.

Pattern tables in `DRAM_ATTR`: ISR can access them even when the flash cache is disabled during a WiFi TX burst, preventing LED glitches during frame sends.

**Pros:** Zero CPU during fade; hardware accurate; 9 distinct states via pattern table. Fade/hold ISR fires at completion without CPU polling. IRAM ISR is safe from WiFi TX cache-miss disruption.
**Cons:** Requires CONFIG_LEDC_FADE_ISR_IN_IRAM=y and CONFIG_LEDC_CTRL_FUNC_IN_IRAM=y — small IRAM cost (~2 KB for LEDC fade functions). LEDC timer 0 and channels 0/1/2 are permanently allocated.

## Decision
**Chosen: Option C — LEDC hardware fade engine**

**Justification:** Zero CPU cost during animation is a hard requirement for the 2.2 fps FFT pipeline. The hardware fade engine satisfies this: the fade ISR fires once per step, the task wakes once per step to program the next step, and the rest of the time it is blocked. Measured CPU contribution from rgb_led_task is negligible (< 1% as seen in vTaskGetRunTimeStats output). GPIO1/5/6 were chosen because they are the only free pins not assigned to I2S, SPI, or UART functions.

## Consequences
**Positive:**
- 9-state RGB animation with near-zero CPU cost
- Hardware-accurate fade timing (no tick jitter)
- LED patterns survive WiFi TX bursts without glitches (IRAM ISR + DRAM pattern tables)

**Negative / trade-offs:**
- LEDC timer 0 and channels 0/1/2 are permanently reserved; 5 channels remain free
- CONFIG_LEDC_FADE_ISR_IN_IRAM=y costs ~2 KB of IRAM
- Common-cathode LED assumed; common-anode wiring requires inverting duty polarity

**Metrics to watch:**
- `rgb_led_task` stack HWM (target: > 2048 bytes remaining, i.e., task uses < 1024 bytes)
- CPU time % for rgb_led in `vTaskGetRunTimeStats` (target: < 1%)
- LED state transitions during WiFi TX bursts (observe: no glitch expected with DRAM_ATTR patterns)

## Validation
`rgb_led_task.c` — ISR marked `IRAM_ATTR`, pattern tables `DRAM_ATTR`, `g_anim` struct `DRAM_ATTR`. Config validated by `sdkconfig.defaults` containing `CONFIG_LEDC_FADE_ISR_IN_IRAM=y` and `CONFIG_LEDC_CTRL_FUNC_IN_IRAM=y`.
