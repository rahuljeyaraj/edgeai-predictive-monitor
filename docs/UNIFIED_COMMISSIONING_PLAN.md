# Plan — Commissioning cleanup: one guided setup in the drawer

Status: **Plan only, 2026-08-01 (rev 2). No code changed.**

Rev 2 **drops the class-shared-autoencoder idea entirely** — explicit decision,
it added a shared mutable artifact in the path that decides FAULT, for a
benefit that cannot be measured with one rig's data. The model stays **one
per node**, exactly as today. Do not reopen without new hardware.

This doc is now only about **cleaning up commissioning**: one guided flow, in
one place, that collects everything a node needs, maps it to its trip output,
and proves that output works.

Companion docs:
[MPU_Software_Architecture.md](MPU_Software_Architecture.md) (S3.5, S3.6) ·
[MOTOR_STOP_PLAN.md](MOTOR_STOP_PLAN.md) (protection, trip) ·
[EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md](EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md)
(asset classes, recordings).

---

## 0. What this plan changes

1. **One setup flow** replaces four scattered ones (§1, §2).
2. **Multiple operating conditions** collected during setup — off, and one or
   more running states (no load / full load / whatever this machine has) —
   instead of a single running batch (§2.3).
3. **Sensor → motor mapping moves into setup, and stops being a guess.** The
   hardcoded 3-motor dropdown goes away; the rig announces its own outputs and
   the mapping is *confirmed by actually stopping the machine* (§3).
4. **Protection UI splits by hot/cold.** Configuration goes into the drawer;
   the live countdown, Hold, and trip failure escalate to a **global banner**
   that does not depend on any tile being expanded (§4).
5. **Nickname and asset class become mandatory** at the start of setup —
   a new constraint, neither was required by commissioning before (§2.2.1).

Not changed: one autoencoder per node, per-node thresholds, per-node gate
calibration, `inference.py`, the classifier, Telegram alerts.

---

## 1. What exists today

Four flows an operator must find, in four different places:

| Flow | Machine | Where in the UI | Code |
|---|---|---|---|
| Stopped baseline | **OFF** | Protection section, inside the *expanded* tile | [stopped_baseline.py](../base-station/python/pipeline/stopped_baseline.py) |
| Commissioning | **ON, healthy** | "Commission" button on the tile | [commissioning.py](../base-station/python/pipeline/commissioning.py) |
| Trip output mapping | either | Dropdown, Protection section, expanded tile | `set_trip_motor`, [registry.py:494](../base-station/python/registry/registry.py#L494) |
| Recording | ON, any condition | Record drawer | [capture.py](../base-station/python/pipeline/capture.py) |

Nothing tells the operator these are related, or what order they go in — even
though the order matters: without a stopped baseline the gate can barely tell
running from stopped, so commissioning collected before it is calibrated
against a weak gate.

### 1.1 Specific problems this plan fixes

- **Order is undiscoverable.** Baseline-then-commission is required; nothing
  says so.
- **Only one running condition is ever learned.** Commission at no load, run
  at full load, and the load change itself reads as a fault.
- **The motor dropdown is a fiction.** `TRIP_MOTOR_COUNT = 3` is hardcoded in
  [app.js:153](../base-station/python/frontend/app.js#L153), duplicating
  `MOTOR_IDS = (1, 2, 3)` in [motor_driver.py:36](../motor-driver/motor_driver.py#L36),
  with a comment conceding the copy. A factory with one motor sees three
  options, two of which are nonsense.
- **The mapping is never verified.** An operator picks "Motor 2" from memory.
  Whether that output actually stops *this* machine is not known until a real
  fault — the worst possible moment to find out it was wrong.
- **The trip can only reach one asset.** The base station publishes to
  `epm/<node_id>/cmd` ([mqtt_publisher.py:71](../base-station/python/ingestion/mqtt_publisher.py#L71)),
  but the rig subscribes to exactly one node's topic
  ([motor_driver.py:194](../motor-driver/motor_driver.py#L194), from a CLI arg). A
  second asset's trip is published into the void. Found while writing this
  plan; it is a real bug, not a design choice.
- **The countdown hides.** "Tripping in 8s…" and **Hold** live in the expanded
  tile's Protection section. An operator on the Classifier or Performance tab
  sees nothing during the one window where a human decision matters.

---

## 2. The setup flow

### 2.1 Principle

Sequence the existing sessions; do not merge the modules.

`StoppedBaselineSession` and `CommissioningSession` stay independent and
untouched. A new thin orchestrator, `SetupSession`, owns step order and step
state and drives the existing controllers.

This respects [stopped_baseline.py](../base-station/python/pipeline/stopped_baseline.py)'s
docstring, which argues the two must not be one flow because that would mean
"a stop/start in the middle of collecting a training batch." That objection is
about **interleaving**. This plan **orders** them — off first, then on, with
one machine state change at the boundary the operator performs anyway. Every
property that docstring protects stays true: a baseline can still be
recaptured on its own, without invalidating the model or forcing a retrain —
that is just the same step re-entered by itself.

### 2.2 The steps

| # | Step | Machine | Required | Produces |
|---|---|---|---|---|
| 1 | **Name & class** | — | **yes** | `device_name`, `device_type` |
| 2 | **Machine off** | **OFF** | **yes** | `stopped_spectrum_ref`, `stopped_energy_ref` |
| 3 | **Machine running** | **ON** | **yes** (≥1 condition) | training batch, `running_energy_ref`, `healthy` recordings (§2.3) |
| 4 | **Train** | either | **yes** | `<models_dir>/<node_id>.pt`, `scalar_mu/sigma`, `warning/fault_threshold` |
| 5 | **Stop output** | ON → stopped by us | no | `trip_motor_idx`, *tested* (§3) |
| 6 | **Done** | — | — | Summary; asset goes live |

**Step names, revised 2026-08-17.** Steps 2 and 3 were "Off" and "Running
conditions", which didn't read as two halves of one measurement; they are now
named as a pair after the state the machine must be in. Step 5 was "Trip
output" — "trip" is our word for it, not the word on the machine, and the step's
actual subject is *which output stops this machine*. The step **ids** are
unchanged (`stopped`, `conditions`, `trip_output`), so nothing in the API or
the registry moved.

**Trip output was step 2 until 2026-08-02, and moving it to 5 is a
correction** — see [TRIP_OUTPUT_OPEN_ISSUES.md §1](TRIP_OUTPUT_OPEN_ISSUES.md).
The original argument was that its test *ends* with the machine stopped, which
is exactly the state the Off step needs, so the operator switched the machine
off once rather than twice. That saving never existed. The test refuses to run
unless the gate reports RUNNING; the gate cannot answer until a model exists;
the model is not fitted until **Train**. At position 2 the test could only
409 — nothing published, machine never stopped, operator switched it off by
hand anyway. The early position bought an operator action it did not save, at
the price of an unrunnable test on every fresh asset.

At position 5 every precondition holds: model, stopped baseline, running
baseline, and a machine the operator has just been running for step 3.

Trip output is still the only skippable step — an asset with no trip output
wired must not be blocked. Extra *conditions* within step 3 are optional, but
the step itself is not.

### 2.2.1 Step 1: both fields are required

**Decided 2026-08-01.** Nickname and asset class are both mandatory, with no
skip.

This is a **new constraint**. Today a node auto-appears with
`device_name = node_id` ([registry.py:457](../base-station/python/registry/registry.py#L457))
and `device_type = None`, and **commissioning never required either** — only
the Record drawer and the classifier do.

**Nickname.** Nothing technically breaks without it, but the default is the raw
`node_id`, and that string is what every Telegram alert and now the trip banner
(§4.2) prints. *"Tripped — esp32-a4cf12 at 14:22"* is the wrong thing to read
during a trip. Pre-fill a suggestion so it is Enter-to-accept.

**Asset class.** The autoencoder, gate, thresholds and trip all work without
it; recordings and the classifier do not. Making it optional would mean a
conditional branch through the rest of setup — step 4 would have to silently
*not* save its recordings, producing a state nobody notices until they try to
train a classifier weeks later. One typed word is cheaper than that branch.

**It gets easier as the fleet grows**, which is the point:

- **Day one** — no classes exist, so it is free text. The operator types
  `pump`.
- **Later** — classes exist, so it is a pick-list with the most-used
  pre-selected. Usually zero typing.

That is the opposite of the motor dropdown (§3.1), which got *worse* with a
small factory because it assumed three motors on day one.

**Consequence for §5.3:** requiring the field here means the drawer must
contain a real editor for it. That settles the round-8 reversal — see §5.3.

### 2.3 Operating conditions

Step 4 collects **one or more named running conditions**, not one batch.

- Default, and the only one required: **Running**.
- The operator can add more before finishing: **No load**, **Full load**, or a
  free-typed name. Day one, with one simple machine, they add none and the
  step behaves exactly as commissioning does today.
- Each condition collects ≥50 gated-RUNNING frames, with its own live counter.
- The step is not complete until at least one condition has enough frames.
- **Collection stops between conditions** (added 2026-08-17): *Stop* closes the
  condition being recorded and collects nothing until the next one is named.
  Naming the next condition used to be the only way to end the current one, so
  the walk to the machine and the load change itself were recorded as part of
  one condition or the other. `CommissioningSession.stop_condition()` /
  `POST /nodes/<id>/setup/condition/stop`. The gate keeps being fed while
  paused, so resuming doesn't have to re-earn `debounce_frames` of agreement.
  A condition still can't be banked below `min_frames`, by *Stop* any more than
  by naming the next one — one rule, in `_close_condition()`.

**Why this matters more than it looks.** The autoencoder learns "what healthy
looks like" from whatever it was shown. Shown only no-load, a full-load run is
off-manifold and scores as a fault. Today the only fix is to re-commission
whenever the duty changes. Collecting the conditions up front is the actual
fix.

**Where each condition's frames go — three consumers, one collection:**

1. **Pooled into one training batch.** All running conditions together, one
   model. The healthy manifold now spans the machine's real duty range.
2. **`running_energy_ref`** — the median of the **quietest** condition, not of
   the pool. The gate's running threshold is `0.15 × ref`, and it must still
   call the quietest legitimate running state "running". A pooled median
   biased upward by a loud full-load condition would push that line above the
   machine's own no-load level.
3. **Saved as recordings**, one file per condition, all under the **same label
   `healthy`**, with a new `"condition": "full_load"` key in the capture
   payload.

> The shared label is deliberate. Capture labels are the classifier's class
> list — `healthy_no_load` and `healthy_full_load` as separate labels would
> hand Edge Impulse two classes that both mean "fine." The capture payload is
> already a plain JSON dict
> ([capture.py:255](../base-station/python/pipeline/capture.py#L255)), so
> adding `condition` is back-compatible and needs no format change.

**The honest cost.** Pooling several conditions widens the healthy
reconstruction-error spread, so `mu + 8σ` sits higher and sensitivity drops
somewhat. That is the right trade: the alternative is per-condition
thresholds, which would require knowing at runtime which condition the machine
is in — and nothing can detect that. A slightly higher line that never
false-alarms on a normal load change beats a tight line that cries wolf every
shift.

**Not in setup:** recording fault labels (bearing / loose / unbalanced). Those
need a broken machine, which is not a commissioning-time condition. They stay
in the Record form (§5.2).

---

## 3. Sensor → motor mapping

### 3.1 The rule

**Never ask the operator which motor. Prove it.**

Two changes, both needed:

### 3.2 The rig announces its own outputs

The base station must stop guessing how many outputs exist.

- The rig publishes a **retained** announce on connect: its output indices,
  and a name per output if it has one.
- The base station stores it and serves it at `GET /trip_outputs`.
- `TRIP_MOTOR_COUNT` is deleted from `app.js`. `MOTOR_IDS` in `motor_driver.py`
  becomes the single source of truth, which it effectively already is — the
  rig already rejects unknown indices with "TRIP IGNORED: motor N is not on
  this rig" ([motor_driver.py:229](../motor-driver/motor_driver.py#L229)); it simply
  never told anyone.
- Day one, one motor: exactly one candidate. Grows as the factory grows, with
  no dashboard change.

**Also fix the topic asymmetry (§1.1).** The rig must subscribe to `epm/+/cmd`
and route on the `motor_idx` in the payload, rather than one hardcoded node's
topic. `motor_idx` is what identifies the output; the node_id in the topic is
incidental. Without this, mapping a *second* asset is pointless because its
trip never arrives.

### 3.3 Confirm by stopping

Step 5 of setup, per candidate output:

1. Operator starts the machine by hand and confirms it is running. The gate
   confirms RUNNING too.
2. Dashboard: *"We'll send a stop to output 2. Watch the machine."*
3. Send the stop. Watch **this node's** gate.
4. Gate goes quiet within the confirm window → **mapping confirmed**, recorded
   with a timestamp.
5. Gate keeps reading running → wrong output (or the trip path is broken).
   Offer the next candidate.

What this buys, beyond deleting a dropdown:

- The mapping is **verified against physics**, not operator memory.
- It is a **live end-to-end test of the trip path** — MQTT, topic, rig
  subscription, motor stop, gate confirmation — at commissioning time instead
  of during the first real fault.
- It ends with the machine stopped, which is what step 3 needs anyway.
- It is a genuinely good demo: the system doesn't ask which motor, it finds
  out.

### 3.4 The safety invariant is preserved

[protection.py](../base-station/python/protection/protection.py)'s docstring is
explicit and correct: *"nothing in this module can set a speed, and nothing can
start a machine — restarting is a human action taken at the machine."*

This design does not touch that:

- The only command sent is **stop**. Same command, same code path, same
  payload as a real trip.
- The **operator** starts the machine, by hand, at the machine, both before the
  test and after it. Software never starts anything.
- The confirm test therefore exercises `protection/`'s existing publish path
  rather than adding a second, weaker one. Anything that would let the
  dashboard start a motor to "identify" it is rejected outright.

### 3.5 Manual fallback

Keep a plain "I know which output this is" picker, populated from the
announced list, for a rig where the confirm test is impractical (machine can't
be cycled right now). It records the mapping as **unconfirmed**, and the tile
and drawer both say so. Unconfirmed is honest; a confirmed-looking guess is
not.

---

## 4. Where the protection UI goes

The ask: most of the tile's controls move to the drawer — so what happens to
the trip countdown and Hold?

### 4.1 The rule: cold config in the drawer, hot state out front

| | Cold — done once, deliberately | Hot — happens to you, on a clock |
|---|---|---|
| What | Trip output mapping + confirm, stopped baseline, conditions, training | "Tripping in 8s…", **Hold**, "Trip failed", "Tripped at 14:22" |
| Where | Drawer (setup) | **Global banner**, always visible |

A trip countdown is an alarm, not a setting. Ten seconds is not enough time to
remember which asset, find its tile, expand it, and scroll to Protection.
Putting Hold behind "open the drawer" would make the current situation worse,
not better.

### 4.2 Global trip banner (new)

- Pinned at the top of the dashboard, **above the tab nav**, so it is present
  on Fleet, Classifier, Network and Performance alike.
- One line per affected asset: name, seconds remaining, **Hold**.
- The existing 500 ms `tickTripCountdowns()` keeps the seconds honest between
  5 s polls — retargeted at the banner instead of the tile. No new mechanism.
- After the trip fires, the line becomes an acknowledgement: *"Tripped —
  Pump 1 at 14:22, confirmed stopped"*, dismissible.
- **"Trip failed — machine still running"** stays as a persistent red banner
  until acknowledged. It is the most severe state the system can report, and
  today it is buried in a collapsed panel.
- This is the local counterpart to the Telegram alert that already fires; the
  two say the same thing to different audiences.

### 4.3 The tile

- Collapsed row gains a **small armed indicator** (shield glyph) — no text, no
  second copy of the status string.
- The expanded panel's Protection section becomes **read-only**:
  *"Trip output: Motor 2 · confirmed 01 Aug · Armed"*, plus
  *"Stopped baseline: measured"*. A `Change in setup` link opens the drawer at
  the relevant step.
- Its start / stop / cancel / dropdown controls are removed — they live in
  setup now.
- No countdown, no Hold. Those are in the banner, and only in the banner.

This keeps the standing rule: **no state shown twice.**

### 4.4 The tile's setup affordance

- **Uncommissioned:** one button — `Set up`. Nothing else.
- **Mid-setup:** the same button reads `Setup — step 4 of 6`, tinted. No frame
  counts, no progress bar, no "Training…". It is a door, not a dashboard.
- **Commissioned:** the button is gone. `Re-run setup` lives inside the drawer.

---

## 5. The drawer

The existing Record drawer becomes the **asset drawer** — one slide-over per
asset, two modes.

### 5.1 Setup mode

- Vertical step list. Current step expanded; completed steps collapse to a
  check plus a one-line result ("Off — measured, 34 frames").
- All instructions live here, because this is the only surface the operator is
  actually reading while standing at the machine. **Two parts per step: what to
  do, then why it matters** (the second quieter, `setup-step__why`) — rewritten
  2026-08-17 for a technician at the machine rather than for whoever built
  this:
  - Step 2 (Machine off): *"Switch the machine off. Wait until it has fully
    stopped moving, then press Start."* + *"This measures the machine at rest,
    so the system can tell 'stopped' from 'running'."*
  - Step 5 (Stop output): *"Which output stops this machine? Leave the machine
    running and press Test."* + *"Test sends a stop command. If the machine
    stops, the test passes."* — it says *leave*, not *start*, because step 3
    has just had the operator running the machine.
  - Step 4 (Train) names what is running, shows a percentage that moves, and
    says *"Training complete"* in words. A bar sitting at 100% with no words
    read as the freeze it isn't. Its why line: *"Training keeps running, and
    the machine keeps working."*
  - **Revised again 2026-08-17 (same day): trimmed further.** The
    software-cannot-verify-off point and the never-starts-a-machine point are
    still true (§3's safety invariant, this step's own docstring) — they're
    just no longer spelled out in the drawer copy. Read them in this doc or in
    the code instead.
- The Machine off step's wording carries the whole "software cannot verify the
  machine is off" problem, exactly as the module docstring demands — as the
  operator's own check, not as a lecture about what the model would learn.
- Errors surface inline on their step — *"too unsteady, something was still
  moving"* — with the step still open for a retry. Both sessions already
  support retry-in-place; nothing new is needed.
- The drawer is a top-level element outside `#fleet-list`, so the 5 s poll can
  never wipe an in-progress edit. That existing property is exactly what a
  multi-step wizard needs.

### 5.2 Record mode

Today's label + frame-count + Start form, unchanged — for fault recordings
after the asset is live. Plus a quiet `Re-run setup` link.

### 5.3 One reversal — decided

Round 8 (2026-07-24) removed the drawer's inline asset-class editor in favour
of a blocking "Go to Fleet" message, because it was a *second live editor* for
a field owned by the Fleet row pill.

**Decided 2026-08-01: step 1 puts the editor back.** Once both fields are
mandatory (§2.2.1) there is no alternative — a required field the drawer
cannot set would mean bouncing the operator out to the Fleet row in the middle
of a wizard, which is strictly worse than the problem round 8 was solving.

The round-8 objection was about **two editors competing side by side**. That
does not apply here:

- **During setup**, step 1 is the editor. It is the only surface in play.
- **After setup**, the Fleet row pill remains the editor, as today.
- The blocking "Go to Fleet" state is **deleted** — it only ever existed
  because the drawer had no way to set the field.

One field, one editor at a time. The rule survives.

---

## 6. Data model

No new stores. No new model files. Additions only.

**`RegistryEntry`:**

```python
trip_motor_confirmed_at: Optional[float] = None   # None = mapped but unproven (§3.5)
operating_conditions: Optional[List[str]] = None  # names collected at setup, for display
```

`trip_motor_idx` keeps its current meaning. `model_path`, `scalar_mu/sigma`,
thresholds, `running_energy_ref`, `stopped_*_ref` are all unchanged, still per
node, still written by the same code.

**Capture payload** gains `"condition": str | None` (§2.3). Back-compatible —
absent on every existing file, and `list_captures()` already tolerates missing
keys.

**Rig outputs** are held in memory from the retained MQTT announce, not
persisted. A restart re-reads them from the broker.

**Setup state** is in-memory only, in `SetupSession`. A dashboard restart
mid-setup restarts the current step. Do **not** persist half-collected
batches — a resumed batch spanning a restart is worse data than a fresh one.

---

## 7. API and wire

Additions only; nothing existing is removed.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/nodes/{id}/setup` | Current step, per-step state, counters, last error |
| `POST` | `/nodes/{id}/setup/start` | Enter setup (also `Re-run setup`) |
| `POST` | `/nodes/{id}/setup/advance` | Complete the current step |
| `POST` | `/nodes/{id}/setup/skip` | Skip an optional step (2, 4-extra) |
| `POST` | `/nodes/{id}/setup/cancel` | Abort; cancels whichever sub-session is live |
| `POST` | `/nodes/{id}/setup/condition` | Start/stop one named running condition |
| `GET` | `/trip_outputs` | Announced rig outputs + who claims each |
| `POST` | `/nodes/{id}/trip_motor/confirm` | Run the stop-and-watch test (§3.3) |

`setup_progress` rides `GET /nodes`, mirroring how `commissioning_progress`
and `capture_progress` already do, so the tile's one button needs no second
fetch.

WS: reuse `training_progress`; add `setup` (step changed) and `trip_confirm`
(test result). The banner reads the existing `registry` broadcast — protection
state already rides it.

MQTT: one new retained announce topic from the rig, and a wildcard `epm/+/cmd`
subscription on the rig side (§3.2). No change to `MqttMsgType.MOTOR_STOP` or
its payload — a confirm test sends the identical bytes a real trip does, which
is the point.

---

## 8. Build order

**R1 — Trip banner + tile read-only Protection.**
Smallest, highest safety value, independent of everything else. Countdown and
Hold stop hiding in a collapsed panel. Ship first even if nothing else lands.

**R2 — `SetupSession` + drawer wizard.**
Steps 1, 3, 5, 6 only — i.e. today's behaviour, sequenced and guided. Tile
drops its narration. Pure orchestration, zero ML risk.

**R3 — Operating conditions (step 4).**
Multi-condition collection, pooled batch, quietest-condition
`running_energy_ref`, `condition` in the capture payload.

**R4 — Trip output mapping (step 5; was step 2 when this was written).**
Rig announce + `epm/+/cmd` fix + `GET /trip_outputs` + confirm-by-stopping +
delete `TRIP_MOTOR_COUNT`. Last because it touches the rig script on the other
machine and needs live hardware to verify.

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| Confirm test misread — machine stopped by hand at the same moment | Require gate-confirmed RUNNING immediately before the stop is sent; a machine already stopped fails the precondition rather than reporting a false confirm |
| Confirm test stops the wrong machine | That is the finding, not a failure — it is exactly what an unverified dropdown does silently today, only now it happens with the operator watching, once, on purpose |
| Pooled conditions dull sensitivity | Accepted and stated (§2.3). Watch it on the real rig: compare `mu + 8σ` before and after adding a second condition |
| Rig announce absent (older `motor_driver.py`) | Fall back to the manual picker (§3.5) with an unconfirmed mapping — degrades to roughly today's behaviour, never blocks setup |
| Setup state lost on restart | In-memory by design (§6); restart the step |
| Regression in a commissioning path that currently works | R1/R2 are separable and separately verifiable on hardware; deadline is 23 Aug 2026, so land R1+R2 early and treat R3/R4 as stretch |
| Tile loses its progress readout, operators feel blind | The step-number button is the compromise. Revisit only if it actually reads worse on real hardware |

---

## 10. Open questions

1. ~~**§5.3** — putting the asset-class editor back in the drawer reverses the
   round-8 decision.~~ **Resolved 2026-08-01: yes.** Follows from step 1's
   fields being mandatory (§2.2.1).
2. `Re-run setup` on a live asset: force all steps, or allow jumping straight
   to one? *Leaning: allow jumping — recapturing a baseline must not force a
   retrain, which is an existing invariant.*
3. Does adding a new operating condition to a live asset trigger an immediate
   retrain, or mark the model stale and let the operator choose? *Leaning:
   retrain immediately — the batch is right there, and a stale-model state
   nobody clears is worse.*
4. Should the trip banner also surface FAULT with no trip output wired (an
   asset that can only be stopped by hand)? *Leaning: yes, but as a quieter
   variant with no Hold — there is nothing to hold.*
5. Rig announce topic and payload shape — settle against
   [mqtt_publisher.py](../base-station/python/ingestion/mqtt_publisher.py)'s
   existing `epm/{node_id}/cmd` convention before implementing.
