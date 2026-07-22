#!/usr/bin/env python3
"""
tools/satellite_node_sim.py's data-shaping logic: build_frame() (raw capture
windows -> wire sections, gated by the fused/per-axis/mic/scalars toggles and
bin-count knobs) and CaptureState/load_capture (streaming a raw_capture.py
-shaped .npz file window-by-window). No MQTT/HTTP involved, so directly
testable like telemetry_frame_test.py/raw_features_test.py.

Covers the zero-fill behavior for fused accel/mic (module docstring's
"Zero-fill, not omit"): those two sections are ALWAYS present once
build_frame() is called at all, real values when enabled and the capture has
the data, an all-zero spectrum at the same bin count otherwise -- this is
what keeps a node's frame shape constant for PipelineManager's committed
sensor_config/input_dim, fixing a real crash (toggling a channel off
mid-session used to omit its section, which broke ingestion for the whole
MQTT fleet, not just this node -- see ingestion/mqtt_subscriber.py's
docstring/tests for the other half of that fix). Per-axis and scalars are
never validated (display-only), so they stay freely omittable.

Run with PYTHONPATH covering base-station/python/common and
base-station/python/tools:
    PYTHONPATH=base-station/python/common:base-station/python/tools \\
        python3 base-station/tests/satellite_node_sim_test.py
"""
import os
import sys
import tempfile

import numpy as np

import telemetry_schema as schema
from telemetry_frame import decode_frame, encode_frame
from satellite_node_sim import ACCEL_AXES, MIC_CHANNEL, CaptureState, build_frame, load_capture

SOURCE_ID = schema.SOURCE_ID["satellite"]


def _signal(n: int, freq_bin: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    return (np.sin(2 * np.pi * freq_bin * t / n) + 0.01 * rng.standard_normal(n)).astype(np.float32)


def _windows(include_axes=ACCEL_AXES, include_mic=True):
    out = {}
    for i, name in enumerate(include_axes):
        out[name] = (_signal(1024, freq_bin=3 + i, seed=i), 6400.0)
    if include_mic:
        out[MIC_CHANNEL] = (_signal(2048, freq_bin=50, seed=99), 48000.0)
    return out


def _build(windows, **overrides):
    kwargs = dict(accel_fused=True, accel_per_axis=True, mic=True, scalars=(),
                  bin_count=64, mic_bin_count=128, axis_bin_count=32, source_id=SOURCE_ID)
    kwargs.update(overrides)
    return build_frame(windows, **kwargs)


def test_fused_and_per_axis_and_mic_all_emit():
    sections, preview, scalar_values = _build(_windows())
    decoded = decode_frame(encode_frame(sections))
    assert set(decoded.bins) == {"accel", "accel_x", "accel_y", "accel_z", "mic"}, decoded.bins
    assert len(decoded.bins["accel_x"]) == 32, len(decoded.bins["accel_x"])  # axis_bin_count
    assert len(decoded.bins["accel"]) == 64, len(decoded.bins["accel"])      # bin_count
    assert len(decoded.bins["mic"]) == 128, len(decoded.bins["mic"])         # mic_bin_count
    assert decoded.scalars == {}
    assert "accel" in preview and "mic" in preview
    print("test_fused_and_per_axis_and_mic_all_emit: PASS")


def test_accel_fused_disabled_zero_fills_not_omits():
    """The whole point of the fix: turning fused accel off must NOT remove
    its section -- it must still appear, at the committed bin count, with
    all-zero values (real data, not a structural absence)."""
    sections, preview, _scalars = _build(_windows(), accel_fused=False, accel_per_axis=False, mic=False)
    decoded = decode_frame(encode_frame(sections))
    assert set(decoded.bins) == {"accel", "mic"}, decoded.bins
    assert decoded.bins["accel"] == tuple(0.0 for _ in range(64)), decoded.bins["accel"]
    assert preview["accel"].bins == decoded.bins["accel"]
    print("test_accel_fused_disabled_zero_fills_not_omits: PASS")


def test_mic_disabled_zero_fills_not_omits():
    sections, _preview, _scalars = _build(_windows(), accel_fused=False, accel_per_axis=False, mic=False)
    decoded = decode_frame(encode_frame(sections))
    assert decoded.bins["mic"] == tuple(0.0 for _ in range(128)), decoded.bins["mic"]
    print("test_mic_disabled_zero_fills_not_omits: PASS")


def test_per_axis_and_scalars_freely_omitted():
    """Unlike accel/mic, per-axis and scalars are display-only/never
    validated -- they're genuinely absent (not zero-filled) when off."""
    sections, _preview, scalar_values = _build(_windows(), accel_per_axis=False, scalars=())
    decoded = decode_frame(encode_frame(sections))
    assert set(decoded.bins) == {"accel", "mic"}, decoded.bins
    assert decoded.scalars == {}
    assert scalar_values == {}
    print("test_per_axis_and_scalars_freely_omitted: PASS")


def test_scalar_subset_emitted_under_perf_channel():
    sections, _preview, scalar_values = _build(_windows(), scalars=("rms", "peak"))
    decoded = decode_frame(encode_frame(sections))
    expected_ids = {schema.SCALAR_ID_BY_NAME["rms"], schema.SCALAR_ID_BY_NAME["peak"]}
    assert set(decoded.scalars) == expected_ids, decoded.scalars
    assert set(scalar_values) == {"rms", "peak"}, scalar_values
    print("test_scalar_subset_emitted_under_perf_channel: PASS")


def test_missing_accel_axis_tolerated():
    """A capture missing one accel axis: per-axis still emits the axes that
    ARE present, fused still sums whatever's present (real, not zero, since
    at least one axis exists), but scalars (needing all 3 axes for
    vector_magnitude) are skipped entirely -- no crash."""
    windows = _windows(include_axes=("accel_x_raw", "accel_z_raw"))  # no accel_y_raw
    sections, _preview, scalar_values = _build(windows, scalars=("rms",))
    decoded = decode_frame(encode_frame(sections))
    assert set(decoded.bins) == {"accel_x", "accel_z", "accel", "mic"}, decoded.bins
    assert "accel_y" not in decoded.bins
    assert any(b != 0.0 for b in decoded.bins["accel"]), "2 of 3 axes present -- fused must be real, not zero"
    assert decoded.scalars == {}, "scalars need all 3 axes, must be skipped, not crash"
    assert scalar_values == {}
    print("test_missing_accel_axis_tolerated: PASS")


def test_accel_structurally_absent_zero_fills():
    """No accel axes at all in the capture (not just toggled off) -- fused
    accel must still be present, zero-filled, same as the toggle-off case."""
    windows = _windows(include_axes=())
    sections, _preview, _scalars = _build(windows, accel_per_axis=False)
    decoded = decode_frame(encode_frame(sections))
    assert set(decoded.bins) == {"accel", "mic"}, decoded.bins
    assert decoded.bins["accel"] == tuple(0.0 for _ in range(64)), decoded.bins["accel"]
    print("test_accel_structurally_absent_zero_fills: PASS")


def test_mic_structurally_absent_zero_fills():
    windows = _windows(include_mic=False)
    sections, _preview, _scalars = _build(windows)
    decoded = decode_frame(encode_frame(sections))
    assert set(decoded.bins) == {"accel", "accel_x", "accel_y", "accel_z", "mic"}, decoded.bins
    assert decoded.bins["mic"] == tuple(0.0 for _ in range(128)), decoded.bins["mic"]
    print("test_mic_structurally_absent_zero_fills: PASS")


def test_no_data_at_all_still_zero_fills_accel_and_mic():
    """Even with a completely empty windows dict (e.g. a capture file with
    none of the 4 raw channels -- degenerate, but must not crash), fused
    accel/mic still come out zero-filled rather than the frame having zero
    sections; only the caller (_publish_loop) skips publishing entirely when
    next_windows() itself returns {} before build_frame is ever called."""
    sections, preview, scalar_values = _build({}, accel_per_axis=False)
    decoded = decode_frame(encode_frame(sections))
    assert set(decoded.bins) == {"accel", "mic"}, decoded.bins
    assert decoded.bins["accel"] == tuple(0.0 for _ in range(64))
    assert decoded.bins["mic"] == tuple(0.0 for _ in range(128))
    assert scalar_values == {}
    print("test_no_data_at_all_still_zero_fills_accel_and_mic: PASS")


def _write_npz_capture(path: str, label: str, num_windows: int = 3, with_mic: bool = True) -> None:
    data = {"label": label}
    for name in ACCEL_AXES:
        data[name] = np.stack([_signal(1024, freq_bin=5, seed=i) for i in range(num_windows)])
        data[f"{name}_fs"] = np.float32(6400.0)
    if with_mic:
        data[MIC_CHANNEL] = np.stack([_signal(2048, freq_bin=50, seed=i) for i in range(num_windows)])
        data[f"{MIC_CHANNEL}_fs"] = np.float32(48000.0)
    np.savez(path, **data)


def test_capture_state_streams_and_wraps():
    with tempfile.TemporaryDirectory() as tmp_dir:
        _write_npz_capture(os.path.join(tmp_dir, "healthy.npz"), "healthy", num_windows=3)
        state = CaptureState()
        state.set_file("healthy.npz", tmp_dir)
        assert state.label() == "healthy"

        seen = []
        for _ in range(3):
            windows = state.next_windows()
            assert set(windows) == set(ACCEL_AXES) | {MIC_CHANNEL}, windows
            seen.append(windows[ACCEL_AXES[0]][0].tobytes())
        # a 4th call should wrap back to the first window (same bytes as the first)
        wrapped = state.next_windows()
        assert wrapped[ACCEL_AXES[0]][0].tobytes() == seen[0]
        print("test_capture_state_streams_and_wraps: PASS")


def test_capture_missing_channel_tolerated():
    with tempfile.TemporaryDirectory() as tmp_dir:
        _write_npz_capture(os.path.join(tmp_dir, "accel_only.npz"), "accel_only", with_mic=False)
        capture = load_capture(os.path.join(tmp_dir, "accel_only.npz"))
        assert MIC_CHANNEL not in capture, capture.keys()
        state = CaptureState()
        state.set_file("accel_only.npz", tmp_dir)
        windows = state.next_windows()
        assert MIC_CHANNEL not in windows, windows
        assert set(windows) == set(ACCEL_AXES)
        print("test_capture_missing_channel_tolerated: PASS")


def main():
    test_fused_and_per_axis_and_mic_all_emit()
    test_accel_fused_disabled_zero_fills_not_omits()
    test_mic_disabled_zero_fills_not_omits()
    test_per_axis_and_scalars_freely_omitted()
    test_scalar_subset_emitted_under_perf_channel()
    test_missing_accel_axis_tolerated()
    test_accel_structurally_absent_zero_fills()
    test_mic_structurally_absent_zero_fills()
    test_no_data_at_all_still_zero_fills_accel_and_mic()
    test_capture_state_streams_and_wraps()
    test_capture_missing_channel_tolerated()
    print("RESULT: PASS - satellite_node_sim build_frame()/CaptureState verified "
          "(including zero-fill for fused accel/mic)")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"RESULT: FAIL - {e}")
        sys.exit(1)
