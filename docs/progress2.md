# Progress 2 — condensed handoff + SPI transport plan

Condensed successor to [PROGRESS.md](PROGRESS.md) (which stays as the full, detailed
archive). This file is the quick-reference for the next session: a short summary of
where the port stands, how to clean up the currently-wedged board, and the concrete
plan for the next change — moving the fuser stream off the shared UART onto the
dedicated MCU↔MPU **SPI** link.

---

## 1. Where the port stands (summary of PROGRESS.md)

Porting `edgeai-predictive-monitor-unoq` (raw Zephyr/west) onto the **Arduino UNO Q**
App Lab structure (`app.yaml` + `python/` + `sketch/`). Board = QRB2210 **MPU** (Linux)
+ STM32U585 **MCU** (Arduino `arduino:zephyr` sketch). MCU side first.

All six MCU interfaces are ported and were verified on hardware (see PROGRESS.md for
the full per-interface rationale):

| Module (`base-station/sketch/`) | What | How (differs from old repo) |
|---|---|---|
| `matrix_display.{h,cpp}` | LED matrix | `Arduino_LED_Matrix` `loadPixels()`, priority-3 tick thread |
| `rgb_display.{h,cpp}` | WS2812 8-LED ring (D4/PA12) | direct-register bit-bang via `LL_GPIO_*` (no `led_strip` devicetree) |
| `accel_sampler.{h,cpp}` | KX134 accel | bundled `SPI`=spi2, INT1=D9 `attachInterrupt()`, hand radix-2 FFT (1024) |
| `mic_sampler.{h,cpp}` | INMP441 I2S mic | SAI1_A via LL/registers, **GPDMA1** capture, hand FFT (2048) |
| `fuser.{h,cpp}` | sensor fusion / transport | pushes full 512+512 **float32** spectrum, chunked `Bridge.notify` |
| `bench.{h,cpp}` | pipeline stats | `get_bench_stats` String; per-stage `*_get_stats()` accessors |

**Bridge / RPC link mechanics** (the important, non-obvious bits):
- MCU↔MPU control link = **UART**: STM32 `lpuart1` ↔ Linux `/dev/ttyHS1`, owned by the
  `arduino-router` systemd service. Baud raised to **1000000** via
  `base-station/provision-baud.sh` (a one-time, sudo, out-of-app provisioning step;
  MCU side is `Bridge.begin(BRIDGE_BAUD)`, `app_config.h`). Both ends must match.
- **RPClite 256-byte message ceiling** → the fuser frame (4112 B) is split into 200-B
  chunks, each sent fire-and-forget as `Bridge.notify("spec_chunk", <msgpack bin>)`.
- Config/tunables consolidated in **`app_config.h`** (bin counts, thread priorities,
  tick/epoch periods, `BRIDGE_BAUD`). `bridge_config.h` was deleted/folded into it.
- **Gotchas to remember:** integer RPC params are broken on this RPClite build → pass
  every arg as `String` + `.toInt()`. Thread priorities have caused the two worst bugs
  (mic busy-poll starving `setup()`; fuser tied with Bridge's update thread) — fuser is
  priority **6** (one below Bridge's update thread, 5), samplers/displays priority 3,
  mic 7. `setup()` order: matrix→rgb→accel→mic→bench→**fuser last** (all providers
  register before the stream starts).

**Deploy/verify:** `base-station/deploy.sh` (push+build+flash+restart, ~5 min).
Query from host: `adb shell "docker exec edgeai-predictive-monitor-base-station-main-1
python3 -c \"from arduino.app_utils import Bridge; print(Bridge.call('get_mic_info'))\""`.
Test scripts under `base-station/tests/` (`fuser_test.py` reassembles the stream — it
will eventually move into `python/main.py`). Board sudo password: `help100S`
(the `arduino` user has full sudo).

---

## 2. THE OPEN PROBLEM — UART wedge (root-caused this session)

**The continuous ~15.8 fps fuser notify stream recurringly wedges the whole Bridge
link.** Symptom: after minutes of streaming, every `Bridge.call()` times out and the
router logs floods of `invalid packet, expected array, got: int8`.

Root cause (proven this session by isolating the stream — with `fuser_start()`
commented out the link was rock-solid for the full test, all providers responsive):
- It's a **msgpack framing desync** on the serial stream. One dropped/corrupt byte on a
  continuous, unframed msgpack stream permanently delaminates the router's decoder, and
  msgpack has **no resync marker**, so the link never recovers — RPC and notify both die.
- Onset scales with throughput (≈constant bytes before failure): 15.8 fps wedged in
  ~5 min, 10 fps in ~15 min. **Lowering the frame rate only extends time-to-wedge, it
  does not fix it** — confirmed on hardware (10 fps hard-wedged at round 8 of a 36-round
  monitor and never recovered). Not baud/RX-margin (reproduced at 1M and 2M).

**Recovery procedure the previous session was missing** (this is the "how to clean up"):
the fix is to reset the router's decoder **and** re-register the MCU against it, in order:
```
# 1. restart the router (fresh serial fd + fresh msgpack decoder)
adb shell "echo 'help100S' | sudo -S -p '' systemctl restart arduino-router"
# 2. THEN reset the MCU so it re-runs setup() and re-registers its providers
#    against the fresh router (app start reflashes+resets the MCU):
adb shell "arduino-app-cli app stop  /home/arduino/ArduinoApps/edgeai-predictive-monitor-base-station"
adb shell "arduino-app-cli app start /home/arduino/ArduinoApps/edgeai-predictive-monitor-base-station"
```
A bare `sudo reboot` does **not** reliably clear it (it races the router and MCU bring-up);
`adb reboot` is a documented no-op on this board. Order matters: router first, MCU second.

**Current board state:** left **wedged** (the 10 fps durability test wedged it). Run the
recovery above to get a responsive link. Note it will re-wedge under streaming — that is
exactly what the SPI change below fixes for good.

---

## 3. THE NEXT CHANGE — move the fuser stream onto the dedicated MCU↔MPU SPI

The UNO Q has a **dedicated SPI bus wired directly between MCU and MPU, separate from the
Bridge UART, currently dormant.** Moving the bulk fuser stream there takes ~65 KB/s off
the UART (leaving it for RPC only) and — crucially — **removes the failure mode**: SPI
transfers are **CS/NSS-delimited**, so a bit error costs one frame and the next chip-select
self-realigns. No msgpack-over-lossy-UART desync. Verified feasibility (this session):

| | MPU (QRB2210) — SPI **master** | MCU (STM32U585) — SPI **slave** |
|---|---|---|
| DT node | `spi@4a94000`→`mcu@0`, `compatible="arduino,unoq-mcu"` | `&spi3`, `compatible="zephyr,spi-slave"` |
| Exposed | `/dev/spidev0.0` (driver = plain `spidev`, **no** custom protocol) | `spi3` (`deferred-init`, idle) |
| Pins | GENI QUP SPI | SCK=PG9, MISO=PG10, MOSI=PB5, NSS=PG12 |
| Ready line | (investigate — see below) | **PG13 = "Internal SPI RDY"** (`control-gpios`, `zephyr,user`) |

Facts confirmed: `CONFIG_SPI_SLAVE=y` is in the firmware; STM32 **LL SPI headers ship in
llext-edk** (`stm32u5xx_ll_spi.h`) so register-level SPI is available to a sketch (same as
the SAI/GPDMA work); spi3 pins don't collide with our sensors; bandwidth is a non-issue
(GENI runs tens of MHz — the 4112 B frame is ~4 ms even at 8 MHz). `CONFIG_SPI_STM32_DMA`
is **not** set, so the Zephyr STM32 SPI driver path is interrupt-driven.

### Design decisions (from the user, 2026-07-14)

1. **Keep full float32 — no prescale / int16.** The int16/float16 discussion only existed
   to shrink bytes for the saturated UART; SPI has ample bandwidth, so we keep lossless
   float32 (no quantization noise into the autoencoder). Revisit only for very high rates.
2. **SPI must be DMA-driven on the MCU.** Do **not** send message-at-a-time / CPU-active.
   Stage each frame via GPDMA into the SPI3-slave TX path (established pattern: mic GPDMA).
3. **No master polling as the primary model** — it's the last resort. Prefer **signalling**
   so the MPU only clocks when a frame is ready, for least CPU on both sides:
   - First choice: MCU raises the **PG13 "SPI RDY"** line → MPU takes a **GPIO interrupt**
     → MPU does a DMA `spidev` read. **TODO: find whether PG13 is exposed to the MPU as a
     GPIO/IRQ line** (no named `mcu/rdy` gpioline surfaced in `gpioinfo` this session — dig
     into the QRB2210 side / the `arduino,unoq-mcu` binding / schematic).
   - Acceptable alternative: an **RPC message** over the (now-quiet) UART to trigger the
     master read. Architecture must stay sound / low-CPU either way.
4. **Consider bringing back a dedicated MCU transport thread** to own the SPI staging/
   handshake (the old repo's `transport_thread` idea, previously decided unnecessary for
   the per-interface Bridge model — it may earn its place here).

### Implementation plan / task order

1. **[Risk-1 spike — "try it out"] MCU SPI3-slave bring-up.** Determine whether Zephyr's
   `zephyr,spi-slave` device API is cleanly usable from a sketch (`DEVICE_DT_GET` +
   `spi_transceive` async/DMA); if not, fall back to **register-level SPI3 slave + GPDMA
   TX** via `stm32u5xx_ll_spi.h` (register+DMA is always available). Prove it by staging a
   fixed known pattern.
2. **MPU spidev consumer spike.** Open `/dev/spidev0.0` from Python and read the fixed
   pattern back, validating the link end-to-end. Resolve permissions: spidev0.0 is
   `root:root 0600` and the app runs as `arduino` → add a udev rule / group (one-time
   provisioning like `provision-baud.sh`) or a small root helper. (User: permissions = N/A
   concern, just handle it.)
3. **Handshake.** Implement the RDY-signalled model (decision 3) — resolve the PG13→MPU
   question; use interrupt-driven read, DMA both sides.
4. **Port the fuser frame onto SPI.** Reuse the existing `fuser_frame_header` +
   float32 payload (full res); define our own minimal SPI framing (len/seq/CRC — CS
   delimits transactions). Remove the UART chunking path for the bulk stream.
5. **Move the reassembler into `python/main.py`** (from `tests/fuser_test.py`), consuming
   SPI instead of `Bridge.provide("spec_chunk")`.
6. **UART keeps RPC only** (matrix/rgb/sensor-info/bench) — should be permanently stable
   once the bulk stream is off it. Re-verify with the extended stability monitor.

### Reference

- Full detail + all prior findings: [PROGRESS.md](PROGRESS.md).
- Old repo (logic reference only): `../edgeai-predictive-monitor-unoq`.
- DT sources on device: MPU `/proc/device-tree/.../spi@4a94000/mcu@0`; MCU core dts
  `~/.arduino15/packages/arduino/hardware/zephyr/0.56.0/firmwares/zephyr-arduino_uno_q_stm32u585xx.dts`
  and `.../variants/arduino_uno_q_stm32u585xx/arduino_uno_q_stm32u585xx.overlay`.
