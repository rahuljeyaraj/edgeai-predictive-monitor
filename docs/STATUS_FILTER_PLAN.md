# Plan — Clickable status counts (fleet filter)

Status: **SHIPPED 2026-07-19.** Design locked same day, implemented same day.
This doc captures the outcome of a design discussion for the "Clickable status
counts" item in [DASHBOARD_IDEAS_BACKLOG.md](DASHBOARD_IDEAS_BACKLOG.md) — the
status tally row (`.summary__row` in `base-station/python/frontend/index.html`)
now filters the fleet list when clicked, per the design below. Verified with a
headless-browser smoke test (mock `/nodes` data covering all 6 buckets): toggling
individual tiles, the derived "all" select-all/deselect-all flip, and the blank
empty state all behave as designed. Not yet verified live on real hardware.

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

## 2. Active-filter indicator — tinted fill, not dim, not underline

Considered three options for marking which tiles are currently selected: dim the
deselected tiles, an underline bar, or a tinted accent-color fill on selected tiles.
**Shipped the underline first (2026-07-19); replaced it with tinted fill same day**
after live use showed the underline was too subtle against the already-bright
border/count/label — hard to tell selected from deselected at a glance.

The ISA-101-alarm-readout reasoning still holds (`style.css`'s color-meaning comment,
healthy=emerald/warning=amber/fault=crimson, meant to match the physical RGB status
LED): dimming a deselected "Fault" tile would visually weaken an active-fault signal
just because the user is currently filtered to something else. Tinted fill keeps that
guarantee — **border, count, and label stay at full `--accent` color in every
state** — but adds a second, much larger visual channel (the tile's background) that
the alarm reading never used, so selection state gets a clear, independent signal
instead of competing with the alarm color for the same channel.

- Selected tile: background is `color-mix(in srgb, var(--accent) 18%, #1e293b)` — a
  soft tint of the tile's own color.
- Deselected tile: flat `#1e293b` (today's plain tile background). Border/count/label
  unchanged (full color) in both states.

## 3. No "All" tile as a separate status — replaced with a select-all control

The existing `.tile--all` (`--color-all: #e2e8f0`, neutral gray) stays, but its role
changes from "static total count" to a **derived select-all toggle**, same pattern as
a table header's "select all" checkbox:

- Its tinted-fill state reflects a derived boolean: **filled only when every
  individual status tile is currently selected.** Not an independent toggle state of
  its own.
- Deselecting any one status tile flattens the All tile's fill (no longer "all"
  selected) — whether that's a partial deselect or everything deselected looks the
  same to it.
- Clicking the All tile is a single flip on that derived boolean:
  - If it's currently filled (everything selected) → click → everything
    deselects, fleet list goes blank.
  - If it's currently flat (anything less than everything selected) →
    click → everything reselects, full fleet list returns.
- **Still shows the total fleet count** (unchanged from today) — it's a useful number
  independent of its new toggle behavior, and its already-neutral color continues to
  visually mark it as "a different kind of tile" from the health/state tiles next to
  it.
- **Added post-ship (2026-07-19): hidden entirely when 0 or 1 status buckets are
  non-empty.** With only one visible status tile, All's count just duplicates that
  tile's own count, and toggling All does exactly what toggling that one tile already
  does — no functionality lost by hiding it. Only earns its keep once 2+ buckets are
  visible and a bulk toggle actually saves clicks. Implemented as
  `visibleBucketCount > 1` alongside the zero-count filter in §4.1.

This also resolves the multi-select model's obvious gap (no quick way back to "show
everything" once several tiles are off) without reintroducing the old All tile's
problem — as a static count it was just another category, redundant with the sum of
the others; as a select-all control it's clearly a control, not a competing status.

## 4. Empty state

If the union of currently-selected statuses matches zero fleet assets — whether
because every tile is off, or because the specific combination that's on happens to
have no members right now — the fleet list is simply left blank. No separate "no
results" message; decided this doesn't need special-casing.

## 4.1. Zero-count tiles are hidden, not shown empty

Added post-ship (2026-07-19, same day): a status tile with a live count of 0 is
omitted from `.summary__row` entirely, rather than rendered as an empty `0` box.
Implemented as a `.filter()` over `SUMMARY_TILES` ahead of the `.map()` in
`renderSummary()` — filtering (not reordering) the fixed array means a bucket that
goes from 0 back to >0 reappears at its **original relative position** among
whichever other tiles are currently visible, not appended wherever it last changed.
The "all" tile follows the same rule with no special-casing (if total count is 0,
every bucket is 0, so the whole row is empty — consistent with §4's fleet-list empty
state).

Row is also left-aligned now (`justify-content: start`, was `center`) — with tiles
routinely appearing/disappearing as counts move to/from zero, a centered row visibly
shifts left-right on every such change; left-aligned stays anchored and matches the
fleet list's own left alignment below it.

## 5. Next steps

- [x] Build: frontend — click handler on `#summary-row` (delegated) in
      `base-station/python/frontend/app.js`, tracking per-status selected/deselected
      state in a `selectedBuckets` Set (default: all selected).
- [x] Build: frontend — `renderFleetList()` filters entries to the union of
      `selectedBuckets` before rendering; filtered-to-zero renders an empty string
      (not the "no assets yet" placeholder, which is reserved for a genuinely empty
      fleet).
- [x] Build: CSS — underline-bar treatment per tile (`style.css`'s `.tile::after` +
      `.is-selected`), layered under the existing static tile styling rather than
      replacing it, so selected/deselected states never touch tile color/opacity.
      All tile's underline driven by the derived "all selected" boolean
      (`REAL_BUCKETS.every(...)`), not its own click state.
- [x] Decide: filter state does **not** persist across refresh (plain in-memory
      `Set`, no localStorage) — consistent with Dev/perf's precedent (§7 of
      [DEV_PERF_PAGE_PLAN.md](DEV_PERF_PAGE_PLAN.md)).
- [ ] Verify live on real hardware (so far only checked with a headless-browser
      smoke test against mock `/nodes` data on this dev machine).
