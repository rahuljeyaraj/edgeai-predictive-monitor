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
// applied to the fleet listing.
// "tripped" and "idle" both mean "this machine isn't turning" (registry.py's
// NodeStatus) and both get their own tile, including "idle" -- a bucket with
// no tile would be absent from REAL_BUCKETS, so it could never be in
// selectedBuckets and every node in it would silently vanish from the fleet
// list. Zero-count tiles are hidden below, so neither costs anything on a
// fleet that has no stopped machines.
const SUMMARY_TILES = [
  { bucket: "all", label: "Assets" },
  { bucket: "tripped", label: "Tripped" },
  { bucket: "fault", label: "Faulty" },
  { bucket: "warning", label: "Warning" },
  { bucket: "healthy", label: "Healthy" },
  { bucket: "idle", label: "Idle" },
  { bucket: "new", label: "New" },
  { bucket: "paused", label: "Paused" },
  { bucket: "offline", label: "Offline" },
];

const REAL_BUCKETS = SUMMARY_TILES.filter((t) => t.bucket !== "all").map((t) => t.bucket);

// Fleet filter state -- multi-select toggle, default every status selected
// (matches today's no-filter behavior with zero clicks). Not persisted
// across refresh, matching Dev/perf's precedent (docs/STATUS_FILTER_PLAN.md).
const selectedBuckets = new Set(REAL_BUCKETS);

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
  // Both checked after staleness, deliberately -- unlike "paused" above.
  // Paused is an operator's standing intent, so it outranks going quiet, but a
  // stopped machine we've genuinely lost contact with is offline first and
  // stopped second, exactly as a faulted node that goes quiet reads offline.
  if (entry.status === "tripped") return "tripped";
  if (entry.status === "idle") return "idle";
  // uncommissioned, commissioning_collecting, commissioning_training
  return "new";
}

function renderSummary(nodes) {
  // Built from REAL_BUCKETS rather than written out by hand, so adding a
  // bucket to SUMMARY_TILES can't leave an undefined count here.
  const counts = { all: 0 };
  for (const bucket of REAL_BUCKETS) counts[bucket] = 0;
  for (const entry of Object.values(nodes)) {
    counts.all += 1;
    counts[bucketFor(entry)] += 1;
  }

  // "All" has no selection state of its own -- it's derived: filled in
  // only when every individual status tile is currently selected
  // (docs/STATUS_FILTER_PLAN.md S3).
  const allSelected = REAL_BUCKETS.every((b) => selectedBuckets.has(b));
  // With 0 or 1 non-empty status bucket, "All" would just duplicate that
  // bucket's own count and toggle button -- only earns its keep as a bulk
  // control once there are 2+ buckets to select across.
  const visibleBucketCount = REAL_BUCKETS.filter((b) => counts[b] > 0).length;

  const row = document.getElementById("summary-row");
  // Zero-count tiles are hidden rather than shown as empty -- filtering
  // SUMMARY_TILES (instead of reordering it) means a tile that later goes
  // from 0 back to >0 reappears at its original fixed position, not
  // appended wherever it last changed.
  row.innerHTML = SUMMARY_TILES.filter((t) => {
    if (t.bucket === "all") return visibleBucketCount > 1;
    return counts[t.bucket] > 0;
  }).map((t) => {
    const isSelected = t.bucket === "all" ? allSelected : selectedBuckets.has(t.bucket);
    return `<div class="tile tile--${t.bucket}${isSelected ? " is-selected" : ""}" data-bucket="${t.bucket}">
      <div class="tile__count">${counts[t.bucket]}</div>
      <div class="tile__label">${t.label}</div>
    </div>`;
  }).join("");
}

// Clicking "all" flips the derived all-selected boolean as a single unit
// (deselect everything, or reselect everything); clicking any other tile
// toggles just that status. Either way, both the tiles' underlines and
// the fleet list below need to reflect the new selection.
document.getElementById("summary-row").addEventListener("click", (e) => {
  const tile = e.target.closest(".tile");
  if (!tile) return;
  const bucket = tile.dataset.bucket;

  if (bucket === "all") {
    const allSelected = REAL_BUCKETS.every((b) => selectedBuckets.has(b));
    selectedBuckets.clear();
    if (!allSelected) REAL_BUCKETS.forEach((b) => selectedBuckets.add(b));
  } else if (selectedBuckets.has(bucket)) {
    selectedBuckets.delete(bucket);
  } else {
    selectedBuckets.add(bucket);
  }

  renderSummary(state.lastNodes);
  if (editingNodeId === null && editingDeviceTypeNodeId === null) renderFleetList(state.lastNodes);
});

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
  // "Stopped" rather than "Idle" for the pill: it says what an operator can
  // see at the machine. The tile keeps the shorter "Idle".
  idle: "Stopped",
  tripped: "Tripped",
};

// ---------------------------------------------------------------------
// Machinery protection (docs/MOTOR_STOP_PLAN.md)
// ---------------------------------------------------------------------

// How many motors the rig exposes. Unavoidably a second copy of
// motor-driver/run_demo.py's MOTOR_IDS -- that's a standalone script on a
// different machine, with no shared module to import from. Keep in sync.
const TRIP_MOTOR_COUNT = 3;

// nodeId -> epoch ms the trip fires at. The server sends a remaining-seconds
// figure, but GET /nodes only refreshes every 5s, so a 10s countdown would
// visibly jump 10 -> 5 -> 0. Converting to a local deadline once and ticking
// against it keeps the number honest between polls, while the server stays the
// source of truth for whether a countdown exists at all.
const tripDeadlines = {};

function noteTripCountdown(entry) {
  const p = entry.protection;
  if (!p || p.trip_in_s === null || p.trip_in_s === undefined) {
    delete tripDeadlines[entry.node_id];
    return;
  }
  tripDeadlines[entry.node_id] = Date.now() + p.trip_in_s * 1000;
}

function tripSecondsLeft(nodeId) {
  const deadline = tripDeadlines[nodeId];
  if (deadline === undefined) return null;
  return Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
}

function protectionStatusText(entry) {
  const p = entry.protection || {};
  const left = tripSecondsLeft(entry.node_id);
  if (left !== null) return `Tripping in ${left}s…`;
  if (p.trip_failed) return "Trip failed — machine still running";
  if (entry.status === "tripped") {
    const when = p.tripped_at
      ? new Date(p.tripped_at * 1000).toLocaleTimeString()
      : null;
    return when ? `Tripped ${when} · confirmed stopped` : "Tripped · confirmed stopped";
  }
  if (!p.armed) return "Not armed";
  return "Armed";
}

// Re-renders only the status line's text, never the whole row: a full
// re-render every 500ms would blow away the <select>'s focus and any open
// dropdown out from under the operator.
function tickTripCountdowns() {
  for (const el of document.querySelectorAll("[data-trip-status]")) {
    const nodeId = el.dataset.nodeId;
    const entry = state.lastNodes[nodeId];
    if (!entry) continue;
    const text = protectionStatusText(entry);
    if (el.textContent !== text) el.textContent = text;
    // The Hold button is only meaningful while a countdown is actually
    // running, so it disappears the moment the trip fires.
    const hold = document.querySelector(`[data-action="protection_hold"][data-node-id="${CSS.escape(nodeId)}"]`);
    if (hold) hold.hidden = tripSecondsLeft(nodeId) === null;
  }
}
setInterval(tickTripCountdowns, 500);

// Stopped baseline (pipeline/stopped_baseline.py). Lives in the Protection
// section because that's when an operator has a reason to care: without one,
// the running/stopped gate can't reliably tell a stopped machine from a
// running one, so a trip can never confirm and the asset can never read IDLE.
//
// The "machine off" instruction rides in the button label rather than a hint
// line under the control. It is the one thing nothing in software can check
// (stopped_baseline.py's module docstring), and a capture taken with the
// machine running teaches the gate the opposite of what it needs.
function stoppedBaselineRowHtml(entry) {
  const safeId = escapeHtml(entry.node_id);
  const progress = entry.stopped_baseline_progress;
  const measured = entry.stopped_energy_ref !== null
    && entry.stopped_energy_ref !== undefined;

  let state;
  let buttons;
  if (progress) {
    const enough = progress.collected >= progress.min_frames;
    state = `Measuring ${progress.collected}/${progress.min_frames}`;
    buttons =
      `<button type="button" class="btn-label" data-action="stopped_baseline_stop" `
      + `data-node-id="${safeId}"${enough ? "" : " disabled"}>Save</button>`
      + `<button type="button" class="btn-label" data-action="stopped_baseline_cancel" `
      + `data-node-id="${safeId}">Cancel</button>`;
  } else {
    state = measured ? "Measured" : "Not measured";
    buttons =
      `<button type="button" class="btn-label" data-action="stopped_baseline_start" `
      + `data-node-id="${safeId}">${measured ? "Re-measure" : "Measure"} with machine off</button>`;
  }

  return `<div class="protection__row">
      <span class="protection__label">Stopped baseline</span>
      <span class="protection__state${measured || progress ? "" : " protection__state--missing"}" `
    + `data-baseline-state data-node-id="${safeId}">${escapeHtml(state)}</span>
      ${buttons}
    </div>`;
}

function protectionSectionHtml(entry) {
  const p = entry.protection;
  // Absent only when the backend has no ProtectionController at all -- render
  // nothing rather than an empty section promising a control that can't work.
  if (!p) return "";

  // escapeHtml, not a separate escapeAttr: this module's escapeHtml already
  // escapes both quote characters, and every other attribute interpolation in
  // app.js uses it the same way. charts.js has its own escapeAttr; app.js
  // never did.
  const safeId = escapeHtml(entry.node_id);
  const current = entry.trip_motor_idx;
  // One motor, one asset: a motor another node already claims is shown but
  // disabled, so the constraint is visible instead of only surfacing as a 409
  // after the operator picks it.
  const claimed = new Map();
  for (const other of Object.values(state.lastNodes)) {
    if (other.node_id !== entry.node_id && other.trip_motor_idx) {
      claimed.set(other.trip_motor_idx, other.device_name || other.node_id);
    }
  }

  let options = `<option value=""${current ? "" : " selected"}>No trip output</option>`;
  for (let idx = 1; idx <= TRIP_MOTOR_COUNT; idx += 1) {
    const owner = claimed.get(idx);
    options += `<option value="${idx}"${current === idx ? " selected" : ""}${owner ? " disabled" : ""}>`
      + `Motor ${idx}${owner ? ` — used by ${escapeHtml(owner)}` : ""}</option>`;
  }

  const countingDown = tripSecondsLeft(entry.node_id) !== null;
  return `<div class="protection" data-role="protection">
    <div class="protection__title">Protection</div>
    <div class="protection__row">
      <label class="protection__label" for="trip-motor-${safeId}">Trip output</label>
      <select class="protection__select" id="trip-motor-${safeId}" data-action="set_trip_motor" data-node-id="${safeId}">${options}</select>
    </div>
    <div class="protection__row">
      <span class="protection__label">Status</span>
      <span class="protection__state${p.trip_failed ? " protection__state--failed" : ""}${countingDown ? " protection__state--counting" : ""}" data-trip-status data-node-id="${safeId}">${escapeHtml(protectionStatusText(entry))}</span>
      <button type="button" class="btn-label btn-label--danger" data-action="protection_hold" data-node-id="${safeId}"${countingDown ? "" : " hidden"}>Hold</button>
    </div>
    ${stoppedBaselineRowHtml(entry)}
  </div>`;
}

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
  if (entry.status === "commissioning_training") {
    // Progress lives here, not on the button (rowControls() below stays a
    // plain "Training…") -- collecting's progress was already in the
    // status text, so training's % belongs in the same place instead of
    // a second, inconsistent location.
    const tp = trainingProgress[entry.node_id];
    if (tp) return `Training ${Math.round((100 * tp.epoch) / tp.total_epochs)}%`;
  }
  return STATUS_LABEL[entry.status];
}

// Commission/train collapsed into a single morphing labeled button --
// previously two icon buttons that read as independent controls even
// though pressing "train" only ever means "stop collecting AND train".
// Mirrors the transition guards in registry/registry.py exactly:
//   uncommissioned          -> "Commission"   (commission_start)
//   commissioning_collecting (not enough frames yet) -> disabled, label
//     mirrors the status pill's own "Collecting n/min" text
//   commissioning_collecting (ready) -> "Train" (commission_stop)
//   commissioning_training  -> disabled "Training…"
//   healthy/warning/fault   -> "Recommission" (commission_start again)
//   paused                  -> disabled "Recommission" (resume() first,
//     matches PAUSED -> COMMISSIONING_COLLECTING being deliberately
//     absent from the state machine)
// Pause/Resume only enabled once a model exists (healthy/warning/
// fault/paused) -- matches pause()/resume()'s own guards.
// Remove (decommission) is always enabled in every status (S3.9:
// registry.decommission() is now removable from any status).
function rowControls(entry) {
  const status = entry.status;
  const progress = entry.commissioning_progress;
  const readyToTrain = status === "commissioning_collecting"
    && progress !== undefined && progress.collected >= progress.min_frames;

  let commissionLabel, commissionAction, commissionEnabled, commissionTooltip, commissionVariant;
  if (status === "uncommissioned") {
    commissionLabel = "Commission"; commissionAction = "commission_start";
    commissionEnabled = true; commissionVariant = "action";
    commissionTooltip = "Start collecting baseline data";
  } else if (status === "commissioning_collecting" && readyToTrain) {
    commissionLabel = "Train"; commissionAction = "commission_stop";
    commissionEnabled = true; commissionVariant = "ready";
    commissionTooltip = "Stop collecting and train";
  } else if (status === "commissioning_collecting") {
    commissionLabel = "Collecting…"; commissionAction = null;
    commissionEnabled = false; commissionVariant = "pending";
    commissionTooltip = `Collecting… (${progress ? progress.collected : 0}/${progress ? progress.min_frames : "?"})`;
  } else if (status === "commissioning_training") {
    // Plain label -- the % lives in the status pill (statusLabelFor())
    // instead, same place Collecting's progress already lives, so there's
    // exactly one place to look for "how far along is this," not two.
    commissionLabel = "Training…"; commissionAction = null;
    commissionEnabled = false; commissionVariant = "pending";
    commissionTooltip = "Training in progress";
  } else if (status === "healthy" || status === "warning" || status === "fault") {
    commissionLabel = "Recommission"; commissionAction = "commission_start";
    commissionEnabled = true; commissionVariant = "action";
    commissionTooltip = "Recollect baseline and retrain";
  } else if (status === "idle" || status === "tripped") {
    // Also correctly disabled, but for a different reason than paused, so it
    // says so: commissioning collects gated *running* frames, and there are
    // none to collect from a machine that isn't turning. Matches
    // start_commissioning's source states, which exclude both.
    commissionLabel = "Recommission"; commissionAction = null;
    commissionEnabled = false; commissionVariant = "pending";
    commissionTooltip = "Start the machine first to recommission";
  } else {
    commissionLabel = "Recommission"; commissionAction = "commission_start";
    commissionEnabled = false; commissionVariant = "pending";
    commissionTooltip = "Resume first to recommission";
  }

  const pauseResumeEnabled = status === "healthy" || status === "warning"
    || status === "fault" || status === "paused";
  const pauseResumeAction = status === "paused" ? "resume" : "pause";
  const pauseResumeTooltip = status === "paused" ? "Resume" : "Pause";

  return {
    commissionLabel, commissionAction, commissionEnabled, commissionTooltip, commissionVariant,
    pauseResumeAction, pauseResumeEnabled, pauseResumeTooltip,
  };
}

// Capture + label (docs/EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md S2) --
// deliberately independent of `status`/rowControls() above: capture never
// touches NodeStatus. 2026-07-24 round 6 redesign: lives in its own
// slide-over drawer now (recordDrawerBackdrop/recordDrawer below), not the
// row's expanded detail panel -- the old design shared one toggle between
// "expand row to see charts" and "record," so there was no way to dismiss
// just the capture form ("we can never remove this once we pressed
// record"). The drawer has its own close button and is a top-level
// element outside #fleet-list, so closing it never touches the row's
// expand state or the capture session itself (which lives server-side,
// independent of any UI).
//
// Label is chosen BEFORE starting (not after stopping) -- Save reuses it
// automatically the moment the capture stops, whether that's the operator
// clicking Save or the server's own auto-stop at target_frames reaching
// it (captureLabelByNode + the "capture" WS handler below), so there's no
// separate post-stop "now type a label" step.

// node_id -> the current label text, kept live from the FIRST keystroke
// (or dropdown pick), not just captured at Start -- recordDrawerBodyHtml()
// reads this as the input's value on every render, so a poll/WS tick
// arriving mid-type redraws the same text instead of blanking the field
// back to empty. Also doubles as "the label to auto-save with" once
// state reaches "stopped" (server-independent, since CaptureSession has
// no notion of a label until save()) -- see maybeAutoSaveCapture() and
// the "capture" WS handler. Cleared on Start->stop->save completing, or
// on Cancel.
const captureLabelByNode = {};

// Same live-draft idea as captureLabelByNode above, for the frame-count
// field -- keeps a typed value from vanishing on a background re-render
// before Start is clicked. Stored as the raw typed string (not a number)
// so a partial/in-progress edit round-trips exactly as typed.
const captureTargetDraftByNode = {};

// Keeps captureLabelByNode and the Start button's disabled state in sync
// with the label input's current value -- called on every keystroke AND
// every dropdown-suggestion pick. A plain `input.value = x` assignment
// (the suggestion click handler below) does NOT fire a native "input"
// event, so without this being called explicitly from both places, Start
// stayed disabled forever after picking a suggestion (2026-07-24 bug
// report: "start button ... simply clears the dropdown value").
function syncCaptureLabelState(input) {
  const nodeId = openRecordNodeId;
  const value = input.value.trim();
  if (value) captureLabelByNode[nodeId] = value;
  else delete captureLabelByNode[nodeId];
  const entry = state.lastNodes[nodeId];
  const startBtn = recordDrawer.querySelector('[data-action="capture_start"]');
  if (startBtn) startBtn.disabled = !(value && entry && entry.device_type);
}

function maybeAutoSaveCapture(nodeId) {
  const label = captureLabelByNode[nodeId];
  if (!label) return;
  delete captureLabelByNode[nodeId]; // before the await, so a near-simultaneous
  // WS+poll detection can't both fire this. Frame count + device_type read
  // here, while state.lastNodes still reflects the just-stopped batch --
  // for the toast message only (save() itself doesn't need either).
  const entry = state.lastNodes[nodeId];
  const cp = entry && entry.capture_progress;
  saveCapture(nodeId, label, cp ? cp.collected : null, entry ? entry.device_type : null);
}

// node_id of whichever node's Record drawer is open right now, or null --
// a singleton (only one node can be actively recorded/viewed at a time),
// unlike the old per-row toolbar this replaces.
let openRecordNodeId = null;

// The drawer is a top-level element, not part of #fleet-list's innerHTML,
// so renderFleetList()'s 5s-poll/WS-driven rebuild can never wipe an
// in-progress label/frame-count edit out from under the operator the way
// the old in-row toolbar could (2026-07-24 round 6). Created once, eagerly
// (not lazily like toastContainer()) since both the backdrop and the
// drawer need their event listeners attached exactly once, further down.
const recordDrawerBackdrop = document.createElement("div");
recordDrawerBackdrop.id = "record-drawer-backdrop";
recordDrawerBackdrop.className = "record-drawer-backdrop";
recordDrawerBackdrop.hidden = true;
document.body.appendChild(recordDrawerBackdrop);

const recordDrawer = document.createElement("div");
recordDrawer.id = "record-drawer";
recordDrawer.className = "record-drawer";
recordDrawer.hidden = true;
document.body.appendChild(recordDrawer);

// Renders the whole capture control into the singleton drawer below --
// asset class (read-only), Label/target inputs, Start (idle) or Save+Cancel
// (capturing/stopped). No "Idle" status line/dot -- 2026-07-24 round 6:
// "i dont know why we need a idle status," and the Start button already
// says nothing's recording, so a second line repeating that was pure
// noise. The dot+status line only appears once a capture is actually
// active.
function recordDrawerBodyHtml(entry) {
  const headerHtml = `<div class="record-drawer__header">
    <span class="record-drawer__title">Record — ${escapeHtml(entry.device_name)}</span>
    <button type="button" class="record-drawer__close" data-action="record_drawer_close" aria-label="Close">&times;</button>
  </div>`;

  // Asset class (device_type) is required before any capture can start,
  // and it's no longer editable from here -- 2026-07-24 round 8: an
  // earlier version had a "Set/Change device type" control right in this
  // drawer, a second live editor for a field whose single source of truth
  // is the Fleet row's pill (motorRowHtml()/startDeviceTypeEdit()).
  // Instead of a small hint line next to a still-usable form, this fully
  // replaces the capture form with a blocking message + a jump back to
  // the Fleet row -- there's nothing else to do in here until it's set.
  if (!entry.device_type) {
    return `${headerHtml}
    <div class="record-drawer__body">
      <div class="record-drawer__block">
        <p class="record-drawer__block-text">This asset has no class assigned yet. Recordings are grouped by asset class -- one fault-detection model gets trained per class -- so recording can't start until this asset has one.</p>
        <button type="button" class="btn-label btn-label--ready" data-action="jump_to_fleet_asset_class">Go to Fleet</button>
      </div>
    </div>`;
  }

  const cp = entry.capture_progress;
  // "stopped" is a near-instant transient (auto-save fires the moment
  // it's observed) -- rendered the same as "capturing", never its own
  // visible state.
  const active = !!cp && cp.state !== "idle";
  const collected = cp ? (cp.collected || 0) : 0;
  const targetFrames = cp ? cp.target_frames : null;
  const label = captureLabelByNode[entry.node_id] || "";
  // Blank by default (not a prefilled "50") so the "0 = indefinite"
  // placeholder is actually visible -- 2026-07-24: a prefilled value
  // hides its own placeholder text, which is why the hint never showed.
  const targetDraft = targetFrames != null ? targetFrames
    : active ? 0
    : (captureTargetDraftByNode[entry.node_id] || "");

  const canStart = !!label;

  // .btn-label, not .btn-primary -- 2026-07-24: the borrowed
  // tools/raw_capture_server.py button style ("doesn't follow the style
  // of other buttons") didn't match Commission/Record/Pause's compact
  // look. Same "only tint the one moment worth flagging" language as
  // Commission/Train: Start/Cancel stay neutral, Save gets the green
  // affirmative tint (analogous to Train's blue).
  const buttonsHtml = active
    ? `<button class="btn-label btn-label--save" data-action="capture_stop" title="Save this capture now" aria-label="Save this capture now">Save</button>
       <button class="btn-label btn-label--cancel" data-action="capture_cancel" title="Discard without saving" aria-label="Discard without saving">Cancel</button>`
    // Disabled until a label exists (asset class is already guaranteed by
    // this point -- see the blocking-state early return above) --
    // 2026-07-24: clicking Start with no label used to just refocus the
    // field, confusing enough that it reads better as an unclickable
    // button (kept in sync live by syncCaptureLabelState(), called on
    // every keystroke AND every dropdown pick).
    : `<button class="btn-label" data-action="capture_start" title="Start capturing" aria-label="Start capturing" ${canStart ? "" : "disabled"}>Start</button>`;

  const statusHtml = active
    ? `<div class="capture-toolbar__status">
        <span class="capture-dot capture-dot--active"></span>
        <span>Recording "${escapeHtml(label)}" — ${collected}${targetFrames ? ` / ${targetFrames}` : ""} frame${collected === 1 ? "" : "s"}</span>
      </div>`
    : "";

  return `${headerHtml}
  <div class="record-drawer__body">
    <div class="record-drawer__device-type-row">
      <label>Asset class</label>
      <div><span class="record-drawer__device-type">${escapeHtml(entry.device_type)}</span></div>
    </div>
    <div class="capture-toolbar__label">
      <label>Label</label>
      <div class="motor-row__capture-label-wrap">
        <input type="text" class="motor-row__capture-label-input" data-role="capture-label-input" placeholder="eg: healthy" autocomplete="off" value="${escapeHtml(label)}" ${active ? "disabled" : ""} />
        <span class="motor-row__capture-label-arrow" aria-hidden="true">&#9662;</span>
        <div class="motor-row__capture-suggestions" data-role="capture-suggestions" hidden></div>
      </div>
    </div>
    <div class="capture-toolbar__label">
      <label>Frame count</label>
      <input type="number" min="0" step="1" data-role="capture-target-input" placeholder="0 = indefinite" value="${targetDraft}" title="0 = capture indefinitely, stop manually" ${active ? "disabled" : ""} />
    </div>
    <div class="record-drawer__actions">${buttonsHtml}</div>
    ${statusHtml}
  </div>`;
}

// Re-renders the drawer's content from current state -- called whenever
// something the drawer displays changes (open/close, capture_progress,
// device_type/device_name) via pollNodes()/the WS handler below.
function renderRecordDrawer() {
  if (!openRecordNodeId) {
    recordDrawer.hidden = true;
    recordDrawerBackdrop.hidden = true;
    return;
  }
  const entry = state.lastNodes[openRecordNodeId];
  if (!entry) {
    openRecordNodeId = null;
    recordDrawer.hidden = true;
    recordDrawerBackdrop.hidden = true;
    return;
  }
  // Don't wipe an in-progress label/frame-count edit out from under the
  // operator on a background poll/WS tick -- same guard editingNodeId
  // uses for the row list's own rebuild (app.js's long-standing pattern).
  if (recordDrawer.contains(document.activeElement)
      && document.activeElement.tagName === "INPUT") {
    return;
  }
  recordDrawer.hidden = false;
  recordDrawerBackdrop.hidden = false;
  recordDrawer.innerHTML = recordDrawerBodyHtml(entry);
}

// "Record" button (motor-row__actions) -- opens the drawer for this node,
// also expanding the row (if collapsed) so the live charts are visible
// alongside it -- the two are independent now (2026-07-24 round 6): either
// can be opened/closed without affecting the other, unlike the old design
// where Record's whole job was just revealing the same panel charts lived
// in.
function openRecordDrawer(nodeId) {
  openRecordNodeId = nodeId;
  if (!expandedNodeIds.has(nodeId)) {
    expandedNodeIds.add(nodeId);
    renderFleetList(state.lastNodes);
  }
  renderRecordDrawer();
  // Deliberately NOT auto-focusing the label input here (2026-07-24 round
  // 7: "only when i press the label text box should the drop down
  // appear") -- an earlier version auto-focused on open, which also
  // auto-showed the suggestions dropdown as a side effect (see
  // recordDrawer's focusin listener). The suggestions box should only
  // ever appear from a deliberate click/tab into the field.
}

// Closing never touches the capture session -- it runs server-side,
// independent of any UI (pipeline/capture.py), so a capture in progress
// just keeps collecting in the background; the row's Record button itself
// turns into a red, pulsing dot (motorRowHtml()'s isRecording) so the
// operator can tell it's still going and reopen to Save/Cancel
// (2026-07-24 round 6: "we can never remove this once we pressed record"
// -- the fix is this close button existing at all, not blocking or
// discarding on close).
function closeRecordDrawer() {
  openRecordNodeId = null;
  renderRecordDrawer();
}

// "Go to Fleet" (recordDrawerBodyHtml()'s blocking state, 2026-07-24 round
// 8) -- closes the drawer and briefly pulses the row's outline so the
// operator can actually find it among the rest of the list, then scrolls
// it into view. Doesn't open the row's asset-class editor itself: this is
// navigation, not a second edit path into a field whose only editor is
// that pill (the whole reason the drawer's old inline "Change" control was
// removed).
function jumpToFleetForAssetClass(nodeId) {
  closeRecordDrawer();
  highlightNodeIds.add(nodeId);
  renderFleetList(state.lastNodes);
  const row = document.querySelector(`.motor-row-group[data-node-id="${CSS.escape(nodeId)}"]`);
  if (row) row.scrollIntoView({ behavior: "smooth", block: "center" });
  setTimeout(() => {
    highlightNodeIds.delete(nodeId);
    renderFleetList(state.lastNodes);
  }, 1600);
}

recordDrawerBackdrop.addEventListener("click", closeRecordDrawer);

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && openRecordNodeId
      && !recordDrawer.contains(document.activeElement)) {
    closeRecordDrawer();
  }
});

const ACTION_ENDPOINT = {
  commission_start: (id) => ["POST", `/nodes/${id}/commission/start`],
  commission_stop: (id) => ["POST", `/nodes/${id}/commission/stop`],
  pause: (id) => ["POST", `/nodes/${id}/pause`],
  resume: (id) => ["POST", `/nodes/${id}/resume`],
  decommission: (id) => ["POST", `/nodes/${id}/decommission`],
  capture_stop: (id) => ["POST", `/nodes/${id}/capture/stop`],
  capture_cancel: (id) => ["POST", `/nodes/${id}/capture/cancel`],
  protection_hold: (id) => ["POST", `/nodes/${id}/protection/hold`],
  stopped_baseline_start: (id) => ["POST", `/nodes/${id}/stopped_baseline/start`],
  stopped_baseline_cancel: (id) => ["POST", `/nodes/${id}/stopped_baseline/cancel`],
  // stopped_baseline_stop is deliberately absent -- it reports back what it
  // measured, so it goes through saveStoppedBaseline() instead of
  // runAction()'s fire-and-forget shape. Same reason saveCapture() does.
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

// Top-of-page confirmation banner (2026-07-24: "no need anything below
// record button... a popup at top like the example of edge impulse
// shows when we save an impulse") -- a transient, dismissible toast
// instead of a persistent line under the button, so the confirmation is
// unmissable in the moment but doesn't permanently take up row space.
function toastContainer() {
  let el = document.getElementById("toast-container");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast-container";
    document.body.appendChild(el);
  }
  return el;
}

function showToast(message, kind = "success") {
  const toast = document.createElement("div");
  toast.className = `toast toast--${kind}`;
  const icon = document.createElement("span");
  icon.className = "toast__icon";
  icon.textContent = kind === "success" ? "✓" : "!";
  const text = document.createElement("span");
  text.className = "toast__message";
  text.textContent = message; // textContent, not innerHTML -- message can carry a user-typed label
  const close = document.createElement("button");
  close.className = "toast__close";
  close.setAttribute("aria-label", "Dismiss");
  close.textContent = "×";
  close.addEventListener("click", () => toast.remove());
  toast.append(icon, text, close);
  toastContainer().appendChild(toast);
  setTimeout(() => toast.remove(), 4000);
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

// Save needs a body (the label) unlike every other action above, so it
// doesn't fit ACTION_ENDPOINT/runAction's no-body POST shape -- called
// from maybeAutoSaveCapture() once a capture reaches "stopped" (manual
// Save click or the server's own auto-stop), not directly from a click
// handler.
async function saveCapture(nodeId, label, count, deviceType) {
  try {
    await api("POST", `/nodes/${nodeId}/capture/save`, { label });
    await fetchCaptureLabels();
    const frameText = count != null ? `${count} frame${count === 1 ? "" : "s"}` : "capture";
    const deviceTypeText = deviceType ? `, asset class "${deviceType}"` : "";
    showToast(`Saved ${frameText} to "${label}"${deviceTypeText}`);
  } catch (err) {
    console.error(`Save capture on ${nodeId} failed`, err);
    alert(`Save capture failed: ${err.message}`);
  }
  await pollNodes();
}

// Reports what it measured (and, on failure, *why* the collected frames
// don't look like a stopped machine), so it doesn't fit runAction's
// fire-and-forget shape -- same reason saveCapture() above doesn't.
async function saveStoppedBaseline(nodeId) {
  try {
    const data = await api("POST", `/nodes/${nodeId}/stopped_baseline/stop`);
    const frames = (data.stopped_baseline_result || {}).frames;
    showToast(`Stopped baseline measured from ${frames} frames`);
  } catch (err) {
    console.error(`Save stopped baseline on ${nodeId} failed`, err);
    // alert, not a toast: the failures here are instructions (the machine
    // was still moving, keep collecting) and a 4s auto-dismiss would drop
    // them before they'd been read.
    alert(err.message);
  }
  await pollNodes();
}

// Needs the typed target-frames value -- same reason saveCapture() above
// doesn't fit runAction/ACTION_ENDPOINT's no-body POST shape.
async function startCapture(nodeId, targetFrames) {
  try {
    await api("POST", `/nodes/${nodeId}/capture/start`,
               { target_frames: targetFrames });
  } catch (err) {
    console.error(`Start capture on ${nodeId} failed`, err);
    alert(`Start capture failed: ${err.message}`);
  }
  await pollNodes();
}

// device_name is user-editable (rename) and node_id can carry arbitrary
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

// Same idiom as charts.js's own titleCase() (fault classification bars) --
// recording labels are the operator's own words (bearing/loose/unbalanced/
// healthy), not Edge Impulse jargon, so title-casing is all display needs.
// Duplicated rather than imported: charts.js doesn't expose it on its
// public Charts object, and it's a one-liner.
function titleCase(label) {
  return String(label).replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

// Inline SVGs rather than an emoji/icon-font dependency -- consistent
// rendering across platforms with no extra asset or build step. Only
// commission/train stays a labeled text button (see rowControls()/
// motorRowHtml()) -- everything else, including Record as of 2026-07-24
// Round 7, is icon-only for a consistent look across the action row
// (teammate: "go with the complete button like pause and delete").
const ICON_PAUSE = '<svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor"><path d="M6 5h4v14H6zM14 5h4v14h-4z"/></svg>';
const ICON_PLAY = '<svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>';
const ICON_TRASH = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>';
// Plain filled circle -- the universal "record" glyph across cameras,
// voice memo apps, and screen/video recorders (a red dot, sometimes
// becoming a square to mean "stop"). Kept as a dot rather than switching
// to a square while active: this button always just opens the drawer
// (Start/Save/Cancel live there), it never itself stops a capture, so a
// "stop" glyph would misrepresent what clicking it does. Color (neutral
// vs. red+pulsing) carries the idle/active distinction instead -- see
// .btn-icon--recording in style.css.
const ICON_RECORD = '<svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor"><circle cx="12" cy="12" r="7"/></svg>';

// Suppresses re-render of the list while a rename input is open, so an
// in-flight edit isn't wiped out by the next 5s poll (same guard the old
// dashboard used for this exact race).
let editingNodeId = null;

// Same guard as editingNodeId above, for the device-type pill's edit mode
// (motorRowHtml(), startDeviceTypeEdit()) -- each recording belongs to a
// device type (2026-07-24), scoping which Edge Impulse project/model a
// node's captures feed into (docs/EDGE_IMPULSE_DASHBOARD_WORKFLOW_PLAN.md
// S1). Lives right next to the device name since it's the same kind of
// identity field, edited the same click-to-edit way.
let editingDeviceTypeNodeId = null;

// Distinct device_type ("asset class") values already assigned across the
// fleet (GET /device_types), backing the pill's suggestions dropdown --
// same reasoning as captureLabels below (avoid near-duplicate classes
// fragmenting the capture/label grouping this field exists for).
let deviceTypes = [];

// Deterministic pill color per asset-class value, cycling through a fixed
// categorical palette by string hash -- lets an operator tell classes
// apart at a glance across the fleet list (e.g. "all the blue pills are
// conveyor motors") instead of re-reading each pill's text every time.
const ASSET_CLASS_PALETTE = [
  "#38bdf8", "#a78bfa", "#fb923c", "#34d399",
  "#f472b6", "#facc15", "#60a5fa", "#4ade80",
];

function hashString(str) {
  let h = 0;
  for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) | 0;
  return Math.abs(h);
}

function assetClassColor(value) {
  return ASSET_CLASS_PALETTE[hashString(value) % ASSET_CLASS_PALETTE.length];
}

async function fetchDeviceTypes() {
  try {
    const data = await api("GET", "/device_types");
    deviceTypes = data.device_types || [];
  } catch (err) {
    console.error("Failed to fetch device types", err);
  }
}

function renderDeviceTypeSuggestions(input) {
  const box = input.parentElement.querySelector('[data-role="device-type-suggestions"]');
  if (!box) return;
  const query = input.value.trim();
  const queryLower = query.toLowerCase();
  const matches = queryLower
    ? deviceTypes.filter((t) => t.toLowerCase().includes(queryLower))
    : deviceTypes;
  // Existing classes first (dropdown-first, to steer operators toward
  // reusing one instead of typing a near-duplicate) -- a pinned "+ Add new
  // class" row only appears once what's typed doesn't already match one
  // exactly, so free text is still possible but reuse is the path of
  // least resistance.
  const isNewClass = queryLower && !deviceTypes.some((t) => t.toLowerCase() === queryLower);
  if (!matches.length && !isNewClass) {
    box.hidden = true;
    box.innerHTML = "";
    return;
  }
  const matchesHtml = matches.map((t) =>
    `<button type="button" class="device-type-suggestion" data-value="${escapeHtml(t)}">${escapeHtml(t)}</button>`
  ).join("");
  const addNewHtml = isNewClass
    ? `<button type="button" class="device-type-suggestion device-type-suggestion--new" data-value="${escapeHtml(query)}">+ Add new class "${escapeHtml(query)}"</button>`
    : "";
  box.innerHTML = matchesHtml + addNewHtml;
  box.hidden = false;
}

function hideDeviceTypeSuggestions(input) {
  const box = input.parentElement.querySelector('[data-role="device-type-suggestions"]');
  if (box) box.hidden = true;
}

// Click-to-edit for the asset-class pill (motorRowHtml()) -- mirrors
// startRename()/commitRename() below exactly, the established pattern for
// this kind of identity field. This pill is the ONLY editor for
// device_type -- the Record drawer only ever links back here
// (jumpToFleetForAssetClass()) rather than duplicating a second live
// editor (recordDrawerBodyHtml()'s comment).
function startDeviceTypeEdit(nodeId) {
  editingDeviceTypeNodeId = nodeId;
  renderFleetList(state.lastNodes);
  // Deferred past the triggering click's bubble phase -- same race as
  // openRecordDrawer()'s comment above: renderFleetList() just replaced
  // the clicked pill/"Change" button with a fresh input, detaching the
  // original click target from the document, so a synchronous focus()
  // here would show the suggestions box only for document's
  // outside-click-closer to immediately hide it again (its `.contains()`
  // check can never match a detached node).
  setTimeout(() => {
    const input = document.querySelector(
      `.motor-row-group[data-node-id="${nodeId}"] [data-role="device-type-input"]`);
    if (input) {
      input.focus();
      input.select();
    }
  }, 0);
}

// Unlike commitRename() (blank = keep the old name), a blank device_type
// is a real, meaningful value here -- "clear it back to unassigned" -- so
// this always calls the API, matching the backend's own contract
// (api/app.py's DeviceTypeBody: empty string clears).
//
// Always lowercased before saving -- asset class is the key that groups
// captures into one training set per Edge Impulse project, so "Conveyor",
// "conveyor motor", and "Conveyor Motor " being three different values
// would silently fragment the same machine's data across separate models.
// Applies regardless of whether the value came from typing or picking an
// existing suggestion, so it's a no-op for anything already normalized.
async function commitDeviceType(nodeId, value) {
  editingDeviceTypeNodeId = null;
  const trimmed = value.trim().toLowerCase();
  try {
    await api("POST", `/nodes/${nodeId}/device_type`, { device_type: trimmed });
    await fetchDeviceTypes();
  } catch (err) {
    console.error("Set device type failed", err);
    alert(`Set device type failed: ${err.message}`);
  }
  await pollNodes();
}

// node_id -> {epoch, total_epochs}, populated by the "training_progress"
// WS broadcast (api/app.py's commission/stop, throttled to ~20 ticks) and
// read by rowControls() to append a live "Training… 42%" label. Cleared
// whenever a "registry" push shows the node has left commissioning_training
// (completed, or a fresh commission started), so a stale percentage from
// a previous run can never linger into the next one.
const trainingProgress = {};

// Previously-used capture labels (pipeline/capture.py's list_labels()),
// backing the custom suggestions dropdown every capture-label <input>
// filters against (S2). Fetched once at startup and again after every
// successful save, since save() may have just introduced a brand new
// label. Not a native <datalist> -- its browser-default popup styling
// clashed with the rest of this dark UI, so the dropdown is hand-built
// below (renderCaptureSuggestions()) instead.
let captureLabels = [];

async function fetchCaptureLabels() {
  try {
    const data = await api("GET", "/captures/labels");
    captureLabels = data.labels || [];
  } catch (err) {
    console.error("Failed to fetch capture labels", err);
  }
}

// Filters captureLabels against the input's current text (substring,
// case-insensitive; empty shows everything) and (re)populates/toggles the
// sibling suggestions box. Called on focus (show everything) and on every
// keystroke (refilter) -- see the delegated listeners below.
function renderCaptureSuggestions(input) {
  const box = input.parentElement.querySelector('[data-role="capture-suggestions"]');
  if (!box) return;
  const query = input.value.trim().toLowerCase();
  const matches = query
    ? captureLabels.filter((l) => l.includes(query))
    : captureLabels;
  if (!matches.length) {
    box.hidden = true;
    box.innerHTML = "";
    return;
  }
  box.innerHTML = matches.map((l) =>
    `<button type="button" class="motor-row__capture-suggestion" data-label="${escapeHtml(l)}">${escapeHtml(l)}</button>`
  ).join("");
  box.hidden = false;
}

function hideCaptureSuggestions(input) {
  const box = input.parentElement.querySelector('[data-role="capture-suggestions"]');
  if (box) box.hidden = true;
}

// Node IDs currently showing their expanded detail panel (node ID +
// anomaly score -- the two fields dropped from the compact row). Rebuilt
// fresh from scratch on every render rather than stored per-DOM-node, so
// it survives the innerHTML replace each poll does.
const expandedNodeIds = new Set();

// Which nodes have their "Scalar values" / "Raw signals" / "Waterfall"
// <details> open -- parallel to expandedNodeIds, driving the `open`
// attribute Charts.detailBodyHtml() renders (never left as native
// uncontrolled state, since renderFleetList()'s innerHTML rebuild would
// silently reset it every 5s poll otherwise). Scalars' content isn't
// gated on this (cheap plain HTML, always kept current -- see charts.js),
// this Set only exists to keep the panel's open/closed state sticky.
const openRawIds = new Set();
const openWaterfallIds = new Set();
const openScalarsIds = new Set();

// Node IDs whose row should render with a brief pulsing outline right now
// -- used by the Record drawer's "Go to Fleet" jump (jumpToFleetForAssetClass()
// below) so the operator can actually find the row it sent them to, not
// just land back on an unchanged-looking list.
const highlightNodeIds = new Set();

function motorRowHtml(entry) {
  const bucket = bucketFor(entry);
  const label = statusLabelFor(entry, bucket);
  const controls = rowControls(entry);
  const isEditing = editingNodeId === entry.node_id;
  const isEditingDeviceType = editingDeviceTypeNodeId === entry.node_id;
  const isExpanded = expandedNodeIds.has(entry.node_id);
  const pauseIcon = controls.pauseResumeAction === "resume" ? ICON_PLAY : ICON_PAUSE;
  // A capture keeps running server-side even with the drawer closed
  // (recordDrawerBodyHtml()'s comment) -- this is how the Record button
  // itself (now icon-only, 2026-07-24 Round 7) tells the operator a
  // background recording is still active without reopening it: neutral
  // icon color when idle, red + pulsing when active.
  const isRecording = !!(entry.capture_progress && entry.capture_progress.state !== "idle");

  // device_name defaults server-side to the raw node_id (registry.py's
  // add()) until an operator sets a real nickname -- treating "still equal
  // to node_id" as the unset signal (rather than persisting a separate
  // blank/set flag) lets the row show an inviting "Add nickname" prompt
  // instead of a technical ID masquerading as a chosen name. The node_id
  // itself is always shown too, right below, so the identity is never
  // ambiguous either way.
  const hasNickname = entry.device_name !== entry.node_id;
  const nameHtml = isEditing
    ? `<input class="motor-row__name-input" data-role="name-input" placeholder="Add nickname" value="${escapeHtml(hasNickname ? entry.device_name : "")}" />`
    : `<span class="motor-row__name${hasNickname ? "" : " motor-row__name--unset"}" data-role="name" title="Double-click to ${hasNickname ? "rename" : "add a nickname"}">${hasNickname ? escapeHtml(entry.device_name) : "Add nickname"}</span>`;
  const identityHtml = `<div class="motor-row__identity">
      ${nameHtml}
      <span class="motor-row__node-id" title="Node ID">${escapeHtml(entry.node_id)}</span>
    </div>`;

  // Each recording belongs to an asset class (2026-07-24) -- a pill right
  // next to the device name, same click-to-edit idiom, so it's glanceable
  // fleet-wide (not just at the moment of recording, where the drawer also
  // shows it). Renamed from "device type" in the UI (still `device_type`
  // in the API/data model -- this is a display-only rename) since "device"
  // in both field names read as two attributes of the same thing, hiding
  // that this one is really "what class of machine is this," the field
  // that decides which trained model applies, not a device property.
  const assetClassHelpHtml = `<span class="device-type-help" tabindex="0" title="Assets in the same class share one fault detection model" aria-label="What is asset class?">?</span>`;
  const deviceTypePillHtml = isEditingDeviceType
    ? `<div class="device-type-edit-wrap">
        <input type="text" class="device-type-edit-input" data-role="device-type-input" placeholder="e.g. conveyor motor" autocomplete="off" value="${escapeHtml(entry.device_type || "")}" />
        <div class="device-type-suggestions" data-role="device-type-suggestions" hidden></div>
      </div>${assetClassHelpHtml}`
    : entry.device_type
      ? `<button type="button" class="device-type-pill" style="--accent:${assetClassColor(entry.device_type)}" data-action="edit_device_type" title="Change asset class" aria-label="Change asset class">${escapeHtml(entry.device_type)}</button>${assetClassHelpHtml}`
      : `<button type="button" class="device-type-pill device-type-pill--unset" data-action="edit_device_type" title="Set asset class" aria-label="Set asset class">Set asset class</button>${assetClassHelpHtml}`;

  // Fault classifier's current read, next to the status pill -- only when
  // there's an actual fault to look at (bucket healthy/new/paused/offline
  // stay bare) AND a model has actually scored this node at least once
  // (entry.last_classification, same presence check charts.js's detail
  // panel now uses -- no device_type-only guess). Deliberately its own
  // chip, not folded into .motor-row__status: the classifier is a signal
  // independent of NodeStatus (docs/EDGE_IMPULSE_CLASSIFIER's own note,
  // see charts.js's buildClassificationHtml) and can legitimately disagree
  // with it, so it keeps the same neutral violet accent used there instead
  // of borrowing --accent's green/amber/red.
  const showClassificationChip = (bucket === "warning" || bucket === "fault") && entry.last_classification;
  const classificationChipHtml = showClassificationChip
    ? `<span class="motor-row__classification-chip" title="Fault classifier's current read -- an independent signal from the status above">${escapeHtml(titleCase(entry.last_classification.label))}</span>`
    : "";

  const rowHtml = `<div class="motor-row${isExpanded ? " motor-row--expanded" : ""}${highlightNodeIds.has(entry.node_id) ? " motor-row--highlight" : ""}" title="Click to expand">
    <div class="motor-row__main">
      ${identityHtml}
      <div class="motor-row__device-type-group">${deviceTypePillHtml}</div>
      <div class="motor-row__status-group">
        <span class="motor-row__status">${label}</span>
        ${classificationChipHtml}
      </div>
    </div>
    <div class="motor-row__actions">
      <button class="btn-label btn-label--${controls.commissionVariant}" ${controls.commissionAction ? `data-action="${controls.commissionAction}"` : ""} title="${controls.commissionTooltip}" aria-label="${controls.commissionTooltip}" ${controls.commissionEnabled ? "" : "disabled"}>${controls.commissionLabel}</button>
      <button class="btn-icon${isRecording ? " btn-icon--recording" : ""}" data-action="record" title="${isRecording ? "Recording in progress -- click to view" : "Record labeled training data"}" aria-label="${isRecording ? "Recording in progress" : "Record labeled training data"}">${ICON_RECORD}</button>
      <button class="btn-icon" data-action="${controls.pauseResumeAction}" title="${controls.pauseResumeTooltip}" aria-label="${controls.pauseResumeTooltip}" ${controls.pauseResumeEnabled ? "" : "disabled"}>${pauseIcon}</button>
      <button class="btn-icon btn-icon--danger" data-action="decommission" title="Remove" aria-label="Remove">${ICON_TRASH}</button>
    </div>
  </div>`;

  // Machinery protection lives above the charts, not below: during a trip
  // countdown this is the most time-critical thing on screen, and it's the
  // only part of the panel an operator can act on.
  //
  // Rendered here in app.js rather than inside Charts.detailBodyHtml() so
  // charts.js stays purely charts -- this is the panel's only non-chart
  // content, and it needs the fleet-wide node list (for already-claimed
  // motors) plus its own event wiring, both of which live here already.

  // Capture no longer lives here (2026-07-24 round 6: see recordDrawer
  // above) -- this is just charts now, one column, no side panel. Node ID
  // is already shown in the identity block above (motor-row__node-id);
  // repeating it here was redundant.
  const detailHtml = isExpanded ? `<div class="motor-row__detail">
    <div class="motor-row__detail-main">
      ${protectionSectionHtml(entry)}
      ${Charts.detailBodyHtml(entry, {
        rawOpen: openRawIds.has(entry.node_id),
        waterfallOpen: openWaterfallIds.has(entry.node_id),
        scalarsOpen: openScalarsIds.has(entry.node_id),
      })}
    </div>
  </div>` : "";

  return `<div class="motor-row-group motor-row-group--${bucket}" data-node-id="${escapeHtml(entry.node_id)}">${rowHtml}${detailHtml}</div>`;
}

function renderFleetList(nodes) {
  const list = document.getElementById("fleet-list");
  const entries = Object.values(nodes);
  // Filtered-to-zero is left blank, no "no results" message of its own
  // (docs/STATUS_FILTER_PLAN.md S4) -- the placeholder below is only for
  // the genuinely-empty fleet (no assets registered at all).
  const visible = entries.filter((entry) => selectedBuckets.has(bucketFor(entry)));
  list.innerHTML = entries.length
    ? visible.map(motorRowHtml).join("")
    : `<div class="fleet__empty">No assets yet -- they appear automatically as soon as they start streaming data.</div>`;
  // innerHTML above just destroyed and recreated every DOM node in the
  // list, including any chart-slot placeholders -- reparent each expanded
  // node's persistent Plotly <div>s (which survived, since they're held by
  // charts.js, not by this markup) back into the fresh slots.
  Charts.attachExpanded(expandedNodeIds, openRawIds, openWaterfallIds);
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

// A blank submit now explicitly reverts to the node_id fallback (the
// unset state motorRowHtml() renders as "Add nickname") rather than
// silently keeping whatever was there before -- once the unset state has
// its own visible meaning, "I cleared the field" has to actually clear it
// server-side too, or the row would show "Add nickname" while the old
// nickname was still secretly stored.
async function commitRename(nodeId, value) {
  editingNodeId = null;
  const trimmed = value.trim();
  const newValue = trimmed || nodeId;
  const entry = state.lastNodes[nodeId];
  if (!entry || newValue !== entry.device_name) {
    try {
      await api("POST", `/nodes/${nodeId}/rename`, { device_name: newValue });
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

// Same idea, for the device-type pill's edit mode.
let skipNextDeviceTypeBlurCommit = false;

// A mousedown on a suggestion button shifts focus off the input as its
// default action, firing blur/focusout (and thus a stale commit of
// whatever was typed) BEFORE the button's own click handler ever runs --
// same ordering the Escape branch below already works around. Set the
// skip flag here, ahead of that default action, so the real commit is
// left to the click handler's explicit commitDeviceType() call instead.
document.getElementById("fleet-list").addEventListener("mousedown", (e) => {
  if (e.target.closest(".device-type-suggestion")) {
    skipNextDeviceTypeBlurCommit = true;
  }
});

document.getElementById("fleet-list").addEventListener("keydown", (e) => {
  const deviceTypeInput = e.target.closest('[data-role="device-type-input"]');
  if (deviceTypeInput) {
    if (e.key === "Enter") {
      deviceTypeInput.blur();
    } else if (e.key === "Escape") {
      hideDeviceTypeSuggestions(deviceTypeInput);
      skipNextDeviceTypeBlurCommit = true;
      editingDeviceTypeNodeId = null;
      renderFleetList(state.lastNodes);
    }
    return;
  }
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
  const deviceTypeInput = e.target.closest('[data-role="device-type-input"]');
  if (deviceTypeInput) {
    if (skipNextDeviceTypeBlurCommit) {
      skipNextDeviceTypeBlurCommit = false;
      return;
    }
    const nodeId = deviceTypeInput.closest(".motor-row-group").dataset.nodeId;
    commitDeviceType(nodeId, deviceTypeInput.value);
    return;
  }
  const input = e.target.closest('[data-role="name-input"]');
  if (!input) return;
  if (skipNextBlurCommit) {
    skipNextBlurCommit = false;
    return;
  }
  const nodeId = input.closest(".motor-row-group").dataset.nodeId;
  commitRename(nodeId, input.value);
});

document.getElementById("fleet-list").addEventListener("focusin", (e) => {
  const input = e.target.closest('[data-role="device-type-input"]');
  if (!input) return;
  renderDeviceTypeSuggestions(input);
});

document.getElementById("fleet-list").addEventListener("input", (e) => {
  const input = e.target.closest('[data-role="device-type-input"]');
  if (!input) return;
  renderDeviceTypeSuggestions(input);
});

// Closes any open suggestions box (device-type pill's, or the Record
// drawer's label field) on a genuine outside click -- covers clicking
// elsewhere in the row/drawer, a different row, or off both entirely, all
// in one place (document-level, so it works regardless of which of the
// two containers the box lives in).
document.addEventListener("click", (e) => {
  document.querySelectorAll(
    '[data-role="capture-suggestions"]:not([hidden]), [data-role="device-type-suggestions"]:not([hidden])'
  ).forEach((box) => {
    if (!box.parentElement.contains(e.target)) box.hidden = true;
  });
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
      const name = entry ? entry.device_name : nodeId;
      if (!confirm(`Remove "${name}"? This cannot be undone.`)) return;
    }
    if (action === "record") {
      openRecordDrawer(nodeId);
      return;
    }
    if (action === "edit_device_type") {
      startDeviceTypeEdit(nodeId);
      return;
    }
    if (action === "stopped_baseline_stop") {
      saveStoppedBaseline(nodeId);
      return;
    }
    runAction(action, nodeId);
    return;
  }
  if (e.target.closest('[data-role="name"]')) return;
  const suggestion = e.target.closest(".device-type-suggestion");
  if (suggestion) {
    // Commit directly rather than writing suggestion.dataset.value into
    // the input and relying on a later blur to commit it -- the mousedown
    // above already suppressed the premature blur-commit, but nothing
    // else would ever fire a real commit for the picked value otherwise.
    const nodeId = suggestion.closest(".motor-row-group").dataset.nodeId;
    commitDeviceType(nodeId, suggestion.dataset.value);
    return;
  }
  // The device-type pill's edit form (input + suggestions) -- same "don't
  // let a click land on the click-anywhere-to-expand handler" guard as
  // .motor-row__body below. Without this, clicking into the input (a
  // plain <input>, not a button[data-action]) would fall all the way
  // through to toggleExpand() instead of focusing it.
  if (e.target.closest(".device-type-edit-wrap")) return;
  // Same guard for the asset-class (?) tooltip icon -- it's a plain <span>,
  // not a button[data-action], so without this a click on it would fall
  // through to toggleExpand() below and collapse/expand the row as an
  // unwanted side effect of just reading the tooltip.
  if (e.target.closest(".device-type-help")) return;
  // Plotly's modebar (zoom/pan/autoscale/...), the plots themselves, the
  // waterfall 2D/3D toggle pills, and the three <details> collapsibles all
  // live inside .motor-row__body, still part of this same
  // .motor-row-group -- without this guard, every modebar click (and the
  // synthetic click Plotly fires after a drag-zoom/pan), every toggle-pill
  // click, and every <summary> click (click bubbles normally, unlike the
  // <details> "toggle" event below) would get treated as "click anywhere
  // else to toggle expand", collapsing the row out from under whatever the
  // operator was just interacting with.
  if (e.target.closest(".motor-row__body")) return;
  // Same problem for the Protection section: it sits inside
  // .motor-row-group but outside .motor-row__body (it's rendered here, not
  // by charts.js), so without this its <select> and Hold button would
  // collapse the row on every click.
  if (e.target.closest(".protection")) return;
  const group = e.target.closest(".motor-row-group");
  if (group) toggleExpand(group.dataset.nodeId);
});

// The trip-output <select> needs "change", which the click handler above
// can't see. Its own listener rather than a branch in that one, since
// change/click have genuinely different semantics here.
document.getElementById("fleet-list").addEventListener("change", async (e) => {
  const select = e.target.closest('[data-action="set_trip_motor"]');
  if (!select) return;
  const nodeId = select.dataset.nodeId;
  const raw = select.value;
  const motorIdx = raw === "" ? null : Number(raw);
  const previous = state.lastNodes[nodeId] ? state.lastNodes[nodeId].trip_motor_idx : null;
  try {
    await api("POST", `/nodes/${nodeId}/trip_motor`, { motor_idx: motorIdx });
    showToast(motorIdx === null
      ? "Trip output cleared -- this asset can no longer stop a machine"
      : `Trip output armed on Motor ${motorIdx}`);
  } catch (err) {
    console.error(`Setting trip motor on ${nodeId} failed`, err);
    // Snap back to what the server still believes rather than leaving the
    // dropdown showing a selection that was rejected (e.g. a 409 from
    // another asset already claiming that motor).
    select.value = previous === null || previous === undefined ? "" : String(previous);
    showToast(`Couldn't set trip output: ${err.message}`, "error");
  }
  await pollNodes();
});

// The native `toggle` event on <details> does NOT bubble -- but
// capture-phase dispatch still traverses ancestors on the way down
// regardless of the bubbles flag, so {capture: true} on this stable
// container is what makes delegation work here. Mounts the corresponding
// collapsible's charts lazily (Charts.attachExpanded, no HTML rebuild) the
// moment it's actually opened, per docs/CHART_CLUTTER_PLAN.md §1.5's "not
// rendered/computed until expanded."
document.getElementById("fleet-list").addEventListener("toggle", (e) => {
  if (!(e.target instanceof HTMLDetailsElement)) return;
  const role = e.target.dataset.role; // "scalars-details" | "raw-signals-details" | "waterfall-details"
  const nodeId = e.target.closest(".motor-row-group")?.dataset.nodeId;
  if (!nodeId || !role) return;
  const set = role === "raw-signals-details" ? openRawIds
    : role === "waterfall-details" ? openWaterfallIds
    : openScalarsIds;
  if (e.target.open) set.add(nodeId); else set.delete(nodeId);
  Charts.attachExpanded(expandedNodeIds, openRawIds, openWaterfallIds);
}, true);

// ---------------------------------------------------------------------
// Record drawer -- own listener set, since it's a top-level element
// outside #fleet-list (recordDrawer/recordDrawerBackdrop, defined above
// alongside recordDrawerBodyHtml()).
// ---------------------------------------------------------------------

recordDrawer.addEventListener("click", (e) => {
  const button = e.target.closest("button[data-action]");
  if (button) {
    const action = button.dataset.action;
    if (action === "record_drawer_close") {
      closeRecordDrawer();
      return;
    }
    if (action === "jump_to_fleet_asset_class") {
      jumpToFleetForAssetClass(openRecordNodeId);
      return;
    }
    if (!openRecordNodeId) return;
    if (action === "capture_start") {
      // Needs the typed label + target-frames values -- doesn't fit
      // runAction/ACTION_ENDPOINT's no-body POST shape, so it's handled
      // here instead (see startCapture()). Label is required up front
      // (2026-07-24: Save auto-reuses it, no post-stop labeling step) --
      // blank just focuses the field rather than starting blind.
      const labelInput = recordDrawer.querySelector('[data-role="capture-label-input"]');
      const label = labelInput.value.trim();
      if (!label) {
        labelInput.focus();
        return;
      }
      const targetInput = recordDrawer.querySelector('[data-role="capture-target-input"]');
      const raw = targetInput.value.trim();
      // 0 (or blank) -> null: capture indefinitely, manual Save/Cancel only.
      const targetFrames = raw && Number(raw) > 0 ? Math.floor(Number(raw)) : null;
      captureLabelByNode[openRecordNodeId] = label;
      delete captureTargetDraftByNode[openRecordNodeId];
      startCapture(openRecordNodeId, targetFrames);
      return;
    }
    if (action === "capture_cancel") {
      delete captureLabelByNode[openRecordNodeId];
      delete captureTargetDraftByNode[openRecordNodeId];
    }
    if (action === "capture_stop" || action === "capture_cancel") {
      runAction(action, openRecordNodeId);
    }
    return;
  }
  const suggestion = e.target.closest(".motor-row__capture-suggestion");
  if (suggestion) {
    const input = suggestion.closest(".motor-row__capture-label-wrap")
      .querySelector('[data-role="capture-label-input"]');
    input.value = suggestion.dataset.label;
    // Setting .value programmatically does NOT fire a native "input"
    // event, so the Start-button-disabled sync (normally driven by the
    // "input" listener below) has to be called explicitly here too --
    // without this, Start stayed disabled forever after picking a
    // suggestion (2026-07-24 bug report).
    syncCaptureLabelState(input);
    hideCaptureSuggestions(input);
    input.focus();
  }
});

recordDrawer.addEventListener("keydown", (e) => {
  const captureInput = e.target.closest('[data-role="capture-label-input"]');
  if (!captureInput) return;
  if (e.key === "Enter") {
    const label = captureInput.value.trim();
    if (label && openRecordNodeId) saveCapture(openRecordNodeId, label);
  } else if (e.key === "Escape") {
    hideCaptureSuggestions(captureInput);
    captureInput.blur();
  }
});

// Refilters the suggestions box on every keystroke -- doesn't hide on
// focusout (see the click handler's suggestion-pick branch above instead)
// since a mousedown on a suggestion button fires before the input's blur,
// and hiding here first would make the suggestion disappear before its
// own click ever lands.
recordDrawer.addEventListener("input", (e) => {
  const labelInput = e.target.closest('[data-role="capture-label-input"]');
  if (labelInput) {
    renderCaptureSuggestions(labelInput);
    syncCaptureLabelState(labelInput);
    return;
  }
  const targetInput = e.target.closest('[data-role="capture-target-input"]');
  if (targetInput && openRecordNodeId) {
    captureTargetDraftByNode[openRecordNodeId] = targetInput.value;
  }
});

recordDrawer.addEventListener("focusin", (e) => {
  const input = e.target.closest('[data-role="capture-label-input"]');
  if (!input) return;
  renderCaptureSuggestions(input);
});

// ---------------------------------------------------------------------
// Topbar tabs -- Fleet, Performance (perf.js), Alerts (alerts.js), and
// Network (network.js) are all real sections.
// ---------------------------------------------------------------------

function activateTab(tab) {
  const link = document.querySelector(`.topbar__nav-link[data-tab="${tab}"]`);
  if (!link) return;

  document.querySelectorAll(".topbar__nav-link").forEach((el) => {
    el.classList.toggle("is-active", el === link);
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.hidden = panel.dataset.tabPanel !== tab;
  });
  // Classifier's sample list isn't fed by the shared WS/poll loop (it only
  // changes via its own mutations, or a Record save on the Fleet tab), so
  // refetch on every switch into the tab rather than risk a stale table.
  if (tab === "classifier") Classifier.refresh();
  // Network has no live WS push either (network.js's own docstring) --
  // same reasoning, refetch on every switch into the tab.
  if (tab === "network") Network.refresh();
}

document.querySelector(".topbar__nav").addEventListener("click", (e) => {
  const link = e.target.closest(".topbar__nav-link");
  if (!link) return;
  activateTab(link.dataset.tab);
});

// ---------------------------------------------------------------------
// Polling
// ---------------------------------------------------------------------

const state = { lastNodes: {} };

// node_id -> Date.now() of the last WS-driven change ("registry" or
// "removed") -- see pollNodes()'s own comment for the race this guards
// against.
const lastWsTouchAt = {};

async function pollNodes() {
  const dispatchedAt = Date.now();
  try {
    const res = await fetch("/nodes");
    const nodes = await res.json();
    // Merge, not blind-replace: a REST request that was in flight before a
    // WS "registry"/"removed" push landed for some node can resolve AFTER
    // it (real race under CPU load, e.g. a training run pegging a core --
    // the poll starves behind it and comes back stale). Applying it
    // verbatim would stomp the fresher WS-delivered status back to the old
    // one, then a later poll/WS message flips it forward again -- this is
    // the "flaps back and forth by itself" bug that guard fixes. See
    // lastWsTouchAt's own comment.
    for (const [nodeId, entry] of Object.entries(nodes)) {
      if ((lastWsTouchAt[nodeId] || 0) > dispatchedAt) continue;
      state.lastNodes[nodeId] = entry;
    }
    for (const nodeId of Object.keys(state.lastNodes)) {
      if (!(nodeId in nodes) && (lastWsTouchAt[nodeId] || 0) <= dispatchedAt) {
        delete state.lastNodes[nodeId];
      }
    }
    // Poll fallback for the WS "capture" handler's auto-save trigger --
    // covers a missed/dropped WS message the same way this poll is
    // already the documented fallback for "registry" pushes.
    for (const [nodeId, entry] of Object.entries(state.lastNodes)) {
      if (entry.capture_progress && entry.capture_progress.state === "stopped") {
        maybeAutoSaveCapture(nodeId);
      }
    }
    // Pass the race-corrected state.lastNodes, not the raw fetch response
    // -- a node that registers *after* this poll was dispatched but
    // *before* it resolved would be missing from `nodes` even though it
    // now genuinely exists (WS already delivered its "registry"/"spectrum"
    // push and charts.js already has live buffers for it). Passing `nodes`
    // straight through made onNodesPolled's own purge-if-absent logic
    // wrongly evict that node's just-created Plotly charts the instant this
    // stale poll landed -- reproduces as a freshly-online node's expanded
    // spectrum going silently inert. state.lastNodes was already merged
    // with the same guard two lines above for this exact race; charts.js
    // needs the identical corrected view, not the raw one.
    Charts.onNodesPolled(state.lastNodes);
    // A decommissioned node never appears in another motorRowHtml() call,
    // so it'd otherwise sit in expandedNodeIds forever (harmless on its
    // own, but see charts.js's attachExpanded()).
    for (const nodeId of expandedNodeIds) {
      if (!(nodeId in state.lastNodes)) expandedNodeIds.delete(nodeId);
    }
    for (const nodeId of openRawIds) {
      if (!(nodeId in state.lastNodes)) openRawIds.delete(nodeId);
    }
    for (const nodeId of openWaterfallIds) {
      if (!(nodeId in state.lastNodes)) openWaterfallIds.delete(nodeId);
    }
    for (const nodeId of openScalarsIds) {
      if (!(nodeId in state.lastNodes)) openScalarsIds.delete(nodeId);
    }
    // Re-base every countdown against this fresh trip_in_s before rendering
    // -- the local deadline is only an interpolation between polls, and the
    // server remains the authority on whether a trip is pending at all.
    for (const entry of Object.values(state.lastNodes)) noteTripCountdown(entry);
    renderSummary(state.lastNodes);
    // Skip the list re-render while a rename or device-type edit is open
    // -- startRename()/startDeviceTypeEdit()/Escape re-render explicitly
    // on their own, this only guards the automatic 5s poll from wiping
    // out an in-flight edit.
    if (editingNodeId === null && editingDeviceTypeNodeId === null) renderFleetList(state.lastNodes);
    // The drawer lives outside #fleet-list now, so it needs its own
    // refresh call -- this is also what keeps its live frame count
    // updating while capturing (capture_progress.collected only changes
    // via this poll; the WS "capture" broadcast only fires on state
    // transitions, not per frame -- see api/capture_controller.py).
    // renderRecordDrawer() itself no-ops while one of its own inputs is
    // focused, so this can't wipe an in-progress edit either.
    if (openRecordNodeId) renderRecordDrawer();
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
    lastWsTouchAt[msg.node_id] = Date.now();
    delete state.lastNodes[msg.node_id];
    expandedNodeIds.delete(msg.node_id);
    openRawIds.delete(msg.node_id);
    openWaterfallIds.delete(msg.node_id);
    openScalarsIds.delete(msg.node_id);
    if (openRecordNodeId === msg.node_id) closeRecordDrawer();
  } else if (msg.type === "registry") {
    lastWsTouchAt[msg.node_id] = Date.now();
    // The WS broadcast now sends the same _node_dict() shape GET /nodes
    // does, so commissioning_progress/capture_progress/protection are all
    // present -- api/app.py's _on_registry_status_change was switched to
    // _node_dict so a FAULT push carries the trip countdown that same
    // transition just started, instead of the dashboard showing FAULT with
    // no sign of a pending trip until the next 5s poll.
    //
    // The carry-forward below is therefore usually redundant, and is kept
    // as belt-and-braces for any other producer of a "registry" message
    // that still sends a bare to_dict().
    const prev = state.lastNodes[msg.node_id];
    const entry = msg.entry;
    if (prev && prev.commissioning_progress
        && entry.status === "commissioning_collecting" && !entry.commissioning_progress) {
      entry.commissioning_progress = prev.commissioning_progress;
    }
    if (prev && prev.capture_progress) {
      entry.capture_progress = prev.capture_progress;
    }
    state.lastNodes[msg.node_id] = entry;
    // A trip countdown starts on the FAULT transition, so this push is the
    // earliest the dashboard can know about it -- re-base the local deadline
    // here rather than waiting for the next poll.
    noteTripCountdown(entry);
    // Stale % from a previous run must never linger into the next one --
    // clear the moment the node leaves commissioning_training for any
    // reason (completed, or a fresh commission started elsewhere).
    if (entry.status !== "commissioning_training") delete trainingProgress[msg.node_id];
  } else if (msg.type === "training_progress") {
    trainingProgress[msg.node_id] = { epoch: msg.epoch, total_epochs: msg.total_epochs };
  } else if (msg.type === "capture") {
    const entry = state.lastNodes[msg.node_id];
    if (entry) {
      if (msg.state === "idle") {
        delete entry.capture_progress;
        delete captureLabelByNode[msg.node_id]; // covers cancel, which never passes through "stopped"
        delete captureTargetDraftByNode[msg.node_id];
      } else {
        entry.capture_progress = { state: msg.state, collected: msg.collected,
                                    target_frames: msg.target_frames };
      }
    }
    if (msg.state === "stopped") maybeAutoSaveCapture(msg.node_id);
  } else if (msg.type === "stopped_baseline") {
    // Pushed per collected frame, not just on transitions -- the count is
    // what the operator is watching while standing next to a machine they
    // just switched off, and GET /nodes only refreshes every 5s.
    const entry = state.lastNodes[msg.node_id];
    if (entry) {
      if (msg.state === "idle") delete entry.stopped_baseline_progress;
      else entry.stopped_baseline_progress = { collected: msg.collected,
                                                min_frames: msg.min_frames };
    }
  }
  renderSummary(state.lastNodes);
  if (editingNodeId === null && editingDeviceTypeNodeId === null) renderFleetList(state.lastNodes);
  if (openRecordNodeId) renderRecordDrawer();
}, (msg) => Perf.handleMessage(msg), (msg) => Alerts.handleMessage(msg),
   (msg) => Classifier.handleMessage(msg));

Perf.init();
Alerts.init();
Classifier.init();
Network.init();
pollNodes();
fetchCaptureLabels();
fetchDeviceTypes();
setInterval(pollNodes, NODES_POLL_MS);

// Lets host/wifi_bridge.py's captive-portal redirect (docs/
// WIFI_ONBOARDING_PLAN.md S1) land a freshly-joined phone/laptop straight
// on the Network tab instead of Fleet -- a bare "?tab=network" query param
// on the page URL it redirects to. After the .init() calls above so the
// target tab's own data is already loading, not just its DOM.
const deepLinkTab = new URLSearchParams(window.location.search).get("tab");
if (deepLinkTab) activateTab(deepLinkTab);
