---
id: ADR-009
title: PSRAM memory layout strategy for large static buffers
status: accepted
date: 2026-06-30
deciders: Abhinav Krishna N
---

## Context

The XIAO ESP32-S3 has 8 MB of internal flash, 512 KB of internal SRAM (DRAM), and 8 MB of external PSRAM connected via 8-wire OPI DDR interface (Octal SPI). The PSRAM is accessible as a cache-coherent extension of the address space via CONFIG_SPIRAM_MODE_OCT=y. The firmware's total static buffer footprint exceeds 512 KB if all large arrays are placed in DRAM:

| Buffer | Size | Owner | Access pattern |
|---|---|---|---|
| s_fft[FFT_MIC_N*2] | 8 KB | dsp_task | Hot: every FFT frame |
| s_windowed[FFT_MIC_N] | 2 KB | dsp_task | Hot: every FFT frame |
| s_pwr_acc[FFT_HALF] | 1 KB | dsp_task | Hot: every SPEC_AVG_N frame |
| s_scratch[FFT_MIC_N] | 2 KB | mic_task | Hot: every SIMD stats frame |
| s_enc_pt[EPM_PLAIN_LEN] | ~14 KB | wifi_task | Per-frame, DMA-required |
| s_enc_ct[EPM_PLAIN_LEN] | ~14 KB | wifi_task | Per-frame, DMA-required |
| s_frame (imu_task) | 12 KB | imu_task | Written once per SPI poll cycle |
| s_mag_db[FFT_HALF] | 2 KB | dsp_task | Written once per SPEC_AVG_N frames |
| Ring buffer storage | 8 KB | mic→dsp handoff | One item in flight at a time |

Total if all in DRAM: ~63 KB static + FreeRTOS stacks (total 58 KB) + heap overhead → exceeds safe DRAM budget.

## Options considered

### Option A: All buffers in DRAM (default)
**Evidence:** Default linker placement: all statics in DRAM unless attributed otherwise.
**Pros:** Maximum access speed; no cache miss risk; DMA-compatible by default.
**Cons:** 63 KB static buffers + 58 KB stacks + 40 KB heap minimum ≈ 161 KB — leaves only 351 KB of DRAM for TCP buffers, mbedTLS state, WiFi driver buffers (~80 KB), and FreeRTOS kernel structures. Tight enough that a single mis-sized heap allocation causes malloc failure. No headroom for future sensors.

### Option B: All large buffers in PSRAM via heap (esp_heap_caps_malloc)
**Evidence:** Allocate large buffers dynamically with `heap_caps_malloc(size, MALLOC_CAP_SPIRAM)`.
**Pros:** Does not consume DRAM.
**Cons:** Dynamic allocation at boot means null-check required everywhere; a PSRAM malloc failure is a runtime crash rather than a link-time error. SIMD instructions (dsps_dotprod_f32 etc.) operate on PSRAM through the OPI DDR cache — cache misses during sequential SIMD access can degrade throughput by 2–3×. AES GDMA buffers cannot be in PSRAM (hardware constraint — DMA cannot address PSRAM on ESP32-S3 without GDM redesign).

### Option C: Selective EXT_RAM_BSS_ATTR for cold-path write-once buffers only
**Evidence:** `EXT_RAM_BSS_ATTR` places static arrays in the `.ext_ram.bss` linker section, which maps to PSRAM. Unlike dynamic heap allocation, placement is determined at link time (crash-safe) and symbol size is visible in the map file.

**Placement rule:** A buffer goes to PSRAM if and only if ALL three conditions hold:
1. Not required by GDMA (DMA_ATTR buffers MUST be in internal DRAM)
2. Not in the hot FFT loop path (avoids PSRAM cache miss penalty during SIMD)
3. Large enough to matter (> 4 KB saves meaningful DRAM)

Applying the rule:

| Buffer | PSRAM? | Reason |
|---|---|---|
| s_fft[FFT_MIC_N*2] (8 KB) | NO | Hot FFT path — cache miss during SIMD |
| s_windowed[FFT_MIC_N] (2 KB) | NO | Hot FFT path |
| s_pwr_acc[FFT_HALF] (1 KB) | NO | Hot FFT path |
| s_scratch[FFT_MIC_N] (2 KB) | NO | Hot SIMD path in mic_task |
| s_enc_pt[EPM_PLAIN_LEN] (~14 KB) | NO | DMA_ATTR required for GDMA |
| s_enc_ct[EPM_PLAIN_LEN] (~14 KB) | NO | DMA_ATTR required for GDMA |
| s_frame imu_task (12 KB) | YES | Written once per SPI cycle (~50 ms); not in FFT loop |
| s_mag_db[FFT_HALF] (2 KB) | YES | Written once per SPEC_AVG_N frames; not in hot loop |
| Ring buffer storage (8 KB) | NO | Cross-core handoff — PSRAM cache coherency not guaranteed between cores on ESP32-S3 |

Savings: 14 KB moved to PSRAM → DRAM headroom increases by 14 KB. Free internal heap at boot (logged by main.c): target > 200 KB DRAM free.

**Pros:** Link-time placement (map file shows sizes); no runtime null-check; 14 KB DRAM saved; hot path buffers remain in fast internal DRAM.
**Cons:** `EXT_RAM_BSS_ATTR` requires CONFIG_SPIRAM=y and CONFIG_SPIRAM_ALLOW_BSS_SEG_EXTERNAL_MEMORY=y in sdkconfig; must be in sdkconfig.defaults.

## Decision
**Chosen: Option C — selective EXT_RAM_BSS_ATTR for cold-path output buffers**

**Justification:** The three-condition rule (no DMA, not in hot loop, > 4 KB) identifies exactly two buffers that safely belong in PSRAM: `s_frame` (12 KB, imu_task) and `s_mag_db` (2 KB, dsp_task). All hot-path SIMD and DMA buffers stay in internal DRAM. This frees 14 KB of internal DRAM without any performance impact on the FFT pipeline.

**sdkconfig.defaults additions:**
```
CONFIG_SPIRAM_MODE_OCT=y
CONFIG_SPIRAM=y
CONFIG_SPIRAM_ALLOW_BSS_SEG_EXTERNAL_MEMORY=y
```

## Consequences
**Positive:**
- 14 KB of internal DRAM freed for heap (WiFi/mbedTLS buffers)
- Link-time verification: if PSRAM is not available, the linker fails at build time
- No performance impact on DSP pipeline (hot buffers remain in DRAM)

**Negative / trade-offs:**
- `s_frame` access latency is ~3–5× higher than DRAM on a cache miss, but IMU SPI polling period (~50 ms) makes this negligible
- Must not place cross-core ring buffer in PSRAM — ESP32-S3 OPI DDR cache is per-core; cross-core coherency is not guaranteed for PSRAM accesses without explicit cache flush

**Metrics to watch:**
- DRAM free at boot (main.c log: `DRAM free=%lu`): target > 200 KB
- PSRAM free at boot (main.c log: `PSRAM free=%lu`): target > 6 MB (8 MB - ~14 KB static - Python gateway allocation)
- imu_task stack HWM: s_frame is now off-stack; HWM should improve vs DRAM stack placement

## Validation
`imu_task.c` — `static EXT_RAM_BSS_ATTR imu_frame_t s_frame`. `dsp_task.c` — `static EXT_RAM_BSS_ATTR float s_mag_db[FFT_HALF]`. `main.c` — boot log prints DRAM free and PSRAM free via `heap_caps_get_free_size()`.
