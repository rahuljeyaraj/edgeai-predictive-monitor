#!/usr/bin/env python3
"""Wiring only -- ported from edgeai-predictive-monitor-unoq/mpu/main.py.

Instantiates Registry/HistoryStore/PerformanceMonitor, builds the single
FastAPI app (REST + WebSocket push) and mounts the static dashboard
frontend on it, starts ingestion (this device's own sensors over the SPI
link, always on, plus optional MQTT satellite nodes), then runs the app
with uvicorn. Every routed frame is also broadcast as a "spectrum"
WebSocket message for the dashboard's live-spectrum/waterfall panels.

REST, WebSocket (/ws), and the static frontend are served from one port
(app.yaml's `ports: [8080]`).

Unlike the old repo, there's no --serial-port toggle: this device's own
SPI-connected sensors (ingestion/spi_reader.py) are always live -- there's
no "is the base station enabled" question the way the old UART flag was
optional across different deployments. --mqtt-host stays optional, for
satellite nodes:
    --mqtt-host <broker>    real satellite nodes over MQTT -- including
                            tools/satellite_node_sim.py, a standalone script
                            that mimics a real ESP32 satellite node over MQTT
                            for dashboard exercising without real hardware,
                            so everything downstream of frame ingestion
                            (gate/features/autoencoder/inference/
                            commissioning) is the same production code path
                            either way.

This module bootstraps sys.path to cover every subpackage it imports
transitively (flat-import convention -- no __init__.py packages exist),
since there's no external PYTHONPATH mechanism in the App Lab container:
    python3 python/main.py --mqtt-host localhost
"""
import argparse
import logging
import os
import sys
import threading

_PYTHON_DIR = os.path.dirname(os.path.abspath(__file__))
for _subpackage in ("common", "registry", "pipeline", "history", "monitoring",
                     "ingestion", "api"):
    sys.path.insert(0, os.path.join(_PYTHON_DIR, _subpackage))

import uvicorn
from fastapi.staticfiles import StaticFiles

from sensor_frame import BASE_STATION_NODE_ID, SensorFrame
from spi_reader import SpiConsumer
from registry import Registry
from status_color import color_for
from wire_protocol import LED_MODE_TO_INT
from gate import MotorStateGate
from manager import PipelineManager
from store import HistoryStore
from perf import PerformanceMonitor
from app import create_app, broadcast_threadsafe
from commissioning_controller import CommissioningController

logger = logging.getLogger("main")

FRONTEND_DIR = os.path.join(_PYTHON_DIR, "frontend")

# .cache/ sits alongside python/ (a sibling, not underneath it) and is the
# one directory deploy.sh/run.sh both preserve across deploys/restarts
# (base-station/.gitignore already ignores it, same as run.sh's own venv
# cache) -- registry.json/history.db/models/*.pt must live here, not under
# python/, since deploy.sh wipes every other top-level entry on each deploy.
DEFAULT_DATA_DIR = os.path.join(os.path.dirname(_PYTHON_DIR), ".cache", "data")


def build_gate_factory(threshold: float, debounce_frames: int):
    def factory() -> MotorStateGate:
        return MotorStateGate(threshold=threshold, debounce_frames=debounce_frames)
    return factory


def run_mqtt(host: str, port: int, on_frame, stop_event: threading.Event):
    from mqtt_subscriber import MqttSubscriber

    subscriber = MqttSubscriber(host, port, on_frame=on_frame)
    subscriber.start()
    stop_event.wait()
    subscriber.stop()


def wire_status_led_publishing(registry: Registry, host: str, port: int):
    """Pushes a STATUS_LED command to a node's own `epm/<node_id>/cmd` topic
    every time Registry.on_status_change fires for it, so a satellite node's
    status LED always reflects what the dashboard currently shows without
    ever polling the REST API. Returns the MqttPublisher so callers can
    stop() it on shutdown."""
    from mqtt_publisher import MqttPublisher

    publisher = MqttPublisher(host, port)

    def on_status_change(node_id: str, status) -> None:
        led = color_for(status)
        publisher.publish_status(node_id, led.rgb, led.mode, led.period_ms)

    registry.on_status_change(on_status_change)
    return publisher


def wire_local_status_led(registry: Registry) -> None:
    """On-device analog of wire_status_led_publishing for this board's own
    RGB ring: base_station has no MQTT client to receive its own
    epm/base_station/cmd command back, so instead of publishing over MQTT
    this drives the ring directly through the local Bridge RPC link
    (rgb_display.cpp's `set_rgb` provider, the same one spi_reader.py's
    spi_arm calls prove is reachable from this process). Filters to
    BASE_STATION_NODE_ID because Registry.on_status_change fires for every
    node, satellite nodes included, and those already get their LED over
    MQTT."""
    from arduino.app_utils import Bridge

    def on_status_change(node_id: str, status) -> None:
        if node_id != BASE_STATION_NODE_ID:
            return
        led = color_for(status)
        try:
            Bridge.call("set_rgb", f"{led.rgb.lstrip('#')},{LED_MODE_TO_INT[led.mode]},{led.period_ms}")
        except Exception:
            logger.exception("failed to push local status LED for %r", node_id)

    registry.on_status_change(on_status_change)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR,
                         help="Directory for registry.json, history.db, models/ "
                              "(default: base-station/.cache/data, survives redeploys)")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080,
                         help="Single port serving REST, WebSocket (/ws), and the dashboard frontend")
    parser.add_argument("--no-frontend", action="store_true",
                         help="Don't serve the static dashboard (e.g. deploying it separately)")

    parser.add_argument("--mqtt-host", default=None,
                         help="Enable real MQTT satellite ingestion against this broker")
    parser.add_argument("--mqtt-port", type=int, default=1883)

    parser.add_argument("--gate-threshold", type=float, default=0.05,
                         help="RMS energy threshold for running/stopped")
    parser.add_argument("--gate-debounce-frames", type=int, default=3)
    parser.add_argument("--status-debounce-frames", type=int, default=3)
    parser.add_argument("--min-commission-frames", type=int, default=50)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    os.makedirs(args.data_dir, exist_ok=True)
    models_dir = os.path.join(args.data_dir, "models")

    registry = Registry(os.path.join(args.data_dir, "registry.json"))
    history = HistoryStore(os.path.join(args.data_dir, "history.db"))
    perf_monitor = PerformanceMonitor()

    # Always on, unlike wire_status_led_publishing below (which needs
    # --mqtt-host) -- the local ring is reachable over Bridge regardless of
    # whether MQTT satellite ingestion is enabled for this run.
    wire_local_status_led(registry)

    def on_score(node_id: str, timestamp: float, score: float, status) -> None:
        # Mirrors on_frame's "spectrum" broadcast below -- pushes every
        # scored frame to the dashboard immediately instead of leaving the
        # anomaly timeline to be sampled off the periodic /nodes poll.
        broadcast_threadsafe(app, {
            "type": "anomaly",
            "node_id": node_id,
            "timestamp": timestamp,
            "score": score,
            "status": status.value,
        })

    gate_factory = build_gate_factory(args.gate_threshold, args.gate_debounce_frames)
    manager = PipelineManager(
        registry, gate_factory, perf_monitor=perf_monitor, history_store=history,
        status_debounce_frames=args.status_debounce_frames, on_score=on_score)

    commissioning = CommissioningController(
        registry, models_dir, gate_factory, min_frames=args.min_commission_frames)

    def on_frame(frame: SensorFrame) -> None:
        manager.route(frame)
        commissioning.feed_frame(frame)
        broadcast_threadsafe(app, {
            "type": "spectrum",
            "node_id": frame.node_id,
            "timestamp": frame.timestamp,
            "channels": {channel: list(bins) for channel, bins in frame.bins.items()},
        })

    spi_consumer = SpiConsumer(on_frame=on_frame)

    stop_event = threading.Event()
    mqtt_thread = None
    status_led_publisher = None
    if args.mqtt_host:
        mqtt_thread = threading.Thread(
            target=run_mqtt, args=(args.mqtt_host, args.mqtt_port, on_frame, stop_event),
            daemon=True)
        status_led_publisher = wire_status_led_publishing(registry, args.mqtt_host, args.mqtt_port)

    def start_ingestion() -> None:
        # Called from create_app's lifespan, after app.state.loop is set --
        # starting these any earlier lets on_frame's broadcast_threadsafe
        # call race app startup and silently no-op (loop is None) for any
        # frame that arrives before uvicorn's ASGI app actually starts.
        spi_consumer.start()
        if mqtt_thread is not None:
            mqtt_thread.start()

    app = create_app(registry, history, commissioning, manager=manager, perf_monitor=perf_monitor,
                      on_startup=start_ingestion)

    # Mounted after every REST/WebSocket route above is registered, so
    # this catch-all static handler can never shadow them.
    if not args.no_frontend:
        app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

    logger.info("Serving REST + WebSocket (/ws)%s on http://%s:%d",
                "" if args.no_frontend else " + dashboard frontend", args.host, args.port)
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    finally:
        logger.info("shutting down")
        stop_event.set()
        if status_led_publisher is not None:
            status_led_publisher.stop()


if __name__ == "__main__":
    main()
