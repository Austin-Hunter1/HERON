"""Rear plate (laser): `LZR_rear_plate`.

Mirror of `rear_plate_local` in heron_rack.py. Local frame: x = world X
(0..W), y = world Z (0..H), sheet thickness along z (0..T). Tabs reach to
-T and H + T in y, and to -T and W + T in x.

USB openings sit on each SDR USB-B axis (clock board rev D, D-042).

Solder windows use the clock board jack positions. The jack to SDR pairing
comes from `kicad_clock.mate_pairs` at build time (static). The window sizes
follow the CLK_J<n>_X / _Z parameters, so a jack move moves the window.
The keep-out discs around the board holes are put back by a join after the
windows are cut (same result as CadQuery `window.cut(disc)`).
"""
from collections import OrderedDict

import fusion_lib as fl
import kicad_clock as kc
from parts.common import sk_tslot

NAME = "LZR_rear_plate"
CATEGORY = "laser"


def _clock_holes():
    """Return the 4 clock board mounting hole centres as (x, z) expressions."""
    xl, xr = "CLK_X0 + CLK_HOLE_INSET", "CLK_X0 + CLK_W - CLK_HOLE_INSET"
    zt, zb = "CLK_TOP_Z - CLK_HOLE_INSET", "CLK_Z0 + CLK_HOLE_INSET"
    return [(xl, zt), (xr, zt), (xl, zb), (xr, zb)]


def _sdr_slots(ctx):
    """Return the slot dicts of the SDR slots (b210 and b200)."""
    return [s for s in ctx.slots if s["kind"] in ("b210", "b200")]


def _solder_windows(ctx, comp) -> None:
    """Cut one solder window behind each SDR jack column, then restore the hole keep-out discs."""
    n = ctx.num
    axes = []
    for slot in _sdr_slots(ctx):
        for key, param in (("ref_a", "SDR_REAR_SMA_REF_A"), ("ref_b", "SDR_REAR_SMA_REF_B")):
            axes.append((slot["name"], key, (n(f"TRAY_X_{slot['name']} + T_ / 2 + STANDOFF + SMA_BZ"),
                                             n(f"Z_BOARD + {param}"))))
    pairs = kc.mate_pairs(ctx.jacks, axes, n("CLK_X0"), n("CLK_TOP_Z"))
    by_sdr = OrderedDict()
    for m in pairs:
        if m["jack"] is None:
            raise fl.ConfigError("Solder windows need the KiCad jacks, but none were found")
        by_sdr.setdefault(m["sdr"], []).append(m["jack"])
    sk = fl.plane_sketch(comp, "solder_windows")
    for jacks in by_sdr.values():
        xs = " + ".join(f"CLK_{j}_X" for j in jacks)
        zs = "; ".join(f"CLK_{j}_Z" for j in jacks)
        xc = f"({xs}) / {len(jacks)}"
        z0 = f"min({zs}) - SOLDER_WIN_DZ" if len(jacks) > 1 else f"CLK_{jacks[0]}_Z - SOLDER_WIN_DZ"
        z1 = f"max({zs}) + SOLDER_WIN_DZ" if len(jacks) > 1 else f"CLK_{jacks[0]}_Z + SOLDER_WIN_DZ"
        fl.sk_rounded_rect(ctx, sk, f"{xc} - SOLDER_WIN_DX", z0, f"{xc} + SOLDER_WIN_DX", z1, "SOLDER_WIN_R")
    fl.extrude(comp, sk, None, op="cut", through=True, name="solder_windows")
    discs = fl.plane_sketch(comp, "hole_keepout")
    for hx, hz in _clock_holes():
        fl.sk_circle(ctx, discs, hx, hz, "CLK_HOLE_D + 2 * CLK_HOLE_KEEP")
    fl.extrude(comp, discs, "T_", op="join", name="hole_keepout_discs")


def build(ctx):
    """Build the rear plate component in its local frame and return it."""
    comp = fl.new_component(ctx, NAME, CATEGORY)
    body = fl.plane_sketch(comp, "outline")
    fl.sk_rect(ctx, body, "0 mm", "0 mm", "W_", "H_")
    fl.extrude(comp, body, "T_", op="new", name="rear_sheet")
    # tabs: top and bottom edge, then the two side edges
    tabs = fl.plane_sketch(comp, "tabs")
    for k in (1, 2):
        tx = f"REAR_TAB_X{k}"
        fl.sk_rect(ctx, tabs, f"{tx} - TAB_L / 2", "-T_", f"{tx} + TAB_L / 2", "0 mm")
        fl.sk_rect(ctx, tabs, f"{tx} - TAB_L / 2", "H_", f"{tx} + TAB_L / 2", "H_ + T_")
    for k in (1, 2):
        tz = f"REAR_SIDE_TAB_Z{k}"
        fl.sk_rect(ctx, tabs, "-T_", f"{tz} - REAR_SIDE_TAB_L / 2", "0 mm", f"{tz} + REAR_SIDE_TAB_L / 2")
        fl.sk_rect(ctx, tabs, "W_", f"{tz} - REAR_SIDE_TAB_L / 2", "W_ + T_", f"{tz} + REAR_SIDE_TAB_L / 2")
    fl.extrude(comp, tabs, "T_", op="join", name="rear_tabs")
    _solder_windows(ctx, comp)
    # USB openings: the SDR USB cable passes through the clock board and this plate (D-042)
    usb = fl.plane_sketch(comp, "usb_openings")
    for slot in _sdr_slots(ctx):
        xc = f"(TRAY_X_{slot['name']} + T_ / 2 + STANDOFF + SMA_BZ + USB_DX)"
        fl.sk_rounded_rect(ctx, usb, f"{xc} - REAR_USB_W / 2", "Z_BOARD + SDR_USB_BX - REAR_USB_H / 2",
                           f"{xc} + REAR_USB_W / 2", "Z_BOARD + SDR_USB_BX + REAR_USB_H / 2", "SOLDER_WIN_R")
    fl.extrude(comp, usb, None, op="cut", through=True, name="usb_openings")
    # T-slots, clock board holes, stop access holes
    cuts = fl.plane_sketch(comp, "slots_and_holes")
    sk_tslot(ctx, cuts, "REAR_SCREW_X1", "0 mm", +1, "y")
    sk_tslot(ctx, cuts, "REAR_SCREW_X1", "H_", -1, "y")
    sk_tslot(ctx, cuts, "REAR_SIDE_SCREW_Z1", "0 mm", +1, "x")
    sk_tslot(ctx, cuts, "REAR_SIDE_SCREW_Z1", "W_", -1, "x")
    for hx, hz in _clock_holes():
        fl.sk_circle(ctx, cuts, hx, hz, "CLK_HOLE_D")
    for slot in _sdr_slots(ctx):
        for z in ("LATCH_Z", "H_ - LATCH_Z"):
            fl.sk_circle(ctx, cuts, f"TRAY_X_{slot['name']}", z, "STOP_ACCESS_D")
    fl.extrude(comp, cuts, None, op="cut", through=True, name="slots_and_holes")
    # window grids: left of the clock board, and above it
    fl.grid_windows(ctx, comp, "windows_left", "REARWIN_L_MARGIN_X", "REARWIN_MARGIN_Z",
                    "CLK_X0 - REARWIN_CLK_GAP", "H_ - REARWIN_MARGIN_Z", "REARWIN_L_NX", "REARWIN_L_NY",
                    "REARWIN_L_RIB", "GRID_WIN_R")
    fl.grid_windows(ctx, comp, "windows_above", "CLK_X0 + REARWIN_CLK_GAP", "CLK_TOP_Z + REARWIN_CLK_GAP",
                    "W_ - REARWIN_R_MARGIN_X", "H_ - REARWIN_MARGIN_Z", "REARWIN_R_NX", "REARWIN_R_NY",
                    "REARWIN_R_RIB", "GRID_WIN_R")
    return comp
