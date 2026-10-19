"""Shapes that several laser-cut parts share.

Why: the T-slot (screw channel + square nut pocket) appears in the side wall
and in the rear plate. One function keeps both the same. Mirror of `tslot`
and `tslot_x` in heron_rack.py.
"""
from typing import Tuple

import fusion_lib as fl


def _span(edge: str, sign: int, a: str, b: str) -> Tuple[str, str]:
    """Return (low, high) expressions of the range edge + sign*a .. edge + sign*b."""
    p, q = f"({edge}) {'+' if sign > 0 else '-'} ({a})", f"({edge}) {'+' if sign > 0 else '-'} ({b})"
    return (p, q) if sign > 0 else (q, p)


def sk_tslot(ctx, sk, pos: str, edge: str, sign: int, along: str = "y") -> None:
    """Sketch one T-slot that enters from an edge.

    pos: expression of the slot position ALONG the edge.
    edge: expression of the edge coordinate (the other axis).
    sign: +1 enters toward larger values, -1 toward smaller values.
    along: 'y' = the slot runs along Y (edge is a Y value, pos is an X value);
           'x' = the slot runs along X (edge is an X value, pos is a Y value).
    Sizes come from the TSLOT_* parameters.
    """
    ch_lo, ch_hi = _span(edge, sign, "0 mm", "TSLOT_DEPTH")
    nut_lo, nut_hi = _span(edge, sign, "TSLOT_NUT_Y0", "TSLOT_NUT_Y1")
    if along == "y":
        fl.sk_rect(ctx, sk, f"({pos}) - TSLOT_SCREW_HW", ch_lo, f"({pos}) + TSLOT_SCREW_HW", ch_hi)
        fl.sk_rect(ctx, sk, f"({pos}) - TSLOT_NUT_HW", nut_lo, f"({pos}) + TSLOT_NUT_HW", nut_hi)
    else:
        fl.sk_rect(ctx, sk, ch_lo, f"({pos}) - TSLOT_SCREW_HW", ch_hi, f"({pos}) + TSLOT_SCREW_HW")
        fl.sk_rect(ctx, sk, nut_lo, f"({pos}) - TSLOT_NUT_HW", nut_hi, f"({pos}) + TSLOT_NUT_HW")
