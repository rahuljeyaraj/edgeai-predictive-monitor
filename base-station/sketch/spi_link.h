#ifndef SPI_LINK_H_
#define SPI_LINK_H_

/*
 * MCU<->MPU dedicated SPI link, slave side - Risk-1 spike from
 * docs/progress2.md ("THE NEXT CHANGE"): proves the board's second,
 * currently-dormant SPI bus (separate from the Bridge UART) actually moves
 * bytes MCU->MPU before any real transport is built on it. See
 * spi_link.cpp's header comment for the full bring-up rationale.
 *
 * This is a bring-up probe, not production transport: no handshake (the
 * MPU's GENI master clocks whenever it feels like it, no PG13 RDY signalling
 * yet - that's a separate follow-up, see docs/progress2.md decision 3), no
 * DMA (CONFIG_SPI_STM32_DMA isn't set in this firmware, so even the "clean"
 * Zephyr device API below is interrupt-driven under the hood - the eventual
 * production path needs the register-level SPI3+GPDMA fallback instead, see
 * docs/progress2.md task 1). Scoped only to answer: is the wiring/slave-mode
 * config correct end-to-end.
 */

/* Brings up SPI3 as a register-level slave (LL_SPI_* + GPDMA1 TX, see
 * spi_link.cpp) and starts a thread that continuously stages a fixed,
 * self-incrementing known pattern for the MPU side to read back over
 * /dev/spidev0.0 (via the host spi-bridge daemon - see base-station/host/).
 * Also registers a "get_spi_link_stats" Bridge provider (transfer/completed/
 * timeout counters) for polling thread liveness without the serial monitor.
 * Call once from setup(). */
void spi_link_start(void);

#endif /* SPI_LINK_H_ */
