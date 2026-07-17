/*
 * fuser / transport - port of the old repo's fuser_thread.c + frame_types.h
 * (spectrum_fused_payload) onto the App Lab structure.
 *
 * Each epoch (FUSER_EPOCH_MS) this samples-and-holds the latest full-resolution
 * mic + accel spectra (mic_copy_full_spectrum()/accel_copy_full_spectrum() -
 * the un-downsampled float32 bins the samplers publish for us, NOT their
 * 32-bucket get_*_spectrum Bridge views), packs them into one self-describing
 * generic section-list frame (docs/SENSOR_TELEMETRY_FRAME_PLAN.md S3, Phase A),
 * and hands that frame to the SPI transport.
 *
 * Frame = [num_sections u8] then, per section,
 * [source_id u8][channel_id u8][data_kind u8][section_len u16][body]; a
 * SPECTRUM body is [fs f32][fft_size u16][bin_count u16][bins f32...]. Channel/
 * source/kind ids come from telemetry_schema.h, generated from the one schema
 * file (telemetry_schema.json) the MPU parser (common/telemetry_frame.py) is
 * also generated from, so the two ends can't drift. This replaced a fixed
 * fuser_frame_header + two hardcoded mic/accel blocks: to change which channels
 * a frame carries (per-axis accel, add a SCALAR_SET, ...) you now just edit
 * num_sections + which write_*_section() calls run below, and the MPU side needs
 * no code change at all (it loops over sections and dispatches on data_kind).
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
#include "telemetry_schema.h"

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

/* Worst-case section count the frame buffer is sized for. Two today (mic +
 * accel); headroom for the near-term experiments the plan is for (per-axis
 * triaxial accel = 3, + mic, + a SCALAR_SET of RMS/kurtosis/...). Only affects
 * the static buffer ceiling below - the actual num_sections written each frame
 * is chosen in the thread loop. */
#define FUSER_MAX_SECTIONS 6

/* Per-section wire overhead: the 5-byte section header (source/channel/kind +
 * section_len u16) plus a SPECTRUM body's own fs/fft_size/bin_count preamble
 * (4 + 2 + 2). A SPECTRUM section is the largest kind, so sizing every slot as
 * a max-bin spectrum is the true worst case. */
#define FUSER_SECTION_OVERHEAD (5 + 4 + 2 + 2)

/* BSS scratch, not thread stack - same reason the samplers use static working
 * buffers (see mic_sampler.cpp): a few KB of frame/bin data shouldn't inflate
 * the thread stack. fuser_frame_buf is sized for the worst case (1 num_sections
 * byte + FUSER_MAX_SECTIONS max-bin SPECTRUM sections); SPI_LINK_MAX_PAYLOAD
 * (spi_link.cpp) must be >= this, and spi_link_stage_frame() clamps defensively.
 */
static float fuser_mic_bins[FUSER_MAX_BINS];
static float fuser_accel_bins[FUSER_MAX_BINS];
static uint8_t fuser_frame_buf[1 + FUSER_MAX_SECTIONS *
                                       (FUSER_SECTION_OVERHEAD +
                                        FUSER_MAX_BINS * sizeof(float))];

/* Little-endian appenders (the Cortex-M and the Linux MPU are both LE, matching
 * the wire format's documented byte order - same assumption the old direct
 * memcpy of the packed header relied on). Each advances *pos past what it
 * wrote; the caller guarantees room via fuser_frame_buf's worst-case sizing. */
static inline void put_u8(uint8_t *buf, size_t *pos, uint8_t v) {
  buf[(*pos)++] = v;
}
static inline void put_u16(uint8_t *buf, size_t *pos, uint16_t v) {
  memcpy(&buf[*pos], &v, sizeof(v));
  *pos += sizeof(v);
}
static inline void put_f32(uint8_t *buf, size_t *pos, float v) {
  memcpy(&buf[*pos], &v, sizeof(v));
  *pos += sizeof(v);
}

/* Append one SPECTRUM section (source_id fixed to this base station). Body is
 * [fs f32][fft_size u16][bin_count u16][bins f32...]; section_len covers only
 * the body, so a receiver that doesn't know this channel can skip it by length.
 * A structurally-absent channel (plan S4) is sent by calling this with an
 * all-zero bins buffer and its real bin_count - a present, zero-valued section,
 * NOT an omitted one. */
static void write_spectrum_section(uint8_t *buf, size_t *pos, uint8_t channel_id,
                                   float fs, uint16_t fft_size,
                                   const float *bins, uint16_t bin_count) {
  uint16_t section_len =
      (uint16_t)(4 + 2 + 2 + (uint32_t)bin_count * sizeof(float));
  put_u8(buf, pos, TELEM_SOURCE_BASE_STATION);
  put_u8(buf, pos, channel_id);
  put_u8(buf, pos, TELEM_KIND_SPECTRUM);
  put_u16(buf, pos, section_len);
  put_f32(buf, pos, fs);
  put_u16(buf, pos, fft_size);
  put_u16(buf, pos, bin_count);
  memcpy(&buf[*pos], bins, (size_t)bin_count * sizeof(float));
  *pos += (size_t)bin_count * sizeof(float);
}

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

  /* Per-channel metadata, fixed at bring-up, read once and re-sent in every
   * frame's SPECTRUM section headers (fs/fft_size travel on the wire so the MPU
   * never needs them out of band). */
  float mic_fs = mic_sample_rate_hz();
  uint16_t mic_fft = (uint16_t)mic_fft_size();
  float accel_fs = accel_sample_rate_hz();
  uint16_t accel_fft = (uint16_t)accel_fft_size();

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

    /* Assemble the section-list frame: [num_sections u8] then one SPECTRUM
     * section per channel. To experiment with a different channel set, change
     * num_sections and the write_*_section() calls here (ids from
     * telemetry_schema.h) - nothing else on either end needs editing. */
    size_t pos = 0;
    put_u8(fuser_frame_buf, &pos, 2);  // num_sections: mic + accel
    write_spectrum_section(fuser_frame_buf, &pos, TELEM_CHANNEL_MIC, mic_fs,
                           mic_fft, fuser_mic_bins, (uint16_t)mic_bins);
    write_spectrum_section(fuser_frame_buf, &pos, TELEM_CHANNEL_ACCEL, accel_fs,
                           accel_fft, fuser_accel_bins, (uint16_t)accel_bins);

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
