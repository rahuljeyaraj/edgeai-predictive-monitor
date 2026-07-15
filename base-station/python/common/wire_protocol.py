"""
Wire codecs shared by the MQTT satellite-node ingestion path
(ingestion/mqtt_subscriber.py, ingestion/mqtt_publisher.py), ported from
edgeai-predictive-monitor-unoq/mpu/common/wire_protocol.py.

Trimmed from the old repo's version: that file also carried a raw UART
[SYNC][VER][TYPE][NODE_ID][LEN][PAYLOAD][CRC16] envelope (FrameParser,
encode_frame, MsgType) for the old board's point-to-point Zephyr UART
link. This repo's MCU<->MPU link is Bridge RPC + the dedicated SPI link
(docs/progress2.md) instead, so that envelope has no counterpart here
and isn't carried forward. Only the MQTT envelope and the payload codecs
it shares with the (now-gone) UART link remain.

MQTT envelope: [TYPE: 1B][PAYLOAD] -- much leaner than the old UART
envelope since MQTT already frames and reliably delivers each message.
Node identity for this direction comes from the topic
(epm/<node_id>/data / epm/<node_id>/cmd), not a wire field.
"""
import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional, Tuple

# Matches the MCU/satellite-side spectrum_fused_payload_header exactly:
# mic_fs (float32), mic_fft_size (uint16), mic_bin_count (uint16), then the
# same three fields for accel -- mic first, accel second throughout.
# fs/fft_size travel on the wire per frame (rather than being fixed
# knowledge the receiver hardcodes separately) so a receiver never has to
# know each sensor's sample rate/FFT length out of band. The two bin
# arrays are NOT part of this struct -- they follow immediately after the
# header in the wire bytes, mic_bins first, sized from the two bin_count
# fields above.
SPECTRUM_HEADER_FMT = "<fHHfHH"  # mic_fs, mic_fft_size, mic_bin_count, accel_fs, accel_fft_size, accel_bin_count
SPECTRUM_HEADER_LEN = struct.calcsize(SPECTRUM_HEADER_FMT)

# display_rgb_payload -- matches the MCU-side frame_types.h struct the
# STATUS_LED command carries (mqtt_publisher.py's publish_status()).
DISPLAY_RGB_PAYLOAD_FMT = "<IBH"  # rgb, mode, period_ms
DISPLAY_RGB_PAYLOAD_LEN = struct.calcsize(DISPLAY_RGB_PAYLOAD_FMT)


@dataclass
class ChannelSpectrum:
    """One channel's worth of a decoded/to-be-encoded SPECTRUM payload."""
    fs: float
    fft_size: int
    bins: Tuple[float, ...]


_EMPTY_CHANNEL = ChannelSpectrum(fs=0.0, fft_size=0, bins=())


def encode_display_rgb_payload(rgb: int, mode: int, period_ms: int) -> bytes:
    return struct.pack(DISPLAY_RGB_PAYLOAD_FMT, rgb, mode, period_ms)


def decode_display_rgb_payload(payload: bytes) -> Tuple[int, int, int]:
    """Returns (rgb, mode, period_ms) -- the inverse of
    encode_display_rgb_payload(). rgb is packed 0xRRGGBB; mode is 0/1/2
    (CONST/BREATHE/STROBE, see LED_MODE_TO_INT below)."""
    return struct.unpack(DISPLAY_RGB_PAYLOAD_FMT, payload[:DISPLAY_RGB_PAYLOAD_LEN])


def encode_spectrum_fused_payload(mic: Optional[ChannelSpectrum],
                                   accel: Optional[ChannelSpectrum]) -> bytes:
    """Inverse of decode_spectrum_fused_payload(). Pass None for a
    disabled channel (encoded as fs=0/fft_size=0/bin_count=0, no bin
    bytes emitted) -- never both None."""
    mic = mic if mic is not None else _EMPTY_CHANNEL
    accel = accel if accel is not None else _EMPTY_CHANNEL
    header = struct.pack(SPECTRUM_HEADER_FMT,
                          mic.fs, mic.fft_size, len(mic.bins),
                          accel.fs, accel.fft_size, len(accel.bins))
    mic_bytes = struct.pack(f"<{len(mic.bins)}f", *mic.bins)
    accel_bytes = struct.pack(f"<{len(accel.bins)}f", *accel.bins)
    return header + mic_bytes + accel_bytes


def decode_spectrum_fused_payload(payload: bytes) -> Tuple[ChannelSpectrum, ChannelSpectrum]:
    """Returns (mic, accel), each a ChannelSpectrum (bins empty if that
    sensor was disabled on the sender - its count is 0, never both)."""
    mic_fs, mic_fft_size, mic_bin_count, accel_fs, accel_fft_size, accel_bin_count = struct.unpack(
        SPECTRUM_HEADER_FMT, payload[:SPECTRUM_HEADER_LEN])

    offset = SPECTRUM_HEADER_LEN
    mic_bins = struct.unpack(f"<{mic_bin_count}f", payload[offset:offset + mic_bin_count * 4])

    offset += mic_bin_count * 4
    accel_bins = struct.unpack(f"<{accel_bin_count}f", payload[offset:offset + accel_bin_count * 4])

    return (ChannelSpectrum(mic_fs, mic_fft_size, mic_bins),
            ChannelSpectrum(accel_fs, accel_fft_size, accel_bins))


class MqttMsgType(IntEnum):
    """TYPE values for the MQTT satellite link. Only the two types this
    codebase actually implements over MQTT are defined; extend as
    HEALTH_ALERT/HEARTBEAT/etc. are implemented."""
    SPECTRUM = 0x01    # Node -> Base Station, payload: spectrum_fused_payload
    STATUS_LED = 0x08  # Base Station -> Node, payload: display_rgb_payload


def encode_mqtt_message(msg_type: int, payload: bytes = b"") -> bytes:
    """MQTT payload framing: [TYPE: 1B][PAYLOAD]."""
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
