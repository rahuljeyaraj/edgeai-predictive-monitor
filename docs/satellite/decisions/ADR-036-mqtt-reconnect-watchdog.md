---
id: ADR-036
title: Self-heal esp_restart() watchdog for a stuck MQTT reconnect loop
status: accepted
date: 2026-08-09
deciders: Abhinav Krishna N
---

## Context

ADR-024 added a heap-margin guard (`EPM_MQTT_MIN_FREE_HEAP_BYTES`, 32768
bytes) in front of `esp_mqtt_client_init()` and named its own revisit
trigger explicitly: *"the guard's `ESP_LOGE` ever actually fires on real
hardware outside of deliberate low-heap testing."* That trigger has now
fired.

The real satellite was left running unattended overnight (no test harness,
no synthetic load, no deliberate interference — the most realistic
condition this project has observed it under) for approximately 7.6 hours.
A serial capture attached the next morning
(`docs/performance/raw/disconnect_recovery_20260809.log`) found it in a
permanent, non-recovering state:

```
E (27325194) esp-tls: [sock=58] select() timeout
E (27325194) transport_base: Failed to open a new connection: 32774
E (27325194) mqtt_client: Error transport connect
W (27325194) link_mqtt: disconnected
```

repeating roughly every 13 seconds, with DIAG snapshots showing:

```
I (27347724) DIAG: Heap free: internal=3144 largest_free=1280 PSRAM=8209812 IRAM=0
I (27347734) DIAG: mqtt: connects=12 disconnects=735 publishes=86767 publish_failures=12 cmds_received=0
```

`largest_free` stayed frozen at exactly 1280 bytes across the whole capture
window while `internal` free heap plateaued in a ~500-600 byte band around
3.1-3.7 KB — both falling together, not just fragmenting, per the existing
DIAG comment's own distinction. `mqtt: connects` never advanced from 12
while `disconnects` climbed steadily; `wifi: disconnects` stayed frozen at
447, confirming WiFi itself was fine and the failure was MQTT-layer only.

`link_mqtt.c` was re-read in full to rule out our own code: after boot,
`link_mqtt_start()` is called exactly once from `net_task_fn()`; every
reconnect attempt after that runs entirely inside esp-mqtt's own internal
state machine (`network.reconnect_timeout_ms = 2000` in the client config),
which never calls back into our driver. The leak is therefore happening
inside ESP-IDF's vendored `esp-mqtt`/`esp-tls` library during its own
internal retry path against a broker that (for whatever reason — the
laptop-hosted dev broker was not confirmed continuously up overnight) the
board could not reach, not in any driver code this repo owns.

## Measurement

Comparing the stuck capture against
`docs/performance/raw/post_stress_verify_20260809.log` — the same boot
session's healthy baseline, captured ~61 seconds after the same reflash:

| | t=61s (baseline) | t=27347s+ (stuck, ~7.6h later) |
|---|---|---|
| Heap free (internal) | 30928 bytes | ~3100-3700 bytes (plateaued) |
| largest_free | 21504 bytes | 1280 bytes (frozen) |
| mqtt connects | 1 | 12 (frozen, no further growth) |
| mqtt disconnects | 0 | 735 → 749+ (climbing ~2-3/30s) |

This is a single continuous boot session's organic trajectory, not a
synthetic accelerated test — stronger evidence for the leak's real-world
end state than the earlier 50-call accelerated stress test could provide,
because it shows what actually happens when the retry loop is allowed to
run for hours against a genuinely unreachable broker.

## Options considered

### Option A: Fork or patch `esp-mqtt`/`esp-tls` to fix the per-attempt leak
**Design:** Locate and fix the leak at its source inside the vendored
component, following the same shadow-component mechanism ADR-024's Option
A already described for a different esp-mqtt bug.
**Cons:** ADR-024 already rejected taking on permanent maintenance of a
forked framework component for a narrower, better-understood bug in the
same library; the leak observed here is not yet root-caused to a specific
line (no `addr2line`-level diagnosis was performed — the board is a single
physical unit currently stuck in the failure state, not gdb-attached), so
a fork would be a much larger, speculative undertaking. Disproportionate
given Option B fully resolves the *observed* failure mode (permanent
lockout) even though it doesn't stop the underlying per-attempt leak.

### Option B: `esp_restart()` watchdog on stuck consecutive disconnects (chosen)
**Design:** Add a `consecutive_disconnects` counter to
`struct link_mqtt_stats` (`components/epm_drivers/include/drivers/link_mqtt.h`),
incremented on every `MQTT_EVENT_DISCONNECTED` and reset to 0 on every
`MQTT_EVENT_CONNECTED` (`components/epm_drivers/link_mqtt.c`). The existing
30-second-cadence `diagnostics_task_fn()` (`src/main.c`) checks this counter
after logging the mqtt DIAG line and calls `esp_restart()` once it reaches
30 (~6.5 minutes of continuous failure at the observed ~13s retry cadence).
**Pros:** `esp_restart()` does not depend on heap availability, unlike
re-invoking `link_mqtt_start()` — which would immediately fail ADR-024's
32768-byte guard once heap is this exhausted, so a reconnect-only retry
strategy cannot self-heal from this state. A full restart clears the leaked
heap unconditionally. Minimal, local change — no forked framework
component, reuses the existing DIAG task's cadence and the existing
Part I `_get_stats()` convention.
**Cons:** Doesn't fix the underlying per-attempt leak, only bounds its
consequence — the board will still burn through its heap margin on every
extended outage and pay the cost of a full reboot (losing in-flight FFT
state, provisioning task state, etc.) rather than a clean reconnect. A
two-stage design (retry `link_mqtt_start()` first, escalate to
`esp_restart()` only if that also fails) was considered and rejected as
unsupported complexity: `link_mqtt_start()` would just hit the ADR-024
guard immediately in this state, so it would never do anything a direct
restart doesn't already accomplish, only defer it.

## Decision

**Chosen: Option B — `esp_restart()` watchdog, threshold 30.**

The threshold is calibrated directly from the observed real retry cadence
(~13s per failed attempt, ~2-3 per 30s DIAG cycle): 30 consecutive
disconnects is ~6.5 minutes of continuous, unbroken failure. That's long
enough that an ordinary transient blip — which resets this counter to 0 on
its very next successful reconnect — cannot false-trigger a restart, and
short enough that a field-deployed unit self-heals within minutes instead
of being stuck for hours (as directly observed) until someone notices and
power-cycles it by hand.

Option A remains the correct move if this watchdog is later found to fire
routinely (i.e., broker outages of 6.5+ minutes are common in the target
deployment environment) — at that point the cost of frequent reboots would
justify root-causing and fixing the underlying per-attempt leak instead of
just bounding it.

## Consequences

**Positive:**
- Closes the permanent-lockout failure mode directly observed on real
  hardware — a field unit can no longer get stuck requiring manual
  power-cycle after an extended broker outage.
- No new maintenance surface: one counter field, two one-line updates in
  the existing event handler, one threshold check in the existing DIAG
  task. No forked framework component.
- `consecutive_disconnects` is independent, permanent telemetry (visible
  in every DIAG cycle via a future log line if added) even below the
  restart threshold — useful for spotting a broker flakiness trend before
  it ever reaches 30.

**Negative / trade-offs:**
- The underlying esp-mqtt/esp-tls per-attempt leak is still present and
  unfixed — this bounds the blast radius, it doesn't close the root cause.
- A restart during a genuine extended outage means the board briefly stops
  capturing/publishing entirely (reboot + reconnect time) rather than
  quietly waiting; judged an acceptable trade given the alternative is an
  indefinite, unrecoverable outage.
- Threshold of 30 is calibrated from one real observed retry cadence
  (~13s/attempt); if `network.reconnect_timeout_ms` or the broker's own
  behavior changes that cadence meaningfully, the ~6.5-minute window this
  threshold implies would shift and may need re-tuning.

**Metrics to watch:**
- How often the `"mqtt stuck: ... restarting to recover (ADR-036)"`
  `ESP_LOGE` line fires in the field — frequent firing is the signal to
  revisit Option A.

## Validation

Hardware was available and in exactly the failure state this ADR fixes at
the time of the change (the real board, stuck since the prior night, still
retrying every ~13s with heap frozen at ~3.1-3.7 KB). The fix was built and
flashed via `.\pio.ps1 run --environment xiao_esp32s3 --target upload`
(never plain `pio run`, which silently falls back to the wrong compiled-in
broker default) directly onto that stuck board, and a fresh serial capture
confirmed recovery — see
`docs/performance/SATELLITE_STRESS_STABILITY_TEST.md` for the exact
before/after log excerpt and the post-restart reconnect timeline.

## Addendum: 2026-08-09 — watchdog blind spot for a boot stuck below the ADR-024 heap margin

`consecutive_disconnects` as shipped above only incremented inside
`mqtt_event_handler`'s `MQTT_EVENT_DISCONNECTED` case
(`components/epm_drivers/link_mqtt.c`). That event is only ever posted by
esp-mqtt *after* `esp_mqtt_client_init()`/`esp_mqtt_client_start()` have
succeeded at least once and a live connection was subsequently lost. If
`link_mqtt_start()` never gets that far — free heap sitting below
ADR-024's 32768-byte `EPM_MQTT_MIN_FREE_HEAP_BYTES` guard on every one of
`net_task.c`'s boot-time retries — `esp_mqtt_client_init()` is never even
called, so `MQTT_EVENT_DISCONNECTED` never fires, `consecutive_disconnects`
never leaves 0, and this watchdog never trips. The board sits indefinitely
with WiFi connected and zero MQTT telemetry (`mqtt: connects=0
disconnects=0` on every DIAG line), recoverable only by an external
power-cycle — exactly the failure mode this ADR was written to close, just
reached by a different path than the one originally measured.

**Repro.** A new Kconfig-gated test hook (`CONFIG_EPM_LOW_HEAP_BOOT_STALL_TEST`,
`components/epm_drivers/Kconfig`, hooked in `src/threads/net_task.c` right
after `wifi_wait_connected()`) reserves internal-DRAM heap at boot to land
free heap at ~19.8KB, deterministically landing below the 32768-byte margin
on a normal WiFi connection. Before the fix
(`docs/performance/raw/gap1_before_lowheap_stall_20260809.log`): heap stuck
at 19824 bytes, `E (...) link_mqtt: free heap 19824 below 32768-byte MQTT
init safety margin ... skipping esp_mqtt_client_init()` repeating every
~2s from t≈11s, `mqtt: connects=0 disconnects=0` on every DIAG line,
confirmed stuck through t≈1.4M ms (~23+ minutes, well over 3x this
watchdog's normal ~6.5-minute-equivalent window at the 2s retry cadence)
with zero restarts.

**Fix.** `link_mqtt_start()` now increments `consecutive_disconnects`
directly on all three of its own failure paths — the ADR-024 heap-guard
skip, `esp_mqtt_client_init()` returning `NULL`, and
`esp_mqtt_client_start()` returning non-`ESP_OK` — not just inside the
event handler. `transport_init()` (called at the top of every
`link_mqtt_start()` retry, not just once at boot) now preserves
`consecutive_disconnects` across its `memset(&s_stats, 0, ...)` instead of
zeroing it, so repeated init-time failures accumulate toward this ADR's
existing threshold of 30 instead of being wiped every 2s retry. No change
was needed to the threshold, the check, or `diagnostics_task_fn()` itself
— the fix feeds the existing counter/threshold from a path that previously
never reached it.

**Verification.** Same test hook, fixed firmware
(`docs/performance/raw/gap1_after_lowheap_stall_20260809.log`): identical
stuck pattern from t≈19s (`free heap 19824 below 32768-byte...`, ~2s
cadence), then at t=91772ms: `E (91772) DIAG: mqtt stuck: 44 consecutive
disconnects with no successful reconnect - restarting to recover
(ADR-036)` followed by `rst:0xc (RTC_SW_CPU_RST)`. The count reaching 44
(not exactly 30) reflects the 30s-cadence DIAG check catching the counter
mid-climb past the threshold, not a bug. Post-restart, the one-shot test
hook did not re-arm (by design — see the hook's own comment), heap was
back to normal, and MQTT connected on the very first attempt: `I (31497)
DIAG: mqtt: connects=1 disconnects=0 publishes=130 ...` — full self-heal in
one restart cycle, ≈92s total from stall onset to confirmed reconnect.

**Methodology note.** The test hook arms via an `RTC_NOINIT_ATTR`
magic-value marker, cleared only by an actual power-loss event (not by an
esp_restart() reboot, and — confirmed the hard way, mid-investigation —
not by an OpenOCD/esp-builtin JTAG reflash either). The first attempt at
this "after" capture silently reused the marker armed by the "before" run
across an intervening reflash, so the heap reservation never reapplied and
that capture showed healthy operation with no evidence value. A real
power-cycle (unplug/replug USB) between the before and after captures was
required to get a valid result; both the Kconfig help text and the hook's
own comment in `net_task.c` have been corrected to document this.

The live-router power-cycle trial (reproducing the original finding's exact
conditions rather than the synthetic heap-reservation hook) remains
deferred to a future validation pass.

## Addendum: 2026-08-11 — threshold lowered 30 → 10 ahead of external testing, confirmed on real hardware

This ADR's threshold of 30 was picked to weigh two failure costs against
each other: false-triggering on an ordinary transient blip vs. leaving a
field unit stuck for a long, human-noticeable time. 30 gave ~6.5 minutes at
the observed ~13s retry cadence — comfortably longer than any transient
this project had evidence for, but that same margin means a real stall
reads as "frozen" for over six minutes to anyone watching without prior
context on this failure mode, which is exactly the situation this
project's satellite node was about to be put in: independent testing on a
colleague's own hardware/network, ahead of a handoff, with no one present
who already knows to just wait it out.

**New value: 10.** At the same ~13s cadence that gives ~130s (~2.2 min)
theoretical — see Verification below for the real measured figure, which
runs somewhat higher due to this ADR's own documented 30s-poll-cadence
overshoot. The reasoning for *why* 10 stays safe from false-triggering
mirrors this ADR's original logic, re-derived from evidence rather than
picked by simply halving 30: every transient this project has actual data
for either clears within seconds (ordinary WiFi/broker blips, never seen to
survive multiple retry cycles) or runs the full original ~6.5-minute
outage without self-clearing at all (the overnight heap-exhaustion capture
above, the live-router power-cycle trials, and the 2026-08-11 repro this
addendum's own test reproduces below). Nothing in this project's history
sits in the gap between "clears in seconds" and "doesn't clear in 6+
minutes," so there is no observed transient that dropping to 10 would
newly false-trigger on — the threshold still comfortably exceeds every
short blip while cutting the stuck window roughly 3x. The full reasoning
is also recorded next to the constant itself
(`src/main.c`'s `MQTT_WATCHDOG_RESTART_THRESHOLD`).

**Real trigger-and-recover test, real hardware, 2026-08-11.** Deliberately
used a Windows Firewall inbound `Block` rule (`TCP`, local port 1883,
scoped to the satellite's IP only) rather than stopping the dev broker
(Mosquitto, running as a Windows service on the same laptop the board
points at) to force the stall. Stopping the broker service closes the TCP
session cleanly (FIN/RST) — an explicit, immediate "you're disconnected"
signal, a different and easier failure mode than what this ADR's own
Context section documents (`select() timeout`, a silent stall with no
close signal at all). A firewall rule with the default `Block` action
drops the satellite's packets with no RST and no ICMP-unreachable, so the
socket just goes quiet from the satellite's side — the same shape as the
originally observed failure, not a substitute for it.

With the rule active, the watchdog fired and restarted the board
repeatedly, cleanly reproducing this ADR's original per-attempt pattern
each time (WiFi reconnects fine post-restart, `Got IP` within 1-2 attempts,
MQTT immediately starts failing again since the block was still up). Seven
consecutive full-block cycles landed within 152.0-152.1 seconds of boot
each time — e.g. `E (152117) DIAG: mqtt stuck: 11 consecutive disconnects
with no successful reconnect - restarting to recover (ADR-036)`, repeated
at t=152057/152107/152117(×2)/152107/152117/152127 across successive
reboots. The very first restart of the session fired at t=513975ms of
*total* device uptime rather than ~152s — not a discrepancy, that boot had
already been running healthily for a while before the firewall rule was
applied mid-session, so most of that uptime was pre-stall normal operation,
not stuck time. The steady-state ~152s figure (cycles where the stall was
present from the very start of the boot) is the comparable, reportable
number.

**~152s vs. the ~130s theoretical estimate.** The extra ~22s matches this
ADR's own already-documented mechanism (see the 2026-08-09 addendum's
count-of-44 case): `diagnostics_task_fn()` only checks the counter every
30 seconds, not the instant it crosses the threshold, so the observed
trigger count came in at 10 the first time and 11 on every subsequent
cycle rather than exactly 10 — one extra ~13s retry sneaking in before the
next poll catches it. Real, reportable self-heal time ahead of independent
testing: **~152 seconds (~2.5 minutes)** from stall onset to automatic
restart, not the raw threshold × cadence math.

**Recovery confirmed clean.** After removing the firewall rule, one more
restart occurred (residual disconnects mid-transition as the block lifted
partway through a boot's retry cycle), and the very next boot reconnected
immediately — `Got IP: 192.168.1.2 (after 1 attempt(s))` at t=4107ms, then
`connects=1 disconnects=0` held for the rest of the observation window
(180+ seconds) with `publishes` climbing steadily (129 → 555) and zero
further disconnects. No manual intervention was needed at any point;
self-heal worked exactly as designed at the new threshold.

`docs/NEW_NODE_SETUP_GUIDE.md` §9 and
`docs/performance/SATELLITE_STRESS_STABILITY_TEST.md` are updated with this
real ~152s/~2.5min figure in place of the original ~6.5-minute one.

**2026-08-11 (later) — name redaction ahead of external sharing.** The
addendum above originally named the external tester by first name in one
sentence; reworded generically before sharing this repo outside the team.
No measurement, figure, or conclusion changed.
