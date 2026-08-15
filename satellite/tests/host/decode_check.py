#!/usr/bin/env python3
"""
decode_check.py -- acceptance check for tests/host/frame.bin against the
reference repo's UNMODIFIED Python pipeline (read-only import, never
copied/edited -- docs/decisions/ADR-011-mqtt-transport-added.md).

Unlike base-station/tests/telemetry_frame_test.py (which only exercises his
codec against hand-built payloads of his own), this runs OUR encoder's
output through his real decode_frame() / normalize_spectrum_message() /
_infer_sensor_config_and_dim() / build_feature_vector(), which is the actual
acceptance bar: does a real satellite frame get this node registered and
scored the way his dashboard expects.

Usage:
    python3 decode_check.py <path-to-reference-repo-root> [path-to-frame.bin]

<path-to-reference-repo-root> is the sibling clone's root, e.g.
C:\\Users\\abhin\\Documents\\edgeai-predictive-monitor-ref (Windows python) or
/mnt/c/Users/abhin/Documents/edgeai-predictive-monitor-ref (WSL python) --
passed explicitly rather than hardcoded so this runs unmodified under
whichever interpreter has his dependencies (torch, paho-mqtt) installed.

Requires his requirements.txt deps on PYTHONPATH's interpreter: torch,
paho-mqtt (only paho-mqtt is actually needed for the functions this touches;
torch is a transitive import of pipeline/manager.py -> inference.py ->
autoencoder.py, never called here).
"""
import os
import sys

FRAME_LEN = 2251
NUM_SECTIONS = 5
EXPECTED_INPUT_DIM = 536  # 4 x 128 spectral bins + 24 scalars


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: decode_check.py <reference-repo-root> [frame.bin]", file=sys.stderr)
        return 2

    ref_root = sys.argv[1]
    frame_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "out", "frame.bin")

    py_root = os.path.join(ref_root, "base-station", "python")
    for sub in ("common", "registry", "ingestion", "pipeline"):
        sys.path.insert(0, os.path.join(py_root, sub))

    import telemetry_frame  # common/telemetry_frame.py
    import mqtt_subscriber  # ingestion/mqtt_subscriber.py
    import manager  # pipeline/manager.py
    import features  # pipeline/features.py
    from registry import SensorChannel  # registry/registry.py

    with open(frame_path, "rb") as f:
        payload = f.read()

    assert len(payload) == FRAME_LEN, f"expected {FRAME_LEN} bytes, got {len(payload)}"
    assert payload[0] == NUM_SECTIONS, f"expected {NUM_SECTIONS} sections, got {payload[0]}"

    # 1. His raw codec: 4 spectrum channels of 128 bins each, 24 scalars.
    decoded = telemetry_frame.decode_frame(payload)
    assert len(decoded.bins) == 4, f"expected 4 spectrum channels, got {len(decoded.bins)}"
    for name, bins in decoded.bins.items():
        assert len(bins) == 128, f"channel {name!r}: expected 128 bins, got {len(bins)}"
    assert len(decoded.scalars) == 24, f"expected 24 scalars, got {len(decoded.scalars)}"

    # 2. His MQTT normalization: must NOT be None (the SCALAR_SET-only frame
    # the original Phase 0.5 prompt specified WOULD be None here -- decoded.bins
    # empty -- and the node would never register; this is the regression
    # guard for that finding).
    frame = mqtt_subscriber.normalize_spectrum_message("epm/aabbcc/data", payload)
    assert frame is not None, "normalize_spectrum_message returned None (node would never register)"
    assert frame.node_id == "aabbcc"

    # 3. His registry commitment logic: sensor_config + input_dim from this
    # (first) frame.
    sensor_config, input_dim = manager._infer_sensor_config_and_dim(frame)
    expected_config = frozenset({SensorChannel.MIC, SensorChannel.ACCEL_X,
                                  SensorChannel.ACCEL_Y, SensorChannel.ACCEL_Z})
    assert sensor_config == expected_config, f"sensor_config={sensor_config}"
    assert input_dim == EXPECTED_INPUT_DIM, f"input_dim={input_dim}"

    # 4. His feature builder: proves the scalar tail is complete (every
    # rms/kurtosis/std/peak/crest_factor/skewness key build_feature_vector
    # needs is actually present in frame.scalars).
    vector, spectral_dim = features.build_feature_vector(frame, sensor_config, input_dim)
    assert len(vector) == EXPECTED_INPUT_DIM, f"vector len={len(vector)}"
    assert spectral_dim == 4 * 128, f"spectral_dim={spectral_dim}"

    print(f"RESULT: PASS (frame.bin: {len(payload)} bytes, node={frame.node_id}, "
          f"sensor_config={sorted(c.value for c in sensor_config)}, input_dim={input_dim})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
