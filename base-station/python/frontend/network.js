"use strict";
/*
 * Network tab (docs/WIFI_ONBOARDING_PLAN.md S1) -- shows the base
 * station's current WiFi mode/SSID/IP (GET /network/wifi/status, backed by
 * host/wifi_bridge.py over its own Unix socket -- see python/network/
 * wifi.py) and a "join factory WiFi" form (POST /network/wifi/connect).
 *
 * No live WS push here (unlike Perf/Alerts): WiFi state changes rarely and
 * mostly only in response to this tab's own action, so a fetch on tab
 * activation (+ after a connect attempt) is enough -- same reasoning
 * Classifier.refresh() uses for its capture list.
 */

const Network = (() => {
  const state = {
    status: { available: false, mode: null, ssid: null, ip: null },
    form: { ssid: "", password: "", busy: false, error: null },
  };

  function escapeHtml(str) {
    return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function escapeAttr(str) {
    return String(str).replace(/"/g, "&quot;");
  }

  function modeLabel(mode) {
    if (mode === "sta") return "Connected";
    if (mode === "ap") return "Access point (onboarding)";
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
      ${s.ip ? statusRow("IP address", s.ip) : ""}
      ${s.mode === "sta" ? `<div class="perf-chart__caption">Reachable at epm-base.local on this network.</div>` : ""}
    </div>`;
  }

  function renderConnect() {
    const el = document.getElementById("network-connect");
    if (!el) return;
    const f = state.form;
    el.innerHTML = `<div class="perf-card">
      <div class="alerts-connect__title">Join factory WiFi</div>
      <div class="perf-chart__caption">While unjoined, this device hosts its own open network
        (EPM-BaseStation) so you can reach this page and submit the real network's details here.</div>
      <div class="classifier-ei__link" id="network-connect-form">
        <input type="text" class="classifier-table__rename-input" placeholder="SSID"
               data-action="network_ssid" value="${escapeAttr(f.ssid)}" autocomplete="off" ${f.busy ? "disabled" : ""}>
        <input type="password" class="classifier-table__rename-input" placeholder="Password"
               data-action="network_password" value="${escapeAttr(f.password)}" autocomplete="off" ${f.busy ? "disabled" : ""}>
        <button type="button" class="btn-primary" data-action="network_connect_submit" ${f.busy ? "disabled" : ""}>
          ${f.busy ? "Connecting…" : "Connect"}
        </button>
        ${f.error ? `<div class="classifier-ei__error">${escapeHtml(f.error)}</div>` : ""}
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

  async function submitConnect() {
    const f = state.form;
    f.busy = true;
    f.error = null;
    renderConnect();
    try {
      await postJson("/network/wifi/connect", { ssid: f.ssid, password: f.password });
      // Success: forget the form entirely, including the password --
      // nothing about it lingers longer than this one request (same rule
      // Classifier's EI link form follows on success).
      state.form = { ssid: "", password: "", busy: false, error: null };
      await refreshStatus();
    } catch (err) {
      // Failure: drop the password and let the user retype it rather than
      // holding it in state indefinitely.
      state.form = { ...f, password: "", busy: false, error: err.message };
    }
    renderConnect();
  }

  document.getElementById("network-connect").addEventListener("click", (e) => {
    if (e.target.closest('[data-action="network_connect_submit"]')) submitConnect();
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
  }

  async function init() {
    await refreshStatus();
    renderConnect();
  }

  return { init, refresh };
})();

window.Network = Network;
