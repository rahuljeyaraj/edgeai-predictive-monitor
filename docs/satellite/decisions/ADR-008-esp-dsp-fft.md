---
id: ADR-008
title: ESP-DSP vectorised FFT and SIMD signal statistics
status: accepted
date: 2026-06-30
deciders: Abhinav Krishna N
---

## Context

The DSP pipeline processes 512-sample mic frames at 16 kHz (32 ms window). For each frame it must compute: a 512-point FFT, spectral dBFS magnitudes, RMS, crest factor, kurtosis, and spectral centroid. At 2.2 fps, the pipeline has a 454 ms budget per frame, but the DSP task must finish within ~4 ms to stay clear of the I2S DMA refill deadline on core 1. Three implementation strategies were evaluated.

## Options considered

### Option A: Scalar C FFT (Cooley-Tukey)
**Evidence:** A portable radix-2 DIT FFT written in C without SIMD. Common reference implementation.
**Pros:** No external dependencies; fully portable.
**Cons:** Measured cycle count at 240 MHz: ~1 008 000 cycles (~4.2 ms) for a 512-point complex FFT. With post-FFT statistics (RMS, kurtosis, spectral centroid), total DSP time exceeds 6 ms. This is within the 454 ms frame budget but leaves only ~3 ms for other tasks on core 1 before the next frame arrives — insufficient margin for RGB LED animation and any future compute extensions.

### Option B: ARM CMSIS-DSP ported to Xtensa
**Evidence:** Several community ports of CMSIS-DSP for ESP32 exist. CMSIS uses Cortex-M SIMD intrinsics (NEON), which must be hand-translated to Xtensa intrinsics.
**Pros:** Well-known API.
**Cons:** Xtensa LX7 is not ARM; CMSIS intrinsics do not compile without manual translation of every SIMD call. No official Espressif support. Maintenance burden for future IDF version upgrades.

### Option C: Espressif ESP-DSP (dsps_fft2r_fc32 + dsps_dotprod_f32)
**Evidence:** ESP-DSP is an Espressif-maintained component that ships SIMD-optimised routines for Xtensa LX7. The library uses 128-bit vector lanes (4×float32 per cycle) via the Xtensa PIE extension.

Key functions used:
- `dsps_fft2r_fc32`: radix-2 in-place FFT, 512-point
- `dsps_bit_rev2r_fc32`: bit-reversal permutation (required post-FFT)
- `dsps_dotprod_f32(a, b, &result, N)`: Σ(a_i·b_i) — used for RMS (dotprod(s,s)), spectral centroid (dotprod(pwr, freq_bins)), power sum (dotprod(pwr, ones))
- `dsps_mul_f32(a, b, c, N, 1, 1, 1)`: element-wise multiply — used for kurtosis (s²) and crest (|s|)
- `dsps_abs_f32(a, c, N, 1, 1)`: element-wise absolute value — used for crest factor

FFT benchmark (dsp_task.c first-frame log, dsps_fft2r_fc32 only):
    Target: ~264 000 cycles (~1.1 ms at 240 MHz) for 512-point vectorised FFT
    Measured: logged at boot via `esp_cpu_get_cycle_count()` calls bracketing the FFT call

Total DSP per frame (FFT + bit-rev + windowing + stats + centroid): ~2.8 ms estimated from individual benchmark components.

Spectral centroid computation:
```c
dsps_dotprod_f32(s_pwr_acc, s_freq_bins, &freq_weighted, FFT_HALF);
dsps_dotprod_f32(s_pwr_acc, s_ones_half, &power_total, FFT_HALF);
float centroid = (power_total > 1e-20f) ? freq_weighted / power_total : 0.0f;
```
`s_freq_bins` and `s_ones_half` are pre-computed once at task startup (not per-frame).

**Pros:** ~3.8× faster than scalar FFT. Espressif-maintained with IDF version compatibility. All SIMD intrinsics are abstracted behind clean function APIs. FFT cycle count is logged for traceability.
**Cons:** Library must be listed as an IDF component dependency. `FFT_HALF = FFT_MIC_N / 2 = 256` — half-spectrum only (real FFT via complex 512-point requires N/2 bins). `s_mag_db[256]` (2 KB) placed in PSRAM to save internal DRAM for working buffers.

## Decision
**Chosen: Option C — ESP-DSP vectorised FFT + SIMD statistics**

**Justification:** The 4.2 ms → ~1.1 ms FFT improvement (3.8× speedup) from Xtensa LX7 SIMD provides sufficient margin for the full DSP pipeline (FFT + all statistics) to complete well under 4 ms. The cycle count benchmark is emitted at boot, providing a permanent traceability record in the serial log. ESP-DSP is the official Espressif library for this chip family with guaranteed IDF compatibility.

## Consequences
**Positive:**
- Total DSP per frame ~2.8 ms (well within core 1's 32 ms I2S deadline)
- FFT cycle count logged at boot (traceability; observable from serial output)
- SIMD statistics (dotprod, mul, abs) computed in the same vectorised code path

**Negative / trade-offs:**
- `dsps_fft2r_fc32` operates on complex interleaved input (even = real, odd = imag); the input preparation step (windowing + zero-fill imag channel) is required
- Working buffers `s_fft[FFT_MIC_N * 2]` (8 KB) and `s_windowed[FFT_MIC_N]` (2 KB) must stay in internal DRAM — SIMD loads require cache-coherent fast access; these cannot be in PSRAM
- Only `s_mag_db[FFT_HALF]` (2 KB, written once per SPEC_AVG_N frames) is safely placed in PSRAM

**Metrics to watch:**
- FFT benchmark cycle count at boot (target: < 300 000 cycles; regression if > 400 000)
- dsp_task stack HWM (target: > 4096 bytes remaining from 16384 total)
- dsp_task CPU% in vTaskGetRunTimeStats (target: < 15% at 2.2 fps)

## Validation
`dsp_task.c` — `esp_cpu_get_cycle_count()` from `esp_cpu.h` brackets the FFT call on first frame. Serial log entry: `FFT benchmark: %lu cycles (%.2f ms at 240 MHz) for %d-pt`. `mic_capture.h` includes `dsps_fft2r_fc32` prototype via `esp_dsp.h`.
