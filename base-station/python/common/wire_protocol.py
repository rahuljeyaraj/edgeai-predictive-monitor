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

- **Node -> base station (event / `epm/<node_id>/evt`)** carries the *same*
  `[TYPE][PAYLOAD]` command envelope in reverse, for the one thing that is a
  reply rather than telemetry: WIFI_PROVISION_ACK (fleet WiFi roaming,
  docs/WIFI_ONBOARDING_PLAN.md S6). A separate topic, not `/data`, keeps the
  telemetry decoder's "every byte on /data is a section-list frame" rule
  intact.

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

# motor_stop_payload -- the machinery-protection trip (docs/MOTOR_STOP_PLAN.md).
# One byte: which motor on the rig to stop, 1-based, matching motor-driver's
# own serial command numbering ("1 0" stops motor 1).
#
# No "stop: bool" field, deliberately: there is no un-stop. Protection is a
# one-way safety output -- restarting a machine is a human action taken at the
# machine, never something this system can command. Adding a bool would imply
# an authority the design does not have.
MOTOR_STOP_PAYLOAD_FMT = "<B"  # motor_idx
MOTOR_STOP_PAYLOAD_LEN = struct.calcsize(MOTOR_STOP_PAYLOAD_FMT)

# wifi_provision_payload -- fleet WiFi roaming (docs/WIFI_ONBOARDING_PLAN.md
# S6): the base station hands its whole fleet the network it is about to join
# itself, so one form onboards every node instead of one captive portal per
# device.
#
# Fixed-width char arrays (not length-prefixed strings) so the ESP32 side can
# memcpy the payload straight onto its struct node_credentials fields, whose
# CREDS_*_MAX_LEN caps these lengths mirror exactly (satellite/include/hal/
# hal_credentials.h). Every field is NUL-padded to its full width; the wire
# size is therefore constant (169B) regardless of credential length, which
# also means a long password is not distinguishable from a short one by
# message size alone.
#
# roam_id is echoed back in the ack below. It exists because a node acks
# *before* it switches networks (it must -- the switch is what kills the link
# carrying the ack), so a retry of an unanswered push would otherwise be
# indistinguishable from the first one, and a late ack from an earlier attempt
# could be counted against the current one.
WIFI_PROVISION_SSID_LEN = 33    # CREDS_SSID_MAX_LEN + 1 (NUL)
WIFI_PROVISION_PASS_LEN = 65    # CREDS_PASS_MAX_LEN + 1
WIFI_PROVISION_BROKER_LEN = 65  # CREDS_BROKER_MAX_LEN + 1
WIFI_PROVISION_PAYLOAD_FMT = (
    f"<I{WIFI_PROVISION_SSID_LEN}s{WIFI_PROVISION_PASS_LEN}s"
    f"{WIFI_PROVISION_BROKER_LEN}sH")  # roam_id, ssid, password, broker_host, broker_port
WIFI_PROVISION_PAYLOAD_LEN = struct.calcsize(WIFI_PROVISION_PAYLOAD_FMT)

# wifi_provision_ack_payload -- node -> base station, on epm/<node_id>/evt.
#
# The only node -> base station message that uses the [TYPE][PAYLOAD] command
# envelope. It rides its own /evt topic rather than /data precisely so the
# telemetry decoder (common/telemetry_frame.py) never sees a byte that is not
# a section-list frame: /data's "the body IS the frame, no envelope" rule
# (module docstring above) stays intact.
WIFI_PROVISION_ACK_PAYLOAD_FMT = "<IB"  # roam_id, status
WIFI_PROVISION_ACK_PAYLOAD_LEN = struct.calcsize(WIFI_PROVISION_ACK_PAYLOAD_FMT)


class WifiProvisionAckStatus(IntEnum):
    """What a node is telling us in its ack. Deliberately not "joined" --
    the node cannot report a successful join over a link the join itself
    tears down, so ACCEPTED means "credentials taken, switching now" and the
    real confirmation is the node reappearing on the new network."""
    ACCEPTED = 0    # saved the push, switching to that network now
    SIMULATED = 1   # a host-process sim node: no radio, nothing to switch
    REJECTED = 2    # unusable push (e.g. empty SSID) -- staying put


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


def encode_motor_stop_payload(motor_idx: int) -> bytes:
    return struct.pack(MOTOR_STOP_PAYLOAD_FMT, motor_idx)


def encode_wifi_provision_payload(roam_id: int, ssid: str, password: str,
                                   broker_host: str, broker_port: int) -> bytes:
    """Raises ValueError if any field is too long for the node-side buffer it
    lands in -- silently truncating a password to 64 bytes would produce a
    fleet-wide join failure whose cause is invisible at both ends."""
    for name, value, cap in (("ssid", ssid, WIFI_PROVISION_SSID_LEN),
                              ("password", password, WIFI_PROVISION_PASS_LEN),
                              ("broker host", broker_host, WIFI_PROVISION_BROKER_LEN)):
        if len(value.encode("utf-8")) > cap - 1:
            raise ValueError(f"{name} is longer than {cap - 1} bytes")
    return struct.pack(WIFI_PROVISION_PAYLOAD_FMT, roam_id, ssid.encode("utf-8"),
                        password.encode("utf-8"), broker_host.encode("utf-8"), broker_port)


def decode_wifi_provision_payload(payload: bytes) -> Tuple[int, str, str, str, int]:
    """Returns (roam_id, ssid, password, broker_host, broker_port) -- the
    inverse of encode_wifi_provision_payload(). Exists for the sim node and
    for tests; the real consumer is the ESP32 firmware's own memcpy."""
    roam_id, ssid, password, broker, port = struct.unpack(
        WIFI_PROVISION_PAYLOAD_FMT, payload[:WIFI_PROVISION_PAYLOAD_LEN])
    return (roam_id, ssid.split(b"\0", 1)[0].decode("utf-8"),
            password.split(b"\0", 1)[0].decode("utf-8"),
            broker.split(b"\0", 1)[0].decode("utf-8"), port)


def encode_wifi_provision_ack_payload(roam_id: int, status: int) -> bytes:
    return struct.pack(WIFI_PROVISION_ACK_PAYLOAD_FMT, roam_id, status)


def decode_wifi_provision_ack_payload(payload: bytes) -> Tuple[int, int]:
    """Returns (roam_id, status) -- status is a WifiProvisionAckStatus value."""
    return struct.unpack(WIFI_PROVISION_ACK_PAYLOAD_FMT,
                          payload[:WIFI_PROVISION_ACK_PAYLOAD_LEN])


def decode_motor_stop_payload(payload: bytes) -> int:
    """Returns motor_idx -- the inverse of encode_motor_stop_payload().

    motor-driver/motor_driver.py's trip listener deliberately re-implements this
    one-byte unpack locally instead of importing this module: it runs on a
    different machine entirely (the host laptop with the rig on USB), the same
    way the ESP32 satellite firmware re-declares this file's wire formats in
    C++ rather than sharing code. This function stays the source of truth
    those copies are written against."""
    return struct.unpack(MOTOR_STOP_PAYLOAD_FMT, payload[:MOTOR_STOP_PAYLOAD_LEN])[0]


class MqttMsgType(IntEnum):
    """TYPE values for the base-station -> node command direction
    (`epm/<node_id>/cmd`), plus the one reply that rides the same envelope
    back on `epm/<node_id>/evt` (WIFI_PROVISION_ACK). The node -> base
    *telemetry* direction no longer uses a TYPE byte (it carries a raw
    section-list telemetry frame), so SPECTRUM is gone; extend this as more
    commands are implemented."""
    STATUS_LED = 0x08  # Base Station -> Node, payload: display_rgb_payload
    MOTOR_STOP = 0x09  # Base Station -> rig host, payload: motor_stop_payload
    WIFI_PROVISION = 0x0a      # Base Station -> Node, payload: wifi_provision_payload
    WIFI_PROVISION_ACK = 0x0b  # Node -> Base Station (epm/<node_id>/evt), payload: wifi_provision_ack_payload


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
