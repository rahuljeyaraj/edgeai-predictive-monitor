#!/usr/bin/env python3
"""
Milestone 9 verification: run
inference for a period, record each score into the history store, then
query it back and confirm records match what inference observed. Also
covers per-node filtering, persistence across a reopen (simulating a
process restart, same convention as registry_test.py), delete, and
prune_before/count (SQLite-backed store, S8 milestone follow-up).

Run with PYTHONPATH covering base-station/python/ingestion, base-station/python/registry, base-station/python/pipeline,
base-station/python/history:
    PYTHONPATH=base-station/python/ingestion:base-station/python/registry:base-station/python/pipeline:base-station/python/history \\
        python3 base-station/tests/history_test.py
"""
import os
import sys
import tempfile

from sensor_frame import FrameSource, SensorFrame
from registry import NodeStatus, Registry, SensorChannel
from gate import MotorStateGate
from autoencoder import build_autoencoder, save_model, train_autoencoder
from inference import InferencePipeline
from store import HistoryStore

NODE_ID = "node-1"
OTHER_NODE_ID = "node-2"
DIM = 512
WARNING_THRESHOLD = 0.05
FAULT_THRESHOLD = 0.2


def frame(node_id, accel_bins, timestamp=0.0) -> SensorFrame:
    return SensorFrame(node_id=node_id, source=FrameSource.SPI, timestamp=timestamp,
                        bins={"accel": accel_bins})


HEALTHY_BINS = tuple(1.0 for _ in range(DIM))
FAULT_BINS = tuple(4.0 if i % 2 == 0 else 1.0 for i in range(DIM))


def new_registry_with_model(tmp_dir: str, node_id: str) -> Registry:
    models_dir = tempfile.mkdtemp(dir=tmp_dir)
    registry = Registry(os.path.join(models_dir, "registry.json"))
    registry.add(node_id, sensor_config=frozenset({SensorChannel.ACCEL}))

    model = build_autoencoder(DIM)
    train_autoencoder(model, [HEALTHY_BINS] * 5, epochs=500)
    model_path = os.path.join(models_dir, f"{node_id}.pt")
    save_model(model, model_path)
    registry.start_commissioning(node_id)
    registry.stop_collecting(node_id)
    # warning/fault thresholds are a required per-node calibration read by
    # InferencePipeline's constructor from the registry entry (not passed to
    # it directly) -- must be set here for test_end_to_end_with_inference_pipeline
    # below to be able to construct one at all.
    registry.complete_commissioning(node_id, model_path,
                                     warning_threshold=WARNING_THRESHOLD,
                                     fault_threshold=FAULT_THRESHOLD)
    return registry


def test_record_and_query_filters_by_node(tmp_dir):
    store = HistoryStore(os.path.join(tmp_dir, "history.db"))
    store.record(NODE_ID, 1.0, 0.01, NodeStatus.HEALTHY)
    store.record(OTHER_NODE_ID, 1.5, 0.9, NodeStatus.FAULT)
    store.record(NODE_ID, 2.0, 0.02, NodeStatus.HEALTHY)

    records = store.query(NODE_ID)
    assert [r.timestamp for r in records] == [1.0, 2.0], records
    assert all(r.node_id == NODE_ID for r in records)

    other_records = store.query(OTHER_NODE_ID)
    assert len(other_records) == 1 and other_records[0].status_at_time == NodeStatus.FAULT
    print("record/query round-trips and filters by node_id: PASS")


def test_query_missing_node_returns_empty(tmp_dir):
    store = HistoryStore(os.path.join(tmp_dir, "history.db"))
    assert store.query("no-such-node") == []
    print("querying an unrecorded node_id returns an empty list: PASS")


def test_query_before_any_write_returns_empty(tmp_dir):
    store = HistoryStore(os.path.join(tmp_dir, "never-written.db"))
    assert store.query(NODE_ID) == []
    print("querying before any record() returns an empty list: PASS")


def test_persists_across_reopen(tmp_dir):
    path = os.path.join(tmp_dir, "history_restart.db")
    store = HistoryStore(path)
    store.record(NODE_ID, 1.0, 0.03, NodeStatus.HEALTHY)
    store.record(NODE_ID, 2.0, 0.07, NodeStatus.WARNING)
    del store

    reopened = HistoryStore(path)
    records = reopened.query(NODE_ID)
    assert [(r.anomaly_score, r.status_at_time) for r in records] == [
        (0.03, NodeStatus.HEALTHY), (0.07, NodeStatus.WARNING)], records
    print("recorded history survives dropping and reopening the store: PASS")


def test_delete_removes_only_that_node(tmp_dir):
    store = HistoryStore(os.path.join(tmp_dir, "history_delete.db"))
    store.record(NODE_ID, 1.0, 0.03, NodeStatus.HEALTHY)
    store.record(OTHER_NODE_ID, 1.5, 0.9, NodeStatus.FAULT)

    store.delete(NODE_ID)

    assert store.query(NODE_ID) == []
    assert len(store.query(OTHER_NODE_ID)) == 1
    print("delete(node_id) removes only that node's rows: PASS")


def test_prune_before_scopes_correctly(tmp_dir):
    store = HistoryStore(os.path.join(tmp_dir, "history_prune.db"))
    store.record(NODE_ID, 1.0, 0.01, NodeStatus.HEALTHY)
    store.record(NODE_ID, 100.0, 0.02, NodeStatus.HEALTHY)
    store.record(OTHER_NODE_ID, 1.0, 0.9, NodeStatus.FAULT)
    store.record(OTHER_NODE_ID, 100.0, 0.9, NodeStatus.FAULT)

    deleted = store.prune_before(50.0, node_id=NODE_ID)
    assert deleted == 1, deleted
    assert [r.timestamp for r in store.query(NODE_ID)] == [100.0]
    assert store.count(OTHER_NODE_ID) == 2

    deleted = store.prune_before(50.0)
    assert deleted == 1, deleted
    assert [r.timestamp for r in store.query(OTHER_NODE_ID)] == [100.0]
    assert store.count() == 2
    print("prune_before scopes to a node_id when given, and globally otherwise: PASS")


def test_count(tmp_dir):
    store = HistoryStore(os.path.join(tmp_dir, "history_count.db"))
    store.record(NODE_ID, 1.0, 0.01, NodeStatus.HEALTHY)
    store.record(NODE_ID, 2.0, 0.01, NodeStatus.HEALTHY)
    store.record(OTHER_NODE_ID, 1.0, 0.9, NodeStatus.FAULT)

    assert store.count() == 3
    assert store.count(NODE_ID) == 2
    assert store.count(OTHER_NODE_ID) == 1
    print("count() reports total and per-node row counts: PASS")


def test_end_to_end_with_inference_pipeline(tmp_dir):
    registry = new_registry_with_model(tmp_dir, NODE_ID)
    gate = MotorStateGate(threshold=0.5, debounce_frames=1)
    pipeline = InferencePipeline(registry, NODE_ID, gate, debounce_frames=1)
    store = HistoryStore(os.path.join(tmp_dir, "history_e2e.db"))

    frames = [frame(NODE_ID, HEALTHY_BINS, timestamp=float(i)) for i in range(3)]
    frames.append(frame(NODE_ID, FAULT_BINS, timestamp=3.0))
    expected = []
    for f in frames:
        score = pipeline.handle_frame(f)
        assert score is not None
        store.record(NODE_ID, f.timestamp, score, pipeline.status)
        expected.append((f.timestamp, score, pipeline.status))

    records = store.query(NODE_ID)
    assert [(r.timestamp, r.anomaly_score, r.status_at_time) for r in records] == expected, (
        records, expected)
    assert records[-1].status_at_time == NodeStatus.FAULT, records[-1]
    print("history recorded during inference matches observed scores/status: PASS")


def main():
    tmp_dir = tempfile.mkdtemp(prefix="history_test_")

    test_record_and_query_filters_by_node(tempfile.mkdtemp(dir=tmp_dir))
    test_query_missing_node_returns_empty(tempfile.mkdtemp(dir=tmp_dir))
    test_query_before_any_write_returns_empty(tempfile.mkdtemp(dir=tmp_dir))
    test_persists_across_reopen(tempfile.mkdtemp(dir=tmp_dir))
    test_delete_removes_only_that_node(tempfile.mkdtemp(dir=tmp_dir))
    test_prune_before_scopes_correctly(tempfile.mkdtemp(dir=tmp_dir))
    test_count(tempfile.mkdtemp(dir=tmp_dir))
    test_end_to_end_with_inference_pipeline(tempfile.mkdtemp(dir=tmp_dir))

    print("RESULT: PASS - history store persists anomaly score + status per node per "
          "timestamp, and records match what inference observed")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"RESULT: FAIL - {e}")
        sys.exit(1)
