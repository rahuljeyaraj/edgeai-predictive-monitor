"use strict";
/*
 * Per-asset live charts for the expanded fleet row: anomaly-score timeline
 * (+ per-channel waterfall, sharing the timeline's x-axis so zoom/pan on one
 * moves the other) and a live current-spectrum group (no zoom). Loaded
 * before app.js (see index.html) but only *called* from event handlers that
 * run after every top-level script has executed, so the load order is safe.
 *
 * Buffers here are keyed by node_id in module-level objects, not attached to
 * any DOM node app.js's renderFleetList() rebuilds -- that rebuild replaces
 * #fleet-list's innerHTML on every 5s poll (and now on every WS registry
 * push too), which would otherwise wipe the waterfall/anomaly history and
 * any zoom the operator applied every single render. Two separate fixes for
 * that:
 *   - Data (waterfall/anomaly ring buffers, latest live-spectrum frame)
 *     accumulates in `nodes` below regardless of expand state, so
 *     collapsing and re-expanding a row never starts over.
 *   - The actual Plotly <div>s are created once via document.createElement
 *     and never appear in the HTML string renderFleetList() interpolates;
 *     attachExpanded() reparents the *same* element into whatever
 *     placeholder slot the latest render produced. Reparenting doesn't
 *     touch Plotly's internal state, so a user's zoom/pan survives a list
 *     rebuild -- only a real page reload resets it.
 *
 * Spectrum data (waterfall + live spectrum) only exists over /ws's
 * "spectrum" broadcast (mpu/main.py's on_frame) -- there is no REST
 * equivalent and nothing is persisted server-side for raw bins (dashboard
 * redesign spec S5.2: "stays client-side buffered"). The anomaly score
 * *is* durably stored too (mpu/history/store.py), so its timeline is
 * seeded once per node from GET /nodes/{id}/history on first expand, but
 * from then on it's kept live over /ws's "anomaly" broadcast (main.py's
 * on_score) -- the same per-frame push pattern as "spectrum", not the 5s
 * GET /nodes poll (that only sampled whatever the latest score happened
 * to be once per tick, decoupled from how often inference actually runs).
 */

const Charts = (() => {
  // Default visible width of the timeline/waterfall's rolling live window,
  // in seconds -- see buildTimelineFigure's explicit xaxis.range. Not a
  // hard limit on how much is buffered (that's *_MAX_COLS/*_MAX_POINTS
  // below); it's what's shown until the operator zooms/pans (uirevision
  // then preserves their view across the periodic redraw, same as any
  // Plotly streaming chart).
  const TIMELINE_WINDOW_S = 60;
  // Both buffers are sample-count caps, not time caps, and the anomaly
  // score and waterfall columns arrive at different, independent rates
  // (anomaly only on gated/scored frames -- pipeline/inference.py skips
  // stopped/transient ones -- waterfall on every raw frame regardless of
  // gate state). Sized for headroom above what TIMELINE_WINDOW_S needs at
  // satellite_node_sim.py's default 5Hz publish rate (60s * 5Hz = 300)
  // rather than tied to it exactly, so a faster real sensor or a bit of
  // pan-back beyond the live window still has buffered data to show.
  const WATERFALL_MAX_COLS = 600;
  const ANOMALY_MAX_POINTS = 600;
  const RENDER_THROTTLE_MS = 300;
  const WS_RECONNECT_MS = 2000;

  // Reuses the exact status palette already defined in style.css (:root)
  // rather than inventing a new one -- the redesign spec (S2) reserves
  // green/amber/red for this one meaning across the whole dashboard.
  const STATUS_COLOR = { healthy: "#10b981", warning: "#f59e0b", fault: "#ef4444" };
  const ANOMALY_LINE_COLOR = "#94a3b8"; // matches style.css's muted label gray

  // Sequential single-hue blue ramp (dataviz skill reference palette),
  // *reversed* relative to the skill's own listing: that ramp's lightest
  // step is meant to recede into a light chart surface, but this dashboard's
  // surface is dark (#0f172a), so the darkest/most-desaturated step is used
  // as the "near zero" floor and the brightest as the peak -- a proper glow
  // against a dark background instead of a washed-out one.
  const WATERFALL_COLORSCALE = [
    [0.000, "#0d366b"], [0.083, "#104281"], [0.167, "#184f95"],
    [0.250, "#1c5cab"], [0.333, "#256abf"], [0.417, "#2a78d6"],
    [0.500, "#3987e5"], [0.583, "#5598e7"], [0.667, "#6da7ec"],
    [0.750, "#86b6ef"], [0.833, "#9ec5f4"], [0.917, "#b7d3f6"],
    [1.000, "#cde2fb"],
  ];

  // Per-channel identity color for the live-spectrum group, fixed order.
  // Deliberately skips the reserved status hues (green/amber/red) and
  // yellow (too close to amber) -- picked from the dataviz skill's
  // pre-validated dark-mode categorical steps.
  const CHANNEL_COLORS = ["#3987e5", "#9085e9", "#d55181", "#d95926"];

  const PAPER_BG = "#0f172a";
  const PLOT_BG = "#0f172a";
  const GRID_COLOR = "#1e293b";
  const AXIS_COLOR = "#334155";
  const TEXT_COLOR = "#94a3b8";
  const FONT_FAMILY = "system-ui, -apple-system, 'Segoe UI', sans-serif";

  const TIMELINE_CONFIG = { displaylogo: false, responsive: true };
  const SPECTRUM_CONFIG = { displaylogo: false, responsive: true, displayModeBar: false };

  // node_id -> { channels, anomaly, anomalySeeded, waterfall, liveSpectrum,
  //              timelineEl, spectrumEl, timelineMounted, spectrumMounted }
  // Deliberately module-level, not tied to any per-render state -- see
  // file docstring.
  const nodes = {};

  let ws = null;
  let expandedIds = new Set();
  let registryHandler = null;
  let perfHandler = null;
  const dirty = new Set();

  function escapeAttr(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function pushCapped(arr, item, cap) {
    arr.push(item);
    if (arr.length > cap) arr.shift();
  }

  function hexToRgba(hex, alpha) {
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`;
  }

  function ensureNode(nodeId) {
    if (!nodes[nodeId]) {
      nodes[nodeId] = {
        channels: [],
        anomaly: [],
        anomalySeeded: false,
        waterfall: {},
        liveSpectrum: {},
        timelineEl: null,
        spectrumEl: null,
        timelineMounted: false,
        spectrumMounted: false,
        // Per-node thresholds, calibrated at commissioning from this
        // motor's own healthy baseline (pipeline/commissioning.py) --
        // null until commissioned. Set from registry data (onNodesPolled/
        // the "registry" WS push below), never from a single global preset:
        // a fixed threshold can't fit every motor's own error scale.
        warningThreshold: null,
        faultThreshold: null,
      };
    }
    return nodes[nodeId];
  }

  function purgeNode(nodeId) {
    const node = nodes[nodeId];
    if (!node) return;
    try { if (node.timelineEl) Plotly.purge(node.timelineEl); } catch (err) { /* already detached */ }
    try { if (node.spectrumEl) Plotly.purge(node.spectrumEl); } catch (err) { /* already detached */ }
    delete nodes[nodeId];
    dirty.delete(nodeId);
  }

  // ---------------------------------------------------------------------
  // WebSocket -- the only source of spectrum data (no REST equivalent).
  // ---------------------------------------------------------------------

  function connectWs() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${location.host}/ws`);
    ws.onmessage = (ev) => {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch (err) {
        return;
      }
      if (msg.type === "spectrum") {
        handleSpectrum(msg);
      } else if (msg.type === "anomaly") {
        handleAnomalyScore(msg);
      } else if (msg.type === "registry") {
        applyThresholds(msg.node_id, msg.entry);
        if (registryHandler) registryHandler(msg);
      } else if (msg.type === "removed" && registryHandler) {
        registryHandler(msg);
      } else if (msg.type === "perf_stats") {
        if (perfHandler) perfHandler(msg);
      }
    };
    ws.onclose = () => setTimeout(connectWs, WS_RECONNECT_MS);
    ws.onerror = () => ws.close();
  }

  function handleSpectrum(msg) {
    const node = ensureNode(msg.node_id);
    const channels = Object.keys(msg.channels || {});
    if (channels.length) node.channels = channels.slice().sort();
    for (const [channel, bins] of Object.entries(msg.channels || {})) {
      node.liveSpectrum[channel] = bins;
      if (!node.waterfall[channel]) node.waterfall[channel] = [];
      pushCapped(node.waterfall[channel], { t: msg.timestamp, bins }, WATERFALL_MAX_COLS);
    }
    dirty.add(msg.node_id);
  }

  // ---------------------------------------------------------------------
  // Anomaly score -- seeded once from history, then kept live over /ws's
  // "anomaly" broadcast (main.py's on_score, fired every gated/scored
  // frame), the same push-based pattern "spectrum" already uses. Not
  // sampled off the 5s /nodes poll any more -- that only reflected
  // whatever registry.last_anomaly_score happened to be once per poll
  // tick, which lagged (or flattened) the real per-frame inference rate.
  // ---------------------------------------------------------------------

  function handleAnomalyScore(msg) {
    const node = ensureNode(msg.node_id);
    pushCapped(node.anomaly, { t: msg.timestamp, score: msg.score, status: msg.status }, ANOMALY_MAX_POINTS);
    dirty.add(msg.node_id);
  }

  async function seedAnomalyHistory(nodeId, node) {
    try {
      const res = await fetch(`/nodes/${encodeURIComponent(nodeId)}/history`);
      if (!res.ok) return;
      const rows = await res.json();
      const historical = rows.map((r) => ({ t: r.timestamp, score: r.anomaly_score, status: r.status_at_time }));
      const merged = historical.concat(node.anomaly).sort((a, b) => a.t - b.t);
      node.anomaly = merged.slice(-ANOMALY_MAX_POINTS);
      dirty.add(nodeId);
    } catch (err) {
      console.error(`Failed to seed anomaly history for ${nodeId}`, err);
    }
  }

  // entry is registry.py's plain to_dict() (either GET /nodes' per-node
  // dict or a "registry" WS push's msg.entry) -- warning_threshold/
  // fault_threshold are only non-null once the node has been commissioned
  // (pipeline/commissioning.py calibrates them from that motor's own
  // healthy batch), so an uncommissioned node simply draws no lines yet.
  function applyThresholds(nodeId, entry) {
    const node = ensureNode(nodeId);
    node.warningThreshold = typeof entry.warning_threshold === "number" ? entry.warning_threshold : null;
    node.faultThreshold = typeof entry.fault_threshold === "number" ? entry.fault_threshold : null;
  }

  function onNodesPolled(nodesObj) {
    const seenIds = new Set(Object.keys(nodesObj));
    for (const [nodeId, entry] of Object.entries(nodesObj)) {
      if (Array.isArray(entry.sensor_config) && entry.sensor_config.length) {
        ensureNode(nodeId).channels = entry.sensor_config.slice().sort();
      }
      applyThresholds(nodeId, entry);
    }
    for (const nodeId of Object.keys(nodes)) {
      if (!seenIds.has(nodeId)) purgeNode(nodeId);
    }
  }

  // ---------------------------------------------------------------------
  // Plotly figure construction
  // ---------------------------------------------------------------------

  function smallFont() {
    return { family: FONT_FAMILY, color: TEXT_COLOR, size: 10 };
  }

  function axisBase(extra) {
    return {
      gridcolor: GRID_COLOR, zerolinecolor: AXIS_COLOR, linecolor: AXIS_COLOR,
      tickfont: smallFont(), automargin: true,
      ...extra,
    };
  }

  function darkLayoutBase() {
    return {
      paper_bgcolor: PAPER_BG, plot_bgcolor: PLOT_BG,
      font: { family: FONT_FAMILY, color: TEXT_COLOR, size: 11 },
      margin: { l: 52, r: 16, t: 8, b: 28 },
      showlegend: false,
    };
  }

  function hLine(value, color, yref) {
    return {
      type: "line", xref: "paper", x0: 0, x1: 1, yref, y0: value, y1: value,
      line: { color, width: 1.5, dash: "dash" },
    };
  }

  function transposeZ(rows, binCount) {
    const z = [];
    for (let b = 0; b < binCount; b++) {
      const row = new Array(rows.length);
      for (let c = 0; c < rows.length; c++) row[c] = rows[c].bins[b];
      z.push(row);
    }
    return z;
  }

  function rowDomain(rowIndex, rowCount, gap) {
    const rowHeight = (1 - gap * (rowCount - 1)) / rowCount;
    const top = 1 - rowIndex * (rowHeight + gap);
    return [top - rowHeight, top];
  }

  // The anomaly series and each waterfall channel arrive at different,
  // independent rates (see TIMELINE_WINDOW_S's comment), so leaving the
  // shared "x" axis on autorange makes it fit the *union* of both --
  // whichever trace's buffer spans more wall-clock time wins, squeezing
  // the other's real (narrower) span into a sliver of the plot. Passing
  // an explicit trailing [now - window, now] range instead makes both
  // traces share one real sliding window. uirevision (set on the layout
  // above) is what keeps this from fighting a user's manual zoom/pan: on
  // repeat Plotly.react calls with the same uirevision, Plotly only
  // applies this default range until the user actually interacts, after
  // which their chosen range sticks across further live updates -- the
  // standard Plotly streaming-chart pattern.
  function slidingWindowRange() {
    const nowMs = Date.now();
    return [new Date(nowMs - TIMELINE_WINDOW_S * 1000), new Date(nowMs)];
  }

  function buildTimelineFigure(nodeId, node) {
    const channels = node.channels;
    const rowCount = 1 + channels.length;
    const gap = 0.06;

    const times = node.anomaly.map((p) => new Date(p.t * 1000));
    const scores = node.anomaly.map((p) => p.score);
    const colors = node.anomaly.map((p) => STATUS_COLOR[p.status] || TEXT_COLOR);

    const traces = [{
      type: "scatter", mode: "lines+markers",
      x: times, y: scores,
      line: { shape: "spline", color: ANOMALY_LINE_COLOR, width: 2 },
      marker: { color: colors, size: 6, line: { width: 0 } },
      xaxis: "x", yaxis: "y",
      name: "Anomaly score", hovertemplate: "%{y:.4f}<extra></extra>",
    }];

    const shapes = [];
    if (typeof node.warningThreshold === "number") {
      shapes.push(hLine(node.warningThreshold, STATUS_COLOR.warning, "y"));
    }
    if (typeof node.faultThreshold === "number") {
      shapes.push(hLine(node.faultThreshold, STATUS_COLOR.fault, "y"));
    }

    const layout = {
      ...darkLayoutBase(),
      uirevision: nodeId,
      shapes,
      height: 150 + channels.length * 150,
      xaxis: axisBase({
        domain: [0, 1], anchor: "y", showticklabels: channels.length === 0,
        range: slidingWindowRange(),
      }),
      yaxis: axisBase({ domain: rowDomain(0, rowCount, gap), anchor: "x", title: { text: "Anomaly", font: smallFont() } }),
    };

    channels.forEach((channel, i) => {
      const rowIdx = i + 1;
      const n = rowIdx + 1; // xaxis2/yaxis2 for the first channel, etc.
      const rows = node.waterfall[channel] || [];
      const binCount = rows.length ? rows[0].bins.length : 0;

      traces.push({
        type: "heatmap",
        x: rows.map((r) => new Date(r.t * 1000)),
        y: Array.from({ length: binCount }, (_, k) => k),
        z: transposeZ(rows, binCount),
        colorscale: WATERFALL_COLORSCALE, showscale: i === 0,
        colorbar: i === 0 ? { thickness: 10, len: 0.5, y: 0.25, tickfont: smallFont() } : undefined,
        xaxis: `x${n}`, yaxis: `y${n}`, name: channel, hoverongaps: false,
      });

      layout[`xaxis${n}`] = axisBase({
        domain: [0, 1], anchor: `y${n}`, matches: "x",
        showticklabels: rowIdx === channels.length,
      });
      layout[`yaxis${n}`] = axisBase({
        domain: rowDomain(rowIdx, rowCount, gap), anchor: `x${n}`,
        title: { text: channel, font: smallFont() },
      });
    });

    return [traces, layout];
  }

  function buildSpectrumFigure(nodeId, node) {
    const channels = node.channels;
    const rowCount = Math.max(channels.length, 1);
    const gap = 0.08;

    const traces = [];
    const layout = { ...darkLayoutBase(), uirevision: nodeId, height: rowCount * 150 };

    channels.forEach((channel, i) => {
      const n = i === 0 ? "" : String(i + 1);
      const color = CHANNEL_COLORS[i % CHANNEL_COLORS.length];
      const bins = node.liveSpectrum[channel] || [];

      traces.push({
        type: "scatter", mode: "lines",
        x: bins.map((_, k) => k), y: bins,
        line: { shape: "spline", color, width: 1.5 },
        fill: "tozeroy", fillcolor: hexToRgba(color, 0.15),
        xaxis: `x${n}`, yaxis: `y${n}`, name: channel,
        hovertemplate: `${channel} bin %{x}: %{y:.3f}<extra></extra>`,
      });

      layout[`xaxis${n}`] = axisBase({
        domain: [0, 1], anchor: `y${n}`, fixedrange: true,
        showticklabels: i === channels.length - 1,
        title: i === channels.length - 1 ? { text: "Frequency bin", font: smallFont() } : undefined,
      });
      layout[`yaxis${n}`] = axisBase({
        domain: rowDomain(i, rowCount, gap), anchor: `x${n}`, fixedrange: true,
        title: { text: channel, font: smallFont() },
      });
    });

    return [traces, layout];
  }

  // ---------------------------------------------------------------------
  // Mount / attach / redraw
  // ---------------------------------------------------------------------

  function ensureHostElements(node) {
    if (!node.timelineEl) {
      node.timelineEl = document.createElement("div");
      node.timelineEl.className = "chart-plotly chart-plotly--timeline";
    }
    if (!node.spectrumEl) {
      node.spectrumEl = document.createElement("div");
      node.spectrumEl.className = "chart-plotly chart-plotly--spectrum";
    }
  }

  // Plotly reads the container's rendered size when it first draws --
  // called only after attachExpanded() has already appended the host
  // element into its slot, never before, or a first-ever mount measures a
  // detached (zero-size) div and draws blank until something forces a
  // resize.
  function mountIfNeeded(nodeId, node) {
    if (!node.anomalySeeded) {
      // Set before the fetch resolves, not after -- attachExpanded() can
      // run again (e.g. another poll tick) before this promise settles,
      // and a second seed fetch would just re-merge the same rows, but
      // there's no reason to pay for it twice.
      node.anomalySeeded = true;
      seedAnomalyHistory(nodeId, node);
    }
    if (!node.timelineMounted) {
      const [traces, layout] = buildTimelineFigure(nodeId, node);
      Plotly.newPlot(node.timelineEl, traces, layout, TIMELINE_CONFIG);
      node.timelineMounted = true;
    }
    if (!node.spectrumMounted) {
      const [traces, layout] = buildSpectrumFigure(nodeId, node);
      Plotly.newPlot(node.spectrumEl, traces, layout, SPECTRUM_CONFIG);
      node.spectrumMounted = true;
    }
  }

  function findSlot(role, nodeId) {
    const slots = document.querySelectorAll(`[data-role="${role}"]`);
    for (const el of slots) {
      if (el.dataset.nodeId === nodeId) return el;
    }
    return null;
  }

  function attachExpanded(expandedNodeIds) {
    expandedIds = expandedNodeIds;
    for (const nodeId of expandedNodeIds) {
      // Look the slots up *before* touching any buffers: a stale id can
      // linger in expandedNodeIds after its node was decommissioned (app.js
      // doesn't prune that Set on removal), in which case motorRowHtml()
      // never rendered a row for it at all this pass, so neither slot
      // exists. Without this guard, ensureNode()+mountIfNeeded() below
      // would recreate a brand-new (empty) NodeCharts and re-fetch its
      // history every single render forever, since purgeNode() already
      // deleted the real one out of `nodes`.
      const timelineSlot = findSlot("chart-slot-timeline", nodeId);
      const spectrumSlot = findSlot("chart-slot-spectrum", nodeId);
      if (!timelineSlot || !spectrumSlot) continue;

      const node = ensureNode(nodeId);
      ensureHostElements(node);
      if (node.timelineEl.parentElement !== timelineSlot) timelineSlot.appendChild(node.timelineEl);
      if (node.spectrumEl.parentElement !== spectrumSlot) spectrumSlot.appendChild(node.spectrumEl);
      mountIfNeeded(nodeId, node); // after appending -- see mountIfNeeded's comment
      dirty.add(nodeId); // redraw with whatever's been buffered since it was last mounted/visible
    }
  }

  function flush() {
    for (const nodeId of Array.from(dirty)) {
      dirty.delete(nodeId);
      if (!expandedIds.has(nodeId)) continue; // still buffering, just not drawing
      const node = nodes[nodeId];
      if (!node || !node.timelineMounted || !node.spectrumMounted) continue;

      const [tTraces, tLayout] = buildTimelineFigure(nodeId, node);
      Plotly.react(node.timelineEl, tTraces, tLayout, TIMELINE_CONFIG);

      const [sTraces, sLayout] = buildSpectrumFigure(nodeId, node);
      Plotly.react(node.spectrumEl, sTraces, sLayout, SPECTRUM_CONFIG);
    }
  }

  function chartSlotsHtml(nodeId) {
    const safeId = escapeAttr(nodeId);
    return `<div class="motor-row__charts">
      <div class="chart-slot chart-slot--timeline" data-role="chart-slot-timeline" data-node-id="${safeId}"></div>
      <div class="chart-slot chart-slot--spectrum" data-role="chart-slot-spectrum" data-node-id="${safeId}"></div>
    </div>`;
  }

  // ---------------------------------------------------------------------
  // Startup
  // ---------------------------------------------------------------------

  async function init(onRegistryPush, onPerfStats) {
    registryHandler = onRegistryPush || null;
    perfHandler = onPerfStats || null;
    connectWs();
    setInterval(flush, RENDER_THROTTLE_MS);
  }

  return { init, onNodesPolled, chartSlotsHtml, attachExpanded };
})();

window.Charts = Charts;
