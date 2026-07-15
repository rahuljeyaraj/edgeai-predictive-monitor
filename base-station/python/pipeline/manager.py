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
                       NodeStatus, input_dim_for)
from gate import MotorStateGate
from inference import InferenceError, InferencePipeline

if TYPE_CHECKING:
    # Type-only -- manager.py has no runtime dependency on monitoring/ or
    # history/, so callers that don't need perf tracking or scoring (e.g.
    # earlier milestones' tests) don't need those packages on PYTHONPATH.
    from perf import PerformanceMonitor
    from store import HistoryStore

logger = logging.getLogger(__name__)

_DEFAULT_STATUS_DEBOUNCE_FRAMES = 3

# FrameSource.value -> footer perf monitor's transport label ("MQTT
# (satellites) / SPI link (base station)", not FrameSource's own "spi" --
# spi_link is the dedicated MCU<->MPU SPI transport the base station's
# ingestion/spi_reader.py actually reads, "spi" is just the source enum's
# generic name for it).
_INGEST_TRANSPORT_LABEL = {"spi": "spi_link", "mqtt": "mqtt"}


def _infer_sensor_config(frame: SensorFrame) -> FrozenSet[SensorChannel]:
    """Derived from which channel keys are present on this node's first
    frame (S4.2: sensor_config drives 512 vs 1024 model dim).

    A multi-channel MQTT node's first frame must already carry every
    channel it will ever report (mqtt_subscriber.py's fused "channels"
    payload, Appendix B S3) -- sensor_config is committed once here and
    every later frame is validated against it in full (_validate_frame_bins
    below), so a node can't grow a second channel mid-stream.
    """
    channels = set()
    for key in frame.bins:
        channels.add(SensorChannel(key))
    return frozenset(channels)


def _validate_frame_bins(frame: SensorFrame, entry: RegistryEntry) -> None:
    """Raise loudly if the frame's bin counts don't match the input dim
    implied by the node's committed sensor_config. input_dim is a
    training-time commitment declared once at commissioning, not
    something to infer from whatever a frame happens to contain --
    a mismatch here means firmware drift, a malformed frame, or a
    protocol version skew, and should fail loudly rather than silently
    corrupt training data or inference."""
    actual = sum(len(frame.bins.get(c.value, ())) for c in entry.sensor_config)
    expected = input_dim_for(entry.sensor_config)
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
                 gate_factory: Callable[[], MotorStateGate],
                 debounce_frames: int, history_store: Optional["HistoryStore"],
                 on_score: Optional[Callable[[str, float, float, NodeStatus], None]] = None):
        self.node_id = node_id
        self.frame_count = 0
        self._registry = registry
        self._gate_factory = gate_factory
        self._debounce_frames = debounce_frames
        self._history_store = history_store
        self._on_score = on_score
        self._inference: Optional[InferencePipeline] = None

    def handle_frame(self, frame: SensorFrame) -> None:
        self.frame_count += 1

        if self._inference is None:
            entry = self._registry.get(self.node_id)
            if not entry.model_path:
                return  # not commissioned yet -- nothing to score
            try:
                self._inference = InferencePipeline(
                    self._registry, self.node_id, self._gate_factory(),
                    self._debounce_frames)
            except InferenceError:
                # Registry said model_path was set but the file vanished
                # between the check and the load (e.g. re-commissioning
                # mid-write) -- try again on the next frame rather than
                # wedging this pipeline permanently.
                return

        score = self._inference.handle_frame(frame)
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


class PipelineManager:
    """One instance owns all per-motor pipelines for this process.
    route() is the only entry point ingestion needs to call."""

    def __init__(self, registry: Registry, gate_factory: Callable[[], MotorStateGate],
                 perf_monitor: Optional["PerformanceMonitor"] = None,
                 history_store: Optional["HistoryStore"] = None,
                 status_debounce_frames: int = _DEFAULT_STATUS_DEBOUNCE_FRAMES,
                 on_score: Optional[Callable[[str, float, float, NodeStatus], None]] = None):
        self._registry = registry
        self._gate_factory = gate_factory
        self._perf_monitor = perf_monitor
        self._history_store = history_store
        self._status_debounce_frames = status_debounce_frames
        self._on_score = on_score
        self._pipelines: Dict[str, MotorPipeline] = {}

    def route(self, frame: SensorFrame) -> Optional[MotorPipeline]:
        with self._registry.lock_for(frame.node_id):
            pipeline = self._pipelines.get(frame.node_id)
            if pipeline is None:
                pipeline = MotorPipeline(
                    frame.node_id, self._registry, self._gate_factory,
                    self._status_debounce_frames, self._history_store,
                    on_score=self._on_score)
                self._pipelines[frame.node_id] = pipeline
                self._registry.add(frame.node_id, sensor_config=_infer_sensor_config(frame))

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
                pipeline.handle_frame(frame)
                self._perf_monitor.record_frame(frame.node_id, time.perf_counter() - start)
            else:
                pipeline.handle_frame(frame)

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
