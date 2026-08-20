#!/usr/bin/env python3
"""
Command-direction wire format (common/wire_protocol.py): the [TYPE:1B][PAYLOAD]
envelope, the MOTOR_STOP payload the machinery-protection trip carries
(docs/MOTOR_STOP_PLAN.md), and the WIFI_PROVISION/WIFI_PROVISION_ACK pair
fleet WiFi roaming uses (docs/WIFI_ONBOARDING_PLAN.md S6).

The load-bearing tests here are the ones asserting exact bytes. Both of
these formats are decoded by a second, independent implementation that
cannot import this module -- MOTOR_STOP by hand in
motor-driver/motor_driver.py (a different machine), WIFI_PROVISION by the
ESP32 firmware's memcpy onto struct wifi_provision_payload -- and two
implementations of one format drift silently.

MOTOR_STOP is decoded in two places by design -- this module, and a hand-rolled copy in
motor-driver/motor_driver.py, which runs on a different machine and cannot import
this package (the same split the ESP32 firmware already has for the telemetry
formats). Two implementations of one format drift silently, so the exact bytes
that copy expects are asserted here, against the encoder that produces them.

Run with PYTHONPATH covering base-station/python/common:
    PYTHONPATH=base-station/python/common python3 base-station/tests/wire_protocol_test.py
"""
import struct
import sys

from wire_protocol import (
    MOTOR_STOP_PAYLOAD_LEN,
    MqttMsgType,
    WIFI_PROVISION_PAYLOAD_LEN,
    WifiProvisionAckStatus,
    decode_display_rgb_payload,
    decode_motor_stop_payload,
    decode_mqtt_message,
    encode_display_rgb_payload,
    decode_wifi_provision_ack_payload,
    decode_wifi_provision_payload,
    encode_motor_stop_payload,
    encode_mqtt_message,
    encode_wifi_provision_ack_payload,
    encode_wifi_provision_payload,
)


def test_motor_stop_payload_round_trips():
    for motor_idx in (1, 2, 3, 255):
        payload = encode_motor_stop_payload(motor_idx)
        assert len(payload) == MOTOR_STOP_PAYLOAD_LEN == 1, payload
        assert decode_motor_stop_payload(payload) == motor_idx
    print("motor_stop payload round-trips for every valid motor index: PASS")


def test_motor_stop_rides_the_same_envelope_as_status_led():
    message = encode_mqtt_message(MqttMsgType.MOTOR_STOP,
                                   encode_motor_stop_payload(2))
    msg_type, payload = decode_mqtt_message(message)
    assert msg_type == MqttMsgType.MOTOR_STOP == 0x09, msg_type
    assert decode_motor_stop_payload(payload) == 2

    # The pre-existing command still decodes unchanged -- MOTOR_STOP is an
    # addition to this channel, not a change to it.
    led = encode_mqtt_message(MqttMsgType.STATUS_LED,
                               encode_display_rgb_payload(0xFF0000, 2, 200))
    led_type, led_payload = decode_mqtt_message(led)
    assert led_type == MqttMsgType.STATUS_LED == 0x08, led_type
    assert decode_display_rgb_payload(led_payload) == (0xFF0000, 2, 200)
    print("MOTOR_STOP and STATUS_LED share one envelope, neither disturbs the other: PASS")


def test_type_values_are_distinct():
    values = [t.value for t in MqttMsgType]
    assert len(values) == len(set(values)), values
    print("every MqttMsgType value is distinct: PASS")


def test_empty_message_rejected():
    try:
        decode_mqtt_message(b"")
    except ValueError:
        print("an empty command message is rejected, not silently mis-parsed: PASS")
        return
    raise AssertionError("empty message should have raised")


def test_bytes_match_the_independent_rig_host_decoder():
    """motor-driver/motor_driver.py decodes this without importing this module.
    That copy reads byte 0 as the type and byte 1 as the motor index, so the
    whole message for "stop motor 2" must be exactly b'\\x09\\x02'."""
    message = encode_mqtt_message(MqttMsgType.MOTOR_STOP,
                                   encode_motor_stop_payload(2))
    assert message == b"\x09\x02", message
    assert len(message) == 2, message

    # And the copy's own logic, spelled out as it is written there, agrees.
    assert message[0] == MqttMsgType.MOTOR_STOP
    assert struct.unpack("<B", message[1:2])[0] == 2
    print("wire bytes match what motor_driver.py's independent decoder expects: PASS")


def test_wifi_provision_payload_round_trips():
    for ssid, password, broker, port in (
            ("FactoryWiFi", "s3cret pass", "epm-base.local", 1883),
            ("a" * 32, "b" * 64, "c" * 64, 65535),   # every field at its cap
            ("Hidden", "", "10.42.0.1", 1883),        # open network: empty password
    ):
        payload = encode_wifi_provision_payload(7, ssid, password, broker, port)
        assert len(payload) == WIFI_PROVISION_PAYLOAD_LEN == 169, len(payload)
        assert decode_wifi_provision_payload(payload) == (7, ssid, password, broker, port)
    print("wifi_provision payload round-trips at every field length: PASS")


def test_wifi_provision_rejects_oversize_fields():
    """Truncating instead would push a silently-wrong password to the whole
    fleet, whose only symptom is every node failing to join at once."""
    for ssid, password, broker in (("a" * 33, "pw", "epm-base.local"),
                                    ("net", "b" * 65, "epm-base.local"),
                                    ("net", "pw", "c" * 65)):
        try:
            encode_wifi_provision_payload(1, ssid, password, broker, 1883)
        except ValueError:
            continue
        raise AssertionError(f"accepted an oversize field: {ssid[:5]}/{password[:5]}/{broker[:5]}")
    print("wifi_provision refuses fields too long for the node-side buffer: PASS")


def test_wifi_provision_wire_layout_matches_the_firmware_struct():
    """The ESP32 memcpy's this payload straight onto struct
    wifi_provision_payload (satellite/include/frame_codec/wire_protocol.h) --
    a second implementation of one format, exactly like MOTOR_STOP above, so
    the field offsets that copy depends on are asserted here."""
    payload = encode_wifi_provision_payload(0x01020304, "N", "P", "B", 0x1234)
    assert payload[0:4] == b"\x04\x03\x02\x01", payload[0:4]  # uint32 roam_id, little-endian
    assert payload[4] == ord("N") and payload[5] == 0, payload[4:6]  # ssid, NUL-padded
    assert payload[37] == ord("P") and payload[38] == 0, payload[37:39]  # password at 4+33
    assert payload[102] == ord("B"), payload[102]                        # broker at 4+33+65
    assert payload[167:169] == b"\x34\x12", payload[167:]              # uint16 port, no tail padding
    print("wifi_provision field offsets match the firmware struct: PASS")


def test_wifi_provision_ack_round_trips_and_rides_the_same_envelope():
    for status in WifiProvisionAckStatus:
        message = encode_mqtt_message(MqttMsgType.WIFI_PROVISION_ACK,
                                       encode_wifi_provision_ack_payload(4242, status))
        msg_type, body = decode_mqtt_message(message)
        assert msg_type == MqttMsgType.WIFI_PROVISION_ACK == 0x0b, msg_type
        assert decode_wifi_provision_ack_payload(body) == (4242, int(status))
    print("wifi_provision_ack round-trips for every status: PASS")


if __name__ == "__main__":
    try:
        test_motor_stop_payload_round_trips()
        test_motor_stop_rides_the_same_envelope_as_status_led()
        test_type_values_are_distinct()
        test_empty_message_rejected()
        test_bytes_match_the_independent_rig_host_decoder()
        test_wifi_provision_payload_round_trips()
        test_wifi_provision_rejects_oversize_fields()
        test_wifi_provision_wire_layout_matches_the_firmware_struct()
        test_wifi_provision_ack_round_trips_and_rides_the_same_envelope()
        print("RESULT: PASS - MOTOR_STOP and WIFI_PROVISION encode/decode and match "
              "the independent copies of their formats")
    except AssertionError as e:
        print(f"RESULT: FAIL - {e}")
        sys.exit(1)
