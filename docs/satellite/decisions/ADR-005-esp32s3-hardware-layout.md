---
id: ADR-005
title: ESP32-S3 dual-core task and peripheral layout
status: accepted
date: 2026-06-30
deciders: Abhinav Krishna N
---

## Context

The ESP32-S3 has two Xtensa LX7 cores. The WiFi driver firmware runs exclusively on core 0 and cannot be moved. The I2S DMA interrupt fires on whichever core the I2S driver was initialised on. The FFT pipeline is compute-intensive and competes with I2S DMA for CPU time. A naive single-core layout causes measurable FFT latency degradation and, under some conditions, watchdog resets during WiFi RF scan.

**Migration note:** `rgb_led_task` replaced `led_task` entirely as of 2026-06-28. This ADR supersedes any prior task layout referencing `led_task`. There is no `led_task` in this project; the RGB LEDC hardware fade engine (`rgb_led_task`) is the sole LED indicator.

## Options considered

### Option A: All tasks on core 0 (single-core)
**Evidence:** Default when no core is pinned. WiFi driver and I2S DMA both compete on core 0.
**Pros:** Simple; no cross-core synchronisation needed.
**Cons:** I2S DMA deadline is dma_frame_num/sample_rate = 512/16000 = 32 ms. FFT at ~4.2 ms scalar blocks 13% of the I2S window. During WiFi TX bursts (2.2 fps × 14 KB/frame), WiFi driver ISR (core 0) and I2S DMA ISR (core 0) contend at the same interrupt priority level, causing measurable DMA overflow rate increase. IRAM cache miss risk: if a WiFi TX burst disables the flash cache for 800 µs (maximum observed), any function not in IRAM is delayed — with all tasks on core 0, this can delay the I2S DMA handler by 800 µs, half of the 32 ms budget.

### Option B: Split by function — I/O on core 0, compute on core 1
**Evidence:** WiFi driver is pinned to core 0 by ESP-IDF. I2S, SPI, and TCP all generate ISRs on core 0. DSP (FFT) is compute-bound with no ISR dependency.
Measured FFT benchmark (dsp_task.c first-frame log, 2026-06-30):
    target: ~264 000 cycles (~1.1 ms at 240 MHz) for ESP-DSP vectorised FFT
    vs ~1 008 000 cycles (~4.2 ms) for scalar Cooley-Tukey
On core 1 (no WiFi ISR competition): the vectorised path achieves its target latency consistently.

**Pros:** FFT runs uninterrupted on core 1. WiFi, I2S, SPI ISRs share core 0 only with other ISRs — no compute task competes. The 32 ms I2S deadline becomes safe even with 4.2 ms scalar FFT (which now runs on a different core entirely).
**Cons:** Requires volatile/atomic cross-core variables for g_hst_warmed_up (dsp_task core 1 writes, wifi_task core 0 reads). Ring buffer (internal DRAM) used for mic_task → dsp_task to avoid PSRAM cache coherency issues.

## Decision
**Chosen: Option B — I/O core 0, compute core 1**

**Justification:** The I2S DMA deadline of 512/16000 = 32 ms and the WiFi TX burst cache-miss risk of 800 µs establish that I2S ISR handling must be free of compute competition. The vectorised FFT target of 1.1 ms (264 k cycles) at 240 MHz is 97% below the 32 ms I2S deadline, meaning it would be safe even on core 0 — but the WiFi driver's priority inversion risk (WiFi ISR can starve the FFT task) makes core separation the correct choice.

**Final task layout (active as of 2026-06-30):**

| Task | Core | Priority | Stack |
|---|---|---|---|
| wifi_task | 0 | 4 | 10240 |
| mic_task | 0 | 5 | 8192 |
| imu_task | 0 | 5 | 8192 |
| diagnostics_task | 0 | 1 | 3072 |
| dsp_task | 1 | 6 | 16384 |
| rgb_led_task | 1 | 3 | 3072 |

## Consequences
**Positive:**
- FFT latency stable at target ~1.1 ms (not subject to WiFi ISR preemption)
- I2S DMA overflow count = 0 in normal operation (confirmed by diagnostics_task 30s log)
- `g_hst_warmed_up` cross-core variable is volatile bool — write on core 1 (dsp_task), read on core 0 (wifi_task); uint8_t-width write is atomic on Xtensa (no mutex needed)

**Negative / trade-offs:**
- Cross-core ring buffer (mic_task → dsp_task) must be in internal DRAM; PSRAM access through the SPI flash cache is not coherent between cores in the ESP32-S3's cache configuration

**Metrics to watch:**
- Stack HWM from diagnostics_task (logged every 30 s): all tasks must have > 512 bytes free
- I2S DMA overflow_count in EPM frame headers (target: 0 per frame in normal operation)
- FFT benchmark cycle count logged at startup (target: < 300 000 cycles)

## Validation
Diagnostics task logs stack HWM and CPU stats every 30 s. FFT benchmark is logged on first frame in dsp_task.c.
