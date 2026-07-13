/*
 * External WS2812B 8-LED ring, ported from the old repo's rgb_display_thread
 * (edgeai-predictive-monitor-unoq/mcu/src/threads/rgb_display_thread.c) +
 * hal_display_rgb.h + drivers/rgb_ws2812.c: a periodic-tick thread rendering
 * the current color/mode/period command (CONST/BREATHE/STROBE), same sine-
 * breathe/square-strobe math, same struct/spinlock-then-mutex-guarded-command
 * shape - copied verbatim in spirit, just reached differently:
 *
 *  - No zephyr,led_strip device. The old repo drove the ring over SPI1 MOSI
 *    (D4/PA12) via Zephyr's worldsemi,ws2812-spi led_strip binding - a
 *    devicetree node added by that repo's own board overlay
 *    (boards/arduino_uno_q.overlay), which required a from-scratch Zephyr
 *    build to add. App Lab's arduino:zephyr toolchain doesn't let a sketch
 *    add devicetree nodes (no board overlay hook is exposed to
 *    apps/sketches), and unlike the onboard LED matrix there's no bundled
 *    Arduino-native library for an external WS2812 strip either - so this
 *    bit-bangs the WS2812 protocol directly instead of going through a
 *    led_strip device.
 *  - The bit-bang uses the same D4/PA12 pin the old repo's ring is wired to,
 *    timed off k_cycle_get_32() (confirmed CONFIG_SYS_CLOCK_HW_CYCLES_PER_SEC
 *    == 160000000 on this board via the installed core's generated
 *    autoconf.h, matching the old repo's own "SPI1 kernel clock is APB2 =
 *    160MHz" note). First attempt toggled the pin via Zephyr's
 *    gpio_pin_set_dt() (arduino_pins[4], cores/arduino/wiring_private.h -
 *    the same table digitalWrite() uses) - that compiled fine but produced a
 *    constant solid white on every command on real hardware, regardless of
 *    requested color/mode: gpio_pin_set_dt()'s driver-dispatch overhead
 *    (STM32 gpio driver call through Zephyr's device_api indirection) alone
 *    eats more cycles than a WS2812 "0" bit's entire ~64-cycle/0.4us high
 *    time budget, so every bit's physical high pulse - 0 or 1 - ends up
 *    longer than the WS2812's 1-bit threshold and gets decoded as 1,
 *    collapsing every byte to 0xFF. Fixed by toggling the pin with direct
 *    register writes instead - LL_GPIO_SetOutputPin()/ResetOutputPin() from
 *    STM32Cube's LL_GPIO driver (stm32u5xx_ll_gpio.h, __STATIC_INLINE - a
 *    single BSRR/BRR store, no driver-API call), the same primitive
 *    Adafruit_NeoPixel's own STM32 backend uses for exactly this reason. The
 *    full STM32Cube HAL/LL tree ships inside this core's llext-edk include
 *    path (modules/hal/stm32/stm32cube/stm32u5xx/...), confirmed
 *    test-compiling directly against it, so this is still within the
 *    platform's own exposed surface, not a hand-hacked memory address.
 *    k_cycle_get_32() is still the Zephyr kernel API (matrix_display.cpp
 *    already uses k_uptime_get()/k_msleep() from the same header) - the
 *    "hybrid RTOS integration" path the platform docs describe as
 *    supported.
 *  - Each full 8-pixel frame (192 bits @ 1.25us/bit =~ 240us) is sent with
 *    interrupts locked (irq_lock()/irq_unlock()) so no ISR/reschedule can
 *    stretch a bit's high or low pulse out of WS2812B's tolerance window -
 *    same worry the old repo's own comment on drivers/rgb_ws2812.c flags for
 *    its DMA-backed SPI write, just guarded here instead of offloaded to
 *    hardware. 240us every RGB_DISPLAY_TICK_MS (20ms) is a ~1.2% duty cycle
 *    of IRQs-off, negligible next to Bridge's own RPC latency.
 *  - Bridge.provide() takes one combined String ("RRGGBB,mode,period_ms")
 *    rather than matrix_display.cpp's two-separate-single-String-providers
 *    split: color/mode/period should latch together atomically (so CONST
 *    never briefly renders an old color under a new mode), and the known
 *    Arduino_RPClite integer-argument bug (see matrix_display.cpp's own
 *    comment) still rules out a native numeric argument, so it's one String,
 *    parsed with toInt()/strtoul() on this side, same workaround.
 */
#include "rgb_display.h"

#include <Arduino_RouterBridge.h>
#define STM32U585xx
#include <stm32u5xx.h>
#include <stm32u5xx_ll_gpio.h>
#include <zephyr/kernel.h>
#include <cmath>
#include <cstdlib>

/* D4/PA12 - ring DIN, same pin the old repo wired. GPIOA/PIN_12 is this
 * board's fixed physical mapping for Arduino header pin D4 (see the old
 * repo's boards/arduino_uno_q.overlay), not something this sketch chooses -
 * pinMode()/digitalWrite() go through the same pin under the hood. */
#define RGB_DISPLAY_GPIO_PORT GPIOA
#define RGB_DISPLAY_GPIO_PIN LL_GPIO_PIN_12
#define RGB_DISPLAY_PIN 4
#define RGB_DISPLAY_NUM_PIXELS 8

/* 160MHz, confirmed via CONFIG_SYS_CLOCK_HW_CYCLES_PER_SEC in this core's
 * generated autoconf.h - see header comment above. k_cycle_get_32() counts
 * at this rate. */
#define RGB_DISPLAY_HW_CYCLES_PER_SEC 160000000UL
#define RGB_DISPLAY_BIT_PERIOD_CYCLES (RGB_DISPLAY_HW_CYCLES_PER_SEC / 800000)  /* 1.25us */
#define RGB_DISPLAY_T0H_CYCLES (RGB_DISPLAY_HW_CYCLES_PER_SEC / 2500000)        /* 0.4us */
#define RGB_DISPLAY_T1H_CYCLES (RGB_DISPLAY_HW_CYCLES_PER_SEC / 1250000)        /* 0.8us */
/* Reset/latch gap after a frame - matches the old repo's rgb_ws2812.c's own
 * "300us latch delay" figure for this hardware. */
#define RGB_DISPLAY_RESET_US 300

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

static inline void ws2812_send_bit(bool one) {
  uint32_t t_start = k_cycle_get_32();
  uint32_t high_cycles = one ? RGB_DISPLAY_T1H_CYCLES : RGB_DISPLAY_T0H_CYCLES;

  LL_GPIO_SetOutputPin(RGB_DISPLAY_GPIO_PORT, RGB_DISPLAY_GPIO_PIN);
  while ((uint32_t)(k_cycle_get_32() - t_start) < high_cycles) {
  }
  LL_GPIO_ResetOutputPin(RGB_DISPLAY_GPIO_PORT, RGB_DISPLAY_GPIO_PIN);
  while ((uint32_t)(k_cycle_get_32() - t_start) < RGB_DISPLAY_BIT_PERIOD_CYCLES) {
  }
}

static void ws2812_send_byte(uint8_t b) {
  for (int i = 7; i >= 0; i--) {
    ws2812_send_bit(b & (1 << i));
  }
}

/* Pushes one solid-color frame to all 8 pixels. WS2812/WS2812B wire order is
 * G,R,B per pixel, not R,G,B. */
static void ws2812_show(uint8_t r, uint8_t g, uint8_t b) {
  unsigned int key = irq_lock();
  for (int i = 0; i < RGB_DISPLAY_NUM_PIXELS; i++) {
    ws2812_send_byte(g);
    ws2812_send_byte(r);
    ws2812_send_byte(b);
  }
  irq_unlock(key);
  k_busy_wait(RGB_DISPLAY_RESET_US);
}

static void rgb_render(uint8_t r, uint8_t g, uint8_t b, uint8_t scale_pct) {
  ws2812_show((uint8_t)(((uint16_t)r * scale_pct) / 100),
              (uint8_t)(((uint16_t)g * scale_pct) / 100),
              (uint8_t)(((uint16_t)b * scale_pct) / 100));
}

/* Single Bridge provider - see header comment for why this is one combined
 * String argument rather than matrix_display.cpp's two-provider split. Runs
 * on Bridge's own update thread: just latch the command under the mutex
 * (and render immediately for CONST) and return; BREATHE/STROBE rendering
 * happens on rgb_display_thread. */
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

  if (mode == RGB_MODE_CONST) {
    rgb_render(current_cmd.r, current_cmd.g, current_cmd.b, 100);
  }
}

/* Same sine-breathe/square-strobe math as the old repo's
 * hal_display_rgb_tick() (drivers/rgb_ws2812.c). */
#define RGB_DISPLAY_BREATHE_PI 3.14159265f

static void rgb_display_tick(void) {
  struct rgb_command cmd;

  k_mutex_lock(&rgb_cmd_mtx, K_FOREVER);
  cmd = current_cmd;
  k_mutex_unlock(&rgb_cmd_mtx);

  if (cmd.mode == RGB_MODE_CONST || cmd.period_ms == 0) {
    return;
  }

  int64_t elapsed = k_uptime_get() - cmd.start_ms;
  float phase = (float)(elapsed % cmd.period_ms) / (float)cmd.period_ms; /* 0..1 */
  uint8_t scale_pct;

  if (cmd.mode == RGB_MODE_BREATHE) {
    scale_pct = (uint8_t)((1.0f - cosf(phase * 2.0f * RGB_DISPLAY_BREATHE_PI)) * 50.0f);
  } else {
    scale_pct = (phase < 0.5f) ? 100 : 0;
  }

  rgb_render(cmd.r, cmd.g, cmd.b, scale_pct);
}

/* 3, matching matrix_display_thread and the old repo's own
 * RGB_DISPLAY_THREAD_PRIORITY - see matrix_display.cpp's priority-choice
 * comment for the shared rationale (preempt Bridge's priority-5 update
 * thread so visible timing doesn't inherit its scheduling jitter). */
#define RGB_DISPLAY_THREAD_STACK_SIZE 1024
#define RGB_DISPLAY_THREAD_PRIORITY 3
#define RGB_DISPLAY_TICK_MS 20

static void rgb_display_thread_entry(void *p1, void *p2, void *p3) {
  ARG_UNUSED(p1);
  ARG_UNUSED(p2);
  ARG_UNUSED(p3);

  while (1) {
    rgb_display_tick();
    k_msleep(RGB_DISPLAY_TICK_MS);
  }
}

K_THREAD_STACK_DEFINE(rgb_display_thread_stack, RGB_DISPLAY_THREAD_STACK_SIZE);
static struct k_thread rgb_display_thread_data;

void rgb_display_start(void) {
  pinMode(RGB_DISPLAY_PIN, OUTPUT);
  /* Blank the ring: like the old repo's hal_display_rgb_init(), WS2812
   * pixels keep whatever they last latched until told otherwise. */
  ws2812_show(0, 0, 0);

  Bridge.begin(); /* idempotent - matrix_display_start() also calls this */
  Bridge.provide("set_rgb", rgb_display_set_command);

  k_thread_create(&rgb_display_thread_data, rgb_display_thread_stack,
                  K_THREAD_STACK_SIZEOF(rgb_display_thread_stack),
                  rgb_display_thread_entry, NULL, NULL, NULL,
                  RGB_DISPLAY_THREAD_PRIORITY, 0, K_NO_WAIT);
  k_thread_name_set(&rgb_display_thread_data, "rgb_display");
}
