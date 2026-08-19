#!/usr/bin/env python3
"""
Machinery-protection ladder (docs/MOTOR_STOP_PLAN.md, python/protection/):
FAULT -> delayed trip -> published stop -> confirmed TRIPPED, plus the
IDLE-vs-TRIPPED distinction, the Hold override, and the failed-trip case.

Pure logic, no hardware and no broker: publish_trip is a recording stub, and
the confirmation that normally arrives from the vibration gate is delivered
by calling on_motor_state() directly (that's exactly what
pipeline/manager.py does with it).

Timings are deliberately tiny so the whole file runs in about a second --
the production defaults are 10s/3s.

Run with PYTHONPATH covering base-station/python/{registry,protection}:
    PYTHONPATH=base-station/python/registry:base-station/python/protection \\
        python3 base-station/tests/protection_test.py
"""
import os
import sys
import tempfile
import time

from registry import NodeStatus, Registry
from protection import ProtectionController

NODE_ID = "node-1"
OTHER_NODE_ID = "node-2"

TRIP_DELAY_S = 0.15
# Enough for a Timer thread to actually run its callback, without making the
# suite slow. Timers are not precise, so every wait here is generous.
SETTLE_S = 0.25
# Comfortably longer than SETTLE_S, so a test that waits for the trip to be
# published still has the confirmation window open afterwards. Only the
# failed-trip test wants a window short enough to expire.
CONFIRM_WINDOW_S = 2.0
SHORT_CONFIRM_WINDOW_S = 0.1


def build(trip_motor_idx=1, with_publisher=True,
           confirm_window_s=CONFIRM_WINDOW_S, motor_state_query=None):
    """A commissioned, HEALTHY node plus a ProtectionController wired to it,
    with the trip output armed against `trip_motor_idx` (None = unarmed)."""
    tmp_dir = tempfile.mkdtemp(prefix="protection_test_")
    registry = Registry(os.path.join(tmp_dir, "registry.json"))
    registry.add(NODE_ID)
    # Walk the real state machine to HEALTHY rather than assigning .status --
    # the transitions under test only exist relative to a legitimate one.
    registry.start_commissioning(NODE_ID)
    registry.stop_collecting(NODE_ID)
    registry.complete_commissioning(NODE_ID, "/tmp/does-not-need-to-exist.pt")
    if trip_motor_idx is not None:
        registry.set_trip_motor(NODE_ID, trip_motor_idx)

    published = []
    protection = ProtectionController(
        registry,
        publish_trip=(lambda idx: published.append(idx)) if with_publisher else None,
        trip_delay_s=TRIP_DELAY_S, confirm_window_s=confirm_window_s,
        motor_state_query=motor_state_query)
    registry.on_status_change(protection.on_status_change)
    return registry, protection, published


def status(registry) -> NodeStatus:
    return registry.get(NODE_ID).status


def test_operator_stop_reads_idle_not_tripped():
    registry, protection, published = build(trip_motor_idx=None)

    protection.on_motor_state(NODE_ID, running=False)

    assert status(registry) == NodeStatus.IDLE, status(registry)
    assert published == [], published
    print("a machine stopped by an operator reads IDLE, nothing published: PASS")


def test_restart_returns_to_healthy_from_idle():
    registry, protection, _ = build(trip_motor_idx=None)
    protection.on_motor_state(NODE_ID, running=False)
    assert status(registry) == NodeStatus.IDLE, status(registry)

    protection.on_motor_state(NODE_ID, running=True)

    # This is the whole recovery path: no acknowledge, no reset button. It
    # also guards the specific bug that IDLE would otherwise stick forever --
    # InferencePipeline's cached status never became IDLE, so a healthy score
    # after the restart would read as "no change" and never re-confirm.
    assert status(registry) == NodeStatus.HEALTHY, status(registry)
    print("restarting the machine returns it to HEALTHY with no operator action: PASS")


def test_fault_trips_after_the_delay_and_confirms():
    registry, protection, published = build(trip_motor_idx=2)

    registry.set_status(NODE_ID, NodeStatus.FAULT)
    # Still FAULT during the countdown, and nothing published yet -- the delay
    # is what stops a transient from shutting the machine down.
    assert published == [], published
    assert status(registry) == NodeStatus.FAULT, status(registry)
    snap = protection.snapshot(NODE_ID)
    assert snap["trip_in_s"] is not None and snap["trip_in_s"] > 0, snap
    print("FAULT starts a visible countdown without tripping immediately: PASS")

    time.sleep(TRIP_DELAY_S + SETTLE_S)
    assert published == [2], published
    # The trip was sent but the machine has not been observed stopping yet, so
    # it must NOT read TRIPPED. Claiming a machine is stopped while it is
    # still turning is the one thing this must never do.
    assert status(registry) == NodeStatus.FAULT, status(registry)
    print("trip published on expiry; status stays FAULT until the stop is confirmed: PASS")

    protection.on_motor_state(NODE_ID, running=False)
    assert status(registry) == NodeStatus.TRIPPED, status(registry)
    assert protection.snapshot(NODE_ID)["trip_failed"] is False
    assert protection.snapshot(NODE_ID)["tripped_at"] is not None
    # A confirmed trip always needs an operator to acknowledge it before
    # anything may move this node off TRIPPED again -- see
    # test_unacknowledged_trip_ignores_gate_flicker below for why.
    assert protection.snapshot(NODE_ID)["needs_ack"] is True
    print("gate-confirmed stop promotes FAULT -> TRIPPED, unacknowledged: PASS")


def test_unacknowledged_trip_ignores_gate_flicker_and_stays_tripped():
    """The actual race this module used to lose: this node's own gate cannot
    tell a real restart apart from cross-talk off a neighbouring motor on a
    shared rig frame (pipeline/gate.py's own accepted masking case). Before
    acknowledge_trip existed, a stray RUNNING blip right after a confirmed
    trip read as a restart (-> HEALTHY), and the matching STOPPED edge that
    followed read as an unrelated operator stop (-> IDLE) -- TRIPPED ->
    HEALTHY -> IDLE while the machine never moved. Asserted on set_status
    *attempts*: neither HEALTHY nor IDLE should even be tried while
    unacknowledged (a redundant TRIPPED attempt on the matching STOPPED
    edge is fine -- registry.py has no TRIPPED -> TRIPPED edge, so it's a
    harmless no-op, swallowed the same way any already-there status is)."""
    registry, protection, published = build(trip_motor_idx=1)
    attempted = []
    real_set_status = registry.set_status
    registry.set_status = lambda node_id, s: (attempted.append(s),
                                               real_set_status(node_id, s))[1]

    registry.set_status(NODE_ID, NodeStatus.FAULT)
    time.sleep(TRIP_DELAY_S + SETTLE_S)
    protection.on_motor_state(NODE_ID, running=False)
    assert status(registry) == NodeStatus.TRIPPED, status(registry)

    attempted.clear()
    protection.on_motor_state(NODE_ID, running=True)   # the blip
    protection.on_motor_state(NODE_ID, running=False)  # its matching edge

    assert NodeStatus.HEALTHY not in attempted, attempted
    assert NodeStatus.IDLE not in attempted, attempted
    assert status(registry) == NodeStatus.TRIPPED, status(registry)
    print("a gate blip on an unacknowledged trip writes nothing and stays TRIPPED: PASS")


def test_acknowledge_re_arms_recovery():
    registry, protection, published = build(trip_motor_idx=1)

    # Nothing to acknowledge yet.
    assert protection.acknowledge_trip(NODE_ID) is False

    registry.set_status(NODE_ID, NodeStatus.FAULT)
    time.sleep(TRIP_DELAY_S + SETTLE_S)
    protection.on_motor_state(NODE_ID, running=False)
    assert status(registry) == NodeStatus.TRIPPED, status(registry)

    # A blip before acknowledgement still changes nothing (previous test
    # covers this in detail; here just confirming TRIPPED holds).
    protection.on_motor_state(NODE_ID, running=True)
    protection.on_motor_state(NODE_ID, running=False)
    assert status(registry) == NodeStatus.TRIPPED, status(registry)

    assert protection.acknowledge_trip(NODE_ID) is True
    assert protection.snapshot(NODE_ID)["needs_ack"] is False
    # Second press finds nothing left to acknowledge.
    assert protection.acknowledge_trip(NODE_ID) is False

    # Now a genuine restart recovers normally, same as the pre-trip IDLE
    # recovery path.
    protection.on_motor_state(NODE_ID, running=True)
    assert status(registry) == NodeStatus.HEALTHY, status(registry)
    print("acknowledging a trip re-arms normal restart recovery: PASS")


def test_unconfirmed_trip_is_reported_failed_and_stays_fault():
    registry, protection, published = build(
        trip_motor_idx=1, confirm_window_s=SHORT_CONFIRM_WINDOW_S)

    registry.set_status(NODE_ID, NodeStatus.FAULT)
    # Never call on_motor_state -- simulating a rig host that didn't act, or
    # neighbouring machines shaking the frame hard enough to hold the gate
    # above its running fraction.
    time.sleep(TRIP_DELAY_S + SHORT_CONFIRM_WINDOW_S + SETTLE_S)

    assert published == [1], published
    assert status(registry) == NodeStatus.FAULT, status(registry)
    assert protection.snapshot(NODE_ID)["trip_failed"] is True, protection.snapshot(NODE_ID)
    print("a trip that never took reads FAULT + trip_failed, never TRIPPED: PASS")


def test_hold_cancels_a_pending_trip():
    registry, protection, published = build(trip_motor_idx=1)

    registry.set_status(NODE_ID, NodeStatus.FAULT)
    assert protection.hold(NODE_ID) is True
    time.sleep(TRIP_DELAY_S + SETTLE_S)

    assert published == [], published
    assert status(registry) == NodeStatus.FAULT, status(registry)
    assert protection.snapshot(NODE_ID)["trip_in_s"] is None
    # Nothing left to cancel the second time -- the REST layer turns this into
    # a 409 rather than reporting a success that stopped nothing.
    assert protection.hold(NODE_ID) is False
    print("Hold cancels the pending trip and leaves the machine running: PASS")


def test_recovering_before_the_delay_expires_abandons_the_trip():
    registry, protection, published = build(trip_motor_idx=1)

    registry.set_status(NODE_ID, NodeStatus.FAULT)
    registry.set_status(NODE_ID, NodeStatus.HEALTHY)
    time.sleep(TRIP_DELAY_S + SETTLE_S)

    assert published == [], published
    assert status(registry) == NodeStatus.HEALTHY, status(registry)
    print("a score that recovers mid-countdown abandons the trip: PASS")


def test_fault_flap_does_not_restart_or_double_the_countdown():
    registry, protection, published = build(trip_motor_idx=1)

    registry.set_status(NODE_ID, NodeStatus.FAULT)
    first = protection.snapshot(NODE_ID)["trip_in_s"]
    time.sleep(TRIP_DELAY_S / 2)
    # WARNING -> FAULT again mid-countdown. The WARNING cancels it and the
    # fresh FAULT starts a new one; what must never happen is two timers
    # racing, i.e. two published trips.
    registry.set_status(NODE_ID, NodeStatus.WARNING)
    registry.set_status(NODE_ID, NodeStatus.FAULT)
    time.sleep(TRIP_DELAY_S + SETTLE_S)

    assert first is not None
    assert published == [1], published
    print("flapping in and out of FAULT publishes exactly one trip: PASS")


def test_unarmed_node_never_trips():
    registry, protection, published = build(trip_motor_idx=None)

    registry.set_status(NODE_ID, NodeStatus.FAULT)
    time.sleep(TRIP_DELAY_S + SETTLE_S)

    assert published == [], published
    assert status(registry) == NodeStatus.FAULT, status(registry)
    snap = protection.snapshot(NODE_ID)
    assert snap["trip_in_s"] is None, snap
    assert protection.armed(NODE_ID) is False
    print("an asset with no trip output faults normally and never trips: PASS")


def test_no_publisher_still_reports_idle_but_cannot_trip():
    registry, protection, _ = build(trip_motor_idx=1, with_publisher=False)

    # armed() is False despite a motor being mapped: there's nothing to
    # publish through. This is the --mqtt-host-absent deployment, and IDLE
    # reporting must still work there because it closes a gap that predates
    # the trip feature entirely.
    assert protection.armed(NODE_ID) is False
    registry.set_status(NODE_ID, NodeStatus.FAULT)
    time.sleep(TRIP_DELAY_S + SETTLE_S)
    assert status(registry) == NodeStatus.FAULT, status(registry)

    protection.on_motor_state(NODE_ID, running=False)
    assert status(registry) == NodeStatus.IDLE, status(registry)
    print("with no publisher: no trips, but IDLE reporting still works: PASS")


def test_already_stopped_machine_confirms_instantly_via_query():
    """A machine an operator already stopped by hand (or that a prior trip
    already parked at STOPPED) before this FAULT/trip fired can never
    produce a fresh on_motor_state edge -- the edge already happened, so
    on_motor_state won't be called again and the confirm window alone would
    time out forever even though the machine really is stopped. The live
    motor_state_query closes that gap by checking the current state directly
    right when the trip publishes."""
    registry, protection, published = build(
        trip_motor_idx=1, confirm_window_s=SHORT_CONFIRM_WINDOW_S,
        motor_state_query=lambda node_id: False)

    registry.set_status(NODE_ID, NodeStatus.FAULT)
    # Never call on_motor_state -- simulating the gate having already
    # confirmed STOPPED before this trip fired, same setup as the
    # unconfirmed-trip test above, just with the query wired in.
    time.sleep(TRIP_DELAY_S + SETTLE_S)

    assert published == [1], published
    assert status(registry) == NodeStatus.TRIPPED, status(registry)
    assert protection.snapshot(NODE_ID)["trip_failed"] is False
    print("a machine already stopped before the trip fires confirms instantly via the live query: PASS")


def test_one_stop_reported_twice_is_decided_once():
    """The IDLE-vs-TRIPPED race. A single stop reaches on_motor_state from two
    threads -- the ingestion thread one frame after the gate flips, and
    _fire_trip's own re-query of that same gate. Whichever loses used to see
    awaiting_confirm already cleared, call the stop an operator's, and write
    IDLE; because the registry write happens outside the lock, that IDLE could
    land before the winner's TRIPPED and leave the node stuck IDLE (TRIPPED is
    only legal from FAULT). Same trip, different answer per run.

    Asserted on the set_status *attempts* rather than the final status, since
    whether the stray IDLE actually landed was the coin flip."""
    registry, protection, published = build(trip_motor_idx=1)
    attempted = []
    real_set_status = registry.set_status
    registry.set_status = lambda node_id, s: (attempted.append(s),
                                               real_set_status(node_id, s))[1]

    registry.set_status(NODE_ID, NodeStatus.FAULT)
    time.sleep(TRIP_DELAY_S + SETTLE_S)
    assert published == [1], published

    attempted.clear()
    protection.on_motor_state(NODE_ID, running=False)
    protection.on_motor_state(NODE_ID, running=False)

    assert attempted == [NodeStatus.TRIPPED], attempted
    assert status(registry) == NodeStatus.TRIPPED, status(registry)
    print("one stop reported by both sources is decided once, as TRIPPED: PASS")


def test_confirmation_arriving_after_the_window_still_reads_tripped():
    """The confirm window is short (3s live) next to a gate that needs
    debounce_frames of agreement at ~2fps on a bridge that can stall for
    seconds, so a real trip regularly confirms just late. Late is not absent:
    the machine we asked to stop did stop, so it reads TRIPPED. Reporting IDLE
    there would credit an operator with a stop this system performed."""
    registry, protection, published = build(
        trip_motor_idx=1, confirm_window_s=SHORT_CONFIRM_WINDOW_S)

    registry.set_status(NODE_ID, NodeStatus.FAULT)
    time.sleep(TRIP_DELAY_S + SHORT_CONFIRM_WINDOW_S + SETTLE_S)
    assert published == [1], published
    assert protection.snapshot(NODE_ID)["trip_failed"] is True
    assert status(registry) == NodeStatus.FAULT, status(registry)

    protection.on_motor_state(NODE_ID, running=False)

    assert status(registry) == NodeStatus.TRIPPED, status(registry)
    snap = protection.snapshot(NODE_ID)
    assert snap["trip_failed"] is False, snap
    assert snap["tripped_at"] is not None, snap
    print("a stop confirmed after the window closed reads TRIPPED, not IDLE: PASS")


def test_running_blip_mid_trip_does_not_erase_it():
    """Regression: motors 2/3 sharing a frame can shake it enough that a
    just-tripped motor 1 flickers back over the gate's RUNNING threshold for
    an instant (the open risk flagged in docs/MOTOR_STOP_PLAN.md) before
    settling stopped. The on_motor_state(running=True) branch used to force
    HEALTHY unconditionally on any such edge -- so that blip alone erased an
    in-flight trip, and the real stop that followed a moment later then read
    as an operator's IDLE, or was simply too late to matter. trip_pending()
    must make this edge a no-op while a trip is unresolved -- and, per
    test_unacknowledged_trip_ignores_gate_flicker, while it's resolved but
    not yet acknowledged either, since the same blip can recur any time
    after confirmation too."""
    registry, protection, published = build(trip_motor_idx=1)

    registry.set_status(NODE_ID, NodeStatus.FAULT)
    time.sleep(TRIP_DELAY_S + SETTLE_S)
    assert published == [1], published
    assert protection.trip_pending(NODE_ID) is True

    # Cross-talk blip: gate reads RUNNING again for one edge before the
    # motor actually settles stopped.
    protection.on_motor_state(NODE_ID, running=True)
    assert status(registry) == NodeStatus.FAULT, status(registry)
    assert protection.trip_pending(NODE_ID) is True
    print("a running blip mid-trip does not erase the pending trip: PASS")

    # The real stop, right after, still confirms TRIPPED. trip_pending()
    # stays True past this point too, but now for case 2 (needs_ack) rather
    # than case 1 (awaiting_confirm) -- it only goes False once acknowledged.
    protection.on_motor_state(NODE_ID, running=False)
    assert status(registry) == NodeStatus.TRIPPED, status(registry)
    assert protection.trip_pending(NODE_ID) is True
    assert protection.snapshot(NODE_ID)["needs_ack"] is True
    print("the real stop that follows still confirms TRIPPED: PASS")

    assert protection.acknowledge_trip(NODE_ID) is True
    assert protection.trip_pending(NODE_ID) is False
    print("acknowledging clears trip_pending: PASS")


def test_tripped_node_restarted_without_a_fix_trips_again():
    registry, protection, published = build(trip_motor_idx=1)

    registry.set_status(NODE_ID, NodeStatus.FAULT)
    time.sleep(TRIP_DELAY_S + SETTLE_S)
    protection.on_motor_state(NODE_ID, running=False)
    assert status(registry) == NodeStatus.TRIPPED, status(registry)

    # A restart before acknowledging does nothing -- see
    # test_unacknowledged_trip_ignores_gate_flicker for why. Acknowledge
    # first, exactly as an operator pressing the dashboard's button would.
    assert protection.acknowledge_trip(NODE_ID) is True

    # Operator restarts it without fixing anything: back to HEALTHY, then the
    # score re-confirms FAULT and it trips a second time. You cannot restart
    # your way out of a real fault.
    protection.on_motor_state(NODE_ID, running=True)
    assert status(registry) == NodeStatus.HEALTHY, status(registry)
    assert protection.snapshot(NODE_ID)["tripped_at"] is None

    registry.set_status(NODE_ID, NodeStatus.FAULT)
    time.sleep(TRIP_DELAY_S + SETTLE_S)
    assert published == [1, 1], published
    print("restarting a tripped machine that is still faulty trips it again: PASS")


if __name__ == "__main__":
    try:
        test_operator_stop_reads_idle_not_tripped()
        test_restart_returns_to_healthy_from_idle()
        test_fault_trips_after_the_delay_and_confirms()
        test_unacknowledged_trip_ignores_gate_flicker_and_stays_tripped()
        test_acknowledge_re_arms_recovery()
        test_unconfirmed_trip_is_reported_failed_and_stays_fault()
        test_hold_cancels_a_pending_trip()
        test_recovering_before_the_delay_expires_abandons_the_trip()
        test_fault_flap_does_not_restart_or_double_the_countdown()
        test_unarmed_node_never_trips()
        test_no_publisher_still_reports_idle_but_cannot_trip()
        test_already_stopped_machine_confirms_instantly_via_query()
        test_one_stop_reported_twice_is_decided_once()
        test_confirmation_arriving_after_the_window_still_reads_tripped()
        test_running_blip_mid_trip_does_not_erase_it()
        test_tripped_node_restarted_without_a_fix_trips_again()
        print("RESULT: PASS - protection ladder, IDLE/TRIPPED split, Hold and "
              "failed-trip handling all behave")
    except AssertionError as e:
        print(f"RESULT: FAIL - {e}")
        sys.exit(1)
