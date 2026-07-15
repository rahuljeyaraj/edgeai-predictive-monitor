#!/usr/bin/env python3
"""Ported from edgeai-predictive-monitor-unoq/mpu/ingestion/mqtt_publisher.py.
Base station -> satellite node command publishing: the publish-side
counterpart to mqtt_subscriber.py's subscribe-only client. Today this
only carries STATUS_LED (0x08) -- pushed whenever Registry.on_status_change
fires (main.py wires that up) -- so a node's own status LED always
reflects what the dashboard currently shows without the node ever
polling the REST API.

Payload is binary: [TYPE: 1B][display_rgb_payload] (common/wire_protocol.py).

Kept as its own client (rather than extending MqttSubscriber into one
bidirectional class) since main.py only ever needs one of these per
process, constructed and torn down independently of the subscriber, and
paho.mqtt.client.Client itself already supports full-duplex pub+sub on one
connection if that's ever wanted."""
import logging

import paho.mqtt.client as mqtt

from wire_protocol import (
    LED_MODE_TO_INT,
    MqttMsgType,
    encode_display_rgb_payload,
    encode_mqtt_message,
    rgb_hex_to_int,
)

logger = logging.getLogger("mqtt_publisher")

CMD_TOPIC_FMT = "epm/{node_id}/cmd"


class MqttPublisher:
    """Wraps paho.mqtt.client for the base station -> node command
    direction. publish_status() is the only command this carries today."""

    def __init__(self, host: str, port: int, client_id: str = ""):
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        self._client.connect(host, port)
        self._client.loop_start()

    def publish_status(self, node_id: str, rgb: str, mode: str, period_ms: int) -> None:
        payload = encode_display_rgb_payload(rgb_hex_to_int(rgb), LED_MODE_TO_INT[mode], period_ms)
        message = encode_mqtt_message(MqttMsgType.STATUS_LED, payload)
        topic = CMD_TOPIC_FMT.format(node_id=node_id)
        self._client.publish(topic, message, qos=1)

    def stop(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()
