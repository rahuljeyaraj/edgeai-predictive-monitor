"""
Wire codecs for the MQTT satellite link, ported from
edgeai-predictive-monitor-unoq/mpu/common/wire_protocol.py.

Two directions, deliberately asymmetric:

- **Node -> base station (telemetry / `epm/<node_id>/data`)** no longer lives
  here. It now carries the *same* generic section-list telemetry frame the SPI
  base-station link uses (common/telemetry_frame.py,
  docs/SENSOR_TELEMETRY_FRAME_PLAN.md S6): the MQTT message body IS the frame
  bytes, with no extra envelope (MQTT already frames + reliably delivers, so a
  second magic/seq/CRC or even a TYPE byte would be redundant). This uniformity
  is the point -- one payload format, one decoder, both transports. The old
  fixed `spectrum_fused_payload` codec that used to live here was removed.

- **Base station -> node (command / `epm/<node_id>/cmd`)** still uses the lean
  `[TYPE: 1B][PAYLOAD]` envelope below -- STATUS_LED is a command, not sensor
  telemetry, with its own tiny fixed payload, so it keeps its own framing rather
  than being forced into the telemetry-frame shape.

Node identity for both directions comes from the topic, not a wire field.
"""
import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Tuple

# display_rgb_payload -- matches the MCU-side frame_types.h struct the
# STATUS_LED command carries (mqtt_publisher.py's publish_status()).
DISPLAY_RGB_PAYLOAD_FMT = "<IBH"  # rgb, mode, period_ms
DISPLAY_RGB_PAYLOAD_LEN = struct.calcsize(DISPLAY_RGB_PAYLOAD_FMT)


@dataclass
class ChannelSpectrum:
    """One channel's spectrum (fs / fft_size / magnitude bins). Shared by the
    section-list codec (common/telemetry_frame.py) and the satellite sim."""
    fs: float
    fft_size: int
    bins: Tuple[float, ...]


def encode_display_rgb_payload(rgb: int, mode: int, period_ms: int) -> bytes:
    return struct.pack(DISPLAY_RGB_PAYLOAD_FMT, rgb, mode, period_ms)


def decode_display_rgb_payload(payload: bytes) -> Tuple[int, int, int]:
    """Returns (rgb, mode, period_ms) -- the inverse of
    encode_display_rgb_payload(). rgb is packed 0xRRGGBB; mode is 0/1/2
    (CONST/BREATHE/STROBE, see LED_MODE_TO_INT below)."""
    return struct.unpack(DISPLAY_RGB_PAYLOAD_FMT, payload[:DISPLAY_RGB_PAYLOAD_LEN])


class MqttMsgType(IntEnum):
    """TYPE values for the base-station -> node command direction
    (`epm/<node_id>/cmd`). The node -> base telemetry direction no longer uses
    a TYPE byte (it carries a raw section-list telemetry frame), so SPECTRUM is
    gone; extend this as more *commands* are implemented."""
    STATUS_LED = 0x08  # Base Station -> Node, payload: display_rgb_payload


def encode_mqtt_message(msg_type: int, payload: bytes = b"") -> bytes:
    """Command-direction framing: [TYPE: 1B][PAYLOAD]."""
    return bytes([msg_type]) + payload


def decode_mqtt_message(data: bytes) -> Tuple[int, bytes]:
    """Returns (type, payload). Raises ValueError on an empty message
    (can't even hold a TYPE byte) -- callers should treat that as a
    malformed message, same as a struct.error from a too-short payload."""
    if not data:
        raise ValueError("empty MQTT payload")
    return data[0], bytes(data[1:])


# rgb_display_mode spelled as this wire int for binary STATUS_LED, matching
# DISPLAY_RGB_PAYLOAD_FMT's mode byte exactly.
LED_MODE_TO_INT = {"const": 0, "breathe": 1, "strobe": 2}
LED_MODE_FROM_INT = {value: name for name, value in LED_MODE_TO_INT.items()}


def rgb_hex_to_int(rgb_hex: str) -> int:
    """"#RRGGBB" -> packed 0xRRGGBB, matching display_rgb_payload's rgb field."""
    return int(rgb_hex.lstrip("#"), 16)


def rgb_int_to_hex(rgb: int) -> str:
    return f"#{rgb:06x}"
