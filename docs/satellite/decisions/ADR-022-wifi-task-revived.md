---
id: ADR-022
title: wifi_task.c revived for WiFi STA lifecycle + power management
status: accepted
date: 2026-08-05
deciders: Abhinav Krishna N
---

## Context

`docs/decisions/ADR-015-tcp-task-split-deferred.md` moved `wifi_task.c`
whole to `src/threads/tcp_task.c` without splitting its three bundled
responsibilities (WiFi STA lifecycle, dynamic CPU frequency scaling / TX
power cap, and the raw-TCP+AES-GCM transport), explicitly deferring that
split to "Phase 7, when the raw-TCP transport is retired ... and this code
is deleted outright."

Phase 7a is that phase. `docs/decisions/ADR-023-transport-adrs-superseded.md`
retires the raw-TCP+AES-GCM transport in favor of the MQTT path that has
carried real sensor data since Phase 6c
(`docs/decisions/ADR-011-mqtt-transport-added.md`). That leaves WiFi STA
lifecycle and power management as the only parts of `tcp_task.c` with a
future: `net_task.c` (the MQTT publisher) already depends on
`wifi_wait_connected()`, and power management has nothing to do with
transport choice at all.

## Options considered

### Option A: fold into net_task.c
`net_task.c` is the file that actually needs `wifi_wait_connected()` now.
Folding WiFi bring-up into it would mean one fewer file and no separate
"task" that isn't really a FreeRTOS task.

Rejected: WiFi STA lifecycle is not an MQTT concern, and `net_task.c`'s own
header comment already frames it as a thin, single-purpose publish loop
(`docs/decisions/ADR-021-net-task-second-consumer-queue.md`). Bundling radio
bring-up into it would recreate exactly the "one file owns three unrelated
things" problem ADR-015 flagged in `tcp_task.c`, just with a new pair of
unrelated things (MQTT publish cadence, WiFi STA event handling) instead of
the old pair (TCP framing, WiFi STA event handling). `esp_pm_configure()`
power management in particular has no logical connection to MQTT at all —
it would be dead weight in a file whose name and header comment are about
telemetry publishing.

### Option B: revive a slim wifi_task.c
Give WiFi STA lifecycle and power management their own file again, this
time containing only what actually belongs together: the event handlers,
`wifi_rf_init()`, and `wifi_wait_connected()`. No task loop, since none of
this is a FreeRTOS task — ESP-IDF's own event loop drives the state machine,
and `wifi_rf_init()`/`wifi_wait_connected()` are two ordinary function calls
made once from `app_main()`, exactly as they already are today.

## Decision

**Option B.** `src/threads/wifi_task.c`/`.h` are revived, containing only
`wifi_rf_init()`, `wifi_wait_connected()`, the three WiFi/IP event
sub-handlers, and `g_wifi_debug_state`. The "task" name is kept (despite
there being no task) for continuity with the existing log tag (`"wifi_task"`)
and because `net_task.c`/`mic_task.c`/etc. establish `_task.c` as this
tree's naming convention for "the module responsible for X," not literally
"the module that owns a FreeRTOS task."

Everything that made `tcp_task.c` genuinely TCP-specific — `tcp_connect()`,
`encrypt_init()`/`encrypt_frame_data()`, `send_frame()`,
`snapshot_send_tcp()`, `read_gateway_alert()`, `update_led()`, the
`wifi_task_fn()` loop and its `wifi_task_start()`/`wifi_task_get_handle()`
task-creation API — is deleted outright along with `tcp_task.c` itself, per
`docs/decisions/ADR-023-transport-adrs-superseded.md`. It is not moved
anywhere; there is no successor task, because the MQTT path has no
per-frame TCP socket to hold open, no AES context, and no gateway-alert
byte to parse — `net_task.c`'s publish loop and the MQTT broker connection
(`components/epm_drivers/link_mqtt.c`) already cover what remains of "send
data to the base station."

**Power management moves, and its trigger point changes.** In
`tcp_task.c`, `esp_pm_configure()` ran once, inside `wifi_task_fn()`, right
after the task's first `wait_for_wifi()` call returned — i.e. after the
first successful WiFi connection. In the revived `wifi_task.c` there is no
task loop for it to live in, so it now runs at the end of `wifi_rf_init()`
itself (`src/threads/wifi_task.c`, immediately after the TX-power-cap
block) — before WiFi even attempts to connect, not after. This is strictly
earlier than before: DFS is configured no later than it previously was, and
`app_main()` still calls `wifi_rf_init()` before starting any DMA-bearing
task, so the timing constraint that motivated `wifi_rf_init()` running
before I2S (`src/main.c`'s header comment) is unaffected — `esp_pm_configure()`
does not touch I2S, DMA, or the WiFi RF scan itself.

## Consequences

**Positive:**
- `src/threads/wifi_task.c` is now a thin file with a single responsibility
  (WiFi STA lifecycle + power management), matching every other file in
  `src/threads/` — the exact inconsistency ADR-015 flagged as temporary is
  now resolved.
- `net_task.c`'s header comment and `#include` swap from `tcp_task.h` to
  `wifi_task.h` with no change to its own logic — `wifi_wait_connected()`'s
  signature and behavior are unchanged.
- `src/main.c` drops the `wifi_task_start()` call and the diagnostics
  task's `h_wifi` handle/stack-HWM line entirely: there is no task handle
  to collect, because there is no task.

**Negative / trade-offs:**
- Power management's trigger point is no longer "first successful WiFi
  connection" but "immediately after `wifi_rf_init()`'s own setup calls."
  If a future phase wants power management to depend on connection state
  (e.g. to avoid capping CPU frequency while still scanning), it will need
  to move this block again — flagging that explicitly here so it isn't
  rediscovered by surprise.
- The `wifi_task` name for a file with no task remains slightly misleading.
  Judged not worth a rename: the log tag, the ADR history, and this
  project's own `_task.c` naming convention for "owning module" (not
  literal FreeRTOS task) all already point at this name.

## Validation

Power management confirmed still executing: `wifi_rf_init()`'s
`esp_pm_configure()` call is unconditional and unchanged from `tcp_task.c`'s
version (same `pm_cfg` values: 240/80 MHz, light sleep disabled), it is
still guarded by the same `ESP_LOGW` on failure, and `esp_wifi_set_max_tx_power()`
runs immediately before it in the same function, also unchanged. No live
hardware was available in this session to capture a boot log; the
functional equivalence is by direct code inspection (same call, same
arguments, same file, unconditional execution path — no branch skips
either call). A future session with hardware access should capture one
boot log confirming both `"WiFi TX power capped to 17.0 dBm"` and the
absence of the `"esp_pm_configure failed"` warning, and attach it here as
an addendum.

`pio run -e xiao_esp32s3` — clean build (see
`docs/decisions/ADR-023-transport-adrs-superseded.md` for the combined
RAM/Flash delta, since this change and the transport deletion landed in one
commit — see that ADR's Validation section for why).
