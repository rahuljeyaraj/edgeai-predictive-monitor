"""Feature builder -- raw mic/accel spectra + time-domain scalars ->
normalized model input vector, per docs/MPU_Software_Architecture.md
S3.3/S8 M5 and docs/SENSOR_TELEMETRY_FRAME_PLAN.md's per-axis+scalars
feature-representation work.

Resolves open question #4 (S6): the per-sensor spectral dim is not
always accel-only -- MIC and the per-axis ACCEL_X/Y/Z channels are
distinct, non-interchangeable spectral variants (different physical
quantity, different bins), so sensor_config selects *which* raw bins feed
the vector, not just its length. The expected length itself is the
caller's node's own committed `entry.input_dim` (pipeline/manager.py's
_infer_sensor_config_and_dim, which now folds in scalar_dim_for() below
too) -- not every node necessarily uses the same per-channel bin count.
"""
from typing import Dict, FrozenSet, Tuple

from sensor_frame import SensorFrame
from registry import SensorChannel

# Per-channel time-domain scalars (rms/kurtosis/std/peak/crest_factor/
# skewness), computed on-device separately per accel axis and on the mic
# raw window (fuser.cpp), NOT on the combined tri-axial vector magnitude --
# tools/offline_experiment.py found that combining x/y/z into one magnitude
# erases the directional signature an imbalance fault produces (+1.8 sigma
# vs. +38.5 sigma per-axis on the same real captures). Fixed order matches
# the wire schema's scalar names (base-station/telemetry_schema.json:
# "rms_x", "kurtosis_x", ..., "skewness_mic") and, like normalize_bins'
# ordering below, is part of the model input layout -- changing it breaks
# compatibility with already-trained weights.
SCALAR_NAMES: Tuple[str, ...] = ("rms", "kurtosis", "std", "peak", "crest_factor", "skewness")

# Which SensorChannel members carry a scalar block, and the wire-name
# suffix each one's 6 scalars use (f"{scalar}_{suffix}", e.g. "rms_x",
# "skewness_mic"). Only channels present in a node's sensor_config
# contribute scalars -- a mic-less or accel-less node just gets fewer.
_SCALAR_SUFFIX_BY_CHANNEL: Dict[SensorChannel, str] = {
    SensorChannel.ACCEL_X: "x",
    SensorChannel.ACCEL_Y: "y",
    SensorChannel.ACCEL_Z: "z",
    SensorChannel.MIC: "mic",
}


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


def scalar_dim_for(sensor_config: FrozenSet[SensorChannel]) -> int:
    """How many scalar-tail columns build_feature_vector() appends for a
    given sensor_config -- len(SCALAR_NAMES) per channel that has a scalar
    suffix mapping and is actually present. Public: pipeline/manager.py
    needs this too (_infer_sensor_config_and_dim/_validate_frame_bins sum
    spectral bins only; without adding this, every node's committed
    input_dim would be short by exactly this many columns and every frame
    would fail bin-count validation forever)."""
    return sum(len(SCALAR_NAMES) for c in _SCALAR_SUFFIX_BY_CHANNEL if c in sensor_config)


def standardize_scalars(vector: Tuple[float, ...], spectral_dim: int,
                         mu: Tuple[float, ...], sigma: Tuple[float, ...]) -> Tuple[float, ...]:
    """Z-score the scalar tail columns (rms/kurtosis/... aren't naturally in
    [0, 1] the way peak-normalized spectrum bins are) using mu/sigma fit on
    this node's own healthy commissioning batch (pipeline/commissioning.py).
    Spectral columns are passed through untouched. Plain-Python port of
    tools/offline_experiment.py's standardize_scalars() (numpy) -- the
    production pipeline has no numpy dependency and this is cheap enough
    (24 columns) not to need one. mu/sigma are None for a sensor_config with
    no scalar tail at all (spectral_dim == len(vector)); callers should just
    not standardize in that case, but this is a harmless no-op either way."""
    if spectral_dim >= len(vector):
        return vector
    tail = vector[spectral_dim:]
    standardized = tuple(
        (v - m) / (s if s > 1e-9 else 1.0)
        for v, m, s in zip(tail, mu, sigma)
    )
    return vector[:spectral_dim] + standardized


def build_feature_vector(frame: SensorFrame, sensor_config: FrozenSet[SensorChannel],
                          expected_dim: int) -> Tuple[Tuple[float, ...], int]:
    """Each present channel's spectrum bins are normalized independently,
    then concatenated in fixed SensorChannel declaration order (MIC then
    ACCEL_X/Y/Z, not sorted-by-name) when more than one is present -- a
    single joint peak would let whichever sensor happens to have larger raw
    magnitude (mic vs accelerometer units are unrelated) swamp the other's
    shape. The fixed order matters beyond just this call: it's the model
    input layout, so changing it would silently break compatibility with
    any already-trained/saved autoencoder weights. The same present-channel
    set then contributes its raw (not yet standardized -- see
    standardize_scalars()) scalar tail, in SCALAR_NAMES order.

    sensor_config (from that node's registry entry) selects which channels
    are expected; expected_dim (that same entry's own committed input_dim)
    is the resulting vector length -- passed in explicitly rather than
    re-derived from a fixed per-channel table, since a node's actual bin
    count is whatever its first frame committed to, not necessarily 512.
    Raises if the frame's bin counts don't match, or if a channel's scalar
    values are missing from frame.scalars entirely -- both are a base
    station/node misconfiguration (e.g. version skew against an older
    firmware), not a normal runtime case to silently paper over with a
    zero-filled column that would quietly corrupt training/inference.

    Returns (vector, spectral_dim) -- spectral_dim is where the scalar tail
    starts, needed by standardize_scalars() and by commissioning.py/
    inference.py to know how much of the vector to standardize."""
    vector: Tuple[float, ...] = ()
    for channel in SensorChannel:
        if channel in sensor_config:
            vector += normalize_bins(frame.bins.get(channel.value, ()))
    spectral_dim = len(vector)

    for channel in SensorChannel:
        suffix = _SCALAR_SUFFIX_BY_CHANNEL.get(channel)
        if suffix is None or channel not in sensor_config:
            continue
        for name in SCALAR_NAMES:
            key = f"{name}_{suffix}"
            if key not in frame.scalars:
                raise ValueError(
                    f"node {frame.node_id!r}: sensor_config="
                    f"{sorted(c.value for c in sensor_config)} expects scalar {key!r}, "
                    f"but frame.scalars has none (keys: {sorted(frame.scalars)})")
            vector += (frame.scalars[key],)

    if len(vector) != expected_dim:
        actual_counts = {c.value: len(frame.bins.get(c.value, ())) for c in sensor_config}
        raise ValueError(
            f"node {frame.node_id!r}: sensor_config={sorted(c.value for c in sensor_config)} expects "
            f"a {expected_dim}-dim vector, got {len(vector)} (bin counts: {actual_counts}, "
            f"scalar_dim={scalar_dim_for(sensor_config)})")
    return vector, spectral_dim
