"""Registry -- the single source of truth for node metadata, per
docs/MPU_Software_Architecture.md S4.2/S5.1. Add/rename/remove/commission
all operate here; every other feature reads or writes this one place.

Disk-backed as a single JSON file so state survives a process restart
(S8 M2's verification method). One process is expected to own the file
at a time (no cross-process locking) -- fine for now, matches every
other single-process module planned through M9.
"""
import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Dict, FrozenSet, Optional, Tuple

logger = logging.getLogger(__name__)

from statemachine import StateMachine
from statemachine.exceptions import TransitionNotAllowed
from statemachine.states import States


class SensorChannel(Enum):
    MIC = "mic"
    ACCEL_X = "accel_x"
    ACCEL_Y = "accel_y"
    ACCEL_Z = "accel_z"
    # future channels are added here, one at a time. The combined "accel"
    # channel (summed x/y/z) was replaced by the three per-axis channels
    # above -- per-axis scalars need the directional signal a combined
    # magnitude erases (see features.py's scalar suffix map); it's still on
    # the wire (fuser.cpp) for whatever else may read it, just no longer a
    # SensorChannel, so it falls through to SensorFrame.display_bins like
    # any other non-model channel.


class NodeStatus(Enum):
    UNCOMMISSIONED = "uncommissioned"
    COMMISSIONING_COLLECTING = "commissioning_collecting"
    COMMISSIONING_TRAINING = "commissioning_training"
    HEALTHY = "healthy"
    WARNING = "warning"
    FAULT = "fault"
    OFFLINE = "offline"
    PAUSED = "paused"
    # Machinery-protection states (docs/MOTOR_STOP_PLAN.md). Both mean "this
    # node's machine is not turning"; they differ only in who stopped it,
    # which is exactly the distinction an operator needs and which a single
    # "stopped" status would erase.
    #
    # IDLE closes a pre-existing hole rather than serving the trip feature:
    # pipeline/gate.py already detects a stopped motor and inference.py
    # already declines to score it, but nothing ever wrote that fact
    # anywhere, so a machine switched off an hour ago kept displaying
    # whatever status it held while it was last running.
    IDLE = "idle"
    TRIPPED = "tripped"


# per-channel input_dim (S4.2: "drives 512 vs 1024 model dim"). Adding a
# new SensorChannel + an entry here automatically supports every
# combination involving it -- no combination is named or enumerated by hand.
# 128 spectral bins + 6 scalars (rms/kurtosis/std/peak/crest_factor/skewness)
# per channel -- every current SensorChannel carries both (see
# pipeline/features.py's _SCALAR_SUFFIX_BY_CHANNEL). The "+6" is deliberately
# duplicated here rather than imported from features.py's scalar_dim_for():
# registry.py is designed to be usable/testable standalone with only
# python/registry/ on the path (registry_test.py's own PYTHONPATH doesn't
# include python/pipeline/), so a cross-import here would break that. Keep
# in sync if features.py's per-channel scalar count ever changes.
_DIM_BY_CHANNEL = {
    SensorChannel.MIC: 134,
    SensorChannel.ACCEL_X: 134,
    SensorChannel.ACCEL_Y: 134,
    SensorChannel.ACCEL_Z: 134,
}


def input_dim_for(channels: FrozenSet[SensorChannel]) -> int:
    """Public accessor for the channel -> input_dim mapping (S4.2), so
    callers outside this module (e.g. pipeline/features.py) don't need
    their own copy of this mapping. Sums the per-channel dim (spectral +
    scalar tail, see _DIM_BY_CHANNEL's comment) over an arbitrary set of
    active channels."""
    return sum(_DIM_BY_CHANNEL[c] for c in channels)


@dataclass
class RegistryEntry:
    node_id: str
    device_name: str
    sensor_config: FrozenSet[SensorChannel]
    input_dim: int
    # Scoping key for capture/label grouping and (later) which Edge Impulse
    # project/model applies to this node -- docs/EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md
    # S1. None until an operator sets it; a node with no device_type just
    # shows the anomaly score, no classification attempted.
    device_type: Optional[str] = None
    model_path: Optional[str] = None
    status: NodeStatus = NodeStatus.UNCOMMISSIONED
    last_seen: Optional[float] = None
    last_commissioned: Optional[float] = None
    last_anomaly_score: Optional[float] = None
    # Latest Edge Impulse fault-classifier result, written alongside
    # last_anomaly_score on every gated frame once device_type has a
    # fetched model (pipeline/manager.py) -- {label, confidence, scores,
    # ts}. None if no classifier model is loaded for this node's
    # device_type; unlike last_anomaly_score there's no per-node "not
    # commissioned yet" gate, since the classifier isn't tied to
    # commissioning at all (docs/EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md
    # S1: "no device_type, or type has no model -> anomaly score only").
    # Plain dict, JSON-safe as-is (asdict()/dataclasses round-trip it with
    # no special handling, unlike scalar_mu/sigma's tuple-restoration
    # below).
    last_classification: Optional[dict] = None
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
    # Per-scalar z-score standardization stats (features.py's
    # standardize_scalars()), fit once at commissioning time from this
    # node's own healthy batch -- spectral bins are already peak-normalized
    # to [0,1] and don't need this, but scalars like rms/kurtosis aren't
    # naturally bounded. Column order matches whatever fixed order
    # build_feature_vector()'s scalar tail uses. None until commissioned.
    scalar_mu: Optional[Tuple[float, ...]] = None
    scalar_sigma: Optional[Tuple[float, ...]] = None
    # Median pipeline/gate.py compute_energy() over this node's own
    # commissioning batch -- the reference the running/stopped gate scales
    # its threshold from (gate.py's DEFAULT_RUNNING_FRACTION). Same
    # motivation as warning_threshold/fault_threshold above: gate energy is
    # an RMS over unnormalized FFT magnitudes, so its absolute scale is a
    # property of this node's sensor range, gain and mounting, and no single
    # global number can mean "stopped" for every node. None for entries
    # commissioned before this field existed -- gate.py falls back to its
    # absolute threshold for those, preserving their old behaviour until
    # they're re-commissioned.
    running_energy_ref: Optional[float] = None
    # Which motor on the rig this asset's machinery-protection trip stops
    # (docs/MOTOR_STOP_PLAN.md), 1-based, matching motor-driver's own serial
    # command numbering. None = no trip output wired, which is the default
    # and stays true for most assets: a monitored point usually has no
    # actuator, so protection is armed per asset rather than fleet-wide.
    #
    # RegistryEntry carried control_circuit_id/auto_cutoff_enabled for this
    # same purpose once before (still popped for compat in from_dict below).
    # They were deleted as dead code because nothing was ever wired to
    # them. This field is different only in that it is wired end to end --
    # set from the dashboard, read by protection/, published as
    # MqttMsgType.MOTOR_STOP. Don't delete it as unused without checking
    # those three.
    trip_motor_idx: Optional[int] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["sensor_config"] = sorted(c.value for c in self.sensor_config)
        d["status"] = self.status.value
        return d

    @staticmethod
    def from_dict(d: dict) -> "RegistryEntry":
        d = dict(d)
        # Legacy compat: entries persisted before the device_name/device_type
        # rename (2026-07-24, docs/EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md S1)
        # have "display_name" and two now-dead fields instead.
        if "display_name" in d:
            d["device_name"] = d.pop("display_name")
        d.pop("control_circuit_id", None)
        d.pop("auto_cutoff_enabled", None)
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
        # JSON round-trips tuples as lists; restore tuple-ness for these two
        # (mirrors the sensor_config special-case above) so callers doing
        # positional/zip work against scalar_mu/sigma get the same type
        # whether the entry was just built or reloaded from disk.
        if d.get("scalar_mu") is not None:
            d["scalar_mu"] = tuple(d["scalar_mu"])
        if d.get("scalar_sigma") is not None:
            d["scalar_sigma"] = tuple(d["scalar_sigma"])
        return RegistryEntry(**d)


class NodeNotFoundError(KeyError):
    pass


class InvalidTransitionError(ValueError):
    pass


class TripMotorInUseError(ValueError):
    """Raised by set_trip_motor() when another node already owns that motor.
    Its own type (not a bare ValueError) so api/app.py can map it to a 409
    rather than the generic 400 every other bad field value gets."""
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

    to_healthy = (
        _.WARNING.to(_.HEALTHY) | _.FAULT.to(_.HEALTHY)
        | _.IDLE.to(_.HEALTHY) | _.TRIPPED.to(_.HEALTHY)
    )
    to_warning = (
        _.HEALTHY.to(_.WARNING) | _.FAULT.to(_.WARNING)
        | _.IDLE.to(_.WARNING) | _.TRIPPED.to(_.WARNING)
    )
    to_fault = (
        _.HEALTHY.to(_.FAULT) | _.WARNING.to(_.FAULT)
        | _.IDLE.to(_.FAULT) | _.TRIPPED.to(_.FAULT)
    )
    # All 6 directed edges between the three confirmable statuses: inference
    # (pipeline/inference.py's threshold -> status, S3.6) can jump straight
    # from HEALTHY to FAULT (or back) without passing through WARNING.
    #
    # IDLE/TRIPPED are additional sources for all three because that is how
    # recovery works, and it is the whole reason no "reset protection" button
    # exists: restarting the machine makes frames score again, and the score
    # alone decides where it lands. Fix the fault and it returns to HEALTHY;
    # don't, and it goes back to FAULT and trips again. An operator cannot
    # restart their way out of a real fault, and nothing here ever restarts
    # a machine on its own.

    to_idle = (
        _.HEALTHY.to(_.IDLE) | _.WARNING.to(_.IDLE) | _.FAULT.to(_.IDLE)
    )
    # Motor stopped and we did not stop it. Only from the three confirmable
    # statuses: a node mid-commissioning or paused has no meaningful running
    # state to lose, and UNCOMMISSIONED -> IDLE would relabel "never set up"
    # as "switched off", which reads as a regression on the Fleet list.

    to_tripped = _.FAULT.to(_.TRIPPED)
    # Only ever from FAULT, and only once the gate confirms the machine
    # actually stopped. A trip published but never confirmed deliberately
    # leaves the node in FAULT (protection/ marks it trip_failed instead) --
    # a trip that didn't take is a safety event, and showing TRIPPED for a
    # machine that is still turning would be the single most dangerous lie
    # this dashboard could tell.


# set_status() is the only caller that picks a transition by an arbitrary
# NodeStatus argument rather than a fixed method name -- this maps that
# argument to the one event that's actually legal for it (inference only
# ever confirms one of the three confirmable statuses, S3.6; IDLE and
# TRIPPED come from the gate and protection/ respectively).
_SET_STATUS_EVENT = {
    NodeStatus.HEALTHY: "to_healthy",
    NodeStatus.WARNING: "to_warning",
    NodeStatus.FAULT: "to_fault",
    NodeStatus.IDLE: "to_idle",
    NodeStatus.TRIPPED: "to_tripped",
}

_SET_STATUS_SETTABLE = ", ".join(sorted(s.value for s in _SET_STATUS_EVENT))


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
        # Isolate a single bad entry (e.g. a sensor_config channel name that
        # no longer maps to a SensorChannel member after a schema change --
        # see registry.py's SensorChannel docstring) rather than letting one
        # stale node fail the whole Registry() construction, which main.py
        # calls at startup with no try/except -- one incompatible node
        # shouldn't take the entire fleet offline. That node just needs
        # re-adding/re-commissioning.
        entries = {}
        for node_id, entry in raw.items():
            try:
                entries[node_id] = RegistryEntry.from_dict(entry)
            except ValueError as e:
                logger.warning(
                    "skipping node %r: incompatible registry entry (%s) -- "
                    "re-add/re-commission it", node_id, e)
        self._entries = entries

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

    def add(self, node_id: str, device_name: Optional[str] = None,
             # A frozenset literal default is safe here (unlike the usual
             # mutable-default-argument trap) since frozensets are immutable.
             sensor_config: FrozenSet[SensorChannel] = frozenset(
                 {SensorChannel.MIC, SensorChannel.ACCEL_X,
                  SensorChannel.ACCEL_Y, SensorChannel.ACCEL_Z}),
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
                device_name=device_name or node_id,
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

    def rename(self, node_id: str, device_name: str) -> RegistryEntry:
        with self._lock_for(node_id):
            entry = self.get(node_id)
            entry.device_name = device_name
            self._save()
            return entry

    def set_device_type(self, node_id: str, device_type: Optional[str]) -> RegistryEntry:
        """device_type is the scoping key for capture/label grouping (docs/
        EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md S1) -- set independently of
        rename() since a device can be renamed without changing what kind of
        machine it is, and vice versa. None clears it back to unassigned."""
        with self._lock_for(node_id):
            entry = self.get(node_id)
            entry.device_type = device_type
            self._save()
            return entry

    def set_trip_motor(self, node_id: str, motor_idx: Optional[int]) -> RegistryEntry:
        """Arms (or disarms) this asset's machinery-protection trip output by
        pointing it at one motor on the rig, 1-based (docs/
        MOTOR_STOP_PLAN.md). None clears it back to "no trip output", which
        is the default for every asset.

        Set independently of rename()/set_device_type() for the same reason
        those two are independent of each other: which physical actuator an
        asset can stop is unrelated to what it's called or what kind of
        machine it is.

        One motor, one asset: a motor already claimed by a different node is
        rejected rather than silently stolen, because two assets pointing at
        the same motor would make a trip from either of them look like it
        came from both."""
        with self._lock_for(node_id):
            entry = self.get(node_id)
            if motor_idx is not None:
                if motor_idx < 1:
                    raise ValueError(
                        f"motor_idx must be 1-based and positive, got {motor_idx}")
                # Deliberately not under the other node's lock: this reads a
                # plain field off an already-loaded entry, and taking a
                # second per-node lock here (while holding this node's) is
                # exactly the shape that deadlocks against a concurrent
                # set_trip_motor() running the mirror-image comparison.
                for other_id, other in self._entries.items():
                    if other_id != node_id and other.trip_motor_idx == motor_idx:
                        raise TripMotorInUseError(
                            f"motor {motor_idx} is already the trip output for node "
                            f"{other_id!r}")
            entry.trip_motor_idx = motor_idx
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
        # No self._save() here, deliberately -- this fires on EVERY ingested
        # frame (pipeline/manager.py's route(), several times a second per
        # active node), and last_seen is purely a liveness heartbeat: the
        # frontend derives online/offline from staleness itself (app.js's
        # bucketFor()), and a stale/missing value after a restart is exactly
        # as correct as a persisted-but-now-ancient one (both read as
        # "offline" until the next real frame arrives). Persisting it
        # synchronously bought nothing but turned every frame into a full
        # registry.json rewrite (mkstemp+json.dump+os.replace) while holding
        # this node's lock -- the same lock REST mutations (pause/rename/
        # commission/...) block on, so any storage hiccup on this device
        # (confirmed: several years' worth of orphaned zero-byte
        # .registry-*.tmp files under data/, evidence of writes getting
        # killed mid-flight) turned into a total dashboard freeze: ingestion
        # stalls inside the lock, REST's worker threadpool piles up waiting
        # on it, WS broadcasts (fired only after route() returns) stop, and
        # the node eventually reads as offline once last_seen goes stale.
        # See record_anomaly_score()/record_classification() below for the
        # same fix, same reasoning.
        with self._lock_for(node_id):
            entry = self.get(node_id)
            entry.last_seen = time.time() if timestamp is None else timestamp
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
        Supports the three confirmable statuses (healthy/warning/fault) plus
        the two machinery-protection ones (idle/tripped, see NodeStatus) --
        every other transition has its own dedicated method."""
        with self._lock_for(node_id):
            entry = self.get(node_id)
            event_name = _SET_STATUS_EVENT.get(status)
            if event_name is None:
                raise InvalidTransitionError(
                    f"cannot set node {node_id!r} to {status.value!r}: set_status only "
                    f"supports {_SET_STATUS_SETTABLE}")
            try:
                getattr(_NodeStateMachine(model=entry, state_field="status"), event_name)()
            except TransitionNotAllowed:
                raise InvalidTransitionError(
                    f"cannot set node {node_id!r} to {status.value!r} from "
                    f"{entry.status.value!r}: that transition is not allowed")
            self._save()
            self._notify_status_change(node_id, entry.status)
            return entry

    def complete_commissioning(self, node_id: str, model_path: str,
                                 timestamp: Optional[float] = None,
                                 warning_threshold: Optional[float] = None,
                                 fault_threshold: Optional[float] = None,
                                 scalar_mu: Optional[Tuple[float, ...]] = None,
                                 scalar_sigma: Optional[Tuple[float, ...]] = None,
                                 running_energy_ref: Optional[float] = None) -> RegistryEntry:
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
            # Thresholds/scalar standardization stats are all calibrated from
            # this run's healthy scores/batch (commissioning.py); on
            # re-commissioning they're overwritten with the fresh values,
            # matching model_path's overwrite behavior.
            if warning_threshold is not None:
                entry.warning_threshold = warning_threshold
            if fault_threshold is not None:
                entry.fault_threshold = fault_threshold
            if scalar_mu is not None:
                entry.scalar_mu = scalar_mu
            if scalar_sigma is not None:
                entry.scalar_sigma = scalar_sigma
            if running_energy_ref is not None:
                entry.running_energy_ref = running_energy_ref
            self._save()
            self._notify_status_change(node_id, entry.status)
            return entry

    def record_anomaly_score(self, node_id: str, score: float) -> RegistryEntry:
        """Latest combined anomaly score, written on every inference frame
        (pipeline/manager.py's MotorPipeline.handle_frame(), alongside the
        existing history_store.record() call) so Fleet cards can show a
        node's anomaly index without per-node history polling (dashboard
        redesign S5.1).

        No self._save() here -- same reasoning as touch_last_seen() above:
        this is a per-frame-refreshed live cache (the durable history is
        history_store, a separate sqlite write, not this field), not
        something that needs to survive a restart. Was one of two other
        per-frame callers (with record_classification() below) piling
        redundant full-registry disk writes onto the same hot path/lock."""
        with self._lock_for(node_id):
            entry = self.get(node_id)
            entry.last_anomaly_score = score
            return entry

    def record_classification(self, node_id: str, result: dict) -> RegistryEntry:
        """Latest fault-classifier result, written on every gated frame a
        classifier model actually scored (pipeline/manager.py's
        MotorPipeline.handle_frame(), alongside record_anomaly_score()
        above) -- same "Fleet card reads this without per-node history
        polling" shape.

        No self._save() here -- same reasoning as touch_last_seen()/
        record_anomaly_score() above."""
        with self._lock_for(node_id):
            entry = self.get(node_id)
            entry.last_classification = result
            return entry
