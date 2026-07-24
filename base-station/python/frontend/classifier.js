"use strict";
/*
 * Classifier tab (docs/EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md S3) -- table
 * of every locally-saved capture (pipeline/capture.py's Record drawer
 * output, GET /captures), with select/rename/delete on-disk management.
 *
 * This round is deliberately UI-only for the on-disk side: list, select,
 * rename (moves a batch into a different label bucket via POST
 * /captures/rename), delete (POST /captures/delete). The Edge Impulse
 * panel below the table is a placeholder -- API key field and upload/
 * fetch-model buttons render but stay disabled, no REST calls behind them
 * yet (S4 in the plan doc is the follow-up round that wires them).
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
              <th></th><th>Device</th><th>Type</th><th>Label</th><th>Frames</th><th>Recorded</th><th></th>
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
  // Edge Impulse panel -- placeholder only this round (S4 wires it up).
  // ---------------------------------------------------------------------

  function renderEi() {
    const el = document.getElementById("classifier-ei");
    if (!el) return;
    const selectedCount = state.selected.size;
    el.innerHTML = `<div class="perf-card">
      <div class="alerts-connect__title">Edge Impulse</div>
      <div class="perf-chart__caption">
        Push selected recordings to Edge Impulse for classifier training, and pull a
        trained model back down. Not wired up yet.
      </div>
      <div class="classifier-ei__row">
        <input type="password" class="classifier-table__rename-input" placeholder="Edge Impulse API key" disabled>
        <button type="button" class="btn-label" disabled>Save key</button>
      </div>
      <div class="classifier-ei__row">
        <button type="button" class="btn-label btn-label--ready" disabled>Upload selected (${selectedCount})</button>
        <button type="button" class="btn-label" disabled>Train</button>
        <button type="button" class="btn-label" disabled>Fetch trained model</button>
      </div>
    </div>`;
  }

  function render() {
    renderSamples();
    renderEi();
  }

  // ---------------------------------------------------------------------
  // Actions
  // ---------------------------------------------------------------------

  async function refresh() {
    try {
      const res = await fetch("/captures");
      const body = await res.json();
      state.captures = body.captures || [];
      const liveIds = new Set(state.captures.map((c) => c.id));
      state.selected.forEach((id) => { if (!liveIds.has(id)) state.selected.delete(id); });
    } catch (err) {
      console.error("Failed to fetch /captures", err);
    }
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
      renderSamples();
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
    refresh();
  }

  return { init, refresh };
})();

window.Classifier = Classifier;
