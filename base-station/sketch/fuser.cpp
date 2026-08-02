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
 * Normal mode's frame (docs/SENSOR_TELEMETRY_FRAME_PLAN.md's per-axis+scalars
 * feature-representation work) carries, every epoch: the fused accel
 * spectrum (display-only now, superseded by per-axis for the model),
 * per-axis accel_x/y/z + mic spectra pooled to FUSER_MODEL_SPECTRUM_BINS
 * (model input), and one SCALAR_SET of rms/kurtosis/std/peak/crest_factor/
 * skewness computed separately per accel axis (x/y/z) + mic (model input,
 * mirrors tools/offline_experiment.py's own per-channel scalar computation).
 * That is the whole normal-mode frame: SPECTRUM + SCALAR_SET, nothing else.
 *
 * Normal mode used to also piggyback decimated accel x/y/z + mic TIME_SERIES
 * sections every Nth epoch, to feed a "Raw signals" dashboard panel. Removed
 * (2026-08-01): time-domain windows are no longer streamed MCU->MPU at all
 * in normal operation. They cost the biggest share of the frame (4 x 1035 B
 * against a ~4.3 KB spectrum+scalar frame), and every byte of that rode the
 * same chunked SPI pull as the anomaly score and spectrum charts, slowing
 * all of them for a panel nothing depended on. The model never read them
 * (features.py builds its vector from spectra + scalars only), so nothing
 * downstream of the wire lost anything. The raw windows themselves are
 * still captured on-device every epoch - compute_scalars() runs on them
 * (see fuser_accel_raw_* below); only the transmission is gone.
 *
 * FUSER_RAW_CAPTURE_MODE below is unaffected and is now the ONLY path that
 * puts time-domain data on the wire: a separate, full-resolution,
 * slow-cadence offline-dataset-capture build that REPLACES the whole frame
 * body and is not meant to run permanently.
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
 * separate + raw mic) at a slower FUSER_RAW_EPOCH_MS cadence, all 4 channels
 * combined into one frame per epoch (matching normal mode's own single-
 * frame-per-epoch guarantee, so a labeled capture run gets exactly matched
 * x/y/z/mic window counts) - for offline sensor/bin-count experimentation
 * off a labeled rig capture, not normal operation. See the
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
 * directly below, unaffected). Every fused frame carries the same fixed
 * set, no conditional sections: FUSER_NUM_SPECTRUM_SECTIONS SPECTRUM
 * sections (mic, accel-fused, + per-axis accel x/y/z - the per-axis ones
 * feed BOTH docs/CHART_CLUTTER_PLAN.md S1's multi-axis overlay chart AND,
 * now pooled to FUSER_MODEL_SPECTRUM_BINS bins, the fault-detection model)
 * and one SCALAR_SET section (rms/kurtosis/std/peak/crest_factor/skewness
 * computed separately per accel axis (x/y/z) + mic, model input -- see the
 * scalar block in fuser_thread_entry and docs/SENSOR_TELEMETRY_FRAME_PLAN.md;
 * exactly mirrors tools/offline_experiment.py's own per-channel scalar
 * computation, no combined-tri-axial-magnitude variant). */
#define FUSER_NUM_SPECTRUM_SECTIONS 5  /* mic, accel, accel_x, accel_y, accel_z */
#define FUSER_NUM_SCALARS 24           /* rms/kurtosis/std/peak/crest_factor/skewness,
                                         * computed separately per accel axis (x/y/z) + mic */
#define FUSER_NUM_SECTIONS (FUSER_NUM_SPECTRUM_SECTIONS + 1)

/* Per-section wire overhead: the 5-byte section header (source/channel/kind +
 * section_len u16) plus a SPECTRUM body's own fs/fft_size/bin_count preamble
 * (4 + 2 + 2). */
#define FUSER_SECTION_OVERHEAD (5 + 4 + 2 + 2)
#define FUSER_SPECTRUM_SECTION_LEN (FUSER_SECTION_OVERHEAD + FUSER_MAX_BINS * sizeof(float))
/* SCALAR_SET body: count u8 + (id u16 + value f32) per scalar. */
#define FUSER_SCALAR_SECTION_LEN (5 + 1 + FUSER_NUM_SCALARS * (2 + 4))

/* BSS scratch, not thread stack - same reason the samplers use static working
 * buffers (see mic_sampler.cpp): a few KB of frame/bin data shouldn't inflate
 * the thread stack. fuser_frame_buf is sized for the worst case (1
 * num_sections byte + 5 max-bin SPECTRUM + 1 SCALAR_SET); SPI_LINK_MAX_PAYLOAD
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
#define FUSER_RAW_ACCEL_SECTIONS_LEN \
  (3 * (FUSER_RAW_TS_OVERHEAD + FUSER_RAW_ACCEL_SAMPLES * sizeof(float)))
#define FUSER_RAW_MIC_SECTION_LEN \
  (FUSER_RAW_TS_OVERHEAD + FUSER_RAW_MIC_SAMPLES * sizeof(float))
/* All 4 raw channels (accel x/y/z + mic) now go out together in ONE frame
 * every epoch, instead of alternating accel-only/mic-only epochs -- a
 * labeled capture run needs exactly matched x/y/z/mic window counts to feed
 * the autoencoder (host-side pairing of independently-arriving frames isn't
 * good enough), and this is the same single-frame-per-epoch guarantee
 * normal mode's own fused spectrum frame already has. This needed
 * SPI_LINK_MAX_PAYLOAD raised (spi_link.cpp, raw-capture-mode-only) to fit
 * the much bigger combined frame -- see that constant's comment for the
 * exact size math and the RAM tradeoff this accepts. */
#define FUSER_RAW_FRAME_BUF_LEN (1 + FUSER_RAW_ACCEL_SECTIONS_LEN + FUSER_RAW_MIC_SECTION_LEN)

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

/* Pooled down to FUSER_MODEL_SPECTRUM_BINS (app_config.h) right before the
 * wire write - mic/accel_x/accel_y/accel_z only, the channels the
 * fault-detection model actually consumes. The combined `accel` channel
 * (fuser_accel_bins above) stays at full FUSER_MAX_BINS resolution, unused
 * by the model but left as-is for whatever else may still read it. */
static float fuser_model_mic_bins[FUSER_MODEL_SPECTRUM_BINS];
static float fuser_model_accel_x_bins[FUSER_MODEL_SPECTRUM_BINS];
static float fuser_model_accel_y_bins[FUSER_MODEL_SPECTRUM_BINS];
static float fuser_model_accel_z_bins[FUSER_MODEL_SPECTRUM_BINS];

/* Raw per-axis/mic windows, needed in normal mode too (not just raw-capture
 * mode) as compute_scalars()'s time-domain input - the SCALAR_SET section is
 * model input. They are NOT transmitted in normal mode; the decimated
 * time-series piggyback that used to send them was removed (see the file
 * header). Lengths mirror each sampler's own FFT_LEN
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

static uint8_t fuser_frame_buf[1 + FUSER_NUM_SPECTRUM_SECTIONS * FUSER_SPECTRUM_SECTION_LEN +
                                   FUSER_SCALAR_SECTION_LEN];
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

#if FUSER_RAW_CAPTURE_MODE
/* Append one TIME_SERIES section (source_id fixed to this base station).
 * Body is [fs f32][sample_count u16][samples f32...] - the raw-capture
 * counterpart to write_spectrum_section() above, minus the un-FFT'd data's
 * fft_size/bin_count fields (a raw window has no bins yet). Raw-capture mode
 * only: normal mode no longer puts time-domain data on the wire at all (see
 * the file header). */
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
#endif /* FUSER_RAW_CAPTURE_MODE */

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

/* Average-pool a full FUSER_MAX_BINS-resolution spectrum down to
 * FUSER_MODEL_SPECTRUM_BINS buckets for the wire - same scheme as
 * accel_sampler.cpp's accel_spectrum_downsample()/mic_sampler.cpp's
 * mic_get_spectrum(), just a different bucket count (the fault-detection
 * model's feature-vector resolution, not the legacy 32-bin Bridge-RPC view -
 * see FUSER_MODEL_SPECTRUM_BINS's comment, app_config.h). Only the wire's
 * spectrum bin depth shrinks - the native FFT window (and therefore
 * compute_scalars()'s time-domain inputs below) is completely untouched. */
#define FUSER_MODEL_DOWNSAMPLE_FACTOR (FUSER_MAX_BINS / FUSER_MODEL_SPECTRUM_BINS)
static void fuser_pool_spectrum(const float *in, float *out) {
  for (int b = 0; b < FUSER_MODEL_SPECTRUM_BINS; b++) {
    float sum = 0.0f;
    for (int i = 0; i < FUSER_MODEL_DOWNSAMPLE_FACTOR; i++) {
      sum += in[b * FUSER_MODEL_DOWNSAMPLE_FACTOR + i];
    }
    out[b] = sum / (float)FUSER_MODEL_DOWNSAMPLE_FACTOR;
  }
}

/* Scalar tile math, called on each raw per-axis accel window and the raw
 * mic window separately (model input - see fuser_thread_entry; NOT a
 * combined tri-axial magnitude - that erases the directional signature an
 * imbalance fault produces). rms()/kurtosis() mirror
 * python/tools/offline_experiment.py's formulas exactly (population
 * std/excess kurtosis) so the on-device number means the same thing that
 * tool already validated against real captures (docs/SENSOR_TELEMETRY_FRAME_PLAN.md)
 * rather than a second, potentially-drifting implementation. crest_factor/
 * peak/std are standard vibration-analysis definitions; skewness is the
 * standard third-standardized-moment. Note `peak` here is "max signed
 * value," not "max magnitude" (the input is signed raw accel/mic data, not
 * a nonnegative magnitude) -- this matches raw_features.py's peak() (x.max(),
 * also not abs-max) exactly, so the offline-validated separation numbers
 * already reflect this convention - don't "fix" it with fabsf(). */
static void compute_scalars(const float *mag, int len, float *out_rms, float *out_kurtosis,
                            float *out_crest, float *out_peak, float *out_std,
                            float *out_skew) {
  float sum = 0.0f, sumsq = 0.0f, peak = mag[0];
  for (int i = 0; i < len; i++) {
    sum += mag[i];
    sumsq += mag[i] * mag[i];
    if (mag[i] > peak) peak = mag[i];
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
  /* Metadata is fixed at bring-up (compile-time constant behind the
   * accessor), read once. Bin count is clamped to the buffer ceiling
   * defensively; in practice it's FUSER_MAX_BINS. Only `accel` (the
   * combined channel) still sends at this full resolution - mic/accel_x/y/z
   * go out pooled to FUSER_MODEL_SPECTRUM_BINS instead (fuser_pool_spectrum()
   * always reads a fixed FUSER_MAX_BINS input, same convention as
   * accel_sampler.cpp's accel_spectrum_downsample(), so mic's own bin count
   * no longer needs a local/clamped copy here). */
  int accel_bins = accel_full_bin_count();
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

#if !FUSER_RAW_CAPTURE_MODE
  /* mic/accel_x/y/z go out pooled FUSER_MODEL_DOWNSAMPLE_FACTOR:1 (see
   * fuser_pool_spectrum()) -- fft_size on the wire must shrink by the same
   * factor, or the dashboard's fs/fft_size bin-width math (charts.js) treats
   * each pooled (wider) bin as if it were still one of the original narrow
   * bins, compressing the whole displayed range by that factor (e.g. mic's
   * real 0-24kHz span was rendering as 0-6kHz before this fix). So fft_size
   * on the wire means "the FFT length whose bin width matches these bins",
   * NOT the native FFT length -- charts.js reads it as exactly that. The
   * combined `accel` channel stays unpooled, so it keeps the un-divided
   * accel_fft. */
  uint16_t mic_fft_pooled = mic_fft / FUSER_MODEL_DOWNSAMPLE_FACTOR;
  uint16_t accel_fft_pooled = accel_fft / FUSER_MODEL_DOWNSAMPLE_FACTOR;
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
    /* Sample-and-hold, same as normal mode, but all 4 raw channels every
     * epoch (accel x/y/z + mic, one combined frame) - see
     * FUSER_RAW_FRAME_BUF_LEN's comment for why this replaced the old
     * accel-only/mic-only alternation, and FUSER_RAW_EPOCH_MS's comment
     * (app_config.h) for why this cadence can't outrun accel's ~80ms
     * window-fill time. */
    size_t pos = 0;
    mic_copy_raw_window(fuser_mic_raw);
    accel_copy_raw_window(fuser_accel_raw_x, fuser_accel_raw_y, fuser_accel_raw_z);
    put_u8(fuser_frame_buf, &pos, 4);  // num_sections: accel x/y/z + mic, all raw
    write_timeseries_section(fuser_frame_buf, &pos, TELEM_CHANNEL_ACCEL_X_RAW,
                             accel_fs, fuser_accel_raw_x, accel_fft);
    write_timeseries_section(fuser_frame_buf, &pos, TELEM_CHANNEL_ACCEL_Y_RAW,
                             accel_fs, fuser_accel_raw_y, accel_fft);
    write_timeseries_section(fuser_frame_buf, &pos, TELEM_CHANNEL_ACCEL_Z_RAW,
                             accel_fs, fuser_accel_raw_z, accel_fft);
    write_timeseries_section(fuser_frame_buf, &pos, TELEM_CHANNEL_MIC_RAW,
                             mic_fs, fuser_mic_raw, mic_fft);

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
    /* Per-mic scalars (right below) need a fresh raw window every epoch,
     * same cadence as the accel raw copy above. Neither window leaves the
     * board in this mode - they are compute_scalars() input only. */
    mic_copy_raw_window(fuser_mic_raw);

    /* Pool mic/accel_x/y/z down to FUSER_MODEL_SPECTRUM_BINS for the wire -
     * see fuser_pool_spectrum()'s comment. The combined `accel` channel
     * (fuser_accel_bins) stays full-resolution, unpooled. */
    fuser_pool_spectrum(fuser_mic_bins, fuser_model_mic_bins);
    fuser_pool_spectrum(fuser_accel_x_bins, fuser_model_accel_x_bins);
    fuser_pool_spectrum(fuser_accel_y_bins, fuser_model_accel_y_bins);
    fuser_pool_spectrum(fuser_accel_z_bins, fuser_model_accel_z_bins);

    /* Scalar tiles: the same 6 scalar functions computed separately on each
     * raw per-axis accel window and the raw mic window (model input --
     * combining x/y/z into one magnitude erases the directional signature
     * an imbalance fault produces; per-axis/per-channel is what
     * tools/offline_experiment.py validated at +38.5 sigma worst-case
     * separation, vs. only +1.8 sigma on a combined tri-axial magnitude --
     * exactly replicating that tool's own per-channel approach, no combined
     * variant). Cheap (a few passes over <=2048 floats), so this runs every
     * epoch. */
    float ax_rms, ax_kurtosis, ax_crest, ax_peak, ax_std, ax_skew;
    compute_scalars(fuser_accel_raw_x, FUSER_ACCEL_WINDOW_SAMPLES, &ax_rms, &ax_kurtosis,
                    &ax_crest, &ax_peak, &ax_std, &ax_skew);
    float ay_rms, ay_kurtosis, ay_crest, ay_peak, ay_std, ay_skew;
    compute_scalars(fuser_accel_raw_y, FUSER_ACCEL_WINDOW_SAMPLES, &ay_rms, &ay_kurtosis,
                    &ay_crest, &ay_peak, &ay_std, &ay_skew);
    float az_rms, az_kurtosis, az_crest, az_peak, az_std, az_skew;
    compute_scalars(fuser_accel_raw_z, FUSER_ACCEL_WINDOW_SAMPLES, &az_rms, &az_kurtosis,
                    &az_crest, &az_peak, &az_std, &az_skew);
    float mic_rms, mic_kurtosis, mic_crest, mic_peak, mic_std, mic_skew;
    compute_scalars(fuser_mic_raw, FUSER_MIC_WINDOW_SAMPLES, &mic_rms, &mic_kurtosis,
                    &mic_crest, &mic_peak, &mic_std, &mic_skew);

    const uint16_t scalar_ids[FUSER_NUM_SCALARS] = {
        TELEM_SCALAR_RMS_X, TELEM_SCALAR_KURTOSIS_X, TELEM_SCALAR_STD_X,
        TELEM_SCALAR_PEAK_X, TELEM_SCALAR_CREST_FACTOR_X, TELEM_SCALAR_SKEWNESS_X,
        TELEM_SCALAR_RMS_Y, TELEM_SCALAR_KURTOSIS_Y, TELEM_SCALAR_STD_Y,
        TELEM_SCALAR_PEAK_Y, TELEM_SCALAR_CREST_FACTOR_Y, TELEM_SCALAR_SKEWNESS_Y,
        TELEM_SCALAR_RMS_Z, TELEM_SCALAR_KURTOSIS_Z, TELEM_SCALAR_STD_Z,
        TELEM_SCALAR_PEAK_Z, TELEM_SCALAR_CREST_FACTOR_Z, TELEM_SCALAR_SKEWNESS_Z,
        TELEM_SCALAR_RMS_MIC, TELEM_SCALAR_KURTOSIS_MIC, TELEM_SCALAR_STD_MIC,
        TELEM_SCALAR_PEAK_MIC, TELEM_SCALAR_CREST_FACTOR_MIC, TELEM_SCALAR_SKEWNESS_MIC};
    const float scalar_values[FUSER_NUM_SCALARS] = {
        ax_rms, ax_kurtosis, ax_std, ax_peak, ax_crest, ax_skew,
        ay_rms, ay_kurtosis, ay_std, ay_peak, ay_crest, ay_skew,
        az_rms, az_kurtosis, az_std, az_peak, az_crest, az_skew,
        mic_rms, mic_kurtosis, mic_std, mic_peak, mic_crest, mic_skew};

    /* Assemble the section-list frame: [num_sections u8] then one section per
     * channel/scalar-set. To experiment with a different channel set, change
     * FUSER_NUM_SECTIONS and the write_*_section() calls here (ids from
     * telemetry_schema.h) - nothing else on either end needs editing. */
    size_t pos = 0;
    put_u8(fuser_frame_buf, &pos, (uint8_t)FUSER_NUM_SECTIONS);
    write_spectrum_section(fuser_frame_buf, &pos, TELEM_CHANNEL_MIC, mic_fs,
                           mic_fft_pooled, fuser_model_mic_bins, (uint16_t)FUSER_MODEL_SPECTRUM_BINS);
    write_spectrum_section(fuser_frame_buf, &pos, TELEM_CHANNEL_ACCEL, accel_fs,
                           accel_fft, fuser_accel_bins, (uint16_t)accel_bins);
    write_spectrum_section(fuser_frame_buf, &pos, TELEM_CHANNEL_ACCEL_X, accel_fs,
                           accel_fft_pooled, fuser_model_accel_x_bins, (uint16_t)FUSER_MODEL_SPECTRUM_BINS);
    write_spectrum_section(fuser_frame_buf, &pos, TELEM_CHANNEL_ACCEL_Y, accel_fs,
                           accel_fft_pooled, fuser_model_accel_y_bins, (uint16_t)FUSER_MODEL_SPECTRUM_BINS);
    write_spectrum_section(fuser_frame_buf, &pos, TELEM_CHANNEL_ACCEL_Z, accel_fs,
                           accel_fft_pooled, fuser_model_accel_z_bins, (uint16_t)FUSER_MODEL_SPECTRUM_BINS);
    write_scalar_section(fuser_frame_buf, &pos, scalar_ids, scalar_values, FUSER_NUM_SCALARS);

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
