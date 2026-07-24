"use strict";
/*
 * Classifier tab (docs/EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md S3) -- table
 * of every locally-saved capture (pipeline/capture.py's Record drawer
 * output, GET /captures), with select/rename/delete on-disk management.
 *
 * On-disk side: list, select, rename (moves a batch into a different
 * label bucket via POST /captures/rename), delete (POST /captures/delete).
 *
 * The Edge Impulse panel (S4's "Upload" round) is now wired: one row per
 * device type with a connect/not-connected state (GET /classifier/ei/status),
 * a login form per row that creates that type's EI project on first use
 * (POST /classifier/ei/connect -- username/password, not a static API key,
 * since project *creation* needs account-level auth; see docs/
 * EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md S0), and "Upload selected" (POST
 * /classifier/ei/upload). Train/Fetch trained model stay disabled
 * placeholders -- S4 steps 5-9, a follow-up round (async job + WS log
 * streaming, a different shape from this round's request/response calls).
 *
 * Same module shape as perf.js/alerts.js: owns its own data (fetches
 * /captures itself rather than reading another module's state), no shared
 * WebSocket wiring needed since nothing here is a live telemetry stream --
 * a plain refetch after every mutation is enough.
 */

const Classifier = (() => {
  // Same trash glyph as the Fleet tab's decommission button (app.js's
  // ICON_TRASH) -- duplicated here rather than read off `window` so this
  // module stays self-contained regardless of script load order (same
  // "each module owns its own small helpers" precedent as alerts.js/
  // charts.js each defining their own escapeAttr). A crisp vector path
  // instead of the emoji glyphs (✎/🗑) used in the first round, which
  // rendered pixelated and, for the pencil, pointing the wrong way.
  const ICON_TRASH = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>';
  const ICON_PENCIL = '<svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor"><path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04a1 1 0 0 0 0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/></svg>';

  const state = {
    captures: [],           // [{id, node_id, device_type, label, timestamp, frame_count}]
    selected: new Set(),    // capture ids
    eiStatus: {},           // {device_type: connected(bool)}
    connecting: null,       // device_type currently showing its login form, or null
    connectForm: { username: "", password: "", totp: "", needsTotp: false, error: null, busy: false },
    uploadResult: null,     // last POST /classifier/ei/upload response, or null
  };
  let editingId = null;
  let editValue = "";

  function escapeHtmlLocal(str) {
    return String(str)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function escapeAttr(str) {
    return String(str).replace(/"/g, "&quot;");
  }

  async function postJson(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || err.error || `${res.status}`);
    }
    return res.json();
  }

  // /classifier/ei/connect's 400 for "needs a 2FA code" carries a
  // structured `{totp_required: true}` error object, not a string --
  // postJson's generic Error(err.detail || err.error) would stringify an
  // object to "[object Object]", losing the signal. This one route gets
  // its own thin fetch wrapper instead of overloading postJson's contract.
  // Note: api/app.py's exception_handler rewrites every HTTPException body
  // to {"error": ...}, NOT FastAPI's default {"detail": ...} -- postJson's
  // `err.detail || err.error` already accounts for this app-wide
  // convention; this helper only needs the `error` key.
  async function postEiConnect(body) {
    const res = await fetch("/classifier/ei/connect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const responseBody = await res.json().catch(() => ({}));
    if (!res.ok) {
      const error = responseBody.error;
      if (error && typeof error === "object" && error.totp_required) {
        const err = new Error("2FA code required");
        err.totpRequired = true;
        throw err;
      }
      throw new Error((typeof error === "string" && error) || `${res.status}`);
    }
    return responseBody;
  }

  // ---------------------------------------------------------------------
  // Samples table
  // ---------------------------------------------------------------------

  function sampleRowHtml(entry) {
    const isEditing = editingId === entry.id;
    const checked = state.selected.has(entry.id);
    const recorded = entry.timestamp
      ? new Date(entry.timestamp * 1000).toLocaleString() : "–";

    const labelCell = isEditing
      ? `<span class="classifier-table__rename">
          <input type="text" class="classifier-table__rename-input" value="${escapeAttr(editValue)}"
                 data-action="rename_input" autocomplete="off">
          <button type="button" class="btn-icon" data-action="rename_confirm" title="Save">✓</button>
          <button type="button" class="btn-icon" data-action="rename_cancel" title="Cancel">✕</button>
        </span>`
      : escapeHtmlLocal(entry.label);

    return `<tr class="classifier-table__row" data-id="${escapeAttr(entry.id)}">
      <td><input type="checkbox" data-action="select" ${checked ? "checked" : ""}></td>
      <td>${escapeHtmlLocal(entry.node_id || "–")}</td>
      <td>${entry.device_type ? escapeHtmlLocal(entry.device_type) : `<span class="classifier-table__muted">unset</span>`}</td>
      <td>${labelCell}</td>
      <td>${entry.frame_count}</td>
      <td>${recorded}</td>
      <td class="classifier-table__actions">
        ${isEditing ? "" : `<button type="button" class="btn-icon" data-action="rename" title="Rename label" aria-label="Rename label">${ICON_PENCIL}</button>`}
        <button type="button" class="btn-icon btn-icon--danger" data-action="delete" title="Delete" aria-label="Delete">${ICON_TRASH}</button>
      </td>
    </tr>`;
  }

  function toolbarHtml() {
    const total = state.captures.length;
    const selectedCount = state.selected.size;
    const allSelected = total > 0 && selectedCount === total;
    return `<div class="classifier-toolbar">
      <label class="classifier-toolbar__all">
        <input type="checkbox" data-action="select_all" ${allSelected ? "checked" : ""} ${total === 0 ? "disabled" : ""}>
        Select all
      </label>
      <span class="classifier-toolbar__count">${selectedCount} selected</span>
      <button type="button" class="btn-label" data-action="delete_selected" ${selectedCount === 0 ? "disabled" : ""}>
        Delete selected
      </button>
    </div>`;
  }

  function renderSamples() {
    const el = document.getElementById("classifier-samples");
    if (!el) return;

    if (state.captures.length === 0) {
      el.innerHTML = `<div class="perf-card">
        <div class="perf-empty">No recordings yet. Use Record on a node in the Fleet tab to save labeled samples.</div>
      </div>`;
      return;
    }

    el.innerHTML = `<div class="perf-card classifier-table-card">
      ${toolbarHtml()}
      <div class="classifier-table__wrap">
        <table class="classifier-table">
          <thead>
            <tr>
              <th></th><th>Device</th><th>Asset class</th><th>Label</th><th>Frames</th><th>Recorded</th><th></th>
            </tr>
          </thead>
          <tbody>${state.captures.map(sampleRowHtml).join("")}</tbody>
        </table>
      </div>
    </div>`;

    if (editingId !== null) {
      const input = el.querySelector(".classifier-table__rename-input");
      if (input) {
        input.focus();
        input.setSelectionRange(input.value.length, input.value.length);
      }
    }
  }

  // ---------------------------------------------------------------------
  // Edge Impulse panel -- one row per device type (S4 "Upload" round).
  // ---------------------------------------------------------------------

  function deviceTypesInView() {
    // Union of device types already assigned to some node (via saved
    // captures) and any type /classifier/ei/status already knows about --
    // covers a type that's been connected but has no captures selected
    // right now, not just types currently visible in the samples table.
    const types = new Set();
    state.captures.forEach((c) => { if (c.device_type) types.add(c.device_type); });
    Object.keys(state.eiStatus).forEach((t) => types.add(t));
    return Array.from(types).sort();
  }

  function connectFormHtml(deviceType) {
    const f = state.connectForm;
    return `<div class="classifier-ei__connect">
      <input type="text" class="classifier-table__rename-input" placeholder="Edge Impulse username or email"
             data-action="ei_username" value="${escapeAttr(f.username)}" autocomplete="off" ${f.busy ? "disabled" : ""}>
      <input type="password" class="classifier-table__rename-input" placeholder="Password"
             data-action="ei_password" value="${escapeAttr(f.password)}" autocomplete="off" ${f.busy ? "disabled" : ""}>
      ${f.needsTotp ? `<input type="text" class="classifier-table__rename-input" placeholder="2FA code"
             data-action="ei_totp" value="${escapeAttr(f.totp)}" autocomplete="off" ${f.busy ? "disabled" : ""}>` : ""}
      <button type="button" class="btn-primary" data-action="ei_connect_submit"
              data-type="${escapeAttr(deviceType)}" ${f.busy ? "disabled" : ""}>
        ${f.busy ? "Connecting…" : "Connect"}
      </button>
      <button type="button" class="btn-text" data-action="ei_connect_cancel">Cancel</button>
      ${f.error ? `<div class="classifier-ei__error">${escapeHtmlLocal(f.error)}</div>` : ""}
    </div>`;
  }

  function eiRowHtml(deviceType) {
    const connected = !!state.eiStatus[deviceType];
    const isConnecting = state.connecting === deviceType;
    return `<div class="classifier-ei__row">
        <span class="classifier-ei__type">${escapeHtmlLocal(deviceType)}</span>
        <span class="classifier-ei__pill ${connected ? "classifier-ei__pill--connected" : ""}">
          ${connected ? "Connected" : "Not connected"}
        </span>
        ${connected || isConnecting ? "" : `<button type="button" class="btn-label"
          data-action="ei_connect_start" data-type="${escapeAttr(deviceType)}">Connect</button>`}
      </div>
      ${isConnecting ? connectFormHtml(deviceType) : ""}`;
  }

  function uploadResultHtml(result) {
    const lines = [];
    Object.entries(result.uploaded || {}).forEach(([deviceType, byLabel]) => {
      Object.entries(byLabel).forEach(([label, counts]) => {
        lines.push(`${escapeHtmlLocal(deviceType)} / ${escapeHtmlLocal(label)}: `
          + `${counts.training} training + ${counts.testing} testing`);
      });
    });
    const notes = [...(result.warnings || []), ...Object.values(result.rejected || {})];
    if (lines.length === 0 && notes.length === 0) return "";
    return `<div class="classifier-ei__result">
      ${lines.map((l) => `<div>${l}</div>`).join("")}
      ${notes.map((n) => `<div class="classifier-ei__warning">${escapeHtmlLocal(n)}</div>`).join("")}
    </div>`;
  }

  function renderEi() {
    const el = document.getElementById("classifier-ei");
    if (!el) return;
    const selectedCount = state.selected.size;
    const types = deviceTypesInView();
    const selectedTypes = new Set(state.captures
      .filter((c) => state.selected.has(c.id) && c.device_type)
      .map((c) => c.device_type));
    const missing = Array.from(selectedTypes).filter((t) => !state.eiStatus[t]);
    const uploadDisabled = selectedCount === 0 || missing.length > 0;
    const uploadTitle = missing.length > 0 ? `Connect ${missing.join(", ")} first` : "";

    el.innerHTML = `<div class="perf-card">
      <div class="alerts-connect__title">Edge Impulse</div>
      <div class="perf-chart__caption">
        Push selected recordings to Edge Impulse for classifier training, and pull a
        trained model back down.
      </div>
      ${types.length === 0
        ? `<div class="perf-empty">No asset classes assigned yet -- set one on a node in the Fleet tab first.</div>`
        : types.map(eiRowHtml).join("")}
      <div class="classifier-ei__row classifier-ei__actions">
        <button type="button" class="btn-label btn-label--ready" data-action="ei_upload"
                ${uploadDisabled ? "disabled" : ""} title="${escapeAttr(uploadTitle)}">
          Upload selected (${selectedCount})
        </button>
        <button type="button" class="btn-label" disabled>Train</button>
        <button type="button" class="btn-label" disabled>Fetch trained model</button>
      </div>
      ${state.uploadResult ? uploadResultHtml(state.uploadResult) : ""}
    </div>`;
  }

  function render() {
    renderSamples();
    renderEi();
  }

  // ---------------------------------------------------------------------
  // Actions
  // ---------------------------------------------------------------------

  async function refreshCaptures() {
    try {
      const res = await fetch("/captures");
      const body = await res.json();
      state.captures = body.captures || [];
      const liveIds = new Set(state.captures.map((c) => c.id));
      state.selected.forEach((id) => { if (!liveIds.has(id)) state.selected.delete(id); });
    } catch (err) {
      console.error("Failed to fetch /captures", err);
    }
  }

  async function refreshEiStatus() {
    try {
      const res = await fetch("/classifier/ei/status");
      const body = await res.json();
      state.eiStatus = body.device_types || {};
    } catch (err) {
      console.error("Failed to fetch /classifier/ei/status", err);
    }
  }

  async function refresh() {
    await Promise.all([refreshCaptures(), refreshEiStatus()]);
    render();
  }

  async function deleteIds(ids) {
    if (ids.length === 0) return;
    const label = ids.length === 1 ? "this recording" : `${ids.length} recordings`;
    if (!confirm(`Delete ${label}? This can't be undone.`)) return;
    try {
      await postJson("/captures/delete", { ids });
    } catch (err) {
      alert(`Delete failed: ${err.message}`);
    }
    await refresh();
  }

  async function confirmRename(id) {
    const newLabel = editValue.trim();
    editingId = null;
    if (!newLabel) { render(); return; }
    try {
      await postJson("/captures/rename", { id, label: newLabel });
    } catch (err) {
      alert(`Rename failed: ${err.message}`);
    }
    await refresh();
  }

  // ---------------------------------------------------------------------
  // Edge Impulse actions
  // ---------------------------------------------------------------------

  function startConnect(deviceType) {
    state.connecting = deviceType;
    state.connectForm = { username: "", password: "", totp: "", needsTotp: false, error: null, busy: false };
    renderEi();
  }

  function cancelConnect() {
    state.connecting = null;
    renderEi();
  }

  async function submitConnect(deviceType) {
    const f = state.connectForm;
    f.busy = true;
    f.error = null;
    renderEi();
    try {
      await postEiConnect({
        device_type: deviceType,
        username: f.username,
        password: f.password,
        totp: f.needsTotp ? f.totp : undefined,
      });
      // Success: forget the form entirely, including the password --
      // nothing about it lingers longer than this one request.
      state.connecting = null;
      state.connectForm = { username: "", password: "", totp: "", needsTotp: false, error: null, busy: false };
      await refreshEiStatus();
    } catch (err) {
      if (err.totpRequired) {
        state.connectForm = { ...f, needsTotp: true, busy: false, error: "Enter your 2FA code" };
      } else {
        // Wrong password / other failure: drop the password and let the
        // user retype it rather than holding it in state indefinitely.
        state.connectForm = { ...f, password: "", busy: false, error: err.message };
      }
    }
    renderEi();
  }

  async function uploadSelected() {
    const ids = Array.from(state.selected);
    if (ids.length === 0) return;
    try {
      state.uploadResult = await postJson("/classifier/ei/upload", { capture_ids: ids });
    } catch (err) {
      alert(`Upload failed: ${err.message}`);
    }
    await refresh();
  }

  function wireEiEvents() {
    const el = document.getElementById("classifier-ei");
    el.addEventListener("click", (e) => {
      const startBtn = e.target.closest('[data-action="ei_connect_start"]');
      if (startBtn) { startConnect(startBtn.dataset.type); return; }
      if (e.target.closest('[data-action="ei_connect_cancel"]')) { cancelConnect(); return; }
      const submitBtn = e.target.closest('[data-action="ei_connect_submit"]');
      if (submitBtn) { submitConnect(submitBtn.dataset.type); return; }
      if (e.target.closest('[data-action="ei_upload"]')) { uploadSelected(); return; }
    });

    el.addEventListener("input", (e) => {
      const usernameInput = e.target.closest('[data-action="ei_username"]');
      if (usernameInput) { state.connectForm.username = usernameInput.value; return; }
      const passwordInput = e.target.closest('[data-action="ei_password"]');
      if (passwordInput) { state.connectForm.password = passwordInput.value; return; }
      const totpInput = e.target.closest('[data-action="ei_totp"]');
      if (totpInput) { state.connectForm.totp = totpInput.value; return; }
    });

    el.addEventListener("keydown", (e) => {
      if (!e.target.closest(".classifier-ei__connect")) return;
      const deviceType = state.connecting;
      if (e.key === "Enter") { e.preventDefault(); if (deviceType) submitConnect(deviceType); }
      else if (e.key === "Escape") { e.preventDefault(); cancelConnect(); }
    });
  }

  function wireEvents() {
    const samplesEl = document.getElementById("classifier-samples");
    samplesEl.addEventListener("click", (e) => {
      const row = e.target.closest(".classifier-table__row");

      if (e.target.closest('[data-action="select_all"]')) {
        const checkbox = e.target.closest('[data-action="select_all"]');
        if (checkbox.checked) state.captures.forEach((c) => state.selected.add(c.id));
        else state.selected.clear();
        render();
        return;
      }
      if (e.target.closest('[data-action="delete_selected"]')) {
        deleteIds(Array.from(state.selected));
        return;
      }
      if (!row) return;
      const id = row.dataset.id;

      if (e.target.closest('[data-action="rename"]')) {
        const entry = state.captures.find((c) => c.id === id);
        editingId = id;
        editValue = entry ? entry.label : "";
        renderSamples();
        return;
      }
      if (e.target.closest('[data-action="rename_confirm"]')) {
        confirmRename(id);
        return;
      }
      if (e.target.closest('[data-action="rename_cancel"]')) {
        editingId = null;
        renderSamples();
        return;
      }
      if (e.target.closest('[data-action="delete"]')) {
        deleteIds([id]);
        return;
      }
    });

    samplesEl.addEventListener("change", (e) => {
      const checkbox = e.target.closest('[data-action="select"]');
      if (!checkbox) return;
      const row = e.target.closest(".classifier-table__row");
      const id = row.dataset.id;
      if (checkbox.checked) state.selected.add(id);
      else state.selected.delete(id);
      // Full render, not just renderSamples() -- the EI panel's "Upload
      // selected (n)" count/enablement depends on the selection too.
      render();
    });

    samplesEl.addEventListener("input", (e) => {
      const input = e.target.closest('[data-action="rename_input"]');
      if (!input) return;
      editValue = input.value;
    });

    samplesEl.addEventListener("keydown", (e) => {
      if (!e.target.closest('[data-action="rename_input"]')) return;
      const row = e.target.closest(".classifier-table__row");
      if (e.key === "Enter") { e.preventDefault(); confirmRename(row.dataset.id); }
      else if (e.key === "Escape") { e.preventDefault(); editingId = null; renderSamples(); }
    });
  }

  function init() {
    wireEvents();
    wireEiEvents();
    refresh();
  }

  return { init, refresh };
})();

window.Classifier = Classifier;
