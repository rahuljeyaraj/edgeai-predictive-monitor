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
 * Normal mode's frame (docs/CHART_CLUTTER_PLAN.md S1's dashboard data needs)
 * carries, every epoch: the fused accel spectrum (unchanged, model input),
 * per-axis accel_x/y/z spectra (additive - display-only, never fed to the
 * model), and one SCALAR_SET of accel-derived scalar tiles (rms/kurtosis/
 * crest_factor/peak/std/skewness, computed on the combined tri-axial vector
 * magnitude). Every FUSER_TIME_SERIES_EVERY_N-th epoch (app_config.h) also
 * piggybacks decimated accel x/y/z + mic TIME_SERIES sections for the
 * collapsible "Raw signals" panel - not every epoch, since that data is
 * substantially bigger and would otherwise slow the whole frame (including
 * the anomaly score and spectrum charts riding the same pull).
 *
 * Note this differs from FUSER_RAW_CAPTURE_MODE below: that mode is a
 * separate, full-resolution, slow-cadence offline-dataset-capture tool: it
 * REPLACES the whole frame body and is not meant to run permanently. This
 * dashboard-facing time-series piggyback is decimated, fast-cadence, and
 * layered ON TOP of the normal spectrum+scalar frame.
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
 *
 * FUSER_RAW_CAPTURE_MODE (app_config.h, default 0) rebuilds this whole thread
 * body to instead stream raw, un-FFT'd TIME_SERIES windows (3 accel axes kept
 * separate + raw mic) at a slower FUSER_RAW_EPOCH_MS cadence, alternating one
 * accel frame / one mic frame per epoch - for offline sensor/bin-count
 * experimentation off a labeled rig capture, not normal operation. See the
 * `#if FUSER_RAW_CAPTURE_MODE` blocks below.
 */
#include "fuser.h"

#include "accel_sampler.h"
#include "app_config.h"
#include "mic_sampler.h"
#include "spi_link.h"
#include "telemetry_schema.h"

#include <Arduino_RouterBridge.h>
#include <zephyr/kernel.h>
#include <cmath>
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

/* Which epoch constant actually paces the thread loop below - normal mode
 * uses FUSER_EPOCH_MS (~15.6 fps), raw-capture mode the much slower
 * FUSER_RAW_EPOCH_MS (app_config.h). */
#if FUSER_RAW_CAPTURE_MODE
#define FUSER_ACTIVE_EPOCH_MS FUSER_RAW_EPOCH_MS
#else
#define FUSER_ACTIVE_EPOCH_MS FUSER_EPOCH_MS
#endif

/* Normal mode's section accounting (raw-capture mode's own sizing is
 * directly below, unaffected). Every fused frame carries
 * FUSER_NUM_SPECTRUM_SECTIONS SPECTRUM sections (mic, accel-fused, + per-axis
 * accel x/y/z for docs/CHART_CLUTTER_PLAN.md S1's multi-axis overlay chart)
 * and one SCALAR_SET section (the accel-derived scalar tiles); every
 * FUSER_TIME_SERIES_EVERY_N-th frame (app_config.h) additionally piggybacks
 * FUSER_NUM_TS_SECTIONS decimated TIME_SERIES sections (the collapsible "Raw
 * signals" panel) - see app_config.h's FUSER_TIME_SERIES_EVERY_N comment and
 * the fuser_epoch_count gating below for why that's piggybacked rather than
 * sent every frame. FUSER_MAX_SECTIONS is the worst-case total (both kinds
 * present in the same frame) - only affects the static buffer ceiling below,
 * the actual per-frame num_sections is computed in the thread loop. */
#define FUSER_NUM_SPECTRUM_SECTIONS 5  /* mic, accel, accel_x, accel_y, accel_z */
#define FUSER_NUM_SCALARS 6            /* rms, kurtosis, crest_factor, peak, std, skewness */
#define FUSER_NUM_TS_SECTIONS 4         /* accel_x_raw, accel_y_raw, accel_z_raw, mic_raw */
#define FUSER_MAX_SECTIONS (FUSER_NUM_SPECTRUM_SECTIONS + 1 + FUSER_NUM_TS_SECTIONS)  /* 10 */

/* Per-section wire overhead: the 5-byte section header (source/channel/kind +
 * section_len u16) plus a SPECTRUM body's own fs/fft_size/bin_count preamble
 * (4 + 2 + 2). */
#define FUSER_SECTION_OVERHEAD (5 + 4 + 2 + 2)
#define FUSER_SPECTRUM_SECTION_LEN (FUSER_SECTION_OVERHEAD + FUSER_MAX_BINS * sizeof(float))
/* SCALAR_SET body: count u8 + (id u16 + value f32) per scalar. */
#define FUSER_SCALAR_SECTION_LEN (5 + 1 + FUSER_NUM_SCALARS * (2 + 4))
/* TIME_SERIES body: fs f32 + sample_count u16 + samples f32[]; sections carry
 * FUSER_TS_DECIMATED_SAMPLES (app_config.h), NOT the native FFT window length
 * - the whole reason the piggyback stays bounded regardless of which
 * sensor's window is longer (mic's is 2x accel's). */
#define FUSER_TS_SECTION_LEN (5 + 4 + 2 + FUSER_TS_DECIMATED_SAMPLES * sizeof(float))

/* BSS scratch, not thread stack - same reason the samplers use static working
 * buffers (see mic_sampler.cpp): a few KB of frame/bin data shouldn't inflate
 * the thread stack. fuser_frame_buf is sized for the true worst case (1
 * num_sections byte + 5 max-bin SPECTRUM + 1 SCALAR_SET + 4 decimated
 * TIME_SERIES - the every-Nth-frame's heavier composition); SPI_LINK_MAX_PAYLOAD
 * (spi_link.cpp) must be >= this, and spi_link_stage_frame() clamps defensively.
 */
#if FUSER_RAW_CAPTURE_MODE
/* Raw window lengths mirror each sampler file's own FFT_LEN derivation
 * (app_config.h's "each file derives its own *_FFT_LEN locally" comment:
 * mic *4, accel *2) - fuser.cpp has no direct access to those file-local
 * #defines, only the runtime accel_fft_size()/mic_fft_size() accessors, so
 * these compile-time twins exist solely to size the static scratch/frame
 * buffers below. Keep in sync if either sampler's multiplier ever changes. */
#define FUSER_RAW_ACCEL_SAMPLES (ACCEL_FFT_BIN_COUNT * 2)
#define FUSER_RAW_MIC_SAMPLES (MIC_FFT_BIN_COUNT * 4)
/* TIME_SERIES section overhead: 5-byte section header + fs f32 + sample_count
 * u16 (no bin_count field, unlike SPECTRUM). */
#define FUSER_RAW_TS_OVERHEAD (5 + 4 + 2)
#define FUSER_RAW_ACCEL_FRAME_LEN \
  (1 + 3 * (FUSER_RAW_TS_OVERHEAD + FUSER_RAW_ACCEL_SAMPLES * sizeof(float)))
#define FUSER_RAW_MIC_FRAME_LEN \
  (1 + (FUSER_RAW_TS_OVERHEAD + FUSER_RAW_MIC_SAMPLES * sizeof(float)))
/* The two raw frame kinds (3-axis accel / mono mic) alternate, never both in
 * one frame - buffer only needs to cover the larger of the two. */
#define FUSER_RAW_FRAME_BUF_LEN \
  (FUSER_RAW_ACCEL_FRAME_LEN > FUSER_RAW_MIC_FRAME_LEN ? FUSER_RAW_ACCEL_FRAME_LEN \
                                                        : FUSER_RAW_MIC_FRAME_LEN)

static float fuser_accel_raw_x[FUSER_RAW_ACCEL_SAMPLES];
static float fuser_accel_raw_y[FUSER_RAW_ACCEL_SAMPLES];
static float fuser_accel_raw_z[FUSER_RAW_ACCEL_SAMPLES];
static float fuser_mic_raw[FUSER_RAW_MIC_SAMPLES];
static uint8_t fuser_frame_buf[FUSER_RAW_FRAME_BUF_LEN];
#else
static float fuser_mic_bins[FUSER_MAX_BINS];
static float fuser_accel_bins[FUSER_MAX_BINS];
static float fuser_accel_x_bins[FUSER_MAX_BINS];
static float fuser_accel_y_bins[FUSER_MAX_BINS];
static float fuser_accel_z_bins[FUSER_MAX_BINS];

/* Raw per-axis/mic windows, needed unconditionally now (not just raw-capture
 * mode) to compute the accel-derived scalar tiles and the piggybacked
 * decimated time-series sections. Lengths mirror each sampler's own FFT_LEN
 * derivation (mic *4, accel *2 of its bin count) - fuser.cpp has no direct
 * access to those file-local #defines, only the runtime
 * accel_fft_size()/mic_fft_size() accessors, so these compile-time twins
 * exist solely to size these buffers (same caveat as raw-capture mode's
 * FUSER_RAW_ACCEL_SAMPLES/FUSER_RAW_MIC_SAMPLES above, deliberately
 * duplicated rather than shared since the two modes compile mutually
 * exclusively). Keep in sync if either sampler's multiplier ever changes. */
#define FUSER_ACCEL_WINDOW_SAMPLES (ACCEL_FFT_BIN_COUNT * 2)
#define FUSER_MIC_WINDOW_SAMPLES (MIC_FFT_BIN_COUNT * 4)
static float fuser_accel_raw_x[FUSER_ACCEL_WINDOW_SAMPLES];
static float fuser_accel_raw_y[FUSER_ACCEL_WINDOW_SAMPLES];
static float fuser_accel_raw_z[FUSER_ACCEL_WINDOW_SAMPLES];
static float fuser_mic_raw[FUSER_MIC_WINDOW_SAMPLES];

/* Combined tri-axial vector-magnitude scratch (sqrt(x^2+y^2+z^2) per sample)
 * the scalar tiles are computed from. Accel-only, not mic: the offline
 * experiment harness (tools/offline_experiment.py) found accel's rms/kurtosis
 * separate healthy from a real tested fault by 74-87 sigma vs. mic-only's
 * +6 sigma, so a mic scalar row would be unjustified clutter right now
 * (docs/CHART_CLUTTER_PLAN.md's whole point is cutting clutter). Same length
 * as the accel raw window. */
static float fuser_accel_vecmag[FUSER_ACCEL_WINDOW_SAMPLES];

/* Reused across all 4 TIME_SERIES sections in turn (decimate, write, move to
 * the next channel) - write_timeseries_section() copies it into the frame
 * buffer immediately, so one scratch buffer is enough. */
static float fuser_ts_scratch[FUSER_TS_DECIMATED_SAMPLES];

static uint8_t fuser_frame_buf[1 + FUSER_NUM_SPECTRUM_SECTIONS * FUSER_SPECTRUM_SECTION_LEN +
                                   FUSER_SCALAR_SECTION_LEN +
                                   FUSER_NUM_TS_SECTIONS * FUSER_TS_SECTION_LEN];
#endif

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

/* Append one TIME_SERIES section (source_id fixed to this base station).
 * Body is [fs f32][sample_count u16][samples f32...] - the raw-capture
 * counterpart to write_spectrum_section() above, minus the un-FFT'd data's
 * fft_size/bin_count fields (a raw window has no bins yet). Used by both
 * raw-capture mode (full-resolution) and normal mode's piggybacked decimated
 * sections. */
static void write_timeseries_section(uint8_t *buf, size_t *pos, uint8_t channel_id,
                                     float fs, const float *samples,
                                     uint16_t sample_count) {
  uint16_t section_len = (uint16_t)(4 + 2 + (uint32_t)sample_count * sizeof(float));
  put_u8(buf, pos, TELEM_SOURCE_BASE_STATION);
  put_u8(buf, pos, channel_id);
  put_u8(buf, pos, TELEM_KIND_TIME_SERIES);
  put_u16(buf, pos, section_len);
  put_f32(buf, pos, fs);
  put_u16(buf, pos, sample_count);
  memcpy(&buf[*pos], samples, (size_t)sample_count * sizeof(float));
  *pos += (size_t)sample_count * sizeof(float);
}

#if !FUSER_RAW_CAPTURE_MODE
/* Append one SCALAR_SET section (source_id fixed to this base station,
 * channel_id fixed to TELEM_CHANNEL_PERF - scalars aren't tied to one wire
 * channel, same convention tests/telemetry_frame_test.py's
 * test_scalar_set_decodes() already uses). Body is
 * [count u8][ids u16...][values f32...], parallel count-length arrays. */
static void write_scalar_section(uint8_t *buf, size_t *pos, const uint16_t *ids,
                                 const float *values, uint8_t count) {
  uint16_t section_len = (uint16_t)(1 + (uint32_t)count * (2 + 4));
  put_u8(buf, pos, TELEM_SOURCE_BASE_STATION);
  put_u8(buf, pos, TELEM_CHANNEL_PERF);
  put_u8(buf, pos, TELEM_KIND_SCALAR_SET);
  put_u16(buf, pos, section_len);
  put_u8(buf, pos, count);
  for (uint8_t i = 0; i < count; i++) {
    put_u16(buf, pos, ids[i]);
  }
  for (uint8_t i = 0; i < count; i++) {
    put_f32(buf, pos, values[i]);
  }
}

/* Simple stride decimation (skip samples, no averaging) for the piggybacked
 * time-series sections - a chart line doesn't need the full FFT window
 * length to read as smooth, and this isn't used for any frequency-domain
 * analysis. in_len must be an exact multiple of out_len (true for both accel
 * 1024/256=4 and mic 2048/256=8). */
static void decimate_stride(const float *in, int in_len, float *out, int out_len) {
  int stride = in_len / out_len;
  for (int i = 0; i < out_len; i++) {
    out[i] = in[i * stride];
  }
}

/* Combined tri-axial vector magnitude, sample-by-sample - the standard
 * "overall vibration" signal the scalar tiles summarize (see
 * fuser_accel_vecmag's comment for why accel-only, no mic). */
static void compute_vector_magnitude(const float *x, const float *y, const float *z,
                                     int len, float *out) {
  for (int i = 0; i < len; i++) {
    out[i] = sqrtf(x[i] * x[i] + y[i] * y[i] + z[i] * z[i]);
  }
}

/* Scalar tile math on the combined vector-magnitude signal
 * (docs/CHART_CLUTTER_PLAN.md S1's "Scalar tiles" section). rms()/kurtosis()
 * mirror python/tools/offline_experiment.py's formulas exactly (population
 * std/excess kurtosis) so the on-device number means the same thing that
 * tool already validated (+74-87 sigma healthy/fault separation, see
 * offline-experiment-harness notes) rather than a second, potentially-
 * drifting implementation. crest_factor/peak/std are standard vibration-
 * analysis definitions; skewness is the standard third-standardized-moment
 * (not itself validated by the offline harness, unlike rms/kurtosis). */
static void compute_scalars(const float *mag, int len, float *out_rms, float *out_kurtosis,
                            float *out_crest, float *out_peak, float *out_std,
                            float *out_skew) {
  float sum = 0.0f, sumsq = 0.0f, peak = 0.0f;
  for (int i = 0; i < len; i++) {
    sum += mag[i];
    sumsq += mag[i] * mag[i];
    if (mag[i] > peak) peak = mag[i];  // mag[] is sqrt(...), always >= 0
  }
  float mean = sum / len;
  float rms = sqrtf(sumsq / len);
  float variance = sumsq / len - mean * mean;
  if (variance < 0.0f) variance = 0.0f;  // fp rounding guard
  float std = sqrtf(variance);

  float m3 = 0.0f, m4 = 0.0f;
  for (int i = 0; i < len; i++) {
    float d = mag[i] - mean;
    float d2 = d * d;
    m3 += d2 * d;
    m4 += d2 * d2;
  }
  m3 /= len;
  m4 /= len;

  *out_rms = rms;
  *out_peak = peak;
  *out_std = std;
  *out_crest = (rms > 0.0f) ? (peak / rms) : 0.0f;
  *out_kurtosis = (std > 0.0f) ? (m4 / (std * std * std * std) - 3.0f) : 0.0f;  // excess
  *out_skew = (std > 0.0f) ? (m3 / (std * std * std)) : 0.0f;
}
#endif  /* !FUSER_RAW_CAPTURE_MODE */

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

#if !FUSER_RAW_CAPTURE_MODE
  /* Metadata is fixed at bring-up (compile-time constants behind the
   * accessors), read once. Bin counts are clamped to the buffer ceiling
   * defensively; in practice both are FUSER_MAX_BINS. */
  int mic_bins = mic_full_bin_count();
  int accel_bins = accel_full_bin_count();
  if (mic_bins > FUSER_MAX_BINS) mic_bins = FUSER_MAX_BINS;
  if (accel_bins > FUSER_MAX_BINS) accel_bins = FUSER_MAX_BINS;
#endif

  /* Per-channel metadata, fixed at bring-up, read once and re-sent in every
   * frame's section headers (fs/fft_size travel on the wire so the MPU never
   * needs them out of band). In raw-capture mode, mic_fft/accel_fft double as
   * the raw window's sample_count - the un-FFT'd window IS the FFT input, so
   * its length is exactly *_fft_size(), no separate accessor needed. */
  float mic_fs = mic_sample_rate_hz();
  uint16_t mic_fft = (uint16_t)mic_fft_size();
  float accel_fs = accel_sample_rate_hz();
  uint16_t accel_fft = (uint16_t)accel_fft_size();

#if FUSER_RAW_CAPTURE_MODE
  /* Alternates which raw frame kind goes out each epoch (accel 3-axis, then
   * mic, ...) - see this file's header comment and FUSER_RAW_FRAME_BUF_LEN's
   * comment on why they're never combined into one frame. */
  bool send_mic_next = false;
#else
  /* Gates the every-Nth-frame time-series piggyback - see app_config.h's
   * FUSER_TIME_SERIES_EVERY_N comment. */
  uint32_t fuser_epoch_count = 0;
#endif

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

#if FUSER_RAW_CAPTURE_MODE
    /* Sample-and-hold, same as normal mode, but only the channel this epoch
     * is sending - see FUSER_RAW_EPOCH_MS's comment (app_config.h) for why
     * this cadence can't outrun accel's ~640ms window-fill time. */
    size_t pos = 0;
    if (send_mic_next) {
      mic_copy_raw_window(fuser_mic_raw);
      put_u8(fuser_frame_buf, &pos, 1);  // num_sections: mic raw only
      write_timeseries_section(fuser_frame_buf, &pos, TELEM_CHANNEL_MIC_RAW,
                               mic_fs, fuser_mic_raw, mic_fft);
    } else {
      accel_copy_raw_window(fuser_accel_raw_x, fuser_accel_raw_y, fuser_accel_raw_z);
      put_u8(fuser_frame_buf, &pos, 3);  // num_sections: accel x/y/z raw
      write_timeseries_section(fuser_frame_buf, &pos, TELEM_CHANNEL_ACCEL_X_RAW,
                               accel_fs, fuser_accel_raw_x, accel_fft);
      write_timeseries_section(fuser_frame_buf, &pos, TELEM_CHANNEL_ACCEL_Y_RAW,
                               accel_fs, fuser_accel_raw_y, accel_fft);
      write_timeseries_section(fuser_frame_buf, &pos, TELEM_CHANNEL_ACCEL_Z_RAW,
                               accel_fs, fuser_accel_raw_z, accel_fft);
    }
    send_mic_next = !send_mic_next;

    size_t frame_len = pos;
#else
    /* Sample-and-hold: copy the latest published spectra/raw windows (each
     * mutex-guarded inside the accessor). Between epochs where a sampler
     * produced nothing new, its buffer simply still holds the previous block
     * - same behaviour as the old repo's msgq sample-and-hold. */
    mic_copy_full_spectrum(fuser_mic_bins);
    accel_copy_full_spectrum(fuser_accel_bins);
    accel_copy_axis_spectra(fuser_accel_x_bins, fuser_accel_y_bins, fuser_accel_z_bins);
    accel_copy_raw_window(fuser_accel_raw_x, fuser_accel_raw_y, fuser_accel_raw_z);

    /* Scalar tiles (docs/CHART_CLUTTER_PLAN.md S1): accel-only, computed from
     * the combined tri-axial vector magnitude - see fuser_accel_vecmag's
     * comment for why accel-only. Cheap (one pass over 1024 floats), so this
     * runs every epoch regardless of the time-series piggyback below. */
    compute_vector_magnitude(fuser_accel_raw_x, fuser_accel_raw_y, fuser_accel_raw_z,
                             FUSER_ACCEL_WINDOW_SAMPLES, fuser_accel_vecmag);
    float scalar_rms, scalar_kurtosis, scalar_crest, scalar_peak, scalar_std, scalar_skew;
    compute_scalars(fuser_accel_vecmag, FUSER_ACCEL_WINDOW_SAMPLES, &scalar_rms,
                    &scalar_kurtosis, &scalar_crest, &scalar_peak, &scalar_std, &scalar_skew);
    const uint16_t scalar_ids[FUSER_NUM_SCALARS] = {
        TELEM_SCALAR_RMS, TELEM_SCALAR_KURTOSIS, TELEM_SCALAR_CREST_FACTOR,
        TELEM_SCALAR_PEAK, TELEM_SCALAR_STD, TELEM_SCALAR_SKEWNESS};
    const float scalar_values[FUSER_NUM_SCALARS] = {
        scalar_rms, scalar_kurtosis, scalar_crest, scalar_peak, scalar_std, scalar_skew};

    /* Every FUSER_TIME_SERIES_EVERY_N-th frame additionally piggybacks the
     * decimated raw-signal sections - see app_config.h's
     * FUSER_TIME_SERIES_EVERY_N comment for why this isn't every frame. */
    bool send_time_series = (fuser_epoch_count % FUSER_TIME_SERIES_EVERY_N) == 0;
    fuser_epoch_count++;
    if (send_time_series) {
      mic_copy_raw_window(fuser_mic_raw);
    }

    /* Assemble the section-list frame: [num_sections u8] then one section per
     * channel/scalar-set. To experiment with a different channel set, change
     * num_sections and the write_*_section() calls here (ids from
     * telemetry_schema.h) - nothing else on either end needs editing. */
    size_t pos = 0;
    uint8_t num_sections = FUSER_NUM_SPECTRUM_SECTIONS + 1 +
                           (uint8_t)(send_time_series ? FUSER_NUM_TS_SECTIONS : 0);
    put_u8(fuser_frame_buf, &pos, num_sections);
    write_spectrum_section(fuser_frame_buf, &pos, TELEM_CHANNEL_MIC, mic_fs,
                           mic_fft, fuser_mic_bins, (uint16_t)mic_bins);
    write_spectrum_section(fuser_frame_buf, &pos, TELEM_CHANNEL_ACCEL, accel_fs,
                           accel_fft, fuser_accel_bins, (uint16_t)accel_bins);
    write_spectrum_section(fuser_frame_buf, &pos, TELEM_CHANNEL_ACCEL_X, accel_fs,
                           accel_fft, fuser_accel_x_bins, (uint16_t)accel_bins);
    write_spectrum_section(fuser_frame_buf, &pos, TELEM_CHANNEL_ACCEL_Y, accel_fs,
                           accel_fft, fuser_accel_y_bins, (uint16_t)accel_bins);
    write_spectrum_section(fuser_frame_buf, &pos, TELEM_CHANNEL_ACCEL_Z, accel_fs,
                           accel_fft, fuser_accel_z_bins, (uint16_t)accel_bins);
    write_scalar_section(fuser_frame_buf, &pos, scalar_ids, scalar_values, FUSER_NUM_SCALARS);

    if (send_time_series) {
      decimate_stride(fuser_accel_raw_x, FUSER_ACCEL_WINDOW_SAMPLES, fuser_ts_scratch,
                      FUSER_TS_DECIMATED_SAMPLES);
      write_timeseries_section(fuser_frame_buf, &pos, TELEM_CHANNEL_ACCEL_X_RAW, accel_fs,
                               fuser_ts_scratch, FUSER_TS_DECIMATED_SAMPLES);
      decimate_stride(fuser_accel_raw_y, FUSER_ACCEL_WINDOW_SAMPLES, fuser_ts_scratch,
                      FUSER_TS_DECIMATED_SAMPLES);
      write_timeseries_section(fuser_frame_buf, &pos, TELEM_CHANNEL_ACCEL_Y_RAW, accel_fs,
                               fuser_ts_scratch, FUSER_TS_DECIMATED_SAMPLES);
      decimate_stride(fuser_accel_raw_z, FUSER_ACCEL_WINDOW_SAMPLES, fuser_ts_scratch,
                      FUSER_TS_DECIMATED_SAMPLES);
      write_timeseries_section(fuser_frame_buf, &pos, TELEM_CHANNEL_ACCEL_Z_RAW, accel_fs,
                               fuser_ts_scratch, FUSER_TS_DECIMATED_SAMPLES);
      decimate_stride(fuser_mic_raw, FUSER_MIC_WINDOW_SAMPLES, fuser_ts_scratch,
                      FUSER_TS_DECIMATED_SAMPLES);
      write_timeseries_section(fuser_frame_buf, &pos, TELEM_CHANNEL_MIC_RAW, mic_fs,
                               fuser_ts_scratch, FUSER_TS_DECIMATED_SAMPLES);
    }

    size_t frame_len = pos;
#endif

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
    if (elapsed >= FUSER_ACTIVE_EPOCH_MS) {
      fuser_overrun_count++;
    }
#endif

    if (elapsed < FUSER_ACTIVE_EPOCH_MS) {
      k_msleep(FUSER_ACTIVE_EPOCH_MS - elapsed);
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
