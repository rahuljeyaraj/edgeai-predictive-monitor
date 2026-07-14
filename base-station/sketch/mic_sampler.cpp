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
 *  - DMA, hand-programmed. The old repo's driver was DMA-backed (GPDMA1
 *    channel 2, request/slot 36 - fixed SAI1_A RX hardware wiring, confirmed
 *    in that repo's own overlay comment) and blocked on a Zephyr mem-slab
 *    queue. This repo reproduces that: capture is GPDMA1-backed too (see the
 *    "GPDMA1-backed capture" section below), hand-programmed against
 *    stm32u5xx_ll_dma.h since there's no linkable `LL_DMA_Init()` (same
 *    missing-.c situation as HAL_SAI). An earlier attempt at this was reverted
 *    after "zero bytes ever transferred" across ten hardware fixes, and the
 *    file long carried a FIFO-polling fallback instead. That's now resolved.
 *    Two staged bring-up probes (a GPDMA1 memory-to-memory self-test, then a
 *    real SAI1_A->memory transfer - both exposed over Bridge and since
 *    removed) isolated the cause: the GPDMA1 controller, its clock, and the
 *    SAI1_A DMA request line (request 36) all work fine; what the reverted
 *    attempt was missing was the single-block linked-list init a non-cyclic
 *    GPDMA transfer needs (LL_DMA_SetLinkStepMode(LSM_1LINK_EXECUTION) +
 *    LL_DMA_SetLinkedListAddrOffset(0)) - exactly what Zephyr's own
 *    dma_stm32u5.c does for a non-cyclic transfer, and what the earlier
 *    hand-roll omitted, so the channel armed and read back perfectly but never
 *    executed its one block. With that in place a full SAI1_A->memory block
 *    moves cleanly (TC set, live audio in the buffer, no error flags). The
 *    prior "power management re-gates GPDMA1's clock" suspicion was wrong - the
 *    M2M self-test transferred fine while the CPU spun, and the run+sleep clock
 *    enables (mic_sampler_init_dma) cover the idle case for the k_msleep-paced
 *    wait. Full detail in docs/PROGRESS.md's "Future improvements" entry.
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
 * Note on sample continuity: capture is single-block (arm, fill 2048 samples,
 * process, re-arm), not double-buffered/circular, so samples produced while
 * the FFT runs between blocks are dropped - the same effective behaviour as
 * the old repo's mem-slab-queue boundary, and fine for a spectrum view.
 * Because the thread now k_msleep()s while GPDMA1 fills each block instead of
 * busy-polling DR, it no longer occupies the CPU per block and no longer has
 * to sit below Bridge/the display threads to avoid starving them (see
 * MIC_SAMPLER_THREAD_PRIORITY's comment) - the priority is left at 7 only to
 * keep this change scoped. A rare Bridge RPC or rgb_display.cpp irq_lock()
 * window overlapping a block can still cost a dropped block (caught as a
 * timeout, counted in get_mic_info), which just re-arms on the next pass.
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

#include "bridge_config.h"

#include <Arduino_RouterBridge.h>
#define STM32U585xx
#include <stm32u5xx.h>
#include <stm32u5xx_ll_bus.h>
#include <stm32u5xx_ll_dma.h>
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
/* Full-resolution (un-downsampled) spectrum published for the fuser
 * (fuser.cpp), which pushes it to the MPU at full float32 fidelity - separate
 * from mic_spectrum_latest[], the 32-bucket view get_mic_spectrum returns
 * under Bridge's 256-byte ceiling. Guarded by the same mic_spectrum_mtx. */
static float mic_full_latest[MIC_FFT_BIN_COUNT];

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
                     | SAI_xCR1_DS_2   /* 16-bit data size */
                     | SAI_xCR1_DMAEN  /* generate DMA requests (drained by GPDMA1 ch2) */
                     | (MIC_SAMPLER_MCKDIV << SAI_xCR1_MCKDIV_Pos);
                     /* CKSTR=0 (falling edge), NODIV=0 (divider enabled), PRTCFG=0 (free
                      * protocol - I2S hand-configured via FRCR/SLOTR above), LSBFIRST=0
                      * (MSB first), SYNCEN=0 (async - this block generates its own clock),
                      * MONO=0, OUTDRIV=0: all left at their reset (0) value. */

  /* Enable SAI once and leave it streaming continuously - the capture thread
   * only re-arms the DMA channel per block, it never stops SCK. Stopping and
   * restarting SAIEN per block re-settles the INMP441 (its output ramps for a
   * few cycles each time the bit clock restarts), which injects a low-frequency
   * transient into every FFT window and pins the spectrum's peak to bucket 0 -
   * observed on hardware. Continuous streaming, like the old repo's circular
   * DMA, avoids that. */
  SAI1_Block_A->CR1 |= SAI_xCR1_SAIEN;
}

/* Diagnostics only. Not mutex-guarded: single writer (the capture thread),
 * read only by mic_get_info() where a torn read is harmless (a debugging aid,
 * not something correctness depends on). mic_last_sr latches SAI1_A's status
 * after each block; mic_capture_timeouts counts blocks whose DMA transfer
 * didn't complete within MIC_DMA_WAIT_TICKS. */
static volatile uint32_t mic_capture_timeouts = 0;
static volatile uint32_t mic_last_sr = 0;

/* --- GPDMA1-backed capture -----------------------------------------------
 * Each 2048-sample block is streamed SAI1_A->DR -> mic_capture_block[] by
 * GPDMA1 channel 2 (hardware request 36 = SAI1_A), and the capture thread
 * k_msleep()s while the DMA runs instead of busy-polling DR one sample at a
 * time. The CPU is free between blocks - the whole point of moving to DMA
 * (see this file's header comment) - so, unlike the old busy-poll, this thread
 * no longer starves lower-priority threads, and the loop() heartbeat runs
 * again while the mic streams.
 *
 * This exact sequence is the one proven on hardware by the mic_dma_probe /
 * mic_dma_sai_probe bring-up diagnostics: a GPDMA1 memory-to-memory self-test
 * confirmed the controller + clock healthy (len->0, TC set, data copied), then
 * a real SAI1_A->memory transfer confirmed the request path moves live audio
 * (len->0, TC set, 254/256 samples non-zero, no error flags). The earlier
 * reverted attempt's "zero bytes ever transferred" was NOT the SAI request
 * path (that works) - the decisive missing piece was the single-block
 * linked-list init (LSM_1LINK_EXECUTION + zero LL offset, in
 * mic_dma_configure_channel below) that Zephyr's own dma_stm32u5.c performs
 * for a non-cyclic transfer and the hand-roll omitted. Those two probes'
 * reads also showed the DMA-written buffer read back correct with no cache
 * maintenance, so DCache coherency isn't in play for this on-chip SRAM buffer. */
#define MIC_DMA_CHANNEL LL_DMA_CHANNEL_2

/* One block == MIC_FFT_LEN halfwords == ~21.3ms at 96kHz. k_msleep(2) between
 * completion checks -> ~11 wake-ups per block; 40 ticks (~80ms) is a generous
 * ceiling before declaring a timeout - bounded like the old poll loop, so a
 * SAI/DMA misconfiguration counts timeouts (visible in get_mic_info) instead
 * of hanging the thread. */
#define MIC_DMA_WAIT_TICK_MS 2
#define MIC_DMA_WAIT_TICKS 40

/* Enable GPDMA1's run- and sleep-mode clocks. Sleep-mode (AHB1SMENR) is the
 * one that matters for the k_msleep()-paced wait below: the capture thread
 * idles the CPU (WFI) while the transfer is in flight, so the controller has
 * to stay clocked through idle or the block would never complete. GPDMA1 is
 * status="disabled" in stm32u5.dtsi, so nothing in the shipped App Lab image
 * is guaranteed to have enabled either clock for us. */
static void mic_sampler_init_dma(void) {
  LL_AHB1_GRP1_EnableClock(LL_AHB1_GRP1_PERIPH_GPDMA1);
  RCC->AHB1SMENR |= RCC_AHB1SMENR_GPDMA1SMEN;
}

/* Reset + fully reconfigure channel 2 for one SAI1_A->mic_capture_block block.
 * A full reconfigure per block (rather than a lightweight length/address
 * re-arm) costs ~15 register writes against a ~21ms block - negligible - and
 * keeps the armed state byte-identical to the proven probe sequence every
 * time. Field values match Zephyr i2s_stm32_sai.c's HAL_DMA_Init for the SAI
 * RX path (halfword, single burst, both ports port0, src fixed / dest
 * incrementing, request 36). */
static void mic_dma_configure_channel(void) {
  LL_DMA_ResetChannel(GPDMA1, MIC_DMA_CHANNEL);
  LL_DMA_SetDataTransferDirection(GPDMA1, MIC_DMA_CHANNEL, LL_DMA_DIRECTION_PERIPH_TO_MEMORY);
  LL_DMA_SetChannelPriorityLevel(GPDMA1, MIC_DMA_CHANNEL, LL_DMA_HIGH_PRIORITY);
  LL_DMA_SetSrcIncMode(GPDMA1, MIC_DMA_CHANNEL, LL_DMA_SRC_FIXED);
  LL_DMA_SetDestIncMode(GPDMA1, MIC_DMA_CHANNEL, LL_DMA_DEST_INCREMENT);
  LL_DMA_SetSrcDataWidth(GPDMA1, MIC_DMA_CHANNEL, LL_DMA_SRC_DATAWIDTH_HALFWORD);
  LL_DMA_SetDestDataWidth(GPDMA1, MIC_DMA_CHANNEL, LL_DMA_DEST_DATAWIDTH_HALFWORD);
  LL_DMA_SetBlkHWRequest(GPDMA1, MIC_DMA_CHANNEL, LL_DMA_HWREQUEST_SINGLEBURST);
  LL_DMA_SetPeriphRequest(GPDMA1, MIC_DMA_CHANNEL, LL_GPDMA1_REQUEST_SAI1_A);
  LL_DMA_SetTransferEventMode(GPDMA1, MIC_DMA_CHANNEL, LL_DMA_TCEM_BLK_TRANSFER);
  LL_DMA_SetSrcAllocatedPort(GPDMA1, MIC_DMA_CHANNEL, LL_DMA_SRC_ALLOCATED_PORT0);
  LL_DMA_SetDestAllocatedPort(GPDMA1, MIC_DMA_CHANNEL, LL_DMA_DEST_ALLOCATED_PORT0);
  LL_DMA_SetBlkDataLength(GPDMA1, MIC_DMA_CHANNEL, MIC_FFT_LEN * 2);
  LL_DMA_ConfigAddresses(GPDMA1, MIC_DMA_CHANNEL,
                         (uint32_t)(uintptr_t)&SAI1_Block_A->DR,
                         (uint32_t)(uintptr_t)mic_capture_block);
  /* Single block, no linked list - the init the reverted attempt was missing. */
  LL_DMA_SetLinkStepMode(GPDMA1, MIC_DMA_CHANNEL, LL_DMA_LSM_1LINK_EXECUTION);
  LL_DMA_SetLinkedListAddrOffset(GPDMA1, MIC_DMA_CHANNEL, 0);
}

/* Bounded drain of whatever the SAI FIFO accumulated while the previous
 * block's FFT ran (SAI streams continuously, so the FIFO overruns during that
 * gap and holds stale samples). Discarding them means each block starts from
 * "now" rather than a few ms in the past. The FIFO is a handful of words deep
 * and drains far faster than 96kHz refills it, so this clears in ~8 reads;
 * capped well above that. */
static void mic_dma_flush_fifo(void) {
  for (int i = 0; i < 64 && (SAI1_Block_A->SR & SAI_xSR_FREQ); i++) {
    (void)SAI1_Block_A->DR;
  }
}

/* Capture one block via DMA. SAI streams continuously (enabled once in
 * mic_sampler_init_sai); this only drops the stale FIFO, re-arms channel 2 for
 * one fresh block, and k_msleep()s until it completes. SCK is never stopped,
 * so the mic doesn't re-settle between blocks. Returns false on a bounded
 * timeout (block left partially filled; caller discards and retries), the same
 * contract the old busy-poll had. Samples produced during the FFT gap are
 * dropped - a block-boundary discontinuity only, not a within-block transient,
 * same effective behaviour as the old repo's msgq-boundary truncation. */
static bool mic_dma_capture_block(void) {
  mic_dma_flush_fifo();
  SAI1_Block_A->CLRFR = SAI_xCLRFR_COVRUDR;

  mic_dma_configure_channel();
  LL_DMA_EnableChannel(GPDMA1, MIC_DMA_CHANNEL);

  bool ok = false;
  for (int i = 0; i < MIC_DMA_WAIT_TICKS; i++) {
    if (LL_DMA_GetBlkDataLength(GPDMA1, MIC_DMA_CHANNEL) == 0 ||
        LL_DMA_IsActiveFlag_TC(GPDMA1, MIC_DMA_CHANNEL)) {
      ok = true;
      break;
    }
    if (LL_DMA_IsActiveFlag_DTE(GPDMA1, MIC_DMA_CHANNEL)) {
      break;
    }
    k_msleep(MIC_DMA_WAIT_TICK_MS);
  }

  mic_last_sr = SAI1_Block_A->SR;
  __DMB();

  /* Park the channel (SAI keeps streaming) so it isn't left armed mid-FFT. */
  LL_DMA_ResetChannel(GPDMA1, MIC_DMA_CHANNEL);

  if (!ok) {
    mic_capture_timeouts++;
  }
  return ok;
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
 * (mic_last_sr latches SAI1_A's SR each block, mic_capture_timeouts counts
 * blocks whose DMA transfer didn't finish in time) - not part of the old repo's
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
/* 7 - kept below Bridge's update thread (5) and the display threads (3).
 * HISTORY: back when capture was a never-yielding busy-poll of DR, priority 3
 * (matching the display threads) hung the *entire* Bridge link the instant
 * this thread started - a same-or-higher-priority thread that never blocks
 * starves Zephyr's preemptive scheduler out of ever running Bridge again -
 * which is also why mic_sampler_start() had to be called last in setup().
 * The DMA capture (mic_dma_capture_block) removes that constraint entirely:
 * the thread now k_msleep()s while GPDMA1 fills each block, so it yields
 * regularly and could safely run at priority 3 - and the setup() ordering no
 * longer needs mic last. The value is left at 7 to keep this change scoped to
 * the DMA port; raising it (and relaxing the ordering) is a separate, now-safe
 * cleanup. Consequence of the yield: the loop() heartbeat, which the old
 * busy-poll starved, blinks again while the mic streams. */
#define MIC_SAMPLER_THREAD_PRIORITY 7

static void mic_sampler_thread_entry(void *p1, void *p2, void *p3) {
  ARG_UNUSED(p1);
  ARG_UNUSED(p2);
  ARG_UNUSED(p3);

  while (1) {
    if (!mic_dma_capture_block()) {
      /* Block timed out (SAI/DMA not streaming). Back off and retry rather
       * than spin - the retry is cheap now that the thread sleeps anyway. */
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
    /* First MIC_FFT_BIN_COUNT bins (the useful <24kHz half) for the fuser -
     * mic_fft_mag[] holds MIC_FFT_LEN/2 bins but only the first
     * MIC_FFT_BIN_COUNT are meaningful (see mic_fft_magnitude()'s comment). */
    memcpy(mic_full_latest, mic_fft_mag, sizeof(mic_full_latest));
    k_mutex_unlock(&mic_spectrum_mtx);
  }
}

K_THREAD_STACK_DEFINE(mic_sampler_thread_stack, MIC_SAMPLER_THREAD_STACK_SIZE);
static struct k_thread mic_sampler_thread_data;

/* Full-resolution spectrum accessors for the fuser (fuser.cpp). Self-describing
 * metadata mirrors the old repo's spectrum_fused_payload_header (mic_fs /
 * mic_fft_size / mic_bin_count). mic_copy_full_spectrum() copies
 * mic_full_bin_count() float magnitudes into out[], mutex-guarded against the
 * capture thread's publish. */
int mic_full_bin_count(void) { return MIC_FFT_BIN_COUNT; }
int mic_fft_size(void) { return MIC_FFT_LEN; }
float mic_sample_rate_hz(void) { return (float)MIC_SAMPLER_SAMPLE_RATE_HZ; }

void mic_copy_full_spectrum(float *out) {
  k_mutex_lock(&mic_spectrum_mtx, K_FOREVER);
  memcpy(out, mic_full_latest, sizeof(mic_full_latest));
  k_mutex_unlock(&mic_spectrum_mtx);
}

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
  mic_sampler_init_dma(); /* GPDMA1 clocks; the capture thread arms channel 2 per block */

  Bridge.begin(BRIDGE_BAUD); /* idempotent - matrix_display_start()/rgb_display_start() also call this */
  Bridge.provide("get_mic_spectrum", mic_get_spectrum);
  Bridge.provide("get_mic_info", mic_get_info);

  k_thread_create(&mic_sampler_thread_data, mic_sampler_thread_stack,
                  K_THREAD_STACK_SIZEOF(mic_sampler_thread_stack),
                  mic_sampler_thread_entry, NULL, NULL, NULL,
                  MIC_SAMPLER_THREAD_PRIORITY, 0, K_NO_WAIT);
  k_thread_name_set(&mic_sampler_thread_data, "mic_sampler");
}
