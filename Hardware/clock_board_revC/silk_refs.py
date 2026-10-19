"""Show the reference designators of all parts on the front silkscreen.

Rev C hid the reference designators of all parts except J1, J2, the ICs and
the test points. The board is dense, so this script finds a clear spot for
each label. It does not move any part. The labels of the ICs, the test points,
J1 and J2 were placed by hand; this script does not touch them.

How it places a label: it tries positions on rings around the resistor, at
0 and 90 degrees. A position is good when the text box (plus MARGIN) is
inside the board and does not touch a front pad or other front silkscreen.
Tracks, vias and courtyards are allowed under the text: the vias are tented,
so the silk prints on solder mask. Of the good positions, it takes the one
nearest to its own part, with a penalty when the label is nearer to a
different part (the label must point to the right part).

The script is deterministic and idempotent: it hides all resistor labels
first, then places them again. It places the prefixes in PREFIXES order, so
the resistors get the first choice of space.

Run it with the Python that comes with KiCad 10 (it needs `pcbnew`):

    <KiCad 10>/bin/python3 silk_refs.py [board.kicad_pcb]

`make_revc.py` calls it after `silk_art.py`.
"""

import math
import os
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = os.path.join(HERE, "project", "heron_clock.kicad_pcb")

# Reference prefixes of the parts that get a label, in placement order.
PREFIXES = ("R", "C", "D", "L", "F", "FB", "J", "H", "FID")
KEEP = {"J1", "J2"}             # labels placed by hand: do not move them
# Parts whose body covers the board inside the courtyard (the SMP jacks are 6 mm
# square). A label there is hidden after assembly, so the courtyard is a keep-out.
BODY_PREFIXES = ("J",)
TEXT_H = 0.8                    # mm, the board min_text_height and the other labels
TEXT_W = 0.12                   # mm stroke, the same as the other labels
MARGIN = 0.12                   # mm, clear space around the text box
EDGE = 0.4                      # mm, clear space to the board edge
RADII = [0.9, 1.1, 1.3, 1.6, 1.9, 2.3, 2.8, 3.4, 4.0, 4.8]   # mm, label centre distance
FAR_RADII = [5.6, 6.4]          # mm, extra distances for a part that found no spot in a pass
DIRECTIONS = 24                 # directions on each ring
PASSES = 5                      # placement passes (see place_labels)
WRONG_PART_PENALTY = 3.0        # mm added when another part is nearer than the own one


def split_ref(ref):
    """Split a reference into (prefix, number): "FB1" -> ("FB", 1)."""
    head = ref.rstrip("0123456789")
    return head, int(ref[len(head):] or 0)


def is_target(fp):
    """True for the parts that get a label from this script."""
    ref = fp.GetReference()
    return split_ref(ref)[0] in PREFIXES and ref not in KEEP


def order(fp):
    """Placement order: PREFIXES order, then the reference number."""
    head, num = split_ref(fp.GetReference())
    return PREFIXES.index(head), num


def boxes_front(board, skip_refs):
    """Collect the front keep-out boxes: pads, footprint silk, visible texts, board silk.

    skip_refs: labels that this script places; they are not keep-outs at the start."""
    boxes = []
    for fp in board.GetFootprints():
        for p in fp.Pads():
            if p.IsOnLayer(pcbnew.F_Cu) or p.IsOnLayer(pcbnew.F_Mask):
                boxes.append(p.GetBoundingBox())
        body = split_ref(fp.GetReference())[0] in BODY_PREFIXES
        for g in fp.GraphicalItems():
            if g.GetLayer() == pcbnew.F_SilkS or (body and g.GetLayer() == pcbnew.F_CrtYd):
                boxes.append(g.GetBoundingBox())
        for t in (fp.Reference(), fp.Value()):
            if t.IsVisible() and t.GetLayer() == pcbnew.F_SilkS and fp.GetReference() not in skip_refs:
                boxes.append(t.GetBoundingBox())
    for d in board.GetDrawings():
        if d.GetLayer() == pcbnew.F_SilkS:
            boxes.append(d.GetBoundingBox())
    return boxes


def overlaps(a, boxes, margin):
    """True when box a, grown by margin, touches any box in the list."""
    m = pcbnew.FromMM(margin)
    ax0, ay0, ax1, ay1 = a.GetX() - m, a.GetY() - m, a.GetRight() + m, a.GetBottom() + m
    for b in boxes:
        if ax0 < b.GetRight() and b.GetX() < ax1 and ay0 < b.GetBottom() and b.GetY() < ay1:
            return True
    return False


def inside(a, edge_box, edge):
    """True when box a is inside the board edge box, with the edge margin."""
    e = pcbnew.FromMM(edge)
    return (a.GetX() >= edge_box.GetX() + e and a.GetRight() <= edge_box.GetRight() - e
            and a.GetY() >= edge_box.GetY() + e and a.GetBottom() <= edge_box.GetBottom() - e)


def candidates(center, radii):
    """Label centres and angles to try, nearest first."""
    cx, cy = center
    out = []
    for r in radii:
        for k in range(DIRECTIONS):
            a = 2 * math.pi * k / DIRECTIONS
            for ang in (0.0, 90.0):
                out.append((r, cx + r * math.cos(a), cy + r * math.sin(a), ang))
    return out


def place_pass(board, parts, centres, far):
    """Place the labels once, in the order of parts. Return the refs with no clear spot.

    far: refs that may also use FAR_RADII."""
    refs = {fp.GetReference() for fp in parts}
    for fp in parts:
        fp.Reference().SetVisible(False)
    boxes = boxes_front(board, refs)
    edge_box = board.GetBoardEdgesBoundingBox()
    failed = []
    for fp in parts:
        ref = fp.GetReference()
        t = fp.Reference()
        t.SetLayer(pcbnew.F_SilkS)
        t.SetTextSize(pcbnew.VECTOR2I(pcbnew.FromMM(TEXT_H), pcbnew.FromMM(TEXT_H)))
        t.SetTextThickness(pcbnew.FromMM(TEXT_W))
        t.SetHorizJustify(pcbnew.GR_TEXT_H_ALIGN_CENTER)
        t.SetVertJustify(pcbnew.GR_TEXT_V_ALIGN_CENTER)
        t.SetKeepUpright(True)
        best = None
        radii = RADII + FAR_RADII if ref in far else RADII
        for r, x, y, ang in candidates(centres[ref], radii):
            t.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)))
            t.SetTextAngleDegrees(ang)
            bb = t.GetBoundingBox()
            if not inside(bb, edge_box, EDGE) or overlaps(bb, boxes, MARGIN):
                continue
            nearest = min(math.dist((x, y), c) for o, c in centres.items() if o != ref)
            score = r + (WRONG_PART_PENALTY if nearest < r else 0.0)
            if best is None or score < best[0]:
                best = (score, x, y, ang)
        if best is None:
            failed.append(ref)
            continue
        _, x, y, ang = best
        t.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)))
        t.SetTextAngleDegrees(ang)
        t.SetVisible(True)
        boxes.append(t.GetBoundingBox())
    return failed


def place_labels(board):
    """Show and place the label of each target part. Return the refs with no clear spot.

    Why more than one pass: in a crowded area an early label can take the only
    spot of a later one. When a pass leaves labels out, the next pass places
    those parts first, and they may also use FAR_RADII. The result is still
    deterministic."""
    parts = sorted((fp for fp in board.GetFootprints() if is_target(fp)), key=order)
    # All parts, not only the targets: a label must not sit nearer to any other part.
    centres = {fp.GetReference(): (pcbnew.ToMM(fp.GetPosition().x), pcbnew.ToMM(fp.GetPosition().y))
               for fp in board.GetFootprints()}
    failed, far = [], set()
    for _ in range(PASSES):
        failed = place_pass(board, parts, centres, far)
        if not failed:
            break
        far.update(failed)
        first = [fp for fp in parts if fp.GetReference() in failed]
        parts = first + [fp for fp in parts if fp.GetReference() not in failed]
    return failed


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else BOARD
    board = pcbnew.LoadBoard(path)
    failed = place_labels(board)
    board.Save(path)
    if failed:
        print("no clear spot, label hidden: " + " ".join(failed))
    print(f"labels placed: {path}")


if __name__ == "__main__":
    main()
