---
id: ADR-017
title: Real KX134-1211 SPI accelerometer driver, accel_stub.c kept as Kconfig fallback
status: accepted
date: 2026-08-04
deciders: Abhinav Krishna N
---

## Context

Interop parity with the reference repo calls for porting its KX134-1211 driver (`satellite/src/drivers/kx134.cpp`, Arduino/SPI.h + `attachInterrupt`) behind our own `hal_accel.h` contract as `components/epm_drivers/accel_kx134_spi.c`, replacing `accel_stub.c` (synthetic per-axis sine+noise generator) as the compiled driver whenever `CONFIG_EPM_ACCEL_USE_STUB` is disabled. `accel_stub.c` stays in the tree, Kconfig-selectable, as the dev fallback for building/testing without hardware.

The register map, bit-field values, and CNTL1/ODCNTL/INC1/INC4/BUF_CNTL1/BUF_CNTL2 init sequencing are chip-datasheet facts (KX134-1211 TRM Rev 5.0), not MCU-specific — the reference's own header comment makes the same point about its own Arduino port relative to a third `mcu/` Zephyr implementation. ODR = 12800 Hz (`OSA<3:0> = 1110`) is hardware-validated on the reference's own boards and adopted as-is, not re-derived.

**Two real design decisions this driver has to make that the reference's code doesn't answer directly:**

1. **Per-axis contract vs. interleaved FIFO hardware.** Our `hal_accel.h` (already fixed pre-Task-0, unchanged this session) is deliberately per-axis float — `hal_accel_read_block(axis, out, max_samples)` — because `accel_stub.c` generates a distinct tuned signal per axis and `src/threads/imu_task.c`'s FFT pipeline consumes one axis at a time. The reference's own `hal_accel.h` is interleaved raw `int32_t` instead, matching its FIFO's physical layout exactly (`X_L,X_H,Y_L,Y_H,Z_L,Z_H` per frame) with no per-axis reshaping. The real KX134 FIFO cannot be told to return only one axis, so this driver has to reshape interleaved hardware output into three independent per-axis streams itself — the reference has no code for this because its own contract never required it.
2. **Raw counts vs. normalised-g float.** The reference passes raw sign-extended `int16_t` counts straight through, unconverted (confirmed by reading `satellite/src/threads/accel_sampler_task.cpp` — FFT/scalar-stats there operate directly on raw counts). Our `hal_accel.h` requires normalised-g float. This conversion has no reference implementation to port; it has to be derived from the datasheet.

**GPIO pin plan.** SCK/MISO/MOSI = GPIO7/8/9 (D8/D9/D10) match the reference's own choice for the same XIAO ESP32S3 hardware SPI bus — no conflict, adopted as-is. CS and INT1 do **not** reuse the reference's D3/D2 (GPIO4/GPIO3): those are permanently claimed on our board by the INMP441 mic (`mic_inmp441_i2s.c`: BCLK=GPIO2, WS=GPIO3, SD=GPIO4 — the reference's own mic wiring differs from ours, so its accel pin choices, which were made to avoid *its* mic pins, don't avoid *ours*). CS=GPIO43 (D6), INT1=GPIO44 (D7) instead — confirmed free (`platformio.ini`: this board's console runs over USB-JTAG/CDC, not UART0, per that file's own comment).

## Options considered

### Option A: Keep accel_stub.c as the only/default driver
**Pros:** Zero risk, already working, no hardware dependency.
**Cons:** Directly contradicts the interop-parity decision (already made) and defeats the entire purpose of this work — a synthetic sine-wave stub cannot detect real bearing faults, and interop testing against the reference's real sensor data is impossible with synthetic input.

### Option B: Match the reference's interleaved raw-int32_t contract instead of ours
**Pros:** Direct 1:1 port of `kx134.cpp`'s `hal_accel_read_block()`, no reshaping logic needed.
**Cons:** Would require changing `hal_accel.h` (out of scope — that header is already pure C, already correct, and needs no changes here) and would break `accel_stub.c` and every existing caller in `src/threads/imu_task.c`, which already consumes per-axis float and has no reason to change (the design goal is to reimplement behind our `hal_accel.h`, not the other way around).

### Option C: Per-axis contract, driver internally reshapes interleaved FIFO bursts (chosen)
**Design:** `src/threads/imu_task.c` calls this driver `X`, then `Y`, then `Z`, once per averaging epoch, each expecting `FFT_IMU_N` samples (verified live in that file — not assumed). A single KX134 FIFO burst yields at most `KX134_FIFO_MAX_FRAMES` (86, adopted verbatim from the reference) interleaved frames — far fewer than one epoch's `FFT_IMU_N` (currently 2048). The `X` call drains as many BFI-interrupt-gated FIFO bursts as needed to accumulate a full epoch, decoding each burst's `Y` and `Z` samples into an internal cache (`s_stage_y`/`s_stage_z`) alongside the `X` samples it hands back directly. The `Y` and `Z` calls that follow read straight out of that cache — no additional hardware transaction, since the hardware already delivered all three axes together. This is a documented ordering requirement of this specific driver (X must be called first each epoch), not a general property `hal_accel.h` guarantees to every implementation — `accel_stub.c` doesn't need this because it doesn't share hardware state across axis calls, but this driver's whole point is that the hardware forces it to.
**Counts→g conversion:** ±8g range (`GSEL=00`), 16-bit output (`BRES=1`) → 4096 counts/g (32768/8), per the TRM sensitivity table for that configuration. **Flagged, not yet hardware-confirmed:** bring-up should verify the at-rest Z axis reads ≈1.0g under this constant; if it doesn't, this is the value to correct.
**Pros:** Satisfies `hal_accel.h` exactly as written, no changes needed to that header or to `imu_task.c`. Reuses the reference's register sequencing and FIFO frame-count cap verbatim, as instructed.
**Cons:** Couples the driver to `imu_task.c`'s specific X→Y→Z call order; a future caller that reads Y or Z first (without ever calling X) would get stale or empty cache data. Documented in the driver's own header comment as a known constraint rather than defended against at runtime, since the one real caller in this codebase is verified not to do that.

## Decision
**Chosen: Option C.**

**Justification:** Preserves `hal_accel.h` and `imu_task.c` exactly as Task 2 requires ("no changes needed there"), reuses the reference's hardware bring-up sequence and FIFO sizing verbatim as instructed, and the X-first cache design is the only way to reconcile a physically-interleaved FIFO with a per-axis synchronous contract without adding a background task or ring buffer neither `hal_accel.h` nor the existing call site asks for.

**`accel_stub.c` default status:** kept at `default y` (`CONFIG_EPM_ACCEL_USE_STUB`) for now, not flipped to `n`, per the Phase 3.5 prompt's explicit gating language ("flip its default to n *once the real driver is confirmed working*") — see this session's final report for whether physical KX134 hardware was available to run that confirmation (WHO_AM_I, at-rest g-values, zero dropped FIFO frames over a sustained run).

## Consequences
**Positive:**
- Real vibration data (once hardware-confirmed) replaces synthetic sine waves for FFT/bearing-fault analysis.
- `hal_accel.h` and every existing consumer (`imu_task.c`) are unchanged — this driver is a pure drop-in.
- `accel_stub.c` remains available with zero changes, so development/CI without hardware is unaffected.

**Negative / trade-offs:**
- The X→Y→Z ordering requirement is a real constraint on this driver, undocumented at the `hal_accel.h` contract level (only in this driver's own file header) — a future refactor of `imu_task.c`'s call order would silently break it.
- `KX134_STAGE_MAX_SAMPLES` (4096) is a locally-chosen cap, not derived from `src/epm_config.h`'s `FFT_IMU_N` (by design — `epm_drivers` must not depend on the main component's config, the same rule `accel_stub.c` already documents for `IMU_FS_HZ`). If `FFT_IMU_N` is ever raised above 4096, this driver's `hal_accel_init()`-time bound check will start failing loudly (`-EINVAL`) rather than silently truncating.
- **ODR mismatch, out of scope to fix here:** `src/epm_config.h`'s `IMU_FS_HZ` build macro (25600) is used by `imu_task.c` only to pace its `vTaskDelay` between reads; the real driver's true hardware ODR (12800 Hz, `hal_accel_get_sample_rate()`) disagrees with it. This does not corrupt data — the BFI-interrupt-gated blocking wait inside `hal_accel_read_block()` gates actual timing correctly regardless of the stale `vTaskDelay` duration — but the delay value itself is now informationally wrong, and bin-resolution doc comments elsewhere that assume 25600 Hz are stale too. Adopting the ODR as-is rather than re-deriving it was a deliberate choice; reconciling `IMU_FS_HZ` is a separate future change, not folded into this commit (one logical change per commit).
  **Superseded by `docs/decisions/ADR-037`:** a later audit found this mismatch's impact understated here — it also invalidated `epm_dsp_envelope_init()`'s band-pass filter design (8kHz upper edge above the real 6400Hz Nyquist) and put every wire-reported accel frequency off by 2×, not just the `vTaskDelay`/doc-comment effects named above. ADR-037 raised the KX134's programmed ODR to 25600Hz to match `IMU_FS_HZ` instead, hardware-validated at the new rate.
- New GPIO claims: CS=GPIO43, INT1=GPIO44 (previously unused UART0 TX/RX) — update the pin table in ADR-005/ADR-006 if either is revisited.

**Metrics to watch:**
- WHO_AM_I register value at `hal_accel_init()` (expect `0x46`).
- At-rest Z-axis g-value (expect ≈1.0g under the 4096 counts/g constant — the first thing to re-derive if wrong).
- FIFO dropped-frame count over a sustained run (Phase 9's original exit test, zero expected).
- Per docs/KX134_Interface_Appendix.md A.7: the XIAO ESP32S3 breakout's ADR jumper should be in the datasheet-standard SPI state (Center–Left severed) — unlike the reference's UNO Q, which needed the non-standard bridged state. Confirm this physically before bring-up if WHO_AM_I fails.

## Validation
Hardware was available and flashed. Live results:

- **WHO_AM_I**: `0x46` on every boot, every capture across this session — confirmed.
- **RAM bug found and fixed**: the original `KX134_STAGE_MAX_SAMPLES=4096` float Y/Z cache (32 KB static) pushed this build to 73.9% RAM, leaving too little free heap at boot for `xQueueCreate()` in `imu_task_start()`, which asserted and boot-looped on real hardware. Fixed by sizing the cache to `FFT_IMU_N` exactly (2048, no headroom) and caching raw `int16_t` counts instead of pre-converted floats (halves the footprint); g-conversion moved to `hal_accel_read_block()`'s copy-out step. Build dropped to 66.4% RAM and the boot-loop cleared. See the follow-up commit on top of this ADR's driver commit.
- **At-rest g-values**: stable across a 357-epoch / 90 s sustained run — `x≈0.65g y≈-0.65g z≈-0.13g`, vector magnitude ≈0.93g. Consistent with a board mounted at an angle rather than flat (not noise — values held steady to ±0.01g across all 357 epochs), and close enough to 1g to be a plausible gravity reading. Orientation wasn't re-checked against the physical mount, so treat the split across axes as unverified-but-plausible.
- **FIFO+interrupt path**: 357 consecutive epochs (≈731k samples) over 90 s, zero `-ETIMEDOUT`, zero SPI errors, zero crashes. `FIFO seen at max capacity (86/86 frames)` fires on *every* epoch — this is expected given BFI-only (Buffer-Full-Interrupt, not watermark) triggering, ported verbatim from the reference driver's own INC4/BUF_CNTL1 config, which only fires once the buffer is genuinely full. Because the KX134 has no hardware dropped-sample counter, this cannot be turned into a hard zero-dropped-frames proof either way — the sustained run had no observable data-integrity failures (no gaps, no timeouts, no corrupt frames), but true silent BM_STREAM overflow during the status-read→burst-read gap can't be ruled out from software alone.
- **Discovered, out-of-scope blocker for the Kconfig default flip**: with the real driver's RAM delta (63.7%→66.4%), `net_task`'s MQTT client (`components/epm_drivers/link_mqtt.c`, Phase 0.5 / ADR-011, unrelated to this ADR) crashes on boot with `LoadProhibited` at `esp_mqtt_client_register_event()`. Root cause, confirmed via `addr2line` against the exact flashed ELF: the vendored esp-mqtt's `esp_mqtt_client_init()` (`mqtt_client.c:875`) calls `esp_event_loop_create()` for its private event loop and never checks the return value; when that allocation fails under tighter heap margin it leaves `client->config->event_loop_handle` NULL, which then null-derefs on the very next call. This is a latent bug in `link_mqtt.c`/the vendored MQTT library, not in this driver — it was only *exposed* by this driver's static RAM footprint reducing the runtime heap margin. Reproduced with the real KX134 driver, absent with the stub, isolated with `addr2line` against a freshly-built matching ELF (an earlier decode attempt against a stale ELF gave a nonsense stack trace — rebuild before re-decoding any future crash on this board). Fixing it is out of scope for this ADR; see the session's final report for why `EPM_ACCEL_USE_STUB`'s default was left at `y`.

`tests/host/` does not cover this driver (ESP-IDF/SPI-dependent, not host-testable); 3/3 unaffected tests still pass.
