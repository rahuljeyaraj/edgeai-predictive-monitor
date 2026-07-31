"""StoppedBaselineController -- API-layer lifecycle shim between REST
control actions and the stopped-baseline capture workflow
(pipeline/stopped_baseline.py), mirroring CommissioningController's and
CaptureController's role for their own workflows.

Sessions are one-shot per capture (created on start, dropped on stop or
cancel) rather than long-lived per node like CaptureController's: unlike a
data capture, which a node does many times over its life, a baseline is
usually captured once and then only re-captured when the sensor is
re-mounted or the machine changes -- there's nothing worth keeping warm in
between, and a lingering session would keep feed_frame() collecting frames
for a machine the operator has already switched back on.
"""
from typing import Callable, Dict, Optional, Tuple

from registry import Registry
from stopped_baseline import (DEFAULT_MIN_FRAMES, StoppedBaselineError,
                               StoppedBaselineSession)


class StoppedBaselineController:
    def __init__(self, registry: Registry, min_frames: int = DEFAULT_MIN_FRAMES):
        self._registry = registry
        self._min_frames = min_frames
        self._sessions: Dict[str, StoppedBaselineSession] = {}
        self._listeners: list = []

    def on_state_change(self, callback: Callable[[str, str, int, int], None]) -> None:
        """Registers `callback(node_id, state, collected, min_frames)`,
        fired after every state change this controller causes. Same
        centralization argument as CaptureController.on_state_change: the
        frame-count progress that drives the dashboard's own "N/30
        collected" readout advances from the ingestion thread
        (feed_frame()), not from any REST handler, so route handlers can't
        be the ones broadcasting it."""
        self._listeners.append(callback)

    def _notify(self, node_id: str, state: str, collected: int) -> None:
        for callback in self._listeners:
            callback(node_id, state, collected, self._min_frames)

    def start(self, node_id: str) -> None:
        if node_id in self._sessions:
            raise StoppedBaselineError(
                f"stopped-baseline capture already running for {node_id!r}")
        session = StoppedBaselineSession(self._registry, node_id, self._min_frames)
        session.start()  # raises NodeNotFoundError for an unknown node
        self._sessions[node_id] = session
        self._notify(node_id, "collecting", 0)

    def feed_frame(self, frame) -> None:
        """Called unconditionally for every incoming frame (main.py's
        on_frame), same contract as the other two controllers' feed_frame:
        a no-op for any node without an active capture.

        Notifies on every collected frame rather than only on transitions,
        unlike CaptureController -- the whole UI for this is a progress
        count an operator is watching while standing next to a machine they
        just switched off, so the count *is* the state worth pushing."""
        session = self._sessions.get(frame.node_id)
        if session is None:
            return
        before = session.collected_count
        session.feed_frame(frame)
        if session.collected_count != before:
            self._notify(frame.node_id, "collecting", session.collected_count)

    def stop(self, node_id: str) -> Tuple[float, int]:
        """Fits and stores the baseline, returning (energy_ref, frames).
        Leaves the session in place on StoppedBaselineError so the operator
        can keep collecting and retry -- the same retry shape
        CommissioningController.stop_collecting() preserves, and the reason
        this doesn't pop the session before calling stop()."""
        session = self._sessions.get(node_id)
        if session is None:
            raise StoppedBaselineError(
                f"no stopped-baseline capture running for {node_id!r}")
        baseline = session.stop()
        frames = session.collected_count
        del self._sessions[node_id]
        self._notify(node_id, "idle", frames)
        return baseline.energy, frames

    def cancel(self, node_id: str) -> None:
        session = self._sessions.pop(node_id, None)
        if session is not None:
            session.cancel()
        self._notify(node_id, "idle", 0)

    def progress(self, node_id: str) -> Optional[Tuple[int, int]]:
        """(collected, min_frames) for a node's active capture, or None if
        it has none -- lets the dashboard unlock its "Save baseline" control
        once enough frames are in without duplicating the frame counting."""
        session = self._sessions.get(node_id)
        if session is None:
            return None
        return session.collected_count, self._min_frames

    def discard(self, node_id: str) -> None:
        """Drops node_id's session with no registry side effects -- used
        when a node is decommissioned mid-capture, same role as
        CommissioningController.discard()."""
        self._sessions.pop(node_id, None)
