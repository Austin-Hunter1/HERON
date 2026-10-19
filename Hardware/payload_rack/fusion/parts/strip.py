"""Rail strips (rigid, printed): `ASA_rail_strip_<sdr|nuc>_<bot|top>`.

Mirror of `strip_local(length, ears, sdr)` in heron_rack.py. Local frame: x across
(centred), y along the strip, z up from the plate. The strip has a base, two
ribs, a rear stop, three ears with heat-set insert holes (from the plate side),
and a latch pivot insert hole in the front face of the first ear. The SDR strip
has a tall rear stop block with an M3 insert hole and a set-screw clearance hole.
The NUC strip has a plain rear stop.

The `_top` strip is the bottom strip mirrored about the local XY plane
(z -> -z), like `mirror_top` (z -> H - z in the world). It is NOT a rotation.
Both use the SAME function with the flag `top`. The flag only changes the
sign of the Z offsets and the extrude direction. Why: one set of rules, so a
parameter change moves both strips the same way.

Order of features:
  1. base          2. ribs          3. rear stop (block)       4. ears
  5. ear insert holes (cut)         6. latch pivot hole (cut)
  7. SDR only: rear stop insert hole and set-screw clearance hole (cuts)
"""
from typing import Dict, List, Tuple

import fusion_lib as fl

CATEGORY = "rigid"
EAR_COUNT = 3     # number of EAR<k>_Y0/_Y1 (NUC_EAR<k>_...) pairs in the seed

# kind -> side -> component name
NAMES: Dict[str, Dict[str, str]] = {
    "sdr": {"bot": "ASA_rail_strip_sdr_bot", "top": "ASA_rail_strip_sdr_top"},
    "nuc": {"bot": "ASA_rail_strip_nuc_bot", "top": "ASA_rail_strip_nuc_top"},
}


def strip_name(kind: str, top: bool) -> str:
    """Return the component name of a strip. kind: 'sdr' or 'nuc'."""
    return NAMES[kind]["top" if top else "bot"]


def _ears(kind: str, length: str) -> List[Tuple[str, str, str]]:
    """Return the ears as (y0, y1, hole_y) expressions.

    heron_rack: e1 = min(e1, length); the hole is at the ear middle, but the
    first ear uses EAR_HOLE0_Y.
    """
    prefix = "NUC_EAR" if kind == "nuc" else "EAR"
    out = []
    for k in range(1, EAR_COUNT + 1):
        y0, y1 = f"{prefix}{k}_Y0", f"min({prefix}{k}_Y1; {length})"
        out.append((y0, y1, "EAR_HOLE0_Y" if k == 1 else f"(({y0}) + ({y1})) / 2"))
    return out


def build(ctx, kind: str, top: bool = False):
    """Build one strip component. kind: 'sdr' or 'nuc'. top: the mirrored strip."""
    sdr = kind == "sdr"
    length = "STRIP_Y1" if sdr else "NUC_STRIP_Y1"
    sgn = -1 if top else 1

    def z(expr: str) -> str:
        """Return a local Z expression, negated for the mirrored strip."""
        return expr if sgn > 0 else f"-({expr})"

    comp = fl.new_component(ctx, strip_name(kind, top), CATEGORY)
    w2 = "STRIP_W / 2"
    ear_x = f"{w2} + EAR_W / 2"          # ear centre line (insert holes, latch pivot)
    rib_top = "STRIP_BASE + RIB_H"

    def extrude(sk, dist, op="join", name=None):
        """Extrude along +z (bottom) or -z (top)."""
        return fl.extrude(comp, sk, dist, op=op, flip=top, name=name)

    # 1. base
    sk = fl.plane_sketch(comp, "base")
    fl.sk_rect(ctx, sk, f"-({w2})", "0 mm", w2, length)
    extrude(sk, "STRIP_BASE", op="new", name="strip_base")
    # 2. ribs (on the base top face)
    plane = fl.offset_plane(comp, "plane_base_top", z("STRIP_BASE"))
    sk = fl.plane_sketch(comp, "ribs", plane)
    fl.sk_rect(ctx, sk, f"-({w2})", "0 mm", f"-({w2}) + RIB_T", length)
    fl.sk_rect(ctx, sk, f"({w2}) - RIB_T", "0 mm", w2, length)
    extrude(sk, "RIB_H", name="ribs")
    # 3. rear stop. SDR: tall block with the insert. NUC: plain stop at rib height.
    stop_l = "STOP_BLOCK_L" if sdr else "NUC_STOP_L"
    stop_h = "STOP_BLOCK_H - STRIP_BASE" if sdr else "RIB_H"
    sk = fl.plane_sketch(comp, "rear_stop", plane)
    fl.sk_rect(ctx, sk, f"-({w2})", f"({length}) - {stop_l}", w2, length)
    extrude(sk, stop_h, name="rear_stop")
    # 4. ears: full height beside the strip, from the plate plane
    sk = fl.plane_sketch(comp, "ears")
    ears = _ears(kind, length)
    for y0, y1, _hole in ears:
        fl.sk_rect(ctx, sk, w2, y0, f"({w2}) + EAR_W", y1)
    extrude(sk, rib_top, name="ears")
    # 5. ear insert holes from the plate side
    sk = fl.plane_sketch(comp, "ear_holes")
    for _y0, _y1, hole_y in ears:
        fl.sk_circle(ctx, sk, ear_x, hole_y, "INSERT_D")
    extrude(sk, "EAR_INSERT_L", op="cut", name="ear_holes")
    # 6. latch pivot hole in the front face (y = 0), along +y. XZ sketch: y = -z.
    xz = comp.xZConstructionPlane
    sk = fl.plane_sketch(comp, "latch_pivot", xz)
    fl.sk_circle(ctx, sk, ear_x, fl.flip_pos(z("LATCH_Z")), "INSERT_D")
    fl.extrude(comp, sk, "LATCH_INSERT_L", op="cut", name="latch_pivot_hole")
    if sdr:
        # 7. the insert enters from the FRONT face of the block; the set screw
        # enters from the rear through the clearance hole.
        front = fl.offset_plane(comp, "plane_stop_front", f"({length}) - STOP_BLOCK_L", xz)
        sk = fl.plane_sketch(comp, "stop_holes", front)
        fl.sk_circle(ctx, sk, "0 mm", fl.flip_pos(z("LATCH_Z")), "INSERT_D")
        fl.extrude(comp, sk, "STOP_INSERT_L", op="cut", name="stop_insert_hole")
        sk = fl.plane_sketch(comp, "stop_clear", front)
        fl.sk_circle(ctx, sk, "0 mm", fl.flip_pos(z("LATCH_Z")), "STOP_SCREW_CLR")
        fl.extrude(comp, sk, "STOP_BLOCK_L", op="cut", name="stop_screw_hole")
    return comp
