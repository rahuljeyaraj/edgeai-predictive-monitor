#ifndef SPI_LINK_H_
#define SPI_LINK_H_

#include <cstdint>

/*
 * MCU<->MPU dedicated SPI link, slave side - the real bulk transport for the
 * fuser stream (docs/progress2.md "THE NEXT CHANGE", tasks 4-5). The board has
 * a second SPI bus wired directly between MCU and MPU, separate from the Bridge
 * UART; this module owns SPI3-slave + GPDMA1 TX and moves whole fuser frames
 * across it so the ~65 KB/s spectrum push no longer rides (and recurringly
 * wedges) the shared Bridge UART. See spi_link.cpp's header comment for the
 * register-level bring-up rationale and the hang history behind it.
 *
 * Model: the fuser thread produces a frame every epoch and hands the raw
 * payload to spi_link_stage_frame(), which wraps it in a minimal SPI framing
 * header (magic | seq | payload_len) + CRC32 trailer and keeps it as the latest
 * "pending" frame. The MPU pulls on its own schedule, chunked (a single
 * multi-KB slave-TX transfer underruns - docs/progress2.md 5.7), via one of
 * two Bridge providers:
 * - "spi_arm(offset, len)": one RPC round trip per chunk, replies
 *   "<seq>,<total_len>,<chunk_len>". Simple, used by tests/spi_link_test.py
 *   and ad-hoc diagnostics.
 * - "spi_arm_stream(chunk_size)": one RPC round trip for the WHOLE frame -
 *   the MCU auto-advances chunk-by-chunk on its own thread as each one
 *   completes, no further RPC needed. This is what ingestion/spi_reader.py
 *   uses in production (added 2026-07-20 - RPC round-trip overhead, not SPI
 *   clock time, was the fps bottleneck once frames grew past a handful of
 *   chunks). See spi_link.cpp's spi_link_arm_stream() comment for the full
 *   design, including why it's safe without PG13/RDY (not wired to the MPU,
 *   docs/progress2.md 4.1).
 * Missed/duplicated frames are fine (lossy live view) - the MPU dedups by seq.
 */

/* Bring up SPI3 as a register-level slave (LL_SPI_* + GPDMA1 TX) and start the
 * transport thread that owns the bounded per-chunk completion wait (auto-
 * advancing through a whole frame in stream mode - see above). Also registers
 * the "spi_arm", "spi_arm_stream", and "get_spi_link_stats" Bridge providers.
 * Call once from setup(), before fuser_start() (so the provider is registered
 * before the fuser begins staging frames). */
void spi_link_start(void);

/* Publish one frame for the MPU to pull. Copies payload[0..payload_len) into the
 * pending-frame buffer under a mutex, wrapped with the SPI framing header + CRC32
 * trailer, and stamps it with the next sequence number. Overwrites any
 * not-yet-pulled pending frame (lossy sample-and-hold). Safe to call from the
 * fuser thread while an unrelated frame is being clocked out. payload_len is
 * clamped to the transport's max payload. */
void spi_link_stage_frame(const uint8_t *payload, uint16_t payload_len);

#endif /* SPI_LINK_H_ */
