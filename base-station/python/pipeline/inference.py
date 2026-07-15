"""Inference loop -- load trained weights, score live gated frames,
threshold + hysteresis -> status, per docs/MPU_Software_Architecture.md
S3.6/S8 M8.

One InferencePipeline per motor, mirroring CommissioningSession
(pipeline/commissioning.py): construction loads that motor's saved
weights once, then handle_frame() is called for every live SensorFrame.
Frames the gate doesn't confirm RUNNING are not scored at all -- both
training (M7) and inference must be suppressed during stopped/transient
states (S3.2) -- and the previously confirmed status is left untouched
rather than reset.

Hysteresis reuses the same debounce pattern as pipeline/gate.py: a raw
threshold crossing only becomes the confirmed status (and is written to
the registry) after debounce_frames consecutive frames agree, so one
noisy frame can't flip status (S3.6).
"""
from typing import FrozenSet, Optional

from sensor_frame import SensorFrame
from registry import InvalidTransitionError, NodeStatus, Registry, SensorChannel
from gate import MotorState, MotorStateGate
from features import build_feature_vector
from autoencoder import Autoencoder, load_model, reconstruction_error

_CONFIRMABLE_STATUSES = (NodeStatus.HEALTHY, NodeStatus.WARNING, NodeStatus.FAULT)


class InferenceError(Exception):
    pass


class InferencePipeline:
    def __init__(self, registry: Registry, node_id: str, gate: MotorStateGate,
                 debounce_frames: int = 3):
        if debounce_frames < 1:
            raise ValueError("debounce_frames must be >= 1")

        entry = registry.get(node_id)
        if not entry.model_path:
            raise InferenceError(
                f"node {node_id!r} has no trained model -- commission it first")

        # Thresholds always come from this node's own commissioning-
        # calibrated baseline (S3.6, pipeline/commissioning.py) -- a fixed
        # global cutoff can't fit every motor's own error scale, and
        # complete_commissioning() always sets both together with
        # model_path, so a commissioned node with no thresholds means it
        # predates per-node calibration and needs re-commissioning.
        if entry.warning_threshold is None or entry.fault_threshold is None:
            raise InferenceError(
                f"node {node_id!r} has no calibrated thresholds -- re-commission it")
        if entry.fault_threshold <= entry.warning_threshold:
            raise ValueError("fault_threshold must be greater than warning_threshold")

        self._registry = registry
        self._node_id = node_id
        self._gate = gate
        self._warning_threshold = entry.warning_threshold
        self._fault_threshold = entry.fault_threshold
        self._debounce_frames = debounce_frames
        self._sensor_config: FrozenSet[SensorChannel] = entry.sensor_config
        self._model: Autoencoder = load_model(entry.model_path)

        self._status = entry.status if entry.status in _CONFIRMABLE_STATUSES else NodeStatus.HEALTHY
        self._candidate_status: Optional[NodeStatus] = None
        self._candidate_count = 0
        self._last_score: Optional[float] = None

    @property
    def status(self) -> NodeStatus:
        return self._status

    @property
    def last_score(self) -> Optional[float]:
        return self._last_score

    def _status_for_score(self, score: float) -> NodeStatus:
        if score > self._fault_threshold:
            return NodeStatus.FAULT
        if score > self._warning_threshold:
            return NodeStatus.WARNING
        return NodeStatus.HEALTHY

    def handle_frame(self, frame: SensorFrame) -> Optional[float]:
        """Returns the reconstruction error for this frame, or None if it
        was skipped (a different node_id, or the gate reports anything but
        confirmed RUNNING -- S3.2). A status change is only confirmed (and
        pushed to the registry) once debounce_frames consecutive frames
        agree on it (S3.6)."""
        if frame.node_id != self._node_id:
            return None
        if self._gate.update(frame) != MotorState.RUNNING:
            return None

        vector = build_feature_vector(frame, self._sensor_config)
        score = reconstruction_error(self._model, vector)
        self._last_score = score

        raw_status = self._status_for_score(score)
        if raw_status == self._status:
            # Back in line with the confirmed status -- any in-progress
            # flip attempt is stale, drop it.
            self._candidate_status = None
            self._candidate_count = 0
            return score

        if raw_status == self._candidate_status:
            self._candidate_count += 1
        else:
            self._candidate_status = raw_status
            self._candidate_count = 1

        if self._candidate_count >= self._debounce_frames:
            try:
                self._registry.set_status(self._node_id, raw_status)
            except InvalidTransitionError:
                # The registry rejected this confirmation -- e.g. the node
                # was paused underneath this pipeline after it started
                # running, so it's no longer in a confirmable state. Drop
                # the candidate like the "back in line" case above rather
                # than crashing the frame handler; self._status is left
                # untouched, so the next scored frame re-evaluates fresh.
                self._candidate_status = None
                self._candidate_count = 0
                return score
            self._status = raw_status
            self._candidate_status = None
            self._candidate_count = 0

        return score
