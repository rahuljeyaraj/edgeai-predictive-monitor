# MCU Software Architecture — EdgeAI Predictive Monitor (EPM)
**Team DragonWing — STM32U585 Firmware Design**

Status: Implemented through Milestone 10, with post-M10 sensor throughput
tuning complete. All sensors live; fused spectrum streaming to MPU at 64 ms
epoch rate. §8 item M8 records the tuning decisions and rationale; §4.4 is
updated with the accel-sampler priority exception that unlocked them.

---

## 1. Scope

This document describes the firmware architecture running on the STM32U585
MCU side of the Arduino UNO Q base station. It covers:

- Sensor acquisition (accelerometer, microphone)
- On-device FFT and spectrum fusion
- MCU↔MPU communication over LPUART1 (data out, commands in)
- Debug logging over USART1
- RGB LED and LED matrix status displays, driven by MPU commands

It does **not** cover ESP32 satellite node firmware, MPU-side inference,
or the MQTT wireless protocol — those are documented separately in the
`edgeai-wire-protocol` repository (Appendix A: network selection rationale,
Appendix B: wire protocol specification).

---

## 2. Hardware inventory

| Peripheral | Interface | Role |
|---|---|---|
| KX134-1211 accelerometer | SPI, FIFO burst-read | Vibration sensing |
| Microphone | I2S, DMA ping-pong | Acoustic sensing |
| RGB LED | PWM, external common-anode LED on D3/D5/D6 (TIM1/TIM3) - see §8 item M4 | Machine health display (MPU-commanded) |
| LED matrix | MCU-attached, charlieplexed via GPIOF0-10 (11 pins) + TIM17 scan timer | Status text display (MPU-commanded) |
| USART1 | UART, routed to USB-UART dongle → host PC | Debug logging only, fully decoupled from MPU |
| LPUART1 | UART, DMA-backed async | Bidirectional MCU↔MPU data + command link |

LPUART1 measured throughput ceiling: **114 KB/s** (DMA-backed). Prior
polling-based implementation was capped at ~44–46 KB/s due to
`uart_poll_out()` byte-at-a-time CPU-bound sends — this is resolved by the
DMA-backed `uart_link.c` implementation already in place.

---

## 3. Repository file structure

This is the authoritative folder/file layout for `edgeai-unoq`. Any
implementation milestone that touches MCU code must conform to this
structure — existing files that don't yet match it (e.g. `uart_link.c/h`
and `wire_protocol.c/h` currently sitting at a flat/different path) must be
**moved** into this structure as part of the milestone that touches them,
not left in place. This section exists specifically because Milestones 1
and 2 were implemented without relocating `uart_link.c/h` into `drivers/`
or `frame_codec/` — that should not happen again for later milestones.

```
edgeai-unoq/
├── hal/
│   ├── hal_accel.h              # accelerometer contract (init, start, read_fifo_block, get_sample_rate, stop)
│   ├── hal_audio.h               # microphone contract (init, start, read_block, get_sample_rate, stop)
│   ├── hal_display_rgb.h         # RGB LED contract (init, set, tick)
│   ├── hal_display_matrix.h      # LED matrix contract (init, set, tick)
│   └── hal_transport.h           # generic transport contract (init, send, recv)
│
├── drivers/
│   ├── kx134.c                   # implements hal_accel.h — SPI FIFO burst-read logic. No public .h: callers use hal_accel.h only.
│   ├── audio_i2s.c               # implements hal_audio.h — I2S DMA ping-pong logic. No public .h: callers use hal_audio.h only.
│   ├── rgb_pwm.c                 # implements hal_display_rgb.h — PWM duty cycle rendering (CONST/BREATHE/STROBE)
│   ├── led_matrix.c              # implements hal_display_matrix.h — matrix rendering (interface TBD, see open item M1)
│   └── uart_link.c               # implements hal_transport.h for LPUART1 — DMA TX/RX, async callback, ping-pong RX buffers
│   └── uart_link.h               # PRIVATE header for uart_link.c internals only — NOT the public contract (that's hal_transport.h)
│
├── frame_codec/
│   ├── wire_protocol.c            # frame encode/decode, CRC16, streaming byte-fed parser — unchanged from existing implementation
│   ├── wire_protocol.h            # frame envelope definition, wp_type enum, wp_frame/wp_parser structs
│   └── frame_types.h              # payload structs: spectrum_fused_payload_header, display_rgb_payload, display_matrix_payload (§6.2)
│
├── threads/
│   ├── accel_sampler_thread.c/h   # owns accel HAL instance, runs FFT, pushes to accel_spectrum_msgq
│   ├── mic_sampler_thread.c/h     # owns mic HAL instance, runs FFT, pushes to mic_spectrum_msgq
│   ├── fuser_thread.c/h           # epoch-timer-driven, drains both msgqs, encodes SPECTRUM frame, sends via transport
│   ├── rgb_display_thread.c/h     # periodic tick, renders current RGB command via hal_display_rgb.h
│   ├── matrix_display_thread.c/h  # periodic tick, renders current matrix command via hal_display_matrix.h
│   └── transport_thread.c/h       # owns LPUART RX dispatch: decodes frames, routes by TYPE to display threads
│
├── app_config.h                   # epoch period (default 250ms, configurable), thread priorities/stack sizes, msgq depths, per-sensor FFT bin counts (§5.2.1)
└── main.c                         # thread creation + HAL instance wiring only — no business logic here
```

### 3.1 Rules this structure enforces

- **`drivers/` files have no public headers**, with the sole exception of
  `uart_link.h`, which is a **private** header for `uart_link.c`'s own
  internals (DMA buffer sizes, static parser state, etc.) — it is not the
  contract other code calls against. Any thread that needs to send/receive
  over LPUART1 includes `hal_transport.h`, never `uart_link.h` directly.
  This is the specific gap that caused the Milestone 1/2 issue: code was
  written against `uart_link.h` directly instead of being routed through
  `hal_transport.h`, so the existing files were never relocated or
  wrapped.
- **`wire_protocol.c/h` is a codec, not a HAL** — it has no driver variants
  and lives in `frame_codec/`, not `drivers/`. It is shared by every thread
  that encodes or decodes a frame (Fuser, transport_thread), and is also
  intended to be reusable as the spec reference for the MPU-side backend.
- **`frame_types.h` is new** relative to the existing code — it didn't
  exist before this design and must be created during whichever milestone
  first defines a concrete payload (Milestone 2 for the envelope test
  frame, fully populated by Milestone 3 onward).
- **No `.c` file outside `drivers/` may include a chip-specific header**
  (e.g. no thread file should ever `#include` anything KX134-specific).
  Threads only include files from `hal/`, `frame_codec/`, and their own
  `threads/*.h`.

---

## 4. Thread inventory

| Thread | Responsibility | Priority |
|---|---|---|
| `accel_sampler_thread` | SPI FIFO read + FFT, pushes result to 1-deep msgq | Same as others (no special-casing — see §4.4) |
| `mic_sampler_thread` | I2S DMA read + FFT, pushes result to 1-deep msgq | Same as others |
| `fuser_thread` | Epoch-timer-driven; reads latest from both msgqs, encodes `SPECTRUM` frame, sends via transport | Same as others |
| `rgb_display_thread` | Periodic tick; renders current RGB command (CONST/BREATHE/STROBE) | Same as others |
| `matrix_display_thread` | Periodic tick; renders current matrix text command | Same as others |
| `transport_thread` | LPUART RX → parses frames → dispatches by `TYPE` | Same as others |

### 4.1 Why accel and mic are separate HALs and threads

Although both are "streaming sample sources" with superficially similar
operations (init, start, read block, get rate, stop), they are kept as
fully separate HAL interfaces and separate threads rather than unified
under one generic sampler abstraction. This was a deliberate reversal of
an earlier draft that attempted unification — the two sensors differ enough
in practice (SPI vs. I2S, FIFO burst vs. DMA ping-pong, different native
sample rates and FFT window sizes) that a shared interface would have been
a false abstraction.

### 4.2 Why RGB LED and LED matrix are separate HALs but the same kind of thread

RGB LED and LED matrix are visually similar ("status displays") but are
fundamentally different devices — different command payloads (color/mode/
period vs. text/scroll-speed) and different rendering logic. They cannot
share one HAL interface. They are kept as two separate threads (not merged
into one "display thread") specifically because the matrix's rendering
latency characteristics are not yet known (see §8, open item M1) — if
matrix rendering involves a slower bus transaction, merging the threads
risks stalling RGB's tick and introducing visible jitter in the breathing/
strobing patterns. Splitting threads costs nothing and removes this risk
entirely.

### 4.3 Sampler → Fuser handoff mechanism

Each sampler thread owns a 1-deep `k_msgq`, sized from that sensor's
configured bin count (§5.2.1):

```c
K_MSGQ_DEFINE(accel_spectrum_msgq, sizeof(float) * ACCEL_FFT_BIN_COUNT, 1, 4);
K_MSGQ_DEFINE(mic_spectrum_msgq, sizeof(float) * MIC_FFT_BIN_COUNT, 1, 4);
```

Each sampler thread pushes its latest FFT result after every completed FFT.
This implements Strategy 1 (sample-and-hold) for spectrum fusion — see §5.2.
If a sensor's bin count is configured to `0` (disabled, §5.2.1), its
sampler thread is never created and its msgq doesn't exist — the Fuser
thread (§9 row 9) only drains msgqs for currently-enabled sensors.

**Implementation note (open, see §8 item M2):** a 1-deep `k_msgq` does not
by itself guarantee "always the latest value." Zephyr's `k_msgq_put()` with
`K_NO_WAIT` on a full queue returns busy and does **not** overwrite the
existing entry. To get true latest-value semantics, the sampler thread must
either purge the queue before each put, or the implementation should use a
shared variable with an atomic/lock guard instead. This must be decided
during M9 implementation (Fuser thread), not before.

### 4.4 Thread priority policy

**`accel_sampler_thread` is an exception: it runs at priority 4.** All
other threads (mic_sampler, fuser, rgb_display, matrix_display, transport)
run at priority 5.

**Rationale.** The KX134's Buffer Full Interrupt (BFI) fires every ~6.7 ms
at 12800 Hz ODR with an 86-frame FIFO. When `accel_sampler_thread` shared
priority 5 with `mic_sampler_thread` and `fuser_thread`, Zephyr's default
10 ms round-robin time slice meant accel_sampler could wait up to 20 ms
after a BFI fired before actually running — longer than the 6.7 ms BFI
period itself. This kept the drained frame rate at ~4600 frames/s (~36%
of ODR) and caused `accel_stale` events once `ACCEL_FFT_BIN_COUNT` exceeded
128 bins (each window requiring 256 frames at a drain rate barely above
the window-production demand).

Raising `accel_sampler_thread` to priority 4 causes it to preempt
mic_sampler and fuser the moment the BFI ISR gives the semaphore. Measured
result after the change: ISR response rate 86/s → 337/s, drained frame rate
4600/s → 18300/s, `accel_stale` dropped to 0 at all tested bin counts
(128, 256, 512). The mic's I2S data collection is DMA-driven (CPU not
involved during the ~21 ms DMA transfer window), so preempting
`mic_sampler_thread` during its semaphore-blocked wait has no measurable
effect on audio capture.

Display and transport threads remain at priority 5: they are periodic
renders, not interrupt-latency-sensitive paths, and the original uniformity
argument holds for that group.

(Milestone 5's mic backpressure issue, §8 item M5, looked at first like a
case for revisiting priority — it wasn't; the actual cause was a
`SYS_FOREVER_MS` timeout-handling bug, unrelated to thread priority.)

---

## 5. Data flow

### 5.1 Sensor → MPU (outbound)

```
KX134 (SPI)          Mic (I2S)
    |                     |
    v                     v
accel_sampler_thread  mic_sampler_thread
 (FFT, configurable     (FFT, configurable
  bins — §5.2.1)         bins — §5.2.1)
    |                     |
    v                     v
accel_spectrum_msgq   mic_spectrum_msgq
    |                     |
    +----------+----------+
               v
         fuser_thread
       (64ms epoch tick)
               |
               v
   spectrum_fused_payload_header
   (mic_bins[] then accel_bins[] raw
    concatenation — size varies with
    config, §5.2.1)
               |
               v
        wp_encode() → LPUART1 TX (DMA)
               |
               v
              MPU
```

### 5.2 Spectrum fusion strategy

**Strategy chosen: Fixed epoch, sample-and-hold (latest-available value).**

- Epoch period: **64 ms, configurable** (set in `app_config.h`;
  stepped 250 ms → 100 ms → 64 ms. At 250 ms, ~67% of accel windows
  and ~75% of mic windows were being dropped by purge-before-put before
  Fuser drained them. 64 ms matches the mic's ~62.5 ms natural window
  cadence at 96 kHz / 2048-sample blocks. See
  `docs/Sensor_Throughput_Tuning_Plan.md` and §8 item M8).
- At each epoch tick, the Fuser reads whatever is currently the latest
  completed FFT result from each sensor's msgq — no blocking, no waiting
  for both to be simultaneously fresh.
- No epoch ID or per-sensor age/timestamp metadata is included in the
  payload (deliberately simplified — see decision log §6).
- Rationale: failure signatures (bearing wear, cavitation, micro-pitting)
  develop over seconds-to-minutes, not milliseconds. A few hundred ms of
  skew between the accel and mic windows is not expected to matter for
  the autoencoder's input. If this assumption proves wrong once validated
  against real sensor data, per-sensor age metadata can be added without
  breaking the frame envelope (just a payload schema change).
- Two alternative strategies (epoch + block-until-both-ready;
  independent streams with shared epoch_id, no fusion in firmware) were
  considered and rejected — the former couples sensor thread timing
  together undesirably, the latter defers a decision that's cheap to make
  now.

### 5.2.1 Per-sensor FFT enable/bin-count configuration

Each FFT (accel, mic) is independently controlled by a single compile-time
integer, conventionally named `<SENSOR>_FFT_BIN_COUNT` (e.g.
`MIC_FFT_BIN_COUNT`, `ACCEL_FFT_BIN_COUNT`):

- For the **accelerometer**, this value is **both** the FFT window
  length (raw samples per FFT call) and the number of unique magnitude
  bins produced and sent (`ACCEL_FFT_LEN = ACCEL_FFT_BIN_COUNT × 2`).
  For the **microphone**, these diverge: `MIC_FFT_BIN_COUNT` (512)
  controls bins *transmitted*, while the actual FFT input length is
  `MIC_FFT_BIN_COUNT × 4 = 2048` samples (`AUDIO_BLOCK_SAMPLES = 2048`).
  The 2048-pt RFFT produces 1024 unique bins; only bins 1–512
  (46.875 Hz–24 kHz) are transmitted — the upper 512 (24–48 kHz) are
  image content from the INMP441's inherent FS/4 = 24 kHz mirror (see
  §8 item M5, sample-rate history) and carry no new information. Both
  sensors use CMSIS-DSP's `arm_rfft_fast_f32`, which requires
  power-of-two FFT sizes (32–4096).
- **`0` disables that sensor entirely.** Its sampler thread is never
  started, its HAL is never initialized, and the physical sensor is
  never powered/clocked up — not merely "the FFT step is skipped while
  still sampling." This is checked at the top of each sampler thread's
  `*_thread_start()` (e.g. `mic_sampler_thread_start()`); the rest of
  that module is unaffected.
- Disabling both sensors at once is not a supported configuration.
- Reason this exists: each sensor (and the Fuser's concatenation of both)
  needs to be validated independently against the AI model while tuning
  bin counts — e.g. run accel alone at 512 bins, then mic alone at 64
  bins, then both together — without rewiring hardware or touching any
  other module beyond the one constant per sensor.
- Until the Fuser thread (§9 row 9, Milestone 9) exists, each sampler's
  bin-count constant lives locally in its own thread `.c` file (matching
  how other thread-local constants like `MIC_SAMPLER_BLOCK_SAMPLES`
  already work) — there's no cross-thread consumer yet to justify
  centralizing it. It moves into `app_config.h` once the Fuser needs to
  read the same value the sampler used to size/drain the msgq (§4.3).
- The payload (§6.2) is self-describing — it carries each sensor's actual
  bin count inline — so the MPU never needs build-time knowledge of the
  MCU's configured bin counts or which sensors are enabled; it decodes
  whatever counts arrive in a given frame.

### 5.3 MPU → MCU (commands, inbound)

```
              MPU
               |
               v
        LPUART1 RX (DMA, async callback)
               |
               v
         wp_parser_feed() (streaming, CRC-validated)
               |
               v
           wp_rx_msgq (depth 2)
               |
               v
      transport_thread dispatch by TYPE
               |
       +-------+-------+
       v               v
DISPLAY_RGB      DISPLAY_MATRIX
   command           command
       |               |
       v               v
rgb_display_thread  matrix_display_thread
  (stores cmd,        (stores cmd,
   tick() renders)      tick() renders)
```

Unrecognized `TYPE` values: logged as a warning (`LOG_WRN`), no other
action taken. The CRC-validated parser already silently drops malformed/
corrupt frames at the byte level; this warning path is specifically for
**valid, CRC-correct frames carrying a TYPE the dispatcher doesn't
recognize**.

---

## 6. Wire protocol

Frame envelope (already implemented in `wire_protocol.c/h`, unchanged by
this design):

```
[SYNC: 0xAA55][VER: 1B][TYPE: 1B][NODE_ID: 1B][LEN: 2B][PAYLOAD: N][CRC16: 2B]
```

- `WP_VERSION` remains `1`. Not bumped despite payload schema changes
  below, since the project is still in design phase and nothing is
  deployed yet. Appendix B is to be updated in place to match.
- `NODE_ID` fixed at `0x00` for the STM32 (single point-to-point link,
  per existing decision).
- CRC16/CCITT-FALSE (poly 0x1021, init 0xFFFF), computed over VER..PAYLOAD
  inclusive — unchanged.

### 6.1 Active message types

Earlier draft included `HEALTH_ALERT`, `HEARTBEAT`, `COMMISSION_START`,
`COMMISSION_DONE`, `CONFIG_SET`, and `ACK`. These are **removed for now** —
there is currently no config to set, nothing to acknowledge, no commissioning
flow defined, and heartbeat is not a current priority. They can be
reintroduced later without breaking the envelope.

```c
enum wp_type {
    WP_TYPE_SPECTRUM        = 0x01,  /* MCU → MPU, payload: spectrum_fused_payload_header */
    WP_TYPE_DISPLAY_RGB     = 0x02,  /* MPU → MCU, payload: display_rgb_payload */
    WP_TYPE_DISPLAY_MATRIX  = 0x03,  /* MPU → MCU, payload: display_matrix_payload */
};
```

### 6.2 Payload structs

```c
/* frame_codec/frame_types.h */

/* WP_TYPE_SPECTRUM — MCU → MPU, sent once per epoch (250ms default).
 * Header only: mic_bins[mic_bin_count] floats then
 * accel_bins[accel_bin_count] floats follow immediately after this
 * header in the actual wire bytes (not part of the struct itself — two
 * flexible array members in one struct isn't valid C). Either count may
 * be 0 if that sensor's FFT is disabled (§5.2.1) — never both. This
 * makes the payload self-describing, so the MPU decodes whatever counts
 * actually arrive without needing build-time knowledge of the MCU's
 * configured bin counts. mic_fs/accel_fs (sample rate) and
 * mic_fft_size/accel_fft_size (FFT length) are also on the wire per
 * frame for the same reason — the receiver no longer hardcodes either
 * separately (this is also the exact payload shape now reused for the
 * MQTT satellite link's SPECTRUM message — Appendix B S3/S4). */
struct spectrum_fused_payload_header {
    float mic_fs;
    uint16_t mic_fft_size;
    uint16_t mic_bin_count;
    float accel_fs;
    uint16_t accel_fft_size;
    uint16_t accel_bin_count;
};
/* size: 16 + (mic_bin_count + accel_bin_count) * 4 bytes. Current
 * configuration (mic=512, accel=512 bins): 16 + 1024*4 = 4112 bytes —
 * well within WP_MAX_PAYLOAD (8192). */

/* WP_TYPE_DISPLAY_RGB — MPU → MCU */
struct display_rgb_payload {
    uint32_t rgb;          /* packed 0xRRGGBB */
    uint8_t  mode;          /* 0 = CONST, 1 = BREATHE, 2 = STROBE */
    uint16_t period_ms;     /* ignored by MCU when mode == CONST; MPU sends 0 by convention */
};

/* WP_TYPE_DISPLAY_MATRIX — MPU → MCU */
struct display_matrix_payload {
    char text[64];          /* truncated if MPU sends more; exact truncation/
                                null-termination behavior TBD pending matrix
                                driver investigation — see §8 item M1 */
    uint16_t scroll_speed_ms;
};
```

### 6.3 Backpressure / drop behavior

- **Outbound (Fuser → TX):** Matches existing `uart_link_send()` behavior —
  if a previous TX is still in flight when the next epoch fires, the new
  frame is dropped (`-EBUSY`), logged via `LOG_WRN`, no retry, no queue, no
  drop counter exposed to MPU. At 114 KB/s with the current configuration (512 mic + 512 accel bins;
  full frame ≈ 4110 bytes), a transmission takes roughly 36 ms — well
  under the 64 ms epoch period, so this is expected to rarely trigger in practice
  **as long as the MPU is actively reading the link** — see §8 item M3 for
  a hardware-confirmed exception when it isn't.
- **Inbound (MPU → RX → Display threads):** Uses the existing `wp_rx_msgq`
  (depth 2) — no changes needed. Display commands are consumed into
  "current command" state, not a stream that must be fully drained, so
  the existing queue depth is sufficient.

---

## 7. RGB LED behavior reference

The MCU has no knowledge of "health" semantics — it only renders generic
display primitives. The mapping below is the *intended MPU-side*
interpretation, included here only for reference:

| MPU-side meaning | Color | Mode | Period |
|---|---|---|---|
| Healthy | Green | CONST | — |
| Warning | Yellow | BREATHE | (MPU's choice) |
| Alert | Red | STROBE | (MPU's choice) |
| Training/commissioning | Blue | BREATHE | (MPU's choice) |

MCU's `rgb_display_thread` ticks periodically (e.g. every 20–50 ms,
exact rate TBD at implementation time) and renders whatever the last
received `DISPLAY_RGB` command specifies — solid color for CONST, a
sine-wave brightness ramp for BREATHE, a square-wave on/off for STROBE.

---

## 8. Open items (not blocking implementation start)

These are tracked explicitly so they are not silently forgotten, but none
of them block starting Milestone 1.

- **M1 — RESOLVED in Milestone 4.** The onboard 8x13 LED matrix is
  charlieplexed via `GPIOF0-10`, confirmed both from Arduino's own
  open-source `ArduinoCore-zephyr` firmware (`loader/matrix.inc`,
  Apache-2.0) - which `drivers/led_matrix.c` directly ports the
  pixel-to-pin table and scan-ISR approach from, since the precompiled
  `matrixWrite()` symbols that firmware exports aren't reachable from our
  pure-Zephyr build - and the official schematic (`ABX00162-schematics.pdf`,
  sheet "LED Matrix"). `display_matrix_payload.text` truncation/
  null-termination: not required to be null-terminated by the sender; if
  no NUL appears within the 64 bytes, all 64 are treated as significant.
  `scroll_speed_ms == 0` means static/no scroll.
- **M2 — `k_msgq` latest-value semantics.** A 1-deep `k_msgq` does not
  automatically overwrite on full; the Fuser handoff mechanism (§4.3)
  needs either an explicit purge-before-put, or a switch to a shared
  variable + atomic guard. Decide during Milestone 9.
- **Sampler thread structure.** Whether `accel_sampler_thread` and
  `mic_sampler_thread` share a common generic thread function
  (parameterized per sensor) or are independently written is left to
  implementation time — both are valid given the separate-HAL decision in
  §4.1, this is just a code-organization choice, not an architectural one.
- **M3 — TX stalls indefinitely (not just one dropped frame) when the MPU
  isn't actively reading.** Confirmed on real hardware during the
  Milestone 1/2 hardware verification pass: if nothing has `/dev/ttyHS1`
  open on the MPU side, the in-flight `uart_tx()` call never completes —
  it blocks on hardware CTS indefinitely (`uart_tx()` is called with
  `SYS_FOREVER_US`, no timeout), so `tx_busy` (§6.3) never clears and
  *every* subsequent `transport_send()` drops with `-EBUSY`, not just the
  one frame that collided with the in-flight transfer. Reproduced
  repeatedly (fresh MCU reset, MPU-side listener closing mid-session) with
  the same result each time. The link self-resynchronizes and becomes
  fully reliable again as soon as the MPU reopens the port — no further
  drops were observed in any post-recovery run. Net effect: §6.3's
  "expected to rarely trigger in practice" assumption only holds while the
  MPU is actively reading; if the MPU ever isn't (reboot, crash, app
  restart), the MCU goes silent indefinitely rather than dropping one
  frame and recovering on the next epoch. Corner case, deliberately
  deferred — revisit when `fuser_thread` (Milestone 9/10) starts sending
  unattended on a fixed epoch regardless of whether the MPU is listening.
  Likely fix shape: a bounded timeout on `uart_tx()` (or an explicit
  `uart_tx_abort()` after a timeout) so `tx_busy` always clears instead of
  latching forever.
- **M4 — RESOLVED.** Milestone 3 was redone against an external
  common-anode RGB LED (common anode -> 3.3V, each cathode -> 100ohm
  resistor -> an Arduino digital pin), driven by real PWM: red on D6
  (PB1/TIM3_CH4), green on D5 (PA11/TIM1_CH4), blue on D3 (PB0/TIM3_CH3),
  via Zephyr's `pwm-leds` binding (`led_pwm.c` driver). `hal_display_rgb.h`'s
  contract was unaffected by the swap, only `drivers/rgb_pwm.c`'s internals
  changed, as anticipated. `CONST` is now a solid color at full brightness,
  `BREATHE` is an actual sine-wave brightness ramp, and `STROBE` is a hard
  on/off square wave - matching §7's original intent exactly. Note: D2 and
  D4 are not usable for this - D4 (PA12) has no timer/PWM alternate
  function on this chip at all (confirmed in the pinctrl table; its only
  alternate functions are analog, FDCAN1_TX, OctoSPI, SPI1_MOSI,
  USART1_DE/RTS, USB_OTG_FS_DP), and D2 is not silkscreened as PWM-capable
  on this board.
- **M5 — RESOLVED.** The INMP441 I2S microphone is wired to **SAI1_A**:
  SCK on the dedicated SCL pin next to AREF (PB10), FS/WS on D10 (PB9),
  SD on A4 (PC1). No MCLK pin - the INMP441 derives its own timing from
  SCK/WS alone. The mic's L/R pin is tied to GND on the breakout itself
  (selects the left I2S slot, fixed in hardware - `drivers/audio_i2s.c`
  only reads that slot).

  This pin set was not a preference - it's the *only* one available.
  Each SAI signal (SCK/FS/SD/MCLK) is fixed by the STM32U585's silicon
  pin multiplexer to a small, specific set of physical pins; devicetree
  `pinctrl` selects among those, it can't invent new ones. Tracing both
  SAI sub-blocks against what's actually wired out to the Arduino header
  (confirmed via `modules/hal/stm32/dts/st/u5/stm32u585aiixq-pinctrl.dtsi`):
  - **SAI1_A FS** can only be PA9, PB9, or PE4. PA9/PE4 aren't on the
    header at all; PB9 (D10) is the only one that is. (D8/PB4 and D9/PB8,
    which might look like "free" alternatives, aren't candidates for FS
    at all - they're SAI1_B's and SAI1_A's *MCLK* alternate-function pins
    respectively, a different signal we don't even need.)
  - **SAI1_A SCK** can only be PA8, PB10, or PE5. PA8/PE5 aren't on the
    header; PB10 is, but only via the dedicated Arduino I2C2/SCL pin
    (next to AREF, not part of the D0-D13/A0-A5 rows) - unused anywhere
    in this project, since the accelerometer (Milestone 7) is SPI, not
    I2C, so disabling I2C2 in `boards/arduino_uno_q.overlay` to free this
    pin costs nothing.
  - **SAI1_A SD** can only be PA10, PC1, PC3, PD6, or PE6. PC1 (A4) is
    the only header-exposed one - no conflict, free either way.
  - **SAI1_B was ruled out entirely**: its only SD candidates
    (PA13/PB5/PE3/PE7/PF6) are either absent from the header, or (PB5)
    already hard-wired to SPI3, this board's internal STM32<->QRB2210
    debug link - not available regardless of anything else.

  D10/PB9 was, before this, SPI2's hardware NSS (chip-select) pin
  (`arduino_spi: &spi2 {};`, per `arduino_uno_q-common.dtsi`). Giving it
  up does **not** block Milestone 7: a SPI chip-select doesn't have to be
  the dedicated hardware NSS pin, any free GPIO works via a
  software-controlled `cs-gpios` (a normal, fully-supported Zephyr SPI
  pattern). At this point in the project, SCK/MISO/MOSI were assumed
  fixed to D13/D12/D11 (PB13/PB14/PB15) - the SPI2 bus signals wired to
  the main Arduino header - and `boards/arduino_uno_q.overlay`'s `&spi2`
  override accordingly dropped only the NSS pinctrl entry, keeping those
  three. That assumption turned out to be wrong in a way that mattered -
  see M7 below, which moves SCK/MISO/MOSI to a different physical pin
  set entirely.

  Separately, getting pins right wasn't sufficient on its own: SAI1's
  `clocks` property (`zephyr/dts/arm/st/u5/stm32u5.dtsi`) selects
  `STM32_SRC_PLL2_P` as its kernel clock source, and **PLL2 is
  `status = "disabled"` by default and not enabled anywhere else in this
  project** - without an explicit `&pll2 { ... status = "okay"; };`
  override, `HAL_SAI_Init()` computes its MCKDIV bit-clock divider from a
  0Hz input, so the peripheral would never produce a working audio clock
  even with every pin wired correctly. `boards/arduino_uno_q.overlay` now
  configures PLL2 from `clk_hse` (16MHz, already enabled board-wide) with
  `div-m=1, mul-n=16, div-p=5` -> PLL2_P = 51.2MHz, chosen as an exact
  multiple (x50) of `16000 * 64` (sample rate * frame length in bits) so
  the resulting MCKDIV is exactly 50, not a rounded approximation.
  `div-r=2` is set only because the `st,stm32u5-pll-clock` binding
  requires it even though nothing downstream uses PLL2_R - its value just
  needs to keep that branch's output under the chip's 160MHz PLL output
  ceiling (same constraint `&pll1` already satisfies for the system
  clock). (`div-p` was later changed from 5 to 10 - see the sixth issue
  below, a consequence of switching `word_size` from 24 to 16.)

  A third issue, found only after extensive on-chip debugging (register
  reads, linker-map diffing, and reading thread state directly via SWD -
  not something visible from logs, since the bug's symptom *was* "no logs
  ever appear"): **SAI1_A's base devicetree node
  (`zephyr/dts/arm/st/u5/stm32u5.dtsi`) hardcodes GPDMA1 *channel 1* for
  its RX DMA** (`dmas = <&gpdma1 1 36 ...>`), which collides with
  LPUART1's own RX channel - also channel 1, from Milestone 2
  (`&lpuart1`'s `dmas` in `boards/arduino_uno_q.overlay`). This was
  completely invisible until the heap fix below: `i2s_stm32_sai.c`'s
  `i2s_stm32_sai_initialize()` calls `k_msgq_alloc_init()` before it ever
  reaches its own DMA setup, so with no heap available that allocation
  failed first and SAI1_A never actually claimed channel 1. Once the heap
  fix let that allocation succeed, SAI1_A's init proceeded to really
  configure channel 1 - and `transport_init()`'s later `dma_config()`
  call for LPUART1 (in `drivers/uart_link.c`, called from `main()`) then
  failed with `-EINVAL`, `transport_thread_start()` returned an error, and
  `main()` hit its `if (ret < 0) { ...; return 0; }` path - the main
  thread cleanly terminated (confirmed by reading `z_main_thread`'s
  `thread_state` field directly: `0x08` = `_THREAD_DEAD`, not a crash) and
  nothing further ever ran, including the one-time boot banner that
  *should* be unconditional - which is why this looked indistinguishable
  from a hardware/wiring fault for so long. Fixed by overriding `&sai1_a`'s
  `dmas` in `boards/arduino_uno_q.overlay` to use GPDMA1 *channel 2*
  instead (request/slot 36 is fixed hardware wiring for SAI1_A and can't
  move; the channel number is software-assigned and freely reassignable).
  Channel 2 is free - nothing else on this board uses a GPDMA1 channel
  beyond LPUART1's 0 (TX) and 1 (RX).

  The heap itself needed its own fix too: `k_msgq_alloc_init()` requires
  a working system heap, and this project's heap was previously unset (0
  bytes) - `prj.conf` now sets `CONFIG_HEAP_MEM_POOL_SIZE=512`, comfortably
  more than the driver's actual need (`CONFIG_I2S_STM32_SAI_BLOCK_COUNT`
  small queue entries, now 16 - see below).

  A fourth issue surfaced once samples started flowing: `mic_sampler_thread`
  hit `i2s_stm32_sai: RX invalid state: 4` (`I2S_STATE_ERROR`) far more
  often than occasional scheduling jitter alone would explain - multiple
  times per second, not the rare/occasional event expected. Root cause:
  `drivers/audio_i2s.c` originally set `i2s_config.timeout =
  SYS_FOREVER_MS`, intending to block indefinitely per block (matching
  `transport_recv()`'s `K_FOREVER` pattern elsewhere) - but
  `i2s_stm32_sai_read()` passes that value straight through to
  `k_msgq_get()` via `K_MSEC()`, and `Z_TIMEOUT_MS()`
  (`zephyr/include/zephyr/sys/clock.h`) clamps negative inputs with
  `MAX(t, 0)` - so `SYS_FOREVER_MS` (-1) silently became a *non-blocking*
  poll (`K_MSEC(0)`), not "wait forever" as the field's own doc comment in
  `zephyr/include/zephyr/drivers/i2s.h` promises. Every read returned
  immediately whether or not a block was actually ready, so ordinary
  "not ready yet" moments were misread as failures, triggering the
  DROP/START recovery cycle (and its leak, below) far more than
  necessary. Fixed by setting a concrete `AUDIO_READ_TIMEOUT_MS` (1000ms -
  generously more than the ~16ms a block should normally take to arrive)
  instead of the sentinel.

  **Remaining known limitation, deliberately not fully resolved**: even
  with the timeout fix above, `mic_sampler_thread` can still legitimately
  hit `I2S_STATE_ERROR` - traced to `HAL_SAI_RxCpltCallback()` failing to
  allocate a new mem-slab block when this thread (equal priority with
  rgb/matrix/transport_thread, §4.4) occasionally falls behind the DMA's
  ~16ms-per-block completion rate. This is now rare rather than constant
  (the timeout bug above was the dominant trigger), but not eliminated.
  Recovering from `I2S_STATE_ERROR`
  requires an explicit `DROP`/`PREPARE` trigger (the driver never clears
  it on its own) - but `i2s_stm32_sai.c`'s `queue_drop()` (run by both
  triggers) **leaks the in-flight `mem_block` instead of freeing it back
  to the slab** on some entry paths. Confirmed empirically: quadrupling
  the slab from 4 to 16 blocks (`AUDIO_SLAB_BLOCK_COUNT` in
  `drivers/audio_i2s.c`, matched by `CONFIG_I2S_STM32_SAI_BLOCK_COUNT` in
  `prj.conf`) roughly doubled time-to-first-error (~25s to ~50s) rather
  than eliminating it - the signature of a slow per-recovery leak, not a
  one-off transient. This is a real bug in the *vendored* driver
  (`zephyr/drivers/i2s/i2s_stm32_sai.c`) - not something this project
  patches, per the standing rule of never editing anything under
  `zephyr/`. `threads/mic_sampler_thread.c` instead bounds the damage:
  read every block (don't fall further behind than necessary), log stats
  only ~once/second (not every block, to reduce *how often* it falls
  behind in the first place), and cap consecutive recovery attempts at
  `MIC_SAMPLER_MAX_RECOVERY_ATTEMPTS` (5) so a string of failures gives up
  cleanly instead of leaking the slab to zero. Acceptable for this
  read-only verification milestone (tens of seconds of healthy capture is
  enough to confirm sane samples and capture a waveform); revisit if a
  future milestone needs the mic running unattended for long stretches -
  likely fix shape: either an upstream Zephyr fix to `queue_drop()`, or
  switching this driver off `i2s_stm32_sai.c` for a different I2S
  backend.

  A fifth issue: once errors stopped and stats were flowing cleanly, the
  actual sample *values* still looked wrong. `hal_audio_read_block()`
  originally assumed the INMP441's 24-bit output sat left-justified in
  the upper bits of its 32-bit slot (matching `word_size = 24`'s naming) -
  but a real waveform capture (`MIC_DUMP_ON_BOOT`,
  `host_tools/mic_dump_parse.py`) showed the meaningful signal was
  actually a 16-bit signed value in the slot's **low** 16 bits, upper 16
  always zero. Corrected with a sign-extending right-shift (via an
  `uint32_t` intermediate, to avoid the undefined behavior of left-
  shifting a signed value into its sign bit) - which fixed the
  always-positive/clustered min-max stats, but (see the sixth issue
  below) turned out to be working around a deeper bug rather than
  reflecting the hardware's true native format.

  With sane-looking stats flowing, a live waveform view (Serial Plotter,
  raw decimated samples via `MIC_PLOTTER_OUTPUT` in
  `mic_sampler_thread.c`) showed pure noise with zero correlation to
  actual sound - claps/whistles produced no visible response. Several
  plausible theories were ruled out empirically before the actual cause
  was found: swapping which slot was read (slot 0 vs slot 1) made no
  difference, ruling out a simple L/R-channel mixup; disconnecting the
  SD line dropped the signal to a clean zero (vs. noise when connected),
  ruling out floating-pin pickup - the mic genuinely was driving the
  line; and rapid `GPIOB_IDR` polling over SWD confirmed SCK/WS were
  actively toggling, ruling out a stuck/dead bit clock. None of these
  pointed at the real cause.

  The actual (sixth) issue was found by reading
  `i2s_stm32_sai_dma_init()` in `zephyr/drivers/i2s/i2s_stm32_sai.c`
  directly: under `#if defined(CONFIG_DMA_STM32U5)` (true for this
  chip), the driver **unconditionally hardcodes**
  `hdma->Init.SrcDataWidth`/`DestDataWidth` to `DMA_*_DATAWIDTH_HALFWORD`
  (16-bit) for every STM32U5/GPDMA instance, regardless of the configured
  I2S `word_size` - the `dmas` devicetree cell's own width flag (e.g.
  `STM32_DMA_16BITS`/`STM32_DMA_32BITS` in `boards/arduino_uno_q.overlay`)
  is parsed but never actually consulted for this MCU family.
  `word_size = 24` maps (via `SAI_InitI2S()`,
  `modules/hal/stm32/.../stm32u5xx_hal_sai.c`) to a 32-bit SAI slot
  (`SAI_SLOTSIZE_32B`) - a real width mismatch against the driver's
  always-16-bit DMA transfers, and the actual cause of the corrupted
  capture (consistent with the slot-swap test above finding no
  difference: both slots were equally corrupted by this same bug, not by
  a channel-numbering mixup). Fixed by setting `word_size = 16` instead,
  which maps to `SAI_SLOTSIZE_16B` - matching the driver's hardcoded
  halfword DMA width exactly. This costs the bottom 8 bits of the
  INMP441's native 24-bit precision (acceptable for this milestone), and
  makes the fifth issue's extraction trivial - a direct `int16_t` read of
  a genuinely 16-bit slot, no manual shifting needed.

  Switching `word_size` had a second-order consequence: 16-bit slots
  halve the I2S frame length (32 bits/frame instead of 64), which
  **doubles** the `MCKDIV` bit-clock-divider value `HAL_SAI_Init()` needs
  to produce the same 16kHz sample rate from the same PLL2 clock - and
  the doubled value (100) overflowed the 6-bit `MCKDIV` register field
  (max 63), silently wrapping to 36 (confirmed via a direct
  `SAI1_A->CR1` register read over SWD). The wrong bit clock decoupled
  capture timing from the real signal - observed as a much
  smaller-amplitude but still sound-unresponsive waveform. Fixed by
  halving PLL2_P to match: `&pll2`'s `div-p` in
  `boards/arduino_uno_q.overlay` changed from 5 to 10 (51.2MHz ->
  25.6MHz), restoring a clean `MCKDIV = 50` for the new 32-bit frame
  length (reconfirmed via the same register read). With both fixes in
  place, the live waveform tracked real sound (verified: silent baseline
  noise floor, immediate spike on clap/whistle, decaying back to
  baseline).

  **Sample rate escalation and the INMP441 FS/4 mirror.** With a working
  16 kHz mic, the spectrum showed a mirror image of all audio content
  folded around 4 kHz — every peak appeared twice, symmetric about 4 kHz,
  with the mirror growing proportionally until the two merged at 4 kHz and
  only noise appeared above. Root cause: the INMP441 without an external
  MCLK pin uses BCLK as its sigma-delta reference clock. Its natural
  (internal) sample rate is BCLK/64 = FS/2; the on-chip upsampler then
  doubles this to FS, which inherently folds an image at FS/4. This
  mirror **cannot be eliminated** — it is intrinsic to the INMP441's
  architecture when MCLK is absent. The only remedy is to push the mirror
  frequency higher by raising FS.

  Sample rate was stepped up in two stages:
  - **16 kHz → 48 kHz**: mirror moved from 4 kHz to 12 kHz, still within
    the audio band. PLL2: `div-m=1, mul-n=12, div-p=5, div-r=2` →
    PLL2_P = 38.4 MHz, Mckdiv = 25.
  - **48 kHz → 96 kHz**: mirror at 24 kHz — above the human hearing range
    (20 kHz), so the full 0–24 kHz band is usable. BCLK = 96000 × 32 =
    3.072 MHz, within the INMP441's 1.5–4.0 MHz spec. Going higher
    (e.g. 128 kHz) would need BCLK = 4.096 MHz, exceeding the 4.0 MHz
    limit — **96 kHz is the ceiling for this sensor without an external
    MCLK.** PLL2 final config: `div-m=1, mul-n=24, div-p=5, div-r=4` →
    VCO = 384 MHz, PLL2_P = 76.8 MHz, Mckdiv = 25, PLL2_R = 96 MHz
    (≤ 160 MHz ✓).
- **M6 — mic FFT bin count and implementation, Milestone 6.** Implemented
  using CMSIS-DSP's `arm_rfft_fast_f32` (already vendored in this
  workspace under `modules/lib/cmsis-dsp`, enabled via `CONFIG_CMSIS_DSP`/
  `CONFIG_CMSIS_DSP_TRANSFORM`/`CONFIG_CMSIS_DSP_COMPLEXMATH` in
  `prj.conf`) rather than a generic O(N²) DFT — the STM32U585 has no
  dedicated FFT hardware peripheral, so this is the closest equivalent:
  the core's FPU driven by precomputed twiddle-factor tables, instead of
  hundreds of per-sample software `sinf`/`cosf` library calls. This
  constrains `MIC_FFT_BIN_COUNT` (§5.2.1) to one of CMSIS-DSP's supported
  power-of-two RFFT sizes (32/64/128/.../4096) — `mic_sampler_thread.c`
  checks `arm_rfft_fast_init_f32()`'s return value at startup rather than
  assuming any configured value works.

  The milestone's first pass used 12 bins (chosen only for fast
  iteration, before this CMSIS-DSP integration existed, back when
  `mic_fft_magnitude()` was still the naive DFT). Confirmed on real
  hardware with a frequency sweep that this was unusable for tracking
  tone frequency at all — not a bug in the DFT math itself (verified
  separately: the real-input magnitude mirror symmetry, `mag[k] ==
  mag[N-k]`, held exactly in every capture) but spectral leakage from a
  window far shorter than one cycle of any audible tone: 12 samples at
  16kHz is 0.75ms, so a frequency sweep just measured leakage into the
  low bins regardless of input frequency, not the tone itself.

  Raised to 64 — the first value where frequency tracking actually worked
  (a frequency sweep visibly shifted the dominant bin on hardware after
  this change). At 64 bins / 16 kHz: resolution = 250 Hz/bin, window =
  4 ms. This fit within one 256-sample DMA block, so no cross-block
  accumulation was needed at that stage.

  **Raised to 512 bins (2048-pt FFT, 96 kHz — resolved).** Following the
  mic sample rate escalation to 96 kHz (§8 item M5), `MIC_FFT_BIN_COUNT`
  was raised to 512 and `AUDIO_BLOCK_SAMPLES` to 2048, making
  `MIC_FFT_LEN = MIC_FFT_BIN_COUNT × 4 = 2048`. The 2048-sample DMA
  block is the FFT window exactly — no cross-block accumulation needed;
  a `BUILD_ASSERT(MIC_FFT_LEN == AUDIO_BLOCK_SAMPLES, ...)` in
  `mic_sampler_thread.c` enforces this. The 2048-pt RFFT yields 1024
  unique bins at 46.875 Hz/bin (resolution doubled over a hypothetical
  1024-pt FFT at the same rate). Only the first 512 (bins 1–512,
  46.875 Hz–24 kHz) are transmitted to the Fuser — the upper 512
  (24–48 kHz) are silently dropped at the `k_msgq_put()` message-size
  boundary, since they lie above the INMP441's FS/4 = 24 kHz mirror and
  carry no new information. Wire payload size is unchanged from a
  512-bin 1024-pt FFT: 512 × 4 = 2048 bytes, but frequency resolution
  is doubled.

  **Separately, a real regression was found and fixed along the way:**
  running the FFT computation and its log/plotter output on every block
  (rather than throttled) reproduced the same already-tight I2S
  backpressure margin issue as item M5 above — the mic went unresponsive
  sooner than M5's documented ~25-50s baseline. Fixed by throttling FFT
  compute+output to a fixed cadence (`MIC_FFT_EVERY_N_BLOCKS` in
  `mic_sampler_thread.c`, currently ~20Hz) instead of every block; a 60s+
  sustained hardware capture confirmed no further regression at that
  cadence. Also raised USART1's console baud rate from the 115200 board
  default to 921600 (`&usart1`'s `current-speed` in
  `boards/arduino_uno_q.overlay`) once the added FFT log/plotter traffic
  started visibly lagging the console at the old rate.
- **M7 — RESOLVED. KX134 SPI2 bring-up: WHO_AM_I never matched on
  D13/D12/D11, fixed by moving SCK/MISO/MOSI to the ICSP connector's
  pins, Milestones 7/8.** First hardware bring-up of `drivers/kx134.c`
  read `WHO_AM_I` (expected `0x46`) and got `0x02` - not a transceive
  error, a wrong-but-consistent value. The chip and its wiring were
  independently confirmed good beforehand (the same KX134 breakout and
  wiring scheme was tested working over both I2C and SPI on a separate
  ESP32S3 board), so the investigation focused entirely on the
  STM32/Zephyr SPI2 side, not on re-litigating the hardware connection.

  A wide range of STM32 SPI-peripheral-configuration hypotheses were
  tested and individually ruled out, each giving either an identical
  wrong result or no change at all:
  - **SPI mode (CPOL/CPHA), all 4 combinations** - each gave a
    different, but still wrong, value; Mode 0 (the datasheet-inferred
    correct mode, per this file's `drivers/kx134.c` header comment) was
    no exception. (The first sweep attempt actually tested Mode 0 four
    times over, not all 4 modes - a reused loop-local `struct spi_config`
    very likely got the same stack address every iteration, and
    `spi_context_configured()` in `zephyr/drivers/spi/spi_context.h`
    skips reapplying CPOL/CPHA whenever the *same config pointer* is
    passed twice in a row - a pointer-identity check, not a value
    comparison. Fixed in the test harness with a `static struct
    spi_config cfgs[4]` array of distinct addresses before drawing any
    conclusion from the real 4-mode sweep above.)
  - **Clock speed**, 625kHz-4MHz (a 6.4x range) at the one mode whose
    wrong output most resembled a clean bit-level pattern - bit-identical
    results at every speed, ruling out a setup/hold timing-margin issue
    (a real margin problem would show some variation across that range).
  - **Chip-select mechanism** - GPIO-controlled `cs-gpios` vs. SPI2's
    hardware NSS - bit-identical results either way.
  - **SPI buffer structuring** - a split multi-entry `spi_buf_set`
    (separate command/dummy bufs) vs. one combined contiguous buffer -
    bit-identical, ruling out an API-usage-level explanation.
  - **Pin bias** - the SoC's default pinctrl sets `bias-pull-down` on
    SCK/MISO/MOSI (`modules/hal/stm32/dts/st/u5/
    stm32u585aiixq-pinctrl.dtsi`); overriding to `bias-disable` made no
    difference to the main symptom.
  - **SCK slew rate** - the default pinctrl also sets `slew-rate =
    "very-high-speed"` on SCK; overriding to `"low-speed"` made no
    difference.
  - **MSSI/MIDI idle timing** - the STM32H7-style SPI peripheral's
    Master-SS-Idleness/Master-Inter-Data-Idleness registers
    (`mssi-clock`/`midi-clock`, `st,stm32h7-spi.yaml`, default 0) and the
    separate, generic `spi-cs-setup-delay-ns` (`spi-device.yaml`,
    `spi_context_cs_control()`'s software-side `k_busy_wait()` between
    asserting CS and the rest of the transceive sequence, also distinct
    from the peripheral-internal MSSI register) were both maxed out -
    neither changed the result.
  - **DSIZE (8-bit data width) and FIFO mode** - confirmed by reading
    `zephyr/drivers/spi/spi_ll_stm32.c` directly: DSIZE was correctly set
    to 8-bit for this config, and the non-FIFO polling code path was in
    use (no `fifo-enable` devicetree property set anywhere for this
    instance) - neither was the cause.

  Two further tests isolated the problem precisely, rather than just
  ruling things out one at a time:
  - **A MOSI/MISO loopback test** (breakout disconnected, D11 jumpered
    directly to D12 on the MCU header) sent known byte patterns through
    the SPI2 peripheral itself, in all 4 modes - every byte came back
    identical. This proved the peripheral's own transmit/receive shifting
    logic is correct, independent of the KX134 entirely.
  - **A software bit-banged read** (SCK/MOSI/MISO/CS forced into plain
    GPIO mode via `gpio_pin_configure()` - which sets `MODER` to
    input/output, overriding whatever alternate-function `AFR` pinctrl
    had selected - then hand-clocked with explicit multi-microsecond
    delays, far slower than the hardware peripheral's 625kHz floor) read
    `WHO_AM_I` correctly as `0x46` on the very first try, over the *same*
    D13/D12/D11/D9 wires the hardware-peripheral path was failing on.

  Together, these two results pin the problem down precisely: the wires,
  the chip, and the SPI2 peripheral's own logic are each individually
  correct - only "the hardware SPI peripheral specifically driving
  PB13/PB14/PB15" produces wrong data. The fix follows directly from a
  detail in `arduino_uno_q-common.dtsi`: SPI2 has a second, documented
  pin set - `<&spi2_sck_pd1 &spi2_miso_pc2 &spi2_mosi_pc3>`, labeled "for
  the ICSP connector" - wired to the same SPI2 peripheral on different
  physical STM32 pins than the main D13/D12/D11 Arduino header. Moving
  `boards/arduino_uno_q.overlay`'s `&spi2` `pinctrl-0` to this pin set
  (same peripheral, same Mode 0, same 4MHz, same GPIO CS on D9/PB8 -
  nothing else changed) fixed `WHO_AM_I` outright: it now reads `0x46` in
  Mode 0 and Mode 3 (a known compatibility overlap some SPI slaves have
  between these two modes) and a consistent-but-wrong value in Mode 1/2 -
  exactly the textbook signature of a healthy link, in sharp contrast to
  *every* mode being wrong on D13/D12/D11.

  **Deliberately left unresolved:** the exact electrical/silicon reason
  the hardware SPI peripheral misbehaves specifically on PB13/14/15, but
  not when those same pins are bit-banged via plain GPIO, was not
  identified beyond this isolation - doing so would need a logic analyzer
  or oscilloscope capture comparing the two cases, neither available
  during this bring-up. The practical fix (use the ICSP connector's pin
  set for SPI2 on this board) stands regardless of that residual
  question. All temporary diagnostic code from this investigation
  (`kx134_debug_*` functions, the SPI mode sweep, the loopback and
  bit-bang tests) was removed from `drivers/kx134.c` once the fix was
  confirmed - none of it shipped.

- **M8 — Post-M10 sensor throughput tuning (RESOLVED).** After initial
  Milestone 10 bring-up at 250 ms epoch / 64 accel bins, the following
  changes were made and hardware-verified in sequence (tracked in
  `docs/Sensor_Throughput_Tuning_Plan.md`):

  **FUSER_EPOCH_MS 250 → 100 → 64 ms** (in `app_config.h`). At 250 ms,
  67% of accel windows and 75% of mic windows were purged by
  purge-before-put before Fuser drained them. 64 ms matches the mic's
  natural ~62.5 ms window cadence (2048 samples at 96 kHz).

  **KX134 ODR escalated to 25600 Hz then settled at 12800 Hz** (in
  `drivers/kx134.c`, `KX134_ODR_HZ` / `KX134_ODCNTL_OSA_ACTIVE`).
  25600 Hz was tested during the bin-count escalation to increase raw
  sample throughput. Final ODR is 12800 Hz: at 512 bins (1024-pt FFT),
  this gives **25 Hz/bin** over 0–6400 Hz — sufficient for all target
  fault frequencies (shaft imbalance, bearing defects, gear mesh all
  below 5 kHz at typical industrial RPMs). 25600 Hz at 512 bins would
  give 50 Hz/bin and 0–12800 Hz; the additional bandwidth is unused by
  the INMP441 (its response rolls off above ~10–15 kHz) and halving ODR
  doubles frequency resolution for the same bin count.

  **ACCEL_FFT_BIN_COUNT 64 → 128 → 256 → 512** (in `app_config.h`).
  64 bins at baseline gave 200 Hz/bin — too coarse to distinguish nearby
  fault-frequency harmonics. Each doubling improved resolution; 512 bins
  at 12800 Hz ODR gives the current **25 Hz/bin**. `ACCEL_SAMPLER_READ_CHUNK_FRAMES`
  (64) divides all tested FFT lengths (128, 256, 512, 1024) evenly —
  no chunk-size change was needed. `mpu/tools/spectrum_server.py`'s
  `bin_freqs()` auto-computes accel FFT length from bin count, so
  required no changes when bin count changed.

  **`accel_sampler_thread` priority 5 → 4.** The throughput shortfall
  at 512 bins (which would have caused `accel_stale` events) was traced
  to equal-priority round-robin scheduling: at priority 5, accel_sampler
  waited up to 20 ms between BFI events vs the BFI's ~6.7 ms firing
  period, keeping drained frame rate at ~4600/s. Raising to priority 4
  caused accel_sampler to preempt mic_sampler and fuser immediately on
  BFI. Measured result: drained frames 4600/s → 18300/s, `accel_stale`
  0 at all bin counts. See §4.4 for the updated priority policy and
  detailed rationale.

---

## 9. Implementation milestones

**Implementation must proceed one milestone at a time.** Each milestone
should be implemented, flashed, and independently verified before moving
to the next. Do not attempt to implement multiple milestones in a single
session/pass — this list exists specifically to prevent that.

**Every milestone must conform to the file structure in §3.** If a
milestone's starting point is existing code that lives outside that
structure (e.g. a flat `uart_link.c/h` + `wire_protocol.c/h` pair sitting
at the repo root), relocating those files into `drivers/` and
`frame_codec/` respectively — and introducing the corresponding `hal/`
contract header — is itself part of that milestone's deliverable, not a
follow-up task. Milestone 1 and Milestone 2 specifically must produce the
full `hal/`, `drivers/`, `frame_codec/`, and `threads/` skeleton (even if
most thread files are still empty stubs at that point) — later milestones
should only need to fill in files, never restructure folders.

| # | Milestone | Files created/touched | Verification method |
|---|---|---|---|
| 1 | USART1 debug logging | New: logging backend wiring in `main.c`/`app_config.h`. Establishes `hal/`, `drivers/`, `frame_codec/`, `threads/` skeleton folders even though this milestone only populates logging. | Logs visible on host PC terminal (PuTTY/minicom) via USB-UART dongle |
| 2 | LPUART1 MCU↔MPU communication (transport + frame codec) | **Relocate** existing `uart_link.c` → `drivers/uart_link.c`; existing `uart_link.h` → `drivers/uart_link.h` (private). **Relocate** existing `wire_protocol.c/h` → `frame_codec/`. **Create** `hal/hal_transport.h` (new contract) and have `drivers/uart_link.c` implement it. **Create** `threads/transport_thread.c/h`. | Send/receive a known test frame between MCU and MPU; verify CRC validates, payload round-trips correctly |
| 3 | RGB LED, driven by MPU command | **Create** `hal/hal_display_rgb.h`, `drivers/rgb_pwm.c`, `threads/rgb_display_thread.c/h`. **Create** `frame_codec/frame_types.h` with `display_rgb_payload`. | MPU sends `DISPLAY_RGB` commands (CONST/BREATHE/STROBE, varying color/period); confirm visually on hardware |
| 4 | LED matrix, driven by MPU command | **Create** `hal/hal_display_matrix.h`, `drivers/led_matrix.c`, `threads/matrix_display_thread.c/h`. Add `display_matrix_payload` to `frame_codec/frame_types.h`. | MPU sends `DISPLAY_MATRIX` commands with varying text; confirm visually on hardware |
| 5 | Microphone data read | **Create** `hal/hal_audio.h`, `drivers/audio_i2s.c`, `threads/mic_sampler_thread.c/h` (read-only stage, no FFT yet). PLL2 required for SAI1_A kernel clock; sample rate escalated 16 kHz → 48 kHz → 96 kHz to push the INMP441's inherent FS/4 mirror above the human hearing range (see §8 item M5). | Live waveform tracks real sound; clap/whistle produces immediate spike and decay to noise floor. At 96 kHz BCLK = 3.072 MHz (within INMP441's 1.5–4.0 MHz spec), mirror at 24 kHz |
| 6 | Microphone FFT | Extend `threads/mic_sampler_thread.c/h` with FFT stage via CMSIS-DSP's `arm_rfft_fast_f32` (§8 item M6). Settled at 512 bins from a 2048-pt FFT at 96 kHz: `AUDIO_BLOCK_SAMPLES=2048`, `MIC_FFT_LEN=MIC_FFT_BIN_COUNT×4=2048`, 46.875 Hz/bin, valid 0–24 kHz. Only bins 1–512 are transmitted; upper 512 (above FS/4 mirror) dropped at msgq boundary. `prj.conf` gained `CONFIG_CMSIS_DSP*` options. | Frequency sweep produces dominant bin that shifts correctly; browser spectrum shows 0–24 kHz with no mirror artifact. Confirmed on hardware at 96 kHz |
| 7 | Accelerometer data read | **Create** `hal/hal_accel.h`, `drivers/kx134.c`, `threads/accel_sampler_thread.c/h` (read-only stage, no FFT yet). SPI2's SCK/MISO/MOSI ended up on the ICSP connector's pins (PD1/PC2/PC3), not the main header's D13/D12/D11 (§8 item M7). | Read raw SPI/FIFO samples from KX134, get them off-device, plot waveform to confirm sane vibration capture |
| 8 | Accelerometer FFT | Extend `threads/accel_sampler_thread.c/h` with FFT stage; bin count set by `ACCEL_FFT_BIN_COUNT` (§5.2.1). No new files. | Compute FFT on captured accel data, set up a way to observe/validate behavior (e.g. known vibration source, expected frequency peak) |
| 9 | Fuser thread | **Create** `threads/fuser_thread.c/h`, the two sampler msgqs (sized from each sensor's bin-count constant, moved into `app_config.h` here, §5.2.1), and add the variable-length `spectrum_fused_payload_header` to `frame_codec/frame_types.h`. Resolve open item M2 (§8) here. Must skip a disabled sensor's msgq entirely rather than draining a nonexistent one. | Confirm Fuser correctly reads latest values from both enabled sampler msgqs at the configured epoch rate, with correct sample-and-hold behavior; confirm a disabled sensor is cleanly omitted from the payload |
| 10 | LPUART transmission of fused data | Wire `fuser_thread` output into `drivers/uart_link.c` via `hal_transport.h`. No new files. | Confirm `SPECTRUM` frames transmit end-to-end to MPU at the expected rate, with correct payload contents (variable size per §6.2 — accel + mic bins, whichever sensors are enabled) |

Each milestone produces a working, observable result before the next
milestone begins. Milestones 1–4 are independent of each other and of
5–10; 5→6 and 7→8 are sequential pairs (raw read before FFT); 9 depends on
6 and 8 both being verified; 10 depends on 9.
