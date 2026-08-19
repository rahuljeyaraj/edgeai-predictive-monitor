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
from gate import (DEFAULT_STOPPED_MARGIN, BinsFrame, MotorState, MotorStateGate,
                   MAX_FLOOR_GAIN, MIN_FLOOR_GAIN_BINS, SpectrumAverager,
                   StoppedBaseline, compute_energy, excess_over_stopped,
                   floor_gain)

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


# ---------------------------------------------------------------------
# Stopped baselines (gate.py's module docstring, docs/progress4.md S4)
#
# The relative gate above fixed the *scale* problem but not the separation
# one: on real hardware a stopped rig reads only ~1.2x below a running one,
# because ~360 of the 384 bins an accel-only frame carries are the KX134's
# broadband noise floor, which is identical either way.
#
# The three tuples below are REAL accel_x spectra measured live off the
# rig's own /ws feed -- the stopped ones with the motors confirmed
# physically off, the running one at the 90rpm commissioning baseline.
# They are here rather than synthesized because synthetic bins are what hid
# both earlier layers of this bug: gate_test's original single-digit values
# hid the absolute-threshold bug for a whole shipped release, and no
# hand-written "stopped" spectrum would have been written with a noise floor
# 65% as tall as the running one, which is the entire difficulty.
#
# RUNNING_FRAME_X is deliberately the QUIETEST of the 45 running frames
# captured, not a typical one -- the worst case is what a gate has to
# survive.
# ---------------------------------------------------------------------

STOPPED_REF_X = (
    13006, 14388, 13321, 14732, 13727, 13373, 14343, 13447, 14344, 13535,
    12772, 11915, 13437, 12796, 11519, 12133, 12260, 11101, 12313, 12423,
    11379, 11180, 10307, 10935, 11405, 10769, 10759, 11063, 10163, 10232,
    8780, 9526, 9266, 8936, 9673, 8716, 8725, 8691, 8657, 8113, 7627,
    8500, 8314, 7472, 7528, 7326, 7541, 7268, 6821, 6177, 6669, 6470,
    6056, 5595, 5456, 5556, 5369, 5750, 5160, 5204, 5206, 4780, 4878,
    5064, 6171, 5147, 4763, 4575, 4693, 4194, 4011, 4103, 4290, 3562,
    3760, 3540, 3564, 3393, 3292, 3392, 3275, 2762, 2872, 3263, 3013,
    3062, 2875, 2859, 2563, 2739, 2514, 2600, 2581, 2438, 2047, 2371,
    2340, 2389, 1943, 1831, 1838, 1804, 1523, 1492, 1541, 1538, 1339,
    1359, 1238, 1237, 1205, 1033, 1024, 1066, 940, 829, 788, 802, 670,
    675, 595, 551, 474, 480, 392, 406, 375, 324,
)

STOPPED_FRAME_X = (
    10384, 21071, 10945, 15791, 20983, 11058, 10148, 12275, 13872, 13111,
    10915, 7725, 14384, 9438, 7561, 10780, 18887, 6644, 9944, 11065,
    18065, 10598, 13426, 13222, 11339, 5741, 8675, 9698, 9019, 9684,
    11151, 5213, 9712, 8902, 8701, 4839, 9124, 6954, 11825, 6182, 9108,
    10827, 9533, 5184, 4482, 8698, 7908, 6112, 3221, 5282, 9200, 3791,
    4708, 4600, 6654, 6076, 6760, 6046, 5157, 3585, 6495, 6330, 4598,
    4371, 6549, 5718, 6903, 5171, 5610, 6762, 2577, 6396, 4189, 3202,
    3498, 5935, 4745, 3626, 1876, 4265, 1997, 3637, 1638, 4451, 4870,
    3270, 3582, 4316, 2496, 3455, 2177, 2592, 3227, 3656, 2685, 2983,
    2348, 2757, 1959, 1888, 1633, 2800, 1584, 1244, 1631, 2446, 1386,
    1237, 1136, 1327, 842, 1279, 726, 693, 1158, 659, 475, 597, 657, 534,
    606, 603, 393, 282, 513, 420, 405, 407,
)

RUNNING_FRAME_X = (
    13130, 13499, 18957, 12371, 6360, 22197, 9073, 22586, 15944, 22662,
    11990, 17741, 9605, 17350, 14418, 19398, 16644, 31036, 18750, 16749,
    17199, 20881, 14553, 12185, 16873, 10987, 10452, 13029, 8117, 7229,
    10416, 7432, 10629, 9065, 12207, 13200, 12115, 11127, 11364, 6663,
    9342, 12191, 4074, 2691, 7444, 10214, 7240, 7257, 5723, 5544, 6005,
    5113, 5621, 9269, 5446, 3393, 5144, 6227, 3111, 3212, 5436, 2984,
    5658, 3127, 7103, 3596, 3279, 3551, 5481, 4083, 2135, 3995, 5304,
    4303, 3614, 4164, 4546, 4147, 4355, 3204, 2268, 3750, 2273, 1259,
    2340, 2362, 2387, 3083, 2988, 4531, 2572, 2987, 2579, 3413, 2196,
    1567, 2453, 2370, 1796, 1819, 1872, 1238, 1570, 1768, 1483, 1125,
    1248, 1228, 1031, 1214, 646, 1141, 922, 791, 658, 1012, 661, 810, 843,
    611, 423, 611, 304, 484, 242, 413, 333, 174,
)


def accel_x_frame(bins):
    return SensorFrame(node_id="node-1", source=FrameSource.SPI, timestamp=0.0,
                        bins={"accel_x": bins})


REAL_STOPPED_FRAME = accel_x_frame(STOPPED_FRAME_X)
REAL_RUNNING_FRAME = accel_x_frame(RUNNING_FRAME_X)
# Measured median of the stopped frames' own excess over STOPPED_REF_X --
# what pipeline/stopped_baseline.py computes and stores as
# RegistryEntry.stopped_energy_ref.
REAL_STOPPED_ENERGY = 1489.3
REAL_BASELINE = StoppedBaseline(spectrum={"accel_x": STOPPED_REF_X},
                                 energy=REAL_STOPPED_ENERGY)


def baseline_gate(baseline, margin=DEFAULT_STOPPED_MARGIN, running_ref=None,
                   debounce_frames=1):
    return MotorStateGate(threshold=0.05, debounce_frames=debounce_frames,
                           initial_state=MotorState.RUNNING,
                           energy_ref_provider=lambda: running_ref,
                           stopped_provider=lambda: baseline,
                           stopped_margin=margin)


def test_real_spectra_defeat_the_unsubtracted_gate():
    """The bug this whole mechanism exists for, as a regression guard.
    These two frames are a genuinely stopped machine and a genuinely running
    one, and their raw RMS energies are only ~1.2x apart -- so the
    running-fraction gate reads the stopped machine as RUNNING, exactly as
    it did live before baselines existed."""
    stopped = compute_energy(REAL_STOPPED_FRAME)
    running = compute_energy(REAL_RUNNING_FRAME)
    assert running / stopped < 1.3, running / stopped
    gate = relative_gate(running, fraction=0.15)
    assert settle(gate, REAL_STOPPED_FRAME) == MotorState.RUNNING, gate.state
    print(f"real stopped/running spectra are only {running / stopped:.2f}x apart, "
           "and the unsubtracted gate misses the stop: PASS")


def test_stopped_baseline_separates_the_same_real_spectra():
    """The same two frames, with the noise floor subtracted out."""
    stopped = compute_energy(REAL_STOPPED_FRAME, REAL_BASELINE)
    running = compute_energy(REAL_RUNNING_FRAME, REAL_BASELINE)
    assert running / stopped > 1.9, running / stopped
    gate = baseline_gate(REAL_BASELINE)
    assert settle(gate, REAL_RUNNING_FRAME) == MotorState.RUNNING, gate.state
    assert settle(gate, REAL_STOPPED_FRAME) == MotorState.STOPPED, gate.state
    print(f"subtracting the measured floor puts them {running / stopped:.2f}x apart "
           "and the gate reads both correctly: PASS")


def test_default_margin_clears_both_sides_on_real_data():
    """The default margin has to sit above the loudest stopped frame and
    below the quietest running one, or it just moves the flap somewhere
    else. Measured live: the loudest of 40 stopped frames came to 2325 on
    this axis, the quietest of 45 running frames to 3010."""
    threshold = REAL_STOPPED_ENERGY * DEFAULT_STOPPED_MARGIN
    loudest_stopped, quietest_running = 2325.0, 3010.3
    assert loudest_stopped < threshold < quietest_running, threshold
    print(f"default margin puts the threshold at {threshold:.0f}, between the loudest "
           f"stopped frame ({loudest_stopped:.0f}) and the quietest running one "
           f"({quietest_running:.0f}): PASS")


def test_baseline_takes_precedence_over_a_running_reference():
    """A node with both must gate on the baseline. Mixing them -- a
    subtracted energy against a running-scale threshold -- would put the
    threshold ~5x too high and read STOPPED forever."""
    gate = baseline_gate(REAL_BASELINE, running_ref=compute_energy(REAL_RUNNING_FRAME))
    assert settle(gate, REAL_RUNNING_FRAME) == MotorState.RUNNING, gate.state
    assert gate.last_threshold == REAL_STOPPED_ENERGY * DEFAULT_STOPPED_MARGIN, \
        gate.last_threshold
    print("a baseline wins over a running reference, and sets the threshold: PASS")


def test_baseline_that_does_not_fit_the_frame_is_ignored_entirely():
    """A node whose bin count or sensor_config changed since its baseline
    was captured. Subtracting the part that still fits would leave a
    partially-subtracted energy under a fully-subtracted threshold, so the
    baseline is dropped wholesale and the node falls back."""
    running_ref = compute_energy(REAL_RUNNING_FRAME)
    wrong_bin_count = StoppedBaseline(spectrum={"accel_x": STOPPED_REF_X[:64]},
                                       energy=REAL_STOPPED_ENERGY)
    wrong_channel = StoppedBaseline(spectrum={"accel_y": STOPPED_REF_X},
                                     energy=REAL_STOPPED_ENERGY)
    for baseline in (wrong_bin_count, wrong_channel):
        assert excess_over_stopped(REAL_STOPPED_FRAME, baseline) is None
        # Falls all the way back to the running-reference behaviour, which
        # on this data means missing the stop -- wrong, but *consistently*
        # wrong, and identical to what the node did before it had a baseline.
        gate = baseline_gate(baseline, running_ref=running_ref)
        assert settle(gate, REAL_STOPPED_FRAME) == MotorState.RUNNING, gate.state
        assert abs(gate.last_threshold - running_ref * 0.15) < 1e-9, gate.last_threshold
    print("a baseline that doesn't fit the frame is dropped whole, not partly: PASS")


def test_zero_energy_baseline_falls_back():
    """A sensor dead throughout the stopped capture reads a perfectly
    constant floor, so every later frame's excess would clear a zero
    threshold and the node would read RUNNING forever."""
    dead = StoppedBaseline(spectrum={"accel_x": STOPPED_REF_X}, energy=0.0)
    gate = baseline_gate(dead, running_ref=compute_energy(REAL_RUNNING_FRAME))
    settle(gate, REAL_STOPPED_FRAME)
    assert gate.last_threshold != 0.0, gate.last_threshold
    print("a degenerate zero-energy baseline falls back instead of gating on zero: PASS")


def test_excess_is_clamped_at_zero_per_bin():
    """A bin quieter than the floor carries no evidence of motion. Letting
    it go negative would let quiet bins cancel out a real line elsewhere."""
    baseline = StoppedBaseline(spectrum={"accel_x": (100.0, 100.0)}, energy=1.0)
    excess = excess_over_stopped(accel_x_frame((0.0, 300.0)), baseline)
    assert excess == [0.0, 200.0], excess
    print("per-bin excess is clamped at zero, so quiet bins can't cancel loud ones: PASS")


def test_baseline_is_reread_every_update():
    """Same requirement as running_energy_ref: capturing a baseline has to
    take effect on the next frame, not the next restart, because gates
    outlive the capture."""
    current = [None]
    gate = MotorStateGate(threshold=0.05, debounce_frames=1,
                          initial_state=MotorState.RUNNING,
                          energy_ref_provider=lambda: compute_energy(REAL_RUNNING_FRAME),
                          stopped_provider=lambda: current[0])
    assert settle(gate, REAL_STOPPED_FRAME) == MotorState.RUNNING, gate.state
    current[0] = REAL_BASELINE
    assert settle(gate, REAL_STOPPED_FRAME) == MotorState.STOPPED, gate.state
    print("a newly captured baseline takes effect on a live gate: PASS")


def test_invalid_stopped_margin_rejected():
    for bad in (1.0, 0.5, 0.0, -1.0):
        try:
            MotorStateGate(threshold=0.05, stopped_margin=bad)
        except ValueError:
            continue
        raise AssertionError(f"stopped_margin={bad} should have been rejected")
    print("stopped_margin at or below 1 is rejected: PASS")


# --- frame averaging ------------------------------------------------------
# The last layer of the same bug (gate.py's DEFAULT_SMOOTHING_FRAMES): a
# mounting whose noise floor jitters frame to frame is unusable at BOTH ends
# -- no baseline can be fitted on it, and a baseline forced through raises
# the running threshold so far that real running frames stop counting.
#
# The jitter modelled here is common-mode, one factor scaling a whole real
# stopped frame, because that is what was measured on the hardware: all
# three axes moving together (r = 0.83-0.96) with lag-1 autocorrelation of
# only +0.17, i.e. near-independent frame to frame. The factors are held in
# a fixed list rather than drawn randomly so a failure is reproducible.
JITTER = (1.00, 1.45, 0.82, 1.30, 0.90, 1.22, 0.86, 1.38, 0.95, 1.15, 1.05, 0.88)


def jittered_stopped_frames():
    return [accel_x_frame(tuple(b * f for b in STOPPED_FRAME_X)) for f in JITTER]


def measured_energies(gate, frames):
    out = []
    for f in frames:
        gate.update(f)
        out.append(gate.last_energy)
    return out


def test_averaging_shrinks_the_floor_jitter_it_is_there_for():
    """Same frames, same baseline, K=1 vs K=6: the spread of what the gate
    measures on a stopped machine has to come down, because that spread is
    the whole reason a baseline gets rejected.

    Measured as excess over 1.0, not as a ratio of ratios. A spread is a
    max/min, so "no spread at all" is 1.0 rather than 0 -- halving the ratio
    itself is only even expressible while it exceeds 2.0, and it no longer
    does: JITTER scales every bin of a frame by one factor, which is
    precisely the uniform drift floor_gain() now takes out, so the K=1 case
    starts at 1.77x here instead of the 6.50x it measured before the gain
    existed. Averaging still cuts what is left by an order of magnitude,
    which is the claim this test is actually making."""
    unsmoothed = MotorStateGate(threshold=THRESHOLD, debounce_frames=1,
                                 stopped_provider=lambda: REAL_BASELINE)
    smoothed = MotorStateGate(
        threshold=THRESHOLD, debounce_frames=1,
        stopped_provider=lambda: StoppedBaseline(spectrum=REAL_BASELINE.spectrum,
                                                  energy=REAL_BASELINE.energy,
                                                  smoothing_frames=6))
    raw = measured_energies(unsmoothed, jittered_stopped_frames())
    avg = measured_energies(smoothed, jittered_stopped_frames())[6:]
    raw_spread = max(raw) / min(raw)
    avg_spread = max(avg) / min(avg)
    assert avg_spread - 1.0 < (raw_spread - 1.0) / 2, (raw_spread, avg_spread)
    print(f"averaging 6 frames cuts the stopped floor's spread {raw_spread:.2f}x -> "
           f"{avg_spread:.2f}x: PASS")


def test_averaging_does_not_erase_a_real_running_signature():
    """The half that makes this a fix rather than a way to hide the
    problem. The motor's lines are in every frame; the floor's jitter is
    not. Averaging must leave a genuinely running machine reading RUNNING
    -- otherwise the baseline would pass its check and commissioning's next
    step would starve, which is the failure this replaced."""
    baseline = StoppedBaseline(spectrum=REAL_BASELINE.spectrum,
                                energy=REAL_BASELINE.energy, smoothing_frames=6)
    gate = MotorStateGate(threshold=THRESHOLD, debounce_frames=DEBOUNCE,
                          stopped_provider=lambda: baseline)
    for _ in range(10):
        gate.update(REAL_RUNNING_FRAME)
    assert gate.state == MotorState.RUNNING, gate.state
    running = gate.last_energy
    for _ in range(10):
        gate.update(REAL_STOPPED_FRAME)
    assert gate.state == MotorState.STOPPED, gate.state
    assert running > gate.last_threshold, (running, gate.last_threshold)
    print(f"averaged real running energy {running:.0f} still clears the "
           f"{gate.last_threshold:.0f} threshold and a real stopped frame does not: PASS")


def test_smoothing_of_one_is_exactly_the_old_behaviour():
    """Every baseline captured before smoothing existed reads back as 1,
    so this is the path most already-commissioned nodes are still on."""
    gate = MotorStateGate(threshold=THRESHOLD, debounce_frames=1,
                          stopped_provider=lambda: REAL_BASELINE)
    frames = jittered_stopped_frames()
    assert measured_energies(gate, frames) == [
        compute_energy(f, REAL_BASELINE) for f in frames]
    print("smoothing_frames=1 measures each frame exactly as before: PASS")


def test_averager_resets_when_the_frame_shape_changes():
    """A node whose sensor_config changes mid-stream must not have two
    different measurements averaged into one frame."""
    averager = SpectrumAverager()
    averager.push(accel_x_frame((10.0, 10.0)), 4)
    averager.push(accel_x_frame((20.0, 20.0)), 4)
    assert averager.push(accel_x_frame((30.0, 30.0)), 4).bins["accel_x"] == (20.0, 20.0)
    other = SensorFrame(node_id="node-1", source=FrameSource.SPI, timestamp=0.0,
                        bins={"accel_y": (100.0, 100.0)})
    assert averager.push(other, 4).bins["accel_y"] == (100.0, 100.0)
    print("the averager drops its window when the channel set changes: PASS")


def test_averager_returns_partial_windows_rather_than_stalling():
    """A gate that produced nothing for its first K frames would stall
    collection at exactly the moment an operator pressed Start."""
    averager = SpectrumAverager()
    assert averager.push(accel_x_frame((10.0, 10.0)), 6).bins["accel_x"] == (10.0, 10.0)
    assert averager.push(accel_x_frame((20.0, 20.0)), 6).bins["accel_x"] == (15.0, 15.0)
    print("a partial window is averaged as-is instead of withheld: PASS")


def test_changing_k_to_one_drops_the_buffered_history():
    """K travels with the baseline, so a re-capture can change it under a
    live gate -- frames averaged for the old baseline must not leak into
    the first measurement made against the new one."""
    averager = SpectrumAverager()
    averager.push(accel_x_frame((10.0, 10.0)), 4)
    averager.push(accel_x_frame((10.0, 10.0)), 1)
    assert averager.push(accel_x_frame((30.0, 30.0)), 4).bins["accel_x"] == (30.0, 30.0)
    print("dropping to K=1 clears the window instead of averaging across the change: PASS")


# --- floor drift (gate.py's floor_gain) -----------------------------------

# A uniform floor drift, the shape measured live on node 194584 an hour
# after its baseline was captured with the machine genuinely stopped: every
# bin up by the same factor, no peak anywhere.
#
# 1.30 rather than the 1.12 that node actually measured, because how much
# drift it takes to break a gate depends on how tight its baseline is, and
# this fixture's is far looser than that node's. Its stopped_energy_ref is
# 20.4% of the floor it was subtracted from, putting its threshold at 35.7%
# of the floor and needing ~1.21x to cross; node 194584's was 7.8%, putting
# its threshold at 13.6% and needing only ~1.14x. That ratio is the real
# lesson -- the tighter the baseline, the less drift it survives -- so this
# picks a drift that crosses THIS fixture's line rather than replaying a
# number that would not.
LIVE_DRIFT = 1.30


def drifted(bins, gain=LIVE_DRIFT):
    return tuple(b * gain for b in bins)


def test_a_drifted_floor_no_longer_reads_running_on_a_stopped_machine():
    """The bug this exists for, on the shape that actually caused it.

    A stopped machine whose noise floor has crept up 12% since commissioning
    was reading RUNNING on 290 of 290 frames live, which held protection/'s
    trip confirmation open on a machine it had genuinely stopped and got the
    trip reported as failed."""
    gate = baseline_gate(REAL_BASELINE)
    frame_ = accel_x_frame(drifted(STOPPED_FRAME_X))

    unscaled = _rms_excess_without_gain(frame_, REAL_BASELINE)
    assert unscaled > gate_threshold(REAL_BASELINE), unscaled

    assert settle(gate, frame_) == MotorState.STOPPED, (gate.state, gate.last_energy)
    print(f"a {LIVE_DRIFT:.2f}x drifted floor measures {gate.last_energy:.0f} against a "
           f"{gate.last_threshold:.0f} threshold and still reads STOPPED "
           f"(unscaled it measured {unscaled:.0f}): PASS")


def test_a_drifted_floor_does_not_hide_a_running_machine():
    """The half that makes it a fix rather than a way to blind the gate:
    the same drift applied to a genuinely running machine must still read
    RUNNING, because the motor's lines are narrow and the median that
    estimates the drift lands on the floor between them."""
    gate = baseline_gate(REAL_BASELINE)
    assert settle(gate, accel_x_frame(drifted(RUNNING_FRAME_X))) == MotorState.RUNNING, \
        (gate.state, gate.last_energy, gate.last_threshold)
    print(f"a running machine seen through the same drift measures "
           f"{gate.last_energy:.0f} over {gate.last_threshold:.0f} and still reads "
           "RUNNING: PASS")


def test_the_gain_is_bounded_so_it_cannot_normalize_a_machine_away():
    """MAX_FLOOR_GAIN's whole job. An unbounded median-of-ratios cannot tell
    a floor that rose from a machine that lifted every bin equally, and
    would report silence for a running machine -- the one error this module
    must never make. Bounded, the most it can ever subtract is
    MAX_FLOOR_GAIN x the floor, so a machine louder than that survives no
    matter what the estimator does."""
    huge = accel_x_frame(tuple(b * 50 for b in STOPPED_REF_X))
    gate = baseline_gate(REAL_BASELINE)
    assert settle(gate, huge) == MotorState.RUNNING, (gate.state, gate.last_energy)
    assert floor_gain(huge, REAL_BASELINE) == MAX_FLOOR_GAIN, floor_gain(huge, REAL_BASELINE)
    print(f"a uniform 50x lift is capped at a {MAX_FLOOR_GAIN}x gain and still reads "
           "RUNNING: PASS")


def test_too_few_bins_falls_back_to_the_unscaled_floor():
    """Below MIN_FLOOR_GAIN_BINS the estimator's premise -- most bins carry
    floor, a few carry machine -- cannot be established at all, so it
    reproduces the old behaviour exactly rather than scaling on a number it
    cannot support. Real frames carry 128 bins per channel and never reach
    this path."""
    baseline = StoppedBaseline(spectrum={"accel_x": (100.0, 100.0)}, energy=1.0)
    tiny = accel_x_frame((0.0, 300.0))
    assert floor_gain(tiny, baseline) == 1.0, floor_gain(tiny, baseline)
    assert excess_over_stopped(tiny, baseline) == [0.0, 200.0]
    print(f"a frame with fewer than {MIN_FLOOR_GAIN_BINS} bins is measured against the "
           "unscaled floor: PASS")


def test_an_undrifted_frame_is_measured_exactly_as_before():
    """No drift, no change: the gain is ~1.0 on a frame taken against its own
    baseline, which is why an existing stopped_energy_ref stays valid and no
    node needs re-commissioning for this."""
    gain = floor_gain(REAL_STOPPED_FRAME, REAL_BASELINE)
    assert 0.95 < gain < 1.05, gain
    scaled = compute_energy(REAL_STOPPED_FRAME, REAL_BASELINE)
    assert abs(scaled - REAL_STOPPED_ENERGY) < 0.25 * REAL_STOPPED_ENERGY, \
        (scaled, REAL_STOPPED_ENERGY)
    print(f"an undrifted frame estimates a {gain:.3f}x gain and still measures "
           f"{scaled:.0f} against a commissioned {REAL_STOPPED_ENERGY:.0f}: PASS")


def gate_threshold(baseline):
    return baseline.energy * DEFAULT_STOPPED_MARGIN


def _rms_excess_without_gain(frame_, baseline):
    """What excess_over_stopped() measured before floor_gain existed."""
    ref = baseline.spectrum["accel_x"]
    excess = [max(b - r, 0.0) for b, r in zip(frame_.bins["accel_x"], ref)]
    return (sum(v * v for v in excess) / len(excess)) ** 0.5


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
        test_real_spectra_defeat_the_unsubtracted_gate()
        test_stopped_baseline_separates_the_same_real_spectra()
        test_default_margin_clears_both_sides_on_real_data()
        test_baseline_takes_precedence_over_a_running_reference()
        test_baseline_that_does_not_fit_the_frame_is_ignored_entirely()
        test_zero_energy_baseline_falls_back()
        test_excess_is_clamped_at_zero_per_bin()
        test_baseline_is_reread_every_update()
        test_invalid_stopped_margin_rejected()
        print("RESULT: PASS - stopped baselines separate real stopped/running spectra")
        test_averaging_shrinks_the_floor_jitter_it_is_there_for()
        test_averaging_does_not_erase_a_real_running_signature()
        test_smoothing_of_one_is_exactly_the_old_behaviour()
        test_averager_resets_when_the_frame_shape_changes()
        test_averager_returns_partial_windows_rather_than_stalling()
        test_changing_k_to_one_drops_the_buffered_history()
        print("RESULT: PASS - frame averaging cuts floor jitter without erasing a "
               "running signature")
        test_a_drifted_floor_no_longer_reads_running_on_a_stopped_machine()
        test_a_drifted_floor_does_not_hide_a_running_machine()
        test_the_gain_is_bounded_so_it_cannot_normalize_a_machine_away()
        test_too_few_bins_falls_back_to_the_unscaled_floor()
        test_an_undrifted_frame_is_measured_exactly_as_before()
        print("RESULT: PASS - a drifted noise floor is scaled out without hiding a "
               "running machine")
    except AssertionError as e:
        print(f"RESULT: FAIL - {e}")
        sys.exit(1)
