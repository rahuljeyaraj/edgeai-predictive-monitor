"""CaptureController -- API-layer lifecycle shim between REST control
actions and the capture workflow (pipeline/capture.py, S2), mirroring
CommissioningController's role for commissioning. One difference: a
CaptureSession is long-lived per node_id (created lazily on first start,
then reused across repeated capture cycles) rather than one-shot, since a
node may be captured many times over its life.
"""
from typing import Callable, Dict, List, Optional, Tuple

from registry import NodeNotFoundError, Registry
from gate import MotorStateGate
from capture import (CaptureError, CaptureSession, list_labels, list_captures,
                      rename_capture, delete_capture)


class CaptureController:
    def __init__(self, registry: Registry, captures_dir: str,
                 gate_factory: Callable[[], MotorStateGate]):
        self._registry = registry
        self._captures_dir = captures_dir
        self._gate_factory = gate_factory
        self._sessions: Dict[str, CaptureSession] = {}
        self._listeners: list = []

    def on_state_change(self, callback) -> None:
        """Registers `callback(node_id, state, collected, target_frames)`,
        fired after every state change this controller causes --
        REST-triggered (start/stop/save/cancel) and the internal
        auto-stop-at-target_frames transition (feed_frame(), from the
        ingestion thread) alike. Mirrors Registry.on_status_change's same
        problem: a status/state can change from more than one trigger
        source, so the broadcast has to be centralized here rather than
        duplicated at every call site that can cause one (api/app.py's
        route handlers used to broadcast individually -- that missed the
        auto-stop case entirely, since that transition doesn't go through
        any REST handler)."""
        self._listeners.append(callback)

    def _notify(self, node_id: str, state: str, collected: int,
                target_frames: Optional[int]) -> None:
        for callback in self._listeners:
            callback(node_id, state, collected, target_frames)

    def _session_for(self, node_id: str) -> CaptureSession:
        session = self._sessions.get(node_id)
        if session is None:
            session = CaptureSession(self._registry, self._captures_dir, node_id,
                                      self._gate_factory())
            self._sessions[node_id] = session
        return session

    def start(self, node_id: str, target_frames: Optional[int] = None) -> None:
        self._registry.get(node_id)  # raises NodeNotFoundError if unknown
        session = self._session_for(node_id)
        session.start(target_frames)
        self._notify(node_id, "capturing", 0, target_frames)

    def feed_frame(self, frame) -> None:
        """Called unconditionally for every incoming frame (main.py's
        on_frame), same contract as CommissioningController.feed_frame --
        a no-op for any node with no session, or one that isn't currently
        capturing. Detects the auto-stop-at-target_frames transition here
        (the only place that can: it's a side effect of this exact call)
        and notifies listeners so a connected dashboard finds out
        immediately instead of waiting on the next REST poll."""
        session = self._sessions.get(frame.node_id)
        if session is None:
            return
        was_capturing = session.state == "capturing"
        try:
            session.feed_frame(frame)
        except CaptureError:
            return
        if was_capturing and session.state == "stopped":
            self._notify(frame.node_id, "stopped", session.collected_count, session.target_frames)

    def stop(self, node_id: str) -> int:
        session = self._sessions.get(node_id)
        if session is None:
            raise CaptureError(f"no capture session for {node_id!r}")
        collected = session.stop()
        self._notify(node_id, "stopped", collected, session.target_frames)
        return collected

    def save(self, node_id: str, label: str) -> str:
        session = self._sessions.get(node_id)
        if session is None:
            raise CaptureError(f"no capture session for {node_id!r}")
        path = session.save(label)
        self._notify(node_id, "idle", 0, None)
        return path

    def cancel(self, node_id: str) -> None:
        session = self._sessions.get(node_id)
        if session is not None:
            session.cancel()
        self._notify(node_id, "idle", 0, None)

    def progress(self, node_id: str) -> Optional[Tuple[str, int, Optional[int]]]:
        """(state, collected_count, target_frames) for a node's session, or
        None if it has never captured -- lets the dashboard show live
        capture progress without duplicating CaptureSession's own
        frame-counting (mirrors CommissioningController.progress)."""
        session = self._sessions.get(node_id)
        if session is None:
            return None
        return session.state, session.collected_count, session.target_frames

    def discard(self, node_id: str) -> None:
        """Drops node_id's session, if any -- used when a node is removed
        (decommission) so its session doesn't linger in self._sessions
        forever (mirrors CommissioningController.discard)."""
        self._sessions.pop(node_id, None)

    def list_labels(self) -> List[str]:
        return list_labels(self._captures_dir)

    def list_captures(self) -> List[dict]:
        return list_captures(self._captures_dir)

    def rename_capture(self, capture_id: str, new_label: str) -> str:
        return rename_capture(self._captures_dir, capture_id, new_label)

    def delete_capture(self, capture_id: str) -> None:
        delete_capture(self._captures_dir, capture_id)
