# MCU↔MPU Communication Architecture Decision

**Project:** EdgeAI Predictive Monitor (EPM) — Team DragonWing
**Scope:** STM32U585 (MCU) ↔ QRB2210 (MPU) link design on the Arduino UNO Q base station

---

## 1. Context / Problem

- Original plan: use LPUART1 for MCU↔QRB2210 data transfer, with no separate path
  left for debug logs.
- Investigated using SPI3 as an additional/alternate data path to free up LPUART1
  for logging.

---

## 2. SPI3 Investigation (explored, ultimately not used)

- QRB2210's SPI controller (Qualcomm QUP / GENI-based) is **master-only** in Linux.
  Confirmed via kernel driver source and Kconfig docs — no slave-mode driver exists
  for Qualcomm SPI peripherals (`spi-qup.c`, `spi-geni-qcom.c` both master-only).
- Therefore STM32U585 would need to run as **SPI slave** (Zephyr does support this).
- Found a real reliability constraint: Zephyr's STM32 SPI slave + DMA requires the
  slave's DMA buffer to be pre-armed *before* the master clocks data in/out.
  Community reports show this becoming unreliable above ~10 MHz and corrupted by
  ~24 MHz. Async slave-mode DMA is not well supported upstream in Zephyr's STM32
  SPI driver (only synchronous DMA transfers are supported).
- Because an SPI slave cannot initiate a transfer, a **DATA_READY** signal
  (slave → master) would have been required to tell the QRB2210 when a new frame
  was available. Explored options:
  - Dedicated GPIO jumper wire (e.g. via JMISC), requiring level-shifting since
    MCU I/O is 3.3 V and MPU GPIO is 1.8 V.
  - SPI "sentinel byte" polling (master does cheap peek reads to detect readiness).
  - Routing a lightweight ready-marker over UART instead of a new GPIO.
- **Decision:** Dropped SPI3 entirely. Added hardware complexity (slave-mode DMA
  fragility, DATA_READY signaling) outweighed the throughput benefit, especially
  once the LPUART1-only throughput was shown to plausibly support real-time rates
  with DMA (see §4).

---

## 3. Final Architecture

| Link | Direction | Purpose |
|---|---|---|
| **USART1** (JDIGITAL D0/D1, PB7/PB6) | MCU → Host PC | Dedicated debug logs, via USB-UART dongle straight to a host PC terminal (e.g. PuTTY). Fully decoupled from the QRB2210 — logs never touch Linux side. |
| **LPUART1** | Bidirectional, MCU ↔ MPU | Carries both: **MCU → MPU** sensor data frames (FFT/spectrum payloads); **MPU → MCU** control packets (e.g. sampling-rate changes, mode switches, commands). |

- No SPI3, no dedicated DATA_READY GPIO needed — UART is inherently bidirectional.
  The MCU sends data frames whenever a new frame is ready; the MPU sends control
  packets independently whenever needed. No master/slave asymmetry to work around.
- A single binary wire protocol and parser handles both data and control message
  types on both ends, distinguished by the existing `TYPE` field:
  `[SYNC: 0xAA55][VER][TYPE][NODE_ID][LEN][PAYLOAD][CRC16]`

---

## 4. Throughput Analysis

- **FFT frame size:** 512 bins × 2 sensors × 4 bytes (float32) = **4096 bytes/frame**
- **Current LPUART1 ceiling:** ~44–46 KB/s, regardless of baud rate (tested
  1.5M–4M baud, same ceiling each time).
  - Root cause confirmed: CPU-bound, byte-at-a-time `uart_poll_out()` on the MCU
    TX path — not a baud-rate or wire limitation.
- **At current ceiling:** 4096 bytes ÷ ~45,000 B/s ≈ 0.091 s/frame → **~11
  frames/sec**.
  - Whether this is sufficient depends on the actual required FFT update rate
    (driven by KX134 sample rate and FFT window/hop size) — **not yet confirmed**.
    Vibration-based fault detection (bearing wear, cavitation, micro-pitting)
    often tolerates 1–10 Hz update rates since fault signatures are persistent,
    not transient, so 11 fps may be adequate even without further optimization.
- **DMA-backed UART:** Expected to remove the CPU-bound bottleneck and approach
  baud-rate-limited throughput, plausibly several times faster than the current
  ceiling. This is a reasonable expectation given the confirmed root cause, but
  **must be verified empirically** after implementation — not assumed.

---

## 5. Open / Next Steps

- [ ] Confirm actual target FFT frame rate (from KX134 sample rate + FFT hop size).
- [ ] Convert LPUART1 from `uart_poll_out()` (polling) to DMA-backed async UART API
      in Zephyr.
- [ ] Re-measure real LPUART1 throughput after the DMA conversion.
- [ ] Confirm USART1 pins (PB6/PB7 on JDIGITAL) are free and not claimed elsewhere
      in the current build.
- [ ] Confirm a 3.3 V-capable USB-UART dongle is used for the debug log link (not
      a 5 V-only dongle — JDIGITAL is 3.3 V logic).
- [ ] Define/confirm `TYPE` values in the wire protocol for control packets
      (MPU → MCU direction) alongside existing data-frame types.
