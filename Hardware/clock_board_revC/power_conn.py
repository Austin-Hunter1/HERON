"""Change the rev C power input J1 from JST-GH (SMD) to JST XH (through-hole) (D-040).

Why: the JST SM02B-GHS-TB(LF)(SN) is out of stock (Digi-Key, 2026-10-20), and
no GH-compatible substitute is sold there. The new part is the JST
S2B-XH-A-1(LF)(SN): 2 pins, 2.5 mm pitch, side entry, friction lock. The
"-1" version has 7.6 mm from the mating face to the pins (the plain S2B-XH-A
has 9.2 mm), so it takes less board depth.

What the script changes:
- Schematic: the J1 footprint and MPN, and the power input note.
- PCB: J1 gets the stock KiCad footprint. The mating face stays on the top
  board edge, as with the GH part. The body (7.5 x 11.6 mm, 6.1 mm high) does
  not fit at the old place, because D1, F1 and D2 are below it. It moves to
  the free area between D2 and J8. The +5 V track to F1 gets a new start.
  The GND pin needs no track: the through-hole pad connects to the In1 plane
  and the pours. The GND stitch vias that touch the new pads or the new track
  are removed.
- The "+5V IN / 5.5V MAX" text moves next to the new J1 (silk_art.py has the
  position). The J1 label is put beside it. The J8 "PPS" label moves out
  from under the J1 body.

The pinout does not change: pin 1 = +5 V, pin 2 = GND.

Two ways to run it:
- `make_revc.py` calls `apply_schematic` and `apply_board` when it makes rev C
  from rev B.
- Alone, on the rev C files as they are now (for example when the logo file
  that `silk_art.py` needs is not on this computer):

      <KiCad 10>/bin/python3 power_conn.py

  The script is idempotent: a second run finds J1 already changed and stops.
  Set KICAD10_FOOTPRINT_DIR when the stock footprints are not in the default
  place (macOS: KiCad.app/Contents/SharedSupport/footprints).
"""

import math
import os
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
SCH = os.path.join(HERE, "project", "heron_clock.kicad_sch")
PCB = os.path.join(HERE, "project", "heron_clock.kicad_pcb")
OX, OY = 100.0, 100.0           # sheet offset of the board corner (the same as make_revc.py)

OLD_FP = "Connector_JST:JST_GH_SM02B-GHS-TB_1x02-1MP_P1.25mm_Horizontal"
OLD_MPN = "SM02B-GHS-TB(LF)(SN)"
NEW_FP = "Connector_JST:JST_XH_S2B-XH-A-1_1x02_P2.50mm_Horizontal"
NEW_MPN = "S2B-XH-A-1(LF)(SN)"
OLD_NOTE = "POWER INPUT: 5 V from payload rail (JST-GH, locking)"
NEW_NOTE = "POWER INPUT: 5 V from payload rail (JST XH, friction lock)"

# Placement. The footprint origin is pin 1. Rotation 180 puts the mating face
# toward the top edge (y = 0). In the footprint the mating face is 7.65 mm from
# the pins. Pin 1 at y 7.85 puts the face 0.2 mm inside the edge, so the
# footprint silk line in front of the face stays on the board (DRC
# silk_edge_clearance).
# x: the rotated courtyard spans pin 1 x - 5.5 to pin 1 x + 3.0. The free area
# is x 30.5 (D1 courtyard) to 39.24 (J8 courtyard). y: the courtyard ends at
# 12.30, above D2 (12.38).
J1_PIN1 = (36.10, 7.85)
J1_ROT = 180.0
J1_LABEL = (27.4, 5.5)          # left of J1, under the "+5V IN / 5.5V MAX" text

# +5 V (net of J1 pin 1): new track from pin 1 to the old route at (30.74, 7.23).
# It goes up from pin 1, over pin 2 (pad top edge y 6.65), then down to the
# old route, which continues between the D1 pads to F1.
OLD_ROUTE_JOIN = (30.74, 7.23)
NEW_ROUTE = [J1_PIN1, (36.10, 6.00), (35.00, 4.90), (31.60, 4.90), (30.74, 5.76), OLD_ROUTE_JOIN]
ROUTE_W = 0.25                  # mm, the width of the old route

# The rev B "PPS" label of J8 stands upright between J1 and J8. The J1 body
# covers it now (DRC silk_overlap). It moves above J8, flat, between the
# "B210_2" title (y 0.7-2.5) and the J8 label (y 5.5-6.3).
PPS_LABEL_OLD = (37.56, 10.84)
PPS_LABEL_NEW = (43.16, 3.9)

VIA_KEEP_PAD = 2.0              # mm, remove GND vias nearer than this to a new pad centre
VIA_KEEP_TRACK = 0.65           # mm, remove GND vias nearer than this to the new track axis


# ------------------------------------------------------------------ schematic
def apply_schematic(path):
    """Change the J1 footprint and MPN and the power input note in the schematic.

    Why text edits: the same reason as make_revc.convert_schematic. The old
    footprint, MPN and note occur only on J1, so a replace of the quoted
    strings is safe. Return the number of strings changed."""
    text = open(path, encoding="utf-8").read()
    n = 0
    for old, new in ((OLD_FP, NEW_FP), (OLD_MPN, NEW_MPN), (OLD_NOTE, NEW_NOTE)):
        n += text.count(f'"{old}"')
        text = text.replace(f'"{old}"', f'"{new}"')
    open(path, "w", encoding="utf-8", newline="\n").write(text)
    print(f"schematic: {n} J1 strings changed")
    return n


# ------------------------------------------------------------------ PCB
def P(x, y):
    """Board-local mm (0, 0 = top-left corner) -> KiCad internal units."""
    return pcbnew.VECTOR2I(pcbnew.FromMM(OX + x), pcbnew.FromMM(OY + y))


def L(v):
    """KiCad position -> board-local mm tuple."""
    return (pcbnew.ToMM(v.x) - OX, pcbnew.ToMM(v.y) - OY)


def seg_dist(p, a, b):
    """Distance from point p to the segment a-b (mm)."""
    ax, ay = a; bx, by = b; px, py = p
    dx, dy = bx - ax, by - ay
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def find_j1(board):
    return next(fp for fp in board.GetFootprints() if fp.GetReference() == "J1")


def lib_id(fp):
    return f"{fp.GetFPID().GetLibNickname()}:{fp.GetFPID().GetLibItemName()}"


def remove_old_pad_tracks(board, old):
    """Delete the tracks that end on a pad of the old J1. The new pads are elsewhere."""
    ends = {(round(x, 2), round(y, 2)) for x, y in (L(p.GetPosition()) for p in old.Pads())}
    n = 0
    for t in list(board.GetTracks()):
        if t.GetClass() == "PCB_TRACK" and ({tuple(round(v, 2) for v in L(t.GetStart())),
                                             tuple(round(v, 2) for v in L(t.GetEnd()))} & ends):
            board.Delete(t)
            n += 1
    print(f"PCB: {n} tracks to the old J1 pads removed")


def remove_blocking_vias(board, pads):
    """Delete the GND stitch vias that touch the new pads or the new track.

    Why only GND: the area has only stitch vias. A via of another net here is
    a mistake in this script, so stop."""
    for v in list(board.GetTracks()):
        if v.GetClass() != "PCB_VIA":
            continue
        at = L(v.GetPosition())
        near_pad = any(math.hypot(at[0] - px, at[1] - py) < VIA_KEEP_PAD for px, py in pads)
        near_trk = any(seg_dist(at, a, b) < VIA_KEEP_TRACK for a, b in zip(NEW_ROUTE, NEW_ROUTE[1:]))
        if near_pad or near_trk:
            if v.GetNetname() != "GND":
                sys.exit(f"via of net {v.GetNetname()} at {at} is in the way of the new J1")
            board.Delete(v)
            print(f"PCB: GND stitch via at ({at[0]:.2f}, {at[1]:.2f}) removed")


def move_pps_label(board):
    """Move the J8 "PPS" label out from under the J1 body (see PPS_LABEL_NEW)."""
    for d in board.GetDrawings():
        if (isinstance(d, pcbnew.PCB_TEXT) and d.GetText() == "PPS"
                and math.dist(L(d.GetPosition()), PPS_LABEL_OLD) < 0.05):
            d.SetPosition(P(*PPS_LABEL_NEW))
            d.SetTextAngleDegrees(0)
            return
    sys.exit(f'"PPS" label not found at {PPS_LABEL_OLD}')


def apply_board(board, swap_footprint):
    """Swap J1 on the board, place it, and connect pin 1.

    swap_footprint: make_revc.swap_footprint. Why a parameter: make_revc
    imports this module, so this module does not import make_revc at load
    time. Return the new J1, or None when J1 is already changed."""
    old = find_j1(board)
    if lib_id(old) == NEW_FP:
        print("PCB: J1 is already the XH part, nothing to do")
        return None
    if lib_id(old) != OLD_FP:
        sys.exit(f"J1 has footprint {lib_id(old)}, expected {OLD_FP}")
    remove_old_pad_tracks(board, old)
    # The net of the new track is the net of the old route it joins. (A second
    # call of old.Pads() can give a raw SWIG pointer in KiCad 10.)
    net5 = next(t.GetNet() for t in board.GetTracks() if t.GetClass() == "PCB_TRACK"
                and OLD_ROUTE_JOIN in (tuple(round(v, 2) for v in L(t.GetStart())),
                                       tuple(round(v, 2) for v in L(t.GetEnd()))))
    new = swap_footprint(board, old, NEW_FP)
    new.SetField("MPN", NEW_MPN)
    # swap_footprint copies the attributes of the old part, which was SMD. The
    # new part is through-hole: it must not be in the pick-and-place file.
    new.SetAttributes((new.GetAttributes() & ~pcbnew.FP_SMD) | pcbnew.FP_THROUGH_HOLE)
    new.SetPosition(P(*J1_PIN1))
    new.SetOrientationDegrees(J1_ROT)
    new.Reference().SetPosition(P(*J1_LABEL))
    new.Reference().SetTextAngleDegrees(0)
    pads = [L(p.GetPosition()) for p in new.Pads()]
    remove_blocking_vias(board, pads)
    move_pps_label(board)
    for a, b in zip(NEW_ROUTE, NEW_ROUTE[1:]):
        t = pcbnew.PCB_TRACK(board)
        t.SetStart(P(*a)); t.SetEnd(P(*b)); t.SetWidth(pcbnew.FromMM(ROUTE_W))
        t.SetLayer(pcbnew.F_Cu); t.SetNet(net5)
        board.Add(t)
    print(f"PCB: J1 is now {NEW_FP} at pin 1 {J1_PIN1}, rotation {J1_ROT}")
    return new


def move_input_text(board):
    """Move the existing "+5V IN / 5.5V MAX" texts to the silk_art.py position.

    make_revc.py makes these texts new from silk_art.FRONT_TEXT. A board made
    before this change has them at the old place, left of the old J1."""
    from silk_art import FRONT_TEXT
    want = {t: at for t, at, _ in FRONT_TEXT if t in ("+5V IN", "5.5V MAX")}
    for d in board.GetDrawings():
        if isinstance(d, pcbnew.PCB_TEXT) and d.GetText() in want:
            d.SetPosition(P(*want[d.GetText()]))


def main():
    """Change J1 on the rev C files as they are now (see the module docstring)."""
    import make_revc
    from silk_refs import place_labels
    board = pcbnew.LoadBoard(PCB)
    if apply_board(board, make_revc.swap_footprint) is None:
        return
    move_input_text(board)
    hidden = place_labels(board)        # the labels near the new J1 must stay clear
    if hidden:
        print("no clear spot, label hidden: " + " ".join(hidden))
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    board.Save(PCB)
    apply_schematic(SCH)


if __name__ == "__main__":
    main()
