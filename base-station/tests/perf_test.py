#!/usr/bin/env python3
"""
Milestone 12 verification: verify
per-pipeline + system stats appear when the performance monitor is
enabled, disappear (and stop being recorded, i.e. overhead drops) when
disabled, and are sane under multi-pipeline load -- covering
monitoring/perf.py directly, its wiring into pipeline/manager.py (M3),
and its exposure over api/app.py (M10).

Migrated by the FastAPI migration (see
docs/CLAUDE_CODE_START_PROMPT_fastapi_migration.md Step 4) from raw
sockets/http.client driving api/rest.py + api/websocket.py to FastAPI's
TestClient driving api/app.py in-process.

Run with PYTHONPATH covering base-station/python/ingestion, base-station/python/registry, base-station/python/pipeline,
base-station/python/history, base-station/python/api, base-station/python/monitoring:
    PYTHONPATH=base-station/python/ingestion:base-station/python/registry:base-station/python/pipeline:base-station/python/history:base-station/python/api:base-station/python/monitoring \\
        python3 base-station/tests/perf_test.py
"""
import os
import sys
import tempfile

from fastapi.testclient import TestClient

from sensor_frame import FrameSource, SensorFrame
from registry import Registry, SensorChannel
from manager import PipelineManager
from perf import PerformanceMonitor
from app import create_app
from commissioning_controller import CommissioningController
from capture_controller import CaptureController
from gate import MotorStateGate

NODE_A = "node-a"
NODE_B = "node-b"


def frame(node_id, timestamp=0.0, source=FrameSource.SPI) -> SensorFrame:
    # 128 bins to match the registry's MIC-only spectral input_dim
    # (manager.py's ingest-time frame-length check rejects any other count
    # -- it also expects a 6-value scalar tail on top, but that's computed
    # from channel membership alone, not read from frame.scalars, so these
    # StubPipeline-only frames -- no node here ever gets commissioned --
    # don't need one).
    return SensorFrame(node_id=node_id, source=source, timestamp=timestamp,
                        bins={"mic": tuple(float(i % 3 + 1) for i in range(128))})


def test_disabled_monitor_reports_nothing():
    monitor = PerformanceMonitor(enabled=False)
    monitor.record_frame(NODE_A, 0.01, now=0.0)
    monitor.record_frame(NODE_A, 0.02, now=1.0)

    snapshot = monitor.snapshot()
    assert snapshot.enabled is False, snapshot
    assert snapshot.system is None, snapshot
    assert snapshot.pipelines == {}, snapshot
    print("disabled monitor records nothing and reports no stats: PASS")


def test_enabled_monitor_tracks_latency_and_rate():
    monitor = PerformanceMonitor(enabled=True, window_size=10)
    monitor.record_frame(NODE_A, 0.01, now=0.0)
    monitor.record_frame(NODE_A, 0.02, now=1.0)
    monitor.record_frame(NODE_A, 0.01, now=2.0)

    stats = monitor.snapshot().pipelines[NODE_A]
    assert stats.frame_count == 3, stats
    assert abs(stats.avg_latency_ms - (0.01 + 0.02 + 0.01) / 3 * 1000.0) < 1e-6, stats
    assert abs(stats.frames_per_sec - 1.0) < 1e-6, stats
    assert stats.falling_behind is False, stats
    print("enabled monitor tracks per-pipeline latency/rate correctly: PASS")


def test_falling_behind_detected_when_latency_exceeds_arrival_interval():
    monitor = PerformanceMonitor(enabled=True, window_size=10)
    # Frames arrive once per second but each takes 2s to process --
    # can't keep up in real time.
    monitor.record_frame(NODE_A, 2.0, now=0.0)
    monitor.record_frame(NODE_A, 2.0, now=1.0)
    monitor.record_frame(NODE_A, 2.0, now=2.0)

    stats = monitor.snapshot().pipelines[NODE_A]
    assert stats.falling_behind is True, stats
    print("falling-behind pipeline (latency > arrival interval) is flagged: PASS")


def test_system_stats_sane_under_multiple_pipelines():
    monitor = PerformanceMonitor(enabled=True, window_size=10)
    for i in range(5):
        monitor.record_frame(NODE_A, 0.005, now=float(i))
        monitor.record_frame(NODE_B, 0.01, now=float(i))

    snapshot = monitor.snapshot()
    assert snapshot.system.pipeline_count == 2, snapshot.system
    assert snapshot.system.process_cpu_percent >= 0.0, snapshot.system
    assert snapshot.system.process_memory_mb > 0.0, snapshot.system
    assert snapshot.system.frames_per_sec > 0.0, snapshot.system
    assert snapshot.system.falling_behind_count == 0, snapshot.system
    assert len(snapshot.system.cpu_percent_per_core) >= 1, snapshot.system
    assert snapshot.system.system_memory_total_mb > 0.0, snapshot.system
    assert snapshot.system.system_memory_used_mb > 0.0, snapshot.system
    assert isinstance(snapshot.system.ingest_fps_by_transport, dict), snapshot.system
    # None (no thermal zone exposed) or a plausible reading -- never a fake
    # 0.0 -- see monitoring/perf.py's _read_cpu_temp_celsius().
    assert snapshot.system.cpu_temp_celsius is None or snapshot.system.cpu_temp_celsius > 0, snapshot.system
    print("system-wide stats are sane across multiple concurrent pipelines: PASS")


def test_toggle_hides_and_restores_visibility():
    monitor = PerformanceMonitor(enabled=True, window_size=10)
    monitor.record_frame(NODE_A, 0.01, now=0.0)
    assert monitor.snapshot().enabled is True

    monitor.disable()
    assert monitor.snapshot().pipelines == {}, monitor.snapshot()

    monitor.enable()
    stats = monitor.snapshot().pipelines[NODE_A]
    assert stats.frame_count == 1, stats
    print("disable/enable toggles stats visibility, prior data survives the toggle: PASS")


def gate_factory() -> MotorStateGate:
    return MotorStateGate(threshold=0.5, debounce_frames=1)


def test_pipeline_manager_reports_frame_counts_matching_stub_pipelines(tmp_dir):
    registry = Registry(os.path.join(tmp_dir, "registry.json"))
    monitor = PerformanceMonitor(enabled=True, window_size=10)
    manager = PipelineManager(registry, gate_factory, perf_monitor=monitor)

    for i in range(3):
        manager.route(frame(NODE_A, timestamp=float(i)))
    manager.route(frame(NODE_B, timestamp=10.0))

    pipelines = manager.pipelines()
    perf_pipelines = monitor.snapshot().pipelines
    assert perf_pipelines[NODE_A].frame_count == pipelines[NODE_A].frame_count == 3
    assert perf_pipelines[NODE_B].frame_count == pipelines[NODE_B].frame_count == 1
    print("PipelineManager-recorded perf stats match each StubPipeline's frame_count: PASS")


def test_disabling_monitor_stops_new_recordings_without_affecting_routing(tmp_dir):
    registry = Registry(os.path.join(tmp_dir, "registry.json"))
    monitor = PerformanceMonitor(enabled=True, window_size=10)
    manager = PipelineManager(registry, gate_factory, perf_monitor=monitor)

    manager.route(frame(NODE_A, timestamp=0.0))
    manager.route(frame(NODE_A, timestamp=1.0))
    assert monitor.snapshot().pipelines[NODE_A].frame_count == 2

    monitor.disable()
    manager.route(frame(NODE_A, timestamp=2.0))
    manager.route(frame(NODE_A, timestamp=3.0))

    # Domain-layer routing is unaffected by the perf toggle -- the
    # StubPipeline still saw every frame.
    assert manager.pipelines()[NODE_A].frame_count == 4
    # But perf recording stopped the instant it was disabled: no new
    # samples were added (still frozen at 2), and the snapshot itself
    # hides pipelines entirely while disabled either way.
    assert monitor.snapshot().pipelines == {}
    monitor.enable()
    assert monitor.snapshot().pipelines[NODE_A].frame_count == 2
    print("disabling the monitor stops new recordings but never blocks pipeline routing: PASS")


def test_ingest_fps_by_transport_tracks_manager_routed_frames(tmp_dir):
    """The footer needs separate MQTT (satellites) vs SPI link (base
    station) ingestion rates, not one aggregate figure -- confirm
    PipelineManager.route() tags each frame's transport correctly."""
    registry = Registry(os.path.join(tmp_dir, "registry.json"))
    monitor = PerformanceMonitor(enabled=True, window_size=10)
    manager = PipelineManager(registry, gate_factory, perf_monitor=monitor)

    for i in range(3):
        manager.route(frame(NODE_A, timestamp=float(i), source=FrameSource.SPI))
    for i in range(3):
        manager.route(frame(NODE_B, timestamp=float(i), source=FrameSource.MQTT))

    transports = monitor.snapshot().system.ingest_fps_by_transport
    assert transports.get("spi_link", 0.0) > 0.0, transports
    assert transports.get("mqtt", 0.0) > 0.0, transports
    print("ingest_fps_by_transport reports separate MQTT/SPI link rates: PASS")


def test_rest_perf_endpoints_and_websocket_broadcast(tmp_dir):
    registry = Registry(os.path.join(tmp_dir, "registry.json"))
    registry.add(NODE_A, sensor_config=frozenset({SensorChannel.MIC}))
    monitor = PerformanceMonitor(enabled=True, window_size=10)
    manager = PipelineManager(registry, gate_factory, perf_monitor=monitor)
    manager.route(frame(NODE_A, timestamp=0.0))
    manager.route(frame(NODE_A, timestamp=1.0))

    from store import HistoryStore
    history = HistoryStore(os.path.join(tmp_dir, "history.db"))
    commissioning = CommissioningController(registry, os.path.join(tmp_dir, "models"),
                                             gate_factory, min_frames=5)
    capture = CaptureController(registry, os.path.join(tmp_dir, "captures"), gate_factory)
    app = create_app(registry, history, commissioning, capture, manager=manager, perf_monitor=monitor)

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            def request(method, path, body=None):
                resp = client.request(method, path, json=body)
                return resp.status_code, resp.json()

            status, body = request("GET", "/perf")
            assert status == 200, (status, body)
            assert body["enabled"] is True, body
            assert body["pipelines"][NODE_A]["frame_count"] == 2, body

            status, body = request("POST", "/perf/disable")
            assert status == 200 and body["enabled"] is False, (status, body)

            message = ws.receive_json()
            assert message == {"type": "perf", "enabled": False}, message

            status, body = request("GET", "/perf")
            assert status == 200 and body["enabled"] is False and body["pipelines"] == {}, body

            status, body = request("POST", "/perf/enable")
            assert status == 200 and body["enabled"] is True, (status, body)
            message = ws.receive_json()
            assert message == {"type": "perf", "enabled": True}, message

            status, body = request("GET", "/perf")
            assert status == 200 and body["pipelines"][NODE_A]["frame_count"] == 2, body

        print("GET/POST /perf reflect toggle state and broadcast over WebSocket: PASS")


def main():
    tmp_dir = tempfile.mkdtemp(prefix="perf_test_")

    test_disabled_monitor_reports_nothing()
    test_enabled_monitor_tracks_latency_and_rate()
    test_falling_behind_detected_when_latency_exceeds_arrival_interval()
    test_system_stats_sane_under_multiple_pipelines()
    test_toggle_hides_and_restores_visibility()
    test_pipeline_manager_reports_frame_counts_matching_stub_pipelines(
        tempfile.mkdtemp(dir=tmp_dir))
    test_disabling_monitor_stops_new_recordings_without_affecting_routing(
        tempfile.mkdtemp(dir=tmp_dir))
    test_ingest_fps_by_transport_tracks_manager_routed_frames(tempfile.mkdtemp(dir=tmp_dir))
    test_rest_perf_endpoints_and_websocket_broadcast(tempfile.mkdtemp(dir=tmp_dir))

    print("RESULT: PASS - perf stats appear when enabled, disappear when disabled, "
          "and stay sane across multiple concurrently-routed pipelines")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"RESULT: FAIL - {e}")
        sys.exit(1)
