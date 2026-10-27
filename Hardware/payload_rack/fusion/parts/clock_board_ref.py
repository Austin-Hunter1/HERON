"""Clock board rev D reference envelope (ref): `REF_clock_board_revD`.

Mirror of `clock_board_ref()` in heron_rack.py. It is NOT a part to make. It
shows where the board and its connectors go, so that the rear plate, the SDR
adapters and the cables can be checked.

Local frame (the same as the rear plate): x = world X, y = world Z (CadQuery
frame), z = world -Y. Local z = 0 is the REAR face of the board. The PCB fills
z = 0..`CLK_PCB_T`. The connectors stand on the front face and reach toward
the SDRs (more local z).

Jack positions come from the `CLK_J<n>_X` and `CLK_J<n>_Z` parameters. So
`sync_kicad.py` moves the jacks when the KiCad file changes. Which jack is
SMP or SMA is read from the KiCad file at build time (`kicad_clock.jack_kind`).
This is static: a new jack kind needs a rebuild of this part.

Order of features: 1. PCB  2. holes and USB holes (cut)  3. J1 (JST XH)  4. SMP bodies,
SMP flanges, SMP shrouds  5. SMA bodies and barrels. The jacks are joined
to the PCB. The shroud end is the mating face, `SMP_JACK_H` from the front face.
"""
import fusion_lib as fl
import kicad_clock as kc

NAME = "REF_clock_board_revD"
CATEGORY = "ref"
JST_REF = "J1"   # the JST XH power connector (clock_board_ref: ref == "J1")


def _holes():
    """Return the 6 mounting hole centres as (x, z) expressions. Same as `clk_holes` in heron_rack.py."""
    xl, xr = "CLK_X0 + CLK_HOLE_INSET", "CLK_X0 + CLK_W - CLK_HOLE_INSET"
    zt, zb = "CLK_TOP_Z - CLK_HOLE_INSET", "CLK_Z0 + CLK_HOLE_INSET"
    xm = "CLK_X0 + CLK_W / 2"          # H5, H6 (rev D, D-043): middle of the long edges
    return [(xl, zt), (xr, zt), (xl, zb), (xr, zb), (xm, zt), (xm, zb)]


def _layer(comp, name: str, depth_expr: str):
    """Return a sketch on a plane `depth_expr` in front of the rear face (local z)."""
    plane = fl.offset_plane(comp, "plane_" + name, depth_expr)
    return fl.plane_sketch(comp, name, plane)


def _boxes(ctx, comp, name: str, refs, side: str, depth: str, depth_start: str, height: str = None,
           dx: str = "0", dz: str = "0") -> None:
    """Join one square (or rectangular) body for each jack in `refs`.

    side: expression of the box width (X). height: box height (Z), default = side.
    depth_start: local z where the box starts. depth: box depth along local z.
    dx, dz: box centre offset from the jack position. Why: the J1 footprint
    origin is pin 1, not the body centre.
    """
    if not refs:
        return
    height = height or side
    sk = _layer(comp, name, depth_start)
    for ref in refs:
        x, z = f"(CLK_{ref}_X + ({dx}))", f"(CLK_{ref}_Z + ({dz}))"
        fl.sk_rect(ctx, sk, f"{x} - ({side}) / 2", f"{z} - ({height}) / 2",
                   f"{x} + ({side}) / 2", f"{z} + ({height}) / 2")
    fl.extrude(comp, sk, depth, op="join", name=name)


def _discs(ctx, comp, name: str, refs, diameter: str, depth: str, depth_start: str) -> None:
    """Join one round post (axis along local z) for each jack in `refs`."""
    if not refs:
        return
    sk = _layer(comp, name, depth_start)
    for ref in refs:
        fl.sk_circle(ctx, sk, f"CLK_{ref}_X", f"CLK_{ref}_Z", diameter)
    fl.extrude(comp, sk, depth, op="join", name=name)


def build(ctx):
    """Build the clock board reference component in its local frame and return it."""
    if not ctx.jacks:
        raise fl.ConfigError("REF_clock_board_revD needs the KiCad jacks, but none were found")
    comp = fl.new_component(ctx, NAME, CATEGORY)
    jst, smp, sma = kc.jack_groups(ctx.jacks, JST_REF)
    # 1. PCB (outline = clk_outline) and 2. its four mounting holes
    sk = fl.plane_sketch(comp, "pcb")
    fl.sk_rect(ctx, sk, "CLK_X0", "CLK_Z0", "CLK_X0 + CLK_W", "CLK_TOP_Z")
    fl.extrude(comp, sk, "CLK_PCB_T", op="new", name="pcb")
    sk = fl.plane_sketch(comp, "pcb_holes")
    for hx, hz in _holes():
        fl.sk_circle(ctx, sk, hx, hz, "CLKREF_HOLE_D")
    fl.extrude(comp, sk, None, op="cut", through=True, name="pcb_holes")
    # 2b. USB holes (rev D, D-042): one on each SDR USB-B axis; the last one runs off the board edge (notch)
    sk = fl.plane_sketch(comp, "usb_holes")
    for slot in ctx.slots:
        if slot["kind"] not in ("b210", "b200"):
            continue
        xc = f"(TRAY_X_{slot['name']} + T_ / 2 + STANDOFF + SMA_BZ + USB_DX)"
        fl.sk_rect(ctx, sk, f"{xc} - CLK_USB_W / 2", "Z_BOARD + SDR_USB_BX - CLK_USB_H / 2",
                   f"{xc} + CLK_USB_W / 2", "Z_BOARD + SDR_USB_BX + CLK_USB_H / 2")
    fl.extrude(comp, sk, None, op="cut", through=True, name="usb_holes")
    # 3. J1 (JST XH): a plain box from the front face, offset from pin 1 to the body centre
    _boxes(ctx, comp, "jst_body", jst, "CLKREF_JST_W", "CLKREF_JST_D", "CLK_PCB_T", "CLKREF_JST_H",
           dx="CLKREF_JST_DX", dz="CLKREF_JST_DZ")
    # 4. SMP jacks: square body, round flange, round shroud (ends at the mating face)
    _boxes(ctx, comp, "smp_body", smp, "CLKREF_SMP_BODY", "CLKREF_SMP_BODY_D", "CLK_PCB_T")
    _discs(ctx, comp, "smp_flange", smp, "CLKREF_SMP_BODY", "CLKREF_SMP_FLANGE_END - CLKREF_SMP_BODY_D",
           "CLK_PCB_T + CLKREF_SMP_BODY_D")
    _discs(ctx, comp, "smp_shroud", smp, "CLKREF_SMP_SHROUD_D", "SMP_JACK_H - CLKREF_SMP_FLANGE_END",
           "CLK_PCB_T + CLKREF_SMP_FLANGE_END")
    # 5. SMA jacks: square body and barrel
    _boxes(ctx, comp, "sma_body", sma, "CLKREF_SMA_BODY", "CLKREF_SMA_BODY_D", "CLK_PCB_T")
    _discs(ctx, comp, "sma_barrel", sma, "CLKREF_SMA_BODY", "CLKREF_SMA_BARREL_L",
           "CLK_PCB_T + CLKREF_SMA_BODY_D")
    fl.apply_ref_look(ctx, comp)
    return comp
