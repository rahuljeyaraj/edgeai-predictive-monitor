#!/usr/bin/env python3
"""
Milestone 8 verification: inject
anomalous data mid-stream and confirm status transitions correctly, and
doesn't flap on a single noisy frame (S3.6). Also covers stopped frames
being excluded from scoring (S3.2), frames for other node_ids being
ignored, and construction failing without a commissioned model.

Run with PYTHONPATH covering base-station/python/ingestion, base-station/python/registry, base-station/python/pipeline:
    PYTHONPATH=base-station/python/ingestion:base-station/python/registry:base-station/python/pipeline \\
        python3 base-station/tests/inference_test.py
"""
import os
import sys
import tempfile

from sensor_frame import FrameSource, SensorFrame
from registry import NodeStatus, Registry, SensorChannel
from gate import MotorStateGate
from features import build_feature_vector
from autoencoder import build_autoencoder, save_model, train_autoencoder
from inference import InferenceError, InferencePipeline

NODE_ID = "node-1"
DIM = 128  # SensorChannel.ACCEL_X's spectral bin count (registry._DIM_BY_CHANNEL)
WARNING_THRESHOLD = 0.05
FAULT_THRESHOLD = 0.2

# Fixed, identical across every synthetic frame -- these tests are about
# threshold/debounce/status-transition behavior, not the scalar tail's own
# signal, so every frame's anomaly-relevant signal comes from accel_x_bins
# alone, same as before this file gained a scalar tail at all. This module
# builds its model directly (not via commissioning.py), so there's no
# standardization fit here either -- constant scalars need none.
#
# accel_x rather than mic as the generic single channel: mic is muted by
# default (features.MUTED_CHANNELS), so a mic-only fixture would feed the
# model an all-zero vector and no injected "fault" could ever raise
# reconstruction error. Nothing here is mic-specific -- see
# features_test.py for the muting behavior itself.
ACCEL_X_SCALARS = {"rms_x": 1.0, "kurtosis_x": 1.0, "std_x": 1.0,
                   "peak_x": 1.0, "crest_factor_x": 1.0, "skewness_x": 1.0}


def frame(accel_x_bins) -> SensorFrame:
    return SensorFrame(node_id=NODE_ID, source=FrameSource.SPI, timestamp=0.0,
                        bins={"accel_x": accel_x_bins}, scalars=ACCEL_X_SCALARS)


# Flat spectrum -- what the model is trained on ("healthy").
HEALTHY = frame(tuple(1.0 for _ in range(DIM)))
# High-energy alternating pattern -- clears the gate's RMS threshold same
# as HEALTHY, but after peak-normalization looks nothing like the flat
# training data, so reconstruction error should be high ("fault").
FAULT = frame(tuple(4.0 if i % 2 == 0 else 1.0 for i in range(DIM)))
# Deep stop (RMS well under the gate threshold).
STOPPED = frame(tuple(0.001 for _ in range(DIM)))


def new_registry_with_model(tmp_dir: str) -> Registry:
    models_dir = tempfile.mkdtemp(dir=tmp_dir)
    registry_path = os.path.join(models_dir, "registry.json")
    registry = Registry(registry_path)
    registry.add(NODE_ID, sensor_config=frozenset({SensorChannel.ACCEL_X}))

    # input_dim_for({ACCEL_X}) == 134 (128 spectral + 6 scalar, registry.py's
    # _DIM_BY_CHANNEL). Built via the real build_feature_vector() (not
    # hand-rolled) so this test trains on exactly what production would --
    # raw, unstandardized (this test builds the model directly, bypassing
    # commissioning.py's standardization fit; constant scalars need none).
    model = build_autoencoder(134)
    healthy_vector, _ = build_feature_vector(HEALTHY, frozenset({SensorChannel.ACCEL_X}), 134)
    train_autoencoder(model, [healthy_vector] * 5, epochs=500)
    model_path = os.path.join(models_dir, f"{NODE_ID}.pt")
    save_model(model, model_path)
    registry.start_commissioning(NODE_ID)
    registry.stop_collecting(NODE_ID)
    registry.complete_commissioning(NODE_ID, model_path,
                                     warning_threshold=WARNING_THRESHOLD,
                                     fault_threshold=FAULT_THRESHOLD)
    return registry


def new_gate() -> MotorStateGate:
    return MotorStateGate(threshold=0.5, debounce_frames=1)


def test_construction_requires_a_commissioned_model(models_dir):
    registry_path = os.path.join(models_dir, "registry_uncommissioned.json")
    registry = Registry(registry_path)
    registry.add("node-2", sensor_config=frozenset({SensorChannel.ACCEL_X}))
    try:
        InferencePipeline(registry, "node-2", new_gate())
        assert False, "expected InferenceError"
    except InferenceError:
        pass
    print("construction without a trained model raises: PASS")


def test_healthy_frames_stay_healthy(registry):
    pipeline = InferencePipeline(registry, NODE_ID, new_gate(), debounce_frames=3)
    for _ in range(10):
        score = pipeline.handle_frame(HEALTHY)
        assert score is not None
    assert pipeline.status == NodeStatus.HEALTHY, pipeline.status
    print("repeated healthy frames stay HEALTHY: PASS")


def test_single_anomalous_frame_does_not_flip_status(registry):
    pipeline = InferencePipeline(registry, NODE_ID, new_gate(), debounce_frames=3)
    pipeline.handle_frame(HEALTHY)
    pipeline.handle_frame(FAULT)  # one noisy/anomalous frame only
    assert pipeline.status == NodeStatus.HEALTHY, pipeline.status
    pipeline.handle_frame(HEALTHY)
    assert pipeline.status == NodeStatus.HEALTHY, pipeline.status
    print("a single anomalous frame does not flip confirmed status: PASS")


def test_sustained_anomaly_flips_to_fault_and_updates_registry(registry):
    pipeline = InferencePipeline(registry, NODE_ID, new_gate(), debounce_frames=3)
    for _ in range(3):
        pipeline.handle_frame(FAULT)
    assert pipeline.status == NodeStatus.FAULT, pipeline.status
    assert registry.get(NODE_ID).status == NodeStatus.FAULT, registry.get(NODE_ID).status
    print("3 consecutive anomalous frames confirm FAULT and update the registry: PASS")


def test_stopped_frames_are_not_scored(registry):
    pipeline = InferencePipeline(registry, NODE_ID, new_gate(), debounce_frames=1)
    for _ in range(5):
        pipeline.handle_frame(HEALTHY)
    assert pipeline.status == NodeStatus.HEALTHY, pipeline.status

    result = pipeline.handle_frame(STOPPED)
    assert result is None, result
    assert pipeline.status == NodeStatus.HEALTHY, pipeline.status
    print("stopped frames are not scored and confirmed status is left untouched: PASS")


def test_confirm_false_scores_but_does_not_write_status(registry):
    # protection.trip_pending()'s scenario: a trip is in flight and the gate
    # hasn't confirmed STOPPED yet, so MotorPipeline calls with confirm=False.
    # The frame must still be scored (chart/telemetry, and self.motor_state
    # must keep tracking the gate) but the confirmed status must not move,
    # even across enough consecutive frames to normally debounce a flip --
    # this is exactly the race that used to let a spin-down score race the
    # gate to FAULT -> HEALTHY before the gate ever confirmed the stop.
    pipeline = InferencePipeline(registry, NODE_ID, new_gate(), debounce_frames=3)
    assert pipeline.status == NodeStatus.HEALTHY, pipeline.status
    for _ in range(5):
        score = pipeline.handle_frame(FAULT, confirm=False)
        assert score is not None
    assert pipeline.status == NodeStatus.HEALTHY, pipeline.status
    assert registry.get(NODE_ID).status == NodeStatus.HEALTHY, registry.get(NODE_ID).status
    print("confirm=False scores frames without ever writing a status: PASS")


def test_frames_for_other_node_ignored(registry):
    pipeline = InferencePipeline(registry, NODE_ID, new_gate())
    other = SensorFrame(node_id="node-other", source=FrameSource.SPI, timestamp=0.0,
                         bins={"accel_x": HEALTHY.bins["accel_x"]}, scalars=ACCEL_X_SCALARS)
    assert pipeline.handle_frame(other) is None
    print("frames for a different node_id are ignored: PASS")


def main():
    tmp_dir = tempfile.mkdtemp(prefix="inference_test_")

    test_construction_requires_a_commissioned_model(tmp_dir)

    registry = new_registry_with_model(tmp_dir)
    test_healthy_frames_stay_healthy(registry)

    registry = new_registry_with_model(tmp_dir)
    test_single_anomalous_frame_does_not_flip_status(registry)

    registry = new_registry_with_model(tmp_dir)
    test_sustained_anomaly_flips_to_fault_and_updates_registry(registry)

    registry = new_registry_with_model(tmp_dir)
    test_stopped_frames_are_not_scored(registry)

    registry = new_registry_with_model(tmp_dir)
    test_confirm_false_scores_but_does_not_write_status(registry)

    registry = new_registry_with_model(tmp_dir)
    test_frames_for_other_node_ignored(registry)

    print("RESULT: PASS - inference loop scores gated live frames, thresholds + "
          "debounces status changes, and pushes confirmed status to the registry")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"RESULT: FAIL - {e}")
        sys.exit(1)
