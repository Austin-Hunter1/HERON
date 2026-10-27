"""Entry point of the flight_rack builder.

Why: one place lists the part modules and where each occurrence goes. A later
phase adds a part module to PART_BUILDERS and rows to PLACEMENTS. Nothing else
changes.

Run in Fusion through `run_in_fusion.py` (ENTRY = "build_flight_rack").
`build(design)` works in an existing design. `main()` makes a NEW dev document.
`build_subset(...)` builds only some parts (short MCP calls, no freeze).
`extend(...)` adds parts to a design that is already built; Fusion is master,
so it never changes an existing parameter or part.
"""
import os
import time
from typing import Callable, Dict, Iterable, List, Optional

import fusion_lib as fl
import kicad_clock as kc
from parts import (blade, clock_board_ref, latch, plate, rear_plate, ref_parts, side_wall, ssd_cage, ssd_sled,
                   strip, tpu_rail, tray)

HERE = os.path.dirname(os.path.abspath(__file__))
SEED_PATH = os.path.join(HERE, "rack_seed.toml")

# Part registry: component name -> function(ctx) that builds it.
# A later phase adds its part modules here.
PART_BUILDERS = {
    plate.BOTTOM: lambda ctx: plate.build(ctx, top=False),
    plate.TOP: lambda ctx: plate.build(ctx, top=True),
    rear_plate.NAME: rear_plate.build,
    side_wall.NAME: side_wall.build,
    "TPU_rail_sdr": lambda ctx: tpu_rail.build(ctx, "TPU_rail_sdr"),
    "TPU_rail_nuc": lambda ctx: tpu_rail.build(ctx, "TPU_rail_nuc"),
    tray.SDR: lambda ctx: tray.build(ctx, "sdr"),
    tray.NUC: lambda ctx: tray.build(ctx, "nuc"),
    # Rail strips (phase 3b). The _top strip is the mirrored geometry (same function, top flag).
    **{strip.strip_name(kind, top): (lambda ctx, kind=kind, top=top: strip.build(ctx, kind, top))
       for kind in ("sdr", "nuc") for top in (False, True)},
    # Swing latches (phase 4a). One component for each type: the top latches reuse it.
    **{latch.latch_name(preload): (lambda ctx, preload=preload: latch.build(ctx, preload))
       for preload in (True, False)},
    # SSD bay (phase 4b). World-axes parts without a local frame.
    ssd_cage.NAME: ssd_cage.build,
    ssd_sled.NAME: ssd_sled.build,
    # Reference envelopes (phase 5). Category "ref": no material, translucent look.
    clock_board_ref.NAME: clock_board_ref.build,
    ref_parts.ADAPTER: ref_parts.build_adapter,
    ref_parts.SDR: ref_parts.build_sdr,
    ref_parts.NUC: ref_parts.build_nuc,
    ref_parts.SSD: ref_parts.build_ssd,
    # A blade needs its tray and rail components first: keep these two last.
    "BLADE_sdr": lambda ctx: blade.build(ctx, "sdr"),
    "BLADE_nuc": lambda ctx: blade.build(ctx, "nuc"),
}

# Placement table. One row for each OCCURRENCE.
#   label   occurrence name (the CadQuery part name where there is one)
#   part    component name in PART_BUILDERS
#   joint   'ground', 'rigid', 'slider' or 'revolute'; None = no joint yet
#   offset  (dx, dy, dz) expressions, world axes, where the part local origin goes
#   frame   (u, v, w) = the part local x, y, z axes in world axes (like place())
# RACK_Z0 turns CadQuery world Z into Fusion world Z (the bottom plate is grounded
# at the Fusion origin, so the CadQuery z=0 plane is RACK_Z0 higher).
PLACEMENTS: List[dict] = [
    dict(label="bottom_plate", part="LZR_bottom_plate", joint="ground"),
    dict(label="top_plate", part="LZR_top_plate", joint="rigid",
         offset=("0 mm", "0 mm", "H_ + RACK_Z0"), frame=fl.IDENTITY),
    dict(label="side_wall_L", part="LZR_side_wall", joint="rigid",
         offset=("-T_", "0 mm", "RACK_Z0"), frame=((0, 1, 0), (0, 0, 1), (1, 0, 0))),
    dict(label="side_wall_R", part="LZR_side_wall", joint="rigid",
         offset=("W_", "0 mm", "RACK_Z0"), frame=((0, 1, 0), (0, 0, 1), (1, 0, 0))),
    dict(label="rear_plate", part="LZR_rear_plate", joint="rigid",
         offset=("0 mm", "REAR_Y0 + T_", "RACK_Z0"), frame=((1, 0, 0), (0, 0, 1), (0, -1, 0))),
]


def blade_rows(slots: Iterable[dict]) -> List[dict]:
    """Make one placement row for each slot (blade). Slot data come from the seed.

    Each blade slides along world Y. Slide value 0 is the installed position.
    The root joint origin sits on the tray centre plane of the slot.
    """
    return [dict(label=slot["name"], part=blade.blade_name(slot["kind"]), joint="slider", axis="Y",
                 offset=(f"TRAY_X_{slot['name']}", "0 mm", "RACK_Z0"), frame=fl.IDENTITY)
            for slot in slots]


def strip_rows(slots: Iterable[dict]) -> List[dict]:
    """Make the rail strip rows: a bottom and a top strip for each slot.

    Both are rigid. The bottom strip origin is (TRAY_X_<slot>, 0, RACK_Z0). The
    top strip is its own mirrored component (z -> -z), so its origin sits at
    z = H_ + RACK_Z0 with the identity frame. This is `mirror_top` (z -> H - z).
    """
    rows = []
    for slot in slots:
        kind = blade.kind_of_slot(slot["kind"])
        x = f"TRAY_X_{slot['name']}"
        rows.append(dict(label=f"strip_bot_{slot['name']}", part=strip.strip_name(kind, False), joint="rigid",
                         offset=(x, "0 mm", "RACK_Z0"), frame=fl.IDENTITY))
        rows.append(dict(label=f"strip_top_{slot['name']}", part=strip.strip_name(kind, True), joint="rigid",
                         offset=(x, "0 mm", "H_ + RACK_Z0"), frame=fl.IDENTITY))
    return rows


def latch_rows(slots: Iterable[dict]) -> List[dict]:
    """Make the swing latch rows: a bottom and a top latch for each slot, and the hatch latch.

    All are revolute joints about the pivot screw axis. Angle 0 is the CadQuery
    position. The child joint origin is `JO_pivot` (the pivot hole centre). The
    parent joint origin sits on the pivot axis: x = TRAY_X + STRIP_W/2 + EAR_W/2
    (the strip ear centre line, where the pivot insert is), y = 0, z = LATCH_Z
    (top: H_ - LATCH_Z). SDR slots use the preload latch, the NUC slot the plain one.
    The top latch is another occurrence of the SAME component, with the same
    frame: `mirror_top` of a latch equals a move in Z, because the latch is
    symmetric (see parts/latch.py).
    The hatch latch lies on the top plate: pivot on the hatch latch hole, axis world Z.
    """
    rows = []
    for slot in slots:
        preload = blade.kind_of_slot(slot["kind"]) == "sdr"
        x = f"TRAY_X_{slot['name']} + STRIP_W / 2 + EAR_W / 2"
        for side, z in (("bot", "LATCH_Z"), ("top", "H_ - LATCH_Z")):
            rows.append(dict(label=f"latch_{side}_{slot['name']}", part=latch.latch_name(preload),
                             joint="revolute", axis="Z", child_jo=latch.PIVOT_JO,
                             offset=(x, "0 mm", f"{z} + RACK_Z0"), frame=latch.SIDE_FRAME))
    rows.append(dict(label="latch_ssd_hatch", part=latch.latch_name(False), joint="revolute", axis="Z",
                     child_jo=latch.PIVOT_JO, offset=("SSD_LATCH_X", "SSD_LATCH_Y", "H_ + T_ + RACK_Z0"),
                     frame=latch.HATCH_FRAME))
    return rows


def ssd_rows() -> List[dict]:
    """Make the SSD bay rows: the cage (rigid) and the sled (slider along world Z).

    Both parts are modelled in world axes, so the joint offset is the identity
    plus RACK_Z0 in Z. Slide value 0 is the installed position (the CadQuery
    position). The sled has no travel limit, because none is specified: it is
    pulled up through the hatch of the top plate by hand.
    """
    origin = ("0 mm", "0 mm", "RACK_Z0")
    return [dict(label="ssd_cage", part=ssd_cage.NAME, joint="rigid", offset=origin, frame=fl.IDENTITY),
            dict(label="ssd_sled", part=ssd_sled.NAME, joint="slider", axis="Z", offset=origin, frame=fl.IDENTITY)]


def ref_rows(slots: Iterable[dict]) -> List[dict]:
    """Make the reference rows: the clock board, 8 SMP adapters, the NUC envelope and the SSD.

    All are rigid joints at the ROOT (D-035 open question: a NUC envelope in
    BLADE_nuc, or the SSD on the sled, is not decided). The SDR board stand-ins
    REF_B210_1 ... REF_B200_2 are boxes (D-048), because the Ettus STEP files
    are missing.
      clock board  local frame = the rear plate frame; origin on the board REAR face
                   (CLK_Y0 + CLK_PCB_T), so the board lies toward -Y from it.
      adapter      origin on the SDR SMA axis at the SMA tip plane; the cylinder runs toward +Y.
      SDR, NUC     origin at the board bottom face X, local frame like a tray.
      SSD          world axes.
    """
    rows = [dict(label=clock_board_ref.NAME, part=clock_board_ref.NAME, joint="rigid",
                 offset=("0 mm", "CLK_Y0 + CLK_PCB_T", "RACK_Z0"), frame=((1, 0, 0), (0, 0, 1), (0, -1, 0)))]
    for slot in slots:
        if slot["kind"] in ("b210", "b200"):
            rows.append(dict(label=f"REF_{slot['name']}", part=ref_parts.SDR, joint="rigid",
                             offset=(f"TRAY_X_{slot['name']} + T_ / 2 + STANDOFF", "0 mm", "RACK_Z0"),
                             frame=ref_parts.NUC_FRAME))
            for key, param in ref_parts.ADAPTER_KEYS.items():
                rows.append(dict(label=f"REF_adapter_{slot['name']}_{key}", part=ref_parts.ADAPTER, joint="rigid",
                                 offset=(f"TRAY_X_{slot['name']} + T_ / 2 + STANDOFF + SMA_BZ", "SDR_SMA_TIP_Y",
                                         f"Z_BOARD + {param} + RACK_Z0"),
                                 frame=ref_parts.ADAPTER_FRAME))
        elif slot["kind"] == "nuc":
            rows.append(dict(label=ref_parts.NUC, part=ref_parts.NUC, joint="rigid",
                             offset=(f"TRAY_X_{slot['name']} + T_ / 2 + STANDOFF", "0 mm", "RACK_Z0"),
                             frame=ref_parts.NUC_FRAME))
    rows.append(dict(label=ref_parts.SSD, part=ref_parts.SSD, joint="rigid",
                     offset=("0 mm", "0 mm", "RACK_Z0"), frame=fl.IDENTITY))
    return rows


def placement_rows(cfg: dict) -> List[dict]:
    """Return all placement rows: the frame rows, the blades, the rail strips, the latches, the SSD bay and the refs."""
    return (PLACEMENTS + blade_rows(cfg["slots"]) + strip_rows(cfg["slots"]) + latch_rows(cfg["slots"])
            + ssd_rows() + ref_rows(cfg["slots"]))


def kicad_specs(cfg) -> list:
    """Read the clock board KiCad file and return the CLK_J<n>_* parameter specs."""
    pcb = os.path.normpath(os.path.join(HERE, cfg["kicad"]["pcb"]))
    jacks = kc.read_clock_board(pcb, cfg["kicad"]["origin"])
    if not jacks:
        raise fl.ConfigError(f"No jacks found in KiCad file: {pcb}")
    return kc.jack_specs(jacks)


def seed(design, cfg) -> int:
    """Create all User Parameters from the seed plus the KiCad values."""
    specs = fl.param_specs(cfg) + kicad_specs(cfg)
    return fl.seed_parameters(design, specs)


def place(ctx: fl.Ctx, row: dict, first: Dict[str, bool]) -> None:
    """Make (or reuse) the occurrence of one placement row and join it."""
    comp = ctx.components[row["part"]]
    # The first occurrence is the one that new_component made in this run. A
    # component from an earlier run has none: then a new occurrence is added.
    if row["part"] in ctx.first_occ and first.get(row["part"]) is None:
        first[row["part"]] = True
        occ = ctx.first_occ[row["part"]]
        fl.label_occurrence(ctx, occ, row["label"])
    else:
        occ = fl.add_occurrence(ctx, comp, row["label"])
    fl.show_tree(occ)       # nested parts (blade rails) can come back hidden
    if row["joint"] == "ground":
        fl.ground(occ)
        return
    child_name = row.get("child_jo", "JO_local")     # a latch uses its pivot origin
    child_jo = comp.jointOrigins.itemByName(child_name)
    if child_jo is None:
        child_jo = fl.joint_origin(comp, child_name)
    parent_jo = fl.joint_origin(ctx.root, "JO_" + row["label"], row["offset"], row["frame"])
    fl.make_joint(ctx, row["joint"], occ, child_jo, parent_jo, "J_" + row["label"], axis=row.get("axis", "Z"))


def _context(design, cfg: dict) -> fl.Ctx:
    """Make a Ctx for a design that has the parameters. Add the seed slots and the KiCad jacks."""
    ctx = fl.ctx_from_design(design)
    ctx.slots = cfg["slots"]
    ctx.ref_look = cfg["ref_look"]
    ctx.jacks = kc.read_clock_board(os.path.normpath(os.path.join(HERE, cfg["kicad"]["pcb"])), cfg["kicad"]["origin"])
    return ctx


def _build_and_place(ctx: fl.Ctx, cfg: dict, parts: Iterable[str], labels: Iterable[str],
                     log: Callable[[str], None]) -> Dict[str, List[str]]:
    """Build the listed parts that are not in the design yet, then add the listed placements.

    Why one function: `build_subset`, `extend` and `build` differ only in how
    they treat the parameters. The part and placement steps are the same.
    Parts are built in PART_BUILDERS order (a blade needs its tray and rail).
    A label that is already in the design is skipped.
    """
    wanted, wanted_labels = set(parts), set(labels)
    unknown = (wanted - set(PART_BUILDERS)) | (wanted_labels - {r["label"] for r in placement_rows(cfg)})
    if unknown:
        raise fl.ConfigError(f"Unknown part or placement label: {sorted(unknown)}")
    report: Dict[str, List[str]] = {"built": [], "kept": [], "placed": [], "skipped": []}
    for name, builder in PART_BUILDERS.items():
        if name not in wanted:
            continue
        if name in ctx.components:
            report["kept"].append(name)
            continue
        log(f"build {name}")
        builder(ctx)
        report["built"].append(name)
    ctx.design.activateRootComponent()      # new_component leaves the last part active
    first: Dict[str, bool] = {}
    for row in placement_rows(cfg):
        if row["label"] not in wanted_labels:
            continue
        if row["label"] in ctx.occurrences:
            report["skipped"].append(row["label"])
            continue
        log(f"place {row['label']}")
        place(ctx, row, first)
        report["placed"].append(row["label"])
    return report


def build_subset(design, parts: Iterable[str], labels: Iterable[str], cfg=None,
                 log: Callable[[str], None] = print) -> fl.Ctx:
    """Seed ALL parameters, then build only `parts` and place only `labels`.

    Why: the full build takes about one minute, which is too close to the
    MCP time limit. A subset keeps each call short. A part whose parent is
    not built is still placed: the joint origins are in the root.
    """
    cfg = cfg or fl.load_config(SEED_PATH)
    seed(design, cfg)
    ctx = _context(design, cfg)
    _build_and_place(ctx, cfg, parts, labels, log)
    return ctx


def extend(design, parts: Iterable[str], labels: Iterable[str], cfg=None,
           log: Callable[[str], None] = print) -> Dict[str, List[str]]:
    """Add parts and placements to a design that is already built.

    Fusion is master (D-035): this function CREATES missing User Parameters
    and never updates an existing one. It builds only listed parts that are
    not in the design and adds only listed placements that are not in it.
    Rails that exist as hidden root occurrences are reused: the blade nests
    new occurrences of the same component, and the stale root occurrences are
    deleted (cleaner than hidden leftovers: no loose parts in the browser, and
    no part counted twice in mass or interference checks).
    Returns the report {built, kept, placed, skipped}; the created parameter
    names are under the key "parameters".
    """
    cfg = cfg or fl.load_config(SEED_PATH)
    specs = fl.param_specs(cfg) + kicad_specs(cfg)
    created = fl.create_missing_parameters(design, specs)
    log(f"parameters created: {len(created)} {created}")
    ctx = _context(design, cfg)
    report = _build_and_place(ctx, cfg, parts, labels, log)
    report["parameters"] = created
    return report


def build(design, cfg=None) -> fl.Ctx:
    """Seed parameters, build all parts, place all occurrences. Return the Ctx."""
    cfg = cfg or fl.load_config(SEED_PATH)
    seed(design, cfg)
    ctx = _context(design, cfg)
    labels = [r["label"] for r in placement_rows(cfg)]
    _build_and_place(ctx, cfg, list(PART_BUILDERS), labels, lambda _msg: None)
    return ctx


def main() -> None:
    """Build the rack into a NEW untitled development document."""
    _doc, design = fl.new_dev_document()
    ctx = build(design)
    print("built", len(ctx.components), "components,", len(ctx.occurrences), "occurrences")
