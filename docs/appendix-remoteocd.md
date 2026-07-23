# Appendix: Flashing via `remoteocd` (Host-Side Alternative)

**Project:** Arduino Physical AI Challenge India 2026 — Industrial Predictive Maintenance System
**Relates to:** "Running a Pure Zephyr Image on the Arduino Uno Q's STM32U585 via the Onboard ADB/SWD Bridge"

---

## 1. What `remoteocd` is

`remoteocd` ([github.com/arduino/remoteocd](https://github.com/arduino/remoteocd)) is Arduino's own wrapper around OpenOCD, built specifically for the Uno Q's split architecture. It is the same tool `arduino-cli upload` calls internally when programming the STM32U585 from App Lab — we are simply invoking it ourselves, against our own Zephyr binary, the same way we did with raw OpenOCD in the main appendix.

It supports three transparently handled modes:

- **Local** — run directly on the Uno Q's own Linux (MPU) side.
- **ADB over USB** — run from a host PC, tunneling the flash command to the board over `adb`.
- **SSH over a remote PC** — run from a host PC with no USB connection at all, over the network.

That third mode is `remoteocd`'s real value over raw OpenOCD: it is the only one of the two approaches that supports flashing a board with no USB cable attached.

For our development loop — one board, one USB cable, one host — `remoteocd` does not offer a functional advantage over invoking OpenOCD directly via `adb shell` (documented in the main appendix). We document it here as a verified, working alternative, and note the non-obvious setup steps it requires.

## 2. Installation (host side)

`remoteocd` is not distributed via a general package manager — it is a Go binary published as GitHub release assets, and normally installed automatically as a tool dependency of the `arduino:zephyr:unoq` platform when using `arduino-cli`. To use it standalone:

```bash
cd ~/workspace/zephyrproject
curl -L -o remoteocd.tar.gz \
  https://github.com/arduino/remoteocd/releases/download/0.1.0/remoteocd-0.1.0-linux-amd64.tar.gz
tar -xzf remoteocd.tar.gz
mv remoteocd-0.1.0-linux-amd64/remoteocd ./remoteocd
chmod +x ./remoteocd
rmdir remoteocd-0.1.0-linux-amd64
```

> **Version note:** the latest tagged GitHub release at the time of writing is `0.1.0`, while the version preinstalled on the board itself was `0.1.1`. The CLI surface was identical between the two for our purposes; if you hit unexpected flag differences, check the board's installed version with `adb shell find / -iname remoteocd` and compare against the release you downloaded.

Verify the binary actually runs on your host (architecture mismatches are silent failures otherwise):

```bash
file ./remoteocd      # should report a native ELF executable for your host arch
./remoteocd version
```

## 3. Required local config files

This is the first non-obvious step: **`remoteocd`'s `-f` flag expects config files that exist locally on the host**, not paths on the board. The OpenOCD config files that already exist on the Uno Q (`/opt/openocd/openocd_gpiod.cfg` and the target scripts it sources) must be pulled to the host first if you intend to reference or extend them:

```bash
mkdir -p openocd_cfg
adb pull /opt/openocd/openocd_gpiod.cfg openocd_cfg/
adb pull /opt/openocd/stm32u5x.cfg openocd_cfg/
adb pull /opt/openocd/stm32x5x_common.cfg openocd_cfg/
```

## 4. The double-config-load pitfall

`remoteocd` **always** prepends the board's own resident `openocd_gpiod.cfg` to the OpenOCD invocation it builds, regardless of what you pass via `-f`. This is visible in its `--verbose` output:

```
Running command: /opt/openocd/bin/openocd -d2 -s /opt/openocd -s /opt/openocd/share/openocd/scripts \
  -f openocd_gpiod.cfg -c set filename /tmp/remoteocd/sketch.elf-zsk.bin \
  -f /tmp/remoteocd/<your-file>.cfg
```

If the file you supply via `-f` *also* sources `openocd_gpiod.cfg` (directly, or indirectly via a copy of it), OpenOCD will attempt to create the same debug-access-port object twice and fail:

```
/opt/openocd/stm32x5x_common.cfg:73: Error: Command: stm32u5.dap Exists
```

**The fix:** your custom `-f` file must assume the adapter and target are already configured by the board's default config. It should contain only the additional commands you actually need — in our case, the flashing sequence itself:

```tcl
# openocd_cfg/flash.cfg
init
program $filename verify reset exit
```

Do **not** add a `source [find openocd_gpiod.cfg]` (or any equivalent) line to this file — that line is exactly what causes the duplicate-DAP error described above.

The `$filename` variable is pre-populated by `remoteocd` itself via the `-c set filename ...` argument it injects automatically — you do not set it yourself.

## 5. Running the flash

```bash
./remoteocd upload <path-to-your.elf> \
  -f openocd_cfg/flash.cfg \
  -s <board-usb-serial> \
  --adb-path "$(which adb)" \
  --verbose
```

Notes on the flags:

- `<board-usb-serial>` — obtain via `adb devices`.
- `--adb-path "$(which adb)"` — **explicitly required** in our testing. Omitting this flag and relying on `remoteocd`'s "try to find it" auto-detection produced an opaque failure (`Error: makedir error: exec: no command`) even though `adb` was correctly on `PATH` and resolvable via `which adb`. Always pass this flag explicitly.
- `--verbose` — strongly recommended. Without it, `remoteocd` does not show the underlying OpenOCD invocation or its output, making the failure modes above effectively undiagnosable.

A successful run produces output identical to a direct OpenOCD invocation, since under the hood it is the same binary:

```
** Programming Started **
Info : device idcode = 0x30076482 (STM32U57/U58xx - Rev U : 0x3007)
...
** Programming Finished **
** Verify Started **
** Verified OK **
** Resetting Target **
```

The same cosmetic, non-fatal warning noted in the main appendix may appear (`Error: Translation from khz to adapter speed not implemented` / `Execution of event reset-init failed`) — it does not affect the actual programming or verification result.

## 6. Summary: when to reach for which tool

| | Raw OpenOCD via `adb shell` | `remoteocd` from host |
|---|---|---|
| Extra dependencies | None (uses what's already on the board) | One additional binary on the host |
| Setup steps | None | Download binary, pull 3 config files, write a custom flash-only cfg |
| Works with no USB cable (SSH mode) | No | Yes |
| Matches Arduino's own internal tooling | No (we drive OpenOCD ourselves) | Yes (same binary App Lab uses) |
| Gotchas encountered | None | Double-config-load on `-f`; silent `adb`-path auto-detection failure |

For a single-developer, single-board, USB-connected workflow, the direct OpenOCD command documented in the main appendix is simpler and has fewer moving parts:

```bash
adb shell "cd /opt/openocd && ./bin/openocd -f openocd_gpiod.cfg -c 'program /tmp/zephyr.elf verify reset exit'"
```

`remoteocd` is worth adopting if the project later needs network/SSH-based flashing, or if integrating with Arduino's own CLI/`west flash` tooling becomes a priority.
