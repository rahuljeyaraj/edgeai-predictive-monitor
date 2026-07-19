"use strict";
/*
 * Dev/perf page (docs/DEV_PERF_PAGE_PLAN.md) -- fills in the "Performance"
 * tab. Loaded after charts.js (see index.html), which owns the one shared
 * WebSocket and forwards this page's "perf_stats" messages here via
 * Charts.init's second callback (app.js).
 *
 * Two tiers, both always-visible live time-plots -- no meters, no static
 * cells/heat-strips, no nested "Advanced" disclosure:
 *   Tier 1 (QRB2210) -- is our own compute hardware under strain? One live
 *     chart per CPU core (this pipeline is single-threaded, so an averaged
 *     "CPU%" could hide one maxed-out core), memory %, GPU % (or an empty
 *     state when the GPU bridge isn't provisioned), temperature if the
 *     board exposes a thermal zone (dropped silently otherwise -- never a
 *     fake reading, same rule as the GPU empty state).
 *   Tier 2 (Pipelines) -- is each pipeline keeping up, and how much
 *     headroom is left? One row per live pipeline (payload.pipelines,
 *     keyed by node_id): frames arrived/sec, and pipeline time-budget
 *     used % (avg_latency_ms / (1000/frames_per_sec) * 100 -- the honest
 *     per-node headroom signal; no fabricated "N more satellites" estimate
 *     on top of it).
 *
 * No MCU/fuser tier (removed, not reshaped -- Tier 2 gets pipeline
 * throughput from Python-side PipelineStats, not MCU Bridge calls) and no
 * Satellites tier (dropped entirely, not a placeholder -- re-add only once
 * real satellite firmware exists).
 *
 * Only the tier *bodies* re-render on each message -- the outer <details>
 * tier wrappers in index.html live untouched, so a collapse/expand the
 * operator did survives every live update, the same problem app.js's
 * renderFleetList solves for Plotly zoom/pan by never recreating the
 * chart <div>s (charts.js's file docstring).
 */

const Perf = (() => {
  // 60 samples @ ~1 perf_stats push/sec (api/app.py's _PERF_BROADCAST_INTERVAL_S)
  // = a 60-second rolling window, matching Windows/macOS Task Manager's default.
  const HISTORY_MAX_SAMPLES = 60;

  // Fixed hue per utilization chart -- dataviz skill's "trend over time,
  // sequential" form: each card is its own small multiple (never overlaid
  // on a shared axis), so a distinct identity color per card reads fine
  // without a legend. Picked to stay clear of the status colors
  // (green/amber/red/cyan) used elsewhere on the dashboard.
  const CPU_COLOR = "#3987e5";
  const MEM_COLOR = "#9085e9";
  const GPU_COLOR = "#d95926";
  const TEMP_COLOR = "#ec4899";
  const FPS_COLOR = "#14b8a6";
  const BUDGET_COLOR = "#6366f1";

  const GRID_COLOR = "#334155";

  const history = {
    cpuCores: [], // array of per-core sample arrays, index = core number
    mem: [],
    gpu: [],
    temp: [],
    pipelines: {}, // node_id -> { fps: [], budget: [] }
  };

  function pushCapped(arr, value) {
    arr.push(value);
    if (arr.length > HISTORY_MAX_SAMPLES) arr.shift();
  }

  // ---------------------------------------------------------------------
  // Live area chart -- dataviz skill's marks-and-anatomy.md: 2px line,
  // ~10% opacity area fill, hairline recessive gridlines, one hue.
  //
  // autoScale=false (default) plots against a fixed 0-100 domain -- right
  // for percentages (CPU/memory/GPU/budget-used), where the fixed ceiling
  // itself is meaningful ("climbing toward 100%"). autoScale=true instead
  // scales to the data's own min/max with a little padding -- right for
  // absolute-unit metrics (temperature in °C, frames/sec) whose natural
  // range would otherwise render as a flat line pinned near the bottom of
  // a 0-100 axis.
  // ---------------------------------------------------------------------

  function areaChartSvg(values, { width = 260, height = 64, color, autoScale = false } = {}) {
    if (values.length < 2) {
      return `<svg class="perf-chart__svg" viewBox="0 0 ${width} ${height}"></svg>`;
    }
    let lo = 0, hi = 100;
    if (autoScale) {
      const dataMin = Math.min(...values);
      const dataMax = Math.max(...values);
      const pad = (dataMax - dataMin) * 0.1 || Math.max(1, Math.abs(dataMax) * 0.1) || 1;
      lo = dataMin - pad;
      hi = dataMax + pad;
    }
    const span = hi - lo || 1;
    const stepX = width / (HISTORY_MAX_SAMPLES - 1);
    const startX = width - (values.length - 1) * stepX;
    const points = values.map((v, i) => {
      const clamped = Math.max(lo, Math.min(hi, v));
      const frac = (clamped - lo) / span;
      return [startX + i * stepX, height - frac * height];
    });
    const linePath = points
      .map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`)
      .join(" ");
    const lastX = points[points.length - 1][0].toFixed(1);
    const firstX = points[0][0].toFixed(1);
    const areaPath = `${linePath} L${lastX},${height} L${firstX},${height} Z`;
    const gridLines = [0.25, 0.5, 0.75]
      .map((f) => {
        const y = (height * f).toFixed(1);
        return `<line x1="0" y1="${y}" x2="${width}" y2="${y}" stroke="${GRID_COLOR}" stroke-width="1" />`;
      })
      .join("");
    return `<svg class="perf-chart__svg" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">
      ${gridLines}
      <path d="${areaPath}" fill="${color}" fill-opacity="0.1" stroke="none" />
      <path d="${linePath}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" />
    </svg>`;
  }

  function chartCard({ label, values, color, valueText, caption = "", autoScale = false }) {
    return `<div class="perf-card perf-chart">
      <div class="perf-chart__top">
        <span class="perf-chart__label">${label}</span>
        <span class="perf-chart__value">${valueText}</span>
      </div>
      ${areaChartSvg(values, { color, autoScale })}
      ${caption ? `<div class="perf-chart__caption">${caption}</div>` : ""}
    </div>`;
  }

  function emptyCard(label, message) {
    return `<div class="perf-card perf-chart">
      <div class="perf-chart__top"><span class="perf-chart__label">${label}</span></div>
      <div class="perf-empty">${message}</div>
    </div>`;
  }

  // ---------------------------------------------------------------------
  // Tier 1 -- QRB2210 (the base station's own compute hardware). Answers
  // "is our own box under strain." One chart per CPU core, memory %,
  // GPU % (or its provisioning empty state), temperature if available.
  // ---------------------------------------------------------------------

  function renderTier1(payload) {
    const el = document.getElementById("perf-mpu-charts");
    if (!el) return;
    const system = payload.system;
    const gpu = payload.gpu || { available: false, busy_percent: null };

    if (!system) {
      el.innerHTML = `<div class="perf-empty">Performance monitoring is disabled (POST /perf/enable to turn it back on).</div>`;
      return;
    }

    const cards = (system.cpu_percent_per_core || []).map((pct, i) => chartCard({
      label: `CPU core ${i}`, values: history.cpuCores[i] || [], color: CPU_COLOR,
      valueText: `${pct.toFixed(0)}%`,
    }));

    const ramPct = system.system_memory_total_mb > 0
      ? (system.system_memory_used_mb / system.system_memory_total_mb) * 100 : null;
    if (ramPct !== null) {
      cards.push(chartCard({
        label: "Memory", values: history.mem, color: MEM_COLOR,
        valueText: `${ramPct.toFixed(0)}%`,
        caption: `${system.system_memory_used_mb.toFixed(0)} / ${system.system_memory_total_mb.toFixed(0)} MB`,
      }));
    }

    cards.push(gpu.available
      ? chartCard({ label: "GPU", values: history.gpu, color: GPU_COLOR, valueText: `${gpu.busy_percent.toFixed(0)}%` })
      : emptyCard("GPU", "Not provisioned on this board."));

    if (system.cpu_temp_celsius !== null && system.cpu_temp_celsius !== undefined) {
      cards.push(chartCard({
        label: "Temperature", values: history.temp, color: TEMP_COLOR,
        valueText: `${system.cpu_temp_celsius.toFixed(1)}°C`, autoScale: true,
      }));
    }

    el.innerHTML = cards.join("");
  }

  // ---------------------------------------------------------------------
  // Tier 2 -- Pipelines. Answers "is each pipeline keeping up, and how
  // much headroom is left." One row per live pipeline, keyed by node_id.
  // ---------------------------------------------------------------------

  function budgetUsedPercent(p) {
    if (!p.frames_per_sec || p.frames_per_sec <= 0) return 0;
    const intervalMs = 1000 / p.frames_per_sec;
    return (p.avg_latency_ms / intervalMs) * 100;
  }

  function renderTier2(payload) {
    const el = document.getElementById("perf-pipelines");
    if (!el) return;
    const pipelines = payload.pipelines || {};
    const nodeIds = Object.keys(pipelines);

    if (nodeIds.length === 0) {
      el.innerHTML = `<div class="perf-empty">No pipelines running yet.</div>`;
      return;
    }

    el.innerHTML = nodeIds.map((nodeId) => {
      const p = pipelines[nodeId];
      const h = history.pipelines[nodeId] || { fps: [], budget: [] };
      return `<div class="perf-pipeline">
        <div class="perf-pipeline__title">${nodeId}</div>
        <div class="perf-charts">
          ${chartCard({
            label: "Frames arrived/sec", values: h.fps, color: FPS_COLOR,
            valueText: `${p.frames_per_sec.toFixed(2)} fps`, autoScale: true,
          })}
          ${chartCard({
            label: "Pipeline time-budget used", values: h.budget, color: BUDGET_COLOR,
            valueText: `${budgetUsedPercent(p).toFixed(0)}%`,
          })}
        </div>
      </div>`;
    }).join("");
  }

  // ---------------------------------------------------------------------
  // Entry points
  // ---------------------------------------------------------------------

  function render(payload) {
    const system = payload.system;
    const gpu = payload.gpu;
    if (system) {
      (system.cpu_percent_per_core || []).forEach((pct, i) => {
        if (!history.cpuCores[i]) history.cpuCores[i] = [];
        pushCapped(history.cpuCores[i], pct);
      });
      if (system.system_memory_total_mb > 0) {
        pushCapped(history.mem, (system.system_memory_used_mb / system.system_memory_total_mb) * 100);
      }
      if (system.cpu_temp_celsius !== null && system.cpu_temp_celsius !== undefined) {
        pushCapped(history.temp, system.cpu_temp_celsius);
      }
    }
    if (gpu && gpu.available) pushCapped(history.gpu, gpu.busy_percent);

    const pipelines = payload.pipelines || {};
    Object.keys(pipelines).forEach((nodeId) => {
      const p = pipelines[nodeId];
      if (!history.pipelines[nodeId]) history.pipelines[nodeId] = { fps: [], budget: [] };
      pushCapped(history.pipelines[nodeId].fps, p.frames_per_sec);
      pushCapped(history.pipelines[nodeId].budget, budgetUsedPercent(p));
    });

    renderTier1(payload);
    renderTier2(payload);
  }

  function handleMessage(msg) {
    // msg is {"type": "perf_stats", ...rest of GET /perf's shape}.
    render(msg);
  }

  async function init() {
    try {
      const res = await fetch("/perf");
      render(await res.json());
    } catch (err) {
      console.error("Failed to fetch initial /perf snapshot", err);
    }
  }

  return { init, handleMessage };
})();

window.Perf = Perf;
