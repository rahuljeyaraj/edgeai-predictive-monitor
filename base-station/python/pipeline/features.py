"""Feature builder -- raw mic/accel bins -> normalized model input
vector, per docs/MPU_Software_Architecture.md S3.3/S8 M5.

Resolves open question #4 (S6): the 512-dim single-sensor case is not
always accel-only -- MIC and ACCEL are two distinct, non-interchangeable
512-dim variants (different physical quantity, different bins), so
sensor_config selects *which* raw bins feed the vector, not just its
length. `registry.input_dim_for()` (S4.2) is the single source of truth
for the length that implies.
"""
from typing import FrozenSet, Tuple

from sensor_frame import SensorFrame
from registry import SensorChannel, input_dim_for


def normalize_bins(bins: Tuple[float, ...]) -> Tuple[float, ...]:
    """Peak-normalize one sensor's magnitude bins to [0, 1]. Overall
    signal amplitude (motor load, mic gain, sensor placement, ...) isn't
    itself diagnostic -- it's the *shape* of the spectrum the
    autoencoder needs to learn (S3.3), so each frame is rescaled to its
    own peak rather than carrying raw magnitude through."""
    if not bins:
        return ()
    peak = max(bins)
    if peak <= 0:
        return tuple(0.0 for _ in bins)
    return tuple(b / peak for b in bins)


def build_feature_vector(frame: SensorFrame, sensor_config: FrozenSet[SensorChannel]) -> Tuple[float, ...]:
    """Each present channel's bins are normalized independently, then
    concatenated in fixed SensorChannel declaration order (MIC then ACCEL,
    not sorted-by-name) when more than one is present -- a single joint
    peak would let whichever sensor happens to have larger raw magnitude
    (mic vs accelerometer units are unrelated) swamp the other's shape.
    The fixed order matters beyond just this call: it's the model input
    layout, so changing it would silently break compatibility with any
    already-trained/saved autoencoder weights.

    sensor_config (from that node's registry entry) selects which
    channels are expected and the resulting vector length (512 vs 1024,
    S4.2). Raises if the frame's bin counts don't match what sensor_config
    implies -- that's a base station/node misconfiguration, not a normal runtime
    case to silently paper over.
    """
    vector: Tuple[float, ...] = ()
    for channel in SensorChannel:
        if channel in sensor_config:
            vector += normalize_bins(frame.bins.get(channel.value, ()))

    expected_dim = input_dim_for(sensor_config)
    if len(vector) != expected_dim:
        actual_counts = {c.value: len(frame.bins.get(c.value, ())) for c in sensor_config}
        raise ValueError(
            f"node {frame.node_id!r}: sensor_config={sorted(c.value for c in sensor_config)} expects "
            f"a {expected_dim}-dim vector, got {len(vector)} (bin counts: {actual_counts})")
    return vector
