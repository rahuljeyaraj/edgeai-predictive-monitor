#!/usr/bin/env python3
"""
Milestone 4 verification: feed
synthetic frames with varying energy across the stopped/running boundary
and confirm gate output matches expected state transitions, with no
flapping on single noisy frames.

Run with PYTHONPATH covering base-station/python/ingestion and base-station/python/pipeline:
    PYTHONPATH=base-station/python/ingestion:base-station/python/pipeline python3 base-station/tests/gate_test.py
"""
import sys

from sensor_frame import FrameSource, SensorFrame
from gate import MotorState, MotorStateGate, compute_energy

THRESHOLD = 1.0
DEBOUNCE = 3


def frame(accel_bins, mic_bins=None) -> SensorFrame:
    bins = {"accel": accel_bins}
    if mic_bins is not None:
        bins["mic"] = mic_bins
    return SensorFrame(node_id="node-1", source=FrameSource.SPI, timestamp=0.0, bins=bins)


LOW = frame((0.01, 0.02, 0.01))     # well under threshold -- "stopped"
HIGH = frame((3.0, 4.0, 5.0))       # well over threshold -- "running"


def main():
    gate = MotorStateGate(threshold=THRESHOLD, debounce_frames=DEBOUNCE)
    assert gate.state == MotorState.STOPPED, gate.state
    print("initial state defaults to STOPPED: PASS")

    # Fewer than debounce_frames high-energy frames must not flip state yet.
    for _ in range(DEBOUNCE - 1):
        state = gate.update(HIGH)
    assert state == MotorState.STOPPED, state
    print("state does not flip before debounce_frames consecutive frames: PASS")

    # The Nth consecutive high-energy frame flips it.
    state = gate.update(HIGH)
    assert state == MotorState.RUNNING, state
    print("state flips to RUNNING after debounce_frames consecutive high-energy frames: PASS")

    # A single low-energy frame amid running frames must not flap the state.
    state = gate.update(LOW)
    assert state == MotorState.RUNNING, state
    state = gate.update(HIGH)
    assert state == MotorState.RUNNING, state
    print("single noisy frame does not flap confirmed RUNNING state: PASS")

    # debounce_frames consecutive low-energy frames flip it back.
    for _ in range(DEBOUNCE - 1):
        state = gate.update(LOW)
    assert state == MotorState.RUNNING, state
    state = gate.update(LOW)
    assert state == MotorState.STOPPED, state
    print("state flips back to STOPPED after debounce_frames consecutive low-energy frames: PASS")

    # A candidate flip must restart, not accumulate, if the raw signal
    # alternates rather than staying consistently on the new side.
    gate2 = MotorStateGate(threshold=THRESHOLD, debounce_frames=DEBOUNCE)
    for _ in range(10):
        gate2.update(HIGH)
        gate2.update(LOW)
    assert gate2.state == MotorState.STOPPED, gate2.state
    print("alternating energy never accumulates into a flip: PASS")

    # Bins-absent frame (both sensors disabled) reads as zero energy, no crash.
    empty = SensorFrame(node_id="node-1", source=FrameSource.SPI, timestamp=0.0, bins={})
    assert compute_energy(empty) == 0.0
    print("frame with no bins present computes zero energy without error: PASS")

    print("RESULT: PASS - gate transitions correctly, no flapping")


# ---------------------------------------------------------------------
# Per-node relative threshold (docs/MOTOR_STOP_PLAN.md)
#
# The absolute threshold above is a fallback only. Real accel bins measure in
# the thousands-to-hundreds-of-thousands, so a fixed number can't mean
# "stopped" for every node -- see gate.py's module docstring for the bug this
# replaced, where the shipped default of 0.05 made STOPPED unreachable on real
# hardware by ~5 orders of magnitude.
# ---------------------------------------------------------------------

# Deliberately at a realistic accel scale, not the single-digit synthetic
# values above -- that mismatch is exactly what hid the original bug.
RUNNING_ENERGY = 19000.0
REAL_RUNNING = frame(tuple([RUNNING_ENERGY] * 4))
REAL_STOPPED = frame(tuple([RUNNING_ENERGY * 0.01] * 4))       # 1% of running
REAL_CROSSTALK = frame(tuple([RUNNING_ENERGY * 0.30] * 4))     # 30% of running


def relative_gate(ref, fraction=0.15, debounce_frames=1):
    return MotorStateGate(threshold=0.05, debounce_frames=debounce_frames,
                           initial_state=MotorState.RUNNING,
                           energy_ref_provider=lambda: ref,
                           running_fraction=fraction)


def settle(gate, f, times=4):
    for _ in range(times):
        gate.update(f)
    return gate.state


def test_relative_gate_detects_a_real_stop():
    gate = relative_gate(RUNNING_ENERGY)
    assert settle(gate, REAL_RUNNING) == MotorState.RUNNING, gate.state
    assert settle(gate, REAL_STOPPED) == MotorState.STOPPED, gate.state
    print("relative gate detects a stop at a realistic accel scale: PASS")


def test_absolute_default_could_never_detect_that_stop():
    """Regression guard for the bug this design replaced. With the shipped
    absolute default, a machine at 1% of its running energy -- unmistakably
    stopped -- still reads RUNNING, because 190 is far above 0.05."""
    gate = MotorStateGate(threshold=0.05, debounce_frames=1,
                          initial_state=MotorState.RUNNING)
    assert settle(gate, REAL_STOPPED) == MotorState.RUNNING, gate.state
    print("the old absolute default provably cannot detect a real stop: PASS")


def test_crosstalk_below_the_fraction_still_reads_stopped():
    """The case the trip feature depends on: motor 1 has stopped but its
    neighbours keep shaking the shared frame. As long as the leak-through sits
    under --gate-running-fraction, the stop is still detected."""
    gate = relative_gate(RUNNING_ENERGY, fraction=0.5)
    assert settle(gate, REAL_CROSSTALK) == MotorState.STOPPED, gate.state
    print("cross-talk under the running fraction still reads stopped: PASS")


def test_crosstalk_above_the_fraction_masks_the_stop():
    """The failure mode to expect on real hardware if the fraction is set too
    low for how rigidly the rig couples: the stop is masked, which surfaces as
    protection's trip_failed rather than a silent lie."""
    gate = relative_gate(RUNNING_ENERGY, fraction=0.15)
    assert settle(gate, REAL_CROSSTALK) == MotorState.RUNNING, gate.state
    print("cross-talk above the running fraction masks the stop, as expected: PASS")


def test_missing_reference_falls_back_to_the_absolute_threshold():
    """A node commissioned before running_energy_ref existed. Falling back
    reproduces its old behaviour exactly rather than changing what it does
    underneath an operator who hasn't re-commissioned yet."""
    for ref in (None, 0.0):
        gate = relative_gate(ref)
        assert settle(gate, REAL_STOPPED) == MotorState.RUNNING, (ref, gate.state)
    print("a node with no calibrated reference falls back to absolute: PASS")


def test_reference_is_reread_every_update():
    """Gates outlive a re-commissioning (MotorPipeline builds its
    classification gate once and never rebuilds it), so a recalibrated
    reference has to take effect without constructing a new gate."""
    ref = [RUNNING_ENERGY]
    gate = MotorStateGate(threshold=0.05, debounce_frames=1,
                          initial_state=MotorState.RUNNING,
                          energy_ref_provider=lambda: ref[0],
                          running_fraction=0.15)
    assert settle(gate, REAL_CROSSTALK) == MotorState.RUNNING, gate.state
    ref[0] = RUNNING_ENERGY * 100          # re-commissioned much "louder"
    assert settle(gate, REAL_CROSSTALK) == MotorState.STOPPED, gate.state
    print("a recalibrated reference takes effect on a live gate: PASS")


def test_energy_and_threshold_are_exposed_for_tuning():
    gate = relative_gate(RUNNING_ENERGY)
    gate.update(REAL_RUNNING)
    assert gate.last_energy == compute_energy(REAL_RUNNING), gate.last_energy
    assert abs(gate.last_threshold - RUNNING_ENERGY * 0.15) < 1e-9, gate.last_threshold
    print("last_energy/last_threshold exposed so the fraction can be tuned: PASS")


def test_invalid_running_fraction_rejected():
    for bad in (0.0, 1.0, -0.1, 1.5):
        try:
            MotorStateGate(threshold=0.05, running_fraction=bad)
        except ValueError:
            continue
        raise AssertionError(f"running_fraction={bad} should have been rejected")
    print("running_fraction outside (0,1) is rejected: PASS")


if __name__ == "__main__":
    try:
        main()
        test_relative_gate_detects_a_real_stop()
        test_absolute_default_could_never_detect_that_stop()
        test_crosstalk_below_the_fraction_still_reads_stopped()
        test_crosstalk_above_the_fraction_masks_the_stop()
        test_missing_reference_falls_back_to_the_absolute_threshold()
        test_reference_is_reread_every_update()
        test_energy_and_threshold_are_exposed_for_tuning()
        test_invalid_running_fraction_rejected()
        print("RESULT: PASS - relative per-node gating behaves, absolute fallback intact")
    except AssertionError as e:
        print(f"RESULT: FAIL - {e}")
        sys.exit(1)
