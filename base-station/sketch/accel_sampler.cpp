/*
 * KX134-1211 SPI accelerometer, ported from the old repo's accel_sampler_thread
 * (edgeai-predictive-monitor-unoq/mcu/src/threads/accel_sampler_thread.c) +
 * hal_accel.h + drivers/kx134.c: continuously drains the KX134's hardware FIFO
 * over SPI (interrupt-driven off Buffer Full Interrupt, not polled), accumulates
 * three axes into ACCEL_FFT_LEN-sample windows, FFTs each axis independently and
 * sums bin-by-bin into one combined magnitude spectrum - same register map, same
 * FIFO framing (X_L,X_H,Y_L,Y_H,Z_L,Z_H per 16-bit frame), same summed-not-maxed
 * 3-axis reasoning (a fault signature split modestly across two or three axes
 * would be diluted by picking only the loudest axis - decided in the old repo's
 * project discussion, re-affirmed here, do not "simplify" back to single-axis or
 * RMS-before-FFT without re-confirming that decision). Two things are
 * fundamentally different, both forced by being back on App Lab instead of a
 * from-scratch Zephyr build - and, unlike mic_sampler.cpp/rgb_display.cpp, both
 * turned out to have a ready-made Arduino API rather than needing a
 * register-level bypass:
 *
 *  - SPI comes from this core's bundled `SPI` library, not Zephyr's spi_dt_spec
 *    (the old repo's kx134_spi). This core's shipped devicetree
 *    (arduino_uno_q_stm32u585xx.overlay, confirmed on-device) declares
 *    `spis = <&spi2>, <&spi3>;`, which is exactly two entries - the
 *    `ARDUINO_SPI_DEFINED_0`/`DT_PROP_LEN(...) > 1` branch in this core's SPI.h
 *    picks that apart into two named objects, `SPI` bound to the first phandle
 *    (&spi2) and `SPI1` to the second (&spi3) - so plain `SPI` here is spi2,
 *    the same peripheral the old repo drove directly. spi2's own default
 *    pinctrl (confirmed in the generated zephyr-arduino_uno_q_stm32u585xx.dts:
 *    `pinctrl-0 = <&spi2_sck_pb13 &spi2_miso_pb14 &spi2_mosi_pb15 &spi2_nss_pb9>`)
 *    routes it to this board's main Arduino header SCK/MISO/MOSI (D13/D12/D11) -
 *    the exact PB13/14/15 pin set the old repo found WHO_AM_I never read back
 *    correctly on with Zephyr's own spi_ll_stm32.c driver, before moving to the
 *    ICSP connector's PD1/PC2/PC3 (see that repo's drivers/kx134.c header
 *    comment). That failure was never root-caused beyond "something specific to
 *    the hardware SPI peripheral driving PB13/14/15", so there's real risk of
 *    it recurring here - see this file's own accel_sampler_start()/WHO_AM_I
 *    check and docs/PROGRESS.md for whether it actually did on real hardware.
 *    Chip-select is a software/GPIO pin, D8/PB4 (ACCEL_CS_PIN,
 *    digitalWrite'd manually around each transfer) - this repo's actual wiring,
 *    confirmed against the old repo's own final overlay (`&spi2`'s
 *    `cs-gpios = <&gpiob 4 GPIO_ACTIVE_LOW>`, i.e. D8, not the D10/PB9 its
 *    prose header comment describes - that comment describes an earlier
 *    design iteration and is stale relative to its own applied devicetree;
 *    the `int-gpios = <&gpiob 8 ...>` on the same node, D9/PB8, matches INT1
 *    below and *is* current). D8/PB4 isn't part of spi2's own default pinctrl
 *    (`pinctrl-0 = <&spi2_sck_pb13 &spi2_miso_pb14 &spi2_mosi_pb15 &spi2_nss_pb9>`,
 *    confirmed in the generated zephyr-arduino_uno_q_stm32u585xx.dts - D10/PB9
 *    is spi2's hardware NSS, D8/PB4 isn't referenced at all), so unlike a CS
 *    pin sharing a peripheral's own AF pin, there's no re-mux race with
 *    `SPI.begin()` to worry about here - plain `pinMode(ACCEL_CS_PIN, OUTPUT)`
 *    is enough, in either order relative to `SPI.begin()`.
 *  - INT1 (Buffer Full Interrupt) uses this core's `attachInterrupt()`
 *    (cores/arduino/WInterrupts.cpp, confirmed on-device) instead of the old
 *    repo's raw `gpio_pin_interrupt_configure_dt()` + `gpio_add_callback()` -
 *    functionally the same mechanism under the hood (WInterrupts.cpp's
 *    `attachInterrupt()` calls those exact two functions itself, keyed off the
 *    same `arduino_pins[]` table `digitalWrite()` uses), just reached through
 *    the standard Arduino API since this core happens to implement it, rather
 *    than a bypass.
 *
 * Everything else - register map, WHO_AM_I/CNTL1/ODCNTL/INC1/INC4/BUF_CNTL1/
 * BUF_CNTL2 values, BUF_STATUS-derived frame count, the FIFO burst-read shape,
 * 16-bit-to-32-bit sign extension, the hand-rolled radix-2 FFT (CMSIS-DSP isn't
 * in this core either, same as mic_sampler.cpp - re-derived here at
 * ACCEL_FFT_LEN=1024 instead of mic's 2048, not shared code, same reasoning as
 * that file's own comment on why) - mirrors the old repo as closely as the
 * platform allows. One deliberate parameter change: ODR is 1600Hz here
 * (KX134_ODCNTL_OSA_1600HZ), not the old repo's final tuned 12800Hz -
 * that value was arrived at through several rounds of hardware tuning specific
 * to *their* DMA-backed Zephyr spi_ll_stm32.c pipeline (docs/Sensor_Throughput_
 * Tuning_Plan.md in the old repo), which doesn't carry over to this core's
 * ZephyrSPI wrapper (not confirmed DMA-backed here). 1600Hz is that same repo's
 * own original, safe starting point before any of that tuning began - this port
 * prioritizes a correct first bring-up over throughput; revisit once this is
 * confirmed stable, same "measure before assuming" spirit as the old repo's own
 * tuning process.
 *
 * Bridge exposure, like mic_sampler.cpp, necessarily differs from the old repo
 * (which had none - accel_sampler_thread only ever fed the not-yet-ported
 * fuser_thread over an in-process msgq). Arduino_RPClite's 256-byte round-trip
 * ceiling (see mic_sampler.cpp's own comment) rules out sending all
 * ACCEL_FFT_BIN_COUNT=512 bins, so "get_accel_spectrum" exposes a further
 * average-pooled ACCEL_SPECTRUM_BINS=32-bucket view (16 bins/bucket), same
 * scheme as mic's "get_mic_spectrum". The sampler -> consumer handoff is also a
 * mutex-guarded static array here (accel_spectrum_mtx/accel_spectrum_latest),
 * not the old repo's `k_msgq` (accel_spectrum_msgq) - mirrors mic_sampler.cpp's
 * own mic_spectrum_mtx/mic_spectrum_latest exactly, since that's this repo's
 * established pattern for a single-latest-value producer/consumer handoff, not
 * a copy of the old repo's msgq mechanism.
 */
#include "accel_sampler.h"

#include <Arduino_RouterBridge.h>
#include <SPI.h>
#include <zephyr/kernel.h>
#include <cmath>
#include <cstring>

/* D8/PB4 - software/GPIO chip-select (see header comment: not part of spi2's
 * own default pinctrl, so no re-mux race with SPI.begin() to worry about).
 * D9/PB8 - KX134 INT1 (Buffer Full Interrupt only; INT2 is unused, same as the
 * old repo's design). Both pins match the old repo's final
 * boards/arduino_uno_q.overlay kx134@0 wiring (`cs-gpios`/`int-gpios`) - same
 * physical hardware, same header pins. */
#define ACCEL_CS_PIN 8
#define ACCEL_INT1_PIN 9

#define ACCEL_SPI_CLOCK_HZ 4000000 /* conservative vs. the KX134-1211's 10MHz ceiling, same as the old repo */

#define KX134_SPI_READ_BIT 0x80

/* Register map (KX134-1211 TRM Rev 5.0 S1.5/S1.7/S1.13/S1.14/S1.23) - same
 * addresses as the old repo's drivers/kx134.c, only the registers this driver
 * actually touches. */
#define KX134_REG_WHO_AM_I     0x13
#define KX134_REG_CNTL1        0x1B
#define KX134_REG_ODCNTL       0x21
#define KX134_REG_INC1         0x22
#define KX134_REG_INC4         0x25
#define KX134_REG_BUF_CNTL1    0x5E
#define KX134_REG_BUF_CNTL2    0x5F
#define KX134_REG_BUF_STATUS_1 0x60
#define KX134_REG_BUF_STATUS_2 0x61
#define KX134_REG_BUF_CLEAR    0x62
#define KX134_REG_BUF_READ     0x63

#define KX134_WHO_AM_I_VALUE 0x46

#define KX134_CNTL1_PC1 (1 << 7)
#define KX134_CNTL1_RES (1 << 6) /* High-Performance mode */
#define KX134_CNTL1_GSEL_8G 0x00 /* GSEL1:0 = 00 -> +/-8g, best LSB resolution for general vibration monitoring */
#define KX134_CNTL1_CONFIG_BITS (KX134_CNTL1_RES | KX134_CNTL1_GSEL_8G)

/* ODCNTL OSA<3:0> - 1600Hz, the old repo's own original/safe baseline (see this
 * file's header comment for why this port doesn't jump straight to that repo's
 * later-tuned 12800Hz). */
#define KX134_ODCNTL_OSA_1600HZ 0x0B
#define ACCEL_ODR_HZ 1600

#define KX134_INC1_IEN1 (1 << 5) /* physical INT1 pin enabled */
#define KX134_INC1_IEA1 (1 << 4) /* INT1 active HIGH */
#define KX134_INC1_IEL1 (1 << 3) /* pulsed (not latched) - see old repo's comment on why */
#define KX134_INC1_CONFIG (KX134_INC1_IEN1 | KX134_INC1_IEA1 | KX134_INC1_IEL1)

#define KX134_INC4_BFI1 (1 << 6) /* route Buffer Full Interrupt to INT1 (not Watermark) */
#define KX134_INC4_CONFIG KX134_INC4_BFI1

#define KX134_BUF_CNTL2_BUFE (1 << 7) /* sample buffer active */
#define KX134_BUF_CNTL2_BRES (1 << 6) /* 16-bit samples */
#define KX134_BUF_CNTL2_BFIE (1 << 5) /* buffer-full interrupt enabled */
#define KX134_BUF_CNTL2_BM_STREAM 0x01 /* Stream mode: discard oldest sample when full, don't stop */
#define KX134_BUF_CNTL2_CONFIG \
  (KX134_BUF_CNTL2_BUFE | KX134_BUF_CNTL2_BRES | KX134_BUF_CNTL2_BFIE | KX134_BUF_CNTL2_BM_STREAM)

#define KX134_BUF_CNTL1_SMP_TH 32 /* valid mid-capacity value; unused since this driver triggers off buffer-full, not watermark */

#define KX134_FIFO_MAX_FRAMES 86 /* TRM: 86 sets of 16-bit samples is the hardware buffer's cap */
#define KX134_FIFO_BYTES_PER_FRAME 6 /* X_L,X_H,Y_L,Y_H,Z_L,Z_H per frame */

#define ACCEL_READ_TIMEOUT_MS 1000 /* generous vs. one frame every ~0.625ms at 1600Hz ODR */

/* Signaled by accel_int1_isr() on every INT1/BFI pulse, taken by
 * accel_read_block() - capped at 1 (not a counting queue), same "only whether a
 * frame is ready *now* matters" reasoning as the old repo's kx134_data_ready_sem. */
K_SEM_DEFINE(accel_data_ready_sem, 0, 1);

/* Diagnostics only, exposed via get_accel_info() - not mutex-guarded, same
 * "single writer, torn read is harmless for a debugging aid" reasoning as
 * mic_sampler.cpp's mic_capture_timeouts/mic_last_sr. */
static volatile uint32_t accel_isr_count = 0;
static volatile uint32_t accel_read_count = 0;
static volatile uint32_t accel_timeout_count = 0;
static volatile uint32_t accel_fifo_full_count = 0;
static volatile uint8_t accel_who_am_i_seen = 0xFF; /* bring-up diagnostic, see accel_get_info() */
static volatile bool accel_sensor_ok = false;

static void accel_int1_isr() {
  accel_isr_count++;
  k_sem_give(&accel_data_ready_sem);
}

static bool kx134_write_reg(uint8_t addr, uint8_t value) {
  uint8_t buf[2] = {(uint8_t)(addr & 0x7F), value};

  SPI.beginTransaction(SPISettings(ACCEL_SPI_CLOCK_HZ, MSBFIRST, SPI_MODE0));
  digitalWrite(ACCEL_CS_PIN, LOW);
  SPI.transfer(buf, sizeof(buf));
  digitalWrite(ACCEL_CS_PIN, HIGH);
  SPI.endTransaction();
  return true;
}

/* Generic "send one command byte, then clock out len data bytes" read - same
 * mechanism for ordinary auto-incrementing register reads and the FIFO's
 * BUF_READ streaming read (KX134 TRM), nCS held low throughout via a single
 * SPI.transfer() call. Static scratch buffer sized for the largest possible
 * caller: one command byte + a full 86-frame FIFO burst. */
static void kx134_read_regs(uint8_t addr, uint8_t *data, size_t len) {
  static uint8_t buf[1 + KX134_FIFO_MAX_FRAMES * KX134_FIFO_BYTES_PER_FRAME];

  buf[0] = KX134_SPI_READ_BIT | (addr & 0x7F);
  memset(&buf[1], 0, len);

  SPI.beginTransaction(SPISettings(ACCEL_SPI_CLOCK_HZ, MSBFIRST, SPI_MODE0));
  digitalWrite(ACCEL_CS_PIN, LOW);
  SPI.transfer(buf, len + 1);
  digitalWrite(ACCEL_CS_PIN, HIGH);
  SPI.endTransaction();

  memcpy(data, &buf[1], len);
}

/* Blocks until at least one frame is available (INT1/BFI), then reads however
 * many whole frames the FIFO is actually holding (up to max_samples frames,
 * capped at KX134_FIFO_MAX_FRAMES regardless) into out_samples, interleaved as
 * [x0, y0, z0, x1, y1, z1, ...] - same contract as the old repo's
 * hal_accel_read_block(). Returns the number of frames written, or -1 on
 * timeout. */
static int accel_read_block(int32_t *out_samples, size_t max_samples) {
  static uint8_t raw[KX134_FIFO_MAX_FRAMES * KX134_FIFO_BYTES_PER_FRAME];

  accel_read_count++;

  if (k_sem_take(&accel_data_ready_sem, K_MSEC(ACCEL_READ_TIMEOUT_MS)) != 0) {
    accel_timeout_count++;
    return -1;
  }

  uint8_t status[2];

  kx134_read_regs(KX134_REG_BUF_STATUS_1, status, sizeof(status));
  uint16_t smp_lev = status[0] | ((uint16_t)(status[1] & 0x03) << 8);

  size_t frames = smp_lev / KX134_FIFO_BYTES_PER_FRAME;

  if (frames >= KX134_FIFO_MAX_FRAMES) {
    accel_fifo_full_count++;
  }
  if (frames > max_samples) {
    frames = max_samples;
  }
  if (frames > KX134_FIFO_MAX_FRAMES) {
    frames = KX134_FIFO_MAX_FRAMES;
  }

  kx134_read_regs(KX134_REG_BUF_READ, raw, frames * KX134_FIFO_BYTES_PER_FRAME);

  /* Little-endian 2's complement per axis, X_L,X_H,Y_L,Y_H,Z_L,Z_H per frame -
   * same layout as the old repo's hal_accel_read_block(). */
  for (size_t i = 0; i < frames; i++) {
    const uint8_t *p = &raw[i * KX134_FIFO_BYTES_PER_FRAME];
    int16_t x = (int16_t)(p[0] | (p[1] << 8));
    int16_t y = (int16_t)(p[2] | (p[3] << 8));
    int16_t z = (int16_t)(p[4] | (p[5] << 8));

    out_samples[i * 3 + 0] = x;
    out_samples[i * 3 + 1] = y;
    out_samples[i * 3 + 2] = z;
  }

  return (int)frames;
}

/* Frames requested per accel_read_block() call - divides ACCEL_FFT_LEN evenly
 * (1024/64=16 reads/window) and stays comfortably under KX134_FIFO_MAX_FRAMES,
 * same value and reasoning as the old repo's ACCEL_SAMPLER_READ_CHUNK_FRAMES. */
#define ACCEL_READ_CHUNK_FRAMES 64

#define ACCEL_FFT_BIN_COUNT 512
#define ACCEL_FFT_LEN (ACCEL_FFT_BIN_COUNT * 2) /* 1024 */
#define ACCEL_FFT_LOG2N 10 /* log2(1024) */

#define ACCEL_SPECTRUM_BINS 32 /* Bridge's 256-byte ceiling forces downsampling, same scheme as mic_sampler.cpp */
#define ACCEL_DOWNSAMPLE_FACTOR (ACCEL_FFT_BIN_COUNT / ACCEL_SPECTRUM_BINS)

#define ACCEL_FFT_PI 3.14159265f

/* Large working buffers are static (BSS), not thread-stack-allocated - same
 * reasoning as mic_sampler.cpp's own static capture/FFT buffers. */
static float accel_window_x[ACCEL_FFT_LEN];
static float accel_window_y[ACCEL_FFT_LEN];
static float accel_window_z[ACCEL_FFT_LEN];
static float accel_fft_re[ACCEL_FFT_LEN];
static float accel_fft_im[ACCEL_FFT_LEN];
static float accel_fft_twiddle_cos[ACCEL_FFT_LEN / 2];
static float accel_fft_twiddle_sin[ACCEL_FFT_LEN / 2];
static float accel_mag_x[ACCEL_FFT_LEN / 2];
static float accel_mag_y[ACCEL_FFT_LEN / 2];
static float accel_mag_z[ACCEL_FFT_LEN / 2];
static float accel_mag_combined[ACCEL_FFT_LEN / 2];

K_MUTEX_DEFINE(accel_spectrum_mtx);
static float accel_spectrum_latest[ACCEL_SPECTRUM_BINS];

static void accel_fft_init_twiddles() {
  for (int k = 0; k < ACCEL_FFT_LEN / 2; k++) {
    float angle = -2.0f * ACCEL_FFT_PI * (float)k / (float)ACCEL_FFT_LEN;
    accel_fft_twiddle_cos[k] = cosf(angle);
    accel_fft_twiddle_sin[k] = sinf(angle);
  }
}

/* Standard iterative radix-2 decimation-in-time Cooley-Tukey FFT, in place
 * over accel_fft_re[]/accel_fft_im[] - same structure as mic_sampler.cpp's
 * mic_fft_run(), re-derived at ACCEL_FFT_LEN=1024 (not shared code, see this
 * file's header comment for why). Reused sequentially for X, then Y, then Z. */
static void accel_fft_run() {
  const int n = ACCEL_FFT_LEN;
  int j = 0;

  for (int i = 0; i < n - 1; i++) {
    if (i < j) {
      float tr = accel_fft_re[i];
      accel_fft_re[i] = accel_fft_re[j];
      accel_fft_re[j] = tr;
      float ti = accel_fft_im[i];
      accel_fft_im[i] = accel_fft_im[j];
      accel_fft_im[j] = ti;
    }
    int m = n >> 1;
    while (m >= 1 && j >= m) {
      j -= m;
      m >>= 1;
    }
    j += m;
  }

  for (int stage = 1; stage <= ACCEL_FFT_LOG2N; stage++) {
    int m = 1 << stage;
    int half = m >> 1;
    int step = n / m;
    for (int start = 0; start < n; start += m) {
      for (int k = 0; k < half; k++) {
        float wr = accel_fft_twiddle_cos[k * step];
        float wi = accel_fft_twiddle_sin[k * step];
        int a = start + k;
        int b = a + half;
        float br = accel_fft_re[b] * wr - accel_fft_im[b] * wi;
        float bi = accel_fft_re[b] * wi + accel_fft_im[b] * wr;
        float ar = accel_fft_re[a];
        float ai = accel_fft_im[a];
        accel_fft_re[a] = ar + br;
        accel_fft_im[a] = ai + bi;
        accel_fft_re[b] = ar - br;
        accel_fft_im[b] = ai - bi;
      }
    }
  }
}

/* Same magnitude extraction as mic_fft_magnitude(): bin 0 (DC) discarded, bins
 * 1..N/2-1 via sqrt(re^2+im^2), bin N/2 (Nyquist) via fabsf() - no mirroring. */
static void accel_fft_magnitude(const float *window, float *out_mag) {
  for (int i = 0; i < ACCEL_FFT_LEN; i++) {
    accel_fft_re[i] = window[i];
    accel_fft_im[i] = 0.0f;
  }
  accel_fft_run();

  for (int k = 1; k < ACCEL_FFT_LEN / 2; k++) {
    float re = accel_fft_re[k];
    float im = accel_fft_im[k];
    out_mag[k - 1] = sqrtf(re * re + im * im);
  }
  out_mag[ACCEL_FFT_LEN / 2 - 1] = fabsf(accel_fft_re[ACCEL_FFT_LEN / 2]);
}

static void accel_spectrum_downsample(const float *mag, float *out) {
  for (int b = 0; b < ACCEL_SPECTRUM_BINS; b++) {
    float sum = 0.0f;
    for (int i = 0; i < ACCEL_DOWNSAMPLE_FACTOR; i++) {
      sum += mag[b * ACCEL_DOWNSAMPLE_FACTOR + i];
    }
    out[b] = sum / (float)ACCEL_DOWNSAMPLE_FACTOR;
  }
}

static String accel_get_spectrum() {
  float snapshot[ACCEL_SPECTRUM_BINS];

  k_mutex_lock(&accel_spectrum_mtx, K_FOREVER);
  memcpy(snapshot, accel_spectrum_latest, sizeof(snapshot));
  k_mutex_unlock(&accel_spectrum_mtx);

  String out;
  for (int i = 0; i < ACCEL_SPECTRUM_BINS; i++) {
    if (i > 0) {
      out += ",";
    }
    out += String((int)(snapshot[i] + 0.5f));
  }
  return out;
}

/* Self-describing metadata (odr/fft_len/full vs. exposed bin count), same
 * spirit as mic_get_info(), plus trailing who_am_i/ok/isr/read/timeout/
 * fifo_full counters as a hardware bring-up diagnostic aid (also mirroring
 * mic_get_info()'s trailing sr=/timeouts= fields) - who_am_i/ok are exposed
 * unconditionally (Bridge.provide() for this runs before the WHO_AM_I check,
 * see accel_sampler_start()) specifically so a WHO_AM_I mismatch is
 * observable from the MPU side without needing on-device console/debugger
 * access, same "no logging available here" constraint as the rest of this
 * repo's sketch code. */
static String accel_get_info() {
  String out;
  out += String(ACCEL_ODR_HZ);
  out += ",";
  out += String(ACCEL_FFT_LEN);
  out += ",";
  out += String(ACCEL_FFT_BIN_COUNT);
  out += ",";
  out += String(ACCEL_SPECTRUM_BINS);
  out += ",who_am_i=0x";
  out += String(accel_who_am_i_seen, HEX);
  out += ",ok=";
  out += String(accel_sensor_ok ? 1 : 0);
  out += ",isr=";
  out += String(accel_isr_count);
  out += ",reads=";
  out += String(accel_read_count);
  out += ",timeouts=";
  out += String(accel_timeout_count);
  out += ",fifo_full=";
  out += String(accel_fifo_full_count);
  return out;
}

#define ACCEL_SAMPLER_THREAD_STACK_SIZE 2048
/* 3, matching matrix_display_thread/rgb_display_thread, NOT mic_sampler_thread's
 * below-Bridge priority 7. Unlike mic's capture loop (a genuine non-blocking
 * busy-wait once SAI1 is streaming - see that file's own comment on why
 * priority 3 hung the entire Bridge link there), accel_read_block() above
 * always blocks on accel_data_ready_sem between FIFO drains, yielding the CPU
 * every single call the same way matrix/rgb's k_msleep() does every tick - so
 * this doesn't have mic's structural reason to run below Bridge's priority.
 * Worth re-verifying on hardware before trusting this reasoning blindly - see
 * docs/PROGRESS.md's mic_sampler_thread entry for how wrong the same
 * "it blocks/sleeps every tick, so it should be safe" assumption turned out to
 * be for a thread that couldn't actually guarantee that in its failure path. */
#define ACCEL_SAMPLER_THREAD_PRIORITY 3

static void accel_sampler_thread_entry(void *p1, void *p2, void *p3) {
  ARG_UNUSED(p1);
  ARG_UNUSED(p2);
  ARG_UNUSED(p3);

  static int32_t block[ACCEL_READ_CHUNK_FRAMES * 3];
  size_t frames_accumulated = 0;

  while (1) {
    int n = accel_read_block(block, ACCEL_READ_CHUNK_FRAMES);

    if (n < 0) {
      continue; /* timeout already counted by accel_read_block(); just retry */
    }

    for (int i = 0; i < n && frames_accumulated < ACCEL_FFT_LEN; i++) {
      accel_window_x[frames_accumulated] = (float)block[i * 3 + 0];
      accel_window_y[frames_accumulated] = (float)block[i * 3 + 1];
      accel_window_z[frames_accumulated] = (float)block[i * 3 + 2];
      frames_accumulated++;
    }

    if (frames_accumulated < ACCEL_FFT_LEN) {
      continue;
    }

    accel_fft_magnitude(accel_window_x, accel_mag_x);
    accel_fft_magnitude(accel_window_y, accel_mag_y);
    accel_fft_magnitude(accel_window_z, accel_mag_z);

    /* Bin-by-bin sum across axes - see this file's header comment on why
     * summing, not max-picking a dominant axis. */
    for (int i = 0; i < ACCEL_FFT_LEN / 2; i++) {
      accel_mag_combined[i] = accel_mag_x[i] + accel_mag_y[i] + accel_mag_z[i];
    }

    k_mutex_lock(&accel_spectrum_mtx, K_FOREVER);
    accel_spectrum_downsample(accel_mag_combined, accel_spectrum_latest);
    k_mutex_unlock(&accel_spectrum_mtx);

    frames_accumulated = 0;
  }
}

K_THREAD_STACK_DEFINE(accel_sampler_thread_stack, ACCEL_SAMPLER_THREAD_STACK_SIZE);
static struct k_thread accel_sampler_thread_data;

void accel_sampler_start(void) {
  /* Bridge providers are registered before the WHO_AM_I check, not after -
   * see accel_get_info()'s own comment for why: this makes a WHO_AM_I
   * mismatch observable from the MPU side (who_am_i=/ok= fields) instead of
   * silently leaving both providers missing the way an early return here
   * would. */
  Bridge.begin(); /* idempotent - matrix/rgb/mic also call this */
  Bridge.provide("get_accel_spectrum", accel_get_spectrum);
  Bridge.provide("get_accel_info", accel_get_info);

  accel_fft_init_twiddles();

  /* D8/PB4 isn't part of spi2's own pinctrl (see header comment), so unlike a
   * CS pin sharing a peripheral's AF pin, this order isn't load-bearing - just
   * kept SPI.begin()-then-pinMode() for readability (bus up before the pin
   * that talks to a device on it). */
  SPI.begin();
  pinMode(ACCEL_CS_PIN, OUTPUT);
  digitalWrite(ACCEL_CS_PIN, HIGH); /* idle high, active-low CS */
  pinMode(ACCEL_INT1_PIN, INPUT);

  uint8_t who_am_i;
  kx134_read_regs(KX134_REG_WHO_AM_I, &who_am_i, 1);
  accel_who_am_i_seen = who_am_i;
  if (who_am_i != KX134_WHO_AM_I_VALUE) {
    /* KX134 not responding - leave the sampling thread unstarted (same
     * "don't proceed into hardware assumed to be present" bail-out as
     * mic_sampler_start() on a clock failure), but Bridge providers stay
     * registered so get_accel_info()'s who_am_i=/ok= fields are still
     * reachable for diagnosis. */
    return;
  }
  accel_sensor_ok = true;

  /* Standby (PC1=0) before touching any other register - required by the TRM
   * for CNTL1/ODCNTL/INC1/INC4 writes, same as the old repo's hal_accel_init(). */
  kx134_write_reg(KX134_REG_CNTL1, 0x00);
  kx134_write_reg(KX134_REG_ODCNTL, KX134_ODCNTL_OSA_1600HZ);
  kx134_write_reg(KX134_REG_INC1, KX134_INC1_CONFIG);
  kx134_write_reg(KX134_REG_INC4, KX134_INC4_CONFIG);
  kx134_write_reg(KX134_REG_CNTL1, KX134_CNTL1_CONFIG_BITS); /* RES/GSEL set, PC1 still 0 */
  kx134_write_reg(KX134_REG_BUF_CNTL1, KX134_BUF_CNTL1_SMP_TH);
  kx134_write_reg(KX134_REG_BUF_CNTL2, KX134_BUF_CNTL2_CONFIG);

  /* Arm INT1 before entering operating mode below, so the callback is live
   * before BFI can possibly assert the pin - same ordering as the old repo's
   * hal_accel_init(). */
  attachInterrupt(digitalPinToInterrupt(ACCEL_INT1_PIN), accel_int1_isr, RISING);

  /* Clear any stale buffer contents, then enter operating mode (PC1=1) - same
   * as the old repo's hal_accel_start(). */
  kx134_write_reg(KX134_REG_BUF_CLEAR, 0x00);
  kx134_write_reg(KX134_REG_CNTL1, KX134_CNTL1_CONFIG_BITS | KX134_CNTL1_PC1);

  k_thread_create(&accel_sampler_thread_data, accel_sampler_thread_stack,
                  K_THREAD_STACK_SIZEOF(accel_sampler_thread_stack),
                  accel_sampler_thread_entry, NULL, NULL, NULL,
                  ACCEL_SAMPLER_THREAD_PRIORITY, 0, K_NO_WAIT);
  k_thread_name_set(&accel_sampler_thread_data, "accel_sampler");
}
