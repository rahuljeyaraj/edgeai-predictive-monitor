# LPUART1 Wire Protocol — Design and Implementation Reference

**Relates to:**
- [mcu_mpu_comms_architecture.md](mcu_mpu_comms_architecture.md) — why LPUART1 carries MCU↔MPU data/control
  and USART1 is reserved for debug logs.
- [Appendix_B_Wire_Protocol_Specification.md](Appendix_B_Wire_Protocol_Specification.md) — the
  transport-agnostic frame format and message types this document implements for the UART leg.
- [appendix-mcu-mpu-channel.md](appendix-mcu-mpu-channel.md) — the baud-rate/flow-control work this
  implementation's link settings build on, including the post-DMA throughput re-measurement (§8.4).

This document covers the concrete LPUART1 implementation: the concurrency model, the design
decisions Appendix B leaves unspecified, and a function-by-function reference for both sides of
the link.

- MCU side: [mcu/src/wire_protocol.h](../mcu/src/wire_protocol.h),
  [wire_protocol.c](../mcu/src/wire_protocol.c), [uart_link.h](../mcu/src/uart_link.h),
  [uart_link.c](../mcu/src/uart_link.c), [main.c](../mcu/src/main.c)
- MPU side: [mpu/wire_protocol.py](../mpu/common/wire_protocol.py),
  [uart_protocol_test.py](../mpu/tests/uart_protocol_test.py),
  [uart_large_transfer_test.py](../mpu/tests/uart_large_transfer_test.py)

This implementation went through two design stages, both documented here since the reasoning for
moving from the first to the second is worth keeping: an interrupt-driven-RX/polling-TX version
proved the protocol correct, then a DMA-backed async version replaced it once an 8KB
`SPECTRUM`-sized payload needed to move without per-byte CPU cost. The current code on disk is the
second (DMA) version — originally all in `main.c`, later split into a transport library
(`uart_link.h`/`.c`, the DMA/callback/buffer plumbing) and `main.c` (pure application behavior: what
to send, when, and what to do with what's received). That split was a pure refactor, not a design
change — confirmed by an identical RAM total before and after.

---

## 1. Why DMA-backed async, not interrupt-driven/polling

Appendix B's link characteristics (section 2) require true full duplex: the MCU must be able to
receive a `CONFIG_SET` or `COMMISSION_START` on RX *while* it is mid-stream sending `SPECTRUM` on
TX, with no contention. A blocking `uart_poll_in()` loop on RX would stall while the (much larger,
slower) TX side is busy, so RX has to be event-driven at minimum.

The first working version used Zephyr's interrupt-driven UART API for RX
(`CONFIG_UART_INTERRUPT_DRIVEN=y`) and left TX on the simple, blocking `uart_poll_out()` byte loop —
correct, and sufficient as long as every message was a handful of bytes (`HEARTBEAT`, `CONFIG_SET`,
`ACK`). It does **not** scale: `uart_poll_out()` has a fixed CPU cost per byte regardless of baud
rate, and the separate baud-rate/DMA appendix already measured this exact pattern capping real
throughput at ~45 KB/s on this UART peripheral family, flat across a 2.7x baud-rate range — i.e. the
CPU loop, not the wire, was the bottleneck.

Once `SPECTRUM` needed to carry a full ~8KB payload (not the multi-hundred-bin sketch the appendix
used), polling TX was no longer acceptable, so both directions moved to Zephyr's async UART API
(`CONFIG_UART_ASYNC_API=y`), DMA-backed via GPDMA1 (devicetree `dmas`/`dma-names` on `&lpuart1` in
[mcu/boards/arduino_uno_q.overlay](../mcu/boards/arduino_uno_q.overlay), using LPUART1's fixed
GPDMA1 hardware request lines — 34 for RX, 35 for TX, per
`LL_GPDMA1_REQUEST_LPUART1_RX`/`_TX` in the STM32U585 HAL). With this, the CPU touches a frame
once per `uart_tx()`/`UART_RX_RDY` call instead of once per byte, regardless of frame size.

LPUART1 was deliberately left at the board-default 115200 baud through the DMA conversion and its
hardware verification (§§1–7 below) — DMA removes the CPU-bound bottleneck, it does not by itself
change the wire's bit rate, so proving the DMA path correct first, before also changing the link
speed, kept the two variables separate. The baud rate was raised afterward, as its own change: see
[appendix-mcu-mpu-channel.md §8.4](appendix-mcu-mpu-channel.md#84-dma-conversion-and-re-measured-throughput)
for the measured results. Settled on **4,000,000 baud with hardware flow control** — current state
in [mcu/boards/arduino_uno_q.overlay](../mcu/boards/arduino_uno_q.overlay).

## 2. Concurrency model: async callback → queue → main thread

```
 uart_link.c (LPUART1 / GPDMA1)           main.c
 ───────────────────────────              ──────
 lpuart1_async_cb()
   UART_RX_RDY
     feed_rx_bytes()
       wp_parser_feed() per byte ──frame─▶  k_msgq (wp_rx_msgq, depth 2)
   UART_RX_BUF_REQUEST                          │
     uart_rx_buf_rsp() (ping-pong)               ▼
   UART_TX_DONE / UART_TX_ABORTED          drain loop: uart_link_recv(K_NO_WAIT)
     clear tx_busy                               │
                                                  ▼
                                            handle_rx_frame()
                                              → LOG_INF/LOG_WRN
                                              → send_ack() ──▶ uart_link_send() (uart_tx, DMA)
```

Everything left of the arrow lives in `uart_link.c`, fully encapsulated behind three calls:
`uart_link_init()`, `uart_link_send()`, `uart_link_recv()` (declared in `uart_link.h`). `main.c`
never touches the UART device, the DMA buffers, or `wp_rx_msgq` directly.

One callback (`lpuart1_async_cb`, registered via `uart_callback_set`) replaces the old RX ISR
entirely, and TX no longer has a separate code path either — `uart_link_send()` calls `uart_tx()`
once and returns immediately; the same callback's `UART_TX_DONE`/`UART_TX_ABORTED` cases clear a
busy flag when the DMA transfer actually finishes. The callback only feeds bytes into the parser
and queues completed frames (or, for TX events, flips a flag) — exactly as little "real work" in
that context as the old ISR did, for the same reason: logging and deciding how to respond stay on
the caller (`main.c`), not the transport library.

**RX uses two static ping-pong buffers** (`rx_dma_buf[2][RX_CHUNK_SIZE]`, 256 bytes each), *not* one
buffer sized to the largest possible frame. `UART_RX_BUF_REQUEST` asks for the next buffer before
the current one fills; the handler hands back whichever of the two isn't currently in use and
flips an index, mirroring Zephyr's own `samples/drivers/uart/async_api` reference exactly (down to
the same toggle-after-handoff pattern). This works regardless of frame size because
`wp_parser_feed()` was already written as a byte-fed streaming state machine (see §5) — it doesn't
care how the bytes were chunked on the way in, so the same 256-byte chunk buffers handle a 3-byte
`ACK` and an 8192-byte `SPECTRUM` payload identically. Chunk size and max frame size are
independent; don't conflate them when reading this code.

**TX uses one static buffer plus a busy flag** (`tx_buf`, sized for the largest frame; `tx_busy`,
an `atomic_t`, both file-scope statics in `uart_link.c`). This is the one place buffer size *does*
directly track `WP_MAX_PAYLOAD`, because `uart_tx()` returns immediately while DMA is still reading
from `tx_buf` in the background — unlike the old `uart_poll_out()` loop, which only returned once
every byte was physically sent, a stack buffer is not safe here. `uart_link_send()` checks-and-sets
`tx_busy` with `atomic_cas()` *before* touching `tx_buf`, not after building the frame — if it built
the frame first, a send while one was already in flight would corrupt the buffer the in-flight DMA
transfer is still reading. A second concurrent send attempt is logged and dropped (`-EBUSY`) rather
than queued, which is fine for today's traffic (one `HEARTBEAT` every 2s, occasional
`ACK`/`SPECTRUM`); revisit if this link ever needs to queue bursts.

> **Gotcha hit while building this: `struct wp_frame` instances must never be plain stack locals.**
> Raising `WP_MAX_PAYLOAD` from 256 to 8192 makes `sizeof(struct wp_frame)` ~8.2KB. Two places
> originally declared one as an ordinary local variable — `main()`'s loop body (now in `main.c`) and
> `feed_rx_bytes()` (now in `uart_link.c`, called from the async callback on a much smaller stack
> than the main thread's) — and the very first hardware test crashed with a `BUS FAULT` / "Instruction bus error" at a
> garbage program counter within one second of boot. That's the signature of stack corruption
> smashing a return address, not a DMA or devicetree bug, even though the timing (right as the
> first large `SPECTRUM` send fired) initially pointed at DMA. Both are now `static` locals instead
> — same single-threaded-reuse semantics as a stack local, but living in static memory instead of
> blowing through whatever stack the calling context happens to have. **If `WP_MAX_PAYLOAD` grows
> again, re-check every function for a `struct wp_frame` (or `struct wp_parser`) declared as a
> plain local — grep for `struct wp_frame ` and `struct wp_parser ` without a preceding `static`.**

## 3. Design choices Appendix B leaves open

Appendix B specifies the frame layout and message types but not every encoding detail. This
implementation had to pick:

| Choice | Decision | Why |
|---|---|---|
| CRC16 variant | CRC-16/CCITT-FALSE (poly `0x1021`, init `0xFFFF`, no reflect, no xorout) | A common, unambiguous, well-documented variant. Verified against the standard check value for ASCII `"123456789"` (`0x29B1`) before writing the Python side, so both implementations were known to agree before ever touching hardware. |
| Multi-byte field endianness | Little-endian for *all* multi-byte fields | Appendix B only states LEN is little-endian explicitly; this extends the same convention to every other multi-byte field (floats, uint16, uint32) for consistency, and because it matches both ends' native byte order (STM32 Cortex-M and the QRB2210's ARM core are both little-endian) — though the implementation never relies on host endianness (see §4, `wp_put_*_le`/`wp_get_*_le`). |
| `NODE_ID` | Always `0x00` | Per Appendix B: point-to-point link, single MCU, the field exists only for symmetry with the multi-node WiFi/MQTT side. |
| `ACK` sequence number | Always `0` | Appendix B explicitly notes the UART path may omit sequencing given the point-to-point, low-loss wired link. No sequence numbers are generated or tracked anywhere in this implementation. |

Any other implementation of this protocol (e.g. a FastAPI ingestion backend) must match the CRC
variant and endianness exactly, or frames will fail CRC validation silently (by design — see §5).

## 4. MCU-side function reference

### `wire_protocol.h` / `wire_protocol.c`

| Function | Purpose |
|---|---|
| `wp_crc16_update(seed, data, len)` | Incremental CRC16/CCITT-FALSE. Takes a running seed so the CRC can be computed across two non-contiguous buffers (the parser's separate `header`/`payload` arrays) without copying them into one buffer first. |
| `wp_crc16(data, len)` | Thin wrapper: `wp_crc16_update(0xFFFF, data, len)`. Use this when the data is already contiguous (e.g. inside `wp_encode`, where header+payload live in one output buffer). |
| `wp_put_u16_le` / `wp_put_u32_le` / `wp_put_f32_le` | Pack an integer/float into a buffer as explicit little-endian bytes, byte-by-byte (bit-shifts for integers; a `memcpy` of the raw IEEE-754 bits for floats). No assumption about the host CPU's native endianness — these would produce the same wire bytes on a big-endian host too. |
| `wp_get_u16_le` / `wp_get_u32_le` / `wp_get_f32_le` | The inverse: reconstruct a value from little-endian wire bytes. |
| `wp_encode(out, out_size, type, payload, payload_len)` | Builds one complete frame (`SYNC`+`VER`+`TYPE`+`NODE_ID`+`LEN`+`PAYLOAD`+`CRC16`) into `out`. Returns the frame length, or `-1` if `payload_len` exceeds `WP_MAX_PAYLOAD` or `out` is too small for the result — the only two failure modes, both checked explicitly since this function takes raw pointers/sizes at a real API boundary. |
| `wp_parser_init(p)` | Zeroes a `struct wp_parser` and sets its state to `WP_PARSE_SYNC0`. Call once before the first byte is fed. |
| `wp_parser_feed(p, byte, out_frame)` | The streaming decoder — see §5 below. Feed it one received byte at a time; it returns `true` and fills `out_frame` exactly when a complete, CRC-valid frame has just been assembled. |

`WP_MAX_PAYLOAD` is `8192` bytes (8KB) — sized for a full `SPECTRUM` frame: an 8-byte header
(`FS`+`FFT_SIZE`+`BIN_COUNT`) plus up to 2046 float32 bins. It drives the size of every
`struct wp_frame`/`struct wp_parser` instance and the MCU's TX buffer (see §2's RAM budget below
and the gotcha callout above) — raising it further means re-auditing all of those, not just the
one `#define`.

**RAM budget at `WP_MAX_PAYLOAD=8192`** (measured via `west build`'s memory report): `rx_parser`
(~8.2KB), `wp_rx_msgq`'s backing store (`2 × sizeof(struct wp_frame)` ≈ 16.4KB — the queue *copies*
full elements by value, so depth multiplies payload size; kept at 2 rather than the original 4 for
exactly this reason), `tx_buf` (~8.2KB), `feed_rx_bytes`'s and `main()`'s now-`static`
`struct wp_frame` locals (~8.2KB each), plus two 256-byte RX chunk buffers. Total: ~66.8KB out of
the STM32U585's 768KB SRAM (8.5%) — comfortable, but worth knowing where it goes before adding more
large static buffers elsewhere.

### `uart_link.h` / `uart_link.c`

The transport library — owns the UART device, RX ping-pong DMA buffers, TX buffer/busy state, and
the parser instance. Three functions are the entire public surface; everything else in this file
is `static`.

| Function | Purpose |
|---|---|
| `uart_link_init(void)` | Checks the LPUART1 device is ready, calls `wp_parser_init`, registers `lpuart1_async_cb` via `uart_callback_set`, and starts RX via `uart_rx_enable`. Returns a negative errno on the first failure, 0 on success. Call once at startup. |
| `uart_link_send(type, payload, payload_len)` | Claims `tx_busy` (or returns `-EBUSY`, logged, if already busy), encodes a frame via `wp_encode` into the static `tx_buf`, and starts a DMA transfer with `uart_tx()`. Returns immediately — completion is signaled later via `UART_TX_DONE` in the callback, not by this call. |
| `uart_link_recv(frame, timeout)` | Thin wrapper over `k_msgq_get(&wp_rx_msgq, frame, timeout)`. Returns 0 and fills `*frame` if one was queued within `timeout`, else `-EAGAIN`. |
| `feed_rx_bytes(data, len)` *(internal)* | Feeds a chunk of just-received bytes (from a `UART_RX_RDY` event) into the shared `rx_parser` one byte at a time, queuing any completed frame onto `wp_rx_msgq`. Its `struct wp_frame` is `static` (see the gotcha callout in §2). |
| `lpuart1_async_cb(dev, evt, user_data)` *(internal)* | The single async event handler, registered via `uart_callback_set`. Dispatches on `evt->type`: `UART_RX_RDY` → `feed_rx_bytes`; `UART_RX_BUF_REQUEST` → hand back the other ping-pong buffer (`uart_rx_buf_rsp`) and flip the index; `UART_RX_DISABLED` → re-arm `uart_rx_enable` (shouldn't happen in normal operation, logged if it does); `UART_RX_STOPPED` → log the driver's stop reason; `UART_TX_DONE`/`UART_TX_ABORTED` → clear `tx_busy`. |

### `main.c`

Pure application behavior: what to send, on what cadence, and what to do with what's received.
Nothing here touches the UART device, DMA, or `wp_rx_msgq` directly — everything goes through
`uart_link_*`.

| Function | Purpose |
|---|---|
| `send_heartbeat(void)` | Builds a `HEARTBEAT` payload (`uptime_sec`, a placeholder `current_fs` of 4000.0, a placeholder `current_fft_size` of 256 — there's no real sensor sampling loop yet, so these are stand-ins for whatever the actual FFT pipeline will report) and sends it via `uart_link_send`. Called every 4th main-loop tick (~2s, given the 500ms `k_msleep`). |
| `send_ack(acked_type)` | Builds and sends an `ACK` for `acked_type` via `uart_link_send`, with `ACKED_SEQ` hardcoded to `0` (see §3). |
| `send_spectrum_test(void)` | DMA large-transfer verification: builds a deterministic ~8KB `SPECTRUM` frame (`BIN_COUNT=2046`, `bin[i] = i` exactly) and sends it via `uart_link_send`. Called every 20th main-loop tick (~10s). Exists specifically so the MPU side can verify every byte of a large payload, not just that *something* arrived — see [uart_large_transfer_test.py](../mpu/tests/uart_large_transfer_test.py). |
| `handle_rx_frame(frame)` | Dispatches a received frame by `type`. Currently only `CONFIG_SET` has real handling: it logs the decoded `fs`/`fft_size`/`active_channels` fields and replies with an `ACK`. Anything else logs a `LOG_WRN` ("unhandled frame type") and is otherwise ignored — this is where handlers for `COMMISSION_START`/`COMMISSION_DONE`/etc. would be added later. |
| `main(void)` | Sets up the LED and calls `uart_link_init()`, then loops: toggle LED, drain received frames completely via `uart_link_recv(K_NO_WAIT)` (calling `handle_rx_frame` for each), send a `HEARTBEAT` every 4th tick, send a `SPECTRUM` test frame every 20th tick, sleep 500ms. |

## 5. The parser state machine

`wp_parser_feed` is a 5-state machine, fed one byte per call. It doesn't care how bytes arrive —
the original version fed it from an ISR's FIFO-read loop; the current DMA version feeds it from
`feed_rx_bytes()` iterating over whatever chunk a `UART_RX_RDY` event just delivered. Same function,
unchanged, in both:

```
SYNC0 ──0xAA──▶ SYNC1 ──0x55──▶ HEADER (5 bytes) ──▶ PAYLOAD (LEN bytes) ──▶ CRC (2 bytes)
  ▲                │                  │                                        │
  └──other byte─────┘                  └──LEN > WP_MAX_PAYLOAD────────────────▶ │
                                                                                 │
                                                                    valid CRC ───┼──▶ return true, frame filled
                                                                  invalid CRC ───┘──▶ return false
                                                        (either way: state resets to SYNC0)
```

Key resync properties:
- In `SYNC1`, seeing another `0xAA` (instead of the expected `0x55`) doesn't drop back to `SYNC0` —
  it's treated as a possible *new* first sync byte, so a stray `0xAA 0xAA 0x55 ...` pattern still
  finds the real frame instead of needing the sync sequence to repeat from scratch.
- An oversized `LEN` (corrupted header, before CRC has a chance to catch it) aborts back to
  `SYNC0` immediately rather than waiting for a payload that may never legitimately arrive.
- A CRC mismatch on a fully-assembled frame is silently dropped, and the parser resyncs on the next
  `0xAA 0x55` it sees in the stream — there is no NACK/retry on the UART path (consistent with
  Appendix B treating this link as low-loss).

This resync behavior was verified locally (no hardware involved) before testing on the board: a
deliberately corrupted CRC byte followed by a valid frame correctly yields exactly one valid
decoded frame, and leading garbage bytes before a valid `SYNC` are silently skipped. The same
property means this implementation doesn't need the "discard the first line, it might be a partial
fragment from before the listener opened the port" workaround that the plain-ASCII counter test in
the other appendix needed — real framing with CRC makes that class of startup artifact a non-issue.

## 6. MPU-side (Python) reference

[mpu/wire_protocol.py](../mpu/common/wire_protocol.py) mirrors the MCU implementation field-for-field:

| Name | Purpose |
|---|---|
| `crc16_ccitt_false(data)` | Same algorithm as `wp_crc16`, single-shot (Python concatenates header+payload into one `bytes` object first, so no incremental variant is needed here). |
| `encode_frame(msg_type, payload)` | Equivalent to `wp_encode`. Uses `struct.pack("<H", ...)` for the length field — Python's own explicit little-endian packing, the direct counterpart to the C side's `wp_put_u16_le`. |
| `FrameParser` (class) | Equivalent state machine to `wp_parser_feed`, as a small class with a `.feed(byte)` method instead of a struct-plus-function pair (idiomatic for each language; same five states, same resync rules, same `MAX_PAYLOAD` sanity bound — must be raised in lockstep with the MCU side's `WP_MAX_PAYLOAD`, which bit once already: see the gotcha callout in §2). |

Two test scripts, each opening `/dev/ttyHS1` at a baud rate given as an optional CLI argument
(default 115200; pass 4000000 to match the link's current actual rate — see §7) and `rtscts=True`
(required once `hw-flow-control` is enabled on the MCU side, or the MCU's CTS input may never see
"clear to send" and TX can stall). Only one script at a time — both want exclusive access to the
port:
- [mpu/uart_protocol_test.py](../mpu/tests/uart_protocol_test.py): listens for `HEARTBEAT`, sends one
  `CONFIG_SET` on the first one seen, waits for the `ACK`. Validates the small control-message
  path end-to-end.
- [mpu/uart_large_transfer_test.py](../mpu/tests/uart_large_transfer_test.py): waits for one of the
  MCU's periodic ~8KB `SPECTRUM` test frames and verifies every one of its 2046 bins individually
  matches the expected deterministic value — a CRC pass alone only proves the frame wasn't
  corrupted in transit, not that producer and consumer agree on the full 8192-byte payload's exact
  contents. Validates the DMA large-transfer path end-to-end.

## 7. Current coverage and known limitations

- **Implemented end-to-end:** `HEARTBEAT` (MCU→MPU), `CONFIG_SET`→`ACK` (MPU→MCU→MPU), and an 8KB
  `SPECTRUM` test frame (MCU→MPU) verified byte-exact on real hardware via DMA.
- **Framing/CRC support exists, no real handler yet:** `SPECTRUM` has a deterministic test sender
  (`send_spectrum_test`) but no handling of *real* FFT data yet; `HEALTH_ALERT` and
  `COMMISSION_START`/`COMMISSION_DONE` have no sender or handler at all. Adding one is a `case` in
  `handle_rx_frame` (MCU→MPU direction) or a new `if frame.type == ...` branch on the Python side
  (MPU→MCU direction) — the framing and CRC machinery underneath doesn't change.
- **TX and RX are both DMA-backed (resolved).** The original polling-TX/interrupt-RX version is
  superseded — see §1 and §2 for why and how.
- **LPUART1 is now at 4,000,000 baud with `hw-flow-control` enabled (resolved).** Raised after the
  DMA path above was confirmed correct at the original 115200 baud, as a deliberately separate
  change. Measured sustained throughput: 114.0 KB/s at 4M baud, 90.7 KB/s at 1.5M baud (both with
  back-to-back ~8KB `SPECTRUM` frames) — confirms throughput now scales with baud rate, unlike the
  flat ~45 KB/s polling-TX ceiling found before the DMA conversion. It does not scale *linearly*
  with baud, though: see
  [appendix-mcu-mpu-channel.md §8.4](appendix-mcu-mpu-channel.md#84-dma-conversion-and-re-measured-throughput)
  for the full results and the new (not yet diagnosed) per-frame software-overhead bottleneck this
  surfaced.
