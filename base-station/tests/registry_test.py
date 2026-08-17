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
import json
import sys
import tempfile
import os

from registry import (InvalidTransitionError, NodeNotFoundError, NodeStatus,
                       Registry, SensorChannel, TripMotorInUseError)


def main():
    tmp_dir = tempfile.mkdtemp(prefix="registry_test_")
    path = os.path.join(tmp_dir, "registry.json")

    reg = Registry(path)
    reg.add("node-1", device_name="Motor 1",
            sensor_config=frozenset({SensorChannel.MIC, SensorChannel.ACCEL_X}))
    reg.add("node-2", device_name="Motor 2", sensor_config=frozenset({SensorChannel.MIC}))
    reg.rename("node-1", "Compressor A")
    reg.set_device_type("node-1", "conveyor_motor")
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
    assert node1.device_name == "Compressor A", node1.device_name
    assert node1.device_type == "conveyor_motor", node1.device_type
    assert node1.sensor_config == frozenset({SensorChannel.MIC, SensorChannel.ACCEL_X})
    assert node1.input_dim == 268, node1.input_dim
    assert node1.status == NodeStatus.UNCOMMISSIONED

    node2 = reopened.get("node-2")
    assert node2.status == NodeStatus.PAUSED, node2.status
    assert node2.input_dim == 134, node2.input_dim
    assert node2.device_type is None, node2.device_type

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


# ---------------------------------------------------------------------
# Machinery protection: trip_motor_idx + the IDLE/TRIPPED statuses
# (docs/MOTOR_STOP_PLAN.md)
# ---------------------------------------------------------------------

def commissioned(registry, node_id):
    """Walk the real state machine to HEALTHY -- the protection transitions
    only exist relative to a legitimately commissioned node."""
    registry.add(node_id)
    registry.start_commissioning(node_id)
    registry.stop_collecting(node_id)
    registry.complete_commissioning(node_id, f"/tmp/{node_id}.pt")


def fresh_registry():
    tmp_dir = tempfile.mkdtemp(prefix="registry_protection_test_")
    path = os.path.join(tmp_dir, "registry.json")
    return Registry(path), path


def test_trip_motor_defaults_unset_and_round_trips():
    registry, path = fresh_registry()
    commissioned(registry, "node-1")
    # Unarmed by default: most monitored points have no actuator, so
    # protection is opt-in per asset rather than fleet-wide.
    assert registry.get("node-1").trip_motor_idx is None

    registry.set_trip_motor("node-1", 2)
    assert registry.get("node-1").trip_motor_idx == 2
    assert Registry(path).get("node-1").trip_motor_idx == 2, "must survive a reload"

    registry.set_trip_motor("node-1", None)
    assert registry.get("node-1").trip_motor_idx is None
    print("trip_motor_idx defaults unset, persists, and clears: PASS")


def test_one_motor_cannot_be_claimed_by_two_assets():
    registry, _ = fresh_registry()
    commissioned(registry, "node-1")
    commissioned(registry, "node-2")
    registry.set_trip_motor("node-1", 1)

    try:
        registry.set_trip_motor("node-2", 1)
    except TripMotorInUseError:
        pass
    else:
        raise AssertionError("a second node claiming motor 1 should have been rejected")
    assert registry.get("node-2").trip_motor_idx is None
    # Re-setting the same motor on the node that already owns it is fine --
    # it isn't a conflict with itself.
    registry.set_trip_motor("node-1", 1)
    # And it frees up once released.
    registry.set_trip_motor("node-1", None)
    registry.set_trip_motor("node-2", 1)
    assert registry.get("node-2").trip_motor_idx == 1
    print("one motor, one asset -- claims conflict, releases free it: PASS")


def test_zero_and_negative_motor_indexes_rejected():
    registry, _ = fresh_registry()
    commissioned(registry, "node-1")
    for bad in (0, -1):
        try:
            registry.set_trip_motor("node-1", bad)
        except ValueError:
            continue
        raise AssertionError(f"motor_idx={bad} should have been rejected")
    print("motor indexes are 1-based; 0 and negatives rejected: PASS")


def test_idle_and_tripped_transitions():
    registry, _ = fresh_registry()
    commissioned(registry, "node-1")

    # A stopped machine is reachable from any of the three confirmable
    # statuses. Each leg starts from HEALTHY, and the first one is already
    # there -- a same-status set is not a legal edge (nor a no-op), which is
    # why inference.py checks for "no change" before ever calling set_status.
    for start in (None, NodeStatus.WARNING, NodeStatus.FAULT):
        if start is not None:
            registry.set_status("node-1", start)
        registry.set_status("node-1", NodeStatus.IDLE)
        assert registry.get("node-1").status == NodeStatus.IDLE
        registry.set_status("node-1", NodeStatus.HEALTHY)

    # TRIPPED only from FAULT: we only ever stop a machine we've faulted.
    registry.set_status("node-1", NodeStatus.FAULT)
    registry.set_status("node-1", NodeStatus.TRIPPED)
    assert registry.get("node-1").status == NodeStatus.TRIPPED

    # Recovery: restarting re-diagnoses from HEALTHY, out of either stopped
    # state, with no acknowledge step.
    registry.set_status("node-1", NodeStatus.HEALTHY)
    assert registry.get("node-1").status == NodeStatus.HEALTHY
    print("IDLE reachable from all confirmable statuses, TRIPPED only from FAULT: PASS")


def test_tripped_not_reachable_from_healthy():
    """The dangerous case: never claim a machine is stopped without having
    faulted and confirmed it."""
    registry, _ = fresh_registry()
    commissioned(registry, "node-1")
    try:
        registry.set_status("node-1", NodeStatus.TRIPPED)
    except InvalidTransitionError:
        pass
    else:
        raise AssertionError("HEALTHY -> TRIPPED should not be allowed")
    assert registry.get("node-1").status == NodeStatus.HEALTHY
    print("HEALTHY -> TRIPPED is rejected: PASS")


def test_uncommissioned_node_cannot_go_idle():
    """"Never set up" must not be relabelled as "switched off"."""
    registry, _ = fresh_registry()
    registry.add("node-1")
    try:
        registry.set_status("node-1", NodeStatus.IDLE)
    except InvalidTransitionError:
        pass
    else:
        raise AssertionError("UNCOMMISSIONED -> IDLE should not be allowed")
    assert registry.get("node-1").status == NodeStatus.UNCOMMISSIONED
    print("an uncommissioned node cannot be reported IDLE: PASS")


def test_running_energy_ref_round_trips():
    registry, path = fresh_registry()
    registry.add("node-1")
    assert registry.get("node-1").running_energy_ref is None
    registry.start_commissioning("node-1")
    registry.stop_collecting("node-1")
    registry.complete_commissioning("node-1", "/tmp/node-1.pt",
                                     running_energy_ref=12345.0)
    assert registry.get("node-1").running_energy_ref == 12345.0
    assert Registry(path).get("node-1").running_energy_ref == 12345.0
    print("running_energy_ref is calibrated at commissioning and persists: PASS")


def test_legacy_entry_without_new_fields_still_loads():
    """An on-disk registry written before these fields existed must keep
    loading -- the same backward-compat contract that still pops
    control_circuit_id/auto_cutoff_enabled."""
    registry, path = fresh_registry()
    commissioned(registry, "node-1")
    with open(path) as f:
        raw = json.load(f)
    raw["node-1"].pop("trip_motor_idx")
    raw["node-1"].pop("running_energy_ref")
    raw["node-1"]["control_circuit_id"] = "legacy"
    raw["node-1"]["auto_cutoff_enabled"] = True
    with open(path, "w") as f:
        json.dump(raw, f)

    entry = Registry(path).get("node-1")
    assert entry.trip_motor_idx is None
    assert entry.running_energy_ref is None
    print("a registry written before these fields existed still loads: PASS")


def test_rename_device_type_cascades_to_every_matching_node():
    # api/app.py's /device_types/rename route relies on this to retag every
    # node currently on old_device_type without touching a node on a
    # different type or with none set at all.
    tmp_dir = tempfile.mkdtemp(prefix="registry_test_")
    path = os.path.join(tmp_dir, "registry.json")
    reg = Registry(path)
    reg.add("node-1", sensor_config=frozenset({SensorChannel.MIC}))
    reg.add("node-2", sensor_config=frozenset({SensorChannel.MIC}))
    reg.add("node-3", sensor_config=frozenset({SensorChannel.MIC}))
    reg.set_device_type("node-1", "motor")
    reg.set_device_type("node-2", "motor")
    reg.set_device_type("node-3", "pump")

    changed = reg.rename_device_type("motor", "conveyor_motor")
    assert set(changed) == {"node-1", "node-2"}, changed
    assert reg.get("node-1").device_type == "conveyor_motor"
    assert reg.get("node-2").device_type == "conveyor_motor"
    assert reg.get("node-3").device_type == "pump", "wrong-type node must not change"

    reopened = Registry(path)
    assert reopened.get("node-1").device_type == "conveyor_motor", \
        "rename must persist across a reload"
    print("rename_device_type cascades to every matching node and persists: PASS")


def test_rename_device_type_no_match_is_a_noop():
    tmp_dir = tempfile.mkdtemp(prefix="registry_test_")
    path = os.path.join(tmp_dir, "registry.json")
    reg = Registry(path)
    reg.add("node-1", sensor_config=frozenset({SensorChannel.MIC}))
    reg.set_device_type("node-1", "pump")
    assert reg.rename_device_type("motor", "conveyor_motor") == []
    assert reg.get("node-1").device_type == "pump"
    print("rename_device_type with no matching node is a no-op: PASS")


if __name__ == "__main__":
    try:
        main()
        test_rename_device_type_cascades_to_every_matching_node()
        test_rename_device_type_no_match_is_a_noop()
        test_trip_motor_defaults_unset_and_round_trips()
        test_one_motor_cannot_be_claimed_by_two_assets()
        test_zero_and_negative_motor_indexes_rejected()
        test_idle_and_tripped_transitions()
        test_tripped_not_reachable_from_healthy()
        test_uncommissioned_node_cannot_go_idle()
        test_running_energy_ref_round_trips()
        test_legacy_entry_without_new_fields_still_loads()
        print("RESULT: PASS - trip mapping and the IDLE/TRIPPED statuses behave")
    except AssertionError as e:
        print(f"RESULT: FAIL - {e}")
        sys.exit(1)
