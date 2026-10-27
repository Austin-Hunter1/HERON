"""Replace the HERON logo on the rev D board as it is now.

Why: a full gen_pcb.py run routes the board again with Freerouting, and
the copper can change on each run (README). A logo change must not change
the copper. This script deletes only the logo polygons in the
HERON_SILK_ART group and adds new ones from logo_polys.json, at the
positions and sizes in gen_pcb.py (FRONT_LOGO, BACK_LOGO). Texts, tracks,
zones and parts do not change.

Steps after a logo change:

    <KiCad 10>/bin/python3 extract_logo.py      # SVG -> logo_polys.json
    <KiCad 10>/bin/python3 update_logo.py       # logo_polys.json -> board
    EXPORT_ONLY=1 ... bash make_fab.sh          # DRC and fab outputs

Run it with the KiCad 10 Python (it needs pcbnew), from this folder.
"""

import sys

import pcbnew

from gen_pcb import PCB, logo_shapes

GROUP_NAME = "HERON_SILK_ART"


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else PCB
    board = pcbnew.LoadBoard(path)
    group = next((g for g in board.Groups() if g.GetName() == GROUP_NAME), None)
    if group is None:
        sys.exit(f"{path}: no group {GROUP_NAME}. Run gen_pcb.py first.")
    # The logo is the only set of filled polygons in the group; the other items are texts.
    old = [it for it in group.GetItems()
           if it.GetClass() == "PCB_SHAPE" and it.GetShape() == pcbnew.SHAPE_T_POLY]
    for it in old:
        board.Delete(it)       # board.Delete, not Remove: Remove crashes the next save (macOS, KiCad 10)
    new = logo_shapes(board)
    for it in new:
        group.AddItem(it)
    pcbnew.SaveBoard(path, board)
    print(f"logo polygons: {len(old)} deleted, {len(new)} added: {path}")


if __name__ == "__main__":
    main()
