"""Build and check the laser and print layouts in flight_rack_asm (needs Fusion).

Why this module exists: the user exports the cut and print files from Fusion
by hand. They want every laser part flat on one layout and every printed part
on another, and both must follow model edits (for example a new sheet
thickness `T_`). `fab_plan.py` makes the expressions; this module turns them
into Fusion objects:

  * one container component for each layout (`FAB_laser_layout`,
    `FAB_print_layout`), placed at the root by a rigid joint;
  * inside it, one occurrence of the SAME rack component for each piece to
    make, placed by a rigid joint to a joint origin with expression offsets.

Because the pieces are occurrences of the rack components, a change to a part
shows in the layout at once. Because the offsets are expressions, the spacing
follows the part sizes. A change to a piece COUNT (for example a fifth SDR
tray) needs `remove` and `build` again.

The layouts do not touch the rack: no rack component, joint or parameter
changes. Only the two seed parameters `FAB_GAP` and `FAB_SPACE` are created
when they are missing.

Fusion fault (found by test 2026-10-21, see also README, blades): every
occurrence of the FIRST component of the document is pinned at the origin.
In flight_rack_asm that is `LZR_bottom_plate`. A joint to it fails ("conflicts
with assembly relationships"), at the root and nested. A new transform and a
position snapshot both snap back to the origin. Arrange refuses it ("pinned").
So the bottom plate piece is not an occurrence. It is a linked body copy
(CopyPasteBody feature) of the rack bottom plate inside the container,
moved into its place by a Move feature with expression distances. The copy
follows the source: after T_ 3 -> 4 mm its volume equalled the source volume.
"""
from __future__ import annotations

import os
from collections import Counter
from typing import Callable, Dict, List, Optional

import adsk.core
import adsk.fusion

import fab_plan as fp
import fusion_lib as fl

HERE = os.path.dirname(os.path.abspath(__file__))
SEED_PATH = os.path.join(HERE, "rack_seed.toml")
CATEGORY = "fab"          # HERON/category of the containers; apply_materials.py ignores it
ORIGIN_JO = "JO_origin"   # joint origin at the container origin
_MM_PER_CM = 10.0


def _rack_counts(design: adsk.fusion.Design) -> Counter:
    """Count the occurrences of each component in the rack (everything outside the layouts)."""
    counts: Counter = Counter()
    for occ in design.rootComponent.allOccurrences:
        if not occ.fullPathName.startswith(tuple(fp.LAYOUTS)):
            counts[occ.component.name] += 1
    return counts


def _check_counts(design: adsk.fusion.Design) -> None:
    """Refuse to build when the layout piece counts differ from the rack.

    Why: the layout lists the pieces to make. If the rack gets a new tray or
    latch, the layout must change too. A silent difference gives a wrong cut.
    """
    rack = _rack_counts(design)
    problems = []
    for layout in fp.LAYOUTS:
        for part, count in fp.part_counts(layout).items():
            if rack[part] != count:
                problems.append(f"{part}: layout {count}, rack {rack[part]}")
    if problems:
        raise fl.ConfigError("Layout counts differ from the rack. Edit fab_plan.LAYOUTS: " + "; ".join(problems))


def _rigid_to_origin(owner: adsk.fusion.Component, occ: adsk.fusion.Occurrence,
                     parent_jo: adsk.fusion.JointOrigin, name: str) -> adsk.fusion.Joint:
    """Join the ORIGIN of a nested occurrence to a joint origin of `owner` (rigid).

    Why the origin point and not a joint origin of the part: some parts have
    no joint origin at their local origin (the latches), and the layout
    must not edit the rack parts. A proxy of the origin construction point
    works for all of them.
    """
    point = occ.component.originConstructionPoint.createForAssemblyContext(occ)
    inp = owner.joints.createInput(adsk.fusion.JointGeometry.createByPoint(point), parent_jo)
    inp.setAsRigidJointMotion()
    joint = owner.joints.add(inp)
    joint.name = name
    return joint


def _first_occurrence(design: adsk.fusion.Design) -> adsk.fusion.Occurrence:
    """Return the rack occurrence of the first component of the document (see the module text)."""
    return design.rootComponent.occurrences.item(0)


def _place_body_copy(comp: adsk.fusion.Component, source: adsk.fusion.Occurrence,
                     move: tuple, label: str) -> adsk.fusion.BRepBody:
    """Put a linked copy of the bodies of `source` into `comp` and move it by `move`.

    The copy lands at the WORLD position of the source, seen from the
    container. The source is the rack part with its local frame on the world
    frame (the grounded bottom plate), so a move by (container offset + piece
    offset) in container space puts the copy where an occurrence would go.
    """
    bodies = adsk.core.ObjectCollection.create()
    for i in range(source.bRepBodies.count):
        bodies.add(source.bRepBodies.item(i))
    copy = comp.features.copyPasteBodies.add(bodies)
    copy.name = f"copy_{label}"
    moved = adsk.core.ObjectCollection.create()
    for i in range(copy.bodies.count):
        moved.add(copy.bodies.item(i))
    inp = comp.features.moveFeatures.createInput2(moved)
    inp.defineAsTranslateXYZ(*(adsk.core.ValueInput.createByString(e) for e in move), False)
    feat = comp.features.moveFeatures.add(inp)
    feat.name = f"move_{label}"
    body = feat.bodies.item(0)
    body.name = label
    body.attributes.add("HERON", "part", source.component.name)
    return body


def _layout_items(design: adsk.fusion.Design, layout: str) -> List[tuple]:
    """Return every piece of a layout as (name, component name, world box in mm).

    Pieces are the nested occurrences and the linked body copies of the container.
    """
    cocc = next((o for o in design.rootComponent.occurrences if o.component.name == layout), None)
    if cocc is None:
        return []
    items = [(o.name, o.component.name, _world_box(o)) for o in cocc.childOccurrences]
    for i in range(cocc.bRepBodies.count):
        body = cocc.bRepBodies.item(i)
        native = body.nativeObject or body     # attributes live on the native body, not the proxy
        attr = native.attributes.itemByName("HERON", "part")
        items.append((body.name, attr.value if attr else body.name, _body_box(body)))
    return items


def build(design: adsk.fusion.Design, log: Callable[[str], None] = print) -> Dict[str, int]:
    """Create both layouts. Refuse when one already exists (call `remove` first)."""
    for layout in fp.LAYOUTS:
        if design.allComponents.itemByName(layout) is not None:
            raise fl.ConfigError(f"'{layout}' exists already. Run remove() first.")
    created = fl.create_missing_parameters(design, fl.param_specs(fl.load_config(SEED_PATH)))
    log(f"parameters created: {created}")
    _check_counts(design)
    ctx = fl.ctx_from_design(design)
    root = design.rootComponent
    first = _first_occurrence(design)
    log(f"first component (linked body copy): {first.component.name}")
    report = {}
    for layout, offset in fp.container_offsets().items():
        cocc = root.occurrences.addNewComponent(adsk.core.Matrix3D.create())
        comp = cocc.component
        comp.name = layout
        fl.set_category(comp, CATEGORY)
        fl.label_occurrence(ctx, cocc, layout)
        child_jo = fl.joint_origin(comp, ORIGIN_JO)
        root_jo = fl.joint_origin(root, f"JO_{layout}", offset)
        fl.make_joint(ctx, "rigid", cocc, child_jo, root_jo, f"J_{layout}")
        rows = fp.placements(layout)
        for row in rows:
            if row["part"] == first.component.name:
                move = tuple(f"({a}) + ({b})" for a, b in zip(offset, row["offset"], strict=True))
                _place_body_copy(comp, first, move, row["label"])
                continue
            part = design.allComponents.itemByName(row["part"])
            occ = fl.nest_occurrence(comp, part)
            occ.attributes.add("HERON", "label", row["label"])
            jo = fl.joint_origin(comp, f"JO_{row['label']}", row["offset"])
            _rigid_to_origin(comp, occ, jo, f"J_{row['label']}")
        fl.show_tree(cocc)
        report[layout] = len(rows)
        log(f"{layout}: {len(rows)} pieces")
    return report


def remove(design: adsk.fusion.Design, log: Callable[[str], None] = print) -> List[str]:
    """Delete both layouts: the container occurrences and the root joint origins."""
    root = design.rootComponent
    gone = []
    for layout in fp.LAYOUTS:
        for i in reversed(range(root.occurrences.count)):
            occ = root.occurrences.item(i)
            if occ.component.name == layout:
                gone.append(occ.name)
                occ.deleteMe()
        jo = root.jointOrigins.itemByName(f"JO_{layout}")
        if jo is not None:
            jo.deleteMe()
    log(f"removed: {gone}")
    return gone


def _world_box(occ: adsk.fusion.Occurrence) -> Optional[List[float]]:
    """Return the world box of all bodies under an occurrence in mm: [x0, y0, z0, x1, y1, z1].

    Why body proxies: `Occurrence.boundingBox` was stale after the joints
    moved the pieces in the same script (found by test). The box of a body
    proxy is in world space and is current. Returns None when there is no body.
    """
    bodies = []
    stack = [occ]
    while stack:                       # walk the nested occurrences (a blade holds 3)
        cur = stack.pop()
        bodies += list(cur.bRepBodies)
        stack += list(cur.childOccurrences)
    return _union([_body_box(b) for b in bodies]) if bodies else None


def _body_box(body: adsk.fusion.BRepBody) -> List[float]:
    """Return the box of one body (a proxy gives world space) in mm."""
    bb = body.boundingBox
    return [v * _MM_PER_CM for v in (bb.minPoint.x, bb.minPoint.y, bb.minPoint.z,
                                     bb.maxPoint.x, bb.maxPoint.y, bb.maxPoint.z)]


def _union(boxes: List[List[float]]) -> List[float]:
    """Return the box that holds all the given boxes."""
    return [min(b[i] for b in boxes) for i in range(3)] + [max(b[i] for b in boxes) for i in range(3, 6)]


def check(design: adsk.fusion.Design, log: Callable[[str], None] = print) -> Dict[str, object]:
    """Check the built layouts against the real Fusion geometry.

    Checks: each layout has every piece; no two pieces come closer than
    FAB_GAP; every piece lies on the layout plane (z = 0); the layouts are
    clear of the rack and of each other; no timeline item has a problem.
    Returns a report. It never changes the design.
    """
    gap = design.userParameters.itemByName(fp.GAP).value * _MM_PER_CM
    root = design.rootComponent
    report: Dict[str, object] = {}
    layout_boxes = {}
    rack = [_world_box(o) for o in root.occurrences if o.component.name not in fp.LAYOUTS]
    rack_box = _union([b for b in rack if b is not None])
    for layout in fp.LAYOUTS:
        found = _layout_items(design, layout)
        if not found:
            report[layout] = "missing"
            continue
        items = [(name, box) for name, _part, box in found]
        counts = Counter(part for _name, part, _box in found)
        layout_boxes[layout] = _union([b for _, b in items])
        report[layout] = {
            "pieces": len(items),
            "counts_ok": dict(counts) == fp.part_counts(layout),
            "overlaps": fp.overlaps(items, gap, tol_mm=1e-3),
            "off_plane": [n for n, b in items if abs(b[2]) > 1e-3],
        }
    named = list(layout_boxes.items()) + [("rack", rack_box)]
    report["layout_clashes"] = fp.overlaps(named, 0.0)
    report["unhealthy"] = [design.timeline.item(i).name for i in range(design.timeline.count)
                           if design.timeline.item(i).healthState != 0]
    for key, value in report.items():
        text = str(value)
        log(f"{key}: {text if len(text) <= 600 else text[:600] + ' ...'}")
    return report
