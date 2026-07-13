/*
 * INMP441 I2S MEMS microphone, ported from the old repo's mic_sampler_thread
 * (edgeai-predictive-monitor-unoq/mcu/src/threads/mic_sampler_thread.c) +
 * hal_audio.h + drivers/audio_i2s.c: continuously captures 2048-sample mono
 * blocks at 96kHz and runs a 2048-pt real FFT on each block, same magnitude
 * extraction (drop DC, keep Nyquist, no mirroring, keep only the first
 * MIC_FFT_BIN_COUNT=512 of the 1024 unique bins - see that reasoning below),
 * same hardware (SAI1_A, SCK=PB10/FS=PB9/SD=PC1, 96kHz/16-bit, no MCLK pin).
 * Three things are fundamentally different, all forced by being back on App
 * Lab instead of a from-scratch Zephyr build:
 *
 *  - No `i2s` Zephyr driver / devicetree node. The old repo's audio_i2s.c
 *    used Zephyr's i2s API against a `sai1_a` devicetree node added by that
 *    repo's own board overlay (boards/arduino_uno_q.overlay) - PLL2 config,
 *    GPDMA1 wiring, and SAI1 protocol settings were all declared in
 *    devicetree and resolved by Zephyr's i2s_stm32_sai.c driver. App Lab's
 *    arduino:zephyr toolchain gives a sketch no devicetree/pinctrl hook (same
 *    constraint already hit by rgb_display.cpp's WS2812 port), and this
 *    board's shipped firmware image doesn't have a `sai1_a` node compiled in
 *    at all - confirmed by checking the installed core's bundled libraries
 *    on-device (only Arduino_LED_Matrix/RTC/SocketWrapper/CAN/SPI/Wire/
 *    ea_malloc; no I2S/PDM/audio library of any kind). So this drives SAI1
 *    directly via STM32Cube LL/CMSIS registers instead of a Zephyr device:
 *    RCC (enable GPIOB/GPIOC/SAI1 clocks, configure+enable PLL2 targeting the
 *    SAI domain, route SAI1's kernel clock to PLL2_P), GPIO (AF13 on
 *    PB9/PB10/PC1 - confirmed against the old repo's own generated
 *    zephyr.dts, which resolved those pinctrl nodes to
 *    STM32_PINMUX('B',9,AF13) etc for this exact chip variant,
 *    stm32u585aiixq), then SAI1_Block_A's CR1/CR2/FRCR/SLOTR registers by
 *    hand. There's no dedicated STM32U5 LL driver for SAI (unlike GPIO/RCC,
 *    which do have one) - only the full HAL (stm32u5xx_hal_sai.h/.c) - and
 *    only the HAL *headers* ship in this core's llext-edk (no .c, so
 *    HAL_SAI_Init() itself isn't linkable) - so the register values below
 *    are the same ones HAL_SAI_InitProtocol(SAI_I2S_STANDARD, ...) would
 *    compute, written directly: MODE=Master RX, PRTCFG=Free protocol (I2S is
 *    hand-configured via FRCR/SLOTR, not one of SAI's hardware protocol
 *    presets), DS=16-bit, MCKDIV=25 (same math as the old repo's PLL2
 *    overlay comment: 76.8MHz kernel clock / (96000Hz * 32-bit frame) = 25
 *    exactly), FRL=32-bit frame/FSALL=16-bit half-frame/FSDEF=channel-
 *    identification/FSOFF=frame-sync-before-first-bit (the four FRCR fields
 *    that define Philips I2S timing), NBSLOT=2 (I2S frames always carry 2
 *    slots), but SLOTEN=slot 0 only - unlike the old repo, which had to read
 *    and discard the right slot in software (audio_i2s.c's own comment),
 *    telling the SAI hardware to only push the left slot into the FIFO in
 *    the first place skips that step entirely.
 *  - No DMA. The old repo's driver was DMA-backed (GPDMA1 channel 2,
 *    request/slot 36 - fixed SAI1_A RX hardware wiring, confirmed in that
 *    repo's own overlay comment) and blocked on a Zephyr mem-slab queue,
 *    which is what let mic_sampler_thread's whole existence be "block until
 *    the next 2048-sample buffer is ready" - cheap for the scheduler, no
 *    busy-waiting. A GPDMA1-based rewrite of this file was built and tested
 *    at length - hand-programming the same channel/request directly against
 *    stm32u5xx_ll_dma.h, since there's no linkable `LL_DMA_Init()` either
 *    (same missing-.c situation as HAL_SAI) - but never got real data
 *    moving: the channel armed and read back exactly as configured
 *    (CTR2/CCR/PRIVCFGR/SECCFGR/RCFGLOCKR all correct, no DTE/USE/ULE error
 *    flags), yet `LL_DMA_GetBlkDataLength()` always read back the full,
 *    untouched block length - zero bytes ever transferred, across ten
 *    different fixes tried on real hardware (clearing SAI's OVRUDR before
 *    arming, a genuine DMAEN 0->1 edge after SAIEN, FIFO threshold EMPTY vs
 *    1/4, GPDMA1 destination port allocation Port0 vs Port1, a full SAIEN
 *    stop/restart cycle around each arm, and finally matching ST's own
 *    `HAL_SAI_Receive_DMA()` sequence exactly - fetched from
 *    github.com/STMicroelectronics/stm32u5xx-hal-driver for reference, since
 *    only that file's headers ship in this core). All ten produced the
 *    identical failure signature, which is itself the most informative data
 *    point: matching ST's own authoritative HAL sequence byte-for-byte
 *    changing nothing points away from a configuration bug and toward
 *    something specific to running as a dynamically-loaded llext extension
 *    inside a full pre-built Zephyr RTOS image (e.g. its own power
 *    management silently re-gating GPDMA1's execution clock after this
 *    sketch enables it, while register read/write access - on a separate
 *    bus-clock domain - keeps working) - not something diagnosable further
 *    without a debugger or logic analyzer on the actual request line, which
 *    this workflow doesn't have. Given FIFOTHRESHOLD_EMPTY (SAI's RX
 *    "request" flag, FREQ, asserts as soon as the FIFO has anything to
 *    read), this instead polls FREQ and reads one 16-bit sample from DR at
 *    a time - much less code, at the cost of the capture loop being a tight
 *    busy-wait for the ~21.3ms one 2048-sample block takes at 96kHz (see the
 *    "Known current limitation" note below). See docs/PROGRESS.md's "Future
 *    improvements" for the full list of what was ruled out, in case whoever
 *    revisits this has debugger access this session didn't.
 *  - No CMSIS-DSP. The old repo's FFT was CMSIS-DSP's arm_rfft_fast_f32() -
 *    part of Zephyr's own module tree in a from-scratch build, but not
 *    present anywhere in this core's llext-edk (no arm_math.h found
 *    on-device). So this hand-rolls a standard iterative radix-2 in-place
 *    Cooley-Tukey complex FFT instead (real input, imaginary part zeroed) -
 *    same 2048-point size, same magnitude extraction as the old repo's own
 *    mic_fft_magnitude() (bin 0/DC discarded, bin N/2/Nyquist kept via
 *    fabsf() since it's purely real for real input, bins 1..N/2-1 via
 *    sqrtf(re^2+im^2), no mirroring). Twiddle factors are precomputed once
 *    at startup (cosf/sinf), not recomputed per FFT call.
 *
 * Known current limitation: with no DMA, the ~21.3ms/block capture loop is a
 * genuine busy-wait (interrupts stay enabled, so it's not as disruptive as
 * rgb_display.cpp's irq_lock()'d WS2812 bursts, but it still occupies the
 * CPU for the whole block). This is also why MIC_SAMPLER_THREAD_PRIORITY is
 * *below* Bridge's and the display threads', not matching their priority-3
 * "preempt Bridge" convention - see that macro's own comment for what
 * happened on real hardware when this thread first tried priority 3 (it
 * hung the entire Bridge link, not just this provider). At its current
 * priority, a Bridge RPC or a display tick can preempt mid-capture-block and
 * cost a dropped/skewed sample; it also can't survive rgb_display.cpp's own
 * ~240us irq_lock() windows without dropping whatever mic samples were due
 * during that window - unavoidable with polling, since irq_lock() blocks
 * literally everything, not just lower-priority threads. Both would go away
 * if capture moved to GPDMA1 (freeing this thread to block/sleep between
 * blocks the way the old repo's did) - attempted and reverted, see the "No
 * DMA" bullet above and docs/PROGRESS.md.
 *
 * Bridge exposure is also necessarily different from the old repo, which
 * never had one: the old repo's mic_sampler_thread only ever produced a
 * 512-bin spectrum for the not-yet-ported fuser_thread to fold into a
 * transport_send() frame over raw UART. This repo's Arduino_RPClite Bridge
 * has a hard-coded 256-byte round-trip message buffer
 * (DEFAULT_RPC_BUFFER_SIZE in Arduino_RPClite/src/request.h, confirmed
 * on-device) - nowhere near enough for a 512-float spectrum, whether sent as
 * binary (2048 bytes) or as CSV text (~3KB). So "get_mic_spectrum" exposes a
 * further-downsampled MIC_SPECTRUM_BINS=32-bucket average-pooled view of the
 * full 512-bin spectrum (16 original bins averaged per bucket) as one
 * comma-separated integer-rounded String, comfortably under the 256-byte
 * ceiling. The full 512-bin FFT output is still computed every block, just
 * not all of it fits back out over Bridge in one call - a real fidelity cut
 * versus the old repo, not a hidden one; full-resolution transport would
 * need chunking across multiple Bridge calls, deferred until it's clear
 * whether anything downstream actually needs bin-level (vs. bucket-level)
 * resolution, same "revisit once more interfaces exist" spirit as
 * docs/PROGRESS.md's transport_thread note.
 */
#include "mic_sampler.h"

#include <Arduino_RouterBridge.h>
#define STM32U585xx
#include <stm32u5xx.h>
#include <stm32u5xx_ll_bus.h>
#include <stm32u5xx_ll_gpio.h>
#include <stm32u5xx_ll_rcc.h>
#include <zephyr/kernel.h>
#include <cmath>
#include <cstring>

/* SAI1_A pins - SCK=PB10, FS/WS=PB9, SD=PC1(A4), all AF13 on this chip
 * (stm32u585aiixq). Confirmed against the old repo's own generated
 * mcu/build/zephyr/zephyr.dts, which resolved its
 * sai1_sck_a_pb10/sai1_fs_a_pb9/sai1_sd_a_pc1 pinctrl nodes to
 * STM32_PINMUX('B', 10, AF13) / STM32_PINMUX('B', 9, AF13) /
 * STM32_PINMUX('C', 1, AF13) - these pins/AF aren't a preference, they're
 * this chip's only header-accessible SAI1_A candidates (see that repo's
 * overlay comment). No MCLK pin - the INMP441 derives its own timing from
 * SCK/WS alone, same as the old repo. */
#define MIC_SAMPLER_SAMPLE_RATE_HZ 96000

/* 2048-sample block == one FFT window. MIC_FFT_BIN_COUNT*4, not *2: at
 * 96kHz, without an external MCLK the INMP441 only has valid audio below
 * Fs/4 = 24kHz (BCLK/64 = Fs/2 is the mic's own natural rate; the unavoidable
 * 2x upsampling to Fs folds an image at Fs/4) - so only the first
 * MIC_FFT_BIN_COUNT of the 1024 unique RFFT bins (up to 24kHz) are useful;
 * see mic_fft_magnitude()'s own comment for where the rest get dropped.
 * Exact same reasoning/numbers as the old repo's mic_sampler_thread.c. */
#define MIC_FFT_BIN_COUNT 512
#define MIC_FFT_LEN (MIC_FFT_BIN_COUNT * 4)
#define MIC_FFT_LOG2N 11 /* log2(MIC_FFT_LEN), MIC_FFT_LEN == 2048 == 1 << 11 */

/* Bridge's hard 256-byte round-trip message ceiling (see header comment)
 * forces further downsampling before "get_mic_spectrum" can return
 * anything - 512 bins averaged 16-to-1 into 32 buckets. */
#define MIC_SPECTRUM_BINS 32
#define MIC_DOWNSAMPLE_FACTOR (MIC_FFT_BIN_COUNT / MIC_SPECTRUM_BINS)

#define MIC_FFT_PI 3.14159265f

/* Large working buffers are static (BSS), not thread-stack-allocated - same
 * reason the old repo's mic_sampler_thread_entry() used `static` locals for
 * its block[]/mic_fft_window[]/mic_fft_mag[] arrays: MIC_SAMPLER_THREAD_STACK_SIZE
 * only needs to cover call frames, not ~26KB of sample/FFT/twiddle data. */
static int16_t mic_capture_block[MIC_FFT_LEN];
static float mic_fft_re[MIC_FFT_LEN];
static float mic_fft_im[MIC_FFT_LEN];
static float mic_fft_twiddle_cos[MIC_FFT_LEN / 2];
static float mic_fft_twiddle_sin[MIC_FFT_LEN / 2];
static float mic_fft_mag[MIC_FFT_LEN / 2]; /* bins 1..MIC_FFT_LEN/2, DC dropped */

K_MUTEX_DEFINE(mic_spectrum_mtx);
static float mic_spectrum_latest[MIC_SPECTRUM_BINS];

static void mic_fft_init_twiddles(void) {
  for (int k = 0; k < MIC_FFT_LEN / 2; k++) {
    float angle = -2.0f * MIC_FFT_PI * (float)k / (float)MIC_FFT_LEN;
    mic_fft_twiddle_cos[k] = cosf(angle);
    mic_fft_twiddle_sin[k] = sinf(angle);
  }
}

/* Standard iterative radix-2 decimation-in-time Cooley-Tukey FFT, in place
 * over mic_fft_re[]/mic_fft_im[]. Replaces the old repo's
 * arm_rfft_fast_f32() (CMSIS-DSP, not available here - see header comment). */
static void mic_fft_run(void) {
  const int n = MIC_FFT_LEN;
  int j = 0;

  for (int i = 0; i < n - 1; i++) {
    if (i < j) {
      float tr = mic_fft_re[i];
      mic_fft_re[i] = mic_fft_re[j];
      mic_fft_re[j] = tr;
      float ti = mic_fft_im[i];
      mic_fft_im[i] = mic_fft_im[j];
      mic_fft_im[j] = ti;
    }
    int m = n >> 1;
    while (m >= 1 && j >= m) {
      j -= m;
      m >>= 1;
    }
    j += m;
  }

  for (int stage = 1; stage <= MIC_FFT_LOG2N; stage++) {
    int m = 1 << stage;
    int half = m >> 1;
    int step = n / m;
    for (int start = 0; start < n; start += m) {
      for (int k = 0; k < half; k++) {
        float wr = mic_fft_twiddle_cos[k * step];
        float wi = mic_fft_twiddle_sin[k * step];
        int a = start + k;
        int b = a + half;
        float br = mic_fft_re[b] * wr - mic_fft_im[b] * wi;
        float bi = mic_fft_re[b] * wi + mic_fft_im[b] * wr;
        float ar = mic_fft_re[a];
        float ai = mic_fft_im[a];
        mic_fft_re[a] = ar + br;
        mic_fft_im[a] = ai + bi;
        mic_fft_re[b] = ar - br;
        mic_fft_im[b] = ai - bi;
      }
    }
  }
}

/* Same magnitude extraction as the old repo's mic_fft_magnitude(): bin 0
 * (DC) discarded, bins 1..N/2-1 via sqrt(re^2+im^2), bin N/2 (Nyquist) via
 * fabsf() since it's purely real for real input - no mirroring, bins beyond
 * N/2 carry no new information. mic_fft_mag[] ends up holding all N/2=1024
 * unique bins (1..1024); only the first MIC_FFT_BIN_COUNT=512 of those
 * (46.875Hz..24kHz) are used downstream by mic_spectrum_downsample() - the
 * upper 512 (24-48kHz mirror noise, see this file's header comment) are
 * simply never read, same effective cutoff as the old repo's msgq-boundary
 * truncation. */
static void mic_fft_magnitude(void) {
  for (int k = 1; k < MIC_FFT_LEN / 2; k++) {
    float re = mic_fft_re[k];
    float im = mic_fft_im[k];
    mic_fft_mag[k - 1] = sqrtf(re * re + im * im);
  }
  mic_fft_mag[MIC_FFT_LEN / 2 - 1] = fabsf(mic_fft_re[MIC_FFT_LEN / 2]);
}

static void mic_spectrum_downsample(float *out) {
  for (int b = 0; b < MIC_SPECTRUM_BINS; b++) {
    float sum = 0.0f;
    for (int i = 0; i < MIC_DOWNSAMPLE_FACTOR; i++) {
      sum += mic_fft_mag[b * MIC_DOWNSAMPLE_FACTOR + i];
    }
    out[b] = sum / (float)MIC_DOWNSAMPLE_FACTOR;
  }
}

static void mic_configure_af_pin(GPIO_TypeDef *port, uint32_t pin, int pin_num) {
  LL_GPIO_SetPinMode(port, pin, LL_GPIO_MODE_ALTERNATE);
  LL_GPIO_SetPinSpeed(port, pin, LL_GPIO_SPEED_FREQ_VERY_HIGH);
  LL_GPIO_SetPinPull(port, pin, LL_GPIO_PULL_NO);
  if (pin_num < 8) {
    LL_GPIO_SetAFPin_0_7(port, pin, LL_GPIO_AF_13);
  } else {
    LL_GPIO_SetAFPin_8_15(port, pin, LL_GPIO_AF_13);
  }
}

/* PLL2 tuned exactly like the old repo's boards/arduino_uno_q.overlay &pll2
 * node: HSE(16MHz)/div-m=1 -> VCO=384MHz (mul-n=24) -> PLL2_P=76.8MHz
 * (div-p=5), an exact x25 multiple of 96000Hz*32 (sample rate * 32-bit I2S
 * frame), so SAI1's MCKDIV comes out to exactly 25 (see MIC_SAMPLER_MCKDIV
 * below). div-r=4 (PLL2_R=96MHz) isn't needed here (nothing on this board's
 * sketch side uses PLL2_R), included anyway to match the old repo's overlay
 * 1:1 in case a future interface wants it. */
#define MIC_SAMPLER_MCKDIV 25

/* Bounded, not infinite: an infinite wait here previously took the whole
 * Bridge link down with it on a misconfiguration - setup() never returned,
 * so nothing after it (Bridge.begin() for every module, since
 * mic_sampler_start() runs last in sketch.ino) ever ran either. A clock
 * that hasn't locked in 10000 short-spin iterations (comfortably longer
 * than PLL2 lock time, which is on the order of microseconds) isn't going
 * to - better to abort mic_sampler_start() and leave the rest of the app
 * (matrix/rgb/Bridge) working than hang the whole board again. */
#define MIC_SAMPLER_CLOCK_WAIT_ITERATIONS 10000

static bool mic_sampler_init_clocks(void) {
  int i;

  if (!LL_RCC_HSE_IsReady()) {
    LL_RCC_HSE_Enable();
    for (i = 0; i < MIC_SAMPLER_CLOCK_WAIT_ITERATIONS && !LL_RCC_HSE_IsReady(); i++) {
    }
    if (!LL_RCC_HSE_IsReady()) {
      return false;
    }
  }

  /* VCO input range must match div-m's actual output (16MHz HSE / div-m=1 =
   * 16MHz falls in the 8-16MHz range) - PLL2RGE defaults to the 4-8MHz range
   * at reset, which was confirmed on hardware to leave PLL2RDY stuck low
   * forever (LL_RCC_PLL2_IsReady() never returns true, hanging setup()
   * inside the wait loop below and taking the whole Bridge link down with
   * it, since nothing after this point in setup() ever ran). Not needed by
   * the old repo's own PLL2 devicetree node, since Zephyr's clock_control
   * driver derives and sets this range automatically from the requested
   * frequencies - there's no equivalent doing that for us here. */
  LL_RCC_PLL2_SetVCOInputRange(LL_RCC_PLLINPUTRANGE_8_16);
  LL_RCC_PLL2_ConfigDomain_SAI(LL_RCC_PLL2SOURCE_HSE, 1, 24, 5);
  LL_RCC_PLL2_EnableDomain_SAI();
  LL_RCC_PLL2_Enable();
  for (i = 0; i < MIC_SAMPLER_CLOCK_WAIT_ITERATIONS && !LL_RCC_PLL2_IsReady(); i++) {
  }
  if (!LL_RCC_PLL2_IsReady()) {
    return false;
  }
  LL_RCC_SetSAIClockSource(LL_RCC_SAI1_CLKSOURCE_PLL2);

  LL_AHB2_GRP1_EnableClock(LL_AHB2_GRP1_PERIPH_GPIOB | LL_AHB2_GRP1_PERIPH_GPIOC);
  LL_APB2_GRP1_EnableClock(LL_APB2_GRP1_PERIPH_SAI1);
  return true;
}

/* SAI1_A configured as I2S RX master, mono (slot 0 only). Register values
 * are the same ones HAL_SAI_InitProtocol(SAI_I2S_STANDARD, ...) would
 * compute for these settings - see this file's header comment for why they
 *'re written by hand instead of calling that function. */
static void mic_sampler_init_sai(void) {
  mic_configure_af_pin(GPIOB, LL_GPIO_PIN_9, 9);   /* FS/WS */
  mic_configure_af_pin(GPIOB, LL_GPIO_PIN_10, 10); /* SCK */
  mic_configure_af_pin(GPIOC, LL_GPIO_PIN_1, 1);   /* SD */

  SAI1_Block_A->CR1 = 0;
  SAI1_Block_A->CR2 = 0; /* FTH = EMPTY: FREQ asserts as soon as the FIFO has anything to read */

  SAI1_Block_A->FRCR = (31UL << 0) /* FRL: 32-bit frame */
                      | (15UL << 8) /* FSALL: 16-bit half-frame */
                      | SAI_xFRCR_FSDEF  /* FS defines channel side (I2S) */
                      | SAI_xFRCR_FSOFF; /* FS one bit before first data bit (I2S, not MSB-justified) */
                      /* FSPOL left clear: SAI_FS_ACTIVE_LOW, standard I2S polarity */

  SAI1_Block_A->SLOTR = (1UL << SAI_xSLOTR_NBSLOT_Pos) /* NBSLOT=2, I2S frame always has 2 slots */
                       | (1UL << SAI_xSLOTR_SLOTEN_Pos); /* only slot 0 (left/INMP441) feeds the FIFO */
                       /* FBOFF=0, SLOTSZ=00 (slot size follows DS, i.e. 16-bit): both left
                        * at their reset value. */

  SAI1_Block_A->CR1 = SAI_xCR1_MODE_0 /* Master RX */
                     | SAI_xCR1_DS_2  /* 16-bit data size */
                     | (MIC_SAMPLER_MCKDIV << SAI_xCR1_MCKDIV_Pos);
                     /* CKSTR=0 (falling edge), NODIV=0 (divider enabled), PRTCFG=0 (free
                      * protocol - I2S hand-configured via FRCR/SLOTR above), LSBFIRST=0
                      * (MSB first), SYNCEN=0 (async - this block generates its own clock),
                      * MONO=0, OUTDRIV=0, DMAEN=0 (no DMA - see header comment): all left
                      * at their reset (0) value. */

  SAI1_Block_A->CR1 |= SAI_xCR1_SAIEN;
}

/* Diagnostics only - see mic_capture_next_block()'s per-sample timeout
 * comment. Not mutex-guarded: single writer (mic_sampler_thread_entry's
 * thread), read only by mic_get_info() where a torn read is harmless (it's
 * a debugging aid, not something correctness depends on). */
static volatile uint32_t mic_capture_timeouts = 0;
static volatile uint32_t mic_last_sr = 0;

/* 50000 register-poll iterations - generously longer than one sample period
 * should ever take (a correctly running 96kHz stream delivers a new sample
 * every ~10.4us; even a slow few-cycles-per-iteration poll loop covers that
 * many times over in 50000 iterations) without being so long that a truly
 * dead peripheral hangs the thread for a perceptible amount of time. */
#define MIC_SAMPLER_FREQ_POLL_LIMIT 50000

/* Busy-polls SAI1_A's FIFO request flag and reads one 16-bit sample (slot 0
 * only, per SLOTR's SLOTEN above) at a time - see header comment's "Known
 * current limitation" for why this is a tight loop instead of a blocking
 * DMA-backed read like the old repo's hal_audio_read_block(). Bounded, not
 * infinite: an early version of this looped forever on FREQ, and because
 * this thread runs at MIC_SAMPLER_THREAD_PRIORITY (3, higher priority than
 * Bridge's own priority-5 update thread), a SAI1 misconfiguration that never
 * asserts FREQ took the *entire* Bridge link down with it, indistinguishable
 * from a hard fault from the Python side (every Bridge.call(), including
 * ones to unrelated providers like matrix/rgb, timed out) - confirmed on
 * real hardware. Returns false (and leaves mic_capture_block only partially
 * filled) on a timeout rather than hang; the caller just discards that
 * block and retries. */
static bool mic_capture_next_block(void) {
  for (int i = 0; i < MIC_FFT_LEN; i++) {
    int spins = 0;
    while (!(SAI1_Block_A->SR & SAI_xSR_FREQ)) {
      if (++spins >= MIC_SAMPLER_FREQ_POLL_LIMIT) {
        mic_last_sr = SAI1_Block_A->SR;
        mic_capture_timeouts++;
        return false;
      }
    }
    uint16_t raw = (uint16_t)(SAI1_Block_A->DR & 0xFFFFUL);
    mic_capture_block[i] = (int16_t)raw;
  }

  mic_last_sr = SAI1_Block_A->SR;
  if (mic_last_sr & SAI_xSR_OVRUDR) {
    SAI1_Block_A->CLRFR = SAI_xCLRFR_COVRUDR;
  }
  return true;
}

static String mic_get_spectrum(void) {
  float snapshot[MIC_SPECTRUM_BINS];

  k_mutex_lock(&mic_spectrum_mtx, K_FOREVER);
  memcpy(snapshot, mic_spectrum_latest, sizeof(snapshot));
  k_mutex_unlock(&mic_spectrum_mtx);

  String out;
  for (int i = 0; i < MIC_SPECTRUM_BINS; i++) {
    if (i > 0) {
      out += ",";
    }
    out += String((int)(snapshot[i] + 0.5f));
  }
  return out;
}

/* Self-describing metadata, same spirit as the old repo's
 * spectrum_fused_payload_header (frame_types.h) carrying mic_fs/
 * mic_fft_size/mic_bin_count on the wire rather than a downstream consumer
 * hardcoding them: sample_rate_hz, fft_len, full_bin_count (what
 * mic_fft_magnitude() computes and mic_spectrum_downsample() reads from,
 * before downsampling), exposed_bin_count (what get_mic_spectrum() actually
 * returns). */
/* Trailing hex(SR)/timeouts fields are a debugging aid for hardware bring-up
 * (see mic_capture_next_block()'s comment) - not part of the old repo's
 * self-describing-header precedent this function is otherwise modeled on. */
static String mic_get_info(void) {
  String out;
  out += String(MIC_SAMPLER_SAMPLE_RATE_HZ);
  out += ",";
  out += String(MIC_FFT_LEN);
  out += ",";
  out += String(MIC_FFT_BIN_COUNT);
  out += ",";
  out += String(MIC_SPECTRUM_BINS);
  out += ",sr=0x";
  out += String(mic_last_sr, HEX);
  out += ",timeouts=";
  out += String(mic_capture_timeouts);
  return out;
}

#define MIC_SAMPLER_THREAD_STACK_SIZE 2048
/* 7 - lower priority (less urgent) than both Bridge's own update thread (5)
 * and matrix_display_thread/rgb_display_thread (3), NOT the same priority-3
 * "preempt Bridge" convention those two use. First tried at priority 3 to
 * match them; confirmed on real hardware that this was wrong and hangs the
 * *entire* Bridge link (every provider, not just this one - matrix/rgb
 * calls timed out too) as soon as the thread starts: this loop's success
 * path (mic_capture_next_block() returning true once SAI1 is genuinely
 * streaming samples) never blocks or sleeps between blocks, so at any
 * priority equal to or higher than Bridge's update thread, Zephyr's
 * preemptive scheduler never lets that lower/equal-priority thread run
 * again - k_yield() doesn't help either, since it only cedes to
 * equal-priority peers, not strictly-lower-priority ones like Bridge. The
 * display threads get away with priority 3 precisely because they always
 * k_msleep() every tick, giving Bridge (and each other) a chance to run;
 * this thread structurally can't (see header comment on why capture can't
 * be interrupted mid-block without losing samples anyway). Priority 7 makes
 * Bridge and the display threads always preempt this one on demand - mic
 * still gets essentially the whole CPU the rest of the time, since nothing
 * else is running continuously, but can no longer starve them permanently.
 * Trade-off: unlike the priority-3 attempt, a Bridge RPC or matrix/rgb tick
 * can now preempt mid-capture-block and cause a dropped/skewed sample or
 * two - same category of acceptable data-quality cost as the "Known current
 * limitation" already documented above, not a new one. */
#define MIC_SAMPLER_THREAD_PRIORITY 7

static void mic_sampler_thread_entry(void *p1, void *p2, void *p3) {
  ARG_UNUSED(p1);
  ARG_UNUSED(p2);
  ARG_UNUSED(p3);

  while (1) {
    if (!mic_capture_next_block()) {
      /* Yield the CPU (this thread would otherwise immediately retry at
       * MIC_SAMPLER_THREAD_PRIORITY, at least as disruptive to Bridge/
       * matrix/rgb as the hang this is meant to avoid - see
       * mic_capture_next_block()'s comment) and try again. */
      k_msleep(100);
      continue;
    }

    for (int i = 0; i < MIC_FFT_LEN; i++) {
      mic_fft_re[i] = (float)mic_capture_block[i];
      mic_fft_im[i] = 0.0f;
    }
    mic_fft_run();
    mic_fft_magnitude();

    k_mutex_lock(&mic_spectrum_mtx, K_FOREVER);
    mic_spectrum_downsample(mic_spectrum_latest);
    k_mutex_unlock(&mic_spectrum_mtx);
  }
}

K_THREAD_STACK_DEFINE(mic_sampler_thread_stack, MIC_SAMPLER_THREAD_STACK_SIZE);
static struct k_thread mic_sampler_thread_data;

void mic_sampler_start(void) {
  mic_fft_init_twiddles();

  /* Bail out rather than proceed into mic_sampler_init_sai() (which assumes
   * SAI1's kernel clock is already running) on a clock failure - see
   * mic_sampler_init_clocks()'s own comment. Bridge/matrix/rgb still come up
   * fine without mic; a missing "get_mic_spectrum"/"get_mic_info" provider
   * just means the mic didn't come up, not a dead board. */
  if (!mic_sampler_init_clocks()) {
    return;
  }
  mic_sampler_init_sai();

  Bridge.begin(); /* idempotent - matrix_display_start()/rgb_display_start() also call this */
  Bridge.provide("get_mic_spectrum", mic_get_spectrum);
  Bridge.provide("get_mic_info", mic_get_info);

  k_thread_create(&mic_sampler_thread_data, mic_sampler_thread_stack,
                  K_THREAD_STACK_SIZEOF(mic_sampler_thread_stack),
                  mic_sampler_thread_entry, NULL, NULL, NULL,
                  MIC_SAMPLER_THREAD_PRIORITY, 0, K_NO_WAIT);
  k_thread_name_set(&mic_sampler_thread_data, "mic_sampler");
}
