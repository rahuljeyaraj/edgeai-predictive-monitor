"use strict";
/*
 * Dev/perf page (docs/DEV_PERF_PAGE_PLAN.md) -- fills in the "Performance"
 * tab. Loaded after charts.js (see index.html), which owns the one shared
 * WebSocket and forwards this page's "perf_stats" messages here via
 * Charts.init's second callback (app.js).
 *
 * Demo-facing redesign: this page shows only what someone can understand
 * with a single glance and no explanation, per direct user feedback --
 * live Task-Manager-style area charts for the handful of figures that
 * actually fluctuate (CPU/memory/GPU utilization), a plain count for
 * pipelines (a flat number isn't a trend worth animating), per-core CPU
 * always visible (never behind a second-level "Advanced" disclosure),
 * and static hardware spec cards for facts that never change at runtime
 * (sensor sampling rate). No raw firmware counters, no unexplained
 * register debug -- if a field can't be understood at a glance, it isn't
 * on this page.
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
  // without a legend. Reuses this dashboard's existing accent blue for CPU;
  // violet/orange are the same validated dark-mode categorical set, picked
  // to stay clear of the status colors (green/amber/red/cyan) used elsewhere.
  const CPU_COLOR = "#3987e5";
  const MEM_COLOR = "#9085e9";
  const GPU_COLOR = "#d95926";
  const GRID_COLOR = "#334155";

  const history = { cpu: [], mem: [], gpu: [] };

  function pushCapped(arr, value) {
    arr.push(value);
    if (arr.length > HISTORY_MAX_SAMPLES) arr.shift();
  }

  // ---------------------------------------------------------------------
  // Live area chart -- dataviz skill's marks-and-anatomy.md: 2px line,
  // ~10% opacity area fill, hairline recessive gridlines, one hue.
  // ---------------------------------------------------------------------

  function areaChartSvg(values, { width = 260, height = 64, color } = {}) {
    if (values.length < 2) {
      return `<svg class="perf-chart__svg" viewBox="0 0 ${width} ${height}"></svg>`;
    }
    const stepX = width / (HISTORY_MAX_SAMPLES - 1);
    const startX = width - (values.length - 1) * stepX;
    const points = values.map((v, i) => {
      const pct = Math.max(0, Math.min(100, v));
      return [startX + i * stepX, height - (pct / 100) * height];
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

  function chartCard({ label, values, color, valueText, caption = "" }) {
    return `<div class="perf-card perf-chart">
      <div class="perf-chart__top">
        <span class="perf-chart__label">${label}</span>
        <span class="perf-chart__value">${valueText}</span>
      </div>
      ${areaChartSvg(values, { color })}
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
  // QRB2210 (MPU) tier -- CPU / memory / GPU utilization, per-core CPU,
  // pipeline count. Nothing here needs a raw counter explained.
  // ---------------------------------------------------------------------

  function renderMpuTier(payload) {
    const chartsEl = document.getElementById("perf-mpu-charts");
    const coresEl = document.getElementById("perf-mpu-cores");
    const statsEl = document.getElementById("perf-mpu-stats");
    if (!chartsEl || !coresEl || !statsEl) return;
    const system = payload.system;
    const gpu = payload.gpu || { available: false, busy_percent: null };

    if (!system) {
      chartsEl.innerHTML = `<div class="perf-empty">Performance monitoring is disabled (POST /perf/enable to turn it back on).</div>`;
      coresEl.innerHTML = "";
      statsEl.innerHTML = "";
      return;
    }

    const ramPct = system.system_memory_total_mb > 0
      ? (system.system_memory_used_mb / system.system_memory_total_mb) * 100 : null;

    chartsEl.innerHTML = [
      chartCard({
        label: "CPU", values: history.cpu, color: CPU_COLOR,
        valueText: `${system.system_cpu_percent.toFixed(0)}%`,
      }),
      ramPct === null ? "" : chartCard({
        label: "Memory", values: history.mem, color: MEM_COLOR,
        valueText: `${ramPct.toFixed(0)}%`,
        caption: `${system.system_memory_used_mb.toFixed(0)} / ${system.system_memory_total_mb.toFixed(0)} MB`,
      }),
      gpu.available
        ? chartCard({ label: "GPU", values: history.gpu, color: GPU_COLOR, valueText: `${gpu.busy_percent.toFixed(0)}%` })
        : emptyCard("GPU", "Not provisioned on this board."),
    ].join("");

    const coreCells = (system.cpu_percent_per_core || []).map((pct, i) => {
      const clamped = Math.max(0, Math.min(100, pct));
      return `<div class="heat-cell" title="Core ${i}: ${pct.toFixed(0)}%"
        style="background:color-mix(in srgb, ${CPU_COLOR} ${clamped.toFixed(0)}%, #1e293b)">${pct.toFixed(0)}</div>`;
    }).join("");
    coresEl.innerHTML = `<div class="perf-cores__label">CPU per core</div>
      <div class="heat-strip">${coreCells || '<span class="perf-empty">no core data</span>'}</div>`;

    statsEl.innerHTML = `<div class="perf-card perf-stat">
      <span class="perf-stat__label">Pipelines running</span>
      <span class="perf-stat__value">${system.pipeline_count}</span>
    </div>`;
  }

  // ---------------------------------------------------------------------
  // STM32U585 (MCU / sensor fusion) tier -- sensor sampling rate is a
  // fixed hardware fact (not something that changes tick to tick), so it
  // renders as a static spec card, never a live chart.
  // ---------------------------------------------------------------------

  const SENSOR_LABELS = { mic: "Microphone", accel: "Accelerometer" };

  function renderMcuTier(payload) {
    const el = document.getElementById("perf-mcu-sensors");
    if (!el) return;
    const sensors = payload.sensors || {};
    const names = Object.keys(sensors);

    if (names.length === 0) {
      el.innerHTML = `<div class="perf-empty">-- no data yet from the MCU</div>`;
      return;
    }

    el.innerHTML = names.map((name) => {
      const s = sensors[name];
      const label = SENSOR_LABELS[name] || name;
      const rate = Math.round(s.fs_hz).toLocaleString();
      const maxHz = s.max_detected_hz === null ? null : Math.round(s.max_detected_hz).toLocaleString();
      return `<div class="perf-card">
        <div class="perf-sensor__name">${label}</div>
        <div class="perf-sensor__rate">${rate} <span class="perf-sensor__rate-unit">Hz sampling</span></div>
        ${maxHz === null ? "" : `<div class="perf-sensor__detail">Detects vibration up to ${maxHz} Hz</div>`}
      </div>`;
    }).join("");
  }

  function renderSatelliteTier() {
    const el = document.getElementById("perf-satellite-body");
    if (!el || el.dataset.rendered) return; // static -- never changes until real satellite firmware exists
    el.dataset.rendered = "1";
    el.innerHTML = `<div class="perf-empty">No satellites connected yet -- blocked on real satellite firmware
      (only the dev simulator exists today). Will show signal strength and frame rate per node.
      See docs/DEV_PERF_PAGE_PLAN.md S6.</div>`;
  }

  // ---------------------------------------------------------------------
  // Entry points
  // ---------------------------------------------------------------------

  function render(payload) {
    const system = payload.system;
    const gpu = payload.gpu;
    if (system) {
      pushCapped(history.cpu, system.system_cpu_percent);
      if (system.system_memory_total_mb > 0) {
        pushCapped(history.mem, (system.system_memory_used_mb / system.system_memory_total_mb) * 100);
      }
    }
    if (gpu && gpu.available) pushCapped(history.gpu, gpu.busy_percent);

    renderMpuTier(payload);
    renderMcuTier(payload);
    renderSatelliteTier();
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
