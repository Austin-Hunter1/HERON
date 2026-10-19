"""Compare Fusion parts with ref_volumes.json (made by the CadQuery reference script).

Why: a part passes when its volume is within 0.5 % and its bounding box within
0.05 mm of the CadQuery reference. This module runs those checks in Fusion and
prints one table. It reads only; it changes nothing.
"""
import json
from typing import Dict, Iterable, List, Optional, Tuple

import fusion_lib as fl

VOL_TOL = 0.005      # 0.5 %
BOX_TOL = 0.05       # mm


def load_reference(path: str) -> dict:
    """Read the reference JSON."""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _row(label: str, got: dict, ref: dict, z_shift: float) -> Tuple[str, float, float, float, float, bool]:
    """Compare one measurement with one reference entry. z_shift moves the reference box in Z."""
    rb = list(ref["bbox"])
    rb[2] += z_shift
    rb[5] += z_shift
    dv = (got["volume"] - ref["volume"]) / ref["volume"] * 100.0
    dbox = max(abs(a - b) for a, b in zip(got["bbox"], rb))
    ok = abs(dv) <= VOL_TOL * 100 and dbox <= BOX_TOL
    return label, got["volume"], ref["volume"], dv, dbox, ok


def check(ctx: fl.Ctx, reference: dict,
          local: Dict[str, str], world: Dict[str, str],
          volume_only: Optional[Dict[str, float]] = None) -> List[tuple]:
    """Return check rows.

    local: {component name: reference key}   (component space, no shift)
    world: {occurrence label: reference key} (world space, Z shifted by RACK_Z0)
    volume_only: {component name: expected volume} for partial parts.
    """
    rows = []
    for comp_name, key in local.items():
        rows.append(_row("local " + comp_name, fl.measure(ctx.components[comp_name]), reference[key], 0.0))
    z0 = ctx.env["RACK_Z0"]
    for label, key in world.items():
        rows.append(_row("world " + label, fl.measure(ctx.occurrences[label]), reference[key], z0))
    for comp_name, vol in (volume_only or {}).items():
        got = fl.measure(ctx.components[comp_name])
        dv = (got["volume"] - vol) / vol * 100.0
        rows.append(("volume " + comp_name, got["volume"], vol, dv, 0.0, abs(dv) <= VOL_TOL * 100))
    return rows


def print_rows(rows: Iterable[tuple]) -> None:
    """Print the table: part, Fusion volume, reference volume, % diff, max bbox error, pass."""
    print(f"{'part':34s} {'fusion mm3':>12s} {'ref mm3':>12s} {'diff %':>8s} {'bbox mm':>8s} ok")
    for label, got, ref, dv, dbox, ok in rows:
        print(f"{label:34s} {got:12.3f} {ref:12.3f} {dv:8.4f} {dbox:8.4f} {'OK' if ok else 'FAIL'}")
