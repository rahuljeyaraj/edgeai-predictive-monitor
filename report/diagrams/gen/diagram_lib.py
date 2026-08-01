"""Small helper library for hand-built SVG block diagrams (no graphviz available)."""

FONT = "DejaVu Sans, Helvetica, Arial, sans-serif"

# semantic palette, reused across all diagrams:
#   gray/slate  = sensing
#   blue        = deciding / brain
#   red         = acting (physical)
#   green       = telling a human
COLORS = {
    "sense":   ("#EEF2F7", "#33475B"),
    "brain":   ("#DCEAFB", "#1F5FA8"),
    "act":     ("#FBE3E1", "#B23A2E"),
    "tell":    ("#E8F5EA", "#2E7D46"),
    "warn":    ("#FDF1DA", "#B8790B"),
    "neutral": ("#F5F5F5", "#555555"),
}


class Svg:
    def __init__(self, w, h):
        self.w = w
        self.h = h
        self.parts = []
        self.defs = []
        self._marker()

    def _marker(self):
        self.defs.append(
            '<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
            'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            '<path d="M0,0 L10,5 L0,10 z" fill="#33475B"/></marker>'
        )
        self.defs.append(
            '<marker id="arrowred" viewBox="0 0 10 10" refX="9" refY="5" '
            'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            '<path d="M0,0 L10,5 L0,10 z" fill="#B23A2E"/></marker>'
        )

    def box(self, x, y, w, h, title, subtitle=None, kind="neutral", rx=10, title_size=15, sub_size=11.5):
        fill, stroke = COLORS[kind]
        self.parts.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.6"/>'
        )
        cx = x + w / 2
        if subtitle:
            lines = subtitle if isinstance(subtitle, list) else [subtitle]
            title_y = y + h / 2 - (6 * len(lines)) - 2
            self.text(cx, title_y, title, size=title_size, weight="bold", anchor="middle", fill=stroke)
            start = title_y + title_size * 0.9 + 4
            for i, line in enumerate(lines):
                self.text(cx, start + i * (sub_size + 5), line, size=sub_size, anchor="middle", fill="#222")
        else:
            self.text(cx, y + h / 2 + title_size * 0.32, title, size=title_size, weight="bold", anchor="middle", fill=stroke)
        return (x, y, w, h)

    def text(self, x, y, s, size=12, anchor="start", weight="normal", fill="#1a1a1a", style="normal", family=None):
        fam = family or FONT
        self.parts.append(
            f'<text x="{x}" y="{y}" font-family="{fam}" font-size="{size}" '
            f'font-weight="{weight}" font-style="{style}" text-anchor="{anchor}" fill="{fill}">{esc(s)}</text>'
        )

    def label_bg(self, x, y, s, size=11):
        w = 6.3 * len(s) * (size / 11)
        self.parts.append(
            f'<rect x="{x - w/2}" y="{y - size}" width="{w}" height="{size + 4}" fill="#FFFFFF" opacity="0.92"/>'
        )

    def arrow(self, x1, y1, x2, y2, label=None, color="#33475B", dashed=False, width=1.8, label_pos=0.5, curve=None):
        marker = "arrow" if color == "#33475B" else "arrowred"
        dash = ' stroke-dasharray="6,4"' if dashed else ""
        if curve:
            path = f'M{x1},{y1} Q{curve[0]},{curve[1]} {x2},{y2}'
            self.parts.append(
                f'<path d="{path}" fill="none" stroke="{color}" stroke-width="{width}"{dash} marker-end="url(#{marker})"/>'
            )
            lx = (1 - label_pos) ** 2 * x1 + 2 * (1 - label_pos) * label_pos * curve[0] + label_pos ** 2 * x2
            ly = (1 - label_pos) ** 2 * y1 + 2 * (1 - label_pos) * label_pos * curve[1] + label_pos ** 2 * y2
        else:
            self.parts.append(
                f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{width}"{dash} '
                f'marker-end="url(#{marker})"/>'
            )
            lx = x1 + (x2 - x1) * label_pos
            ly = y1 + (y2 - y1) * label_pos
        if label:
            self.label_bg(lx, ly, label)
            self.text(lx, ly, label, size=11, anchor="middle", fill="#222")

    def group_box(self, x, y, w, h, title, kind="neutral"):
        fill, stroke = COLORS[kind]
        self.parts.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" '
            f'fill="{fill}" fill-opacity="0.35" stroke="{stroke}" stroke-width="1.6" stroke-dasharray="7,5"/>'
        )
        self.text(x + 14, y + 22, title, size=13, weight="bold", fill=stroke)

    def render(self, dark_ok=False):
        defs = "".join(self.defs)
        body = "".join(self.parts)
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
            f'viewBox="0 0 {self.w} {self.h}">'
            f'<defs>{defs}</defs>'
            f'<rect x="0" y="0" width="{self.w}" height="{self.h}" fill="#FFFFFF"/>'
            f'{body}</svg>'
        )


def esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def save(svg: Svg, path_svg, path_png=None, scale=2):
    with open(path_svg, "w") as f:
        f.write(svg.render())
    if path_png:
        import cairosvg
        cairosvg.svg2png(url=path_svg, write_to=path_png, scale=scale)
