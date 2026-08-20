#!/usr/bin/env python3
"""
Fleet WiFi roaming (network/fleet_roam.py, docs/WIFI_ONBOARDING_PLAN.md S6):
the base station pushes the network it is about to join to every satellite
that can still hear it, then switches itself.

Exercised against a fake paho client rather than a real broker -- the logic
worth pinning down here is entirely about *which answers count* (roam_id
matching, silence, a node acking twice), and that needs deterministic
delivery, not a network.

The load-bearing tests are the two that decide whether the base station is
allowed to switch: a silent node must come back as "no_answer" (the
frontend stops and asks a human), and an ack carrying an older roam_id must
NOT be able to make a silent node look answered.

Run with PYTHONPATH covering base-station/python/common and
base-station/python/network:
    PYTHONPATH=base-station/python/common:base-station/python/network \\
        python3 base-station/tests/fleet_roam_test.py
"""
import sys
import types

import fleet_roam
from wire_protocol import (
    MqttMsgType,
    WifiProvisionAckStatus,
    decode_mqtt_message,
    decode_wifi_provision_payload,
    encode_mqtt_message,
    encode_wifi_provision_ack_payload,
)


class FakeClient:
    """Stands in for paho.mqtt.client.Client. Every node in `ack_from`
    answers the instant its push is published, which is what makes these
    tests finish in milliseconds instead of waiting out a real timeout."""

    def __init__(self, ack_from=(), status=WifiProvisionAckStatus.ACCEPTED,
                  roam_id_override=None, connect_error=None):
        self.ack_from = set(ack_from)
        self.status = status
        self.roam_id_override = roam_id_override
        self.connect_error = connect_error
        self.on_message = None
        self.published = []       # (topic, payload)
        self.subscribed = []
        self.connected_to = None
        self.disconnected = False

    def connect(self, host, port, keepalive=None):
        if self.connect_error is not None:
            raise self.connect_error
        self.connected_to = (host, port)

    def loop_start(self):
        pass

    def loop_stop(self):
        pass

    def disconnect(self):
        self.disconnected = True

    def subscribe(self, topic, qos=0):
        self.subscribed.append(topic)

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload))
        node_id = topic.split("/")[1]
        if node_id not in self.ack_from:
            return
        roam_id, _ssid, _pw, _broker, _port = decode_wifi_provision_payload(
            decode_mqtt_message(payload)[1])
        if self.roam_id_override is not None:
            roam_id = self.roam_id_override
        ack = encode_mqtt_message(
            MqttMsgType.WIFI_PROVISION_ACK,
            encode_wifi_provision_ack_payload(roam_id, self.status))
        self.on_message(self, None, types.SimpleNamespace(
            topic=f"epm/{node_id}/evt", payload=ack))


def _roamer_with(client) -> fleet_roam.FleetRoamer:
    fleet_roam.mqtt.Client = lambda *a, **kw: client
    return fleet_roam.FleetRoamer("broker.local", 1883)


def _statuses(result) -> dict:
    return {n["node_id"]: n["status"] for n in result["nodes"]}


def test_every_node_acking_reports_accepted():
    client = FakeClient(ack_from=("aa1111", "bb2222"))
    result = _roamer_with(client).push("Factory", "pw", ["aa1111", "bb2222"],
                                        ack_timeout_s=5.0)
    assert _statuses(result) == {"aa1111": "accepted", "bb2222": "accepted"}, result
    assert client.connected_to == ("broker.local", 1883), client.connected_to
    assert client.disconnected, "the roam's own MQTT connection must not be left open"
    print("a fleet that all answers comes back fully accepted: PASS")


def test_a_silent_node_is_reported_not_assumed():
    """The whole point of waiting for acks: a node that didn't hear this is
    about to lose the network it reaches us on, and the technician has to
    be told before we switch, not after."""
    client = FakeClient(ack_from=("aa1111",))
    result = _roamer_with(client).push("Factory", "pw", ["aa1111", "bb2222"],
                                        ack_timeout_s=0.3)
    assert _statuses(result) == {"aa1111": "accepted", "bb2222": "no_answer"}, result
    print("a silent node reports no_answer rather than being assumed moved: PASS")


def test_an_ack_for_a_different_roam_does_not_count():
    """A node acks BEFORE it switches, so a retry after a timeout can have
    the previous attempt's ack still in flight. Counting it would let the
    base station switch away from a node that never heard the retry."""
    client = FakeClient(ack_from=("aa1111",), roam_id_override=1)
    result = _roamer_with(client).push("Factory", "pw", ["aa1111"], ack_timeout_s=0.3)
    assert _statuses(result) == {"aa1111": "no_answer"}, result
    print("an ack carrying another roam's id is ignored: PASS")


def test_roam_ids_differ_between_pushes():
    client = FakeClient(ack_from=("aa1111",))
    roamer = _roamer_with(client)
    first = roamer.push("Factory", "pw", ["aa1111"], ack_timeout_s=1.0)
    second = roamer.push("Factory", "pw", ["aa1111"], ack_timeout_s=1.0)
    assert first["roam_id"] != second["roam_id"], (first, second)
    print("consecutive pushes carry distinct roam ids: PASS")


def test_simulated_nodes_are_distinguishable_from_real_ones():
    """A sim node has no radio and nothing to move, but it must still
    answer -- silence from it would stop the switch for a decision that
    isn't needed (tools/satellite_node_sim.py)."""
    client = FakeClient(ack_from=("sim001",), status=WifiProvisionAckStatus.SIMULATED)
    result = _roamer_with(client).push("Factory", "pw", ["sim001"], ack_timeout_s=1.0)
    assert _statuses(result) == {"sim001": "simulated"}, result
    print("a sim node's ack reads as simulated, not as a moved sensor: PASS")


def test_nodes_are_pushed_the_mdns_name_never_an_ip():
    """The address this device gets from the factory network's DHCP is
    unknown at push time and can change later, so the fleet is always given
    the name (docs/WIFI_ONBOARDING_PLAN.md S4)."""
    client = FakeClient(ack_from=("aa1111",))
    result = _roamer_with(client).push("Factory", "pw", ["aa1111"], ack_timeout_s=1.0)
    topic, payload = client.published[0]
    assert topic == "epm/aa1111/cmd", topic
    msg_type, body = decode_mqtt_message(payload)
    assert msg_type == MqttMsgType.WIFI_PROVISION, msg_type
    _roam_id, ssid, password, broker, port = decode_wifi_provision_payload(body)
    assert (ssid, password) == ("Factory", "pw"), (ssid, password)
    assert broker == fleet_roam.BASE_STATION_MDNS_HOST == "epm-base.local", broker
    assert port == 1883, port
    assert result["broker_host"] == broker
    print("the push carries the base station's mDNS name as the new broker: PASS")


def test_no_nodes_means_no_broker_connection_at_all():
    client = FakeClient()
    result = _roamer_with(client).push("Factory", "pw", [])
    assert result["nodes"] == [], result
    assert client.connected_to is None, "shouldn't open a connection with nothing to push"
    print("an empty fleet short-circuits without touching the broker: PASS")


def test_an_unreachable_broker_raises_rather_than_reporting_silence():
    """Distinct outcomes on purpose: "no node answered" is a fleet problem
    the technician must judge, "the broker is down" is a 503 about this
    device -- api/app.py maps them to different responses."""
    client = FakeClient(connect_error=OSError("connection refused"))
    try:
        _roamer_with(client).push("Factory", "pw", ["aa1111"])
    except RuntimeError as e:
        assert "unreachable" in str(e), e
        print("an unreachable broker raises instead of looking like fleet-wide silence: PASS")
        return
    raise AssertionError("expected RuntimeError for an unreachable broker")


if __name__ == "__main__":
    real_client = fleet_roam.mqtt.Client
    try:
        test_every_node_acking_reports_accepted()
        test_a_silent_node_is_reported_not_assumed()
        test_an_ack_for_a_different_roam_does_not_count()
        test_roam_ids_differ_between_pushes()
        test_simulated_nodes_are_distinguishable_from_real_ones()
        test_nodes_are_pushed_the_mdns_name_never_an_ip()
        test_no_nodes_means_no_broker_connection_at_all()
        test_an_unreachable_broker_raises_rather_than_reporting_silence()
        print("RESULT: PASS - the fleet push counts only the acks it should, and "
              "reports the rest honestly")
    except AssertionError as e:
        print(f"RESULT: FAIL - {e}")
        sys.exit(1)
    finally:
        fleet_roam.mqtt.Client = real_client
