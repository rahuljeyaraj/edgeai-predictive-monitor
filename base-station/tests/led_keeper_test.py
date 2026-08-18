#!/usr/bin/env python3
"""
Covers registry/led_keeper.py's reconcile contract -- the restart bug it
exists to fix (a dashboard showing a status above a ring that was never
lit), plus the two properties that make it safe to run continuously.

Run with PYTHONPATH covering base-station/python/registry:
    PYTHONPATH=base-station/python/registry python3 base-station/tests/led_keeper_test.py
"""
import os
import sys
import tempfile

from led_keeper import StatusLedKeeper
from registry import NodeStatus, Registry
from status_color import color_for


def _registry_with(node_ids, commissioned=False):
    path = os.path.join(tempfile.mkdtemp(), "registry.json")
    registry = Registry(path)
    for node_id in node_ids:
        registry.add(node_id)
        if commissioned:
            _walk_to_healthy(registry, node_id)
    return registry, path


def _walk_to_healthy(registry, node_id):
    """UNCOMMISSIONED -> HEALTHY the only way the state machine allows
    (registry.py's _NodeStateMachine) -- set_status can't jump there
    directly, and HEALTHY is the gateway to every other confirmable
    status these tests need."""
    registry.start_commissioning(node_id)
    registry.stop_collecting(node_id)
    registry.complete_commissioning(node_id, model_path="/tmp/unused.pt")


class RecordingSink:
    def __init__(self, fail_times=0):
        self.pushes = []
        self._fail_times = fail_times

    def __call__(self, node_id, led):
        if self._fail_times > 0:
            self._fail_times -= 1
            raise RuntimeError("bridge not ready")
        self.pushes.append((node_id, led))


def test_pushes_current_state_with_no_transition():
    """The actual reboot bug: a registry loaded from disk fires no status
    change at all, so an event-only wiring pushed nothing and the ring
    stayed dark under a dashboard that showed a real status."""
    registry, path = _registry_with(["base_station"], commissioned=True)
    registry.set_status("base_station", NodeStatus.IDLE)

    # Reopen from disk -- exactly what a restart does. No listener has ever
    # seen a transition for this node.
    reloaded = Registry(path)
    sink = RecordingSink()
    keeper = StatusLedKeeper(reloaded)
    keeper.add_node_sink("ring", sink)

    assert sink.pushes == [], "nothing pushed before the first reconcile"
    assert keeper.reconcile() is True
    assert sink.pushes == [("base_station", color_for(NodeStatus.IDLE))], sink.pushes
    print("reconcile pushes current state with no transition: PASS")


def test_unchanged_state_is_never_repushed():
    """Re-pushing an unchanged value would restart the animation phase of
    every breathe/strobe command on a fixed period -- a visible stutter on
    exactly the states (warning/fault/tripped) that must read cleanly."""
    registry, _ = _registry_with(["node_a"], commissioned=True)
    registry.set_status("node_a", NodeStatus.WARNING)
    sink = RecordingSink()
    keeper = StatusLedKeeper(registry)
    keeper.add_node_sink("ring", sink)

    for _ in range(5):
        keeper.reconcile()
    assert len(sink.pushes) == 1, sink.pushes
    assert sink.pushes[0][1].mode == "strobe", sink.pushes

    registry.set_status("node_a", NodeStatus.FAULT)
    keeper.reconcile()
    keeper.reconcile()
    assert len(sink.pushes) == 2, sink.pushes
    assert sink.pushes[1][1] == color_for(NodeStatus.FAULT)
    print("an unchanged status is pushed exactly once: PASS")


def test_failed_push_is_retried():
    """Bridge.call raises when the MCU hasn't finished booting; MQTT
    publishes can fail before the broker connects. A failure must not be
    recorded as pushed, or the readout stays wrong until the next
    unrelated status change."""
    registry, _ = _registry_with(["node_a"], commissioned=True)
    sink = RecordingSink(fail_times=2)
    keeper = StatusLedKeeper(registry)
    keeper.add_node_sink("ring", sink)

    assert keeper.reconcile() is False
    assert keeper.reconcile() is False
    assert sink.pushes == []
    assert keeper.reconcile() is True
    assert sink.pushes == [("node_a", color_for(NodeStatus.HEALTHY))], sink.pushes
    print("a failed push is retried until it lands: PASS")


def test_accepts_filter_splits_local_ring_from_mqtt():
    registry, _ = _registry_with(["base_station", "e36428"])
    local, mqtt = RecordingSink(), RecordingSink()
    keeper = StatusLedKeeper(registry)
    keeper.add_node_sink("local", local, accepts=lambda n: n == "base_station")
    keeper.add_node_sink("mqtt", mqtt, accepts=lambda n: n != "base_station")
    keeper.reconcile()

    assert [n for n, _ in local.pushes] == ["base_station"], local.pushes
    assert [n for n, _ in mqtt.pushes] == ["e36428"], mqtt.pushes
    print("accepts filter routes each node to its own transport: PASS")


def test_fleet_sink_pushes_only_on_text_change():
    registry, _ = _registry_with(["node_a"])
    pushed = []
    keeper = StatusLedKeeper(registry)
    keeper.add_fleet_sink("matrix",
                          render=lambda entries: ",".join(sorted(e.status.value for e in entries)),
                          push=pushed.append)

    keeper.reconcile()
    keeper.reconcile()
    assert pushed == ["uncommissioned"], pushed

    _walk_to_healthy(registry, "node_a")
    keeper.reconcile()
    assert pushed == ["uncommissioned", "healthy"], pushed
    print("fleet sink pushes only when its rendered text changes: PASS")


def main():
    test_pushes_current_state_with_no_transition()
    test_unchanged_state_is_never_repushed()
    test_failed_push_is_retried()
    test_accepts_filter_splits_local_ring_from_mqtt()
    test_fleet_sink_pushes_only_on_text_change()
    print("\nled_keeper: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
