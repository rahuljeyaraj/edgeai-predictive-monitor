# Enclosure logo (emboss-ready)

Vector wordmark for embossing/engraving on the 3D-printed base station and
satellite shells. Matches the dashboard topbar logo
([base-station/python/frontend/index.html](../../base-station/python/frontend/index.html))
but with every glyph converted to a real path (no font dependency at
print/slice time) and single solid black fill, since embossing tools
extrude solid shapes, not text.

## Files

| File | Use | Notes |
|---|---|---|
| `edgeai-logo-emboss-base-station-50mm.svg` | Base station shell | Full "EdgeAI / PREDICTIVE MONITOR" lockup, 50mm wide |
| `edgeai-logo-emboss-satellite-32mm.svg` | Satellite shell | "EdgeAI" only, 32mm wide — the subtitle text was dropped here because it engraves too small to read (see below) |
| `edgeai-logo-emboss-master.svg` | Source / rescaling | Full lockup at natural design scale (1 unit = 1mm) |

All three are vector — open any of them in your slicer/CAD tool and scale
to whatever actually fits your shell face; the mm sizes above are just
starting points, not a size you're locked into.

## Why the satellite mark drops the subtitle

At 32mm total width, "PREDICTIVE MONITOR" prints at ~1.8mm cap height —
below the ~3mm cap height needed for a 0.4mm-nozzle FDM print to hold
individual letterforms (`M`/`O`/`N` strokes merge into blobs below that).
The base station's 50mm version keeps the subtitle at ~2.8mm, which is
workable but still tight — a finer nozzle (0.2mm) or a slightly wider
panel will emboss more cleanly.

## Using with a slicer (PrusaSlicer / OrcaSlicer / Bambu Studio)

1. Load your shell STL, then use the slicer's "Emboss"/SVG tool and import
   the relevant file above.
2. **Engrave (recessed), not raised — depth 0.3–0.4mm.** At these sizes
   (subtitle caps ~1.8–2.8mm) a raised stroke is thin enough to snap off
   in handling, and recessed text needs no support/overhang. Optionally
   paint-fill the recess afterward (rub paint or a marker in, wipe the
   surface clean) for contrast.
3. Position on the face, reduce size only if needed — don't scale below
   the mm width used to generate the file or the strokes will thin
   further.

## Regenerating / editing the text

`gen_logo.py` builds all three files from DejaVu Sans Bold/Regular (close
metrical substitutes for the dashboard's Arial). Edit the strings in
`build_svg()` and rerun:

```
pip install fonttools
python3 gen_logo.py .
```
