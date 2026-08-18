#!/usr/bin/env python3
"""Ported from edgeai-predictive-monitor-unoq/mpu/ingestion/mqtt_publisher.py.
Base station -> node command publishing: the publish-side counterpart to
mqtt_subscriber.py's subscribe-only client. Two commands today:

  STATUS_LED (0x08) -- pushed whenever a node's status color drifts from
      what was last sent to it (registry/led_keeper.py drives this), so a
      node's own status LED always reflects what the dashboard currently
      shows without the node ever polling the REST API. Retained; see
      publish_status() for why.
  MOTOR_STOP (0x09) -- the machinery-protection trip (protection/), pushed
      to whichever host owns the rig's serial port.

Payload is binary: [TYPE: 1B][PAYLOAD] (common/wire_protocol.py).

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
    encode_motor_stop_payload,
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
        # connect_async (not connect): see mqtt_subscriber.py's comment --
        # host is often a best-effort guess now, this must not raise
        # synchronously (main() calls this directly, unguarded) if nothing's
        # listening there yet.
        self._client.connect_async(host, port)
        self._client.loop_start()

    def publish_status(self, node_id: str, rgb: str, mode: str, period_ms: int) -> None:
        """Published RETAINED, unlike publish_motor_stop() below.

        A satellite's ring has no "connected" color of its own -- once MQTT
        is up, transport_task.cpp deliberately keeps showing whatever it
        last showed and waits for this command to tell it the real
        NodeStatus color. With a plain (non-retained) publish, a node that
        rebooted or reconnected after the last status change got nothing at
        all and sat there showing its own boot-time RGB_MQTT_DOWN blue
        while the dashboard showed it cyan (observed 2026-08-18). Retained
        means the broker replays the current color to the node the instant
        it subscribes to its cmd topic on connect -- the color is right on
        the first thing an operator sees, with no polling and no window
        where the ring contradicts the dashboard.

        MOTOR_STOP deliberately does NOT get this treatment: replaying a
        stale trip command at a node that just rebooted would re-stop a
        motor nobody asked to stop. Only retained publishes update a
        topic's retained message, so a non-retained MOTOR_STOP passes
        through without disturbing the retained STATUS_LED sitting behind
        it."""
        payload = encode_display_rgb_payload(rgb_hex_to_int(rgb), LED_MODE_TO_INT[mode], period_ms)
        message = encode_mqtt_message(MqttMsgType.STATUS_LED, payload)
        topic = CMD_TOPIC_FMT.format(node_id=node_id)
        self._client.publish(topic, message, qos=1, retain=True)

    def publish_motor_stop(self, node_id: str, motor_idx: int) -> None:
        """Machinery-protection trip (docs/MOTOR_STOP_PLAN.md): tells whatever
        host owns the rig's serial port to stop one motor.

        node_id here is the *rig host's* topic identity, not the monitored
        asset's -- the asset that faulted and the machine that gets stopped are
        different things, and only the caller (protection/) knows the mapping
        between them.

        Fire-and-forget like publish_status(): paho's publish() queues rather
        than blocking, which is what makes it safe to call from
        Registry.on_status_change's synchronous, lock-holding context."""
        payload = encode_motor_stop_payload(motor_idx)
        message = encode_mqtt_message(MqttMsgType.MOTOR_STOP, payload)
        topic = CMD_TOPIC_FMT.format(node_id=node_id)
        logger.info("publishing MOTOR_STOP motor=%d to %s", motor_idx, topic)
        self._client.publish(topic, message, qos=1)

    def stop(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()
