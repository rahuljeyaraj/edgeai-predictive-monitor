---
id: ADR-034
title: PAUSED/OFFLINE/IDLE are intentionally not local rgb_led_state_t members
status: accepted
date: 2026-08-08
deciders: Abhinav Krishna N
---

## Context

`components/epm_hal/include/hal/hal_display.h`'s `rgb_led_state_t` has no
members corresponding to the base station's `NodeStatus.PAUSED`,
`.OFFLINE`, or `.IDLE` concepts
(`status_color.py`'s `_GREY_PAUSED`, `_GREY_OFFLINE`, `_MAGENTA_IDLE`). This
was already noted once, in passing, in
`docs/decisions/ADR-016-neopixel-display-driver.md`'s 2026-08-06 addendum:

> No new `rgb_led_state_t` entries were added for `status_color.py`'s
> `PAUSED`, `OFFLINE`, or `IDLE`... These are gateway-decided statuses our
> satellite firmware has no local trigger condition for — nothing in our
> own state machine ever decides "I am paused" or "I am offline" from the
> inside. The existing `rgb_led_set_remote()` path... already renders any
> of these correctly if a real base station ever sends one; this is not a
> gap needing a pre-emptive local entry, just three statuses this side
> never originates on its own.

This ADR exists to record that observation as a deliberate, reasoned scope
boundary in its own right — cross-referenced from both ADR-016 (the
original local color table) and
`docs/decisions/ADR-025-remote-status-led-priority.md` (the remote-command
priority mechanism that's the actual answer here) — rather than leaving it
as a single addendum paragraph that's easy to miss when either of those
ADRs is read on its own. No firmware change accompanies this ADR.

**What the satellite can determine about itself locally**, confirmed by
re-grepping `rgb_led_set_state()`'s call sites (`src/`, 2026-08-08, same
result ADR-025 found on 2026-08-05): `led_task.c` (`RGB_BOOT` at boot),
`wifi_task.c` (`RGB_WIFI_CONN` on STA start/reconnect,
`WIFI_EVENT_STA_DISCONNECTED`), `dsp_task.c` (`RGB_OK` once, first
completed DSP cycle), and `net_task.c` (`RGB_WIFI_CONN` on the MQTT-drop
revert path ADR-025 added). Every one of these is a fact the node can
observe about its own hardware/network/link state from the inside — WiFi
associated or not, TCP/MQTT connected or not, a DSP cycle completed or not.

**What PAUSED/OFFLINE/IDLE actually are**: judgments the base station
makes about a node, not observations the node makes about itself.
`OFFLINE` is a staleness call (the registry hasn't heard from this node
recently enough), `PAUSED` is an operator action taken in the dashboard,
and `IDLE` (per `status_color.py`'s own convention, `_MAGENTA_IDLE`) is
similarly a registry-side classification. A satellite has no local signal
that could compute any of these — it cannot know "has the base station
stopped hearing from me" any more precisely than its own already-covered
`RGB_WIFI_CONN`/MQTT-disconnect states already say, and it has no concept
of "an operator paused me" at all unless told.

## Options considered

### Option A: add local `RGB_PAUSED`/`RGB_OFFLINE`/`RGB_IDLE` members
Extend `rgb_led_state_t` with three new members and give some local task
logic that decides when to set them.

Rejected: there is no local trigger condition to wire them to. `OFFLINE`
would have to be inferred from the same connectivity signals
`RGB_WIFI_CONN` and the MQTT-disconnect revert (ADR-025) already cover —
adding a redundant enum member for the same underlying fact is duplication,
not new information. `PAUSED` has no local signal at all; a satellite has
no way to know an operator clicked "pause" in a dashboard it never talks
to except by being told, which is precisely what the remote-command path
already does. `IDLE` is likewise a registry-side classification with
nothing local to observe. Adding these members would either sit unused
(no local caller could ever legitimately set them) or require inventing a
new signal that doesn't exist for a fact the base station already knows
and can already communicate.

### Option B: leave it fully implicit (status quo before this ADR)
Rely on the single addendum paragraph in ADR-016 as the record.

Rejected as insufficient, not wrong: the reasoning in that paragraph is
correct and this ADR does not change it, but it is easy to miss buried at
the bottom of a different ADR's addendum, and doesn't cross-reference
ADR-025's remote-priority mechanism, which is the actual answer to "so how
would a node ever show PAUSED." Worth a dedicated record so a future
reader searching "why doesn't the satellite have a PAUSED color" finds a
direct answer instead of rediscovering ADR-016's addendum by accident.

### Option C: document as an intentional scope boundary (chosen)
Record explicitly that this is a considered decision, not an oversight,
and point at the mechanism that already covers it.

## Decision

**No `rgb_led_state_t` members are added for `PAUSED`/`OFFLINE`/`IDLE`.**
`RGB_BOOT`, `RGB_WIFI_CONN`, `RGB_TCP_CONN`, plus `net_task.c`'s
MQTT-disconnect revert to `RGB_WIFI_CONN` (ADR-025), together cover every
state the satellite can determine about itself locally. `PAUSED` and
operator/staleness-driven `OFFLINE`/`IDLE` are judgments the base station
makes about a node, not something a node can compute from the inside —
there is no local signal to wire a new enum member to.

If the base station ever wants a satellite showing "paused," "offline," or
"idle" colors, it already can today with zero satellite-side changes:
`rgb_led_set_remote(uint32_t rgb, uint8_t mode, uint16_t period_ms)`
(`hal_display.h`) accepts an arbitrary `(rgb, mode, period_ms)` triple over
the `STATUS_LED` command path and renders it directly on the shared
single-slot display queue, bypassing `rgb_led_state_t` entirely. Per
ADR-025's last-write-wins design, that remote command wins over whatever
local state was showing and stays displayed until either another command
arrives or a local caller explicitly re-asserts a state — which, per the
call-site grep above, none currently does after the connected steady
state. The base station has every mechanism it needs to push
`_GREY_PAUSED`/`_GREY_OFFLINE`/`_MAGENTA_IDLE`'s exact `(rgb, mode,
period_ms)` values to a specific node right now, with no firmware change
on this side required.

## Consequences

**Positive:**
- Closes an open question both ADR-025 and ADR-016 left implicit — a
  reader landing on either ADR now has a direct cross-reference instead of
  needing to find ADR-016's addendum paragraph by chance.
- Confirms no firmware work is owed here: the remote-command path already
  fully covers this case, so there is nothing to build, only something to
  record.
- Keeps `rgb_led_state_t` free of enum members with no legitimate local
  caller — consistent with this repo's general preference (see
  `docs/CONVENTIONS.md`'s error-handling section) for naming a real
  feature absence outright rather than adding dead surface area to paper
  over it.

**Negative / trade-offs:**
- If a future phase gives the satellite some local signal that actually
  approximates one of these states (e.g. a local staleness heuristic
  independent of the base station), this decision would need revisiting —
  not expected under the current architecture, where staleness/pause
  judgments are and remain registry-side by design.
- Depends on the base station's `STATUS_LED` command sender actually
  choosing to push these colors when its own `NodeStatus` transitions to
  `PAUSED`/`OFFLINE`/`IDLE` — that sender-side behavior lives in the
  reference/base-station code, not this repo, and isn't verified from this
  side. If it doesn't, a paused/offline/idle node simply keeps showing
  whatever local state it last had (e.g. `RGB_WIFI_CONN`), which is a gap
  in the base station's use of an already-available mechanism, not a gap
  in this mechanism itself.

## Validation

None required — no code changes accompany this ADR. The claims above
(call-site coverage, `rgb_led_set_remote()`'s existing behavior) are
grepped/read-verified against `src/threads/{led_task,wifi_task,dsp_task,net_task}.c`
and `components/epm_hal/include/hal/hal_display.h` as of 2026-08-08, not
assumed.
