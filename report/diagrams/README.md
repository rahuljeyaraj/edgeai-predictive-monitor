# Report diagrams

Block diagrams for `REPORT.md`. No graphviz or mermaid on this machine, so
they're composed in plain Python and rasterised with
[cairosvg](https://cairosvg.org/), via `gen/diagram_lib.py`.

## One-time setup

```sh
cd report/diagrams
python3 -m venv .venv
.venv/bin/pip install cairosvg
```

## Regenerate

Edit the matching script in `gen/`, then run it from inside `gen/`:

```sh
cd report/diagrams/gen
../.venv/bin/python d5_full_architecture.py
```

Each script writes both the `.svg` and the `.png` one level up. `REPORT.md`
embeds the PNGs — Pandoc's PDF pipeline needs raster, not raw SVG.

Regenerate everything:

```sh
cd report/diagrams/gen
for f in d*.py; do ../.venv/bin/python "$f"; done
```

| Script | Output | Used in |
|---|---|---|
| `gen/d1_overview.py` | `01-system-at-a-glance.png` | Ch. 1 |
| `gen/d2_basestation_wiring.py` | `02-base-station-wiring.png` | Ch. 3 |
| `gen/d3_satellite_wiring.py` | `03-satellite-node-wiring.png` | Ch. 4 |
| `gen/d4_feature_pipeline.py` | `04-feature-pipeline.png` | Ch. 5 |
| `gen/d5_full_architecture.py` | `05-full-architecture.png` | Ch. 9 |
| `gen/d6_asset_lifecycle.py` | `06-asset-lifecycle.png` | Ch. 8 |
| `gen/d7_trip_sequence.py` | `07-trip-sequence.png` | Ch. 7 |
| `gen/d8_dashboard_anatomy.py` | `08-dashboard-anatomy.png` | Ch. 9 |
| `gen/d9_onboarding.py` | `09-onboarding.png` | Ch. 4 |
| `gen/d10_setup_flow.py` | `10-setup-flow.png` | Ch. 5 |
| `gen/d11_edge_impulse_flow.py` | `11-edge-impulse-flow.png` | Ch. 7 |
| `gen/d12_software_architecture.py` | `12-software-architecture.png` | Ch. 10 |
| `gen/d13_tab_map.py` | `13-dashboard-tabs.png` | Ch. 9 |
| `gen/d14_two_brains.py` | `14-two-brains.png` | Ch. 2 |
| `gen/d15_code_structure.py` | `15a`…`15f` (six figures) | S5 (code structure) |
| `gen/d16_system_overview.py` | `16-system-overview.png` | Hackster article, sec. 2.2 |

`gen/d15_code_structure.py` is the exception to one-script-one-diagram: it
emits six figures (`15a-tiers`, `15b-wire-format`, `15c-linux-packages`,
`15d-frame-journey`, `15e-trip-path`, `15f-setup-steps`) so they share one
set of sizing constants. It is also the only script that overrides `save()`'s
default `scale=2.2`, passing `scale=3.2` (~307 dpi).

Those figures are sized for **Word**, not for this repo's PDF, and that drives
three rules the other scripts do not follow:

* **Canvas width stays near 900 px.** Legibility on paper is set by the ratio
  of font size to canvas width, not by pixel count. At 900 px wide, 13 pt body
  text lands at ~8.5 pt when the figure is placed at the page's full ~6.5 in
  text width. The first draft of these was 1860 px wide and the same text came
  out under 4 pt.
* **Height stays under ~1.25x the width**, so a figure can sit at full width
  without running past the bottom of the page. Being shrunk to fit is what
  destroys the font size, so a tall figure defeats the rule above.
* **Split rather than cram.** Anything that needed a long edge, a crossing, or
  a three-bend detour became its own figure instead. Every edge in the six is
  straight or has one shared riser.
* **Structure, not call traces.** `15c` and `15d` replaced a pair of figures
  that drew call sites and line numbers: accurate, but unreadable to anyone
  not already holding the source. `15c` answers "where does the code live",
  `15d` answers "what runs when", and both name modules rather than lines.

Note the chapter column moved when the report gained a dedicated Edge Impulse
chapter — `06-asset-lifecycle` and `08-dashboard-anatomy` are both Ch. 9 now,
and `07-trip-sequence` is Ch. 8.

## What `diagram_lib.py` enforces

The library exists so individual scripts can't reintroduce the defects the first
generation of these diagrams had:

* **Framed layout.** A `Canvas` reserves a title band and a footnote band, and
  content is laid out between them — so a caption can never end up sitting on
  top of an arrowhead.
* **Measured text.** `text_width()` estimates rendered width, so legend chips,
  edge labels and group labels are sized to their content. Under-measuring is
  what let a group's dashed border cut straight through its own title.
* **Labels beside lines, not on them.** `link()` hangs an edge label above a
  horizontal segment or alongside a vertical one, so the line and its
  arrowhead stay visible — a backplate centred on a short segment used to
  swallow the arrow whole. `label_side` and `label_seg` override the side and
  the segment when the default lands on something.
* **Orthogonal routing.** `link()` takes explicit points and `elbow()` jogs on
  one axis. Diagonals through a block diagram read as sketchy, and were the main
  source of label collisions.
* **A bend has to mean something.** This one the scripts have to keep, because
  `link()` takes the points it's given. Two boxes joined by one edge sit on the
  *same* centre line, so the edge is straight: a jog only because a box was
  placed at a round number reads as a detour the data actually takes, and a page
  of them reads as a staircase. When one box fans out to several — which cannot
  all be level with it — the branches leave from one shared point and climb one
  shared riser, so it reads as one fan (see `d5`, `d4`, `d7`). Draw an
  odd-coloured branch first, so the neutral ones overdraw the shared trunk and
  it doesn't pick up their colour.
* **One palette, one meaning.** `ROLES`: slate = senses, blue = decides,
  red = acts physically, green = tells a human, amber = degraded, ghost = derived
  or not-a-decision. The reader learns the colour code once and it holds across
  all nine diagrams.

## Editing in a UI instead

Open a `.svg` in Inkscape or Figma and edit it there. If you do, export a
matching `.png` by hand — and don't rerun the Python script afterwards, it will
silently overwrite the manual edit.

## The `*-schematic-kicad.png` files

`02b-base-station-schematic-kicad.png`, `03b-satellite-node-schematic-kicad.png`
and `06-motor-driver-rig-schematic-kicad.png` are a different animal: real KiCad
schematics — actual `.kicad_sch` files with symbols and nets, openable in KiCad —
not hand-drawn blocks. Source lives in `hardware/kicad/`, not in this folder's
`gen/`. They're copied here as PNGs so `REPORT.md`'s image paths stay relative to
one folder. Regenerate from `hardware/kicad/` and recopy; don't edit these PNGs.
