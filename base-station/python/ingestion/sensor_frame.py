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

`scalars`/`time_series` are the same kind of display-only addition for
docs/CHART_CLUTTER_PLAN.md S1 (scalar tiles, the collapsible "Raw signals"
panel) -- name-keyed the same way `bins` is (via telemetry_schema's
SCALAR_NAME_BY_ID/CHANNEL_NAME_BY_ID, resolved by the ingestion layer), and
never read by pipeline/features.py's model feature vector. `time_series`
values are a plain (fs, samples) tuple rather than common/telemetry_frame.py's
TimeSeries dataclass -- this module deliberately has no dependency on
common/ (several tests that transitively import SensorFrame don't have it on
PYTHONPATH), the same reason `bins`/`spectra` don't reuse
common/telemetry_frame.py's ChannelSpectrum either.
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
    # docs/CHART_CLUTTER_PLAN.md S1's scalar tiles -- name -> value.
    scalars: Dict[str, float] = field(default_factory=dict)
    # docs/CHART_CLUTTER_PLAN.md S1's collapsible "Raw signals" panel --
    # name -> (fs, samples). Only populated on frames that carry a
    # TIME_SERIES section (piggybacked every Nth frame, fuser.cpp); empty on
    # every other frame, not a stale carry-over of the last value.
    time_series: Dict[str, Tuple[float, Tuple[float, ...]]] = field(default_factory=dict)
    # name -> (fs, fft_size) for every channel present in `bins`/`display_bins`
    # (mic/accel/accel_x/accel_y/accel_z) -- lets the dashboard turn a bin
    # index into an actual frequency (k * fs / fft_size) instead of plotting
    # a raw, sample-rate-independent bin number. Sourced from the wire's own
    # SPECTRUM header (common/telemetry_frame.py's ChannelSpectrum), not a
    # frontend-side hardcoded constant, so it can't drift from whatever the
    # firmware is actually sampling at.
    spectrum_meta: Dict[str, Tuple[float, int]] = field(default_factory=dict)
