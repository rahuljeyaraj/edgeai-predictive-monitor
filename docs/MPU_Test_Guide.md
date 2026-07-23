# MPU Test Guide

How to run the QRB2210-side (MPU-side) scripts that verify MCU peripherals,
per the milestone table in [MCU_Software_Architecture.md](MCU_Software_Architecture.md) §9.

Covers the four scripts in `mpu/tests/` (milestone verification, explicit
pass/fail) plus `mpu/tools/spectrum_server.py` (debug tool, no pass/fail).

## Common setup

All scripts import `mpu/common/wire_protocol.py`. Push the whole `mpu/`
tree once, preserving structure, then point `PYTHONPATH` at `mpu/common`
when running any script:

```
adb push mpu /home/arduino/mpu
```

**Gotcha**: if `/home/arduino/mpu` already exists on-device, `adb push`
nests the source folder one level deeper
(`/home/arduino/mpu/mpu/...`) instead of overwriting in place, silently
leaving the old code running. When re-pushing after local changes, wipe
the destination first:

```
adb shell "rm -rf /home/arduino/mpu"
adb push mpu /home/arduino/mpu
```

```
adb shell "PYTHONPATH=/home/arduino/mpu/common python3 /home/arduino/mpu/<tests-or-tools>/<script>.py [args]"
```

Only one script at a time — they all want exclusive access to
`/dev/ttyHS1`.

---

## `uart_protocol_test.py`

- **Verifies**: Milestone 2 — bidirectional wire-protocol test over
  LPUART1. MCU→MPU direction (SPECTRUM frames arriving) and MPU→MCU
  direction (DISPLAY_RGB / DISPLAY_MATRIX frames sent and CRC-accepted).
- **Prerequisites**: MCU flashed to at least Milestone 2. Board connected
  via `/dev/ttyHS1`.
- **Push**:
  ```
  adb push mpu /home/arduino/mpu
  ```
- **Run** (default baud `115200`):
  ```
  adb shell "PYTHONPATH=/home/arduino/mpu/common python3 /home/arduino/mpu/tests/uart_protocol_test.py [baud]"
  ```
- **Pass/fail**: exact terminal output:
  ```
  RESULT: PASS - both directions exercised
  ```
  Anything else (`RESULT: FAIL`) is a failure. Also cross-check the MCU's
  USART1 debug log for matching `RX frame type=0x02 ...` / `type=0x03 ...`
  lines — this script can't itself confirm the MCU parsed DISPLAY_*
  payload fields correctly, only that a CRC-valid frame of that type
  arrived.

---

## `uart_large_transfer_test.py`

- **Verifies**: Milestone 2 — DMA-backed LPUART1 link moves a full
  `MAX_PAYLOAD`-sized SPECTRUM frame correctly and byte-exact (not just
  CRC-valid), and throughput hasn't regressed.
- **Prerequisites**: MCU flashed to at least Milestone 2. Board connected
  via `/dev/ttyHS1`. MCU sends its deterministic ~8KB test SPECTRUM frame
  every ~2s (`send_spectrum_test()` in `mcu/src/main.c`).
- **Push**:
  ```
  adb push mpu /home/arduino/mpu
  ```
- **Run** (default baud `115200`):
  ```
  adb shell "PYTHONPATH=/home/arduino/mpu/common python3 /home/arduino/mpu/tests/uart_large_transfer_test.py [baud]"
  ```
- **Pass/fail**: exact terminal output:
  ```
  RESULT: PASS - 8KB DMA transfer verified byte-exact
  ```
  Anything else (`RESULT: FAIL`) is a failure — check the printed failure
  reasons list for which bins mismatched or whether no frame arrived.

---

## `display_rgb_test.py`

- **Verifies**: Milestone 3 — external common-anode RGB LED (D3/D5/D6,
  `mcu/src/drivers/rgb_pwm.c`) driven via DISPLAY_RGB commands
  (CONST/BREATHE/STROBE modes, varying color/period).
- **Prerequisites**: MCU flashed to at least Milestone 3. Board connected
  via `/dev/ttyHS1`. RGB LED physically visible.
- **Push**:
  ```
  adb push mpu /home/arduino/mpu
  ```
- **Run** (default baud `4000000`):
  ```
  adb shell "PYTHONPATH=/home/arduino/mpu/common python3 /home/arduino/mpu/tests/display_rgb_test.py [baud]"
  ```
- **Pass/fail**: no automatic result — this script only sends, it can't
  see the LED. Visually confirm on hardware:
  - Solid colors for CONST mode
  - Smooth sine-wave brightness fade for BREATHE mode
  - Hard on/off square wave for STROBE mode
  Cross-check the MCU's USART1 debug log for matching lines:
  ```
  RX DISPLAY_RGB rgb=0x%06x mode=%u period_ms=%u
  ```

---

## `display_matrix_test.py`

- **Verifies**: Milestone 4 — onboard 8x13 LED matrix driven via
  DISPLAY_MATRIX commands with varying text. First step ("HI", static) is
  also the key check for `mcu/src/drivers/led_matrix.c`'s
  framebuffer-bit-index-to-physical-position assumption.
- **Prerequisites**: MCU flashed to at least Milestone 4. Board connected
  via `/dev/ttyHS1`. LED matrix physically visible.
- **Push**:
  ```
  adb push mpu /home/arduino/mpu
  ```
- **Run** (default baud `4000000`):
  ```
  adb shell "PYTHONPATH=/home/arduino/mpu/common python3 /home/arduino/mpu/tests/display_matrix_test.py [baud]"
  ```
- **Pass/fail**: no automatic result — this script only sends, it can't
  see the matrix. Visually confirm on hardware:
  - "HI" renders correctly, static, left-to-right, top-aligned (not
    scrambled/rotated — that would indicate the bit-index assumption is
    wrong)
  - "OK 8x13" renders correctly
  - "HELLO EPM 123" scrolls correctly, left-to-right
  Cross-check the MCU's USART1 debug log for matching lines:
  ```
  RX DISPLAY_MATRIX text="..." scroll_speed_ms=%u
  ```

---

## `spectrum_server.py` (tool, not a test)

- **What it does**: debug-only live spectrum viewer. Reads fused
  SPECTRUM frames off LPUART1 on the MPU side, serves a live
  frequency-axis plot over HTTP to any browser on the same LAN. No
  auth/TLS, no history — single in-memory "latest frame" snapshot. Not a
  milestone verification step.
- **Prerequisites**: MCU flashed and streaming SPECTRUM frames. Board
  connected via `/dev/ttyHS1`. QRB2210 has a WiFi/LAN address reachable
  from the viewing machine.
- **Push**:
  ```
  adb push mpu /home/arduino/mpu
  ```
- **Run** (defaults: serial port `/dev/ttyHS1`, baud `4000000`, HTTP port
  `8000`):
  ```
  adb shell "PYTHONPATH=/home/arduino/mpu/common python3 /home/arduino/mpu/tools/spectrum_server.py"
  ```
  Override with `--serial-port`, `--baud`, `--http-port` if needed. Stop
  with Ctrl+C when done.
- **How to confirm it's working** (not pass/fail):
  - Terminal prints `Reading SPECTRUM frames from ... @ ... baud` and
    `Serving on http://<ip>:8000 ...`
  - Open `http://<ip-printed-on-startup>:8000` from a browser on the same
    LAN
  - Plot updates live; frame count shown in the UI increases over time
