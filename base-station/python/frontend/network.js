"use strict";
/*
 * Network tab (docs/WIFI_ONBOARDING_PLAN.md S1) -- shows the base
 * station's current WiFi mode/SSID/IP (GET /network/wifi/status, backed by
 * host/wifi_bridge.py over its own Unix socket -- see python/network/
 * wifi.py) and a join-a-network form (scanned SSID list via GET
 * /network/wifi/scan, submit via POST /network/wifi/connect).
 *
 * No live WS push here (unlike Perf/Alerts): WiFi state changes rarely and
 * mostly only in response to this tab's own action, so a fetch on tab
 * activation (+ after a connect attempt) is enough -- same reasoning
 * Classifier.refresh() uses for its capture list.
 */

const Network = (() => {
  const state = {
    status: { available: false, mode: null, ssid: null, ip: null },
    form: { ssid: "", password: "", busy: false, error: null, notice: null, success: null },
    networks: { list: [], scanning: false, error: null },
  };

  function escapeHtml(str) {
    return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function escapeAttr(str) {
    return String(str).replace(/"/g, "&quot;");
  }

  function modeLabel(mode) {
    if (mode === "sta") return "Connected";
    if (mode === "ap") return "Access point";
    return "Disconnected";
  }

  function statusRow(label, value) {
    return `<div class="network-status__row">
      <span class="perf-chart__label">${label}</span>
      <span class="network-status__value">${escapeHtml(value)}</span>
    </div>`;
  }

  function renderStatus() {
    const el = document.getElementById("network-status");
    if (!el) return;
    const s = state.status;
    if (!s.available) {
      el.innerHTML = `<div class="perf-card network-status__card">
        <div class="alerts-connect__title">WiFi</div>
        <div class="perf-empty">Not provisioned on this board yet (run provision-wifi.sh).</div>
      </div>`;
      return;
    }
    el.innerHTML = `<div class="perf-card network-status__card">
      <div class="alerts-connect__title">WiFi</div>
      ${statusRow("Mode", modeLabel(s.mode))}
      ${s.ssid ? statusRow("SSID", s.ssid) : ""}
      ${(s.ip && s.mode !== "ap") ? statusRow("IP address", s.ip) : ""}
      ${s.mode === "sta" ? `<div class="perf-chart__caption">Reachable at epm-base.local on this network.</div>` : ""}
    </div>`;
  }

  function renderConnect() {
    const el = document.getElementById("network-connect");
    if (!el) return;
    const f = state.form;
    const n = state.networks;
    const networkOptions = n.list.map((net) => `<option value="${escapeAttr(net.ssid)}">`).join("");
    // Read this BEFORE tapping Connect, not after: on the onboarding
    // hotspot, tapping Connect can close this page almost immediately
    // (the device's own network switches out from under it) -- too fast
    // to read a message that only appears afterward.
    const closeTip = state.status.mode === "ap"
      ? `<div class="perf-chart__caption">This page may close right after you tap Connect --
          that's normal. Reconnect to your usual Wi-Fi and reopen the dashboard to check.</div>`
      : "";
    el.innerHTML = `<div class="perf-card">
      <div class="alerts-connect__title">Connect to Wi-Fi</div>
      ${closeTip}
      <div class="classifier-ei__link" id="network-connect-form">
        <input type="text" class="classifier-table__rename-input" placeholder="SSID" list="network-ssid-options"
               data-action="network_ssid" value="${escapeAttr(f.ssid)}" autocomplete="off" ${f.busy ? "disabled" : ""}>
        <datalist id="network-ssid-options">${networkOptions}</datalist>
        <input type="password" class="classifier-table__rename-input" placeholder="Password"
               data-action="network_password" value="${escapeAttr(f.password)}" autocomplete="off" ${f.busy ? "disabled" : ""}>
        <button type="button" class="btn-primary" data-action="network_connect_submit" ${f.busy ? "disabled" : ""}>
          ${f.busy ? "Connecting…" : "Connect"}
        </button>
        <button type="button" class="btn-label" data-action="network_rescan" ${(f.busy || n.scanning) ? "disabled" : ""}>
          ${n.scanning ? "Scanning…" : "Scan for networks"}
        </button>
        ${!n.scanning && n.error ? `<div class="classifier-ei__error">Couldn't scan for networks -- try again.</div>` : ""}
        ${!n.scanning && !n.error && n.list.length === 0 ? `<div class="perf-chart__caption">No networks found yet -- try Scan, or type a hidden network's name directly.</div>` : ""}
        ${f.error ? `<div class="classifier-ei__error">${escapeHtml(f.error)}</div>` : ""}
        ${f.notice ? `<div class="network-connect__notice">${escapeHtml(f.notice)}</div>` : ""}
        ${f.success ? `<div class="network-connect__success">${escapeHtml(f.success)}</div>` : ""}
      </div>
    </div>`;
  }

  async function postJson(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const responseBody = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(responseBody.error || `${res.status}`);
    }
    return responseBody;
  }

  async function refreshStatus() {
    try {
      const res = await fetch("/network/wifi/status");
      state.status = await res.json();
    } catch (err) {
      console.error("Failed to fetch /network/wifi/status", err);
    }
    renderStatus();
  }

  async function scanNetworks() {
    state.networks.scanning = true;
    state.networks.error = null;
    renderConnect();
    try {
      const res = await fetch("/network/wifi/scan");
      const body = await res.json().catch(() => ({}));
      state.networks.list = body.networks || [];
      // A real scan failure (nmcli itself failed/timed out, or the bridge
      // is unreachable) vs. a genuinely clean empty scan look the same
      // over the wire unless the backend says which -- see host/
      // wifi_bridge.py's scan_payload() and python/network/wifi.py's
      // scan(), both of which carry this `error` field for exactly that.
      state.networks.error = body.error || (res.ok ? null : `${res.status}`);
    } catch (err) {
      console.error("Failed to fetch /network/wifi/scan", err);
      state.networks.list = [];
      state.networks.error = "unreachable";
    }
    state.networks.scanning = false;
    renderConnect();
  }

  // After a network-level drop (see submitConnect below), poll this same
  // origin's /network/wifi/status for a while to tell a real failure
  // (the device fell back to its own Hotspot -- reachable again at this
  // exact address) apart from a real success (this address is gone for
  // good, since the device switched away from it). Fires and updates
  // state/re-renders whenever it resolves or gives up.
  const POLL_FOR_OUTCOME_TIMEOUT_MS = 25000;
  const POLL_FOR_OUTCOME_INTERVAL_MS = 2000;

  async function pollForOutcome(targetSsid) {
    const deadline = Date.now() + POLL_FOR_OUTCOME_TIMEOUT_MS;
    while (Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, POLL_FOR_OUTCOME_INTERVAL_MS));
      try {
        const res = await fetch("/network/wifi/status");
        if (!res.ok) continue;
        const s = await res.json();
        if (s.mode === "sta" && s.ssid === targetSsid) {
          state.form.notice = null;
          state.form.success = `Connected to "${targetSsid}".`;
          renderConnect();
          return;
        }
        // Reachable again at this same address but NOT on the target
        // network (back on its own Hotspot, or fell back to whatever it
        // had before) -- the join did not hold, a real failure, not a
        // guess.
        state.form.notice = null;
        state.form.error = `Couldn't join "${targetSsid}". Still on EPM-BaseStation -- check the password and try again.`;
        renderConnect();
        return;
      } catch (err) {
        // Still unreachable -- keep polling until the deadline.
      }
    }
    // Never became reachable at this address again within the window --
    // the one thing that WOULD make it reachable again (falling back to
    // its own Hotspot on a failed join) didn't happen, so the most likely
    // explanation left is that it joined successfully and this address is
    // simply gone for good (single-radio "full switch on success", see
    // docs/WIFI_ONBOARDING_PLAN.md S1).
    state.form.notice = `Probably connected to "${targetSsid}". Reconnect your phone to it, then check http://epm-base.local.`;
    renderConnect();
  }

  async function submitConnect() {
    const f = state.form;
    const targetSsid = f.ssid;
    f.busy = true;
    f.error = null;
    f.notice = null;
    f.success = null;
    renderConnect();
    try {
      await postJson("/network/wifi/connect", { ssid: f.ssid, password: f.password });
      // Success: forget the form entirely, including the password --
      // nothing about it lingers longer than this one request (same rule
      // Classifier's EI link form follows on success).
      state.form = { ssid: "", password: "", busy: false, error: null, notice: null,
        success: `Connected to "${targetSsid}".` };
      await refreshStatus();
    } catch (err) {
      if (err instanceof TypeError) {
        // fetch() itself failed at the network level (Chrome: "Failed to
        // fetch", Firefox: "NetworkError...") -- distinct from postJson's
        // plain Error for a real HTTP-level failure. This does NOT by
        // itself mean the join succeeded: connect_wifi tears the Hotspot
        // down BEFORE attempting the join (see host/wifi_bridge.py's
        // handle_connect), so a client that submitted this form while on
        // the Hotspot loses its connection EITHER way -- success or a
        // wrong password both look identical from here. pollForOutcome
        // tells them apart by checking whether this address becomes
        // reachable again (== fell back to the Hotspot == failed).
        state.form = { ssid: "", password: "", busy: false, error: null,
          notice: `Switching networks… checking if "${targetSsid}" connected.`,
          success: null };
        pollForOutcome(targetSsid);
      } else {
        // Failure: drop the password and let the user retype it rather than
        // holding it in state indefinitely.
        state.form = { ...f, password: "", busy: false, error: err.message, notice: null, success: null };
      }
    }
    renderConnect();
  }

  document.getElementById("network-connect").addEventListener("click", (e) => {
    if (e.target.closest('[data-action="network_connect_submit"]')) submitConnect();
    if (e.target.closest('[data-action="network_rescan"]')) scanNetworks();
  });

  document.getElementById("network-connect").addEventListener("input", (e) => {
    const ssidInput = e.target.closest('[data-action="network_ssid"]');
    if (ssidInput) { state.form.ssid = ssidInput.value; return; }
    const passwordInput = e.target.closest('[data-action="network_password"]');
    if (passwordInput) { state.form.password = passwordInput.value; return; }
  });

  document.getElementById("network-connect").addEventListener("keydown", (e) => {
    if (!e.target.closest("#network-connect-form")) return;
    if (e.key === "Enter") { e.preventDefault(); submitConnect(); }
  });

  async function refresh() {
    await refreshStatus();
    await scanNetworks();
  }

  async function init() {
    await refreshStatus();
    renderConnect();
    await scanNetworks();
  }

  return { init, refresh };
})();

window.Network = Network;
