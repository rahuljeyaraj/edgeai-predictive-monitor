# Progress 4 — machinery protection trip: confirm-race FIXED, gate energy calibration OPEN (2026-07-30)

> **Superseded in part by [progress5.md](progress5.md).** §4's open gate-energy
> problem is now root-caused and fixed, and §4's leading hypothesis (a DC term
> in the FFT bins) turned out to be **wrong** — the firmware already discards
> bin 0. The real cause was the accelerometer's broadband noise floor
> dominating an all-bins RMS. §7.2 and §7.3 are done. Everything else in this
> file still stands.

Condensed handoff for the next session. Predecessor: [progress3.md](progress3.md)
(RGB ring SPI/DMA work — unrelated to this file, its pixel-0 issue is still
separately open). [MOTOR_STOP_PLAN.md](MOTOR_STOP_PLAN.md) is **stale** — its
own header still says "design-only, not yet implemented," but that plan was
superseded and the machinery-protection-trip feature it describes was actually
built and committed as `ebc08f4` before this session started. This file picks
up from there: two real bugs found in that already-shipped feature, one fixed,
one open.

---

## 1. Where this stands right now

**Fixed, tested, deployed:** a confirm-logic race in `python/protection/protection.py`
that made `protection: trip for <node> was NOT confirmed` ("Trip failed —
machine still running" in the UI) possible even when the machine really was
stopped. See §2.

**Fixed, tested, deployed:** the vibration gate (`python/pipeline/gate.py`)
now excludes the mic channel from its running/stopped energy calculation —
accelerometer only. See §3.

**Still open, NOT fixed:** even accelerometer-only, this node's idle
"energy" (~7000–8000) sits uncomfortably close to its real running energy
(~9000–13400) — only a 30–40% margin, not the "orders of magnitude" gap the
design assumed. STOPPED is technically reachable now (it wasn't before commit
`ebc08f4`'s fix either, until re-commissioning — see gate.py's own module
docstring), but the margin is thin enough that the node is currently sitting
in FAULT/WARNING flap at rest. See §4 for the numbers and a live-verified
hypothesis (DC/gravity bias, not ambient noise) that needs someone to actually
open `features.py`/the FFT pipeline to confirm.

**The physical trip mechanism itself (MQTT → serial → real motor stop) is
proven working end-to-end on real hardware this session** — see §5. That was
the part of the original plan (`ebc08f4`) that had never actually been
live-tested against the real rig before today.

---

## 2. The confirm-race bug (FIXED)

`ProtectionController.on_motor_state()` (the only thing that can confirm a
trip) is **edge-triggered** — `pipeline/manager.py`'s `_report_motor_state()`
only calls it when the gate's confirmed running/stopped state *changes*.
`_fire_trip()` starts a 3s confirm timer and waits for that callback.

The bug: if the machine was **already** stopped before the trip fired — an
operator stopped it by hand, or the FAULT that triggered the trip was itself
detected against an already-quiet signal — the edge already happened in the
past. No future edge is coming, so the confirm timer always times out and
`trip_failed` latches permanently, even though the machine is genuinely
stopped. This is exactly what reproduced the user's second symptom too
("healthy → operator stops it → shows FAULT, not IDLE" — same root cause,
the recovery edge that should force HEALTHY/IDLE never re-fires because it
already fired once, before the fault/trip existed).

**Fix:** added a live, level-triggered query alongside the existing edge
callback.
- `pipeline/manager.py`: `MotorPipeline.motor_running` property (reads
  `self._inference.motor_state` directly, not the edge cache) +
  `PipelineManager.is_running(node_id)`.
- `protection/protection.py`: `ProtectionController.set_motor_state_query()`
  (mirrors the existing `set_publish_trip()` late-binding pattern, same
  reason — `main.py` builds `PipelineManager` after `ProtectionController`).
  `_fire_trip()` now checks this query right after publishing; if it reports
  already-stopped, confirms instantly instead of waiting on an edge that will
  never come.
- `main.py`: wires `protection.set_motor_state_query(manager.is_running)`
  right after `PipelineManager` is constructed.

**Regression test:** `tests/protection_test.py`'s
`test_already_stopped_machine_confirms_instantly_via_query` — fails on the
pre-fix code (same assertions as the existing
`test_unconfirmed_trip_is_reported_failed_and_stays_fault`, but with the query
wired in), passes after. Full suite still green (12/12 in `protection_test.py`,
plus `pipeline_manager_test.py` unaffected).

**Not yet committed as of writing this section** — see §7 for the exact
commit to make.

---

## 3. The mic-exclusion fix (FIXED, but see §4 — it wasn't sufficient alone)

User's call, confirmed correct in principle: whether a motor is turning is a
mechanical fact, and `compute_energy()` (gate.py) was summing mic bins into
the same RMS as accel bins. Ambient/room noise picked up by the mic inflates
the "energy" reading with something that has nothing to do with the motor.

`compute_energy()` now prefers accelerometer channels
(`chan.startswith("accel")`) and only falls back to summing every channel
present (i.e. mic) when a frame has **no** accel channel at all — that's the
real, supported mic-only `sensor_config` case
(`features_test.py::test_single_sensor_mic_only`,
`inference_test.py`/`commissioning_test.py`'s synthetic frames are all
mic-only and would otherwise always compute zero energy and never leave
MotorState.STOPPED — this fallback is why they still pass).

`sensor_frame.py`'s module docstring was updated too — it used to name
`gate.py`'s energy computation as an example of "iterates every channel
generically," which is no longer true.

**running_energy_ref is scale-dependent on this definition** (same rule as
the original relative-threshold design) — `base_station` was re-commissioned
under the new accel-only definition this session: **28007.82 → 11432.79**
(commissioned live at 90rpm/3-motors baseline, see §5's rig commands). Any
node commissioned before this change needs re-commissioning again for the new
number to apply, same requirement gate.py's docstring already documents for
the original running_energy_ref bug.

---

## 4. OPEN: idle/running margin is too thin (needs a decision, not just a fix)

Live-measured on real hardware this session (temporary `logger.info` in
`compute_energy()`'s caller, removed before every commit — do not leave
debug logging in gate.py, verify with `git diff` before pushing):

| condition | accel-only energy | commissioned running_energy_ref | 0.15-fraction threshold |
|---|---|---|---|
| all 3 motors OFF (rig physically silent, confirmed by the user) | ~7000–8000 | — | — |
| all 3 motors at 90rpm baseline | ~9000–13400 (median → 11432.79) | 11432.79 | 1714.92 |

**Mic was not the dominant noise source** — excluding it barely moved the
idle number (it was ~6600–7250 mic+accel combined, ~7000–8000 accel-only;
same order of magnitude). Whatever is keeping "idle" energy at ~65-70% of
"running" energy is coming from the accelerometer channels themselves.

**Live-verified consequence:** with the current commissioned baseline and
default `--gate-running-fraction 0.15` (threshold 1714.92), idle energy
(~7000-8000) is **~4-5x above** that threshold — STOPPED is unreachable
in practice, same failure shape as the original absolute-threshold bug this
whole relative-threshold design replaced, just at a different layer.
Confirmed live: with the rig fully powered down, `base_station` kept
computing fresh (non-frozen) anomaly scores and flapping FAULT/WARNING
rather than settling to IDLE.

**Leading hypothesis, NOT confirmed:** `compute_energy()`'s RMS is over raw
FFT-bin magnitudes (per gate.py's own module docstring). If bin 0 (or a few
low bins) carries a large, roughly-constant DC/gravity term, it would
dominate the RMS regardless of whether the motor is spinning, which would
explain why idle and running energy are the same order of magnitude instead
of differing by orders of magnitude. **This needs someone to actually open
`features.py`/wherever the FFT is computed and check whether bin 0 is DC**,
not another round of threshold-guessing — confirming/excluding this is a
prerequisite to picking a `--gate-running-fraction` that will actually be
reliable rather than fragile.

**What was NOT done, and is the actual next step:** no `--gate-running-fraction`
value was changed or deployed. A value around 0.7-0.8 might separate the two
observed ranges today, but the margin is thin enough (idle can spike to 8285,
running can dip to 8937 — overlapping in the raw samples observed) that this
would be fragile, not a real fix. Recommend investigating the DC-bias
hypothesis first; if confirmed, the real fix is likely excluding bin 0 (or a
small guard band) from `compute_energy()`'s sum, the same kind of change §3
already made for the mic channel, and would need a fresh re-commissioning
pass afterward regardless.

---

## 5. Live hardware verification done this session (proves the trip mechanism itself works)

The rig's Arduino was in Windows-native `usbipd` "Shared" state (COM10, busid
`3-3`, `2341:0043`) — attached to WSL this session via (from within WSL,
usbipd-win's client exe is on PATH through Windows interop):
```
usbipd.exe attach --wsl --busid 3-3
```
It shows up as `/dev/ttyACM1` (NOT `/dev/ttyACM0` — that's the base
station's own board, busid `1-5`, already attached).

The on-device mosquitto broker (port 1883 on the UNO Q) was reached from WSL
over an **adb-forwarded tunnel**, not the board's own WiFi hotspot IP
(10.42.0.1 isn't routable from this WSL VM) — and NOT port 1883 directly
either, because this dev machine already runs its own unrelated local
mosquitto on 1883:
```
adb forward tcp:18830 tcp:1883   # -> localhost:18830 reaches the board's broker
```
(`tcp:8080 tcp:8080` for the dashboard REST API was already forwarded from
earlier sessions.)

`motor-driver/motor_driver.py` needs `paho-mqtt`, not installed system-wide
(PEP 668 externally-managed-environment) — a venv was created:
```
cd motor-driver && python3 -m venv .venv && ./.venv/bin/pip install paho-mqtt pyserial
```

With that, the trip listener connects and receives real commands:
```
./.venv/bin/python motor_driver.py --port /dev/ttyACM1 --mqtt-host localhost --mqtt-port 18830 --hold-open
```
Live log evidence (this session, twice, under a real induced FAULT + 10s
countdown): `*** TRIP RECEIVED: stopping motor 1 ***` followed by `motor 1
stopped -- restart it by hand`. The base station really did detect FAULT,
count down, publish `MOTOR_STOP` over MQTT to `epm/motor_rig/cmd`, and the
listener really did send `1 0.0` over serial to the physical rig. **This is
the first time this had been proven against real hardware** — `ebc08f4`
had only ever been tested with a faked/local publisher, never a real listener
on the real rig.

**Known trap, hit twice this session:** don't run `motor_driver.py`'s full
default-timed scripted profile (`--baseline-s`/`--sweep-s`/`--fault-s`
defaults) expecting to observe a live trip — the profile's own fixed timing
raced the base station's variable fault-detection timing twice, and the
script disabled + exited before the trip could land both times. Use
`--hold-open` plus driving the `Rig`/`TripListener` classes directly for an
open-ended hold (see the throwaway `hold_fault.py`/`hold_baseline.py`
pattern used this session — not committed, was scratch-only) if you need the
condition to persist indefinitely while you watch.

**Cross-talk risk from the original plan is real and was reproduced live:**
with motor 1 tripped/stopped but motors 2/3 still spinning, the shared
sensor kept reading "running" (motor 1's own stop never dropped the combined
signal below threshold) — exactly the risk `MOTOR_STOP_PLAN.md`/the locked
plan called out in advance. Not something to fix in software; either accept
it for the single-shared-sensor demo topology, or don't run other motors
while testing one motor's trip confirmation.

---

## 6. Deploy/debug discipline learned or re-confirmed this session

**A bare `docker restart <container>` intermittently left the board's
sensor Bridge dead** (zero new frames ingested, `last_seen` frozen, no
error even logged) **twice this session**, both times after a restart that
followed heavy USB/serial activity on the shared USB controller (the
motor rig's usbipd attach/detach happening around the same time). This may
be related to — but is a different symptom from —
[BRIDGE_SPI_ARM_STREAM_STALL_INVESTIGATION.md](BRIDGE_SPI_ARM_STREAM_STALL_INVESTIGATION.md)'s
recurring 2-3s stalls; that doc's issue is a periodic freeze during otherwise
normal operation, this was a full, permanent halt that a plain container
restart did not clear. **What worked, both times:**
```
R=/home/arduino/ArduinoApps/edgeai-predictive-monitor-base-station
adb shell "arduino-app-cli app stop $R"
adb shell "arduino-app-cli app start $R"     # re-enumerates USB, resets the MCU side too, ~1-2 min (rebuilds/reflashes the sketch)
```
A plain `docker restart` only restarts the Python container, not the MCU-side
link — use the full `app stop`/`app start` cycle whenever ingestion looks
dead after a restart, don't just keep restarting the container hoping it
clears.

**Targeted push+restart** (per
[deploy-via-targeted-file-push memory]) worked fine for all the
Python-only edits in this session:
```
REMOTE=/home/arduino/ArduinoApps/edgeai-predictive-monitor-base-station
adb push python/pipeline/gate.py "$REMOTE/python/pipeline/gate.py"
adb shell "docker restart edgeai-predictive-monitor-base-station-main-1"
```
Use the full `app stop`/`app start` cycle above instead if frames stop
flowing afterward.

**Running the test suite live, in the container, against real dependencies**
(no local venv on this dev machine has `torch`/`python-statemachine` —
installing them here is unnecessary, the on-device venv already has them):
```
C=edgeai-predictive-monitor-base-station-main-1
adb shell "docker exec $C sh -c 'cd /app && . .cache/.venv/bin/activate && PYTHONPATH=python/registry:python/protection python3 tests/protection_test.py'"
```
(Adjust `PYTHONPATH` per each test file's own header comment — they differ
per file, e.g. `pipeline_manager_test.py` wants
`ingestion:registry:pipeline:history`.) Push the test file itself the same
way if you've edited it — `adb push tests/protection_test.py "$REMOTE/tests/protection_test.py"`.

---

## 7. Left to do, in order

1. **Commit** the confirm-race fix (§2) and the mic-exclusion fix (§3) —
   both are deployed live and test-verified but were not yet committed as of
   this doc being written. See the actual commit for the exact file list.
2. **Investigate the DC-bias hypothesis** (§4) before touching
   `--gate-running-fraction` — open `features.py`/wherever FFT bins are
   produced and check whether bin 0 (or low bins) carry a large constant
   term. This is the real blocker for both original user reports (trip
   confirmation, and idle-vs-fault status) being fully resolved in practice,
   even though the code-level race (§2) is fixed.
3. Once §4's root cause is confirmed/fixed, **re-commission `base_station`
   again** (same two REST calls as §3: `POST /nodes/base_station/commission/start`,
   spin the rig at baseline, `POST /nodes/base_station/commission/stop`) and
   re-verify live: rig fully off should settle to IDLE (not flap
   FAULT/WARNING), and a real induced FAULT should trip, publish, get
   received by `motor_driver.py`'s listener, and confirm TRIPPED within the 3s
   confirm window.
4. Physical rig is currently **disabled/powered down** (left that way
   deliberately at the end of this session) and still attached to WSL as
   `/dev/ttyACM1` (`usbipd.exe attach --wsl --busid 3-3` from Windows, or
   `usbipd.exe detach --busid 3-3` to hand it back to Windows/the browser
   dashboard). `adb forward`s for `tcp:8080` and `tcp:18830` are still active
   in this WSL session as of this writing — they don't survive an `adb
   kill-server` or a fresh shell if `adb` was restarted, re-add them if
   `curl localhost:8080/nodes` / MQTT stop working.
5. `trip_motor_idx=1` is still set on `base_station`'s registry entry and
   `armed: true` — the mapping from §5's live test is still live, no need to
   re-set it.

---

## Do not re-litigate

- The absolute-threshold bug (0.05 vs ~19000) and the original
  `running_energy_ref`/re-commissioning requirement — already fixed, see
  gate.py's own module docstring and the machinery-protection-trip plan.
- Whether the trip mechanism (MQTT → listener → serial → real stop) works —
  **yes, proven live this session, §5**. Don't re-verify from scratch; if
  something looks broken, suspect the listener not running / wrong
  `--mqtt-host`/port / rig not attached to the right side (usbipd) before
  suspecting the mechanism itself.
- Whether mic was the dominant idle-noise source — **checked live, it
  wasn't** (§4). Don't re-propose "just exclude mic" as if it were untested;
  it's already excluded and the gap barely moved.
- The three previously-rejected trip-mechanism designs (typed topic string,
  per-motor dropdown, full controller discovery) — still rejected, see the
  machinery-protection-trip plan/memory, not re-litigated this session.
