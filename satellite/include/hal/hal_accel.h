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

/* Hardware FIFO depth (KX134 TRM: 86 sets of 16-bit samples). Exposed
 * because the caller's chunk size has to be chosen against it - asking
 * for fewer frames than this per read leaves the FIFO permanently near
 * its cap, which is where samples get discarded. */
#define HAL_ACCEL_FIFO_MAX_FRAMES 86

/*
 * FIFO-drain diagnostics. The KX134 runs in BM_STREAM mode ("discard
 * oldest when full") off the Buffer Full interrupt, so a late read loses
 * samples SILENTLY - the next read just returns newer data and the
 * assembled FFT window is spliced across the gap, which splatters energy
 * across every bin. base-station/sketch/accel_sampler.h counts the same
 * condition (its fifo_full_count); this node had no equivalent, so drops
 * were invisible here.
 *
 * gap_us is the discriminator, not fifo_full_reads: the FIFO holds
 * KX134_FIFO_MAX_FRAMES=86 frames, which at ODR 12800Hz is 6.72ms of
 * data, so any interval longer than that between consecutive reads means
 * samples were definitely overwritten before they were read. Being at
 * cap merely means the interrupt fired, which is the normal steady state.
 */
struct hal_accel_stats {
	uint32_t reads;           /* successful hal_accel_read_block() calls */
	uint32_t fifo_full_reads; /* reads that found the FIFO at its cap */
	uint32_t overrun_reads;   /* reads later than the FIFO's own span */
	uint32_t frames_read;     /* frames handed back in total */
	uint32_t max_gap_us;      /* longest gap between consecutive reads */
	uint32_t span_us;         /* the FIFO's span, i.e. the overrun limit */
	uint64_t total_gap_us;    /* for a mean; pair with `reads` */
};

/* Snapshots the counters above. Safe to call from any task. */
void hal_accel_get_stats(struct hal_accel_stats *out);

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
