#!/usr/bin/env python3
"""
Milestone 7 verification: trigger
commissioning end-to-end on replayed synthetic data; confirm a model file
is created and the registry's status/model_path/last_commissioned are
updated. Also covers the gate filtering out non-running frames, the
too-few-frames guard, and re-commissioning overwriting the same model
file (resolves open question #6).

Run with PYTHONPATH covering base-station/python/ingestion, base-station/python/registry, base-station/python/pipeline:
    PYTHONPATH=base-station/python/ingestion:base-station/python/registry:base-station/python/pipeline \\
        python3 base-station/tests/commissioning_test.py
"""
import os
import sys
import tempfile

from sensor_frame import FrameSource, SensorFrame
from registry import InvalidTransitionError, Registry, SensorChannel, NodeStatus
from gate import MotorStateGate
from commissioning import CommissioningError, CommissioningSession

NODE_ID = "node-1"
DOUBLE_START_NODE_ID = "node-double-start"  # owned only by test_double_start_raises
DIM = 128  # SensorChannel.MIC's spectral bin count (registry._DIM_BY_CHANNEL)

# Fixed, identical across every synthetic frame in this file -- these tests
# are about the commissioning workflow (gating/collection/training/
# re-commissioning), not the scalar tail's own signal, so the scalars stay
# constant and every frame's anomaly-relevant signal comes from mic_bins
# alone, same as before this file gained a scalar tail at all.
MIC_SCALARS = {"rms_mic": 1.0, "kurtosis_mic": 1.0, "std_mic": 1.0,
               "peak_mic": 1.0, "crest_factor_mic": 1.0, "skewness_mic": 1.0}


def frame(mic_bins) -> SensorFrame:
    return SensorFrame(node_id=NODE_ID, source=FrameSource.SPI, timestamp=0.0,
                        bins={"mic": mic_bins}, scalars=MIC_SCALARS)


RUNNING = frame(tuple(3.0 + 0.001 * i for i in range(DIM)))   # RMS well over threshold
STOPPED = frame(tuple(0.01 for _ in range(DIM)))               # RMS well under threshold


def new_session(registry: Registry, models_dir: str, node_id: str = NODE_ID,
                 min_frames: int = 5) -> CommissioningSession:
    gate = MotorStateGate(threshold=1.0, debounce_frames=1)
    return CommissioningSession(registry, models_dir, node_id, gate, min_frames=min_frames)


def test_stopped_frames_are_not_collected(registry, models_dir):
    session = new_session(registry, models_dir)
    session.start()
    for _ in range(10):
        session.feed_frame(STOPPED)
    assert session.collected_count == 0, session.collected_count
    print("stopped frames are gated out, not collected: PASS")

    try:
        session.stop_collecting()
        assert False, "expected CommissioningError for too few collected frames"
    except CommissioningError:
        pass
    print("stop_collecting() with too few frames raises without resetting the session: PASS")

    # Session is still active -- more frames can still be fed and
    # stop_collecting() retried.
    for _ in range(5):
        session.feed_frame(RUNNING)
    assert session.collected_count == 5, session.collected_count
    session.stop_collecting()
    session.train()
    print("session survives a failed stop_collecting() and can be completed afterward: PASS")


def test_end_to_end_commissioning(registry, models_dir):
    session = new_session(registry, models_dir)
    session.start()

    entry = registry.get(NODE_ID)
    assert entry.status == NodeStatus.COMMISSIONING_COLLECTING, entry.status
    print("start() moves registry status to COMMISSIONING_COLLECTING: PASS")

    for _ in range(5):
        session.feed_frame(RUNNING)
    session.stop_collecting()

    entry = registry.get(NODE_ID)
    assert entry.status == NodeStatus.COMMISSIONING_TRAINING, entry.status
    print("stop_collecting() moves registry status to COMMISSIONING_TRAINING: PASS")

    model_path = session.train()

    assert os.path.exists(model_path), model_path
    entry = registry.get(NODE_ID)
    assert entry.status == NodeStatus.HEALTHY, entry.status
    assert entry.model_path == model_path, entry.model_path
    assert entry.last_commissioned is not None
    print("train() creates a model file and updates registry status/model_path/last_commissioned: PASS")
    return model_path


def test_recommissioning_overwrites_same_path(registry, models_dir, first_model_path):
    first_mtime = os.path.getmtime(first_model_path)

    session = new_session(registry, models_dir)
    session.start()
    for _ in range(5):
        session.feed_frame(RUNNING)
    session.stop_collecting()
    second_model_path = session.train()

    assert second_model_path == first_model_path, (second_model_path, first_model_path)
    assert os.path.getmtime(second_model_path) >= first_mtime
    assert len(os.listdir(models_dir)) == 1, os.listdir(models_dir)
    print("re-commissioning overwrites the same model file, not a new version: PASS")


def test_feed_frame_before_start_raises(registry, models_dir):
    session = new_session(registry, models_dir)
    try:
        session.feed_frame(RUNNING)
        assert False, "expected CommissioningError"
    except CommissioningError:
        pass
    print("feed_frame() before start() raises: PASS")


def test_double_start_raises(registry, models_dir):
    # Dedicated node_id, never touched by any other test: this session is
    # deliberately left stuck in COMMISSIONING_COLLECTING (started, never
    # stopped), so it must not share a node_id with a test that runs after
    # it and needs a clean/provisioned entry to work with.
    session = new_session(registry, models_dir, node_id=DOUBLE_START_NODE_ID)
    session.start()
    try:
        session.start()
        assert False, "expected InvalidTransitionError"
    except InvalidTransitionError:
        pass
    print("start() while already active raises: PASS")


def test_frames_for_other_node_ignored(registry, models_dir):
    session = new_session(registry, models_dir)
    session.start()
    other = SensorFrame(node_id="node-2", source=FrameSource.SPI, timestamp=0.0,
                         bins={"mic": RUNNING.bins["mic"]}, scalars=MIC_SCALARS)
    for _ in range(5):
        session.feed_frame(other)
    assert session.collected_count == 0, session.collected_count
    print("frames for a different node_id are ignored: PASS")


def main():
    tmp_dir = tempfile.mkdtemp(prefix="commissioning_test_")
    registry_path = os.path.join(tmp_dir, "registry.json")
    models_dir = os.path.join(tmp_dir, "models")

    registry = Registry(registry_path)
    registry.add(NODE_ID, sensor_config=frozenset({SensorChannel.MIC}))
    registry.add(DOUBLE_START_NODE_ID, sensor_config=frozenset({SensorChannel.MIC}))

    test_stopped_frames_are_not_collected(registry, models_dir)

    # Reset this node's state (previous test left it HEALTHY/collected)
    # by re-adding a fresh registry entry under a clean session.
    registry.decommission(NODE_ID)
    registry.add(NODE_ID, sensor_config=frozenset({SensorChannel.MIC}))

    model_path = test_end_to_end_commissioning(registry, models_dir)
    test_recommissioning_overwrites_same_path(registry, models_dir, model_path)
    test_feed_frame_before_start_raises(registry, models_dir)
    test_double_start_raises(registry, models_dir)
    test_frames_for_other_node_ignored(registry, models_dir)

    print("RESULT: PASS - commissioning collects gated healthy data, trains, saves, "
          "and updates the registry end-to-end")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"RESULT: FAIL - {e}")
        sys.exit(1)
