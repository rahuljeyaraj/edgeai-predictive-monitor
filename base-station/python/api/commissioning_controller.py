"""CommissioningController -- API-layer lifecycle shim between REST
control actions and the commissioning workflow (pipeline/commissioning.py,
S3.5/M7). Moved out of the old rest.py (removed by the FastAPI migration,
see docs/CLAUDE_CODE_START_PROMPT_fastapi_migration.md Step 4) since it
was never part of that module's HTTP-framework-specific code -- it's
framework-agnostic glue that api/app.py's route handlers also use.
"""
from typing import Callable, Dict, List, Optional, Tuple

from registry import Registry
from gate import MotorStateGate
from commissioning import CommissioningError, CommissioningSession


class CommissioningController:
    """Owns at most one active CommissioningSession per node_id, so a
    REST start/stop call and the live frame feed (fed by whatever owns
    the ingestion loop -- Pipeline Manager or main.py, once wired) can
    find each other by node_id. Not a pipeline stage itself -- a lookup +
    lifecycle shim between the REST layer and the commissioning workflow
    (pipeline/commissioning.py, S3.5/M7)."""

    def __init__(self, registry: Registry, models_dir: str,
                 gate_factory: Callable[[str], MotorStateGate], min_frames: int = 50,
                 epochs: int = 300, captures_dir: Optional[str] = None):
        self._registry = registry
        self._models_dir = models_dir
        self._gate_factory = gate_factory
        self._min_frames = min_frames
        self._epochs = epochs
        self._captures_dir = captures_dir
        self._sessions: Dict[str, CommissioningSession] = {}

    @property
    def min_frames(self) -> int:
        """What one operating condition needs before it can be closed --
        read by api/setup_controller.py so setup's step 4 doesn't keep its
        own second copy of the number."""
        return self._min_frames

    def start(self, node_id: str) -> None:
        if node_id in self._sessions:
            raise CommissioningError(f"commissioning already in progress for {node_id!r}")
        session = CommissioningSession(self._registry, self._models_dir, node_id,
                                        self._gate_factory(node_id), self._min_frames,
                                        self._epochs, captures_dir=self._captures_dir)
        session.start()
        self._sessions[node_id] = session

    def start_condition(self, node_id: str, name: str) -> None:
        """Closes the operating condition currently collecting and opens a
        named new one (docs/UNIFIED_COMMISSIONING_PLAN.md S2.3). Same
        leave-the-session-in-place-on-error contract as stop_collecting()
        below: too few frames raises without closing anything, so the
        operator can keep the machine running and try again."""
        session = self._sessions.get(node_id)
        if session is None:
            raise CommissioningError(f"no active commissioning session for {node_id!r}")
        session.start_condition(name)

    def condition_counts(self, node_id: str) -> Optional[List[Tuple[str, int]]]:
        """Per-condition (name, frames) for a node's active session, or None
        if it has none -- setup's step 4 shows one live counter per
        condition, which progress() below (a single total) can't express."""
        session = self._sessions.get(node_id)
        if session is None:
            return None
        return session.condition_counts

    def feed_frame(self, frame) -> None:
        """Called by whatever owns the live frame stream for every
        incoming frame -- a no-op for any node without an active
        session, so it's safe to call unconditionally for every frame
        regardless of whether that node is currently being commissioned.

        A session now stays in self._sessions through the whole
        COMMISSIONING_TRAINING window too (so run_training() can find it,
        see stop_collecting()'s comment below), so frames keep arriving
        here for a node that has already moved past COMMISSIONING_COLLECTING.
        CommissioningSession.feed_frame() raises CommissioningError for
        that status mismatch -- swallowed here rather than left to
        propagate, since an uncaught exception here would otherwise crash
        whatever ingestion thread is calling this unconditionally per
        frame (main.py's on_frame has no try/except of its own around this
        call)."""
        session = self._sessions.get(frame.node_id)
        if session is not None:
            try:
                session.feed_frame(frame)
            except CommissioningError:
                pass

    def stop_collecting(self, node_id: str) -> None:
        session = self._sessions.get(node_id)
        if session is None:
            raise CommissioningError(f"no active commissioning session for {node_id!r}")
        # Session stays in self._sessions either way -- on success it's
        # needed for the follow-up run_training() call; on CommissioningError
        # (too few frames collected), pipeline/commissioning.py's
        # stop_collecting() leaves the session active by design -- S3.5:
        # "the technician can keep the motor running and feed more frames,
        # then call stop_collecting() again" -- so the session must stay
        # reachable by feed_frame() for that retry to work.
        session.stop_collecting()

    def progress(self, node_id: str) -> Optional[Tuple[int, int]]:
        """(collected, min_frames) for a node's active session, or None if
        it has none -- lets the dashboard's train icon (see frontend/app.js)
        unlock once enough frames are in, without the frontend duplicating
        CommissioningSession's own frame-counting."""
        session = self._sessions.get(node_id)
        if session is None:
            return None
        return session.collected_count, self._min_frames

    def discard(self, node_id: str) -> None:
        """Drops node_id's session, if any, with no side effects on the
        registry or the collected batch itself -- used when a node is
        removed (bin icon, frontend/app.js) while still mid-commissioning,
        so its session doesn't linger in self._sessions forever (feed_frame
        would keep swallowing CommissioningError for it otherwise, per its
        own docstring above)."""
        self._sessions.pop(node_id, None)

    def run_training(self, node_id: str,
                      on_epoch: Optional[Callable[[int, int], None]] = None) -> str:
        """Runs the (slow, fixed-epoch) training step for a node already
        past stop_collecting() -- split out from stop_collecting() so the
        caller (api/app.py) can run this off the request thread and stream
        on_epoch progress over /ws in the meantime (dashboard redesign S6).
        Only drops the session on success -- a mid-training failure leaves
        the node in COMMISSIONING_TRAINING with its session still present,
        since there's no established retry path for that case yet (unlike
        stop_collecting()'s too-few-frames retry) and silently discarding
        the session would strand the node with no way to retry train()."""
        session = self._sessions.get(node_id)
        if session is None:
            raise CommissioningError(f"no active commissioning session for {node_id!r}")
        model_path = session.train(on_epoch=on_epoch)
        del self._sessions[node_id]
        return model_path
