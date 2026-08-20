"use strict";
/*
 * Guided setup -- docs/UNIFIED_COMMISSIONING_PLAN.md S5.1. One vertical
 * step list, rendered into the asset drawer (app.js owns the drawer
 * element; this owns what setup mode puts inside it).
 *
 * All the instructions live here, because this is the only surface the
 * operator is actually reading while standing at the machine. Written for a
 * technician at the machine, in two parts per step: what to do, then why it
 * matters (`setup-step__why`, quieter). Plain words over ours -- "the
 * output that stops this machine", not "trip output mapping" -- and no
 * sentence that only makes sense to whoever built this. The Machine off
 * step's wording carries the "software cannot verify the machine is off"
 * problem, exactly as pipeline/stopped_baseline.py's module docstring
 * demands, but it states it as the operator's own check rather than as a
 * lecture about what the model would learn.
 *
 * The drawer is a top-level element outside #fleet-list, so the 5s poll can
 * never wipe an in-progress edit -- that existing property is exactly what a
 * multi-step wizard needs.
 *
 * Errors surface inline on their own step, with the step still open for a
 * retry: every underlying session (stopped baseline, commissioning)
 * already supports retry-in-place, so nothing new is needed for that.
 *
 * Same self-contained module shape as classifier.js/perf.js: it owns its own
 * fetches and its own state, and takes a small bridge from app.js for the
 * things app.js is the authority on (the node list, the poll, toasts).
 */

const Setup = (() => {
  // Step 2 and 3 are named as a pair -- "Machine off" / "Machine running" --
  // because the only thing the operator has to get right about either is
  // which state the machine must be in while it runs. The old "Off" /
  // "Running conditions" didn't read as two halves of one measurement.
  //
  // "Stop output" replaces "Trip output": that step picks the output wired to
  // stop this machine and proves it, and "trip" is our word for it, not the
  // word on the machine.
  const STEP_TITLES = {
    name: "Name & class",
    trip_output: "Stop output",
    stopped: "Machine off",
    conditions: "Machine running",
    train: "Train",
    done: "Done",
  };

  const ICON_CHECK = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>';

  const state = {
    // node_id -> the server's setup snapshot (GET /nodes/<id>/setup). The
    // server owns step order and step state; this never advances a step on
    // its own, it only renders what came back.
    byNode: {},
    // Announced rig outputs (GET /trip_outputs) -- never a hardcoded motor
    // count. An empty list is a normal answer and drives the manual
    // fallback below, not an error.
    tripOutputs: [],
    // Live drafts for the two typed fields in step 1, and the condition
    // name in step 3 -- kept out of the DOM so a WS/poll re-render
    // mid-type redraws the same text instead of blanking the field.
    drafts: {},
    // node_id -> {motor_idx} while a confirm-by-stopping test is in flight.
    // The result arrives as a "trip_confirm" broadcast seconds later.
    confirming: {},
    // node_id -> the last confirm result, shown on the step until the next
    // test replaces it.
    confirmResult: {},
    // node_id -> message from the last failed stopped-baseline save (a 409,
    // e.g. "too unsteady to gate on"). stopped_baseline.py stays outside
    // SetupController's state.error on purpose (see the no-merge note up
    // top), so without this the step swallowed the reason and the Save
    // button looked unresponsive.
    baselineError: {},
    // node_id -> {epoch, total_epochs} from "training_progress".
    training: {},
    busy: false,
  };

  let bridge = {
    getNode: () => null,
    refreshNodes: async () => {},
    toast: () => {},
    rerender: () => {},
  };

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function titleCase(value) {
    return String(value).replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  }

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

  function draft(nodeId) {
    if (!state.drafts[nodeId]) state.drafts[nodeId] = {};
    return state.drafts[nodeId];
  }

  function snapshotFor(nodeId) {
    return state.byNode[nodeId] || null;
  }

  function stepOf(snapshot, id) {
    return (snapshot.steps || []).find((s) => s.id === id) || {};
  }

  // -- data ----------------------------------------------------------

  async function fetchTripOutputs() {
    try {
      const data = await api("GET", "/trip_outputs");
      state.tripOutputs = data.outputs || [];
    } catch (err) {
      console.error("Failed to fetch trip outputs", err);
      state.tripOutputs = [];
    }
  }

  async function start(nodeId, step) {
    try {
      const data = await api("POST", `/nodes/${nodeId}/setup/start`,
                              step ? { step } : {});
      state.byNode[nodeId] = data.setup;
    } catch (err) {
      bridge.toast(`Couldn't start setup: ${err.message}`, "error");
      bridge.rerender();
      return false;
    }
    await fetchTripOutputs();
    bridge.rerender();
    return true;
  }

  async function refresh(nodeId) {
    try {
      const data = await api("GET", `/nodes/${nodeId}/setup`);
      if (!data.setup && state.byNode[nodeId]) {
        // The server has no session for a node this drawer already thinks
        // it's mid-setup on -- almost always a restart wiping the in-memory
        // session out from under a drawer left open across it
        // (setup_controller.py: state is in-memory only). Left alone this
        // sticks on "Loading setup..." forever: every later poll just
        // re-confirms there's still nothing there, and nothing else ever
        // calls start() again. Re-enter instead of just recording the null.
        await start(nodeId);
        return;
      }
      state.byNode[nodeId] = data.setup;
    } catch (err) {
      console.error("Failed to fetch setup state", err);
    }
    bridge.rerender();
  }

  async function run(nodeId, fn) {
    // One in-flight action at a time per drawer: every button here either
    // moves the flow or drives a machine, and two of those racing is how a
    // step ends up disagreeing with what the operator just saw.
    if (state.busy) return;
    state.busy = true;
    bridge.rerender();
    try {
      await fn();
    } catch (err) {
      // Step-level errors come back on the snapshot and render inline on
      // the step; anything else (a network failure) gets a toast, since
      // there's no step to hang it on.
      if (err && err.__inline) await refresh(nodeId);
      else bridge.toast(err.message, "error");
    } finally {
      state.busy = false;
    }
    await bridge.refreshNodes();
    bridge.rerender();
  }

  async function post(nodeId, path, body) {
    try {
      const data = await api("POST", path, body);
      if (data.setup !== undefined) state.byNode[nodeId] = data.setup;
      return data;
    } catch (err) {
      err.__inline = true;
      throw err;
    }
  }

  // -- step bodies ---------------------------------------------------

  // Suggests a nickname from the asset class the operator is choosing right
  // now ("pump" + two existing pumps -> "Pump 3"), so the required field is
  // usually Enter-to-accept rather than a typing task. Only a suggestion:
  // it seeds the input's value and the operator overwrites it freely.
  function suggestName(nodeId, assetClass) {
    if (!assetClass) return "";
    const nodes = bridge.allNodes ? bridge.allNodes() : {};
    const sameClass = Object.values(nodes)
      .filter((n) => n.device_type === assetClass && n.node_id !== nodeId).length;
    return `${titleCase(assetClass)} ${sameClass + 1}`;
  }

  function nameStepHtml(nodeId, entry, step) {
    const d = draft(nodeId);
    const assetClass = d.deviceType !== undefined
      ? d.deviceType
      : (entry.device_type || "");
    const name = d.deviceName !== undefined
      ? d.deviceName
      : (step.has_nickname ? entry.device_name : suggestName(nodeId, assetClass));
    const classes = bridge.assetClasses ? bridge.assetClasses() : [];
    const options = classes.map((c) =>
      `<button type="button" class="setup-chip${c === assetClass ? " is-selected" : ""}" data-action="setup_pick_class" data-value="${escapeHtml(c)}">${escapeHtml(c)}</button>`
    ).join("");

    return `
      <p class="setup-step__hint">Name this machine and say what kind of machine it is.
        Both are required.</p>
      <p class="setup-step__why">The name is what alerts and the alarm banner show. The
        class groups this machine's recordings with others of the same kind.</p>
      <label class="setup-field">
        <span>Name</span>
        <input type="text" data-role="setup-name" autocomplete="off"
               placeholder="e.g. Pump 1" value="${escapeHtml(name)}" />
      </label>
      <label class="setup-field">
        <span>Asset class</span>
        <input type="text" data-role="setup-class" autocomplete="off"
               placeholder="e.g. pump" value="${escapeHtml(assetClass)}" />
      </label>
      ${options ? `<div class="setup-chips">${options}</div>` : ""}
      <div class="setup-step__actions">
        <button type="button" class="btn-label btn-label--ready" data-action="setup_advance">Continue</button>
      </div>`;
  }

  function tripOutputStepHtml(nodeId, entry, step) {
    const confirming = state.confirming[nodeId];
    const result = state.confirmResult[nodeId];
    const claimedElsewhere = (o) => o.claimed_by && o.claimed_by !== nodeId;

    let outputsHtml;
    if (!state.tripOutputs.length) {
      // No rig has announced (or it's running an older motor_driver.py) -- this
      // is the S3.5 fallback, and it's stated plainly rather than dressed up
      // as a working test.
      const d = draft(nodeId);
      outputsHtml = `
        <p class="setup-step__hint">No outputs have been reported, so there is nothing to
          test against. If you know the output number, enter it — it will be saved
          untested.</p>
        <label class="setup-field">
          <span>Output number</span>
          <input type="number" min="1" step="1" data-role="setup-manual-output"
                 value="${escapeHtml(d.manualOutput !== undefined ? d.manualOutput : (entry.trip_motor_idx || ""))}" />
        </label>
        <div class="setup-step__actions">
          <button type="button" class="btn-label" data-action="setup_manual_output">Save untested</button>
        </div>`;
    } else {
      outputsHtml = `<div class="setup-outputs">${state.tripOutputs.map((o) => {
        const claimed = claimedElsewhere(o);
        const isCurrent = entry.trip_motor_idx === o.idx;
        return `<div class="setup-output${isCurrent ? " is-current" : ""}">
          <span class="setup-output__name">${escapeHtml(o.name)}</span>
          ${claimed ? `<span class="setup-output__claim">used by ${escapeHtml(o.claimed_by)}</span>` : ""}
          <button type="button" class="btn-label" data-action="setup_confirm_output"
                  data-idx="${o.idx}" ${claimed || confirming ? "disabled" : ""}>
            ${isCurrent && entry.trip_motor_confirmed_at ? "Re-test" : "Test"}</button>
          <button type="button" class="btn-label setup-output__manual" data-action="setup_manual_output"
                  data-idx="${o.idx}" ${claimed || confirming ? "disabled" : ""}
                  title="Save this output without testing it">Save untested</button>
        </div>`;
      }).join("")}</div>`;
    }

    const liveHtml = confirming
      ? `<p class="setup-step__live">Stop sent to output ${confirming.motor_idx} — watch the machine…</p>`
      : result
        ? `<p class="setup-step__${result.confirmed ? "ok" : "warn"}">${escapeHtml(result.message)}</p>`
        : "";

    return `
      <p class="setup-step__hint">Which output stops this machine? Leave the machine
        running and press Test.</p>
      <p class="setup-step__why">Test sends a stop command. If the machine stops, the test
        passes.</p>
      ${outputsHtml}
      ${liveHtml}
      <div class="setup-step__actions">
        <button type="button" class="btn-label btn-label--ready" data-action="setup_advance"
                ${entry.trip_motor_idx ? "" : "disabled"}>Continue</button>
        ${entry.trip_motor_idx ? `<button type="button" class="btn-label" data-action="setup_unpair_output">Unpair</button>` : ""}
        <button type="button" class="btn-label" data-action="setup_skip">Nothing wired — skip</button>
      </div>`;
  }

  function stoppedStepHtml(nodeId, entry, step) {
    // Live count off the node entry, not off this step's snapshot: the
    // collected count advances from the ingestion thread and is pushed per
    // frame as a "stopped_baseline" broadcast (api/stopped_baseline_controller.py
    // notifies on every frame precisely because "the count IS the state
    // worth pushing"), which app.js merges into the node. The setup
    // snapshot only refreshes on a step change, so reading it here left an
    // operator standing at a switched-off machine watching a frozen 0/30
    // while the backend had already collected hundreds.
    const progress = entry.stopped_baseline_progress || step.progress;
    const error = state.baselineError[nodeId];
    let controls;
    if (progress) {
      const enough = progress.collected >= progress.min_frames;
      controls = `
        <p class="setup-step__live">Measuring — ${progress.collected} of ${progress.min_frames} readings.${enough ? " Enough to save." : ""}</p>
        ${error ? `<p class="setup-step__warn">${escapeHtml(error)}</p>` : ""}
        <div class="setup-step__actions">
          <button type="button" class="btn-label btn-label--save" data-action="setup_baseline_save" ${enough ? "" : "disabled"}>Save</button>
          <button type="button" class="btn-label" data-action="setup_baseline_cancel">Cancel</button>
        </div>`;
    } else {
      controls = `
        <div class="setup-step__actions">
          <button type="button" class="btn-label" data-action="setup_baseline_start">${step.measured ? "Measure again" : "Start"}</button>
          <button type="button" class="btn-label btn-label--ready" data-action="setup_advance" ${step.measured ? "" : "disabled"}>Continue</button>
        </div>`;
    }
    return `
      <p class="setup-step__hint">Switch the machine off. Wait until it has fully stopped
        moving, then press Start.</p>
      <p class="setup-step__why">This measures the machine at rest, so the system can tell
        "stopped" from "running".</p>
      ${step.measured ? `<p class="setup-step__ok">Measured.</p>` : ""}
      ${controls}`;
  }

  // The step has two shapes, and which one shows is the whole point of the
  // "stop between conditions" control: while a condition is COLLECTING the
  // operator gets a live count and one Stop; while nothing is collecting
  // (before the first condition, and after each Stop) they get the name field
  // for the next one. Previously naming the next condition was the only way
  // to end the current one, so the walk to the machine and the load change
  // itself landed in one condition or the other.
  function conditionsStepHtml(nodeId, entry, step) {
    const d = draft(nodeId);
    const conditions = step.conditions || [];
    const collecting = !!step.collecting;
    const current = collecting && conditions.length ? conditions[conditions.length - 1] : null;
    const enough = conditions.some((c) => c.frames >= step.min_frames);
    const nameValue = d.condition !== undefined
      ? d.condition
      : (conditions.length ? "" : "Normal running");

    const listHtml = conditions.length
      ? `<div class="setup-conditions">${conditions.map((c, i) => {
          const done = c.frames >= step.min_frames;
          const isCurrent = collecting && i === conditions.length - 1;
          return `<div class="setup-condition${isCurrent ? " is-current" : ""}">
            <span class="setup-condition__name">${escapeHtml(titleCase(c.name))}</span>
            <span class="setup-condition__count${done ? " is-done" : ""}">${c.frames} of ${step.min_frames}</span>
          </div>`;
        }).join("")}</div>`
      : "";

    // Only ever one primary action, and Enter picks the first enabled button
    // in this list (see app.js's drawer keydown handler), so Stop/Start
    // collecting has to come before Train.
    // The count lives in the list row above and is not repeated here: this
    // line carries the one thing the row can't say, which is whether the
    // operator may move on yet.
    const controls = current
      ? `<p class="setup-step__${current.frames >= step.min_frames ? "ok" : "live"}">${current.frames >= step.min_frames
             ? `Enough recorded for “${escapeHtml(titleCase(current.name))}”. Press Stop before you change the load.`
             : `Recording “${escapeHtml(titleCase(current.name))}”… Press Stop to cancel this attempt.`}</p>
         <div class="setup-step__actions">
           <button type="button" class="btn-label btn-label--save" data-action="setup_stop_condition">Stop</button>
           <button type="button" class="btn-label btn-label--ready" data-action="setup_advance"
                   ${enough ? "" : "disabled"}>Train</button>
         </div>`
      : `<label class="setup-field">
           <span>${conditions.length ? "Next condition" : "First condition"}</span>
           <input type="text" data-role="setup-condition" autocomplete="off"
                  placeholder="e.g. full load" value="${escapeHtml(nameValue)}" />
         </label>
         <div class="setup-step__actions">
           <button type="button" class="btn-label" data-action="setup_add_condition">Start recording</button>
           <button type="button" class="btn-label btn-label--ready" data-action="setup_advance"
                   ${enough ? "" : "disabled"}>Train</button>
         </div>`;

    return `
      <p class="setup-step__hint">Start the machine. Record each way it normally runs — no
        load, half load, full load — one at a time. Set the load, press Start recording,
        then press Stop before you change it.</p>
      <p class="setup-step__why">The system learns "normal" only from what you record here.
        Cover the machine's full working range, or a normal change of load can later be
        reported as a fault.</p>
      ${listHtml}
      ${controls}`;
  }

  // The one step that takes minutes, so it is the one that must never look
  // stuck: it names what is running, shows a percentage that moves, and says
  // outright that the page can be left. "Training complete" is stated in
  // words rather than left as a bar sitting at 100% -- the flow moves to the
  // next step on its own when the model lands, and 100% with no words looked
  // like the freeze it isn't. A model already on disk (a re-run of setup that
  // passes back through this step) says so instead of showing a dead 0%.
  function trainStepHtml(nodeId, entry, step) {
    if (step.error) {
      return `<p class="setup-step__warn">Training failed: ${escapeHtml(step.error)}</p>
        <p class="setup-step__why">Nothing was overwritten. Go back to Machine running and
          record again, or close setup and retry later.</p>`;
    }
    const tp = state.training[nodeId];
    // Floored at 1% once the first epoch has been reported: epoch 1 of 300
    // rounds to 0, and "Training -- 0%" under an empty bar is the exact
    // "nothing is happening" read this step has to avoid.
    const percent = tp ? Math.max(1, Math.round((100 * tp.epoch) / tp.total_epochs)) : 0;
    const complete = percent >= 100;
    const liveText = !tp
      ? (step.model_path ? "Model already trained." : "Starting…")
      : (complete ? "Training complete." : `Training — ${percent}%`);
    return `
      <p class="setup-step__hint">Building this machine's model from the readings you just
        recorded. This takes a few minutes.</p>
      <p class="setup-step__why">Training keeps running, and the machine keeps working.</p>
      <div class="setup-progress"><div class="setup-progress__fill" style="width:${percent}%"></div></div>
      <p class="setup-step__${complete || (!tp && step.model_path) ? "ok" : "live"}">${liveText}</p>`;
  }

  function doneStepHtml(nodeId, entry, snapshot) {
    const trip = stepOf(snapshot, "trip_output");
    const conditions = stepOf(snapshot, "conditions");
    const names = (conditions.trained_conditions || []).map(titleCase).join(", ");
    const tripText = trip.skipped || !entry.trip_motor_idx
      ? "None"
      : `Output ${entry.trip_motor_idx}${entry.trip_motor_confirmed_at ? " (tested)" : " (untested)"}`;
    return `
      <p class="setup-step__ok">${escapeHtml(entry.device_name)} is live and being monitored.</p>
      <dl class="setup-summary">
        <div><dt>Class</dt><dd>${escapeHtml(entry.device_type || "—")}</dd></div>
        <div><dt>Stop output</dt><dd>${escapeHtml(tripText)}</dd></div>
        <div><dt>Recorded</dt><dd>${escapeHtml(names || "—")}</dd></div>
      </dl>
      <div class="setup-step__actions">
        <button type="button" class="btn-label btn-label--ready" data-action="setup_finish">Close</button>
      </div>`;
  }

  function stepResultText(entry, snapshot, step) {
    switch (step.id) {
      case "name":
        return entry.device_type ? `${entry.device_name} · ${entry.device_type}` : "";
      case "trip_output":
        if (step.skipped) return "None";
        if (!entry.trip_motor_idx) return "";
        return `Output ${entry.trip_motor_idx}`
          + (entry.trip_motor_confirmed_at ? " · tested" : " · untested");
      case "stopped":
        return step.measured ? "Measured" : "";
      case "conditions": {
        const names = (step.conditions || []).map((c) => titleCase(c.name));
        const trained = (step.trained_conditions || []).map(titleCase);
        const list = names.length ? names : trained;
        return list.length ? list.join(", ") : "";
      }
      case "train":
        return step.model_path ? "Trained" : "";
      default:
        return "";
    }
  }

  function stepBodyHtml(nodeId, entry, snapshot, step) {
    switch (step.id) {
      case "name": return nameStepHtml(nodeId, entry, step);
      case "trip_output": return tripOutputStepHtml(nodeId, entry, step);
      case "stopped": return stoppedStepHtml(nodeId, entry, step);
      case "conditions": return conditionsStepHtml(nodeId, entry, step);
      case "train": return trainStepHtml(nodeId, entry, step);
      default: return doneStepHtml(nodeId, entry, snapshot);
    }
  }

  // -- drawer body ---------------------------------------------------

  function bodyHtml(entry) {
    const nodeId = entry.node_id;
    const snapshot = snapshotFor(nodeId);
    if (!snapshot) {
      return `<div class="record-drawer__body"><p class="setup-step__hint">Loading setup…</p></div>`;
    }
    const steps = (snapshot.steps || []).map((step, i) => {
      const isCurrent = step.id === snapshot.step;
      const result = stepResultText(entry, snapshot, step);
      // Completed steps collapse to a check plus a one-line result, and stay
      // clickable: re-entering one on its own is the point (recapturing a
      // baseline must not force a retrain).
      return `<div class="setup-step${isCurrent ? " is-current" : ""}${step.complete ? " is-done" : ""}"
                   data-step="${escapeHtml(step.id)}">
        <button type="button" class="setup-step__head" data-action="setup_goto" data-step="${escapeHtml(step.id)}">
          <span class="setup-step__num">${step.complete && !isCurrent ? ICON_CHECK : i + 1}</span>
          <span class="setup-step__title">${escapeHtml(STEP_TITLES[step.id] || step.id)}</span>
          ${!isCurrent && result ? `<span class="setup-step__result">${escapeHtml(result)}</span>` : ""}
        </button>
        ${isCurrent ? `<div class="setup-step__body">
          ${snapshot.error ? `<p class="setup-step__warn">${escapeHtml(snapshot.error)}</p>` : ""}
          ${stepBodyHtml(nodeId, entry, snapshot, step)}
        </div>` : ""}
      </div>`;
    }).join("");

    return `<div class="record-drawer__body setup-body${state.busy ? " is-busy" : ""}">
      <div class="setup-steps">${steps}</div>
      <button type="button" class="setup-cancel" data-action="setup_cancel">Cancel setup</button>
    </div>`;
  }

  // No step number in the title: the numbered step list right below it
  // already shows where the operator is, and appending "· step 4 of 6"
  // pushed a real asset name out of the header entirely (it ellipsised at
  // "Set up — Pump 1 · step 4 o…"). The tile's button is where the step
  // number earns its place, because there the list isn't visible.
  function headerTitle(entry) {
    const hasNickname = entry.device_name !== entry.node_id;
    return `Set up — ${hasNickname ? entry.device_name : entry.node_id}`;
  }

  // -- events --------------------------------------------------------

  function readInputs(root, nodeId) {
    // Read every visible field into the draft before acting on a click --
    // a click on a button doesn't fire "input" for the field the operator
    // was typing in, so the last keystroke would otherwise be lost.
    const d = draft(nodeId);
    const name = root.querySelector('[data-role="setup-name"]');
    if (name) d.deviceName = name.value;
    const cls = root.querySelector('[data-role="setup-class"]');
    if (cls) d.deviceType = cls.value;
    const condition = root.querySelector('[data-role="setup-condition"]');
    if (condition) d.condition = condition.value;
    const manual = root.querySelector('[data-role="setup-manual-output"]');
    if (manual) d.manualOutput = manual.value;
  }

  function handleInput(e, nodeId) {
    const d = draft(nodeId);
    const target = e.target;
    if (target.dataset.role === "setup-name") d.deviceName = target.value;
    else if (target.dataset.role === "setup-class") d.deviceType = target.value;
    else if (target.dataset.role === "setup-condition") d.condition = target.value;
    else if (target.dataset.role === "setup-manual-output") d.manualOutput = target.value;
    else return false;
    return true;
  }

  function handleClick(e, nodeId) {
    const button = e.target.closest("button[data-action]");
    if (!button) return false;
    const action = button.dataset.action;
    const root = e.currentTarget;
    const d = draft(nodeId);

    if (action === "setup_pick_class") {
      d.deviceType = button.dataset.value;
      // A picked class re-seeds the name suggestion, but never overwrites a
      // name the operator has actually typed.
      const nameInput = root.querySelector('[data-role="setup-name"]');
      if (nameInput && !nameInput.value.trim()) delete d.deviceName;
      bridge.rerender();
      return true;
    }
    if (action === "setup_goto") {
      const step = button.dataset.step;
      const snapshot = snapshotFor(nodeId);
      if (!snapshot || snapshot.step === step) return true;
      run(nodeId, () => start(nodeId, step));
      return true;
    }
    if (action === "setup_advance") {
      readInputs(root, nodeId);
      const body = {};
      if (snapshotFor(nodeId) && snapshotFor(nodeId).step === "name") {
        body.device_name = (d.deviceName || "").trim();
        body.device_type = (d.deviceType || "").trim();
      }
      run(nodeId, async () => {
        await post(nodeId, `/nodes/${nodeId}/setup/advance`, body);
        delete state.drafts[nodeId];
      });
      return true;
    }
    if (action === "setup_skip") {
      run(nodeId, () => post(nodeId, `/nodes/${nodeId}/setup/skip`));
      return true;
    }
    if (action === "setup_cancel") {
      run(nodeId, async () => {
        await post(nodeId, `/nodes/${nodeId}/setup/cancel`);
        delete state.byNode[nodeId];
        delete state.drafts[nodeId];
        bridge.close();
      });
      return true;
    }
    if (action === "setup_finish") {
      run(nodeId, async () => {
        await post(nodeId, `/nodes/${nodeId}/setup/cancel`);
        delete state.byNode[nodeId];
        bridge.close();
      });
      return true;
    }
    if (action === "setup_confirm_output") {
      const motorIdx = Number(button.dataset.idx);
      state.confirming[nodeId] = { motor_idx: motorIdx };
      delete state.confirmResult[nodeId];
      run(nodeId, async () => {
        try {
          await api("POST", `/nodes/${nodeId}/trip_motor/confirm`, { motor_idx: motorIdx });
        } catch (err) {
          delete state.confirming[nodeId];
          state.confirmResult[nodeId] = { confirmed: false, message: err.message };
          throw Object.assign(err, { __inline: true });
        }
      });
      return true;
    }
    if (action === "setup_manual_output") {
      readInputs(root, nodeId);
      const raw = button.dataset.idx !== undefined ? button.dataset.idx : d.manualOutput;
      const motorIdx = raw && Number(raw) > 0 ? Math.floor(Number(raw)) : null;
      run(nodeId, async () => {
        await post(nodeId, `/nodes/${nodeId}/trip_motor`, { motor_idx: motorIdx });
        await refresh(nodeId);
        bridge.toast(motorIdx ? `Output ${motorIdx} saved untested` : "Unpaired");
      });
      return true;
    }
    if (action === "setup_unpair_output") {
      run(nodeId, async () => {
        await post(nodeId, `/nodes/${nodeId}/trip_motor`, { motor_idx: null });
        await refresh(nodeId);
        bridge.toast("Unpaired");
      });
      return true;
    }
    if (action === "setup_baseline_start") {
      delete state.baselineError[nodeId];
      run(nodeId, async () => {
        await post(nodeId, `/nodes/${nodeId}/stopped_baseline/start`);
        await refresh(nodeId);
      });
      return true;
    }
    if (action === "setup_baseline_save") {
      run(nodeId, async () => {
        try {
          const data = await post(nodeId, `/nodes/${nodeId}/stopped_baseline/stop`);
          delete state.baselineError[nodeId];
          const frames = (data.stopped_baseline_result || {}).frames;
          bridge.toast(`Stopped baseline measured from ${frames} frames`);
        } catch (err) {
          // The capture stays live (see stopped_baseline/stop's docstring),
          // so tell the operator why and leave Save up for a retry --
          // post() tags this __inline, and without surfacing it here run()
          // would just refresh silently and Save would look dead.
          state.baselineError[nodeId] = err.message;
        }
        await refresh(nodeId);
      });
      return true;
    }
    if (action === "setup_baseline_cancel") {
      delete state.baselineError[nodeId];
      run(nodeId, async () => {
        await post(nodeId, `/nodes/${nodeId}/stopped_baseline/cancel`);
        await refresh(nodeId);
      });
      return true;
    }
    if (action === "setup_add_condition") {
      readInputs(root, nodeId);
      const name = (d.condition || "").trim();
      if (!name) return true;
      run(nodeId, async () => {
        await post(nodeId, `/nodes/${nodeId}/setup/condition`, { name });
        delete d.condition;
      });
      return true;
    }
    if (action === "setup_stop_condition") {
      // Ends this condition without starting the next one, so the load can be
      // changed with nothing being recorded. Always succeeds, even at 0
      // frames (e.g. the gate never confirmed RUNNING) -- below min_frames
      // the attempt is discarded rather than banked, see
      // CommissioningSession.stop_condition().
      run(nodeId, async () => {
        await post(nodeId, `/nodes/${nodeId}/setup/condition/stop`);
      });
      return true;
    }
    return false;
  }

  // -- WS ------------------------------------------------------------

  function handleMessage(msg) {
    if (msg.type === "setup") {
      if (msg.setup) state.byNode[msg.node_id] = msg.setup;
      else delete state.byNode[msg.node_id];
      return true;
    }
    if (msg.type === "trip_confirm") {
      delete state.confirming[msg.node_id];
      state.confirmResult[msg.node_id] = { confirmed: msg.confirmed, message: msg.message };
      // A failed test is a finding, not an error -- it says this is the
      // wrong output, which is exactly what the test is for.
      bridge.toast(msg.message, msg.confirmed ? "success" : "error");
      return true;
    }
    if (msg.type === "training_progress") {
      state.training[msg.node_id] = { epoch: msg.epoch, total_epochs: msg.total_epochs };
      return true;
    }
    return false;
  }

  function init(hooks) {
    bridge = Object.assign(bridge, hooks);
  }

  return { init, start, refresh, bodyHtml, headerTitle, handleClick, handleInput,
           handleMessage, snapshotFor };
})();

window.Setup = Setup;
