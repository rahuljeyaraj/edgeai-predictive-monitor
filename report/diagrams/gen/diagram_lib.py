"""Hand-built SVG block diagrams for REPORT.md.

No graphviz/mermaid available on this machine, so diagrams are composed in
plain Python and rasterized with cairosvg. This module is the shared drawing
layer; one script per diagram lives alongside it in gen/.

Design rules this library enforces (rather than leaving to each script):

  * A diagram is a *framed* object -- title band on top, optional footnote
    band at the bottom, fixed margins. Content is laid out inside
    `Canvas.body`, so a footnote can never overlap the drawing (the single
    worst defect in the first-generation diagrams: caption text sitting on
    top of an arrowhead).
  * Text width is estimated (`text_width`) so legend chips, edge labels and
    label backplates are sized to their content instead of to a guess.
  * Edges route orthogonally by default (`elbow`). Diagonal lines through a
    block diagram read as sketchy and are the other thing that made the
    first generation collide with its own labels.
  * One semantic palette, used identically in every diagram, so the reader
    learns the colour code once: slate = senses, blue = decides,
    red = acts physically, green = tells a human, amber = degraded/warning.
"""

FONT = "DejaVu Sans, Helvetica, Arial, sans-serif"
MONO = "DejaVu Sans Mono, Menlo, Consolas, monospace"

INK = "#16202B"          # primary text
INK_SOFT = "#4A5A6A"     # secondary text
HAIRLINE = "#D6DEE6"
PAPER = "#FFFFFF"
BAND = "#F4F7FA"         # title/footnote band fill

# (fill, stroke, text) per semantic role. Fills are deliberately pale so the
# same diagram prints legibly in greyscale; strokes carry the identity.
ROLES = {
    "sense":   ("#EDF2F7", "#3D5266", "#2A3B4C"),
    "brain":   ("#DCEAFB", "#1B5FA8", "#154A85"),
    "act":     ("#FBE1DE", "#B03225", "#8C271D"),
    "tell":    ("#E4F3E8", "#237A41", "#1A5C31"),
    "warn":    ("#FDF0D6", "#A97105", "#845804"),
    "neutral": ("#F4F6F8", "#5B6B7A", "#3D4B58"),
    "ghost":   ("#FBFCFD", "#AAB6C2", "#6B7887"),
}

ROLE_LEGEND = {
    "sense": "Senses",
    "brain": "Decides",
    "act": "Acts (physical)",
    "tell": "Tells a human",
    "warn": "Degraded",
}


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def text_width(s, size=12, weight="normal", family=FONT):
    """Approximate rendered width. DejaVu Sans averages ~0.55em per glyph at
    regular weight; bold and monospace run wider. Only needs to be good
    enough to size a backplate or a legend chip, never exact."""
    per = 0.545
    if weight in ("bold", "600", "700"):
        per = 0.600
    if family == MONO:
        per = 0.602
    narrow = sum(1 for c in str(s) if c in "iljtfrI.,:;'|! ")
    wide = sum(1 for c in str(s) if c in "MWmw@")
    return (len(str(s)) * per - narrow * 0.20 + wide * 0.14) * size


class Canvas:
    """A framed diagram. Scripts draw into `body` coordinates (absolute --
    the frame just reserves space, it doesn't translate)."""

    def __init__(self, width, height, title=None, subtitle=None,
                 footnotes=(), margin=34, legend=None):
        self.w = width
        self.h = height
        self.parts = []
        self.defs = []
        self.margin = margin
        self.title = title
        self.subtitle = subtitle
        self.footnotes = list(footnotes)
        self.legend = legend
        self._defs()

        # Vertical budget: title band, then body, then footnote band.
        self.top = margin
        if title:
            self.top += 30
            if subtitle:
                self.top += 20
            self.top += 14
        if legend:
            self.top += 30

        self.bottom = height - margin
        if self.footnotes:
            self.bottom -= 16 + 21 * len(self.footnotes)

        self.left = margin
        self.right = width - margin

    # ---------------------------------------------------------------- defs

    def _defs(self):
        for name, colour in (("arrow", "#3D5266"), ("arrowAct", "#B03225"),
                             ("arrowSoft", "#8C9AA8"), ("arrowTell", "#237A41")):
            self.defs.append(
                f'<marker id="{name}" viewBox="0 0 10 10" refX="8.5" refY="5" '
                f'markerWidth="6.5" markerHeight="6.5" orient="auto-start-reverse">'
                f'<path d="M0,0.6 L9.4,5 L0,9.4 z" fill="{colour}"/></marker>'
            )
        self.defs.append(
            '<filter id="softshadow" x="-12%" y="-12%" width="130%" height="140%">'
            '<feDropShadow dx="0" dy="1.4" stdDeviation="1.6" '
            'flood-color="#16202B" flood-opacity="0.13"/></filter>'
        )

    # -------------------------------------------------------------- atoms

    def raw(self, markup):
        self.parts.append(markup)

    def text(self, x, y, s, size=12, anchor="start", weight="normal",
             fill=INK, style="normal", family=None, opacity=None):
        fam = family or FONT
        op = f' opacity="{opacity}"' if opacity is not None else ""
        self.parts.append(
            f'<text x="{x:.1f}" y="{y:.1f}" font-family="{fam}" font-size="{size}" '
            f'font-weight="{weight}" font-style="{style}" text-anchor="{anchor}" '
            f'fill="{fill}"{op}>{esc(s)}</text>'
        )

    def lines(self, x, y, rows, size=11.5, anchor="middle", fill=INK_SOFT,
              leading=None, family=None):
        step = leading or (size + 4.5)
        for i, row in enumerate(rows):
            self.text(x, y + i * step, row, size=size, anchor=anchor,
                      fill=fill, family=family)
        return y + (len(rows) - 1) * step

    def rule(self, x1, y, x2, colour=HAIRLINE, width=1):
        self.parts.append(
            f'<line x1="{x1:.1f}" y1="{y:.1f}" x2="{x2:.1f}" y2="{y:.1f}" '
            f'stroke="{colour}" stroke-width="{width}"/>'
        )

    # --------------------------------------------------------------- boxes

    def box(self, x, y, w, h, title, body=(), role="neutral", rx=9,
            title_size=15, body_size=11.5, shadow=True, dashed=False,
            title_family=None, badge=None):
        """A titled block. Returns a Node with anchor points."""
        fill, stroke, tcol = ROLES[role]
        dash = ' stroke-dasharray="6,4"' if dashed else ""
        sh = ' filter="url(#softshadow)"' if shadow else ""
        self.parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.5"{dash}{sh}/>'
        )
        cx = x + w / 2
        rows = list(body)
        if rows:
            block_h = title_size + 5 + len(rows) * (body_size + 4.5)
            ty = y + (h - block_h) / 2 + title_size * 0.82
            self.text(cx, ty, title, size=title_size, weight="bold",
                      anchor="middle", fill=tcol, family=title_family)
            self.lines(cx, ty + title_size * 0.42 + 13, rows, size=body_size,
                       anchor="middle", fill=INK_SOFT)
        else:
            self.text(cx, y + h / 2 + title_size * 0.34, title, size=title_size,
                      weight="bold", anchor="middle", fill=tcol,
                      family=title_family)
        if badge:
            bw = text_width(badge, 10, "bold") + 14
            self.parts.append(
                f'<rect x="{x + w - bw - 8:.1f}" y="{y + 7:.1f}" width="{bw:.1f}" '
                f'height="17" rx="8.5" fill="{stroke}" opacity="0.92"/>'
            )
            self.text(x + w - bw / 2 - 8, y + 19.3, badge, size=10, weight="bold",
                      anchor="middle", fill="#FFFFFF")
        return Node(x, y, w, h, stroke)

    def chip(self, x, y, label, role="neutral", size=11, pad=11, h=23):
        """A small pill -- used for wires, pin names, statuses."""
        fill, stroke, tcol = ROLES[role]
        w = text_width(label, size, "bold") + pad * 2
        self.parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h}" rx="{h/2:.1f}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.2"/>'
        )
        self.text(x + w / 2, y + h / 2 + size * 0.35, label, size=size,
                  weight="bold", anchor="middle", fill=tcol)
        return Node(x, y, w, h, stroke)

    def group(self, x, y, w, h, title, role="neutral", rx=13):
        fill, stroke, tcol = ROLES[role]
        self.parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}" '
            f'fill="{fill}" fill-opacity="0.30" stroke="{stroke}" stroke-width="1.4" '
            f'stroke-dasharray="8,5"/>'
        )
        # Generous padding: the label sits on the group's dashed border, so an
        # under-measured chip lets the border cut straight through the text.
        lw = text_width(title, 12, "bold") * 1.10 + 26
        self.parts.append(
            f'<rect x="{x + 15:.1f}" y="{y - 11:.1f}" width="{lw:.1f}" height="22" '
            f'rx="6" fill="{PAPER}" stroke="{stroke}" stroke-width="1.2"/>'
        )
        self.text(x + 15 + lw / 2, y + 4, title, size=12, weight="bold",
                  anchor="middle", fill=tcol)
        return Node(x, y, w, h, stroke)

    # --------------------------------------------------------------- edges

    ARROW_COLOURS = {
        "arrow": "#3D5266", "arrowAct": "#B03225",
        "arrowSoft": "#8C9AA8", "arrowTell": "#237A41",
    }

    def _edge_label(self, x, y, label, size=11, colour=INK, align="middle"):
        w = text_width(label, size) + 12
        h = size + 9
        lx = x - w / 2 if align == "middle" else x
        self.parts.append(
            f'<rect x="{lx:.1f}" y="{y - h / 2:.1f}" width="{w:.1f}" height="{h:.1f}" '
            f'rx="4" fill="{PAPER}" opacity="0.94"/>'
        )
        self.text(x if align == "middle" else x + 6, y + size * 0.35, label,
                  size=size, anchor=align if align == "middle" else "start",
                  fill=colour)

    def link(self, pts, label=None, kind="arrow", width=1.7, dashed=False,
             label_at=0.5, label_dx=0, label_dy=0, both=False, label_size=11):
        """Polyline edge through `pts` (list of (x, y)), arrowhead at the end.
        Points are taken literally, so callers control routing exactly."""
        colour = self.ARROW_COLOURS[kind]
        dash = ' stroke-dasharray="6,4"' if dashed else ""
        d = f'M{pts[0][0]:.1f},{pts[0][1]:.1f}' + "".join(
            f' L{x:.1f},{y:.1f}' for x, y in pts[1:])
        start = f' marker-start="url(#{kind})"' if both else ""
        self.parts.append(
            f'<path d="{d}" fill="none" stroke="{colour}" stroke-width="{width}" '
            f'stroke-linejoin="round" stroke-linecap="round"{dash}'
            f'{start} marker-end="url(#{kind})"/>'
        )
        if label:
            # Place the label on the longest segment, at `label_at` along it,
            # so it never lands on a corner or an arrowhead.
            best, best_len = 0, -1
            for i in range(len(pts) - 1):
                seg = abs(pts[i + 1][0] - pts[i][0]) + abs(pts[i + 1][1] - pts[i][1])
                if seg > best_len:
                    best, best_len = i, seg
            (x1, y1), (x2, y2) = pts[best], pts[best + 1]
            lx = x1 + (x2 - x1) * label_at + label_dx
            ly = y1 + (y2 - y1) * label_at + label_dy
            self._edge_label(lx, ly, label, size=label_size,
                             colour=colour if kind == "arrowAct" else INK)

    def elbow(self, a, b, label=None, kind="arrow", frac=0.5, gap=0, **kw):
        """Orthogonal edge between two anchor points. `frac` is where the
        vertical/horizontal jog happens along the dominant axis."""
        (x1, y1), (x2, y2) = a, b
        if abs(x2 - x1) >= abs(y2 - y1):
            mx = x1 + (x2 - x1) * frac
            pts = [(x1, y1), (mx, y1), (mx, y2), (x2, y2)] if y1 != y2 else [(x1, y1), (x2, y2)]
        else:
            my = y1 + (y2 - y1) * frac
            pts = [(x1, y1), (x1, my), (x2, my), (x2, y2)] if x1 != x2 else [(x1, y1), (x2, y2)]
        self.link(pts, label=label, kind=kind, **kw)

    # -------------------------------------------------------------- legend

    def _draw_legend(self, y):
        items = self.legend
        gap = 26
        widths = [18 + 7 + text_width(lbl, 11.5) for _, lbl in items]
        total = sum(widths) + gap * (len(items) - 1)
        x = self.left
        if total < self.right - self.left:
            x = self.left
        for (role, lbl), w in zip(items, widths):
            fill, stroke, _ = ROLES[role]
            self.parts.append(
                f'<rect x="{x:.1f}" y="{y - 10:.1f}" width="15" height="14" rx="3.5" '
                f'fill="{fill}" stroke="{stroke}" stroke-width="1.3"/>'
            )
            self.text(x + 22, y + 1.5, lbl, size=11.5, fill=INK_SOFT)
            x += w + gap

    # -------------------------------------------------------------- render

    def render(self):
        head = []
        y = self.margin
        if self.title:
            y += 22
            head.append((self.left, y, self.title, 19, "bold", INK))
            if self.subtitle:
                y += 20
                head.append((self.left, y, self.subtitle, 12.5, "normal", INK_SOFT))
            y += 14
            self.rule(self.left, y, self.right)
        if self.legend:
            self._draw_legend(y + 20)

        for x, ty, s, size, weight, fill in head:
            self.text(x, ty, s, size=size, weight=weight, fill=fill)

        if self.footnotes:
            fy = self.h - self.margin - 21 * (len(self.footnotes) - 1) - 2
            self.rule(self.left, fy - 24, self.right)
            for i, (note, colour) in enumerate(self.footnotes):
                self.text(self.left, fy + i * 21, note, size=12.5,
                          fill=colour or INK_SOFT)

        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" '
            f'height="{self.h}" viewBox="0 0 {self.w} {self.h}">'
            f'<defs>{"".join(self.defs)}</defs>'
            f'<rect x="0" y="0" width="{self.w}" height="{self.h}" fill="{PAPER}"/>'
            f'{"".join(self.parts)}</svg>'
        )


class Node:
    """Anchor points on a drawn rectangle."""

    __slots__ = ("x", "y", "w", "h", "stroke")

    def __init__(self, x, y, w, h, stroke):
        self.x, self.y, self.w, self.h, self.stroke = x, y, w, h, stroke

    @property
    def cx(self):
        return self.x + self.w / 2

    @property
    def cy(self):
        return self.y + self.h / 2

    @property
    def left(self):
        return (self.x, self.cy)

    @property
    def right(self):
        return (self.x + self.w, self.cy)

    @property
    def top(self):
        return (self.cx, self.y)

    @property
    def bottom(self):
        return (self.cx, self.y + self.h)

    def l(self, t=0.5):
        return (self.x, self.y + self.h * t)

    def r(self, t=0.5):
        return (self.x + self.w, self.y + self.h * t)

    def t(self, f=0.5):
        return (self.x + self.w * f, self.y)

    def b(self, f=0.5):
        return (self.x + self.w * f, self.y + self.h)


def save(canvas, basename, scale=2.2):
    """Writes <basename>.svg and <basename>.png into the diagrams folder."""
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.abspath(os.path.join(here, ".."))
    svg_path = os.path.join(out, basename + ".svg")
    png_path = os.path.join(out, basename + ".png")
    with open(svg_path, "w") as f:
        f.write(canvas.render())
    import cairosvg
    cairosvg.svg2png(url=svg_path, write_to=png_path, scale=scale)
    print(f"wrote {basename}.svg + .png")
