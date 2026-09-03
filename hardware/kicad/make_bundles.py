#!/usr/bin/env python3
"""Bundle the generated KiCad projects into upload-ready zips under dist/.

Each board -> dist/<board>-kicad.zip  (its .kicad_pro + .kicad_sch + .pdf)
All three -> dist/edgeai-schematics-kicad.zip

Hackster serves Attachments as raw downloads (no image-CDN recompression),
so a zip attached there stays byte-for-byte what KiCad wrote. Run the three
board scripts first if anything changed; this only packages what's on disk.
"""
import os
import zipfile

BOARDS = ["base_station", "satellite_node", "motor_driver_rig"]
EXTS = [".kicad_pro", ".kicad_sch", ".pdf"]

here = os.path.dirname(os.path.abspath(__file__))
dist = os.path.join(here, "dist")
os.makedirs(dist, exist_ok=True)


def add(zf, board):
    for ext in EXTS:
        name = board + ext
        path = os.path.join(here, name)
        if not os.path.exists(path):
            raise SystemExit(f"missing {name} - run `python3 {board}.py` first")
        zf.write(path, arcname=name)


for board in BOARDS:
    out = os.path.join(dist, f"{board}-kicad.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        add(zf, board)
    print("wrote", os.path.relpath(out, here))

allzip = os.path.join(dist, "edgeai-schematics-kicad.zip")
with zipfile.ZipFile(allzip, "w", zipfile.ZIP_DEFLATED) as zf:
    for board in BOARDS:
        add(zf, board)
print("wrote", os.path.relpath(allzip, here))
