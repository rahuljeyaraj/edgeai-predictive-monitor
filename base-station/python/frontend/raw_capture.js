"use strict";
/*
 * Live view for tools/raw_capture_server.py (docs/SENSOR_TELEMETRY_FRAME_PLAN.md):
 * accel x/y/z spectrum, mic spectrum, raw time-domain, and 6 rolling scalar
 * trends (rms/kurtosis per accel axis), fed entirely over /ws. Standalone
 * page (not part of index.html's Fleet/Network/Performance tab switcher --
 * this only makes sense while the firmware is in FUSER_RAW_CAPTURE_MODE, an
 * exclusive alternative to the normal live dashboard, not a tab alongside it).
 *
 * Same dark-theme constants/layout helpers as charts.js (PAPER_BG/AXIS_COLORS/
 * darkLayoutBase/axisBase/smallFont, SPECTRUM_CONFIG) -- duplicated rather
 * than imported since charts.js's module scope is built entirely around its
 * per-node fleet buffers, which don't apply here (one local fuser, no nodes).
 *
 * Two different Plotly update strategies, deliberately:
 *   - Spectrum/raw-time-domain charts: Plotly.newPlot once, then Plotly.react
 *     on every arrival (same as charts.js) -- each new window fully replaces
 *     what's on screen.
 *   - The 6 scalar trend charts: Plotly.extendTraces with a bounded
 *     maxPoints ring buffer -- the correct no-lag primitive for a
 *     continuously-growing trend rather than a full-frame replace.
 */
(function () {
  const PAPER_BG = "#0f172a";
  const PLOT_BG = "#0f172a";
  const GRID_COLOR = "#1e293b";
  const AXIS_COLOR = "#334155";
  const TEXT_COLOR = "#94a3b8";
  const FONT_FAMILY = "system-ui, -apple-system, 'Segoe UI', sans-serif";
  const SPECTRUM_CONFIG = { displaylogo: false, responsive: true, displayModeBar: false };
  const MAX_SCALAR_POINTS = 300;

  const AXIS_COLORS = {
    accel_x_raw: "#3987e5", accel_y_raw: "#9085e9", accel_z_raw: "#d55181",
    mic_raw: "#d95926",
  };
  const AXIS_SUFFIX = { accel_x_raw: "x", accel_y_raw: "y", accel_z_raw: "z" };
  const ACCEL_CHANNELS = ["accel_x_raw", "accel_y_raw", "accel_z_raw"];

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
      // Legend anchored above the plot area (not Plotly's bottom default) --
      // a bottom legend collides with the x-axis title text at this chart
      // height, since this trimmed vendor bundle's automargin doesn't
      // reserve extra room for both.
      margin: { l: 52, r: 16, t: 28, b: 28 },
      showlegend: true,
      legend: { font: smallFont(), orientation: "h", x: 0, y: 1.22, xanchor: "left" },
    };
  }

  // Live-preview windows (raw samples + downsampled spectrum), keyed by
  // channel name -- always the LATEST arrival, whether or not a labeled
  // recording is active (an operator should be able to eyeball signal
  // quality before ever hitting Start).
  const latest = {}; // channel -> { fs, samples, spectrum }

  const els = {
    accelSpectrum: document.getElementById("chart-accel-spectrum"),
    micSpectrum: document.getElementById("chart-mic-spectrum"),
    rawAccel: document.getElementById("chart-raw-accel"),
    rawMic: document.getElementById("chart-raw-mic"),
    rms: document.getElementById("chart-rms"),
    kurtosis: document.getElementById("chart-kurtosis"),
  };

  const mounted = { accelSpectrum: false, micSpectrum: false, rawAccel: false, rawMic: false,
                     scalars: false };

  let scalarT0 = null;

  // ---------------------------------------------------------------------
  // Spectrum + raw time-domain (react-per-arrival)
  // ---------------------------------------------------------------------

  function buildAccelSpectrumFigure() {
    const traces = ACCEL_CHANNELS.filter((name) => latest[name]).map((name) => {
      const { spectrum, fs, samples } = latest[name];
      const freqStep = samples.length ? fs / samples.length : 1;
      return {
        type: "scatter", mode: "lines", name,
        x: spectrum.map((_, k) => k * freqStep), y: spectrum,
        line: { color: AXIS_COLORS[name], width: 1.5 },
      };
    });
    const layout = {
      ...darkLayoutBase(), uirevision: "accel-spectrum", height: 240,
      xaxis: axisBase({ title: { text: "Frequency (Hz)", font: smallFont() } }),
      yaxis: axisBase({ title: { text: "Normalized magnitude", font: smallFont() } }),
    };
    return [traces, layout];
  }

  function buildMicSpectrumFigure() {
    const traces = [];
    if (latest.mic_raw) {
      const { spectrum, fs, samples } = latest.mic_raw;
      const freqStep = samples.length ? fs / samples.length : 1;
      traces.push({
        type: "scatter", mode: "lines", name: "mic",
        x: spectrum.map((_, k) => k * freqStep), y: spectrum,
        line: { color: AXIS_COLORS.mic_raw, width: 1.5 },
      });
    }
    const layout = {
      ...darkLayoutBase(), uirevision: "mic-spectrum", height: 240, showlegend: false,
      xaxis: axisBase({ title: { text: "Frequency (Hz)", font: smallFont() } }),
      yaxis: axisBase({ title: { text: "Normalized magnitude", font: smallFont() } }),
    };
    return [traces, layout];
  }

  function buildRawAccelFigure() {
    const traces = ACCEL_CHANNELS.filter((name) => latest[name]).map((name) => {
      const { fs, samples } = latest[name];
      return {
        type: "scatter", mode: "lines", name,
        x: samples.map((_, k) => k / fs), y: samples,
        line: { color: AXIS_COLORS[name], width: 1 },
      };
    });
    const layout = {
      ...darkLayoutBase(), uirevision: "raw-accel", height: 200,
      xaxis: axisBase({ title: { text: "Time (s)", font: smallFont() } }),
      yaxis: axisBase({}),
    };
    return [traces, layout];
  }

  function buildRawMicFigure() {
    const traces = [];
    if (latest.mic_raw) {
      const { fs, samples } = latest.mic_raw;
      traces.push({
        type: "scatter", mode: "lines", name: "mic",
        x: samples.map((_, k) => k / fs), y: samples,
        line: { color: AXIS_COLORS.mic_raw, width: 1 },
      });
    }
    const layout = {
      ...darkLayoutBase(), uirevision: "raw-mic", height: 200, showlegend: false,
      xaxis: axisBase({ title: { text: "Time (s)", font: smallFont() } }),
      yaxis: axisBase({}),
    };
    return [traces, layout];
  }

  function redrawAccel() {
    const [traces, layout] = buildAccelSpectrumFigure();
    if (!mounted.accelSpectrum) {
      Plotly.newPlot(els.accelSpectrum, traces, layout, SPECTRUM_CONFIG);
      mounted.accelSpectrum = true;
    } else {
      Plotly.react(els.accelSpectrum, traces, layout, SPECTRUM_CONFIG);
    }
    const [rawTraces, rawLayout] = buildRawAccelFigure();
    if (!mounted.rawAccel) {
      Plotly.newPlot(els.rawAccel, rawTraces, rawLayout, SPECTRUM_CONFIG);
      mounted.rawAccel = true;
    } else {
      Plotly.react(els.rawAccel, rawTraces, rawLayout, SPECTRUM_CONFIG);
    }
  }

  function redrawMic() {
    const [traces, layout] = buildMicSpectrumFigure();
    if (!mounted.micSpectrum) {
      Plotly.newPlot(els.micSpectrum, traces, layout, SPECTRUM_CONFIG);
      mounted.micSpectrum = true;
    } else {
      Plotly.react(els.micSpectrum, traces, layout, SPECTRUM_CONFIG);
    }
    const [rawTraces, rawLayout] = buildRawMicFigure();
    if (!mounted.rawMic) {
      Plotly.newPlot(els.rawMic, rawTraces, rawLayout, SPECTRUM_CONFIG);
      mounted.rawMic = true;
    } else {
      Plotly.react(els.rawMic, rawTraces, rawLayout, SPECTRUM_CONFIG);
    }
  }

  function handleRawWindow(msg) {
    latest[msg.channel] = { fs: msg.fs, samples: msg.samples, spectrum: msg.spectrum };
    if (ACCEL_CHANNELS.includes(msg.channel)) {
      redrawAccel();
    } else if (msg.channel === "mic_raw") {
      redrawMic();
    }
  }

  // ---------------------------------------------------------------------
  // Scalar trends (extendTraces ring buffer -- see file docstring)
  // ---------------------------------------------------------------------

  function mountScalarCharts() {
    const rmsTraces = ACCEL_CHANNELS.map((name) => ({
      type: "scatter", mode: "lines", name: `rms_${AXIS_SUFFIX[name]}`,
      x: [], y: [], line: { color: AXIS_COLORS[name], width: 1.5 },
    }));
    const kurtosisTraces = ACCEL_CHANNELS.map((name) => ({
      type: "scatter", mode: "lines", name: `kurtosis_${AXIS_SUFFIX[name]}`,
      x: [], y: [], line: { color: AXIS_COLORS[name], width: 1.5 },
    }));
    const rmsLayout = {
      ...darkLayoutBase(), uirevision: "rms", height: 200,
      xaxis: axisBase({ title: { text: "Time since Start (s)", font: smallFont() } }),
      yaxis: axisBase({}),
    };
    const kurtosisLayout = {
      ...darkLayoutBase(), uirevision: "kurtosis", height: 200,
      xaxis: axisBase({ title: { text: "Time since Start (s)", font: smallFont() } }),
      yaxis: axisBase({}),
    };
    Plotly.newPlot(els.rms, rmsTraces, rmsLayout, SPECTRUM_CONFIG);
    Plotly.newPlot(els.kurtosis, kurtosisTraces, kurtosisLayout, SPECTRUM_CONFIG);
    mounted.scalars = true;
    scalarT0 = null;
  }

  function handleRawScalars(msg) {
    if (!mounted.scalars) mountScalarCharts();
    if (scalarT0 === null) scalarT0 = msg.t;
    const t = msg.t - scalarT0;

    const rmsUpdate = { x: ACCEL_CHANNELS.map(() => [t]), y: [] };
    const kurtosisUpdate = { x: ACCEL_CHANNELS.map(() => [t]), y: [] };
    ACCEL_CHANNELS.forEach((name) => {
      const axis = AXIS_SUFFIX[name];
      rmsUpdate.y.push([msg.scalars[`rms_${axis}`]]);
      kurtosisUpdate.y.push([msg.scalars[`kurtosis_${axis}`]]);
    });

    const traceIndices = ACCEL_CHANNELS.map((_, i) => i);
    Plotly.extendTraces(els.rms, rmsUpdate, traceIndices, MAX_SCALAR_POINTS);
    Plotly.extendTraces(els.kurtosis, kurtosisUpdate, traceIndices, MAX_SCALAR_POINTS);
  }

  // ---------------------------------------------------------------------
  // Capture control (label/Start/Stop) + status
  // ---------------------------------------------------------------------

  const labelInput = document.getElementById("capture-label");
  const startBtn = document.getElementById("capture-start");
  const stopBtn = document.getElementById("capture-stop");
  const dotEl = document.getElementById("capture-dot");
  const statusTextEl = document.getElementById("capture-status-text");
  const countsEl = document.getElementById("capture-counts");

  let localStartedAt = null;
  let tickHandle = null;
  let wasRecording = false;

  function renderCounts(counts) {
    const parts = Object.entries(counts).map(([name, n]) => `${name}: ${n}`);
    countsEl.textContent = parts.join("   ");
  }

  function applyStatus(status) {
    startBtn.disabled = status.recording;
    stopBtn.disabled = !status.recording;
    labelInput.disabled = status.recording;
    dotEl.classList.toggle("capture-dot--active", status.recording);
    renderCounts(status.counts || {});

    if (status.recording) {
      // A fresh run: reset the scalar trend charts so each labeled run's
      // rms/kurtosis trend starts from a clean 0s baseline instead of
      // appending onto whatever the previous run left on screen.
      if (!wasRecording) mountScalarCharts();
      if (localStartedAt === null) localStartedAt = Date.now() - (status.elapsed_s || 0) * 1000;
      if (!tickHandle) tickHandle = setInterval(tickElapsed, 1000);
      tickElapsed();
    } else {
      localStartedAt = null;
      if (tickHandle) { clearInterval(tickHandle); tickHandle = null; }
      statusTextEl.textContent = "Idle";
    }
    wasRecording = status.recording;
  }

  function tickElapsed() {
    if (localStartedAt === null) return;
    const elapsed = Math.floor((Date.now() - localStartedAt) / 1000);
    statusTextEl.textContent = `Recording "${labelInput.value.trim()}" -- ${elapsed}s`;
  }

  async function startCapture() {
    const label = labelInput.value.trim();
    if (!label) {
      window.alert("Enter a label first (e.g. healthy, imbalance).");
      return;
    }
    const resp = await fetch("/capture/start", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ label }),
    });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      window.alert(`Couldn't start: ${body.error || resp.statusText}`);
      return;
    }
    applyStatus(await resp.json());
  }

  async function stopCapture() {
    const resp = await fetch("/capture/stop", { method: "POST" });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      window.alert(`Couldn't stop: ${body.error || resp.statusText}`);
      return;
    }
    const summary = await resp.json();
    applyStatus({ recording: false, label: null, elapsed_s: null, counts: summary.counts });
    if (summary.path) {
      window.alert(`Saved ${summary.total_windows} windows -> ${summary.path}`);
    } else {
      window.alert("Stopped -- no windows were captured (was the sketch actually sending data?).");
    }
  }

  startBtn.addEventListener("click", startCapture);
  stopBtn.addEventListener("click", stopCapture);

  // ---------------------------------------------------------------------
  // WebSocket
  // ---------------------------------------------------------------------

  function connectWs() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${location.host}/ws`);
    ws.onmessage = (ev) => {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch (err) {
        return;
      }
      if (msg.type === "raw_window") {
        handleRawWindow(msg);
      } else if (msg.type === "raw_scalars") {
        handleRawScalars(msg);
      } else if (msg.type === "capture_status") {
        applyStatus(msg);
      }
    };
    ws.onclose = () => setTimeout(connectWs, 1000);
  }

  fetch("/capture/status").then((r) => r.json()).then(applyStatus).catch(() => {});
  connectWs();
})();
