# Plan — Dev/perf page

Status: **Brainstorm/design complete 2026-07-19. Implementation not started.**
This doc captures the outcome of a design discussion for the "Dev/perf page" item in
[DASHBOARD_IDEAS_BACKLOG.md](DASHBOARD_IDEAS_BACKLOG.md) — CPU/RAM/GPU, live sampling
rate, dropped-frame count, a judge-facing "no data lost" highlight. Nothing here has
been built yet; this is the design to build against next.

---

## 0. Why this exists

The dashboard has no visibility into its own health/performance today. Judges see
sensor data and fault status, but nothing about whether the pipeline itself is keeping
up — sampling rate, dropped frames, resource headroom. This page is meant to both be a
real dev diagnostic and a judge-facing "no data lost" showcase of the UNO Q's
capability (MPU tier is shown first, deliberately, for this reason).

## 1. Layout

Single scrolling page (matches the existing no-router vanilla-JS frontend —
`base-station/python/frontend/`, no SPA framework). Top to bottom:

1. **Hero band** — judge-facing, shown first regardless of tier order below it.
2. **MPU tier** — first tier, showcases the UNO Q's own capability.
3. **MCU/fuser tier**
4. **Satellite tier**

Each tier below the hero band is a collapsible section (no real tab/router support in
this frontend, so collapse/expand is the right primitive, not a route).

## 2. Hero band

- **"No data lost" %**: `(frames_sent - overrun - dropped) / frames_expected`, one big
  live number/gauge. This is the single judge-facing headline metric.
- Fleet-wide traffic-light strip: MPU / MCU / each satellite, one glance, green/amber/
  red.

## 3. MPU tier (shown first)

Data already flows — this tier is a frontend-only build, no new backend work for
CPU/RAM.

- **CPU / RAM**: already computed by `base-station/python/monitoring/perf.py`
  (`PerformanceMonitor`: process CPU%, per-core CPU%, process RSS, system memory,
  per-pipeline latency/fps/"falling behind", `ingest_fps_by_transport`), already
  exposed at `GET /perf`. Render as small trend sparklines, not gauges — these are
  time-series, not point-in-time values. Per-core as a small heat strip.
- **fps-by-transport**: small multiples, one sparkline per transport.
- **GPU**: showcase real Adreno GPU usage (decided 2026-07-19 — GPU must be used, not
  skipped). Two independent tracks feed this tile; see §5.

## 4. MCU/fuser tier

New plumbing required. **MCU pushes, MPU does not poll** — decided explicitly to avoid
adding a polling loop where a push already has a proven pattern in this codebase:
`Bridge.notify(event, payload)` on the MCU side already lands as a
`Bridge.provide(event, handler)` callback on the Python side (this is exactly how
full-spectrum data was pushed pre-SPI-migration, see `docs/PROGRESS.md:436`). For
perf scalars at ~1 Hz this is a clean fit — far lower volume than the spectrum data
that outgrew it originally.

- MCU periodically calls `Bridge.notify("perf_stats", {...})` with:
  - `bench.cpp`'s existing `get_bench_stats` fields: `mic_fps`, `mic_win`, `mic_to`,
    `acc_fps`, `acc_win`, `acc_isr`, `acc_ff`, `acc_to`, `fus_fps`, `fus_frm`,
    `fus_ovr`, `fus_avg`/`fus_max`.
  - `spi_link.cpp`'s existing `get_spi_link_stats` fields: staged/armed/completed/
    timeout/error counts.
- Python side: register the matching `Bridge.provide("perf_stats", handler)`, cache
  `{values, received_at}`.
- **Frontend UX rule** (applies to this tier and the satellite tier below): show `--`
  until the first message ever arrives, then hold and live-update the last received
  value. No polling, no "waiting" spinner — either it has never arrived (`--`) or it
  has and is live.

## 5. GPU — two separate tracks, do not conflate

Raised and resolved during brainstorm: chart-rendering GPU use and on-device inference
GPU use are unrelated and answer different needs. Both are wanted; neither blocks the
other.

### 5a. Chart rendering (fixes the separate "Chart clutter" backlog item)

- Swap `type: "scatter"` → `"scattergl"` and `type: "heatmap"` → `"heatmapgl"` in
  `base-station/python/frontend/charts.js` (`buildSpectrumFigure` /
  `buildTimelineFigure`). Moves per-redraw repaint cost off the browser's main JS
  thread onto the GPU, so more channels/nodes can stay expanded at once without
  jank — the actual mechanism behind "Chart clutter."
- **Prerequisite**: the vendored bundle (`plotly-cartesian.min.js`) is SVG-only; needs
  swapping for a bundle that includes gl2d trace support.
- **Robustness note**: this GPU is the *viewer's* device, not the UNO Q's — WebGL
  falls back to a software rasterizer on most browsers (so it still renders, just
  without the benefit) but can fail outright in locked-down/headless environments.
  Since this renders in front of judges on unknown hardware, feature-detect WebGL
  context creation and fall back to plain `scatter`/`heatmap` traces rather than
  assuming success.
- This work does **not** produce any MPU-side GPU metric — it's client-side and
  belongs to the Chart-clutter backlog item, not this page's GPU tile.

### 5b. On-device GPU inference (feeds the MPU tier's GPU metric, §3)

- Decided 2026-07-19: pursue this so the MPU tier's GPU number is real, not an idle
  placeholder — also chosen for the portability story (train once, deploy anywhere).
- Plan: export the live PyTorch autoencoder (`base-station/python/pipeline/
  autoencoder.py`) to **ONNX** (`torch.onnx.export`), then run it via **ONNX
  Runtime's QNN Execution Provider** targeting the QRB2210's Adreno GPU backend.
- **Unverified — separate research track, tracked outside this doc**: whether the
  QRB2210's specific Adreno GPU version is actually supported by QNN's GPU backend.
  Neither the current PyTorch autoencoder pipeline nor the (still unbuilt) EI TFLite
  classifier plan (`docs/EDGE_IMPULSE_FAULT_CLASSIFICATION_PLAN.md`, locked to
  CPU-only TFLite) currently touch the GPU at all — this would be new capability, not
  a redirect of existing work.
- Until this lands, the MPU tier's GPU tile reads idle/near-zero honestly rather than
  faking a number.

## 6. Satellite tier

Same push-based philosophy as §4 (MQTT publish, not polled), and the same `--`-until-
first-arrival UX rule. **Blocked on real satellite firmware existing at all** — only
`base-station/python/tools/satellite_node_sim.py` exists today, with no health fields.

- Signal strength (RSSI) per node.
- Connectivity timeline: green/red stripe per satellite over the last N minutes,
  driven by MQTT LWT (fires on ungraceful disconnect) + heartbeat age — not polling.
- Last-seen freshness badge.
- Its own fps/dropped-frame stats, mirroring the MCU's `bench.cpp` pattern, once real
  firmware can report them.

## 7. Explicitly out of scope / accepted as-is

- **Waterfall/spectrum history storage**: raw spectrum bins pass through MPU RAM only
  transiently (`base-station/python/main.py`'s `on_frame` broadcasts straight to the
  WebSocket, no ring buffer, no persistence) and are buffered only client-side
  (`charts.js`'s `node.waterfall[channel]`, capped at `WATERFALL_MAX_COLS`). This
  resets on page refresh. Discussed and explicitly accepted as-is — no storage change
  planned. (Contrast with anomaly score, which *is* durably stored in
  `mpu/history/store.py` and reseeded via `GET /nodes/{id}/history` — that asymmetry
  is intentional per the existing dashboard redesign spec, S5.2.)

## 8. Next steps

- [ ] Research: QNN Execution Provider GPU-backend support for QRB2210's Adreno GPU
      (§5b) — separate task, not blocking the rest of this plan.
- [ ] Build: MCU firmware — wire `get_bench_stats`/`get_spi_link_stats` into a
      periodic `Bridge.notify("perf_stats", ...)` push.
- [ ] Build: Python side — `Bridge.provide("perf_stats", ...)` handler + cache +
      REST/WS exposure to the frontend.
- [ ] Build: frontend Dev/perf page — hero band, MPU tier (CPU/RAM/GPU), MCU tier.
- [ ] Blocked: satellite tier — needs real (non-simulated) satellite firmware first.
- [ ] Separate backlog item, not this one: Chart-clutter GPU rendering swap (§5a).
