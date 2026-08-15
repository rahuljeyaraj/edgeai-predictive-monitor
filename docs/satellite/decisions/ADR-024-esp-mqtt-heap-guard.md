---
id: ADR-024
title: esp-mqtt null-deref mitigated with a heap-margin guard, not a component fork
status: accepted
date: 2026-08-05
deciders: Abhinav Krishna N
---

## Context

ADR-017 (Phase 3.5) hardware-validated the real KX134 accelerometer driver standalone, and in doing so discovered — and root-caused via `addr2line` against a freshly-flashed ELF — a latent bug in ESP-IDF's vendored `esp-mqtt` component: `esp_mqtt_client_init()` (`framework-espidf/components/mqtt/esp-mqtt/mqtt_client.c:875`, confirmed live in this session, outside this repo) calls `esp_event_loop_create()` for its private event loop and never checks the return value. Under tight heap margin the allocation can fail, `client->config->event_loop_handle` stays NULL, `esp_mqtt_client_init()` still returns a non-NULL client anyway, and the crash surfaces one call later as a `LoadProhibited` null-deref in `esp_mqtt_client_register_event()` → `esp_event_handler_register_with()`. That crash is why `CONFIG_EPM_ACCEL_USE_STUB` (`components/epm_drivers/Kconfig`) was left at `default y` in ADR-017 despite the real driver working: flipping it there pushed static RAM 63.7%→66.4% and the crash reproduced on real hardware.

Since ADR-017, Phase 7a (ADR-022/ADR-023) deleted the entire TCP+AES transport, improving static build RAM to 44.0% (from a Flash-constrained 95.2% at the Phase 6c baseline the crash was originally seen at). That headroom improvement was never measured with the real accel driver and real MQTT running together, so this ADR is based on a live measurement taken this session, not assumed from the aggregate RAM% alone.

Separately, and out of scope for this decision: `EPM_MQTT_BROKER_HOST` (`platformio.ini`, currently `192.168.1.8`, a laptop-on-home-router stand-in for the not-yet-available Uno Q) is the exact host ADR-011's addenda root-caused to a reliable TCP-handshake stall (`select() timeout`). The crash this ADR addresses happens purely inside `esp_mqtt_client_init()`/`esp_mqtt_client_register_event()`, before any network I/O — fully testable and, as validated below, fully independent of whether the broker itself is reachable.

## Measurement

`ESP_LOGI` instrumentation was added in `src/threads/net_task.c` immediately before `link_mqtt_start()`, logging `heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)` — the same call convention `src/main.c`'s boot-memory-map log already uses. `EPM_ACCEL_USE_STUB` was locally overridden to `n` (real accel driver active, generated `sdkconfig.xiao_esp32s3` only — not the committed Kconfig default) and the board (XIAO ESP32S3, COM15, `esp-builtin` JTAG upload) was flashed and captured from a clean boot (monitor attached to COM15 before triggering the reset, so every capture is from `ESP-ROM` boot forward, not mid-stream) across three separate flash/reset cycles:

| Boot | WHO_AM_I | Free internal heap before `link_mqtt_start()` | `link_mqtt_start()` result | Crash? |
|---|---|---|---|---|
| 1 | `0x46` | 67552 bytes | succeeded (`node_id=... broker=...` logged) | none |
| 2 | `0x46` | 67568 bytes | succeeded | none |
| 3 | `0x46` | 67552 bytes | succeeded | none |

Build with the real driver active: **RAM 46.8% (153332–153348/327680 bytes), Flash 94.4% (989498–989798/1048576 bytes)** — comfortably below ADR-017's crash-triggering static-RAM range (63.7%→66.4%).

Across all three boots, the KX134 driver ran continuously alongside `esp_mqtt_client_start()` (real epochs, `max_fifo=86/86`, zero timeouts) for the full duration of each capture with **zero `LoadProhibited` crashes, zero boot loops**. MQTT connection attempts failed with the expected `esp-tls: [sock=54] select() timeout` / `Failed to open a new connection: 32774` — exactly ADR-011's documented broker-stall signature, occurring well after `esp_mqtt_client_init()`/`register_event()` had already succeeded.

## Options considered

### Option A: Fork `esp-mqtt` into `components/mqtt/` with the one-line fix
**Design:** ESP-IDF's component resolution lets a project-local `components/<name>/` shadow a framework component of the same name (the vendored component's own `CMakeLists.txt` registers under `mqtt`, confirmed live). A local fork would carry a one-line fix: check `esp_event_loop_create()`'s return and propagate failure instead of leaving `event_loop_handle` NULL.
**Pros:** An actual fix — closes the null-deref at its root, not just around it. Would also protect any future caller of esp-mqtt in this tree.
**Cons:** Takes on permanent maintenance of a forked framework component (staying in sync with upstream `esp-mqtt` changes on every ESP-IDF bump) for a bug that, per the measurement above, is not currently reachable — this build never gets close to the margin that triggered it. Disproportionate to the actual, currently-measured risk.

### Option B: Heap-margin guard in `link_mqtt.c` (chosen)
**Design:** Check `heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)` in `link_mqtt_start()` immediately before calling `esp_mqtt_client_init()`; if free heap is below a threshold set well under the ~67.5 KB margin measured above, skip the call and return `-ENOMEM` instead of proceeding into the framework bug's precondition.
**Pros:** Small, local, no framework fork to maintain. Matches the measured data: current margin is nowhere near the crash-triggering range, so this guard is a safety net for a regression, not a workaround for a live problem.
**Cons:** Not a real fix — it's a TOCTOU guard (heap could still drop between the check and `esp_mqtt_client_init()` itself) and only prevents *this* call site from hitting the bug, not any future one. If the crash-triggering condition is ever reached again, this guard degrades MQTT to "silently never starts" rather than fixing the underlying null-deref.

## Decision
**Chosen: Option B — heap-margin guard.**

**Justification:** The live measurement is unambiguous — free internal heap immediately before `link_mqtt_start()` is consistently ~67.5 KB across three independent boots, comfortably clear of ADR-017's 63.7%→66.4% crash-triggering static-RAM range, and zero crashes reproduced with the real accel driver and real MQTT running concurrently (never tested together before this phase). Forking a framework component is a maintenance cost that should be paid for a bug this build can actually hit, not preemptively for one it can't currently reach. `EPM_MQTT_MIN_FREE_HEAP_BYTES` is set to 32768 (32 KB) — roughly half of the observed ~67.5 KB margin, chosen to trip only if something regresses heap usage well beyond today's baseline, not under current or near-future normal operation. `esp_event_loop_create()`'s own allocation for a no-task private loop (a small struct plus a size-1 queue) is a tiny fraction of that; the threshold isn't tuned to that minimum, it's tuned to leave visible headroom against regressions.

**Revisit this decision** (switch to Option A) if: the guard's `ESP_LOGE` ever actually fires on real hardware outside of deliberate low-heap testing, or if a future change (larger buffers, new tasks, more queues) meaningfully erodes the ~67.5 KB margin measured here.

## Consequences
**Positive:**
- `EPM_ACCEL_USE_STUB` can now default to `n` (Task 1, same commit series) — real accel driver ships by default with no observed crash risk.
- No new maintenance surface: `link_mqtt.c` gains one threshold constant and one check, no forked framework code to track across ESP-IDF upgrades.
- The `net_task.c` heap-log line added for this measurement stays as permanent telemetry — cheap, and useful if margin ever does erode.

**Negative / trade-offs:**
- The underlying esp-mqtt bug is still present upstream, unfixed. Any other future caller of `esp_mqtt_client_init()` in this tree (there is currently only one — `link_mqtt_start()`) would not be protected by this guard.
- TOCTOU: the check and the actual allocation are not atomic. Given the ~35 KB of headroom between the 32 KB threshold and the measured ~67.5 KB baseline, and that nothing else in this task runs between the check and `esp_mqtt_client_init()`, this is a theoretical gap, not a practically demonstrated one.
- If margin does erode below 32 KB in the future, MQTT publishing silently stops (logged `ESP_LOGE`, `-ENOMEM` returned) rather than the system crashing — a behavior change to watch for if telemetry ever goes quiet without an obvious cause.

**Metrics to watch:**
- The `net_task: free heap before link_mqtt_start(): internal=...` log line, every boot — if this trends down toward 32768, that's the signal to revisit Option A before the guard actually trips.

## Validation

Hardware was available and flashed (XIAO ESP32S3, COM15). With the real accel driver active and the guard in place:

- **WHO_AM_I**: `0x46` on every boot this session — consistent with ADR-017.
- **esp-mqtt crash validation (this ADR's core deliverable)**: three separate flash/reset cycles, real accel driver + real MQTT running concurrently for the first time, zero `LoadProhibited` crashes, zero boot loops, `link_mqtt_start()` returned success (guard never tripped — free heap ~67.5 KB, well above the 32 KB threshold) on every boot.
- **Accel + MQTT concurrency**: continuous KX134 epochs (`n=2048`, `max_fifo=86/86`, zero timeouts/SPI errors) sustained through and past every MQTT connection-retry cycle, across all three boots.
- **Broker reachability — verified live, not assumed stale**: `EPM_MQTT_BROKER_HOST` still resolves to `192.168.1.8` (`platformio.ini`, unchanged, laptop stand-in). Every connection attempt this session reproduced ADR-011's exact documented signature — `esp-tls: [sock=54] select() timeout` → `transport_base: Failed to open a new connection: 32774` → `mqtt_client: Error transport connect` → `link_mqtt: disconnected` — confirming this is still the same pre-existing, already-root-caused, out-of-scope issue, not a new gap introduced by this phase. "Frames actually publish and are received at a broker" remains explicitly unvalidated this session, deferred to the real Uno Q per ADR-011.

`tests/host/` does not cover `link_mqtt.c` (ESP-IDF/network-dependent, not host-testable); the host suite was run unmodified to confirm this change doesn't affect it.
