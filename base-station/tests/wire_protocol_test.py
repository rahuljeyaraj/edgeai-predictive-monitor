#!/usr/bin/env python3
"""
Command-direction wire format (common/wire_protocol.py): the [TYPE:1B][PAYLOAD]
envelope plus the MOTOR_STOP payload the machinery-protection trip carries
(docs/MOTOR_STOP_PLAN.md).

The load-bearing test here is the last one. MOTOR_STOP is decoded in two
places by design -- this module, and a hand-rolled copy in
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
    decode_display_rgb_payload,
    decode_motor_stop_payload,
    decode_mqtt_message,
    encode_display_rgb_payload,
    encode_motor_stop_payload,
    encode_mqtt_message,
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


if __name__ == "__main__":
    try:
        test_motor_stop_payload_round_trips()
        test_motor_stop_rides_the_same_envelope_as_status_led()
        test_type_values_are_distinct()
        test_empty_message_rejected()
        test_bytes_match_the_independent_rig_host_decoder()
        print("RESULT: PASS - MOTOR_STOP encodes/decodes and matches the rig host's copy")
    except AssertionError as e:
        print(f"RESULT: FAIL - {e}")
        sys.exit(1)
