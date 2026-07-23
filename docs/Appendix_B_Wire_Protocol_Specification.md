# Appendix B: Wire Protocol Specification — UART and WiFi/MQTT

## Overview

The EdgeAI Predictive Monitor (EPM) base station (UNO Q / QRB2210) communicates with two
classes of sensing node over two distinct physical transports:

| Node | Transport | Link type |
|---|---|---|
| STM32U585 (on-board MCU) | UART (LPUART1) | Point-to-point, full duplex |
| ESP32 satellite nodes | WiFi (base station-hosted AP) + MQTT | Multi-node, full duplex |

Both transports carry the **same conceptual message types**. Only the framing
differs, because each transport already provides different guarantees natively:
UART is a raw byte stream with no inherent message boundaries, while MQTT already
provides framing, addressing (topics), and delivery semantics (QoS).

This appendix defines: (1) the shared message type table, (2) the UART frame format,
and (3) the WiFi/MQTT topic and payload format.

---

## 1. Shared Message Types

| TYPE (hex) | Name | Direction | Purpose |
|---|---|---|---|
| 0x01 | SPECTRUM | Node → Base Station | FFT bins or peak data from vibration sensing |
| 0x02 | HEALTH_ALERT | Node → Base Station | Anomaly threshold crossing (μ+3σ) |
| 0x03 | HEARTBEAT | Node → Base Station | Liveness signal, current config echo |
| 0x04 | COMMISSION_START | Base Station → Node | Begin commissioning capture |
| 0x05 | COMMISSION_DONE | Base Station → Node | Commissioning complete; switch to inference mode |
| 0x06 | CONFIG_SET | Base Station → Node | Sample rate, FFT size, active channel config |
| 0x07 | ACK | Either direction | Acknowledge receipt of a critical message |
| 0x08 | STATUS_LED | Base Station → Node | Drive the node's status LED to reflect its current dashboard status |

TYPE values are reserved identically across both transports so that base station-side
ingestion code can treat a message uniformly regardless of which link it arrived on.

**TYPE range convention** (applies to the UART header byte; informative for MQTT too):

```
0x01-0x3F  Node -> Base Station (data plane)
0x40-0x7F  Base Station -> Node (control plane)
0x80-0xFF  Reserved
```

---

## 2. UART Transport (STM32U585 <-> QRB2210)

### Link characteristics

- Physical link: LPUART1, `/dev/ttyHS1` on the QRB2210 side, hardware RTS/CTS flow control.
- Point-to-point: exactly one MCU on this link. `NODE_ID` is therefore a constant
  (`0x00`), reserved for symmetry with the WiFi-side schema and potential future use
  (e.g., logging, multi-MCU expansion), not for addressing.
- Full duplex: independent TX/RX wires. STM32U585 may stream SPECTRUM on TX while
  simultaneously receiving CONFIG_SET or COMMISSION_START on RX without contention.
  This requires the Zephyr application to implement an interrupt-driven (or DMA-driven)
  RX path in addition to the TX path, since UART RX cannot be a blocking poll loop if
  control messages must be received promptly during continuous streaming.

### Frame format

Because raw UART is a byte stream with no inherent message boundaries, an explicit
frame format is required:

```
[SYNC: 2B][VER: 1B][TYPE: 1B][NODE_ID: 1B][LEN: 2B][PAYLOAD: N bytes][CRC16: 2B]
```

| Field | Size | Description |
|---|---|---|
| SYNC | 2 bytes | Fixed marker `0xAA55`. Allows the receiver to resynchronize after a dropped byte or buffer overrun. |
| VER | 1 byte | Protocol version. Matches the version convention already used in `edgeai-wire-protocol`. |
| TYPE | 1 byte | Message type (see shared table above). |
| NODE_ID | 1 byte | `0x00` for the STM32U585 link (single node, fixed value). |
| LEN | 2 bytes | Length of PAYLOAD in bytes, little-endian. |
| PAYLOAD | N bytes | Message-specific content (see per-type payload layouts below). |
| CRC16 | 2 bytes | Checksum over VER..PAYLOAD (inclusive), detects UART bit errors. |

### Payload layouts (UART, binary)

**SPECTRUM (0x01)** - STM32U585 sends full FFT bin data (not just peaks, unlike the
older ESP32/WiFi path this table used to describe -- both transports now carry the
full spectrum this way; see S3). A single fused payload always carries both
channels' headers, mic first, then accel -- a disabled channel has `BIN_COUNT = 0`
and contributes no bin bytes, never both disabled at once:

```
[MIC_FS: 4B float][MIC_FFT_SIZE: 2B uint][MIC_BIN_COUNT: 2B uint]
[ACCEL_FS: 4B float][ACCEL_FFT_SIZE: 2B uint][ACCEL_BIN_COUNT: 2B uint]
[MIC_BINS: MIC_BIN_COUNT x 4B float][ACCEL_BINS: ACCEL_BIN_COUNT x 4B float]
```

- `*_FS`: sample rate in Hz used for this channel's capture (`0` if disabled).
- `*_FFT_SIZE`: FFT length used for this channel (`0` if disabled).
- `*_BIN_COUNT`: number of magnitude bins included for this channel (may be less
  than `*_FFT_SIZE`/2 if truncated to a frequency range of interest; `0` if disabled).
- `*_BINS`: magnitude values, one 4-byte float per bin, mic's array immediately
  followed by accel's.

Matches `mcu/src/frame_codec/frame_types.h`'s `struct spectrum_fused_payload_header`
and `mpu/common/wire_protocol.py`'s `encode_spectrum_fused_payload()`/
`decode_spectrum_fused_payload()` exactly -- the two must stay byte-for-byte
compatible. `*_FS`/`*_FFT_SIZE` travel on the wire per frame (rather than being
fixed knowledge the receiver hardcodes separately) so a receiver never has to know
each sensor's sample rate/FFT length out of band.

**HEALTH_ALERT (0x02)**:
```
[TIMESTAMP: 4B uint][ANOMALY_SCORE: 4B float][THRESHOLD: 4B float]
```

**HEARTBEAT (0x03)**:
```
[UPTIME_SEC: 4B uint][CURRENT_FS: 4B float][CURRENT_FFT_SIZE: 2B uint]
```

**COMMISSION_START (0x04)** / **COMMISSION_DONE (0x05)**: empty payload (`LEN = 0`);
the TYPE itself is the instruction.

**CONFIG_SET (0x06)**:
```
[FS: 4B float][FFT_SIZE: 2B uint][ACTIVE_CHANNELS: 1B bitmask]
```

**ACK (0x07)**:
```
[ACKED_TYPE: 1B][ACKED_SEQ: 2B uint]
```
(Note: UART path may omit sequence numbering if not needed given the point-to-point,
low-loss nature of the wired link - to be confirmed during implementation.)

---

## 3. WiFi/MQTT Transport (ESP32 <-> QRB2210)

### Link characteristics

- ESP32 nodes connect to the UNO Q-hosted WiFi access point (SSID `EPM-BaseStation`,
  2.4 GHz, base station at `10.42.0.1`).
- Mosquitto MQTT broker runs on the UNO Q; ESP32 nodes and the base station backend are both
  MQTT clients.
- Full duplex via pub/sub: nodes publish data topics, the base station publishes command
  topics; each side subscribes to the other's topics.
- Node identity: derived automatically from each ESP32's factory-assigned WiFi MAC
  address (e.g., last 6 hex characters), avoiding any per-device flashing-time
  configuration despite all nodes running identical firmware. A separate mapping
  table on the base station (MAC-derived ID -> human-friendly name, e.g., `motor_07`) handles
  friendly naming for the dashboard, decoupling wire-level identity from
  deployment-time naming.

### Topic structure

```
epm/<node_id>/data     - Node -> Base Station (SPECTRUM, HEALTH_ALERT, HEARTBEAT)
epm/<node_id>/cmd      - Base Station -> Node (COMMISSION_START, COMMISSION_DONE, CONFIG_SET, STATUS_LED)
epm/<node_id>/ack      - Either direction (ACK)
```

`<node_id>` is the MAC-derived identifier (e.g., `a4cf12`).

### Payload format (binary)

MQTT already provides message framing, addressing (topics), and reliable delivery,
so payloads use a much leaner envelope than UART's SYNC/LEN/CRC16 frame -- just a
single TYPE byte in front of the type-specific payload:

```
[TYPE: 1B][PAYLOAD: N bytes]
```

`TYPE` is the numeric byte from the shared TYPE table (Section 1) -- the same
values UART uses, unlike an earlier revision of this spec which spelled `type` as
a JSON string. There is no `VER`/`NODE_ID`/`LEN`/`CRC16` on this wire: version
negotiation isn't needed yet (no field currently varies across implementations),
node identity comes from the topic (`epm/<node_id>/data` / `epm/<node_id>/cmd`) --
a real `node_id` is a MAC-derived string, too large for a 1-byte field the way
UART's fixed `NODE_ID = 0x00` is -- and MQTT's own delivery guarantees make a
length prefix and checksum redundant.

**Only SPECTRUM and STATUS_LED are implemented today** (`mpu/common/
wire_protocol.py`'s `MqttMsgType`, `mpu/ingestion/mqtt_subscriber.py`, `mpu/
ingestion/mqtt_publisher.py`, and the ESP32 stand-in `mpu/tools/
satellite_node_sim.py`). HEALTH_ALERT/HEARTBEAT/COMMISSION_START/COMMISSION_DONE/
CONFIG_SET/ACK below are still sketches of a payload shape, not implemented
wire formats -- when built, they should follow this same lean binary envelope
rather than reintroducing JSON, for consistency with SPECTRUM/STATUS_LED.

An earlier revision of this spec used a JSON envelope (`{"ver", "type", "node_id",
"seq", "ts", "payload"}`) with SPECTRUM carrying sparse top-N FFT peaks
(`{"fs", "fft_size", "peaks": [{"freq", "mag"}, ...]}`) instead of the full
spectrum, motivated by ESP32/`esp-dsp` bandwidth and processing constraints. That
capped how much of the spectrum ever reached the base station and cost far more
bytes per bin than a packed float32 array. It was replaced outright (no dual
JSON/binary transition path) since no real satellite-node firmware existed yet to
require one -- see S4 below.

### Payload contents by type (WiFi/MQTT, binary)

**SPECTRUM (0x01)** - same `spectrum_fused_payload` struct UART uses (S2's
Payload layouts, `MIC_FS`/`MIC_FFT_SIZE`/`MIC_BIN_COUNT`/`ACCEL_FS`/
`ACCEL_FFT_SIZE`/`ACCEL_BIN_COUNT` header followed by the two bin arrays) --
one codec, two transports, differing only in the outer envelope. A node with
only one active channel sends `BIN_COUNT = 0` for the other (never both `0`);
base station-side ingestion commits a node's full `sensor_config` (S4.2) once
at commissioning and validates every subsequent frame carries bins for *all*
of it at once, not a channel at a time -- same requirement the old fused JSON
shape existed to satisfy.

**HEALTH_ALERT (0x02)** *(not yet implemented; illustrative payload shape)*:
```
[TIMESTAMP: 4B uint][ANOMALY_SCORE: 4B float][THRESHOLD: 4B float]
```

**HEARTBEAT (0x03)** *(not yet implemented; illustrative payload shape)*:
```
[UPTIME_SEC: 4B uint][CURRENT_FS: 4B float][CURRENT_FFT_SIZE: 2B uint]
```

**COMMISSION_START (0x04)** / **COMMISSION_DONE (0x05)** *(not yet implemented)*:
empty payload (`PAYLOAD` is zero bytes); the TYPE itself is the instruction.

**CONFIG_SET (0x06)** *(not yet implemented; illustrative payload shape)*:
```
[FS: 4B float][FFT_SIZE: 2B uint][ACTIVE_CHANNELS: 1B bitmask]
```

**ACK (0x07)** *(not yet implemented; illustrative payload shape)*:
```
[ACKED_TYPE: 1B][ACKED_SEQ: 2B uint]
```

**STATUS_LED (0x08)** - pushed by the base station whenever this node's
dashboard status changes (commissioned, confirmed healthy/warning/fault,
paused/resumed, etc.), so the node's own status LED always reflects what the
dashboard currently shows without the node ever having to poll the REST API.
Same `display_rgb_payload` struct UART's DISPLAY_RGB (0x02) uses:
```
[RGB: 4B uint][MODE: 1B uint][PERIOD_MS: 2B uint]
```
- `RGB`: packed `0xRRGGBB`. Uses the same color-per-status values the dashboard
  frontend renders with (`mpu/frontend/style.css`'s
  `--color-new/healthy/warning/fault`), so the LED and the dashboard tile for
  a given node are always the same color.
- `MODE`: `0` = CONST, `1` = BREATHE, `2` = STROBE -- the same values as the
  UART-side MCU display's `enum rgb_display_mode` (`mcu/src/hal/
  hal_display_rgb.h`'s `RGB_DISPLAY_CONST/BREATHE/STROBE`), so both LED
  command paths (UART MCU display vs. MQTT satellite) mean the same thing by
  "mode" -- unlike an earlier revision of this spec, which spelled `mode` as
  a JSON string (`"const"/"breathe"/"strobe"`).
- `PERIOD_MS`: blink/breathe period in milliseconds; ignored when
  `MODE == 0` (CONST) (mirrors `hal_display_rgb_set()`'s own doc comment for
  the UART path).

### QoS assignment

| Message type | QoS | Rationale |
|---|---|---|
| SPECTRUM | 0 | High frequency; occasional loss acceptable; avoid broker overhead on the dominant data stream. |
| HEARTBEAT | 0 | Same rationale as SPECTRUM. |
| HEALTH_ALERT | 1 | Must arrive; duplicates are harmless (idempotent alert). |
| COMMISSION_START / COMMISSION_DONE | 1 | Critical state transition; must be delivered. |
| CONFIG_SET | 1 | Must be delivered; duplicates are harmless (idempotent config write). |
| ACK | 0 | Acknowledgments are advisory, not critical-path. |
| STATUS_LED | 1 | Must be delivered; duplicates are harmless (idempotent -- always the current status, not a delta). |

---

## 4. Cross-transport consistency notes

- The shared TYPE table (Section 1) is the single source of truth; both transports
  now use the same numeric `TYPE` byte (`MqttMsgType` in `mpu/common/
  wire_protocol.py` mirrors the shared table's values for the types it implements,
  independent of `MsgType`'s UART-link-specific numbering -- see that module for
  why the two enums aren't merged) -- keep both in sync if new types are added.
- SPECTRUM and STATUS_LED now share the exact same payload struct codec between
  UART and MQTT (`mpu/common/wire_protocol.py`'s `spectrum_fused_payload`/
  `display_rgb_payload` encode/decode functions) -- the transports differ only in
  the outer envelope (UART's SYNC/LEN/CRC16 frame vs. MQTT's single TYPE byte),
  not in payload shape. This reverses an earlier, deliberate divergence (full bins
  over UART vs. sparse peak-only over WiFi/MQTT, justified by ESP32/`esp-dsp`
  bandwidth constraints at the time) -- the sparse representation both capped the
  transmitted spectrum and cost more bytes per bin than a packed float32 array, so
  it was dropped once nothing depended on it (no real satellite-node firmware
  existed yet). HEALTH_ALERT/HEARTBEAT/COMMISSION_START/COMMISSION_DONE/
  CONFIG_SET/ACK remain unimplemented on both transports; when built, keep them
  struct-for-struct identical between UART and MQTT the same way, for the same
  reason.
- Base Station-side ingestion (FastAPI backend) should normalize both transports into one
  internal message model immediately upon receipt, so downstream processing
  (anomaly detection, storage, dashboard) does not need to be aware of which
  transport a given message arrived through.
