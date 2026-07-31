# Progress 5 — gate energy calibration: root-caused and FIXED (2026-07-30/31)

Closes the one item [progress4.md](progress4.md) left open (its §4, "idle/running
margin is too thin"). Everything in progress4 §1-§3 and §5 still stands; this
file only replaces its §4 and §7.2-7.3.

**Status: fixed, deployed, and verified live end to end on the real rig.**
NOT committed — the working tree carries the change.

---

## 1. progress4's leading hypothesis was wrong

progress4 §4 guessed a DC/gravity term in bin 0 was dominating the RMS, and
said to check `features.py`/the FFT pipeline before touching any threshold.
Checked, and it is not that:

- `features.py` computes no FFT at all. It normalizes bins the MCU already
  produced.
- The MCU **already discards bin 0**. `accel_sampler.cpp`'s
  `accel_fft_magnitude()` starts its magnitude loop at `k = 1` and says so:
  *"bin 0 (DC) discarded"*. Gravity never reaches these bins.

Real captures confirm it from the other side: `captures_small_rig`'s raw
accel_y windows sit at a mean of ~4228 counts (that's the gravity offset)
with a std of ~415, and none of that offset appears in the bins.

## 2. The actual root cause: the sensor's own noise floor, not the machine's

`compute_energy()` was an RMS over **all** bins of every accel channel — 384
of them for a 3-axis, 128-bin node. Measured live, per pooled bin:

| bin | Hz | stopped | running (90rpm) | delta |
|---|---|---|---|---|
| 2 | 131 | 13192 | 36134 | **+22942** |
| 5 | 281 | 12680 | 44798 | **+32118** |
| 7 | 381 | 13586 | 40638 | **+27052** |
| 12 | 631 | 13453 | 13545 | +92 |
| 24 | 1231 | 11217 | 11482 | +265 |
| 64 | 3231 | 5525 | 5483 | −42 |

The motor's whole signature is a handful of narrow lines below ~600Hz (the
stepper's step rate: 90rpm x 200 full steps = 300Hz, which lands right on
bins 5-7). **Every other bin is the KX134's broadband noise at
ACCEL_ODR_HZ=12800, and it reads the same whether the machine runs or not.**

So the old metric was ~360 bins of sensor noise plus ~24 bins of signal, and
an RMS over that is mostly a measurement of the accelerometer. Hence
stopped ~7500 vs running ~11400: a 1.18x worst-case margin, unusable — and
the same shape as the two earlier layers of this bug (the absolute 0.05
threshold, then the mic inclusion), just one level further down.

This is also why the four fault classes in `captures_small_rig` have nearly
identical spectra above bin ~24. Worth remembering next time classifier
accuracy is the question.

## 3. The fix: subtract a measured stopped baseline

A node can now capture what its sensor reads with its machine deliberately
**off**, and the gate measures only each bin's excess over that.

- `pipeline/stopped_baseline.py` (new) — collects >=30 ungated frames, fits a
  per-bin median floor, and measures what those same frames still produce
  once it's subtracted.
- `RegistryEntry.stopped_spectrum_ref` / `stopped_energy_ref` — persisted
  together (`Registry.set_stopped_baseline`, which rejects a half-set pair).
- `pipeline/gate.py` — `compute_energy(frame, stopped)` subtracts;
  `MotorStateGate` takes a `stopped_provider` and thresholds at
  `stopped_energy_ref * DEFAULT_STOPPED_MARGIN` (1.75).
- REST `POST /nodes/{id}/stopped_baseline/{start,stop,cancel,clear}`,
  live progress over `/ws`, and a row in the dashboard's Protection section.

Measured effect on the same rig, same session:

| | stopped | running | worst-case gap |
|---|---|---|---|
| RMS all bins (old) | 7480 | 11137 | 1.18x |
| excess over baseline | 1414 | 6194 | **2.09x** |

### Why it does NOT touch running_energy_ref or force a retrain

They're independent on purpose. `running_energy_ref` stays on the raw,
unsubtracted scale and is still what the gate falls back to for any node
with no baseline — so capturing one can't invalidate an existing model, and
a node that never captures one behaves exactly as it does today. The gate
picks energy and threshold **together** in `_measure()` so a subtracted
energy can never be compared against an unsubtracted threshold; a baseline
that doesn't fit the frame's channels/bin counts is dropped whole rather
than applied in part, for the same reason.

### Why it isn't part of commissioning

Commissioning collects with the machine running and gates on
`MotorState.RUNNING`; this needs it stopped and gates on nothing (gating a
stopped capture on the gate it's calibrating is circular). Folding them
together would also mean a stop/start in the middle of a training batch.

## 4. Live verification on the real rig

All of this was run against the real hardware, not a simulator:

1. Captured a baseline with the rig confirmed off — **65 frames, 3 accel
   channels, energy_ref 1533.1, spread 1.39x, gate threshold 2682.9**
   (matching the ~1475-1517 predicted offline from `/ws` captures).
2. Node went from **flapping FAULT/WARNING at rest to settling on IDLE**,
   with the anomaly score frozen — the "suppress inference while stopped"
   behaviour (S3.2) that progress4 correctly noted *had never actually
   engaged live*.
3. Spun the rig back up: gate left IDLE immediately, scores went live again.
   Both directions work.
4. Re-commissioned (progress4 §7.3, now unblocked because commissioning's
   own gate finally works): **HEALTHY at 90rpm, score 0.046 against a
   warning threshold of 0.144**. New thresholds 0.144/0.288,
   running_energy_ref 10538.4.
5. Ramped down: back to **IDLE**, not FAULT.
6. `trip_failed` cleared itself once the stop was correctly detected.
7. Dashboard checked in a real headless browser against the live device:
   both UI states render ("Measured" + Re-measure; "Measuring 28/30" with
   Save disabled until 30 and Cancel working), zero console errors.

Before this, step 2 was the user's original bug report and step 4 could not
have produced a trustworthy model — the batch could contain stopped frames.

## 5. Tests

`base-station/tests/stopped_baseline_test.py` (new, 9 checks) and 9 added to
`gate_test.py`. Suite is **26 pass / 7 fail**, up from 25/7; the 7 are the
usual pre-existing ones (6 need on-device `arduino.app_utils`, plus
`satellite_node_sim_test`).

The gate fixtures are **real measured accel_x spectra** — a real stopped
frame, the real fitted floor, and the *quietest* of 45 real running frames.
That is deliberate and worth preserving: synthetic single-digit bins are
what hid the original absolute-threshold bug through a whole release, and
nobody hand-writing a "stopped" spectrum would have given it a noise floor
65% as tall as the running one, which is the entire difficulty here.
`test_real_spectra_defeat_the_unsubtracted_gate` fails on the pre-fix code.

## 6. Left to do

1. **Commit.** Nothing here is committed.
2. Other nodes need their own baseline captured before their gate improves;
   without one they keep today's behaviour, no regression either way.
3. Cross-talk is still a physical property of the single shared sensor —
   with motor 1 tripped and 2/3 running, the sensor still reads RUNNING.
   Unchanged by this work, and progress4 §5's call to accept it for the
   demo topology still stands.
4. `--gate-stopped-margin` is the tuning knob now, not
   `--gate-running-fraction` (which only affects baseline-less nodes).

## Do not re-litigate

- **DC/gravity in bin 0** — checked, firmware already discards it (§1).
- **Excluding mic** — already done in `055523e`, and measured insufficient
  on its own.
- Band-limiting the energy to the motor's own frequency range instead of
  subtracting a floor: measured, and it separates worse (1.09x at bins
  0..7 vs 2.09x full-band subtracted), because the noise floor is *tallest*
  in exactly the low bins where the signal lives.
- Reference-free "peakiness"/spectral-flatness metrics: all measured, all
  overlapped in the worst case.
