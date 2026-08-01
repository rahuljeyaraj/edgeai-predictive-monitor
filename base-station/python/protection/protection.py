"""Machinery protection -- the escalation ladder from a detected fault to a
physically stopped machine, per docs/MOTOR_STOP_PLAN.md.

This is protection, not control. The distinction matters and is deliberate:
a condition-monitoring system does not run the process, but it does carry a
trip output, exactly as real machinery-protection systems do (API 670). The
only command that ever leaves here is "stop motor N". Nothing in this module
can set a speed, and nothing can start a machine -- restarting is a human
action taken at the machine.

    HEALTHY -> WARNING -> FAULT --(delay)--> publish trip --(confirm)--> TRIPPED

Two jobs, and they are separated because only one of them needs MQTT:

  1. Deciding what a stopped machine's status is -- IDLE if an operator
     stopped it, TRIPPED if we did. Always on, needs nothing external.
  2. Running the trip countdown and publishing the trip. Needs a publisher,
     so it's inert when main.py has no MQTT host.

That split is why `publish_trip` is injectable and optional rather than this
module owning an MqttPublisher: a deployment with no broker still gets
correct IDLE reporting, which is a pre-existing gap this closes (see
NodeStatus.IDLE's comment in registry.py) and not part of the trip feature.

Why the trip is delayed
-----------------------
A protection trip with no time delay is a nuisance trip -- one transient
spike and the plant stops. Real protection systems delay the trip so a
momentary excursion has to persist to be believed. The delay is also what
makes the decision legible to an operator: the dashboard counts down, and
`hold()` cancels it.

Why a failed trip is not TRIPPED
--------------------------------
If the trip is published but the machine keeps turning, status stays FAULT
and `trip_failed` is set. Showing TRIPPED for a machine that is still
running would be the most dangerous lie this system could tell, so
confirmation comes from the vibration gate actually going quiet
(on_motor_state) -- never from having sent the message.

Locking
-------
`_lock` guards this module's own dicts ONLY. Registry calls are always made
after releasing it. This is not stylistic: `on_motor_state` arrives on the
ingestion thread already holding that node's registry lock, while timer
threads take `_lock` first and then need the registry lock. Doing registry
work inside `_lock` would invert the two orders and deadlock.
"""
import logging
import threading
import time
from typing import Callable, Dict, Optional

from registry import InvalidTransitionError, NodeStatus, Registry

logger = logging.getLogger(__name__)

# Long enough to read a countdown on screen and act on it, and comfortably
# longer than the status debounce that produced the FAULT in the first place.
# Real protection relays use 1-3s; this is a demonstrable system, and the
# countdown is the moment the decision becomes visible.
DEFAULT_TRIP_DELAY_S = 10.0

# How long to wait for the vibration gate to confirm the machine actually
# stopped before calling the trip failed. Needs to cover the gate's own
# debounce (a few frames) plus the mechanical spin-down of an unpowered
# stepper, which is close to instant.
DEFAULT_CONFIRM_WINDOW_S = 3.0

# The same wait, for the commissioning-time "does this output actually stop
# this machine" test (docs/UNIFIED_COMMISSIONING_PLAN.md S3.3). Longer than
# the trip's window on purpose: a failed real trip must be reported fast,
# because a faulted machine is still turning while we wait, whereas this
# runs with an operator standing at a healthy machine watching it, and
# reporting "wrong output" against a rig that was merely slow to spin down
# would send them to test a second output for no reason.
DEFAULT_MAPPING_CONFIRM_WINDOW_S = 8.0


class ProtectionError(Exception):
    """A protection action that cannot be attempted at all -- today, only a
    confirm test whose preconditions aren't met (see confirm_trip_output)."""


class ProtectionState:
    """Per-node protection bookkeeping. Plain attributes rather than a
    dataclass so the timers aren't part of any equality/repr."""

    def __init__(self):
        self.trip_deadline: Optional[float] = None   # countdown target, monotonic
        self.countdown_timer: Optional[threading.Timer] = None
        self.confirm_timer: Optional[threading.Timer] = None
        self.awaiting_confirm = False
        self.trip_failed = False
        self.tripped_at: Optional[float] = None      # wall clock, for the UI


class _MappingConfirmTest:
    """One in-flight confirm-by-stopping test (ProtectionController.
    confirm_trip_output). Resolves exactly once, from whichever of the two
    racing sources gets there first -- the gate edge, or the timeout -- so a
    gate confirmation landing as the timer fires can't report both answers."""

    def __init__(self, motor_idx: int, on_result: Callable[[bool, str], None]):
        self.motor_idx = motor_idx
        self.timer: Optional[threading.Timer] = None
        self._on_result = on_result
        self._done = False

    def finish(self, confirmed: bool, message: str) -> None:
        if self._done:
            return
        self._done = True
        try:
            self._on_result(confirmed, message)
        except Exception:
            # on_result reaches the REST/WS layer; a broadcast that fails
            # must not kill a Timer thread or the ingestion thread whose
            # gate edge resolved this.
            logger.exception("protection: confirm-test result callback failed")


class ProtectionController:
    def __init__(self, registry: Registry,
                 publish_trip: Optional[Callable[[int], None]] = None,
                 trip_delay_s: float = DEFAULT_TRIP_DELAY_S,
                 confirm_window_s: float = DEFAULT_CONFIRM_WINDOW_S,
                 motor_state_query: Optional[Callable[[str], Optional[bool]]] = None,
                 mapping_confirm_window_s: float = DEFAULT_MAPPING_CONFIRM_WINDOW_S):
        """publish_trip: called with the motor index when a countdown expires.
        None disables tripping entirely (no countdowns start) while leaving
        the IDLE/TRIPPED status reporting fully working.

        motor_state_query: node_id -> currently-confirmed running state (True/
        False), or None if unknown. See set_motor_state_query."""
        self._registry = registry
        self._publish_trip = publish_trip
        self._trip_delay_s = trip_delay_s
        self._confirm_window_s = confirm_window_s
        self._mapping_confirm_window_s = mapping_confirm_window_s
        self._motor_state_query = motor_state_query
        self._lock = threading.Lock()
        self._states: Dict[str, ProtectionState] = {}
        # node_id -> in-flight confirm test (see confirm_trip_output). Kept
        # apart from ProtectionState's awaiting_confirm, which belongs to a
        # real trip: a machine stopped by a confirm test is not TRIPPED, it
        # is stopped, and on_motor_state must go on reporting it as IDLE.
        self._confirm_tests: Dict[str, "_MappingConfirmTest"] = {}

    def set_publish_trip(self, publish_trip: Optional[Callable[[int], None]]) -> None:
        """Late-bound because main.py builds the MQTT publisher after the
        PipelineManager that already needs this controller's on_motor_state
        callback, and that startup order has its own documented constraints
        (app.state.loop must exist before ingestion starts). Passing None
        leaves IDLE/TRIPPED reporting live with tripping disabled."""
        self._publish_trip = publish_trip

    def set_motor_state_query(self, motor_state_query: Optional[Callable[[str], Optional[bool]]]) -> None:
        """Late-bound for the same reason set_publish_trip is: main.py builds
        the PipelineManager this reads from after this controller already
        exists (it's what supplies PipelineManager's own on_motor_state
        hook)."""
        self._motor_state_query = motor_state_query

    # -- introspection for the REST layer ------------------------------

    def snapshot(self, node_id: str) -> dict:
        """What api/app.py merges into this node's GET /nodes entry.
        trip_in_s counts down; None when no trip is pending."""
        with self._lock:
            state = self._states.get(node_id)
            if state is None:
                return {"trip_in_s": None, "trip_failed": False, "tripped_at": None}
            trip_in_s = None
            if state.trip_deadline is not None:
                # max(0, ...) rather than letting it go negative: the timer
                # has fired but its thread may not have run yet, and a
                # negative countdown on screen reads as a bug.
                trip_in_s = max(0.0, state.trip_deadline - time.monotonic())
            return {
                "trip_in_s": trip_in_s,
                "trip_failed": state.trip_failed,
                "tripped_at": state.tripped_at,
            }

    def armed(self, node_id: str) -> bool:
        """True when this node could actually trip: an operator has pointed it
        at a motor AND this process can publish."""
        if self._publish_trip is None:
            return False
        try:
            return self._registry.get(node_id).trip_motor_idx is not None
        except KeyError:
            return False

    # -- inputs --------------------------------------------------------

    def on_status_change(self, node_id: str, status: NodeStatus) -> None:
        """Registry.on_status_change listener. Fires synchronously on the
        ingestion thread inside that node's registry lock (see main.py's
        warning), so this must not block -- starting a Timer doesn't."""
        if status == NodeStatus.FAULT:
            self._start_countdown(node_id)
            return
        # Any other status means the fault is no longer current -- recovered,
        # paused, re-commissioned, already tripped. Cancel a pending trip
        # rather than letting a countdown started under an old status fire
        # against a machine that is no longer faulted.
        self._cancel_countdown(node_id)
        if status in (NodeStatus.HEALTHY, NodeStatus.WARNING):
            # Scoring is live again, so whatever happened last time is
            # history. This is the whole of "recovery": no acknowledge step,
            # no reset button.
            self._clear_trip_record(node_id)

    def on_motor_state(self, node_id: str, running: bool) -> None:
        """Called from the ingestion path whenever a node's confirmed
        running/stopped state changes (pipeline/manager.py). This is the only
        thing that can confirm a trip."""
        if running:
            # Started again. This is the entire recovery path -- no
            # acknowledge, no reset button. Land on HEALTHY and let the next
            # few scored frames re-diagnose, exactly as Registry.resume() does
            # coming out of a pause and for the same reason: we don't guess a
            # status, we re-measure one.
            #
            # This write is not optional. Without it the node stays IDLE
            # forever: InferencePipeline caches its own last confirmed status,
            # which never became IDLE, so the first healthy score after a
            # restart compares equal to that cache, reads as "no change" and
            # never calls set_status. pipeline/manager.py resets that cache on
            # the same edge; both halves are needed. Same trap as
            # InferencePipeline.reset_to_healthy()'s docstring describes for
            # pause/resume.
            self._set_status(node_id, NodeStatus.HEALTHY)
            return
        # Stopped -- and if a confirm test asked for exactly this, that is
        # the mapping proven (confirm_trip_output). Resolved before the
        # status work below and independently of it: the machine is stopped
        # by our own stop with no fault in play, so it still reads IDLE.
        confirm_test = self._take_confirm_test(node_id)
        if confirm_test is not None:
            logger.info("protection: trip output %d confirmed against %s",
                         confirm_test.motor_idx, node_id)
            confirm_test.finish(
                True, f"output {confirm_test.motor_idx} stopped this machine")

        # Whether that reads as TRIPPED or IDLE depends entirely on
        # whether we asked for it.
        with self._lock:
            state = self._states.get(node_id)
            was_ours = bool(state and state.awaiting_confirm)
            if was_ours:
                state.awaiting_confirm = False
                state.trip_failed = False
                state.tripped_at = time.time()
                self._cancel_timer_locked(state, "confirm_timer")

        target = NodeStatus.TRIPPED if was_ours else NodeStatus.IDLE
        self._set_status(node_id, target)

    def confirm_trip_output(self, node_id: str, motor_idx: int,
                             on_result: Callable[[bool, str], None]) -> None:
        """Proves a sensor -> output mapping by using it: send this output a
        stop, and watch THIS node's gate (docs/UNIFIED_COMMISSIONING_PLAN.md
        S3.3). Returns as soon as the stop is published; the answer arrives
        later on `on_result(confirmed, message)`, from a gate edge or from
        this test's own timeout.

        Never asks the operator which motor -- proves it. Beyond deleting a
        dropdown, this is a live end-to-end test of the entire trip path
        (MQTT, topic, rig subscription, motor stop, gate confirmation) run at
        commissioning time instead of during the first real fault.

        The safety invariant in this module's docstring is untouched. The
        only command sent is stop -- the identical bytes, through the
        identical publish path, that a real trip sends. Nothing here starts
        a machine: the operator does that by hand, at the machine, both
        before this test and after it.

        Raises ProtectionError when the test cannot be meaningfully run,
        rather than running it and reporting a result it hasn't earned. The
        gate-confirmed-RUNNING precondition is the important one: a machine
        that is already stopped would "confirm" any output at all."""
        if self._publish_trip is None:
            raise ProtectionError(
                "no trip path from this base station (MQTT is not configured), so a "
                "trip output cannot be tested")
        if self._motor_state_query is None:
            raise ProtectionError("no live running/stopped state for this node")
        running = self._motor_state_query(node_id)
        if running is not True:
            raise ProtectionError(
                "start the machine and wait for it to read as running before testing "
                "the trip output -- a machine that is already stopped would appear to "
                "confirm whichever output we tried")

        with self._lock:
            if node_id in self._confirm_tests:
                raise ProtectionError("a trip-output test is already running for this node")
            if self._states.get(node_id) and self._states[node_id].awaiting_confirm:
                # A real trip is mid-flight against this very machine.
                raise ProtectionError("a real trip is already stopping this machine")
            test = _MappingConfirmTest(motor_idx, on_result)
            test.timer = threading.Timer(self._mapping_confirm_window_s,
                                          self._confirm_test_timeout, args=(node_id,))
            test.timer.daemon = True
            self._confirm_tests[node_id] = test

        logger.info("protection: testing trip output %d against %s -- publishing a stop",
                     motor_idx, node_id)
        try:
            self._publish_trip(motor_idx)
        except Exception:
            logger.exception("protection: publishing the confirm-test stop for %s failed",
                              node_id)
            resolved = self._take_confirm_test(node_id)
            if resolved is not None:
                resolved.finish(False, "the stop could not be published to the rig")
            return
        test.timer.start()
        # The gate may already read stopped by the time we get here only if
        # something else stopped the machine in the last instant -- but the
        # precondition above just saw it running, so unlike _fire_trip there
        # is deliberately no immediate re-query here: taking the current gate
        # state as the answer would confirm a mapping on the strength of a
        # stop this test didn't cause.

    def _take_confirm_test(self, node_id: str) -> Optional["_MappingConfirmTest"]:
        """Pops a node's in-flight test and cancels its timer, under the
        lock. None if there wasn't one (already resolved, or never started).
        Callers invoke finish() outside the lock -- on_result reaches the
        REST/WS layer, which is exactly the kind of work this module's
        docstring keeps out of _lock."""
        with self._lock:
            test = self._confirm_tests.pop(node_id, None)
            if test is not None and test.timer is not None:
                test.timer.cancel()
            return test

    def cancel_confirm_trip_output(self, node_id: str) -> bool:
        """Abandons an in-flight test without waiting for its window, e.g.
        the operator cancelled setup. Reports nothing back -- the caller is
        the one giving up. The machine stays stopped: this cannot restart
        anything, and would not if it could."""
        return self._take_confirm_test(node_id) is not None

    def _confirm_test_timeout(self, node_id: str) -> None:
        test = self._take_confirm_test(node_id)
        if test is None:
            return
        logger.warning("protection: trip output %d did NOT stop %s within %.0fs",
                        test.motor_idx, node_id, self._mapping_confirm_window_s)
        test.finish(
            False,
            f"the machine kept running, so output {test.motor_idx} isn't the one that "
            "stops it (or the trip never reached the rig)")

    def hold(self, node_id: str) -> bool:
        """Operator override: cancel a pending trip. Returns whether there was
        one to cancel, so the REST layer can 409 an already-expired countdown
        instead of silently reporting success.

        This is the operator authority the long-dead `auto_cutoff_enabled`
        registry field was originally meant to express (registry.py's
        trip_motor_idx comment). It does not disarm protection permanently --
        a fresh FAULT transition starts a new countdown."""
        return self._cancel_countdown(node_id)

    # -- internals -----------------------------------------------------

    def _state_for(self, node_id: str) -> ProtectionState:
        state = self._states.get(node_id)
        if state is None:
            state = ProtectionState()
            self._states[node_id] = state
        return state

    def _cancel_timer_locked(self, state: ProtectionState, attr: str) -> None:
        timer = getattr(state, attr)
        if timer is not None:
            timer.cancel()
            setattr(state, attr, None)

    def _start_countdown(self, node_id: str) -> None:
        if not self.armed(node_id):
            # No trip output wired, or nothing to publish through. A fault on
            # an unarmed asset is still a fault -- it just has no actuator.
            return
        with self._lock:
            state = self._state_for(node_id)
            if state.countdown_timer is not None or state.awaiting_confirm:
                # Already counting down, or already tripped and waiting for
                # the machine to go quiet. Re-entering FAULT (e.g. a
                # WARNING->FAULT flap) must not restart or double the trip.
                return
            state.trip_deadline = time.monotonic() + self._trip_delay_s
            state.trip_failed = False
            timer = threading.Timer(self._trip_delay_s, self._fire_trip, args=(node_id,))
            timer.daemon = True
            state.countdown_timer = timer
            timer.start()
        logger.info("protection: trip countdown started for %s (%.1fs)",
                     node_id, self._trip_delay_s)

    def _cancel_countdown(self, node_id: str) -> bool:
        with self._lock:
            state = self._states.get(node_id)
            if state is None or state.countdown_timer is None:
                return False
            self._cancel_timer_locked(state, "countdown_timer")
            state.trip_deadline = None
        logger.info("protection: trip countdown cancelled for %s", node_id)
        return True

    def _clear_trip_record(self, node_id: str) -> None:
        with self._lock:
            state = self._states.get(node_id)
            if state is None:
                return
            state.trip_failed = False
            state.tripped_at = None
            state.awaiting_confirm = False
            self._cancel_timer_locked(state, "confirm_timer")

    def _fire_trip(self, node_id: str) -> None:
        """Countdown expired -- on a timer thread, so no registry lock is
        held and blocking here is safe."""
        try:
            motor_idx = self._registry.get(node_id).trip_motor_idx
        except KeyError:
            return  # decommissioned while counting down
        if motor_idx is None:
            return  # disarmed while counting down

        with self._lock:
            state = self._state_for(node_id)
            state.countdown_timer = None
            state.trip_deadline = None
            state.awaiting_confirm = True
            confirm = threading.Timer(self._confirm_window_s, self._confirm_timeout,
                                       args=(node_id,))
            confirm.daemon = True
            state.confirm_timer = confirm

        logger.warning("protection: TRIPPING %s -- stopping motor %d", node_id, motor_idx)
        try:
            self._publish_trip(motor_idx)
        except Exception:
            # A publish that raised is a failed trip, not a crashed timer
            # thread. Report it the same way an unconfirmed one is reported.
            logger.exception("protection: publishing the trip for %s failed", node_id)
            with self._lock:
                state = self._state_for(node_id)
                state.awaiting_confirm = False
                state.trip_failed = True
                self._cancel_timer_locked(state, "confirm_timer")
            return

        confirm.start()
        # The gate may already have confirmed STOPPED before this trip fired
        # -- e.g. an operator already stopped the machine by hand, or a
        # previous trip attempt already parked it there. on_motor_state only
        # fires on a fresh edge, so if the state was already STOPPED nothing
        # will ever call it again and the confirm window above would time
        # out for a machine that is, in fact, stopped. Ask the live gate
        # state directly to catch that case immediately instead.
        if self._motor_state_query is not None and self._motor_state_query(node_id) is False:
            self.on_motor_state(node_id, running=False)

    def _confirm_timeout(self, node_id: str) -> None:
        """The trip went out but the machine never went quiet."""
        with self._lock:
            state = self._states.get(node_id)
            if state is None or not state.awaiting_confirm:
                return  # confirmed in the meantime
            state.awaiting_confirm = False
            state.confirm_timer = None
            state.trip_failed = True
        logger.error(
            "protection: trip for %s was NOT confirmed -- the machine is still "
            "vibrating. Status stays FAULT; check the rig host's trip listener, "
            "and whether neighbouring machines are shaking this sensor's frame "
            "hard enough to hold the gate above --gate-running-fraction.",
            node_id)

    def _set_status(self, node_id: str, status: NodeStatus) -> None:
        """Never called with self._lock held -- see the module docstring."""
        try:
            self._registry.set_status(node_id, status)
        except InvalidTransitionError:
            # Expected and harmless: a node that's PAUSED, mid-commissioning,
            # or already in the target status has no legal edge to it. The
            # motor genuinely is stopped either way; there's just nowhere to
            # record it right now.
            pass
        except KeyError:
            pass  # decommissioned underneath us
