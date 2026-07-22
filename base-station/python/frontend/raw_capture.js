"use strict";
/*
 * Live view for tools/raw_capture_server.py (docs/SENSOR_TELEMETRY_FRAME_PLAN.md):
 * accel x/y/z spectrum, mic spectrum, raw time-domain, and 6 rolling scalar
 * trends (rms/kurtosis/crest_factor/peak/std/skewness, on the combined
 * tri-axial vector magnitude -- same math as fuser.cpp's normal-mode scalar
 * tiles), fed entirely over /ws. Standalone
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
  const ACCEL_CHANNELS = ["accel_x_raw", "accel_y_raw", "accel_z_raw"];

  // The 6 scalar tiles raw_capture_server.py computes on the combined
  // tri-axial vector magnitude (same math + same input signal as fuser.cpp's
  // compute_scalars(), normal mode's on-device equivalent) -- split into an
  // amplitude group (same units as the raw signal) and a shape group
  // (dimensionless), since plotting all 6 on one y-axis would let rms/peak/std
  // swamp crest_factor/kurtosis/skewness's much smaller range.
  const AMPLITUDE_SCALARS = ["rms", "peak", "std"];
  const SHAPE_SCALARS = ["crest_factor", "kurtosis", "skewness"];
  const SCALAR_COLORS = {
    rms: "#3987e5", peak: "#9085e9", std: "#d55181",
    crest_factor: "#3987e5", kurtosis: "#9085e9", skewness: "#d55181",
  };

  // raw_features.py's downsample() average-pools fft_magnitude()'s raw,
  // DC-dropped bins (indices 0..N/2-1, true frequency (i+1)*fs/N each) into
  // `spectrum.length` buckets of `factor` raw bins apiece. A displayed
  // bucket's representative frequency is that group's mean true frequency,
  // NOT k*fs/samples.length (that formula ignores both the pooling factor
  // and the DC-drop offset, badly compressing/shifting the axis whenever
  // factor > 1 -- e.g. it showed accel's spectrum as 0-388Hz instead of the
  // real 0-6400Hz Nyquist range at the old bin-count=32 default).
  // uniqueBinFraction: what fraction of sampleLen the spectrum's raw (pre-
  // downsample) bins were drawn from. Accel uses the default 0.5 (all
  // fft_magnitude() unique bins, full 0..Nyquist range). Mic passes 0.25 --
  // raw_capture_server.py's mic_useful_magnitude() already trims to the
  // first quarter-FFT (half the unique bins, up to Fs/4) before downsampling
  // (mic_sampler.cpp's own MIC_FFT_BIN_COUNT=MIC_FFT_LEN/4 convention, an
  // aliasing image fills everything above that with no external MCLK) --
  // this must match or the x-axis mislabels 0..24kHz data as 0..48kHz.
  function spectrumFreqAxis(spectrumLen, sampleLen, fs, uniqueBinFraction = 0.5) {
    if (!sampleLen || !spectrumLen) return [];
    const rawLen = sampleLen * uniqueBinFraction;
    const factor = rawLen / spectrumLen;
    const freqPerRawBin = fs / sampleLen;
    return Array.from({ length: spectrumLen },
      (_, k) => ((factor * (2 * k + 1) + 1) / 2) * freqPerRawBin);
  }

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
      return {
        type: "scatter", mode: "lines", name,
        x: spectrumFreqAxis(spectrum.length, samples.length, fs), y: spectrum,
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
      traces.push({
        type: "scatter", mode: "lines", name: "mic",
        x: spectrumFreqAxis(spectrum.length, samples.length, fs, 0.25), y: spectrum,
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

  // All 3 accel axes ride one batched message (raw_capture_server.py sends
  // one "raw_accel_epoch" per epoch, not one "raw_window" per axis, so a
  // redraw happens once per epoch instead of 3x) -- distinct handler/shape
  // from handleRawWindow's single-channel "raw_window" messages above.
  function handleRawAccelEpoch(msg) {
    ACCEL_CHANNELS.forEach((name) => {
      const payload = msg.channels[name];
      if (payload) {
        latest[name] = { fs: payload.fs, samples: payload.samples, spectrum: payload.spectrum };
      }
    });
    redrawAccel();
  }

  // ---------------------------------------------------------------------
  // Scalar trends (extendTraces ring buffer -- see file docstring)
  // ---------------------------------------------------------------------

  function mountScalarCharts() {
    const amplitudeTraces = AMPLITUDE_SCALARS.map((name) => ({
      type: "scatter", mode: "lines", name,
      x: [], y: [], line: { color: SCALAR_COLORS[name], width: 1.5 },
    }));
    const shapeTraces = SHAPE_SCALARS.map((name) => ({
      type: "scatter", mode: "lines", name,
      x: [], y: [], line: { color: SCALAR_COLORS[name], width: 1.5 },
    }));
    const amplitudeLayout = {
      ...darkLayoutBase(), uirevision: "amplitude-scalars", height: 200,
      xaxis: axisBase({ title: { text: "Time since Start (s)", font: smallFont() } }),
      yaxis: axisBase({}),
    };
    const shapeLayout = {
      ...darkLayoutBase(), uirevision: "shape-scalars", height: 200,
      xaxis: axisBase({ title: { text: "Time since Start (s)", font: smallFont() } }),
      yaxis: axisBase({}),
    };
    Plotly.newPlot(els.rms, amplitudeTraces, amplitudeLayout, SPECTRUM_CONFIG);
    Plotly.newPlot(els.kurtosis, shapeTraces, shapeLayout, SPECTRUM_CONFIG);
    mounted.scalars = true;
    scalarT0 = null;
  }

  function handleRawScalars(msg) {
    if (!mounted.scalars) mountScalarCharts();
    if (scalarT0 === null) scalarT0 = msg.t;
    const t = msg.t - scalarT0;

    const amplitudeUpdate = { x: AMPLITUDE_SCALARS.map(() => [t]), y: [] };
    const shapeUpdate = { x: SHAPE_SCALARS.map(() => [t]), y: [] };
    AMPLITUDE_SCALARS.forEach((name) => amplitudeUpdate.y.push([msg.scalars[name]]));
    SHAPE_SCALARS.forEach((name) => shapeUpdate.y.push([msg.scalars[name]]));

    const amplitudeIndices = AMPLITUDE_SCALARS.map((_, i) => i);
    const shapeIndices = SHAPE_SCALARS.map((_, i) => i);
    Plotly.extendTraces(els.rms, amplitudeUpdate, amplitudeIndices, MAX_SCALAR_POINTS);
    Plotly.extendTraces(els.kurtosis, shapeUpdate, shapeIndices, MAX_SCALAR_POINTS);
  }

  // ---------------------------------------------------------------------
  // Capture control (label/Start/Stop) + status
  // ---------------------------------------------------------------------

  const labelInput = document.getElementById("capture-label");
  const durationInput = document.getElementById("capture-duration");
  const startBtn = document.getElementById("capture-start");
  const stopBtn = document.getElementById("capture-stop");
  const dotEl = document.getElementById("capture-dot");
  const statusTextEl = document.getElementById("capture-status-text");
  const countsEl = document.getElementById("capture-counts");

  let localStartedAt = null;
  let tickHandle = null;
  let wasRecording = false;
  let autoStopHandle = null;

  function clearAutoStop() {
    if (autoStopHandle) { clearTimeout(autoStopHandle); autoStopHandle = null; }
  }

  function renderCounts(counts) {
    const parts = Object.entries(counts).map(([name, n]) => `${name}: ${n}`);
    countsEl.textContent = parts.join("   ");
  }

  function applyStatus(status) {
    startBtn.disabled = status.recording;
    stopBtn.disabled = !status.recording;
    labelInput.disabled = status.recording;
    durationInput.disabled = status.recording;
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
      if (!wasRecording) scheduleAutoStop(status.elapsed_s || 0);
    } else {
      localStartedAt = null;
      if (tickHandle) { clearInterval(tickHandle); tickHandle = null; }
      clearAutoStop();
      statusTextEl.textContent = "Idle";
    }
    wasRecording = status.recording;
  }

  function tickElapsed() {
    if (localStartedAt === null) return;
    const elapsed = Math.floor((Date.now() - localStartedAt) / 1000);
    statusTextEl.textContent = `Recording "${labelInput.value.trim()}" -- ${elapsed}s`;
  }

  // Auto-stop: purely a client-side timer that calls the same stopCapture()
  // a manual click would -- the server has no notion of a duration limit.
  // `alreadyElapsed` accounts for a page reload mid-recording (status carries
  // elapsed_s), so the remaining time is duration - alreadyElapsed, not the
  // full duration again.
  function scheduleAutoStop(alreadyElapsed) {
    clearAutoStop();
    const duration = parseFloat(durationInput.value);
    if (!duration || duration <= 0) return;
    const remaining = duration - alreadyElapsed;
    if (remaining <= 0) {
      stopCapture();
      return;
    }
    autoStopHandle = setTimeout(() => {
      autoStopHandle = null;
      stopCapture();
    }, remaining * 1000);
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
      } else if (msg.type === "raw_accel_epoch") {
        handleRawAccelEpoch(msg);
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
