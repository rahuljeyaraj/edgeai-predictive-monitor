#!/usr/bin/env python3
"""
Ported from edgeai-predictive-monitor-unoq/mpu/ingestion/mqtt_subscriber.py.
Subscribes to the satellite MQTT topics and normalizes SPECTRUM messages
into SensorFrame, feeding PipelineManager the same way
ingestion/spi_reader.py does for this device's own SPI-connected sensors.

Satellite nodes now speak the *same* generic section-list telemetry frame the
base station's own SPI link does (common/telemetry_frame.py,
docs/SENSOR_TELEMETRY_FRAME_PLAN.md S6) -- one payload format, one decoder,
both transports:

- Topic `epm/<node_id>/data` carries the telemetry frame. The MQTT message body
  IS the section-list frame bytes, decoded here by decode_frame() -- no [TYPE]
  envelope (MQTT already frames + delivers; a heartbeat is just a frame with no
  spectrum sections, which becomes a None/skip here). Health/perf, when a node
  sends it, rides as a SCALAR_SET section in the same frame rather than a
  separate message type.
- node_id is not a wire field (a real node_id is a MAC-derived string, too big
  for a wire field) -- it comes from the topic (epm/<node_id>/data), the
  addressing mechanism MQTT already provides.
- A multi-channel node must carry every channel it will ever report in each
  frame (as SPECTRUM sections): PipelineManager validates every frame carries
  bins for a node's *entire* committed sensor_config at once (manager.py's
  `_validate_frame_bins`), so its channels can't arrive as separate frames.

SensorFrame.timestamp is local receipt time.

paho-mqtt is already installed in the App Lab container image (no
requirements.txt entry needed -- confirmed via `pip3 list` on-device).

main.py bootstraps sys.path to cover every base-station/python
subpackage this imports (flat-import convention, no __init__.py
packages) before importing this module.

Verification: publish a synthetic satellite SPECTRUM message (e.g. via
tools/satellite_node_sim.py, or mosquitto_pub with raw bytes) against a
running Mosquitto broker; confirm a new pipeline is created and routed
exactly as SPI frames are.
"""
import logging
import time
from typing import Callable, Optional

import paho.mqtt.client as mqtt

import telemetry_schema as schema
from registry import SensorChannel
from sensor_frame import FrameSource, SensorFrame
from telemetry_frame import MalformedFrameError, decode_frame

logger = logging.getLogger(__name__)

DATA_TOPIC_FILTER = "epm/+/data"


class MalformedMessageError(Exception):
    """Raised for a message that cannot be parsed/normalized at all --
    distinct from a well-formed message of a type this module doesn't
    turn into a SensorFrame (HEALTH_ALERT, HEARTBEAT), which is a normal
    skip, not a drop (mirrors uart_reader.py's FrameParser.dropped_frames,
    which only counts actual parse failures)."""


def _node_id_from_topic(topic: str) -> str:
    """epm/<node_id>/data -> <node_id>. Topic wildcards only route
    delivery in MQTT; the segment itself is the sole source of node
    identity now that the wire payload carries no node_id field (see
    module docstring)."""
    parts = topic.split("/")
    if len(parts) != 3 or parts[0] != "epm" or parts[2] != "data":
        raise MalformedMessageError(f"topic {topic!r} doesn't match epm/<node_id>/data")
    node_id = parts[1]
    if not node_id:
        raise MalformedMessageError(f"empty node_id in topic {topic!r}")
    return node_id


def normalize_spectrum_message(topic: str, payload: bytes,
                                timestamp: Optional[float] = None) -> Optional[SensorFrame]:
    """Pure decode+normalize step, split out from MqttSubscriber so it can
    be exercised without a real broker connection (see
    tests/mqtt_subscriber_test.py). Returns None for a well-formed frame that
    carries no spectrum bins (a heartbeat / scalar-only frame -- a normal skip);
    raises MalformedMessageError for anything that can't be parsed (counted as
    dropped by the caller)."""
    node_id = _node_id_from_topic(topic)

    try:
        decoded = decode_frame(payload)
    except MalformedFrameError as e:
        raise MalformedMessageError(f"malformed telemetry frame on {topic!r}: {e}") from e

    if not decoded.bins:
        # No spectrum sections with data (heartbeat, scalar-only, or every
        # channel bin_count=0) -- nothing to route, same normal-skip semantics
        # a non-SPECTRUM message had before.
        return None

    # decoded.bins is schema-driven (every SPECTRUM channel the schema knows
    # about, model-facing or not) -- split off channels that aren't a
    # SensorChannel (e.g. a future satellite sending the per-axis accel
    # overlay, docs/CHART_CLUTTER_PLAN.md S1) into display_bins, mirroring
    # ingestion/spi_reader.py, so gate/manager/features keep seeing only the
    # model-relevant set in .bins.
    model_bins, display_bins = {}, {}
    for name, bins in decoded.bins.items():
        try:
            SensorChannel(name)
        except ValueError:
            display_bins[name] = bins
        else:
            model_bins[name] = bins

    # Resolve scalars/time_series to the same friendly names decoded.bins
    # already uses, mirroring ingestion/spi_reader.py's SensorFrame
    # construction (docs/CHART_CLUTTER_PLAN.md S1). No satellite firmware
    # emits these yet, so this is normally empty for MQTT frames today.
    scalars = {schema.SCALAR_NAME_BY_ID[sid]: value
               for sid, value in decoded.scalars.items()
               if sid in schema.SCALAR_NAME_BY_ID}
    ts = {schema.CHANNEL_NAME_BY_ID[cid]: (series.fs, series.samples)
          for cid, series in decoded.time_series.items()
          if cid in schema.CHANNEL_NAME_BY_ID}
    # (fs, fft_size) per channel actually present in decoded.bins, mirroring
    # ingestion/spi_reader.py -- lets the dashboard convert a bin index into
    # an actual frequency regardless of ingestion path.
    spectrum_meta = {name: (s.fs, s.fft_size) for name, s in decoded.spectra.items()
                      if name in decoded.bins}

    return SensorFrame(
        node_id=node_id,
        source=FrameSource.MQTT,
        timestamp=time.time() if timestamp is None else timestamp,
        bins=model_bins,
        display_bins=display_bins,
        scalars=scalars,
        time_series=ts,
        spectrum_meta=spectrum_meta,
    )


class MqttSubscriber:
    """Wraps paho.mqtt.client, normalizing every SPECTRUM message on
    `epm/+/data` into a SensorFrame and handing it to on_frame -- the
    push-model counterpart to UartReader's pull-model read_frames()
    generator. on_frame is typically PipelineManager.route."""

    def __init__(self, host: str, port: int, on_frame: Callable[[SensorFrame], None],
                 client_id: str = ""):
        self._on_frame = on_frame
        self._dropped = 0
        self._routing_errors = 0
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        self._client.on_connect = self._handle_connect
        self._client.on_message = self._handle_message
        # connect_async (not connect): host is often a best-effort guess now
        # (main.py's _default_mqtt_host()) rather than a confirmed broker --
        # this defers the actual TCP attempt to loop_start()'s background
        # thread, which retries quietly via paho's own reconnect logic
        # instead of raising synchronously if nothing's listening yet.
        self._client.connect_async(host, port)

    @property
    def dropped_frames(self) -> int:
        return self._dropped

    @property
    def routing_errors(self) -> int:
        return self._routing_errors

    def _handle_connect(self, client, userdata, flags, reason_code, properties=None):
        client.subscribe(DATA_TOPIC_FILTER, qos=0)

    def _handle_message(self, client, userdata, msg) -> None:
        try:
            frame = normalize_spectrum_message(msg.topic, msg.payload)
        except MalformedMessageError:
            self._dropped += 1
            return
        if frame is None:
            return
        # on_frame (PipelineManager.route) can raise -- e.g. a frame whose
        # bin count no longer matches what this node_id committed to on its
        # first-ever frame (manager.py's _validate_frame_bins). This runs
        # inside paho-mqtt's own background thread, shared by every node on
        # this broker: an uncaught exception here has previously taken down
        # message processing for the whole fleet, not just the one node that
        # misbehaved, until the process was restarted. Log + drop instead,
        # mirroring the MalformedMessageError handling just above.
        try:
            self._on_frame(frame)
        except Exception:
            self._routing_errors += 1
            logger.exception("on_frame failed for node_id=%r topic=%r -- frame dropped",
                              frame.node_id, msg.topic)

    def start(self) -> None:
        self._client.loop_start()

    def stop(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()


def main():
    import argparse

    arg_parser = argparse.ArgumentParser(description=__doc__)
    arg_parser.add_argument("--host", default="localhost",
                             help="Mosquitto broker host (default: localhost, "
                                  "the UNO Q-hosted broker per Appendix B S3)")
    arg_parser.add_argument("--port", type=int, default=1883)
    args = arg_parser.parse_args()

    def print_frame(frame: SensorFrame) -> None:
        counts = " ".join(f"{ch}_bins={len(bins)}" for ch, bins in frame.bins.items())
        print(f"[{frame.timestamp:.3f}] node={frame.node_id} {counts} "
              f"dropped={subscriber.dropped_frames}", flush=True)

    subscriber = MqttSubscriber(args.host, args.port, on_frame=print_frame)
    print(f"Subscribing to {DATA_TOPIC_FILTER} on {args.host}:{args.port}", flush=True)
    subscriber.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        subscriber.stop()


if __name__ == "__main__":
    main()
