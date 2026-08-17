/*
 * MCU<->MPU dedicated SPI link, slave side - the fuser stream's bulk transport
 * (docs/progress2.md "THE NEXT CHANGE", tasks 4-5). The board has a second SPI
 * bus wired directly between the MCU and MPU, separate from the Bridge UART;
 * this module carries whole fuser frames over it so the ~65 KB/s spectrum push
 * no longer rides (and recurringly wedges, via msgpack framer desync - see
 * docs/progress2.md section 2) the shared Bridge UART. SPI transfers are
 * CS/NSS-delimited, so a bit error costs one frame and the next chip-select
 * self-realigns - and a CRC32 per frame lets the MPU detect and drop that one
 * bad frame rather than feed corrupt magnitudes to the autoencoder.
 *
 * This is the register-level SPI3-slave + GPDMA1 TX path (docs/progress2.md
 * task 1's designated Plan B), used directly rather than the Zephyr device
 * API: the "clean" path (this core's Arduino `SPI1` object, bound to the &spi3
 * bus node, in `SPISettings(..., SPI_PERIPHERAL)` mode) hung setup()/the whole
 * Bridge link with zero serial output, reproduced twice; targeting the
 * overlay's `compatible = "zephyr,spi-slave"` child node (`device0`) was
 * disproven at link time (no driver instance in this firmware). Both historical
 * total-Bridge-death hangs were later root-caused as scheduling starvation, not
 * SPI bring-up faults (docs/progress2.md 4.3/4.7/4.8) - fixed by dropping this
 * thread below Bridge's (SPI_LINK_THREAD_PRIORITY, app_config.h) + per-arm flag
 * clearing. This file sidesteps the Zephyr SPI subsystem entirely, same
 * reasoning as mic_sampler.cpp's SAI/GPDMA1 work: register-level + GPDMA is
 * always available even when a Zephyr driver path is missing or broken.
 *
 * Pin mux: SCK=PG9, MISO=PG10, MOSI=PB5, NSS=PG12, all **AF6** - decoded by
 * hand from the shipped overlay's raw `pinmux = < 0x... >` values against the
 * STM32 encoding (`zephyr/dt-bindings/pinctrl/stm32-pinctrl.h`'s
 * `STM32_PINMUX(port, line, mode) = ((port-'A') << 9) | ((line & 0xF) << 5) |
 * (mode & 0x1F)`) - e.g. SCK's `0xd26` decodes to port=6('G'), line=9,
 * mode=6(AF6). All four decode to AF6, SPI3's alternate function on the
 * stm32u585aiixq.
 *
 * GPDMA1 request line: `LL_GPDMA1_REQUEST_SPI3_TX` = 11 (stm32u5xx_ll_dma.h) -
 * fixed hardware wiring. Uses GPDMA1 channel 3 (mic_sampler.cpp owns channel 2
 * for SAI1_A RX - the two never share a channel).
 *
 * SPI3 config (CFG1/CFG2, all "can't change while SPI enabled" fields) is set
 * once in spi_link_init_hw(): slave mode, full duplex, 8-bit words, mode 0
 * (CPOL=0/CPHA=0 - must match the MPU's spidev config, base-station/host/
 * spi_bridge.py), MSB first, **hardware NSS input** (PG12 driven externally by
 * the MPU's GENI master), Motorola frame format, DMA TX request enabled.
 * Per-transfer, only TSIZE (CR2, the frame count this SPI IP needs for EOT
 * tracking, unlike classic F4/F7 SPI) and SPE toggle, mirroring
 * mic_sampler.cpp's "full reconfigure per block" pattern.
 *
 * Wire frame (little-endian on both ends): [magic u32][seq u16][payload_len u16]
 * [payload payload_len bytes][crc32 u32 over header+payload]. The payload is the
 * fuser's generic section-list telemetry frame (fuser.cpp /
 * docs/SENSOR_TELEMETRY_FRAME_PLAN.md S3) - this envelope is payload-agnostic and
 * doesn't parse it; only payload_len and the CRC matter here. CRC32 is
 * the standard reflected/zlib variant (poly 0xEDB88320, init/xorout 0xFFFFFFFF)
 * so the MPU can verify with a plain zlib.crc32().
 *
 * Handshake: RPC-triggered (docs/progress2.md decision 3's documented fallback -
 * PG13/"SPI RDY" is not wired through to the MPU, see 4.1). The fuser stages
 * frames continuously via spi_link_stage_frame(); the MPU calls the "spi_arm"
 * provider, which loads the latest pending frame into the DMA TX buffer and
 * replies "<seq>,<total_len>"; the MPU then clocks total_len bytes out over
 * spidev and verifies the CRC + seq. The wait for the master's clock happens on
 * this module's own thread and is bounded (k_msleep-polled, like
 * mic_sampler.cpp's DMA wait), so an MPU that arms but never reads just counts a
 * timeout - it does not hang this thread, and (being DMA driven, not a
 * busy-poll) it does not risk starving Bridge's own thread either.
 */
#include "spi_link.h"

#include "app_config.h"

#include <Arduino_RouterBridge.h>
#define STM32U585xx
#include <stm32u5xx.h>
#include <stm32u5xx_ll_bus.h>
#include <stm32u5xx_ll_dma.h>
#include <stm32u5xx_ll_gpio.h>
#include <stm32u5xx_ll_spi.h>
#include <zephyr/kernel.h>
#include <zephyr/irq.h>
#include <cstring>

#define SPI_LINK_DMA_CHANNEL LL_DMA_CHANNEL_3 /* mic_sampler.cpp owns channel 2 */

/* Diagnostic (docs/progress2.md 5.4): when 1, spi_link stages one fixed
 * 4112-byte ramp pattern at start (independent of the fuser) so a fuser-disabled
 * build still has a full-size frame to clock out - used to isolate/tune the
 * transport (e.g. the chunk-size sweep in tests/spi_link_test.py). Pair with
 * fuser_start() commented out in sketch.ino when set. Normally 0. */
#define SPI_LINK_SELFTEST 0

/* SPI framing. Magic is an arbitrary sentinel matched verbatim on the MPU side
 * (main.py / tests). Max payload is sized for the fuser's generic section-list
 * frame worst case (docs/SENSOR_TELEMETRY_FRAME_PLAN.md S3): 1 num_sections
 * byte + 5 max-bin SPECTRUM sections (mic, accel-fused, accel_x/y/z; each
 * 5-byte section header + 8-byte fs/fft/bin_count preamble + 512*4 bins =
 * 2061 B) + 1 SCALAR_SET section (5-byte header + 1 count byte + 24 scalars
 * * 6 B = 150 B) = 1 + 5*2061 + 150 = 10456 B; 10600 gives a little slack.
 * Was 14600 until 2026-08-01, when the 4 decimated TIME_SERIES sections
 * (1035 B each, piggybacked every Nth frame) were removed from normal mode
 * entirely - see fuser.cpp's file header. That reclaims ~4 KB from EACH of
 * spi_link_pending_buf/spi_link_frame_buf/spi_link_selftest_payload below,
 * which matters on a board already flagged "Low memory available".
 * Kept as a named constant here rather than #include'ing fuser
 * internals - spi_link_stage_frame() clamps to it defensively, and the fuser
 * sizes its own buffer identically. TSIZE and the GPDMA block length are both
 * 16-bit (max 65535), so even this larger frame fits with room to spare; the
 * chunked pull (spi_arm) already sub-divides it CHUNK_SIZE bytes at a time,
 * so the on-wire transfer size is unaffected by this ceiling (it does mean
 * more chunks, hence more spi_arm RPC round-trips per frame - see
 * ingestion/spi_reader.py's own reasoning on why that's the real fps cost).
 *
 * FUSER_RAW_CAPTURE_MODE raises this instead: that mode's one combined frame
 * (accel x/y/z + mic raw TIME_SERIES sections, all in one epoch - see
 * fuser.cpp's FUSER_RAW_FRAME_BUF_LEN) needs 1 + 3*(11 + 1024*4) +
 * (11 + 2048*4) = 20525 B, well past normal mode's 10600. This is a real RAM
 * tradeoff, not a free bump: it grows spi_link_pending_buf/spi_link_frame_buf
 * below AND spi_link_selftest_payload by the same delta each, on a board
 * already flagged "Low memory available, stability problems may occur" at
 * the smaller size. Accepted deliberately (2026-07-21) so a labeled capture
 * run gets exactly matched x/y/z/mic window counts instead of independently-
 * arriving, uneven ones - but this is exactly why FUSER_RAW_CAPTURE_MODE
 * stays a temporary, supervised data-collection build (app_config.h's own
 * comment): normal mode's buffers stay at the smaller, safer 10600. */
#define SPI_LINK_MAGIC 0x46555331u /* "1SUF" on the wire (LE) - just a sentinel */
#if FUSER_RAW_CAPTURE_MODE
#define SPI_LINK_MAX_PAYLOAD 20525
#else
#define SPI_LINK_MAX_PAYLOAD 10600
#endif
#define SPI_LINK_HEADER_LEN 8 /* sizeof(spi_link_frame_header) */
#define SPI_LINK_CRC_LEN 4
#define SPI_LINK_MAX_FRAME \
  (SPI_LINK_HEADER_LEN + SPI_LINK_MAX_PAYLOAD + SPI_LINK_CRC_LEN)

struct __attribute__((packed)) spi_link_frame_header {
  uint32_t magic;
  uint16_t seq;
  uint16_t payload_len;
};

/* Bounded like mic_sampler.cpp's MIC_DMA_WAIT_TICKS: a wait loop, not a
 * blocking/forever wait. The ~1 s ceiling (1000 * 1 ms) is generous for a
 * single chunk at the daemon's real ~40 MHz SPI clock (host/spi_bridge.py) -
 * an MPU that arms but never reads (or a lost read) just times out and the
 * next arm re-uses a fresh frame.
 *
 * 2026-07-20, alongside spi_arm_stream's auto-advance (below): this loop's
 * per-iteration wait is now interrupt-driven (spi_link_dma_isr gives
 * spi_link_tc_sem the instant GPDMA1 channel 3 signals TC), not a plain
 * k_msleep poll - SPI_LINK_DMA_WAIT_TICK_MS is now just the PER-ITERATION
 * k_sem_take timeout, a safety net in case a completion is ever missed
 * (spurious/lost interrupt), not the thing determining how fast a real
 * completion is noticed.
 *
 * This mattered because a plain poll wasn't fast enough for spi_arm_stream:
 * first attempt was tightening SPI_LINK_DMA_WAIT_TICK_MS 5ms->1ms, assuming
 * the poll tick itself was the bottleneck - measured NO improvement (still
 * needed ~10-20ms of pacing on the MPU side, ingestion/spi_reader.py's
 * STREAM_PACING_S, to pull a frame reliably - no better than the plain
 * per-chunk-RPC protocol it was meant to replace). Root cause wasn't the
 * poll granularity at all (CONFIG_SYS_CLOCK_TICKS_PER_SEC=10000, tickless
 * kernel - a 1ms k_msleep really is ~1ms here): it's that spi_link_thread
 * (priority 6) can be preempted for a few ms at a time by fuser (same
 * priority 6, real per-epoch compute) or by scheduling jitter generally,
 * and no amount of shortening a THREAD-LEVEL poll interval helps if the
 * thread just doesn't get scheduled promptly. An NVIC interrupt preempts
 * all of that regardless of Zephyr thread priority, which a poll
 * fundamentally cannot. Not yet re-validated with real pacing numbers this
 * unlocks - see spi_link_dma_isr's comment for the interrupt setup and
 * ingestion/spi_reader.py's STREAM_PACING_S for the current tuned value.
 *
 * 2026-08-17: ceiling cut 1000 -> 100 ticks (~1s -> ~100ms). The ~1s value
 * was sized as "generous", but generosity on this path is not free: this wait
 * runs with spi_link_busy held for the WHOLE stream, so every spi_arm_stream
 * the MPU makes while it runs is answered "busy" (see spi_link_thread_entry's
 * header). A single timeout therefore stops frame service dead for the entire
 * ceiling, and measured on real hardware these fire every ~10-20s - each one
 * showing up on the dashboard as a ~1-2.8s freeze of the base_station node's
 * charts while the satellite kept updating (get_spi_link_stats' timeout
 * counter incrementing by exactly 1 per freeze is what pinned it).
 *
 * 100ms is still far above any legitimate completion: a 512-byte chunk at the
 * daemon's ~40MHz clock is ~100us of actual SPI time, and the slowest real
 * contributor is the MPU's own inter-chunk pacing (STREAM_PACING_S = 15ms,
 * ingestion/spi_reader.py) plus a few ms of thread jitter. Anything that has
 * not completed in 100ms is not slow, it is lost - the MPU gave up on that
 * frame long before (FRAME_RETRIES x ARM_RETRIES there), so waiting the other
 * 900ms only withholds the NEXT frame, which is already staged and fine.
 * Lower still would start eating into legitimate jitter margin. */
#define SPI_LINK_DMA_WAIT_TICK_MS 1
#define SPI_LINK_DMA_WAIT_TICKS 100
#define SPI_LINK_THREAD_STACK_SIZE 2048

/* Back-off after a transfer that ended in a DMA error flag (not a timeout - the
 * timeout path already slept its way through the wait loop). Same idiom/value as
 * mic_sampler_thread_entry()'s capture-failure back-off. Load-bearing, not
 * politeness: an error flag latching on every re-arm would otherwise turn the
 * thread loop into a never-yielding spin - a thread doing exactly that above
 * Bridge is the leading explanation for both historical SPI hangs
 * (docs/progress2.md 4.3/4.7/4.8; see also SPI_LINK_THREAD_PRIORITY in
 * app_config.h). */
#define SPI_LINK_ERROR_BACKOFF_MS 100

/* spi_link_wait_transfer() outcomes - kept distinct so the stats provider can
 * tell "MPU never clocked us" (timeout) from "the DMA itself failed" (error). */
enum spi_link_xfer_result {
  SPI_LINK_XFER_OK,
  SPI_LINK_XFER_TIMEOUT,
  SPI_LINK_XFER_ERROR,
};

/* Given to by spi_link_dma_isr the instant GPDMA1 channel 3 signals transfer-
 * complete, taken (with a bounded per-iteration timeout, not forever) by
 * spi_link_wait_transfer - see that function's SPI_LINK_DMA_WAIT_TICK_MS
 * comment for why this exists (interrupt-driven completion, added 2026-07-20
 * for spi_arm_stream). Binary (max count 1): an extra/stale give before
 * anyone's waiting just leaves it signaled, which spi_link_configure_dma's
 * k_sem_reset (below) clears before every arm so a stale signal from transfer
 * N can't be mistaken for transfer N+1's completion. */
static struct k_sem spi_link_tc_sem;

/* DIAG (temporary): incremented by spi_link_dma_isr on every real TC
 * interrupt, to directly verify the ISR is firing (vs. wait_transfer's
 * k_sem_take always just timing out) - added 2026-07-20 debugging
 * spi_arm_stream's pacing requirement. */
static volatile uint32_t spi_link_isr_count = 0;

/* Deliberately minimal (RTOS best practice for ISRs, doubly so here - it can
 * preempt ANY thread including Bridge's): clear the flag that woke us
 * (mandatory - otherwise the interrupt refires the instant we return) and
 * give the semaphore. No SPI_LINK_CP - that macro's counter increment isn't
 * atomic and spi_link_thread can be mid-read of it at the exact moment this
 * interrupt lands. spi_link_wait_transfer still re-checks the real DMA flags
 * as the source of truth after waking (not just "the semaphore was given"),
 * so a spurious/stale wake here costs one extra fast loop iteration, never a
 * false completion. */
static void spi_link_dma_isr(const void *arg) {
  ARG_UNUSED(arg);
  LL_DMA_ClearFlag_TC(GPDMA1, SPI_LINK_DMA_CHANNEL);
  spi_link_isr_count++;
  k_sem_give(&spi_link_tc_sem);
}

/* Two frame buffers: spi_link_pending_buf is written by the fuser thread (under
 * spi_link_pending_lock) and holds the latest complete SPI frame ready to send;
 * spi_link_frame_buf is the DMA source, loaded from pending at arm time. The
 * split lets the fuser stage a new frame while a previous one is still being
 * clocked out of frame_buf. */
/* 4-byte aligned: the DMA moves this as 32-bit words (see spi_link_configure_dma)
 * to cut bus transactions 4x and keep ahead of the gapless master. */
static uint8_t spi_link_pending_buf[SPI_LINK_MAX_FRAME] __attribute__((aligned(4)));
static uint8_t spi_link_frame_buf[SPI_LINK_MAX_FRAME] __attribute__((aligned(4)));
static struct k_mutex spi_link_pending_lock;
static uint16_t spi_link_pending_len = 0; /* 0 = nothing staged yet */
static uint16_t spi_link_pending_seq = 0;
static uint16_t spi_link_seq_counter = 0;

/* Counters, all single-writer / torn-read-harmless (diagnostics, same reasoning
 * as the samplers'). staged = frames the fuser handed us; armed = spi_arm calls
 * that loaded a real frame (vs. "empty"/"busy"); completed/timeout/error =
 * outcomes of the DMA wait. */
static uint32_t spi_link_staged_count = 0;
static uint32_t spi_link_armed_count = 0;
static uint32_t spi_link_completed_count = 0;
static uint32_t spi_link_timeout_count = 0;
static uint32_t spi_link_error_count = 0;
/* Bitmask of which GPDMA error flags were set on the most recent
 * SPI_LINK_XFER_ERROR (1=DTE, 2=ULE, 4=USE, 8=TO). */
static volatile uint32_t spi_link_last_error_flags = 0;

/* DIAG (temporary) - SPI/DMA register snapshot at the end of the last wait, to
 * root-cause the 4 KB-frame underrun. Remove once the transport is solid. */
static volatile uint32_t spi_link_dbg_sr = 0;
static volatile uint32_t spi_link_dbg_cr1 = 0;
static volatile uint32_t spi_link_dbg_dma_rem = 0;

/* Checkpoint diagnostic: updated right before each risky register-level step so
 * a hang's last-reached value is visible from get_spi_link_stats() even without
 * a working serial monitor. volatile + plain int write is word-atomic on
 * Cortex-M. Numbering has gaps on purpose (room to insert finer checkpoints). */
static volatile int spi_link_checkpoint = 0;

/* Post-mortem-findable mirror of the checkpoint (see docs/progress2.md 4.8 - the
 * known freeze leaves Bridge dead but RAM dumpable over SWD; the llext loader
 * gives this block no symbol, so it's located by scanning the dump for the
 * leading magic word). Layout: [0] magic, [1] last checkpoint, [2] stamp
 * (liveness), [3] trailing magic. */
static volatile uint32_t spi_link_diag[4] = {0xC0FFEE01u, 0u, 0u, 0xC0FFEE02u};

#define SPI_LINK_CP(n)                \
  (spi_link_checkpoint = (n),         \
   spi_link_diag[1] = (uint32_t)(n),  \
   spi_link_diag[2] = spi_link_diag[2] + 1u)

/* Standard reflected CRC32 (zlib/Ethernet: poly 0xEDB88320, init/xorout
 * 0xFFFFFFFF) so the MPU verifies with a plain zlib.crc32(). Table built once
 * at start; a 4 KB frame is a handful of microseconds this way. */
static uint32_t spi_link_crc_table[256];

static void spi_link_crc_init(void) {
  for (uint32_t i = 0; i < 256; i++) {
    uint32_t c = i;
    for (int k = 0; k < 8; k++) {
      c = (c & 1u) ? (0xEDB88320u ^ (c >> 1)) : (c >> 1);
    }
    spi_link_crc_table[i] = c;
  }
}

static uint32_t spi_link_crc32(const uint8_t *data, size_t len) {
  uint32_t c = 0xFFFFFFFFu;
  for (size_t i = 0; i < len; i++) {
    c = spi_link_crc_table[(c ^ data[i]) & 0xFFu] ^ (c >> 8);
  }
  return c ^ 0xFFFFFFFFu;
}

void spi_link_stage_frame(const uint8_t *payload, uint16_t payload_len) {
  if (payload_len > SPI_LINK_MAX_PAYLOAD) {
    payload_len = SPI_LINK_MAX_PAYLOAD;
  }

  k_mutex_lock(&spi_link_pending_lock, K_FOREVER);

  struct spi_link_frame_header header;
  header.magic = SPI_LINK_MAGIC;
  header.seq = ++spi_link_seq_counter;
  header.payload_len = payload_len;

  memcpy(spi_link_pending_buf, &header, SPI_LINK_HEADER_LEN);
  memcpy(spi_link_pending_buf + SPI_LINK_HEADER_LEN, payload, payload_len);

  uint32_t crc = spi_link_crc32(spi_link_pending_buf,
                                SPI_LINK_HEADER_LEN + payload_len);
  memcpy(spi_link_pending_buf + SPI_LINK_HEADER_LEN + payload_len, &crc,
         SPI_LINK_CRC_LEN);

  spi_link_pending_len = SPI_LINK_HEADER_LEN + payload_len + SPI_LINK_CRC_LEN;
  spi_link_pending_seq = header.seq;
  spi_link_staged_count++;

  k_mutex_unlock(&spi_link_pending_lock);
}

static void spi_link_configure_af_pin(GPIO_TypeDef *port, uint32_t pin, int pin_num, uint32_t pull) {
  LL_GPIO_SetPinMode(port, pin, LL_GPIO_MODE_ALTERNATE);
  LL_GPIO_SetPinSpeed(port, pin, LL_GPIO_SPEED_FREQ_VERY_HIGH);
  LL_GPIO_SetPinPull(port, pin, pull);
  if (pin_num < 8) {
    LL_GPIO_SetAFPin_0_7(port, pin, LL_GPIO_AF_6);
  } else {
    LL_GPIO_SetAFPin_8_15(port, pin, LL_GPIO_AF_6);
  }
}

/* All SPI3 CFG1/CFG2 fields (slave, full duplex, 8-bit, mode 0, MSB-first,
 * hardware NSS, word-matched FIFO threshold, TX DMA req). Split out from
 * spi_link_init_hw so it can be re-applied after a per-transfer RCC reset
 * (spi_link_reset_spi) - the reset is how the TX FIFO / transfer state gets
 * fully cleared between frames, which SPE=0 alone does not do under word packing
 * (docs/progress2.md 5.7). Leaves SPE=0. */
static void spi_link_configure_spi(void) {
  SPI_LINK_CP(30);
  LL_SPI_SetMode(SPI3, LL_SPI_MODE_SLAVE);
  SPI_LINK_CP(31);
  LL_SPI_SetStandard(SPI3, LL_SPI_PROTOCOL_MOTOROLA);
  SPI_LINK_CP(32);
  LL_SPI_SetTransferDirection(SPI3, LL_SPI_FULL_DUPLEX);
  SPI_LINK_CP(33);
  LL_SPI_SetDataWidth(SPI3, LL_SPI_DATAWIDTH_8BIT);
  SPI_LINK_CP(34);
  LL_SPI_SetClockPhase(SPI3, LL_SPI_PHASE_1EDGE);      /* mode 0 - must match spi_bridge.py's SPI_MODE */
  SPI_LINK_CP(35);
  LL_SPI_SetClockPolarity(SPI3, LL_SPI_POLARITY_LOW);  /* mode 0 */
  SPI_LINK_CP(36);
  LL_SPI_SetTransferBitOrder(SPI3, LL_SPI_MSB_FIRST);
  SPI_LINK_CP(37);
  LL_SPI_SetNSSMode(SPI3, LL_SPI_NSS_HARD_INPUT);      /* PG12 driven externally by the MPU */
  SPI_LINK_CP(38);
  LL_SPI_SetFIFOThreshold(SPI3, LL_SPI_FIFO_TH_01DATA);
  SPI_LINK_CP(39);
  LL_SPI_EnableDMAReq_TX(SPI3);
  SPI_LINK_CP(40);
}

/* One-time bring-up: clocks, pin muxing, and the SPI3 CFG. SPI3 is left
 * disabled (SPE=0) on return; each transfer enables it. */
static void spi_link_init_hw(void) {
  SPI_LINK_CP(10);
  LL_AHB2_GRP1_EnableClock(LL_AHB2_GRP1_PERIPH_GPIOB | LL_AHB2_GRP1_PERIPH_GPIOG);
  SPI_LINK_CP(11);
  LL_APB3_GRP1_EnableClock(LL_APB3_GRP1_PERIPH_SPI3);
  SPI_LINK_CP(12);
  /* GPDMA1 clock: idempotent with mic_sampler.cpp's own enable of the same
   * bits - harmless if already on (setup() order: mic_sampler_start() then
   * spi_link_start()). */
  LL_AHB1_GRP1_EnableClock(LL_AHB1_GRP1_PERIPH_GPDMA1);
  SPI_LINK_CP(13);
  RCC->AHB1SMENR |= RCC_AHB1SMENR_GPDMA1SMEN;

  /* Bias matches the shipped overlay's spi3 pinctrl nodes (pull-down on
   * SCK/MISO/MOSI, pull-up on NSS) - kept identical even though slave mode with
   * hardware NSS mostly drives these itself, to match the intended board config. */
  SPI_LINK_CP(20);
  spi_link_configure_af_pin(GPIOG, LL_GPIO_PIN_9, 9, LL_GPIO_PULL_DOWN);   /* SCK */
  SPI_LINK_CP(21);
  spi_link_configure_af_pin(GPIOG, LL_GPIO_PIN_10, 10, LL_GPIO_PULL_DOWN); /* MISO */
  SPI_LINK_CP(22);
  spi_link_configure_af_pin(GPIOB, LL_GPIO_PIN_5, 5, LL_GPIO_PULL_DOWN);   /* MOSI */
  SPI_LINK_CP(23);
  spi_link_configure_af_pin(GPIOG, LL_GPIO_PIN_12, 12, LL_GPIO_PULL_UP);   /* NSS */

  spi_link_configure_spi();
}

/* Reset + fully reconfigure DMA channel 3 for one `len`-byte block, memory
 * (spi_link_frame_buf) -> peripheral (SPI3->TXDR) - the mirror image of
 * mic_sampler.cpp's mic_dma_configure_channel() (peripheral->memory for SAI1_A
 * RX). Same single-block/no-linked-list init (LSM_1LINK_EXECUTION + zero LL
 * offset) proven necessary there. */
static void spi_link_configure_dma(uint16_t offset, uint16_t len) {
  LL_DMA_ResetChannel(GPDMA1, SPI_LINK_DMA_CHANNEL);
  LL_DMA_SetDataTransferDirection(GPDMA1, SPI_LINK_DMA_CHANNEL, LL_DMA_DIRECTION_MEMORY_TO_PERIPH);
  LL_DMA_SetChannelPriorityLevel(GPDMA1, SPI_LINK_DMA_CHANNEL, LL_DMA_HIGH_PRIORITY);
  LL_DMA_SetSrcIncMode(GPDMA1, SPI_LINK_DMA_CHANNEL, LL_DMA_SRC_INCREMENT);
  LL_DMA_SetDestIncMode(GPDMA1, SPI_LINK_DMA_CHANNEL, LL_DMA_DEST_FIXED);
  /* Byte-width transfers: proven rock-solid for small (<=~256B) sub-transfers
   * (the 64B spike was 40/40). The 4KB frame is pulled as a sequence of these
   * chunks and reassembled on the MPU (docs/progress2.md 5.7 - word-DMA packing
   * that would let one big transfer through had an unresolved FIFO desync). */
  LL_DMA_SetSrcDataWidth(GPDMA1, SPI_LINK_DMA_CHANNEL, LL_DMA_SRC_DATAWIDTH_BYTE);
  LL_DMA_SetDestDataWidth(GPDMA1, SPI_LINK_DMA_CHANNEL, LL_DMA_DEST_DATAWIDTH_BYTE);
  LL_DMA_SetBlkHWRequest(GPDMA1, SPI_LINK_DMA_CHANNEL, LL_DMA_HWREQUEST_SINGLEBURST);
  LL_DMA_SetPeriphRequest(GPDMA1, SPI_LINK_DMA_CHANNEL, LL_GPDMA1_REQUEST_SPI3_TX);
  LL_DMA_SetTransferEventMode(GPDMA1, SPI_LINK_DMA_CHANNEL, LL_DMA_TCEM_BLK_TRANSFER);
  LL_DMA_SetSrcAllocatedPort(GPDMA1, SPI_LINK_DMA_CHANNEL, LL_DMA_SRC_ALLOCATED_PORT0);
  LL_DMA_SetDestAllocatedPort(GPDMA1, SPI_LINK_DMA_CHANNEL, LL_DMA_DEST_ALLOCATED_PORT0);
  LL_DMA_SetBlkDataLength(GPDMA1, SPI_LINK_DMA_CHANNEL, len);
  LL_DMA_ConfigAddresses(GPDMA1, SPI_LINK_DMA_CHANNEL,
                         (uint32_t)(uintptr_t)(spi_link_frame_buf + offset),
                         (uint32_t)(uintptr_t)&SPI3->TXDR);
  LL_DMA_SetLinkStepMode(GPDMA1, SPI_LINK_DMA_CHANNEL, LL_DMA_LSM_1LINK_EXECUTION);
  LL_DMA_SetLinkedListAddrOffset(GPDMA1, SPI_LINK_DMA_CHANNEL, 0);
  /* TCIE: ResetChannel above clears CCR (incl. any previously-set TCIE), so
   * this needs re-arming every time, not just once at init - see
   * spi_link_dma_isr's comment for why this exists. */
  LL_DMA_EnableIT_TC(GPDMA1, SPI_LINK_DMA_CHANNEL);

  /* Clear every channel status flag before arming. A flag latched from a
   * previous iteration (TC or an error) would make the wait loop below break out
   * instantly on every subsequent transfer - see SPI_LINK_ERROR_BACKOFF_MS. */
  LL_DMA_ClearFlag_TC(GPDMA1, SPI_LINK_DMA_CHANNEL);
  LL_DMA_ClearFlag_HT(GPDMA1, SPI_LINK_DMA_CHANNEL);
  LL_DMA_ClearFlag_DTE(GPDMA1, SPI_LINK_DMA_CHANNEL);
  LL_DMA_ClearFlag_ULE(GPDMA1, SPI_LINK_DMA_CHANNEL);
  LL_DMA_ClearFlag_USE(GPDMA1, SPI_LINK_DMA_CHANNEL);
  LL_DMA_ClearFlag_TO(GPDMA1, SPI_LINK_DMA_CHANNEL);
  LL_DMA_ClearFlag_SUSP(GPDMA1, SPI_LINK_DMA_CHANNEL);
  /* Clear any stale/spurious give left over from the previous transfer -
   * see spi_link_tc_sem's comment. */
  k_sem_reset(&spi_link_tc_sem);
}

/* Reads all four GPDMA error flags into the bitmask documented at
 * spi_link_last_error_flags - 0 means "no error flag set". */
static uint32_t spi_link_read_error_flags(void) {
  uint32_t flags = 0;
  if (LL_DMA_IsActiveFlag_DTE(GPDMA1, SPI_LINK_DMA_CHANNEL)) flags |= 1;
  if (LL_DMA_IsActiveFlag_ULE(GPDMA1, SPI_LINK_DMA_CHANNEL)) flags |= 2;
  if (LL_DMA_IsActiveFlag_USE(GPDMA1, SPI_LINK_DMA_CHANNEL)) flags |= 4;
  if (LL_DMA_IsActiveFlag_TO(GPDMA1, SPI_LINK_DMA_CHANNEL)) flags |= 8;
  return flags;
}

/* Stages the frame already sitting in spi_link_frame_buf (loaded by spi_arm):
 * TSIZE, DMA channel config (which also clears every latched flag), channel +
 * SPI enable. All non-blocking register writes - safe to run inline on Bridge's
 * thread from "spi_arm". TSIZE/SPE are set fresh each time (SPI3 disabled at both
 * entry and exit of a full arm/wait cycle - TSIZE can't change while enabled). */
static void spi_link_arm_frame(uint16_t offset, uint16_t len) {
  SPI_LINK_CP(201);
  LL_SPI_SetTransferSize(SPI3, len);
  SPI_LINK_CP(202);
  spi_link_configure_dma(offset, len);
  SPI_LINK_CP(203);
  LL_DMA_EnableChannel(GPDMA1, SPI_LINK_DMA_CHANNEL);
  SPI_LINK_CP(204);
  LL_SPI_Enable(SPI3);
  SPI_LINK_CP(205);
}

/* Waits (bounded) for the MPU to clock the armed frame out, then disarms. OK =
 * the DMA block completed (MPU clocked all bytes); TIMEOUT = it never did;
 * ERROR = a GPDMA error flag latched (see spi_link_last_error_flags). */
static enum spi_link_xfer_result spi_link_wait_transfer(void) {
  enum spi_link_xfer_result result = SPI_LINK_XFER_TIMEOUT;
  for (int i = 0; i < SPI_LINK_DMA_WAIT_TICKS; i++) {
    if (LL_DMA_GetBlkDataLength(GPDMA1, SPI_LINK_DMA_CHANNEL) == 0 ||
        LL_DMA_IsActiveFlag_TC(GPDMA1, SPI_LINK_DMA_CHANNEL)) {
      result = SPI_LINK_XFER_OK;
      break;
    }
    uint32_t error_flags = spi_link_read_error_flags();
    if (error_flags != 0) {
      spi_link_last_error_flags = error_flags;
      result = SPI_LINK_XFER_ERROR;
      break;
    }
    /* Interrupt-driven, not a plain sleep: spi_link_dma_isr gives this the
     * instant TC fires, so this usually returns almost immediately rather
     * than waiting out the full tick - see SPI_LINK_DMA_WAIT_TICK_MS's
     * comment for why a plain k_msleep poll wasn't fast enough. The timeout
     * bounds it the same way k_msleep did if a completion is ever missed. */
    k_sem_take(&spi_link_tc_sem, K_MSEC(SPI_LINK_DMA_WAIT_TICK_MS));
  }
  SPI_LINK_CP(206);

  /* DIAG (temporary): snapshot the SPI + DMA state at the moment the wait ended,
   * before we tear anything down, so get_spi_link_stats can show WHY a transfer
   * timed out (underrun vs stuck flag vs DMA never advanced). */
  spi_link_dbg_sr = SPI3->SR;
  spi_link_dbg_cr1 = SPI3->CR1;
  spi_link_dbg_dma_rem = LL_DMA_GetBlkDataLength(GPDMA1, SPI_LINK_DMA_CHANNEL);

  LL_SPI_Disable(SPI3);
  /* Full SPI3 peripheral reset (RCC APB3) to flush the TX FIFO and clear all
   * internal transfer state. SPE=0 + the IFCR flag clears (below) are NOT enough
   * under word packing: residual FIFO words carried over and desynced every frame
   * after the first (docs/progress2.md 5.7). The reset re-applies the CFG, so it
   * also subsumes the old EOT/UDR/... flag-clear hygiene, but we still clear the
   * flags explicitly first in case a future change drops the reset. */
  LL_SPI_ClearFlag_EOT(SPI3);
  LL_SPI_ClearFlag_TXTF(SPI3);
  LL_SPI_ClearFlag_UDR(SPI3);
  LL_SPI_ClearFlag_OVR(SPI3);
  LL_SPI_ClearFlag_MODF(SPI3);
  LL_SPI_ClearFlag_FRE(SPI3);
  LL_SPI_ClearFlag_SUSP(SPI3);
  SPI_LINK_CP(207);
  LL_DMA_ResetChannel(GPDMA1, SPI_LINK_DMA_CHANNEL);
  SPI_LINK_CP(208);
  return result;
}

/* RPC-triggered handshake (docs/progress2.md decision 3's documented fallback):
 * the fuser stages frames continuously; the MPU asks for the latest by calling
 * "spi_arm" over the (now bulk-free) Bridge UART, then clocks it out. Division of
 * labour: the "spi_arm" provider (Bridge's own thread, priority 5) loads the
 * pending frame into the DMA buffer and arms it inline - a handful of register
 * writes, nothing blocking - and wakes this thread, which owns the bounded
 * completion wait + disarm + stats. spi_link_busy serializes the two: an arm
 * while the previous wait is still in flight gets a clean "busy" reply (MPU
 * retries) instead of two threads poking the same DMA channel. */
static struct k_sem spi_link_armed_sem;
static volatile bool spi_link_busy = false;

/* Latched-frame state, recorded once per frame (spi_arm's offset==0 call, or
 * spi_arm_stream's single call). */
static uint16_t spi_link_cur_total = 0;
static uint16_t spi_link_cur_seq = 0;

/* Auto-advance state for the streamed multi-chunk pull (spi_arm_stream,
 * below) - only touched by spi_arm_stream (Bridge's thread, before the
 * sem-give) and spi_link_thread_entry (after take/mid-loop); spi_link_busy
 * already serializes the two so there's never concurrent access, same as
 * the plain spi_arm() path. */
static volatile bool spi_link_stream_mode = false;
static uint16_t spi_link_stream_chunk_size = 0;
static uint16_t spi_link_stream_offset = 0;   /* offset of the chunk currently armed */
static uint16_t spi_link_stream_cur_len = 0;  /* length of the chunk currently armed */

/* Owns the bounded completion wait for whatever's currently armed (by either
 * spi_arm or spi_arm_stream) + disarm + stats. In stream mode, a completed
 * chunk immediately re-arms the next one and loops straight back into
 * wait_transfer() - no RPC round trip, no re-taking the semaphore (spi_arm_
 * stream only gives it once, at the start of the frame). spi_link_busy stays
 * true for the WHOLE stream, not per-chunk, so a concurrent arm call
 * anywhere in the frame gets a clean "busy" (matches the pre-stream
 * contract: an arm while a wait is in flight never touches the DMA channel
 * concurrently). */
static void spi_link_thread_entry(void *, void *, void *) {
  SPI_LINK_CP(100);
  while (true) {
    k_sem_take(&spi_link_armed_sem, K_FOREVER);
    SPI_LINK_CP(101);
    /* Inner loop re-enters wait_transfer() directly for each auto-advanced
     * chunk - it must NOT go back through k_sem_take above: the semaphore is
     * only ever given once per frame (by spi_arm/spi_arm_stream), so doing
     * that would block forever on the second chunk of every stream. */
    bool advance;
    do {
      advance = false;
      switch (spi_link_wait_transfer()) {
        case SPI_LINK_XFER_OK:
          spi_link_completed_count++;
          if (spi_link_stream_mode) {
            spi_link_stream_offset += spi_link_stream_cur_len;
            if (spi_link_stream_offset < spi_link_cur_total) {
              uint32_t next_len = spi_link_cur_total - spi_link_stream_offset;
              if (next_len > spi_link_stream_chunk_size) next_len = spi_link_stream_chunk_size;
              spi_link_stream_cur_len = (uint16_t)next_len;
              spi_link_armed_count++;
              SPI_LINK_CP(211);
              spi_link_arm_frame(spi_link_stream_offset, spi_link_stream_cur_len);
              advance = true;
            } else {
              spi_link_stream_mode = false; /* whole frame streamed */
            }
          }
          break;
        case SPI_LINK_XFER_TIMEOUT:
          /* Already slept through the whole bounded wait loop. */
          spi_link_timeout_count++;
          spi_link_stream_mode = false;
          break;
        case SPI_LINK_XFER_ERROR:
          /* Error flags latch fast - back off so a persistent error can't become a
           * tight give/take cycle if the MPU re-arms aggressively. */
          spi_link_error_count++;
          spi_link_stream_mode = false;
          k_msleep(SPI_LINK_ERROR_BACKOFF_MS);
          break;
      }
      if (advance) {
        SPI_LINK_CP(210);
      }
    } while (advance);
    spi_link_busy = false;
    SPI_LINK_CP(209);
  }
}

K_THREAD_STACK_DEFINE(spi_link_thread_stack, SPI_LINK_THREAD_STACK_SIZE);
static struct k_thread spi_link_thread_data;

/* Poll counters from the MPU side. Order:
 * checkpoint,staged,armed,completed,timeouts,errors,last_error_flags. */
static String spi_link_get_stats() {
  return String(spi_link_checkpoint) + "," + String(spi_link_staged_count) + "," +
         String(spi_link_armed_count) + "," + String(spi_link_completed_count) + "," +
         String(spi_link_timeout_count) + "," + String(spi_link_error_count) + "," +
         String((unsigned long)spi_link_last_error_flags) +
         ",sr=0x" + String((unsigned long)spi_link_dbg_sr, HEX) +
         ",cr1=0x" + String((unsigned long)spi_link_dbg_cr1, HEX) +
         ",rem=" + String((unsigned long)spi_link_dbg_dma_rem) +
         ",isr=" + String((unsigned long)spi_link_isr_count);
}

/* The MPU's side of the handshake, now CHUNKED (docs/progress2.md 5.7): a single
 * ~4KB slave-TX transfer underruns, but small byte-DMA sub-transfers are rock
 * solid, so the MPU pulls the frame in pieces. spi_arm(offset, len):
 * - offset==0 LATCHES the current pending frame into frame_buf (stable for the
 *   whole pull) and records its total + seq; later offsets reuse that frame_buf.
 * - arms frame_buf[offset .. offset+chunk], chunk = min(len, total-offset).
 * - replies "<seq>,<total>,<chunk>" (so the MPU knows how many bytes to clock and
 *   can detect a frame change by seq); "busy" = previous chunk still in flight
 *   (retry); "empty" = nothing staged; "done" = offset past the end.
 * The MPU clocks <chunk> bytes over spidev, reassembles all chunks, and verifies
 * the whole-frame CRC (dropping/retrying on mismatch). Chunk size is chosen by
 * the MPU so it can be tuned without reflashing. */
static String spi_link_arm(String offset_s, String len_s) {
  if (spi_link_busy) {
    return String("busy");
  }
  uint32_t offset = (uint32_t)offset_s.toInt();
  uint32_t req = (uint32_t)len_s.toInt();
  if (req == 0) {
    return String("empty");
  }

  if (offset == 0) {
    k_mutex_lock(&spi_link_pending_lock, K_FOREVER);
    if (spi_link_pending_len == 0) {
      k_mutex_unlock(&spi_link_pending_lock);
      return String("empty");
    }
    spi_link_cur_total = spi_link_pending_len;
    spi_link_cur_seq = spi_link_pending_seq;
    memcpy(spi_link_frame_buf, spi_link_pending_buf, spi_link_cur_total);
    k_mutex_unlock(&spi_link_pending_lock);
  }

  if (spi_link_cur_total == 0) {
    return String("empty");
  }
  if (offset >= spi_link_cur_total) {
    return String("done");
  }
  uint32_t chunk = spi_link_cur_total - offset;
  if (chunk > req) chunk = req;

  spi_link_busy = true;
  spi_link_armed_count++;
  SPI_LINK_CP(200);
  spi_link_arm_frame((uint16_t)offset, (uint16_t)chunk);
  k_sem_give(&spi_link_armed_sem);
  return String(spi_link_cur_seq) + "," + String(spi_link_cur_total) + "," + String(chunk);
}

/* Streaming variant, added 2026-07-20 (docs/progress2.md 5.8 had flagged this
 * as a follow-up: "one arm that auto-advances chunks"). One RPC call arms the
 * WHOLE latched frame, chunk_size at a time; spi_link_thread_entry (above)
 * auto-advances to each next chunk itself the instant the previous one
 * completes - no further round trip until the whole frame is done. spi_arm()
 * above is untouched and stays available (tests/spi_link_test.py and ad-hoc
 * diagnostics still use the one-chunk-at-a-time form) - this is purely
 * additive.
 *
 * Why this is safe without a hardware ready line (PG13 "SPI RDY" isn't wired
 * to the MPU - docs/progress2.md 4.1 - so there's no way for the MCU to tell
 * the MPU "chunk N+1 is armed, go"): the MPU just reads chunk_size bytes
 * back-to-back off spidev, paced by STREAM_PACING_S (ingestion/
 * spi_reader.py), tuned to comfortably clear the MCU's worst-case re-arm
 * latency (bounded by SPI_LINK_DMA_WAIT_TICK_MS, tightened 5->1ms alongside
 * this same change). A read that still races ahead of the re-arm just clocks
 * whatever SPI3 happens to be holding for that one chunk - the existing
 * whole-frame CRC check on the MPU side catches it and retries the whole
 * frame, same safety net the chunked path already relies on. Worst case is a
 * wasted frame, never corruption or a hang.
 *
 * Replies "<seq>,<total>,<first_chunk_len>" - same shape as spi_arm - so the
 * MPU can size its first read. Every following chunk's length is
 * min(chunk_size, total-offset), identical arithmetic on both ends, so no
 * further reply is needed to size the rest. */
static String spi_link_arm_stream(String chunk_size_s) {
  if (spi_link_busy) {
    return String("busy");
  }
  uint32_t chunk_size = (uint32_t)chunk_size_s.toInt();
  if (chunk_size == 0) {
    return String("empty");
  }

  k_mutex_lock(&spi_link_pending_lock, K_FOREVER);
  if (spi_link_pending_len == 0) {
    k_mutex_unlock(&spi_link_pending_lock);
    return String("empty");
  }
  spi_link_cur_total = spi_link_pending_len;
  spi_link_cur_seq = spi_link_pending_seq;
  memcpy(spi_link_frame_buf, spi_link_pending_buf, spi_link_cur_total);
  k_mutex_unlock(&spi_link_pending_lock);

  uint32_t first_len = spi_link_cur_total;
  if (first_len > chunk_size) first_len = chunk_size;

  spi_link_stream_mode = true;
  spi_link_stream_chunk_size = (uint16_t)chunk_size;
  spi_link_stream_offset = 0;
  spi_link_stream_cur_len = (uint16_t)first_len;

  spi_link_busy = true;
  spi_link_armed_count++;
  SPI_LINK_CP(220);
  spi_link_arm_frame(0, (uint16_t)first_len);
  k_sem_give(&spi_link_armed_sem);
  return String(spi_link_cur_seq) + "," + String(spi_link_cur_total) + "," + String(first_len);
}

void spi_link_start(void) {
  SPI_LINK_CP(3);
  Bridge.begin(BRIDGE_BAUD); /* idempotent - matrix/rgb/accel/mic also call this */
  SPI_LINK_CP(4);
  spi_link_crc_init();
  k_mutex_init(&spi_link_pending_lock);
  k_sem_init(&spi_link_armed_sem, 0, 1);
  k_sem_init(&spi_link_tc_sem, 0, 1);
  Bridge.provide("get_spi_link_stats", spi_link_get_stats);
  Bridge.provide("spi_arm", spi_link_arm);
  Bridge.provide("spi_arm_stream", spi_link_arm_stream);
  SPI_LINK_CP(1);
  /* Give Bridge's own thread a scheduling slice to actually flush this
   * registration over the UART before risking a fault in the register-level work
   * below - a prior attempt's registration notify never reached the router
   * because a fault landed before it was transmitted (docs/progress2.md 4.5). */
  k_msleep(50);
  SPI_LINK_CP(2);

  spi_link_init_hw();

  /* Dynamic IRQ (CONFIG_DYNAMIC_INTERRUPTS=y on this core, confirmed via the
   * board's own .config - a build-time IRQ_CONNECT() isn't an option for an
   * llext) for GPDMA1 channel 3's transfer-complete line - see
   * spi_link_dma_isr's comment. Priority 5: prompt (this is what
   * spi_arm_stream's whole benefit rides on) but not maximal - nothing else
   * in this sketch registers a custom IRQ to contend with, so there's no
   * specific value it needs to beat, just "faster than ordinary thread
   * scheduling," which any enabled NVIC interrupt already is. */
  irq_connect_dynamic(GPDMA1_Channel3_IRQn, 5, spi_link_dma_isr, NULL, 0);
  irq_enable(GPDMA1_Channel3_IRQn);

  SPI_LINK_CP(50);
  k_thread_create(&spi_link_thread_data, spi_link_thread_stack,
                  K_THREAD_STACK_SIZEOF(spi_link_thread_stack),
                  spi_link_thread_entry, NULL, NULL, NULL,
                  SPI_LINK_THREAD_PRIORITY, 0, K_NO_WAIT);
  k_thread_name_set(&spi_link_thread_data, "spi_link");
  SPI_LINK_CP(99);

#if SPI_LINK_SELFTEST
  /* Stage one fixed 4112-byte pattern so a fuser-disabled build can still be
   * clocked at full frame size (diagnostic - see SPI_LINK_SELFTEST). Pending
   * persists, so staging once is enough - spi_arm re-copies it each arm. */
  static uint8_t spi_link_selftest_payload[SPI_LINK_MAX_PAYLOAD];
  for (int i = 0; i < SPI_LINK_MAX_PAYLOAD; i++) {
    spi_link_selftest_payload[i] = (uint8_t)i;
  }
  spi_link_stage_frame(spi_link_selftest_payload, SPI_LINK_MAX_PAYLOAD);
#endif
}
