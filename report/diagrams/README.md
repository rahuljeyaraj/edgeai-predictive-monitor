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
| `gen/d15_system_overview.py` | `15-system-overview.png` | S5 (code structure) |
| `gen/d16_mpu_pipeline_detail.py` | `16-mpu-pipeline-detail.png` | S5 (code structure) |

`d15` and `d16` are the two code-structure figures, and they are the only
scripts that override `save()`'s default `scale=2.2` — they pass `scale=3.2`
(~307 dpi) because they are embedded in the Word report, which asked for
300 dpi. Keep that argument if you regenerate them.

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
