"""Make logo_polys.json from the HERON pixel logo SVG.

Why: gen_pcb.py and update_logo.py put the logo on the silkscreen from
logo_polys.json. The JSON keeps the board build independent of the SVG
parser. Run this script again each time the logo SVG changes, then run
update_logo.py (the board as it is) or gen_pcb.py (a full rebuild).

Source: HERON/Templates and Logos/HERON_pixel_20mm.svg. The SVG is a set
of filled <rect> runs on a 64 x 64 viewBox, 20 mm wide. The script stops
when the file has other shapes, because they would be lost without a
message (the same rule as ../clock_board_revC/silk_art.py).

Output: two outline sets, "front" (as drawn) and "back" (mirrored in x, so
the logo reads correctly from the back side). Each set is centred on
(0, 0), in mm at the 20 mm SVG size; "size" is the larger of the width and
the height of the filled pixels. gen_pcb.py scales and places each set.

Run it with the KiCad 10 Python (it needs pcbnew for the polygon union):

    <KiCad 10>/bin/python3 extract_logo.py [logo.svg]
"""

import json
import os
import sys
import xml.etree.ElementTree as ET

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "..", "HERON", "Templates and Logos", "HERON_pixel_20mm.svg")
OUT = os.path.join(HERE, "logo_polys.json")
SCALE = 1e6        # internal units per mm for the union (KiCad nm), only inside this script
# SVG elements that hold no shapes. Inkscape adds <defs> and <sodipodi:namedview>.
NO_SHAPE_TAGS = {"svg", "g", "defs", "namedview", "metadata", "title", "desc"}


def read_logo(path):
    """Read the pixel logo. Return (rects in SVG units, mm per SVG unit)."""
    root = ET.parse(path).getroot()
    vb = [float(v) for v in root.get("viewBox").split()]
    width = root.get("width", "")
    if not width.endswith("mm"):
        sys.exit(f"{path}: the svg width must be in mm (found '{width}').")
    rects = []
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag == "rect":
            if el.get("transform"):
                sys.exit(f"{path}: rect {el.get('id')} has a transform. Remove it in the editor.")
            rects.append(tuple(float(el.get(k)) for k in ("x", "y", "width", "height")))
        elif tag not in NO_SHAPE_TAGS:
            sys.exit(f"{path}: unsupported SVG element <{tag}>. Use only <rect>.")
    for el in root.iter():
        if el.get("transform"):
            sys.exit(f"{path}: element <{el.tag.split('}')[-1]}> has a transform. Remove it in the editor.")
    return rects, float(width[:-2]) / vb[2]


def outlines(rects, mm, mirror):
    """Union the pixel rects into outlines. Return a list of [(x, y), ...] in mm.

    Why the union: a few dozen clean outlines in place of about 250 rects.
    Fracture() removes the holes, because a board polygon cannot have holes."""
    poly = pcbnew.SHAPE_POLY_SET()
    for x, y, w, h in rects:
        x0, x1 = x * mm, (x + w) * mm
        if mirror:
            x0, x1 = -x1, -x0
        y0, y1 = y * mm, (y + h) * mm
        poly.NewOutline()
        for px, py in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
            poly.Append(int(round(px * SCALE)), int(round(py * SCALE)))
    poly.Simplify()
    poly.Fracture()
    out = []
    for i in range(poly.OutlineCount()):
        o = poly.Outline(i)
        out.append([(o.CPoint(k).x / SCALE, o.CPoint(k).y / SCALE) for k in range(o.PointCount())])
    return out


def centred(polys):
    """Centre the outlines on (0, 0). Return the JSON entry (size and polys)."""
    xs = [x for p in polys for x, _ in p]
    ys = [y for p in polys for _, y in p]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    return dict(size=round(max(max(xs) - min(xs), max(ys) - min(ys)), 4),
                polys=[[(round(x - cx, 4), round(y - cy, 4)) for x, y in p] for p in polys])


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else SRC
    if not os.path.isfile(src):
        sys.exit(f"Logo file not found: {src}")
    rects, mm = read_logo(src)
    out = {side: centred(outlines(rects, mm, mirror)) for side, mirror in (("front", False), ("back", True))}
    for side, d in out.items():
        print(side, len(d["polys"]), "polygons, size", d["size"], "mm, from", len(rects), "rects")
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=0)


if __name__ == "__main__":
    main()
