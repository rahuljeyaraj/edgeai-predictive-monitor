#!/usr/bin/env python3
"""Generate KiCad 9 schematic files for the EdgeAI Predictive Monitor wiring
diagrams (base station, satellite node, motor-driver rig) from the pinout
tables in report/REPORT.md.

Style: each physical part is a hand-drawn box symbol with datasheet-style
pin names. Connectivity is expressed with short pin stubs + net labels
(label text = net name), not long point-to-point wires -- this keeps the
generated layout readable regardless of part placement.
"""
import uuid, textwrap, os

SPACING = 7.62
PIN_LEN = 2.54
STUB_LEN = 8.89

# Text sizes. Bumped well above KiCad's 1.27mm default so the schematic
# stays legible after it's pasted into a Word doc and shrunk to fit a page
# column -- at 1.27mm that pass made pin/net text unreadable.
PROP_SIZE = 1.8    # part Reference/Value captions above each box
PIN_TEXT_SIZE = 1.6    # pin name (shown) / number (hidden) next to each pin
PIN_TEXT_OFFSET = 0.8    # ~0.5x PIN_TEXT_SIZE, same ratio as the KiCad default
LABEL_SIZE = 2.0    # net labels at the end of each stub

def u():
    return str(uuid.uuid4())

# Vertical clearance below a part's box that its Reference/Value caption
# needs (captions are drawn below the box, not above it).
CAPTION_CLEAR = 3.2 * PROP_SIZE + 2.0

def stack_column(first_top, parts, gap=6.0):
    """Y-centers for `parts` stacked top-to-bottom, each row's top clearing
    the previous row's box + caption text + `gap`, starting at `first_top`.
    Each entry in `parts` is a Part (uses .height) or a bare height in mm --
    the latter lets a row shared by two side-by-side parts of different
    heights (e.g. a driver + motor pair) be spaced by the taller one."""
    heights = [p.height if hasattr(p, "height") else p for p in parts]
    ys = []
    y = first_top + heights[0] / 2
    for i, h in enumerate(heights):
        if i > 0:
            y = y_prev + heights[i - 1] / 2 + CAPTION_CLEAR + gap + h / 2
        ys.append(y)
        y_prev = y
    return ys

# ---------------------------------------------------------------------------
# Symbol (part) definitions
# ---------------------------------------------------------------------------

class Part:
    """A box symbol with named pins on the left and/or right side."""
    def __init__(self, libname, ref_prefix, value, left=None, right=None, width=20.32):
        self.libname = libname      # e.g. "epm:KX134"
        self.symname = libname.split(":", 1)[1]
        self.ref_prefix = ref_prefix
        self.value = value
        self.left = left or []      # list of pin display-names, top->bottom
        self.right = right or []
        self.width = width
        n = max(len(self.left), len(self.right), 1)
        self.height = (n - 1) * SPACING + 2 * SPACING  # margin top/bottom

    def pin_pos(self, side, index, n):
        """Local (x, y, angle) of a pin's OUTER (wire-attach) endpoint."""
        y = ((n - 1) / 2 - index) * SPACING
        hw = self.width / 2
        if side == "L":
            return (-hw - PIN_LEN, y, 0)     # points +x into body
        else:
            return (hw + PIN_LEN, y, 180)    # points -x into body

    def symbol_block(self):
        hw, hh = self.width / 2, self.height / 2
        lines = []
        lines.append(f'\t(symbol "{self.libname}"')
        lines.append('\t\t(pin_numbers')
        lines.append('\t\t\t(hide yes)')
        lines.append('\t\t)')
        lines.append('\t\t(pin_names')
        lines.append(f'\t\t\t(offset {PIN_TEXT_OFFSET})')
        lines.append('\t\t)')
        lines.append('\t\t(exclude_from_sim no)')
        lines.append('\t\t(in_bom yes)')
        lines.append('\t\t(on_board yes)')
        lines.append(f'\t\t(property "Reference" "{self.ref_prefix}"')
        lines.append(f'\t\t\t(at {-hw} {hh + 3.2 * PROP_SIZE} 0)')
        lines.append(f'\t\t\t(effects (font (size {PROP_SIZE} {PROP_SIZE})) (justify left))')
        lines.append('\t\t)')
        lines.append(f'\t\t(property "Value" "{self.value}"')
        lines.append(f'\t\t\t(at {-hw} {hh + 1.1 * PROP_SIZE} 0)')
        lines.append(f'\t\t\t(effects (font (size {PROP_SIZE} {PROP_SIZE})) (justify left))')
        lines.append('\t\t)')
        lines.append('\t\t(property "Footprint" ""')
        lines.append('\t\t\t(at 0 0 0)')
        lines.append('\t\t\t(effects (font (size 1.27 1.27)) (hide yes))')
        lines.append('\t\t)')
        lines.append('\t\t(property "Datasheet" ""')
        lines.append('\t\t\t(at 0 0 0)')
        lines.append('\t\t\t(effects (font (size 1.27 1.27)) (hide yes))')
        lines.append('\t\t)')
        lines.append(f'\t\t(symbol "{self.symname}_0_1"')
        lines.append('\t\t\t(rectangle')
        lines.append(f'\t\t\t\t(start {-hw} {hh}) (end {hw} {-hh})')
        lines.append('\t\t\t\t(stroke (width 0.254) (type default))')
        lines.append('\t\t\t\t(fill (type background))')
        lines.append('\t\t\t)')
        lines.append('\t\t)')
        lines.append(f'\t\t(symbol "{self.symname}_1_1"')
        pin_no = 1
        for side, names in (("L", self.left), ("R", self.right)):
            n = len(names)
            for i, name in enumerate(names):
                x, y, ang = self.pin_pos(side, i, n)
                lines.append('\t\t\t(pin bidirectional line')
                lines.append(f'\t\t\t\t(at {x} {y} {ang})')
                lines.append(f'\t\t\t\t(length {PIN_LEN})')
                lines.append(f'\t\t\t\t(name "{name}" (effects (font (size {PIN_TEXT_SIZE} {PIN_TEXT_SIZE}))))')
                lines.append(f'\t\t\t\t(number "{pin_no}" (effects (font (size {PIN_TEXT_SIZE} {PIN_TEXT_SIZE}))))')
                lines.append('\t\t\t)')
                pin_no += 1
        lines.append('\t\t)')
        lines.append('\t\t(embedded_fonts no)')
        lines.append('\t)')
        return "\n".join(lines)


POWER_SYMS = {}
def load_power_symbols():
    txt = open('/usr/share/kicad/symbols/power.kicad_sym').read()
    def extract(name):
        key = f'(symbol "{name}"'
        i = txt.index(key)
        depth, j = 0, i
        while True:
            if txt[j] == '(':
                depth += 1
            elif txt[j] == ')':
                depth -= 1
                if depth == 0:
                    return txt[i:j+1]
            j += 1
    for name in ["GND", "+3V3", "+5V", "+12V"]:
        block = extract(name)
        # rename the top-level symbol key to "power:NAME" and re-indent by one tab
        block = block.replace(f'(symbol "{name}"', f'(symbol "power:{name}"', 1)
        block = "\n".join(("\t" + l if l.strip() else l) for l in block.splitlines())
        POWER_SYMS[name] = block

load_power_symbols()

# ---------------------------------------------------------------------------
# Schematic writer
# ---------------------------------------------------------------------------

class Schematic:
    def __init__(self, title, paper="A3"):
        self.title = title
        self.paper = paper
        self.parts_used = {}   # libname -> Part
        self.power_used = set()
        self.instances = []    # (Part, ref, x, y, pin_nets: dict pinname->net)
        self.texts = []
        self.sheet_uuid = u()
        self.project_name = "epm"

    def place(self, part, ref, x, y, pin_nets):
        self.parts_used[part.libname] = part
        self.instances.append((part, ref, x, y, pin_nets))

    def add_power(self, kind, x, y, net_override=None):
        self.power_used.add(kind)
        self.instances.append(("POWER", kind, x, y, net_override or kind))

    def note(self, text, x, y, size=2.0):
        self.texts.append((text, x, y, size))

    def render(self):
        out = []
        out.append("(kicad_sch")
        out.append("\t(version 20250114)")
        out.append('\t(generator "eeschema")')
        out.append('\t(generator_version "9.0")')
        out.append(f'\t(uuid "{self.sheet_uuid}")')
        out.append(f'\t(paper "{self.paper}")')
        out.append("\t(title_block")
        out.append(f'\t\t(title "{self.title}")')
        out.append('\t\t(company "EdgeAI Predictive Monitor")')
        out.append("\t)")
        # Pre-scan pin_nets for PWR: references so the lib_symbols cache
        # (written before instances are walked) includes every power symbol
        # actually placed -- otherwise the lib_id can't resolve and neither
        # the glyph nor its Value text is drawn.
        for entry in self.instances:
            if entry[0] == "POWER":
                continue
            _, _, _, _, pin_nets = entry
            for net in pin_nets.values():
                if isinstance(net, str) and net.startswith("PWR:"):
                    self.power_used.add(net[4:])

        out.append("\t(lib_symbols")
        for part in self.parts_used.values():
            out.append(part.symbol_block())
        for kind in sorted(self.power_used):
            out.append(POWER_SYMS[kind])
        out.append("\t)")

        wires = []
        labels = []
        symbols = []
        texts = []

        for entry in self.instances:
            if entry[0] == "POWER":
                _, kind, x, y, net = entry
                symbols.append(self._power_symbol(kind, x, y))
                continue
            part, ref, x, y, pin_nets = entry
            symbols.append(self._part_symbol(part, ref, x, y))
            for side, names in (("L", part.left), ("R", part.right)):
                n = len(names)
                for i, name in enumerate(names):
                    lx, ly, ang = part.pin_pos(side, i, n)
                    # Symbol-local space is Y-up; sheet space is Y-down, so
                    # placing a symbol at rotation 0 flips the local Y sign.
                    px, py = x + lx, y - ly
                    net = pin_nets.get(name)
                    if net is None:
                        continue
                    # stub direction: same direction the pin points AWAY from body
                    if side == "L":
                        ex, ey = px - STUB_LEN, py
                    else:
                        ex, ey = px + STUB_LEN, py
                    wires.append(self._wire(px, py, ex, ey))
                    if isinstance(net, str) and net.startswith("PWR:"):
                        kind = net[4:]
                        self.power_used.add(kind)
                        symbols.append(self._power_symbol(kind, ex, ey))
                    else:
                        labels.append(self._label(net, ex, ey, 0 if side == "L" else 180))

        for text, x, y, size in self.texts:
            texts.append(self._text(text, x, y, size))

        out.extend(wires)
        out.extend(labels)
        out.extend(texts)
        out.extend(symbols)
        out.append("\t(sheet_instances")
        out.append('\t\t(path "/"')
        out.append('\t\t\t(page "1")')
        out.append("\t\t)")
        out.append("\t)")
        out.append(")")
        return "\n".join(out) + "\n"

    def _wire(self, x1, y1, x2, y2):
        return (f"\t(wire\n\t\t(pts\n\t\t\t(xy {x1} {y1}) (xy {x2} {y2})\n\t\t)\n"
                f"\t\t(stroke (width 0) (type default))\n\t\t(uuid \"{u()}\")\n\t)")

    def _label(self, text, x, y, angle):
        # angle 0 == pin exits to the left (side "L"): label text must run
        # further left, away from the box, so anchor at the text's right edge.
        justify = "right" if angle == 0 else "left"
        return (f'\t(label "{text}"\n\t\t(at {x} {y} 0)\n'
                f'\t\t(effects (font (size {LABEL_SIZE} {LABEL_SIZE})) (justify {justify}))\n'
                f'\t\t(uuid "{u()}")\n\t)')

    def _text(self, text, x, y, size):
        return (f'\t(text "{text}"\n\t\t(exclude_from_sim no)\n\t\t(at {x} {y} 0)\n'
                f'\t\t(effects (font (size {size} {size})) (justify left))\n'
                f'\t\t(uuid "{u()}")\n\t)')

    def _part_symbol(self, part, ref, x, y):
        lines = []
        lines.append("\t(symbol")
        lines.append(f'\t\t(lib_id "{part.libname}")')
        lines.append(f"\t\t(at {x} {y} 0)")
        lines.append("\t\t(unit 1)")
        lines.append("\t\t(exclude_from_sim no)")
        lines.append("\t\t(in_bom yes)")
        lines.append("\t\t(on_board yes)")
        lines.append("\t\t(dnp no)")
        iuuid = u()
        lines.append(f'\t\t(uuid "{iuuid}")')
        hw, hh = part.width / 2, part.height / 2
        lines.append(f'\t\t(property "Reference" "{ref}"')
        lines.append(f"\t\t\t(at {x - hw} {y + hh + 3.2 * PROP_SIZE} 0)")
        lines.append(f"\t\t\t(effects (font (size {PROP_SIZE} {PROP_SIZE})) (justify left))")
        lines.append("\t\t)")
        lines.append(f'\t\t(property "Value" "{part.value}"')
        lines.append(f"\t\t\t(at {x - hw} {y + hh + 1.1 * PROP_SIZE} 0)")
        lines.append(f"\t\t\t(effects (font (size {PROP_SIZE} {PROP_SIZE})) (justify left))")
        lines.append("\t\t)")
        lines.append('\t\t(property "Footprint" ""')
        lines.append(f"\t\t\t(at {x} {y} 0)")
        lines.append("\t\t\t(effects (font (size 1.27 1.27)) (hide yes))")
        lines.append("\t\t)")
        lines.append('\t\t(property "Datasheet" ""')
        lines.append(f"\t\t\t(at {x} {y} 0)")
        lines.append("\t\t\t(effects (font (size 1.27 1.27)) (hide yes))")
        lines.append("\t\t)")
        pin_no = 1
        for side, names in (("L", part.left), ("R", part.right)):
            for _ in names:
                lines.append(f'\t\t(pin "{pin_no}" (uuid "{u()}"))')
                pin_no += 1
        lines.append("\t\t(instances")
        lines.append(f'\t\t\t(project "{self.project_name}"')
        lines.append(f'\t\t\t\t(path "/{self.sheet_uuid}"')
        lines.append(f'\t\t\t\t\t(reference "{ref}")')
        lines.append("\t\t\t\t\t(unit 1)")
        lines.append("\t\t\t\t)")
        lines.append("\t\t\t)")
        lines.append("\t\t)")
        lines.append("\t)")
        return "\n".join(lines)

    def _power_symbol(self, kind, x, y):
        ref = {"GND": "#PWR", "+3V3": "#PWR", "+5V": "#PWR", "+12V": "#PWR"}[kind] + u()[:4]
        lines = []
        lines.append("\t(symbol")
        lines.append(f'\t\t(lib_id "power:{kind}")')
        lines.append(f"\t\t(at {x} {y} 0)")
        lines.append("\t\t(unit 1)")
        lines.append("\t\t(exclude_from_sim no)")
        lines.append("\t\t(in_bom yes)")
        lines.append("\t\t(on_board yes)")
        lines.append("\t\t(dnp no)")
        iuuid = u()
        lines.append(f'\t\t(uuid "{iuuid}")')
        lines.append(f'\t\t(property "Reference" "{ref}"')
        lines.append(f"\t\t\t(at {x} {y - 3 * LABEL_SIZE} 0)")
        lines.append("\t\t\t(effects (font (size 1.27 1.27)) (hide yes))")
        lines.append("\t\t)")
        lines.append(f'\t\t(property "Value" "{kind}"')
        lines.append(f"\t\t\t(at {x} {y + 2.2 * LABEL_SIZE} 0)")
        lines.append(f"\t\t\t(effects (font (size {LABEL_SIZE} {LABEL_SIZE})))")
        lines.append("\t\t)")
        lines.append('\t\t(property "Footprint" ""')
        lines.append(f"\t\t\t(at {x} {y} 0)")
        lines.append("\t\t\t(effects (font (size 1.27 1.27)) (hide yes))")
        lines.append("\t\t)")
        lines.append('\t\t(property "Datasheet" ""')
        lines.append(f"\t\t\t(at {x} {y} 0)")
        lines.append("\t\t\t(effects (font (size 1.27 1.27)) (hide yes))")
        lines.append("\t\t)")
        lines.append(f'\t\t(pin "1" (uuid "{u()}"))')
        lines.append("\t\t(instances")
        lines.append(f'\t\t\t(project "{self.project_name}"')
        lines.append(f'\t\t\t\t(path "/{self.sheet_uuid}"')
        lines.append(f'\t\t\t\t\t(reference "{ref}")')
        lines.append("\t\t\t\t\t(unit 1)")
        lines.append("\t\t\t\t)")
        lines.append("\t\t\t)")
        lines.append("\t\t)")
        lines.append("\t)")
        return "\n".join(lines)


def write_pro(path, sch=None):
    """Write a minimal KiCad 9 project file next to the .kicad_sch so the
    schematic opens as a self-contained *project* (Hackster/other people
    download one bundle and double-click), not a loose sheet that makes
    KiCad prompt to create a project on open. KiCad backfills every other
    setting with its defaults on first open; the one thing it wants up front
    is `sheets` mapping the root sheet's UUID to a page name."""
    import json
    stem = os.path.splitext(os.path.basename(path))[0]
    root_uuid = sch.sheet_uuid if sch is not None else str(uuid.uuid4())
    pro = {
        "board": {"design_settings": {}, "layer_presets": [], "viewports": []},
        "boards": [],
        "cvpcb": {"equivalence_files": []},
        "libraries": {"pinned_footprint_libs": [], "pinned_symbol_libs": []},
        "meta": {"filename": os.path.basename(path), "version": 1},
        "net_settings": {"classes": []},
        "pcbnew": {"page_layout_descr_file": ""},
        "schematic": {
            "legacy_lib_dir": "",
            "legacy_lib_list": [],
        },
        "sheets": [[root_uuid, "Root"]],
        "text_variables": {},
    }
    with open(path, "w") as f:
        json.dump(pro, f, indent=2)


if __name__ == "__main__":
    print("library helpers loaded")
