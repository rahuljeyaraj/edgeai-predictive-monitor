/*
 * External WS2812B 8-LED ring, ported from the old repo's rgb_display_thread
 * (edgeai-predictive-monitor-unoq/mcu/src/threads/rgb_display_thread.c) +
 * hal_display_rgb.h + drivers/rgb_ws2812.c: a periodic-tick thread rendering
 * the current color/mode/period command (CONST/BREATHE/STROBE), same sine-
 * breathe/square-strobe math, same struct/mutex-guarded-command shape.
 *
 * TRANSPORT: SPI1 master TX (MOSI only) + GPDMA1, NOT a bit-bang. The ring's
 * DIN (D4/PA12) is driven as SPI1_MOSI (AF5); each WS2812 bit is encoded as one
 * SPI byte clocked out at 5 MHz, and GPDMA1 streams the encoded frame with zero
 * CPU involvement. This replaces the earlier irq_lock()+k_cycle_get_32() busy-
 * wait bit-bang, which had two hardware-confirmed bugs (2026-07-16, found via
 * tests/display_rgb_test.py):
 *   1. CONST yellow (and any color with two adjacent 0xFF wire bytes) rendered
 *      GREEN - the byte after a full-1s byte lost its high pulses to the per-bit
 *      k_cycle_get_32() re-sample jitter. Single-channel colors never exposed it.
 *   2. BREATHE/STROBE hung Bridge: ws2812_show() held irq_lock() for the whole
 *      ~240us frame, and re-rendering every 20ms from this priority-3 thread
 *      (above Bridge's update thread at priority 5) overran the 115200 RPC UART
 *      -> msgpack framer desync -> the MPU's Bridge.read_loop died on a non-UTF-8
 *      byte and every later Bridge.call timed out. (See app_config.h's priority
 *      notes and the "nothing above Bridge may have a non-yielding loop" rule.)
 * Hardware-timed SPI fixes (1): every bit is an identical, jitter-free 5 MHz
 * byte. DMA fixes (2): no irq_lock, no busy-wait - the SPI IP clocks the frame
 * out on its own while this thread sleeps, so Bridge's UART is never starved.
 *
 * Why SPI-as-WS2812 and not a led_strip device / Arduino lib: the old repo drove
 * the ring over a zephyr,led_strip (worldsemi,ws2812-spi) node added by its own
 * board overlay - App Lab's arduino:zephyr toolchain exposes no board-overlay
 * hook to a sketch, and there's no bundled Arduino WS2812 library either. So we
 * drive the WS2812 waveform directly out of an SPI peripheral, the same trick
 * Adafruit_NeoPixel's SPI backends and the old repo's own SPI1 path used. This
 * is the register-level SPI1 + GPDMA1 path, mirroring spi_link.cpp's SPI3-slave
 * + GPDMA1 work (different peripheral instance, different DMA channel) - the
 * platform's own STM32Cube LL surface (modules/hal/stm32/...), always available
 * even where a Zephyr driver path is missing.
 *
 * Encoding: SPI @ 5 MHz (APB2 160 MHz / 32) => 200 ns per SPI bit, 1.6 us per
 * 8-bit byte = one WS2812 bit slot. MSB-first, so a byte's leading 1-bits form
 * the high pulse: WS2812 '0' -> 0xC0 (2 highs = 400 ns ~ T0H), '1' -> 0xF0
 * (4 highs = 800 ns ~ T1H). One SPI byte per WS2812 bit keeps everything byte-
 * aligned (24 bytes/pixel). A run of >=200 trailing zero bytes (>= 320 us low,
 * matching the old rgb_ws2812.c's 300 us latch figure) ends each frame as the
 * WS2812 reset/latch; the ~19 ms idle between 50 Hz ticks is itself a huge reset.
 *
 * Rendering runs ONLY on rgb_display_thread (every RGB_DISPLAY_TICK_MS), so a
 * single thread owns the SPI1/DMA channel - no cross-thread arbitration. CONST
 * just renders at 100% scale each tick; the ring holds its last latch between
 * ticks, so a steady color is a steady color. set_rgb only latches the command.
 *
 * Bridge.provide() takes one combined String ("RRGGBB,mode,period_ms") rather
 * than matrix_display.cpp's two-provider split so color/mode/period latch
 * atomically, and the known Arduino_RPClite integer-argument bug (see
 * matrix_display.cpp) rules out a native numeric arg - parsed with
 * toInt()/strtoul() on this side.
 */
#include "rgb_display.h"

#include "app_config.h"

#include <Arduino_RouterBridge.h>
#define STM32U585xx
#include <stm32u5xx.h>
#include <stm32u5xx_ll_bus.h>
#include <stm32u5xx_ll_dma.h>
#include <stm32u5xx_ll_gpio.h>
#include <stm32u5xx_ll_spi.h>
#include <zephyr/kernel.h>
#include <cmath>
#include <cstdlib>

/* D4/PA12 - ring DIN, driven as SPI1_MOSI (AF5 on the stm32u585aiixq, decoded
 * from the shipped u5 pinctrl dtsi: spi1_mosi_pa12 = STM32_PINMUX('A',12,AF5)).
 * Same physical pin the old repo wired the ring to. */
#define RGB_DISPLAY_GPIO_PORT GPIOA
#define RGB_DISPLAY_GPIO_PIN LL_GPIO_PIN_12
#define RGB_DISPLAY_NUM_PIXELS 8

/* GPDMA1 channel 4: mic_sampler.cpp owns channel 2 (SAI1_A RX), spi_link.cpp
 * owns channel 3 (SPI3 TX) - this never shares either. Placed on GPDMA port 1
 * (mic RX + spi_link TX both sit on port 0), so the ring's 50 Hz bursts don't
 * add to the port-0 contention that already costs the mic ~8% OVR timeouts. */
#define RGB_DISPLAY_DMA_CHANNEL LL_DMA_CHANNEL_4

/* WS2812-over-SPI encoding, see header comment. */
#define RGB_WS_SPI_BIT0 0xC0 /* two leading highs @ 5 MHz = 400 ns ~ T0H */
/* 0xFC = six leading highs @ 5 MHz = 1200 ns high, 400 ns low. Widened from
 * 0xF0 (800 ns) -> 0xF8 (1000 ns) -> 0xFC: this particular ring misread long
 * runs of shorter highs as 0 (yellow's 16 consecutive 1-bits -> R byte dropped
 * -> green; 0xF8 got close but not pure yellow). 1200 ns high clears the ring's
 * '1' threshold with margin; 400 ns low ~ WS2812B T1L (0.45 us). */
#define RGB_WS_SPI_BIT1 0xFC
#define RGB_SPI_BITS_PER_COLOR 8
#define RGB_SPI_BYTES_PER_PIXEL (3 * RGB_SPI_BITS_PER_COLOR)          /* GRB, 1 SPI byte/bit = 24 */
#define RGB_SPI_DATA_BYTES (RGB_DISPLAY_NUM_PIXELS * RGB_SPI_BYTES_PER_PIXEL) /* 192 */
#define RGB_SPI_RESET_BYTES 200                                       /* >= 320 us low = latch */
#define RGB_SPI_BUF_LEN (RGB_SPI_DATA_BYTES + RGB_SPI_RESET_BYTES)    /* 392 */

/* DMA source. 4-byte aligned (harmless for the byte-width DMA, cheap to keep).
 * Statically zero-initialized, so the RGB_SPI_RESET_BYTES tail is already the
 * latch and is never written by rgb_spi_fill(). */
static uint8_t rgb_spi_buf[RGB_SPI_BUF_LEN] __attribute__((aligned(4)));

enum rgb_mode {
  RGB_MODE_CONST = 0,
  RGB_MODE_BREATHE = 1,
  RGB_MODE_STROBE = 2,
};

struct rgb_command {
  uint8_t r;
  uint8_t g;
  uint8_t b;
  rgb_mode mode;
  uint16_t period_ms;
  int64_t start_ms;
};

K_MUTEX_DEFINE(rgb_cmd_mtx);
static struct rgb_command current_cmd = {0, 0, 0, RGB_MODE_CONST, 0, 0};

/* DIAG (temporary): register snapshot of the PREVIOUS transfer, captured at the
 * top of each rgb_spi_show(). Lets get_rgb_stats() report whether SPI1 is
 * actually clocking the frame out (rem should hit 0; EOT should set) or stuck.
 * Remove once the ring is confirmed working. */
static volatile uint32_t rgb_dbg_render_count = 0;
static volatile uint32_t rgb_dbg_sr = 0;
static volatile uint32_t rgb_dbg_cr1 = 0;
static volatile uint32_t rgb_dbg_cfg1 = 0;
static volatile uint32_t rgb_dbg_cfg2 = 0;
static volatile uint32_t rgb_dbg_dma_rem = 0xFFFFFFFFu;

/* Encode one 8-bit color value (MSB first) into 8 SPI bytes. */
static inline void rgb_encode_color(uint8_t val, uint8_t *out) {
  for (int i = 7; i >= 0; i--) {
    out[7 - i] = (val & (1 << i)) ? RGB_WS_SPI_BIT1 : RGB_WS_SPI_BIT0;
  }
}

/* Fill the SPI frame for all 8 pixels at one solid color. WS2812 wire order is
 * G,R,B per pixel. The reset tail is left untouched (statically zero). */
static void rgb_spi_fill(uint8_t r, uint8_t g, uint8_t b) {
  uint8_t *p = rgb_spi_buf;
  for (int i = 0; i < RGB_DISPLAY_NUM_PIXELS; i++) {
    rgb_encode_color(g, p);
    rgb_encode_color(r, p + RGB_SPI_BITS_PER_COLOR);
    rgb_encode_color(b, p + 2 * RGB_SPI_BITS_PER_COLOR);
    p += RGB_SPI_BYTES_PER_PIXEL;
  }
}

/* SPI1 CFG (master, simplex-TX, 8-bit, mode 0, MSB-first, 5 MHz, soft NSS held
 * high so master mode never faults MODF, TX DMA request). Set once; per frame
 * only TSIZE/SPE/CSTART move (TSIZE can't change while SPE=1). Leaves SPE=0. */
static void rgb_spi_configure_spi(void) {
  LL_SPI_SetMode(SPI1, LL_SPI_MODE_MASTER);
  LL_SPI_SetStandard(SPI1, LL_SPI_PROTOCOL_MOTOROLA);
  LL_SPI_SetTransferDirection(SPI1, LL_SPI_SIMPLEX_TX); /* MOSI only, no MISO/RX */
  LL_SPI_SetDataWidth(SPI1, LL_SPI_DATAWIDTH_8BIT);
  LL_SPI_SetClockPhase(SPI1, LL_SPI_PHASE_1EDGE);       /* mode 0 (SCK unused, but set sane) */
  LL_SPI_SetClockPolarity(SPI1, LL_SPI_POLARITY_LOW);
  LL_SPI_SetTransferBitOrder(SPI1, LL_SPI_MSB_FIRST);   /* leading 1-bits form the high pulse */
  LL_SPI_SetBaudRatePrescaler(SPI1, LL_SPI_BAUDRATEPRESCALER_DIV32); /* 160 MHz / 32 = 5 MHz */
  LL_SPI_SetNSSMode(SPI1, LL_SPI_NSS_SOFT);
  LL_SPI_SetInternalSSLevel(SPI1, LL_SPI_SS_LEVEL_HIGH); /* SSI=1: master sees NSS high, no MODF */
  LL_SPI_SetFIFOThreshold(SPI1, LL_SPI_FIFO_TH_01DATA);
  LL_SPI_EnableDMAReq_TX(SPI1);
}

/* One-time bring-up: clocks, PA12 -> SPI1_MOSI AF5, SPI1 CFG. Enables GPDMA1's
 * clock itself - rgb_display_start() runs before mic_sampler/spi_link in setup(),
 * so it cannot assume they've turned GPDMA1 on yet (the enable is idempotent). */
static void rgb_spi_init_hw(void) {
  LL_AHB2_GRP1_EnableClock(LL_AHB2_GRP1_PERIPH_GPIOA);
  LL_APB2_GRP1_EnableClock(LL_APB2_GRP1_PERIPH_SPI1);
  LL_AHB1_GRP1_EnableClock(LL_AHB1_GRP1_PERIPH_GPDMA1);
  RCC->AHB1SMENR |= RCC_AHB1SMENR_GPDMA1SMEN;

  LL_GPIO_SetPinMode(RGB_DISPLAY_GPIO_PORT, RGB_DISPLAY_GPIO_PIN, LL_GPIO_MODE_ALTERNATE);
  LL_GPIO_SetPinSpeed(RGB_DISPLAY_GPIO_PORT, RGB_DISPLAY_GPIO_PIN, LL_GPIO_SPEED_FREQ_VERY_HIGH);
  LL_GPIO_SetPinPull(RGB_DISPLAY_GPIO_PORT, RGB_DISPLAY_GPIO_PIN, LL_GPIO_PULL_DOWN);
  LL_GPIO_SetAFPin_8_15(RGB_DISPLAY_GPIO_PORT, RGB_DISPLAY_GPIO_PIN, LL_GPIO_AF_5); /* PA12 = SPI1_MOSI */

  rgb_spi_configure_spi();
}

/* Reset + configure DMA channel 4 for one RGB_SPI_BUF_LEN block, memory
 * (rgb_spi_buf) -> peripheral (SPI1->TXDR). Byte width, single burst, on port 1.
 * Mirrors spi_link.cpp's spi_link_configure_dma(), incl. clearing every latched
 * status flag before arming. */
static void rgb_spi_configure_dma(void) {
  LL_DMA_ResetChannel(GPDMA1, RGB_DISPLAY_DMA_CHANNEL);
  LL_DMA_SetDataTransferDirection(GPDMA1, RGB_DISPLAY_DMA_CHANNEL, LL_DMA_DIRECTION_MEMORY_TO_PERIPH);
  LL_DMA_SetChannelPriorityLevel(GPDMA1, RGB_DISPLAY_DMA_CHANNEL, LL_DMA_LOW_PRIORITY_LOW_WEIGHT);
  LL_DMA_SetSrcIncMode(GPDMA1, RGB_DISPLAY_DMA_CHANNEL, LL_DMA_SRC_INCREMENT);
  LL_DMA_SetDestIncMode(GPDMA1, RGB_DISPLAY_DMA_CHANNEL, LL_DMA_DEST_FIXED);
  LL_DMA_SetSrcDataWidth(GPDMA1, RGB_DISPLAY_DMA_CHANNEL, LL_DMA_SRC_DATAWIDTH_BYTE);
  LL_DMA_SetDestDataWidth(GPDMA1, RGB_DISPLAY_DMA_CHANNEL, LL_DMA_DEST_DATAWIDTH_BYTE);
  LL_DMA_SetBlkHWRequest(GPDMA1, RGB_DISPLAY_DMA_CHANNEL, LL_DMA_HWREQUEST_SINGLEBURST);
  LL_DMA_SetPeriphRequest(GPDMA1, RGB_DISPLAY_DMA_CHANNEL, LL_GPDMA1_REQUEST_SPI1_TX);
  LL_DMA_SetTransferEventMode(GPDMA1, RGB_DISPLAY_DMA_CHANNEL, LL_DMA_TCEM_BLK_TRANSFER);
  LL_DMA_SetSrcAllocatedPort(GPDMA1, RGB_DISPLAY_DMA_CHANNEL, LL_DMA_SRC_ALLOCATED_PORT1);
  LL_DMA_SetDestAllocatedPort(GPDMA1, RGB_DISPLAY_DMA_CHANNEL, LL_DMA_DEST_ALLOCATED_PORT1);
  LL_DMA_SetBlkDataLength(GPDMA1, RGB_DISPLAY_DMA_CHANNEL, RGB_SPI_BUF_LEN);
  LL_DMA_ConfigAddresses(GPDMA1, RGB_DISPLAY_DMA_CHANNEL,
                         (uint32_t)(uintptr_t)rgb_spi_buf,
                         (uint32_t)(uintptr_t)&SPI1->TXDR);
  LL_DMA_SetLinkStepMode(GPDMA1, RGB_DISPLAY_DMA_CHANNEL, LL_DMA_LSM_1LINK_EXECUTION);
  LL_DMA_SetLinkedListAddrOffset(GPDMA1, RGB_DISPLAY_DMA_CHANNEL, 0);

  LL_DMA_ClearFlag_TC(GPDMA1, RGB_DISPLAY_DMA_CHANNEL);
  LL_DMA_ClearFlag_HT(GPDMA1, RGB_DISPLAY_DMA_CHANNEL);
  LL_DMA_ClearFlag_DTE(GPDMA1, RGB_DISPLAY_DMA_CHANNEL);
  LL_DMA_ClearFlag_ULE(GPDMA1, RGB_DISPLAY_DMA_CHANNEL);
  LL_DMA_ClearFlag_USE(GPDMA1, RGB_DISPLAY_DMA_CHANNEL);
  LL_DMA_ClearFlag_TO(GPDMA1, RGB_DISPLAY_DMA_CHANNEL);
  LL_DMA_ClearFlag_SUSP(GPDMA1, RGB_DISPLAY_DMA_CHANNEL);
}

/* Bounded, YIELDING wait for the previous frame's DMA to finish before the
 * buffer is refilled/re-armed. k_msleep (not a busy-wait) so this priority-3
 * thread never starves Bridge - the whole point of the rewrite. A frame is
 * ~630 us and ticks are 20 ms apart, so in practice this returns on the first
 * check with zero sleeps; the loop only ever matters if a transfer stalled. */
static void rgb_spi_wait_idle(void) {
  for (int i = 0; i < 4; i++) {
    if (LL_DMA_GetBlkDataLength(GPDMA1, RGB_DISPLAY_DMA_CHANNEL) == 0) {
      return;
    }
    k_msleep(1);
  }
}

/* Render one solid color at scale_pct (0..100) via SPI1+GPDMA1. All register
 * writes are non-blocking; the SPI IP clocks the frame out on its own. */
static void rgb_spi_show(uint8_t r, uint8_t g, uint8_t b) {
  rgb_spi_wait_idle();

  /* DIAG: snapshot the state left by the previous transfer before we tear it
   * down and re-arm. */
  rgb_dbg_sr = SPI1->SR;
  rgb_dbg_cr1 = SPI1->CR1;
  rgb_dbg_cfg1 = SPI1->CFG1;
  rgb_dbg_cfg2 = SPI1->CFG2;
  rgb_dbg_dma_rem = LL_DMA_GetBlkDataLength(GPDMA1, RGB_DISPLAY_DMA_CHANNEL);
  rgb_dbg_render_count++;

  rgb_spi_fill(r, g, b);

  LL_SPI_Disable(SPI1);
  /* A latched MODF (mode fault) hardware-clears CFG2.MASTER and holds the IP in
   * slave mode until MODF is explicitly cleared (IFCR.MODFC) - in which state a
   * simplex-TX "master" waits forever for an external clock and never sends
   * (symptom: DMA block length never decrements). Clear MODF and re-assert
   * master every arm so a one-time fault (or any future one) self-recovers. */
  LL_SPI_ClearFlag_MODF(SPI1);
  LL_SPI_SetMode(SPI1, LL_SPI_MODE_MASTER);
  LL_SPI_ClearFlag_EOT(SPI1);
  LL_SPI_ClearFlag_TXTF(SPI1);
  LL_SPI_ClearFlag_UDR(SPI1);
  LL_SPI_SetTransferSize(SPI1, RGB_SPI_BUF_LEN);
  rgb_spi_configure_dma();
  LL_DMA_EnableChannel(GPDMA1, RGB_DISPLAY_DMA_CHANNEL);
  LL_SPI_Enable(SPI1);
  LL_SPI_StartMasterTransfer(SPI1); /* CSTART: begin clocking TSIZE bytes */
}

static void rgb_render(uint8_t r, uint8_t g, uint8_t b, uint8_t scale_pct) {
  rgb_spi_show((uint8_t)(((uint16_t)r * scale_pct) / 100),
               (uint8_t)(((uint16_t)g * scale_pct) / 100),
               (uint8_t)(((uint16_t)b * scale_pct) / 100));
}

/* Single Bridge provider - one combined String, see header comment. Runs on
 * Bridge's own update thread: just latch the command under the mutex and return.
 * ALL rendering (incl. CONST) happens on rgb_display_thread, so only one thread
 * ever touches the SPI1/DMA channel. */
static void rgb_display_set_command(String cmd) {
  int c1 = cmd.indexOf(',');
  int c2 = cmd.indexOf(',', c1 + 1);
  if (c1 < 0 || c2 < 0) {
    return;
  }

  String hex = cmd.substring(0, c1);
  String mode_str = cmd.substring(c1 + 1, c2);
  String period_str = cmd.substring(c2 + 1);

  uint32_t rgb = (uint32_t)strtoul(hex.c_str(), nullptr, 16);
  rgb_mode mode = (rgb_mode)mode_str.toInt();
  uint16_t period_ms = (uint16_t)period_str.toInt();

  k_mutex_lock(&rgb_cmd_mtx, K_FOREVER);
  current_cmd.r = (rgb >> 16) & 0xFF;
  current_cmd.g = (rgb >> 8) & 0xFF;
  current_cmd.b = rgb & 0xFF;
  current_cmd.mode = mode;
  current_cmd.period_ms = period_ms;
  current_cmd.start_ms = k_uptime_get();
  k_mutex_unlock(&rgb_cmd_mtx);
}

/* Same sine-breathe/square-strobe math as the old repo's hal_display_rgb_tick()
 * (drivers/rgb_ws2812.c). CONST renders at full scale each tick; the ring holds
 * its latch between ticks, so a steady color stays steady. */
#define RGB_DISPLAY_BREATHE_PI 3.14159265f

static void rgb_display_tick(void) {
  struct rgb_command cmd;

  k_mutex_lock(&rgb_cmd_mtx, K_FOREVER);
  cmd = current_cmd;
  k_mutex_unlock(&rgb_cmd_mtx);

  uint8_t scale_pct;
  if (cmd.mode == RGB_MODE_CONST || cmd.period_ms == 0) {
    scale_pct = 100;
  } else {
    int64_t elapsed = k_uptime_get() - cmd.start_ms;
    float phase = (float)(elapsed % cmd.period_ms) / (float)cmd.period_ms; /* 0..1 */
    if (cmd.mode == RGB_MODE_BREATHE) {
      scale_pct = (uint8_t)((1.0f - cosf(phase * 2.0f * RGB_DISPLAY_BREATHE_PI)) * 50.0f);
    } else {
      scale_pct = (phase < 0.5f) ? 100 : 0;
    }
  }

  rgb_render(cmd.r, cmd.g, cmd.b, scale_pct);
}

/* RGB_DISPLAY_THREAD_PRIORITY (app_config.h) == 3. The render path is now DMA-
 * driven (no irq_lock, no busy-wait), so being above Bridge's priority-5 thread
 * no longer risks starving it - see this file's header comment (bug #2). */
#define RGB_DISPLAY_THREAD_STACK_SIZE 1024

static void rgb_display_thread_entry(void *p1, void *p2, void *p3) {
  ARG_UNUSED(p1);
  ARG_UNUSED(p2);
  ARG_UNUSED(p3);

  while (1) {
    rgb_display_tick();
    k_msleep(RGB_DISPLAY_TICK_MS);
  }
}

/* DIAG (temporary): SPI1 + DMA state after the last frame. rem==0 & EOT set in
 * SR => the frame clocked out; rem stuck near RGB_SPI_BUF_LEN => SPI never
 * transmitted (clock/enable problem). Remove with the other DIAG bits once the
 * ring is confirmed. */
static String rgb_display_get_stats() {
  return String("n=") + String((unsigned long)rgb_dbg_render_count) +
         ",sr=0x" + String((unsigned long)rgb_dbg_sr, HEX) +
         ",cr1=0x" + String((unsigned long)rgb_dbg_cr1, HEX) +
         ",cfg1=0x" + String((unsigned long)rgb_dbg_cfg1, HEX) +
         ",cfg2=0x" + String((unsigned long)rgb_dbg_cfg2, HEX) +
         ",rem=" + String((unsigned long)rgb_dbg_dma_rem);
}

K_THREAD_STACK_DEFINE(rgb_display_thread_stack, RGB_DISPLAY_THREAD_STACK_SIZE);
static struct k_thread rgb_display_thread_data;

void rgb_display_start(void) {
  rgb_spi_init_hw();
  /* Blank the ring once up front (WS2812 pixels keep their last latch across a
   * reflash until told otherwise). Safe to render here: the thread isn't created
   * yet, so nothing else touches the SPI1/DMA channel. */
  rgb_spi_show(0, 0, 0);

  Bridge.begin(BRIDGE_BAUD); /* idempotent - matrix_display_start() also calls this */
  Bridge.provide("set_rgb", rgb_display_set_command);
  Bridge.provide("get_rgb_stats", rgb_display_get_stats); /* DIAG - temporary */

  k_thread_create(&rgb_display_thread_data, rgb_display_thread_stack,
                  K_THREAD_STACK_SIZEOF(rgb_display_thread_stack),
                  rgb_display_thread_entry, NULL, NULL, NULL,
                  RGB_DISPLAY_THREAD_PRIORITY, 0, K_NO_WAIT);
  k_thread_name_set(&rgb_display_thread_data, "rgb_display");
}
