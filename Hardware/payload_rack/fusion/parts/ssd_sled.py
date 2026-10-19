"""SSD sled (rigid, printed): `ASA_ssd_sled`.

Mirror of `ssd_sled()` in heron_rack.py. There is no `*_local` function, so
the component uses the WORLD (CadQuery) axes. The joint offset is the identity,
plus `RACK_Z0` in Z. The sled plate lies on the drive hole face (the -X face of
the drive). A handle block above the drive reaches up to the hatch of the top
plate. A cord hole goes through the handle. Four stadium slots take the M3
flat-head screws (no countersink is modelled). A window lightens the plate.

Order of features: 1. plate  2. handle (join)  3. cord hole (cut)
4. screw slots (cut)  5. lightening window (cut).

Sketch planes: the cuts go along X, so the sketches lie on offset copies of the
YZ plane. On the YZ plane Fusion puts sketch (x, y) at world (-Z, Y) (found by
test). So a world Z value is negated, like on the XZ plane. The slots and the
window start on the outer (+X) face of the plate and cut toward -X. The cord
hole starts on the -X face of the handle and cuts toward +X. Both go through
everything on the sled, which is the same as the CadQuery 1 mm overshoot.
"""
import fusion_lib as fl

NAME = "ASA_ssd_sled"
CATEGORY = "rigid"
HOLE_COUNT = 2   # number of SSD_HOLE<k>_Y0/_Y1 pairs in the seed (SSD_HOLE_Y in heron_rack.py)


def build(ctx):
    """Build the sled component in world axes."""
    comp = fl.new_component(ctx, NAME, CATEGORY)
    yz = comp.yZConstructionPlane

    # 1. sled plate: from the connector edge to 0.3 mm above the drive top edge
    plane = fl.offset_plane(comp, "plane_plate", "SSD_ZD0")
    sk = fl.plane_sketch(comp, "plate", plane)
    fl.sk_rect(ctx, sk, "SSD_SX0", "SSD_Y0", "SSD_SX0 + SLED_T", "SSD_GY1")
    fl.extrude(comp, sk, "SSD_ZD1 - SSD_ZD0 + SLED_GAP", op="new", name="sled_plate")

    # 2. handle block above the drive; it fills the channel width
    plane = fl.offset_plane(comp, "plane_handle", "SSD_ZD1 + SLED_GAP")
    sk = fl.plane_sketch(comp, "handle", plane)
    fl.sk_rect(ctx, sk, "SSD_SX0", "SSD_Y0 + SLED_HANDLE_Y_INSET", "SSD_SX1", "SSD_GY1 - SLED_HANDLE_Y_INSET")
    fl.extrude(comp, sk, "H_ + T_ - SLED_TOP_GAP - (SSD_ZD1 + SLED_GAP)", op="join", name="handle_block")

    # 3. cord hole through the handle (a pull loop goes here)
    plane = fl.offset_plane(comp, "plane_x_in", "SSD_SX0", yz)
    sk = fl.plane_sketch(comp, "cord_hole", plane)
    zlo, zhi = fl.flip_range("SSD_ZD1 + SLED_CORD_Z0", "SSD_ZD1 + SLED_CORD_Z1")
    fl.sk_rect(ctx, sk, zlo, "SSD_Y0 + SLED_CORD_Y_INSET", zhi, "SSD_GY1 - SLED_CORD_Y_INSET")
    fl.extrude(comp, sk, None, op="cut", through=True, name="cord_hole")

    # 4. screw slots: along world Z (sketch x), two rows of two. They cover both SFF-8201 hole positions.
    plane = fl.offset_plane(comp, "plane_x_out", "SSD_SX0 + SLED_T", yz)
    sk = fl.plane_sketch(comp, "screw_slots", plane)
    for k in range(1, HOLE_COUNT + 1):
        a, b = f"SSD_HOLE{k}_Y0", f"SSD_HOLE{k}_Y1"
        zc = fl.flip_pos(f"SSD_ZD0 + ({a} + {b}) / 2")
        for yy in ("SSD_Y0 + SSD_HOLE_IN", "SSD_GY1 - SSD_HOLE_IN"):
            fl.sk_slot(ctx, sk, zc, yy, f"({b} - {a}) + SLED_SLOT_D", "SLED_SLOT_D")
    fl.extrude(comp, sk, None, op="cut", through=True, flip=True, name="screw_slots")

    # 5. lightening window between the screw rows
    sk = fl.plane_sketch(comp, "window", plane)
    zlo, zhi = fl.flip_range("SSD_ZD0 + SLED_LIGHT_Z_INSET", "SSD_ZD1 - SLED_LIGHT_Z_INSET")
    fl.sk_rect(ctx, sk, zlo, "SSD_Y0 + SLED_LIGHT_Y_INSET", zhi, "SSD_GY1 - SLED_LIGHT_Y_INSET")
    fl.extrude(comp, sk, None, op="cut", through=True, flip=True, name="lightening_window")
    return comp
