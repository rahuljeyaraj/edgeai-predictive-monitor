# satellite — XIAO ESP32-S3 satellite node firmware

Real ESP-IDF (not Arduino) firmware for the Seeed Studio XIAO ESP32-S3, one of
potentially several **satellite** vibration/audio sensing nodes for the
EdgeAI Predictive Monitor. It talks to `base-station/` over WiFi + MQTT rather
than a wired link, since it isn't physically attached to the UNO Q. This
replaces an earlier Arduino/PlatformIO stub that lived at this path — that
port was never run against real hardware; this firmware has been, extensively
(see [Hardware-verified status](#hardware-verified-status)).

Ported wholesale from
[`Abhinavkrishna3211/edgeai-predictive-monitor-satellite`](https://github.com/Abhinavkrishna3211/edgeai-predictive-monitor-satellite),
where it's developed and bench-tested against real XIAO ESP32-S3 + KX134 +
INMP441 + WS2812 hardware. Treat that repo as upstream for anything not
covered here.

## Repository layout

```
satellite/
├── src/
│   ├── main.c                       # app_main — FFT table init, task start (boot order in file header)
│   ├── epm_config.h                 # Compile-time tunables: FFT sizes, task stacks, GPIO pins
│   └── threads/
│       ├── mic_task.c/h             # I2S capture, windowed FFT, time-domain stats
│       ├── dsp_task.c/h             # mic spectrum compute (Welch overlap, centroid)
│       ├── imu_task.c/h             # KX134 3-axis spectrum + envelope compute
│       ├── net_task.c/h             # builds + publishes the MQTT telemetry frame
│       ├── led_task.c/h             # thin wrapper around the epm_hal display driver
│       ├── wifi_task.c/h            # WiFi STA event-driven bring-up (no task of its own)
│       └── wifi_provision_task.c/h  # captive-portal provisioning state machine
│
├── components/
│   ├── epm_codec/     # Wire-format codec (section-list telemetry frame, MQTT cmd envelope)
│   ├── epm_drivers/    # link_mqtt.c, mic_inmp441_i2s.c, accel_kx134_spi.c,
│   │                    # display_ledc.c / display_neopixel.c, captive-portal provisioning
│   ├── epm_dsp/        # FFT window, spectrum, scalar stats, envelope analysis
│   └── epm_hal/        # HAL interfaces (hal_transport, hal_display, hal_accel, hal_provisioning)
│
├── schema/
│   └── telemetry_schema.json   # Source of truth for section/channel/scalar ids — generates
│                                 # components/epm_codec/include/frame_codec/telemetry_schema.h
│
├── tests/host/          # C unit tests (DSP, codec, scalar stats) — CMake + CTest, no ESP-IDF needed
│
├── CMakeLists.txt       # Root ESP-IDF project
├── platformio.ini       # PlatformIO build + upload config
├── sdkconfig.defaults   # ESP-IDF KConfig overrides (watchdog, TCP buffers, -O2)
└── partitions_simple_8mb.csv
```

## Wire format

Telemetry (`epm/<node_id>/data`) is the same generic section-list frame
`base-station/` speaks, generated from `schema/telemetry_schema.json` (run
`python3 schema/gen_schema.py` after editing it — regenerate every generated
side, this firmware's own
`components/epm_codec/include/frame_codec/telemetry_schema.h` included, or
they drift):

```
[num_sections u8]
  repeated num_sections times:
  [source_id u8][channel_id u8][data_kind u8][section_len u16][body...]

SPECTRUM body:    [fs f32][fft_size u16][bin_count u16][bins f32...]
SCALAR_SET body:  [count u8][ids u16...][values f32...]
```

Each publish carries one `mic` spectrum section, three `accel_x/y/z` spectrum
sections, and three `accel_x/y/z_envelope` spectrum sections (amplitude-
demodulated bearing-impact spectra —
`docs/satellite/decisions/ADR-032-envelope-analysis-channels-no-reference-repo-coordination-needed.md`)
— each of those four channel groups followed by its own six-scalar
`SCALAR_SET` (`rms`/`kurtosis`/`crest_factor`/`peak`/`std`/`skewness`).
`bin_count` is a per-section header field read dynamically, not a fixed
global — this firmware currently emits 256 bins per channel
(`docs/satellite/decisions/ADR-040-wire-resolution-raised-to-256-bins.md`,
raised from 128 the same day this port was cut), and nothing on the wire
format needs to change to support that.

The command direction (`epm/<node_id>/cmd`) keeps the same lean
`[TYPE u8][PAYLOAD...]` envelope. Only `0x08 STATUS_LED` is currently
handled — payload `struct { uint32_t rgb; uint8_t mode; uint16_t period_ms; } __attribute__((packed))`
— unrecognized TYPE bytes are ignored, so `base-station/`'s other command
types are safe to send without this firmware choking on them.

## Hardware / wiring

| Signal | XIAO pin | GPIO | Notes |
|---|---|---|---|
| INMP441 SCK (BCLK) | D1 | 2 | I2S clock |
| INMP441 WS (LRCLK) | D2 | 3 | I2S word-select |
| INMP441 SD (data out) | D3 | 4 | mic → MCU |
| WS2812 ring DIN | D5 | 6 | 8-pixel ring, default display driver (`display_neopixel.c`) |
| KX134 CS | D6 | 43 | SPI chip-select, active LOW |
| KX134 INT1 | D7 | 44 | wired but not currently read by firmware |
| KX134 SPI SCLK | D8 | 7 | 10 MHz |
| KX134 SPI MISO | D9 | 8 | |
| KX134 SPI MOSI | D10 | 9 | |

GPIO43/44 double as UART0 TX/RX on this board, but that's safe since the
debug console runs over USB-JTAG (`esp-builtin`), not physical UART0 pins.
3.3V powers both peripherals — no level shifting needed. Full pin rationale:
[`docs/satellite/PIN_ALLOCATION.md`](../docs/satellite/PIN_ALLOCATION.md).

A plain 3-channel monochrome LEDC driver (`display_ledc.c`, GPIO1/5/6) exists
as a Kconfig fallback (`EPM_DISPLAY_USE_LEDC`) but is not the default build.

## Configuration

There is no compile-time credentials file to edit for normal use. On first
boot (or whenever no WiFi credentials are saved in NVS), the node brings up
its own AP, `EPM-SAT-<node_id>`, with a captive portal:

1. Connect a phone/laptop to `EPM-SAT-<node_id>`. Its WPA2 password is
   generated once on-device and printed to the serial console at first
   boot — write it down.
2. The OS should auto-open the captive-portal form; if not, browse to
   `192.168.4.1`.
3. Submit WiFi SSID and WiFi password. Credentials persist in NVS and
   survive reboots/reflashes.

`src/epm_config.h`'s `WIFI_SSID`/`WIFI_PASS` `#define`s are only a first-boot
seed for the very first join attempt before NVS holds anything — they're
overridden immediately once a credential is submitted through the portal,
and can themselves be overridden without editing tracked source by dropping
a gitignored `src/wifi_creds.h` next to them (`#if __has_include(...)`).

The portal form also has MQTT broker host/port fields, and submitted values
are persisted to NVS alongside the WiFi credentials — but nothing in the
runtime MQTT-connect path (`components/epm_drivers/link_mqtt.c`) reads them
back. The actual broker target is always the compile-time
`EPM_MQTT_BROKER_HOST`/`EPM_MQTT_BROKER_PORT` macros (default `10.42.0.1` :
`1883`), overridable only via a `platformio.ini` `build_flags` entry, e.g.
`-DEPM_MQTT_BROKER_HOST='"192.168.1.50"'`. Treat the portal's broker fields
as inert until that's wired up.

Node identity is derived automatically from the ESP32's WiFi MAC address
(last 3 octets, lowercase hex, no separators) — no per-device
flashing-time configuration, same derivation `base-station/` expects.

## Build / flash

```sh
cd satellite
pio run                                     # build
pio run --target upload -e xiao_esp32s3     # flash (USB-JTAG, upload_protocol = esp-builtin)
pio device monitor                          # serial log console (115200 baud)
```

`platformio.ini` targets `board = seeed_xiao_esp32s3` with `framework =
espidf`, 8 MB flash and the matching partition table
(`partitions_simple_8mb.csv`). ESP-IDF directly also works:
`idf.py -p <port> flash monitor`.

Host-side unit tests (codec, DSP, scalar stats — no ESP-IDF toolchain
required) live in `tests/host/`; see that folder's own README for the CMake
invocation.

## Hardware-verified status

Unlike the stub this replaces, every piece of this firmware has been run on
real XIAO ESP32-S3 + KX134 + INMP441 + WS2812 hardware, not just compiled.
Specifics live in `docs/satellite/` (curated from the upstream repo's own
`docs/performance/`), including a multi-hour continuous stability soak, mic
+ accelerometer sensor characterization against a physical shaker/speaker
rig, and a live interop session against this reference repository's own
unmodified dashboard/classifier code. One open item: the reconnect logic in
`src/threads/wifi_provision_task.c` self-heals a dropped MQTT session inside
~150s via a watchdog restart
(`docs/satellite/decisions/ADR-036-mqtt-reconnect-watchdog.md`) — real but
not instant, worth knowing about before assuming a "blue breathing" LED
means a hard failure.
