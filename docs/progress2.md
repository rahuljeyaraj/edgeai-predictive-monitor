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

---

## 4. Session 2026-07-14 (cont'd) - spike progress, two real findings, one open hang

Picked up task 1 ("Risk-1 spike - MCU SPI3-slave bring-up"). Two sub-problems the
plan flagged as open got resolved; a third (the spike itself) hit a reproducible
hardware hang that's still unresolved.

### 4.1 PG13/RDY resolved: NOT exposed to the MPU (decision 3's TODO)

Checked the MPU-side devicetree directly (`/proc/device-tree/soc@0/geniqup@4ac0000/
spi@4a94000/mcu@0/` - compatible `"arduino,unoq-mcu"`, reg 0): **no `gpios`/
`interrupts` property at all**. No dedicated TLMM pinctrl group exists for it either
(`pinctrl@500000` only has `qup-spi[0-5]-default-state`). `gpioinfo` across all three
MPU gpiochips shows no named ready/mcu/IRQ line. So PG13 ("Internal SPI RDY" in the
MCU-side overlay, `control-gpios = <&gpiog 13 ...>`) is purely an MCU-side concept -
**the interrupt-driven RDY→MPU-GPIO-IRQ handshake (decision 3's first choice) is not
buildable as-is.** Adopting the plan's own documented fallback: an RPC-trigger over
the (now UART-quiet) link, deferred to the handshake step (task 3) - doesn't block
the wiring spike.

### 4.2 MPU spidev access needed more than a permissions tweak

The plan assumed "permissions = N/A concern, just handle it" (expecting a udev-rule-
style fix like `provision-baud.sh`). Reality: the app's Python code runs inside a
non-privileged Docker container (`edgeai-predictive-monitor-base-station-main-1`).
`/dev` is bind-mounted wholesale into it (confirmed: `/dev/ttyHS1`, `/dev/spidev0.0`
both visible there), but the container's **device-cgroup allowlist** - regenerated by
`arduino-app-cli` fresh on every `app start` from compiled-in logic (`.cache/
app-compose.yaml`'s `device_cgroup_rules: [c 226:* rmw, c 250:* rmw, c 504:* rmw,
c 81:* rmw, c 116:* rmw]`) - doesn't include spidev's major (153). No `app.yaml`/CLI
knob extends it (checked `arduino-app-cli config`/`system --help`, `/etc/arduino-app-
cli/AGENTS.md`, and searched `/var/lib/arduino-app-cli/assets/` for a data-driven
board device profile - none exists; it's compiled into the binary). A plain udev
group fix (what `provision-spi.sh` originally did) gets past the file-permission
check but the container still gets `EPERM` from the cgroup layer regardless.

**Fix shipped** (user chose this over privileged-container or further spelunking):
a small **root-owned host daemon** (`base-station/host/spi_bridge.py` +
`spi-bridge.service`, installed by the rewritten `provision-spi.sh`) that owns the
`/dev/spidev0.0` fd from *outside* the container - where the cgroup restriction
doesn't apply - and re-exposes it over a Unix domain socket at `/dev/spi-link.sock`.
That path is under `/dev`, already bind-mounted into the container, so it needs no
new compose/bind-mount plumbing and survives every `app start` untouched (verified:
a file created under host `/dev` appears live inside the container, same bind
mount). App-side Python talks to the socket, never to spidev0.0 directly. Verified
end-to-end: container → socket → daemon → `spidev.xfer2()` → physical bus round-trip
all work (read back `0xff` fill bytes with the MCU not yet driving the line, as
expected). `python3-spidev` (apt, `3.6-1+b6`) is now installed on the host - the
daemon uses the real package, not hand-rolled ioctl.

### 4.3 OPEN: SPI1 (spi3) in SPI_PERIPHERAL mode hangs setup() - reproduced 2x

Wrote `spi_link.{h,cpp}` per the "clean API first" instructions in task 1: this
core's bundled `SPI` library already exposes `&spi3` as a second object (`SPI1` -
`spis = <&spi2>, <&spi3>;` in `zephyr_user`, confirmed on-device), and its
`ZephyrSPI` class has native slave-mode support (`SPI_HAS_PERIPHERAL_MODE`,
`SPISettings(..., SPI_PERIPHERAL)` sets `SPI_OP_MODE_SLAVE` and calls the same
`spi_transceive()` as controller mode - confirmed by reading `SPI.h`/`SPI.cpp` on-
device, no trial-and-error needed to find this part). Staged a 64-byte known
pattern (4-byte counter + 0..59 ramp) in a loop, `SPI_LINK_THREAD_PRIORITY 3`.

**Result: total hang.** Every Bridge provider (not just spi_link-related) stops
responding, no framing-desync signature in the router log (`journalctl -u
arduino-router` shows zero "invalid packet" errors - this is NOT the known msgpack-
desync wedge from section 2), and the MCU produces **no serial output at all**, even
with `Serial.println()` breadcrumbs added at every step (`SPI1.begin()` start/done,
thread entry, per-iteration begin/transfer/end). Reproduced twice from a clean
baseline (confirmed the disabled-spi_link build is healthy via a full `sudo reboot`
before each attempt - `app stop`/`app start` alone were NOT sufficient to rule out a
stuck OpenOCD/debug session as a confounder, cost real debugging time). `arduino-
app-cli monitor` itself could not be gotten to yield any captured output in this
session (times out unpredictably over the adb-shell harness), so the breadcrumbs
were never actually observed - can't yet tell how far it gets before hanging.

**Leading hypothesis, NOT yet confirmed:** `SPI1` binds to `DEVICE_DT_GET` of the
**`&spi3` bus/controller node** (index 1 of the `spis` phandle list) - it has no
accessor at all for the overlay's actual `compatible = "zephyr,spi-slave"` node
(`spi3/device@0`, a *child* of `&spi3`). `SPI1.begin()`'s `device_init()` path
(`zephyrPinctrl.cpp`'s `init_dev_apply_pinctrl()`) only initializes the bus device,
never touches `device0`. If the shipped Zephyr build's STM32 SPI driver expects
slave-mode operation to be requested through that specific child device rather than
dynamically via `SPI_OP_MODE_SLAVE` on the bus device directly, calling
`spi_transceive()` on the wrong handle could hit unconfigured driver state - fully
consistent with a hard fault/hang (default Zephyr fault handling can halt every
thread, not just the caller, matching the observed total-Bridge-death symptom).

**Current state:** `spi_link_start()` is commented out in `sketch.ino` (code still
present in `spi_link.{h,cpp}`, just not called) - board redeployed and confirmed
healthy (Bridge responsive) after the revert. Diagnostic `Serial.println()` calls
are left in `spi_link.cpp` for the next attempt.

**Next steps to try** (not yet attempted):
1. Get `arduino-app-cli monitor` actually capturing output (needed regardless of
   which path below - flying blind on hangs is expensive, ~3-5min/cycle incl. a
   full reboot to reliably recover).
2. ~~Try targeting `device0` directly~~ - **tried, disproven** (4.4 below).
3. ~~Register-level SPI3 slave + GPDMA TX~~ - **implemented** (4.5), correctness
   unconfirmed due to unrelated UART instability (4.6) - re-test first once the
   link is stable again, don't restart from scratch.

### 4.4 device0 hypothesis DISPROVEN (cheap, compile-time - no hardware risk)

Tried targeting `device0` (spi3/device@0, `compatible = "zephyr,spi-slave"`)
directly via raw `DEVICE_DT_GET(DT_NODELABEL(device0))` + `spi_transceive()`,
instead of going through the Arduino `SPI1` wrapper (which binds to the `&spi3`
**bus** node, not `device0`) - the leading hypothesis in 4.3.

**Result: link error, never reached hardware.** `undefined reference to
__device_dts_ord_201` - `device0` has **no actual driver/device instance compiled
into this firmware at all**. The `zephyr,spi-slave` compatible in the overlay is a
devicetree marker with nothing backing it in this build; there is no separate
"slave device" to target. This disproves the "wrong device handle" hypothesis
cleanly, and for free (a link failure leaves the previously-flashed firmware
running - no hang, no reboot needed to recover this time).

Net effect: `SPI1` (the `&spi3` bus device) was already the only real device
available for this - there was no alternate correct handle to have used instead.
**The hang's root cause is still open.** `spi_link.cpp` is back to the SPI1-based
version (compiles clean, diagnostic `Serial.println()`s left in place) but
`spi_link_start()` remains commented out in `sketch.ino` pending the next
diagnostic step - see the numbered list above (device0's line struck through, plan B
next).

**Operational note:** during this round, `arduino-app-cli app stop`+`start` alone
was *not* sufficient to recover Bridge responsiveness even after a build that never
touched the flashed firmware (the link-error deploy left old-good firmware in place,
yet Bridge still stayed dead until a full `sudo reboot`). This happened on a config
that logged zero framing-desync errors, so treat **any** deploy cycle in this
session as needing a full reboot to reliably confirm board health, not just
app stop/start - cost real time twice before this was internalized.

### 4.5 Register-level SPI3 slave + GPDMA1 TX - IMPLEMENTED (plan B), correctness unconfirmed

With `device0` disproven and SPI1's hang unexplained, went straight to task 1's
designated Plan B per the user's explicit choice: bypass the Zephyr SPI subsystem
entirely, same approach as `mic_sampler.cpp`'s SAI/GPDMA1 work. `spi_link.cpp` was
rewritten around `stm32u5xx_ll_spi.h` + `stm32u5xx_ll_dma.h` directly:

- **Pin mux decoded by hand from the shipped overlay**, not guessed: the overlay's
  raw `pinmux = < 0x... >` values (`spi3_sck_pg9`=`0xd26`, `spi3_miso_pg10`=`0xd46`,
  `spi3_mosi_pb5`=`0x2a6`, `spi3_nss_pg12`=`0xd86`) were decoded against the real
  STM32 encoding, found in the llext-edk's own
  `dt-bindings/pinctrl/stm32-pinctrl.h`: `STM32_PINMUX(port, line, mode) =
  ((port-'A') << 9) | ((line & 0xF) << 5) | (mode & 0x1F)`. All four pins decode to
  **AF6** (e.g. SCK: port=6('G'), line=9, mode=6). This is a primary-source decode,
  not a guess from a datasheet skim.
- **GPDMA1 request line confirmed from source**: `LL_GPDMA1_REQUEST_SPI3_TX = 11`
  (`stm32u5xx_ll_dma.h`). Uses channel 3 (`mic_sampler.cpp` owns channel 2 for
  SAI1_A RX - confirmed no other enabled devicetree node claims any other GPDMA1
  channel, checked the whole generated dts for `dmas = <&gpdma1 ...>` references;
  the only two besides mic's manual channel-2 claim are `sai1_a`/`sai1_b`, both
  `status = "disabled"`).
- SPI3 configured as slave, full duplex, 8-bit, mode 0, MSB-first, **hardware NSS
  input** (PG12 driven externally by the MPU, no software CS needed), Motorola
  frame format, DMA TX request enabled - all in a one-time `spi_link_init_hw()`.
  Per-iteration only `TSIZE` (CR2 - this SPI IP, unlike classic F4/F7, needs the
  frame count set for EOT tracking) and `SPE` toggle, mirroring
  `mic_sampler.cpp`'s full-reconfigure-per-block pattern.
- The wait loop is **bounded** (`k_msleep`-polled like `mic_dma_capture_block()`),
  not blocking forever - a design choice specifically to avoid the SPI1 version's
  hang shape, regardless of root cause: an MPU read that never comes just counts a
  timeout and re-arms.
- Added a `get_spi_link_stats` Bridge provider (transfer/completed/timeout
  counters) and a `spi_link_checkpoint` volatile int updated before every risky
  register-level step, specifically because `arduino-app-cli monitor` could not be
  gotten to capture any output all session (tried plain `adb shell`, backgrounded
  capture, remote-side `timeout` wrapping - all yielded zero bytes even against a
  confirmed-healthy board). This gives a serial-monitor-free way to see how far
  setup() got if something hangs again.

First deploy attempt hit a **flash verify failure** (`Error: verify failed in bank
at 0x08000000 starting at 0x00100000`) - recurred on every deploy of this
specific file (not on the smaller SPI1 version), so it's likely an artifact of this
binary's size crossing a dual-bank boundary, not a real device fault; OpenOCD's own
retry/pad logic appeared to self-correct it each time (`Adding extra erase range`),
confirmed by redeploying cleanly with no errors and getting the same behavior either
way.

**Correctness of this implementation was NOT conclusively validated on hardware**
this session - see 4.6.

### 4.6 CONFOUND: UART link degraded session-wide, independent of any of today's code changes

Early observations looked like `spi_link`'s register-level code was *also* hanging
the MCU (get_spi_link_stats timing out, other providers timing out) - but two
control tests disproved that attribution:

- **Control A**: `spi_link_start()` commented out entirely (matching the very
  baseline this session started from) - **still wedged** within under a minute of
  a fresh router restart + fresh MCU boot (`journalctl -u arduino-router` showed
  30-70 "invalid packet, expected array, got: int8" errors almost immediately -
  this IS the section 2 msgpack-desync signature, unlike 4.3's SPI1 hang which had
  none).
- **Control B**: `spi_link_start()` enabled *and* `fuser_start()` commented out (so
  the one known trigger for the desync - the continuous notify stream - wasn't even
  running) - **also wedged** just as fast.

Both controls wedging, in both directions, means neither `spi_link.cpp` nor the
fuser stream is what's causing the fast wedging observed for the back half of this
session. The most likely explanation: this specific board/session has been under
continuous, heavy stress for several hours - dozens of full deploys, several
`sudo reboot`s, several `systemctl restart arduino-router`s, `fuser` streaming
almost the entire time - and something (the long-running `arduino-router` process,
the physical UART link, or the MCU-side USART peripheral state) has degraded well
below the ~5-15min-of-sustained-streaming threshold documented in section 2. A
`dmesg | grep -i uart` pass found no kernel-level fault on `ttyHS1` itself, which
weakly points at the application/protocol layer (router or MCU) rather than a raw
hardware fault, but this is not conclusive.

**Board left in the session's last known-good source configuration**
(`spi_link_start()` commented out, `fuser_start()` active - i.e. matching what this
project has been running in production, wedge-prone as documented in section 2) -
deployed and confirmed running. `spi_link.cpp`'s register-level implementation is
believed correct (every constant traced to a primary source, not guessed) but
**remains functionally unverified** - next session should re-test it fresh (ideally
after a genuine cool-down / power cycle, not just `sudo reboot`, and confirm 2-3
consecutive clean `Bridge.call()`s before touching `spi_link_start()` at all) rather
than starting the design over.
