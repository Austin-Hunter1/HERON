"""TPU C-rail parts (flex): `TPU_rail_sdr` and `TPU_rail_nuc`.

Why: the rail clips on a tray edge and slides in the printed strip. Both rails
share one profile and differ only in length. Mirror of `tpu_rail_local` in
heron_rack.py. Local frame: x across (centred), y along the rail, z up from
the rail back. The profile sits on the XZ plane and is extruded along +Y.
"""
import fusion_lib as fl

CATEGORY = "flex"
# name -> length parameter (names are seed parameters)
VARIANTS = {"TPU_rail_sdr": "RAIL_L_SDR", "TPU_rail_nuc": "RAIL_L_NUC"}


def build(ctx, name: str):
    """Build one rail variant and return its component."""
    length = VARIANTS[name]
    comp = fl.new_component(ctx, name, CATEGORY)
    xz = comp.xZConstructionPlane
    # back plate: x -RAIL_W/2..RAIL_W/2, z 0..RAIL_BACK
    base = fl.plane_sketch(comp, "base", xz)
    _rect_xz(ctx, base, "-RAIL_W / 2", "0 mm", "RAIL_W / 2", "RAIL_BACK")
    fl.extrude(comp, base, length, op="new", name="rail_base")
    # two lips on top of the back plate
    lips = fl.plane_sketch(comp, "lips", xz)
    _rect_xz(ctx, lips, "-RAIL_W / 2", "RAIL_BACK", "-RAIL_W / 2 + RAIL_LIP", "RAIL_BACK + RAIL_GRIP")
    _rect_xz(ctx, lips, "RAIL_W / 2 - RAIL_LIP", "RAIL_BACK", "RAIL_W / 2", "RAIL_BACK + RAIL_GRIP")
    fl.extrude(comp, lips, length, op="join", name="rail_lips")
    return comp


def _rect_xz(ctx, sk, x0, z0, x1, z1):
    """Draw a rectangle in X and Z on an XZ-plane sketch."""
    y0, y1 = fl.flip_range(z0, z1)
    fl.sk_rect(ctx, sk, x0, y0, x1, y1)
