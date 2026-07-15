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
  `arduino-router` systemd service. Baud is the library default **115200** (reverted
  2026-07-14, see 4.7 - `provision-baud.sh`'s 1000000 override is no longer applied
  on-device; baud was never the fix for the wedge, see section 2). MCU side is
  `Bridge.begin(BRIDGE_BAUD)`, `app_config.h`. Both ends must match.
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

### 4.7 Session 2026-07-14 (new session) - UART baud reverted, spi_link re-tested fresh: hang REPRODUCED, not a confound this time

Picked up the recommended next steps: committed the SPI spike (4.1-4.6) as-is,
reverted `BRIDGE_BAUD` 1000000 -> 115200 (library default) since it was never
the fix for anything - the wedge reproduces identically at 1M/2M baud (section
2) and the only reason it was raised was to carry the fuser stream directly
over Bridge, which is exactly what SPI is meant to replace. Removed the
on-device `99-baud.conf` systemd override so the router's stock generator
drop-in (already 115200) is what's active; confirmed after a full `sudo
reboot`.

**Then re-tested `spi_link_start()` on a verified-clean baseline**, exactly as
4.6 recommended: full reboot, confirmed 3 consecutive clean `Bridge.call()`s
and zero "invalid packet" errors, *then* enabled `spi_link_start()` and
deployed.

**Result: the hang reproduced**, and cleanly enough this time to rule out the
4.6 confound:
- Immediately after the deploy, `get_spi_link_stats` returned a fast, clean
  "method not available" (router alive, MCU hadn't registered it yet) while
  setup() was presumably still running through matrix/rgb/accel/mic/bench -
  then every subsequent call, to *any* provider including ones registered
  earlier in setup() (`get_mic_info`, `get_bench_stats`), timed out with no
  response at all.
- Repeated the recovery procedure (restart router, then `app stop`/`app
  start`, which reflashes+resets the MCU) to get a second fresh trial from the
  same flashed firmware - **hung again, this time from the very first call**
  (even `get_spi_link_stats` itself timed out rather than returning "not
  available"), consistent with the fault landing earlier on this run, before
  the Bridge.provide() registration's `k_msleep(50)` flush window.
- Router log both times: **zero new "invalid packet" errors** correlated with
  the hang itself (the only errors were a burst at the reset/reflash moment,
  matching the router-reconnect transient seen elsewhere this session, not
  the section-2 desync signature). This is the *same* symptom as 4.3's SPI1
  hang - total, silent Bridge death - not the msgpack framing desync.

**Conclusion: this is not the 4.6 confound.** That confound was real (both
controls in 4.6 wedged with the classic desync errors, on a link degraded by
hours of continuous stress) but this session started from a freshly-rebooted,
freshly-verified-clean board and reproduced a *different, silent* failure
signature twice, correlated 1:1 with `spi_link_start()` being enabled at all.
**The register-level SPI3+GPDMA1 rewrite (4.5) does not fix the hang** - it
reproduces the same "total Bridge death, zero desync errors" shape as the
original SPI1/Zephyr-API attempt (4.3), despite going through neither the
Zephyr SPI subsystem nor `SPI1`. The common factor between the two failed
attempts is no longer "which SPI API" - it's something else shared by both:
candidates worth checking next are the SPI3/GPIO/DMA clock-enable sequence
itself (`spi_link_init_hw()`'s `LL_AHB2_GRP1_EnableClock`/
`LL_APB3_GRP1_EnableClock`/GPDMA1 calls), a pin conflict, or a power-domain
issue - anything that could hard-fault *before* the checkpoint/DMA-timeout
machinery in `spi_link.cpp` even gets a chance to bound it, since the
whole point of that machinery (bounded `k_msleep` polling instead of a
blocking wait) assumed the failure mode would be "MPU never reads," not "MCU
faults outright."

`spi_link_checkpoint`/`get_spi_link_stats` could not be read post-hang either
time (Bridge itself was dead), so **the checkpoint diagnostic added in 4.5 has
not yet been observed mid-failure** - it only ever returned "not available"
(pre-registration) or timed out (post-hang), never a value. Getting
`arduino-app-cli monitor` (or any serial capture) actually working remains the
single highest-value next step (flagged since 4.3) - without it, this is
still blind trial and error.

**Board recovered**: redeployed with `spi_link_start()` re-commented-out
(sketch.ino), confirmed 3 clean `Bridge.call()`s and provider list back to
normal (`get_spi_link_stats` cleanly "not available" as expected when
disabled). Left running in this last-known-good state, `BRIDGE_BAUD` now
permanently at 115200 pending the SPI work actually landing.

## 4.8 Session 2026-07-14 (cont'd) - SPI LINK WORKING END-TO-END; every hang root-caused

**Bottom line: the SPI link works.** `spi_link_test.py` passes **40/40 armed
frames** (two consecutive 20-round runs): each round RPCs `spi_arm`, the MCU
stages a 64-byte frame (counter + ramp) via SPI3-slave + GPDMA1 TX, the MPU
clocks it out through `/dev/spidev0.0` (via the spi-bridge daemon socket),
and counter + ramp verify exactly - 0 timeouts, 0 DMA errors, Bridge healthy
throughout. The register-level implementation (4.5) was essentially correct
all along; the hangs were **scheduling starvation**, not SPI bring-up faults,
plus two latched-flag bugs (one in spi_link, one pre-existing in mic).

### The debugging unlock: SWD via the on-board OpenOCD (no serial monitor needed)

`/opt/openocd/bin/openocd` on the MPU + `/opt/openocd/openocd_gpiod.cfg`
(linuxgpiod SWD bit-bang, needs sudo, add `-s /opt/openocd -s
/opt/openocd/share/openocd/scripts`) attaches to the live MCU non-intrusively:
halt/resume, read PC/BASEPRI/xPSR/CFSR, and `dump_image /tmp/ram.bin
0x20000000 0xC0000` for full-RAM post-mortems. The base firmware ELF with
symbols ships at `~/.arduino15/.../firmwares/zephyr-arduino_uno_q_stm32u585xx.elf`
(host `addr2line`/`nm` work on it). llext sketch statics have no symbol map,
so `spi_link.cpp` keeps a magic-marked diag block (`0xC0FFEE01`, last
checkpoint, stamp count) findable by scanning the dump. Thread forensics:
`_kernel` @ 0x20001e30 (`.current` at +8), thread structs findable by their
name strings (CONFIG_THREAD_NAME=y), `thread_state` at +0xd (0x80=QUEUED),
prio at +0xe, saved PSP at +0x50 → exception frame → the thread's parked PC.
This replaced ~3-5min blind reboot cycles and is what cracked everything below.

### Root causes found (in the order they were peeled back)

1. **spi_link thread priority 3 (above Bridge's 5)** - both historical
   total-Bridge-death hangs (4.3, 4.7). Any non-yielding path in a thread
   above Bridge starves Bridge + setup() forever: silent death, zero desync
   errors - exactly the observed signature. Fixed: `SPI_LINK_THREAD_PRIORITY`
   → **6** (below Bridge 5; above mic 7 - see #4), plus per-arm GPDMA flag
   clears, split error/timeout counters in `get_spi_link_stats`, 100ms error
   back-off.
2. **fuser spins inside `Bridge.notify` at 115200 baud.** The 4.7 baud revert
   made one ~4.4KB frame take ~407ms of UART vs the 64ms epoch (measured:
   `fus_ovr==fus_frm`, `fus_avg=407.5`). Once TX backs up, fuser (prio 6)
   busy-spins in notify and starves mic/spi_link/loop() forever while Bridge
   (5) still answers RPC (SWD: `_kernel.current==fuser`, all fps 0, victims
   QUEUED-ready). Also fixed the over-budget path `k_yield()` → `k_msleep(1)`
   (k_yield only yields to equal priority). **fuser_start() is disabled in
   sketch.ino until the stream rides SPI - do not re-enable on UART@115200.**
3. **mic latched-TC fast-exit** - pre-existing: mic never cleared GPDMA
   flags, so after the first block its wait exited instantly on stale TC:
   mic_fps=188.6 vs the ~47 real-time max, ~100% CPU at prio 7, FFT-ing a
   frozen buffer. Fixed with the same per-arm flag clears. **This unmasked a
   deeper pre-existing bug: SAI1 isn't actually delivering data**
   (`sr=0x0` always; with honest flags every block now times out,
   `mic_win=0`, `mic_to` climbing). The mic pipeline had been serving frozen
   spectra for an unknown time. OPEN - needs its own session; suspect SAI
   clock/pin state, unrelated to SPI work.
4. **spi_link at 8 was then starved by #2/#3** (anything below mic inherits
   whatever mic leaves over) - hence priority 6, fuser's vacated slot.
5. **SPI3 latched EOT suppresses re-arm** - first armed frame perfect, every
   subsequent one clocked 1 stale byte + underrun zeros with DMA never
   firing: SPE=0 flushes FIFOs but not IFCR flags, and a pending EOT gates
   TXP/DMA requests. Fixed: clear EOT/TXTF/UDR/OVR/MODF/FRE/SUSP after every
   disarm in `spi_link_wait_transfer()`.

### Handshake (task 3) - done, RPC-triggered

Free-running arm + blind MPU reads only synced ~1-2/10. Now: MPU calls
`spi_arm` provider → MCU stages a frame inline on Bridge's thread (pure
register writes), replies with the frame counter, wakes the spi_link thread
which owns the bounded completion wait + disarm + stats; `busy` reply while a
wait is in flight (MPU retries). `tests/spi_link_test.py` implements the MPU
side.

### Operational notes

- After every reflash this session, Bridge came up dead until
  `systemctl restart arduino-router` + app stop/start (the 4.4 note is now a
  hard rule: **deploy = push, app start, router restart, app start**).
- The first deploy of the day froze differently (BASEPRI stuck at 0x10,
  SysTick masked, kernel tick frozen, CPU parked in WFI) and never recurred
  after the router-restart discipline; unexplained, lower priority now.
- The flash-verify error on deploy (4.5) remains cosmetic.

### Next steps

1. Port the fuser frame onto this link (plan tasks 4-5): `spi_arm`-style
   handshake, 4112-byte frames (TSIZE fits), reassembler into
   `python/main.py`; re-enable fuser as the SPI producer.
2. Fix the real mic/SAI capture bug (see #3 above).
3. Re-run the extended UART stability monitor with the bulk stream gone
   (plan task 6).

## 5. Session 2026-07-15 - fuser ported onto SPI (code done); 4KB slave-TX underrun is the new blocker

Implemented tasks 4-5 end-to-end in code. The software architecture works and
is deployed; a **hardware/timing blocker remains**: the SPI3 slave cannot
sustain a gapless ~4.1KB DMA-fed transfer - it underruns and only ~a handful of
frames per thousand complete. Root cause narrowed but not yet fixed.

### 5.1 What was built (all committed-worthy, in the working tree)

- **`spi_link.{h,cpp}` - generalized from the 64-byte spike into a real frame
  transport.** New `spi_link_stage_frame(payload, len)`: wraps the payload in a
  minimal SPI framing header + CRC32 trailer and holds it as the latest
  "pending" frame under a mutex. Wire frame (little-endian):
  `[magic u32 = 0x46555331][seq u16][payload_len u16][payload...][crc32 u32 over
  header+payload]`. CRC32 is the standard reflected/zlib variant (table built in
  `spi_link_crc_init`), so the MPU verifies with a plain `zlib.crc32`. `spi_arm`
  now serves the **latest staged frame** (copies pending -> DMA buffer,
  variable-length TSIZE/DMA), replies `"<seq>,<total_len>"` (or `"empty"`/
  `"busy"`). Two buffers (pending vs DMA source) so the fuser can stage while a
  frame is still clocking. Stats string extended:
  `checkpoint,staged,armed,completed,timeouts,errors,last_error_flags` +
  (TEMP diag) `,sr=,cr1=,rem=`.
- **`fuser.cpp` - transport swapped.** Dropped the `Bridge.notify("spec_chunk")`
  chunking entirely; the thread now builds the same 4112-byte payload and calls
  `spi_link_stage_frame()`. Full float32, sample-and-hold, epoch pacing all
  unchanged. `fuser_start()` re-enabled in `sketch.ino`; priority/epoch comments
  reconciled in `app_config.h` (fuser + spi_link coexist at prio 6, cooperative,
  priority-inheriting mutex).
- **`python/main.py` - the real SPI consumer** (background thread: `spi_arm` ->
  socket read -> CRC/magic/seq verify -> decode header+spectra -> dedup by seq,
  `loop()` prints a live fps/peak/health summary). `tests/spi_link_test.py`
  rewritten for the SPI fuser frame; obsolete UART `tests/fuser_test.py` deleted.
- **`host/spi_bridge.py` - two real fixes** (see 5.2): the transfer is now a
  single raw `SPI_IOC_MESSAGE` ioctl (kernel bufsiz raised to 65536), and the
  daemon catches all per-client exceptions so a bad request can't kill it.
  `provision-spi.sh` now also installs `/etc/modprobe.d/spidev.conf`
  (`bufsiz=65536`) - **this was set by hand on-device this session; the script
  change makes it reproducible but has NOT been re-run through provision-spi.sh.**

### 5.2 spidev/daemon fixes (needed before the MCU issue was even visible)

- **spidev `bufsiz` (default 4096) < our 4124-byte frame.** The daemon's
  `spi.xfer2([0]*4124)` first died with `OverflowError: Argument list size
  exceeds 4096 bytes` and, being uncaught, crashed the daemon and removed the
  socket (-> app-side `ENOENT`). Raised the kernel module param to 65536
  (`/etc/modprobe.d/spidev.conf` + `rmmod/modprobe spidev`; spidev is a loadable
  module here, `/dev/spidev0.0` auto-rebinds on reload).
- **py-spidev has its OWN hardcoded 4096 cap on `xfer2`** independent of kernel
  bufsiz (still `OverflowError` after raising bufsiz). Its `xfer3` splits into
  multiple `SPI_IOC_MESSAGE` calls, each toggling CS - which would break the
  MCU's single-CS-per-frame contract (TSIZE-bounded slave transfer; a mid-frame
  NSS deassert aborts it). **Fix: do the transfer as ONE raw `SPI_IOC_MESSAGE`
  ioctl** (`_SPI_IOC_MESSAGE_1 = 0x40206B00`, `struct spi_ioc_transfer` packed
  as `"QQIIHBBBBBB"`), one CS assertion for the whole frame. Validated
  standalone on the host: 4124 bytes in one transfer, `spi.fileno()` works.

### 5.3 THE BLOCKER - SPI3 slave TX underruns on the 4KB frame (root cause narrowed)

Symptom: with fuser enabled and streaming, `get_spi_link_stats` shows
`completed` ~= 4 while `timeouts` climbs into the hundreds; the MPU reads the
right length but wrong content (`bad_magic`, and the "magic" values decode to
mic-magnitude floats -> the read is byte-shifted into the payload / mostly
underrun zeros). The 64-byte spike (4.8) still worked 40/40; only the jump to
4KB frames (and fuser being enabled) is new.

Register truth (TEMP `sr/cr1/rem` diag added to `spi_link_wait_transfer`, read
via `get_spi_link_stats`), captured at 1 MHz:
`sr=0x907f, cr1=0x1, rem=~3600`.
- `cr1=0x1` -> SPE=1 (SPI still enabled at capture, expected).
- `sr=0x907f` decodes to RXP|TXP|DXP|**EOT**|TXTF|**UDR**|OVR|TXC set, **CTSIZE=0**.
  i.e. the SPI clocked ALL 4124 frames (EOT, CTSIZE=0), TXP=1 (SPI still wants
  data), and **UDR=1 (transmit underrun)**.
- `rem=~3600` -> the GPDMA only moved ~500 of 4124 bytes before the master
  finished and EOT cut it off. The other ~3600 clocked out as underrun zeros.

So: the master (spidev/GENI) clocks the whole frame gaplessly in one window
(measured 36 ms @ 1 MHz = ~8.7 us/byte, genuinely 1 MHz - not secretly faster),
but the **GPDMA feeds TXDR far slower than the master consumes it** (~14 KB/s
effective, ~70 us/byte), so most of the frame underruns and EOT then halts the
DMA with `rem` still high.

Clock-sweep (daemon `SPI_MAX_HZ`, reverted to 1 MHz afterwards) ruled out a
simple rate story: 1 MHz -> ~500 B moved; 100 kHz -> ~3800 B; 50 kHz -> ~3200 B.
Slowing the master does NOT reliably fix it and 50 kHz was *worse* than 100 kHz -
consistent with "any single stall/underrun in the window kills the frame, and a
longer transfer is exposed to more stalls," NOT "DMA just needs more time."

`mic_sampler.cpp`'s GPDMA1-ch2 config is byte-for-byte the same shape (mirror
direction) and worked; the only material deltas vs the working 64B spike are
**(a) frame size 64 -> 4124 and (b) fuser now enabled** (extra threads + a 4KB
memcpy+CRC every 64 ms, though its duty cycle is <1% so a *sustained* 14 KB/s
throttle from fuser contention alone is hard to explain).

### 5.4 The decisive next experiment (was mid-setup when paused)

Isolate size vs fuser-contention: **disable `fuser_start()` and have `spi_link`
self-stage one fixed 4112-byte pattern at start**, then read it repeatedly.
- If `completed` climbs -> a 4KB transfer is fine on its own -> the fuser's
  presence (bus contention / scheduling) is the cause -> fix by reducing
  contention (e.g. move the CRC/memcpy off the hot path, dedicate an SRAM bank
  for `spi_link_frame_buf`, or pace/lower fuser load).
- If it still fails -> the underrun is intrinsic to the large DMA-fed slave
  transfer -> fix on the transfer side (candidates below).

A `SPI_LINK_SELFTEST` #define + a `// fuser_start()` toggle were half-added then
reverted so the tree stays coherent; re-add them for this experiment. (The
`sr/cr1/rem` TEMP diag in `spi_link.cpp` was LEFT IN - it's useful and marked.)

### 5.6 Isolation experiment DONE - underrun is INTRINSIC to the 4KB transfer

Ran the 5.4 experiment (2026-07-15): `SPI_LINK_SELFTEST 1` (spi_link self-stages
one fixed 4112-B pattern), `fuser_start()` commented out, rebuilt/flashed.

**Result: the 4KB transfer STILL underruns with the fuser fully disabled.**
`completed=0`, `sr=0x907f` (UDR again), but the DMA got FURTHER before dying:
`rem`≈2500-2800 (~1300-1600 B moved) vs ~500 B with the fuser on. So:
- The underrun is **intrinsic to sustaining a gapless ~4KB DMA-fed slave TX** -
  NOT caused by the fuser. The fuser only makes it worse (more bus contention ->
  the DMA falls behind sooner: ~500 B vs ~1500 B before the fatal stall).
- Mechanism (best model): the master (GENI) clocks the whole frame **gaplessly**
  at 1 MHz; the GPDMA keeps pace for a while, then a momentary bus-contention
  stall (CPU doing mic/accel FFTs, etc.) lets the 16-byte TX FIFO run dry; **UDR
  latches and halts the TX DMA requests**, so that one underrun kills the whole
  remaining frame (rem stuck). The stall interval scales with system load:
  ~1 per ~1500 B (fuser off) to ~1 per ~500 B (fuser on).

**Ruled out this session (no rebuild needed):**
- `word_delay_usecs` in the SPI ioctl to add inter-byte gaps (would let the DMA
  refill the FIFO between bytes): **the GENI master ignores it** - 4124 B took
  ~33 ms at word_delay 0/10/30/60 µs alike. The master is inherently gapless.
- Lowering SCK (100 kHz/50 kHz): does NOT help and 50 kHz was worse - a longer
  transfer window is exposed to MORE stalls (5.3).

**Also found:** spidev `bufsiz` does NOT survive a board power-cycle
(`/etc/modprobe.d/spidev.conf` is applied too late - spidev auto-loads at boot
before modprobe.d, comes up 4096). Manual `rmmod spidev; modprobe spidev` picks
up the conf (65536). `provision-spi.sh` now writes the conf but that's not enough
at boot - **make the daemon self-heal it** (spi-bridge.service `ExecStartPre` that
reloads spidev with bufsiz before the daemon opens the fd), or use the kernel
cmdline `spidev.bufsiz=`. The daemon's broad per-client `except` DID hold through
the EMSGSIZE storm (it logged `[Errno 90] Message too long` and kept the socket).

### 5.7 BREAKTHROUGH: word-width DMA fixes the underrun (throughput); a FIFO-sync desync remains

The underrun was a **DMA throughput** problem: the byte-width GPDMA (1 byte per TX
request) couldn't stay ahead of the gapless 1 MHz master. Fix that landed the
completion:
- **32-bit WORD-width TX DMA** (`spi_link_configure_dma`: `SRC/DEST_DATAWIDTH_WORD`,
  buffers `__attribute__((aligned(4)))`) - a 32-bit write to `SPI3->TXDR` with
  8-bit frames packs 4 bytes into the FIFO, so the DMA does 1/4 the bus
  transactions. Our framing is always a multiple of 4.
- **SPI FIFO threshold `TH_04DATA`** (was `TH_01DATA`) - required so TXP / the TX
  DMA request fires only when >=4 slots are free, matching the word write. With
  threshold 1 the DMA wrote a full word whenever >=1 slot was free, overran the
  FIFO, and raced to a **false completion with no master clock** (verified: `armed`
  and `completed` both incremented on a bare `spi_arm` with no socket read).

**Result: `completed` == `armed`, `timeouts=0`, `errors=0`, `rem=0`, `sr=0x0`
clean - the transfer now completes on real master clocks only. The gross underrun
is solved.**

**BUT the frame content is still wrong** (single-consumer, fuser off, SELFTEST
ramp payload `0,1,2,...`): each read is missing the 8-byte header (magic never
appears anywhere), the last 4 bytes are always zero (small tail underrun), and the
read's start byte advances **exactly +28 every transfer** (0x54,0x70,0x8c,... ;
4124 mod 256 = 28). So there's (a) an ~28-byte tail underrun still, and (b) an
accumulating phase desync where the wire position isn't reset to `frame_buf[0]`
per transfer - a stale-FIFO / SPI-transfer-state carry-over that `SPE=0` + the
IFCR flag clears + `DMA ResetChannel` don't fully clear with word packing. The
header (magic) never reaching the wire is the practical blocker.

**Tried: full RCC APB3 SPI3 reset per disarm** (`spi_link_reset_spi()`, refactored
`spi_link_configure_spi()` out of init_hw, called from `spi_link_wait_transfer`'s
disarm). It made the **first transfer after a fresh boot byte-perfect** (magic +
seq + len + ramp all correct - proof the whole path CAN deliver a clean frame),
but did NOT fix subsequent transfers, and deeper measurement showed the
corruption is **inconsistent and multi-layered**, not a single clean shift:
- Some reads deliver a long valid ramp then underrun; one fresh transfer delivered
  only 28 valid bytes (hdr + ramp 0..19) then jumped to `0x0c` and zeros. The
  valid-length and start-offset vary run to run.
- So there are several interacting failure modes at once: a tail/early underrun
  whose onset varies, residual FIFO/word-ordering carry-over between transfers,
  and word-packing alignment. `SPE=0`, IFCR clears, DMA ResetChannel, AND a full
  RCC reset all together still don't make transfer N match transfer 0.

**Assessment / decision needed:** the register-level word-DMA slave-TX path for a
single ~4KB frame is a deep STM32U5-SPI-FIFO bring-up problem that likely needs a
logic analyzer or live SWD register watching to finish (watching CTSIZE/BNDT/FIFO
level during a transfer). The alternative is to **pivot to chunking**: 64-byte
transfers were rock-solid (40/40, §4.8), so send the frame as a sequence of small
CS-delimited sub-transfers the DMA can reliably deliver, reassembled on the MPU
(byte-width DMA is fine at small sizes - no word-packing needed, no FIFO-residual
problem). Cost: more sub-reads per frame -> lower fps, a chunk state machine on
both ends, per-chunk CRC/retry. Given how solid the small path is, chunking is the
lower-risk route to a working link; the word-DMA work stays valuable if we later
want single-shot 4KB.

### 5.8 RESOLVED - chunked pull works end to end (tasks 4-5 DONE)

Pivoted to chunking (user's call). Reverted the word-DMA experiment back to
**byte-width DMA** (rock solid for small transfers) and made `spi_arm(offset, len)`
arm an arbitrary sub-range of the latched frame, so the MPU pulls each ~4.1KB
frame as a sequence of small sub-transfers and reassembles + CRC-checks them.
Chunk size is MPU-chosen (tunable without reflashing).

Chunk-size sweep (SELFTEST ramp, fuser off): **512B = 20/20 CRC-OK in both runs,
~8 fps** and was the reliable sweet spot; larger sizes were faster but flaky and
non-monotonic (2048 hit 20/20 once, but 1536/1024/768 partially failed - the
slave-TX underrun risk is probabilistic above ~512B). So `CHUNK_SIZE = 512` in
`python/main.py`, with a whole-frame CRC retry (3x) that absorbs the rare bad
chunk.

**End-to-end on the real pipeline (fuser ON, SELFTEST off, real chunked
main.py):** full-res float32 spectra flow MCU->SPI->MPU at **~6.5 fps, drop=0,
crc_fail=0** sustained (600+ frames), the **accel peak tracks live motion** (bins
move with shaking), and **Bridge RPC stays healthy** the whole time
(get_bench_stats responds; ZERO steady-state router "invalid packet" errors - the
§2 UART wedge is gone now that the bulk stream is off the UART). `mic mag=0` is the
separate pre-existing SAI capture bug (§4.8 #3), NOT a transport issue.

**This closes plan tasks 4-5.** The fuser frame rides the dedicated SPI link;
`python/main.py` is the reassembler/consumer; the UART carries RPC only and is
stable.

**Board/tree state:** working build deployed (byte-DMA, chunked, fuser ON,
`SPI_LINK_SELFTEST 0`, real main.py). The `sr/cr1/rem` diag is still in
get_spi_link_stats (harmless, can be trimmed later). Board healthy and streaming.
All changes **uncommitted** - ready to commit when desired. `bufsiz` note (5.6)
still applies: it reverts to 4096 on a board power-cycle; add a spi-bridge.service
`ExecStartPre` (or kernel cmdline) so the daemon self-heals it.

### Remaining (separate from this transport work)
- **mic/SAI capture bug** (§4.8 #3): SAI1 delivers no data (`mic mag=0`), so the
  mic half of every frame is zeros. Needs its own session.
- **Extended UART stability monitor** (plan task 6): now worth running long with
  the bulk stream permanently off the UART.
- Tune up from 6.5 fps if needed (bigger chunks with retry, or fewer RPC
  round-trips per frame - e.g. one arm that auto-advances chunks).

Transfer-side fix candidates if the residual-flush approach stalls:
- **Chunk the frame** into pieces that reliably complete (the 64B path is rock
  solid), pulled efficiently (avoid one UART-RPC `spi_arm` per chunk - ~10 ms
  each would cap fps hard; batch multiple chunk-reads per arm, or re-arm on the
  spi_link thread between the master's chunk reads).
- **Deeper FIFO buffering / burst DMA** (raise `LL_SPI_SetFIFOThreshold`, use a
  source burst) so momentary DMA stalls are absorbed by a fuller TX FIFO (only
  16 bytes deep, so limited headroom).
- **Check GPDMA port allocation** - both src+dest are on `ALLOCATED_PORT0`
  (serialized); try splitting SRAM-read and APB3-write across the two GPDMA
  ports so they pipeline.
- Confirm whether **UDR halts TX DMA requests** on this SPI IP (if so, the frame
  is unrecoverable after the first underrun regardless of everything else).

### 5.5 Current board + tree state

- **Board:** flashed with the instrumented firmware (fuser ENABLED, `sr/cr1/rem`
  diag present); `main.py` = the real SPI consumer, running but logging
  "no frames yet / bad_magic" (the 5.3 blocker). Bridge is HEALTHY throughout
  (this is NOT the section-2 UART wedge - RPC works fine, `get_bench_stats`/
  `get_spi_link_stats` respond). `spi-bridge` daemon = the fixed raw-ioctl
  version, `SPI_MAX_HZ` restored to 1_000_000, `active`. spidev `bufsiz`=65536
  (persisted). Deploy discipline held: push -> app start -> router restart ->
  app start (the 4.8 rule).
- **Tree:** all of 5.1's code changes are uncommitted in the working tree
  (`git status`: modified sketch/*, python/main.py, host/spi_bridge.py,
  provision-spi.sh, tests/spi_link_test.py; deleted tests/fuser_test.py). The
  temporary `SPI_LINK_SELFTEST` scaffolding was reverted; the `sr/cr1/rem` diag
  remains (marked TEMP - remove once the transport is solid).
- **mic/SAI** still broken (4.8 #3), independent of all this.
