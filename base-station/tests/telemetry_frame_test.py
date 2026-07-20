#!/usr/bin/env python3
"""
Generic sensor telemetry frame codec verification
(docs/SENSOR_TELEMETRY_FRAME_PLAN.md S3, Phase A -- T1/T3/T4).

Exercises common/telemetry_frame.py's decode_frame() against the section-list
payload the MCU writer (sketch/fuser.cpp's write_spectrum_section) emits and
that ingestion/spi_reader.py parses:

  - round-trips a mic+accel frame identically to what's on the wire today
    (T1's "confirm the new format round-trips before changing anything");
  - decodes a byte-exact hand-built frame matching the C writer's layout, so
    this test is the pinned contract the two generated sides agree on;
  - skips an unrecognized data_kind / channel_id by section_len (the property
    that lets the MPU side need zero code changes, plan S3);
  - treats a present all-zero SPECTRUM as real data (zero-fill, plan S4) while
    bin_count=0 contributes no channel;
  - decodes SCALAR_SET / TIME_SERIES bodies;
  - raises MalformedFrameError on structural corruption.

Run with PYTHONPATH covering base-station/python/common (and registry, for the
SensorChannel-value channel names this asserts against):
    PYTHONPATH=base-station/python/common:base-station/python/registry \\
        python3 base-station/tests/telemetry_frame_test.py
"""
import struct
import sys

import telemetry_schema as schema
from telemetry_frame import (
    DecodedFrame,
    MalformedFrameError,
    decode_frame,
    encode_frame,
    encode_scalar_body,
    encode_section,
    encode_spectrum_body,
    encode_spectrum_frame,
    encode_timeseries_body,
)

MIC = schema.CHANNEL_ID_BY_NAME["mic"]
ACCEL = schema.CHANNEL_ID_BY_NAME["accel"]
BASE = schema.SOURCE_ID["base_station"]
K_SPECTRUM = schema.DATA_KIND["SPECTRUM"]
K_SCALAR = schema.DATA_KIND["SCALAR_SET"]
K_TIME = schema.DATA_KIND["TIME_SERIES"]


def test_round_trip_mic_accel():
    mic_bins = tuple(float(i) * 0.5 for i in range(512))
    accel_bins = tuple(float(i) for i in range(512))
    payload = encode_spectrum_frame(BASE, [
        (MIC, 16000.0, 1024, mic_bins),
        (ACCEL, 12800.0, 1024, accel_bins),
    ])
    decoded = decode_frame(payload)
    assert set(decoded.bins.keys()) == {"mic", "accel"}, decoded.bins.keys()
    assert decoded.bins["mic"] == mic_bins
    assert decoded.bins["accel"] == accel_bins
    assert decoded.spectra["mic"].fs == 16000.0
    assert decoded.spectra["mic"].fft_size == 1024
    assert decoded.spectra["accel"].fs == 12800.0
    assert decoded.unknown_sections == 0
    print("mic+accel section frame round-trips to exact bins/metadata: PASS")


def test_byte_exact_layout_matches_c_writer():
    """Hand-build the exact bytes sketch/fuser.cpp's write_spectrum_section
    emits for one mic section, so a drift in either generated side trips here."""
    bins = (1.0, 2.0, 3.0)
    body = struct.pack("<fHH", 16000.0, 1024, len(bins)) + struct.pack("<3f", *bins)
    section = struct.pack("<BBBH", BASE, MIC, K_SPECTRUM, len(body)) + body
    payload = struct.pack("<B", 1) + section

    # ... and confirm the codec produces byte-identical output.
    assert encode_spectrum_frame(BASE, [(MIC, 16000.0, 1024, bins)]) == payload

    decoded = decode_frame(payload)
    assert decoded.bins["mic"] == bins
    assert decoded.spectra["mic"].fs == 16000.0 and decoded.spectra["mic"].fft_size == 1024
    print("byte-exact hand-built frame matches codec + decodes correctly: PASS")


def test_unknown_kind_skipped_by_length():
    good = encode_section(BASE, ACCEL, K_SPECTRUM, encode_spectrum_body(12800.0, 256, (1.0, 2.0)))
    unknown_kind = 0x7A  # not in schema.DATA_KIND
    junk = encode_section(BASE, 9, unknown_kind, b"\xde\xad\xbe\xef\x01\x02")
    payload = encode_frame([junk, good])  # unknown first, real section after it
    decoded = decode_frame(payload)
    assert decoded.bins == {"accel": (1.0, 2.0)}, decoded.bins
    assert decoded.unknown_sections == 1, decoded.unknown_sections
    print("unrecognized data_kind is skipped by section_len; later sections still decode: PASS")


def test_unknown_channel_id_not_in_bins():
    mystery_channel = 200  # not in schema.CHANNEL_NAME_BY_ID
    payload = encode_spectrum_frame(BASE, [(mystery_channel, 1000.0, 128, (5.0, 6.0))])
    decoded = decode_frame(payload)
    assert decoded.bins == {}, decoded.bins
    assert decoded.unknown_sections == 1
    print("spectrum on an unmapped channel_id stays out of frame.bins: PASS")


def test_zero_fill_present_section_is_real_data():
    """plan S4: a structurally-absent channel is sent as a present, zero-valued
    SPECTRUM section (real bin_count, all-zero values) -- decoded as real zeros,
    NOT omitted. Distinct from bin_count=0, which contributes no channel."""
    zeros = tuple(0.0 for _ in range(64))
    payload = encode_spectrum_frame(BASE, [
        (MIC, 16000.0, 128, (1.0, 2.0, 3.0)),
        (ACCEL, 12800.0, 128, zeros),  # zero-filled accel slot
    ])
    decoded = decode_frame(payload)
    assert decoded.bins["accel"] == zeros, "zero-filled channel must be present as zeros"
    assert "accel" in decoded.bins
    print("zero-filled channel decodes as present zeros (not omitted): PASS")


def test_bin_count_zero_omits_channel():
    payload = encode_spectrum_frame(BASE, [
        (MIC, 16000.0, 128, (1.0, 2.0, 3.0)),
        (ACCEL, 12800.0, 128, ()),  # bin_count=0 -> channel not present
    ])
    decoded = decode_frame(payload)
    assert "accel" not in decoded.bins, decoded.bins
    assert "mic" in decoded.bins
    # the spectrum metadata is still recorded even with no bins
    assert "accel" in decoded.spectra
    print("bin_count=0 SPECTRUM omits the channel from frame.bins: PASS")


def test_scalar_set_decodes():
    rms = schema.SCALAR_ID_BY_NAME["rms"]
    kurt = schema.SCALAR_ID_BY_NAME["kurtosis"]
    body = encode_scalar_body({rms: 0.75, kurt: 3.2})
    payload = encode_frame([encode_section(BASE, schema.PERF_CHANNEL_ID, K_SCALAR, body)])
    decoded = decode_frame(payload)
    assert abs(decoded.scalars[rms] - 0.75) < 1e-6, decoded.scalars
    assert abs(decoded.scalars[kurt] - 3.2) < 1e-6, decoded.scalars
    assert decoded.bins == {}
    print("SCALAR_SET body decodes to id->value scalars: PASS")


def test_new_scalar_tile_ids_decode():
    """docs/CHART_CLUTTER_PLAN.md S1's scalar tiles beyond rms/kurtosis --
    crest_factor/peak/std/skewness, added to telemetry_schema.json alongside
    the per-axis accel channels below."""
    ids = {name: schema.SCALAR_ID_BY_NAME[name]
           for name in ("crest_factor", "peak", "std", "skewness")}
    values = {ids["crest_factor"]: 4.5, ids["peak"]: 12.0, ids["std"]: 0.8, ids["skewness"]: -0.2}
    body = encode_scalar_body(values)
    payload = encode_frame([encode_section(BASE, schema.PERF_CHANNEL_ID, K_SCALAR, body)])
    decoded = decode_frame(payload)
    for sid, expected in values.items():
        assert abs(decoded.scalars[sid] - expected) < 1e-6, decoded.scalars
    print("new scalar tile ids (crest_factor/peak/std/skewness) decode: PASS")


def test_per_axis_accel_channels_resolve_to_names():
    """Per-axis accel spectra (docs/CHART_CLUTTER_PLAN.md S1's multi-axis
    overlay chart) are additive channels, separate from the fused `accel`
    channel the model reads -- confirm all three resolve to their own names
    and land in frame.bins independently."""
    x_bins = tuple(float(i) for i in range(64))
    y_bins = tuple(float(i) * 2 for i in range(64))
    z_bins = tuple(float(i) * 3 for i in range(64))
    payload = encode_spectrum_frame(BASE, [
        (schema.CHANNEL_ID_BY_NAME["accel_x"], 1600.0, 1024, x_bins),
        (schema.CHANNEL_ID_BY_NAME["accel_y"], 1600.0, 1024, y_bins),
        (schema.CHANNEL_ID_BY_NAME["accel_z"], 1600.0, 1024, z_bins),
    ])
    decoded = decode_frame(payload)
    assert decoded.bins["accel_x"] == x_bins, decoded.bins
    assert decoded.bins["accel_y"] == y_bins, decoded.bins
    assert decoded.bins["accel_z"] == z_bins, decoded.bins
    assert "accel" not in decoded.bins, "fused accel channel must stay independent"
    print("per-axis accel_x/y/z channels resolve to distinct names in frame.bins: PASS")


def test_time_series_decodes():
    samples = (0.5, -0.25, 0.125, -0.75)  # exactly representable in float32
    body = encode_timeseries_body(48000.0, samples)
    payload = encode_frame([encode_section(BASE, ACCEL, K_TIME, body)])
    decoded = decode_frame(payload)
    assert ACCEL in decoded.time_series
    ts = decoded.time_series[ACCEL]
    assert ts.fs == 48000.0
    assert ts.samples == samples
    print("TIME_SERIES body decodes to fs + samples: PASS")


def test_malformed_frames_raise():
    good = encode_spectrum_frame(BASE, [(MIC, 16000.0, 1024, (1.0, 2.0, 3.0))])
    bad_cases = {
        "empty": b"",
        "truncated mid-section": good[:len(good) - 4],
        # num_sections claims 3 but only one section's bytes follow
        "over-claimed num_sections": struct.pack("<B", 3) + good[1:],
        # section_len larger than the bytes actually present
        "section_len past end": struct.pack("<B", 1) +
            struct.pack("<BBBH", BASE, MIC, K_SPECTRUM, 9999) + b"\x00\x00",
    }
    for name, raw in bad_cases.items():
        try:
            decode_frame(raw)
            raise AssertionError(f"expected MalformedFrameError for {name!r}")
        except MalformedFrameError:
            pass
    print("structurally corrupt frames raise MalformedFrameError: PASS")


def test_empty_frame_zero_sections():
    decoded = decode_frame(struct.pack("<B", 0))
    assert decoded == DecodedFrame(), decoded
    print("a zero-section frame decodes to an empty result: PASS")


def main():
    test_round_trip_mic_accel()
    test_byte_exact_layout_matches_c_writer()
    test_unknown_kind_skipped_by_length()
    test_unknown_channel_id_not_in_bins()
    test_zero_fill_present_section_is_real_data()
    test_bin_count_zero_omits_channel()
    test_scalar_set_decodes()
    test_new_scalar_tile_ids_decode()
    test_per_axis_accel_channels_resolve_to_names()
    test_time_series_decodes()
    test_malformed_frames_raise()
    test_empty_frame_zero_sections()
    print("RESULT: PASS - generic section-list telemetry frame codec round-trips, "
          "skips unknown sections, and handles zero-fill")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"RESULT: FAIL - {e}")
        sys.exit(1)
