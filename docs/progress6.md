# Progress 6 — unified commissioning build (2026-08-01)

Implements [UNIFIED_COMMISSIONING_PLAN.md](UNIFIED_COMMISSIONING_PLAN.md).
All four rounds (R1–R4) are written, and **§5's hardware pass has now run on
the real UNO Q + motor rig** — results, two frontend bugs it found, and the
S9 pooled-conditions measurement are in §6.

---

## 1. What landed

### R1 — Global trip banner + read-only tile Protection
- New `#trip-banner` above the tab nav (`index.html`), so a countdown, **Hold**,
  or a failed trip is visible on every tab. Four line kinds: countdown (pulsing
  red + Hold), trip-failed (persistent, acknowledge-only), tripped
  (dismissible), and faulty-but-unarmed (quieter, no Hold — S10 Q4's leaning).
- `tickTripCountdowns()` retargeted at the banner; it only rewrites the seconds,
  and rebuilds only when a line changes *kind*, so Hold stays clickable.
- The expanded tile's Protection section is now read-only: trip output +
  confirmed/unconfirmed + armed, stopped-baseline state, and a `Change in setup`
  link. Its dropdown / start / stop / cancel controls are gone.
- Collapsed row gained a small shield when protection is armed.

### R2 — SetupController + drawer wizard
- New [`api/setup_controller.py`](../base-station/python/api/setup_controller.py):
  a thin orchestrator over the *existing* controllers. Six steps
  (`name → trip_output → stopped → conditions → train → done`); only
  `trip_output` is skippable. Per-step state is **derived** from the registry on
  every call, never cached, so a single step can be re-entered on its own.
- Step 1 makes nickname + asset class mandatory (S2.2.1). The round-8 "Go to
  Fleet" blocking state is **deleted** — the drawer sets the field itself now.
- New REST routes (§7 of the plan) + `setup_progress` on `GET /nodes` +
  `setup`/`trip_confirm` WS broadcasts. `_start_training()` is shared by
  `POST /commission/stop` and setup's step 4→5 advance — one training path.
- New `Registry.cancel_commissioning()` (COLLECTING → UNCOMMISSIONED if never
  trained, else HEALTHY and let inference re-diagnose).
- New [`frontend/setup.js`](../base-station/python/frontend/setup.js): the
  vertical step list. The drawer now has two modes (`setup` / `record`);
  `Re-run setup` lives in record mode.
- Tile narration removed: one button (`Set up` / `Setup — step N of 6` / none
  once commissioned), and the status pill no longer carries frame counts or a
  training percentage.

### R3 — Operating conditions
- `CommissioningSession` collects one or more **named conditions**
  (`start_condition`, `condition_counts`); a caller that never names one gets
  exactly the old behaviour under a condition called `running`.
- Training pools every condition into one model, but `running_energy_ref` is
  the **quietest condition's** median, not the pool's.
- Each condition is also saved as a `healthy` recording carrying a new
  `"condition"` key, via a new shared `capture.save_vectors()` primitive.
- `RegistryEntry.operating_conditions` records what the live model covers.

### R4 — Trip output mapping
- The rig announces itself: `run_demo.py` publishes a **retained** JSON
  `epm/<host>/outputs` on connect, and now subscribes to **`epm/+/cmd`**,
  routing on the payload's `motor_idx`. That fixes the real bug found while
  writing the plan (a second asset's trip was published into the void).
- New [`protection/trip_outputs.py`](../base-station/python/protection/trip_outputs.py)
  (in-memory, re-read from the broker on connect) + `GET /trip_outputs`.
  `TRIP_MOTOR_COUNT` is deleted from `app.js`.
- `ProtectionController.confirm_trip_output()` — confirm by stopping. Refuses to
  run unless the gate says RUNNING *right now* (a stopped machine would appear
  to confirm any output). Sends the identical bytes a real trip sends. Resolves
  from the gate edge or an 8 s timeout, and never reports TRIPPED — a machine
  stopped by a confirm test is stopped, not tripped.
- `trip_motor_confirmed_at` on the entry; re-pointing an output clears it.
- Manual fallback ("use without testing") records the mapping as *unconfirmed*,
  and both the drawer and the tile say so.

---

## 2. Files

Changed: `registry/registry.py`, `pipeline/commissioning.py`,
`pipeline/capture.py`, `api/commissioning_controller.py`, `api/app.py`,
`protection/protection.py`, `ingestion/mqtt_subscriber.py`, `main.py`,
`frontend/{app.js,index.html,style.css}`, `motor-driver/run_demo.py`.

New: `api/setup_controller.py`, `protection/trip_outputs.py`,
`frontend/setup.js`, `tests/setup_test.py`.

---

## 3. What is verified

**Python suite: 27 pass, 1 pre-existing failure.**
`tests/satellite_node_sim_test.py` fails identically on a stashed (pre-change)
tree — not caused by this work, and not fixed here.

New `tests/setup_test.py` covers the whole flow: mandatory name/class, the
confirm test refusing an already-stopped machine, confirm success and failure,
each step's precondition, two conditions with per-condition counters, pooled
training with the quietest-condition energy ref, one `healthy` recording per
condition, cancel leaving calibration alone, re-entry opening on the first gap,
skip, and the announce store.

**Real browser (headless chromium against the real dashboard + sim node),
zero JS errors.** Walked `Set up → name → skip trip → baseline (live counter
1/30 → 47/30) → two conditions (78/50, 66/50) → train → Done`, then confirmed
the tile drops its setup button, the expanded Protection panel is read-only,
and `Record → Re-run setup` reopens the wizard on the first gap.

Two real bugs were found and fixed this way:
1. The live frame counters rendered from a stale setup snapshot — an operator
   stood watching a frozen `0/30` while the backend had 429 frames. The stopped
   step now reads the per-frame `stopped_baseline_progress` off the node entry,
   and `pollNodes()` refreshes the setup snapshot while its drawer is open.
2. The drawer header ellipsised (`Set up — Pump 1 · step 4 o…`); the step number
   was dropped from it (the numbered list already shows position).

---

## 4. What was NOT verified before the hardware pass

Everything in this list is now covered by §6 unless it says otherwise there.

- **Anything on real hardware.** Not deployed to the UNO Q at all.
- **The trip banner's countdown/Hold path.** Locally the sim node never reached
  FAULT (synthetic `fault.npz` scored 0.066 against a 0.153 warning threshold,
  because the model had just been trained on the same synthetic family). The
  armed shield renders; the countdown line, Hold, and the trip-failed line are
  code-reviewed only.
- **Confirm-by-stopping against the real rig** (unit-tested with a stub
  publisher, never against `run_demo.py`).
- **The rig announce end to end** (parser and store are unit-tested; the actual
  retained publish from `run_demo.py` has not been observed).
- **The stopped-baseline step with a genuinely stopped machine.** Locally it was
  measured on running sim data, which — correctly — then made the gate read
  every running frame as stopped. That is the module's documented failure mode,
  not a regression, but it means only the *wiring* of step 3 is proven.

---

## 5. The hardware pass — how it was run

Node under test: `base_station` (the UNO Q's own accel + mic, mounted on the
rig), renamed `Rig Motor 1`, class `motor rig`. Only **motor 1** was run for
most of it: it is the one the accelerometer is coupled to, which is what makes
the confirm-by-stopping test meaningful.

Two deployment notes for next time:

- `start_dashboard.sh` **could not push**. The usbipd/`vhci_hcd` link on this
  WSL2 box cycles every ~30–40 s, and a 14 MB tarball never fits in one window
  (8/8 retries, `adb: error: connect failed: closed`). What worked: push the 14
  changed files individually (each lands first try), then
  `arduino-app-cli app stop` + a detached `app start` on the device. That is
  [[deploy-via-targeted-file-push]] plus a real `app start`, which is needed
  because a bare `docker start` leaves the MCU side dead — the container comes
  up, HTTP answers, and `last_seen` never advances.
- The board is in **WiFi AP mode** (`Hotspot`, 10.42.0.1), not on the LAN, so
  everything here ran over `adb forward` (`8080` for the dashboard, `11883` →
  the device's own `1883` for the broker). `nmcli con up FTTH-F05C` can't fix
  it from an adb shell: `wifi-bridge.service`'s monitor loop puts the Hotspot
  straight back, and stopping that service needs a root password. Rejoining
  the LAN has to go through the dashboard's own WiFi onboarding, which wants
  the passphrase.
- Consequence worth knowing: with no LAN there is no NTP, and the device clock
  is **~33 h behind**. A browser on a correctly-clocked machine therefore reads
  every node as `OFFLINE` (`now − last_seen` client-side). Nothing to do with
  this work, but it is why the screenshots say Offline.

---

## 6. What the hardware pass proved

**R4 — rig announce.** `epm/motor_rig/outputs` published retained on connect;
`GET /trip_outputs` returned all three with `claimed_by` filled in. The drawer
renders the announced names, not a hardcoded count.

**R4 — confirm by stopping**, all three paths, on the real rig:

| test | result |
| --- | --- |
| wrong output (2) while motor 1 runs | `"the machine kept running, so output 2 isn't the one that stops it"`, mapping unchanged |
| right output (1) | `"output 1 stopped this machine"`, `trip_motor_confirmed_at` recorded, protection armed |
| any output, machine already stopped | HTTP 409, `"start the machine and wait for it to read as running…"` |

Run twice: once over REST, once by clicking **Test** in the drawer.

**Setup steps 1–6** walked end to end. Step 1 rejects a missing class (409) and
lowercases it. Step 3 measured a **genuinely stopped** machine —
`energy_ref = 1494.8` from 306 frames, against the 1446 a previous session
measured, so the step is doing what it claims. Step 4 collected two named
conditions (484 + 491 frames), step 5 trained, step 6 summarised. Re-entering
setup jumped straight to the first real gap both times it was asked to.

**R3 — per-condition recordings.** Two `healthy` captures, one per condition,
each carrying its `"condition"` key and its own frame count.

**R1 — trip banner**, all four line kinds, live:

- countdown — `Rig Motor 1 — tripping in 10s` with **Hold**, ticking 10→2;
- **Hold** clicked at 9.2 s left: no trip fired, motor kept running;
- tripped — `Tripped — Rig Motor 1 at 7:45:33 AM, confirmed stopped`, with ×;
- trip failed — staged by pointing the output at motor 2 (uncoupled) and
  faulting motor 1: `Rig Motor 1 — trip failed, machine still running`,
  persistent, **no buttons at all**;
- unarmed — `Rig Motor 1 — faulty, no trip output wired`, dismissible, no Hold.

Visible on all five tabs in every case. **Zero JS errors** throughout.

### 6.1 Two real bugs, found only here

1. **`charts.js` silently dropped `setup` and `trip_confirm`.** Its WS
   `onmessage` forwards `registry`/`removed`/`training_progress`/`capture` to
   app.js and ignores everything else — so the handler app.js *does* have for
   both never ran. Symptom: the drawer stuck on `Stopping output 1 — watch the
   machine…` forever, because the result that clears it only ever arrives as a
   broadcast. Setup's own 5 s refresh had been papering over the step
   broadcasts well enough to hide this until a real rig ran the test. Fixed and
   re-verified: the drawer now shows `output 1 stopped this machine`.
2. **The output name was squeezed to nothing.** `Test` + `Use without testing`
   filled the drawer row, and `.setup-output__name` (`flex: 1 1 auto`) shrank to
   a two-line `Mo`/`1`. The name now takes its own line above the buttons.

### 6.2 S9 — what a second condition actually costs

Same rig, same node, same ~450–490 frames per condition, trained twice:

| conditions | warning (`µ+8σ`) | fault (`2×`) |
| --- | --- | --- |
| `slow_90rpm` alone | **0.146** | **0.292** |
| `slow_90rpm` + `fast_150rpm` | **0.745** | **1.490** |

**Pooling the second condition widened the healthy band 5.1×** — and that is
not academic. Under the two-condition model, driving motor 1 to 220 RPM (2.4×
the slow condition) peaked at 0.235–1.413 and **never crossed 1.490**: the
overspeed would not have tripped. Under the one-condition model the same
overspeed scored 1.851, ~6× its fault threshold, and tripped in ~11 s.

So S9's admitted cost is real and large on this rig. `running_energy_ref` did
behave as designed (12 619 from the quietest condition, against 13 073 for that
condition measured alone), so the quietest-condition rule is doing its job —
the widening is in the reconstruction-error spread, not the energy reference.

Worth deciding before this ships wider: per-condition thresholds, or a
condition-aware model, rather than one pooled band. Neither is implemented.

---

## 7. Still open

- **S10 Q3** (deliberately unresolved): does adding a condition to a live asset
  retrain immediately, or mark the model stale? Neither is implemented — a new
  condition today means re-running setup's step 4.
- **The 5.1× threshold widening above.** Measured, not addressed.
- **The board is still on its own hotspot** with a ~33 h clock skew. Rejoining
  the LAN needs the WiFi passphrase through the onboarding page.
- The python suite is unchanged by the two frontend fixes: **27 pass, 1
  pre-existing failure** (`satellite_node_sim_test.py`, fails the same way on a
  stashed tree), plus 5 device-only tests that need the App Lab container's
  `arduino.app_utils`.
