"""Blade trays (laser): `LZR_tray_sdr` and `LZR_tray_nuc`.

Mirror of `tray_local(kind)` in heron_rack.py. Local frame: x = world Y,
y = world Z, sheet thickness along local z (0..T). The SDR tray is solid
(copper tape shield between SDRs) and has four board holes. The NUC tray has
four board holes and a window grid. Both have the pull tab with a finger hole.

Order of features:
  1. tray sheet
  2. pull tab (join), because the finger hole cuts through the tab
  3. finger hole and board holes (one cut)
  4. NUC only: window grid
"""
import fusion_lib as fl

SDR = "LZR_tray_sdr"
NUC = "LZR_tray_nuc"
CATEGORY = "laser"
SDR_HOLES = 4      # number of SDR_HOLE<k>_BX/_BY parameter pairs in the seed
NUC_HOLES = 4      # number of NUC_HOLE<k>_X/_Z parameter pairs in the seed


def _board_holes(kind: str):
    """Return the (x, y) hole centres in tray local axes as expressions.

    SDR: heron_rack holes = (Y_BOARD - by, Z_BOARD + bx).
    NUC: heron_rack holes = (Y_BOARD + hx, Z_BOARD + hz).
    """
    if kind == "nuc":
        return [(f"Y_BOARD + NUC_HOLE{k}_X", f"Z_BOARD + NUC_HOLE{k}_Z") for k in range(1, NUC_HOLES + 1)]
    return [(f"Y_BOARD - SDR_HOLE{k}_BY", f"Z_BOARD + SDR_HOLE{k}_BX") for k in range(1, SDR_HOLES + 1)]


def build(ctx, kind: str):
    """Build the SDR tray (kind 'sdr') or the NUC tray (kind 'nuc'). Return the component."""
    nuc = kind == "nuc"
    y1 = "NUC_TRAY_Y1" if nuc else "TRAY_Y1"
    comp = fl.new_component(ctx, NUC if nuc else SDR, CATEGORY)
    sheet = fl.plane_sketch(comp, "outline")
    fl.sk_rect(ctx, sheet, "TRAY_Y0", "TRAY_Z0", y1, "TRAY_Z1")
    fl.extrude(comp, sheet, "T_", op="new", name="tray_sheet")
    # front pull tab: a rounded rectangle that overlaps the sheet front edge
    tab = fl.plane_sketch(comp, "pull_tab")
    fl.sk_rounded_rect(ctx, tab, "PULL_TAB_CX - PULL_TAB_W / 2", "PULL_TAB_CY - PULL_TAB_H / 2",
                       "PULL_TAB_CX + PULL_TAB_W / 2", "PULL_TAB_CY + PULL_TAB_H / 2", "PULL_TAB_R")
    fl.extrude(comp, tab, "T_", op="join", name="pull_tab")
    # finger hole and board holes
    cuts = fl.plane_sketch(comp, "holes")
    fl.sk_circle(ctx, cuts, "FINGER_HOLE_CX", "FINGER_HOLE_CY", "FINGER_HOLE_D")
    for cx, cy in _board_holes(kind):
        fl.sk_circle(ctx, cuts, cx, cy, "M3_CLR_D")
    fl.extrude(comp, cuts, None, op="cut", through=True, name="holes")
    if nuc:
        fl.grid_windows(ctx, comp, "tray_windows", "TRAY_WIN_X0", "TRAY_WIN_Z0",
                        f"{y1} - TRAY_WIN_REAR_GAP", "H_ - TRAY_WIN_Z_TOP_GAP",
                        "TRAY_WIN_NX", "TRAY_WIN_NY", "TRAY_WIN_RIB", "GRID_WIN_R")
    return comp
