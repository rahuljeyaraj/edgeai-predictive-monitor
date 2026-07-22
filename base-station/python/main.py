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
                     "ingestion", "api", "alerts"):
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
from gpu_perf import GpuPerfPoller
from app import create_app, broadcast_threadsafe
from commissioning_controller import CommissioningController
from alert_store import AlertStore

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
    try:
        from arduino.app_utils import Bridge
    except ImportError:
        # Desktop dev run (base-station/start_desktop_dashboard.sh) -- no
        # App Lab container, so there's no local RGB ring to drive. Same
        # convention as spi_reader.py's own guard: skip wiring instead of
        # crashing main.py at startup.
        logger.warning("arduino.app_utils unavailable -- local status LED disabled (desktop dev run?)")
        return

    from bridge_lock import BRIDGE_LOCK

    def on_status_change(node_id: str, status) -> None:
        if node_id != BASE_STATION_NODE_ID:
            return
        led = color_for(status)

        def push() -> None:
            try:
                with BRIDGE_LOCK:
                    Bridge.call("set_rgb", f"{led.rgb.lstrip('#')},{LED_MODE_TO_INT[led.mode]},{led.period_ms}")
            except Exception:
                logger.exception("failed to push local status LED for %r", node_id)

        # Off the frame-ingestion thread, same reason telegram_alerts.py's
        # on_status_change backgrounds its send(): this fires from inside
        # PipelineManager.route()'s per-node lock, on whichever thread
        # routed the triggering frame (spi_reader's SPI-consumer thread, or
        # the MQTT client thread for a satellite node). Bridge.call blocks
        # on BRIDGE_LOCK -- the same lock the SPI-consumer thread grabs on
        # every single frame pull -- so a synchronous call here would stall
        # ingestion (and every dashboard broadcast) fleet-wide, not just
        # for this node, until it clears.
        threading.Thread(target=push, daemon=True).start()

    registry.on_status_change(on_status_change)


# Firmware defaults scroll_speed to 0 = static/no-scroll (matrix_display.cpp),
# which clips any message past the 13-col matrix, so a non-zero speed is
# required for the multi-word counts to scroll into view. 150ms/col is
# display_matrix_test.py's proven-readable value.
MATRIX_SCROLL_SPEED_MS = 150


def wire_local_matrix_text(registry: Registry) -> None:
    """Drives this board's own 8x13 LED matrix with a rolling fleet-health
    summary (docs/LED_MATRIX_STATUS_PLAN.md) -- a glanceable, no-app-needed
    readout physically on the base station. Sibling to wire_local_status_led:
    same local-Bridge-RPC push (set_matrix_text instead of set_rgb), same
    desktop-dev ImportError guard. Unlike the RGB ring (this board's OWN
    status only), the matrix shows FLEET counts, so it rebuilds on *every*
    node's status change, not just BASE_STATION_NODE_ID's."""
    try:
        from arduino.app_utils import Bridge
    except ImportError:
        # Desktop dev run -- no App Lab container, no local matrix to drive.
        # Same convention as wire_local_status_led/spi_reader.py: skip wiring
        # instead of crashing main.py at startup.
        logger.warning("arduino.app_utils unavailable -- local matrix text disabled (desktop dev run?)")
        return

    from bridge_lock import BRIDGE_LOCK
    from matrix_status import fleet_status_text

    def on_status_change(node_id: str, status) -> None:
        text = fleet_status_text(registry.list().values())

        def push() -> None:
            try:
                with BRIDGE_LOCK:
                    # Scroll speed first, then text: set_matrix_text resets
                    # the scroll position (and any new text restarts the
                    # scroll), so the speed must already be in effect when
                    # the text lands -- the same ordering
                    # display_matrix_test.py relies on. Both args go over
                    # the wire as strings; integer RPC params fail
                    # Arduino_RPClite's type-check (see matrix_display.cpp).
                    Bridge.call("set_matrix_scroll_speed", str(MATRIX_SCROLL_SPEED_MS))
                    Bridge.call("set_matrix_text", text)
            except Exception:
                logger.exception("failed to push fleet status to LED matrix")

        # See wire_local_status_led's comment above: this fires on the
        # frame-ingestion thread (for every node's status change, not just
        # the base station's own) and must never block it on BRIDGE_LOCK.
        threading.Thread(target=push, daemon=True).start()

    registry.on_status_change(on_status_change)


def build_telegram_alerts(registry: Registry, alert_store: AlertStore, on_subscriber_change):
    """Constructs the arduino:telegram_bot brick and wires it to `registry`
    + `alert_store` (docs/DASHBOARD_IDEAS_BACKLOG.md's Telegram alerts).
    Only called when TELEGRAM_BOT_TOKEN is set (main() below) -- Telegram
    alerts are opt-in, same as --mqtt-host satellite ingestion. Returns the
    bot; caller is responsible for .start()/.stop() (deferred to
    start_ingestion() below, same reason spi_consumer/gpu_perf are: this
    needs app.state.loop set before any inbound Telegram message could
    trigger a broadcast_threadsafe call)."""
    from telegram_alerts import build_telegram_bot, wire_telegram_alerts

    bot = build_telegram_bot()
    wire_telegram_alerts(registry, bot, alert_store, on_subscriber_change=on_subscriber_change)
    return bot


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
    alert_store = AlertStore(os.path.join(args.data_dir, "alerts.json"))

    # Always on, unlike wire_status_led_publishing below (which needs
    # --mqtt-host) -- the local ring is reachable over Bridge regardless of
    # whether MQTT satellite ingestion is enabled for this run.
    wire_local_status_led(registry)
    # Same story for the board's own LED matrix (fleet-health summary).
    wire_local_matrix_text(registry)

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
            # "channels" stays exactly frame.bins -- model-relevant channels
            # (mic/accel_x/accel_y/accel_z), each fanning out one waterfall
            # row + one spectrum trace in charts.js's handleSpectrum. The
            # fused/combined `accel` channel is display-only now (superseded
            # by the per-axis model channels) -- it rides under
            # "axis_channels" below, which today's frontend deliberately
            # doesn't consume (nothing renders the old single combined-axis
            # spectrum anymore).
            "channels": {channel: list(bins) for channel, bins in frame.bins.items()},
            "axis_channels": {channel: list(bins) for channel, bins in
                              frame.display_bins.items()},
            # (fs, fft_size) per spectrum channel -- charts.js turns a bin
            # index into an actual frequency (k * fs / fft_size) with this
            # instead of plotting a raw, sample-rate-independent bin number.
            "spectrum_meta": {channel: {"fs": fs, "fft_size": fft_size}
                              for channel, (fs, fft_size) in frame.spectrum_meta.items()},
            # docs/CHART_CLUTTER_PLAN.md S1: scalar tiles + the collapsible
            # "Raw signals" panel. scalars is usually present every frame;
            # time_series is usually empty (only populated on the frames that
            # piggyback it -- see fuser.cpp's FUSER_TIME_SERIES_EVERY_N).
            "scalars": frame.scalars,
            "time_series": {name: {"fs": fs, "samples": list(samples)}
                            for name, (fs, samples) in frame.time_series.items()},
        })

    spi_consumer = SpiConsumer(on_frame=on_frame)
    gpu_perf = GpuPerfPoller()

    def on_subscriber_change() -> None:
        broadcast_threadsafe(app, {
            "type": "telegram_subscribers",
            "subscribers": {chat_id: sub.to_dict()
                             for chat_id, sub in alert_store.list_subscribers().items()},
        })

    # Opt-in, same as --mqtt-host: only wired when the arduino:telegram_bot
    # brick's secret is actually set (App Lab's UI, once the brick is
    # declared in app.yaml). Unset is the expected case for anyone who
    # hasn't configured a bot yet -- GET /alerts/telegram/status reports
    # `configured: false` and the dashboard's Alerts tab hides the connect
    # flow rather than erroring.
    telegram_bot = None
    if os.getenv("TELEGRAM_BOT_TOKEN"):
        telegram_bot = build_telegram_alerts(registry, alert_store, on_subscriber_change)

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
        # telegram_bot.start() belongs here for the same reason: a message
        # (e.g. /start) could arrive and trigger on_subscriber_change's
        # broadcast_threadsafe before the loop is ready otherwise.
        spi_consumer.start()
        gpu_perf.start()
        if mqtt_thread is not None:
            mqtt_thread.start()
        if telegram_bot is not None:
            telegram_bot.start()

    app = create_app(registry, history, commissioning, manager=manager, perf_monitor=perf_monitor,
                      gpu_perf=gpu_perf, spi_consumer=spi_consumer,
                      alert_store=alert_store, telegram_bot=telegram_bot,
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
        if telegram_bot is not None:
            telegram_bot.stop()


if __name__ == "__main__":
    main()
