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
- [ ] **Telegram alerts** — brick already exists in App Lab; add a way for the user
      to configure which Telegram accounts get fault/warning notifications.
- [ ] **Per-node EI data collection UI** — collect + label data per node from the
      dashboard, push to (possibly per-node) Edge Impulse projects, then download and
      load the trained model back into the pipeline.
- [ ] **Clickable status counts** — the status tally at the top of the dashboard
      should filter the fleet list when clicked.
- [ ] **LED matrix status message** — short rolling message ("Fault: Motor A ·
      Warning: Motor B", else "All good") pushed to the base station's LED matrix.
