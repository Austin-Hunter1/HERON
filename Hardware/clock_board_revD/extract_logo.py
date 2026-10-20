"""Extract the HERON logo polygons from the rev C board into logo_polys.json.

Why: rev D needs the same logo, but the source SVG
(HERON/Templates and Logos/HERON_pixel_20mm.svg, see ../clock_board_revC/
silk_art.py) is not in every checkout. The rev C board holds the logo as
filled silkscreen polygons (group HERON_SILK_ART): a 20 mm logo on B.SilkS
(mirrored, to read from the back) and an 8.6 mm logo on F.SilkS. This script
saves both outlines, centred on (0, 0), in mm at their rev C size. gen_pcb.py
scales and places them.

Run once with the KiCad 10 Python:

    <KiCad 10>/bin/python3 extract_logo.py
"""

import json
import os

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "clock_board_revC", "project", "heron_clock.kicad_pcb")
OUT = os.path.join(HERE, "logo_polys.json")


def main():
    b = pcbnew.LoadBoard(SRC)
    group = next(g for g in b.Groups() if g.GetName() == "HERON_SILK_ART")
    layers = {pcbnew.F_SilkS: "front", pcbnew.B_SilkS: "back"}
    polys = {"front": [], "back": []}
    for item in group.GetItems():
        if item.GetClass() != "PCB_SHAPE" or item.GetShape() != pcbnew.SHAPE_T_POLY:
            continue
        side = layers.get(item.GetLayer())
        if side is None:
            continue
        ps = item.GetPolyShape()
        for i in range(ps.OutlineCount()):
            o = ps.Outline(i)
            polys[side].append([(pcbnew.ToMM(o.CPoint(k).x), pcbnew.ToMM(o.CPoint(k).y)) for k in range(o.PointCount())])
    out = {}
    for side, pl in polys.items():
        xs = [x for p in pl for x, _ in p]
        ys = [y for p in pl for _, y in p]
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        out[side] = dict(size=round(max(max(xs) - min(xs), max(ys) - min(ys)), 3),
                         polys=[[(round(x - cx, 4), round(y - cy, 4)) for x, y in p] for p in pl])
        print(side, len(pl), "polygons, size", out[side]["size"], "mm")
    json.dump(out, open(OUT, "w"), indent=0)


if __name__ == "__main__":
    main()
