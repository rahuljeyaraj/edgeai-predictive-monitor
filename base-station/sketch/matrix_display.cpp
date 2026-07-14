/*
 * LED matrix display, ported from the old repo's matrix_display_thread
 * (edgeai-predictive-monitor-unoq/mcu/src/threads/matrix_display_thread.c)
 * + hal_display_matrix.h + drivers/led_matrix.c: a periodic-tick thread
 * that renders the current text/scroll-speed command into the onboard
 * 8-row x 13-col matrix's framebuffer every MATRIX_DISPLAY_TICK_MS, using
 * the exact same font_5x7/glyph_column/scroll-cycle-with-trailing-blank-
 * gap algorithm as the old repo's font_5x7[]/glyph_column()/
 * hal_display_matrix_tick() (drivers/led_matrix.c) - copied verbatim,
 * column-major layout and all.
 *
 * An 8-row-tall font (github.com/dhepper/font8x8) was tried first, to
 * use the matrix's full physical height instead of just 7 of its 8 rows.
 * Reverted after testing on hardware: that font's glyphs are also only 7
 * rows of actual content (row 8 is a blank line-spacing row, unused by
 * anything but descenders like g/j/p/q/y - same convention as this
 * font), so it bought no extra height, while its 8-column-wide glyphs
 * (vs. this font's 5) made scrolling text noticeably wider/slower to
 * read for no benefit. A real full-8-row font would need hand-designed,
 * non-standard glyph shapes - not attempted. So this is the old repo's
 * original 5x7 font/algorithm, unchanged.
 *
 * What else is different from the old repo, and why:
 *  - No hand-rolled charlieplex scan driver. The old repo bit-banged the
 *    matrix's GPIOF pins directly and drove a TIM17 counter ISR itself,
 *    because that repo had replaced Arduino's own firmware/loader with a
 *    from-scratch Zephyr build, so the board's own matrix support wasn't
 *    linkable. This repo builds through the real arduino:zephyr App Lab
 *    toolchain, where Arduino_LED_Matrix (bundled with the core) already
 *    drives the same physical matrix - confirmed same hardware via
 *    Arduino_LED_Matrix.h's canvasWidth/canvasHeight (13/8) matching
 *    HAL_MATRIX_ROWS/HAL_MATRIX_COLS below. matrix.loadPixels() takes a
 *    flat row-major on/off pixel buffer and does the scanning; this
 *    thread just has to fill that buffer each tick instead of writing
 *    GPIOF/timer registers directly.
 *  - Bridge.provide() split into two single-String-argument providers
 *    ("set_matrix_text", "set_matrix_scroll_speed") rather than one
 *    "set_matrix_text(text, scroll_speed_ms)" two-argument provider, and
 *    scroll speed is a String parsed with toInt() rather than a native
 *    integer RPC parameter. Both were tried first and confirmed broken
 *    on hardware: any integer-typed RPC argument (alone or alongside a
 *    String one) reliably failed Arduino_RPClite's argument type-check
 *    ("Wrong type parameter in position: N"), even though the exact
 *    wire bytes were verified correct by dumping msgpack.packb()'s
 *    output on-device. String arguments decoded correctly throughout, so
 *    everything goes over the wire as a String now. See
 *    base-station/tests/display_matrix_test.py for the MPU/Python side.
 */
#include "matrix_display.h"

#include "app_config.h"

#include <Arduino_RouterBridge.h>
#include <Arduino_LED_Matrix.h>
#include <zephyr/kernel.h>
#include <cstring>

/* Physical matrix dimensions, matching the old repo's hal_display_matrix.h
 * (HAL_MATRIX_ROWS/HAL_MATRIX_COLS) and confirmed against
 * Arduino_LED_Matrix.h's canvasHeight/canvasWidth. */
#define HAL_MATRIX_ROWS 8
#define HAL_MATRIX_COLS 13

/*
 * 5x7 dot-matrix font, ported verbatim from the old repo's
 * drivers/led_matrix.c: the standard glyph set used across countless
 * embedded character-LCD/LED-matrix projects (space..'Z', 0x20-0x5A
 * contiguous). Each glyph is 5 columns; bit r (0-6) of a column is row r
 * (0 = top). Row 7 is always blank. Lowercase is folded to uppercase by
 * glyph_column() - no separate lowercase glyphs.
 */
static const uint8_t font_5x7[][5] = {
    {0x00, 0x00, 0x00, 0x00, 0x00}, /* ' ' */
    {0x00, 0x00, 0x5F, 0x00, 0x00}, /* '!' */
    {0x00, 0x07, 0x00, 0x07, 0x00}, /* '"' */
    {0x14, 0x7F, 0x14, 0x7F, 0x14}, /* '#' */
    {0x24, 0x2A, 0x7F, 0x2A, 0x12}, /* '$' */
    {0x23, 0x13, 0x08, 0x64, 0x62}, /* '%' */
    {0x36, 0x49, 0x55, 0x22, 0x50}, /* '&' */
    {0x00, 0x05, 0x03, 0x00, 0x00}, /* ''' */
    {0x00, 0x1C, 0x22, 0x41, 0x00}, /* '(' */
    {0x00, 0x41, 0x22, 0x1C, 0x00}, /* ')' */
    {0x14, 0x08, 0x3E, 0x08, 0x14}, /* '*' */
    {0x08, 0x08, 0x3E, 0x08, 0x08}, /* '+' */
    {0x00, 0x50, 0x30, 0x00, 0x00}, /* ',' */
    {0x08, 0x08, 0x08, 0x08, 0x08}, /* '-' */
    {0x00, 0x60, 0x60, 0x00, 0x00}, /* '.' */
    {0x20, 0x10, 0x08, 0x04, 0x02}, /* '/' */
    {0x3E, 0x51, 0x49, 0x45, 0x3E}, /* '0' */
    {0x00, 0x42, 0x7F, 0x40, 0x00}, /* '1' */
    {0x42, 0x61, 0x51, 0x49, 0x46}, /* '2' */
    {0x21, 0x41, 0x45, 0x4B, 0x31}, /* '3' */
    {0x18, 0x14, 0x12, 0x7F, 0x10}, /* '4' */
    {0x27, 0x45, 0x45, 0x45, 0x39}, /* '5' */
    {0x3C, 0x4A, 0x49, 0x49, 0x30}, /* '6' */
    {0x01, 0x71, 0x09, 0x05, 0x03}, /* '7' */
    {0x36, 0x49, 0x49, 0x49, 0x36}, /* '8' */
    {0x06, 0x49, 0x49, 0x29, 0x1E}, /* '9' */
    {0x00, 0x36, 0x36, 0x00, 0x00}, /* ':' */
    {0x00, 0x56, 0x36, 0x00, 0x00}, /* ';' */
    {0x08, 0x14, 0x22, 0x41, 0x00}, /* '<' */
    {0x14, 0x14, 0x14, 0x14, 0x14}, /* '=' */
    {0x00, 0x41, 0x22, 0x14, 0x08}, /* '>' */
    {0x02, 0x01, 0x51, 0x09, 0x06}, /* '?' */
    {0x32, 0x49, 0x79, 0x41, 0x3E}, /* '@' */
    {0x7E, 0x11, 0x11, 0x11, 0x7E}, /* 'A' */
    {0x7F, 0x49, 0x49, 0x49, 0x36}, /* 'B' */
    {0x3E, 0x41, 0x41, 0x41, 0x22}, /* 'C' */
    {0x7F, 0x41, 0x41, 0x22, 0x1C}, /* 'D' */
    {0x7F, 0x49, 0x49, 0x49, 0x41}, /* 'E' */
    {0x7F, 0x09, 0x09, 0x09, 0x01}, /* 'F' */
    {0x3E, 0x41, 0x49, 0x49, 0x7A}, /* 'G' */
    {0x7F, 0x08, 0x08, 0x08, 0x7F}, /* 'H' */
    {0x00, 0x41, 0x7F, 0x41, 0x00}, /* 'I' */
    {0x20, 0x40, 0x41, 0x3F, 0x01}, /* 'J' */
    {0x7F, 0x08, 0x14, 0x22, 0x41}, /* 'K' */
    {0x7F, 0x40, 0x40, 0x40, 0x40}, /* 'L' */
    {0x7F, 0x02, 0x0C, 0x02, 0x7F}, /* 'M' */
    {0x7F, 0x04, 0x08, 0x10, 0x7F}, /* 'N' */
    {0x3E, 0x41, 0x41, 0x41, 0x3E}, /* 'O' */
    {0x7F, 0x09, 0x09, 0x09, 0x06}, /* 'P' */
    {0x3E, 0x41, 0x51, 0x21, 0x5E}, /* 'Q' */
    {0x7F, 0x09, 0x19, 0x29, 0x46}, /* 'R' */
    {0x46, 0x49, 0x49, 0x49, 0x31}, /* 'S' */
    {0x01, 0x01, 0x7F, 0x01, 0x01}, /* 'T' */
    {0x3F, 0x40, 0x40, 0x40, 0x3F}, /* 'U' */
    {0x1F, 0x20, 0x40, 0x20, 0x1F}, /* 'V' */
    {0x3F, 0x40, 0x38, 0x40, 0x3F}, /* 'W' */
    {0x63, 0x14, 0x08, 0x14, 0x63}, /* 'X' */
    {0x07, 0x08, 0x70, 0x08, 0x07}, /* 'Y' */
    {0x61, 0x51, 0x49, 0x45, 0x43}, /* 'Z' */
};

#define FONT_FIRST_CHAR ' '
#define FONT_LAST_CHAR 'Z'
#define FONT_GLYPH_COLS 5
#define FONT_GLYPH_STRIDE (FONT_GLYPH_COLS + 1) /* + 1 inter-character gap column */

static uint8_t glyph_column(char ch, int col) {
  if (ch >= 'a' && ch <= 'z') {
    ch = (char)(ch - 'a' + 'A');
  }
  if (ch < FONT_FIRST_CHAR || ch > FONT_LAST_CHAR || col >= FONT_GLYPH_COLS) {
    return 0;
  }

  return font_5x7[ch - FONT_FIRST_CHAR][col];
}

/* 1024, matching the old repo's MATRIX_DISPLAY_THREAD_STACK_SIZE. */
#define MATRIX_DISPLAY_THREAD_STACK_SIZE 1024
/*
 * MATRIX_DISPLAY_THREAD_PRIORITY (app_config.h) == 3, same as rgb_display_thread
 * - see that thread's own priority-choice rationale in the old repo
 * (threads/rgb_display_thread.c): a visible-timing render thread should
 * preempt the RPC/worker threads (Bridge's own background update thread
 * runs at priority 5, UPDATE_THREAD_PRIORITY in Arduino_RouterBridge's
 * bridge.h) rather than share their priority and inherit their
 * scheduling jitter. MATRIX_DISPLAY_TICK_MS (20ms) is also in app_config.h,
 * matching the old repo's own value.
 */
#define MATRIX_DISPLAY_MAX_TEXT_LEN 63

static Arduino_LED_Matrix matrix;

K_MUTEX_DEFINE(matrix_cmd_mtx);
static char matrix_cmd_text[MATRIX_DISPLAY_MAX_TEXT_LEN + 1];
static uint32_t matrix_cmd_scroll_speed_ms;
static uint32_t scroll_col;
static int64_t last_advance_ms;

/* Two single-argument Bridge providers, both taking Arduino's String, not
 * one two-argument "set_matrix_text(text, scroll_speed_ms)" or a numeric
 * scroll-speed argument. Two things were tried and confirmed broken on
 * real hardware first:
 *  - A two-argument (String, uint32_t) provider: the call reached the
 *    router and the sketch (wire bytes verified correct - fixarray[4],
 *    method name, fixarray[2] params, fixstr "HI", positive-fixint 0, by
 *    dumping the exact msgpack.packb() output on-device), but
 *    Arduino_RPClite's RpcFunctionWrapper rejected the second (uint32_t)
 *    argument every time with "Wrong type parameter in position: 1".
 *  - A single-argument uint32_t provider on its own (isolating whether it
 *    was an arity bug): still rejected, "Wrong type parameter in
 *    position: 0", again with wire bytes confirmed correct (fixarray[1],
 *    positive-fixint 0). So it's specifically integer-typed RPC
 *    parameters that this Arduino_RPClite build fails to decode, not a
 *    multi-arg dispatch issue - the String argument, by contrast,
 *    decoded correctly in both attempts (always at position 0). So
 *    scroll speed goes over the wire as a String too, parsed with
 *    toInt() on this side, sidestepping the integer path entirely.
 *
 * Both run on Bridge's own update thread, so just copy the command under
 * the mutex and return; rendering happens on matrix_display_thread.
 *
 * Takes Arduino's String, not std::string: on this platform
 * MsgPack::str_t (Arduino_RPClite's wire type for RPC string args) is
 * aliased to arduino::String, not std::string - confirmed by a build
 * failure with a std::string parameter here ("no known conversion ...
 * to 'const arduino::msgpack::str_t&' {aka 'const arduino::String&'}"). */
static void matrix_display_set_text(String text) {
  k_mutex_lock(&matrix_cmd_mtx, K_FOREVER);
  text.toCharArray(matrix_cmd_text, sizeof(matrix_cmd_text));
  scroll_col = 0;
  last_advance_ms = k_uptime_get();
  k_mutex_unlock(&matrix_cmd_mtx);
}

static void matrix_display_set_scroll_speed(String scroll_speed_ms) {
  k_mutex_lock(&matrix_cmd_mtx, K_FOREVER);
  matrix_cmd_scroll_speed_ms = (uint32_t)scroll_speed_ms.toInt();
  k_mutex_unlock(&matrix_cmd_mtx);
}

/* Renders the current command into the matrix's framebuffer - same
 * algorithm as the old repo's hal_display_matrix_tick() (scroll_col
 * advance gated on scroll_speed_ms elapsed, cycle_cols = text columns +
 * one full screen-width trailing blank gap so scrolling text fully exits
 * before looping), just producing a flat row-major on/off pixel buffer
 * for Arduino_LED_Matrix's loadPixels() instead of a hand-packed bitmask
 * for a hand-rolled scan ISR. */
static void matrix_display_tick(void) {
  char text[MATRIX_DISPLAY_MAX_TEXT_LEN + 1];
  uint32_t scroll_speed_ms;
  uint32_t col;

  k_mutex_lock(&matrix_cmd_mtx, K_FOREVER);
  strcpy(text, matrix_cmd_text);
  scroll_speed_ms = matrix_cmd_scroll_speed_ms;

  if (scroll_speed_ms != 0 && text[0] != '\0') {
    int64_t now = k_uptime_get();

    if (now - last_advance_ms >= scroll_speed_ms) {
      scroll_col++;
      last_advance_ms = now;
    }
  }
  col = scroll_col;
  k_mutex_unlock(&matrix_cmd_mtx);

  uint8_t next_fb[HAL_MATRIX_ROWS * HAL_MATRIX_COLS] = {0};
  uint32_t total_cols = (uint32_t)strlen(text) * FONT_GLYPH_STRIDE;
  uint32_t cycle_cols = total_cols + HAL_MATRIX_COLS;

  for (int c = 0; c < HAL_MATRIX_COLS && total_cols > 0; c++) {
    uint32_t logical_col = (col + (uint32_t)c) % cycle_cols;
    uint8_t bits = 0;

    if (logical_col < total_cols) {
      uint32_t char_idx = logical_col / FONT_GLYPH_STRIDE;
      uint32_t within = logical_col % FONT_GLYPH_STRIDE;

      if (within < FONT_GLYPH_COLS) {
        bits = glyph_column(text[char_idx], (int)within);
      }
    }

    /* Row 7 is always blank in font_5x7 (see its header comment), so
     * only rows 0-6 are ever set - matches the old repo's own
     * hal_display_matrix_tick() bound (r < 7). */
    for (int r = 0; r < HAL_MATRIX_ROWS && r < 7; r++) {
      if (bits & (1U << r)) {
        next_fb[r * HAL_MATRIX_COLS + c] = 1;
      }
    }
  }

  matrix.loadPixels(next_fb, sizeof(next_fb));
}

static void matrix_display_thread_entry(void *p1, void *p2, void *p3) {
  ARG_UNUSED(p1);
  ARG_UNUSED(p2);
  ARG_UNUSED(p3);

  while (1) {
    matrix_display_tick();
    k_msleep(MATRIX_DISPLAY_TICK_MS);
  }
}

K_THREAD_STACK_DEFINE(matrix_display_thread_stack, MATRIX_DISPLAY_THREAD_STACK_SIZE);
static struct k_thread matrix_display_thread_data;

void matrix_display_start(void) {
  matrix.begin();
  matrix.clear();

  Bridge.begin(BRIDGE_BAUD);
  Bridge.provide("set_matrix_text", matrix_display_set_text);
  Bridge.provide("set_matrix_scroll_speed", matrix_display_set_scroll_speed);

  k_thread_create(&matrix_display_thread_data, matrix_display_thread_stack,
                  K_THREAD_STACK_SIZEOF(matrix_display_thread_stack),
                  matrix_display_thread_entry, NULL, NULL, NULL,
                  MATRIX_DISPLAY_THREAD_PRIORITY, 0, K_NO_WAIT);
  k_thread_name_set(&matrix_display_thread_data, "matrix_display");
}
