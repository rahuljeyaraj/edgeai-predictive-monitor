# satellite — ESP32S3 satellite node firmware

PlatformIO/Arduino firmware for the Seeed Studio XIAO ESP32S3, one of
potentially several **satellite** vibration/audio sensing nodes for the
EdgeAI Predictive Monitor. It ports [`base-station/sketch`](../base-station/sketch)
(the Zephyr firmware for the UNO Q's onboard STM32U585) to this board,
mirroring every functional piece — KX134 accelerometer, INMP441 I2S
microphone, WS2812 status ring, per-channel FFT, fused publish cadence —
except the transport: the base station talks SPI to its MPU over a wired
point-to-point link; this node talks WiFi + MQTT instead, since it isn't
physically attached to the UNO Q
([docs/SENSOR_TELEMETRY_FRAME_PLAN.md](../docs/SENSOR_TELEMETRY_FRAME_PLAN.md)
S6).

There is no LED matrix on this node (no MQTT command type exists for one,
and the existing satellite tooling doesn't model one either — see
[`python/tools/satellite_node_sim.py`](../base-station/python/tools/satellite_node_sim.py)).

## Wire format

Telemetry (`epm/<node_id>/data`) is the **same generic section-list frame**
the base station's own SPI link sends
([`base-station/telemetry_schema.json`](../base-station/telemetry_schema.json),
[docs/SENSOR_TELEMETRY_FRAME_PLAN.md](../docs/SENSOR_TELEMETRY_FRAME_PLAN.md)
S3/S6): `[num_sections u8]` then, per channel, a
`[source_id][channel_id][data_kind][section_len]` header followed by a
SPECTRUM body (`fs`/`fft_size`/`bin_count`/dense float32 bins). Published as
the raw MQTT message body with **no extra envelope** — MQTT already frames
and delivers each message, so a second TYPE byte would be redundant. This
node only ever emits SPECTRUM sections for `mic`/`accel`
(`frame_codec/spectrum_codec.h`, `struct spectrum_channel` +
`telemetry_build_spectrum_frame()`) — no per-axis/scalar/time-series
sections. Wire constants (`TELEM_SOURCE_SATELLITE`, `TELEM_CHANNEL_MIC`, …)
live in `include/frame_codec/telemetry_schema.h`, generated (not hand-edited)
by
[`python/tools/gen_telemetry_schema.py`](../base-station/python/tools/gen_telemetry_schema.py)
from the same schema file the base station and MPU parser are generated
from, so the three sides can't drift. This replaced an earlier fixed
`spectrum_fused_payload_header` struct (itself replacing a still-earlier
JSON envelope of top-N peaks per channel) once the base station's own wire
format moved to this generic section-list shape.

The command direction (`epm/<node_id>/cmd`, STATUS_LED) keeps its own lean
`[TYPE: 1B][display_rgb_payload]` envelope (`frame_codec/wire_protocol.h`) —
unaffected by the telemetry-format change above.

The wire shape matches
[`python/tools/satellite_node_sim.py`](../base-station/python/tools/satellite_node_sim.py)
and
[`python/ingestion/mqtt_subscriber.py`](../base-station/python/ingestion/mqtt_subscriber.py)
field-for-field, so a real node and the simulator are interchangeable from
the dashboard's point of view. FFT window sizes (1024 per channel → 512
bins, matching
[`python/registry/registry.py`](../base-station/python/registry/registry.py)'s
per-channel input dim) and the publish cadence (200ms) are set in
`include/app_config.h`.

## Hardware / wiring

XIAO ESP32S3 breaks out 11 GPIOs (D0–D10). Full pin rationale is in
`include/board_pins.h`; summary:

| Signal | Pin | Notes |
|---|---|---|
| KX134 SPI SCK | D8 | hardware SPI default |
| KX134 SPI MISO | D9 | hardware SPI default |
| KX134 SPI MOSI | D10 | hardware SPI default |
| KX134 CS | D3 | software chip-select |
| KX134 INT1 (BFI) | D2 | buffer-full interrupt |
| INMP441 WS/LRCLK | D0 | |
| INMP441 BCLK | D1 | |
| INMP441 SD (data in) | D4 | |
| WS2812 ring DIN | D5 | 8-pixel ring, same product as the base station's |

`LED_BUILTIN` (GPIO21, onboard) is the heartbeat indicator — independent
of the WS2812 ring, mirroring the base station sketch's own onboard-LED
heartbeat.

## Configuration

WiFi/MQTT settings live in `include/app_config.h` as overridable
`#define`s (`WIFI_SSID`, `WIFI_PASSWORD`, `MQTT_BROKER_HOST`,
`MQTT_BROKER_PORT`). Defaults assume the base station hosts its own AP
(SSID `EPM-BaseStation`) and runs the Mosquitto broker at `10.42.0.1` —
confirm these against the actual base-station deployment before flashing.
**`WIFI_PASSWORD` has no documented value and must be set before
flashing**, either by editing `app_config.h` directly or overriding via
`platformio.ini`'s `build_flags` (`-D WIFI_PASSWORD=\"...\"`).

Node identity is derived automatically from the ESP32's WiFi MAC address
(last 6 hex chars, lowercase) — no per-device flashing-time configuration.

## Build / flash

```sh
cd satellite
pio run                # build
pio run -t upload      # flash over USB (native USB CDC, no separate bridge chip)
pio device monitor     # serial log console (115200 baud)
```

After editing `base-station/telemetry_schema.json`, regenerate every
generated side (including this firmware's
`include/frame_codec/telemetry_schema.h`) with:

```sh
python3 base-station/python/tools/gen_telemetry_schema.py
```

## What's *not* hardware-verified

Everything in `base-station/sketch` was brought up and tuned against real
STM32U585 hardware (documented empirically in that codebase's comments —
e.g. the KX134 SPI pin-routing finding, the I2S DMA-width bug). This port
compiles clean (`pio run`, zero warnings under `-Wall -Wextra`) but **has
not been run against a physical XIAO ESP32S3 + KX134 + INMP441 + WS2812
ring**. Specifically flagged as assumptions, not findings, in the source
comments:

- `src/drivers/mic_i2s.cpp`: sample rate (48kHz) and the INMP441's
  32-bit-slot right-shift (`>> 8`) — reasonable per-datasheet defaults,
  not bench-confirmed.
- `src/threads/accel_sampler_task.cpp`: `ACCEL_SAMPLER_READ_CHUNK_FRAMES`
  (64) — carried over from the base station sketch's own tuned result, not
  re-derived from a characterization run on this board.
- `include/board_pins.h`: the D0–D5 assignments are conflict-free on
  paper (verified against the XIAO ESP32S3 variant's actual
  `pins_arduino.h`) but unverified against a real wiring harness.

Bring-up on real hardware should revisit these the same way
`base-station/sketch`'s own comments document its STM32 tuning history.
The telemetry wire format itself (this doc's "Wire format" section) is
verified by construction — generated from the same schema the base
station and MPU parser share — but has not yet been exercised by a real
publish against a running `mqtt_subscriber.py`; do that once hardware is
available, the same way `satellite_node_sim.py` already exercises it in
simulation.
