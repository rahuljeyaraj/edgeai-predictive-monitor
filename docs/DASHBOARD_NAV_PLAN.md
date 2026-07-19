# Plan — Dashboard navigation (topbar sections)

Status: **Brainstorm/design complete 2026-07-19. Implementation not started.** Not a
backlog item of its own — this fell out of a discussion about where the
Clickable-status-counts, Dev/perf, and WiFi-onboarding items would each live in the
UI once more than one exists. Captures that discussion so it doesn't need re-deriving
when the second of these sections gets built.

---

## 0. Why this exists

The dashboard (`base-station/python/frontend/`) is a single no-router vanilla-JS
page (established constraint, see [DEV_PERF_PAGE_PLAN.md](DEV_PERF_PAGE_PLAN.md) §1
and [CHART_CLUTTER_PLAN.md](CHART_CLUTTER_PLAN.md)). Right now it's one section
(Fleet). Once Dev/perf and a WiFi/Network section exist too, there needs to be a way
to move between them without turning the topbar into dead space.

## 1. Decision: topbar anchor-link nav, not a real router

Same collapsible-section primitive already locked in for Dev/perf's tiers and Chart
clutter's waterfall toggle — no SPA framework, no client-side routing. The topbar
(`.topbar` in `index.html`, currently just a title) gets a small set of anchor links
that jump/scroll to a section and expand it if collapsed. No distinct URLs, no
back-button semantics — consistent with everything else in this frontend.

Sections, as currently scoped:

- **Fleet** — the existing default view (summary tiles + fleet list).
- **Network** — base station WiFi status/reconfiguration (§2 below).
- **Dev-Perf** — per [DEV_PERF_PAGE_PLAN.md](DEV_PERF_PAGE_PLAN.md).
- **Alerts** — Telegram connect/prefs, per the Telegram item in
  [DASHBOARD_IDEAS_BACKLOG.md](DASHBOARD_IDEAS_BACKLOG.md).

## 2. Why the base station's WiFi setup belongs in this nav (and satellites don't)

Initially assumed the WiFi captive-portal form was inherently separate from the main
dashboard nav, since it's shown while the device has no network yet. **That was
wrong for the base station specifically** — its server already binds `0.0.0.0`
(`base-station/python/main.py:134,226`, via `uvicorn.run(app, host=args.host, ...)`
with `--host` defaulting to `0.0.0.0`), so it's the exact same dashboard app reachable
at whatever IP is currently live — the AP's IP while unconfigured, `epm-base.local`
via mDNS once joined to the factory network. There's no separate captive-portal app
on the base station side to keep out of the nav.

So the real flow: technician joins the base station's own AP → dashboard is already
reachable there → a **Network tab in that same dashboard** lets them either leave it
as AP mode (satellites join the base station's own AP later) or hand it factory
SSID+password to join and switch over to mDNS, per
[WIFI_ONBOARDING_PLAN.md](WIFI_ONBOARDING_PLAN.md) §1.

**Satellites are the genuine exception.** A satellite (ESP32-S3) doesn't run this
dashboard at all — it's a bare device with its own tiny captive-portal page
(WIFI_ONBOARDING_PLAN.md §2), reached by joining `EPM-SAT-<id>`'s own AP directly.
That page is a different device's UI entirely and has no relationship to this nav.

## 3. Next steps

- [ ] Build: topbar anchor-link markup in `index.html` (Fleet / Network / Dev-Perf /
      Alerts), scroll-to + expand-if-collapsed behavior in `app.js`.
- [ ] Build: Network section itself — base station WiFi status display +
      reconfigure form, per WIFI_ONBOARDING_PLAN.md §1. Not designed in detail yet
      (form fields, success/failure states) — that's its own follow-up.
- [ ] Blocked on: Dev-Perf and Alerts sections actually being built before there's a
      real second/third destination for this nav to jump to.
