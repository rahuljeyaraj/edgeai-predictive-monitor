# Plan — LED matrix fleet status message

Status: **Brainstorm/design complete 2026-07-19. Implementation not started.**
This doc captures the outcome of a design discussion for the "LED matrix status
message" item in [DASHBOARD_IDEAS_BACKLOG.md](DASHBOARD_IDEAS_BACKLOG.md) — a short
rolling text summary of fleet health pushed to the base station's own LED matrix.
Nothing here has been built yet; this is the design to build against next.

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
- `W = F = O = 0, H > 0` (everything healthy) → `"H HEALTHY"` (count included, not
  just "ALL GOOD" — doubles as an implicit fleet-size readout).
- Otherwise (anything wrong) → list only the **nonzero** buckets, comma-separated, in
  fixed severity order **fault → warning → offline**, healthy count dropped entirely:
  - All three nonzero: `"F FAULTY, W WARNING, O OFFLINE"`
  - Any subset nonzero: e.g. just `"O OFFLINE"` if that's the only issue.

`OFFLINE` is treated as a peer severity bucket, not folded into fault — a silently
dropped node is a distinct failure mode from an actively-faulting one, and burying it
inside the fault count would hide *why* the count went up. It's listed after warning
in the fixed order per this decision (fault, then warning, then offline).

## 4. Next steps

- [ ] Build: `base-station/python/main.py` — `wire_local_matrix_text(registry)`,
      sibling to `wire_local_status_led`, subscribing to `registry.on_status_change`
      and computing the H/W/F/O counts per §3.
- [ ] Build: format string builder implementing the truth table in §3 (likely a small
      pure function, easy to unit test independent of the Bridge call).
- [ ] Decide (not yet raised): scroll speed / refresh cadence when status changes
      mid-scroll — restart the scroll, or let the current pass finish first?
- [ ] Decide (not yet raised): does this coexist with `display_matrix_test.py`'s
      manual usage, or does production wiring take over the matrix exclusively once
      built?
