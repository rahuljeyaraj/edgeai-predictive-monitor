"""
Generate an emboss-ready SVG wordmark ("EdgeAI" / "PREDICTIVE MONITOR")
matching the dashboard topbar logo (base-station/python/frontend/index.html),
but with all glyphs converted to real vector paths (no font dependency at
print time) and a single solid fill, since 3D-print embossing tools extrude
solid shapes rather than rendering text.

Usage: python3 gen_logo.py [output_dir]
Requires: pip install fonttools
"""
import re
import sys
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen

BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def glyph_path_and_advance(font, glyph_name):
    gs = font.getGlyphSet()
    pen = SVGPathPen(gs)
    gs[glyph_name].draw(pen)
    return pen.getCommands(), gs[glyph_name].width


def layout_line(font_path, text, font_size, letter_spacing, x0, baseline_y):
    """Return list of (path_d, transform) placing each glyph at the right
    cursor position, baseline-aligned, font units flipped to SVG y-down."""
    font = TTFont(font_path)
    upm = font["head"].unitsPerEm
    scale = font_size / upm
    cmap = font.getBestCmap()

    items = []
    cursor = x0
    for ch in text:
        if ch == " ":
            glyph_name = cmap[ord(" ")]
            _, adv = glyph_path_and_advance(font, glyph_name)
            cursor += adv * scale + letter_spacing
            continue
        glyph_name = cmap[ord(ch)]
        d, adv = glyph_path_and_advance(font, glyph_name)
        if d:
            transform = f"translate({cursor:.3f},{baseline_y:.3f}) scale({scale:.6f},{-scale:.6f})"
            items.append((d, transform))
        cursor += adv * scale + letter_spacing
    return items, cursor


def bbox_of_paths(items):
    """Rough bbox by parsing numeric coordinate pairs out of path data and
    applying the accompanying transform (translate + scale only, matches
    what layout_line emits)."""
    minx = miny = float("inf")
    maxx = maxy = float("-inf")
    for d, transform in items:
        m = re.match(r"translate\(([-\d.]+),([-\d.]+)\) scale\(([-\d.]+),([-\d.]+)\)", transform)
        tx, ty, sx, sy = (float(v) for v in m.groups())
        nums = [float(n) for n in re.findall(r"-?\d+\.?\d*(?:e-?\d+)?", d)]
        for i in range(0, len(nums) - 1, 2):
            x, y = nums[i], nums[i + 1]
            px, py = tx + x * sx, ty + y * sy
            minx, maxx = min(minx, px), max(maxx, px)
            miny, maxy = min(miny, py), max(maxy, py)
    return minx, miny, maxx, maxy


def build_svg(pad=2.0, target_width_mm=None, compact=False):
    main_items = []
    items, cursor = layout_line(BOLD, "Edge", 30, 0.3, 0, 34)
    main_items += items
    items, _ = layout_line(BOLD, "AI", 30, 0.3, cursor, 34)
    main_items += items

    if compact:
        # "EdgeAI" only -- for shells too small to emboss the
        # "PREDICTIVE MONITOR" subtitle legibly (see min-cap-height note
        # in README.md).
        all_items = main_items
    else:
        # Bold + larger + tighter letter-spacing than the dashboard's
        # subtitle (which was sized for a screen, not an engraving bit):
        # thicker strokes and taller caps survive a 0.4mm nozzle groove.
        # Baseline moved from 52->54 to keep clear of the main line's
        # descenders ("g") at the bigger size.
        sub_items, _ = layout_line(BOLD, "PREDICTIVE MONITOR", 16, 1.0, 1, 54)
        all_items = main_items + sub_items
    minx, miny, maxx, maxy = bbox_of_paths(all_items)
    minx -= pad; miny -= pad; maxx += pad; maxy += pad
    w, h = maxx - minx, maxy - miny

    if target_width_mm is not None:
        k = target_width_mm / w
        out_w, out_h = w * k, h * k
    else:
        out_w, out_h = w, h

    paths = "\n".join(
        f'    <path d="{d}" transform="{t}"/>' for d, t in all_items
    )

    svg = f'''<?xml version="1.0" encoding="UTF-8"?>
<!-- EdgeAI Predictive Monitor wordmark, emboss-ready: all glyphs are
     outlined vector paths (DejaVu Sans Bold / Regular, close Arial
     substitutes), single solid fill, no text elements and no font
     dependency. Import directly into a slicer's Emboss/SVG tool or a
     CAD tool and extrude. Matches base-station/python/frontend/index.html
     topbar logo layout (2 lines: "EdgeAI" + "PREDICTIVE MONITOR"). -->
<svg xmlns="http://www.w3.org/2000/svg" viewBox="{minx:.3f} {miny:.3f} {w:.3f} {h:.3f}" width="{out_w:.3f}mm" height="{out_h:.3f}mm">
  <g fill="#000000" stroke="none">
{paths}
  </g>
</svg>
'''
    return svg


if __name__ == "__main__":
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "."

    with open(f"{out_dir}/edgeai-logo-emboss-master.svg", "w") as f:
        f.write(build_svg())

    # Base station enclosure is the larger of the two shells; satellite is
    # the small sensor node -- scale the same artwork down for it rather
    # than re-laying it out, so both stay vertex-identical.
    with open(f"{out_dir}/edgeai-logo-emboss-base-station-60mm.svg", "w") as f:
        f.write(build_svg(target_width_mm=60))

    with open(f"{out_dir}/edgeai-logo-emboss-satellite-32mm.svg", "w") as f:
        f.write(build_svg(target_width_mm=32, compact=True))

    print("wrote 3 files to", out_dir)
