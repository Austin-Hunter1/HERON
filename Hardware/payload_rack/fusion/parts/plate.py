"""Bottom plate and top plate (laser): `LZR_bottom_plate`, `LZR_top_plate`.

Mirror of `plate_local(top)` in heron_rack.py. Local frame: x = world X
(-T..W+T), y = world Y (0..D), sheet thickness along z (0..T).
Both plates share the hole pattern. The top plate also has the SSD hatch and
the hatch-latch pivot hole.

Order of features (it matters):
  1. slab
  2. airflow windows (cut), then the SSD cage keep-out patch (join).
     CadQuery cuts "windows minus keep box". The patch puts the keep box
     material back, which gives the same result.
  3. all holes and slots (one cut), including the cage screws and the hatch.
     They come after the patch, so the patch never fills them.
Window keep tests (skip a window that is too narrow, grid size test) run at
build time with the current parameter values.
"""
import fusion_lib as fl

BOTTOM = "LZR_bottom_plate"
TOP = "LZR_top_plate"
CATEGORY = "laser"


def _windows(ctx, comp) -> None:
    """Cut the standard airflow windows between the strips and the two grids."""
    n = ctx.num
    names = [s["name"] for s in ctx.slots]
    edges = ["0 mm"]
    for name in names:
        edges += [f"TRAY_X_{name} - STRIP_W / 2", f"TRAY_X_{name} + STRIP_W / 2"]
    edges.append("W_")
    sk = fl.plane_sketch(comp, "airflow_windows")
    count = 0
    for k in range(0, len(edges), 2):
        a, b = f"({edges[k]}) + PLATE_WIN_EDGE", f"({edges[k + 1]}) - PLATE_WIN_EDGE"
        if n(b) - n(a) < n("PLATE_WIN_MIN_W"):
            continue
        right_of_nuc = k == 2 and ctx.slots[0]["kind"] == "nuc"
        ranges = ((("PLATE_WIN_NUCR_A_Y0", "PLATE_WIN_NUCR_A_Y1"), ("PLATE_WIN_NUCR_B_Y0", "PLATE_WIN_NUCR_B_Y1"))
                  if right_of_nuc else
                  (("PLATE_WIN_A_Y0", "PLATE_WIN_A_Y1"), ("PLATE_WIN_B_Y0", "PLATE_WIN_B_Y1")))
        for y0, y1 in ranges:
            fl.sk_rounded_rect(ctx, sk, a, y0, b, y1, "PLATE_WIN_R")
            count += 1
    if count:
        fl.extrude(comp, sk, None, op="cut", through=True, name="airflow_windows")
    # behind the short NUC blade (NUC cables and SSD bay)
    nuc_x1 = f"TRAY_X_{names[1]} - STRIP_W / 2 - NUCBEHIND_X1_GAP"
    fl.grid_windows(ctx, comp, "windows_behind_nuc", "NUCBEHIND_X0", "NUC_STRIP_Y1 + NUCBEHIND_Y_OFF",
                    nuc_x1, "STRIP_Y1", "NUCBEHIND_NX", "NUCBEHIND_NY", "NUCBEHIND_RIB", "GRID_WIN_R")
    # full width area behind the strips (cables and exhaust)
    fl.grid_windows(ctx, comp, "windows_rear_area", "REARAREA_MARGIN_X", "STRIP_Y1 + REARAREA_Y_OFF",
                    "W_ - REARAREA_MARGIN_X", "REAR_Y0 - REARAREA_Y_OFF", "REARAREA_NX", "REARAREA_NY",
                    "REARAREA_RIB", "GRID_WIN_R")


def _keep_patch(ctx, comp) -> None:
    """Put the plate material back under the SSD cage (ssd_cage_keep)."""
    sk = fl.plane_sketch(comp, "ssd_keep")
    fl.sk_rect(ctx, sk, "SSD_KEEP_X0", "SSD_KEEP_Y0", "SSD_KEEP_X1", "SSD_KEEP_Y1")
    fl.extrude(comp, sk, "T_", op="join", name="ssd_keep_patch")


def _holes(ctx, comp, top: bool) -> None:
    """Cut the wall joints, rear plate joints, strip screws, cage screws, and the hatch."""
    sk = fl.plane_sketch(comp, "holes_and_slots")
    # side wall tab slots and screw holes (wall centre plane xw)
    for xw in ("-T_ / 2", "W_ + T_ / 2"):
        for k in (1, 2, 3):
            ty = f"WALL_TAB_Y{k}"
            fl.sk_rect(ctx, sk, f"{xw} - T_ / 2 - KERF", f"{ty} - TAB_L / 2 - KERF",
                       f"{xw} + T_ / 2 + KERF", f"{ty} + TAB_L / 2 + KERF")
        for k in (1, 2):
            fl.sk_circle(ctx, sk, xw, f"WALL_SCREW_Y{k}", "M3_CLR_D")
    # rear plate tab slots and screw hole
    for k in (1, 2):
        tx = f"REAR_TAB_X{k}"
        fl.sk_rect(ctx, sk, f"{tx} - TAB_L / 2 - KERF", "REAR_Y0 - KERF",
                   f"{tx} + TAB_L / 2 + KERF", "REAR_Y0 + T_ + KERF")
    fl.sk_circle(ctx, sk, "REAR_SCREW_X1", "REAR_Y0 + T_ / 2", "M3_CLR_D")
    # strip screws: three on each ear column of each slot
    for slot in ctx.slots:
        prefix = "NUC_EAR" if slot["kind"] == "nuc" else "EAR"
        xs = f"TRAY_X_{slot['name']} + STRIP_W / 2 + EAR_W / 2"
        for k in (1, 2, 3):
            hy = "EAR_HOLE0_Y" if k == 1 else f"({prefix}{k}_Y0 + {prefix}{k}_Y1) / 2"
            fl.sk_circle(ctx, sk, xs, hy, "M3_CLR_D")
    # SSD cage screws
    for x in ("SSD_XL", "SSD_XR"):
        for sign in ("-", "+"):
            fl.sk_circle(ctx, sk, x, f"SSD_YM {sign} SSD_CAGE_HOLE_DY", "M3_CLR_D")
    if top:
        fl.sk_rect(ctx, sk, "SSD_SX0 - SSD_HATCH_CLR", "SSD_Y0 - SSD_HATCH_CLR",
                   "SSD_SX1 + SSD_HATCH_CLR", "SSD_GY1 + SSD_HATCH_CLR")
        fl.sk_circle(ctx, sk, "SSD_LATCH_X", "SSD_LATCH_Y", "M3_CLR_D")
    fl.extrude(comp, sk, None, op="cut", through=True, name="holes_and_slots")


def build(ctx, top: bool = False):
    """Build the bottom plate (top=False) or the top plate (top=True)."""
    comp = fl.new_component(ctx, TOP if top else BOTTOM, CATEGORY)
    slab = fl.plane_sketch(comp, "outline")
    fl.sk_rect(ctx, slab, "-T_", "0 mm", "W_ + T_", "D")
    fl.extrude(comp, slab, "T_", op="new", name="plate_sheet")
    _windows(ctx, comp)
    _keep_patch(ctx, comp)
    _holes(ctx, comp, top)
    return comp
