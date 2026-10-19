"""Map heron_rack.py values to seed parameter names (flat names).

Why: the seed flattens lists (SDR_HOLE1_BX, ...) and renames T, H, W. The
tests and the Fusion check need the same map. This file builds it from the
frozen CadQuery module. Import it only where cadquery is installed.
"""
import heron_rack as hr

_SPECIAL = {"T": "T_", "H": "H_", "W": "W_"}


def reference_values() -> dict:
    """Return {seed name: reference value in mm (or plain number)}."""
    ref = {}
    for key, val in vars(hr).items():
        if key.startswith("_") or isinstance(val, bool):
            continue
        if isinstance(val, (int, float)) and key not in ("OUT", "REF"):
            ref[_SPECIAL.get(key, key)] = float(val)
    for i, (bx, by) in enumerate(hr.SDR_HOLES, 1):
        ref[f"SDR_HOLE{i}_BX"], ref[f"SDR_HOLE{i}_BY"] = bx, by
    for key, val in hr.SDR_REAR_SMA.items():
        ref["SDR_REAR_SMA_" + key.upper()] = val
    for i, (hx, hz) in enumerate(hr.NUC_HOLES, 1):
        ref[f"NUC_HOLE{i}_X"], ref[f"NUC_HOLE{i}_Z"] = hx, hz
    for i, (a, b) in enumerate(hr.SSD_HOLE_Y, 1):
        ref[f"SSD_HOLE{i}_Y0"], ref[f"SSD_HOLE{i}_Y1"] = a, b
    for i, v in enumerate(hr.WALL_TABS_Y, 1):
        ref[f"WALL_TAB_Y{i}"] = v
    for i, v in enumerate(hr.WALL_SCREWS_Y, 1):
        ref[f"WALL_SCREW_Y{i}"] = v
    for i, v in enumerate(hr.REAR_TABS_X, 1):
        ref[f"REAR_TAB_X{i}"] = v
    for i, v in enumerate(hr.REAR_SCREWS_X, 1):
        ref[f"REAR_SCREW_X{i}"] = v
    for i, v in enumerate(hr.REAR_SIDE_TABS_Z, 1):
        ref[f"REAR_SIDE_TAB_Z{i}"] = v
    for i, v in enumerate(hr.REAR_SIDE_SCREW_Z, 1):
        ref[f"REAR_SIDE_SCREW_Z{i}"] = v
    for i, (a, b) in enumerate(hr.EAR_Y, 1):
        ref[f"EAR{i}_Y0"], ref[f"EAR{i}_Y1"] = a, b
    for i, (a, b) in enumerate(hr.NUC_EAR_Y, 1):
        ref[f"NUC_EAR{i}_Y0"], ref[f"NUC_EAR{i}_Y1"] = a, b
    for i, (name, _kind, pitch) in enumerate(hr.SLOTS):
        ref[f"PITCH_{name}"] = pitch
        ref[f"SLOT_X0_{name}"] = hr.slot_x0(i)
        ref[f"TRAY_X_{name}"] = hr.tray_x(i)
    g = hr.ssd_geom()
    ref.update(SSD_SX0=g["sx0"], SSD_SX1=g["sx1"], SSD_GY1=g["y1"], SSD_YM=(g["y0"] + g["y1"]) / 2,
               SSD_ZD0=g["zd0"], SSD_ZD1=g["zd1"])
    # ssd_cage() locals (phase 4b): the cage outer and channel faces, from the same geometry
    cx0, cx1 = g["sx0"] - hr.SSD_CLR, g["sx1"] + hr.SSD_CLR
    ref.update(SSD_CX0=cx0, SSD_CX1=cx1, SSD_OX0=cx0 - 2.0, SSD_OX1=cx1 + 2.0, SSD_PC=(cx0 + cx1) / 2,
               SSD_FY0=g["y0"] - hr.SSD_CLR - 2.0, SSD_FY1=g["y1"] + hr.SSD_CLR + 2.0,
               SSD_CCY=g["y0"] + hr.SSD_CONN_C)
    xs = [h[0] for h in hr.ssd_cage_holes()]
    ref.update(SSD_XL=min(xs), SSD_XR=max(xs))
    kb = hr.ssd_cage_keep().BoundingBox()
    ref.update(SSD_KEEP_X0=kb.xmin, SSD_KEEP_X1=kb.xmax, SSD_KEEP_Y0=kb.ymin, SSD_KEEP_Y1=kb.ymax)
    lx, ly = hr.ssd_latch_xy()
    ref.update(SSD_LATCH_X=lx, SSD_LATCH_Y=ly)
    ox = hr.clk_outline()
    ref["CLK_X0"], ref["CLK_Z0"] = ox[0], ox[2]
    return ref
