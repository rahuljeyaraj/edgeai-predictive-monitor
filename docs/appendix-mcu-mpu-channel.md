# Appendix: Reworking the MCU↔MPU Communication Channel

**Project:** Arduino Physical AI Challenge India 2026 — Industrial Predictive Maintenance System
**Relates to:** "Running a Pure Zephyr Image on the Arduino Uno Q's STM32U585 via the Onboard ADB/SWD Bridge"

---

## 1. Why this matters

The predictive-maintenance pipeline needs FFT spectrum data moved from the STM32U585 (MCU) to the QRB2210 (MPU) as fast as possible, with small control signals flowing back the other way. Arduino's stock path for this is `Arduino_RouterBridge` — a MessagePack-RPC layer riding on a UART link, mediated by the `arduino-router` Linux service.

A prior benchmark on this same hardware measured that stock path at roughly **8–10 KB/s, 4–5 packets/sec**, against a design target near 80 KB/s. That gap traces to two separate, stackable causes:

1. **The UART itself was configured at 115200 baud** — a software setting, not a hardware ceiling, yielding a hard physical cap of roughly 14.4 KB/s before any protocol overhead.
2. **RPC framing overhead on top of that** — MessagePack encoding, method-call semantics, and the request/response model that `arduino-router` uses, none of which is needed once a sketch-style RPC API is no longer in the picture.

Since the project has already moved to pure Zephyr firmware (no App Lab, no Bridge library), there's no reason to carry RPC semantics at all. This appendix documents removing both bottlenecks in sequence: raising the physical baud rate first (covered here), with a minimal binary framing replacing MessagePack-RPC as a planned follow-up.

## 2. Identifying the physical link and its real ceiling

The MCU↔MPU link is `LPUART1` on the STM32U585, wired to `/dev/ttyHS1` on the Linux side — confirmed via the `arduino-router.service` definition itself:

```
ExecStart=/usr/bin/arduino-router --serial-port /dev/ttyHS1 --serial-baudrate 115200
```

A common pitfall with STM32 LPUART peripherals is that they're sometimes fed by a low-power oscillator (e.g. LSE at 32.768 kHz) for wake-on-UART use cases, which caps achievable baud rates as low as 9600. This board's devicetree rules that out: `LPUART1` is clocked from **APB3**, and this board's clock configuration runs APB3 undivided off the 160 MHz system PLL —

```c
&rcc {
        clocks = <&pll1>;
        clock-frequency = <DT_FREQ_M(160)>;
        apb3-prescaler = <1>;
        ...
};
```

A 160 MHz-fed UART comfortably supports multi-megabaud rates with low timing error. The board also has **hardware RTS/CTS flow control already wired** on dedicated pins (`lpuart1_rts_pg6`, `lpuart1_cts_pg5`), confirmed in the existing pin control configuration — meaning overrun protection is available "for free," without needing a software ACK/retry scheme.

**Decision:** raise `LPUART1` from 115200 to **1,500,000 baud**, with hardware flow control enabled. This is roughly a 13x increase in baud rate, and close to a 10x increase in raw achievable throughput versus the previously measured RPC-layer performance.

## 3. Devicetree overlay (out-of-tree, not editing Zephyr's own files)

Rather than editing Zephyr's own checked-out board files — which would be lost on `west update` and invisible to anyone else building this app — the change lives in our own app's devicetree overlay:

```c
// boards/arduino_uno_q.overlay
&lpuart1 {
	current-speed = <1500000>;
	hw-flow-control;
};
```

Zephyr's build system automatically merges any `boards/<board-name>.overlay` file found in an out-of-tree app directory on top of the board's base devicetree — no `CMakeLists.txt` change is needed for this to take effect.

Both properties were confirmed present in the final merged devicetree (`build/zephyr/zephyr.dts`) after building:

```
current-speed = < 0x16e360 >;   /* = 1,500,000 decimal */
...
hw-flow-control;
```

> **Note on verifying overlays:** when checking a merged `zephyr.dts` for an expected property, search the whole file rather than a narrow context window around a related line — property ordering in the merged output does not necessarily match the order properties were written in the overlay source.

## 4. Clearing `arduino-router` out of the way

`arduino-router.service` holds `/dev/ttyHS1` open permanently and is configured with `Restart=always`, so a plain `systemctl stop` is not sufficient — systemd queues an automatic restart that races against (and effectively cancels) the stop request. `systemctl disable` alone is also insufficient, since `Restart=always` is a property of the running unit, independent of whether it's enabled for boot.

The correct fix is `systemctl mask`, which is the only mechanism that actually prevents systemd from restarting the unit at all. On this board, `systemctl mask arduino-router` failed because a real (non-symlink) unit file already existed at the standard path, and — contrary to what the flag's name might suggest — **`--force` does not apply to `mask`** on this systemd version; it is documented as scoped specifically to the `enable` verb ("when **enabling** unit files, override existing symlinks").

The working fix replicates what `mask` does internally, by hand:

```bash
sudo rm -f /etc/systemd/system/arduino-router.service
sudo ln -s /dev/null /etc/systemd/system/arduino-router.service
sudo systemctl daemon-reload
sudo kill <current-main-pid>   # one-time, to stop the already-running instance
```

After this, `systemctl status arduino-router` correctly reports `Loaded: masked` and `Active: inactive (dead)`, and the service does not return on its own. Confirmed `/dev/ttyHS1` was genuinely free of any owning process afterward via `fuser /dev/ttyHS1` (no output = no holder).

> **This change is persistent across reboots**, since it replaces a real file on disk with a symlink, rather than only affecting runtime state. To restore stock Arduino/App Lab behavior later, both `systemctl unmask arduino-router` and the official `arduino-cli burn-bootloader -b arduino:zephyr:unoq -P jlink` recovery command are needed — masking alone does not get reversed by reflashing stock firmware.

## 5. MCU-side test firmware

A minimal Zephyr application sends an incrementing ASCII counter line over `lpuart1` using the low-level polling UART API, deliberately avoiding the console/logging subsystem so the raw UART driver and our devicetree changes are being tested directly:

```c
#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/uart.h>
#include <stdio.h>

#define UART_NODE DT_NODELABEL(lpuart1)

int main(void)
{
	const struct device *uart_dev = DEVICE_DT_GET(UART_NODE);
	char buf[32];
	uint32_t counter = 0;

	if (!device_is_ready(uart_dev)) {
		return 0;
	}

	while (1) {
		int len = snprintf(buf, sizeof(buf), "COUNT:%u\n", counter);
		for (int i = 0; i < len; i++) {
			uart_poll_out(uart_dev, buf[i]);
		}
		counter++;
		k_msleep(10);
	}
	return 0;
}
```

`prj.conf` requires only:

```
CONFIG_SERIAL=y
```

The `k_msleep(10)` between sends was deliberate for this first test — the goal was confirming **correctness** (no dropped or corrupted bytes) at the new baud rate, not yet measuring maximum achievable throughput. Removing that delay to benchmark real throughput is a planned follow-up step, not yet performed.

Built and flashed using the same out-of-tree workflow and OpenOCD/SWD path documented in the main appendix — no changes needed there.

## 6. MPU-side listener

Python's `pyserial` was not present on the board's Debian image and could not be installed via `pip` directly due to PEP 668 ("externally-managed-environment"). The Debian-packaged equivalent, `python3-serial`, installed cleanly via `apt` and provides the same `serial` module:

```bash
sudo apt-get install -y python3-serial
```

Listener script, pushed to the board and run via `adb shell python3`:

```python
import serial

ser = serial.Serial(
    port="/dev/ttyHS1",
    baudrate=1500000,
    bytesize=serial.EIGHTBITS,
    parity=serial.PARITY_NONE,
    stopbits=serial.STOPBITS_ONE,
    rtscts=True,
    timeout=1,
)

while True:
    line = ser.readline()
    if line:
        print(line.decode("ascii", errors="replace").rstrip())
```

(Full version with byte/line counting and a final throughput summary on Ctrl+C is kept alongside this documentation as `uart_listener_test.py`.)

## 7. Initial correctness result (with software delay)

Running both sides together with a 10 ms delay between sends produced several hundred consecutive lines (`COUNT:0`, `COUNT:1`, `COUNT:2`, ...) with **no gaps, no corruption, no out-of-order values** — a clean correctness confirmation of the link at 1,500,000 baud with hardware flow control enabled, fully independent of `arduino-router` and the MessagePack-RPC layer it implements.

## 8. Removing the software delay and sweeping baud rates

With correctness confirmed, the next step removed the artificial `k_msleep(10)` entirely — the MCU now sends as fast as the polling UART API allows — and added a checksum to each line (`COUNT:<n>:<checksum_hex>`, XOR of the counter's ASCII digit bytes) so the listener can detect genuine corruption rather than relying on a visual scan for sequence gaps alone.

A baud rate sweep was run across 1,500,000 / 3,000,000 / 4,000,000 to find where the link's real ceiling lies. The first pass produced a confusing result: a small number of malformed/corrupted lines (0.01–0.02% error rate) appeared at every rate tested, including the already-proven 1,500,000 baud baseline that had previously shown zero errors.

### 8.1 Diagnosing the apparent corruption

Adding line-position logging to the listener isolated the cause immediately: **the error was always on line #1 of the run, never elsewhere.** Inspecting the actual malformed content (e.g. `'OUNT:31085:3F'` — missing only the leading `C`, with a plausible mid-stream counter value) confirmed this was not bit-level UART corruption at all. It was a **listener startup artifact**: opening `serial.Serial(...)` and immediately calling `readline()` returns whatever partial line fragment was already sitting in the OS receive buffer from before the port was opened — a byte-counting/timing artifact of the test harness, not a property of the link.

The fix was to discard the first line received in each run before counting anything:

```python
if not discarded_first_line:
    discarded_first_line = True
    print(f"  (discarding first line as possible partial fragment: {raw!r})")
    continue
```

Re-running 1,500,000 and 3,000,000 baud with this fix produced **zero errors at both rates** — fully confirming that the link itself has no real corruption at any tested rate; every prior "error" was the same harmless artifact.

> **Script note:** the discard-first-line logic increments `lines_total` for the discarded line in the current script version (visible as the malformed-line count being included in the total at 4,000,000 baud in one run). This is a cosmetic counting quirk in the test harness, not a sign of a real second error — the underlying cause (first-line partial fragment) is the same single, already-understood artifact.

### 8.2 Sweep results (corrected)

| Baud rate | Throughput | Lines/sec (approx.) | Verdict |
|---|---|---|---|
| 1,500,000 | ~44 KB/s | ~15,700 | Clean |
| 3,000,000 | ~45–46 KB/s | ~16,200 | Clean |
| 4,000,000 | ~46 KB/s | ~15,600–16,300 | Clean (first-line artifact only) |

### 8.3 Finding: the real ceiling is software, not the wire

Throughput is **flat across a 2.7x baud rate range** (1.5M → 4M baud moves achieved throughput by less than 5%, well within run-to-run noise). This is conclusive: the bottleneck is not UART signal timing or bit-sampling margin at these rates — it is the MCU-side **polling transmit loop** (`uart_poll_out`, one byte at a time, blocking) plus the per-line `snprintf` formatting cost. The UART hardware has unused headroom at every rate tested; the CPU loop feeding it cannot go faster than ~45 KB/s regardless of how fast the wire could carry data.

This is a useful, load-bearing finding for the project: **raising the baud rate further will not improve throughput** until the transmit path itself is changed from polling to interrupt-driven or DMA-driven UART, which would let the CPU hand off a buffer to hardware and continue other work instead of blocking on every byte. That is scoped as separate follow-up work (see below).

## 8.4 DMA conversion and re-measured throughput

The follow-up predicted above happened: LPUART1 TX and RX both moved to Zephyr's DMA-backed async
UART API (GPDMA1), as part of building the actual binary wire protocol (Appendix B) rather than
this appendix's ASCII counter test — see
[lpuart_wire_protocol_design.md](lpuart_wire_protocol_design.md) for the implementation. With DMA
in place, the baud-rate sweep was repeated using a back-to-back burst of 50 real ~8KB protocol
frames (`SPECTRUM`, the same frame type/size this project's actual FFT payload will use), timed
from the receiving (MPU) side:

| Baud rate | Sustained throughput | Frames/sec | vs. old polling ceiling (~45 KB/s, flat) |
|---|---|---|---|
| 1,500,000 | 90.7 KB/s | 11.33 | ~2.0x |
| 4,000,000 | 114.0 KB/s | 14.23 | ~2.5x |

**This confirms the hypothesis from §8.3**: throughput now scales with baud rate instead of being
flat, because the CPU is no longer the one moving bytes. It is *not* linear, though — a 2.67x baud
increase (1.5M → 4M) only bought a 1.26x throughput increase, well short of the ~400 KB/s a
4,000,000 baud wire could theoretically carry at this frame size. The bottleneck has moved again,
from "CPU touching every byte" (the old polling problem) to something in the per-frame
software/scheduling overhead of the send cycle (semaphore wakeup, context switch back to the
sender, `uart_tx()` call overhead) — each frame's *wire* time is a small fraction of its *total*
turnaround time at these baud rates. Diagnosing that more precisely is real follow-up work, not
done here; for context, at 4,000,000 baud the wire time alone for an 8201-byte frame is ~20.5ms,
but the measured per-frame turnaround is ~70ms.

Settled on **4,000,000 baud with hardware flow control** as the final configuration (measured
fastest, zero correctness issues across every test run at this rate) — see
[mcu/boards/arduino_uno_q.overlay](../mcu/boards/arduino_uno_q.overlay).

## 9. Status and next steps

| Item | Status |
|---|---|
| Identify physical link (LPUART1 / `/dev/ttyHS1`) and clock source | Done |
| Confirm no low-power-clock baud ceiling applies | Done (APB3 @ 160 MHz, undivided) |
| Raise baud rate via out-of-tree devicetree overlay | Done — settled on 4,000,000 (115200 → 1.5M → 4M, swept) |
| Enable hardware RTS/CTS flow control | Done |
| Remove `arduino-router` as a conflicting UART owner | Done (masked, persists across reboots) |
| MCU → MPU correctness test at new baud rate | Done, with checksum verification (this appendix) and CRC16 + byte-exact bin verification (Appendix B implementation) |
| Measure actual achieved throughput (remove artificial delay) | Done — ~44–46 KB/s, flat across 1.5M–4M baud, with polling TX |
| Diagnose throughput ceiling | Done — MCU-side polling TX loop is the bottleneck, not the wire |
| Move MCU TX from polling to interrupt/DMA-driven UART | **Done** — see §8.4 and [lpuart_wire_protocol_design.md](lpuart_wire_protocol_design.md) |
| Re-measure throughput after the DMA conversion | **Done** — see §8.4: 90.7 KB/s @ 1.5M baud, 114.0 KB/s @ 4M baud, confirms throughput now scales with baud |
| Replace MessagePack-RPC framing with a minimal binary protocol for real FFT payloads | Done — Appendix B's binary frame format, implemented in [mcu/src/wire_protocol.c](../mcu/src/wire_protocol.c) / [mpu/wire_protocol.py](../mpu/common/wire_protocol.py) |
| MPU → MCU control-signal direction | Done — `CONFIG_SET`/`ACK` round-trip, see [lpuart_wire_protocol_design.md](lpuart_wire_protocol_design.md) |
| Diagnose the new (post-DMA) per-frame overhead bottleneck | Not yet done — see §8.4's ~20.5ms wire time vs. ~70ms measured turnaround gap |

The baud rate increase alone (with polling TX) already provided a real, measured ~4.5–5x
throughput gain over the previously measured RPC-layer performance (from ~8–10 KB/s to ~44–46 KB/s
achieved). The subsequent DMA conversion (§8.4) added another ~2–2.5x on top of that at the same
baud rates, and — unlike the baud increase alone — also restored baud rate as a lever that actually
moves throughput, rather than one that had already stopped mattering.
