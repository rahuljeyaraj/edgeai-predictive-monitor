# Plan — Dev/perf page

Status: **Redesigned and built 2026-07-19, verified end-to-end on real
hardware (see §8).** This doc originally captured a pre-implementation
brainstorm (§9 below, kept for history). That version got built, but two
problems surfaced once it was actually used:

1. It never live-updated in a real browser — only a full page reload showed
   fresh numbers. Root cause: `run_perf_broadcast_loop` in `api/app.py` had no
   exception guard, so a single transient failure permanently killed the WS
   push task for the rest of the process's life (`GET /perf` kept working,
   since that's a separate code path — only the live push died). **Fixed**,
   committed `e4aefce`.
2. The content itself didn't serve a demo — raw firmware counters and register
   debug behind a nested "Advanced" disclosure aren't explainable to someone
   watching live. Feedback session produced the redesign below. The old
   meters/fps-tiles/Satellites-tier version is committed as `dd25281`,
   explicitly marked in its own commit message as a superseded draft.

**This section (through §8) is the current design to build against.** §9 is
the original brainstorm, kept only as historical record — don't build from it.

---

## 0. Why this exists

Same as originally: the dashboard has no visibility into its own health, and
judges/operators see sensor data and fault status but nothing about whether
the pipeline is keeping up. Reframed after the redesign discussion into two
sharper questions any viewer should be able to answer in one glance:

- **Is our own compute hardware under strain?**
- **Is each pipeline keeping up, and is there headroom for more (satellite)
  nodes?**

Anything that doesn't directly answer one of those two questions is out.

## 1. Layout

Two tiers, both always-visible live time-plots — no meters, no static
cells/heat-strips, no nested "Advanced" disclosure anywhere. **No Satellites
tier** (dropped entirely, not just hidden/placeholder — re-add only on a new,
explicit ask once real satellite firmware exists).

## 2. Tier 1 — QRB2210 (the base station's own compute hardware)

Answers "is our own box under strain." Every metric is a live area/line chart
(Task-Manager-style: big current value + small filled trend, ~60s rolling
window — `perf_stats` already broadcasts once/second per
`_PERF_BROADCAST_INTERVAL_S`, so 60 client-side samples = 60s). No subtitle
text under the tier header (explicitly disliked in feedback — just the chip
name).

- **One live chart per CPU core** — `system.cpu_percent_per_core` (a list,
  one entry per core; this board reports 4). Not a single aggregate "CPU %"
  chart, and not a heat-strip of static cells — both dropped in favor of one
  time-plot per core. Rationale: this pipeline is single-threaded Python (no
  multiprocessing in `pipeline/manager.py`), so under the GIL, load likely
  pins to one core while the rest idle — an aggregate average could read as a
  comfortable number while one core is actually maxed out. A viewer can still
  read overall load by scanning across the per-core charts, so a separate
  aggregate chart is redundant.
- **Memory %** — derived from `system_memory_used_mb` / `system_memory_total_mb`
  (already in `/perf`'s payload); keep the used/total MB as a small caption.
- **GPU %** — `gpu.busy_percent` when `gpu.available`, else the existing "GPU
  bridge not provisioned" empty-state (already implemented, keep as-is — see
  §5 history below for how this data source works).
- **Temperature** — built. Real thermal zones confirmed on-device:
  `/sys/class/thermal/thermal_zone*` exposes `cpuss0_thermal`/`cpuss1_thermal`
  among others (gpu/wlan/mdm/camera/video); averages the two `cpuss` zones.
  **Do not use `psutil.sensors_temperatures()`** to read them — measured
  8-10+ *seconds* per call on this board (vs. ~1-2ms reading the same files
  directly), apparently pathological hwmon-scanning behavior in psutil on
  this platform, not the sysfs I/O itself. Calling it from the once-a-second
  broadcast loop stalled the asyncio event loop for 8-10s per tick, breaking
  live updates for everything (WS pushes, REST) exactly like the historical
  bug this page already had to fix once (`e4aefce`) — caught via the existing
  `perf_test.py` WS-ordering test failing consistently on real hardware, not
  by inspection. `monitoring/perf.py`'s `_read_cpu_temp_celsius()` reads
  `type`/`temp` under each `thermal_zone*` dir with plain `open()` instead.
  Drops the metric silently (no chart) if no `cpuss` zone is exposed, same
  "don't show a fake reading" rule as the GPU empty state.

Nothing else on this tier — no pipeline count here (Tier 2's row count already
shows it), no ingest/transport counters, no falling-behind flag as a raw
number.

## 3. Tier 2 — Pipelines (replaces the old "MCU/fuser tier" — no longer about
the MCU chip's own compute at all)

Answers "is each pipeline keeping up, and how much headroom is left" — which
is what actually answers "room for more satellites," not the sensor node's own
CPU (explicit user feedback: "we don't care [about node CPU] as long as we're
getting data at the required frame rate").

**One row/card per live pipeline**, keyed by `node_id` — iterate
`payload.pipelines` (already a dict `{node_id: {frame_count, avg_latency_ms,
frames_per_sec, falling_behind}}`, see `monitoring/perf.py`'s
`PipelineStats.to_dict()` — already broadcast today, no backend change needed
for this tier). Today that's exactly one row (`base_station`); build it as N
rows from the start so it extends automatically once satellite pipelines
exist.

Each row is two live charts:

- **Frames arrived/sec** — `frames_per_sec` from that pipeline's own stats.
  (`route()` calls `handle_frame()` synchronously in the same thread that
  pulls frames off SPI, so "arrived" and "processed" are the same event — a
  second "processed/sec" line was discussed and dropped as redundant; this
  one line already answers "are we getting the frame rate we need.")
- **Pipeline time-budget used, %** — derived, no new backend field:
  ```
  budget_used_% = (avg_latency_ms / (1000 / frames_per_sec)) × 100
  ```
  numerator = average time `handle_frame()` takes per frame (`avg_latency_ms`,
  already computed); denominator = average time between frames arriving for
  that pipeline (`1000 / frames_per_sec`, already computed). Both inputs
  already exist per-pipeline in the broadcast payload. This is the metric that
  actually reads as "working well" (low %) vs. "struggling" (climbing toward
  100%), and `100% − this` is the honest per-node headroom signal — **do not
  compute or display a fabricated "N more satellites" estimate** on top of it;
  let the viewer read the raw percentage.

**Explicitly not built now**: a third "frames processed by classification
model/sec" line. Confirmed via code search this session that no classification
model is wired into the live pipeline at all — `pipeline/manager.py`'s
`handle_frame()` runs exactly one model stage (the autoencoder's
`reconstruction_error()`, `pipeline/autoencoder.py`), timed as one
undifferentiated lump alongside gate-check and feature extraction. The Edge
Impulse fault classifier (`docs/EDGE_IMPULSE_FAULT_CLASSIFICATION_PLAN.md`)
exists only as an offline experiment under `tools/` — no `pipeline/
classifier.py`, no per-node "classifier enabled" flag, nothing to hook into.
Don't stub or fake this row; it simply doesn't exist until a real classifier +
per-node flag are built (a separate, larger project).

## 4. Sensor sampling-rate / Nyquist showcase — explored, explicitly dropped

Investigated this session as a candidate metric (mic 96,000 Hz sampling / ~24,000
Hz effective usable bandwidth since firmware only sends 512 of 1024 bins;
accel 1,600 Hz ODR / clean 800 Hz Nyquist, both fixed compile-time constants —
see `sketch/mic_sampler.cpp:139`, `sketch/app_config.h:38`). Real, correct
data, and it's already decoded per-frame into `SpiConsumer.last_meta` — but the
user decided it belongs on the Fleet tab if anywhere, not Performance. **Out of
scope for this page.**

## 5. GPU — two separate tracks, do not conflate (unchanged from original brainstorm)

Still accurate, carried forward as-is:

### 5a. Chart rendering (separate backlog item, not this page)

Swap `type: "scatter"`/`"heatmap"` → `"scattergl"`/`"heatmapgl"` in
`frontend/charts.js` to move redraw cost onto the *viewer's* GPU. Needs a
gl2d-capable Plotly bundle (current vendored one is SVG-only). Feature-detect
WebGL and fall back to plain traces — this renders in front of judges on
unknown hardware. Unrelated to this page's GPU tile.

### 5b. Real GPU busy% for Tier 1's GPU chart — built and working

`host/gpu_bridge.py` (root daemon) reads `/sys/kernel/debug/dri/<N>/perf`
continuously (this board's `msm`/Adreno driver combines DPU+GPU into one DRM
device — no separate KGSL; don't use the `/sys/class/drm/renderD<N>/device`
symlink, it resolves to the *display* device, not the GPU — scan
`/sys/kernel/debug/dri/*/` for a dir with both a `gpu` and `perf` file) and
serves the latest busy% over `/dev/gpu-perf.sock`. `monitoring/gpu_perf.py`
polls it ~1Hz, tri-state (`available: False` when the bridge isn't
provisioned/reachable vs. `available: True, busy_percent: 0.0` when genuinely
idle — never show a fake number for "no data"). `provision-gpu.sh` is a
one-time host step, **not** applied by `deploy.sh`, wiped by an OS reflash.
This reports whatever's actually driving the GPU (currently ~nothing, hence
~0%) — it doesn't make anything use the GPU; on-device ONNX/QNN inference
(unverified Adreno-version feasibility, separate research track) is the thing
that would eventually make this read non-zero.

## 6. MCU stats — removed, not just reshaped

The original brainstorm (§9) and the first implementation both had a MCU/fuser
tier polling `bench.cpp`'s `get_bench_stats`/`spi_link.cpp`'s
`get_spi_link_stats` (`monitoring/mcu_perf.py`'s `McuPerfPoller`, `Bridge.call`
on a 1s Python timer). **That module is deleted** (`e4aefce`) — Tier 2 above
gets pipeline throughput from Python-side `PipelineStats`, not MCU Bridge
calls, and nothing in the new design needs mic/accel/fuser fps or SPI-link
register debug. Don't recreate it.

Still-good background if MCU-side stats ever come back: this codebase's
Bridge UART is documented (`docs/PROGRESS.md`) to wedge under concurrent
access from multiple threads — it's happened twice now (an original
continuous-`Bridge.notify` incident, and `mcu_perf.py`'s poller thread this
session, which ran concurrently with `spi_reader.py`'s own `spi_arm` loop with
no synchronization). `common/bridge_lock.py` (a shared `threading.Lock()`) now
serializes every remaining `Bridge.call()` site (`spi_reader.py`'s `spi_arm`,
`main.py`'s `set_rgb`) and is kept as standing protection even though today's
two call sites happen to run on one thread — so the next poller that gets
added inherits the protection instead of silently reintroducing this bug a
third time.

## 7. Explicitly out of scope / accepted as-is

- **Waterfall/spectrum history storage** — unchanged from original brainstorm:
  raw spectrum bins pass through MPU RAM only transiently, buffered only
  client-side (`charts.js`'s `node.waterfall[channel]`), resets on page
  refresh. No storage change planned.
- **STM32U585 real CPU%/RAM usage** — investigated this session, confirmed
  infeasible without real firmware R&D: no Zephyr runtime-stats hook anywhere
  in `sketch/*`, no `prj.conf`/Kconfig mechanism in this Arduino-sketch-based
  build at all, and this board has a well-documented history of
  thread-priority disasters from much smaller changes. Not part of this plan;
  a separate firmware project if ever wanted.

## 8. Next steps

Status: **built and verified end-to-end on real hardware, 2026-07-19.**

- [x] Confirm `psutil.sensors_temperatures()` feasibility on real hardware
      (§2's temperature chart) before building it — confirmed real thermal
      zones exist, but psutil itself turned out unusable (8-10s/call); see
      §2's temperature bullet for the raw-sysfs fix.
- [x] Build: rewrote `frontend/index.html`/`perf.js`/`style.css`'s Performance
      tab against §2/§3 above (the WS-wiring in `charts.js`/`app.js` — the
      `perfHandler` plumbing routing `"perf_stats"` WS messages into `Perf`
      — was solid from the first pass and needed no changes). Backend:
      `monitoring/perf.py`'s `SystemStats` gained `cpu_temp_celsius`
      (`Optional[float]`, `None` when unavailable); no other backend change
      needed — Tier 2's `budget_used_%` is computed client-side from fields
      already broadcast.
- [x] Verify live-update end to end on real hardware: deployed via
      `deploy.sh`'s push+start (see gotcha below), backend test suite run
      on-device (`docker exec ... tests/perf_test.py`, `api_test.py` — both
      pass), then a headless-Chromium (Playwright) smoke test against the
      live dashboard over `adb forward tcp:8080` confirmed both tiers render
      (7 Tier-1 cards incl. temperature, one Tier-2 pipeline row), zero
      console errors, and `perf_stats` WS frames kept arriving with changing
      `frame_count`/`cpu_temp_celsius` over a 20s soak with no stall —
      screenshots matched the intended Task-Manager-style design.
      `adb forward` (not the device's LAN IP) was used since this dev
      machine is USB-attached only, no LAN route to the board; fine for a
      single scripted Playwright session even though §8 originally flagged
      `adb forward` as flaky for a human's real multi-tab browser use.

**New deploy.sh gotcha found this session:** the documented tar-over-`adb
shell`-stdin push (`tar -C "$LOCAL_DIR" ... -cf - . | adb shell "tar -C
'$REMOTE_DIR' -xf -"`) silently truncated mid-stream twice in a row on this
run — remote ended up with only a handful of top-level entries (`sketch/`,
`captures/`, a couple scripts), missing `app.yaml`/`python/`/`tests/`/etc.
entirely, with **no non-zero exit code** to catch it (the pipeline's exit
status is `adb shell`'s, not `tar`'s, and that returned 0). Symptom:
`arduino-app-cli app start` then fails with `descriptor app.yaml file
missing from app`. Fix used: `adb push <local.tar> /tmp/x.tar` (a real adb
file transfer, not a live shell-stdin pipe) then `adb shell "tar -C
'$REMOTE_DIR' -xf /tmp/x.tar"` — transferred the full ~5.8MB tar cleanly on
the first try both times it was used. `deploy.sh` itself hasn't been changed
to this two-step form yet since this wasn't reproduced enough times to be
sure it's not a one-off; worth switching if the truncation recurs.

---

## 9. Original brainstorm (2026-07-19, pre-implementation) — historical record only, do not build from this

Kept for context on how the page's scope evolved; every section above
supersedes the corresponding idea here.

Original framing: CPU/RAM/GPU, live sampling rate, dropped-frame count, a
judge-facing "no data lost" highlight, for the "Dev/perf page" item in
[DASHBOARD_IDEAS_BACKLOG.md](DASHBOARD_IDEAS_BACKLOG.md).

- **Hero band** (judge-facing "No data lost %" + fleet-wide traffic-light
  strip) — **dropped**: read as confusing/unexplained once built, and
  fault-detection reliability isn't this page's job (that's the Fleet tab's).
- **MPU tier as Meters** (CPU/RAM/GPU as filled-track gauges, per-core as a
  static heat-strip) — **dropped**: redesigned as live time-plots, see §2.
- **MCU/fuser tier via `Bridge.notify` push** — the brainstorm's original
  instinct was MCU-push over `Bridge.notify`; the first implementation
  reversed this to `Bridge.call` polling instead (continuous `Bridge.notify`
  streams have a documented history of wedging this board's UART permanently,
  `docs/PROGRESS.md`'s fuser-notify entries) — and this redesign removes the
  MCU stats tier entirely, see §6.
- **Satellite tier** (signal strength, connectivity timeline, freshness
  badge) — **dropped entirely** this session (not kept as a placeholder).
  Blocked on real satellite firmware regardless (only
  `tools/satellite_node_sim.py` exists).
