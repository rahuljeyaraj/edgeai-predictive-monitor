/*
 * MCU<->MPU dedicated SPI link, slave side - Risk-1 spike from
 * docs/progress2.md's "THE NEXT CHANGE": the board has a second SPI bus wired
 * directly between the MCU and MPU, separate from the Bridge UART and
 * currently dormant. This proves it moves bytes before any real transport
 * (the fuser stream) gets ported onto it.
 *
 * This is the register-level SPI3-slave + GPDMA1 TX fallback
 * (docs/progress2.md task 1's designated Plan B), used directly rather than
 * the Zephyr device API: the "clean" path (this core's Arduino `SPI1`
 * object, bound to the &spi3 bus node, in `SPISettings(..., SPI_PERIPHERAL)`
 * mode) hung setup()/the whole Bridge link with zero serial output,
 * reproduced twice on hardware. A follow-up attempt targeting the overlay's
 * `compatible = "zephyr,spi-slave"` child node (`device0`) directly via raw
 * Zephyr device API was disproven at link time - that node has no driver/
 * device instance in this firmware at all (`undefined reference to
 * __device_dts_ord_201`), so there was no alternate correct device handle to
 * have used instead. Root cause of the SPI1 hang was never isolated - see
 * docs/progress2.md 4.3/4.4 for the full history. This file sidesteps the
 * Zephyr SPI subsystem entirely, same reasoning as mic_sampler.cpp's
 * SAI/GPDMA1 work: register-level + GPDMA is always available even when a
 * Zephyr driver path is missing, broken, or (as here) unexplained.
 *
 * Pin mux: SCK=PG9, MISO=PG10, MOSI=PB5, NSS=PG12, all **AF6** - decoded by
 * hand from the shipped overlay's raw `pinmux = < 0x... >` values
 * (arduino_uno_q_stm32u585xx.overlay via the generated
 * zephyr-arduino_uno_q_stm32u585xx.dts) against the actual STM32 encoding
 * (`zephyr/dt-bindings/pinctrl/stm32-pinctrl.h`'s `STM32_PINMUX(port, line,
 * mode) = ((port-'A') << 9) | ((line & 0xF) << 5) | (mode & 0x1F)`) - e.g.
 * SCK's `0xd26` decodes to port=6('G'), line=9, mode=6(AF6). All four pins
 * decoded to the same AF6, which is SPI3's alternate function on this chip
 * (stm32u585aiixq).
 *
 * GPDMA1 request line: `LL_GPDMA1_REQUEST_SPI3_TX` = 11 (stm32u5xx_ll_dma.h)
 * - fixed hardware wiring, not a choice. Uses GPDMA1 channel 3 (mic_sampler.cpp
 * owns channel 2 for SAI1_A RX - the two never run concurrently on the same
 * channel).
 *
 * SPI3 config (CFG1/CFG2, all "can't change while SPI enabled" fields) is
 * set once in spi_link_init_hw(): slave mode, full duplex, 8-bit words,
 * mode 0 (CPOL=0/CPHA=0 - arbitrary but must match the MPU's spidev config,
 * see base-station/host/spi_bridge.py), MSB first, **hardware NSS input**
 * (PG12 driven externally by the MPU's GENI master, no software CS
 * management needed on this side), Motorola frame format, DMA TX request
 * enabled. Per-iteration, only TSIZE (CR2, number of frames in this
 * transfer - required by this SPI IP for EOT/completion tracking, unlike
 * classic F4/F7-style SPI) and SPE (enable) toggle, mirroring
 * mic_sampler.cpp's "full reconfigure per block" pattern.
 *
 * Pattern staged: a 64-byte buffer, first 4 bytes a little-endian
 * transfer counter (increments once per re-arm, so successive MPU reads can
 * be told apart even if the link is idle between them), remaining 60 bytes a
 * fixed 0..59 ramp (easy to eyeball/assert against from the Python side -
 * see tests/spi_link_test.py). No CRC/framing beyond that - this is a wiring
 * probe, not the real frame format (docs/progress2.md task 4 defines that).
 *
 * Handshake: RPC-triggered (docs/progress2.md decision 3's documented
 * fallback - PG13/"SPI RDY" is not wired through to the MPU, see 4.1). The
 * MPU calls the "spi_arm" Bridge provider, which stages one frame inline and
 * answers with its counter; the MPU then clocks the frame out over spidev
 * and verifies the counter matches. The wait for the master's clock happens
 * on this module's own thread and is bounded (k_msleep-polled, like
 * mic_sampler.cpp's DMA wait) rather than blocking forever, so an MPU that
 * arms but never reads just counts a timeout - it does not hang this thread,
 * and (being DMA driven, not a busy-poll) it does not risk starving Bridge's
 * own thread either.
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
#include <cstring>

#define SPI_LINK_FRAME_LEN 64
#define SPI_LINK_DMA_CHANNEL LL_DMA_CHANNEL_3 /* mic_sampler.cpp owns channel 2 */

/* Bounded like mic_sampler.cpp's MIC_DMA_WAIT_TICKS: k_msleep-polled, not a
 * blocking/forever wait - no handshake yet, so the MPU may not read for a
 * while (or ever, during this spike). ~1s ceiling per re-arm before giving
 * up and trying a fresh frame. */
#define SPI_LINK_DMA_WAIT_TICK_MS 5
#define SPI_LINK_DMA_WAIT_TICKS 200
#define SPI_LINK_THREAD_STACK_SIZE 2048

/* Back-off after a transfer that ended in a DMA error flag (not a timeout -
 * the timeout path already slept its way through the wait loop). Same idiom
 * and value as mic_sampler_thread_entry()'s capture-failure back-off. This is
 * load-bearing, not politeness: an error flag latching on every re-arm would
 * otherwise turn the thread loop into a never-yielding spin - a priority-3
 * thread doing exactly that is the leading explanation for both SPI bring-up
 * hangs (docs/progress2.md 4.3/4.7; see also SPI_LINK_THREAD_PRIORITY's
 * comment in app_config.h, lowered to 8 for the same reason). */
#define SPI_LINK_ERROR_BACKOFF_MS 100

/* spi_link_transfer_once() outcomes - kept distinct so the stats provider
 * can tell "MPU never clocked us" (timeout, expected while there's no
 * handshake) from "the DMA itself failed" (error, never expected). */
enum spi_link_xfer_result {
  SPI_LINK_XFER_OK,
  SPI_LINK_XFER_TIMEOUT,
  SPI_LINK_XFER_ERROR,
};

static uint8_t spi_link_buf[SPI_LINK_FRAME_LEN];
static uint32_t spi_link_transfer_count = 0;
static uint32_t spi_link_completed_count = 0;
static uint32_t spi_link_timeout_count = 0;
static uint32_t spi_link_error_count = 0;
/* Bitmask of which GPDMA error flags were set on the most recent
 * SPI_LINK_XFER_ERROR (1=DTE data transfer, 2=ULE linked-list update,
 * 4=USE user setting, 8=TO trigger overrun) - the "why", where
 * spi_link_error_count is the "how often". */
static volatile uint32_t spi_link_last_error_flags = 0;

/* Checkpoint diagnostic: updated right before each risky register-level step
 * so a hang's last-reached value is visible from get_spi_link_stats() even
 * without a working serial monitor (which could not be gotten to reliably
 * capture output over this session's adb-shell harness - see
 * docs/progress2.md). volatile + plain int write is word-atomic on
 * Cortex-M, same "diagnostics, not correctness-critical" reasoning as
 * mic_sampler.cpp's mic_last_sr. Numbering has gaps on purpose, room to
 * insert finer-grained checkpoints later without renumbering everything. */
static volatile int spi_link_checkpoint = 0;

/* Post-mortem-findable mirror of the checkpoint. The known failure mode is a
 * total system freeze (BASEPRI stuck at irq_lock level, SysTick masked, every
 * thread asleep forever - see docs/progress2.md 4.8), where Bridge is dead
 * and get_spi_link_stats can never be read - but RAM survives and is
 * dumpable over SWD (/opt/openocd on the MPU, linuxgpiod adapter). The llext
 * loader copies .data into its RAM heap at a load-dependent address with no
 * symbol map, so the block is located by scanning the dump for the leading
 * magic word. Layout: [0] magic, [1] last checkpoint, [2] stamp counter
 * (liveness - distinguishes "stuck at CP x" from "still looping through x"),
 * [3] trailing magic to confirm the block wasn't a coincidental word. */
static volatile uint32_t spi_link_diag[4] = {0xC0FFEE01u, 0u, 0u, 0xC0FFEE02u};

#define SPI_LINK_CP(n)                       \
  (spi_link_checkpoint = (n),                \
   spi_link_diag[1] = (uint32_t)(n),         \
   spi_link_diag[2] = spi_link_diag[2] + 1u)

/* Re-fills spi_link_buf with the next counter value + ramp pattern (see
 * file header comment). Called right before each re-arm, never while the
 * DMA channel is enabled. */
static void spi_link_fill_pattern(void) {
  spi_link_transfer_count++;
  memcpy(spi_link_buf, &spi_link_transfer_count, sizeof(spi_link_transfer_count));
  for (size_t i = sizeof(spi_link_transfer_count); i < SPI_LINK_FRAME_LEN; i++) {
    spi_link_buf[i] = (uint8_t)(i - sizeof(spi_link_transfer_count));
  }
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

/* One-time bring-up: clocks, pin muxing, and every SPI3 CFG1/CFG2 field that
 * can't be changed once SPE is set - see file header comment. SPI3 is left
 * disabled (SPE=0) on return; the thread's loop enables it per-iteration. */
static void spi_link_init_hw(void) {
  SPI_LINK_CP(10);
  LL_AHB2_GRP1_EnableClock(LL_AHB2_GRP1_PERIPH_GPIOB | LL_AHB2_GRP1_PERIPH_GPIOG);
  SPI_LINK_CP(11);
  LL_APB3_GRP1_EnableClock(LL_APB3_GRP1_PERIPH_SPI3);
  SPI_LINK_CP(12);
  /* GPDMA1 clock: idempotent with mic_sampler.cpp's own enable of the same
   * bits (mic_sampler_init_dma()) - harmless if already on by the time this
   * runs (setup() order: ... mic_sampler_start() then spi_link_start()). */
  LL_AHB1_GRP1_EnableClock(LL_AHB1_GRP1_PERIPH_GPDMA1);
  SPI_LINK_CP(13);
  RCC->AHB1SMENR |= RCC_AHB1SMENR_GPDMA1SMEN;

  /* Bias matches the shipped overlay's spi3 pinctrl nodes (bias-pull-down on
   * SCK/MISO/MOSI, bias-pull-up on NSS) - kept identical even though slave
   * mode with hardware NSS mostly drives these itself, to match the known-
   * intended board config exactly. */
  SPI_LINK_CP(20);
  spi_link_configure_af_pin(GPIOG, LL_GPIO_PIN_9, 9, LL_GPIO_PULL_DOWN);   /* SCK */
  SPI_LINK_CP(21);
  spi_link_configure_af_pin(GPIOG, LL_GPIO_PIN_10, 10, LL_GPIO_PULL_DOWN); /* MISO */
  SPI_LINK_CP(22);
  spi_link_configure_af_pin(GPIOB, LL_GPIO_PIN_5, 5, LL_GPIO_PULL_DOWN);   /* MOSI */
  SPI_LINK_CP(23);
  spi_link_configure_af_pin(GPIOG, LL_GPIO_PIN_12, 12, LL_GPIO_PULL_UP);   /* NSS */

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

/* Reset + fully reconfigure DMA channel 3 for one SPI_LINK_FRAME_LEN block,
 * memory (spi_link_buf) -> peripheral (SPI3->TXDR) - the mirror image of
 * mic_sampler.cpp's mic_dma_configure_channel() (which is peripheral->memory
 * for SAI1_A RX). Same single-block/no-linked-list init
 * (LSM_1LINK_EXECUTION + zero LL offset) proven necessary there. */
static void spi_link_configure_dma(void) {
  LL_DMA_ResetChannel(GPDMA1, SPI_LINK_DMA_CHANNEL);
  LL_DMA_SetDataTransferDirection(GPDMA1, SPI_LINK_DMA_CHANNEL, LL_DMA_DIRECTION_MEMORY_TO_PERIPH);
  LL_DMA_SetChannelPriorityLevel(GPDMA1, SPI_LINK_DMA_CHANNEL, LL_DMA_HIGH_PRIORITY);
  LL_DMA_SetSrcIncMode(GPDMA1, SPI_LINK_DMA_CHANNEL, LL_DMA_SRC_INCREMENT);
  LL_DMA_SetDestIncMode(GPDMA1, SPI_LINK_DMA_CHANNEL, LL_DMA_DEST_FIXED);
  LL_DMA_SetSrcDataWidth(GPDMA1, SPI_LINK_DMA_CHANNEL, LL_DMA_SRC_DATAWIDTH_BYTE);
  LL_DMA_SetDestDataWidth(GPDMA1, SPI_LINK_DMA_CHANNEL, LL_DMA_DEST_DATAWIDTH_BYTE);
  LL_DMA_SetBlkHWRequest(GPDMA1, SPI_LINK_DMA_CHANNEL, LL_DMA_HWREQUEST_SINGLEBURST);
  LL_DMA_SetPeriphRequest(GPDMA1, SPI_LINK_DMA_CHANNEL, LL_GPDMA1_REQUEST_SPI3_TX);
  LL_DMA_SetTransferEventMode(GPDMA1, SPI_LINK_DMA_CHANNEL, LL_DMA_TCEM_BLK_TRANSFER);
  LL_DMA_SetSrcAllocatedPort(GPDMA1, SPI_LINK_DMA_CHANNEL, LL_DMA_SRC_ALLOCATED_PORT0);
  LL_DMA_SetDestAllocatedPort(GPDMA1, SPI_LINK_DMA_CHANNEL, LL_DMA_DEST_ALLOCATED_PORT0);
  LL_DMA_SetBlkDataLength(GPDMA1, SPI_LINK_DMA_CHANNEL, SPI_LINK_FRAME_LEN);
  LL_DMA_ConfigAddresses(GPDMA1, SPI_LINK_DMA_CHANNEL,
                         (uint32_t)(uintptr_t)spi_link_buf,
                         (uint32_t)(uintptr_t)&SPI3->TXDR);
  LL_DMA_SetLinkStepMode(GPDMA1, SPI_LINK_DMA_CHANNEL, LL_DMA_LSM_1LINK_EXECUTION);
  LL_DMA_SetLinkedListAddrOffset(GPDMA1, SPI_LINK_DMA_CHANNEL, 0);

  /* Clear every channel status flag before arming. Nothing else in this file
   * clears them, and a flag latched from a previous iteration (TC or an
   * error) would make the wait loop below break out instantly on every
   * subsequent transfer - see SPI_LINK_ERROR_BACKOFF_MS's comment for why an
   * instant-exit loop here is catastrophic and not just wasteful. */
  LL_DMA_ClearFlag_TC(GPDMA1, SPI_LINK_DMA_CHANNEL);
  LL_DMA_ClearFlag_HT(GPDMA1, SPI_LINK_DMA_CHANNEL);
  LL_DMA_ClearFlag_DTE(GPDMA1, SPI_LINK_DMA_CHANNEL);
  LL_DMA_ClearFlag_ULE(GPDMA1, SPI_LINK_DMA_CHANNEL);
  LL_DMA_ClearFlag_USE(GPDMA1, SPI_LINK_DMA_CHANNEL);
  LL_DMA_ClearFlag_TO(GPDMA1, SPI_LINK_DMA_CHANNEL);
  LL_DMA_ClearFlag_SUSP(GPDMA1, SPI_LINK_DMA_CHANNEL);
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

/* Stages one frame: pattern fill, TSIZE, DMA channel config (which also
 * clears every latched flag), channel + SPI enable. All non-blocking
 * register writes - safe to run inline on Bridge's thread from the
 * "spi_arm" provider. TSIZE and SPE are set fresh each time (SPI3 disabled
 * at both entry and exit of a full arm/wait cycle) - TSIZE can't change
 * while SPI3 is enabled, so this can't be a lighter "just re-arm DMA"
 * update the way a steady-state master-driven link might do it. */
static void spi_link_arm_frame(void) {
  SPI_LINK_CP(200);
  spi_link_fill_pattern();

  SPI_LINK_CP(201);
  LL_SPI_SetTransferSize(SPI3, SPI_LINK_FRAME_LEN);
  SPI_LINK_CP(202);
  spi_link_configure_dma();
  SPI_LINK_CP(203);
  LL_DMA_EnableChannel(GPDMA1, SPI_LINK_DMA_CHANNEL);
  SPI_LINK_CP(204);
  LL_SPI_Enable(SPI3);
  SPI_LINK_CP(205);
}

/* Waits (bounded) for the MPU to clock the armed frame out, then disarms.
 * OK means the DMA block completed (MPU actually clocked all
 * SPI_LINK_FRAME_LEN bytes); TIMEOUT means the MPU never did (it asked to
 * arm but never read - counted, not fatal); ERROR means a GPDMA error flag
 * latched (see spi_link_last_error_flags for which). */
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
    k_msleep(SPI_LINK_DMA_WAIT_TICK_MS);
  }
  SPI_LINK_CP(206);

  LL_SPI_Disable(SPI3);
  /* Clear the latched SPI status flags (IFCR). SPE=0 flushes the FIFOs and
   * resets the transfer state machine, but NOT these - and a leftover EOT
   * from a completed transfer suppresses TXP (and with it the TX DMA
   * request) on the next SPE=1: hardware-measured 2026-07-14 as "first armed
   * frame perfect, every re-arm clocks out one stale byte + underrun zeros
   * with the DMA never moving a single byte". Same per-arm hygiene as the
   * GPDMA flag clears in spi_link_configure_dma(). */
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

/* RPC-triggered handshake (docs/progress2.md decision 3's documented
 * fallback, adopted in 4.1 once PG13/RDY turned out not to be wired to the
 * MPU): the MCU arms a frame only when the MPU asks for one over the (now
 * bulk-free) Bridge UART, then the MPU clocks it out immediately. This
 * replaces the free-running re-arm loop the first spike used - free-running
 * meant an MPU read raced the arm/disarm cycle and only ~1-2 reads in 10
 * landed on a freshly-armed frame (hardware-measured 2026-07-14); with the
 * arm serialized before each read, every read hits a staged frame.
 *
 * Division of labour: the "spi_arm" Bridge provider (runs on Bridge's own
 * thread, priority 5) does the arm inline - it's a handful of register
 * writes, nothing blocking - and wakes this thread, which owns the bounded
 * completion wait + disarm + stats. spi_link_busy serializes the two: an
 * arm while the previous wait is still in flight gets a clean "busy" reply
 * (MPU side just retries) instead of two threads poking the same DMA
 * channel. */
static struct k_sem spi_link_armed_sem;
static volatile bool spi_link_busy = false;

static void spi_link_thread_entry(void *, void *, void *) {
  SPI_LINK_CP(100);
  while (true) {
    k_sem_take(&spi_link_armed_sem, K_FOREVER);
    SPI_LINK_CP(101);
    switch (spi_link_wait_transfer()) {
      case SPI_LINK_XFER_OK:
        spi_link_completed_count++;
        break;
      case SPI_LINK_XFER_TIMEOUT:
        /* Already slept through the whole bounded wait loop. */
        spi_link_timeout_count++;
        break;
      case SPI_LINK_XFER_ERROR:
        /* Error flags latch fast - back off so a persistent error can't
         * become a tight give/take cycle if the MPU re-arms aggressively. */
        spi_link_error_count++;
        k_msleep(SPI_LINK_ERROR_BACKOFF_MS);
        break;
    }
    spi_link_busy = false;
    SPI_LINK_CP(209);
  }
}

K_THREAD_STACK_DEFINE(spi_link_thread_stack, SPI_LINK_THREAD_STACK_SIZE);
static struct k_thread spi_link_thread_data;

/* Diagnostics for the spike - not the real transport's Bridge surface (the
 * whole point is the bulk data doesn't go over Bridge/UART), just a way to
 * poll thread liveness/progress from the MPU side without needing the
 * serial monitor (which could not be gotten to reliably capture output over
 * this session's adb-shell harness). */
static String spi_link_get_stats() {
  return String(spi_link_checkpoint) + "," + String(spi_link_transfer_count) + "," +
         String(spi_link_completed_count) + "," + String(spi_link_timeout_count) + "," +
         String(spi_link_error_count) + "," + String((unsigned long)spi_link_last_error_flags);
}

/* The MPU's side of the handshake (see spi_link_thread_entry's comment):
 * stage one fresh frame and reply with the counter it carries, so the caller
 * can verify the frame it then clocks out is the one it asked for. Replies
 * "busy" (caller retries) while a previous arm is still being waited on. */
static String spi_link_arm(void) {
  if (spi_link_busy) {
    return String("busy");
  }
  spi_link_busy = true;
  spi_link_arm_frame();
  k_sem_give(&spi_link_armed_sem);
  return String(spi_link_transfer_count);
}

void spi_link_start(void) {
  SPI_LINK_CP(3);
  Bridge.begin(BRIDGE_BAUD); /* idempotent - matrix/rgb/accel/mic also call this */
  SPI_LINK_CP(4);
  k_sem_init(&spi_link_armed_sem, 0, 1);
  Bridge.provide("get_spi_link_stats", spi_link_get_stats);
  Bridge.provide("spi_arm", spi_link_arm);
  SPI_LINK_CP(1);
  /* Give Bridge's own thread a scheduling slice to actually flush this
   * registration over the UART before risking a fault in the register-level
   * work below - a prior attempt's registration notify never reached the
   * router at all (clean "not available" response, not a timeout), most
   * likely because a fault landed before the notify was ever transmitted.
   * See docs/progress2.md 4.5. */
  k_msleep(50);
  SPI_LINK_CP(2);

  spi_link_init_hw();

  SPI_LINK_CP(50);
  k_thread_create(&spi_link_thread_data, spi_link_thread_stack,
                  K_THREAD_STACK_SIZEOF(spi_link_thread_stack),
                  spi_link_thread_entry, NULL, NULL, NULL,
                  SPI_LINK_THREAD_PRIORITY, 0, K_NO_WAIT);
  k_thread_name_set(&spi_link_thread_data, "spi_link");
  SPI_LINK_CP(99);
}
