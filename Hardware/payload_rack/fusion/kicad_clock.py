"""Read the clock board rev C KiCad file (pure Python, no Fusion import).

Why this module exists: the rear plate windows and the clock board
reference need the jack positions. The jack positions live in the KiCad PCB
file. The logic is the same as `_clk_read`, `clk_jack_kind` and `mate_pairs`
in heron_rack.py. It is a copy so that Fusion scripts and pytest can use it
without CadQuery. heron_rack.py stays frozen.
"""
from __future__ import annotations

import math
import os
import re
from typing import Dict, List, Mapping, NamedTuple, Sequence, Tuple

from rack_config import ParamSpec


class Jack(NamedTuple):
    """One connector of the board: KiCad-local position and footprint name."""

    kx: float
    ky: float
    footprint: str


# Jack references that are SMP by design (J3..J10 are the SDR jacks).
SMP_REF_NUMBERS = range(3, 11)


def read_clock_board(pcb_path: str, origin: float = 100.0) -> Dict[str, Jack]:
    """Return {ref: Jack} for every J<n> footprint in the KiCad file.

    Why subtract `origin`: the KiCad page origin is at (100, 100) mm. The
    board local frame starts at the board top-left corner. heron_rack.py
    does the same. An empty dictionary means the file is missing.
    """
    if not os.path.exists(pcb_path):
        return {}
    with open(pcb_path, encoding="utf-8") as handle:
        text = handle.read()
    out: Dict[str, Jack] = {}
    # KiCad 7 indents with spaces. KiCad 8 indents with tabs. Accept both.
    for block in re.split(r"\n[ \t]+\(footprint ", text)[1:]:
        at = re.search(r"\(at ([-\d.]+) ([-\d.]+)", block)
        ref = (re.search(r'\(property "Reference" "([^"]+)"', block)
               or re.search(r'fp_text reference "([^"]+)"', block))
        name = re.match(r'"([^"]*)"', block)
        if at and ref and re.fullmatch(r"J\d+", ref.group(1)):
            out[ref.group(1)] = Jack(float(at.group(1)) - origin,
                                     float(at.group(2)) - origin,
                                     name.group(1) if name else "")
    return out


def jack_kind(ref: str, footprint: str = "") -> str:
    """Return 'smp' or 'sma' for a jack.

    The footprint name decides. The file may still hold the old SMA footprint
    on J3..J10, so the reference decides too: J3..J10 are SMP, J2 stays SMA.
    """
    if "SMP" in footprint.upper():
        return "smp"
    return "smp" if ref in ("J%d" % n for n in SMP_REF_NUMBERS) else "sma"


def jack_groups(jacks: Mapping[str, Jack], jst_ref: str = "J1") -> Tuple[List[str], List[str], List[str]]:
    """Split the jack references into (jst, smp, sma) lists in natural order.

    Why: the clock board reference draws three connector shapes. The JST XH
    connector is named by `jst_ref` (J1). The kind of every other jack follows
    `jack_kind`. The build and the tests use this same split.
    """
    refs = sorted(jacks, key=lambda r: int(r[1:]))
    jst = [r for r in refs if r == jst_ref]
    smp = [r for r in refs if r != jst_ref and jack_kind(r, jacks[r].footprint) == "smp"]
    sma = [r for r in refs if r != jst_ref and r not in smp]
    return jst, smp, sma


def to_world(kx: float, ky: float, board_x0: float, board_top_z: float) -> Tuple[float, float]:
    """Map KiCad board coordinates to rack (X, Z). The board origin is top-left."""
    return board_x0 + kx, board_top_z - ky


def jack_params(jacks: Mapping[str, Jack]) -> Dict[str, float]:
    """Return {'CLK_J3_KX': value, 'CLK_J3_KY': value, ...} in mm.

    Why: these names become Fusion User Parameters. Natural order (J1, J2,
    J3 ... J10) keeps the parameter list easy to read.
    """
    out: Dict[str, float] = {}
    for ref in sorted(jacks, key=lambda r: int(r[1:])):
        out[f"CLK_{ref}_KX"] = jacks[ref].kx
        out[f"CLK_{ref}_KY"] = jacks[ref].ky
    return out


def mate_pairs(jacks: Mapping[str, Jack],
               sdr_axes: Sequence[Tuple[str, str, Tuple[float, float]]],
               board_x0: float, board_top_z: float) -> List[dict]:
    """Pair each SDR reference SMA axis with the nearest SMP jack.

    sdr_axes: list of (slot name, key 'ref_a'/'ref_b', (X, Z) of the SDR SMA axis).
    Each result has the keys sdr, key, axis_xz, jack, jack_xz (like heron_rack).
    If the board file is missing, the jack axis equals the SDR axis.
    """
    smp = {ref: to_world(j.kx, j.ky, board_x0, board_top_z)
           for ref, j in jacks.items() if jack_kind(ref, j.footprint) == "smp"}
    out: List[dict] = []
    for name, key, axis in sdr_axes:
        ref, jxz = None, axis
        if smp:
            ref = min(smp, key=lambda r: math.hypot(smp[r][0] - axis[0], smp[r][1] - axis[1]))
            jxz = smp[ref]
        out.append(dict(sdr=name, key=key, axis_xz=axis, jack=ref, jack_xz=jxz))
    return out


def jack_specs(jacks: Mapping[str, Jack]) -> List[ParamSpec]:
    """Return ParamSpec rows for the KiCad-derived User Parameters.

    For each jack: CLK_J<n>_KX and _KY (board-local mm, literal), and CLK_J<n>_X
    and _Z (rack world X and Z, expressions that use CLK_X0 and CLK_TOP_Z).
    The sync script updates only the _KX and _KY literals.
    """
    specs: List[ParamSpec] = []
    for ref in sorted(jacks, key=lambda r: int(r[1:])):
        jack = jacks[ref]
        note = f"KiCad {jack.footprint or 'footprint'} at board-local ({jack.kx}, {jack.ky}) mm"
        specs.append(ParamSpec(f"CLK_{ref}_KX", f"{jack.kx!r} mm", "mm", note, True, jack.kx))
        specs.append(ParamSpec(f"CLK_{ref}_KY", f"{jack.ky!r} mm", "mm", note, True, jack.ky))
        specs.append(ParamSpec(f"CLK_{ref}_X", f"CLK_X0 + CLK_{ref}_KX", "mm", "world X of the jack axis", False))
        specs.append(ParamSpec(f"CLK_{ref}_Z", f"CLK_TOP_Z - CLK_{ref}_KY", "mm", "world Z of the jack axis (CadQuery frame)", False))
    return specs
