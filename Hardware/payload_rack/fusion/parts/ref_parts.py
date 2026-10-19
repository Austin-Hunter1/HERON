"""Reference envelopes (ref) for the adapters, the NUC and the SSD.

These are NOT parts to make. They show where real parts go, so fit and
interference checks can use them. They get no material and a translucent
colour (`fl.apply_ref_look`). Mirrors of `mate_refs`, `nuc_ref` and `ssd_ref`
in heron_rack.py.

  REF_smp_adapter   one cylinder, ADAPTER_D x ADAPTER_REACH. Local axis = local z.
                    The occurrence label names the SDR and the jack: REF_adapter_<slot>_<ref_a|ref_b>.
  REF_NUC_envelope  NUC board and cooler envelope. Local frame: x = world Y,
                    y = world Z, z = world X (like a tray). Origin on the board
                    rear face plane X = TRAY_X_NUC + T_/2 + STANDOFF, Y = 0, Z = RACK_Z0.
  REF_SSD           the 2.5 in drive body, in WORLD axes (no local frame).
"""
import fusion_lib as fl

CATEGORY = "ref"
ADAPTER = "REF_smp_adapter"
NUC = "REF_NUC_envelope"
SSD = "REF_SSD"

# The SDR SMA key -> seed parameter of its axis height above the board bottom.
ADAPTER_KEYS = {"ref_a": "SDR_REAR_SMA_REF_A", "ref_b": "SDR_REAR_SMA_REF_B"}
# Adapter local frame: x -> world X, y -> world -Z, z -> world +Y (cylinder runs toward the rear).
ADAPTER_FRAME = ((1, 0, 0), (0, 0, -1), (0, 1, 0))
# NUC local frame: x -> world Y, y -> world Z, z -> world X.
NUC_FRAME = ((0, 1, 0), (0, 0, 1), (1, 0, 0))


def build_adapter(ctx):
    """Build the adapter cylinder: diameter ADAPTER_D, length ADAPTER_REACH, axis = local z."""
    comp = fl.new_component(ctx, ADAPTER, CATEGORY)
    sk = fl.plane_sketch(comp, "body")
    fl.sk_circle(ctx, sk, "0 mm", "0 mm", "ADAPTER_D")
    fl.extrude(comp, sk, "ADAPTER_REACH", op="new", name="adapter_body")
    fl.apply_ref_look(ctx, comp)
    return comp


def build_nuc(ctx):
    """Build the NUC envelope: the board, then the cooler block on the board front side."""
    comp = fl.new_component(ctx, NUC, CATEGORY)
    sk = fl.plane_sketch(comp, "board")
    fl.sk_rect(ctx, sk, "Y_BOARD", "Z_BOARD", "Y_BOARD + NUC_S", "Z_BOARD + NUC_S")
    fl.extrude(comp, sk, "PCB_T", op="new", name="nuc_board")
    plane = fl.offset_plane(comp, "plane_cooler", "PCB_T")
    sk = fl.plane_sketch(comp, "cooler", plane)
    fl.sk_rect(ctx, sk, "Y_BOARD + NUCREF_INSET", "Z_BOARD + NUCREF_INSET",
               "Y_BOARD + NUC_S - NUCREF_INSET", "Z_BOARD + NUC_S - NUCREF_INSET")
    fl.extrude(comp, sk, "NUC_TOP - PCB_T", op="join", name="nuc_cooler")
    fl.apply_ref_look(ctx, comp)
    return comp


def build_ssd(ctx):
    """Build the drive body in world axes: from the sled face to the top cover, connector edge to top edge."""
    comp = fl.new_component(ctx, SSD, CATEGORY)
    plane = fl.offset_plane(comp, "plane_drive", "SSD_ZD0")
    sk = fl.plane_sketch(comp, "drive", plane)
    fl.sk_rect(ctx, sk, "SSD_SX0 + SLED_T", "SSD_Y0", "SSD_SX1", "SSD_GY1")
    fl.extrude(comp, sk, "SSD_ZD1 - SSD_ZD0", op="new", name="drive_body")
    fl.apply_ref_look(ctx, comp)
    return comp
