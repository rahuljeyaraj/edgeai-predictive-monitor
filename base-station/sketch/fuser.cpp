/*
 * fuser / transport - port of the old repo's fuser_thread.c + frame_types.h
 * (spectrum_fused_payload) onto the App Lab structure.
 *
 * Each epoch (FUSER_EPOCH_MS) this samples-and-holds the latest full-resolution
 * mic + accel spectra (mic_copy_full_spectrum()/accel_copy_full_spectrum() -
 * the un-downsampled float32 bins the samplers publish for us, NOT their
 * 32-bucket get_*_spectrum Bridge views), packs them into one self-describing
 * frame (a fuser_frame_header mirroring the old repo's
 * spectrum_fused_payload_header, then mic_bin_count float32s, then
 * accel_bin_count float32s), and hands that frame to the SPI transport.
 *
 * Transport = the dedicated MCU<->MPU SPI bus (spi_link.{h,cpp}), NOT the shared
 * Bridge UART. This is the fix documented in docs/progress2.md ("THE NEXT
 * CHANGE"): the old repo owned a dedicated raw UART and sent the whole ~4 KB
 * frame in one transport_send(); this project's single MCU<->MPU UART is shared
 * with every Bridge provider and its continuous notify stream recurringly
 * wedged the router's msgpack framer (section 2). So the frame no longer goes
 * over Bridge at all - fuser just calls spi_link_stage_frame(), which wraps it
 * (framing header + CRC32) and holds it as the latest pending frame; the MPU
 * pulls it over SPI on its own schedule via the "spi_arm" RPC (spi_link.cpp).
 * This thread's only job is to keep a fresh frame staged at the epoch rate; the
 * actual transfer is DMA-driven and owned by spi_link.
 *
 * float32 is kept (not quantized): the MPU consumer is the autoencoder
 * inference pipeline, whose features are these exact magnitudes, so any
 * quantization would inject noise into the model input. SPI has ample bandwidth
 * for full float32 (docs/progress2.md decision 1), so unlike the old UART path
 * there's no byte-pressure motive to prescale.
 */
#include "fuser.h"

#include "accel_sampler.h"
#include "app_config.h"
#include "mic_sampler.h"
#include "spi_link.h"

#include <Arduino_RouterBridge.h>
#include <zephyr/kernel.h>
#include <cstring>

/* FUSER_EPOCH_MS (~15.6 frames/s) and FUSER_THREAD_PRIORITY now live in
 * app_config.h. The samplers produce a new FFT block every ~21ms (mic) /
 * faster (accel), so at 64ms cadence every frame carries fresh data; a
 * slower epoch just sub-samples, a faster one repeats. */

/* Compile-time ceiling for the per-sensor bin count, used to size the static
 * frame/scratch buffers. Both samplers currently publish exactly 512 bins
 * (MIC_FFT_BIN_COUNT / ACCEL_FFT_BIN_COUNT, app_config.h); the runtime counts
 * from the accessors are asserted against this. */
#define FUSER_MAX_BINS 512

/* FUSER_THREAD_PRIORITY (app_config.h) == one below Bridge's own update
 * thread (5), NOT matching it as originally set. At equal priority (5) the
 * continuous ~15.8fps notify stream starved the round-trip register/call
 * path badly enough that every Bridge.provide() provider (matrix/rgb/
 * sensor-info) came back "method not available" for the whole time the
 * fuser streamed - confirmed on hardware 2026-07-14, reproduced identically
 * at both 1M and 2M baud, so it wasn't a baud/RX-margin issue, and it
 * correlated with frame rate, not flood-during-setup (see PROGRESS.md's
 * 2026-07-14 fuser entry). Dropping the fuser one band below Bridge's update
 * thread lets that thread always preempt the stream to service a pending
 * register/call, at the cost of the fuser's own k_msleep() wakeups being
 * delayed by however long Bridge's thread runs - acceptable since the fuser
 * only cares about average frame rate, not per-frame timing. Diverges from
 * the old repo's FUSER_THREAD_PRIORITY (which had no Bridge update thread to
 * share a priority band with in the first place). */
#define FUSER_THREAD_STACK_SIZE 3072

/* One-time delay before the first frame, so setup() can finish registering
 * every module's Bridge providers before this thread starts streaming (see the
 * thread entry). Generous vs. the few ms setup() actually needs. */
#define FUSER_STARTUP_DELAY_MS 1000

/* Self-describing frame header, byte-for-byte the old repo's
 * spectrum_fused_payload_header (frame_types.h): the receiver reads sample
 * rate / FFT size / bin count per sensor off the wire rather than hardcoding
 * them. Little-endian on both ends (Cortex-M and the Linux host), packed, so a
 * direct memcpy into the frame buffer matches the documented wire layout. */
struct __attribute__((packed)) fuser_frame_header {
  float mic_fs;
  uint16_t mic_fft_size;
  uint16_t mic_bin_count;
  float accel_fs;
  uint16_t accel_fft_size;
  uint16_t accel_bin_count;
};

/* BSS scratch, not thread stack - same reason the samplers use static working
 * buffers (see mic_sampler.cpp): a few KB of frame/bin data shouldn't inflate
 * the thread stack. */
static float fuser_mic_bins[FUSER_MAX_BINS];
static float fuser_accel_bins[FUSER_MAX_BINS];
static uint8_t fuser_frame_buf[sizeof(fuser_frame_header) +
                               2 * FUSER_MAX_BINS * sizeof(float)];

#if BENCHMARK_STATS_ENABLED
/* Single writer (fuser_thread_entry), read by fuser_get_stats() from
 * whatever thread bench.cpp's Bridge handler runs on - same "torn read is
 * harmless for a diagnostic" reasoning as the samplers' own counters. */
static volatile uint32_t fuser_frames_sent = 0;
static volatile uint32_t fuser_overrun_count = 0;
static volatile uint32_t fuser_send_ms_sum = 0;
static volatile uint32_t fuser_send_ms_max = 0;
#endif

static void fuser_thread_entry(void *p1, void *p2, void *p3) {
  ARG_UNUSED(p1);
  ARG_UNUSED(p2);
  ARG_UNUSED(p3);

  /* Metadata is fixed at bring-up (compile-time constants behind the
   * accessors), read once. Bin counts are clamped to the buffer ceiling
   * defensively; in practice both are FUSER_MAX_BINS. */
  int mic_bins = mic_full_bin_count();
  int accel_bins = accel_full_bin_count();
  if (mic_bins > FUSER_MAX_BINS) mic_bins = FUSER_MAX_BINS;
  if (accel_bins > FUSER_MAX_BINS) accel_bins = FUSER_MAX_BINS;

  struct fuser_frame_header header;
  header.mic_fs = mic_sample_rate_hz();
  header.mic_fft_size = (uint16_t)mic_fft_size();
  header.mic_bin_count = (uint16_t)mic_bins;
  header.accel_fs = accel_sample_rate_hz();
  header.accel_fft_size = (uint16_t)accel_fft_size();
  header.accel_bin_count = (uint16_t)accel_bins;

  /* Let setup() finish first. This thread (FUSER_THREAD_PRIORITY, app_config.h)
   * is created mid-setup() and would otherwise preempt the priority-14 setup()
   * thread and start flooding the link before the other modules' Bridge
   * providers are registered - registration is a round-trip that the continuous
   * notify stream can crowd out (2026-07-14: a too-fast stream wedged the whole
   * link and starved every provider registration; see docs/PROGRESS.md).
   * fuser_start() is now called last in setup() so registration is already done
   * by the time this runs; this sleep is belt-and-suspenders on top of that. */
  k_msleep(FUSER_STARTUP_DELAY_MS);

  while (1) {
    /* Fixed-period pacing: time the whole build+stage below and sleep only the
     * remainder of the epoch, so the production rate is a true 1000/FUSER_EPOCH_MS
     * (~15.6 fps) instead of (build_time + FUSER_EPOCH_MS). Staging is just a
     * couple of ~4KB memcpys + a CRC pass (well under 1ms), so this practically
     * always sleeps; the over-budget path is kept only as a safety net. Note the
     * MPU's actual pull rate (spi_arm) is independent of this - a slower puller
     * just skips staged frames, a faster one re-reads the latest (lossy live
     * view). */
    int64_t frame_start = k_uptime_get();

    /* Sample-and-hold: copy the latest published spectra (each mutex-guarded
     * inside the accessor). Between epochs where a sampler produced nothing
     * new, its buffer simply still holds the previous block - same behaviour
     * as the old repo's msgq sample-and-hold. */
    mic_copy_full_spectrum(fuser_mic_bins);
    accel_copy_full_spectrum(fuser_accel_bins);

    /* Assemble the frame: header, then mic bins, then accel bins. */
    size_t pos = 0;
    memcpy(&fuser_frame_buf[pos], &header, sizeof(header));
    pos += sizeof(header);
    memcpy(&fuser_frame_buf[pos], fuser_mic_bins, mic_bins * sizeof(float));
    pos += mic_bins * sizeof(float);
    memcpy(&fuser_frame_buf[pos], fuser_accel_bins, accel_bins * sizeof(float));
    pos += accel_bins * sizeof(float);

    size_t frame_len = pos;

    /* Hand the assembled frame to the SPI transport. It wraps the payload with
     * its own framing header + CRC32, stamps a sequence number, and holds it as
     * the latest pending frame for the MPU to pull over SPI (spi_link.cpp).
     * Non-blocking: one mutex-guarded memcpy + CRC, no wire I/O on this thread. */
    spi_link_stage_frame(fuser_frame_buf, (uint16_t)frame_len);

    int64_t elapsed = k_uptime_get() - frame_start;

#if BENCHMARK_STATS_ENABLED
    fuser_frames_sent++;
    fuser_send_ms_sum += (uint32_t)elapsed;
    if ((uint32_t)elapsed > fuser_send_ms_max) {
      fuser_send_ms_max = (uint32_t)elapsed;
    }
    if (elapsed >= FUSER_EPOCH_MS) {
      fuser_overrun_count++;
    }
#endif

    if (elapsed < FUSER_EPOCH_MS) {
      k_msleep(FUSER_EPOCH_MS - elapsed);
    } else {
      /* Over budget - MUST actually sleep, not k_yield(): k_yield() only yields
       * to threads of the SAME priority, so a permanently-over-budget fuser
       * would become a busy loop at priority 6 that starves mic (7), spi_link
       * and loop()/setup() (14) forever. This bit us hard on the old UART
       * transport, where the fuser spun inside Bridge.notify at 115200 baud
       * (one ~4.3KB frame took ~375ms > the 64ms epoch) - proven on hardware
       * 2026-07-14 via SWD thread-state forensics (docs/progress2.md 4.8). With
       * the SPI transport staging is sub-1ms so this path realistically never
       * triggers, but one tick of real sleep is the correct safety net
       * regardless: it guarantees every lower-priority thread a scheduling
       * window each frame. */
      k_msleep(1);
    }
  }
}

K_THREAD_STACK_DEFINE(fuser_thread_stack, FUSER_THREAD_STACK_SIZE);
static struct k_thread fuser_thread_data;

void fuser_start(void) {
  Bridge.begin(BRIDGE_BAUD); /* idempotent - the samplers/displays also call this */

  k_thread_create(&fuser_thread_data, fuser_thread_stack,
                  K_THREAD_STACK_SIZEOF(fuser_thread_stack),
                  fuser_thread_entry, NULL, NULL, NULL,
                  FUSER_THREAD_PRIORITY, 0, K_NO_WAIT);
  k_thread_name_set(&fuser_thread_data, "fuser");
}

#if BENCHMARK_STATS_ENABLED
void fuser_get_stats(struct fuser_bench_stats *out) {
  out->frames_sent = fuser_frames_sent;
  out->overrun_count = fuser_overrun_count;
  out->send_ms_sum = fuser_send_ms_sum;
  out->send_ms_max = fuser_send_ms_max;
}
#endif
