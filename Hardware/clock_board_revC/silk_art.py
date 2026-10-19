"""Add the HERON logo and the board title to the rev C silkscreen.

The back gets the full-size logo (20 mm) and the title block. The back has
almost no parts, so it has the room. The front gets a small logo and the
board name in the free strip under the 10 MHz jacks.

Why a separate script: the rev C KiCad files can be hand-edited (see the
README, EXPORT_ONLY). This script works on the board file as it is now, and
`make_revc.py` also calls it, so a full rebuild keeps the art.

The script is idempotent. It puts all its items in one group (GROUP_NAME)
and deletes that group before it adds the items again. It also replaces the
old one-line back title ("HERON CLOCK DIST ...") with the new title block,
and deletes the old front "+5V" text (the J1 input text replaces it).

Run it with the Python that comes with KiCad 10 (it needs `pcbnew`):

    <KiCad 10>/bin/python3 silk_art.py [board.kicad_pcb]

The default board is project/heron_clock.kicad_pcb. All positions are
board-local mm, (0, 0) = top-left corner, seen from the component side.
"""

import os
import sys
import xml.etree.ElementTree as ET

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = os.path.join(HERE, "project", "heron_clock.kicad_pcb")
LOGO_SVG = os.path.join(HERE, "..", "..", "HERON", "Templates and Logos", "HERON_pixel_20mm.svg")

OX, OY = 100.0, 100.0           # sheet offset of the board corner (the same as make_revc.py)
GROUP_NAME = "HERON_SILK_ART"

# The old back title from rev B. It overlaps the J2 legs. The title block replaces it.
OLD_TITLE_PREFIX = "HERON CLOCK DIST"
# Old front text that the new J1 text ("+5V IN / 5.5V MAX") replaces. It also ran onto
# the J1 mounting pad (DRC: silkscreen clipped by solder mask).
OLD_EXACT_TEXTS = {"+5V"}
# The assembly warning in the old title stays on the board as its own line.
FLUX_LINE = ("U3 SiT5155: water-soluble flux only", (59.0, 35.5), 1.0)

# Back: full-size logo between the B200_1 and B200_2 jack columns (bay x 77.2-99.1),
# centred between the PPS and 10 MHz jack rows.
BACK_LOGO = dict(center=(88.16, 20.81), pixel=20.0 / 64)
# Back title block in the band between the jack rows (y 14.9-26.7, no jack legs).
# (text, (x, y) centre, text height mm)
BACK_TEXT = [
    ("HERON Clock Distribution Board", (54.0, 17.2), 1.5),
    ("10 MHz + PPS to 4 SDRs", (54.0, 19.9), 1.0),
    ("Rev C - 10/2026", (54.0, 22.2), 1.0),
    ("Designed by Nathan Cohen for CU Boulder", (54.0, 24.5), 1.0),
]

# Front: the free strip under J4 and J5 (y 34.9-43.6, x 39-85.5). The strip sets the size:
# 0.135 mm pixels give a 8.6 mm logo. That is under the usual 0.15 mm silk minimum, so the
# 1-pixel lines of the logo can print thin or break up. Fine detail is not critical here.
FRONT_LOGO = dict(center=(44.32, 39.27), pixel=0.135)
FRONT_TEXT = [
    ("HERON Clock Distribution Board", (66.5, 37.3), 1.2),
    ("10 MHz + PPS to 4 SDRs", (66.5, 39.5), 0.9),
    ("Rev C - 10/2026", (66.5, 41.5), 0.9),
    # Input limit, left of J1 (free area x 16.5-26.4, y 0.5-6.5). Why: U1 and U2
    # (LP5907, TLV755) have a 5.5 V absolute maximum input.
    ("+5V IN", (21.3, 2.3), 0.9),
    ("5.5V MAX", (21.3, 3.8), 0.9),
    # R23 note, right of J2 (free area x 94.6-110.2, y 35.0-43.6). Why: R23 is DNP.
    # Fit it only when the GNSS PPS source needs a 50 ohm load.
    ("R23: 50 ohm PPS load", (102.4, 37.6), 0.8),
    ("DNP - fit if needed", (102.4, 39.0), 0.8),
]

STROKE_RATIO = 0.15             # text stroke width / text height


def P(x, y):
    """Board-local mm -> KiCad internal units."""
    return pcbnew.VECTOR2I(pcbnew.FromMM(OX + x), pcbnew.FromMM(OY + y))


def read_logo(path):
    """Read the pixel logo. Return (rects, size): rects in SVG pixel units.

    The SVG is a set of filled <rect> runs on a 64 x 64 viewBox. Only rect
    elements are read; the script stops when the file has other shapes,
    because they would be lost without a message."""
    root = ET.parse(path).getroot()
    vb = [float(v) for v in root.get("viewBox").split()]
    rects = []
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag == "rect":
            rects.append(tuple(float(el.get(k)) for k in ("x", "y", "width", "height")))
        elif tag not in ("svg", "g"):
            sys.exit(f"{path}: unsupported SVG element <{tag}>. Use only <rect>.")
    return rects, (vb[2], vb[3])


def logo_shapes(board, rects, size, center, pixel, layer, mirror):
    """Make the logo as filled silkscreen polygons.

    Why polygons and not one shape per rect: the union gives clean outlines
    and a few dozen items in place of about 300. Fracture() removes the holes,
    because a board polygon cannot have holes. mirror=True flips x, so the
    logo reads correctly from the back side."""
    w, h = size
    cx, cy = center
    poly = pcbnew.SHAPE_POLY_SET()
    for x, y, rw, rh in rects:
        x0, x1 = (x - w / 2) * pixel, (x + rw - w / 2) * pixel
        if mirror:
            x0, x1 = -x1, -x0
        y0, y1 = (y - h / 2) * pixel, (y + rh - h / 2) * pixel
        poly.NewOutline()
        for px, py in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
            poly.Append(P(cx + px, cy + py))
    poly.Simplify()
    poly.Fracture()
    shapes = []
    for i in range(poly.OutlineCount()):
        one = pcbnew.SHAPE_POLY_SET()
        one.AddOutline(poly.Outline(i))
        s = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_POLY)
        s.SetPolyShape(one)
        s.SetFilled(True)
        s.SetWidth(0)
        s.SetLayer(layer)
        shapes.append(s)
    return shapes


def text_item(board, text, at, height, layer, mirror):
    """Make one centred silkscreen text."""
    t = pcbnew.PCB_TEXT(board)
    t.SetText(text)
    t.SetPosition(P(*at))
    t.SetLayer(layer)
    t.SetTextSize(pcbnew.VECTOR2I(pcbnew.FromMM(height), pcbnew.FromMM(height)))
    t.SetTextThickness(pcbnew.FromMM(height * STROKE_RATIO))
    t.SetHorizJustify(pcbnew.GR_TEXT_H_ALIGN_CENTER)
    t.SetVertJustify(pcbnew.GR_TEXT_V_ALIGN_CENTER)
    t.SetMirrored(mirror)
    return t


def remove_old(board):
    """Delete the items of an earlier run, the old one-line back title and the old +5V text."""
    for g in list(board.Groups()):
        if g.GetName() == GROUP_NAME:
            for item in list(g.GetItems()):
                board.Delete(item)
            board.Delete(g)
    for d in list(board.GetDrawings()):
        if isinstance(d, pcbnew.PCB_TEXT) and (d.GetText().startswith(OLD_TITLE_PREFIX)
                                               or d.GetText() in OLD_EXACT_TEXTS):
            board.Delete(d)


def add_silk_art(board, logo_svg=LOGO_SVG):
    """Add the logos and the title texts to the board, in one group."""
    if not os.path.isfile(logo_svg):
        sys.exit(f"Logo file not found: {logo_svg}")
    remove_old(board)
    rects, size = read_logo(logo_svg)
    items = []
    items += logo_shapes(board, rects, size, layer=pcbnew.B_SilkS, mirror=True, **BACK_LOGO)
    items += logo_shapes(board, rects, size, layer=pcbnew.F_SilkS, mirror=False, **FRONT_LOGO)
    for text, at, height in BACK_TEXT + [FLUX_LINE]:
        items.append(text_item(board, text, at, height, pcbnew.B_SilkS, mirror=True))
    for text, at, height in FRONT_TEXT:
        items.append(text_item(board, text, at, height, pcbnew.F_SilkS, mirror=False))
    group = pcbnew.PCB_GROUP(board)
    group.SetName(GROUP_NAME)
    board.Add(group)
    for item in items:
        board.Add(item)
        group.AddItem(item)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else BOARD
    board = pcbnew.LoadBoard(path)
    add_silk_art(board)
    board.Save(path)
    print(f"silk art added: {path}")


if __name__ == "__main__":
    main()
