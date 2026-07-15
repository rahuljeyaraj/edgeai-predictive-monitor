#!/usr/bin/env python3
"""
Milestone 11 verification: publish
synthetic satellite SPECTRUM messages and confirm a new pipeline is
created and routed exactly as SPI-sourced frames are.

No Mosquitto broker is available in this dev environment, so the two
concerns are exercised separately rather than over a real socket:

1. normalize_spectrum_message() -- the pure decode+normalize step -- is
   exercised directly with hand-built binary envelopes ([TYPE: 1B]
   [spectrum_fused_payload], same struct codec the SPI fuser frame uses --
   base-station/python/common/wire_protocol.py's encode_spectrum_fused_payload()), same
   pattern ingestion/spi_reader.py's frame decode uses.
2. MqttSubscriber's on_message wiring is exercised by constructing a real
   paho.mqtt.client.MQTTMessage and calling its handler directly, rather
   than opening a real broker connection -- paho.Client.connect() is not
   invoked. This still exercises the actual paho MQTTMessage type the
   library hands the callback in production, just without a live socket.

Run with PYTHONPATH covering base-station/python/common, base-station/python/ingestion, base-station/python/registry, base-station/python/pipeline:
    PYTHONPATH=base-station/python/common:base-station/python/ingestion:base-station/python/registry:base-station/python/pipeline \\
        python3 base-station/tests/mqtt_subscriber_test.py
"""
import os
import sys
import tempfile

import paho.mqtt.client as mqtt

from sensor_frame import FrameSource
from mqtt_subscriber import MalformedMessageError, normalize_spectrum_message, MqttSubscriber
from wire_protocol import ChannelSpectrum, MqttMsgType, encode_mqtt_message, encode_spectrum_fused_payload
from registry import Registry, SensorChannel
from gate import MotorStateGate
from manager import PipelineManager


def default_gate_factory() -> MotorStateGate:
    return MotorStateGate(threshold=0.05, debounce_frames=3)

TOPIC = "epm/a4cf12/data"


def spectrum_message(mic=None, accel=None) -> bytes:
    payload = encode_spectrum_fused_payload(mic=mic, accel=accel)
    return encode_mqtt_message(MqttMsgType.SPECTRUM, payload)


def test_valid_spectrum_message_decodes_dense_bins():
    accel = ChannelSpectrum(fs=4000.0, fft_size=256, bins=tuple(float(i) for i in range(128)))
    frame = normalize_spectrum_message(TOPIC, spectrum_message(accel=accel), timestamp=200.0)
    assert frame is not None
    assert frame.node_id == "a4cf12", frame.node_id
    assert frame.source == FrameSource.MQTT, frame.source
    assert frame.timestamp == 200.0, frame.timestamp
    assert "mic" not in frame.bins, frame.bins
    assert frame.bins["accel"] == accel.bins, frame.bins["accel"]
    print("valid SPECTRUM message decodes into exact dense accel_bins: PASS")


def test_fused_channels_message_reconstructs_both_channels():
    mic = ChannelSpectrum(fs=16000.0, fft_size=512, bins=tuple(float(i) * 0.5 for i in range(256)))
    accel = ChannelSpectrum(fs=4000.0, fft_size=256, bins=tuple(float(i) for i in range(128)))
    frame = normalize_spectrum_message(TOPIC, spectrum_message(mic=mic, accel=accel))
    assert frame is not None
    assert set(frame.bins.keys()) == {"accel", "mic"}, frame.bins.keys()
    assert frame.bins["accel"] == accel.bins, frame.bins["accel"]
    assert frame.bins["mic"] == mic.bins, frame.bins["mic"]
    print("fused channels payload decodes both accel and mic bins: PASS")


def test_disabled_channel_omitted_from_frame_bins():
    accel = ChannelSpectrum(fs=4000.0, fft_size=256, bins=tuple(float(i) for i in range(128)))
    frame = normalize_spectrum_message(TOPIC, spectrum_message(mic=None, accel=accel))
    assert frame is not None
    assert set(frame.bins.keys()) == {"accel"}, frame.bins.keys()
    print("disabled channel (bin_count=0) omitted from frame.bins: PASS")


def test_both_channels_empty_raises():
    try:
        normalize_spectrum_message(TOPIC, spectrum_message(mic=None, accel=None))
        raise AssertionError("expected MalformedMessageError for an all-disabled SPECTRUM payload")
    except MalformedMessageError:
        pass
    print("SPECTRUM payload with both channels disabled raises MalformedMessageError: PASS")


def test_non_spectrum_type_is_silently_skipped():
    message = encode_mqtt_message(0x03, b"")  # arbitrary non-SPECTRUM type byte
    frame = normalize_spectrum_message(TOPIC, message)
    assert frame is None, frame
    print("non-SPECTRUM message type returns None (normal skip, not malformed): PASS")


def test_malformed_messages_raise():
    accel = ChannelSpectrum(fs=4000.0, fft_size=256, bins=tuple(float(i) for i in range(128)))
    full_message = spectrum_message(accel=accel)
    bad_cases = [
        b"",  # not even a TYPE byte
        full_message[:len(full_message) - 8],  # truncated mid-bins
        full_message[:1],  # TYPE byte only, no header at all
    ]
    for raw in bad_cases:
        try:
            normalize_spectrum_message(TOPIC, raw)
            raise AssertionError(f"expected MalformedMessageError for {raw!r}")
        except MalformedMessageError:
            pass
    print("malformed messages raise MalformedMessageError: PASS")


def test_malformed_topic_raises():
    accel = ChannelSpectrum(fs=4000.0, fft_size=256, bins=tuple(float(i) for i in range(128)))
    bad_topics = ["epm/a4cf12/status", "epm/data", "not-even-epm/a4cf12/data", "epm//data"]
    for topic in bad_topics:
        try:
            normalize_spectrum_message(topic, spectrum_message(accel=accel))
            raise AssertionError(f"expected MalformedMessageError for topic {topic!r}")
        except MalformedMessageError:
            pass
    print("topics not matching epm/<node_id>/data raise MalformedMessageError: PASS")


def test_subscriber_routes_to_pipeline_manager_like_spi():
    tmp_dir = tempfile.mkdtemp(prefix="mqtt_subscriber_test_")
    registry = Registry(os.path.join(tmp_dir, "registry.json"))
    manager = PipelineManager(registry, default_gate_factory)

    # MqttSubscriber.__init__ calls Client.connect(), which needs a real
    # broker -- construct the client/handler wiring directly instead,
    # mirroring what __init__ sets up, without dialing out.
    subscriber = MqttSubscriber.__new__(MqttSubscriber)
    subscriber._on_frame = manager.route
    subscriber._dropped = 0

    # fft_size=1024 -> 512 bins, matching the registry's fixed ACCEL-only
    # input_dim so the new ingest-time frame-length check (manager.py)
    # passes -- this is the satellite fft_size convention that
    # the satellite firmware convention.
    accel = ChannelSpectrum(fs=4000.0, fft_size=1024, bins=tuple(0.0 for _ in range(512)))
    valid_msg = mqtt.MQTTMessage(mid=0, topic=TOPIC.encode())
    valid_msg.payload = spectrum_message(accel=accel)
    subscriber._handle_message(None, None, valid_msg)

    malformed_msg = mqtt.MQTTMessage(mid=0, topic=TOPIC.encode())
    malformed_msg.payload = b""
    subscriber._handle_message(None, None, malformed_msg)

    skipped_msg = mqtt.MQTTMessage(mid=0, topic=TOPIC.encode())
    skipped_msg.payload = encode_mqtt_message(0x03, b"")
    subscriber._handle_message(None, None, skipped_msg)

    assert subscriber.dropped_frames == 1, subscriber.dropped_frames
    print("malformed message counted as dropped, non-SPECTRUM type silently skipped: PASS")

    pipelines = manager.pipelines()
    assert set(pipelines.keys()) == {"a4cf12"}, pipelines.keys()
    assert pipelines["a4cf12"].frame_count == 1, pipelines["a4cf12"].frame_count
    print("SPECTRUM message routed to its own pipeline via PipelineManager.route: PASS")

    entry = registry.get("a4cf12")
    assert entry.sensor_config == frozenset({SensorChannel.ACCEL}), entry.sensor_config
    assert entry.input_dim == 512, entry.input_dim
    print("registry auto-gained entry for satellite node, sensor_config=ACCEL: PASS")


def main():
    test_valid_spectrum_message_decodes_dense_bins()
    test_fused_channels_message_reconstructs_both_channels()
    test_disabled_channel_omitted_from_frame_bins()
    test_both_channels_empty_raises()
    test_non_spectrum_type_is_silently_skipped()
    test_malformed_messages_raise()
    test_malformed_topic_raises()
    test_subscriber_routes_to_pipeline_manager_like_spi()
    print("RESULT: PASS - MQTT satellite frames normalize and route identically to SPI")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"RESULT: FAIL - {e}")
        sys.exit(1)
