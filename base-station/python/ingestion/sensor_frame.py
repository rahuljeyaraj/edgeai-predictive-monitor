"""SensorFrame -- the common in-memory frame format, ported from
edgeai-predictive-monitor-unoq/mpu/ingestion/sensor_frame.py. Both
ingestion paths (SPI, MQTT) normalize into this same shape before
anything downstream (pipeline manager, gate, feature builder, ...)
touches them.

`bins` is keyed by SensorChannel value string (e.g. "mic"/"accel"), not
fixed mic_bins/accel_bins fields -- so downstream code iterates whatever
channels a frame actually carries instead of assuming exactly two. A
channel absent from a frame is simply absent from this dict, not
present-with-None.
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
