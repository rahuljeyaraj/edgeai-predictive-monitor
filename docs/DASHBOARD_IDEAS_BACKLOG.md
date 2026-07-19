# Dashboard / fleet ideas — brainstorm backlog

Status: **Backlog — raw ideas, not designed yet.** Captured from a brainstorm
session, one line each. Pick one and go deeper when ready.

- [x] **WiFi onboarding** (base station + satellite captive portal, mDNS+IP-override
      broker address) — done, see [WIFI_ONBOARDING_PLAN.md](WIFI_ONBOARDING_PLAN.md).
- [x] **Dev/perf page** — CPU/RAM/GPU, live sampling rate, dropped-frame count. A
      judge-facing "no data lost" highlight. Brainstorm/design complete, see
      [DEV_PERF_PAGE_PLAN.md](DEV_PERF_PAGE_PLAN.md); implementation not started.
- [x] **Chart clutter** — 3-axis accel + mic × (waterfall/spectrum/time-domain) could
      be 12+ graphs on one node. Brainstorm/design complete, see
      [CHART_CLUTTER_PLAN.md](CHART_CLUTTER_PLAN.md); implementation not started.
- [ ] **Per-node EI data collection UI** — collect + label data per node from the
      dashboard, push to (possibly per-node) Edge Impulse projects, then download and
      load the trained model back into the pipeline.
- [x] **Clickable status counts** — the status tally at the top of the dashboard
      should filter the fleet list when clicked. Brainstorm/design complete, see
      [STATUS_FILTER_PLAN.md](STATUS_FILTER_PLAN.md); implementation not started.
- [x] **LED matrix status message** — short rolling message summarizing fleet
      health, pushed to the base station's LED matrix. Brainstorm/design complete,
      see [LED_MATRIX_STATUS_PLAN.md](LED_MATRIX_STATUS_PLAN.md); implementation not
      started.
- [X] **Telegram alerts** (lowest priority of the bunch) — App Lab's `TelegramBot`
      brick can't push to a bare `@username` (Telegram platform restriction: bots
      need a numeric `chat_id`, only obtainable after the user messages the bot
      once). Flow: dashboard "Connect Telegram" button → deep link
      `t.me/<bot>?start=<token>` → user taps it → brick's built-in `/start` handler
      (`enable_builtin_welcome=True`) hands back `chat_id`, matched to the dashboard
      session via `<token>` and persisted against alert prefs (which nodes,
      fault-only vs fault+warning). Fault/warning pipeline then calls
      `bot.send_message(chat_id, text)` per subscriber when state transitions,
      debounced against flapping. Not designed further yet.
- [x] **Dashboard navigation (topbar sections)** — not a brainstormed item of its
      own, fell out of the status-counts/WiFi/Dev-perf discussions above. Brainstorm/
      design complete, see [DASHBOARD_NAV_PLAN.md](DASHBOARD_NAV_PLAN.md);
      implementation not started.
