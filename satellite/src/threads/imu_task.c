/*
 * imu_task.c — KX134 3-axis accelerometer capture + FFT task.
 *
 * Three independent FFTs (X radial, Y radial, Z axial) computed sequentially
 * using one shared set of compute buffers.  Per-axis power accumulators are
 * kept across SPEC_AVG_N blocks, then converted to dBFS and posted.
 *
 * Memory layout (sequential compute avoids 3× duplication):
 *   s_block[N]      — raw sample buffer, filled once per axis per call
 *   s_window[N]     — Hann coefficients, computed once and shared
 *   s_windowed[N]   — Hann-weighted block, reused per axis
 *   s_fft[2N]       — interleaved complex FFT output, reused per axis
 *   s_pwr_x/y/z[N/2] — per-axis power accumulators (kept for SPEC_AVG_N avg)
 *   s_db_x/y/z[N/2]  — per-axis dBFS output (built into imu_frame_t)
 *
 * Axis samples come from hal/hal_accel.h. components/epm_drivers/accel_kx134_spi.c
 * is the default implementation (hardware-confirmed WHO_AM_I, at-rest
 * g-values, zero dropped FIFO frames — docs/decisions/ADR-017); Kconfig's
 * EPM_ACCEL_USE_STUB selects components/epm_drivers/accel_stub.c instead for
 * development/CI without KX134 hardware wired up — see that file for the
 * synthetic per-axis signal (X: 50 Hz radial imbalance, Y: 50+150 Hz, Z:
 * 100 Hz axial misalignment).
 */

/*
 * HW-OPT: KX134 SPI DMA implementation notes, applied to accel_kx134_spi.c
 * once hardware arrived (ADR-017) — kept as a record of what each point
 * required, not a pending TODO list:
 *
 *   3a: CONFIG_SPI_MASTER_ISR_IN_IRAM=y in sdkconfig.defaults +
 *       .intr_flags = ESP_INTR_FLAG_IRAM on spi_bus_add_device().
 *       Required: SPI ISR must stay in IRAM when flash cache is off (WiFi TX).
 *       Without it: 800 µs ISR delay → SPI DMA transaction overrun.
 *
 *   3b: KX134 FIFO burst-read buffer declared as:
 *       static DMA_ATTR uint8_t s_kx134_fifo_buf[3072];
 *       (3072 = 512 samples × 3 axes × 2 bytes/sample at ±16g mode)
 *       DMA on ESP32-S3 cannot address PSRAM directly — buffer must be
 *       in internal DRAM or DMA will silently corrupt data.
 *
 *   3c: SPI clock set to 8 MHz (KX134 max is 10 MHz SPI read).
 *       Transfer time: 3072 × 8 bits / 8 000 000 bps = 3.07 ms per burst.
 *       With SPEC_AVG_N=16 and IMU_FS_HZ=25600, burst happens every
 *       (FFT_IMU_N × 1000 / IMU_FS_HZ) = 80 ms → transfer is 3.8 % of window.
 *
 *   3d: Use spi_device_queue_trans() with queue depth 2 instead of
 *       spi_device_transmit() blocking call.  ISR callback posts a
 *       semaphore; imu_task waits on the semaphore rather than busy-spinning.
 *       This returns the SPI bus to the RTOS scheduler during the 3 ms burst.
 */

#include <math.h>
#include <string.h>

#include "esp_attr.h"     /* EXT_RAM_BSS_ATTR for PSRAM placement */

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"

#include "esp_log.h"
#include "esp_timer.h"

#include "dsps_fft2r.h"
#include "dsps_wind.h"
#include "dsps_math.h"

#include "dsp/envelope.h"
#include "dsp/scalar_stats.h"

#include "epm_config.h"
#include "hal/hal_accel.h"
#include "threads/imu_task.h"

static const char *TAG = "imu_task";

#define IMU_HALF    (FFT_IMU_N / 2)
#define ENV_N       IMU_ENVELOPE_N     /* 256 — decimated envelope block   */
#define ENV_HALF    IMU_ENVELOPE_HALF  /* 128 — matches EPM_MODEL_SPECTRUM_BINS */

/* ─── Shared compute buffers (sequential axis processing) ─────────────────── */

static float s_block   [FFT_IMU_N]     __attribute__((aligned(16)));
static float s_window  [FFT_IMU_N]     __attribute__((aligned(16)));
static float s_windowed[FFT_IMU_N]     __attribute__((aligned(16)));
static float s_fft     [FFT_IMU_N * 2] __attribute__((aligned(16)));

/* Per-axis power accumulators — kept across SPEC_AVG_N blocks for averaging */
static float s_pwr_x[IMU_HALF] __attribute__((aligned(16)));
static float s_pwr_y[IMU_HALF] __attribute__((aligned(16)));
static float s_pwr_z[IMU_HALF] __attribute__((aligned(16)));

/* ─── Envelope-analysis compute buffers (Phase 11a / ADR-032) ──────────────
 * Reuses s_block (post DC-removal, pre-Hann) as the band-pass filter's input
 * — the same raw axis block fft_axis_accumulate() already produced, so no
 * extra capture or copy is needed. One epm_dsp_envelope_t per axis: the
 * band/envelope biquads carry IIR state across epochs (continuity between
 * 80 ms blocks), so each axis needs its own persistent filter state even
 * though axes share the downstream FFT scratch buffers sequentially. */
static float s_env_block   [ENV_N]     __attribute__((aligned(16)));
static float s_env_window  [ENV_N]     __attribute__((aligned(16)));
static float s_env_windowed[ENV_N]     __attribute__((aligned(16)));
static float s_env_fft     [ENV_N * 2] __attribute__((aligned(16)));

static float s_env_pwr_x[ENV_HALF] __attribute__((aligned(16)));
static float s_env_pwr_y[ENV_HALF] __attribute__((aligned(16)));
static float s_env_pwr_z[ENV_HALF] __attribute__((aligned(16)));

static epm_dsp_envelope_t s_env_state_x;
static epm_dsp_envelope_t s_env_state_y;
static epm_dsp_envelope_t s_env_state_z;

static QueueHandle_t s_queue      = NULL;
static TaskHandle_t  s_task_handle = NULL;
TaskHandle_t imu_task_get_handle(void) { return s_task_handle; }

/* ── Stats (Part I: one <module>_get_stats() accessor per module) ────────── */

static uint32_t s_epochs           = 0;
static uint32_t s_read_errors      = 0;
static uint32_t s_reinit_attempts  = 0;
static uint32_t s_reinit_successes = 0;

void imu_task_get_stats(struct imu_task_stats *out)
{
    if (out == NULL) return;
    out->epochs           = s_epochs;
    out->read_errors      = s_read_errors;
    out->reinit_attempts  = s_reinit_attempts;
    out->reinit_successes = s_reinit_successes;
}

/* s_frame in PSRAM: confirmed working (8 MB free, SESSION_7). */
static EXT_RAM_BSS_ATTR imu_frame_t s_frame;

/* ─── Per-axis stats ──────────────────────────────────────────────────────── */

typedef struct {
    float   rms;
    float   peak_abs;    /* max(|x|) — internal, feeds crest factor only */
    float   peak_signed; /* max(x), ADR-019 — the wire "peak" scalar */
    float   kurtosis;    /* excess/Fisher, ADR-018 */
    float   std;
    float   skewness;
    float   dc;
    uint8_t clip;
} axis_stats_t;

static axis_stats_t compute_axis_stats(void)
{
    /* double accumulators: FFT_IMU_N (1024+) terms, and sum3/sum4 grow as
     * v^3/v^4 — matches the existing sum/sq precision choice below. */
    double sum = 0.0, sq = 0.0, cube = 0.0, quad = 0.0;
    float  peak_abs    = 0.0f;
    float  peak_signed = s_block[0];
    uint32_t clip = 0;

    for (int i = 0; i < FFT_IMU_N; i++) {
        float  v  = s_block[i];
        float  a  = fabsf(v);
        double vd = (double)v;
        sum  += vd;
        sq   += vd * vd;
        cube += vd * vd * vd;
        quad += vd * vd * vd * vd;
        if (a > peak_abs) peak_abs = a;
        if (v > peak_signed) peak_signed = v;
        if (a >= 1.0f) clip++;
    }

    axis_stats_t st;
    st.dc          = (float)(sum / FFT_IMU_N);
    st.rms         = sqrtf((float)(sq  / FFT_IMU_N));
    st.peak_abs    = peak_abs;
    st.peak_signed = peak_signed;
    st.kurtosis    = epm_dsp_kurtosis_from_sums((float)sq, (float)quad, FFT_IMU_N, 0.0f);
    st.std         = epm_dsp_std_from_sums((float)sum, (float)sq, FFT_IMU_N);
    st.skewness    = epm_dsp_skewness_from_sums((float)sum, (float)sq, (float)cube, FFT_IMU_N, 0.0f);
    st.clip        = clip > 0 ? 1 : 0;
    return st;
}

/* ─── Single-axis FFT → accumulate power ─────────────────────────────────── */

static void fft_axis_accumulate(float *pwr_acc)
{
    /* DC removal */
    float dc = 0.0f;
    for (int i = 0; i < FFT_IMU_N; i++) dc += s_block[i];
    dc /= FFT_IMU_N;
    for (int i = 0; i < FFT_IMU_N; i++) s_block[i] -= dc;

    /* Hann window (SIMD) */
    dsps_mul_f32(s_block, s_window, s_windowed, FFT_IMU_N, 1, 1, 1);

    /* Pack interleaved complex (imag = 0) */
    for (int i = 0; i < FFT_IMU_N; i++) {
        s_fft[2 * i]     = s_windowed[i];
        s_fft[2 * i + 1] = 0.0f;
    }

    dsps_fft2r_fc32(s_fft, FFT_IMU_N);
    dsps_bit_rev2r_fc32(s_fft, FFT_IMU_N);

    /* Accumulate linear power */
    const float nf = 2.0f / FFT_IMU_N;
    for (int i = 0; i < IMU_HALF; i++) {
        float re = s_fft[2 * i]     * nf;
        float im = s_fft[2 * i + 1] * nf;
        pwr_acc[i] += re * re + im * im;
    }
}

/* ─── Envelope: band-pass -> rectify -> low-pass -> decimate -> FFT ──────── *
 * Consumes s_block as left by fft_axis_accumulate() (DC already removed —
 * harmless either way, since the band-pass's own high-pass stage removes
 * any residual DC/gravity component too). Mirrors fft_axis_accumulate()'s
 * Hann-window-then-FFT structure at ENV_N instead of FFT_IMU_N. */

static void envelope_axis_accumulate(epm_dsp_envelope_t *env, float *pwr_acc)
{
    int rc = epm_dsp_envelope_process(env, s_block, FFT_IMU_N, IMU_ENVELOPE_DECIM,
                                       s_env_block, ENV_N);
    if (rc != 0) {
        return;
    }

    dsps_mul_f32(s_env_block, s_env_window, s_env_windowed, ENV_N, 1, 1, 1);

    for (int i = 0; i < ENV_N; i++) {
        s_env_fft[2 * i]     = s_env_windowed[i];
        s_env_fft[2 * i + 1] = 0.0f;
    }

    dsps_fft2r_fc32(s_env_fft, ENV_N);
    dsps_bit_rev2r_fc32(s_env_fft, ENV_N);

    const float nf = 2.0f / ENV_N;
    for (int i = 0; i < ENV_HALF; i++) {
        float re = s_env_fft[2 * i]     * nf;
        float im = s_env_fft[2 * i + 1] * nf;
        pwr_acc[i] += re * re + im * im;
    }
}

/* ─── Convert accumulated power to dBFS array, reset accumulator ─────────── */

static void pwr_to_db(float *pwr_acc, float *db_out, int n)
{
    const float inv_n = 1.0f / SPEC_AVG_N;
    for (int i = 0; i < n; i++) {
        db_out[i]  = 10.0f * log10f(pwr_acc[i] * inv_n + 1e-12f);
        pwr_acc[i] = 0.0f;
    }
    db_out[0] = -120.0f; /* zero DC bin */
}

/* ─── Task function ───────────────────────────────────────────────────────── */

static void imu_task_fn(void *arg)
{
    (void)arg;

    /* Local to this task — single owner, no cross-task access. */
    int avg_cnt = 0;
    int fail_cnt = 0;

    /* Per-axis stats from the final block of the averaging window */
    axis_stats_t st_x = {0}, st_y = {0}, st_z = {0};

    while (1) {
        /* Simulate acquisition time for one block at the target ODR.
         * Explicit parens ensure (N*1000) is computed first — without them
         * integer division (1000/IMU_FS_HZ) truncates to 0 when Fs > 1000. */
        vTaskDelay(pdMS_TO_TICKS((FFT_IMU_N * 1000) / IMU_FS_HZ));

        /* Each hal_accel_read_block() call returns the sample count written,
         * or a negative errno (e.g. -ETIMEDOUT from an unplugged/unresponsive
         * KX134). The return value used to be discarded here, so a sensor
         * read failure went completely unnoticed: the task processed
         * whatever garbage/stale bytes were left in s_block and republished
         * it as if it were a fresh reading, forever. Each axis is now
         * skipped (accumulator left untouched) on failure instead. */

        /* ── X axis (radial A): 50 Hz imbalance tone ── */
        int rc_x = hal_accel_read_block(HAL_ACCEL_AXIS_X, s_block, FFT_IMU_N);
        bool ok_x = (rc_x >= 0);
        if (ok_x) {
            st_x = compute_axis_stats();
            fft_axis_accumulate(s_pwr_x);
            envelope_axis_accumulate(&s_env_state_x, s_env_pwr_x);
        }

        /* ── Y axis (radial B): 50 Hz + 150 Hz (3× harmonic) ── */
        int rc_y = hal_accel_read_block(HAL_ACCEL_AXIS_Y, s_block, FFT_IMU_N);
        bool ok_y = (rc_y >= 0);
        if (ok_y) {
            st_y = compute_axis_stats();
            fft_axis_accumulate(s_pwr_y);
            envelope_axis_accumulate(&s_env_state_y, s_env_pwr_y);
        }

        /* ── Z axis (axial): 100 Hz (2× shaft — mild misalignment) ── */
        int rc_z = hal_accel_read_block(HAL_ACCEL_AXIS_Z, s_block, FFT_IMU_N);
        bool ok_z = (rc_z >= 0);
        if (ok_z) {
            st_z = compute_axis_stats();
            fft_axis_accumulate(s_pwr_z);
            envelope_axis_accumulate(&s_env_state_z, s_env_pwr_z);
        }

        s_epochs++;

        if (!ok_x || !ok_y || !ok_z) {
            s_read_errors++;
            fail_cnt++;
            if (fail_cnt >= IMU_FAIL_MAX) {
                ESP_LOGE(TAG, "hal_accel_read_block: %d consecutive failed epochs "
                         "(x=%d y=%d z=%d) — reinitializing KX134",
                         fail_cnt, rc_x, rc_y, rc_z);
                /* A KX134 that lost and regained power (not the ESP32 side —
                 * that infrastructure is untouched) needs its registers,
                 * interrupt routing included, reprogrammed before it will
                 * ever assert INT1 again; nothing else does this
                 * automatically. One attempt per streak, not per failed
                 * epoch, so a genuinely still-unplugged sensor doesn't get
                 * fought every 80ms — fail_cnt resets below regardless of
                 * outcome, so a sensor still absent will simply climb back
                 * to IMU_FAIL_MAX and trigger another attempt in due course. */
                s_reinit_attempts++;
                int rc_reinit = hal_accel_reinit();
                if (rc_reinit == 0) {
                    s_reinit_successes++;
                    ESP_LOGW(TAG, "hal_accel_reinit OK — resuming read attempts");
                } else {
                    ESP_LOGE(TAG, "hal_accel_reinit failed: %d — sensor still absent, "
                             "will retry after next %d failures", rc_reinit, IMU_FAIL_MAX);
                }
                fail_cnt = 0;
            } else {
                ESP_LOGW(TAG, "hal_accel_read_block failed this epoch "
                         "(x=%d y=%d z=%d) (%d/%d)",
                         rc_x, rc_y, rc_z, fail_cnt, IMU_FAIL_MAX);
            }
        } else {
            fail_cnt = 0;
        }

        avg_cnt++;

        if (avg_cnt < SPEC_AVG_N) continue;

        /* ── Build frame after SPEC_AVG_N blocks ── */
        pwr_to_db(s_pwr_x, s_frame.fft_x, IMU_HALF);
        pwr_to_db(s_pwr_y, s_frame.fft_y, IMU_HALF);
        pwr_to_db(s_pwr_z, s_frame.fft_z, IMU_HALF);
        pwr_to_db(s_env_pwr_x, s_frame.fft_x_env, ENV_HALF);
        pwr_to_db(s_env_pwr_y, s_frame.fft_y_env, ENV_HALF);
        pwr_to_db(s_env_pwr_z, s_frame.fft_z_env, ENV_HALF);

        s_frame.rms_x   = st_x.rms;
        s_frame.rms_y   = st_y.rms;
        s_frame.rms_z   = st_z.rms;
        s_frame.crest_x = (st_x.rms > 1e-8f) ? st_x.peak_abs / st_x.rms : 0.0f;
        s_frame.crest_y = (st_y.rms > 1e-8f) ? st_y.peak_abs / st_y.rms : 0.0f;
        s_frame.crest_z = (st_z.rms > 1e-8f) ? st_z.peak_abs / st_z.rms : 0.0f;
        s_frame.kurtosis_x = st_x.kurtosis;
        s_frame.kurtosis_y = st_y.kurtosis;
        s_frame.kurtosis_z = st_z.kurtosis;
        s_frame.std_x      = st_x.std;
        s_frame.std_y      = st_y.std;
        s_frame.std_z      = st_z.std;
        s_frame.skewness_x = st_x.skewness;
        s_frame.skewness_y = st_y.skewness;
        s_frame.skewness_z = st_z.skewness;
        s_frame.peak_x = st_x.peak_signed;
        s_frame.peak_y = st_y.peak_signed;
        s_frame.peak_z = st_z.peak_signed;
        s_frame.dc_x         = st_x.dc;   /* X-axis gravity/tilt component — useful for mounting angle detection */
        s_frame.clip         = st_x.clip | st_y.clip | st_z.clip;
        s_frame.timestamp_ms = (uint32_t)(esp_timer_get_time() / 1000);

        avg_cnt = 0;
        xQueueOverwrite(s_queue, &s_frame);
    }
}

/* ─── Public API ──────────────────────────────────────────────────────────── */

QueueHandle_t imu_task_get_queue(void) { return s_queue; }

void imu_task_start(void)
{
    s_queue = xQueueCreate(1, sizeof(imu_frame_t));
    configASSERT(s_queue != NULL);

    int rc = hal_accel_init();
    if (rc != 0) {
        ESP_LOGE(TAG, "hal_accel_init failed: %d", rc);
    }
    rc = hal_accel_start();
    if (rc != 0) {
        ESP_LOGE(TAG, "hal_accel_start failed: %d", rc);
    }
    /* Non-fatal either way, matching net_task_start()'s link_mqtt_start()
     * convention: the task still starts, and imu_task_fn's per-axis
     * fail-streak escalation (see below) already handles an accelerometer
     * that never becomes responsive after this point. */

    dsps_wind_hann_f32(s_window, FFT_IMU_N);
    dsps_wind_hann_f32(s_env_window, ENV_N);
    /* FFT twiddle-factor table initialised in app_main */

    epm_dsp_envelope_init(&s_env_state_x, (float)IMU_FS_HZ, IMU_ENVELOPE_BAND_LO_HZ,
                           IMU_ENVELOPE_BAND_HI_HZ, IMU_ENVELOPE_LP_HZ);
    epm_dsp_envelope_init(&s_env_state_y, (float)IMU_FS_HZ, IMU_ENVELOPE_BAND_LO_HZ,
                           IMU_ENVELOPE_BAND_HI_HZ, IMU_ENVELOPE_LP_HZ);
    epm_dsp_envelope_init(&s_env_state_z, (float)IMU_FS_HZ, IMU_ENVELOPE_BAND_LO_HZ,
                           IMU_ENVELOPE_BAND_HI_HZ, IMU_ENVELOPE_LP_HZ);

    ESP_LOGI(TAG, "imu_task starting (3-axis): %d-pt × 3 axes, "
             "avg=%d, %.2f Hz/bin, Fs=%d Hz, envelope %.0f-%.0f Hz -> %d-pt/%d",
             FFT_IMU_N, SPEC_AVG_N, (float)IMU_FS_HZ / FFT_IMU_N, IMU_FS_HZ,
             IMU_ENVELOPE_BAND_LO_HZ, IMU_ENVELOPE_BAND_HI_HZ, ENV_N, IMU_ENVELOPE_DECIM);

    xTaskCreatePinnedToCore(imu_task_fn, "imu_task", TASK_STACK_IMU, NULL,
                            TASK_PRIO_IMU, &s_task_handle, 0);
}
