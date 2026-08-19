"""Pipeline Manager -- routes SensorFrames by node_id to per-motor
pipeline instances, per docs/MPU_Software_Architecture.md S5/S5.1.

Does no domain logic itself. Auto-creates a pipeline + registry entry
the first time a node_id is seen -- there's no separate "add node"
step before a motor can start reporting data; showing up on the wire
is enough.

MotorPipeline (below) replaces the M3-M9 placeholder StubPipeline now
that gate.py/features.py/autoencoder.py/inference.py all exist: a node
with no trained model yet still just counts frames (S3.8's "Add" flow --
a node can be seen and named before it's ever commissioned), and a node
that has been commissioned gets a real InferencePipeline scoring every
gated frame, with each score appended to HistoryStore (S3.4/S4.3/M9) so
the dashboard's per-motor graph (S3.9) has something to read.
"""
import logging
import time
from typing import TYPE_CHECKING, Callable, Dict, FrozenSet, Optional

from sensor_frame import SensorFrame
from registry import (Registry, RegistryEntry, SensorChannel, NodeNotFoundError,
                       NodeStatus)
from features import build_feature_vector, scalar_dim_for, standardize_scalars
from ei_scaling import get_scaling
from gate import MotorState, MotorStateGate
from inference import InferenceError, InferencePipeline

if TYPE_CHECKING:
    # Type-only -- manager.py has no runtime dependency on monitoring/ or
    # history/, so callers that don't need perf tracking or scoring (e.g.
    # earlier milestones' tests) don't need those packages on PYTHONPATH.
    # Same for classifier.py's ClassifierRegistry: manager.py only ever
    # calls .get() on an instance main.py already constructed, never
    # constructs one itself (unlike InferencePipeline above).
    from perf import PerformanceMonitor
    from store import HistoryStore
    from classifier import ClassifierRegistry

logger = logging.getLogger(__name__)

_DEFAULT_STATUS_DEBOUNCE_FRAMES = 3

# FrameSource.value -> footer perf monitor's transport label ("MQTT
# (satellites) / SPI link (base station)", not FrameSource's own "spi" --
# spi_link is the dedicated MCU<->MPU SPI transport the base station's
# ingestion/spi_reader.py actually reads, "spi" is just the source enum's
# generic name for it).
_INGEST_TRANSPORT_LABEL = {"spi": "spi_link", "mqtt": "mqtt"}


def _infer_sensor_config_and_dim(frame: SensorFrame) -> "tuple[FrozenSet[SensorChannel], int]":
    """Derived from which channel keys are present on this node's first
    frame, and each channel's actual bin count on that same frame (S4.2:
    sensor_config + per-channel bin count drives the model's input dim).
    Not every node necessarily uses the same FFT bin count per channel --
    this commits to whatever the node's own first frame actually sent,
    rather than a fixed global table (registry.input_dim_for()'s spectral-
    only per-channel default is only a fallback for callers with no frame to
    derive from -- see its docstring).

    A multi-channel MQTT node's first frame must already carry every
    channel it will ever report (mqtt_subscriber.py's fused "channels"
    payload, Appendix B S3) -- sensor_config AND input_dim are committed
    once here and every later frame is validated against them in full
    (_validate_frame_bins below), so a node can't grow a second channel or
    change its bin count mid-stream.

    Display-only spectrum channels (e.g. the per-axis accel_x/y/z overlay,
    docs/CHART_CLUTTER_PLAN.md S1) are kept out of frame.bins entirely
    (SensorFrame.display_bins instead, split out by the ingestion layer) so
    this should never actually see a non-SensorChannel key -- the try/except
    below is defensive depth against schema/ingestion drift (a new channel
    added to telemetry_schema.json without updating the ingestion split),
    not a routine path, mirroring this file's existing defensive-depth
    comment on the NodeNotFoundError branch in route() below.
    """
    channels = set()
    dim = 0
    for key, bins in frame.bins.items():
        try:
            channel = SensorChannel(key)
        except ValueError:
            continue
        channels.add(channel)
        dim += len(bins)
    channels = frozenset(channels)
    # Scalar tail (features.py's build_feature_vector) rides on top of the
    # spectral bins summed above but isn't itself a frame.bins entry -- it
    # comes from frame.scalars, keyed by channel-suffixed name (e.g.
    # "rms_x"), not a SensorChannel value. Without this, a fresh node would
    # commit an input_dim short by exactly this many columns, and every
    # frame afterward would fail _validate_frame_bins below forever (silent,
    # permanent frame-dropping, not a crash -- see that function's comment).
    dim += scalar_dim_for(channels)
    return channels, dim


def _validate_frame_bins(frame: SensorFrame, entry: RegistryEntry) -> None:
    """Raise loudly if the frame's bin counts don't match the input dim
    the node committed to on its first frame (entry.input_dim -- see
    _infer_sensor_config_and_dim). input_dim is a training-time commitment
    declared once, not something to re-derive from a global table on every
    call: a mismatch here means firmware drift, a malformed frame, a live
    reconfiguration mid-session, or a protocol version skew, and should fail
    loudly rather than silently corrupt training data or inference."""
    actual = (sum(len(frame.bins.get(c.value, ())) for c in entry.sensor_config)
              + scalar_dim_for(entry.sensor_config))
    expected = entry.input_dim
    if actual != expected:
        raise ValueError(
            f"node {frame.node_id!r}: expected {expected} bins for sensor_config "
            f"{sorted(c.value for c in entry.sensor_config)}, got {actual}")


class MotorPipeline:
    """Real per-motor pipeline: gate -> features -> autoencoder -> score,
    lazily switching from "just count frames" to "run inference" the
    first time this node has a commissioned model. One instance per
    node_id, owned by PipelineManager."""

    def __init__(self, node_id: str, registry: Registry,
                 gate_factory: Callable[[str], MotorStateGate],
                 debounce_frames: int, history_store: Optional["HistoryStore"],
                 on_score: Optional[Callable[[str, float, float, NodeStatus], None]] = None,
                 classifier_registry: Optional["ClassifierRegistry"] = None,
                 scaling_path: Optional[str] = None,
                 on_classification: Optional[Callable[[str, float, dict], None]] = None,
                 on_motor_state: Optional[Callable[[str, bool], None]] = None,
                 trip_pending: Optional[Callable[[str], bool]] = None):
        self.node_id = node_id
        self.frame_count = 0
        self._registry = registry
        self._gate_factory = gate_factory
        self._debounce_frames = debounce_frames
        self._history_store = history_store
        self._on_score = on_score
        self._classifier_registry = classifier_registry
        self._scaling_path = scaling_path
        self._on_classification = on_classification
        self._trip_pending = trip_pending
        # Independent of self._inference's own internal gate below --
        # the classifier runs "in parallel with the autoencoder ... both
        # always-on independently whenever a model exists for that node's
        # device type" (docs/EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md S6),
        # i.e. it must not require this node to ever have been commissioned
        # (self._inference stays None until it has). Two gate instances
        # debouncing the same frames independently is a little duplicated
        # state, but far simpler than threading a shared gate through
        # InferencePipeline's constructor just to avoid it.
        self._classification_gate = gate_factory(node_id)
        self._inference: Optional[InferencePipeline] = None
        self._commissioned_at: Optional[float] = None
        self._paused_since_last_frame = False
        self._on_motor_state = on_motor_state
        # Edge-triggered, so protection/ hears each stop/start once rather
        # than on every frame. None until the gate has confirmed RUNNING at
        # least once -- see _report_motor_state for why the initial STOPPED
        # default must not be reported as a real observation.
        self._motor_running: Optional[bool] = None
        self._seen_running = False

    @property
    def motor_running(self) -> Optional[bool]:
        """Live read of the gate's confirmed state -- unlike on_motor_state's
        edge callback (which only fires when the state *changes*), this
        answers "is it stopped right now" even if the gate already confirmed
        STOPPED before whoever's asking started watching. None before a
        model exists to gate against."""
        if self._inference is None:
            return None
        return self._inference.motor_state == MotorState.RUNNING

    def handle_frame(self, frame: SensorFrame, status: NodeStatus) -> None:
        self.frame_count += 1

        if status == NodeStatus.PAUSED:
            # An operator pause must freeze scoring itself, not just the
            # confirmed status -- InferencePipeline already refuses to
            # *write* PAUSED -> HEALTHY/WARNING/FAULT (InvalidTransitionError,
            # inference.py), but it was still computing/recording/broadcasting
            # a fresh reconstruction error every frame underneath that,
            # which is what made the dashboard's anomaly score keep moving
            # while the node showed paused.
            self._paused_since_last_frame = True
            return

        entry = self._registry.get(self.node_id)

        self._maybe_classify(frame, entry)

        if not entry.model_path:
            return  # not commissioned yet -- nothing to score

        if self._inference is None or entry.last_commissioned != self._commissioned_at:
            # Rebuild whenever this node has (re-)commissioned since the
            # cached pipeline was built -- otherwise a node re-commissioned
            # while this process keeps running silently keeps scoring
            # against the pre-recommission model/thresholds/standardization
            # forever (self._inference used to be built once and never
            # invalidated), even though the registry -- and the dashboard,
            # which reads thresholds straight from it -- already show the
            # new calibration. That's what let a live score sit 50+x over
            # the *displayed* fault_threshold while status stayed healthy:
            # the number on screen wasn't the number actually driving
            # set_status() underneath.
            try:
                self._inference = InferencePipeline(
                    self._registry, self.node_id, self._gate_factory(self.node_id),
                    self._debounce_frames)
            except InferenceError:
                # Registry said model_path was set but the file vanished
                # between the check and the load (e.g. re-commissioning
                # mid-write) -- try again on the next frame rather than
                # wedging this pipeline permanently.
                return
            self._commissioned_at = entry.last_commissioned
        elif self._paused_since_last_frame:
            # Resumed with no re-commission in between (the branch above
            # already reads the registry's current, correct status fresh,
            # so it doesn't need this) -- registry.resume() just forced
            # entry.status to HEALTHY out from under this cached pipeline,
            # but self._inference's own ._status is still whatever it was
            # pre-pause. Re-sync it so a post-resume score landing back in
            # that same pre-pause zone isn't mistaken for "no change" (see
            # reset_to_healthy's docstring).
            self._inference.reset_to_healthy()

        self._paused_since_last_frame = False

        confirm = not (self._trip_pending is not None and self._trip_pending(self.node_id))
        score = self._inference.handle_frame(frame, confirm=confirm)
        self._report_motor_state(self._inference.motor_state)
        if score is not None:
            self._registry.record_anomaly_score(self.node_id, score)
            if self._history_store is not None:
                self._history_store.record(self.node_id, frame.timestamp, score,
                                            self._inference.status)
            if self._on_score is not None:
                # Fired synchronously, on every gated/scored frame -- this is
                # what lets the dashboard's anomaly timeline push over /ws at
                # the same cadence real inference actually runs at, instead
                # of only sampling registry.last_anomaly_score once per 5s
                # REST poll (dashboard redesign follow-up: the poll made the
                # anomaly plot look throttled relative to the per-frame
                # waterfall/spectrum WS push, even though scoring itself
                # already runs per gated frame).
                self._on_score(self.node_id, frame.timestamp, score, self._inference.status)

    def _report_motor_state(self, state: MotorState) -> None:
        """Fire on_motor_state only when the confirmed running/stopped state
        actually changes, so protection/ sees one event per stop rather than
        one per frame.

        Suppressed until the gate has confirmed RUNNING at least once:
        MotorStateGate starts at STOPPED by construction and needs
        debounce_frames of agreement to leave it, so the first few frames of
        every fresh pipeline report STOPPED regardless of what the machine is
        doing. Reporting that would flash every node through IDLE on each
        process restart and each re-commission. The cost is that a machine
        already stopped when this process started stays at its last persisted
        status until it runs once -- which is honest: we have no observation
        of it running to compare against yet."""
        if self._on_motor_state is None:
            return
        running = state == MotorState.RUNNING
        if running:
            self._seen_running = True
        elif not self._seen_running:
            return
        if running == self._motor_running:
            return
        self._motor_running = running
        if running and self._inference is not None:
            # The other half of the recovery fix in protection.py's
            # on_motor_state: that call forces the registry back to HEALTHY,
            # and this re-syncs this pipeline's own cached confirmed status to
            # match. Skip it and the two disagree -- the registry reads
            # HEALTHY while this cache still holds the pre-stop status, so the
            # first score landing back in that same zone is mistaken for "no
            # change" and never re-confirms. Identical trap to the
            # pause/resume one reset_to_healthy() was written for.
            self._inference.reset_to_healthy()
        self._on_motor_state(self.node_id, running)

    def _maybe_classify(self, frame: SensorFrame, entry: RegistryEntry) -> None:
        """Fault classification (docs/EDGE_IMPULSE_FAULT_CLASSIFICATION_PLAN.md
        S4 T5): a no-op unless classifier_registry was wired in AND
        entry.device_type has a fetched model -- "no device_type, or type
        has no model -> anomaly score only" (docs/
        EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md S1). Deliberately runs
        ahead of the `not entry.model_path` early-return above: this must
        work for a node that's never been commissioned at all, not only
        ones with a trained autoencoder."""
        if self._classifier_registry is None or not entry.device_type:
            return
        classifier = self._classifier_registry.get(entry.device_type)
        if classifier is None:
            return
        if self._classification_gate.update(frame) != MotorState.RUNNING:
            return

        scaling = get_scaling(self._scaling_path, entry.device_type) if self._scaling_path else None
        if scaling is None:
            # Upload() (api/ei_controller.py) always fits+saves this before
            # a model becomes fetchable, so reaching this means the scaling
            # store was removed/pointed elsewhere independently of the
            # model file -- skip rather than classify against an
            # unstandardized vector, which would silently produce garbage
            # confidences (docs/EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md S8.4).
            logger.warning(
                "device_type %r has a fetched classifier model but no scalar-tail "
                "baseline in ei_scaling.json -- skipping classification for node %r",
                entry.device_type, self.node_id)
            return

        # scaling["spectral_dim"] (the EI baseline's own recorded value at
        # fit time), not a freshly-recomputed one -- ties standardization
        # to exactly what the baseline was fit against.
        vector, _ = build_feature_vector(frame, entry.sensor_config, entry.input_dim)
        vector = standardize_scalars(vector, scaling["spectral_dim"],
                                      tuple(scaling["mu"]), tuple(scaling["sigma"]))
        try:
            scores = classifier.classify(vector)
        except ValueError:
            logger.exception(
                "classification failed for node %r (device_type %r) -- label list is "
                "likely stale for the currently-loaded model", self.node_id, entry.device_type)
            return

        label = max(scores, key=scores.get)
        result = {"label": label, "confidence": scores[label], "scores": scores, "ts": frame.timestamp}
        self._registry.record_classification(self.node_id, result)
        if self._on_classification is not None:
            self._on_classification(self.node_id, frame.timestamp, result)


class PipelineManager:
    """One instance owns all per-motor pipelines for this process.
    route() is the only entry point ingestion needs to call."""

    def __init__(self, registry: Registry, gate_factory: Callable[[str], MotorStateGate],
                 perf_monitor: Optional["PerformanceMonitor"] = None,
                 history_store: Optional["HistoryStore"] = None,
                 status_debounce_frames: int = _DEFAULT_STATUS_DEBOUNCE_FRAMES,
                 on_score: Optional[Callable[[str, float, float, NodeStatus], None]] = None,
                 classifier_registry: Optional["ClassifierRegistry"] = None,
                 scaling_path: Optional[str] = None,
                 on_classification: Optional[Callable[[str, float, dict], None]] = None,
                 on_motor_state: Optional[Callable[[str, bool], None]] = None,
                 trip_pending: Optional[Callable[[str], bool]] = None):
        self._registry = registry
        self._gate_factory = gate_factory
        self._perf_monitor = perf_monitor
        self._history_store = history_store
        self._status_debounce_frames = status_debounce_frames
        self._on_score = on_score
        self._classifier_registry = classifier_registry
        self._scaling_path = scaling_path
        self._on_classification = on_classification
        self._on_motor_state = on_motor_state
        self._trip_pending = trip_pending
        self._pipelines: Dict[str, MotorPipeline] = {}

    def route(self, frame: SensorFrame) -> Optional[MotorPipeline]:
        with self._registry.lock_for(frame.node_id):
            pipeline = self._pipelines.get(frame.node_id)
            if pipeline is None:
                pipeline = MotorPipeline(
                    frame.node_id, self._registry, self._gate_factory,
                    self._status_debounce_frames, self._history_store,
                    on_score=self._on_score, classifier_registry=self._classifier_registry,
                    scaling_path=self._scaling_path, on_classification=self._on_classification,
                    on_motor_state=self._on_motor_state, trip_pending=self._trip_pending)
                self._pipelines[frame.node_id] = pipeline
                sensor_config, input_dim = _infer_sensor_config_and_dim(frame)
                self._registry.add(frame.node_id, sensor_config=sensor_config, input_dim=input_dim)

            try:
                entry = self._registry.get(frame.node_id)
            except NodeNotFoundError:
                # Should be unreachable post-fix: decommission() evicts this
                # node's pipeline cache entry under this same lock, so
                # route() can never again find a cached pipeline for a node
                # whose registry entry is gone. Left in place as defensive
                # depth rather than removed.
                del self._pipelines[frame.node_id]
                return None

            _validate_frame_bins(frame, entry)

            self._registry.touch_last_seen(frame.node_id, timestamp=frame.timestamp)

            if self._perf_monitor is not None and self._perf_monitor.enabled:
                # Timing itself skipped, not just the recording, when
                # disabled -- S3.10's "disable entirely to save CPU" means
                # the perf_counter() calls, not only what's done with them.
                transport = _INGEST_TRANSPORT_LABEL.get(frame.source.value, frame.source.value)
                self._perf_monitor.record_ingest(transport, now=frame.timestamp)
                start = time.perf_counter()
                pipeline.handle_frame(frame, entry.status)
                self._perf_monitor.record_frame(frame.node_id, time.perf_counter() - start)
            else:
                pipeline.handle_frame(frame, entry.status)

            return pipeline

    def decommission(self, node_id: str) -> None:
        """Atomic decommission: registry lookup first (raises NodeNotFoundError
        with no side effects if unknown -- removable from any status
        otherwise, see registry.py's decommission()), then evict the cached
        pipeline and delete history -- all under this node's lock, borrowed
        from Registry rather than a second lock of our own."""
        with self._registry.lock_for(node_id):
            self._registry.decommission(node_id)
            self._pipelines.pop(node_id, None)
            if self._history_store is not None:
                self._history_store.delete(node_id)

    def pipelines(self) -> Dict[str, MotorPipeline]:
        return dict(self._pipelines)

    def is_running(self, node_id: str) -> Optional[bool]:
        """protection/'s trip-confirmation query hook -- see
        MotorPipeline.motor_running for why this has to be a live read
        rather than riding the on_motor_state edge callback. None if this
        node has no pipeline yet, or none scoring (not commissioned)."""
        pipeline = self._pipelines.get(node_id)
        if pipeline is None:
            return None
        return pipeline.motor_running
