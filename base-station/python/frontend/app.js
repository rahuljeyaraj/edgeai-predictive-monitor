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
//
// A node that stops publishing cannot announce that it has, so this timeout
// is the ONLY way offline is ever noticed -- unlike coming online, which a
// single frame proves instantly (see the "last_seen" WS branch below). 30s
// made a stopped node linger on the dashboard for half a minute.
//
// 10s is bounded below by the slowest frame rate any node is expected to
// sustain: the sims publish at 5fps and real satellite nodes have measured
// 0.27-4.9fps, so even the slowest is ~2.7 frames inside this window. Do not
// cut this much finer without re-checking that floor -- at 5s a real node
// that stalls briefly (SPI bridge hiccup, WiFi retry) would flap to Offline
// and back, which reads as a fault the operator has to go investigate.
const OFFLINE_AFTER_S = 10;

// last_seen is stamped from the *device's* clock, so it can only be compared
// against the device's clock -- never against Date.now(), which is the
// browser host's. The two are not synced and need not be close: on an
// AP-mode/hotspot deployment the device has no upstream NTP at all, so its
// clock free-runs from whatever it booted with. Measured 131s of skew on a
// live rig, which put every streaming node past OFFLINE_AFTER_S and rendered
// the whole fleet "Offline" while frames were in fact arriving 0.6s apart --
// the giveaway being that Dev/perf still showed a healthy fps, because fps is
// a delta *between* frames and so is immune to a constant clock offset.
//
// Corrected with the HTTP Date header, which every response already carries
// and which is the device's own clock by definition. That keeps OFFLINE
// frontend-computed (docs/EPM_Dashboard_Redesign_Spec.md S5.1) and needs no
// payload/schema change -- notably it also fixes the WS-pushed entries, whose
// last_seen is in the same device-clock terms, for free. Whole-second
// resolution is plenty against a 30s threshold. Stays 0 until the first poll
// lands and if the header is ever missing/unparseable, degrading to exactly
// the old same-clock behavior rather than to something worse.
let clockSkewS = 0;

function deviceNowS() {
  return Date.now() / 1000 + clockSkewS;
}

// Called with each GET /nodes response -- see clockSkewS above.
function noteServerClock(res) {
  const header = res.headers.get("Date");
  if (!header) return;
  const serverMs = Date.parse(header);
  if (Number.isNaN(serverMs)) return;
  clockSkewS = serverMs / 1000 - Date.now() / 1000;
}

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
      && deviceNowS() - entry.last_seen > OFFLINE_AFTER_S) {
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
  // "Idle", matching the summary tile's label exactly -- the pill used to say
  // "Stopped" (what an operator sees at the machine), but one status wearing
  // two words across the same screen just reads as two different states.
  idle: "Idle",
  tripped: "Tripped",
};

// ---------------------------------------------------------------------
// Machinery protection (docs/MOTOR_STOP_PLAN.md)
// ---------------------------------------------------------------------

// The hardcoded TRIP_MOTOR_COUNT that used to live here is gone
// (docs/UNIFIED_COMMISSIONING_PLAN.md S3.2). It was a hand-copy of
// motor-driver/motor_driver.py's MOTOR_IDS, so a factory with one motor saw
// three options, two of which were nonsense. The rig announces its own
// outputs now and setup.js reads them from GET /trip_outputs.

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

// ---------------------------------------------------------------------
// Global trip banner (docs/UNIFIED_COMMISSIONING_PLAN.md S4.2)
//
// Cold config lives in the drawer; hot state lives out front. A trip
// countdown is an alarm, not a setting: ten seconds is not enough time to
// remember which asset, find its tile, expand it and scroll to Protection,
// which is where the countdown and Hold used to be. This banner sits above
// the tab nav, so it's present on Fleet, Classifier, Network and
// Performance alike -- the local counterpart to the Telegram alert that
// already fires.
// ---------------------------------------------------------------------

const tripBanner = document.getElementById("trip-banner");

// Acknowledged post-trip lines, so a resolved event stops shouting. Only
// the *settled* states are dismissible: a live countdown and a failed trip
// are not, because both are still true and still need a decision.
const dismissedTripNodes = new Set();

function tripBannerLine(entry) {
  const p = entry.protection || {};
  const left = tripSecondsLeft(entry.node_id);
  const name = entry.device_name || entry.node_id;
  if (left !== null) {
    return { kind: "countdown", text: `${name} — tripping in ${left}s`, hold: true };
  }
  if (p.trip_failed) {
    // The most severe state this system can report, and it used to be
    // buried in a collapsed panel. Persistent until acknowledged.
    return { kind: "failed", text: `${name} — trip failed, machine still running` };
  }
  if (entry.status === "tripped") {
    const when = p.tripped_at ? new Date(p.tripped_at * 1000).toLocaleTimeString() : null;
    const text = `Tripped — ${name}${when ? ` at ${when}` : ""}, confirmed stopped`;
    if (p.needs_ack) {
      // Unacknowledged: same "still true, still needs a decision" reasoning
      // as countdown/failed below, so not locally dismissible -- a gate
      // blip on this asset's own sensor can't be told apart from a real
      // restart (cross-talk off a neighbouring motor on a shared rig
      // frame), so nothing auto-recovers this node until a human presses
      // Acknowledge. Dismissing the banner would hide that it's still
      // parked at TRIPPED with no way back except this button.
      return { kind: "tripped", text, ack: true };
    }
    return { kind: "tripped", text, dismissible: true };
  }
  return null;
}

function renderTripBanner() {
  if (!tripBanner) return;
  const lines = [];
  for (const entry of Object.values(state.lastNodes)) {
    const line = tripBannerLine(entry);
    if (!line) continue;
    if (line.dismissible && dismissedTripNodes.has(entry.node_id)) continue;
    lines.push({ ...line, nodeId: entry.node_id });
  }
  // A node that has moved on from whatever was dismissed can raise its
  // voice again next time -- otherwise one acknowledgement would silence
  // every future trip on that asset.
  for (const nodeId of dismissedTripNodes) {
    const entry = state.lastNodes[nodeId];
    if (!entry || !tripBannerLine(entry)) dismissedTripNodes.delete(nodeId);
  }

  tripBanner.hidden = lines.length === 0;
  tripBanner.innerHTML = lines.map((line) => {
    const safeId = escapeHtml(line.nodeId);
    return `<div class="trip-banner__line trip-banner__line--${line.kind}" data-node-id="${safeId}">
      <span class="trip-banner__text" data-trip-text data-node-id="${safeId}">${escapeHtml(line.text)}</span>
      ${line.hold ? `<button type="button" class="trip-banner__hold" data-action="protection_hold" data-node-id="${safeId}">Hold</button>` : ""}
      ${line.ack ? `<button type="button" class="trip-banner__ack" data-action="protection_acknowledge" data-node-id="${safeId}">Acknowledge</button>` : ""}
      ${line.dismissible ? `<button type="button" class="trip-banner__dismiss" data-action="trip_dismiss" data-node-id="${safeId}" aria-label="Dismiss">&times;</button>` : ""}
    </div>`;
  }).join("");
}

// Re-renders only the seconds, never the whole banner, while a countdown is
// running -- rebuilding the markup twice a second would make the Hold
// button unclickable at exactly the moment it matters. A change of *kind*
// (countdown expiring into tripped/failed) does need a rebuild.
function tickTripCountdowns() {
  if (!tripBanner || tripBanner.hidden) {
    if (Object.values(state.lastNodes).some((e) => tripBannerLine(e))) renderTripBanner();
    return;
  }
  for (const el of tripBanner.querySelectorAll("[data-trip-text]")) {
    const entry = state.lastNodes[el.dataset.nodeId];
    if (!entry) { renderTripBanner(); return; }
    const line = tripBannerLine(entry);
    if (!line) { renderTripBanner(); return; }
    const wasCountdown = el.parentElement.classList.contains("trip-banner__line--countdown");
    if ((line.kind === "countdown") !== wasCountdown) { renderTripBanner(); return; }
    if (el.textContent !== line.text) el.textContent = line.text;
  }
}
setInterval(tickTripCountdowns, 500);

// Going offline is the one status change nothing pushes: it is the ABSENCE
// of frames, so no WS message and no REST response ever announces it (see
// OFFLINE_AFTER_S). Without this the fleet list would only notice a node had
// gone quiet whenever something else happened to trigger a render -- in
// practice the 5s poll -- so a 10s threshold would surface at 10-15s.
//
// Diffed, not an unconditional re-render: this runs every second forever, and
// renderFleetList rebuilds the whole list. Nothing is redrawn on the ticks
// where no node actually crossed the threshold, which is almost all of them.
let lastOfflineBuckets = {};
function tickOfflineStatus() {
  let changed = false;
  const seen = {};
  for (const [nodeId, entry] of Object.entries(state.lastNodes)) {
    const bucket = bucketFor(entry);
    seen[nodeId] = bucket;
    if (lastOfflineBuckets[nodeId] !== bucket) changed = true;
  }
  for (const nodeId of Object.keys(lastOfflineBuckets)) {
    if (!(nodeId in seen)) changed = true;
  }
  lastOfflineBuckets = seen;
  if (!changed) return;
  renderSummary(state.lastNodes);
  renderTripBanner();
  if (editingNodeId === null && editingDeviceTypeNodeId === null) renderFleetList(state.lastNodes);
}
setInterval(tickOfflineStatus, 1000);

tripBanner.addEventListener("click", (e) => {
  const button = e.target.closest("button[data-action]");
  if (!button) return;
  const nodeId = button.dataset.nodeId;
  if (button.dataset.action === "trip_dismiss") {
    dismissedTripNodes.add(nodeId);
    renderTripBanner();
    return;
  }
  runAction(button.dataset.action, nodeId);
});

// The expanded tile's Protection section is READ-ONLY as of
// docs/UNIFIED_COMMISSIONING_PLAN.md S4.3. Its start/stop/cancel/dropdown
// controls moved into setup (they're configuration, done once,
// deliberately); the countdown and Hold moved the other way, out to the
// global banner, because they're an alarm on a clock. Nothing is shown in
// both places -- that standing rule is what decided each half.
function protectionSectionHtml(entry) {
  const p = entry.protection;
  // Absent only when the backend has no ProtectionController at all -- render
  // nothing rather than an empty section promising a control that can't work.
  if (!p) return "";

  const measured = entry.stopped_energy_ref !== null
    && entry.stopped_energy_ref !== undefined;
  let tripText;
  if (!entry.trip_motor_idx) {
    tripText = "None";
  } else {
    const when = entry.trip_motor_confirmed_at
      ? new Date(entry.trip_motor_confirmed_at * 1000)
          .toLocaleDateString(undefined, { day: "2-digit", month: "short" })
      : null;
    // "Untested" is stated, never hidden: an output nobody has proven is
    // exactly what the old dropdown produced silently, and the whole point
    // of the stop test is that the difference is now visible. Both labels
    // here match the wizard's own step names (setup.js's STEP_TITLES), so
    // the same fact isn't called two different things in two places.
    tripText = `Output ${entry.trip_motor_idx} · ${when ? `tested ${when}` : "untested"}`
      + ` · ${p.armed ? "armed" : "not armed"}`;
  }

  return `<div class="protection" data-role="protection">
    <div class="protection__title">Protection</div>
    <div class="protection__row">
      <span class="protection__label">Stop output</span>
      <span class="protection__state${entry.trip_motor_idx ? "" : " protection__state--missing"}">${escapeHtml(tripText)}</span>
    </div>
    <div class="protection__row">
      <span class="protection__label">Machine-off reading</span>
      <span class="protection__state${measured ? "" : " protection__state--missing"}">${measured ? "Measured" : "Not measured"}</span>
    </div>
    <button type="button" class="protection__change" data-action="setup_open" data-step="trip_output">Change in setup</button>
  </div>`;
}

// Status label shown in the row. No frame counts, no training percentage
// (docs/UNIFIED_COMMISSIONING_PLAN.md S4.4): every one of those readouts now
// lives on the setup step that produces it, where the operator is actually
// looking, rather than being narrated a second time out here.
function statusLabelFor(entry, bucket) {
  if (bucket === "offline") return "Offline";
  return STATUS_LABEL[entry.status];
}

// The row's setup affordance (docs/UNIFIED_COMMISSIONING_PLAN.md S4.4).
// One button, three states, and nothing else:
//   uncommissioned -> "Set up"
//   mid-setup      -> "Setup — step 4 of 6", tinted
//   commissioned   -> no button at all; `Re-run setup` lives in the drawer
// It is a door, not a dashboard: no frame counts, no progress bar, no
// "Training…". All of that lives on the step that produces it.
//
// Pause/Resume only enabled once a model exists (healthy/warning/
// fault/paused) -- matches pause()/resume()'s own guards.
// Remove (decommission) is always enabled in every status (S3.9:
// registry.decommission() is now removable from any status).
function rowControls(entry) {
  const status = entry.status;
  const progress = entry.setup_progress;
  const commissioned = entry.model_path !== null && entry.model_path !== undefined;

  let setupLabel = null, setupVariant = "action", setupTooltip = "";
  if (progress) {
    setupLabel = `Setup — step ${progress.index} of ${progress.total}`;
    setupVariant = "pending";
    setupTooltip = "Continue setting this asset up";
  } else if (!commissioned) {
    setupLabel = "Set up";
    setupTooltip = "Name it, train its model, and test the output that stops it";
  }

  const pauseResumeEnabled = status === "healthy" || status === "warning"
    || status === "fault" || status === "paused";
  const pauseResumeAction = status === "paused" ? "resume" : "pause";
  const pauseResumeTooltip = status === "paused" ? "Resume" : "Pause";

  return {
    setupLabel, setupVariant, setupTooltip,
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

// node_id of whichever node's drawer is open right now, or null -- a
// singleton (only one node can be actively set up/recorded at a time),
// unlike the old per-row toolbar this replaces.
let openRecordNodeId = null;

// Which of the drawer's two modes is showing (docs/UNIFIED_COMMISSIONING_PLAN.md
// S5): "setup" is the guided flow (setup.js), "record" is today's
// label + frame-count + Start form, for fault recordings after the asset is
// live. One slide-over per asset, two modes -- not two drawers.
let drawerMode = "record";

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
function drawerHeaderHtml(title) {
  return `<div class="record-drawer__header">
    <span class="record-drawer__title">${escapeHtml(title)}</span>
    <button type="button" class="record-drawer__close" data-action="record_drawer_close" aria-label="Close">&times;</button>
  </div>`;
}

function recordDrawerBodyHtml(entry) {
  const headerHtml = drawerHeaderHtml(`Record — ${entry.device_name}`);

  // Asset class is set in setup step 1 now, and it's mandatory there
  // (S2.2.1), so the round-8 "Go to Fleet" blocking state is gone -- it
  // only ever existed because the drawer had no way to set the field. An
  // asset can still reach this drawer before setup (the Record button is
  // always available), which is what this shorter prompt covers: it sends
  // the operator into the flow that owns the field rather than opening a
  // second live editor for it.
  if (!entry.device_type) {
    return `${headerHtml}
    <div class="record-drawer__body">
      <div class="record-drawer__block">
        <p class="record-drawer__block-text">Recordings are grouped by asset class — one fault-detection model per class — and this asset doesn't have one yet. Setting it is the first step of setup.</p>
        <button type="button" class="btn-label btn-label--ready" data-action="setup_open">Set up this asset</button>
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
    <button type="button" class="setup-cancel" data-action="setup_open">Re-run setup</button>
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
  // Don't wipe an in-progress edit out from under the operator on a
  // background poll/WS tick -- same guard editingNodeId uses for the row
  // list's own rebuild (app.js's long-standing pattern). Record mode reads
  // its typed values straight off the DOM, so for it that means skipping
  // the redraw entirely.
  //
  // Setup mode must NOT skip, and that is the fix for the wizard freezing
  // between steps. Enter-to-submit (the keydown handler below) leaves focus
  // in the field it was typed in, so every redraw that should have followed
  // -- the busy dimming, the new step, the poll, the WS "setup" push -- hit
  // this guard and returned. The drawer sat on the step the operator had
  // just completed, sometimes still dimmed and inert (`.setup-body.is-busy`
  // is pointer-events:none), until something happened to blur that field.
  // It is safe to redraw setup because setup.js keeps every typed value in
  // its own draft rather than in the DOM; focus and caret are restored
  // below so a redraw mid-typing stays invisible.
  const focusedInput = recordDrawer.contains(document.activeElement)
      && document.activeElement.tagName === "INPUT" ? document.activeElement : null;
  if (focusedInput && drawerMode !== "setup") return;
  const focusRole = focusedInput ? focusedInput.dataset.role : null;
  const caret = focusedInput ? [focusedInput.selectionStart, focusedInput.selectionEnd] : null;
  recordDrawer.hidden = false;
  recordDrawerBackdrop.hidden = false;
  recordDrawer.innerHTML = drawerMode === "setup"
    ? drawerHeaderHtml(Setup.headerTitle(entry)) + Setup.bodyHtml(entry)
    : recordDrawerBodyHtml(entry);
  if (focusRole) {
    // Absent after a step change (the field belonged to the previous step),
    // which is exactly when focus should NOT be restored.
    const again = recordDrawer.querySelector(`[data-role="${focusRole}"]`);
    if (again) {
      again.focus();
      // type="number" has no selection to restore and throws if asked.
      if (caret[0] !== null) {
        try { again.setSelectionRange(caret[0], caret[1]); } catch (e) { /* not a text input */ }
      }
    }
  }
}

// "Set up" / "Setup — step N of 6" (the tile), "Change in setup" (the
// expanded Protection section), and "Re-run setup" (the Record drawer) all
// land here. `step` jumps straight to one -- re-entering a single step
// without walking the whole wizard is deliberate (S10 Q2).
async function openSetupDrawer(nodeId, step) {
  drawerMode = "setup";
  openRecordNodeId = nodeId;
  renderRecordDrawer();
  const existing = Setup.snapshotFor(nodeId);
  if (!existing || step) await Setup.start(nodeId, step);
  else await Setup.refresh(nodeId);
}

// "Record" button (motor-row__actions) -- opens the drawer for this node,
// also expanding the row (if collapsed) so the live charts are visible
// alongside it -- the two are independent now (2026-07-24 round 6): either
// can be opened/closed without affecting the other, unlike the old design
// where Record's whole job was just revealing the same panel charts lived
// in.
function openRecordDrawer(nodeId) {
  drawerMode = "record";
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

recordDrawerBackdrop.addEventListener("click", closeRecordDrawer);

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && openRecordNodeId
      && !recordDrawer.contains(document.activeElement)) {
    closeRecordDrawer();
  }
});

// The commission/* and stopped_baseline/* actions are gone from here:
// those routes still exist and still work standalone, but the only UI that
// drives them now is setup (setup.js), which owns their instructions and
// their inline errors. What's left is the actions that belong to a live
// asset rather than to setting one up.
const ACTION_ENDPOINT = {
  pause: (id) => ["POST", `/nodes/${id}/pause`],
  resume: (id) => ["POST", `/nodes/${id}/resume`],
  decommission: (id) => ["POST", `/nodes/${id}/decommission`],
  capture_stop: (id) => ["POST", `/nodes/${id}/capture/stop`],
  capture_cancel: (id) => ["POST", `/nodes/${id}/capture/cancel`],
  protection_hold: (id) => ["POST", `/nodes/${id}/protection/hold`],
  protection_acknowledge: (id) => ["POST", `/nodes/${id}/protection/acknowledge`],
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
// Armed-protection indicator on the collapsed row (S4.3). A shield is the
// standard protection glyph and carries no verb -- it says this asset has a
// trip output, not that anything is happening.
const ICON_SHIELD = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M12 3l7 3v6c0 4-3 7-7 9-4-2-7-5-7-9V6z"/></svg>';

// Drag-to-rearrange handle on each row. Six dots is the near-universal
// "grab me" affordance (list rows in phone settings, kanban cards, table
// column reorder) and, unlike an up/down arrow pair, says the whole row
// moves rather than that it swaps one place per click.
const ICON_GRIP = '<svg viewBox="0 0 24 24" width="18" height="22" fill="currentColor"><circle cx="9" cy="5" r="2"/><circle cx="15" cy="5" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="15" cy="12" r="2"/><circle cx="9" cy="19" r="2"/><circle cx="15" cy="19" r="2"/></svg>';

// Suppresses re-render of the list while a rename input is open, so an
// in-flight edit isn't wiped out by the next 5s poll (same guard the old
// dashboard used for this exact race).
let editingNodeId = null;

// node_id of the row currently being dragged by its grip, or null. Same
// idea as editingNodeId above -- it suppresses the automatic re-render --
// but it has to be checked inside renderFleetList() itself rather than at
// each call site, because a drag must survive EVERY render path (poll, WS,
// offline tick, expand), not just the 5s poll.
let draggingNodeId = null;

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

// Which nodes have their "Scalar values" / "Waterfall" <details> open --
// parallel to expandedNodeIds, driving the `open` attribute
// Charts.detailBodyHtml() renders (never left as native uncontrolled state,
// since renderFleetList()'s innerHTML rebuild would silently reset it every
// 5s poll otherwise). Both panels are Plotly-backed, so these Sets also gate
// whether charts.js mounts their charts at all -- see attachExpanded().
// ("Raw signals" was a third such panel until 2026-08-01, see charts.js.)
const openScalarsIds = new Set();
const openWaterfallIds = new Set();

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
  // A small shield when this asset could actually stop its machine
  // (S4.3): a glyph, not text, and not a second copy of any status string
  // -- the collapsed row's job is to say at a glance which assets carry a
  // trip output, and the expanded panel already spells out which one.
  const armedChipHtml = (entry.protection && entry.protection.armed)
    ? `<span class="motor-row__armed" title="Protection armed — this asset can stop its machine" aria-label="Protection armed">${ICON_SHIELD}</span>`
    : "";

  const showClassificationChip = (bucket === "warning" || bucket === "fault") && entry.last_classification;
  const classificationChipHtml = showClassificationChip
    ? `<span class="motor-row__classification-chip" title="Fault classifier's current read -- an independent signal from the status above">${escapeHtml(titleCase(entry.last_classification.label))}</span>`
    : "";

  // The grip is the ONLY drag surface -- the row body stays a plain
  // click-to-expand target. Making the whole row draggable would put a
  // press-and-hold gesture on top of the row's primary action and turn
  // every slightly-dragged tap into an accidental reorder.
  const gripHtml = `<span class="motor-row__grip" data-role="grip" role="button" tabindex="0" title="Drag to move this asset up or down" aria-label="Drag to reorder ${escapeHtml(entry.device_name)}">${ICON_GRIP}</span>`;

  const rowHtml = `<div class="motor-row${isExpanded ? " motor-row--expanded" : ""}" title="Click to expand">
    ${gripHtml}
    <div class="motor-row__main">
      ${identityHtml}
      <div class="motor-row__device-type-group">${deviceTypePillHtml}</div>
      <div class="motor-row__status-group">
        <span class="motor-row__status">${label}</span>
        ${armedChipHtml}
        ${classificationChipHtml}
      </div>
    </div>
    <div class="motor-row__actions">
      ${controls.setupLabel ? `<button class="btn-label btn-label--${controls.setupVariant}" data-action="setup_open" title="${controls.setupTooltip}" aria-label="${controls.setupTooltip}">${controls.setupLabel}</button>` : ""}
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
        scalarsOpen: openScalarsIds.has(entry.node_id),
        waterfallOpen: openWaterfallIds.has(entry.node_id),
      })}
    </div>
  </div>` : "";

  return `<div class="motor-row-group motor-row-group--${bucket}" data-node-id="${escapeHtml(entry.node_id)}">${rowHtml}${detailHtml}</div>`;
}

// The Assets list is in an order the operator chose by dragging rows
// (entry.sort_index, persisted server-side by POST /nodes/order) -- NOT
// sorted by status or name. A fleet is a physical layout an operator holds
// in their head ("the two pumps, then the line motors"), and a list that
// re-sorts itself the moment a machine changes state is one where the row
// you were about to click moves out from under you.
//
// sort_index is null for any node never dragged, which is the whole fleet
// until someone starts arranging: those keep discovery order and sit after
// the placed ones, so this is a no-op until first used.
function orderedEntries(nodes) {
  return Object.values(nodes)
    .map((entry, i) => ({ entry, i }))
    .sort((a, b) => {
      const ai = a.entry.sort_index, bi = b.entry.sort_index;
      if (ai == null && bi == null) return a.i - b.i;
      if (ai == null) return 1;
      if (bi == null) return -1;
      return ai - bi || a.i - b.i;
    })
    .map((x) => x.entry);
}

function renderFleetList(nodes) {
  // A drag physically moves .motor-row-group elements around inside the
  // list, so any rebuild mid-drag would destroy the element under the
  // operator's finger. Every caller is either the 5s poll, a WS message or
  // the 1s offline tick -- none of them are worth interrupting a drag for,
  // and finishDrag() re-renders once the drop lands.
  if (draggingNodeId !== null) return;
  const list = document.getElementById("fleet-list");
  const entries = orderedEntries(nodes);
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
  Charts.attachExpanded(expandedNodeIds, openScalarsIds, openWaterfallIds);
}

// ---------------------------------------------------------------------
// Drag to rearrange (grip handle on each row)
//
// Rows are moved directly in the DOM as the pointer passes their midpoint,
// with no floating ghost element: the row an operator is dragging is often
// an expanded one carrying live Plotly charts, and cloning that for a ghost
// would either duplicate the charts or show an empty box. Moving the real
// element keeps the charts mounted and mounted-once -- charts.js holds the
// <div>s by reference, so reparenting the group never detaches them.
// ---------------------------------------------------------------------

let dragGroupEl = null;
// The full fleet order as it stood when the drag began -- captured up front
// because renderFleetList() is suppressed mid-drag, so state.lastNodes'
// sort_index values are the pre-drag ones the whole time.
let dragOrderBefore = [];

// A status filter can hide rows, so the row dragged past is not necessarily
// the neighbour in the full fleet. Hidden nodes keep their absolute
// positions and the visible ones are re-dealt into the slots they already
// occupied -- which is exactly what "put this one above that one" means
// when you can only see some of them.
function mergeVisibleOrder(fullOrder, visibleAfter) {
  const visibleSet = new Set(visibleAfter);
  let next = 0;
  return fullOrder.map((nodeId) => (visibleSet.has(nodeId) ? visibleAfter[next++] : nodeId));
}

// Applies an ordering everywhere: locally first (so the row stays where it
// was dropped even if the request is slow), then to the server.
async function commitOrder(order, refocusNodeId) {
  order.forEach((nodeId, i) => {
    if (state.lastNodes[nodeId]) state.lastNodes[nodeId].sort_index = i;
  });
  renderFleetList(state.lastNodes);
  if (refocusNodeId) {
    document.querySelector(`.motor-row-group[data-node-id="${refocusNodeId}"] [data-role="grip"]`)?.focus();
  }
  try {
    await api("POST", "/nodes/order", { node_ids: order });
  } catch (err) {
    console.error("Saving the asset order failed", err);
    showToast("Could not save the new order", "error");
    // Server is the authority -- pull the real order back rather than
    // leaving the optimistic one on screen as if it had stuck.
    await pollNodes();
  }
}

function beginDrag(grip, e) {
  const group = grip.closest(".motor-row-group");
  if (!group) return;
  dragOrderBefore = orderedEntries(state.lastNodes).map((entry) => entry.node_id);
  draggingNodeId = group.dataset.nodeId;
  dragGroupEl = group;
  group.classList.add("motor-row-group--dragging");
  document.body.classList.add("is-reordering");
  e.preventDefault();
}

function onDragMove(e) {
  if (draggingNodeId === null) return;
  e.preventDefault();
  const list = document.getElementById("fleet-list");
  const groups = Array.from(list.querySelectorAll(".motor-row-group"));
  // First row whose midpoint is still below the pointer -- insert above it.
  // Comparing against midpoints (not edges) is what makes the swap happen
  // once, at the halfway mark, instead of flickering back and forth while
  // the pointer sits over the boundary between two rows.
  const target = groups.find((g) => g !== dragGroupEl
    && e.clientY < g.getBoundingClientRect().top + g.getBoundingClientRect().height / 2);
  if (target) {
    if (target.previousElementSibling !== dragGroupEl) list.insertBefore(dragGroupEl, target);
  } else if (list.lastElementChild !== dragGroupEl) {
    list.appendChild(dragGroupEl);
  }
}

function endDrag() {
  if (draggingNodeId === null) return;
  const list = document.getElementById("fleet-list");
  const droppedNodeId = draggingNodeId;
  dragGroupEl.classList.remove("motor-row-group--dragging");
  document.body.classList.remove("is-reordering");
  const visibleAfter = Array.from(list.querySelectorAll(".motor-row-group"))
    .map((g) => g.dataset.nodeId);
  // Cleared before commitOrder(), whose renderFleetList() is the whole
  // point of the call and would no-op against the guard otherwise.
  draggingNodeId = null;
  dragGroupEl = null;
  commitOrder(mergeVisibleOrder(dragOrderBefore, visibleAfter), droppedNodeId);
}

// Moves one row by one visible slot -- the keyboard (and shaky-hand) route
// to the same result as a drag, from the grip's arrow keys.
function nudgeOrder(nodeId, delta) {
  const ordered = orderedEntries(state.lastNodes);
  const full = ordered.map((entry) => entry.node_id);
  const visible = ordered.filter((entry) => selectedBuckets.has(bucketFor(entry)))
    .map((entry) => entry.node_id);
  const from = visible.indexOf(nodeId);
  const to = from + delta;
  if (from < 0 || to < 0 || to >= visible.length) return;
  visible.splice(from, 1);
  visible.splice(to, 0, nodeId);
  commitOrder(mergeVisibleOrder(full, visible), nodeId);
}

document.getElementById("fleet-list").addEventListener("pointerdown", (e) => {
  const grip = e.target.closest('[data-role="grip"]');
  // Left button / touch / pen only -- a right-click on the grip should open
  // the context menu, not start a drag nothing can cancel.
  if (grip && e.button === 0) beginDrag(grip, e);
});

// On document, not on the grip: the grip lives inside the element being
// moved, and moving an element that holds pointer capture drops the capture
// mid-drag. Listening on document means the drag survives the pointer
// leaving the row (or the list) entirely.
document.addEventListener("pointermove", onDragMove, { passive: false });
document.addEventListener("pointerup", endDrag);
// A cancelled pointer (touch turned into a system gesture, window blur)
// still commits wherever the row currently sits rather than snapping it
// back -- the row is already visibly there, and silently undoing it would
// read as the drag having been lost.
document.addEventListener("pointercancel", endDrag);

document.getElementById("fleet-list").addEventListener("keydown", (e) => {
  const grip = e.target.closest('[data-role="grip"]');
  if (!grip) return;
  const nodeId = grip.closest(".motor-row-group").dataset.nodeId;
  if (e.key === "ArrowUp") { e.preventDefault(); nudgeOrder(nodeId, -1); }
  else if (e.key === "ArrowDown") { e.preventDefault(); nudgeOrder(nodeId, 1); }
});

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
    if (action === "setup_open") {
      openSetupDrawer(nodeId, button.dataset.step);
      return;
    }
    runAction(action, nodeId);
    return;
  }
  if (e.target.closest('[data-role="name"]')) return;
  // The grip is a drag handle, not a button -- a click that ends on it is
  // the tail of a drag (or a mis-aimed tap), never a request to expand.
  if (e.target.closest('[data-role="grip"]')) return;
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

// The native `toggle` event on <details> does NOT bubble -- but
// capture-phase dispatch still traverses ancestors on the way down
// regardless of the bubbles flag, so {capture: true} on this stable
// container is what makes delegation work here. Mounts the corresponding
// collapsible's charts lazily (Charts.attachExpanded, no HTML rebuild) the
// moment it's actually opened, per docs/CHART_CLUTTER_PLAN.md §1.5's "not
// rendered/computed until expanded."
document.getElementById("fleet-list").addEventListener("toggle", (e) => {
  if (!(e.target instanceof HTMLDetailsElement)) return;
  const role = e.target.dataset.role; // "scalars-details" | "waterfall-details"
  const nodeId = e.target.closest(".motor-row-group")?.dataset.nodeId;
  const set = role === "scalars-details" ? openScalarsIds
    : role === "waterfall-details" ? openWaterfallIds
    : null;
  if (!nodeId || !set) return;
  if (e.target.open) set.add(nodeId); else set.delete(nodeId);
  Charts.attachExpanded(expandedNodeIds, openScalarsIds, openWaterfallIds);
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
    if (action === "setup_open") {
      // Record mode's "Re-run setup" link, and the no-asset-class prompt.
      openSetupDrawer(openRecordNodeId, button.dataset.step);
      return;
    }
    if (!openRecordNodeId) return;
    // Setup owns every other button while its mode is showing -- it has
    // its own busy/error handling per step, which runAction's
    // fire-and-forget shape can't express.
    if (drawerMode === "setup") {
      Setup.handleClick(e, openRecordNodeId);
      return;
    }
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
  if (drawerMode === "setup") {
    // Enter submits the step the operator is on -- the same button they'd
    // otherwise reach for, so a name typed and Entered just moves on.
    if (e.key === "Enter" && e.target.tagName === "INPUT") {
      e.preventDefault();
      const step = recordDrawer.querySelector(".setup-step.is-current");
      const primary = step && step.querySelector(".setup-step__actions button:not([disabled])");
      if (primary) primary.click();
    }
    return;
  }
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
  if (drawerMode === "setup") {
    Setup.handleInput(e, openRecordNodeId);
    return;
  }
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
  if (drawerMode === "setup") return;
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
    // Before the body, so the skew is already corrected for the renders below.
    noteServerClock(res);
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
    for (const nodeId of openScalarsIds) {
      if (!(nodeId in state.lastNodes)) openScalarsIds.delete(nodeId);
    }
    for (const nodeId of openWaterfallIds) {
      if (!(nodeId in state.lastNodes)) openWaterfallIds.delete(nodeId);
    }
    // Re-base every countdown against this fresh trip_in_s before rendering
    // -- the local deadline is only an interpolation between polls, and the
    // server remains the authority on whether a trip is pending at all.
    for (const entry of Object.values(state.lastNodes)) noteTripCountdown(entry);
    renderSummary(state.lastNodes);
    renderTripBanner();
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
    // Setup's own state doesn't ride GET /nodes (only the step number does,
    // as setup_progress, for the tile's button) -- the per-condition frame
    // counters live on the setup snapshot, and those advance from the
    // ingestion thread with no step change to broadcast. Refreshed here, and
    // only while its drawer is actually open, so it costs nothing otherwise.
    if (openRecordNodeId && drawerMode === "setup") Setup.refresh(openRecordNodeId);
  } catch (err) {
    console.error("Failed to fetch /nodes", err);
  }
}

// Primary channel for registry/removed updates is this WS push (S4:
// "WebSocket for continuous real-time push"); the 5s poll below stays as
// the documented fallback. A brand new node IS broadcast the instant it's
// auto-added (registry.add() fires the same on_status_change listener as
// every other transition, api/app.py's _on_registry_status_change) --
// what the poll is still the ONLY source for is a node going quiet/online
// again with no NodeStatus change involved, which is why "last_seen"
// above exists.
Charts.init((msg) => {
  if (msg.type === "last_seen") {
    // charts.js forwards this on every "spectrum" frame -- see its own
    // comment. Coming online (or back online) is pure connectivity, no
    // NodeStatus transition, so it's otherwise invisible to this WS
    // handler and waits on the 5s poll fallback. Bucket-diffed rather than
    // an unconditional render like the branches below: this fires at full
    // frame rate for every node (unlike a status edge), and the shared
    // render tail is a full fleet-list rebuild -- see dashboard smoothness
    // notes for why that must not run per frame across a whole fleet.
    const entry = state.lastNodes[msg.node_id];
    if (!entry) return;
    const wasBucket = bucketFor(entry);
    entry.last_seen = msg.timestamp;
    if (bucketFor(entry) === wasBucket) return;
    // Only stamp lastWsTouchAt on an actual bucket flip, never on plain
    // last_seen advancement: this fires per frame, and stamping it every
    // time would leave it permanently newer than any poll's dispatch time,
    // so pollNodes()'s merge would skip every streaming node forever and
    // silently disable the REST fallback for exactly the nodes that are
    // live. A superseded last_seen is self-correcting anyway -- the next
    // frame is 200ms behind it.
    lastWsTouchAt[msg.node_id] = Date.now();
  } else if (msg.type === "removed") {
    lastWsTouchAt[msg.node_id] = Date.now();
    delete state.lastNodes[msg.node_id];
    expandedNodeIds.delete(msg.node_id);
    openScalarsIds.delete(msg.node_id);
    openWaterfallIds.delete(msg.node_id);
    if (openRecordNodeId === msg.node_id) closeRecordDrawer();
  } else if (msg.type === "node_order") {
    // Another browser (or phone) rearranged the list -- POST /nodes/order
    // is fleet-wide, so this carries every node's new index at once rather
    // than one "registry" message per row. The tab that did the dragging
    // has already applied the same mapping optimistically (commitOrder),
    // so this is a no-op there.
    for (const [nodeId, index] of Object.entries(msg.order || {})) {
      if (state.lastNodes[nodeId]) state.lastNodes[nodeId].sort_index = index;
    }
    renderFleetList(state.lastNodes);
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
  } else if (msg.type === "setup" || msg.type === "trip_confirm"
             || msg.type === "training_progress") {
    // Setup owns all three: its own step state, the confirm-by-stopping
    // result, and the training percentage that used to be narrated on the
    // tile. Nothing here needs to know their shapes.
    Setup.handleMessage(msg);
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
  renderTripBanner();
  if (editingNodeId === null && editingDeviceTypeNodeId === null) renderFleetList(state.lastNodes);
  if (openRecordNodeId) renderRecordDrawer();
}, (msg) => Perf.handleMessage(msg), (msg) => Alerts.handleMessage(msg),
   (msg) => Classifier.handleMessage(msg));

// setup.js renders into the drawer this module owns, and needs four things
// only this module is the authority on: the current node list, the poll,
// the toast container, and a way to ask for a re-render.
Setup.init({
  getNode: (nodeId) => state.lastNodes[nodeId],
  allNodes: () => state.lastNodes,
  assetClasses: () => deviceTypes,
  refreshNodes: () => pollNodes(),
  toast: (message, kind) => showToast(message, kind),
  rerender: () => { if (openRecordNodeId) renderRecordDrawer(); },
  close: () => closeRecordDrawer(),
});

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
