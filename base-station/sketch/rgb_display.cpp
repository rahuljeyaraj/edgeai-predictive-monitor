/*
 * External WS2812B 8-LED ring, ported from the old repo's rgb_display_thread
 * (edgeai-predictive-monitor-unoq/mcu/src/threads/rgb_display_thread.c) +
 * hal_display_rgb.h + drivers/rgb_ws2812.c: a periodic-tick thread rendering
 * the current color/mode/period command (CONST/BREATHE/STROBE), same sine-
 * breathe/square-strobe math, same struct/mutex-guarded-command shape.
 *
 * TRANSPORT (2026-07-16, commit after 74ca11e): TIM3_CH3 PWM + GPDMA1, DMA
 * fed by the timer's own UPDATE event - NOT the SPI1-as-encoder trick used
 * previously. The ring's DIN moved from D4 (PA12) to D3 (PB0/TIM3_CH3, AF2):
 * PA12 has no TIMx_CHy alternate function on this exact package at all (only
 * SPI1_MOSI/FDCAN1_TX/OCTOSPI_NCS/USART1_DE-RTS/USB_OTG_FS_DP - confirmed via
 * modules/hal/stm32/dts/st/u5/stm32u585aiix-pinctrl.dtsi), so the SPI1+GPDMA1
 * design (74ca11e) could not be swapped for a timer in place. D3/PB0 was
 * chosen over the other free header pins because: D0(PB7)/D1(PB6) are the
 * board's console UART (usart1_rx/tx, arduino_uno_q-common.dtsi); D9(PB9) is
 * already mic_sampler.cpp's SAI1 FS/WS pin; TIM3_CH3 is a plain positive
 * channel on a simple general-purpose timer (unlike TIM1/TIM8's
 * complementary-only options on D5/D7, which also carry break-input
 * complexity this ring has no use for). TIM3 is not used anywhere else in
 * this sketch.
 *
 * Why a timer at all - this replaces the SPI1+GPDMA1 design (74ca11e), which
 * fixed two bugs (CONST-yellow-renders-green; BREATHE/STROBE hanging Bridge -
 * see docs/progress2.md and docs/progress3.md §1 for the full history) but
 * left a third, narrower one: pixel 0 (closest to DIN) consistently showed
 * the wrong color while pixels 1-7 were correct. Two SPI-side fixes were
 * tried and ruled out (docs/progress3.md §2); the working theory was that
 * SPI's CSTART (LL_SPI_StartMasterTransfer) - which cold-starts the SPI
 * clock generator fresh every frame - has a first-edge timing quirk on this
 * SPIv3-style peripheral that no "get the data there sooner" fix could
 * reach. A free-running hardware timer has no equivalent cold-start: once
 * enabled (done ONCE at init, see rgb_pwm_init_hw), TIM3 free-runs forever
 * generating one UPDATE event per WS2812 bit slot; DMA is only ever armed/
 * disarmed to feed its CCR3 duty register, the timer's own clock generator
 * is never toggled on a per-frame basis the way SPI1's was. This is the same
 * technique Adafruit_NeoPixel and most STM32 WS2812 drivers use.
 *
 * Encoding: TIM3 @ 160 MHz (APB1, prescaler=1 per board dtsi -> timer clock
 * == PCLK1, no x2), PSC=0, ARR=255 -> 256-count period = 1.6 us/bit, i.e. the
 * exact same bit period the SPI version used (8 SPI bits @ 5 MHz). CCR3 sets
 * the high time within that period (PWM mode 1: OC3 high while CNT<CCR3).
 * Reuses the SPI version's hardware-validated high times verbatim, just
 * expressed as CCR fractions of 256 instead of SPI-byte leading-1-bit counts:
 * WS2812 '0' -> CCR=64 (400 ns high, matches old 0xC0's 2/8 high), '1' ->
 * CCR=192 (1200 ns high, matches old 0xFC's 6/8 high - this ring needed T1H
 * widened past the WS2812B datasheet's nominal 800 ns, see docs/progress3.md
 * §1 bug 1 for why). One TIM3 UPDATE event = one WS2812 bit slot, MSB-first,
 * 24 slots/pixel (GRB). DMA is fed by TIM3's UPDATE DMA request (not a CCx
 * compare-match request): the standard "DMA writes the NEXT period's CCR
 * value each time the current period rolls over" pattern, glitch-free by
 * construction since OC preload (LL_TIM_OC_EnablePreload) defers each write
 * to the next update event automatically. A 200-slot all-zero tail (320 us
 * low, same margin the SPI version used for its reset/latch) follows the 192
 * data slots so the line is left low after each frame; the ~19 ms idle
 * between RGB_DISPLAY_TICK_MS ticks is itself a much bigger reset on top of
 * that.
 *
 * Rendering runs ONLY on rgb_display_thread (every RGB_DISPLAY_TICK_MS), so a
 * single thread owns the TIM3/DMA channel - no cross-thread arbitration.
 * CONST just renders at 100% scale each tick; the ring holds its last latch
 * between ticks, so a steady color is a steady color. set_rgb only latches
 * the command.
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
#include <stm32u5xx_ll_tim.h>
#include <zephyr/kernel.h>
#include <cmath>
#include <cstdlib>

/* D3/PB0 - ring DIN, driven as TIM3_CH3 (AF2 on the stm32u585aiixq, decoded
 * from the shipped u5 pinctrl dtsi: tim3_ch3_pb0 = STM32_PINMUX('B',0,AF2)).
 * Moved here from D4/PA12 (SPI1_MOSI, commit 74ca11e) - see header comment. */
#define RGB_DISPLAY_GPIO_PORT GPIOB
#define RGB_DISPLAY_GPIO_PIN LL_GPIO_PIN_0
#define RGB_DISPLAY_NUM_PIXELS 8

/* GPDMA1 channel 4: mic_sampler.cpp owns channel 2 (SAI1_A RX), spi_link.cpp
 * owns channel 3 (SPI3 TX) - this never shares either. Placed on GPDMA port 1
 * (mic RX + spi_link TX both sit on port 0), so the ring's 50 Hz bursts don't
 * add to the port-0 contention that already costs the mic ~8% OVR timeouts.
 * Same channel the SPI version used - only the peripheral request line and
 * destination register change (TIM3_UP -> TIM3->CCR3 instead of SPI1_TX ->
 * SPI1->TXDR). */
#define RGB_DISPLAY_DMA_CHANNEL LL_DMA_CHANNEL_4

/* WS2812-over-PWM encoding, see header comment. Period = ARR+1 = 256 counts
 * @ 160 MHz = 1.6 us/bit, matching the old SPI version's 8-bits-@-5-MHz byte
 * period so the already hardware-validated T0H/T1H carry over exactly. */
#define RGB_PWM_ARR 255
#define RGB_WS_PWM_CCR0 64  /* 400 ns high / 1200 ns low ~ T0H (was SPI 0xC0) */
#define RGB_WS_PWM_CCR1 192 /* 1200 ns high / 400 ns low ~ T1H (was SPI 0xFC) */
#define RGB_PWM_BITS_PER_COLOR 8
#define RGB_PWM_SLOTS_PER_PIXEL (3 * RGB_PWM_BITS_PER_COLOR)            /* GRB, 1 slot/bit = 24 */
#define RGB_PWM_DATA_SLOTS (RGB_DISPLAY_NUM_PIXELS * RGB_PWM_SLOTS_PER_PIXEL) /* 192 */
#define RGB_PWM_RESET_SLOTS 200                                          /* >= 320 us low = latch */
#define RGB_PWM_BUF_LEN (RGB_PWM_DATA_SLOTS + RGB_PWM_RESET_SLOTS)       /* 392 */

/* DMA source: one CCR3 duty value per WS2812 bit slot. 4-byte aligned
 * (harmless for the halfword-width DMA, cheap to keep). Statically zero-
 * initialized, so the RGB_PWM_RESET_SLOTS tail is already the latch/reset
 * value and is never written by rgb_pwm_fill(). */
static uint16_t rgb_pwm_buf[RGB_PWM_BUF_LEN] __attribute__((aligned(4)));

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

/* DIAG (temporary): register snapshot of the PREVIOUS transfer, captured at
 * the top of each rgb_pwm_show(). Lets get_rgb_stats() report whether TIM3/
 * DMA is actually clocking the frame out (rem should hit 0) or stuck.
 * Remove once the ring is confirmed working. */
static volatile uint32_t rgb_dbg_render_count = 0;
static volatile uint32_t rgb_dbg_sr = 0;
static volatile uint32_t rgb_dbg_cr1 = 0;
static volatile uint32_t rgb_dbg_ccr3 = 0;
static volatile uint32_t rgb_dbg_cnt = 0;
static volatile uint32_t rgb_dbg_dma_rem = 0xFFFFFFFFu;

/* Encode one 8-bit color value (MSB first) into 8 CCR duty values. */
static inline void rgb_encode_color(uint8_t val, uint16_t *out) {
  for (int i = 7; i >= 0; i--) {
    out[7 - i] = (val & (1 << i)) ? RGB_WS_PWM_CCR1 : RGB_WS_PWM_CCR0;
  }
}

/* Fill the PWM frame for all 8 pixels at one solid color. WS2812 wire order is
 * G,R,B per pixel. The reset tail is left untouched (statically zero). */
static void rgb_pwm_fill(uint8_t r, uint8_t g, uint8_t b) {
  uint16_t *p = rgb_pwm_buf;
  for (int i = 0; i < RGB_DISPLAY_NUM_PIXELS; i++) {
    rgb_encode_color(g, p);
    rgb_encode_color(r, p + RGB_PWM_BITS_PER_COLOR);
    rgb_encode_color(b, p + 2 * RGB_PWM_BITS_PER_COLOR);
    p += RGB_PWM_SLOTS_PER_PIXEL;
  }
}

/* TIM3 CH3 PWM config (mode 1, preload, DMA-on-update). Set ONCE at init and
 * left running forever - see header comment for why a free-running timer
 * (vs. SPI1's per-frame CSTART) is the whole point of this rewrite. Only the
 * DMA channel is armed/disarmed per frame; TIM3 itself is never re-enabled. */
static void rgb_pwm_configure_tim(void) {
  LL_TIM_SetCounterMode(TIM3, LL_TIM_COUNTERMODE_UP);
  LL_TIM_SetClockDivision(TIM3, LL_TIM_CLOCKDIVISION_DIV1);
  LL_TIM_SetPrescaler(TIM3, 0);
  LL_TIM_SetAutoReload(TIM3, RGB_PWM_ARR);

  LL_TIM_OC_SetMode(TIM3, LL_TIM_CHANNEL_CH3, LL_TIM_OCMODE_PWM1);
  LL_TIM_OC_SetPolarity(TIM3, LL_TIM_CHANNEL_CH3, LL_TIM_OCPOLARITY_HIGH);
  LL_TIM_OC_SetCompareCH3(TIM3, 0); /* idle low until a frame is armed */
  LL_TIM_OC_EnablePreload(TIM3, LL_TIM_CHANNEL_CH3);
  LL_TIM_CC_EnableChannel(TIM3, LL_TIM_CHANNEL_CH3);

  LL_TIM_EnableDMAReq_UPDATE(TIM3); /* DMA loads the NEXT period's CCR3 on each UPDATE */

  /* Force the shadow registers (ARR/CCR3 preload) to load immediately rather
   * than waiting for the first natural UPDATE, then clear the resulting
   * pending flags before the counter (and DMA-request generation) starts. */
  LL_TIM_GenerateEvent_UPDATE(TIM3);
  LL_TIM_ClearFlag_UPDATE(TIM3);

  LL_TIM_EnableCounter(TIM3); /* free-runs from here on, see header comment */
}

/* One-time bring-up: clocks, PB0 -> TIM3_CH3 AF2, TIM3 CFG. Enables GPDMA1's
 * clock itself - rgb_display_start() runs before mic_sampler/spi_link in setup(),
 * so it cannot assume they've turned GPDMA1 on yet (the enable is idempotent). */
static void rgb_pwm_init_hw(void) {
  LL_AHB2_GRP1_EnableClock(LL_AHB2_GRP1_PERIPH_GPIOB);
  LL_APB1_GRP1_EnableClock(LL_APB1_GRP1_PERIPH_TIM3);
  LL_AHB1_GRP1_EnableClock(LL_AHB1_GRP1_PERIPH_GPDMA1);
  RCC->AHB1SMENR |= RCC_AHB1SMENR_GPDMA1SMEN;
  RCC->APB1SMENR1 |= RCC_APB1SMENR1_TIM3SMEN; /* keep TIM3 ticking through CPU sleep too */

  LL_GPIO_SetPinMode(RGB_DISPLAY_GPIO_PORT, RGB_DISPLAY_GPIO_PIN, LL_GPIO_MODE_ALTERNATE);
  LL_GPIO_SetPinSpeed(RGB_DISPLAY_GPIO_PORT, RGB_DISPLAY_GPIO_PIN, LL_GPIO_SPEED_FREQ_VERY_HIGH);
  LL_GPIO_SetPinPull(RGB_DISPLAY_GPIO_PORT, RGB_DISPLAY_GPIO_PIN, LL_GPIO_PULL_DOWN);
  LL_GPIO_SetAFPin_0_7(RGB_DISPLAY_GPIO_PORT, RGB_DISPLAY_GPIO_PIN, LL_GPIO_AF_2); /* PB0 = TIM3_CH3 */

  rgb_pwm_configure_tim();
}

/* Reset + configure DMA channel 4 for one RGB_PWM_BUF_LEN block, memory
 * (rgb_pwm_buf) -> peripheral (TIM3->CCR3). Halfword width, single burst, on
 * port 1. Mirrors spi_link.cpp's spi_link_configure_dma(), incl. clearing
 * every latched status flag before arming. */
static void rgb_pwm_configure_dma(void) {
  LL_DMA_ResetChannel(GPDMA1, RGB_DISPLAY_DMA_CHANNEL);
  LL_DMA_SetDataTransferDirection(GPDMA1, RGB_DISPLAY_DMA_CHANNEL, LL_DMA_DIRECTION_MEMORY_TO_PERIPH);
  LL_DMA_SetChannelPriorityLevel(GPDMA1, RGB_DISPLAY_DMA_CHANNEL, LL_DMA_LOW_PRIORITY_LOW_WEIGHT);
  LL_DMA_SetSrcIncMode(GPDMA1, RGB_DISPLAY_DMA_CHANNEL, LL_DMA_SRC_INCREMENT);
  LL_DMA_SetDestIncMode(GPDMA1, RGB_DISPLAY_DMA_CHANNEL, LL_DMA_DEST_FIXED);
  LL_DMA_SetSrcDataWidth(GPDMA1, RGB_DISPLAY_DMA_CHANNEL, LL_DMA_SRC_DATAWIDTH_HALFWORD);
  LL_DMA_SetDestDataWidth(GPDMA1, RGB_DISPLAY_DMA_CHANNEL, LL_DMA_DEST_DATAWIDTH_HALFWORD);
  LL_DMA_SetBlkHWRequest(GPDMA1, RGB_DISPLAY_DMA_CHANNEL, LL_DMA_HWREQUEST_SINGLEBURST);
  LL_DMA_SetPeriphRequest(GPDMA1, RGB_DISPLAY_DMA_CHANNEL, LL_GPDMA1_REQUEST_TIM3_UP);
  LL_DMA_SetTransferEventMode(GPDMA1, RGB_DISPLAY_DMA_CHANNEL, LL_DMA_TCEM_BLK_TRANSFER);
  LL_DMA_SetSrcAllocatedPort(GPDMA1, RGB_DISPLAY_DMA_CHANNEL, LL_DMA_SRC_ALLOCATED_PORT1);
  LL_DMA_SetDestAllocatedPort(GPDMA1, RGB_DISPLAY_DMA_CHANNEL, LL_DMA_DEST_ALLOCATED_PORT1);
  LL_DMA_SetBlkDataLength(GPDMA1, RGB_DISPLAY_DMA_CHANNEL, RGB_PWM_BUF_LEN * sizeof(rgb_pwm_buf[0]));
  LL_DMA_ConfigAddresses(GPDMA1, RGB_DISPLAY_DMA_CHANNEL,
                         (uint32_t)(uintptr_t)rgb_pwm_buf,
                         (uint32_t)(uintptr_t)&TIM3->CCR3);
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
 * thread never starves Bridge - the whole point of the original SPI rewrite,
 * still true here since nothing about this loop blocks/disables interrupts.
 * A frame is ~630 us and ticks are 20 ms apart, so in practice this returns
 * on the first check with zero sleeps; the loop only ever matters if a
 * transfer stalled. */
static void rgb_pwm_wait_idle(void) {
  for (int i = 0; i < 4; i++) {
    if (LL_DMA_GetBlkDataLength(GPDMA1, RGB_DISPLAY_DMA_CHANNEL) == 0) {
      return;
    }
    k_msleep(1);
  }
}

/* Render one solid color at scale_pct (0..100) via TIM3+GPDMA1. All register
 * writes are non-blocking; TIM3 keeps free-running (never disabled here) and
 * DMA feeds it the frame on its own while this thread sleeps. */
static void rgb_pwm_show(uint8_t r, uint8_t g, uint8_t b) {
  rgb_pwm_wait_idle();

  /* DIAG: snapshot the state left by the previous transfer before we tear it
   * down and re-arm. */
  rgb_dbg_sr = TIM3->SR;
  rgb_dbg_cr1 = TIM3->CR1;
  rgb_dbg_ccr3 = TIM3->CCR3;
  rgb_dbg_cnt = TIM3->CNT;
  rgb_dbg_dma_rem = LL_DMA_GetBlkDataLength(GPDMA1, RGB_DISPLAY_DMA_CHANNEL);
  rgb_dbg_render_count++;

  rgb_pwm_fill(r, g, b);

  /* Gate TIM3's UPDATE->DMA request off for the reset+reconfigure below. TIM3
   * never stops counting (see header comment - no CSTART-equivalent), so
   * unlike spi_link.cpp/mic_sampler.cpp's peripherals (SPI3/SAI1, which only
   * request while a transfer is actively being clocked by data movement),
   * this channel's hardware request line is continuously live at 1/1.6us
   * regardless of whether DMA is listening. docs/progress3.md §8/§9: a solid
   * color that is repeatedly re-rendered (never a single one-shot send)
   * flickers whenever it isn't pure 0x00/0xFF per channel, and removing the
   * per-frame LL_DMA_ResetChannel() entirely (in favor of a configure-once/
   * rearm-only split) fixed it but introduced an unrelated intermittent
   * cold-boot failure - suggesting the reset itself is fine (it's what every
   * other GPDMA1 user in this sketch does every transfer) but doing it while
   * TIM3's request line is live can let a stray/latched request from
   * mid-reset land against a not-yet-consistent channel state, which for a
   * mixed 0/1-bit pattern shows up as a misaligned slot - invisible for
   * uniform 0x00/0xFF. Disabling the request bit (not CEN) brackets exactly
   * the reset+reconfigure window without touching the free-running counter. */
  LL_TIM_DisableDMAReq_UPDATE(TIM3);
  rgb_pwm_configure_dma();
  LL_DMA_EnableChannel(GPDMA1, RGB_DISPLAY_DMA_CHANNEL);
  LL_TIM_EnableDMAReq_UPDATE(TIM3);
  /* No CSTART-equivalent: TIM3 is already running (started once in
   * rgb_pwm_configure_tim) and will pull rgb_pwm_buf[0] into CCR3 at its very
   * next UPDATE event, whenever that naturally falls. */
}

static void rgb_render(uint8_t r, uint8_t g, uint8_t b, uint8_t scale_pct) {
  rgb_pwm_show((uint8_t)(((uint16_t)r * scale_pct) / 100),
               (uint8_t)(((uint16_t)g * scale_pct) / 100),
               (uint8_t)(((uint16_t)b * scale_pct) / 100));
}

/* Single Bridge provider - one combined String, see header comment. Runs on
 * Bridge's own update thread: just latch the command under the mutex and return.
 * ALL rendering (incl. CONST) happens on rgb_display_thread, so only one thread
 * ever touches the TIM3/DMA channel. */
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

/* RGB_DISPLAY_THREAD_PRIORITY (app_config.h) == 3. The render path is DMA-
 * driven (no irq_lock, no busy-wait), so being above Bridge's priority-5
 * thread doesn't risk starving it - see this file's header comment. */
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

/* DIAG (temporary): TIM3 + DMA state after the last frame. rem==0 => the
 * frame's DMA fetches all completed (TIM3 pulled every slot via its UPDATE
 * DMA request); rem stuck near RGB_PWM_BUF_LEN => DMA never fired (TIM3 not
 * generating UPDATE events, or DMA request never enabled). Remove with the
 * other DIAG bits once the ring is confirmed. */
static String rgb_display_get_stats() {
  return String("n=") + String((unsigned long)rgb_dbg_render_count) +
         ",sr=0x" + String((unsigned long)rgb_dbg_sr, HEX) +
         ",cr1=0x" + String((unsigned long)rgb_dbg_cr1, HEX) +
         ",ccr3=" + String((unsigned long)rgb_dbg_ccr3) +
         ",cnt=" + String((unsigned long)rgb_dbg_cnt) +
         ",rem=" + String((unsigned long)rgb_dbg_dma_rem);
}

K_THREAD_STACK_DEFINE(rgb_display_thread_stack, RGB_DISPLAY_THREAD_STACK_SIZE);
static struct k_thread rgb_display_thread_data;

void rgb_display_start(void) {
  rgb_pwm_init_hw();
  /* Blank the ring once up front (WS2812 pixels keep their last latch across a
   * reflash until told otherwise). Safe to render here: the thread isn't created
   * yet, so nothing else touches the TIM3/DMA channel. */
  rgb_pwm_show(0, 0, 0);

  Bridge.begin(BRIDGE_BAUD); /* idempotent - matrix_display_start() also calls this */
  Bridge.provide("set_rgb", rgb_display_set_command);
  Bridge.provide("get_rgb_stats", rgb_display_get_stats); /* DIAG - temporary */

  k_thread_create(&rgb_display_thread_data, rgb_display_thread_stack,
                  K_THREAD_STACK_SIZEOF(rgb_display_thread_stack),
                  rgb_display_thread_entry, NULL, NULL, NULL,
                  RGB_DISPLAY_THREAD_PRIORITY, 0, K_NO_WAIT);
  k_thread_name_set(&rgb_display_thread_data, "rgb_display");
}
