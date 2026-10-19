"""Swing latches (rigid, printed): `ASA_latch_sdr_preload` and `ASA_latch`.

Mirror of `latch_local(preload)` in heron_rack.py. Local frame: x along the arm
(from the arm start), y across the arm (centred), z = thickness `T_`, from the
rear face (z = 0). The arm plate has a pivot hole and a grip nub on the front
face. The SDR latch (`preload`) is longer. It also has a boss with a heat-set
insert pocket and a clearance hole for the preload thumbscrew.

The top latches use the SAME components as the bottom latches (D-035,
2026-10-14). Why: the latch is symmetric about local y = 0. So `mirror_top`
of a latch equals the bottom latch moved by `H_ - 2 * LATCH_Z` in Z, not a
mirror image. The README print list also has no mirrored latches
(latch_sdr_preload x8, latch x3). One component for each latch type means
one edit changes every copy.

Order of features:
  1. arm plate        2. boss (preload only)    3. pivot hole (cut)
  4. insert pocket and screw clearance hole (preload only, cuts)
  5. grip nub (join, last: CadQuery fuses it after the cuts)
The CadQuery cuts start 0.01 mm before the rear face. Fusion starts on the
face. The volume difference is below 0.05 mm3.
"""
from typing import Dict

import fusion_lib as fl

CATEGORY = "rigid"

# preload (SDR latch) -> component name
NAMES: Dict[bool, str] = {True: "ASA_latch_sdr_preload", False: "ASA_latch"}
# Joint origin at the pivot hole centre. The revolute joint uses it.
PIVOT_JO = "JO_pivot"
# Frame of the bottom AND the top latch in the world: local x -> -X, y -> -Z, z -> -Y.
# The top latch has the same frame, because it is a move in Z (see the module text).
SIDE_FRAME = ((-1, 0, 0), (0, 0, -1), (0, -1, 0))
# Frame of the hatch latch: local x -> -X, y -> -Y, z -> +Z (arm lies on the top plate).
HATCH_FRAME = ((-1, 0, 0), (0, -1, 0), (0, 0, 1))


def latch_name(preload: bool) -> str:
    """Return the component name of a latch. preload: SDR latch with the boss."""
    return NAMES[preload]


def build(ctx, preload: bool):
    """Build one latch component. The bottom and top latches both use it."""
    comp = fl.new_component(ctx, latch_name(preload), CATEGORY)
    length = "LATCH_L_PRE" if preload else "LATCH_L"
    half_w = "LATCH_W / 2"
    lo, hi = (f"-({half_w})", half_w)
    boss_x = "STRIP_W / 2 + EAR_W / 2 + LATCH_PIVOT_X"   # tray centre plane in the latch frame

    # 1. arm plate
    sk = fl.plane_sketch(comp, "arm")
    fl.sk_rect(ctx, sk, "0 mm", lo, length, hi)
    fl.extrude(comp, sk, "T_", op="new", name="arm_plate")
    # 2. boss (preload only): a round post from the rear face, along z
    if preload:
        sk = fl.plane_sketch(comp, "boss")
        fl.sk_circle(ctx, sk, boss_x, "0 mm", "PRE_BOSS_D")
        fl.extrude(comp, sk, "PRE_BOSS_T", op="join", name="boss")
    # 3. pivot hole, through the plate
    sk = fl.plane_sketch(comp, "pivot_hole")
    fl.sk_circle(ctx, sk, "LATCH_PIVOT_X", "0 mm", "M3_CLR_D")
    fl.extrude(comp, sk, "T_", op="cut", name="pivot_hole")
    # 4. preload screw holes. The insert enters from the REAR face (z = 0).
    if preload:
        sk = fl.plane_sketch(comp, "insert_pocket")
        fl.sk_circle(ctx, sk, boss_x, "0 mm", "INSERT_D")
        fl.extrude(comp, sk, "STOP_INSERT_L", op="cut", name="insert_pocket")
        sk = fl.plane_sketch(comp, "screw_clearance")
        fl.sk_circle(ctx, sk, boss_x, "0 mm", "STOP_SCREW_CLR")
        fl.extrude(comp, sk, "PRE_BOSS_T", op="cut", name="screw_clearance")
    # 5. grip nub on the front face (z = T_)
    plane = fl.offset_plane(comp, "plane_front", "T_")
    sk = fl.plane_sketch(comp, "nub", plane)
    fl.sk_circle(ctx, sk, "LATCH_NUB_X", "0 mm", "LATCH_NUB_D")
    fl.extrude(comp, sk, "LATCH_NUB_H", op="join", name="grip_nub")
    # Joint origin on the pivot hole centre (the revolute joints use it).
    fl.joint_origin(comp, PIVOT_JO, ("LATCH_PIVOT_X", "0 mm", "0 mm"), fl.IDENTITY)
    return comp
