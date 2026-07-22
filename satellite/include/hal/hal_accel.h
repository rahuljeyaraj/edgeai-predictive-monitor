#ifndef HAL_ACCEL_H_
#define HAL_ACCEL_H_

#include <stddef.h>
#include <stdint.h>

/*
 * KX134-1211 accelerometer contract - same chip, same SPI FIFO burst-read
 * approach as mcu/src/hal/hal_accel.h, ported to Arduino's SPI library.
 * src/drivers/kx134.cpp is the implementation.
 *
 * Samples handed back by hal_accel_read_block() are three interleaved
 * axes (X, Y, Z) per sample, oldest first - identical layout/semantics to
 * mcu/'s hal_accel_read_block(), see that header's own comment for the
 * KX134 FIFO framing this mirrors exactly (same silicon, same register
 * map).
 */

int hal_accel_init(void);
int hal_accel_start(void);

/* Blocks until at least one frame is available, then copies up to
 * max_samples *frames* (X, Y, Z per frame) into out_samples, interleaved
 * as [x0, y0, z0, x1, y1, z1, ...] - out_samples must have room for
 * max_samples * 3 int32_t values. Returns the number of frames written,
 * or a negative errno (negated Arduino/ESP-IDF-style error codes - see
 * kx134.cpp). */
int hal_accel_read_block(int32_t *out_samples, size_t max_samples);

uint32_t hal_accel_get_sample_rate(void);
void hal_accel_stop(void);

#endif /* HAL_ACCEL_H_ */
