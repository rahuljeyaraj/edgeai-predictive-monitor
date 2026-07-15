"""Performance monitor -- per-pipeline + system-wide CPU/memory/latency/
throughput stats, toggleable, per docs/MPU_Software_Architecture.md
S3.10/S8 M12.

pipeline/manager.py times each pipeline.handle_frame() call and reports
it here via record_frame() when a PerformanceMonitor is attached and
enabled -- this module never touches SensorFrame or pipeline internals
itself, matching S5.1's "cross-cutting, used by everything above" (same
role as Registry/History Store).

The toggle is load-bearing, not cosmetic (S3.10: "must be possible to
disable entirely to save CPU"): record_frame() is a single attribute
check when disabled, and PipelineManager skips the time.perf_counter()
calls around handle_frame() entirely rather than timing and discarding --
so the overhead this module exists to *report* isn't itself added back
by the reporting.
"""
import os
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional, Tuple

import psutil

_DEFAULT_WINDOW = 50


@dataclass
class PipelineStats:
    node_id: str
    frame_count: int
    avg_latency_ms: float
    frames_per_sec: float
    falling_behind: bool

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "frame_count": self.frame_count,
            "avg_latency_ms": self.avg_latency_ms,
            "frames_per_sec": self.frames_per_sec,
            "falling_behind": self.falling_behind,
        }


@dataclass
class SystemStats:
    process_cpu_percent: float
    process_memory_mb: float
    pipeline_count: int
    avg_latency_ms: float
    frames_per_sec: float
    falling_behind_count: int
    # Additive fields for the dashboard redesign's footer (S5.3): host-wide
    # figures, distinct from process_cpu_percent/process_memory_mb above
    # (this process only). avg_latency_ms above remains the closest
    # available proxy for "inter-core latency" -- no lower-level IPC timer
    # exists in this codebase to measure that directly.
    cpu_percent_per_core: List[float]
    system_memory_used_mb: float
    system_memory_total_mb: float
    ingest_fps_by_transport: Dict[str, float]

    def to_dict(self) -> dict:
        return {
            "process_cpu_percent": self.process_cpu_percent,
            "process_memory_mb": self.process_memory_mb,
            "pipeline_count": self.pipeline_count,
            "avg_latency_ms": self.avg_latency_ms,
            "frames_per_sec": self.frames_per_sec,
            "falling_behind_count": self.falling_behind_count,
            "cpu_percent_per_core": self.cpu_percent_per_core,
            "system_memory_used_mb": self.system_memory_used_mb,
            "system_memory_total_mb": self.system_memory_total_mb,
            "ingest_fps_by_transport": self.ingest_fps_by_transport,
        }


@dataclass
class PerfSnapshot:
    enabled: bool
    system: Optional[SystemStats]
    pipelines: Dict[str, PipelineStats]

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "system": self.system.to_dict() if self.system is not None else None,
            "pipelines": {node_id: stats.to_dict()
                          for node_id, stats in self.pipelines.items()},
        }


class _NodeWindow:
    """Rolling window of (arrival_time, processing_seconds) for one node,
    capped at `window_size` samples -- bounded memory regardless of how
    long a pipeline has been running. frame_count is tracked separately,
    uncapped, since S4.2-style "total frames seen" shouldn't reset just
    because the latency window rolled over."""

    __slots__ = ("frame_count", "samples")

    def __init__(self, window_size: int):
        self.frame_count = 0
        self.samples: Deque[Tuple[float, float]] = deque(maxlen=window_size)

    def record(self, now: float, processing_seconds: float) -> None:
        self.frame_count += 1
        self.samples.append((now, processing_seconds))

    def stats(self, node_id: str) -> PipelineStats:
        if not self.samples:
            return PipelineStats(node_id, self.frame_count, 0.0, 0.0, False)

        avg_latency_seconds = sum(s for _, s in self.samples) / len(self.samples)
        span = self.samples[-1][0] - self.samples[0][0]
        frames_per_sec = ((len(self.samples) - 1) / span
                           if len(self.samples) >= 2 and span > 0 else 0.0)
        # A pipeline is "falling behind" (S3.10: frames queuing/dropping)
        # when it takes longer to process a frame, on average, than
        # frames are arriving -- no queue exists yet to inspect directly
        # (manager.route() calls handle_frame() synchronously), so this
        # is the earliest observable proxy for that condition.
        avg_interval_seconds = 1.0 / frames_per_sec if frames_per_sec > 0 else float("inf")
        falling_behind = frames_per_sec > 0 and avg_latency_seconds > avg_interval_seconds

        return PipelineStats(
            node_id=node_id,
            frame_count=self.frame_count,
            avg_latency_ms=avg_latency_seconds * 1000.0,
            frames_per_sec=frames_per_sec,
            falling_behind=falling_behind,
        )


class _TransportWindow:
    """Rolling window of ingest arrival timestamps for one transport
    (mqtt/lpuart1), used only for the footer's separate MQTT-vs-LPUART1
    FPS figures (dashboard redesign S5.3). No processing-time/latency
    semantics needed here, so this doesn't reuse _NodeWindow -- that one's
    shape (arrival_time, processing_seconds) is built for a different job,
    per-node processing latency."""

    __slots__ = ("samples",)

    def __init__(self, window_size: int):
        self.samples: Deque[float] = deque(maxlen=window_size)

    def record(self, now: float) -> None:
        self.samples.append(now)

    def frames_per_sec(self) -> float:
        if len(self.samples) < 2:
            return 0.0
        span = self.samples[-1] - self.samples[0]
        return (len(self.samples) - 1) / span if span > 0 else 0.0


class PerformanceMonitor:
    """One instance per process, shared by PipelineManager (feeds
    record_frame per frame) and api/rest.py (reads snapshot() for GET
    /perf, toggles via enable()/disable()). Starts enabled -- S3.10 lists
    this as demo-visible by default, with disabling as the opt-out under
    load, not the other way round."""

    def __init__(self, enabled: bool = True, window_size: int = _DEFAULT_WINDOW):
        self.enabled = enabled
        self._window_size = window_size
        self._nodes: Dict[str, _NodeWindow] = {}
        self._transports: Dict[str, _TransportWindow] = {}
        self._process = psutil.Process(os.getpid())

    def enable(self) -> None:
        self.enabled = True

    def disable(self) -> None:
        self.enabled = False

    def record_frame(self, node_id: str, processing_seconds: float,
                      now: Optional[float] = None) -> None:
        if not self.enabled:
            return
        window = self._nodes.get(node_id)
        if window is None:
            window = _NodeWindow(self._window_size)
            self._nodes[node_id] = window
        window.record(time.monotonic() if now is None else now, processing_seconds)

    def record_ingest(self, transport: str, now: Optional[float] = None) -> None:
        if not self.enabled:
            return
        window = self._transports.get(transport)
        if window is None:
            window = _TransportWindow(self._window_size)
            self._transports[transport] = window
        window.record(time.monotonic() if now is None else now)

    def snapshot(self) -> PerfSnapshot:
        """Stats appear only while enabled -- returning an empty,
        system=None snapshot while disabled (rather than the last-known
        values) is what lets api/rest.py's GET /perf visibly reflect the
        toggle, per M12's verification method."""
        if not self.enabled:
            return PerfSnapshot(enabled=False, system=None, pipelines={})

        pipelines = {node_id: window.stats(node_id) for node_id, window in self._nodes.items()}

        total_latency_ms = sum(p.avg_latency_ms for p in pipelines.values())
        virtual_memory = psutil.virtual_memory()
        system = SystemStats(
            process_cpu_percent=self._process.cpu_percent(interval=None),
            process_memory_mb=self._process.memory_info().rss / (1024 * 1024),
            pipeline_count=len(pipelines),
            avg_latency_ms=(total_latency_ms / len(pipelines)) if pipelines else 0.0,
            frames_per_sec=sum(p.frames_per_sec for p in pipelines.values()),
            falling_behind_count=sum(1 for p in pipelines.values() if p.falling_behind),
            cpu_percent_per_core=psutil.cpu_percent(percpu=True),
            system_memory_used_mb=virtual_memory.used / (1024 * 1024),
            system_memory_total_mb=virtual_memory.total / (1024 * 1024),
            ingest_fps_by_transport={transport: window.frames_per_sec()
                                      for transport, window in self._transports.items()},
        )
        return PerfSnapshot(enabled=True, system=system, pipelines=pipelines)
