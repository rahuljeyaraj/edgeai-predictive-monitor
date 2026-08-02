"""SetupController -- the one guided flow that commissions an asset, per
docs/UNIFIED_COMMISSIONING_PLAN.md.

Before this there were four flows, in four places, with no sign that they
were related or what order they went in -- even though the order matters:
without a stopped baseline the gate can barely tell running from stopped, so
a commissioning batch collected before one was calibrated against a weak
gate.

    1 Name & class -> 2 Off -> 3 Running conditions -> 4 Train
      -> 5 Trip output -> 6 Done

Sequences the existing sessions; does not merge the modules (S2.1).
StoppedBaselineSession and CommissioningSession stay independent and
untouched, and this owns only step order and step state. That respects
pipeline/stopped_baseline.py's docstring, which argues the two must not be
one flow because it would mean "a stop/start in the middle of collecting a
training batch" -- an objection about INTERLEAVING them. This ORDERS them:
off first, then on, with one machine state change at a boundary the operator
performs anyway. Every property that docstring protects stays true, in
particular that a baseline can still be recaptured on its own without
invalidating the model or forcing a retrain -- that is just this same step
re-entered by itself.

Trip output sits LAST, after Train, and that is a correction. It was step 2
originally, on the argument that its test ends with the machine stopped --
exactly the state the Off step needs -- so the operator switched the machine
off once rather than twice. That saving was never real. The test refuses to
run unless the gate reports RUNNING, the gate cannot answer at all until a
model exists (MotorPipeline.motor_running returns None while _inference is
None), and the model is not fitted until Train. So at step 2 the test could
only ever 409, nothing was published, the machine never stopped, and the
operator switched it off by hand for the Off step regardless. The step's
early position bought an operator action it did not actually save, at the
cost of making its own test unrunnable on every fresh asset.

Placed after Train, every precondition it needs is in place: a model, a
stopped baseline, a running baseline, and a machine the operator has just
been running for the conditions step. See docs/TRIP_OUTPUT_OPEN_ISSUES.md S1.

State is in memory only (S6). A dashboard restart mid-setup restarts the
current step. Half-collected batches are deliberately not persisted -- a
batch resumed across a restart is worse data than a fresh one.
"""
import logging
import threading
from typing import Callable, Dict, List, Optional

from registry import InvalidTransitionError, NodeNotFoundError, Registry
from commissioning import CommissioningError

logger = logging.getLogger(__name__)


class SetupError(Exception):
    """A setup action that can't be taken from where the flow currently is
    (wrong step, unmet prerequisite, nothing in progress)."""


# Step ids, in order. The frontend renders these; it does not define them.
STEP_NAME = "name"
STEP_TRIP_OUTPUT = "trip_output"
STEP_STOPPED = "stopped"
STEP_CONDITIONS = "conditions"
STEP_TRAIN = "train"
STEP_DONE = "done"

STEPS = (STEP_NAME, STEP_STOPPED, STEP_CONDITIONS, STEP_TRAIN,
         STEP_TRIP_OUTPUT, STEP_DONE)

# The only skippable step (S2.2). An asset with no trip output wired must
# not be blocked -- most monitored points have no actuator at all. Extra
# CONDITIONS beyond the first are optional too, but that's expressed by
# simply not adding them, not by skipping the step.
SKIPPABLE_STEPS = frozenset({STEP_TRIP_OUTPUT})


class _SetupState:
    """One node's in-flight setup. Plain attributes, not a dataclass: this
    is mutable working state, never compared or serialized as a whole."""

    def __init__(self, step: str):
        self.step = step
        self.skipped: set = set()
        # Last error for the CURRENT step only, cleared on every successful
        # move -- errors surface inline on their own step with that step
        # still open for a retry (S5.1), so a stale one from three steps ago
        # has nowhere to be shown and no business surviving.
        self.error: Optional[str] = None
        self.training_error: Optional[str] = None


class SetupController:
    """Owns at most one in-flight setup per node, driving the existing
    controllers. Same API-layer lifecycle-shim role CommissioningController
    plays for commissioning -- one level up, over all of them."""

    def __init__(self, registry: Registry, commissioning, stopped_baseline=None,
                 protection=None):
        self._registry = registry
        self._commissioning = commissioning
        self._stopped_baseline = stopped_baseline
        self._protection = protection
        self._lock = threading.Lock()
        self._sessions: Dict[str, _SetupState] = {}
        self._listeners: List[Callable[[str, dict], None]] = []

    def on_change(self, callback: Callable[[str, dict], None]) -> None:
        """Registers `callback(node_id, snapshot)`, fired after every step
        change. Same centralization argument as CaptureController.
        on_state_change: a step can also complete from outside a REST
        handler (training finishing on its background thread), so the
        broadcast belongs here rather than in each route."""
        self._listeners.append(callback)

    def _notify(self, node_id: str) -> None:
        snapshot = self.snapshot(node_id)
        for callback in self._listeners:
            callback(node_id, snapshot)

    # -- introspection -------------------------------------------------

    def progress(self, node_id: str) -> Optional[dict]:
        """The compact form that rides GET /nodes, mirroring how
        commissioning_progress and capture_progress already do (S7) -- just
        enough for the tile's one button to read "Setup - step 4 of 6"
        without a second fetch. None when no setup is in flight, the same
        "absent means nothing to show" contract those two use."""
        state = self._sessions.get(node_id)
        if state is None:
            return None
        return {"step": state.step, "index": STEPS.index(state.step) + 1,
                "total": len(STEPS)}

    def snapshot(self, node_id: str) -> Optional[dict]:
        """Everything the drawer renders: the current step, per-step state,
        live counters and the last error.

        Per-step state is DERIVED from the registry and the live sessions on
        every call, never cached here -- the registry is the single source of
        truth for what a node actually has, and a cached copy would go stale
        the moment a step was re-entered on its own (which S2.1 requires
        keep working)."""
        state = self._sessions.get(node_id)
        if state is None:
            return None
        try:
            entry = self._registry.get(node_id)
        except NodeNotFoundError:
            return None
        return {
            "node_id": node_id,
            "step": state.step,
            "index": STEPS.index(state.step) + 1,
            "total": len(STEPS),
            "steps": [self._step_dict(entry, state, step) for step in STEPS],
            "error": state.error,
        }

    def _step_dict(self, entry, state: _SetupState, step: str) -> dict:
        return {
            "id": step,
            "complete": self._is_complete(entry, state, step),
            "skipped": step in state.skipped,
            "skippable": step in SKIPPABLE_STEPS,
            **self._step_detail(entry, state, step),
        }

    def _step_detail(self, entry, state: _SetupState, step: str) -> dict:
        node_id = entry.node_id
        if step == STEP_NAME:
            return {"device_name": entry.device_name, "device_type": entry.device_type,
                    # What "unset" looks like for the nickname: registry.py's
                    # add() defaults device_name to the raw node_id, so
                    # equality with it IS the unset signal (the same test
                    # frontend/app.js's row already makes).
                    "has_nickname": entry.device_name != node_id}
        if step == STEP_TRIP_OUTPUT:
            return {"trip_motor_idx": entry.trip_motor_idx,
                    "confirmed_at": entry.trip_motor_confirmed_at}
        if step == STEP_STOPPED:
            progress = (self._stopped_baseline.progress(node_id)
                        if self._stopped_baseline is not None else None)
            return {"measured": entry.stopped_energy_ref is not None,
                    "progress": ({"collected": progress[0], "min_frames": progress[1]}
                                  if progress else None)}
        if step == STEP_CONDITIONS:
            counts = self._commissioning.condition_counts(node_id)
            return {"min_frames": self._commissioning.min_frames,
                    "collecting": counts is not None,
                    "conditions": ([{"name": name, "frames": frames}
                                     for name, frames in counts] if counts else []),
                    # What a previous run trained across, so a re-run shows
                    # the conditions this asset's live model actually covers
                    # rather than an empty list.
                    "trained_conditions": entry.operating_conditions or []}
        if step == STEP_TRAIN:
            return {"model_path": entry.model_path, "error": state.training_error}
        return {}

    def _is_complete(self, entry, state: _SetupState, step: str) -> bool:
        if step in state.skipped:
            return True
        # Everything before the current step has been passed; everything from
        # it on is judged on what the node actually has. Both halves matter:
        # the first is what keeps a completed step showing as done after the
        # operator moved past it, the second is what lets a re-run of setup
        # open straight onto the first thing genuinely missing.
        if STEPS.index(step) < STEPS.index(state.step):
            return True
        if step == STEP_NAME:
            return bool(entry.device_type) and entry.device_name != entry.node_id
        if step == STEP_TRIP_OUTPUT:
            return entry.trip_motor_idx is not None
        if step == STEP_STOPPED:
            return entry.stopped_energy_ref is not None
        if step == STEP_CONDITIONS:
            counts = self._commissioning.condition_counts(entry.node_id)
            if counts is not None:
                return any(frames >= self._commissioning.min_frames for _, frames in counts)
            # No live session: fall back to what the node's current model was
            # actually trained across. Without this, re-running setup on a
            # fully commissioned asset would always stop here and demand a
            # fresh collection -- the exact "walk back through steps you
            # already passed" that start()'s jump-to-the-first-gap exists to
            # avoid.
            return bool(entry.operating_conditions)
        if step == STEP_TRAIN:
            return entry.model_path is not None
        return False

    # -- flow ----------------------------------------------------------

    def start(self, node_id: str, step: Optional[str] = None) -> dict:
        """Enters setup, or re-enters it (`Re-run setup`). Opens on the
        first step this asset hasn't actually satisfied, so a fresh node
        starts at step 1 while a live asset re-running setup doesn't have to
        walk back through four steps it already passed.

        `step` jumps straight to one instead (S10 Q2's resolution: allow
        jumping). Recapturing a stopped baseline must not force a retrain --
        that is an existing invariant of stopped_baseline.py, and forcing the
        whole flow would break it."""
        entry = self._registry.get(node_id)  # raises NodeNotFoundError
        if step is not None and step not in STEPS:
            raise SetupError(f"unknown setup step {step!r}")
        with self._lock:
            state = _SetupState(step or STEP_NAME)
            if step is None:
                state.step = self._first_incomplete(entry, state)
            self._sessions[node_id] = state
        self._notify(node_id)
        return self.snapshot(node_id)

    def _first_incomplete(self, entry, state: _SetupState) -> str:
        for step in STEPS:
            if not self._is_complete(entry, state, step):
                return step
        return STEP_DONE

    def _require(self, node_id: str) -> _SetupState:
        state = self._sessions.get(node_id)
        if state is None:
            raise SetupError(f"no setup in progress for {node_id!r}")
        return state

    def _move_to(self, state: _SetupState, step: str) -> None:
        state.step = step
        state.error = None

    def _next_step(self, step: str) -> str:
        return STEPS[min(STEPS.index(step) + 1, len(STEPS) - 1)]

    def advance(self, node_id: str, device_name: Optional[str] = None,
                 device_type: Optional[str] = None) -> dict:
        """Completes the current step and moves to the next. Raises
        SetupError with an operator-readable reason (and leaves the step
        exactly where it was, still open for a retry) when the step's own
        precondition isn't met."""
        state = self._require(node_id)
        step = state.step
        try:
            if step == STEP_NAME:
                self._complete_name(node_id, device_name, device_type)
            elif step == STEP_TRIP_OUTPUT:
                self._complete_trip_output(node_id)
            elif step == STEP_STOPPED:
                self._complete_stopped(node_id)
            elif step == STEP_CONDITIONS:
                self._complete_conditions(node_id)
            elif step == STEP_TRAIN:
                raise SetupError(
                    "training finishes on its own -- it moves to Done when the model "
                    "is fitted")
            elif step == STEP_DONE:
                raise SetupError("setup is already finished for this asset")
        except SetupError as e:
            state.error = str(e)
            self._notify(node_id)
            raise
        self._move_to(state, self._next_step(step))
        self._notify(node_id)
        return self.snapshot(node_id)

    def _complete_name(self, node_id: str, device_name: Optional[str],
                        device_type: Optional[str]) -> None:
        """Both fields mandatory, no skip (S2.2.1) -- a new constraint;
        commissioning never required either before.

        The nickname's default is the raw node_id, and that string is what
        every Telegram alert and the trip banner print: "Tripped --
        esp32-a4cf12 at 14:22" is the wrong thing to read during a trip. The
        asset class is what recordings and the classifier are grouped by, and
        making it optional would mean a conditional branch through the rest
        of setup -- step 4 would silently not save its recordings, producing
        a state nobody notices until they try to train a classifier weeks
        later."""
        name = (device_name or "").strip()
        # Lowercased for the same reason frontend/app.js's commitDeviceType()
        # does it: asset class is the key that groups captures into one
        # training set per Edge Impulse project, so "Conveyor" and "conveyor"
        # being two values would silently fragment one machine's data across
        # two models.
        asset_class = (device_type or "").strip().lower()
        if not name:
            raise SetupError("this asset needs a name")
        if name == node_id:
            raise SetupError(
                "give this asset a name of its own -- the node id is what alerts and "
                "the trip banner fall back to, and it doesn't say which machine this is")
        if not asset_class:
            raise SetupError("this asset needs a class")
        self._registry.rename(node_id, name)
        self._registry.set_device_type(node_id, asset_class)

    def _complete_trip_output(self, node_id: str) -> None:
        if self._registry.get(node_id).trip_motor_idx is None:
            raise SetupError(
                "pick and test a trip output, or skip this step if this asset has none "
                "wired")

    def _complete_stopped(self, node_id: str) -> None:
        if self._registry.get(node_id).stopped_energy_ref is None:
            raise SetupError(
                "measure this asset with its machine off first -- without it the gate "
                "can barely tell running from stopped")

    def _complete_conditions(self, node_id: str) -> None:
        """Freezes the collected conditions and hands over to training. The
        caller (api/app.py) starts the training thread, exactly as it already
        does for POST /commission/stop -- this doesn't grow a second training
        path of its own."""
        try:
            self._commissioning.stop_collecting(node_id)
        except (CommissioningError, InvalidTransitionError) as e:
            raise SetupError(str(e)) from e

    def add_condition(self, node_id: str, name: str) -> dict:
        """Starts collecting a named operating condition (S2.3), beginning
        the commissioning session on the first call. Closing the previous
        condition is part of opening the next one -- see
        CommissioningSession.start_condition."""
        state = self._require(node_id)
        if state.step != STEP_CONDITIONS:
            raise SetupError("conditions can only be collected during that step")
        try:
            if self._commissioning.condition_counts(node_id) is None:
                self._commissioning.start(node_id)
            self._commissioning.start_condition(node_id, name)
        except (CommissioningError, InvalidTransitionError) as e:
            state.error = str(e)
            self._notify(node_id)
            raise SetupError(str(e)) from e
        state.error = None
        self._notify(node_id)
        return self.snapshot(node_id)

    def skip(self, node_id: str) -> dict:
        state = self._require(node_id)
        if state.step not in SKIPPABLE_STEPS:
            raise SetupError(f"the {state.step!r} step can't be skipped")
        state.skipped.add(state.step)
        self._move_to(state, self._next_step(state.step))
        self._notify(node_id)
        return self.snapshot(node_id)

    def finish_training(self, node_id: str, error: Optional[str] = None) -> None:
        """Called by whoever ran the training thread (api/app.py). On
        success the flow moves on to whatever follows Train; on failure it
        stays on the training step carrying the reason, since a node stuck
        mid-training with no visible explanation is the state this whole plan
        exists to remove.

        Derived from STEPS rather than jumping straight to Done: Train stopped
        being the last real step when Trip output moved after it, and a
        hardcoded Done here would have silently skipped it."""
        state = self._sessions.get(node_id)
        if state is None:
            return  # setup was cancelled while training ran -- the model still landed
        state.training_error = error
        if error is None:
            self._move_to(state, self._next_step(STEP_TRAIN))
        self._notify(node_id)

    def cancel(self, node_id: str) -> None:
        """Aborts setup and whichever sub-session is live. Never touches
        anything already calibrated: a cancelled setup leaves the asset
        exactly as it was, including a stopped baseline captured earlier in
        this same run (that step stands on its own -- see the module
        docstring)."""
        state = self._sessions.pop(node_id, None)
        if state is None:
            return
        if self._stopped_baseline is not None:
            self._stopped_baseline.cancel(node_id)
        if self._protection is not None:
            self._protection.cancel_confirm_trip_output(node_id)
        if self._commissioning.condition_counts(node_id) is not None:
            self._commissioning.discard(node_id)
            try:
                self._registry.cancel_commissioning(node_id)
            except InvalidTransitionError:
                # Already past collecting (training started between the
                # operator pressing Cancel and this running) -- the model is
                # on its way regardless, and there's no thread to call back.
                logger.info("setup cancel for %s: commissioning already past collecting",
                             node_id)
        self._notify(node_id)

    def discard(self, node_id: str) -> None:
        """Drops a node's setup with no side effects -- used when the node is
        decommissioned mid-setup, same role as CommissioningController.
        discard()."""
        self._sessions.pop(node_id, None)
