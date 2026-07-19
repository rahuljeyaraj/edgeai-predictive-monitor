# Plan — Clickable status counts (fleet filter)

Status: **Brainstorm/design complete 2026-07-19. Implementation not started.**
This doc captures the outcome of a design discussion for the "Clickable status
counts" item in [DASHBOARD_IDEAS_BACKLOG.md](DASHBOARD_IDEAS_BACKLOG.md) — the
status tally row (`.summary__row` in `base-station/python/frontend/index.html`)
should filter the fleet list when clicked. Nothing here has been built yet; this is
the design to build against next.

---

## 0. Why this exists

Today the summary tiles (`.tile--healthy`, `.tile--warning`, `.tile--fault`, etc. in
`base-station/python/frontend/style.css`) are read-only counts. As the fleet grows,
scanning the full `.fleet__list` to find e.g. the 3 faulted nodes among 240 gets
tedious. The tiles should double as filter controls for the list below them.

## 1. Interaction model — multi-select toggle, not single-select radio

Rejected the obvious "click a tile, see only that status" (single-select) model in
favor of **multi-select toggle**:

- **Default state: every tile selected**, fleet list shows everything (matches
  today's no-filter behavior with zero clicks).
- Clicking a tile **toggles it off** — that status's assets disappear from the fleet
  list, tile stays visually present (not removed from the row).
- Clicking it again toggles it back on — those assets reappear.
- Any combination of tiles can be off at once (e.g. hide Paused + Offline to focus on
  live nodes, independent of health state).

## 2. Active-filter indicator — underline, not dim, not fill

Considered three options for marking which tiles are currently selected (see backlog
discussion): dim the deselected tiles, invert to a solid accent-color fill on
selected tiles, or a thin underline bar. **Chose the underline.**

Reason: these tiles double as an always-on ISA-101 alarm readout
(`style.css`'s color-meaning comment, healthy=emerald/warning=amber/fault=crimson —
meant to match the physical RGB status LED). Dimming a deselected "Fault" tile would
visually weaken an active-fault signal just because the user is currently filtered to
something else — a real risk on a monitoring dashboard, not just a generic filtered
list. Keeping every tile at full color/opacity and only toggling a bottom-border
underline preserves the alarm-color legibility regardless of filter state.

- Selected tile: underline bar in the tile's own `--accent` color underneath it.
- Deselected tile: no underline, count/label/border otherwise unchanged (full color).

## 3. No "All" tile as a separate status — replaced with a select-all control

The existing `.tile--all` (`--color-all: #e2e8f0`, neutral gray) stays, but its role
changes from "static total count" to a **derived select-all toggle**, same pattern as
a table header's "select all" checkbox:

- Its underline reflects a derived boolean: **on only when every individual status
  tile is currently selected.** Not an independent toggle state of its own.
- Deselecting any one status tile removes the All tile's underline (no longer "all"
  selected) — whether that's a partial deselect or everything deselected looks the
  same to it.
- Clicking the All tile is a single flip on that derived boolean:
  - If it's currently underlined (everything selected) → click → everything
    deselects, fleet list goes blank.
  - If it's currently not underlined (anything less than everything selected) →
    click → everything reselects, full fleet list returns.
- **Still shows the total fleet count** (unchanged from today) — it's a useful number
  independent of its new toggle behavior, and its already-neutral color continues to
  visually mark it as "a different kind of tile" from the health/state tiles next to
  it.

This also resolves the multi-select model's obvious gap (no quick way back to "show
everything" once several tiles are off) without reintroducing the old All tile's
problem — as a static count it was just another category, redundant with the sum of
the others; as a select-all control it's clearly a control, not a competing status.

## 4. Empty state

If the union of currently-selected statuses matches zero fleet assets — whether
because every tile is off, or because the specific combination that's on happens to
have no members right now — the fleet list is simply left blank. No separate "no
results" message; decided this doesn't need special-casing.

## 5. Next steps

- [ ] Build: frontend — click handlers on `.tile` elements in
      `base-station/python/frontend/app.js`, tracking per-status selected/deselected
      state (default: all selected).
- [ ] Build: frontend — filter `.fleet__list` rendering to the union of selected
      statuses.
- [ ] Build: CSS — underline-bar treatment per tile (`style.css`), replacing the
      current static `.tile` styling for the selected/deselected states; All tile's
      underline driven by the derived "all selected" boolean, not its own click
      state.
- [ ] Decide (not yet raised): does filter state persist across page refresh, or
      reset to all-selected like Dev/perf's non-persistent state (§7 of
      [DEV_PERF_PAGE_PLAN.md](DEV_PERF_PAGE_PLAN.md))? Likely reset-on-refresh for
      consistency with that precedent, but not explicitly decided yet.
