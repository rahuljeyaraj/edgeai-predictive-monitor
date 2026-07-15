/*
 * Sensor fusion / transport (port of the old repo's fuser_thread.c).
 * Reads the latest full-resolution mic + accel spectra, packs them into one
 * self-describing frame, and hands it to the dedicated MCU<->MPU SPI transport
 * (spi_link_stage_frame()) for the MPU to pull - NOT over the shared Bridge UART
 * (which the old chunked notify stream recurringly wedged; docs/progress2.md),
 * and not the 32-bucket downsampled Bridge providers the samplers expose for
 * their standalone tests. See fuser.cpp's header comment for the full rationale
 * (why SPI, why float32/full-res).
 */
#pragma once

#include <stdint.h>

#include "app_config.h"

void fuser_start(void);

#if BENCHMARK_STATS_ENABLED
/* This module's own transport-stage counters - fuser only reports on
 * itself here (frames pushed, epoch overruns, build+send duration), the
 * same "one accessor per module" shape as mic_sampler_get_stats()/
 * accel_sampler_get_stats(). Aggregation across modules into one Bridge-
 * pollable summary lives in bench.cpp, not here - fuser stays scoped to
 * fusion + transport. Compiled out entirely when BENCHMARK_STATS_ENABLED is
 * 0 (app_config.h). */
struct fuser_bench_stats {
  uint32_t frames_sent;    /* fused frames pushed since boot */
  uint32_t overrun_count;  /* epochs where build+send took >= FUSER_EPOCH_MS,
                             * i.e. the link/CPU couldn't keep up that cycle */
  uint32_t send_ms_sum;    /* cumulative build+send time, for an avg-since-
                             * boot in bench.cpp (send_ms_sum / frames_sent) */
  uint32_t send_ms_max;    /* worst single-epoch build+send time seen */
};

/* Snapshot of the counters above - same no-locking rationale as
 * mic_sampler_get_stats() (mic_sampler.h): single writer (the fuser
 * thread), a torn read across fields is harmless for this diagnostic. */
void fuser_get_stats(struct fuser_bench_stats *out);
#endif /* BENCHMARK_STATS_ENABLED */
