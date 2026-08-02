#!/usr/bin/env python3
"""
docs/UNIFIED_COMMISSIONING_PLAN.md verification: the guided setup flow
end-to-end (name & class -> off -> running conditions -> train ->
trip output -> done), plus the three pieces it depends on:

  - multiple operating conditions pooled into one model, with
    running_energy_ref taken from the QUIETEST condition rather than the pool
    (S2.3) and one `healthy` recording saved per condition;
  - the confirm-by-stopping trip-output test, including its refusal to run
    against a machine that is already stopped (S3.3 / S9's first risk);
  - the rig's own outputs announce replacing the hardcoded motor list (S3.2).

Pure logic, no hardware and no broker: frames are synthetic, the trip
publisher is a recording stub, and the gate confirmation that normally
arrives from the vibration gate is delivered by calling on_motor_state()
directly, exactly as pipeline/manager.py does.

Run with PYTHONPATH covering base-station/python/{ingestion,registry,
pipeline,api,protection}:
    PYTHONPATH=base-station/python/ingestion:base-station/python/registry:base-station/python/pipeline:base-station/python/api:base-station/python/protection \\
        python3 base-station/tests/setup_test.py
"""
import json
import random
import os
import sys
import tempfile
import time

from sensor_frame import FrameSource, SensorFrame
from registry import NodeStatus, Registry, SensorChannel
from gate import MotorStateGate
from capture import list_captures
from commissioning_controller import CommissioningController
from stopped_baseline_controller import StoppedBaselineController
from setup_controller import (STEP_CONDITIONS, STEP_DONE, STEP_NAME, STEP_STOPPED,
                               STEP_TRAIN, STEP_TRIP_OUTPUT, SetupController, SetupError)
from protection import ProtectionController, ProtectionError
from trip_outputs import TripOutputStore

NODE_ID = "node-1"
DIM = 128  # SensorChannel.MIC's spectral bin count (registry._DIM_BY_CHANNEL)

MIC_SCALARS = {"rms_mic": 1.0, "kurtosis_mic": 1.0, "std_mic": 1.0,
               "peak_mic": 1.0, "crest_factor_mic": 1.0, "skewness_mic": 1.0}

# Deliberately different loudnesses: "no load" is the quieter running state,
# "full load" the louder one. That gap is the whole point of the
# quietest-condition rule -- a pooled median would sit between them, above
# what a legitimate no-load run measures.
QUIET_BINS = tuple(2.0 + 0.001 * i for i in range(DIM))
LOUD_BINS = tuple(9.0 + 0.001 * i for i in range(DIM))
STOPPED_BINS = tuple(0.01 + 0.0001 * (i % 7) for i in range(DIM))

# Small enough to keep this file fast; the production defaults are 50/30/300.
MIN_FRAMES = 5
BASELINE_MIN_FRAMES = 4
EPOCHS = 2


def frame(bins, node_id: str = NODE_ID) -> SensorFrame:
    return SensorFrame(node_id=node_id, source=FrameSource.SPI, timestamp=time.time(),
                        bins={"mic": bins}, scalars=MIC_SCALARS)


def gate_factory(node_id: str) -> MotorStateGate:
    return MotorStateGate(threshold=1.0, debounce_frames=1)


class Harness:
    """Everything main.py wires together for setup, minus the web layer."""

    def __init__(self, tmp_dir: str):
        self.captures_dir = os.path.join(tmp_dir, "captures")
        self.registry = Registry(os.path.join(tmp_dir, "registry.json"))
        self.registry.add(NODE_ID, sensor_config=frozenset({SensorChannel.MIC}))
        self.commissioning = CommissioningController(
            self.registry, os.path.join(tmp_dir, "models"), gate_factory,
            min_frames=MIN_FRAMES, epochs=EPOCHS, captures_dir=self.captures_dir)
        self.stopped_baseline = StoppedBaselineController(
            self.registry, min_frames=BASELINE_MIN_FRAMES)
        self.published = []
        self.running = {}
        self.protection = ProtectionController(
            self.registry,
            publish_trip=self.published.append,
            motor_state_query=lambda node_id: self.running.get(node_id),
            mapping_confirm_window_s=0.4)
        self.setup = SetupController(self.registry, self.commissioning,
                                      stopped_baseline=self.stopped_baseline,
                                      protection=self.protection)
        self.changes = []
        self.setup.on_change(lambda node_id, snapshot: self.changes.append(snapshot))

    def feed(self, bins, count: int) -> None:
        """One frame into every controller, exactly as main.py's on_frame
        does -- the sessions have to find their own frames out of one shared
        stream, which is where a wrongly-routed frame would show up."""
        for _ in range(count):
            f = frame(bins)
            self.commissioning.feed_frame(f)
            self.stopped_baseline.feed_frame(f)

    def feed_stopped(self, count: int) -> None:
        """Stopped frames carry per-bin noise around a fixed floor, which is
        what a real quiet sensor looks like and what stopped_baseline.py's
        two rejections are shaped around: a baseline with exactly zero spread
        reads as a dead sensor, and one whose loudest frame is far above its
        median reads as a machine that was still moving. Independent per-bin
        noise lands between them, with every frame's excess over the fitted
        median floor about the same size."""
        rng = random.Random(20260801)
        for _ in range(count):
            self.feed(tuple(b * rng.uniform(0.8, 1.2) for b in STOPPED_BINS), 1)

    def train_now(self) -> None:
        """What api/app.py's _start_training thread does, synchronously."""
        self.commissioning.run_training(NODE_ID)
        self.setup.finish_training(NODE_ID)


def test_step_one_requires_a_name_and_a_class(h: Harness):
    snapshot = h.setup.start(NODE_ID)
    assert snapshot["step"] == STEP_NAME, snapshot
    assert snapshot["total"] == 6, snapshot

    for name, asset_class, why in [
        ("", "pump", "no name"),
        ("Pump 1", "", "no asset class"),
        (NODE_ID, "pump", "the node id is not a name"),
    ]:
        try:
            h.setup.advance(NODE_ID, device_name=name, device_type=asset_class)
        except SetupError:
            pass
        else:
            raise AssertionError(f"step 1 accepted {why}")
    assert h.setup.snapshot(NODE_ID)["step"] == STEP_NAME, "a rejected step must stay open"
    assert h.setup.snapshot(NODE_ID)["error"], "a rejected step must say why"
    print("step 1 requires both a real nickname and an asset class: PASS")

    h.setup.advance(NODE_ID, device_name="Pump 1", device_type="  Pump  ")
    entry = h.registry.get(NODE_ID)
    assert entry.device_name == "Pump 1", entry.device_name
    # Lowercased like the Fleet pill's own editor: asset class is the key
    # that groups captures into one training set per EI project.
    assert entry.device_type == "pump", entry.device_type
    assert h.setup.snapshot(NODE_ID)["step"] == STEP_STOPPED
    assert h.setup.snapshot(NODE_ID)["error"] is None, "moving on clears the error"
    print("step 1 stores the name and normalizes the asset class: PASS")


def test_confirm_distinguishes_no_gate_from_a_stopped_machine(h: Harness):
    """None (no model, so the gate cannot answer) used to share the stopped
    machine's message, which told the operator to start a machine that was
    often already running -- with no way to make the test pass, since the
    model only arrives at Train. The two now read differently."""
    h.running.pop(NODE_ID, None)
    try:
        h.protection.confirm_trip_output(NODE_ID, 1, lambda ok, msg: None)
    except ProtectionError as e:
        assert "no model yet" in str(e), str(e)
        assert "start the machine" not in str(e), \
            "an un-modelled asset must not be told to start a machine it may already be running"
    else:
        raise AssertionError("a node with no gate must fail the precondition")
    assert not h.published, "nothing may be published when the test can't run"
    print("confirm-by-stopping says what's missing when there's no gate yet: PASS")


def test_confirm_refuses_a_stopped_machine(h: Harness):
    h.running[NODE_ID] = False
    try:
        h.protection.confirm_trip_output(NODE_ID, 1, lambda ok, msg: None)
    except ProtectionError:
        pass
    else:
        raise AssertionError("a stopped machine must fail the precondition, not confirm")
    assert not h.published, "nothing may be published when the test can't run"
    print("confirm-by-stopping refuses a machine that is already stopped: PASS")


def test_confirm_by_stopping(h: Harness):
    results = []
    h.running[NODE_ID] = True
    h.protection.confirm_trip_output(NODE_ID, 2, lambda ok, msg: results.append((ok, msg)))
    assert h.published == [2], h.published

    # The gate goes quiet -- the machine really did stop.
    h.running[NODE_ID] = False
    h.protection.on_motor_state(NODE_ID, running=False)
    assert results and results[0][0] is True, results
    # Stopped by a confirm test, not by a trip: never TRIPPED, and no trip
    # record left behind. (The status itself doesn't move here -- the node is
    # still UNCOMMISSIONED mid-setup, and registry.py deliberately has no
    # UNCOMMISSIONED -> IDLE edge: relabelling "never set up" as "switched
    # off" would read as a regression on the Fleet list.)
    assert h.registry.get(NODE_ID).status != NodeStatus.TRIPPED, h.registry.get(NODE_ID).status
    assert h.protection.snapshot(NODE_ID)["tripped_at"] is None
    print("confirm-by-stopping proves the mapping and never reports TRIPPED: PASS")


def test_confirm_failure_reports_the_wrong_output(h: Harness):
    results = []
    h.running[NODE_ID] = True
    h.protection.confirm_trip_output(NODE_ID, 3, lambda ok, msg: results.append((ok, msg)))
    time.sleep(0.7)  # comfortably past mapping_confirm_window_s
    assert results and results[0][0] is False, results
    assert "isn't the one" in results[0][1], results[0][1]
    assert h.registry.get(NODE_ID).trip_motor_confirmed_at is None, \
        "a failed test must not record a confirmation"
    print("a trip output that doesn't stop the machine reports back as wrong: PASS")


def test_trip_output_step(h: Harness):
    assert h.setup.snapshot(NODE_ID)["step"] == STEP_TRIP_OUTPUT, \
        "trip output is the step after training, not before the baseline"
    try:
        h.setup.advance(NODE_ID)
    except SetupError:
        pass
    else:
        raise AssertionError("the trip-output step advanced with no output picked and no skip")

    # The route records this on a confirmed test; here it stands in for one.
    h.registry.set_trip_motor(NODE_ID, 2, confirmed_at=time.time())
    h.setup.advance(NODE_ID)
    assert h.setup.snapshot(NODE_ID)["step"] == STEP_DONE
    print("step 5 needs a trip output before it will advance: PASS")

    # ... and re-pointing it at a different output drops the confirmation,
    # rather than carrying a proof earned by the previous one forward.
    h.registry.set_trip_motor(NODE_ID, 1)
    assert h.registry.get(NODE_ID).trip_motor_confirmed_at is None
    h.registry.set_trip_motor(NODE_ID, 2, confirmed_at=time.time())
    print("re-pointing a trip output clears its confirmation: PASS")


def test_stopped_baseline_step(h: Harness):
    try:
        h.setup.advance(NODE_ID)
    except SetupError:
        pass
    else:
        raise AssertionError("step 2 advanced with no baseline measured")

    h.stopped_baseline.start(NODE_ID)
    h.feed_stopped(BASELINE_MIN_FRAMES + 2)
    h.stopped_baseline.stop(NODE_ID)
    assert h.registry.get(NODE_ID).stopped_energy_ref is not None

    h.setup.advance(NODE_ID)
    assert h.setup.snapshot(NODE_ID)["step"] == STEP_CONDITIONS
    print("step 2 needs a measured stopped baseline before it will advance: PASS")


def test_two_operating_conditions(h: Harness):
    h.setup.add_condition(NODE_ID, "No load")
    h.feed(QUIET_BINS, MIN_FRAMES + 1)
    step = next(s for s in h.setup.snapshot(NODE_ID)["steps"] if s["id"] == STEP_CONDITIONS)
    assert step["conditions"] == [{"name": "no_load", "frames": MIN_FRAMES + 1}], step

    # A second condition can't be opened until the first has enough frames --
    # half a condition pooled in widens the healthy manifold without covering
    # that duty point.
    h.setup.add_condition(NODE_ID, "Full load")
    h.feed(LOUD_BINS, 2)
    try:
        h.setup.add_condition(NODE_ID, "Third")
    except SetupError:
        pass
    else:
        raise AssertionError("a half-collected condition was allowed to close")
    h.feed(LOUD_BINS, MIN_FRAMES)

    step = next(s for s in h.setup.snapshot(NODE_ID)["steps"] if s["id"] == STEP_CONDITIONS)
    assert [c["name"] for c in step["conditions"]] == ["no_load", "full_load"], step
    print("step 3 collects several named conditions with their own counters: PASS")


def test_training_pools_conditions_and_takes_the_quietest_energy(h: Harness):
    h.setup.advance(NODE_ID)  # freezes the batch; api/app.py starts training here
    assert h.setup.snapshot(NODE_ID)["step"] == STEP_TRAIN
    assert h.registry.get(NODE_ID).status == NodeStatus.COMMISSIONING_TRAINING

    h.train_now()
    entry = h.registry.get(NODE_ID)
    assert entry.status == NodeStatus.HEALTHY, entry.status
    assert os.path.exists(entry.model_path), entry.model_path
    assert entry.operating_conditions == ["no_load", "full_load"], entry.operating_conditions

    # The gate's running threshold is a fraction of this reference, and it
    # must still call the QUIETEST legitimate running state "running" -- so
    # it comes from no_load's median, not from the pool's.
    quiet_energy = _median_energy(h, QUIET_BINS)
    loud_energy = _median_energy(h, LOUD_BINS)
    assert quiet_energy < loud_energy, (quiet_energy, loud_energy)
    assert abs(entry.running_energy_ref - quiet_energy) < 0.01 * quiet_energy, \
        (entry.running_energy_ref, quiet_energy, loud_energy)
    print("training pools every condition but scales the gate from the quietest: PASS")

    # Train is no longer the last real step, and this is what would have caught
    # a finish_training() that still jumped straight to Done: the trip-output
    # step would have been silently skipped over.
    assert h.setup.snapshot(NODE_ID)["step"] == STEP_TRIP_OUTPUT
    print("step 4 hands on to the trip-output step when the model is fitted: PASS")


def _median_energy(h: Harness, bins) -> float:
    from gate import StoppedBaseline, compute_energy
    entry = h.registry.get(NODE_ID)
    baseline = StoppedBaseline(spectrum=entry.stopped_spectrum_ref,
                                energy=entry.stopped_energy_ref)
    return compute_energy(frame(bins), baseline)


def test_each_condition_is_saved_as_a_healthy_recording(h: Harness):
    captures = list_captures(h.captures_dir)
    assert len(captures) == 2, captures
    # One label, two conditions -- separate labels would hand Edge Impulse
    # two classes that both mean "fine".
    assert {c["label"] for c in captures} == {"healthy"}, captures
    assert {c["condition"] for c in captures} == {"no_load", "full_load"}, captures
    assert all(c["device_type"] == "pump" for c in captures), captures
    assert all(c["frame_count"] >= MIN_FRAMES for c in captures), captures
    print("each condition is also kept as a `healthy` recording, tagged by condition: PASS")


def test_cancel_leaves_calibration_alone(h: Harness):
    before = h.registry.get(NODE_ID)
    baseline_before = before.stopped_energy_ref
    model_before = before.model_path

    h.setup.start(NODE_ID, step=STEP_CONDITIONS)
    h.setup.add_condition(NODE_ID, "running")
    h.feed(QUIET_BINS, 2)
    assert h.registry.get(NODE_ID).status == NodeStatus.COMMISSIONING_COLLECTING

    h.setup.cancel(NODE_ID)
    entry = h.registry.get(NODE_ID)
    # Already trained once, so it goes back to HEALTHY and lets the next few
    # scored frames re-diagnose it -- we re-measure a status, never guess one.
    assert entry.status == NodeStatus.HEALTHY, entry.status
    assert entry.stopped_energy_ref == baseline_before, "cancel must not drop the baseline"
    assert entry.model_path == model_before, "cancel must not drop the model"
    assert h.setup.snapshot(NODE_ID) is None
    assert h.setup.progress(NODE_ID) is None
    print("cancelling setup abandons the batch and touches nothing calibrated: PASS")


def test_reentering_setup_opens_on_the_first_gap(h: Harness):
    # Everything is set on this node now, so setup opens finished...
    assert h.setup.start(NODE_ID)["step"] == STEP_DONE
    h.setup.cancel(NODE_ID)
    # ... until something genuinely goes missing.
    h.registry.set_stopped_baseline(NODE_ID, None, None)
    assert h.setup.start(NODE_ID)["step"] == STEP_STOPPED
    h.setup.cancel(NODE_ID)
    print("re-running setup opens on the first step this asset hasn't satisfied: PASS")


def test_skip_only_applies_to_the_trip_output_step(h: Harness):
    h.setup.start(NODE_ID, step=STEP_TRIP_OUTPUT)
    h.setup.skip(NODE_ID)
    snapshot = h.setup.snapshot(NODE_ID)
    assert snapshot["step"] == STEP_DONE, snapshot
    trip_step = next(s for s in snapshot["steps"] if s["id"] == STEP_TRIP_OUTPUT)
    assert trip_step["skipped"] and trip_step["complete"], trip_step

    h.setup.start(NODE_ID, step=STEP_STOPPED)
    try:
        h.setup.skip(NODE_ID)
    except SetupError:
        pass
    else:
        raise AssertionError("a required step was skippable")
    h.setup.cancel(NODE_ID)
    print("only the trip-output step can be skipped: PASS")


def test_setup_broadcasts_every_step_change(h: Harness):
    assert h.changes, "no setup changes were broadcast at all"
    assert h.changes[-1] is None, "cancel must broadcast the flow ending"
    assert any(c and c["step"] == STEP_DONE for c in h.changes), \
        "reaching Done must be broadcast, not just polled"
    print("every step change is pushed to listeners, including the flow ending: PASS")


def test_rig_announces_its_own_outputs():
    store = TripOutputStore()
    assert store.snapshot() == [], "an un-announced rig offers nothing"
    store.announce("motor_rig", [{"idx": 1, "name": "Motor 1"}, {"idx": 2}])
    assert [o["idx"] for o in store.snapshot()] == [1, 2], store.snapshot()
    assert store.host_for(2) == "motor_rig"
    # A later announce replaces rather than merges -- a removed output is
    # genuinely gone, and merging would resurrect it.
    store.announce("motor_rig", [{"idx": 1}])
    assert [o["idx"] for o in store.snapshot()] == [1], store.snapshot()
    print("the rig announces its own outputs, replacing what it said before: PASS")


def test_outputs_announce_parsing():
    from mqtt_subscriber import MalformedMessageError, parse_outputs_message
    host, outputs = parse_outputs_message("epm/motor_rig/outputs",
                                           json.dumps({"outputs": [{"idx": 1}]}).encode())
    assert host == "motor_rig" and outputs == [{"idx": 1}], (host, outputs)
    # An empty retained payload is how an announce is cleared on the broker:
    # "this rig offers nothing", not a parse failure.
    assert parse_outputs_message("epm/motor_rig/outputs", b"") == ("motor_rig", [])
    for bad in (b"not json", b'{"nope": 1}'):
        try:
            parse_outputs_message("epm/motor_rig/outputs", bad)
        except MalformedMessageError:
            pass
        else:
            raise AssertionError(f"parsed a malformed announce: {bad!r}")
    print("a malformed outputs announce is rejected, an empty one means no outputs: PASS")


def main():
    tmp_dir = tempfile.mkdtemp(prefix="setup_test_")
    h = Harness(tmp_dir)

    test_step_one_requires_a_name_and_a_class(h)
    test_confirm_distinguishes_no_gate_from_a_stopped_machine(h)
    test_confirm_refuses_a_stopped_machine(h)
    test_confirm_by_stopping(h)
    test_confirm_failure_reports_the_wrong_output(h)
    # These walk one node through the flow in order, so they run in it: the
    # trip-output step now comes after training, not before the baseline.
    test_stopped_baseline_step(h)
    test_two_operating_conditions(h)
    test_training_pools_conditions_and_takes_the_quietest_energy(h)
    test_trip_output_step(h)
    test_each_condition_is_saved_as_a_healthy_recording(h)
    test_cancel_leaves_calibration_alone(h)
    test_reentering_setup_opens_on_the_first_gap(h)
    test_skip_only_applies_to_the_trip_output_step(h)
    test_setup_broadcasts_every_step_change(h)
    test_rig_announces_its_own_outputs()
    test_outputs_announce_parsing()

    print("RESULT: PASS - guided setup sequences naming, trip-output proof, baseline, "
          "multi-condition collection and training end-to-end")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"RESULT: FAIL - {e}")
        sys.exit(1)
