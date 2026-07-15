#!/usr/bin/env python3
"""
Ported from edgeai-predictive-monitor-unoq/mpu/ingestion/mqtt_subscriber.py.
Subscribes to the satellite MQTT topics and normalizes SPECTRUM messages
into SensorFrame, feeding PipelineManager the same way
ingestion/spi_reader.py does for this device's own SPI-connected sensors.

The byte-level satellite format isn't tied to this repo's own MCU link
(that's Bridge RPC + the dedicated SPI link, docs/progress2.md) -- it's a
separate wire contract for satellite ESP32 nodes:

- Topic `epm/<node_id>/data` carries SPECTRUM, HEALTH_ALERT and HEARTBEAT
  messages from a node. Only SPECTRUM becomes a SensorFrame here -- other
  types are silently skipped.
- Payload is binary: [TYPE: 1B][PAYLOAD] (common/wire_protocol.py's
  MQTT envelope), the same spectrum_fused_payload struct format
  (decode_spectrum_fused_payload()) used by the fuser's own SPI frame.
- node_id is not a wire field (a real node_id is a MAC-derived string,
  too big for a 1-byte field) -- it comes from the topic
  (epm/<node_id>/data), which is the addressing mechanism MQTT already
  provides.
- SPECTRUM's fused payload always carries both mic and accel channel
  headers (fs/fft_size/bin_count each); a disabled channel has
  bin_count=0 and contributes no bin bytes. This is required, not just
  convenient: PipelineManager validates every frame carries bins for a
  node's *entire* committed sensor_config at once (manager.py's
  `_validate_frame_bins`), so a dual-channel node's mic and accel data
  can't arrive as two separate single-channel frames.

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
import struct
import time
from typing import Callable, Dict, Optional, Tuple

import paho.mqtt.client as mqtt

from sensor_frame import FrameSource, SensorFrame
from wire_protocol import MqttMsgType, decode_mqtt_message, decode_spectrum_fused_payload

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
    mpu/tests/mqtt_subscriber_test.py). Returns None for a well-formed
    non-SPECTRUM message (normal skip); raises MalformedMessageError for
    anything that can't be parsed (counted as dropped by the caller)."""
    node_id = _node_id_from_topic(topic)

    try:
        msg_type, body = decode_mqtt_message(payload)
    except ValueError as e:
        raise MalformedMessageError(f"bad MQTT payload on {topic!r}: {e}") from e

    if msg_type != MqttMsgType.SPECTRUM:
        return None

    try:
        mic, accel = decode_spectrum_fused_payload(body)
    except struct.error as e:
        raise MalformedMessageError(f"malformed SPECTRUM payload on {topic!r}: {e}") from e

    bins: Dict[str, Tuple[float, ...]] = {}
    if mic.bins:
        bins["mic"] = mic.bins
    if accel.bins:
        bins["accel"] = accel.bins
    if not bins:
        raise MalformedMessageError(f"SPECTRUM payload on {topic!r} has no channel bins "
                                     f"(both mic and accel disabled)")

    return SensorFrame(
        node_id=node_id,
        source=FrameSource.MQTT,
        timestamp=time.time() if timestamp is None else timestamp,
        bins=bins,
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
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        self._client.on_connect = self._handle_connect
        self._client.on_message = self._handle_message
        self._client.connect(host, port)

    @property
    def dropped_frames(self) -> int:
        return self._dropped

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
        self._on_frame(frame)

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
