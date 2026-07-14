/*
 * See bench.h for why this is its own module rather than living in
 * fuser.cpp.
 *
 * No background thread and no periodic timer, unlike the old repo's fixed
 * ~1s LOG_INF cadence: this project has no logging backend to piggyback on
 * (the established pattern here is a Bridge.provide() String, not LOG_INF -
 * see mic_sampler.cpp's header comment), and this codebase's worst hardware
 * bugs so far have all been thread-priority interactions (mic's busy-poll
 * starving Bridge, the fuser/Bridge-update-thread tie - see
 * docs/PROGRESS.md), so this avoids adding a fourth priority band to reason
 * about for something with no real-time requirement of its own.
 *
 * Instead, get_bench_stats() computes each rate on demand from the delta
 * since its *own* previous call: whatever cadence the MPU dashboard polls
 * at becomes the rate window, the same way a network monitoring tool derives
 * throughput from repeated counter polls rather than a fixed device-side
 * interval. The tradeoff: the very first call after boot (or after a long
 * gap since the last poll) has no prior snapshot / a stale one, so it
 * reports 0.0 fps rather than a real rate - acceptable for a dashboard that
 * polls on a steady cadence, which is the intended use.
 *
 * Field set kept deliberately compact - single Bridge round trip, well
 * under Arduino_RPClite's 256-byte ceiling (see mic_sampler.cpp's header
 * comment) even at worst-case (multi-day uptime, 10-digit counters):
 * measured on hardware at ~150-170 bytes in the current 3-module form (see
 * docs/PROGRESS.md's benchmark entry for the measured length). Adding a
 * module's stats here later (e.g. a satellite node) should keep new fields
 * similarly short-labeled, or split into a second "get_bench_stats_2"
 * provider rather than risk the combined string outgrowing the ceiling.
 *
 * Forward-looking note for the MPU dashboard/satellite work: every module
 * here already follows the same "cumulative counters behind a
 * *_get_stats() accessor" shape (mic_sampler.h/accel_sampler.h/fuser.h)
 * specifically so a future satellite node can expose an identically-shaped
 * get_bench_stats string without inventing a new schema - the MPU side can
 * poll every node the same way.
 */
#include "bench.h"

#include "accel_sampler.h"
#include "app_config.h"
#include "fuser.h"
#include "mic_sampler.h"

#include <Arduino_RouterBridge.h>
#include <zephyr/kernel.h>

#if BENCHMARK_STATS_ENABLED

/* Previous poll's snapshot, used to compute this poll's rates. Bridge
 * invokes get_bench_stats() from its own update thread, one call at a time
 * (never concurrently with itself), so no locking needed. */
struct bench_snapshot {
  int64_t uptime_ms;
  uint32_t mic_windows;
  uint32_t accel_windows;
  uint32_t fuser_frames;
};
static struct bench_snapshot bench_prev;
static bool bench_prev_valid = false;

static String bench_get_stats() {
  struct mic_bench_stats mic;
  struct accel_bench_stats accel;
  struct fuser_bench_stats fuser;
  mic_sampler_get_stats(&mic);
  accel_sampler_get_stats(&accel);
  fuser_get_stats(&fuser);

  int64_t now = k_uptime_get();
  float mic_fps = 0.0f, accel_fps = 0.0f, fuser_fps = 0.0f;

  if (bench_prev_valid) {
    float dt_s = (float)(now - bench_prev.uptime_ms) / 1000.0f;
    if (dt_s > 0.0f) {
      mic_fps = (float)(mic.windows_completed - bench_prev.mic_windows) / dt_s;
      accel_fps = (float)(accel.windows_completed - bench_prev.accel_windows) / dt_s;
      fuser_fps = (float)(fuser.frames_sent - bench_prev.fuser_frames) / dt_s;
    }
  }

  bench_prev.uptime_ms = now;
  bench_prev.mic_windows = mic.windows_completed;
  bench_prev.accel_windows = accel.windows_completed;
  bench_prev.fuser_frames = fuser.frames_sent;
  bench_prev_valid = true;

  float fus_avg_ms = fuser.frames_sent > 0
                      ? (float)fuser.send_ms_sum / (float)fuser.frames_sent
                      : 0.0f;

  String out;
  out += "mic_fps="; out += String(mic_fps, 1);
  out += ",mic_win="; out += String(mic.windows_completed);
  out += ",mic_to="; out += String(mic.timeouts);
  out += ",acc_fps="; out += String(accel_fps, 1);
  out += ",acc_win="; out += String(accel.windows_completed);
  out += ",acc_isr="; out += String(accel.isr_count);
  out += ",acc_ff="; out += String(accel.fifo_full_count);
  out += ",acc_to="; out += String(accel.timeout_count);
  out += ",fus_fps="; out += String(fuser_fps, 1);
  out += ",fus_frm="; out += String(fuser.frames_sent);
  out += ",fus_ovr="; out += String(fuser.overrun_count);
  out += ",fus_avg="; out += String(fus_avg_ms, 1);
  out += ",fus_max="; out += String(fuser.send_ms_max);
  return out;
}

#endif /* BENCHMARK_STATS_ENABLED */

void bench_start(void) {
#if BENCHMARK_STATS_ENABLED
  Bridge.begin(BRIDGE_BAUD); /* idempotent - every other module also calls this */
  Bridge.provide("get_bench_stats", bench_get_stats);
#endif
}
