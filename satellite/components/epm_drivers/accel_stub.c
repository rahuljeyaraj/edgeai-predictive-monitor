/*
 * accel_stub.c — synthetic 3-axis accelerometer stub behind hal_accel.h.
 *
 * Dev fallback used until the real KX134 SPI driver lands (Phase 9).
 * Selected via Kconfig (CONFIG_EPM_ACCEL_USE_STUB, default y).
 *
 * Stub signal (unchanged from the pre-Phase-3 imu_task.c inline generator):
 *   X: 50 Hz sine 0.005g  + noise (radial imbalance at shaft fundamental)
 *   Y: 50 Hz + 150 Hz     + noise (same shaft, slightly different radial path)
 *   Z: 100 Hz sine 0.003g + noise (2x shaft -- typical mild misalignment)
 */

#include <math.h>

#include "esp_random.h"   /* hardware TRNG -- thread-safe, no global shared state */

#include "hal/hal_accel.h"

/* Mirrors src/epm_config.h's IMU_FS_HZ. Not included directly: epm_drivers
 * must not depend on the main component's config (src already depends on
 * epm_drivers to call hal_accel_*(); the reverse would be a component
 * dependency cycle) -- same pattern as link_mqtt.c's broker host/port. */
#ifndef IMU_FS_HZ
#define IMU_FS_HZ   25600   /* KX134 ODR -- configure register at runtime too */
#endif

/* Phase accumulators -- one pair per axis, persisting across calls so tone
 * phase stays continuous the same way the original task-local statics did. */
static float s_ph_x1 = 0.0f, s_ph_x2 = 0.0f;
static float s_ph_y1 = 0.0f, s_ph_y2 = 0.0f;
static float s_ph_z1 = 0.0f, s_ph_z2 = 0.0f;

static void generate_stub_axis(float *out, size_t n,
                                float *phase1, float *phase2,
                                float freq1_hz, float amp1,
                                float freq2_hz, float amp2,
                                float noise_amp)
{
    /* Accumulate phase in radians and wrap to keep cosf() arguments small.
     * Large arguments (after hours of operation) force expensive range
     * reduction inside cosf() and cause a gradual fps drop. */
    const float pi2  = 2.0f * 3.14159265f;
    const float inc1 = pi2 * freq1_hz / (float)IMU_FS_HZ;
    const float inc2 = pi2 * freq2_hz / (float)IMU_FS_HZ;

    for (size_t i = 0; i < n; i++) {
        float v = amp1 * cosf(*phase1);
        *phase1 += inc1;
        if (*phase1 >= pi2) *phase1 -= pi2;

        if (freq2_hz > 0.0f) {
            v += amp2 * cosf(*phase2);
            *phase2 += inc2;
            if (*phase2 >= pi2) *phase2 -= pi2;
        }
        /* esp_random() -- hardware TRNG, thread-safe, no global shared state.
         * Cast to int32_t before dividing to get a signed value in [-1, 1]. */
        v += noise_amp * ((float)(int32_t)esp_random() / 2147483648.0f);
        out[i] = v;
    }
}

int hal_accel_init(void)   { return 0; }
int hal_accel_start(void)  { return 0; }
int hal_accel_reinit(void) { return 0; }
void hal_accel_stop(void)  { }

uint32_t hal_accel_get_sample_rate(void) { return IMU_FS_HZ; }

/* ── Stats (Part I: one <module>_get_stats() accessor per module) ────────── */

static uint32_t s_reads_ok    = 0;
static uint32_t s_read_errors = 0;

void hal_accel_get_stats(struct hal_accel_stats *out)
{
    if (out == NULL) return;
    out->reads_ok      = s_reads_ok;
    out->read_errors   = s_read_errors;
    out->fifo_max_hits = 0; /* stub has no FIFO */
}

int hal_accel_read_block(enum hal_accel_axis axis, float *out_samples, size_t max_samples)
{
    switch (axis) {
    case HAL_ACCEL_AXIS_X:
        generate_stub_axis(out_samples, max_samples, &s_ph_x1, &s_ph_x2,
                            50.0f, 0.005f, 0.0f, 0.0f, 0.001f);
        break;
    case HAL_ACCEL_AXIS_Y:
        generate_stub_axis(out_samples, max_samples, &s_ph_y1, &s_ph_y2,
                            50.0f, 0.004f, 150.0f, 0.002f, 0.001f);
        break;
    case HAL_ACCEL_AXIS_Z:
        generate_stub_axis(out_samples, max_samples, &s_ph_z1, &s_ph_z2,
                            100.0f, 0.003f, 0.0f, 0.0f, 0.0008f);
        break;
    default:
        s_read_errors++;
        return -1;
    }
    s_reads_ok++;
    return (int)max_samples;
}
