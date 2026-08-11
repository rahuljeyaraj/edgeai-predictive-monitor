#pragma once

#include <stddef.h>
#include <stdint.h>

/*
 * Accelerometer contract for the 3-axis vibration path (KX134-1211,
 * matches the reference repo's satellite/include/hal/hal_accel.h
 * init/start/read_block/get_sample_rate/stop shape). Any code that needs
 * axis samples includes this header; components/epm_drivers/accel_stub.c
 * is the only implementation until the real SPI driver lands (Phase 9).
 *
 * Deliberately per-axis float, not the reference's interleaved raw
 * int32_t frame: the stub generates a distinct tuned synthetic signal per
 * axis (see accel_stub.c), and src/threads/imu_task.c's FFT pipeline
 * already consumes one axis at a time. The real KX134 driver can still
 * satisfy this contract (its FIFO burst-read naturally de-interleaves
 * into per-axis buffers before handing samples up).
 *
 * Pure C, zero ESP-IDF includes, per this repo's HAL layering/naming conventions.
 */

enum hal_accel_axis {
    HAL_ACCEL_AXIS_X = 0,
    HAL_ACCEL_AXIS_Y = 1,
    HAL_ACCEL_AXIS_Z = 2,
};

int hal_accel_init(void);
int hal_accel_start(void);

/* Re-programs the sensor's own registers (ODR/range/interrupt-routing/FIFO
 * config) without touching host-side SPI bus/semaphore/GPIO-ISR state —
 * that infrastructure lives on the ESP32 and survives a sensor-side power
 * loss untouched, only the sensor's registers do not. Call after a
 * sustained read-failure streak (task's call when "sustained" means enough
 * to rule out a single transient miss) to recover a KX134 that lost and
 * regained power without a full board reset. Returns the same errno
 * conventions as hal_accel_init(); a non-zero return (e.g. sensor still
 * disconnected) is expected and not fatal — the caller's normal failure
 * counting resumes and will trigger another attempt after the next streak.
 * No-op returning 0 on the stub. */
int hal_accel_reinit(void);

/* Fills out_samples[0..max_samples) with one axis' normalised-g samples
 * (oldest first). Returns the number of samples written, or a negative
 * errno. */
int hal_accel_read_block(enum hal_accel_axis axis, float *out_samples, size_t max_samples);

uint32_t hal_accel_get_sample_rate(void);
void hal_accel_stop(void);

struct hal_accel_stats {
    uint32_t reads_ok;      /* hal_accel_read_block() calls that returned >= 0 */
    uint32_t read_errors;   /* hal_accel_read_block() calls that returned a negative errno */
    uint32_t fifo_max_hits; /* KX134 FIFO seen at max capacity (possible dropped samples); always 0 on the stub */
};

/* Per Part I's one-<module>_get_stats()-per-module convention. Shared HAL
 * contract (not module-prefixed): both accel_kx134_spi.c and accel_stub.c
 * implement this, matching the naming precedent of the other hal_accel_*
 * functions above. */
void hal_accel_get_stats(struct hal_accel_stats *out);
