"""Give D2 (Panjit SS1030HEWS, SOD-323HE) its own land pattern (D-041).

Why: D2 had the stock SOD-323F land, two 0.5 x 0.5 mm pads 2.2 mm apart.
The SOD-323HE cathode is a large tab under the body (1.15-1.55 mm long).
The small pad caught only its outer end, and the F.Cu GND pour ran under the
rest of the tab: the 5 V input after F1 was then insulated from GND only by
solder mask. The Panjit land (datasheet rev 06, page 3) puts a 1.85 x 1.1 mm
cathode pad under the tab, so the pour moves away.

What the script changes:
- Schematic: the D2 footprint property.
- PCB: D2 gets HERON_Clock:D_SOD-323HE_Panjit (made by gen_lib.py) at the
  same position and rotation. The origin of both footprints is the land
  centre and pad 1 is the cathode in both, so the polarity and the two
  tracks stay. The old pad centres are inside the new pads.

Two ways to run it:
- `make_revc.py` calls `apply_schematic` and `apply_board`.
- Alone, on the rev C files as they are now:

      <KiCad 10>/bin/python3 d2_land.py

  It writes the footprint file first (gen_lib.write_sod323he). A second run
  finds D2 already changed and stops. Set KICAD10_FOOTPRINT_DIR when the
  stock footprints are not in the default place (make_revc.py needs it).
"""

import os
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
SCH = os.path.join(HERE, "project", "heron_clock.kicad_sch")
PCB = os.path.join(HERE, "project", "heron_clock.kicad_pcb")

REF = "D2"
OLD_FP = "Diode_SMD:D_SOD-323F"
NEW_FP = "HERON_Clock:D_SOD-323HE_Panjit"


def apply_schematic(path):
    """Change the D2 footprint property. The old footprint name occurs only on D2."""
    text = open(path, encoding="utf-8").read()
    n = text.count(f'"{OLD_FP}"')
    if n > 1:
        sys.exit(f"{OLD_FP} is on {n} symbols, expected only D2")
    text = text.replace(f'"{OLD_FP}"', f'"{NEW_FP}"')
    open(path, "w", encoding="utf-8", newline="\n").write(text)
    print(f"schematic: {n} D2 footprint property changed")
    return n


def apply_board(board, swap_footprint):
    """Swap the D2 footprint in place. Return the new D2, or None when it is already changed.

    swap_footprint: make_revc.swap_footprint (see power_conn.apply_board for why)."""
    import gen_lib
    gen_lib.write_sod323he()
    old = next(fp for fp in board.GetFootprints() if fp.GetReference() == REF)
    lib_id = f"{old.GetFPID().GetLibNickname()}:{old.GetFPID().GetLibItemName()}"
    if lib_id == NEW_FP:
        print("PCB: D2 already has the SOD-323HE land, nothing to do")
        return None
    if lib_id != OLD_FP:
        sys.exit(f"D2 has footprint {lib_id}, expected {OLD_FP}")
    ref_pos, ref_vis = old.Reference().GetPosition(), old.Reference().IsVisible()
    new = swap_footprint(board, old, NEW_FP)
    new.Reference().SetPosition(ref_pos)     # keep the label that silk_refs.py placed
    new.Reference().SetVisible(ref_vis)
    print(f"PCB: D2 is now {NEW_FP}")
    return new


def main():
    """Change D2 on the rev C files as they are now (see the module docstring)."""
    import make_revc
    from silk_refs import place_labels
    board = pcbnew.LoadBoard(PCB)
    if apply_board(board, make_revc.swap_footprint) is None:
        return
    hidden = place_labels(board)        # the larger D2 silk must not cover a label
    if hidden:
        print("no clear spot, label hidden: " + " ".join(hidden))
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    board.Save(PCB)
    apply_schematic(SCH)


if __name__ == "__main__":
    main()
