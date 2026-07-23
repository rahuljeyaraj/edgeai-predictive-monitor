# Appendix: Running a Pure Zephyr Image on the Arduino Uno Q's STM32U585 via the Onboard ADB/SWD Bridge

**Project:** Arduino Physical AI Challenge India 2026 — Industrial Predictive Maintenance System
**Scope:** Bare-metal Zephyr RTOS on the STM32U585 MCU, bypassing Arduino App Lab / `arduino-router` entirely.

---

## 1. Why this matters

The Arduino Uno Q's headline feature — and the reason it suits a "Physical AI" edge-inference project — is its dual-brain split: a Qualcomm QRB2210 MPU running Linux, and an STMicroelectronics STM32U585 MCU (Cortex-M33 @ 160 MHz) for real-time work. Arduino's official development path keeps you inside **App Lab**, where sketches on the MCU talk to the Linux side over a MessagePack-RPC layer (`arduino-router`) via the **Bridge** API.

That path is convenient, but it boxes you into Arduino's scheduling, RPC overhead, and runtime assumptions. For a predictive-maintenance pipeline doing FFTs and feature extraction in real time, we wanted direct, unmediated control of the MCU's execution environment — which means treating the STM32U585 as a standalone Zephyr target, not "the Arduino sketch processor."

The interesting discovery that justifies this appendix: **Arduino's own stock firmware on the STM32U585 is itself a Zephyr build.** Their shipped binaries live at:

```
~/.arduino15/packages/arduino/hardware/zephyr/<version>/firmwares/zephyr-arduino_uno_q_stm32u585xx.{elf,bin,hex,dts,config}
```

Most teams will never look past App Lab and will not realize this. We went one layer deeper: we replaced Arduino's Zephyr image with our **own**, built from upstream Zephyr `main`, flashed over the exact same SWD path their internal tooling uses — but without their bootloader, RPC layer, or sketch model at all. This is a genuine architecture-level result, not just "we got Zephyr running on a dev board."

This told us the underlying platform was already Zephyr-native, even though App Lab presents it as an Arduino sketch target. We used that as the starting point to go one layer deeper: we replaced Arduino's Zephyr image with our own, built from upstream Zephyr `main`, flashed over the exact same SWD path their internal tooling uses — but without their bootloader, RPC layer, or sketch model at all. The result is full, unmediated control of the MCU's execution environment: our firmware owns the boot sequence, the scheduler, and every peripheral driver directly, with nothing in between it and the silicon.

It is also, as of this writing, **not a turnkey `west flash` workflow**. The upstream Zephyr documentation for this board states plainly that the QRB2210-as-SWD-adapter interface "is not yet integrated with the `west flash` command" — debugging via `west debug` is supported, but flashing is a manual OpenOCD invocation. This appendix documents exactly that manual path, reproducibly.

---

## 2. Key discovery: how the board actually flashes its own MCU

The Uno Q has no exposed SWD header for an external debug probe. Instead, the QRB2210's Linux userspace bit-bangs SWD directly into the STM32U585 over Linux GPIO character devices (`gpiod`), using OpenOCD's `linuxgpiod` adapter driver. This is the same mechanism Arduino's own App Lab uploader uses internally — we are simply invoking it ourselves, against our own binary.

The relevant files already exist on the board's Linux filesystem (found via `adb shell`, no installation needed):

```
/opt/openocd/bin/openocd          # the OpenOCD binary
/opt/openocd/openocd_gpiod.cfg    # adapter + GPIO pin config (see below)
/opt/openocd/stm32u5x.cfg         # STM32U5 family target script (sourced)
/opt/openocd/stm32x5x_common.cfg
```

`openocd_gpiod.cfg` in full:

```tcl
adapter driver linuxgpiod
adapter gpio srst 38 -chip 1
adapter gpio swclk 26 -chip 1
adapter gpio swdio 25 -chip 1
adapter gpio trst 38 -chip 1
transport select swd
source [find stm32u5x.cfg]
```

This tells us precisely how the link works: SWCLK on GPIO line 26 of `gpiochip1`, SWDIO on line 25, shared SRST/TRST on line 38, transport SWD. No external debug probe, no JTAG header — it's a direct wired connection from the application processor's GPIO bank to the microcontroller's debug pins.

We verified this link is live and independent of whatever firmware happens to be running on the STM32, with a connect-only test (no programming):

```bash
adb shell "cd /opt/openocd && ./bin/openocd -f openocd_gpiod.cfg -c init -c 'targets' -c shutdown"
```

Output confirmed a valid SWD handshake and correct core identification:

```
Info : SWD DPIDR 0x0be12477
Info : [stm32u5.ap0] Examination succeed
Info : [stm32u5.cpu] Cortex-M33 r0p4 processor detected
```

---

## 3. Reproducible procedure

### 3.1 Prerequisites

- Arduino Uno Q connected via USB to a Linux host (or WSL2 on Windows, with the USB device attached via `usbipd`).
- `adb` installed and able to see the board (`adb devices` shows the board's serial as `device`, not `unauthorized` or `no permissions`).

> **WSL2 note:** if `usbipd attach` fails with `Device busy (exported)`, check for a stray `adb.exe` process already running on the Windows host (e.g. left over from Arduino IDE) and kill it before retrying the attach. If `adb devices` reports `no permissions`, add a udev rule for vendor ID `2341` and add your user to the `plugdev` group (or use the official post-install script shipped with the Arduino Core Zephyr repo, which does this automatically).

### 3.2 Install the Zephyr toolchain (one-time)

```bash
# System dependencies
sudo apt update
sudo apt install -y --no-install-recommends git cmake ninja-build gperf \
  ccache dfu-util device-tree-compiler wget \
  python3-dev python3-pip python3-setuptools python3-tk python3-wheel xz-utils file \
  make gcc gcc-multilib g++-multilib libsdl2-dev libmagic1 \
  python3-venv pkg-config libusb-1.0-0-dev

# Isolated Python environment for west
mkdir -p ~/workspace/zephyrproject
python3 -m venv ~/workspace/zephyrproject/.venv
source ~/workspace/zephyrproject/.venv/bin/activate
pip install west

# Initialize workspace against upstream main
# (arduino_uno_q board support requires Zephyr >= 4.3.0; main is safely ahead of that)
cd ~/workspace/zephyrproject
west init -m https://github.com/zephyrproject-rtos/zephyr --mr main .
west update

# Zephyr's own Python requirements (note: requires libusb-1.0/pkg-config above,
# needed to build the hidapi wheel)
pip install -r zephyr/scripts/requirements.txt

# SDK + ARM cross-compiler toolchain
cd zephyr
west sdk install --gnu-toolchains arm-zephyr-eabi
```

> **Gotcha:** `west sdk install` with no toolchain flag, or with the deprecated `--toolchains` flag, may silently install **host tools only** and report success without ever fetching `arm-zephyr-eabi-gcc`. If `find ~/zephyr-sdk-*/gnu -iname "arm-zephyr-eabi-gcc"` comes back empty after install, delete `~/zephyr-sdk-<version>` and `~/.cmake/packages/Zephyr-sdk` entirely and re-run `west sdk install --gnu-toolchains arm-zephyr-eabi` against a clean slate — a stale "already installed" check otherwise short-circuits the real toolchain download.

Set the toolchain environment variables (add to `~/.bashrc` to persist across sessions):

```bash
export ZEPHYR_TOOLCHAIN_VARIANT=zephyr
export ZEPHYR_SDK_INSTALL_DIR=$HOME/zephyr-sdk-1.0.1   # match your installed version
```

> **Gotcha:** do not manually export `ZEPHYR_BASE`. Let `west` auto-discover the workspace root from `.west/config`. A stray `ZEPHYR_BASE` pointing at the wrong path (e.g. left over from an earlier, abandoned workspace location) silently overrides west's own topdir discovery and produces confusing "file not found" errors that look unrelated to the actual cause.

### 3.3 Build

```bash
cd ~/workspace/zephyrproject/zephyr
west build -p always -b arduino_uno_q samples/basic/blinky
```

A successful build prints a memory usage table and ends with `Generating files ... for board: arduino_uno_q/stm32u585xx`. Confirm the board/SoC string is exactly that — it's the signal that the correct board definition was picked up from the manifest.

Artifacts of interest:

```
build/zephyr/zephyr.elf   # use this for flashing — OpenOCD reads load addresses from it directly
build/zephyr/zephyr.bin   # raw binary, matches reported FLASH usage exactly
```

### 3.4 Transfer to the board

```bash
adb push build/zephyr/zephyr.elf /tmp/zephyr.elf
adb shell "ls -la /tmp/zephyr.elf"   # confirm byte count matches the local file
```

### 3.5 Flash via SWD

```bash
adb shell "cd /opt/openocd && ./bin/openocd -f openocd_gpiod.cfg -c 'program /tmp/zephyr.elf verify reset exit'"
```

Expected tail of output on success:

```
** Programming Started **
Info : device idcode = 0x30076482 (STM32U57/U58xx - Rev U : 0x3007)
Info : TZEN = 0 : TrustZone disabled by option bytes
...
** Programming Finished **
** Verify Started **
** Verified OK **
** Resetting Target **
```

> **Cosmetic, non-fatal warning:** you may see `Error: Translation from khz to adapter speed not implemented` and `Execution of event reset-init failed` partway through. This comes from the `linuxgpiod` bit-bang driver not supporting OpenOCD's clocked-adapter-speed abstraction during an optional reset-init hook. It does not affect programming or verification — confirm success by the `** Verified OK **` line and the actual `** Programming Finished **` / `** Resetting Target **` markers, not by the absence of any warning text.

### 3.6 Verify

For `samples/basic/blinky`, the simplest verification is visual: the board's LED should be blinking immediately after the reset. Also confirm the Linux side survived the SWD operation untouched:

```bash
adb devices   # should still show the board as "device"
```

---

## 4. Operational note: reclaiming the UART after flashing your own image

`arduino-router.service` runs by default on the Linux side and owns `/dev/ttyHS1`, the UART wired to the STM32U585, for its MessagePack-RPC Bridge protocol. Once you flash your own Zephyr image — which does not speak that protocol — this service has no peer to talk to and will hold the UART, blocking your own console/printk access over that same line.

To free it for your own use:

```bash
adb shell "systemctl stop arduino-router"
# or, to prevent it from restarting on every boot:
adb shell "systemctl disable arduino-router"
```

To restore the board to stock Arduino sketch-upload behavior later (e.g. before returning a loaned board, or switching back to App Lab development), the Arduino-provided recovery command is:

```bash
adb shell "arduino-cli burn-bootloader -b arduino:zephyr:unoq -P jlink"
```

---

## 5. Summary

| Layer | What we used | Notes |
|---|---|---|
| Transport | USB → ADB → QRB2210 Linux userspace | No external programmer/debugger needed |
| Debug link | OpenOCD `linuxgpiod` driver, bit-banged SWD | GPIO chip 1, lines 25 (SWDIO) / 26 (SWCLK) / 38 (SRST/TRST) |
| Target | STM32U585 (Cortex-M33 @ 160 MHz) | Confirmed via SWD `idcode 0x30076482` |
| Toolchain | Zephyr SDK 1.0.1, `arm-zephyr-eabi-gcc` 14.3.0 | Installed via `west sdk install` |
| Board target | `arduino_uno_q` (upstream Zephyr `main`, requires ≥ 4.3.0) | Official board support, confirmed present in mainline |
| Flash command | `openocd -f openocd_gpiod.cfg -c 'program <elf> verify reset exit'` | Manual; not yet integrated into `west flash` upstream |

This procedure gives full, unmediated control of the STM32U585 — the foundation for running our own real-time DSP/inference firmware on it, independent of Arduino's sketch/RPC runtime.
