"use strict";
/*
 * EPM Dashboard frontend. Vanilla JS, no build step, no framework --
 * matches this project's stdlib-only backend convention (api/app.py).
 * Same-origin: REST and this static frontend are served from the one
 * FastAPI app (mpu/main.py), so no separate host/port to target.
 */

const NODES_POLL_MS = 5000;

// A node counts as offline once nothing has been heard from it for a
// while, independent of its last confirmed status -- last_seen staleness
// is the online/offline signal (docs/EPM_Dashboard_Redesign_Spec.md S5.1,
// "OFFLINE continues to be frontend-computed"). Never pushed by the server.
const OFFLINE_AFTER_S = 30;

// "all" isn't a bucketFor() outcome -- it's the unfiltered total, shown
// as its own tile so a click can reset whatever per-status filter is
// applied to the (future) fleet listing.
const SUMMARY_TILES = [
  { bucket: "all", label: "Assets" },
  { bucket: "fault", label: "Faulty" },
  { bucket: "warning", label: "Warning" },
  { bucket: "healthy", label: "Healthy" },
  { bucket: "new", label: "New" },
  { bucket: "paused", label: "Paused" },
  { bucket: "offline", label: "Offline" },
];

function bucketFor(entry) {
  // Paused is an intentional, operator-initiated state -- staleness
  // shouldn't demote it to "offline" the way it would for an
  // unattended node (matches the old dashboard's effectiveStatus()).
  if (entry.status === "paused") return "paused";
  // null/undefined last_seen means "never streamed a frame yet" -- that's
  // "New", not "Offline": offline implies it *was* online and went quiet,
  // which isn't true for a node that's never connected at all.
  if (entry.last_seen !== null && entry.last_seen !== undefined
      && Date.now() / 1000 - entry.last_seen > OFFLINE_AFTER_S) {
    return "offline";
  }
  if (entry.status === "healthy") return "healthy";
  if (entry.status === "warning") return "warning";
  if (entry.status === "fault") return "fault";
  // uncommissioned, commissioning_collecting, commissioning_training
  return "new";
}

function renderSummary(nodes) {
  const counts = { all: 0, fault: 0, warning: 0, healthy: 0, new: 0, paused: 0, offline: 0 };
  for (const entry of Object.values(nodes)) {
    counts.all += 1;
    counts[bucketFor(entry)] += 1;
  }

  const row = document.getElementById("summary-row");
  row.innerHTML = SUMMARY_TILES.map(
    (t) => `<div class="tile tile--${t.bucket}" data-bucket="${t.bucket}">
      <div class="tile__count">${counts[t.bucket]}</div>
      <div class="tile__label">${t.label}</div>
    </div>`
  ).join("");
}

// ---------------------------------------------------------------------
// Asset list
// ---------------------------------------------------------------------

const STATUS_LABEL = {
  uncommissioned: "New",
  commissioning_collecting: "Collecting",
  commissioning_training: "Training",
  healthy: "Healthy",
  warning: "Warning",
  fault: "Faulty",
  paused: "Paused",
};

// Status label shown in the row -- appends live collected/min_frames
// progress while collecting (entry.commissioning_progress, from
// CommissioningController.progress() via GET /nodes) so there's no need
// for a separate progress bar/text element.
function statusLabelFor(entry, bucket) {
  if (bucket === "offline") return "Offline";
  if (entry.status === "commissioning_collecting" && entry.commissioning_progress) {
    const { collected, min_frames } = entry.commissioning_progress;
    return `Collecting ${collected}/${min_frames}`;
  }
  return STATUS_LABEL[entry.status];
}

// Every row control is icon-only -- no spelled-out buttons in any state.
// Mirrors the transition guards in registry/registry.py exactly:
//   - Record (commission_start) is disabled while collecting/training
//     (already running) and while paused (resume() first, matches
//     PAUSED -> COMMISSIONING_COLLECTING being deliberately absent there).
//   - Train (commission_stop) only unlocks once enough frames are in
//     (entry.commissioning_progress); shows a spinner during
//     commissioning_training and a checkmark once a model exists
//     (healthy/warning/fault/paused) -- pressing Record again resets it.
//   - Pause/Resume only enabled once a model exists (healthy/warning/
//     fault/paused) -- matches pause()/resume()'s own guards.
//   - Remove (decommission) is always enabled in every status (S3.9:
//     registry.decommission() is now removable from any status).
function rowControls(entry) {
  const status = entry.status;
  const progress = entry.commissioning_progress;
  const readyToTrain = status === "commissioning_collecting"
    && progress !== undefined && progress.collected >= progress.min_frames;

  let recordEnabled, recordTooltip;
  if (status === "uncommissioned") {
    recordEnabled = true; recordTooltip = "Start recording";
  } else if (status === "healthy" || status === "warning" || status === "fault") {
    recordEnabled = true; recordTooltip = "Re-record (retrain)";
  } else if (status === "commissioning_collecting") {
    recordEnabled = false; recordTooltip = "Recording in progress";
  } else if (status === "commissioning_training") {
    recordEnabled = false; recordTooltip = "Training in progress";
  } else {
    recordEnabled = false; recordTooltip = "Resume first to re-record";
  }

  let trainIcon, trainEnabled, trainTooltip;
  if (status === "commissioning_training") {
    trainIcon = "spinner"; trainEnabled = false; trainTooltip = "Training…";
  } else if (status === "commissioning_collecting") {
    trainIcon = "retrain";
    trainEnabled = readyToTrain;
    trainTooltip = readyToTrain
      ? "Start training"
      : `Collecting… (${progress ? progress.collected : 0}/${progress ? progress.min_frames : "?"})`;
  } else if (status === "uncommissioned") {
    trainIcon = "retrain"; trainEnabled = false; trainTooltip = "No data collected yet";
  } else {
    trainIcon = "check"; trainEnabled = false; trainTooltip = "Model trained";
  }

  const pauseResumeEnabled = status === "healthy" || status === "warning"
    || status === "fault" || status === "paused";
  const pauseResumeAction = status === "paused" ? "resume" : "pause";
  const pauseResumeTooltip = status === "paused" ? "Resume" : "Pause";

  return {
    recordEnabled, recordTooltip,
    trainIcon, trainEnabled, trainTooltip,
    pauseResumeAction, pauseResumeEnabled, pauseResumeTooltip,
  };
}

const ACTION_ENDPOINT = {
  commission_start: (id) => ["POST", `/nodes/${id}/commission/start`],
  commission_stop: (id) => ["POST", `/nodes/${id}/commission/stop`],
  pause: (id) => ["POST", `/nodes/${id}/pause`],
  resume: (id) => ["POST", `/nodes/${id}/resume`],
  decommission: (id) => ["POST", `/nodes/${id}/decommission`],
};

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `${method} ${path} -> ${res.status}`);
  return data;
}

async function runAction(action, nodeId) {
  const [method, path] = ACTION_ENDPOINT[action](nodeId);
  try {
    await api(method, path);
  } catch (err) {
    console.error(`Action ${action} on ${nodeId} failed`, err);
    alert(`${action.replace("_", " ")} failed: ${err.message}`);
  }
  await pollNodes();
}

// display_name is user-editable (rename) and node_id can carry arbitrary
// text from ingestion -- both land in innerHTML below, so escape before
// interpolating rather than trusting them as safe markup.
function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function formatScore(entry) {
  return typeof entry.last_anomaly_score === "number"
    ? entry.last_anomaly_score.toFixed(3)
    : "—";
}

// Inline SVGs rather than an emoji/icon-font dependency -- consistent
// rendering across platforms with no extra asset or build step.
const ICON_RECORD = '<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><circle cx="12" cy="12" r="7"/></svg>';
const ICON_RETRAIN = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/><path d="M3 21v-5h5"/></svg>';
const ICON_SPINNER = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21 12a9 9 0 1 1-9-9"/></svg>';
const ICON_CHECK = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.5 2.5L16 9"/></svg>';
const ICON_PAUSE = '<svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor"><path d="M6 5h4v14H6zM14 5h4v14h-4z"/></svg>';
const ICON_PLAY = '<svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>';
const ICON_TRASH = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>';

const TRAIN_ICON = { retrain: ICON_RETRAIN, spinner: ICON_SPINNER, check: ICON_CHECK };

// Suppresses re-render of the list while a rename input is open, so an
// in-flight edit isn't wiped out by the next 5s poll (same guard the old
// dashboard used for this exact race).
let editingNodeId = null;

// Node IDs currently showing their expanded detail panel (node ID +
// anomaly score -- the two fields dropped from the compact row). Rebuilt
// fresh from scratch on every render rather than stored per-DOM-node, so
// it survives the innerHTML replace each poll does.
const expandedNodeIds = new Set();

function motorRowHtml(entry) {
  const bucket = bucketFor(entry);
  const label = statusLabelFor(entry, bucket);
  const controls = rowControls(entry);
  const isEditing = editingNodeId === entry.node_id;
  const isExpanded = expandedNodeIds.has(entry.node_id);
  const pauseIcon = controls.pauseResumeAction === "resume" ? ICON_PLAY : ICON_PAUSE;

  const nameHtml = isEditing
    ? `<input class="motor-row__name-input" data-role="name-input" value="${escapeHtml(entry.display_name)}" />`
    : `<span class="motor-row__name" data-role="name" title="Double-click to rename">${escapeHtml(entry.display_name)}</span>`;

  const rowHtml = `<div class="motor-row${isExpanded ? " motor-row--expanded" : ""}" title="Click to expand">
    <div class="motor-row__main">
      ${nameHtml}
      <span class="motor-row__status">${label}</span>
    </div>
    <div class="motor-row__actions">
      <button class="btn-icon btn-icon--record" data-action="commission_start" title="${controls.recordTooltip}" aria-label="${controls.recordTooltip}" ${controls.recordEnabled ? "" : "disabled"}>${ICON_RECORD}</button>
      <button class="btn-icon${controls.trainIcon === "spinner" ? " btn-icon--spin" : ""}${controls.trainIcon === "check" ? " btn-icon--done" : ""}" data-action="commission_stop" title="${controls.trainTooltip}" aria-label="${controls.trainTooltip}" ${controls.trainEnabled ? "" : "disabled"}>${TRAIN_ICON[controls.trainIcon]}</button>
      <button class="btn-icon" data-action="${controls.pauseResumeAction}" title="${controls.pauseResumeTooltip}" aria-label="${controls.pauseResumeTooltip}" ${controls.pauseResumeEnabled ? "" : "disabled"}>${pauseIcon}</button>
      <button class="btn-icon btn-icon--danger" data-action="decommission" title="Remove" aria-label="Remove">${ICON_TRASH}</button>
    </div>
  </div>`;

  const detailHtml = isExpanded ? `<div class="motor-row__detail">
    <div class="motor-row__detail-item">
      <span class="motor-row__detail-label">Node ID</span>
      <span class="motor-row__detail-value">${escapeHtml(entry.node_id)}</span>
    </div>
    <div class="motor-row__detail-item">
      <span class="motor-row__detail-label">Anomaly score</span>
      <span class="motor-row__detail-value">${formatScore(entry)}</span>
    </div>
    ${Charts.chartSlotsHtml(entry.node_id)}
  </div>` : "";

  return `<div class="motor-row-group motor-row-group--${bucket}" data-node-id="${escapeHtml(entry.node_id)}">${rowHtml}${detailHtml}</div>`;
}

function renderFleetList(nodes) {
  const list = document.getElementById("fleet-list");
  const entries = Object.values(nodes);
  list.innerHTML = entries.length
    ? entries.map(motorRowHtml).join("")
    : `<div class="fleet__empty">No assets yet -- they appear automatically as soon as they start streaming data.</div>`;
  // innerHTML above just destroyed and recreated every DOM node in the
  // list, including any chart-slot placeholders -- reparent each expanded
  // node's persistent Plotly <div>s (which survived, since they're held by
  // charts.js, not by this markup) back into the fresh slots.
  Charts.attachExpanded(expandedNodeIds);
}

function toggleExpand(nodeId) {
  if (expandedNodeIds.has(nodeId)) {
    expandedNodeIds.delete(nodeId);
  } else {
    expandedNodeIds.add(nodeId);
  }
  renderFleetList(state.lastNodes);
}

function startRename(nodeId) {
  editingNodeId = nodeId;
  renderFleetList(state.lastNodes);
  const input = document.querySelector(`.motor-row-group[data-node-id="${nodeId}"] [data-role="name-input"]`);
  if (input) {
    input.focus();
    input.select();
  }
}

async function commitRename(nodeId, value) {
  editingNodeId = null;
  const trimmed = value.trim();
  if (trimmed) {
    try {
      await api("POST", `/nodes/${nodeId}/rename`, { display_name: trimmed });
    } catch (err) {
      console.error("Rename failed", err);
      alert(`Rename failed: ${err.message}`);
    }
  }
  await pollNodes();
}

document.getElementById("fleet-list").addEventListener("dblclick", (e) => {
  const nameEl = e.target.closest('[data-role="name"]');
  if (!nameEl) return;
  const nodeId = nameEl.closest(".motor-row-group").dataset.nodeId;
  startRename(nodeId);
});

// Enter commits via blur() (below) rather than calling commitRename
// directly, so there's exactly one commit path -- avoids a double
// rename call when blur() also fires focusout synchronously.
let skipNextBlurCommit = false;

document.getElementById("fleet-list").addEventListener("keydown", (e) => {
  const input = e.target.closest('[data-role="name-input"]');
  if (!input) return;
  if (e.key === "Enter") {
    input.blur();
  } else if (e.key === "Escape") {
    skipNextBlurCommit = true;
    editingNodeId = null;
    renderFleetList(state.lastNodes);
  }
});

document.getElementById("fleet-list").addEventListener("focusout", (e) => {
  const input = e.target.closest('[data-role="name-input"]');
  if (!input) return;
  if (skipNextBlurCommit) {
    skipNextBlurCommit = false;
    return;
  }
  const nodeId = input.closest(".motor-row-group").dataset.nodeId;
  commitRename(nodeId, input.value);
});

// A single delegated click handler covers both action icons and the
// click-anywhere-to-expand affordance (S3.9: the chevron button is gone --
// clicking anywhere else in the row/detail toggles it instead).
document.getElementById("fleet-list").addEventListener("click", (e) => {
  const button = e.target.closest("button[data-action]");
  if (button) {
    const action = button.dataset.action;
    const nodeId = button.closest(".motor-row-group").dataset.nodeId;
    if (action === "decommission") {
      const entry = state.lastNodes[nodeId];
      const name = entry ? entry.display_name : nodeId;
      if (!confirm(`Remove "${name}"? This cannot be undone.`)) return;
    }
    runAction(action, nodeId);
    return;
  }
  if (e.target.closest('[data-role="name"]')) return;
  // Plotly's modebar (zoom/pan/autoscale/...) and the plots themselves
  // live inside .motor-row__charts, still part of this same
  // .motor-row-group -- without this guard, every modebar click (and
  // the synthetic click Plotly fires after a drag-zoom/pan) bubbles up
  // and gets treated as "click anywhere else to toggle expand", collapsing
  // the row out from under the chart the operator was just interacting with.
  if (e.target.closest(".motor-row__charts")) return;
  const group = e.target.closest(".motor-row-group");
  if (group) toggleExpand(group.dataset.nodeId);
});

// ---------------------------------------------------------------------
// Topbar tabs -- Fleet is the only real section so far; Network/
// Performance/Alerts are placeholders until each is built out
// (docs/DASHBOARD_NAV_PLAN.md).
// ---------------------------------------------------------------------

document.querySelector(".topbar__nav").addEventListener("click", (e) => {
  const link = e.target.closest(".topbar__nav-link");
  if (!link) return;
  const tab = link.dataset.tab;

  document.querySelectorAll(".topbar__nav-link").forEach((el) => {
    el.classList.toggle("is-active", el === link);
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.hidden = panel.dataset.tabPanel !== tab;
  });
});

// ---------------------------------------------------------------------
// Polling
// ---------------------------------------------------------------------

const state = { lastNodes: {} };

async function pollNodes() {
  try {
    const res = await fetch("/nodes");
    const nodes = await res.json();
    state.lastNodes = nodes;
    Charts.onNodesPolled(nodes);
    // A decommissioned node never appears in another motorRowHtml() call,
    // so it'd otherwise sit in expandedNodeIds forever (harmless on its
    // own, but see charts.js's attachExpanded()).
    for (const nodeId of expandedNodeIds) {
      if (!(nodeId in nodes)) expandedNodeIds.delete(nodeId);
    }
    renderSummary(nodes);
    // Skip the list re-render while a rename is open -- startRename()/
    // Escape re-render explicitly on their own, this only guards the
    // automatic 5s poll from wiping out an in-flight edit.
    if (editingNodeId === null) renderFleetList(nodes);
  } catch (err) {
    console.error("Failed to fetch /nodes", err);
  }
}

// Primary channel for registry/removed updates is this WS push (S4:
// "WebSocket for continuous real-time push"); the 5s poll below stays as
// the documented fallback -- it's also still how a brand new node is
// discovered in the first place (nothing broadcasts on first auto-add).
Charts.init((msg) => {
  if (msg.type === "removed") {
    delete state.lastNodes[msg.node_id];
    expandedNodeIds.delete(msg.node_id);
  } else if (msg.type === "registry") {
    // The WS broadcast's entry is registry.py's plain to_dict() -- unlike
    // GET /nodes, it has no commissioning_progress (that's REST-only, added
    // in app.py's _node_dict). Carry the last-known progress forward so the
    // "Collecting X/Y" label doesn't flicker back to bare "Collecting" for
    // the few seconds until the next REST poll restores it.
    const prev = state.lastNodes[msg.node_id];
    const entry = msg.entry;
    if (prev && prev.commissioning_progress
        && entry.status === "commissioning_collecting" && !entry.commissioning_progress) {
      entry.commissioning_progress = prev.commissioning_progress;
    }
    state.lastNodes[msg.node_id] = entry;
  }
  renderSummary(state.lastNodes);
  if (editingNodeId === null) renderFleetList(state.lastNodes);
});

pollNodes();
setInterval(pollNodes, NODES_POLL_MS);
