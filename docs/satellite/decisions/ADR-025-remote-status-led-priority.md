---
id: ADR-025
title: Remote STATUS_LED commands take priority over local display state via last-write-wins
status: accepted
date: 2026-08-05
deciders: Abhinav Krishna N
---

## Context

`components/epm_drivers/link_mqtt.c` already decodes inbound `STATUS_LED`
commands (`mqtt_event_handler()`'s `MQTT_EVENT_DATA` case) and already calls
`s_cmd_handler(type, payload, payload_len)` — but nothing registers a
handler via `transport_set_cmd_handler()` anywhere in the tree, so a fully
decoded command is silently discarded. Wiring that handler through to the
display (this phase's Task 1/2) first requires deciding what happens when a
remote command and this node's own local `rgb_led_state_t` state disagree
about what the LED should show, and what happens to that remote color once
the MQTT link that delivered it drops.

`components/epm_hal/include/hal/hal_display.h`'s only entry point,
`rgb_led_set_state(rgb_led_state_t)`, drives the LED from a fixed local enum
via `display_neopixel.c`'s `k_pattern[]` table (ADR-016). A `STATUS_LED`
command carries an arbitrary `(rgb, mode, period_ms)` triple with no enum
slot to land in — the display API itself needs a second entry point for
"whatever the base station just sent," which is Task 1's job. This ADR
covers the priority/lifecycle question that entry point's design depends on.

**Who actually calls `rgb_led_set_state()` today** (grepped across `src/`,
2026-08-05): `led_task.c` once at boot (`RGB_BOOT`), `wifi_task.c` on STA
start / reconnect (`RGB_WIFI_CONN`) and on IP acquisition (`RGB_TCP_CONN`),
and `dsp_task.c` once, the first time a DSP cycle completes (`RGB_OK`). No
firmware task currently computes `RGB_WARN`/`RGB_FAULT`/`RGB_TRIPPED` on
this board — fault/anomaly detection lives in the gateway
(`mic_tools/`'s ADWIN/HST/Bayesian fusion), not on-device. In practice, once
WiFi connects and the first DSP cycle completes, no local code calls
`rgb_led_set_state()` again during normal operation.

## Decision

**Remote overrides local via last-write-wins on the display driver's
existing single-slot queue — no separate priority/arbitration flag.**
`rgb_led_set_state()` (local) and the new `rgb_led_set_remote()` (Task 1)
both write into the same `xQueueOverwrite()`-backed slot `display_neopixel.c`
already uses for `RGB_BOOT`/`RGB_WIFI_CONN`/etc. — whichever call lands most
recently wins and is what the ring renders next. No new state machine is
needed to make "remote overrides local:" it falls out for free from the fact
that, per the grep above, no local caller keeps re-asserting a state after
WiFi/MQTT connects — so once a `STATUS_LED` command arrives, it really is
the most recent write and stays that way until either another command
arrives or something explicitly calls `rgb_led_set_state()` again.

**On MQTT disconnect, the display reverts to a local state rather than
holding a stale remote color.** A remote-driven color reads as "everything
is fine" even after the link that would tell this node otherwise is gone —
misleading for an operator glancing at the physical LED. The revert target
is `RGB_WIFI_CONN`: `net_task.c` (Task 2, same commit series) polls
`transport_is_connected()` once per publish tick and calls
`rgb_led_set_state(RGB_WIFI_CONN)` on the falling edge (was connected, now
isn't). `RGB_WIFI_CONN` is the honest state here — WiFi itself may still be
up (a broker-level MQTT drop doesn't imply a WiFi drop), so "reconnecting to
the network the base station lives on" is a more accurate signal than
reusing `RGB_BOOT` or inventing a new enum member for a case the existing
blue connectivity color already covers. This composes cleanly with
`wifi_task.c`'s existing behavior: a WiFi-level drop already calls
`rgb_led_set_state(RGB_WIFI_CONN)` on `WIFI_EVENT_STA_DISCONNECTED` — the
MQTT-level revert in `net_task.c` covers the case that path doesn't (broker
drops the session while WiFi stays associated).

**Not adopted: an explicit remote-active flag with local-call blocking.**
Considered gating `rgb_led_set_state()` to no-op while a remote command is
active, so a stray local call couldn't clobber the base station's authority.
Rejected: it adds a stateful special case for a race that today's call graph
doesn't actually produce (nothing calls `rgb_led_set_state()` after the
connected steady-state except the disconnect-revert path itself, which
*should* win). If a future phase adds on-device fault detection that calls
`rgb_led_set_state(RGB_FAULT)` mid-flight, last-write-wins means it would
immediately override a remote command too — that's the point at which this
decision should be revisited (see Consequences), not before.

## Consequences

**Positive:**
- No new state/flag in `display_neopixel.c` beyond the queue payload itself
  growing to carry either a local enum value or a remote triple — Task 1's
  implementation stays a small extension of the existing single-slot
  queue/notify mechanism, not a new arbitration layer.
- Disconnect behavior reuses an existing, already-understood color
  (`RGB_WIFI_CONN`) instead of adding a tenth `rgb_led_state_t` member for a
  case that's semantically "still trying to reach the base station."

**Negative / trade-offs:**
- Last-write-wins has no memory of *why* a state was set — if a future
  on-device fault-detection path starts calling `rgb_led_set_state()` after
  a remote command has landed, it will silently win the race with no log or
  guard, which could look like "the base station's command was ignored."
  **Revisit this ADR** (add explicit priority/arbitration) if/when on-device
  fault detection is added — the grep-verified assumption this decision
  rests on ("nothing else calls `rgb_led_set_state()` once connected") would
  no longer hold.
- The disconnect-revert poll in `net_task.c` runs at
  `EPM_NET_PUBLISH_INTERVAL_MS` (200 ms) granularity, not immediately on the
  MQTT `DISCONNECTED` event — a remote color can visibly persist up to one
  publish tick after the broker actually drops. Acceptable: this is a status
  indicator, not a safety-critical alarm path, and 200 ms is imperceptible
  for a human glancing at an LED.

**Metrics to watch:**
- Whether any future task besides `net_task.c`'s disconnect-revert path
  starts calling `rgb_led_set_state()` after boot — if so, re-open this ADR.

## Addendum: 2026-08-11 — revert target changed from `RGB_WIFI_CONN` to a new `RGB_MQTT_STALL`

This ADR's Decision picked `RGB_WIFI_CONN` as the disconnect-revert target,
reasoning it was "the honest state" since WiFi itself may still be up. That
reasoning about *what's true* was correct, but it had a cost this ADR didn't
weigh at the time: reusing `RGB_WIFI_CONN` means a broker-level MQTT stall
and a real WiFi-association drop render as the exact same color and
animation, with no way to tell them apart from the LED alone.

That collision caused real confusion during demo prep and stress testing —
see `docs/performance/SATELLITE_STRESS_STABILITY_TEST.md`'s 2026-08-11
addendum, independently reproduced again on a different network during
`docs/performance/HARDWARE_INTEROP_TEST.md`'s 2026-08-11 addendum. Both times
the LED alone was insufficient to tell "just wait, this self-heals"
(MQTT-layer stall, ADR-036) apart from "something actually dropped WiFi"
without opening a serial monitor and checking for a `Disconnect reason` log
line.

`net_task.c`'s disconnect-revert call now targets a new `RGB_MQTT_STALL`
state (`components/epm_hal/include/hal/hal_display.h`, violet,
`0xBB00FF`/breathe/900ms on the NeoPixel driver) instead of `RGB_WIFI_CONN`.
The color was checked against both this project's own pattern table and the
reference base station's `status_color.py` directly (not assumed from a
stale doc) to confirm it collides with neither palette. This doesn't change
anything else this ADR decided — last-write-wins priority, remote overriding
local, and the revert-on-disconnect behavior itself are all unchanged; only
which color that revert renders as.
