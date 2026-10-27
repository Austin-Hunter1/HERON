"""Fabrication layout plan for flight_rack_asm (pure Python, no `adsk`).

Why this module exists: the user wants the laser-cut parts and the printed
parts laid out flat in Fusion, so they can export them from there. The layout
must follow the model: when a parameter such as the sheet thickness `T_`
changes, the parts change shape and the layout must not overlap.

How: each layout occurrence is a new occurrence of the SAME component as in
the rack, so its shape updates with the rack. Each one is placed by a rigid
joint whose offset is a Fusion expression. The expression is built from the
part extents below, which are also expressions of the User Parameters. So
the layout spacing grows and shrinks with the parts.

This module only makes the expressions and checks them. `fab_layout.py`
builds them in Fusion. The tests evaluate the expressions with the seed.

Layout rules:
  * Parts keep their own local frame (no rotation). The laser parts are
    flat in their local XY plane (thickness along local z), so they lie flat.
    The printed parts keep the orientation of their local frame. Choose the
    print orientation in the slicer.
  * Rows: parts are placed left to right (+X) in a row. Rows stack in +Y.
    `FAB_GAP` is the space between parts and between rows.
  * Each part sits with its lowest point on the layout plane (z = 0).
"""
from __future__ import annotations

from collections import Counter
from typing import Dict, List, Mapping, Sequence, Tuple

from rack_config import evaluate_expression

GAP = "FAB_GAP"       # seed parameter: space between parts and rows
SPACE = "FAB_SPACE"   # seed parameter: space between the rack and the layouts

# Local bounding box of each part: (xmin, ymin, zmin, xmax, ymax, zmax).
# Each expression was checked against the Fusion body box of flight_rack_asm
# on 2026-10-21 (all 16 parts equal to 1e-4 cm). Keep them in step with the
# part modules in `parts/`: a new feature that grows a part past this box
# makes the layout overlap. `fab_layout.check` finds that in Fusion.
_TRAY_X0 = "min(TRAY_Y0; PULL_TAB_CX - PULL_TAB_W / 2)"
_TRAY_Y0 = "min(TRAY_Z0; PULL_TAB_CY - PULL_TAB_H / 2)"
_TRAY_Y1 = "max(TRAY_Z1; PULL_TAB_CY + PULL_TAB_H / 2)"
_SDR_STRIP_H = "max(STRIP_BASE + RIB_H; STOP_BLOCK_H)"
_NUC_STRIP_H = "(STRIP_BASE + RIB_H)"
EXTENTS: Dict[str, Tuple[str, str, str, str, str, str]] = {
    "LZR_bottom_plate": ("-T_", "0 mm", "0 mm", "W_ + T_", "D", "T_"),
    "LZR_top_plate": ("-T_", "0 mm", "0 mm", "W_ + T_", "D", "T_"),
    "LZR_rear_plate": ("-T_", "-T_", "0 mm", "W_ + T_", "H_ + T_", "T_"),
    "LZR_side_wall": ("0 mm", "-T_", "0 mm", "D", "H_ + T_", "T_"),
    "LZR_tray_sdr": (_TRAY_X0, _TRAY_Y0, "0 mm",
                     "max(TRAY_Y1; PULL_TAB_CX + PULL_TAB_W / 2)", _TRAY_Y1, "T_"),
    "LZR_tray_nuc": (_TRAY_X0, _TRAY_Y0, "0 mm",
                     "max(NUC_TRAY_Y1; PULL_TAB_CX + PULL_TAB_W / 2)", _TRAY_Y1, "T_"),
    "ASA_rail_strip_sdr_bot": ("-STRIP_W / 2", "0 mm", "0 mm", "STRIP_W / 2 + EAR_W", "STRIP_Y1", _SDR_STRIP_H),
    "ASA_rail_strip_sdr_top": ("-STRIP_W / 2", "0 mm", f"-{_SDR_STRIP_H}", "STRIP_W / 2 + EAR_W", "STRIP_Y1", "0 mm"),
    "ASA_rail_strip_nuc_bot": ("-STRIP_W / 2", "0 mm", "0 mm", "STRIP_W / 2 + EAR_W", "NUC_STRIP_Y1", _NUC_STRIP_H),
    "ASA_rail_strip_nuc_top": ("-STRIP_W / 2", "0 mm", f"-{_NUC_STRIP_H}", "STRIP_W / 2 + EAR_W", "NUC_STRIP_Y1", "0 mm"),
    "ASA_latch_sdr_preload": ("0 mm", "-LATCH_W / 2", "0 mm", "LATCH_L_PRE", "LATCH_W / 2",
                              "max(T_ + LATCH_NUB_H; PRE_BOSS_T)"),
    "ASA_latch": ("0 mm", "-LATCH_W / 2", "0 mm", "LATCH_L", "LATCH_W / 2", "T_ + LATCH_NUB_H"),
    "TPU_rail_sdr": ("-RAIL_W / 2", "0 mm", "0 mm", "RAIL_W / 2", "RAIL_L_SDR", "RAIL_BACK + RAIL_GRIP"),
    "TPU_rail_nuc": ("-RAIL_W / 2", "0 mm", "0 mm", "RAIL_W / 2", "RAIL_L_NUC", "RAIL_BACK + RAIL_GRIP"),
    "ASA_ssd_cage": ("SSD_XL - SSD_FLANGE_OVER", "SSD_FY0", "0 mm", "SSD_XR + SSD_FLANGE_OVER", "SSD_FY1",
                     "H_ - SSD_CAGE_TOP_GAP"),
    "ASA_ssd_sled": ("SSD_SX0", "SSD_Y0", "SSD_ZD0", "SSD_SX1", "SSD_GY1", "H_ + T_ - SLED_TOP_GAP"),
}

# Layout containers. Each is a list of rows; each row is a list of (component, count).
# The counts are the pieces to make (README "Cut and print list"). `fab_layout`
# compares them with the occurrences in the rack before it builds.
LASER = "FAB_laser_layout"
PRINT = "FAB_print_layout"
LAYOUTS: Dict[str, List[List[Tuple[str, int]]]] = {
    LASER: [
        [("LZR_bottom_plate", 1), ("LZR_top_plate", 1), ("LZR_rear_plate", 1)],
        [("LZR_side_wall", 2), ("LZR_tray_nuc", 1)],
        [("LZR_tray_sdr", 2)],
        [("LZR_tray_sdr", 2)],
    ],
    PRINT: [
        [("ASA_rail_strip_sdr_bot", 4), ("ASA_rail_strip_sdr_top", 4),
         ("ASA_rail_strip_nuc_bot", 1), ("ASA_rail_strip_nuc_top", 1)],
        [("TPU_rail_sdr", 8), ("TPU_rail_nuc", 2), ("ASA_ssd_cage", 1), ("ASA_ssd_sled", 1)],
        [("ASA_latch_sdr_preload", 8), ("ASA_latch", 3)],
    ],
}


def _min(part: str, axis: int) -> str:
    """Return the local minimum of a part along axis 0, 1 or 2 (x, y, z)."""
    return EXTENTS[part][axis]


def size(part: str, axis: int) -> str:
    """Return the size of a part along axis 0, 1 or 2 as an expression."""
    return f"(({EXTENTS[part][axis + 3]}) - ({EXTENTS[part][axis]}))"


def _sum(terms: Sequence[str]) -> str:
    """Add expression terms. Equal terms are grouped as 'n * (term)' to keep the text short."""
    if not terms:
        return "0 mm"
    counts = Counter(terms)
    parts = [t if n == 1 else f"{n} * {t}" for t, n in counts.items()]
    return " + ".join(parts)


def _max(terms: Sequence[str]) -> str:
    """Return the largest of the terms. Fusion max() takes two arguments, so nest them."""
    unique = list(dict.fromkeys(terms))
    out = unique[0]
    for term in unique[1:]:
        out = f"max({out}; {term})"
    return out


def _pieces(rows: List[List[Tuple[str, int]]]) -> List[List[str]]:
    """Expand (component, count) rows into rows of single pieces."""
    return [[part for part, count in row for _ in range(count)] for row in rows]


def _row_height(row: Sequence[str]) -> str:
    """Return the Y size of a row: the tallest piece in it."""
    return _max([size(p, 1) for p in row])


def placements(layout: str) -> List[dict]:
    """Return one row for each layout piece: {label, part, offset (x, y, z)}.

    offset is where the part LOCAL ORIGIN goes in the container frame. It is
    the piece corner (anchor) minus the part local minimum, so the part box
    starts at the anchor. Labels are 'fab_<component>_<n>' with n from 1.
    """
    out = []
    seen: Counter = Counter()
    rows = _pieces(LAYOUTS[layout])
    row_y: List[str] = []
    for row in rows:
        y0 = _sum(row_y)
        x_terms: List[str] = []
        for part in row:
            seen[part] += 1
            x0 = _sum(x_terms)
            offset = (f"{x0} - ({_min(part, 0)})", f"{y0} - ({_min(part, 1)})", f"-({_min(part, 2)})")
            out.append({"label": f"fab_{part}_{seen[part]}", "part": part, "offset": offset})
            x_terms += [size(part, 0), GAP]
        row_y += [_row_height(row), GAP]
    return out


def layout_size(layout: str) -> Tuple[str, str]:
    """Return the (X, Y) size of a whole layout as expressions."""
    rows = _pieces(LAYOUTS[layout])
    widths = [_sum([t for p in row for t in (size(p, 0), GAP)][:-1]) for row in rows]
    heights = [t for row in rows for t in (_row_height(row), GAP)][:-1]
    return f"({_max([f'({w})' for w in widths])})", f"({_sum(heights)})"


def container_offsets() -> Dict[str, Tuple[str, str, str]]:
    """Return where each layout container origin goes in the root (world) frame.

    Both layouts sit at +X beside the rack (the rack ends at X = W_ + T_). The
    print layout sits above the laser layout in +Y.
    """
    x0 = f"W_ + T_ + {SPACE}"
    _, laser_y = layout_size(LASER)
    return {LASER: (x0, "0 mm", "0 mm"), PRINT: (x0, f"{laser_y} + {SPACE}", "0 mm")}


def part_counts(layout: str) -> Dict[str, int]:
    """Return {component: pieces} for one layout."""
    counts: Counter = Counter()
    for row in LAYOUTS[layout]:
        for part, count in row:
            counts[part] += count
    return dict(counts)


def boxes(layout: str, env: Mapping[str, float]) -> List[Tuple[str, List[float]]]:
    """Evaluate each piece box in the container frame: (label, [x0, y0, z0, x1, y1, z1]) in mm."""
    out = []
    for row in placements(layout):
        off = [evaluate_expression(e, env) for e in row["offset"]]
        ext = [evaluate_expression(e, env) for e in EXTENTS[row["part"]]]
        out.append((row["label"], [off[i % 3] + ext[i] for i in range(6)]))
    return out


def overlaps(items: Sequence[Tuple[str, Sequence[float]]], gap_mm: float,
             tol_mm: float = 1e-6) -> List[Tuple[str, str]]:
    """Return the pairs of boxes whose XY distance is smaller than `gap_mm`.

    Why XY only: all pieces lie on one plane, so two pieces touch only when
    their footprints come closer than the gap.
    """
    bad = []
    for i, (la, a) in enumerate(items):
        for lb, b in items[i + 1:]:
            dx = max(b[0] - a[3], a[0] - b[3])
            dy = max(b[1] - a[4], a[1] - b[4])
            if max(dx, dy) < gap_mm - tol_mm:
                bad.append((la, lb))
    return bad
