"""SSD cage (rigid, printed): `ASA_ssd_cage`.

Mirror of `ssd_cage()` in heron_rack.py. This part has no `*_local`
function, so the component uses the WORLD (CadQuery) axes. The joint offset is
the identity, plus `RACK_Z0` in Z (the bottom plate is grounded at the Fusion
origin). The cage is a base block with two U-channel guides that hold the long
edges of the drive and the sled. Tie ribs join the guides at two heights.
Flanges at the bottom and ears at the top take the M3 screws of the plates.

Order of features (the same as the CadQuery order, for the same result):
  1. base block         2. guides (outer shape), then the channel cut
  3. tie ribs, two heights            4. bottom flanges
  5. top ears           6. insert holes, bottom and top
  7. receptacle pocket (open toward -Y)     8. base lightening pocket

Change from heron_rack.py (D-035, 2026-10-14): in heron_rack.py the two top
ears span `SSD_YM +/- SSD_FLANGE_HALF`, the same as the bottom flanges. There
they touch no other part of the cage (a 4.9 mm gap to each guide), so the
printed cage would be 3 loose pieces. Here the top ears span the full cage
length, `SSD_EAR_Y0` to `SSD_EAR_Y1` (= `SSD_FY0` to `SSD_FY1`). So each ear
joins the outer face of both guides, and the cage is one body. The screw
holes and the bottom flanges do not change. The CadQuery 0.01 mm overlaps of the
flanges with the base are not copied: the faces touch, and the volume is the
same. The lightening pocket cut goes 1 mm outside the base in X, so it nicks
the two bottom flanges (3 mm to 4 mm high). The cut copies this.
"""
import fusion_lib as fl

NAME = "ASA_ssd_cage"
CATEGORY = "rigid"

# Top of the guides, the ears and the flanges are measured from the interior height.
_TOP = "H_ - SSD_CAGE_TOP_GAP"


def _screw_circles(ctx, sk) -> None:
    """Sketch the four insert circles (screw columns XL and XR, rows YM -/+ DY)."""
    for x in ("SSD_XL", "SSD_XR"):
        for sign in ("-", "+"):
            fl.sk_circle(ctx, sk, x, f"SSD_YM {sign} SSD_CAGE_HOLE_DY", "INSERT_D")


def _flange_rects(ctx, sk, y0: str, y1: str) -> None:
    """Sketch the two flange or ear footprints (left of the cage and right of it).

    y0, y1: the Y span. The bottom flanges and the top ears have different spans.
    """
    fl.sk_rect(ctx, sk, "SSD_XL - SSD_FLANGE_OVER", y0, "SSD_OX0", y1)
    fl.sk_rect(ctx, sk, "SSD_OX1", y0, "SSD_XR + SSD_FLANGE_OVER", y1)


def build(ctx):
    """Build the cage component in world axes."""
    comp = fl.new_component(ctx, NAME, CATEGORY)

    # 1. base block
    sk = fl.plane_sketch(comp, "base")
    fl.sk_rect(ctx, sk, "SSD_OX0", "SSD_FY0", "SSD_OX1", "SSD_FY1")
    fl.extrude(comp, sk, "SSD_BASE", op="new", name="base_block")

    # 2. guides: a full block at each Y end, then the channel is cut out of it.
    # The cut starts on the base top face and goes up, so the base stays whole.
    plane = fl.offset_plane(comp, "plane_base_top", "SSD_BASE")
    sk = fl.plane_sketch(comp, "guides", plane)
    fl.sk_rect(ctx, sk, "SSD_OX0", "SSD_FY0", "SSD_OX1", "SSD_Y0 + SSD_GUIDE_T")
    fl.sk_rect(ctx, sk, "SSD_OX0", "SSD_GY1 - SSD_GUIDE_T", "SSD_OX1", "SSD_FY1")
    fl.extrude(comp, sk, f"{_TOP} - SSD_BASE", op="join", name="guides")
    sk = fl.plane_sketch(comp, "channel", plane)
    fl.sk_rect(ctx, sk, "SSD_CX0", "SSD_Y0 - SSD_CLR", "SSD_CX1", "SSD_Y0 + SSD_GUIDE_T + SSD_GUIDE_CUT_EXTRA")
    fl.sk_rect(ctx, sk, "SSD_CX0", "SSD_GY1 - SSD_GUIDE_T - SSD_GUIDE_CUT_EXTRA", "SSD_CX1", "SSD_GY1 + SSD_CLR")
    fl.extrude(comp, sk, None, op="cut", through=True, name="channel_cut")

    # 3. tie ribs outside the channel, at two heights
    for k in (1, 2):
        plane = fl.offset_plane(comp, f"plane_rib{k}", f"SSD_RIB_Z{k}")
        sk = fl.plane_sketch(comp, f"rib{k}", plane)
        y0, y1 = "SSD_Y0 + SSD_RIB_Y_INSET", "SSD_GY1 - SSD_RIB_Y_INSET"
        fl.sk_rect(ctx, sk, "SSD_OX0", y0, "SSD_CX0", y1)
        fl.sk_rect(ctx, sk, "SSD_CX1", y0, "SSD_OX1", y1)
        fl.extrude(comp, sk, "SSD_RIB_T", op="join", name=f"tie_ribs_{k}")

    # 4. bottom flanges (the plate screws go through them)
    sk = fl.plane_sketch(comp, "flanges")
    _flange_rects(ctx, sk, "SSD_YM - SSD_FLANGE_HALF", "SSD_YM + SSD_FLANGE_HALF")
    fl.extrude(comp, sk, "SSD_FLANGE_T", op="join", name="bottom_flanges")

    # 5. top ears: full cage length, so they join both guides (see the module text)
    plane = fl.offset_plane(comp, "plane_ears", "H_ - SSD_EAR_Z_OFF")
    sk = fl.plane_sketch(comp, "ears", plane)
    _flange_rects(ctx, sk, "SSD_EAR_Y0", "SSD_EAR_Y1")
    fl.extrude(comp, sk, "SSD_EAR_Z_OFF - SSD_CAGE_TOP_GAP", op="join", name="top_ears")

    # 6. heat-set insert holes: four from the bottom, four from the top
    sk = fl.plane_sketch(comp, "insert_holes_bottom")
    _screw_circles(ctx, sk)
    fl.extrude(comp, sk, "SSD_INSERT_DEPTH", op="cut", name="insert_holes_bottom")
    plane = fl.offset_plane(comp, "plane_insert_top", "H_ - SSD_INSERT_TOP_Z_OFF")
    sk = fl.plane_sketch(comp, "insert_holes_top", plane)
    _screw_circles(ctx, sk)
    fl.extrude(comp, sk, "SSD_INSERT_DEPTH", op="cut", name="insert_holes_top")

    # 7. receptacle pocket, centred on the connector, open toward -Y (the NUC)
    plane = fl.offset_plane(comp, "plane_pocket", "SSD_POCKET_Z0")
    sk = fl.plane_sketch(comp, "pocket", plane)
    fl.sk_rect(ctx, sk, "SSD_PC - SSD_CONN_H / 2", "SSD_FY0 - SSD_POCKET_Y_OVER",
               "SSD_PC + SSD_CONN_H / 2", "SSD_CCY + SSD_CONN_W / 2")
    fl.extrude(comp, sk, "SSD_BASE + SSD_POCKET_OVER - SSD_POCKET_Z0", op="cut", name="receptacle_pocket")

    # 8. lighten the base behind the pocket (the cut reaches past the base in X)
    plane = fl.offset_plane(comp, "plane_lighten", "SSD_LIGHTEN_Z0")
    sk = fl.plane_sketch(comp, "lighten", plane)
    fl.sk_rect(ctx, sk, "SSD_OX0 - SSD_LIGHTEN_X_OVER", "SSD_CCY + SSD_CONN_W / 2 + SSD_LIGHTEN_Y0",
               "SSD_OX1 + SSD_LIGHTEN_X_OVER", "SSD_FY1 - SSD_LIGHTEN_Y1")
    fl.extrude(comp, sk, "SSD_BASE - SSD_LIGHTEN_Z_TOP - SSD_LIGHTEN_Z0", op="cut", name="base_lightening")
    return comp
