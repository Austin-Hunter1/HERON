"""Blade sub-assemblies: `BLADE_sdr` (4 occurrences) and `BLADE_nuc` (1).

A blade is one tray with a TPU rail clipped on its bottom edge and a second
TPU rail on its top edge. The blade component has no body. It holds three
nested occurrences (tray, bottom rail, top rail) and three rigid joints.

Blade local frame = the CadQuery world frame, moved in X so that the origin
is on the tray centre plane (x = TRAY_X_<slot>) and with Z measured from the
CadQuery z = 0 plane. The root slider joint puts that origin at
(TRAY_X_<slot>, 0, RACK_Z0). Slide value 0 is the installed position.

Top rail: `mirror_top` of the rail (z -> H - z) equals the same rail turned
180 deg about its own Y axis. Why: the rail profile is symmetric in x. So one
rail component serves both edges. The top joint origin has the frame
x -> -X, y -> +Y, z -> -Z. No `_top` rail component is needed.
"""
import fusion_lib as fl

CATEGORY = "assembly"
NUC_KIND = "nuc"

# Blade kind -> (blade name, tray component, rail component)
KINDS = {
    "sdr": ("BLADE_sdr", "LZR_tray_sdr", "TPU_rail_sdr"),
    "nuc": ("BLADE_nuc", "LZR_tray_nuc", "TPU_rail_nuc"),
}
# Frames (u, v, w) = local axes in blade axes
TRAY_FRAME = ((0, 1, 0), (0, 0, 1), (1, 0, 0))      # tray_local placement in build()
RAIL_TOP_FRAME = ((-1, 0, 0), (0, 1, 0), (0, 0, -1))  # 180 deg about Y = mirror_top of the rail


def kind_of_slot(slot_kind: str) -> str:
    """Map a seed slot kind (nuc, b210, b200) to a blade kind (nuc, sdr)."""
    return "nuc" if slot_kind == NUC_KIND else "sdr"


def blade_name(slot_kind: str) -> str:
    """Return the blade component name for a seed slot kind."""
    return KINDS[kind_of_slot(slot_kind)][0]


def _need(ctx, name: str):
    """Return a component that must exist already. Stop with a clear message if not."""
    comp = ctx.components.get(name)
    if comp is None:
        raise fl.ConfigError(f"blade: component '{name}' is not in the design. Build it first.")
    return comp


def _local_joint_origin(comp):
    """Return the identity joint origin `JO_local` of a part. Make it when missing."""
    jo = comp.jointOrigins.itemByName("JO_local")
    return jo if jo is not None else fl.joint_origin(comp, "JO_local")


def build(ctx, kind: str):
    """Build one blade component from its tray and rail components.

    The root-level occurrences of the tray and the rails are deleted at the
    end (they were only the first occurrence that `new_component` makes).
    """
    name, tray_name, rail_name = KINDS[kind]
    tray, rail = _need(ctx, tray_name), _need(ctx, rail_name)
    comp = fl.new_component(ctx, name, CATEGORY)
    fl.joint_origin(comp, "JO_local")                     # the root slider joint uses this one
    jo_tray = fl.joint_origin(comp, "JO_tray", ("-T_ / 2", "0 mm", "0 mm"), TRAY_FRAME)
    jo_bot = fl.joint_origin(comp, "JO_rail_bot", ("0 mm", "TRAY_Y0", "TRAY_Z0 - RAIL_BACK"), fl.IDENTITY)
    jo_top = fl.joint_origin(comp, "JO_rail_top", ("0 mm", "TRAY_Y0", "H_ - TRAY_Z0 + RAIL_BACK"), RAIL_TOP_FRAME)
    for child, label, parent_jo in ((tray, "tray", jo_tray), (rail, "rail_bot", jo_bot), (rail, "rail_top", jo_top)):
        occ = fl.nest_occurrence(comp, child)
        fl.make_joint(ctx, "rigid", occ, _local_joint_origin(child), parent_jo, "J_" + label, container=comp)
    ctx.design.activateRootComponent()
    for child in (tray, rail):
        fl.delete_root_occurrences(ctx, child)
    return comp
