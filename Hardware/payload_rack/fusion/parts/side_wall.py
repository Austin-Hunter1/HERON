"""Side wall part (laser): `LZR_side_wall`. One component, two occurrences (L and R).

Why: both walls are the same sheet part. Mirror of `side_wall_local` in
heron_rack.py. Local frame: x = world Y (0..D), y = world Z (0..H), sheet
thickness along local z (0..T). Tabs reach to -T and H + T in local y.
Cuts: T-slots for the plate screws, rear plate tab slots, one rear plate
screw hole, and the airflow window grid.
"""
import fusion_lib as fl
from parts.common import sk_tslot

NAME = "LZR_side_wall"
CATEGORY = "laser"


def build(ctx):
    """Build the side wall component in its local frame and return it."""
    comp = fl.new_component(ctx, NAME, CATEGORY)
    body = fl.plane_sketch(comp, "outline")
    fl.sk_rect(ctx, body, "0 mm", "0 mm", "D", "H_")
    fl.extrude(comp, body, "T_", op="new", name="wall_sheet")
    # tabs on the bottom and top edge
    tabs = fl.plane_sketch(comp, "tabs")
    for k in (1, 2, 3):
        ty = f"WALL_TAB_Y{k}"
        fl.sk_rect(ctx, tabs, f"{ty} - TAB_L / 2", "-T_", f"{ty} + TAB_L / 2", "0 mm")
        fl.sk_rect(ctx, tabs, f"{ty} - TAB_L / 2", "H_", f"{ty} + TAB_L / 2", "H_ + T_")
    fl.extrude(comp, tabs, "T_", op="join", name="wall_tabs")
    # cuts: T-slots, rear plate tab slots, rear plate screw hole
    cuts = fl.plane_sketch(comp, "cuts")
    for k in (1, 2):
        sk_tslot(ctx, cuts, f"WALL_SCREW_Y{k}", "0 mm", +1)
        sk_tslot(ctx, cuts, f"WALL_SCREW_Y{k}", "H_", -1)
    for k in (1, 2):
        tz = f"REAR_SIDE_TAB_Z{k}"
        fl.sk_rect(ctx, cuts, "REAR_Y0 - KERF", f"{tz} - REAR_SIDE_TAB_L / 2 - KERF",
                   "REAR_Y0 + T_ + KERF", f"{tz} + REAR_SIDE_TAB_L / 2 + KERF")
    fl.sk_circle(ctx, cuts, "REAR_Y0 + T_ / 2", "REAR_SIDE_SCREW_Z1", "M3_CLR_D")
    fl.extrude(comp, cuts, None, op="cut", through=True, name="wall_cuts")
    fl.grid_windows(ctx, comp, "wall_windows", "SW_WIN_X0", "SW_WIN_Z0", "REAR_Y0 - SW_WIN_REAR_GAP",
                    "H_ - SW_WIN_Z_TOP_GAP", "SW_WIN_NX", "SW_WIN_NY", "SW_WIN_RIB", "GRID_WIN_R")
    return comp
