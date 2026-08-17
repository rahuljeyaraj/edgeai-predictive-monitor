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
| `edgeai-logo-emboss-base-station-60mm.svg` | Base station shell | Full "EdgeAI / PREDICTIVE MONITOR" lockup, 60mm wide |
| `edgeai-logo-emboss-satellite-32mm.svg` | Satellite shell | "EdgeAI" only, 32mm wide — the subtitle text was dropped here because it engraves too small to read (see below) |
| `edgeai-logo-emboss-master.svg` | Source / rescaling | Full lockup at natural design scale (1 unit = 1mm) |

All three are vector — open any of them in your slicer/CAD tool and scale
to whatever actually fits your shell face; the mm sizes above are just
starting points, not a size you're locked into.

The "PREDICTIVE MONITOR" subtitle is set in **bold**, not the dashboard's
regular weight, and larger relative to "EdgeAI" than the on-screen version
— the dashboard sizing was tuned for a screen, not a 0.4mm engraving
groove, and thin regular-weight strokes at that scale don't hold.

## Why the satellite mark drops the subtitle

At 32mm total width even the bold subtitle would print under 3mm cap
height — too small for a 0.4mm-nozzle FDM print to hold individual
letterforms (`M`/`O`/`N` strokes merge into blobs below that). The base
station's 60mm version keeps the subtitle at ~4mm cap height, comfortably
legible.

## Using with a slicer (PrusaSlicer / OrcaSlicer / Bambu Studio)

1. Load your shell STL, then use the slicer's "Emboss"/SVG tool and import
   the relevant file above.
2. **Engrave (recessed), not raised — depth 0.3–0.4mm.** A raised stroke
   this thin can snap off in handling, and recessed text needs no
   support/overhang. Optionally paint-fill the recess afterward (rub
   paint or a marker in, wipe the surface clean) for contrast.
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
