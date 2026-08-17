"use strict";
/*
 * Classifier tab -- docs/EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md S8 (the
 * 2026-07-25 UI reshape; supersedes S3/S4's single-table + separate-panel
 * shape), reworked again for the 2026-07-26 tab cleanup. One card per
 * asset class (device_type), each self-contained: an "Asset Class: <name>"
 * heading (no more Linked/Not-linked pill -- the row right under it IS the
 * link state: a Link button, or the Studio-project link + Unlink), a
 * "Recordings" heading + table, a single selection-driven action bar
 * (Upload(N)/Edit label(N)/Delete(N) -- all three now driven by the same
 * row-checkbox selection, replacing the old separate "Upload all" button),
 * and -- linked only -- a Fetch-trained-model row. Orphaned device types (a
 * capture's device_type no longer on any fleet node) get their own
 * de-emphasized delete-only card, unchanged from the original S3 design.
 *
 * Upload sends only the selected recordings (never "all" anymore); the
 * backend still fits its pooled normalization baseline from every local
 * recording for the asset class regardless of selection (api/
 * ei_controller.py's upload() docstring). Runs as a background job like
 * Train/Fetch always have, streaming "ei_progress" over the shared /ws
 * connection (handleMessage below, wired into Charts.init's 4th callback
 * in app.js) with an inline "Uploading… N/M" + failures readout instead of
 * an alert(). Round B's Train button is gone from this tab per S8.2
 * (training now happens in EI Studio itself; Fetch is the only glue left)
 * -- the backend route for it no longer exists either.
 *
 * Same module shape as perf.js/alerts.js otherwise: owns its own data
 * (fetches /captures itself rather than reading another module's state).
 */

const Classifier = (() => {
  // Same trash glyph as the Fleet tab's decommission button (app.js's
  // ICON_TRASH) -- duplicated here rather than read off `window` so this
  // module stays self-contained regardless of script load order.
  const ICON_TRASH = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>';

  const state = {
    captures: [],           // [{id, node_id, device_type, label, timestamp, frame_count}]
    selected: new Set(),    // capture ids -- global set, each card filters to its own rows
    eiStatus: {},           // {device_type: linked(bool)}
    eiProjectIds: {},       // {device_type: EI project_id}, for the Studio link
    eiProjectNames: {},     // {device_type: EI project name}, for the Studio link's label
    eiModels: {},           // {device_type: fetched-model mtime(epoch s) | null}
    eiJobs: {},             // {device_type: "fetch"|"upload"} for whichever are currently running (survives a refresh)
    eiJobStage: {},         // {device_type: last "ei_progress" stage string} -- fetch only
    eiUploadProgress: {},   // {device_type: {stage, uploaded, total, failures}} -- upload only, live-WS-only
    eiJobErrors: {},        // {device_type: last error message}, cleared on the next job start for that type
    fleetAssetClasses: [],  // GET /device_types -- classes currently assigned to a live node
    linking: null,          // device_type currently showing its login form, or null
    linkForm: { username: "", password: "", totp: "", needsTotp: false, error: null, busy: false },
  };

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

  // /classifier/ei/link's 400 for "needs a 2FA code" carries a
  // structured `{totp_required: true}` error object, not a string --
  // postJson's generic Error(err.detail || err.error) would stringify an
  // object to "[object Object]", losing the signal. This one route gets
  // its own thin fetch wrapper instead of overloading postJson's contract.
  // Note: api/app.py's exception_handler rewrites every HTTPException body
  // to {"error": ...}, NOT FastAPI's default {"detail": ...} -- postJson's
  // `err.detail || err.error` already accounts for this app-wide
  // convention; this helper only needs the `error` key.
  async function postEiLink(body) {
    const res = await fetch("/classifier/ei/link", {
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
  // Card helpers
  // ---------------------------------------------------------------------

  function deviceTypesInView() {
    // "live" cards: asset classes GET /device_types says some node
    // currently has, unioned with anything /classifier/ei/status already
    // knows about (covers a class that's linked but whose last node
    // was just reassigned/decommissioned -- the EI project is still real,
    // shouldn't vanish just because no node currently carries the class).
    //
    // "orphaned": a saved recording's device_type that isn't in either of
    // those -- the asset class was renamed/unset on its node (Fleet tab's
    // pill) after the recording was saved, capture.py freezes device_type
    // onto the JSON at save time and never updates it retroactively.
    const live = new Set(state.fleetAssetClasses);
    Object.keys(state.eiStatus).forEach((t) => live.add(t));
    const captureTypes = new Set();
    state.captures.forEach((c) => { if (c.device_type) captureTypes.add(c.device_type); });
    const orphaned = Array.from(captureTypes).filter((t) => !live.has(t));
    return { live: Array.from(live).sort(), orphaned: orphaned.sort() };
  }

  function capturesFor(deviceType) {
    return state.captures.filter((c) => c.device_type === deviceType);
  }

  function selectedCountFor(captures) {
    return captures.filter((c) => state.selected.has(c.id)).length;
  }

  function linkFormHtml(deviceType) {
    const f = state.linkForm;
    return `<div class="classifier-ei__link">
      <input type="text" class="classifier-table__rename-input" placeholder="Edge Impulse username or email"
             data-action="ei_username" value="${escapeAttr(f.username)}" autocomplete="off" ${f.busy ? "disabled" : ""}>
      <input type="password" class="classifier-table__rename-input" placeholder="Password"
             data-action="ei_password" value="${escapeAttr(f.password)}" autocomplete="off" ${f.busy ? "disabled" : ""}>
      ${f.needsTotp ? `<input type="text" class="classifier-table__rename-input" placeholder="2FA code"
             data-action="ei_totp" value="${escapeAttr(f.totp)}" autocomplete="off" ${f.busy ? "disabled" : ""}>` : ""}
      <button type="button" class="btn-primary" data-action="ei_link_submit"
              data-type="${escapeAttr(deviceType)}" ${f.busy ? "disabled" : ""}>
        ${f.busy ? "Linking…" : "Link"}
      </button>
      <button type="button" class="btn-text" data-action="ei_link_cancel">Cancel</button>
      ${f.error ? `<div class="classifier-ei__error">${escapeHtmlLocal(f.error)}</div>` : ""}
    </div>`;
  }

  function eiStudioUrl(projectId) {
    return `https://studio.edgeimpulse.com/studio/${projectId}`;
  }

  // Fetch (S4 steps 8-9) stage labels -- keyed by the "stage" field on the
  // "ei_progress" WS broadcast. Falls back to a bare "Fetching…" for a job
  // whose only known state is the server's job_state() action (e.g. right
  // after a page refresh, before any WS tick has arrived yet).
  const EI_STAGE_LABELS = {
    building: "Building model…",
    downloading: "Downloading model…",
  };

  function fetchButtonLabel(deviceType) {
    if (state.eiJobs[deviceType] !== "fetch") return "Fetch trained model";
    const stage = state.eiJobStage[deviceType];
    return (stage && EI_STAGE_LABELS[stage]) || "Fetching…";
  }

  function modelStatusHtml(deviceType) {
    const fetchedAt = state.eiModels[deviceType];
    if (!fetchedAt) return `<span class="classifier-table__muted">No model fetched yet</span>`;
    return `Model fetched ${escapeHtmlLocal(new Date(fetchedAt * 1000).toLocaleString())}`;
  }

  function tableHtml(deviceType, captures) {
    const heading = `<h3 class="chart-section__title classifier-card__section-heading">Recordings</h3>`;
    if (captures.length === 0) {
      return `${heading}<div class="perf-empty">No recordings yet for this asset class.</div>`;
    }
    const allSelected = captures.every((c) => state.selected.has(c.id));
    const rows = captures.map((entry) => {
      const checked = state.selected.has(entry.id);
      const recorded = entry.timestamp
        ? new Date(entry.timestamp * 1000).toLocaleString() : "–";
      return `<tr class="classifier-table__row" data-id="${escapeAttr(entry.id)}">
        <td><input type="checkbox" data-action="select" ${checked ? "checked" : ""}></td>
        <td>${escapeHtmlLocal(entry.node_id || "–")}</td>
        <td>${escapeHtmlLocal(entry.label)}</td>
        <td>${entry.frame_count}</td>
        <td>${recorded}</td>
      </tr>`;
    }).join("");
    return `${heading}<div class="classifier-table__wrap">
      <table class="classifier-table">
        <thead>
          <tr>
            <th><input type="checkbox" data-action="select_all" data-type="${escapeAttr(deviceType)}" ${allSelected ? "checked" : ""}></th>
            <th>Node</th><th>Label</th><th>Frames</th><th>Recorded</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
  }

  // Upload joins Delete/Edit label as a third selection-driven action (2026-
  // 07-26 cleanup) -- all three counts come from the same selectedCountFor().
  // Upload sends only the selected recordings; disabled when not linked, a
  // job's already running, or nothing's selected.
  function uploadButtonLabel(deviceType, n) {
    if (state.eiJobs[deviceType] !== "upload") return `Upload (${n})`;
    const p = state.eiUploadProgress[deviceType];
    return p ? `Uploading… ${p.uploaded}/${p.total}` : "Uploading…";
  }

  function uploadFailuresHtml(deviceType) {
    const failures = (state.eiUploadProgress[deviceType] || {}).failures || [];
    if (!failures.length) return "";
    return `<div class="classifier-card__failures">
      ${failures.map((f) => `<div>${escapeHtmlLocal(f)}</div>`).join("")}
    </div>`;
  }

  // Order + coloring per 2026-07-26/27 follow-ups: Upload/Edit label/
  // Delete. Upload keeps the blue "ready" tint (.btn-label--ready, same
  // language as Train/Save elsewhere); Delete alone gets the persistent
  // red .btn-label--danger treatment (same color-mix convention), since
  // it's the one destructive action in the row.
  function actionsBarHtml(deviceType, captures, linked) {
    const n = selectedCountFor(captures);
    const jobRunning = !!state.eiJobs[deviceType];
    const uploadDisabled = !linked || jobRunning || n === 0;
    return `<div class="classifier-card__actions">
      <button type="button" class="btn-label btn-label--ready" data-action="ei_upload"
              data-type="${escapeAttr(deviceType)}" ${uploadDisabled ? "disabled" : ""}
              ${linked ? "" : `title="Link to Edge Impulse first"`}>
        ${uploadButtonLabel(deviceType, n)}
      </button>
      <button type="button" class="btn-label" data-action="edit_label_selected"
              data-type="${escapeAttr(deviceType)}" ${n === 0 ? "disabled" : ""}>Edit label (${n})</button>
      <button type="button" class="btn-label btn-label--danger" data-action="delete_selected"
              data-type="${escapeAttr(deviceType)}" ${n === 0 ? "disabled" : ""}>Delete (${n})</button>
    </div>
    ${uploadFailuresHtml(deviceType)}`;
  }

  // No more Linked/Not-linked pill (2026-07-26 cleanup) -- a small muted
  // "ASSET CLASS" kicker above a large bold device name; link state is
  // shown by linkStudioRowHtml() right below (2026-07-27: back to its own
  // row under the heading, not sharing a row with it -- and both rows use
  // the same plain .classifier-ei__row wrapper as everything else in the
  // card, so the flex `gap` on .classifier-card is the ONE source of
  // vertical spacing throughout -- no more per-pair margin-top making some
  // gaps bigger than others).
  function cardHeaderHtml(deviceType) {
    return `<div class="classifier-ei__row">
      <div class="classifier-card__title">
        <span class="classifier-card__kicker">Asset Class</span>
        <span class="classifier-card__name">${escapeHtmlLocal(deviceType)}</span>
      </div>
      <button type="button" class="btn-text classifier-card__rename" data-action="rename_class"
              data-type="${escapeAttr(deviceType)}">Rename</button>
    </div>`;
  }

  // Link button (+ inline login form) when not linked, or the Studio
  // link + Unlink when linked. Studio link text stays short ("Open in
  // Studio ↗"); the actual project name (which can run long) shows on
  // hover instead of inline.
  function linkStudioRowHtml(deviceType, linked) {
    if (!linked) {
      const isLinking = state.linking === deviceType;
      // While the login form is showing, it's the ONLY thing in this
      // slot -- no empty .classifier-ei__row wrapper left behind around
      // it (that phantom empty row used to eat an extra flex `gap` on
      // both sides of itself, throwing off the otherwise-uniform card
      // spacing the instant Link is clicked).
      if (isLinking) return linkFormHtml(deviceType);
      return `<div class="classifier-ei__row">
        <button type="button" class="btn-label"
          data-action="ei_link_start" data-type="${escapeAttr(deviceType)}">Link to Edge Impulse</button>
      </div>`;
    }
    const projectId = state.eiProjectIds[deviceType];
    const projectName = state.eiProjectNames[deviceType] || deviceType;
    return `<div class="classifier-ei__row">
      ${projectId ? `<a class="btn-label" href="${eiStudioUrl(projectId)}"
        target="_blank" rel="noopener noreferrer" title="Project: ${escapeAttr(projectName)}">Open in Studio ↗</a>` : ""}
      <button type="button" class="btn-text" data-action="ei_unlink" data-type="${escapeAttr(deviceType)}">Unlink</button>
    </div>`;
  }

  // Fetch-trained-model row -- linked cards only, stays below the action
  // bar. Button first, then the fetched-at status text after it.
  function modelRowHtml(deviceType) {
    const jobRunning = !!state.eiJobs[deviceType];
    return `<div class="classifier-ei__row">
      <button type="button" class="btn-label" data-action="ei_fetch_model"
              data-type="${escapeAttr(deviceType)}" ${jobRunning ? "disabled" : ""}
              title="Build + download the trained TFLite model">
        ${fetchButtonLabel(deviceType)}
      </button>
      <span class="classifier-ei__model-status">${modelStatusHtml(deviceType)}</span>
    </div>`;
  }

  function deviceTypeCardHtml(deviceType) {
    const linked = !!state.eiStatus[deviceType];
    const captures = capturesFor(deviceType);
    const error = state.eiJobErrors[deviceType];
    return `<div class="perf-card classifier-card">
      ${cardHeaderHtml(deviceType)}
      ${linkStudioRowHtml(deviceType, linked)}
      ${tableHtml(deviceType, captures)}
      ${actionsBarHtml(deviceType, captures, linked)}
      ${linked ? modelRowHtml(deviceType) : ""}
      ${error ? `<div class="classifier-ei__row classifier-ei__error">${escapeHtmlLocal(error)}</div>` : ""}
    </div>`;
  }

  function orphanedCardHtml(deviceType) {
    const count = state.captures.filter((c) => c.device_type === deviceType).length;
    return `<div class="perf-card classifier-card">
      <div class="classifier-ei__row">
        <span class="classifier-ei__type">${escapeHtmlLocal(deviceType)}</span>
        <span class="classifier-ei__pill classifier-ei__pill--orphaned">Not in fleet anymore</span>
        <button type="button" class="btn-text classifier-card__rename" data-action="rename_class"
                data-type="${escapeAttr(deviceType)}"
                title="Move ${count} recording(s) to a live or new asset class">Rename</button>
        <button type="button" class="btn-icon btn-icon--danger" data-action="ei_delete_orphaned"
                data-type="${escapeAttr(deviceType)}"
                title="Delete ${count} recording(s) saved under this class"
                aria-label="Delete recordings for ${escapeAttr(deviceType)}">${ICON_TRASH}</button>
      </div>
    </div>`;
  }

  function render() {
    const el = document.getElementById("classifier-cards");
    if (!el) return;
    const { live, orphaned } = deviceTypesInView();
    if (live.length === 0 && orphaned.length === 0) {
      el.innerHTML = `<div class="perf-card">
        <div class="perf-empty">No asset classes assigned yet -- set one on a node in the Fleet tab first.</div>
      </div>`;
      return;
    }
    el.innerHTML = live.map(deviceTypeCardHtml).join("") + orphaned.map(orphanedCardHtml).join("");
  }

  // ---------------------------------------------------------------------
  // Data refresh
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
      state.eiProjectIds = body.project_ids || {};
      state.eiProjectNames = body.project_names || {};
      state.eiModels = body.models || {};
      // Server-reported job state wins over anything stale left locally
      // from a page load before a job's "done"/"error" broadcast arrived
      // (e.g. a refresh mid-job) -- GET /classifier/ei/status is the
      // source of truth, WS "ei_progress" ticks are the live overlay on
      // top of it between polls.
      state.eiJobs = body.jobs || {};
    } catch (err) {
      console.error("Failed to fetch /classifier/ei/status", err);
    }
  }

  async function refreshFleetAssetClasses() {
    try {
      const res = await fetch("/device_types");
      const body = await res.json();
      state.fleetAssetClasses = body.device_types || [];
    } catch (err) {
      console.error("Failed to fetch /device_types", err);
    }
  }

  async function refresh() {
    await Promise.all([refreshCaptures(), refreshEiStatus(), refreshFleetAssetClasses()]);
    render();
  }

  // ---------------------------------------------------------------------
  // Local (on-disk) actions
  // ---------------------------------------------------------------------

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

  function deleteSelectedForCard(deviceType) {
    const ids = capturesFor(deviceType).filter((c) => state.selected.has(c.id)).map((c) => c.id);
    return deleteIds(ids);
  }

  async function editLabelForCard(deviceType) {
    const ids = capturesFor(deviceType).filter((c) => state.selected.has(c.id)).map((c) => c.id);
    if (ids.length === 0) return;
    const label = window.prompt(`New label for ${ids.length} recording(s):`, "");
    if (!label || !label.trim()) return;
    try {
      await postJson("/captures/rename_bulk", { ids, label: label.trim() });
    } catch (err) {
      alert(`Rename failed: ${err.message}`);
    }
    await refresh();
  }

  // Renames a whole asset class -- on a live card, fixes a typo (or
  // deliberately re-groups a class) without orphaning its past
  // recordings the way retyping a node's device_type on the Fleet tab
  // used to; on an orphaned card, this IS the recovery path: point the
  // old name at a live class (or just its own corrected spelling) and
  // its recordings reappear under that card. api/app.py's route cascades
  // to the registry, every matching capture, and (if linked) the EI
  // project/scaling baseline/fetched model.
  async function renameClass(deviceType) {
    const next = window.prompt(`Rename asset class "${deviceType}" to:`, deviceType);
    if (!next || !next.trim() || next.trim() === deviceType) return;
    try {
      await postJson("/device_types/rename", { old_device_type: deviceType, new_device_type: next.trim() });
    } catch (err) {
      alert(`Rename failed: ${err.message}`);
    }
    await refresh();
  }

  async function deleteOrphanedType(deviceType) {
    const ids = state.captures.filter((c) => c.device_type === deviceType).map((c) => c.id);
    if (ids.length === 0) return;
    const label = ids.length === 1 ? "1 recording" : `${ids.length} recordings`;
    if (!confirm(`Delete ${label} saved under "${deviceType}"? That asset class no longer `
        + "exists in the fleet, so this is the only way to clear it. This can't be undone.")) {
      return;
    }
    try {
      await postJson("/captures/delete", { ids });
    } catch (err) {
      alert(`Delete failed: ${err.message}`);
    }
    await refresh();
  }

  // ---------------------------------------------------------------------
  // Edge Impulse actions
  // ---------------------------------------------------------------------

  function startLink(deviceType) {
    state.linking = deviceType;
    state.linkForm = { username: "", password: "", totp: "", needsTotp: false, error: null, busy: false };
    render();
  }

  function cancelLink() {
    state.linking = null;
    render();
  }

  async function submitLink(deviceType) {
    const f = state.linkForm;
    f.busy = true;
    f.error = null;
    render();
    try {
      await postEiLink({
        device_type: deviceType,
        username: f.username,
        password: f.password,
        totp: f.needsTotp ? f.totp : undefined,
      });
      // Success: forget the form entirely, including the password --
      // nothing about it lingers longer than this one request.
      state.linking = null;
      state.linkForm = { username: "", password: "", totp: "", needsTotp: false, error: null, busy: false };
      await refreshEiStatus();
    } catch (err) {
      if (err.totpRequired) {
        state.linkForm = { ...f, needsTotp: true, busy: false, error: "Enter your 2FA code" };
      } else {
        // Wrong password / other failure: drop the password and let the
        // user retype it rather than holding it in state indefinitely.
        state.linkForm = { ...f, password: "", busy: false, error: err.message };
      }
    }
    render();
  }

  async function unlinkType(deviceType) {
    // Local-only: drops the saved project_id/api_key so a later "Link"
    // creates a fresh project instead of treating a since-deleted (or
    // just-unwanted) Studio project as still linked. Doesn't touch
    // anything in EI itself -- see EIController.unlink()'s docstring for
    // why a local "forget this" click shouldn't reach out and delete a
    // Studio project as a side effect.
    if (!confirm(`Unlink "${deviceType}" from Edge Impulse? If its Studio project still `
        + "exists, this dashboard loses the API key needed to upload to it -- linking "
        + "again creates a brand new project rather than reusing the old one.")) {
      return;
    }
    try {
      await postJson("/classifier/ei/unlink", { device_type: deviceType });
    } catch (err) {
      alert(`Unlink failed: ${err.message}`);
    }
    await refreshEiStatus();
    render();
  }

  // Both start a background job and return immediately (api/app.py's POST
  // /classifier/ei/upload + /fetch_model); state.eiJobs is set
  // optimistically here so the button disables/relabels the instant it's
  // clicked, without waiting for the first "ei_progress" WS tick.
  //
  // Sends only the currently-selected rows for this card (2026-07-26 --
  // Upload joins Delete/Edit label as a selection-driven action, no more
  // "always every local recording"); the backend still fits its
  // normalization baseline from every local recording regardless.
  async function uploadSelected(deviceType) {
    const ids = capturesFor(deviceType).filter((c) => state.selected.has(c.id)).map((c) => c.id);
    if (ids.length === 0) return;
    state.eiJobs[deviceType] = "upload";
    delete state.eiUploadProgress[deviceType];
    delete state.eiJobErrors[deviceType];
    render();
    try {
      await postJson("/classifier/ei/upload", { device_type: deviceType, ids });
    } catch (err) {
      delete state.eiJobs[deviceType];
      alert(`Upload failed to start: ${err.message}`);
    }
    await refreshEiStatus();
    render();
  }

  async function fetchModelForDeviceType(deviceType) {
    state.eiJobs[deviceType] = "fetch";
    delete state.eiJobStage[deviceType];
    delete state.eiJobErrors[deviceType];
    render();
    try {
      await postJson("/classifier/ei/fetch_model", { device_type: deviceType });
    } catch (err) {
      delete state.eiJobs[deviceType];
      alert(`Fetch failed to start: ${err.message}`);
    }
    await refreshEiStatus();
    render();
  }

  // "ei_progress" WS handler (api/app.py's _run_ei_job broadcasts one per
  // stage transition + poll tick/upload batch). "done"/"error" both clear
  // the in-flight job so the button re-enables; "error" also keeps the
  // message visible under the card until the next job for that type.
  function handleMessage(msg) {
    // Another client renamed a class (or this one just did, but the
    // broadcast round-trips back too) -- just re-pull everything rather
    // than trying to patch every bit of state device_type keys into.
    if (msg.type === "device_types_renamed") {
      refresh();
      return;
    }

    const deviceType = msg.device_type;

    if (msg.action === "upload") {
      if (msg.stage === "done") {
        delete state.eiJobs[deviceType];
        delete state.eiUploadProgress[deviceType];
        render();
        return;
      }
      if (msg.stage === "error") {
        delete state.eiJobs[deviceType];
        delete state.eiUploadProgress[deviceType];
        state.eiJobErrors[deviceType] = msg.error || "upload failed";
        render();
        return;
      }
      state.eiJobs[deviceType] = "upload";
      state.eiUploadProgress[deviceType] = {
        stage: msg.stage, uploaded: msg.uploaded, total: msg.total, failures: msg.failures || [],
      };
      render();
      return;
    }

    // Fetch.
    if (msg.stage === "done") {
      delete state.eiJobs[deviceType];
      delete state.eiJobStage[deviceType];
      refreshEiStatus().then(render);
      return;
    }
    if (msg.stage === "error") {
      delete state.eiJobs[deviceType];
      delete state.eiJobStage[deviceType];
      state.eiJobErrors[deviceType] = msg.error || "job failed";
      render();
      return;
    }
    state.eiJobs[deviceType] = msg.action;
    state.eiJobStage[deviceType] = msg.stage;
    render();
  }

  // ---------------------------------------------------------------------
  // Event wiring -- one container, every card lives inside it.
  // ---------------------------------------------------------------------

  function wireEvents() {
    const el = document.getElementById("classifier-cards");

    el.addEventListener("click", (e) => {
      const selectAll = e.target.closest('[data-action="select_all"]');
      if (selectAll) {
        const deviceType = selectAll.dataset.type;
        const checked = selectAll.checked;
        capturesFor(deviceType).forEach((c) => {
          if (checked) state.selected.add(c.id); else state.selected.delete(c.id);
        });
        render();
        return;
      }
      const deleteBtn = e.target.closest('[data-action="delete_selected"]');
      if (deleteBtn) { deleteSelectedForCard(deleteBtn.dataset.type); return; }
      const editBtn = e.target.closest('[data-action="edit_label_selected"]');
      if (editBtn) { editLabelForCard(editBtn.dataset.type); return; }

      const startBtn = e.target.closest('[data-action="ei_link_start"]');
      if (startBtn) { startLink(startBtn.dataset.type); return; }
      if (e.target.closest('[data-action="ei_link_cancel"]')) { cancelLink(); return; }
      const submitBtn = e.target.closest('[data-action="ei_link_submit"]');
      if (submitBtn) { submitLink(submitBtn.dataset.type); return; }
      const unlinkBtn = e.target.closest('[data-action="ei_unlink"]');
      if (unlinkBtn) { unlinkType(unlinkBtn.dataset.type); return; }
      const deleteOrphanBtn = e.target.closest('[data-action="ei_delete_orphaned"]');
      if (deleteOrphanBtn) { deleteOrphanedType(deleteOrphanBtn.dataset.type); return; }
      const renameBtn = e.target.closest('[data-action="rename_class"]');
      if (renameBtn) { renameClass(renameBtn.dataset.type); return; }
      const uploadBtn = e.target.closest('[data-action="ei_upload"]');
      if (uploadBtn) { uploadSelected(uploadBtn.dataset.type); return; }
      const fetchBtn = e.target.closest('[data-action="ei_fetch_model"]');
      if (fetchBtn) { fetchModelForDeviceType(fetchBtn.dataset.type); return; }
    });

    el.addEventListener("change", (e) => {
      const checkbox = e.target.closest('[data-action="select"]');
      if (!checkbox) return;
      const row = e.target.closest(".classifier-table__row");
      const id = row.dataset.id;
      if (checkbox.checked) state.selected.add(id);
      else state.selected.delete(id);
      render();
    });

    el.addEventListener("input", (e) => {
      const usernameInput = e.target.closest('[data-action="ei_username"]');
      if (usernameInput) { state.linkForm.username = usernameInput.value; return; }
      const passwordInput = e.target.closest('[data-action="ei_password"]');
      if (passwordInput) { state.linkForm.password = passwordInput.value; return; }
      const totpInput = e.target.closest('[data-action="ei_totp"]');
      if (totpInput) { state.linkForm.totp = totpInput.value; return; }
    });

    el.addEventListener("keydown", (e) => {
      if (!e.target.closest(".classifier-ei__link")) return;
      const deviceType = state.linking;
      if (e.key === "Enter") { e.preventDefault(); if (deviceType) submitLink(deviceType); }
      else if (e.key === "Escape") { e.preventDefault(); cancelLink(); }
    });
  }

  function init() {
    wireEvents();
    refresh();
  }

  return { init, refresh, handleMessage };
})();

window.Classifier = Classifier;
