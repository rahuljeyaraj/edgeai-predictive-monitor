"use strict";
/*
 * Alerts tab (docs/DASHBOARD_IDEAS_BACKLOG.md's Telegram alerts item) --
 * "Connect Telegram" deep-link flow + per-subscriber alert-level/node-scope
 * prefs. Same module shape as perf.js: charts.js owns the one shared
 * WebSocket and forwards "telegram_subscribers" messages here via
 * Charts.init's third callback (app.js).
 *
 * Subscriber list comes from GET /alerts/telegram/subscribers + the live
 * WS push (api/app.py broadcasts on every connect/prefs-change/
 * disconnect); node names for the "specific nodes" scope editor come from
 * this module's own GET /nodes fetch, independent of app.js's Fleet
 * state -- same "each module owns its own data" precedent as perf.js
 * fetching /perf itself.
 */

const Alerts = (() => {
  // Mirrors alerts/alert_store.py's TOKEN_TTL_SECONDS -- purely a
  // client-side "stop showing 'waiting' forever if the flow was never
  // completed" timeout, not itself the source of truth for token expiry
  // (the backend re-checks that on every /start).
  const CONNECT_WAIT_TIMEOUT_MS = 15 * 60 * 1000;

  const state = {
    status: { configured: false, bot_username: null },
    subscribers: {},
    nodes: {},
    connecting: false,
    qrCode: null,
  };
  let connectTimeout = null;

  function escapeAttr(str) {
    return String(str).replace(/"/g, "&quot;");
  }

  function renderConnect() {
    const el = document.getElementById("alerts-connect");
    if (!el) return;

    if (!state.status.configured) {
      el.innerHTML = `<div class="perf-card alerts-connect__card">
        <div class="alerts-connect__title">Telegram alerts</div>
        <div class="perf-empty">Not configured yet -- set TELEGRAM_BOT_TOKEN (the
          arduino:telegram_bot brick's secret, via App Lab), then restart.</div>
      </div>`;
      return;
    }

    if (state.connecting) {
      el.innerHTML = `<div class="perf-card alerts-connect__card">
        <div class="alerts-connect__title">Telegram alerts</div>
        <div class="perf-empty">Waiting for you to tap Start in Telegram…</div>
        <button type="button" class="btn-text" id="alerts-connect-cancel">Cancel</button>
      </div>`;
      return;
    }

    const qrHtml = state.qrCode
      ? `<img class="alerts-connect__qr" src="${escapeAttr(state.qrCode)}"
               alt="QR code to open this Telegram connect link">`
      : "";
    el.innerHTML = `<div class="perf-card alerts-connect__card">
      <div class="alerts-connect__title">Telegram alerts</div>
      ${qrHtml}
      <button type="button" class="btn-primary" id="alerts-connect-btn">Connect Telegram</button>
    </div>`;
  }

  function nodeCheckboxesHtml(sub) {
    const nodeIds = Object.keys(state.nodes);
    if (nodeIds.length === 0) {
      return `<div class="perf-empty">No nodes yet.</div>`;
    }
    const selected = new Set(sub.node_ids || []);
    return nodeIds.map((nodeId) => {
      const name = (state.nodes[nodeId] && state.nodes[nodeId].device_name) || nodeId;
      return `<label class="alert-sub__node">
        <input type="checkbox" data-action="node" value="${escapeAttr(nodeId)}"
               ${selected.has(nodeId) ? "checked" : ""}>
        ${escapeHtml(name)}
      </label>`;
    }).join("");
  }

  function subscriberHtml(chatId, sub) {
    const allNodes = sub.node_ids === null || sub.node_ids === undefined;
    return `<div class="alert-sub" data-chat-id="${escapeAttr(chatId)}">
      <div class="alert-sub__header">
        <span class="alert-sub__name">${escapeHtml(sub.first_name)}${
          sub.username ? ` <span class="alert-sub__username">@${escapeHtml(sub.username)}</span>` : ""
        }</span>
        <button type="button" class="btn-icon btn-icon--danger" data-action="disconnect" title="Disconnect">✕</button>
      </div>
      <div class="alert-sub__prefs">
        <div class="alert-sub__row">
          <button type="button" class="waterfall-toggle__btn${!sub.fault_only ? " is-active" : ""}"
                  data-action="tier" data-fault-only="false">Fault + Warning</button>
          <button type="button" class="waterfall-toggle__btn${sub.fault_only ? " is-active" : ""}"
                  data-action="tier" data-fault-only="true">Fault only</button>
        </div>
        <div class="alert-sub__row">
          <button type="button" class="waterfall-toggle__btn${allNodes ? " is-active" : ""}"
                  data-action="scope" data-scope="all">All nodes</button>
          <button type="button" class="waterfall-toggle__btn${!allNodes ? " is-active" : ""}"
                  data-action="scope" data-scope="pick">Specific nodes</button>
        </div>
        ${allNodes ? "" : `<div class="alert-sub__nodes">${nodeCheckboxesHtml(sub)}</div>`}
      </div>
    </div>`;
  }

  function renderSubscribers() {
    const el = document.getElementById("alerts-subscribers");
    if (!el) return;
    const chatIds = Object.keys(state.subscribers);
    if (chatIds.length === 0) {
      el.innerHTML = `<div class="perf-empty">No connected Telegram chats yet.</div>`;
      return;
    }
    el.innerHTML = chatIds.map((chatId) => subscriberHtml(chatId, state.subscribers[chatId])).join("");
  }

  function render() {
    renderConnect();
    renderSubscribers();
  }

  async function postJson(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || `${res.status}`);
    }
    return res.json();
  }

  function clearConnectWait() {
    state.connecting = false;
    if (connectTimeout !== null) {
      clearTimeout(connectTimeout);
      connectTimeout = null;
    }
  }

  async function startConnect() {
    try {
      const { deep_link } = await postJson("/alerts/telegram/connect");
      window.open(deep_link, "_blank");
      state.connecting = true;
      connectTimeout = setTimeout(() => { clearConnectWait(); render(); }, CONNECT_WAIT_TIMEOUT_MS);
      render();
    } catch (err) {
      alert(`Connect failed: ${err.message}`);
    }
  }

  // The QR shown by default alongside the button is its own connect token,
  // independent of whatever token startConnect() mints on click -- each is
  // one-shot (alert_store.py's consume_token), so re-minting here after
  // every subscriber-list change (i.e. a possible connect) keeps the QR
  // scannable for the next person instead of going dead after first use.
  async function refreshConnectQr() {
    if (!state.status.configured) return;
    try {
      const { qr_code } = await postJson("/alerts/telegram/connect");
      state.qrCode = qr_code;
      renderConnect();
    } catch (err) {
      console.error("Failed to load Telegram connect QR", err);
    }
  }

  async function updatePrefs(chatId, faultOnly, nodeIds) {
    try {
      await postJson(`/alerts/telegram/subscribers/${chatId}/prefs`,
                      { fault_only: faultOnly, node_ids: nodeIds });
    } catch (err) {
      alert(`Failed to update alert prefs: ${err.message}`);
    }
    await refreshSubscribers();
  }

  async function refreshSubscribers() {
    try {
      const res = await fetch("/alerts/telegram/subscribers");
      state.subscribers = await res.json();
      renderSubscribers();
    } catch (err) {
      console.error("Failed to fetch /alerts/telegram/subscribers", err);
    }
  }

  document.getElementById("alerts-connect").addEventListener("click", (e) => {
    if (e.target.closest("#alerts-connect-btn")) {
      startConnect();
    } else if (e.target.closest("#alerts-connect-cancel")) {
      clearConnectWait();
      render();
    }
  });

  document.getElementById("alerts-subscribers").addEventListener("click", (e) => {
    const card = e.target.closest(".alert-sub");
    if (!card) return;
    const chatId = card.dataset.chatId;
    const sub = state.subscribers[chatId];
    if (!sub) return;

    const disconnectBtn = e.target.closest('[data-action="disconnect"]');
    if (disconnectBtn) {
      if (!confirm(`Disconnect ${sub.first_name} from Telegram alerts?`)) return;
      postJson(`/alerts/telegram/subscribers/${chatId}/disconnect`)
        .then(refreshSubscribers)
        .catch((err) => alert(`Disconnect failed: ${err.message}`));
      return;
    }

    const tierBtn = e.target.closest('[data-action="tier"]');
    if (tierBtn) {
      updatePrefs(chatId, tierBtn.dataset.faultOnly === "true", sub.node_ids);
      return;
    }

    const scopeBtn = e.target.closest('[data-action="scope"]');
    if (scopeBtn) {
      if (scopeBtn.dataset.scope === "all") {
        updatePrefs(chatId, sub.fault_only, null);
      } else {
        // Switching from "All nodes" to "Specific nodes" defaults to every
        // currently-known node selected (so behavior doesn't silently
        // change to "alerts for nothing" the instant the toggle flips) --
        // the operator then unchecks what they don't want.
        updatePrefs(chatId, sub.fault_only, Object.keys(state.nodes));
      }
    }
  });

  document.getElementById("alerts-subscribers").addEventListener("change", (e) => {
    const input = e.target.closest('input[data-action="node"]');
    if (!input) return;
    const card = e.target.closest(".alert-sub");
    const chatId = card.dataset.chatId;
    const sub = state.subscribers[chatId];
    if (!sub) return;
    const checked = new Set(
      Array.from(card.querySelectorAll('input[data-action="node"]:checked')).map((el) => el.value));
    updatePrefs(chatId, sub.fault_only, Array.from(checked));
  });

  function handleMessage(msg) {
    // msg is {"type": "telegram_subscribers", "subscribers": {...}}.
    state.subscribers = msg.subscribers || {};
    clearConnectWait();
    render();
    refreshConnectQr();
  }

  async function init() {
    try {
      const [statusRes, subsRes, nodesRes] = await Promise.all([
        fetch("/alerts/telegram/status"), fetch("/alerts/telegram/subscribers"), fetch("/nodes"),
      ]);
      state.status = await statusRes.json();
      state.subscribers = await subsRes.json();
      state.nodes = await nodesRes.json();
    } catch (err) {
      console.error("Failed to fetch initial Telegram alerts state", err);
    }
    render();
    refreshConnectQr();
  }

  return { init, handleMessage };
})();

window.Alerts = Alerts;
