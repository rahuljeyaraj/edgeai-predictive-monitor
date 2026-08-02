"""SensorFrame -- the common in-memory frame format, ported from
edgeai-predictive-monitor-unoq/mpu/ingestion/sensor_frame.py. Both
ingestion paths (SPI, MQTT) normalize into this same shape before
anything downstream (pipeline manager, gate, feature builder, ...)
touches them.

`bins` is keyed by SensorChannel value string (e.g. "mic"/"accel"), not
fixed mic_bins/accel_bins fields -- so downstream code (manager.py's
sensor_config inference, features.py's model vector) iterates whatever
channels a frame actually carries instead of assuming exactly two, AND every
key is guaranteed to be a real SensorChannel value -- that invariant is what
lets those consumers treat every bins entry as model-relevant without
checking. A channel absent from a frame is simply absent from this dict, not
present-with-None. (gate.py's energy computation is the one consumer that
does filter by channel name -- accelerometer only, deliberately excluding
mic -- see its own docstring.)

`display_bins` holds spectrum channels that exist purely for dashboard
display and deliberately do NOT correspond to any SensorChannel -- today
that's just the fused/combined `accel` channel, superseded by the per-axis
accel_x/y/z channels (which DO feed the model, and live in `bins` instead).
Kept in a separate dict rather than mixed into `bins` specifically so
gate/manager/features never have to filter `bins` themselves; the ingestion
layer (spi_reader.py/mqtt_subscriber.py) does that split once, resolving
telemetry_schema's channel names against registry.SensorChannel.

`scalars` is name-keyed the same way `bins` is (via telemetry_schema's
SCALAR_NAME_BY_ID, resolved by the ingestion layer) and IS model input --
pipeline/features.py appends it as the feature vector's scalar tail.

There is no `time_series` field: normal-mode firmware no longer streams
time-domain windows at all (2026-08-01, see sketch/fuser.cpp's header). The
one path that still puts TIME_SERIES sections on the wire is the offline
FUSER_RAW_CAPTURE_MODE build, and its consumers (tools/raw_capture.py,
tools/raw_capture_server.py) read common/telemetry_frame.py's DecodedFrame
directly via SpiConsumer's on_decoded hook -- they never go through
SensorFrame.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Tuple


class FrameSource(Enum):
    # SPI is this repo's local base-station link (fuser stream over the
    # dedicated MCU<->MPU SPI link, docs/progress2.md tasks 4-5) -- the
    # direct analog of the old repo's single-node UART link, not a
    # routable multi-node transport like MQTT.
    SPI = "spi"
    MQTT = "mqtt"


# This SPI link is point-to-point to a single base station board -- unlike
# MQTT (many satellite nodes -> one MPU), there's no per-frame node identity
# to route by on the wire, so this node_id is assigned by the ingestion
# layer (ingestion/spi_reader.py) rather than read off the wire. Kept here
# rather than in spi_reader.py itself so pure-logic code (tests, main.py)
# can reference it without transitively importing arduino.app_utils, which
# only exists inside the on-device App Lab container.
BASE_STATION_NODE_ID = "base_station"


@dataclass
class SensorFrame:
    node_id: str
    source: FrameSource
    timestamp: float
    bins: Dict[str, Tuple[float, ...]] = field(default_factory=dict)
    # docs/CHART_CLUTTER_PLAN.md S1's per-axis accel spectrum overlay --
    # display-only, never a SensorChannel, never read by gate/manager/features.
    display_bins: Dict[str, Tuple[float, ...]] = field(default_factory=dict)
    # Per-channel time-domain statistics (rms/kurtosis/std/peak/
    # crest_factor/skewness per accel axis + mic) -- name -> value. Model
    # input: features.py's build_feature_vector() appends these after the
    # spectral bins.
    scalars: Dict[str, float] = field(default_factory=dict)
    # name -> (fs, fft_size) for every channel present in `bins`/`display_bins`
    # (mic/accel/accel_x/accel_y/accel_z) -- lets the dashboard turn a bin
    # index into an actual frequency (k * fs / fft_size) instead of plotting
    # a raw, sample-rate-independent bin number. Sourced from the wire's own
    # SPECTRUM header (common/telemetry_frame.py's ChannelSpectrum), not a
    # frontend-side hardcoded constant, so it can't drift from whatever the
    # firmware is actually sampling at.
    spectrum_meta: Dict[str, Tuple[float, int]] = field(default_factory=dict)
