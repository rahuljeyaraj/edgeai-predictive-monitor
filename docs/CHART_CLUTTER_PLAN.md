# Plan — Chart clutter

Status: **Brainstorm/design complete 2026-07-19. Implementation not started.**
This doc captures the outcome of a design discussion for the "Chart clutter" item in
[DASHBOARD_IDEAS_BACKLOG.md](DASHBOARD_IDEAS_BACKLOG.md) — 3-axis accel + mic ×
(waterfall/spectrum/time-domain) could be 12+ graphs on one node. Nothing here has
been built yet; this is the design to build against next.

---

## 0. Why this exists

A node's sensor set is variable: 0/1/3 accelerometer axes, 0/1 mic, time-domain of
both, plus 0–5 scalar values. Naively giving every channel × domain its own chart
balloons past 12 plots per node — unreadable and slow to render. Two different
audiences need different things from this same data:

- **Demo (judged)**: narrate frequency range + detection precision — the spectrum
  chart is the star, anomaly score is the health headline.
- **Showcase (booth walk-by)**: the waterfall is a wow-factor extra, not meant to be
  read precisely.

That split drove almost every decision below: what's default-visible vs. opt-in.

## 1. Per-node layout (top to bottom)

1. **Hero: anomaly score** — single number/gauge, status color. Always visible.
2. **Scalar tiles (0–5 values)** — stat tiles, not charts. Always visible when
   present.
3. **Accel spectrum** — hero chart. 1–3 axes overlaid in one chart when accel is
   present; the whole section is omitted (not an empty chart) when accel is absent.
4. **Mic spectrum** — its own chart, separate from accel (§2). Omitted when mic is
   absent.
5. **Collapsible "Raw signals"** — accel time-domain (axes overlaid, same color
   mapping as the spectrum chart) + mic time-domain (separate chart). Collapsed by
   default; not rendered/computed until expanded.
6. **Collapsible "Waterfall"** — same collapsible tier as raw signals, not a
   separate page. 2D/3D toggle inside (§4). Underlying data collection is unchanged
   from the current implementation — this is a rendering/placement change only.

## 2. Accel and mic never share a chart

Different units (acceleration vs. sound pressure/amplitude) and mic's frequency
range runs much higher than accel's. Combining them onto one x-scale forces one of
the two bands to be squashed — the same failure mode as a dual-axis chart, just
applied to frequency instead of value. So: separate spectrum chart per sensor type,
same treatment for time-domain.

## 3. Conditional rendering, not empty states

0 accel axes or 0 mic → that section doesn't render at all. An axis with no data
reads as "broken," not "sensor absent." If the fleet-grid layout needs visual
stability across differently-equipped nodes, a one-line "No accelerometer" text
placeholder is acceptable — never an empty chart shell.

## 4. Waterfall rendering — current implementation reads as a grid, not organic

Decided: a 2D/3D toggle inside the collapsible waterfall panel.

- **2D (default)**: smooth spectrogram — interpolate/blur between time/frequency
  bins so color transitions are continuous, no visible cell boundaries. Current
  implementation is a blocky heatmap; this fixes that while staying in the
  precision-readable top-down view.
- **3D**: classic SDR/oscilloscope waterfall — successive spectrum traces as
  receding line curves, older traces fading. A different renderer, not a heatmap
  variant. This is the wow-factor mode.
- Toggle lives inside the same collapsible panel — no separate page/route (matches
  this frontend's no-router constraint, same primitive as the Dev/perf page's
  tiers, see [DEV_PERF_PAGE_PLAN.md](DEV_PERF_PAGE_PLAN.md)).
- Data collection/buffering is explicitly unchanged: client-side ring buffer per
  `charts.js`'s `node.waterfall[channel]` (see DEV_PERF_PAGE_PLAN.md §7). This doc
  only changes rendering + placement, not the pipeline.

## 5. Color consistency

Accel-X/Y/Z and mic use the same categorical color across every chart that includes
them (spectrum, time-domain) so a viewer tracks "accel-Z" without relearning the
legend per chart. Fixed hue order, never reassigned based on which axes happen to
be present on a given node.

## 6. Related but separate: GPU-accelerated rendering

[DEV_PERF_PAGE_PLAN.md](DEV_PERF_PAGE_PLAN.md) §5a already scoped a
`scattergl`/`heatmapgl` swap in `charts.js` to move redraw cost off the browser's
main JS thread — a performance fix for keeping more panels expanded at once,
orthogonal to the layout/hierarchy decisions in this doc. Applies to whichever
charts land here, including the new 2D spectrogram mode.

## 7. Next steps

- [ ] Build: frontend layout per §1 — hero, scalar tiles, spectrum chart(s),
      collapsible raw-signals, collapsible waterfall.
- [ ] Build: accel/mic spectrum + time-domain rendering with shared per-axis color
      mapping (§5).
- [ ] Build: 2D smooth-spectrogram renderer (replaces current blocky heatmap).
- [ ] Build: 3D stacked-line waterfall renderer + 2D/3D toggle.
- [ ] Apply DEV_PERF_PAGE_PLAN.md §5a's scattergl/heatmapgl swap to whichever
      charts land here.
