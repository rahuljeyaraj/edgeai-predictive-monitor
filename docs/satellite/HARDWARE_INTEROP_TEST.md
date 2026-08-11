# Real Hardware Interop Test — XIAO Satellite vs Reference Base Station

**Date:** 2026-08-07
**Branch:** feat/base-station-interop
**Commit:** 19d530eb2aa4b9d473b63a8a4d587c15f4bc4409

---

## Setup

Real Seeed XIAO ESP32-S3 satellite hardware (node_id `5ab004`) tested
end-to-end against the reference repo's unmodified
`base-station/python/main.py`, run under WSL Python pointed at a native
Windows Mosquitto broker (`192.168.1.7:1883`). No reference-repo files were
modified, per `tools/devrig/README.md`'s own rule.

## Result

- Node `5ab004` registered on the dashboard (`/nodes`) with correct
  `sensor_config`/`input_dim`, and the Fleet view rendered a live
  `accel_x`/`accel_y`/`accel_z` spectrum.
- A remote `STATUS_LED` command sent from the dashboard side was visually
  confirmed to change the satellite's physical LED (steady green -> red
  strobe).

## Soak window

17m45s continuous run (`mosquitto_sub -t 'epm/#' -v`, single unbroken
process, no restarts):

| Topic | Count |
|---|---|
| `epm/5ab004/data` | 4599 |
| `epm/5ab004/cmd` | 2 |

- Zero cross-talk from unexpected topics.
- Zero `WARN`/`ERROR`/`Exception`/`malformed`/`disconnect` matches in the
  dashboard-side log for the full session.

**Throughput:** ~4.3 msg/s against a nominal 5 msg/s (200ms
`EPM_NET_PUBLISH_INTERVAL_MS`). Observed, unexplained at the margins — most
likely attributable to DSP cycle timing rather than transport loss (the 2
successful `cmd` round-trips rule out a congested or dead link), not a
confirmed root cause.

## Known limitation of this test

Broker-side connect/disconnect logging was disabled (`log_dest`/`log_type`
commented out in the native Windows Mosquitto's `mosquitto.conf`) for this
run. The "zero drops" observation above rests on subscriber-side and
dashboard-side evidence only, not the broker itself. Recommend enabling
broker logging before the next soak test.

## What this does NOT prove

This validated the wire protocol and gateway software path against a real
satellite for the first time — not the reference implementation's own
physical hardware. The reference base station's own MCU<->MPU link and
hardware-specific bridges were not exercised.

---

## Addendum: 2026-08-07 — Repeat/Regression Check

**Date:** 2026-08-07
**Branch:** feat/base-station-interop

Repeat of the setup above (real satellite `5ab004` vs. the reference repo's unmodified
`base-station/python/main.py`), run to confirm the wire path still holds after this
session's documentation/diagram/git-hygiene changes. No satellite or gateway code was
touched between the original run and this one.

### Deviations from the original setup

- **Broker path:** connected via `192.168.1.5:1883` (Ethernet), not the `192.168.1.7`
  (WiFi) address in the original entry above. Same broker process, same port — only the
  local NIC differs. At the time of this run the WiFi adapter had no resolved Windows
  network profile, so `.7` was not a live path to test against; `.5` was the address the
  satellite's own connection was already landing on.
- **STATUS_LED step skipped:** this session's node registration was a fresh,
  uncommissioned `5ab004` (unlike the original run's already-commissioned node), and the
  reference dashboard only pushes a `STATUS_LED` command as a side effect of a
  commissioned node's status transitions — it exposes no manual/direct trigger for an
  uncommissioned node. Running the node through full commissioning just to exercise this
  wire path was judged out of scope for a regression check with no LED/firmware code
  changed this session. `epm/5ab004/cmd` traffic was correspondingly zero.

### Result

- Node `5ab004` registered on the dashboard (`/nodes`) with `sensor_config`/`input_dim`
  matching the original run.
- Dashboard process uptime: **~5h43m**, zero `WARN`/`ERROR`/`Exception`/`malformed`/
  `disconnect` matches across the full log. The satellite's broker-side TCP session held
  a single stable ephemeral port (`50041`) for that entire span — no evidence of a
  reconnect at any point.

### Soak window

Dedicated ground-truth capture (`mosquitto_sub -t 'epm/#' -v`, single unbroken process):
**10.5 minutes (22:08:29-22:18:59)**, not the originally intended 15-20 minutes — this
was a timekeeping mistake on the operator side, caught and corrected before reporting,
not a shortened test by design.

| Topic | Count |
|---|---|
| `epm/5ab004/data` | 3001 |
| `epm/5ab004/cmd` | 0 |

**Throughput:** ~4.76 msg/s vs. nominal 5 msg/s (vs. ~4.3 msg/s in the original run).

### Conclusion

Satellite-to-reference-dashboard interop still holds after this session's changes. No
code changed, so this is a confirmation of the original result, not a new validation.

---

## Addendum: 2026-08-08 — Transient zero-publish window, resolved by reset (unconfirmed cause)

**Date:** 2026-08-08
**Branch:** feat/base-station-interop

While preparing for the Phase 1 bench-signal-gen hardware run (ADR-035), the satellite
connected and subscribed cleanly on boot (`connected, subscribed to epm/5ab004/cmd`,
matching broker target `192.168.1.5:1883`), but **zero application data reached a live
independent MQTT subscriber for an extended window**, despite the firmware's own
`net_task_get_stats()` diagnostics reporting `frames_built` incrementing normally with
`build_failures=0` and `publish_failures=0` throughout — i.e. every layer the firmware
can see reported success while no bytes demonstrably arrived.

This session had an unrelated real power/router outage shortly before this was observed
(confirmed separately: WiFi dropped, then recovered once power was restored), which is
the leading candidate for how broker-side client state could have gone stale, though this
is not confirmed.

### What was ruled out before the reset

- IP/broker-target mismatch (the specific failure mode from the 2026-08-07 addendum
  above): firmware's `broker=192.168.1.5:1883` matched this PC's live Ethernet IP
  exactly — no mismatch this time.
- Duplicate process bound to port 1883: only one real broker (`mosquitto.exe`) was bound
  to `0.0.0.0:1883`; the second process found on that port was WSL's `wslrelay.exe`,
  IPv6-loopback-only, unrelated.
- Broker listener misconfiguration, ACLs, packet-size limits, bridges: `mosquitto.conf`
  checked directly, clean/unrestricted (`listener 1883 0.0.0.0`, `allow_anonymous true`,
  no `acl_file`/`bridge`/`mount_point`).
- Sensor/queue starvation (no mic+IMU frame pair ever ready to send): `mic`/`dsp`/`imu`/
  `accel` DIAG counters all showed healthy continuous production alongside the `net`
  counters, ruling this out.
- Windows Firewall: rule correctly scoped to allow inbound TCP/1883; also moot since TCP
  was already demonstrably establishing.

### What actually resolved it

A DTR/RTS serial reset of the satellite (forcing a fresh boot, fresh WiFi association,
and fresh MQTT session) immediately fixed it. The very next boot's DIAG line showed
healthy counters (`net: frames_built=100 build_failures=0 publish_failures=0
cmd_malformed=0 disconnect_reverts=0` by ~31s uptime), and a live independent `paho-mqtt`
subscriber confirmed 98 real `epm/5ab004/data` messages (3826 bytes each) in the
following 20s window.

### Suggestive but unconfirmed evidence

`$SYS/broker/clients/connected` read **1** during the zero-publish window (queried while
a genuinely fresh, freshly-`CONNACK`'d satellite session was the only expected client)
and **3-4** immediately after the reset, with multiple test-harness connections active.
This is consistent with — but does not prove — stale broker-side client/session state
left over from before the outage (or from earlier test-harness connections that weren't
cleanly torn down) silently stranding the satellite's publishes without surfacing as a
connection error on either end. `mosquitto`'s broker-side connect/disconnect logging was
not enabled during this session (same known limitation noted in the original 2026-08-07
entry above), so this cannot be confirmed from broker logs.

### Status

**Not a closed investigation.** No packet capture was taken (the live independent
subscriber confirming successful delivery after the reset made it unnecessary for
unblocking the bench run), so the exact mechanism of the zero-publish window was never
directly observed — only bracketed by before/after state. Flagging this as an explicit
**watch item for the Phase 3 soak test**: if a similar zero-publish-despite-healthy-
counters window recurs during an extended soak, enable broker-side connect/disconnect
logging first (per the existing known limitation) and capture packets during the actual
window, rather than resetting past it.

---

## Addendum: 2026-08-11 — Wire re-verification after ADR-040/ADR-032, plus a live-reproduced stall

**Date:** 2026-08-11
**Branch:** feat/base-station-interop
**Commit:** 0bc9c0725018939d0a1b51adee40014df7a602fc

### Context and scope

Contest submission rules require the Arduino UNO Q as the primary board, with Rahul
listed as a teammate. That makes a real pairing test against **his actual physical UNO
Q hardware** the eventual goal — but today Rahul was remote at his own home while this
session ran from a different location, so no physical co-location was possible. That
specific test is off the table for this session regardless of anything else; no
speculative "should work" writeup is included for it here.

What *was* tractable: two wire-format changes have landed since the last interop check
(2026-08-08 addendum above) — ADR-040 (128→256 FFT bins) and ADR-032 (HFRT envelope
channels, scalar ids 9-11) — and neither had been re-verified against Rahul's own
unmodified base-station code. This re-verifies the wire contract only, via
`tools/devrig/` running his real, untouched `base-station/python/main.py` under WSL
against the same live satellite (`5ab004`, uptime ~17h at test time), pointed at the
same native Windows Mosquitto broker (`192.168.1.5:1883`) the satellite was already
connected to. No reference-repo files were modified.

### Result: wire format still holds

- `5ab004` registered on `/nodes/5ab004` with `sensor_config: ['accel_x', 'accel_y',
  'accel_z', 'mic']` and **`input_dim: 1048`** — up from the `536` last documented
  (2026-08-04 contract doc, pre-ADR-040/032). His code computes `input_dim` dynamically
  from the actual wire bytes rather than a hardcoded constant, so this jump required
  **zero changes on his side** — the dynamic-sizing design absorbed both firmware
  changes automatically.
- Zero cross-talk: only `epm/5ab004/data` and `epm/5ab004/cmd` topics observed.

### Soak window

14m16s continuous `mosquitto_sub -t 'epm/#' -v` capture inside WSL, cross-checked
against Rahul's own dashboard (`/nodes/5ab004`) and our own production gateway's
dashboard as two independent second/third observers:

| Topic | Count (at final check, still climbing) |
|---|---|
| `epm/5ab004/data` | 2091 |
| `epm/5ab004/cmd` | 0 |

**Throughput:** ~4.75 msg/s during confirmed-healthy windows — consistent with the
2026-08-07 addendum's ~4.76 msg/s and the original run's ~4.3 msg/s.

### Live-reproduced MQTT stall (self-recovered)

Partway through this soak, the data stream went completely silent for **~400-450
seconds**, independently confirmed by two separate subscriber implementations at once:

- The raw `mosquitto_sub` capture: message count froze at exactly 1944 from t≈409s
  through at least t≈818s into the soak (the pre-stall rate of ~4.75 msg/s × 409s
  predicts 1943 — an exact match, confirming the freeze point).
- Rahul's own unmodified `base-station/python/main.py`: its `/nodes/5ab004.last_seen`
  field independently froze at the same wall-clock instant and was still reporting that
  same stale value ~409s later.
- Our own production gateway (third, differently-implemented observer) shows `fps`
  dipping 4.8 → 3.5 across the same window while `frame_count` kept climbing —
  consistent with a publish-side stall rather than the satellite itself locking up.

Both external subscribers recovered on their own before the next check (~t=856s) — no
satellite reset, WiFi bounce, or manual intervention. This is a live, real-hardware
reproduction of the pattern root-caused in commit `0bc9c07` ("recurring blue-LED drops
as MQTT-layer stalls, not WiFi"), and the fact that it was caught **simultaneously by
Rahul's own independent MQTT subscriber code** rules out "bug specific to our gateway's
ingestion path" as an explanation — the loss point is upstream of any one subscriber
(broker- or satellite-publish-side), not a parsing/ingestion bug in either codebase.

A trailing `mosquitto_sub ... '$SYS/broker/clients/connected' -C 1 -W 5` check chained
onto this soak's automation exited non-zero (27) during the live run; a standalone
retry moments later exited cleanly (0, no retained value inside the 5s window). Given
the nested `wsl.exe -- bash -c "..."` invocation this ran under, and that the retry
didn't reproduce a hang, this reads as a shell-nesting artifact of the test harness, not
a second broker fault — treated as inconclusive, not folded into the stall finding
above.

### Stage 2 & 3 — not attempted this session

Deploying our gateway onto real UNO Q hardware (Stage 2) and a reliability soak on
Rahul's actual network (Stage 3) both require physical access to his board. Flagged as
outstanding for the next session where that's possible, rather than written up
speculatively here.

### Gateway-choice evidence gathered this session

Read (not exercised) from the reference repo to inform the "which gateway ships"
decision, since real-hardware fault-detection validation to date in this project has
only ever run against our own classifier (`gateway/pipeline/alerting.py`):

- Rahul's system does have a second-stage supervised fault classifier
  (`base-station/python/pipeline/classifier.py`, Edge-Impulse-trained TFLite). Per his
  own `docs/EDGE_IMPULSE_FAULT_CLASSIFICATION_PLAN.md`, it is currently ~42% accurate,
  trained on a 4-class Kaggle vibration dataset (`Ideal`/`Cracking`/`Offset_Pulley`/
  `Wear`) that doesn't map onto this project's fault taxonomy (Bearing/Imbalance/
  Misalignment/Looseness), and is explicitly scoped **out** for his real local hardware
  nodes — that doc states local SPI motors "stay anomaly-only." His classifier only
  runs against simulated/replayed satellites, not real hardware.
- Our gateway's fault classifier is physics-based (bearing-resonance-band scoring,
  Bayesian multi-channel fusion, RUL estimation) and has been validated against real
  hardware repeatedly this project (see the accuracy-harness and Phase B/D closeout
  memory entries).
- Rahul's `classifier.py` docstring documents real-hardware GPU/NPU findings on the
  actual QRB2210/QCM2290 board relevant to our own (not-yet-attempted) Stage 2 plan:
  `ai-edge-litert`'s GPU delegate SIGILLs on this board (Kryo 260 CPU lacks ARMv8.1 LSE
  atomics), there is no usable NPU path (`/dev/fastrpc-cdsp` absent), and the `ncnn`
  Vulkan GPU path works but shows zero speedup on a model this small — a concrete,
  real-hardware precedent worth planning around before assuming GPU acceleration pays
  off for our own `mic_tools/inference_gpu.py` path.

**Net read:** on fault-type classification specifically, running our own gateway keeps
real, validated capability in the submission; running his drops it to anomaly-only for
any real hardware node. This is evidence gathered from his repo, not a decision made
for him — his own repo/hardware calls are his to make.

### What this does NOT prove

Same caveat as every entry above: this validates the wire protocol and reference
gateway software path against a real satellite, not the reference implementation's own
physical UNO Q hardware. It does add one new thing beyond re-confirming the wire
contract: the first live, dual-independent-subscriber reproduction of the known
MQTT-stall pattern, caught in the wild during a real interop test rather than
constructed intentionally.
