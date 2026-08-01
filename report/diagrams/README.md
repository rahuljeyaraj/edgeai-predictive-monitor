# Report diagrams

Hand-built SVG block diagrams for `REPORT.md` (no graphviz/mermaid — generated
with plain Python + [cairosvg](https://cairosvg.org/), via `gen/diagram_lib.py`).

## One-time setup

```sh
cd report/diagrams
python3 -m venv .venv
.venv/bin/pip install cairosvg
```

## Regenerate a diagram

Edit the matching script in `gen/`, then rerun it from inside `gen/`:

```sh
cd report/diagrams/gen
../.venv/bin/python d5_full_architecture.py
```

Each script writes both the `.svg` and the `.png` next to this README (same
basename as the script's diagram number). REPORT.md embeds the `.png` files
(Pandoc's PDF pipeline needs PNG, not raw SVG — see PLANNING.md §6).

| Script | Output | Used in |
|---|---|---|
| `gen/d1_overview.py` | `01-system-at-a-glance.png` | Ch.1 |
| `gen/d2_basestation_wiring.py` | `02-base-station-wiring.png` | Ch.2 |
| `gen/d3_satellite_wiring.py` | `03-satellite-node-wiring.png` | Ch.3 |
| `gen/d4_feature_pipeline.py` | `04-feature-pipeline.png` | Ch.4 |
| `gen/d5_full_architecture.py` | `05-full-architecture.png` | Ch.7 |

## Editing in a UI instead

Open a `.svg` directly in Inkscape or Figma and edit it there. If you do,
export/save a matching `.png` (same filename) alongside it by hand — and
don't rerun the Python script afterward, it will silently overwrite the
manual edit.
