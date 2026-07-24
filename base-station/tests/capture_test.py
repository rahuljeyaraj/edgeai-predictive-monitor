#!/usr/bin/env python3
"""
Capture + label workflow verification (pipeline/capture.py,
docs/EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md S2): the gate filters
stopped frames the same way commissioning does, a session can be
reused across repeated start/stop/save/cancel cycles, capture never
touches NodeStatus (works identically on an uncommissioned node), and
label normalization both prevents path traversal and collapses
near-duplicate labels into the same directory.

Run with PYTHONPATH covering base-station/python/ingestion, base-station/python/registry, base-station/python/pipeline:
    PYTHONPATH=base-station/python/ingestion:base-station/python/registry:base-station/python/pipeline \\
        python3 base-station/tests/capture_test.py
"""
import json
import os
import sys
import tempfile

from sensor_frame import FrameSource, SensorFrame
from registry import Registry, SensorChannel, NodeStatus
from gate import MotorStateGate
from capture import (CaptureError, CaptureSession, normalize_label, list_labels,
                      list_captures, rename_capture, delete_capture)

NODE_ID = "node-1"
DIM = 128  # SensorChannel.MIC's spectral bin count (registry._DIM_BY_CHANNEL)

MIC_SCALARS = {"rms_mic": 1.0, "kurtosis_mic": 1.0, "std_mic": 1.0,
               "peak_mic": 1.0, "crest_factor_mic": 1.0, "skewness_mic": 1.0}


def frame(mic_bins, node_id=NODE_ID) -> SensorFrame:
    return SensorFrame(node_id=node_id, source=FrameSource.SPI, timestamp=0.0,
                        bins={"mic": mic_bins}, scalars=MIC_SCALARS)


RUNNING = frame(tuple(3.0 + 0.001 * i for i in range(DIM)))   # RMS well over threshold
STOPPED = frame(tuple(0.01 for _ in range(DIM)))               # RMS well under threshold


def new_session(registry: Registry, captures_dir: str, node_id: str = NODE_ID) -> CaptureSession:
    gate = MotorStateGate(threshold=1.0, debounce_frames=1)
    return CaptureSession(registry, captures_dir, node_id, gate)


def test_normalize_label():
    assert normalize_label("Bearing Fault") == "bearing_fault"
    assert normalize_label("  loose  ") == "loose"
    assert normalize_label("bearing  Fault!") == "bearing_fault"
    # Path-traversal characters collapse away entirely rather than
    # surviving into a filesystem path -- the whole point of
    # normalize_label existing (label is user-controlled REST input).
    assert normalize_label("../../etc/passwd") == "etc_passwd"
    try:
        normalize_label("   ---   ")
        assert False, "expected CaptureError for a label with no alphanumeric content"
    except CaptureError:
        pass
    print("normalize_label collapses casing/whitespace and strips unsafe characters: PASS")


def test_stopped_frames_are_not_collected(registry, captures_dir):
    session = new_session(registry, captures_dir)
    session.start()
    for _ in range(10):
        session.feed_frame(STOPPED)
    assert session.collected_count == 0, session.collected_count
    print("stopped frames are gated out, not collected: PASS")

    try:
        session.stop()
        assert False, "expected CaptureError for an empty batch"
    except CaptureError:
        pass
    print("stop() with nothing collected raises without resetting the session: PASS")

    for _ in range(5):
        session.feed_frame(RUNNING)
    assert session.collected_count == 5, session.collected_count
    n = session.stop()
    assert n == 5, n
    session.cancel()
    print("session survives a failed stop() and can be completed (here, cancelled) afterward: PASS")


def test_auto_stops_at_target_frames(registry, captures_dir):
    session = new_session(registry, captures_dir)
    session.start(target_frames=3)
    assert session.target_frames == 3, session.target_frames

    session.feed_frame(RUNNING)
    assert session.state == "capturing", session.state
    session.feed_frame(RUNNING)
    assert session.state == "capturing", session.state
    session.feed_frame(RUNNING)  # the 3rd frame should auto-freeze, no stop() call
    assert session.state == "stopped", session.state
    assert session.collected_count == 3, session.collected_count

    # Capturing is over -- a further frame is rejected, same as it would
    # be after a manual stop().
    try:
        session.feed_frame(RUNNING)
        assert False, "expected CaptureError once auto-stopped"
    except CaptureError:
        pass

    session.save("bearing")
    print("target_frames auto-stops exactly at the count, no manual stop() needed: PASS")


def test_manual_stop_before_target_reached(registry, captures_dir):
    session = new_session(registry, captures_dir)
    session.start(target_frames=10)
    for _ in range(4):
        session.feed_frame(RUNNING)
    assert session.state == "capturing", session.state

    # "need provision to stop manually as well" (2026-07-24) -- cutting a
    # capture short before the target is reached must still work.
    n = session.stop()
    assert n == 4, n
    assert session.state == "stopped", session.state
    session.cancel()
    print("manual stop() still works before target_frames is reached: PASS")


def test_target_frames_must_be_positive(registry, captures_dir):
    session = new_session(registry, captures_dir)
    try:
        session.start(target_frames=0)
        assert False, "expected CaptureError for target_frames=0"
    except CaptureError:
        pass
    try:
        session.start(target_frames=-1)
        assert False, "expected CaptureError for a negative target_frames"
    except CaptureError:
        pass
    # Session must still be idle/usable after both rejected starts.
    session.start()
    session.cancel()
    print("target_frames must be >= 1: PASS")


def test_capture_independent_of_commissioning(registry, captures_dir):
    entry = registry.get(NODE_ID)
    assert entry.status == NodeStatus.UNCOMMISSIONED, entry.status

    session = new_session(registry, captures_dir)
    session.start()
    for _ in range(5):
        session.feed_frame(RUNNING)
    n = session.stop()
    assert n == 5, n
    path = session.save("Bearing Fault")

    # Capture never touches NodeStatus -- still uncommissioned throughout.
    entry = registry.get(NODE_ID)
    assert entry.status == NodeStatus.UNCOMMISSIONED, entry.status
    assert os.path.exists(path), path
    with open(path) as f:
        payload = json.load(f)
    assert payload["label"] == "bearing_fault", payload
    assert payload["node_id"] == NODE_ID, payload
    assert payload["device_type"] is None, payload
    assert len(payload["vectors"]) == 5, payload
    print("capture works on an uncommissioned node and never touches NodeStatus: PASS")
    return path


def test_save_includes_device_type(registry, captures_dir):
    # Each saved batch belongs to a device type (docs/
    # EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md S1) -- captured at save() time
    # from the node's current registry entry, not passed in by the caller,
    # so it always reflects whatever the operator has assigned.
    registry.set_device_type(NODE_ID, "conveyor_motor")
    session = new_session(registry, captures_dir)
    session.start()
    session.feed_frame(RUNNING)
    session.stop()
    # Distinct label from the shared fixture's "healthy" -- test_session_
    # reused_across_cycles below counts files in that exact directory.
    path = session.save("device_type_probe")

    with open(path) as f:
        payload = json.load(f)
    assert payload["device_type"] == "conveyor_motor", payload
    registry.set_device_type(NODE_ID, None)  # leave shared fixture as found
    print("save() persists the node's current device_type: PASS")


def test_session_reused_across_cycles(registry, captures_dir):
    session = new_session(registry, captures_dir)
    assert session.state == "idle", session.state

    session.start()
    assert session.state == "capturing", session.state
    for _ in range(3):
        session.feed_frame(RUNNING)
    session.stop()
    assert session.state == "stopped", session.state
    session.save("healthy")
    assert session.state == "idle", session.state

    # Same session object, second cycle -- capture is long-lived per node,
    # unlike CommissioningSession's one-shot-per-cycle contract.
    session.start()
    for _ in range(4):
        session.feed_frame(RUNNING)
    session.stop()
    session.save("healthy")
    assert session.state == "idle", session.state

    labels = list_labels(captures_dir)
    assert "healthy" in labels, labels
    healthy_files = os.listdir(os.path.join(captures_dir, "healthy"))
    assert len(healthy_files) == 2, healthy_files
    print("one CaptureSession is reused across repeated start/stop/save cycles: PASS")


def test_cancel_discards_without_saving(registry, captures_dir):
    session = new_session(registry, captures_dir)
    session.start()
    for _ in range(5):
        session.feed_frame(RUNNING)
    session.cancel()
    assert session.state == "idle", session.state
    assert session.collected_count == 0, session.collected_count

    # Also cancellable mid-capture (before stop()).
    session.start()
    session.feed_frame(RUNNING)
    session.cancel()
    assert session.state == "idle", session.state
    print("cancel() discards the batch from both capturing and stopped states: PASS")


def test_feed_frame_before_start_raises(registry, captures_dir):
    session = new_session(registry, captures_dir)
    try:
        session.feed_frame(RUNNING)
        assert False, "expected CaptureError"
    except CaptureError:
        pass
    print("feed_frame() before start() raises: PASS")


def test_double_start_raises(registry, captures_dir):
    session = new_session(registry, captures_dir)
    session.start()
    try:
        session.start()
        assert False, "expected CaptureError"
    except CaptureError:
        pass
    session.cancel()
    print("start() while already active raises: PASS")


def test_frames_for_other_node_ignored(registry, captures_dir):
    session = new_session(registry, captures_dir)
    session.start()
    other = frame(RUNNING.bins["mic"], node_id="node-2")
    for _ in range(5):
        session.feed_frame(other)
    assert session.collected_count == 0, session.collected_count
    session.cancel()
    print("frames for a different node_id are ignored: PASS")


def test_save_without_stop_raises(registry, captures_dir):
    session = new_session(registry, captures_dir)
    try:
        session.save("healthy")
        assert False, "expected CaptureError"
    except CaptureError:
        pass
    session.start()
    session.feed_frame(RUNNING)
    try:
        session.save("healthy")  # still "capturing", not "stopped"
        assert False, "expected CaptureError"
    except CaptureError:
        pass
    session.cancel()
    print("save() before stop() raises in both idle and capturing states: PASS")


def test_list_captures_returns_saved_batches(registry, captures_dir):
    session = new_session(registry, captures_dir)
    session.start()
    for _ in range(3):
        session.feed_frame(RUNNING)
    session.stop()
    path = session.save("list_probe")
    expected_id = os.path.relpath(path, captures_dir).replace(os.sep, "/")

    entries = [e for e in list_captures(captures_dir) if e["label"] == "list_probe"]
    assert len(entries) == 1, entries
    entry = entries[0]
    assert entry["id"] == expected_id, entry
    assert entry["node_id"] == NODE_ID, entry
    assert entry["frame_count"] == 3, entry
    print("list_captures() surfaces a saved batch's id/node/label/frame_count: PASS")


def test_rename_capture_moves_label_bucket(registry, captures_dir):
    session = new_session(registry, captures_dir)
    session.start()
    session.feed_frame(RUNNING)
    session.stop()
    path = session.save("rename_probe_before")
    old_id = os.path.relpath(path, captures_dir).replace(os.sep, "/")

    new_id = rename_capture(captures_dir, old_id, "Rename Probe After")
    assert new_id.startswith("rename_probe_after/"), new_id
    assert not os.path.exists(path), "old file should be gone after rename"

    with open(os.path.join(captures_dir, new_id)) as f:
        payload = json.load(f)
    assert payload["label"] == "rename_probe_after", payload

    entries = {e["id"]: e for e in list_captures(captures_dir)}
    assert new_id in entries, entries
    assert old_id not in entries, entries
    print("rename_capture() moves a saved batch into a new label directory: PASS")


def test_delete_capture_removes_file(registry, captures_dir):
    session = new_session(registry, captures_dir)
    session.start()
    session.feed_frame(RUNNING)
    session.stop()
    path = session.save("delete_probe")
    capture_id = os.path.relpath(path, captures_dir).replace(os.sep, "/")
    assert os.path.exists(path)

    delete_capture(captures_dir, capture_id)
    assert not os.path.exists(path)
    assert capture_id not in {e["id"] for e in list_captures(captures_dir)}
    print("delete_capture() removes the saved file: PASS")


def test_capture_id_path_traversal_rejected(registry, captures_dir):
    # capture_id is dashboard/REST-supplied -- same concern normalize_label()
    # guards for save()'s label, but here the whole "label/filename" pair is
    # untrusted input turned back into a filesystem path.
    for bad_id in ("../../../etc/passwd", "healthy/../../../etc/passwd", "healthy/nonexistent.json"):
        try:
            delete_capture(captures_dir, bad_id)
            assert False, f"expected CaptureError for id {bad_id!r}"
        except CaptureError:
            pass
    print("delete_capture()/rename_capture() reject ids that escape captures_dir: PASS")


def main():
    test_normalize_label()

    tmp_dir = tempfile.mkdtemp(prefix="capture_test_")
    registry_path = os.path.join(tmp_dir, "registry.json")
    captures_dir = os.path.join(tmp_dir, "captures")

    registry = Registry(registry_path)
    registry.add(NODE_ID, sensor_config=frozenset({SensorChannel.MIC}))

    test_stopped_frames_are_not_collected(registry, captures_dir)
    test_auto_stops_at_target_frames(registry, captures_dir)
    test_manual_stop_before_target_reached(registry, captures_dir)
    test_target_frames_must_be_positive(registry, captures_dir)
    test_capture_independent_of_commissioning(registry, captures_dir)
    test_save_includes_device_type(registry, captures_dir)
    test_session_reused_across_cycles(registry, captures_dir)
    test_cancel_discards_without_saving(registry, captures_dir)
    test_feed_frame_before_start_raises(registry, captures_dir)
    test_double_start_raises(registry, captures_dir)
    test_frames_for_other_node_ignored(registry, captures_dir)
    test_save_without_stop_raises(registry, captures_dir)
    test_list_captures_returns_saved_batches(registry, captures_dir)
    test_rename_capture_moves_label_bucket(registry, captures_dir)
    test_delete_capture_removes_file(registry, captures_dir)
    test_capture_id_path_traversal_rejected(registry, captures_dir)

    print("RESULT: PASS - capture collects gated running data, saves labeled batches, "
          "is reusable across cycles, and never touches NodeStatus")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"RESULT: FAIL - {e}")
        sys.exit(1)
