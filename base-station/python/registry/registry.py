"""Registry -- the single source of truth for node metadata, per
docs/MPU_Software_Architecture.md S4.2/S5.1. Add/rename/remove/commission
all operate here; every other feature reads or writes this one place.

Disk-backed as a single JSON file so state survives a process restart
(S8 M2's verification method). One process is expected to own the file
at a time (no cross-process locking) -- fine for now, matches every
other single-process module planned through M9.
"""
import json
import os
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Dict, FrozenSet, Optional

from statemachine import StateMachine
from statemachine.exceptions import TransitionNotAllowed
from statemachine.states import States


class SensorChannel(Enum):
    MIC = "mic"
    ACCEL = "accel"
    # future channels are added here, one at a time


class NodeStatus(Enum):
    UNCOMMISSIONED = "uncommissioned"
    COMMISSIONING_COLLECTING = "commissioning_collecting"
    COMMISSIONING_TRAINING = "commissioning_training"
    HEALTHY = "healthy"
    WARNING = "warning"
    FAULT = "fault"
    OFFLINE = "offline"
    PAUSED = "paused"


# per-channel input_dim (S4.2: "drives 512 vs 1024 model dim"). Adding a
# new SensorChannel + an entry here automatically supports every
# combination involving it -- no combination is named or enumerated by hand.
_DIM_BY_CHANNEL = {
    SensorChannel.MIC: 512,
    SensorChannel.ACCEL: 512,
}


def input_dim_for(channels: FrozenSet[SensorChannel]) -> int:
    """Public accessor for the channel -> input_dim mapping (S4.2), so
    callers outside this module (e.g. pipeline/features.py) don't need
    their own copy of this mapping. Sums the per-channel dim over an
    arbitrary set of active channels."""
    return sum(_DIM_BY_CHANNEL[c] for c in channels)


@dataclass
class RegistryEntry:
    node_id: str
    display_name: str
    sensor_config: FrozenSet[SensorChannel]
    input_dim: int
    model_path: Optional[str] = None
    status: NodeStatus = NodeStatus.UNCOMMISSIONED
    last_seen: Optional[float] = None
    last_commissioned: Optional[float] = None
    control_circuit_id: Optional[str] = None
    auto_cutoff_enabled: bool = False
    last_anomaly_score: Optional[float] = None
    # Per-node anomaly-score thresholds, calibrated from this node's own
    # healthy baseline at commissioning (pipeline/commissioning.py). None
    # until commissioned -- inference falls back to the process-wide
    # defaults (main.py's --warning/--fault-threshold) in that case. A
    # fixed global threshold can't fit every motor: the autoencoder's
    # healthy reconstruction-error scale depends on that motor's own
    # spectrum, so what reads "healthy" for one node reads "fault" for
    # another. Calibrating per node is what makes a real fault actually
    # cross the line on the dashboard.
    warning_threshold: Optional[float] = None
    fault_threshold: Optional[float] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["sensor_config"] = sorted(c.value for c in self.sensor_config)
        d["status"] = self.status.value
        return d

    @staticmethod
    def from_dict(d: dict) -> "RegistryEntry":
        d = dict(d)
        d["sensor_config"] = frozenset(SensorChannel(v) for v in d["sensor_config"])
        # Legacy compat: entries persisted before the commissioning split
        # (S6 dashboard redesign) have status "training". Map to
        # COMMISSIONING_COLLECTING rather than COMMISSIONING_TRAINING --
        # the safer of the two, since it forces an operator to re-stop
        # (re-confirm the collected batch) rather than silently claiming
        # to be mid-training on a batch that may no longer be fresh.
        raw_status = d["status"]
        if raw_status == "training":
            raw_status = "commissioning_collecting"
        d["status"] = NodeStatus(raw_status)
        return RegistryEntry(**d)


class NodeNotFoundError(KeyError):
    pass


class InvalidTransitionError(ValueError):
    pass


class _NodeStateMachine(StateMachine):
    """Owns every legal NodeStatus transition in one place, so a Registry
    method can no longer overwrite `.status` without checking what it's
    overwriting (the bug behind pause() being callable during commissioning,
    silently diverging from what commissioning.py's CommissioningSession
    believes it owns). Built once per call against a live RegistryEntry
    (model=entry, state_field="status") rather than kept as a long-lived
    instance -- cheap to construct, and it always reads the entry's
    current on-disk status rather than assuming a fixed starting state."""

    validate_disconnected_states = False
    validate_trap_states = False
    # Both checks would otherwise reject OFFLINE at class-definition time:
    # it has no transition in or out anywhere in this codebase today.
    # OFFLINE is a frontend-computed staleness label (frontend/app.js's
    # effectiveStatus(), S3.8) derived from last_seen -- nothing server-side
    # ever assigns it to entry.status. It stays a valid NodeStatus value
    # with no wiring here until something server-side actually sets it.

    _ = States.from_enum(NodeStatus, initial=NodeStatus.UNCOMMISSIONED)

    start_commissioning = (
        _.UNCOMMISSIONED.to(_.COMMISSIONING_COLLECTING)
        | _.HEALTHY.to(_.COMMISSIONING_COLLECTING)
        | _.WARNING.to(_.COMMISSIONING_COLLECTING)
        | _.FAULT.to(_.COMMISSIONING_COLLECTING)
    )
    # HEALTHY/WARNING/FAULT -> COMMISSIONING_COLLECTING: re-commissioning
    # overwrites the existing model rather than versioning it
    # (commissioning.py's own resolution of open question #6). PAUSED ->
    # COMMISSIONING_COLLECTING is deliberately absent: an operator's pause
    # shouldn't be silently overridden by a recommission -- resume() first.

    stop_collecting = _.COMMISSIONING_COLLECTING.to(_.COMMISSIONING_TRAINING)
    # Explicit "Stop & Train" trigger (S3.5, dashboard redesign S6): splits
    # what used to be a single TRAINING status into an observable two-step
    # flow so the frontend can distinguish "still collecting" from
    # "fitting the model now" and show real epoch progress for the latter.

    complete_commissioning = _.COMMISSIONING_TRAINING.to(_.HEALTHY)

    pause = _.HEALTHY.to(_.PAUSED) | _.WARNING.to(_.PAUSED) | _.FAULT.to(_.PAUSED)
    # COMMISSIONING_COLLECTING/COMMISSIONING_TRAINING -> PAUSED is absent:
    # this status field is the sole source of truth for "mid-commissioning"
    # (CommissioningSession has no independent _active flag), so pausing a
    # commissioning node would silently steal it out from under the active
    # session. UNCOMMISSIONED -> PAUSED is absent: nothing to pause -- no
    # model, no confirmed status (the bug this state machine closes).

    resume = _.PAUSED.to(_.HEALTHY)
    # Always lands on HEALTHY rather than a stored previous_status:
    # InferencePipeline already treats any non-confirmable status as
    # HEALTHY on construction (_CONFIRMABLE_STATUSES), so a resumed node
    # gets re-diagnosed via the normal debounce logic within a few frames
    # if it's still actually WARNING/FAULT. PAUSED -> WARNING/FAULT
    # directly is absent for the same reason: resume always re-diagnoses
    # from HEALTHY, it never guesses.

    to_healthy = _.WARNING.to(_.HEALTHY) | _.FAULT.to(_.HEALTHY)
    to_warning = _.HEALTHY.to(_.WARNING) | _.FAULT.to(_.WARNING)
    to_fault = _.HEALTHY.to(_.FAULT) | _.WARNING.to(_.FAULT)
    # All 6 directed edges between the three confirmable statuses: inference
    # (pipeline/inference.py's threshold -> status, S3.6) can jump straight
    # from HEALTHY to FAULT (or back) without passing through WARNING.


# set_status() is the only caller that picks a transition by an arbitrary
# NodeStatus argument rather than a fixed method name -- this maps that
# argument to the one event that's actually legal for it (inference only
# ever confirms one of the three confirmable statuses, S3.6).
_SET_STATUS_EVENT = {
    NodeStatus.HEALTHY: "to_healthy",
    NodeStatus.WARNING: "to_warning",
    NodeStatus.FAULT: "to_fault",
}


class Registry:
    """CRUD over RegistryEntry, persisted to a JSON file. Every mutating
    call writes the full file back out immediately -- registry writes are
    rare (add/rename/remove/status-change) compared to reads, so there's
    no need for batching or a write-behind cache."""

    def __init__(self, path: str):
        self._path = path
        self._entries: Dict[str, RegistryEntry] = {}
        self._locks: Dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()
        self._status_listeners: list = []
        self._load()

    def on_status_change(self, callback) -> None:
        """Registers `callback(node_id, NodeStatus)`, invoked after every
        method below that changes a node's `.status` (including add(), for
        a newly-discovered node's initial UNCOMMISSIONED). Lets a caller
        outside Registry (main.py, when --mqtt-host is set) react to every
        status transition from one place regardless of which of these
        methods caused it -- an explicit REST action, or PipelineManager's
        automatic inference-driven confirm."""
        self._status_listeners.append(callback)

    def _notify_status_change(self, node_id: str, status: NodeStatus) -> None:
        for callback in self._status_listeners:
            callback(node_id, status)

    def _lock_for(self, node_id: str) -> threading.RLock:
        with self._locks_guard:
            lock = self._locks.get(node_id)
            if lock is None:
                lock = threading.RLock()
                self._locks[node_id] = lock
            return lock

    def lock_for(self, node_id: str) -> threading.RLock:
        """Public accessor so callers outside Registry (PipelineManager)
        can borrow this node's lock instead of keeping a second,
        independent lock over the same data."""
        return self._lock_for(node_id)

    def _load(self) -> None:
        if not os.path.exists(self._path):
            return
        with open(self._path, "r") as f:
            raw = json.load(f)
        self._entries = {node_id: RegistryEntry.from_dict(entry)
                          for node_id, entry in raw.items()}

    def _save(self) -> None:
        # Write to a temp file in the same directory then rename, so a
        # crash mid-write can't leave a truncated/corrupt registry file
        # behind -- os.replace() is atomic on the same filesystem.
        directory = os.path.dirname(self._path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".registry-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump({node_id: entry.to_dict() for node_id, entry in self._entries.items()},
                          f, indent=2)
            os.replace(tmp_path, self._path)
        except BaseException:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

    def add(self, node_id: str, display_name: Optional[str] = None,
             # A frozenset literal default is safe here (unlike the usual
             # mutable-default-argument trap) since frozensets are immutable.
             sensor_config: FrozenSet[SensorChannel] = frozenset(
                 {SensorChannel.MIC, SensorChannel.ACCEL}),
             input_dim: Optional[int] = None) -> RegistryEntry:
        """input_dim: the actual per-node vector length to commit to (e.g.
        derived from a real first frame's bin counts, PipelineManager's
        _infer_sensor_config_and_dim) -- not every node necessarily uses the
        same per-channel FFT bin count. Defaults to input_dim_for()'s fixed
        512-per-channel table when the caller has no frame to derive it from
        yet (a manually pre-added node, or any caller that just wants the
        old fixed-dim behavior)."""
        with self._lock_for(node_id):
            if node_id in self._entries:
                return self._entries[node_id]
            entry = RegistryEntry(
                node_id=node_id,
                display_name=display_name or node_id,
                sensor_config=sensor_config,
                input_dim=input_dim if input_dim is not None else input_dim_for(sensor_config),
            )
            self._entries[node_id] = entry
            self._save()
            self._notify_status_change(node_id, entry.status)
            return entry

    def get(self, node_id: str) -> RegistryEntry:
        with self._lock_for(node_id):
            try:
                return self._entries[node_id]
            except KeyError:
                raise NodeNotFoundError(node_id)

    def list(self) -> Dict[str, RegistryEntry]:
        return dict(self._entries)

    def rename(self, node_id: str, display_name: str) -> RegistryEntry:
        with self._lock_for(node_id):
            entry = self.get(node_id)
            entry.display_name = display_name
            self._save()
            return entry

    def decommission(self, node_id: str) -> None:
        # Removable from any status -- the dashboard's bin icon (S3.9
        # redesign) is meant to always be available, including for a node
        # that's still New or mid-commissioning (give up on this asset
        # entirely, rather than only ever letting go of a fully provisioned
        # one). Callers that also own a CommissioningController should
        # discard() that node's session first (api/app.py's decommission
        # route does), since this call has no visibility into it.
        with self._lock_for(node_id):
            self.get(node_id)  # raises NodeNotFoundError if unknown
            del self._entries[node_id]
            self._save()

    def pause(self, node_id: str) -> RegistryEntry:
        with self._lock_for(node_id):
            entry = self.get(node_id)
            try:
                _NodeStateMachine(model=entry, state_field="status").pause()
            except TransitionNotAllowed:
                raise InvalidTransitionError(
                    f"cannot pause node {node_id!r}: status is {entry.status.value!r}, "
                    "must be healthy, warning, or fault")
            self._save()
            self._notify_status_change(node_id, entry.status)
            return entry

    def resume(self, node_id: str) -> RegistryEntry:
        # Always lands on HEALTHY rather than a stored previous_status --
        # see _NodeStateMachine.resume's comment for why.
        with self._lock_for(node_id):
            entry = self.get(node_id)
            try:
                _NodeStateMachine(model=entry, state_field="status").resume()
            except TransitionNotAllowed:
                raise InvalidTransitionError(
                    f"cannot resume node {node_id!r}: status is {entry.status.value!r}, "
                    "must be paused")
            self._save()
            self._notify_status_change(node_id, entry.status)
            return entry

    def touch_last_seen(self, node_id: str, timestamp: Optional[float] = None) -> RegistryEntry:
        with self._lock_for(node_id):
            entry = self.get(node_id)
            entry.last_seen = time.time() if timestamp is None else timestamp
            self._save()
            return entry

    def start_commissioning(self, node_id: str) -> RegistryEntry:
        with self._lock_for(node_id):
            entry = self.get(node_id)
            try:
                _NodeStateMachine(model=entry, state_field="status").start_commissioning()
            except TransitionNotAllowed:
                # Most commonly hit for a paused node: resume() first so
                # commissioning can't silently override an operator's pause.
                raise InvalidTransitionError(
                    f"cannot start commissioning node {node_id!r}: status is "
                    f"{entry.status.value!r}, must be uncommissioned, healthy, warning, or fault")
            self._save()
            self._notify_status_change(node_id, entry.status)
            return entry

    def stop_collecting(self, node_id: str) -> RegistryEntry:
        with self._lock_for(node_id):
            entry = self.get(node_id)
            try:
                _NodeStateMachine(model=entry, state_field="status").stop_collecting()
            except TransitionNotAllowed:
                raise InvalidTransitionError(
                    f"cannot stop collecting for node {node_id!r}: status is "
                    f"{entry.status.value!r}, must be commissioning_collecting")
            self._save()
            self._notify_status_change(node_id, entry.status)
            return entry

    def set_status(self, node_id: str, status: NodeStatus) -> RegistryEntry:
        """Generic status setter for callers outside a specific registry
        workflow (e.g. pipeline/inference.py's threshold -> status, S3.6),
        as opposed to start_commissioning/complete_commissioning below
        which also update other fields as part of their own workflow.
        Only supports the three confirmable statuses (healthy/warning/
        fault) -- every other transition has its own dedicated method."""
        with self._lock_for(node_id):
            entry = self.get(node_id)
            event_name = _SET_STATUS_EVENT.get(status)
            if event_name is None:
                raise InvalidTransitionError(
                    f"cannot set node {node_id!r} to {status.value!r}: set_status only "
                    "supports healthy, warning, or fault")
            try:
                getattr(_NodeStateMachine(model=entry, state_field="status"), event_name)()
            except TransitionNotAllowed:
                raise InvalidTransitionError(
                    f"cannot set node {node_id!r} to {status.value!r}: status is "
                    f"{entry.status.value!r}, must be healthy, warning, or fault")
            self._save()
            self._notify_status_change(node_id, entry.status)
            return entry

    def complete_commissioning(self, node_id: str, model_path: str,
                                 timestamp: Optional[float] = None,
                                 warning_threshold: Optional[float] = None,
                                 fault_threshold: Optional[float] = None) -> RegistryEntry:
        with self._lock_for(node_id):
            entry = self.get(node_id)
            try:
                _NodeStateMachine(model=entry, state_field="status").complete_commissioning()
            except TransitionNotAllowed:
                raise InvalidTransitionError(
                    f"cannot complete commissioning for node {node_id!r}: status is "
                    f"{entry.status.value!r}, must be commissioning_training")
            entry.model_path = model_path
            entry.last_commissioned = time.time() if timestamp is None else timestamp
            # Thresholds are calibrated from this run's healthy scores
            # (commissioning.py); on re-commissioning they're overwritten
            # with the fresh values, matching model_path's overwrite behavior.
            if warning_threshold is not None:
                entry.warning_threshold = warning_threshold
            if fault_threshold is not None:
                entry.fault_threshold = fault_threshold
            self._save()
            self._notify_status_change(node_id, entry.status)
            return entry

    def record_anomaly_score(self, node_id: str, score: float) -> RegistryEntry:
        """Latest combined anomaly score, written on every inference frame
        (pipeline/manager.py's MotorPipeline.handle_frame(), alongside the
        existing history_store.record() call) so Fleet cards can show a
        node's anomaly index without per-node history polling (dashboard
        redesign S5.1)."""
        with self._lock_for(node_id):
            entry = self.get(node_id)
            entry.last_anomaly_score = score
            self._save()
            return entry
