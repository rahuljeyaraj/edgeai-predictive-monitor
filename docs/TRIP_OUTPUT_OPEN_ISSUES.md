# Trip output — open issues

Findings from a live debugging session on 2026-08-02, against the real UNO Q
(`adb forward` → `localhost:8080`) with the rig announcing on the broker.
Nothing here is speculative: every claim below was checked against the running
device or reproduced with a request.

Written to be picked up cold in a later session. Background on the feature
itself is in [MOTOR_STOP_PLAN.md](MOTOR_STOP_PLAN.md) (the trip) and
[UNIFIED_COMMISSIONING_PLAN.md](UNIFIED_COMMISSIONING_PLAN.md) §3 (the guided
setup step that maps one).

---

## 0. What a trip output is, in one paragraph

The system watches an asset's vibration. When it confirms a FAULT, it can stop
the machine — that is the *trip*, and it is the only command this system ever
sends (`protection/protection.py:1-10`). A **trip output** is the mapping from
one monitored asset to one motor on the rig it is allowed to stop. The rig
announces which outputs it offers over MQTT, retained, so the dashboard never
hardcodes a motor list (`protection/trip_outputs.py`). The setup step offers to
*prove* the mapping: publish a stop to that output, then watch this asset's own
vibration go quiet. (That step is number **5** as of 2026-08-02 — it was 2, and
§1 is the whole story of why it moved.)

---

## 1. FIXED — the "Test" button could never succeed on a fresh asset

**Fixed 2026-08-02** by taking candidate fix 2: *Trip output* moved from step
2 to step 5, after Train. The analysis below is kept because it is what
justified the move, and because the "off once" argument it demolishes is the
one that will get the step moved back if nobody writes down why it was wrong.
The resolution is at the end of this section.

**Symptom.** In Setup → step 2 *Trip output*, clicking **Test** returned 409:

> start the machine and wait for it to read as running before testing the trip
> output — a machine that is already stopped would appear to confirm whichever
> output we tried

The message is wrong, and starting the motor does not help. Reproduced live:

```
POST /nodes/base_station/trip_motor/confirm  {"motor_idx": 1}
  → 409  {"error": "start the machine and wait for it to read as running ..."}
```

**Root cause — a step-ordering contradiction.**

| # | Fact | Where |
|---|------|-------|
| 1 | The confirm test refuses unless the gate reports RUNNING | [protection.py:291-296](../base-station/python/protection/protection.py#L291-L296) |
| 2 | That answer comes from `MotorPipeline.motor_running`, which returns `None` when `self._inference is None` | [manager.py:162-170](../base-station/python/pipeline/manager.py#L162-L170) |
| 3 | `self._inference` is only built once `entry.model_path` is set | [manager.py:190](../base-station/python/pipeline/manager.py#L190) |
| 4 | `model_path` is set by **step 5 (Train)** | [setup_controller.py:56](../base-station/python/api/setup_controller.py#L56) |
| 5 | But *Trip output* is **step 2 of 6** | same line |

So step 2 depends on something that does not exist until step 5. The test is
only ever runnable by re-opening the step on an already-commissioned asset.

**Why the message misleads.** `is_running()` returns `None` (no pipeline at
all), and `None` falls into the same `running is not True` branch as a genuinely
stopped machine — so the operator is told to start a motor that is already
running. This is the single most confusing part of the symptom and should be
fixed even if the ordering is not.

**Also note:** the gate's own calibration (`stopped_energy_ref`,
`running_energy_ref`) is captured at steps 3 and 4 — *also* after step 2. So at
step 2 there is no calibrated running/stopped detection of any kind. The
classification gate (`_classification_gate`) does not help: it is only fed when
a fetched classifier exists for the asset class
([manager.py:293-297](../base-station/python/pipeline/manager.py#L293-L297)).

**Live state that produced this** (device, 2026-08-02):

```
base_station: status=uncommissioned, model_path=null,
              running_energy_ref=null, stopped_energy_ref=null,
              trip_motor_idx=null, setup step=trip_output (2 of 6)
```

**Not a deploy problem.** On-device `api/app.py`, `registry/registry.py`,
`frontend/setup.js` and `api/setup_controller.py` all md5-match the repo.

### Candidate fixes

1. **Self-calibrating test.** Keep the step at position 2. Measure raw frame
   energy for ~1s, publish the stop, confirm if energy collapses to a small
   fraction of what it was. Needs no model and no baseline, so it works on a
   brand-new asset — and it puts "watch it stop the motor" early in the flow
   rather than after training. Requires exposing live frame energy from
   `MotorPipeline` before `_inference` exists.
2. **Move the step after Train.** ← **APPLIED.**
3. **Message only.** Make the 409 say "this asset isn't commissioned yet" and
   point at *Use without testing*. Honest, but leaves the test unusable on new
   assets. Applied *as well*, for the residual `None` case.

### Why 2, and why the "off once" objection was wrong

Option 2 was originally discounted because moving the step past Train makes
the operator switch the machine off twice instead of once — the exact property
position 2 was chosen for.

**That saving was never being collected.** Every precondition is checked
before `_publish_trip`, so the 409 meant *nothing was sent to the rig* and the
machine never stopped. The operator switched it off by hand for the Off step
either way, on every single run. Position 2 was paying for an operator action
it did not save.

And the cost inverts once the step sits at 5. The conditions step (3) has just
had the operator running the machine, so at step 5 it is *already running* —
which is precisely the precondition the test needs. The step's hint now reads
"Leave the machine running", not "Start the machine".

### What changed

| Where | Change |
|---|---|
| [setup_controller.py](../base-station/python/api/setup_controller.py) | `STEPS` reordered to name → stopped → conditions → train → **trip_output** → done |
| [setup_controller.py](../base-station/python/api/setup_controller.py) | `finish_training()` now moves to `_next_step(STEP_TRAIN)` instead of hardcoding `STEP_DONE` — that hardcode would have silently skipped the newly-relocated step |
| [protection.py](../base-station/python/protection/protection.py) | `running is None` split off from `running is False`, with its own message ("this asset has no model yet…") instead of telling the operator to start a machine that is already running |
| [setup.js](../base-station/python/frontend/setup.js) | Step hint: "Start the machine" → "Leave the machine running" |

`setup_test.py` covers the new order, including an assertion that training
hands on to the trip-output step rather than to Done, and one that the
no-gate message never says "start the machine".

### Workaround, still available

**"Use without testing"** records the mapping as unconfirmed and unblocks
*Continue*. Trips still fire; the wiring is simply unproven. Still the right
answer for an asset whose machine cannot be stopped during commissioning.

---

## 2. DONE — one motor at start, and adding one is a UI action

**Symptom.** Setup step 2 showed Motor 1 / 2 / 3. The story monitors one
asset, so two of the three offered outputs were noise.

**Cause.** `motor_driver.py` announced every motor it can drive, because
`MOTOR_IDS` was doing double duty as both the trip whitelist and the offered-
outputs list.

**Fixed**, and then taken further than a CLI flag — see §3.

- `OutputSet` now holds the installed set, separate from `MOTOR_IDS` (still
  the hardware capability, and still the trip whitelist in `_on_message`).
  It's mutable at runtime and notifies subscribers; `TripListener` subscribes
  its announce, so a change re-announces immediately.
- `--motors` sets the startup set and **defaults to `1`**. Indices not on the
  rig are rejected before the port is opened rather than announced — an
  offered output the rig would then refuse is the one mapping that looks
  armed and does nothing.
- An empty set publishes `{"outputs": []}` rather than staying silent, which
  is what clears a previous retained announce off the broker.

```bash
./start_motor_driver.sh
```

Verified: `rig_trip_test.py` and `control_page_test.py` all PASS; announce
payload is `{"outputs": [{"idx": 1, "name": "Motor 1"}]}`; empty gives
`{"outputs": []}`. The announce is retained, so restarting the rig host
**overwrites** whatever is currently on the broker.

**Do NOT "just disable" the announce.** With nothing announced the dashboard
falls into the manual fallback — a typed output number and no Test button at
all ([setup.js:221-239](../base-station/python/frontend/setup.js#L221-L239)).
That is strictly worse than announcing one output.

---

## 3. DONE — motor count as a UI control

The ask was to make the offered-motor count a control on the motor-driver
page rather than a CLI flag. The original analysis concluded it was
impossible, on these facts:

- [dashboard.html](../motor-driver/dashboard.html) is a **Web Serial** page.
  It drives the Uno over USB and has **no MQTT client**.
- The announce is published by the rig host — different process, different
  transport.
- The broker is TCP-only (`listener 1883`, no `websockets` listener anywhere
  in the repo), so a browser page cannot publish to it as-is.

All three are still true. The conclusion was wrong because it only considered
**browser → broker directly**. There is a hop in between: the rig host
already holds the MQTT client, and `start_dashboard.sh` was already running a
`python3 -m http.server` purely to serve the page. Merging those two removes
the problem.

**What was built.** `motor_driver.py` now serves `dashboard.html` itself
(`ControlServer`, stdlib `http.server`, bound to localhost) and exposes:

| Endpoint | Does |
|---|---|
| `GET /api/state` | installed motors, tripped motors, base-station URL |
| `POST /api/installed` | replace the installed set → **re-announce** |
| `POST /api/trip/clear` | re-arm a tripped motor (`Rig.clear_trip`) |

No websockets listener, no `sudo` on the UNO Q, no new dependency. The old
`start_dashboard.sh` is gone; `start_motor_driver.sh` starts the rig host,
which serves the page.

**The three visual changes this unlocked:**

1. **The rig starts as one motor.** Motors 2 and 3 are dashed `+ Add Motor`
   slots. Clicking one installs it *and* re-announces, so a new trip output
   appears in the base station's setup live, with nothing restarted. `✕`
   removes one again (locked out while it's running).
2. **A PROTECTED badge per motor.** The page reads the base station's
   `GET /trip_outputs` directly — cross-origin, which works because
   [app.py:361](../base-station/python/api/app.py#L361) already sends
   `allow_origins=["*"]`. A claimed motor shows `● PROTECTED · <asset>`, and
   the header counts them.
3. **A trip lands on the motor's own card.** Previously a trip was a line in
   the rig host's *terminal*: this page went on showing the motor as RUNNING
   at its old speed, and its ramp tick was free to command it back up the
   moment anyone touched a slider. Now the card turns red, the pill reads
   TRIPPED, the run switch is locked, the reason names the asset that
   faulted, and only **Reset & re-arm** brings it back.

Point 3 also partially covers for §1: the mapping still can't be *proved*
during setup on a fresh asset, but when a real trip fires, it is now
unmissable on the rig's own page.

**Serial ownership is unchanged.** The browser still drives the Uno over Web
Serial while the rig host holds the same port over pyserial — exactly what
the old `--hold-open` mode did. Moving motor control onto the HTTP hop too
(which would end that contention and drop the Chrome/Edge-only restriction)
is a bigger rework and was not done.

**Verified.** `control_page_test.py` drives the real page in headless
Chromium against a real `ControlServer` and a stand-in base station — 28
assertions, all PASS. Not yet run against the real UNO Q and rig.

---
## 4. Field notes for whoever picks this up

- **`docker logs` on this device is unreliable.** It terminates with
  `Error grabbing logs: invalid character '\x00' looking for beginning of value`
  and truncates. Timestamps in the surviving lines are the only way to tell a
  recent event from a stale one — several `POST /trip_motor` entries near the
  tail turned out to be from 2026-07-31, not from the current session.
- **The registry lives at `/app/.cache/data/registry.json`** inside the
  container, not `/app/data/`. `find / -name registry.json` also turns up
  `/app/.cache/data-desktop/registry.json` from the desktop-sim runs.
- **Reading the live state beats reading the code** for this feature:
  `GET /trip_outputs` shows what the rig announced plus `claimed_by`, and
  `GET /nodes/<id>/setup` shows exactly which step is blocked.
- **`POST /nodes/<id>/trip_motor/confirm` is safe to probe.** Every precondition
  is checked before `_publish_trip` is called, so a 409 means nothing was sent
  to the rig.
- **The rig host now serves the control page**, so there is one command, not
  two: `motor-driver/start_motor_driver.sh --port <port> --mqtt-host <uno-q>`.
  `start_dashboard.sh` in that folder is gone (the base station's script of
  the same name is unrelated and still exists).
- **`motor-driver/control_page_test.py` needs playwright**, which on this
  machine lives in the base station's venv and needs `LD_LIBRARY_PATH` at an
  extracted `libnspr4`/`libnss3` — see the Playwright setup notes.
- Uncommitted in the working tree at the end of the 2026-08-02 session:
  everything in §2/§3 above, plus `base-station/python/frontend/charts.js`,
  `index.html` and `style.css` (pre-existing, unrelated to any of this).
