#!/usr/bin/env python3
"""
Milestone 11 verification: publish
synthetic satellite SPECTRUM messages and confirm a new pipeline is
created and routed exactly as SPI-sourced frames are.

No Mosquitto broker is available in this dev environment, so the two
concerns are exercised separately rather than over a real socket:

1. normalize_spectrum_message() -- the pure decode+normalize step -- is
   exercised directly with hand-built section-list telemetry frames (the exact
   same payload the SPI fuser frame carries -- common/telemetry_frame.py's
   encode_spectrum_frame()), the same decoder ingestion/spi_reader.py now uses.
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
from wire_protocol import ChannelSpectrum
import telemetry_schema as schema
from telemetry_frame import (encode_frame, encode_scalar_body, encode_section,
                              encode_spectrum_body, encode_spectrum_frame,
                              encode_timeseries_body)
from registry import Registry, SensorChannel
from gate import MotorStateGate
from manager import PipelineManager


def default_gate_factory() -> MotorStateGate:
    return MotorStateGate(threshold=0.05, debounce_frames=3)

TOPIC = "epm/a4cf12/data"

_SAT = schema.SOURCE_ID["satellite"]


def spectrum_message(mic=None, accel=None) -> bytes:
    """The raw section-list telemetry frame that is now the MQTT data-topic
    message body -- one SPECTRUM section per present channel, no envelope."""
    sections = []
    if mic is not None:
        sections.append((schema.CHANNEL_ID_BY_NAME["mic"], mic.fs, mic.fft_size, mic.bins))
    if accel is not None:
        sections.append((schema.CHANNEL_ID_BY_NAME["accel"], accel.fs, accel.fft_size, accel.bins))
    return encode_spectrum_frame(_SAT, sections)


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


def test_absent_channel_omitted_from_frame_bins():
    accel = ChannelSpectrum(fs=4000.0, fft_size=256, bins=tuple(float(i) for i in range(128)))
    frame = normalize_spectrum_message(TOPIC, spectrum_message(mic=None, accel=accel))
    assert frame is not None
    assert set(frame.bins.keys()) == {"accel"}, frame.bins.keys()
    print("channel with no section is absent from frame.bins: PASS")


def test_empty_frame_is_skipped():
    # A frame with no sections (num_sections=0) -- a heartbeat -- carries no
    # bins, so it's a normal skip (None), not a malformed drop.
    frame = normalize_spectrum_message(TOPIC, encode_spectrum_frame(_SAT, []))
    assert frame is None, frame
    print("empty (zero-section) heartbeat frame returns None (normal skip): PASS")


def test_scalar_only_frame_is_skipped():
    # A frame carrying only a SCALAR_SET section (e.g. health/perf) has no
    # spectrum bins to route -- also a normal skip, not malformed.
    body = encode_scalar_body({schema.SCALAR_ID_BY_NAME["rms"]: 0.5})
    payload = encode_frame([encode_section(_SAT, schema.PERF_CHANNEL_ID,
                                           schema.DATA_KIND["SCALAR_SET"], body)])
    frame = normalize_spectrum_message(TOPIC, payload)
    assert frame is None, frame
    print("scalar-only (no spectrum) frame returns None (normal skip): PASS")


def test_per_axis_accel_channels_land_in_display_bins_not_bins():
    """Regression test: per-axis accel_x/y/z spectra
    (docs/CHART_CLUTTER_PLAN.md S1) aren't a SensorChannel, so they must
    land in display_bins, NOT bins -- mixing them into bins broke
    manager.py's _infer_sensor_config() (SensorChannel('accel_x') raises)
    the first time this was tried against real hardware."""
    accel = ChannelSpectrum(fs=4000.0, fft_size=1024, bins=tuple(float(i) for i in range(512)))
    axis = ChannelSpectrum(fs=4000.0, fft_size=1024, bins=tuple(float(i) * 2 for i in range(512)))
    payload = encode_spectrum_frame(_SAT, [
        (schema.CHANNEL_ID_BY_NAME["accel"], accel.fs, accel.fft_size, accel.bins),
        (schema.CHANNEL_ID_BY_NAME["accel_x"], axis.fs, axis.fft_size, axis.bins),
        (schema.CHANNEL_ID_BY_NAME["accel_y"], axis.fs, axis.fft_size, axis.bins),
        (schema.CHANNEL_ID_BY_NAME["accel_z"], axis.fs, axis.fft_size, axis.bins),
    ])
    frame = normalize_spectrum_message(TOPIC, payload)
    assert frame is not None
    assert set(frame.bins.keys()) == {"accel"}, frame.bins.keys()
    assert set(frame.display_bins.keys()) == {"accel_x", "accel_y", "accel_z"}, \
        frame.display_bins.keys()
    assert frame.display_bins["accel_x"] == axis.bins, frame.display_bins["accel_x"]
    print("per-axis accel_x/y/z land in display_bins, model-facing bins only has accel: PASS")


def test_scalars_and_time_series_resolve_to_names():
    """docs/CHART_CLUTTER_PLAN.md S1: a frame carrying spectrum bins alongside
    a SCALAR_SET and a TIME_SERIES section should resolve the latter two to
    the same friendly names decoded.bins already uses (schema.py's
    SCALAR_NAME_BY_ID/CHANNEL_NAME_BY_ID) -- not the raw wire ids."""
    accel = ChannelSpectrum(fs=4000.0, fft_size=256, bins=tuple(float(i) for i in range(128)))
    scalar_body = encode_scalar_body({
        schema.SCALAR_ID_BY_NAME["rms"]: 0.5,
        schema.SCALAR_ID_BY_NAME["kurtosis"]: 3.2,
    })
    ts_body = encode_timeseries_body(4000.0, (1.0, 2.0, 3.0))
    payload = encode_frame([
        encode_section(_SAT, schema.CHANNEL_ID_BY_NAME["accel"], schema.DATA_KIND["SPECTRUM"],
                       encode_spectrum_body(accel.fs, accel.fft_size, accel.bins)),
        encode_section(_SAT, schema.PERF_CHANNEL_ID, schema.DATA_KIND["SCALAR_SET"], scalar_body),
        encode_section(_SAT, schema.CHANNEL_ID_BY_NAME["accel_x_raw"],
                       schema.DATA_KIND["TIME_SERIES"], ts_body),
    ])
    frame = normalize_spectrum_message(TOPIC, payload)
    assert frame is not None
    # float32 wire round-trip (e.g. 3.2 -> 3.200000047683716), same tolerance
    # telemetry_frame_test.py's own scalar assertions use.
    assert abs(frame.scalars["rms"] - 0.5) < 1e-6, frame.scalars
    assert abs(frame.scalars["kurtosis"] - 3.2) < 1e-6, frame.scalars
    assert frame.time_series["accel_x_raw"] == (4000.0, (1.0, 2.0, 3.0)), frame.time_series
    print("scalars/time_series resolve to friendly names alongside bins: PASS")


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
    skipped_msg.payload = encode_spectrum_frame(_SAT, [])  # heartbeat: no sections
    subscriber._handle_message(None, None, skipped_msg)

    assert subscriber.dropped_frames == 1, subscriber.dropped_frames
    print("malformed message counted as dropped, empty heartbeat frame silently skipped: PASS")

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
    test_absent_channel_omitted_from_frame_bins()
    test_empty_frame_is_skipped()
    test_scalar_only_frame_is_skipped()
    test_per_axis_accel_channels_land_in_display_bins_not_bins()
    test_scalars_and_time_series_resolve_to_names()
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
