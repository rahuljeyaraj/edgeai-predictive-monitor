"use strict";
/*
 * Per-asset live charts for the expanded fleet row (docs/CHART_CLUTTER_PLAN.md
 * §1): anomaly score chart, accel/mic spectrum charts, and two collapsible
 * panels (scalar trends, waterfall). Loaded before app.js (see index.html)
 * but only *called* from event handlers that run after every top-level
 * script has executed, so the load order is safe.
 *
 * "Raw signals" (the decimated time-domain traces) was removed on
 * 2026-08-01: the firmware no longer streams time-domain windows at all
 * (sketch/fuser.cpp), so it had no data source left.
 *
 * "Scalar values" was briefly removed in that same pass and then rebuilt as
 * a GRID OF TREND PLOTS rather than the 24 bare numeric tiles it used to be
 * -- a single instantaneous number doesn't tell an operator whether a
 * statistic is drifting, and drift is the whole reason these are in the
 * feature vector. See SCALAR_STATS for why the grid groups by statistic
 * rather than by channel.
 *
 * Buffers here are keyed by node_id in module-level objects, not attached to
 * any DOM node app.js's renderFleetList() rebuilds -- that rebuild replaces
 * #fleet-list's innerHTML on every 5s poll (and now on every WS registry
 * push too), which would otherwise wipe every buffer/mount-state and any
 * zoom the operator applied every single render. Two separate fixes for
 * that:
 *   - Data (waterfall/anomaly ring buffers, latest spectra) accumulates in
 *     `nodes` below regardless of expand state, so collapsing and
 *     re-expanding a row -- or a native <details> panel -- never starts
 *     over.
 *   - The actual host <div>s (Plotly or plain innerHTML content) are
 *     created once via document.createElement and never appear in the HTML
 *     string detailBodyHtml() interpolates; attachExpanded() reparents the
 *     *same* elements into whatever placeholder slot the latest render
 *     produced. Reparenting doesn't touch Plotly's internal state, so a
 *     user's zoom/pan survives a list rebuild -- only a real page reload
 *     resets it.
 *
 * Spectrum data only exists over /ws's "spectrum" broadcast (main.py's
 * on_frame) -- there is no REST equivalent and nothing is persisted
 * server-side for it (client-side buffered only). The anomaly score *is*
 * durably stored too (history/store.py), so its trend chart is seeded once
 * per node from GET /nodes/{id}/history on first expand, but from then on
 * it's kept live over /ws's "anomaly" broadcast (main.py's on_score) -- the
 * same per-frame push pattern as "spectrum".
 *
 * Both collapsibles are native <details>, collapsed by default, and both
 * are Plotly-backed, so NEITHER is mounted until first expanded
 * (docs/CHART_CLUTTER_PLAN.md §1.5's "not rendered/computed until
 * expanded") -- see mountScalarIfNeeded/mountWaterfallIfNeeded and their
 * gating on the open-Sets app.js passes into attachExpanded(), not on
 * whether the slot element exists (a collapsed <details>'s children are
 * still present in the DOM, just not rendered -- mounting Plotly into a
 * hidden/zero-size div is the same failure mode already called out above
 * for the row-collapse case, just triggered by <details> collapse
 * instead). Note the scalar panel DID skip this gating in its old
 * numeric-tile form, when it was plain innerHTML with no layout to
 * measure; as charts it can't.
 */

const Charts = (() => {
  const WATERFALL_MAX_COLS = 600;
  // Anomaly score buffer: retention is time-based (ANOMALY_RETENTION_SECONDS),
  // not point-count-based, so scrollback depth doesn't depend on frame rate.
  // ANOMALY_MAX_POINTS is just a memory backstop for an abnormally high rate.
  const ANOMALY_WINDOW_SECONDS = 120; // default live-tail width shown on the chart
  const ANOMALY_RETENTION_SECONDS = 30 * 60; // how far back the rangeslider can scrub
  const ANOMALY_LIVE_SNAP_TOLERANCE_SECONDS = 3; // dragging the slider back within this of "now" resumes live-follow
  const ANOMALY_MAX_POINTS = 20000;
  // Cap on points actually handed to Plotly per redraw, independent of how
  // deep the buffer above is -- see anomalyRenderPoints for the measurements
  // that set it. ~1200 is where a 1500px-wide chart stops resolving extra
  // points anyway (under two per pixel across the rangeslider's full width),
  // and it holds the redraw near the ~38ms the chart cost when the buffer was
  // still short, for any session length.
  const ANOMALY_MAX_RENDERED_POINTS = 1200;
  const RENDER_THROTTLE_MS = 300;
  const WS_RECONNECT_MS = 2000;

  // The four SensorChannel values that exist today (registry/registry.py) --
  // fixed order matches the doc's own layout order (accel axes before mic).
  // Drives waterfall (one heatmap per present channel) and the
  // present/absent section gating in detailBodyHtml().
  const ALL_CHANNELS = ["accel_x", "accel_y", "accel_z", "mic"];

  // entry.status values with no anomaly model yet -- either never
  // commissioned, or a recommission overwriting the old one is in flight
  // (registry.py's start_commissioning() transitions HEALTHY/WARNING/FAULT
  // straight back into COMMISSIONING_COLLECTING). The Anomaly score section
  // is hidden for all three: an uncommissioned node has nothing to plot, and
  // showing the pre-recommission chart while a fresh baseline is being
  // collected would just be stale data masquerading as current.
  const UNCOMMISSIONED_STATUSES = new Set([
    "uncommissioned", "commissioning_collecting", "commissioning_training",
  ]);

  // Reuses the exact status palette already defined in style.css (:root)
  // rather than inventing a new one -- the redesign spec (S2) reserves
  // green/amber/red for this one meaning across the whole dashboard.
  const STATUS_COLOR = { healthy: "#00ff00", warning: "#f59e0b", fault: "#ff0000" };
  const ANOMALY_LINE_COLOR = "#94a3b8"; // matches style.css's muted label gray

  // Sequential single-hue blue ramp (dataviz skill reference palette),
  // *reversed* relative to the skill's own listing: that ramp's lightest
  // step is meant to recede into a light chart surface, but this dashboard's
  // surface is dark (#0f172a), so the darkest/most-desaturated step is used
  // as the "near zero" floor and the brightest as the peak -- a proper glow
  // against a dark background instead of a washed-out one. Reused (not
  // duplicated) for the 3D ridgeline waterfall's age-based fade, see
  // sampleColorscale().
  const WATERFALL_COLORSCALE = [
    [0.000, "#0d366b"], [0.083, "#104281"], [0.167, "#184f95"],
    [0.250, "#1c5cab"], [0.333, "#256abf"], [0.417, "#2a78d6"],
    [0.500, "#3987e5"], [0.583, "#5598e7"], [0.667, "#6da7ec"],
    [0.750, "#86b6ef"], [0.833, "#9ec5f4"], [0.917, "#b7d3f6"],
    [1.000, "#cde2fb"],
  ];

  // Fixed per-identity color, name-keyed (not positional) so a node missing
  // one axis never shifts another axis's color -- docs/CHART_CLUTTER_PLAN.md
  // §5 "fixed hue order, never reassigned based on which axes happen to be
  // present."
  const AXIS_COLORS = {
    accel_x: "#3987e5", accel_y: "#9085e9", accel_z: "#d55181",
    mic: "#d95926",
  };

  // Scalar trend grid: one plot per statistic, all four channels overlaid.
  // Grouped by STATISTIC, not by channel, on purpose -- the diagnostic
  // signal is directional (rms climbing on X while Y/Z stay flat is an
  // imbalance, see fuser.cpp's compute_scalars() comment), and that only
  // reads if the three accel axes share one plot's y-scale. Labels are
  // plain English; the keys are telemetry_schema.json's wire names
  // (rms_x/kurtosis_x/.../rms_mic) composed as `${key}_${suffix}`.
  const SCALAR_STATS = [
    { key: "rms", label: "RMS" },
    { key: "kurtosis", label: "Kurtosis" },
    { key: "std", label: "Std deviation" },
    { key: "peak", label: "Peak" },
    { key: "crest_factor", label: "Crest factor" },
    { key: "skewness", label: "Skewness" },
  ];

  // Wire-name suffix -> the AXIS_COLORS identity it shares with the spectrum
  // charts, so one axis reads as the same color everywhere on the page.
  const SCALAR_CHANNELS = [
    { suffix: "x", channel: "accel_x" },
    { suffix: "y", channel: "accel_y" },
    { suffix: "z", channel: "accel_z" },
    { suffix: "mic", channel: "mic" },
  ];

  // Scalar trends are a fixed-width live tail, NOT an autoranged view of the
  // whole buffer. With autorange the plot spends its first ~100s squeezing an
  // ever-growing series into the same width -- everything compresses, then it
  // abruptly starts scrolling once the ring buffer fills. Pinning the x-axis
  // to [latest - WINDOW, latest] makes it scroll from the first frame and
  // keeps the horizontal scale constant, so drift is actually comparable
  // between two glances. Same fix (and same reason) as the anomaly chart's
  // anomalyLiveRange -- see ANOMALY_WINDOW_SECONDS.
  const SCALAR_WINDOW_SECONDS = 60;
  // Retain a little past the window so the left edge is never a ragged gap.
  const SCALAR_RETENTION_SECONDS = SCALAR_WINDOW_SECONDS + 10;
  // Memory backstop only; SCALAR_RETENTION_SECONDS is what normally evicts.
  const SCALAR_MAX_POINTS = 1200;

  const RIDGE_TRACE_COUNT = 16;
  const RIDGE_X_SHIFT_PER_STEP = -0.4;
  const RIDGE_Y_SHIFT_FRACTION = 0.15;

  const PAPER_BG = "#0f172a";
  const PLOT_BG = "#0f172a";
  const GRID_COLOR = "#1e293b";
  const AXIS_COLOR = "#334155";
  const TEXT_COLOR = "#94a3b8";
  const FONT_FAMILY = "system-ui, -apple-system, 'Segoe UI', sans-serif";

  // Spectrum charts: single current snapshot, no zoom (fixedrange axes
  // below). Waterfall charts: history over time, zoom/pan is useful.
  const SPECTRUM_CONFIG = { displaylogo: false, responsive: true, displayModeBar: false };
  const WATERFALL_CONFIG = { displaylogo: false, responsive: true };

  // node_id -> per-node buffers, host elements, and mount flags. Deliberately
  // module-level, not tied to any per-render state -- see file docstring.
  const nodes = {};

  let ws = null;
  let expandedIds = new Set();
  let scalarsOpenIds = new Set();
  let waterfallOpenIds = new Set();
  let registryHandler = null;
  let perfHandler = null;
  let alertsHandler = null;
  let classifierHandler = null;
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

  // Evicts by age (ANOMALY_RETENTION_SECONDS) rather than a fixed point
  // count, so scrollback depth is a stable duration regardless of frame
  // rate; ANOMALY_MAX_POINTS only guards against runaway memory use.
  // `arr` is assumed sorted ascending by `t` (true for both call sites:
  // live pushes append newest-last, and the seed merge sorts before this
  // runs).
  function trimByAge(arr, latestT, retentionSeconds, maxPoints) {
    let i = 0;
    while (i < arr.length && arr[i].t < latestT - retentionSeconds) i++;
    if (i > 0) arr.splice(0, i);
    if (arr.length > maxPoints) arr.splice(0, arr.length - maxPoints);
  }

  function trimAnomalyByAge(arr, latestT) {
    trimByAge(arr, latestT, ANOMALY_RETENTION_SECONDS, ANOMALY_MAX_POINTS);
  }

  function clamp01(x) {
    return Math.max(0, Math.min(1, x));
  }

  function hexToRgba(hex, alpha) {
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`;
  }

  function hexToRgb(hex) {
    const n = parseInt(hex.slice(1), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }

  // Linear RGB interpolation against a Plotly-style [frac, hex] colorscale
  // array -- used to reuse WATERFALL_COLORSCALE for the ridgeline waterfall's
  // age-based fade instead of inventing a second palette.
  function sampleColorscale(stops, frac) {
    const clamped = clamp01(frac);
    for (let i = 1; i < stops.length; i++) {
      const [f0, c0] = stops[i - 1];
      const [f1, c1] = stops[i];
      if (clamped <= f1 || i === stops.length - 1) {
        const span = f1 - f0 || 1;
        const t = clamp01((clamped - f0) / span);
        const [r0, g0, b0] = hexToRgb(c0);
        const [r1, g1, b1] = hexToRgb(c1);
        const r = Math.round(r0 + (r1 - r0) * t);
        const g = Math.round(g0 + (g1 - g0) * t);
        const b = Math.round(b0 + (b1 - b0) * t);
        return `rgb(${r},${g},${b})`;
      }
    }
    return stops[stops.length - 1][1];
  }

  function ensureNode(nodeId) {
    if (!nodes[nodeId]) {
      nodes[nodeId] = {
        channels: [],
        liveSpectrum: {},   // accel_x/accel_y/accel_z/mic -> latest bins (model channels)
        spectrumMeta: {},   // mic/accel/accel_x/accel_y/accel_z -> {fs, fftSize}
        scalarSeries: {},   // rms_x/kurtosis_x/.../skewness_mic -> [{t, v}] ring buffer (24 keys)
        waterfall: {},      // accel_x/accel_y/accel_z/mic -> [{t, bins}] ring buffer
        waterfallMode: "2d",
        anomaly: [],
        anomalySeeded: false,
        anomalyLive: true, // false once the user drags the rangeslider away from the live tail
        anomalyPinnedRange: null, // [x0, x1] the user scrubbed to, while anomalyLive is false
        anomalyWindowSeconds: ANOMALY_WINDOW_SECONDS, // live-tail width; user-adjustable via the rangeslider

        classification: null, // {label, confidence, scores, ts} -- Edge Impulse fault classifier, seeded from entry.last_classification and kept live over the "classification" WS event; null if no model loaded for this node's device_type
        classificationEl: null,
        anomalyEl: null, anomalyMounted: false,
        accelSpectrumEl: null, accelSpectrumMounted: false,
        micSpectrumEl: null, micSpectrumMounted: false,
        scalarEls: {}, scalarMounted: {},
        waterfallEls: {}, waterfallMounted: {},

        // Per-node thresholds + status, mirrored from registry data
        // (onNodesPolled/the "registry" WS push below) -- null threshold
        // until commissioned (pipeline/commissioning.py calibrates from that
        // motor's own healthy baseline), never from a single global preset.
        warningThreshold: null,
        faultThreshold: null,
        status: null,
        commissioningProgress: null,
      };
    }
    return nodes[nodeId];
  }

  function purgeNode(nodeId) {
    const node = nodes[nodeId];
    if (!node) return;
    const purge = (el) => { try { if (el) Plotly.purge(el); } catch (err) { /* already detached */ } };
    purge(node.anomalyEl);
    purge(node.accelSpectrumEl);
    purge(node.micSpectrumEl);
    for (const el of Object.values(node.scalarEls)) purge(el);
    for (const el of Object.values(node.waterfallEls)) purge(el);
    delete nodes[nodeId];
    dirty.delete(nodeId);
  }

  // ---------------------------------------------------------------------
  // WebSocket -- the only source of spectrum data (no
  // REST equivalent).
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
      } else if (msg.type === "classification") {
        handleClassification(msg);
      } else if (msg.type === "registry") {
        applyThresholds(msg.node_id, msg.entry);
        if (registryHandler) registryHandler(msg);
      } else if (msg.type === "removed" && registryHandler) {
        registryHandler(msg);
      } else if ((msg.type === "training_progress" || msg.type === "capture"
                  || msg.type === "setup" || msg.type === "trip_confirm")
                 && registryHandler) {
        // None of these touch NodeStatus (capture never does;
        // training_progress is an in-between tick, not a status change; the
        // setup step and the confirm-by-stopping result are their own
        // flow) so they ride the same fleet-state push handler as
        // "registry"/"removed" rather than getting their own init()
        // callback -- app.js's handler already branches on msg.type for
        // this exact reason.
        //
        // "setup"/"trip_confirm" were missing here, which is the whole
        // reason this list is worth reading twice: app.js DID handle both,
        // and this dispatch dropped them first. The visible symptom was a
        // drawer stuck on "Stopping output 1 -- watch the machine..."
        // forever, because the result that clears it only ever arrives as
        // a broadcast. Setup's own 5s refresh papered over the step
        // broadcasts well enough to hide it until a real rig ran the test.
        registryHandler(msg);
      } else if (msg.type === "perf_stats") {
        if (perfHandler) perfHandler(msg);
      } else if (msg.type === "telegram_subscribers") {
        if (alertsHandler) alertsHandler(msg);
      } else if (msg.type === "ei_progress" || msg.type === "device_types_renamed") {
        if (classifierHandler) classifierHandler(msg);
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
    // msg.axis_channels (SensorFrame.display_bins) is display-only data no
    // chart reads today (the fused `accel` channel, superseded by the
    // per-axis accel_x/y/z model channels above) -- deliberately dropped
    // rather than buffered for nothing.
    for (const [name, meta] of Object.entries(msg.spectrum_meta || {})) {
      node.spectrumMeta[name] = { fs: meta.fs, fftSize: meta.fft_size };
    }
    // Appended per key rather than replacing a latest-value object: these
    // are trend charts now, so each scalar keeps its own history. A key
    // absent from this frame simply doesn't advance -- it is never treated
    // as a zero or a gap.
    for (const [name, value] of Object.entries(msg.scalars || {})) {
      if (typeof value !== "number") continue;
      if (!node.scalarSeries[name]) node.scalarSeries[name] = [];
      const rows = node.scalarSeries[name];
      rows.push({ t: msg.timestamp, v: value });
      trimByAge(rows, msg.timestamp, SCALAR_RETENTION_SECONDS, SCALAR_MAX_POINTS);
    }
    dirty.add(msg.node_id);
  }

  // ---------------------------------------------------------------------
  // Anomaly score -- seeded once from history, then kept live over /ws's
  // "anomaly" broadcast (main.py's on_score, fired every gated/scored
  // frame). Feeds both the hero gauge/number and the anomaly trend chart.
  // ---------------------------------------------------------------------

  function handleAnomalyScore(msg) {
    const node = ensureNode(msg.node_id);
    node.anomaly.push({ t: msg.timestamp, score: msg.score, status: msg.status });
    trimAnomalyByAge(node.anomaly, msg.timestamp);
    dirty.add(msg.node_id);
  }

  function handleClassification(msg) {
    const node = ensureNode(msg.node_id);
    node.classification = { label: msg.label, confidence: msg.confidence, scores: msg.scores, ts: msg.timestamp };
    dirty.add(msg.node_id);
  }

  async function seedAnomalyHistory(nodeId, node) {
    try {
      const res = await fetch(`/nodes/${encodeURIComponent(nodeId)}/history`);
      if (!res.ok) return;
      const rows = await res.json();
      const historical = rows.map((r) => ({ t: r.timestamp, score: r.anomaly_score, status: r.status_at_time }));
      const merged = historical.concat(node.anomaly).sort((a, b) => a.t - b.t);
      if (merged.length) trimAnomalyByAge(merged, merged[merged.length - 1].t);
      node.anomaly = merged;
      dirty.add(nodeId);
    } catch (err) {
      console.error(`Failed to seed anomaly history for ${nodeId}`, err);
    }
  }

  // entry is registry.py's plain to_dict() (either GET /nodes' per-node
  // dict or a "registry" WS push's msg.entry) -- warning_threshold/
  // fault_threshold are only non-null once the node has been commissioned,
  // so an uncommissioned node's hero renders its neutral state instead.
  function applyThresholds(nodeId, entry) {
    const node = ensureNode(nodeId);
    // A recommission just completed (was collecting/training, now scored) --
    // history/store.py's row for this node was wiped server-side alongside
    // the new model (api/app.py's commission/stop), so the old pre-
    // recommission points must go too, or the chart that reappears (see
    // UNCOMMISSIONED_STATUSES gating in detailBodyHtml) would show a stale
    // trend merged with the fresh one. Re-seeding on the next expand just
    // re-fetches the now-empty history, so this is a no-op there too.
    if (UNCOMMISSIONED_STATUSES.has(node.status) && !UNCOMMISSIONED_STATUSES.has(entry.status)) {
      node.anomaly = [];
      node.anomalySeeded = false;
    }
    node.warningThreshold = typeof entry.warning_threshold === "number" ? entry.warning_threshold : null;
    node.faultThreshold = typeof entry.fault_threshold === "number" ? entry.fault_threshold : null;
    node.status = entry.status;
    node.commissioningProgress = entry.commissioning_progress || null;
    node.classification = entry.last_classification || null;
    dirty.add(nodeId);
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

  // Recording labels are the operator's own words (bearing/loose/unbalanced/
  // healthy, etc, docs/EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md), not Edge
  // Impulse jargon -- title-casing is all that's needed for display, no
  // translation table.
  function titleCase(label) {
    return String(label).replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  }

  // Hand-rolled bars, not Plotly (cheap plain HTML is enough for a static
  // per-frame snapshot -- no zoom/pan needed). Deliberately a
  // single neutral accent, not STATUS_COLOR's green/amber/red -- that palette
  // is reserved dashboard-wide for NodeStatus (doc S2), and this is a
  // completely independent signal (pipeline/manager.py's classifier never
  // feeds status) that happens to often share the English word "healthy"
  // with one of its class labels. Reusing status colors here would visually
  // claim the two are the same thing when they can legitimately disagree.
  function buildClassificationHtml(node) {
    const c = node.classification;
    if (!c || !c.scores || !Object.keys(c.scores).length) {
      return `<div class="classification-empty">No classifier trained for this asset class yet</div>`;
    }
    const rows = Object.entries(c.scores).sort((a, b) => b[1] - a[1]);
    return `<div class="classification-bars">${rows.map(([label, prob]) => {
      const isTop = label === c.label;
      const pct = Math.round(prob * 100);
      return `<div class="classification-bar${isTop ? " classification-bar--top" : ""}">
        <div class="classification-bar__label">${escapeAttr(titleCase(label))}</div>
        <div class="classification-bar__track"><div class="classification-bar__fill" style="width:${pct}%"></div></div>
        <div class="classification-bar__value">${pct}%</div>
      </div>`;
    }).join("")}</div>`;
  }

  // ---------------------------------------------------------------------
  // Plotly figure construction
  // ---------------------------------------------------------------------

  function smallFont() {
    return { family: FONT_FAMILY, color: TEXT_COLOR, size: 10 };
  }

  // The scalar grid packs six plots side by side, so its ticks/legend are the
  // page's most crowded text -- smallFont()'s 10px is unreadable there.
  function scalarFont() {
    return { family: FONT_FAMILY, color: TEXT_COLOR, size: 12 };
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

  function transposeZ(rows, binCount) {
    const z = [];
    for (let b = 0; b < binCount; b++) {
      const row = new Array(rows.length);
      for (let c = 0; c < rows.length; c++) row[c] = rows[c].bins[b];
      z.push(row);
    }
    return z;
  }

  function hLine(value, color, yref) {
    return {
      type: "line", xref: "paper", x0: 0, x1: 1, yref, y0: value, y1: value,
      line: { color, width: 1.5, dash: "dash" },
    };
  }

  // [x0, x1] for the live tail: node.anomalyWindowSeconds wide (starts at
  // ANOMALY_WINDOW_SECONDS, but the user can narrow/widen it -- see
  // onAnomalyRelayout below) anchored to the newest point (or "now"
  // pre-first-point), never to the buffer's actual min/max. That's what
  // makes the window a constant size from the very first point instead of
  // growing until ANOMALY_MAX_POINTS fills up (the old autorange-driven
  // "compresses, then suddenly snaps into a moving window" behavior).
  function anomalyLiveRange(node) {
    const n = node.anomaly.length;
    const latestT = n ? node.anomaly[n - 1].t : Date.now() / 1000;
    return [new Date((latestT - node.anomalyWindowSeconds) * 1000), new Date(latestT * 1000)];
  }

  // Fired on every rangeslider drag (registered once at mount, see
  // attachExpanded). "Live" means the right edge is still at/near "now" --
  // dragging the *left* handle alone (narrowing/widening the window while
  // still tracking the tail, e.g. "zoom into just the new part") stays live
  // but adopts the user's new width via node.anomalyWindowSeconds, instead
  // of every flush() tick reverting it to the ANOMALY_WINDOW_SECONDS default
  // just because the right edge still reads as "live" (that conflation --
  // "right edge is live" treated as "so the old fixed default width must be
  // what's wanted" -- was the actual bug: a left-handle-only drag kept
  // re-qualifying as live on every intermediate relayout event during the
  // drag, so the custom width it carried was thrown away on the very next
  // redraw). Dragging the right handle away from "now" (or both handles)
  // pins the view exactly where dropped (node.anomalyLive = false) until
  // the user drags back within ANOMALY_LIVE_SNAP_TOLERANCE_SECONDS of "now"
  // or clicks the Live button.
  //
  // A rangeslider drag emits a single `"xaxis.range": [x0, x1]` array key,
  // NOT the `"xaxis.range[0]"`/`"xaxis.range[1]"` bracket-indexed pair a
  // main-plot zoom-box drag or a programmatic Plotly.relayout() call uses
  // -- confirmed against a real rangeslider drag, not assumed. Handling
  // only the bracket form here silently never matched a real drag, so
  // anomalyLive never flipped and every drag got overwritten by the next
  // live-range redraw within one flush() tick.
  function onAnomalyRelayout(nodeId, node, ev) {
    let x0, x1;
    if (Array.isArray(ev["xaxis.range"])) {
      [x0, x1] = ev["xaxis.range"];
    } else {
      x0 = ev["xaxis.range[0]"];
      x1 = ev["xaxis.range[1]"];
    }
    if (x0 === undefined || x1 === undefined) return;
    const n = node.anomaly.length;
    const latestMs = n ? node.anomaly[n - 1].t * 1000 : Date.now();
    const rightEdgeMs = new Date(x1).getTime();
    if (Math.abs(latestMs - rightEdgeMs) <= ANOMALY_LIVE_SNAP_TOLERANCE_SECONDS * 1000) {
      node.anomalyLive = true;
      node.anomalyPinnedRange = null;
      const widthSeconds = (rightEdgeMs - new Date(x0).getTime()) / 1000;
      if (widthSeconds > 0) node.anomalyWindowSeconds = widthSeconds;
    } else {
      node.anomalyLive = false;
      node.anomalyPinnedRange = [x0, x1];
    }
  }

  // Full-size replacement for the old hero sparkline: a real Plotly chart
  // with an axis/gridlines/hover, matching the accel/mic spectrum charts'
  // idiom rather than a bespoke hand-rolled SVG. Per-point marker color by
  // that point's own status (carried over from the pre-redesign timeline
  // chart) plus dashed threshold lines.
  //
  // y-range is recomputed from the points inside the current x-window (not
  // Plotly's default autorange, which fits the *entire* trace) -- a
  // zoomed-in window used to keep the full-history y-scale, so small real
  // moves in a narrow time slice rendered as a flat line near the bottom of
  // a range sized for the dataset's all-time min/max. The warning/fault
  // threshold lines are always folded into min/max too, even if they sit
  // well outside the visible data -- otherwise a zoomed-in view would
  // silently scroll the dashed threshold lines off-screen with no
  // indication they'd left the picture.
  function anomalyVisibleYRange(node, range) {
    if (!range) return null;
    const t0 = new Date(range[0]).getTime();
    const t1 = new Date(range[1]).getTime();
    let min = Infinity, max = -Infinity;
    for (const p of node.anomaly) {
      const ms = p.t * 1000;
      if (ms < t0 || ms > t1) continue;
      if (p.score < min) min = p.score;
      if (p.score > max) max = p.score;
    }
    if (typeof node.warningThreshold === "number") {
      min = Math.min(min, node.warningThreshold);
      max = Math.max(max, node.warningThreshold);
    }
    if (typeof node.faultThreshold === "number") {
      min = Math.min(min, node.faultThreshold);
      max = Math.max(max, node.faultThreshold);
    }
    if (min > max) return null;
    const pad = (max - min) * 0.1 || Math.abs(max) * 0.1 || 0.05;
    return [min - pad, max + pad];
  }

  // Min/max bucket decimation: splits `points` into budget/2 equal-count
  // buckets and keeps each bucket's lowest- and highest-scoring sample.
  // Every emitted point is a real sample carrying its own t/score/status, so
  // its marker colour stays truthful -- nothing is averaged into existence.
  // Keeping BOTH extremes (rather than every Nth point, or a per-bucket mean)
  // is what stops a one-frame spike across the fault threshold from being
  // decimated out of the picture, which is the one thing this chart exists to
  // show.
  function decimateMinMax(points, budget) {
    if (points.length <= budget) return points;
    const buckets = Math.max(1, Math.floor(budget / 2));
    const out = [];
    for (let b = 0; b < buckets; b++) {
      const start = Math.floor((b * points.length) / buckets);
      const end = Math.floor(((b + 1) * points.length) / buckets);
      if (end <= start) continue;
      let lo = start, hi = start;
      for (let i = start + 1; i < end; i++) {
        if (points[i].score < points[lo].score) lo = i;
        if (points[i].score > points[hi].score) hi = i;
      }
      // Emit in ascending t, not lo-then-hi: the trace is a line plot, and
      // one out-of-order pair draws a visible backwards spike.
      const first = Math.min(lo, hi), second = Math.max(lo, hi);
      out.push(points[first]);
      if (second !== first) out.push(points[second]);
    }
    return out;
  }

  // Which of node.anomaly's points actually get drawn.
  //
  // Plotly.react's cost here is linear in the point count -- measured
  // ~37us/point for this exact figure (SVG scatter, per-point marker colour
  // array, spline, rangeslider). The buffer holds ANOMALY_RETENTION_SECONDS,
  // so at the live ~5.4 scores/s it reaches ~9,700 points, and drawing all of
  // them every RENDER_THROTTLE_MS measured 370ms per redraw: the main thread
  // was ~67% busy in Plotly, this one chart took 49% of wall-clock, and every
  // other chart's redraw starved behind it. That is the "charts frozen while
  // the status LED still updates" failure -- the LED/gauge are plain DOM
  // writes on the WS handler, so they kept full rate while the render queue
  // never drained.
  //
  // Only the point count was worth attacking: at 10k points, dropping the
  // rangeslider bought 370->190ms, a flat marker colour 341ms, linear instead
  // of spline 323ms, scattergl 332ms. So the figure keeps every design choice
  // it had and just draws fewer points.
  //
  // Full resolution inside the visible window, decimated envelope outside it:
  // the window is the only part whose individual samples a reader can
  // resolve, while the off-screen remainder exists purely to give the
  // rangeslider its 30-minute overview, where an envelope is all that's
  // wanted. Splitting it this way means zooming or scrubbing never shows
  // decimated data -- the window moves and whatever it now covers is
  // re-selected at full resolution on the next redraw.
  function anomalyRenderPoints(node, range) {
    const points = node.anomaly;
    if (points.length <= ANOMALY_MAX_RENDERED_POINTS) return points;
    if (!range) return decimateMinMax(points, ANOMALY_MAX_RENDERED_POINTS);

    // points is sorted ascending by t -- trimByAge's documented contract.
    const t0 = new Date(range[0]).getTime() / 1000;
    const t1 = new Date(range[1]).getTime() / 1000;
    let lo = 0;
    while (lo < points.length && points[lo].t < t0) lo++;
    let hi = lo;
    while (hi < points.length && points[hi].t <= t1) hi++;

    const visible = points.slice(lo, hi);
    // A window wide enough to hold the whole budget on its own (a fully
    // zoomed-out scrub) gets decimated like anything else.
    if (visible.length >= ANOMALY_MAX_RENDERED_POINTS) {
      return decimateMinMax(visible, ANOMALY_MAX_RENDERED_POINTS);
    }

    const before = points.slice(0, lo);
    const after = points.slice(hi);
    const budget = ANOMALY_MAX_RENDERED_POINTS - visible.length;
    // Split the leftover budget between the two off-screen sides in
    // proportion to how much off-screen data each holds, so a window scrubbed
    // into the middle of the buffer doesn't spend it all on one side.
    const offscreen = before.length + after.length;
    const beforeBudget = offscreen ? Math.round((budget * before.length) / offscreen) : 0;
    return decimateMinMax(before, beforeBudget)
      .concat(visible, decimateMinMax(after, budget - beforeBudget));
  }

  function buildAnomalyFigure(nodeId, node) {
    // Hoisted above the traces: the range decides which points are drawn at
    // full resolution (see anomalyRenderPoints), not just how the axis reads.
    const range = node.anomalyLive ? anomalyLiveRange(node) : node.anomalyPinnedRange;
    const points = anomalyRenderPoints(node, range);
    const times = points.map((p) => new Date(p.t * 1000));
    const scores = points.map((p) => p.score);
    const colors = points.map((p) => STATUS_COLOR[p.status] || TEXT_COLOR);
    const traces = [{
      type: "scatter", mode: "lines+markers",
      x: times, y: scores,
      line: { shape: "spline", color: ANOMALY_LINE_COLOR, width: 1.5 },
      marker: { color: colors, size: 5, line: { width: 0 } },
      hovertemplate: "%{y:.4f}<extra></extra>",
    }];
    const shapes = [];
    if (typeof node.warningThreshold === "number") shapes.push(hLine(node.warningThreshold, STATUS_COLOR.warning, "y"));
    if (typeof node.faultThreshold === "number") shapes.push(hLine(node.faultThreshold, STATUS_COLOR.fault, "y"));
    const yRange = anomalyVisibleYRange(node, range);
    const layout = {
      ...darkLayoutBase(), uirevision: nodeId, shapes, height: 230,
      xaxis: axisBase({
        anchor: "y", fixedrange: true, range,
        rangeslider: { visible: true, thickness: 0.12, bgcolor: PLOT_BG, bordercolor: AXIS_COLOR, borderwidth: 1 },
      }),
      yaxis: axisBase({
        anchor: "x", fixedrange: true, autorange: !yRange,
        ...(yRange ? { range: yRange } : {}),
      }),
    };
    return [traces, layout];
  }

  // bins carry no frequency info of their own -- k is just an index into
  // whatever FFT the firmware ran. node.spectrumMeta (wire-sourced fs/fft_size,
  // see SensorFrame.spectrum_meta) is what turns that into an actual frequency.
  // fft_size on the wire is the *pooled* value (fuser.cpp divides it by the
  // pooling factor before sending), so fs/fft_size is exactly one wire bin's
  // width -- call it W.
  //
  // The bin's frequency is NOT k*W. Firmware discards DC before sending
  // anything (accel_fft_magnitude()/mic_fft_magnitude() write native bin k+1
  // into slot k), and then average-pools P native bins into each wire bin, so
  // wire bin k covers the half-open native band (k*W, (k+1)*W] -- there is no
  // 0 Hz bin on the wire at all. Plotting it at k*W pinned the first bin to
  // 0 Hz and read a whole wire bin low across the axis; that showed up as a
  // wrong frequency against a tone generator (2026-08-02). Plot each bin at
  // the centre of the band it actually covers instead.
  //
  // Residual: the exact centre is (k + 0.5 + 1/(2P))*W, i.e. this sits half a
  // *native* bin (fs/2*nativeFftSize -- 6.25 Hz accel, 23 Hz mic) low. P isn't
  // recoverable from the wire: the pooled fft_size hides it, and bin_count
  // can't stand in for it because mic truncates its native bins (only the
  // useful <24kHz half of 1024 is ever pooled, mic_sampler.cpp). Fixing that
  // last fraction of a bin needs a real per-bin frequency on the wire; it is
  // far below the 50 Hz / 187.5 Hz bin width either chart can resolve.
  //
  // Returns one frequency per bin, or null if metadata hasn't arrived yet (a
  // chart can render before the first frame), in which case callers plot bare
  // bin indices.
  function binFreqsFor(node, name, binCount) {
    const meta = node.spectrumMeta[name];
    if (!meta || !meta.fftSize || !binCount) return null;
    const width = meta.fs / meta.fftSize;
    return Array.from({ length: binCount }, (_, k) => (k + 0.5) * width);
  }

  // Per-axis only -- the fused/combined `accel` channel (old single-line
  // "raw" spectrum, pre-per-axis) is display-only now and no longer shown;
  // base station always carries real accel_x/y/z, so there's no fallback
  // case left to handle.
  function buildAccelSpectrumFigure(nodeId, node) {
    const axisNames = ["accel_x", "accel_y", "accel_z"].filter((n) => node.liveSpectrum[n]);
    const traces = [];
    const layout = { ...darkLayoutBase(), uirevision: nodeId, height: 190 };
    let haveFreq = false;

    if (axisNames.length) {
      layout.showlegend = true;
      layout.legend = { orientation: "h", y: 1.2, font: smallFont() };
      axisNames.forEach((name) => {
        const bins = node.liveSpectrum[name];
        const color = AXIS_COLORS[name];
        const freqs = binFreqsFor(node, name, bins.length);
        if (freqs) haveFreq = true;
        traces.push({
          type: "scatter", mode: "lines",
          x: freqs || bins.map((_, k) => k), y: bins,
          line: { shape: "spline", color, width: 1.5 },
          xaxis: "x", yaxis: "y", name,
          hovertemplate: freqs
            ? `${name} %{x:.0f} Hz: %{y:.3f}<extra></extra>`
            : `${name} bin %{x}: %{y:.3f}<extra></extra>`,
        });
      });
    }

    layout.xaxis = axisBase({
      anchor: "y", fixedrange: true,
      title: { text: haveFreq ? "Frequency (Hz)" : "Frequency bin", font: smallFont() },
    });
    // tozero: magnitudes never go negative, but Plotly's default "normal"
    // autorange still pads a bit below the data min, so the 0 gridline ends
    // up floating above the actual x-axis baseline instead of sitting on it.
    layout.yaxis = axisBase({ anchor: "x", fixedrange: true, rangemode: "tozero" });
    return [traces, layout];
  }

  function buildMicSpectrumFigure(nodeId, node) {
    const bins = node.liveSpectrum.mic || [];
    const color = AXIS_COLORS.mic;
    const freqs = binFreqsFor(node, "mic", bins.length);
    const traces = [{
      type: "scatter", mode: "lines",
      x: freqs || bins.map((_, k) => k), y: bins,
      line: { shape: "spline", color, width: 1.5 },
      fill: "tozeroy", fillcolor: hexToRgba(color, 0.15),
      xaxis: "x", yaxis: "y", name: "mic",
      hovertemplate: freqs
        ? "mic %{x:.0f} Hz: %{y:.3f}<extra></extra>"
        : "mic bin %{x}: %{y:.3f}<extra></extra>",
    }];
    const layout = {
      ...darkLayoutBase(), uirevision: nodeId, height: 160,
      xaxis: axisBase({
        anchor: "y", fixedrange: true,
        // mic's range runs 0-24kHz (vs. accel's 0-800Hz) -- Plotly's default
        // tick spacing on a range that wide lands on bare 5kHz marks, too
        // sparse to read intermediate values off. 2kHz gives ~12 ticks.
        dtick: freqs ? 2000 : undefined,
        title: { text: freqs ? "Frequency (Hz)" : "Frequency bin", font: smallFont() },
      }),
      yaxis: axisBase({ anchor: "x", fixedrange: true, rangemode: "tozero" }),
    };
    return [traces, layout];
  }

  // One statistic, up to four channel traces. Mic rides its own right-hand
  // y-axis: its magnitude is orders away from the accelerometer's, and on a
  // single shared scale it squashes all three accel traces into one flat
  // line -- which is exactly the comparison this grid exists to show. The
  // trace keeps AXIS_COLORS.mic (same hue as everywhere else on the page),
  // and the right-hand axis is tinted to match so it's obvious which scale
  // belongs to which trace.
  function buildScalarFigure(nodeId, node, stat) {
    const traces = [];
    let hasMic = false;
    let latestT = null;
    for (const ch of SCALAR_CHANNELS) {
      const rows = node.scalarSeries[`${stat.key}_${ch.suffix}`];
      if (!rows || !rows.length) continue;
      const isMic = ch.suffix === "mic";
      if (isMic) hasMic = true;
      const tail = rows[rows.length - 1].t;
      if (latestT === null || tail > latestT) latestT = tail;
      traces.push({
        type: "scatter", mode: "lines",
        x: rows.map((r) => new Date(r.t * 1000)),
        y: rows.map((r) => r.v),
        // spline for the same reason the spectrum charts use it: these are
        // samples of a continuous physical quantity, and the polyline's
        // corners read as structure that isn't in the signal.
        line: { shape: "spline", smoothing: 0.9, color: AXIS_COLORS[ch.channel], width: 2 },
        name: ch.channel,
        xaxis: "x", yaxis: isMic ? "y2" : "y",
        hovertemplate: `${ch.channel} %{y:.4g}<extra></extra>`,
      });
    }
    // Fixed-width live tail (see SCALAR_WINDOW_SECONDS). Null until the first
    // frame lands, which leaves Plotly autoranging an empty plot -- fine.
    const range = latestT === null
      ? undefined
      : [new Date((latestT - SCALAR_WINDOW_SECONDS) * 1000), new Date(latestT * 1000)];
    const layout = {
      ...darkLayoutBase(),
      uirevision: `${nodeId}-scalar-${stat.key}`,
      height: 260,
      margin: { l: 56, r: hasMic ? 56 : 16, t: 4, b: 30 },
      showlegend: true,
      legend: { orientation: "h", y: 1.16, font: scalarFont() },
      hovermode: "x unified",
      xaxis: axisBase({
        anchor: "y", fixedrange: true, range,
        nticks: 5, tickformat: "%H:%M:%S", tickfont: scalarFont(),
      }),
      yaxis: axisBase({ anchor: "x", fixedrange: true, tickfont: scalarFont(), nticks: 6 }),
    };
    if (hasMic) {
      layout.yaxis2 = axisBase({
        overlaying: "y", side: "right", showgrid: false, fixedrange: true, nticks: 6,
        tickfont: { ...scalarFont(), color: AXIS_COLORS.mic },
        linecolor: AXIS_COLORS.mic,
      });
    }
    return [traces, layout];
  }

  // 2D mode: same heatmap trace/transposeZ as before, plus zsmooth -- the
  // scoped fix for "reads as a blocky grid, not organic" (doc §4).
  function buildWaterfall2DFigure(nodeId, node, channel) {
    const rows = node.waterfall[channel] || [];
    const binCount = rows.length ? rows[0].bins.length : 0;
    const traces = [{
      type: "heatmap",
      x: rows.map((r) => new Date(r.t * 1000)),
      y: Array.from({ length: binCount }, (_, k) => k),
      z: transposeZ(rows, binCount),
      zsmooth: "best",
      colorscale: WATERFALL_COLORSCALE, showscale: true,
      colorbar: { thickness: 10, len: 0.8, tickfont: smallFont() },
      hoverongaps: false,
    }];
    const layout = {
      ...darkLayoutBase(), uirevision: `${nodeId}-waterfall-${channel}`, height: 220,
      xaxis: axisBase({ anchor: "y" }),
      yaxis: axisBase({ anchor: "x", title: { text: channel, font: smallFont() } }),
    };
    return [traces, layout];
  }

  // 3D-illusion mode: a classic SDR-style waterfall built as a 2D "ridgeline"
  // -- recent spectrum columns drawn as separate lines twith a small offset
  // per column (older = more offset) and an age-based color fade sampled
  // from the SAME WATERFALL_COLORSCALE (newest = bright end, oldest = dim
  // end), using only plain `scatter` (the vendor bundle has no real
  // scatter3d/surface). Reuses node.waterfall[channel] directly -- no
  // transposeZ, each trace wants one column's bins as its own y array.
  function buildWaterfallRidgelineFigure(nodeId, node, channel) {
    const rows = node.waterfall[channel] || [];
    const recent = rows.slice(-RIDGE_TRACE_COUNT); // chronological: oldest..newest
    const n = recent.length;
    const traces = [];

    if (n) {
      const newestBins = recent[n - 1].bins;
      const ampRange = (Math.max(...newestBins) - Math.min(...newestBins)) || 1;
      const yStep = RIDGE_Y_SHIFT_FRACTION * ampRange;
      // Draw oldest first (drawn underneath) through newest last (drawn on
      // top) -- j is draw order, i is age (0 = newest/front).
      for (let j = 0; j < n; j++) {
        const i = n - 1 - j;
        const bins = recent[j].bins;
        const color = sampleColorscale(WATERFALL_COLORSCALE, 1 - i / Math.max(1, n - 1));
        traces.push({
          type: "scatter", mode: "lines",
          x: bins.map((_, k) => k + i * RIDGE_X_SHIFT_PER_STEP),
          y: bins.map((v) => v + i * yStep),
          line: { color, width: i === 0 ? 2 : 1 },
          xaxis: "x", yaxis: "y", showlegend: false, hoverinfo: "skip",
        });
      }
    }

    const layout = {
      ...darkLayoutBase(), uirevision: `${nodeId}-waterfall-${channel}`, height: 220,
      xaxis: axisBase({ anchor: "y", showticklabels: false }),
      yaxis: axisBase({ anchor: "x", showticklabels: false, title: { text: channel, font: smallFont() } }),
    };
    return [traces, layout];
  }

  function buildWaterfallFigure(nodeId, node, channel) {
    return node.waterfallMode === "3d"
      ? buildWaterfallRidgelineFigure(nodeId, node, channel)
      : buildWaterfall2DFigure(nodeId, node, channel);
  }

  // ---------------------------------------------------------------------
  // Mount / attach / redraw
  // ---------------------------------------------------------------------

  function ensureHostElements(node) {
    if (!node.classificationEl) {
      node.classificationEl = document.createElement("div");
      node.classificationEl.className = "chart-host";
    }
    if (!node.anomalyEl) {
      node.anomalyEl = document.createElement("div");
      node.anomalyEl.className = "chart-plotly";
    }
    if (!node.accelSpectrumEl) {
      node.accelSpectrumEl = document.createElement("div");
      node.accelSpectrumEl.className = "chart-plotly";
    }
    if (!node.micSpectrumEl) {
      node.micSpectrumEl = document.createElement("div");
      node.micSpectrumEl.className = "chart-plotly";
    }
    for (const stat of SCALAR_STATS) {
      if (!node.scalarEls[stat.key]) {
        const el = document.createElement("div");
        el.className = "chart-plotly";
        node.scalarEls[stat.key] = el;
      }
    }
    for (const channel of ALL_CHANNELS) {
      if (!node.waterfallEls[channel]) {
        const el = document.createElement("div");
        el.className = "chart-plotly";
        node.waterfallEls[channel] = el;
      }
    }
  }

  // Plotly-backed now (it was plain HTML tiles before), so it needs the same
  // don't-mount-into-a-collapsed-<details> gating Waterfall already has --
  // measuring layout in a hidden, zero-size div is the failure mode called
  // out in the file docstring.
  function mountScalarIfNeeded(nodeId, node, stat) {
    const el = node.scalarEls[stat.key];
    const [traces, layout] = buildScalarFigure(nodeId, node, stat);
    if (!traces.length) {
      el.innerHTML = `<div class="chart-placeholder">Waiting for data…</div>`;
      node.scalarMounted[stat.key] = false;
      return;
    }
    el.innerHTML = "";
    Plotly.newPlot(el, traces, layout, SPECTRUM_CONFIG);
    node.scalarMounted[stat.key] = true;
  }

  function mountWaterfallIfNeeded(nodeId, node, channel) {
    if (node.waterfallMounted[channel]) return;
    const el = node.waterfallEls[channel];
    const [traces, layout] = buildWaterfallFigure(nodeId, node, channel);
    Plotly.newPlot(el, traces, layout, WATERFALL_CONFIG);
    node.waterfallMounted[channel] = true;
  }

  // Switching mode fully remounts (Plotly.newPlot, never .react()) since 2D
  // heatmap and 3D ridgeline trace shapes are fundamentally incompatible.
  function setWaterfallMode(nodeId, mode) {
    const node = ensureNode(nodeId);
    if (node.waterfallMode === mode) return;
    node.waterfallMode = mode;
    for (const channel of Object.keys(node.waterfallEls)) {
      node.waterfallMounted[channel] = false;
    }
    if (waterfallOpenIds.has(nodeId)) {
      for (const channel of ALL_CHANNELS) mountWaterfallIfNeeded(nodeId, node, channel);
    }
    dirty.add(nodeId);
  }

  function findSlot(role, nodeId, channel) {
    const slots = document.querySelectorAll(`[data-role="${role}"]`);
    for (const el of slots) {
      if (el.dataset.nodeId !== nodeId) continue;
      if (channel !== undefined && el.dataset.channel !== channel) continue;
      return el;
    }
    return null;
  }

  function reparent(el, slot) {
    if (el.parentElement !== slot) slot.appendChild(el);
  }

  function attachExpanded(expandedNodeIds, openScalarsIds, openWaterfallIds) {
    expandedIds = expandedNodeIds;
    scalarsOpenIds = openScalarsIds;
    waterfallOpenIds = openWaterfallIds;

    for (const nodeId of expandedNodeIds) {
      // Look the row up *before* touching any buffers: a stale id can
      // linger in expandedNodeIds after its node was decommissioned (app.js
      // doesn't prune that Set on removal), in which case detailBodyHtml()
      // never rendered a row for it at all this pass, so no anchor exists.
      // motor-row-body is the row-exists signal (always rendered whenever a
      // row exists at all) -- chart-slot-anomaly can no longer serve that
      // role since it's now conditional on the node having a model.
      const rowBody = findSlot("motor-row-body", nodeId);
      if (!rowBody) continue;

      const node = ensureNode(nodeId);
      ensureHostElements(node);

      const classificationSlot = findSlot("chart-slot-classification", nodeId);
      if (classificationSlot) reparent(node.classificationEl, classificationSlot);

      const anomalySlot = findSlot("chart-slot-anomaly", nodeId);
      if (anomalySlot) {
        reparent(node.anomalyEl, anomalySlot);
        if (!node.anomalyMounted) {
          const [traces, layout] = buildAnomalyFigure(nodeId, node);
          Plotly.newPlot(node.anomalyEl, traces, layout, SPECTRUM_CONFIG);
          node.anomalyMounted = true;
          node.anomalyEl.on("plotly_relayout", (ev) => onAnomalyRelayout(nodeId, node, ev));
        }
        if (!node.anomalySeeded) {
          node.anomalySeeded = true;
          seedAnomalyHistory(nodeId, node);
        }
      }

      const accelSlot = findSlot("chart-slot-accel-spectrum", nodeId);
      if (accelSlot) {
        reparent(node.accelSpectrumEl, accelSlot);
        if (!node.accelSpectrumMounted) {
          const [traces, layout] = buildAccelSpectrumFigure(nodeId, node);
          Plotly.newPlot(node.accelSpectrumEl, traces, layout, SPECTRUM_CONFIG);
          node.accelSpectrumMounted = true;
        }
      }
      const micSlot = findSlot("chart-slot-mic-spectrum", nodeId);
      if (micSlot) {
        reparent(node.micSpectrumEl, micSlot);
        if (!node.micSpectrumMounted) {
          const [traces, layout] = buildMicSpectrumFigure(nodeId, node);
          Plotly.newPlot(node.micSpectrumEl, traces, layout, SPECTRUM_CONFIG);
          node.micSpectrumMounted = true;
        }
      }

      if (node.classificationEl) node.classificationEl.innerHTML = buildClassificationHtml(node);

      if (openScalarsIds.has(nodeId)) {
        for (const stat of SCALAR_STATS) {
          const slot = findSlot("chart-slot-scalar", nodeId, stat.key);
          if (!slot) continue;
          reparent(node.scalarEls[stat.key], slot);
          mountScalarIfNeeded(nodeId, node, stat);
        }
      }
      if (openWaterfallIds.has(nodeId)) {
        for (const channel of ALL_CHANNELS) {
          const wfSlot = findSlot("chart-slot-waterfall", nodeId, channel);
          if (!wfSlot) continue;
          reparent(node.waterfallEls[channel], wfSlot);
          mountWaterfallIfNeeded(nodeId, node, channel);
        }
      }
      dirty.add(nodeId); // redraw with whatever's been buffered since it was last mounted/visible
    }
  }

  // A single Plotly.react() runs 8-16ms for a spectrum trace and considerably
  // more for a waterfall (its heatmap goes through a canvas toDataURL, the
  // single most expensive call in a CPU profile of this page). Redrawing every
  // mounted chart of every dirty node in one synchronous pass therefore
  // produced ~265ms tasks with two nodes expanded and all panels open -- the
  // main thread was blocked ~47% of the time, so scrolling, hovering and the
  // WebSocket handler all had to wait behind a redraw. Total work was never
  // the problem on its own; doing it all in one uninterruptible task was.
  //
  // So redraws are queued as individual jobs and drained under a per-frame
  // time budget: whatever doesn't fit resumes on the next animation frame.
  // Same work, same data, but the browser gets a turn between charts.
  const FRAME_BUDGET_MS = 10; // of a 16.7ms frame, leaving room to composite

  const renderQueue = [];
  const queuedKeys = new Set();

  // A chart scrolled out of view is still mounted and still gets fresh data,
  // but redrawing it paints nothing anyone can see. Skipping those is what
  // keeps a fleet with several nodes expanded affordable at all -- the queue
  // is refilled from live state every flush, so anything scrolled back into
  // view redraws on the very next tick with current data, not stale pixels.
  function onScreen(el) {
    if (!el || !el.isConnected) return false;
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return false; // display:none / collapsed
    const margin = 200; // start drawing just before it scrolls in
    return r.bottom > -margin && r.top < window.innerHeight + margin;
  }

  function enqueue(key, el, run) {
    // Already pending: jobs read live node state when they run, so the queued
    // one will pick up whatever arrived since. Re-adding would only make the
    // same chart redraw twice for one frame's worth of data.
    if (queuedKeys.has(key)) return;
    if (!onScreen(el)) return;
    queuedKeys.add(key);
    renderQueue.push({ key, run });
  }

  function drainRenderQueue() {
    const started = performance.now();
    while (renderQueue.length) {
      const job = renderQueue.shift();
      queuedKeys.delete(job.key);
      job.run();
      if (performance.now() - started > FRAME_BUDGET_MS) break;
    }
    return renderQueue.length > 0; // more to do next frame
  }

  function flush() {
    for (const nodeId of Array.from(dirty)) {
      dirty.delete(nodeId);
      if (!expandedIds.has(nodeId)) continue; // still buffering, just not drawing
      const node = nodes[nodeId];
      if (!node || !node.anomalyEl) continue;

      if (node.classificationEl) node.classificationEl.innerHTML = buildClassificationHtml(node);

      if (node.anomalyMounted) {
        enqueue(`${nodeId}/anomaly`, node.anomalyEl, () => {
          const [traces, layout] = buildAnomalyFigure(nodeId, node);
          Plotly.react(node.anomalyEl, traces, layout, SPECTRUM_CONFIG);
        });
      }
      if (node.accelSpectrumMounted) {
        enqueue(`${nodeId}/accel`, node.accelSpectrumEl, () => {
          const [traces, layout] = buildAccelSpectrumFigure(nodeId, node);
          Plotly.react(node.accelSpectrumEl, traces, layout, SPECTRUM_CONFIG);
        });
      }
      if (node.micSpectrumMounted) {
        enqueue(`${nodeId}/mic`, node.micSpectrumEl, () => {
          const [traces, layout] = buildMicSpectrumFigure(nodeId, node);
          Plotly.react(node.micSpectrumEl, traces, layout, SPECTRUM_CONFIG);
        });
      }

      if (scalarsOpenIds.has(nodeId)) {
        for (const stat of SCALAR_STATS) {
          if (!node.scalarMounted[stat.key]) {
            mountScalarIfNeeded(nodeId, node, stat); // data may have arrived since the panel opened
            continue;
          }
          enqueue(`${nodeId}/scalar/${stat.key}`, node.scalarEls[stat.key], () => {
            const [traces, layout] = buildScalarFigure(nodeId, node, stat);
            Plotly.react(node.scalarEls[stat.key], traces, layout, SPECTRUM_CONFIG);
          });
        }
      }
      if (waterfallOpenIds.has(nodeId)) {
        for (const channel of ALL_CHANNELS) {
          if (!node.waterfallMounted[channel]) {
            mountWaterfallIfNeeded(nodeId, node, channel);
            continue;
          }
          enqueue(`${nodeId}/waterfall/${channel}`, node.waterfallEls[channel], () => {
            const [traces, layout] = buildWaterfallFigure(nodeId, node, channel);
            Plotly.react(node.waterfallEls[channel], traces, layout, WATERFALL_CONFIG);
          });
        }
      }
    }
  }

  // flush() is a synchronous burst of Plotly.react() calls -- one per mounted
  // chart on every dirty node -- so on a fleet with several nodes expanded it
  // is comfortably the most expensive thing this page does. setInterval(flush,
  // 300) started a new burst every 300ms whether or not the previous one had
  // finished, so once a redraw exceeded its own interval the browser was left
  // permanently behind, with the main thread never idle enough to service
  // scrolling, hover or the incoming WebSocket -- the page reads as "stuck"
  // even though data is still arriving.
  //
  // Self-scheduling instead: the next flush is only ever queued once the
  // previous one has actually returned, so redraws degrade to a lower frame
  // rate under load rather than overlapping. requestAnimationFrame both aligns
  // the draw with the compositor (no half-painted frames) and is throttled to
  // ~0 by the browser on a hidden tab, which is the right behavior for a
  // background dashboard: handleSpectrum keeps buffering into `dirty`, and one
  // redraw catches up on return.
  function startRenderLoop() {
    let scheduled = false;

    const run = () => {
      scheduled = false;
      let more = false;
      try {
        flush();
        more = drainRenderQueue();
      } finally {
        // Still charts left over from this batch: come straight back on the
        // next frame to finish them. Only once the queue is empty does the
        // loop fall back to the RENDER_THROTTLE_MS idle cadence, so a heavy
        // batch is spread across consecutive frames rather than either
        // blocking one frame or being stretched over several throttle ticks.
        if (more) {
          scheduled = true; // keep schedule() a no-op while this batch finishes
          requestAnimationFrame(run);
        } else {
          setTimeout(schedule, RENDER_THROTTLE_MS);
        }
      }
    };

    const schedule = () => {
      if (scheduled) return;
      scheduled = true;
      requestAnimationFrame(run);
    };

    schedule();
  }

  function chartSlotHtml(role, nodeId, channel) {
    const channelAttr = channel !== undefined ? ` data-channel="${escapeAttr(channel)}"` : "";
    return `<div class="chart-slot" data-role="${role}" data-node-id="${escapeAttr(nodeId)}"${channelAttr}></div>`;
  }

  // Section-level omission (whole chart/collapsible present or not) is
  // decided from entry.sensor_config -- REST truth, known from frame 1, so
  // a brand-new node's first expand never flashes "no chart" waiting on a
  // WS race. Within-chart conditionals (which axes overlay) are decided from
  // this module's own WS-fed state at mount/render time instead (see the
  // build*Figure functions).
  function detailBodyHtml(entry, uiState) {
    const nodeId = entry.node_id;
    const safeId = escapeAttr(nodeId);
    const node = ensureNode(nodeId);
    const sensorConfig = Array.isArray(entry.sensor_config) ? entry.sensor_config : [];
    // sensor_config carries accel_x/accel_y/accel_z now (registry.py's
    // SensorChannel), never a bare "accel" -- that name only survives as the
    // display-only fused channel, which isn't a SensorChannel and never
    // appears here.
    const hasAccel = sensorConfig.some((c) => c.startsWith("accel"));
    const hasMic = sensorConfig.includes("mic");
    const presentChannels = ALL_CHANNELS.filter((c) => sensorConfig.includes(c));

    // Anomaly score chart leads the section (always visible, no standalone
    // number anymore).
    // The outer wrapper (not the anomaly slot below) is attachExpanded()'s
    // "does this row exist at all" anchor now, since the anomaly section
    // itself is conditional -- see its own data-role.
    let html = `<div class="motor-row__body" data-role="motor-row-body" data-node-id="${safeId}">`;
    // Hidden until a model exists: an uncommissioned node has nothing to
    // plot, and a recommission in flight would otherwise show the stale
    // pre-recommission trend while a fresh baseline is being collected.
    if (!UNCOMMISSIONED_STATUSES.has(entry.status)) {
      html += `<div class="chart-section">
        <div class="chart-section__title-row">
          <div class="chart-section__title">Anomaly score</div>
          <button type="button" class="btn-label" data-anomaly-live-toggle data-node-id="${safeId}">Live</button>
        </div>
        ${chartSlotHtml("chart-slot-anomaly", nodeId)}
      </div>`;
    }

    // Independent of the anomaly score above -- gated on last_classification
    // actually having a result, not just entry.device_type being set, so
    // this stays hidden (not an empty "no classifier trained" placeholder)
    // until a model for this device_type has actually scored a frame. The
    // classifier runs regardless of commissioning state (S1: "no
    // device_type, or type has no model -> anomaly score only").
    if (entry.last_classification) {
      html += `<div class="chart-section">
        <div class="chart-section__title">Fault classification</div>
        ${chartSlotHtml("chart-slot-classification", nodeId)}
      </div>`;
    }

    if (hasAccel) {
      html += `<div class="chart-section">
        <div class="chart-section__title">Accel spectrum</div>
        ${chartSlotHtml("chart-slot-accel-spectrum", nodeId)}
      </div>`;
    }
    if (hasMic) {
      html += `<div class="chart-section">
        <div class="chart-section__title">Mic spectrum</div>
        ${chartSlotHtml("chart-slot-mic-spectrum", nodeId)}
      </div>`;
    }

    if (hasAccel || hasMic) {
      html += `<details class="perf-tier" data-role="scalars-details" ${uiState.scalarsOpen ? "open" : ""}>
        <summary class="perf-tier__header"><span class="perf-tier__chip">Scalar values</span></summary>
        <div class="perf-tier__body">
          <div class="scalar-grid">
            ${SCALAR_STATS.map((stat) => `<div class="chart-section">
              <div class="chart-section__title">${stat.label}</div>
              ${chartSlotHtml("chart-slot-scalar", nodeId, stat.key)}
            </div>`).join("")}
          </div>
        </div>
      </details>`;
    }

    if (presentChannels.length) {
      const mode = node.waterfallMode;
      html += `<details class="perf-tier" data-role="waterfall-details" ${uiState.waterfallOpen ? "open" : ""}>
        <summary class="perf-tier__header"><span class="perf-tier__chip">Waterfall</span></summary>
        <div class="perf-tier__body">
          <div class="waterfall-toggle">
            <button type="button" class="waterfall-toggle__btn${mode === "2d" ? " is-active" : ""}" data-waterfall-toggle="2d" data-node-id="${safeId}">2D</button>
            <button type="button" class="waterfall-toggle__btn${mode === "3d" ? " is-active" : ""}" data-waterfall-toggle="3d" data-node-id="${safeId}">3D</button>
          </div>
          ${presentChannels.map((channel) => `<div class="chart-section"><div class="chart-section__title">${escapeAttr(channel)}</div>${chartSlotHtml("chart-slot-waterfall", nodeId, channel)}</div>`).join("")}
        </div>
      </details>`;
    }

    html += `</div>`;
    return html;
  }

  // ---------------------------------------------------------------------
  // Startup
  // ---------------------------------------------------------------------

  function wireWaterfallToggle() {
    document.getElementById("fleet-list").addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-waterfall-toggle]");
      if (!btn) return;
      setWaterfallMode(btn.dataset.nodeId, btn.dataset.waterfallToggle);
      // detailBodyHtml() only bakes the is-active class in at render time --
      // setWaterfallMode() alone doesn't touch this markup (it just swaps
      // the chart), so update the pressed-state here directly or the pills
      // silently show the stale mode until some unrelated re-render happens.
      const group = btn.closest(".waterfall-toggle");
      if (group) {
        group.querySelectorAll("button[data-waterfall-toggle]").forEach((b) => {
          b.classList.toggle("is-active", b === btn);
        });
      }
    });
  }

  // Jumps back to the live tail on click -- the counterpart to
  // onAnomalyRelayout pinning the view when the user drags the rangeslider
  // away from "now". Marks the node dirty so the next flush() picks up
  // anomalyLive's new value and redraws with the live range immediately,
  // rather than waiting on the next real data point.
  function wireAnomalyLiveToggle() {
    document.getElementById("fleet-list").addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-anomaly-live-toggle]");
      if (!btn) return;
      const nodeId = btn.dataset.nodeId;
      const node = ensureNode(nodeId);
      node.anomalyLive = true;
      node.anomalyPinnedRange = null;
      dirty.add(nodeId);
    });
  }

  async function init(onRegistryPush, onPerfStats, onTelegramSubscribers, onEiProgress) {
    registryHandler = onRegistryPush || null;
    perfHandler = onPerfStats || null;
    alertsHandler = onTelegramSubscribers || null;
    classifierHandler = onEiProgress || null;
    connectWs();
    wireWaterfallToggle();
    wireAnomalyLiveToggle();
    startRenderLoop();
  }

  return { init, onNodesPolled, detailBodyHtml, attachExpanded };
})();

window.Charts = Charts;
