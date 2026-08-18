#!/usr/bin/env python3
"""
Stopped-baseline capture verification (pipeline/stopped_baseline.py):
fit a noise floor from frames measured with a machine off, confirm it
lands on the registry and survives a restart, and confirm the resulting
baseline actually separates real stopped from real running spectra
through pipeline/gate.py.

The spectra here are the same REAL accel_x measurements gate_test.py
uses -- captured live off the rig's /ws feed with the motors confirmed
physically off, and at the 90rpm commissioning baseline. Synthetic bins
are what hid two earlier layers of this same bug; see gate_test.py's own
comment above STOPPED_REF_X.

Run with PYTHONPATH covering base-station/python/{ingestion,pipeline,registry}:
    PYTHONPATH=base-station/python/ingestion:base-station/python/pipeline:base-station/python/registry \\
        python3 base-station/tests/stopped_baseline_test.py
"""
import os
import statistics
import sys
import tempfile

from sensor_frame import FrameSource, SensorFrame
from gate import MotorState, MotorStateGate, StoppedBaseline
from registry import Registry, SensorChannel
from gate import BinsFrame, compute_energy
from stopped_baseline import (MAX_STOPPED_SPREAD, OUTLIER_ENERGY_FACTOR,
                               StoppedBaselineError, StoppedBaselineSession)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate_test import RUNNING_FRAME_X, STOPPED_FRAME_X, STOPPED_REF_X  # noqa: E402

NODE = "node-1"
MIN_FRAMES = 6


def frame(bins, node_id=NODE, channel="accel_x"):
    return SensorFrame(node_id=node_id, source=FrameSource.SPI, timestamp=0.0,
                        bins={channel: bins})


# Six stopped frames built by taking the REAL per-bin deviation a real
# stopped frame has from the real fitted floor (STOPPED_FRAME_X /
# STOPPED_REF_X) and sliding it across the bins by a different, coprime
# stride per frame. That keeps the deviations' real magnitudes and their
# real distribution -- what the fitted energy, and therefore the gate
# threshold, actually depend on -- while decorrelating the frames enough
# for a median fit to mean something.
#
# The obvious alternative, scaling each whole frame by a factor, does not
# work here and is worth not re-trying: it puts every bin of a frame on the
# same side of the floor, so each frame's clamped excess is either all-zero
# or all-positive and the fitted energy comes out ~5x too small. These six
# fit to 1454 against the 1489 the 40 real frames fit to.
_STRIDE = 17


def _slide(bins, by):
    n = len(bins)
    ratios = [STOPPED_FRAME_X[j] / STOPPED_REF_X[j] for j in range(n)]
    return tuple(bins[j] * ratios[(j + by) % n] for j in range(n))


STOPPED_FRAMES = [_slide(STOPPED_REF_X, i * _STRIDE) for i in range(6)]
# Same generator, enough frames that outlier rejection has somewhere to
# drop one from without dipping under MIN_FRAMES.
MANY_STOPPED_FRAMES = [_slide(STOPPED_REF_X, i * _STRIDE) for i in range(20)]


def session_for(reg, min_frames=MIN_FRAMES, smoothing_frames=1):
    """Most of these tests are about the fit, the retry shape and the
    registry, so they pin smoothing to 1 (the pre-smoothing behaviour)
    rather than the shipped default: six slid copies of one real frame are
    a deliberately decorrelated fixture, not a time series, so averaging
    them models nothing real. The tests below that ARE about smoothing say
    so and set it explicitly; what the shipped default actually does was
    measured on 251 live frames instead (gate.py's
    DEFAULT_SMOOTHING_FRAMES)."""
    return StoppedBaselineSession(reg, NODE, min_frames=min_frames,
                                   smoothing_frames=smoothing_frames)


def new_registry():
    tmp_dir = tempfile.mkdtemp(prefix="stopped_baseline_test_")
    path = os.path.join(tmp_dir, "registry.json")
    reg = Registry(path)
    reg.add(NODE, device_name="Motor 1",
            sensor_config=frozenset({SensorChannel.ACCEL_X}))
    return reg, path


def collect(session, frames):
    for bins in frames:
        session.feed_frame(frame(bins))


def test_baseline_is_fitted_and_persisted():
    reg, path = new_registry()
    session = session_for(reg)
    session.start()
    collect(session, STOPPED_FRAMES)
    baseline = session.stop()

    entry = reg.get(NODE)
    assert entry.stopped_spectrum_ref is not None
    fitted = entry.stopped_spectrum_ref["accel_x"]
    expected = [statistics.median(values) for values in zip(*STOPPED_FRAMES)]
    assert len(fitted) == len(STOPPED_REF_X), len(fitted)
    assert all(abs(f - e) < 1e-9 for f, e in zip(fitted, expected)), fitted[:4]
    # Median per bin, not mean -- a knock against the bench during the
    # capture must not raise the floor everywhere.
    assert fitted != tuple(statistics.fmean(v) for v in zip(*STOPPED_FRAMES))
    assert entry.stopped_energy_ref == baseline.energy > 0, entry.stopped_energy_ref
    print("baseline fits the per-bin median and lands on the registry: PASS")

    del reg
    reopened = Registry(path).get(NODE)
    assert reopened.stopped_energy_ref == entry.stopped_energy_ref
    # JSON round-trips tuples as lists; gate.py zips this against live bins.
    assert isinstance(reopened.stopped_spectrum_ref["accel_x"], tuple)
    assert reopened.stopped_spectrum_ref == entry.stopped_spectrum_ref
    print("baseline survives a restart with its tuple-ness intact: PASS")


def test_captured_baseline_separates_real_spectra_through_the_gate():
    """The end-to-end point of the whole mechanism: a baseline fitted by
    this module, fed to a real MotorStateGate, correctly reads a real
    stopped frame as STOPPED and a real running one as RUNNING -- the pair
    that the pre-baseline gate could not tell apart at all."""
    reg, _ = new_registry()
    session = session_for(reg)
    session.start()
    collect(session, STOPPED_FRAMES)
    session.stop()

    entry = reg.get(NODE)
    baseline = StoppedBaseline(spectrum=entry.stopped_spectrum_ref,
                                energy=entry.stopped_energy_ref)
    gate = MotorStateGate(threshold=0.05, debounce_frames=1,
                          initial_state=MotorState.RUNNING,
                          stopped_provider=lambda: baseline)
    for _ in range(4):
        gate.update(frame(RUNNING_FRAME_X))
    assert gate.state == MotorState.RUNNING, gate.state
    for _ in range(4):
        gate.update(frame(STOPPED_FRAME_X))
    assert gate.state == MotorState.STOPPED, gate.state
    print("a captured baseline drives a real gate across real spectra: PASS")


def test_too_few_frames_keeps_the_capture_alive():
    """Same retry shape as commissioning's stop_collecting(): the operator
    is standing next to a machine they just switched off, so the fix is to
    keep collecting, not to start over."""
    reg, _ = new_registry()
    session = session_for(reg)
    session.start()
    collect(session, STOPPED_FRAMES[:3])
    try:
        session.stop()
        raise AssertionError("stop() should have refused 3 frames")
    except StoppedBaselineError as e:
        assert "need at least" in str(e), e
    assert session.active, "capture should still be collecting"
    assert reg.get(NODE).stopped_energy_ref is None, "registry must be untouched"
    collect(session, STOPPED_FRAMES[3:])
    session.stop()
    assert reg.get(NODE).stopped_energy_ref is not None
    print("too few frames leaves the capture live to retry: PASS")


def test_dead_sensor_is_rejected():
    """Identical frames mean the sensor isn't producing live data, not that
    the machine is quiet. Storing that would give gate.py a zero threshold."""
    reg, _ = new_registry()
    session = session_for(reg)
    session.start()
    collect(session, [STOPPED_REF_X] * MIN_FRAMES)
    try:
        session.stop()
        raise AssertionError("stop() should have refused a perfectly constant floor")
    except StoppedBaselineError as e:
        assert "not producing live data" in str(e), e
    assert reg.get(NODE).stopped_energy_ref is None
    print("a dead sensor is rejected instead of stored as a zero floor: PASS")


def test_unsteady_floor_is_rejected():
    """Something still moving during the capture -- steadily, which is the
    case no amount of averaging or outlier-dropping can help. Left alone it
    would fit a floor whose own frames are already past where the gate
    would put the line, so the node would flap on its own baseline data.

    Every frame here is inside OUTLIER_ENERGY_FACTOR of the others, so the
    transient path deliberately does not fire: this is the "your machine is
    on" verdict, not the "you knocked the bench" one."""
    reg, _ = new_registry()
    session = session_for(reg, min_frames=12)
    session.start()
    swelling = [tuple(b * (1.0 + 1.2 * (i % 2)) for b in bins)
                for i, bins in enumerate(MANY_STOPPED_FRAMES)]
    collect(session, swelling)
    try:
        session.stop()
        raise AssertionError("stop() should have refused an unsteady floor")
    except StoppedBaselineError as e:
        assert "still moving" in str(e), e
        assert str(MAX_STOPPED_SPREAD) in str(e), e
    assert reg.get(NODE).stopped_energy_ref is None
    print("a floor too unsteady to gate on is rejected with the reason: PASS")


def test_one_knock_is_dropped_instead_of_failing_the_capture():
    """The whole reason a long capture kept failing: one transient -- a
    knock against the bench, someone leaning on the machine -- used to be
    measured as max() and sank the entire capture, however long it was."""
    reg, _ = new_registry()
    session = session_for(reg, min_frames=12)
    session.start()
    knock = tuple(b * 8.0 for b in STOPPED_REF_X)
    collect(session, MANY_STOPPED_FRAMES[:-1] + [knock])
    baseline = session.stop()
    assert baseline.energy > 0

    # The knock must not have raised the stored floor either: fitting it in
    # would put the gate's line above real running energy.
    clean = session_for(reg, min_frames=12)
    clean.start()
    collect(clean, MANY_STOPPED_FRAMES[:-1])
    assert abs(clean.stop().energy - baseline.energy) < 1e-6
    print("a single knock is dropped and changes nothing about the fit: PASS")


def test_a_capture_full_of_transients_is_rejected_as_still_running():
    """The other side of that: dropping outliers must not become a way to
    quietly fit a floor from the quiet quarter of a capture taken with the
    machine still on."""
    reg, _ = new_registry()
    session = session_for(reg, min_frames=6)
    session.start()
    # 6 of 20 frames (30%) well past OUTLIER_ENERGY_FACTOR -- above
    # MAX_OUTLIER_FRACTION, so dropping them all is no longer a credible
    # reading of the capture.
    noisy = [tuple(b * (20.0 if i % 3 == 0 else 1.0) for b in bins)
             for i, bins in enumerate(MANY_STOPPED_FRAMES[:18])]
    collect(session, noisy)
    try:
        session.stop()
        raise AssertionError("stop() should have refused a capture of transients")
    except StoppedBaselineError as e:
        assert "too many to be knocks" in str(e), e
    assert reg.get(NODE).stopped_energy_ref is None
    print("a capture where half the frames are transients is rejected: PASS")


def test_smoothing_is_recorded_and_the_gate_obeys_it():
    """The scale rule from gate.py's StoppedBaseline.smoothing_frames: a
    baseline fitted on averaged frames is only meaningful against averaged
    frames, so how it was fitted travels with it to the registry and out to
    the gate, instead of both ends reading a shared setting."""
    reg, _ = new_registry()
    session = session_for(reg, smoothing_frames=2)
    session.start()
    collect(session, STOPPED_FRAMES)
    baseline = session.stop()
    assert baseline.smoothing_frames == 2
    assert reg.get(NODE).stopped_smoothing_frames == 2

    # And the gate really averages: fed a loud frame then a quiet one, the
    # second measurement must be the mean of the two, not the quiet one.
    gate = MotorStateGate(threshold=0.05, debounce_frames=1,
                          stopped_provider=lambda: baseline)
    loud = tuple(b * 3.0 for b in STOPPED_REF_X)
    quiet = STOPPED_REF_X
    gate.update(frame(loud))
    gate.update(frame(quiet))
    averaged = BinsFrame(NODE, {"accel_x": tuple((a + b) / 2 for a, b in zip(loud, quiet))})
    assert abs(gate.last_energy - compute_energy(averaged, baseline)) < 1e-6, gate.last_energy
    assert gate.last_energy > compute_energy(frame(quiet), baseline)
    print("smoothing travels with the baseline and the gate averages by it: PASS")


def test_a_baseline_from_before_smoothing_existed_is_gated_unsmoothed():
    """Back-compat, and it is not cosmetic: those baselines were fitted at
    the raw bins' per-bin MEDIAN, and gating them on averaged frames (which
    converge on the MEAN, ~6.5% higher for Rayleigh-ish magnitudes) would
    leave a systematic positive excess in every bin and read RUNNING at
    rest."""
    reg, _ = new_registry()
    session = session_for(reg)
    session.start()
    collect(session, STOPPED_FRAMES)
    session.stop()
    entry = reg.get(NODE)
    # What Registry stores for a caller that never mentions smoothing.
    reg.set_stopped_baseline(NODE, entry.stopped_spectrum_ref, entry.stopped_energy_ref)
    assert reg.get(NODE).stopped_smoothing_frames == 1
    assert StoppedBaseline(spectrum={}, energy=1.0).smoothing_frames == 1
    print("a pre-smoothing baseline reads back as unsmoothed: PASS")


def test_a_capture_too_short_for_its_averaging_is_refused():
    """Caught by the shipped defaults being 150 and 6, but not by a caller
    that lowers one and forgets the other: at min_frames 4 with 6-frame
    averaging, every window is a partial average and the spread measured is
    the averaging's, not the machine's."""
    reg, _ = new_registry()
    try:
        StoppedBaselineSession(reg, NODE, min_frames=4, smoothing_frames=6)
        raise AssertionError("a capture shorter than twice its averaging should be refused")
    except ValueError as e:
        assert "at least twice" in str(e), e
    StoppedBaselineSession(reg, NODE, min_frames=12, smoothing_frames=6)
    print("a capture too short for its own averaging is refused up front: PASS")


def test_foreign_and_mismatched_frames_are_dropped():
    """A live stream mixes nodes by nature, and a node whose sensor_config
    changes mid-capture would otherwise contribute bins the baseline can
    never be applied to."""
    reg, _ = new_registry()
    session = session_for(reg)
    session.start()
    session.feed_frame(frame(STOPPED_REF_X, node_id="someone-else"))
    assert session.collected_count == 0, session.collected_count
    collect(session, STOPPED_FRAMES[:1])
    session.feed_frame(frame(STOPPED_REF_X, channel="accel_y"))   # channel set changed
    session.feed_frame(frame(STOPPED_REF_X[:64]))                  # bin count changed
    assert session.collected_count == 1, session.collected_count
    print("frames from other nodes and mismatched channel sets are dropped: PASS")


def test_mic_only_node_uses_mic():
    """A node with no accelerometer has nothing else to measure -- same
    fallback gate.energy_channels() makes for the live gate."""
    reg, _ = new_registry()
    session = session_for(reg)
    session.start()
    for bins in STOPPED_FRAMES:
        session.feed_frame(frame(bins, channel="mic"))
    session.stop()
    assert set(reg.get(NODE).stopped_spectrum_ref) == {"mic"}
    print("a mic-only node baselines on mic: PASS")


def test_cancel_leaves_any_existing_baseline_alone():
    reg, _ = new_registry()
    session = session_for(reg)
    session.start()
    collect(session, STOPPED_FRAMES)
    session.stop()
    stored = reg.get(NODE).stopped_energy_ref

    again = session_for(reg)
    again.start()
    collect(again, STOPPED_FRAMES[:2])
    again.cancel()
    assert not again.active
    assert reg.get(NODE).stopped_energy_ref == stored, "cancel must not overwrite"
    print("cancelling a capture keeps the previously stored baseline: PASS")


def test_clearing_requires_both_fields_together():
    """gate.py needs a floor and a scale or neither -- a half-set entry
    would make it subtract a floor and threshold on the wrong scale."""
    reg, _ = new_registry()
    reg.set_stopped_baseline(NODE, {"accel_x": STOPPED_REF_X}, 1489.3)
    reg.set_stopped_baseline(NODE, None, None)
    assert reg.get(NODE).stopped_spectrum_ref is None
    assert reg.get(NODE).stopped_energy_ref is None
    for spectrum, energy in (({"accel_x": STOPPED_REF_X}, None), (None, 1489.3)):
        try:
            reg.set_stopped_baseline(NODE, spectrum, energy)
            raise AssertionError("a half-set baseline should have been rejected")
        except ValueError:
            pass
    print("baseline fields can only be set or cleared together: PASS")


if __name__ == "__main__":
    try:
        test_baseline_is_fitted_and_persisted()
        test_captured_baseline_separates_real_spectra_through_the_gate()
        test_too_few_frames_keeps_the_capture_alive()
        test_dead_sensor_is_rejected()
        test_unsteady_floor_is_rejected()
        test_one_knock_is_dropped_instead_of_failing_the_capture()
        test_a_capture_full_of_transients_is_rejected_as_still_running()
        test_smoothing_is_recorded_and_the_gate_obeys_it()
        test_a_baseline_from_before_smoothing_existed_is_gated_unsmoothed()
        test_a_capture_too_short_for_its_averaging_is_refused()
        test_foreign_and_mismatched_frames_are_dropped()
        test_mic_only_node_uses_mic()
        test_cancel_leaves_any_existing_baseline_alone()
        test_clearing_requires_both_fields_together()
        print("RESULT: PASS - stopped-baseline capture fits, validates and persists")
    except AssertionError as e:
        print(f"RESULT: FAIL - {e}")
        sys.exit(1)
