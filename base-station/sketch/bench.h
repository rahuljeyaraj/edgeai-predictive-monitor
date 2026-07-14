/*
 * Cross-module benchmark/throughput reporting - the "get_bench_stats" Bridge
 * provider. Port of the old repo's BENCHMARK_STATS_ENABLED instrumentation
 * (docs/Sensor_Throughput_Tuning_Plan.md Phase 0: per-stage counters in
 * hal_accel.h/hal_audio.h/hal_transport.h/the sampler thread headers, plus
 * fuser_thread.c's periodic LOG_INF summary line).
 *
 * Deliberately its own module, not folded into fuser.cpp: fuser owns sensor
 * fusion + transport only, same as every other module here owns exactly one
 * concern. This one owns aggregating everyone else's already-published
 * counters (mic_sampler_get_stats()/accel_sampler_get_stats()/
 * fuser_get_stats()) into a single poll-friendly summary string, nothing
 * more - it doesn't touch hardware and doesn't run its own thread. See
 * bench.cpp's header comment for why no background thread/timer is needed
 * (unlike the old repo's fixed ~1s LOG_INF cadence).
 */
#pragma once

void bench_start(void);
