#!/usr/bin/env python3
"""
Milestone 2 verification: add,
rename, and pause a test entry, then drop the Registry instance and
reopen the same file (simulating a process restart) -- confirm every
mutation survived. Also confirms decommission() (full-removal semantics,
orchestrated in api/app.py alongside HistoryStore.delete()) persists
across a reload, for both a provisioned node and one that never
completed provisioning (removable from any status, S3.9 dashboard
redesign's always-enabled bin icon).

Run with PYTHONPATH covering base-station/python/registry:
    PYTHONPATH=base-station/python/registry python3 base-station/tests/registry_test.py
"""
import sys
import tempfile
import os

from registry import Registry, SensorChannel, NodeStatus, NodeNotFoundError


def main():
    tmp_dir = tempfile.mkdtemp(prefix="registry_test_")
    path = os.path.join(tmp_dir, "registry.json")

    reg = Registry(path)
    reg.add("node-1", display_name="Motor 1",
            sensor_config=frozenset({SensorChannel.MIC, SensorChannel.ACCEL_X}))
    reg.add("node-2", display_name="Motor 2", sensor_config=frozenset({SensorChannel.MIC}))
    reg.rename("node-1", "Compressor A")
    reg.start_commissioning("node-2")
    reg.stop_collecting("node-2")
    reg.complete_commissioning("node-2", model_path="unused.pt")
    reg.pause("node-2")
    reg.add("node-3", sensor_config=frozenset({SensorChannel.MIC}))
    print("mutations applied: PASS")

    # Simulate a process restart: drop the in-memory instance, reopen
    # the same file from a fresh Registry.
    del reg
    reopened = Registry(path)

    entries = reopened.list()
    assert set(entries.keys()) == {"node-1", "node-2", "node-3"}, entries.keys()

    node1 = reopened.get("node-1")
    assert node1.display_name == "Compressor A", node1.display_name
    assert node1.sensor_config == frozenset({SensorChannel.MIC, SensorChannel.ACCEL_X})
    assert node1.input_dim == 268, node1.input_dim
    assert node1.status == NodeStatus.UNCOMMISSIONED

    node2 = reopened.get("node-2")
    assert node2.status == NodeStatus.PAUSED, node2.status
    assert node2.input_dim == 134, node2.input_dim

    node3 = reopened.get("node-3")
    assert node3.status == NodeStatus.UNCOMMISSIONED, node3.status
    print("state survived restart: PASS")

    reopened.decommission("node-3")  # UNCOMMISSIONED -- removable from any status
    assert "node-3" not in reopened.list()
    print("decommission removes a node that never completed provisioning: PASS")

    reopened.decommission("node-2")  # PAUSED -- a provisioned node
    reloaded_again = Registry(path)
    assert "node-2" not in reloaded_again.list()
    try:
        reloaded_again.get("node-2")
        assert False, "expected NodeNotFoundError"
    except NodeNotFoundError:
        pass
    print("decommission persists and get() raises for missing node: PASS")

    changes = []
    reloaded_again.on_status_change(lambda node_id, status: changes.append((node_id, status)))
    reloaded_again.add("node-4", sensor_config=frozenset({SensorChannel.MIC}))
    reloaded_again.start_commissioning("node-4")
    reloaded_again.stop_collecting("node-4")
    reloaded_again.complete_commissioning("node-4", model_path="unused.pt")
    reloaded_again.pause("node-4")
    reloaded_again.resume("node-4")
    reloaded_again.set_status("node-4", NodeStatus.FAULT)
    assert changes == [
        ("node-4", NodeStatus.UNCOMMISSIONED),
        ("node-4", NodeStatus.COMMISSIONING_COLLECTING),
        ("node-4", NodeStatus.COMMISSIONING_TRAINING),
        ("node-4", NodeStatus.HEALTHY),
        ("node-4", NodeStatus.PAUSED),
        ("node-4", NodeStatus.HEALTHY),
        ("node-4", NodeStatus.FAULT),
    ], changes
    print("on_status_change fires for every status-changing method, in order: PASS")

    print("RESULT: PASS - add/rename/pause/decommission all survive a reload")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"RESULT: FAIL - {e}")
        sys.exit(1)
