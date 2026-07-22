# Plan — LED matrix fleet status message

Status: **Built 2026-07-22 (not yet run on hardware).** Design complete
2026-07-19. This doc captures the outcome of a design discussion for the "LED
matrix status message" item in [DASHBOARD_IDEAS_BACKLOG.md](DASHBOARD_IDEAS_BACKLOG.md)
— a short rolling text summary of fleet health pushed to the base station's own
LED matrix. Implementation landed per §4 below; the pure builder is unit-tested,
but the on-device Bridge push hasn't been exercised on real hardware yet.

---

## 0. Why this exists

The UNO Q base station has an 8×13 LED matrix and working scroll-text firmware
(`base-station/sketch/matrix_display.h`/`.cpp`) with an RPC path to set the message
(`Bridge.call("set_matrix_text", ...)`), but nothing in production drives it from
fleet status — the only current caller is a manual test script
(`base-station/tests/display_matrix_test.py`). This is also a UNO Q capability worth
highlighting on its own: a glanceable, no-app-needed field readout of fleet health,
physically on the device. The closest existing precedent is the RGB ring's status
wiring (`wire_local_status_led` in `base-station/python/main.py`, subscribing to
`registry.on_status_change`) — the matrix message should follow the same shape,
calling `set_matrix_text` instead of `set_rgb`.

## 1. Constraints from the firmware

- Max message length: 63 chars (`MATRIX_DISPLAY_MAX_TEXT_LEN`), truncated beyond that.
- Uppercase-only 5×7 font; no lowercase glyphs.
- Scrolling text on a small physical matrix is genuinely hard to read at a glance —
  favor short, simple, low-cognitive-load strings over anything dense or clever.

## 2. Data available — no fault-type taxonomy

`RegistryEntry` (`base-station/python/registry/registry.py`) has a `status`
(`NodeStatus` enum: `UNCOMMISSIONED`, `COMMISSIONING_COLLECTING`,
`COMMISSIONING_TRAINING`, `HEALTHY`, `WARNING`, `FAULT`, `OFFLINE`, `PAUSED`) and a
`display_name` per node, but fault detection is a single anomaly-score-vs-threshold
signal — there's no categorized fault reason to show. Considered naming individual
faulted nodes (`FAULT: MOTOR A`) but rejected in favor of counts-only: simpler,
always fits the 63-char limit regardless of fleet size, and avoids
rotation/pagination logic for large fleets. Commissioning states were considered too
but rejected — that flow already has dashboard UI; no point duplicating it on a
display that's hard to read closely. `PAUSED` is intentional/expected, so it's
ignored (not counted, not shown).

## 3. Message format — counts only, severity-ordered

Let `H` = healthy count, `W` = warning count, `F` = fault count, `O` = offline count,
among commissioned nodes only (`COMMISSIONING_*`/`UNCOMMISSIONED`/`PAUSED` excluded
from all counts):

- `H = W = F = O = 0` (empty fleet, or nothing commissioned yet) → **blank display**.
- `W = F = O = 0, H > 0` (everything healthy) → `"HOK"` (count included, not just
  "ALL GOOD" — doubles as an implicit fleet-size readout).
- Otherwise (anything wrong) → list only the **nonzero** buckets, comma-separated
  with **no space**, in fixed severity order **fault → warning → offline**, healthy
  count dropped entirely:
  - All three nonzero: `"FFLT,WWRN,OOFF"`
  - Any subset nonzero: e.g. just `"OOFF"` if that's the only issue.

`OFFLINE` is treated as a peer severity bucket, not folded into fault — a silently
dropped node is a distinct failure mode from an actively-faulting one, and burying it
inside the fault count would hide *why* the count went up. It's listed after warning
in the fixed order per this decision (fault, then warning, then offline).

**Words are display-only shorthand, not the `NodeStatus` vocabulary** —
`OK`/`FLT`/`WRN`/`OFF` here, vs. `healthy`/`fault`/`warning`/`offline` everywhere
else (registry, frontend, alerts). `FLT` abbreviates "fault", not "error": renaming
`FAULT`→`ERROR` throughout the codebase was considered (raised 2026-07-22, "error"
would match `WARNING`'s cadence) but rejected — this app already logs genuine
software errors (Bridge RPC failures) separately from equipment faults, and
conflating "this motor has a fault" with "the software errored" would be confusing.
`FLT` keeps the display-only word matched to the real vocabulary instead.
No spaces anywhere in the message: confirmed live on hardware that every glyph,
including a space, costs a fixed 6-column slot in the firmware's 5x7 font
(`FONT_GLYPH_STRIDE`, `matrix_display.cpp`) — on the 13-column-wide matrix, one space
blanks ~46% of the visible window at a time, which read as an oversized gap on real
hardware before this was tightened.

## 4. Next steps

- [x] Build: `base-station/python/main.py` — `wire_local_matrix_text(registry)`,
      sibling to `wire_local_status_led`, subscribing to `registry.on_status_change`.
      Unlike the RGB ring it does **not** filter to `BASE_STATION_NODE_ID` — the
      matrix shows fleet-wide counts, so it rebuilds on every node's change.
- [x] Build: format string builder — `matrix_status.fleet_status_text()`
      (`base-station/python/registry/matrix_status.py`), a pure function
      implementing the §3 truth table, unit-tested in
      `base-station/tests/matrix_status_test.py`.
- **Resolved — offline is staleness-derived, event-only.** `NodeStatus.OFFLINE`
  is never stored server-side (it's a `last_seen` staleness label the frontend
  computes), and `on_status_change` never fires when a node just goes quiet.
  Decision: no background timer — recompute the H/W/F/O counts on each
  `on_status_change` event, deriving offline from `last_seen` (mirroring
  `frontend/app.js`'s `OFFLINE_AFTER_S = 30`). Tradeoff accepted: the offline
  count can lag until some *other* status change triggers a rebuild.
- **Resolved — scroll restarts on change.** The firmware's `set_matrix_text`
  resets `scroll_col = 0` (matrix_display.cpp), so any new message restarts its
  scroll inherently; nothing to decide. Scroll speed is set to 150 ms/col
  (display_matrix_test.py's proven-readable value), required because the firmware
  default of 0 means static/no-scroll and would clip multi-word messages.
- **Resolved — coexists with `display_matrix_test.py`.** No exclusive ownership.
  Production wiring pushes on status change; the manual test still runs and
  transiently overwrites the matrix, and the next real status change restores
  the fleet summary. Both use the same two Bridge providers.
