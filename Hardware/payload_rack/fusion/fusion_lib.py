"""Shared Fusion helpers for the flight_rack builder.

Why this module exists: every part of the rack is built with the same few
steps. A part needs parameters, sketches that are fixed by dimensions,
extrudes, joint origins and joints. Parts call these helpers and never call
the raw Fusion API for these steps. A change in method then needs a change in
one place only.

Rules that this module enforces (see FUSION_SPEC.md):
  * Fusion length unit is cm. All lengths pass as expression STRINGS with
    units ("3 mm", "W_ + 2 * T_"). Never pass raw cm floats as a size.
  * Every sketch size and position is a dimension whose expression uses
    User Parameters. A parameter change then moves the geometry.
  * Sketch dimensions are unsigned. The entity is drawn on the correct side
    first (numbers come from the same expressions), then dimensioned.
  * Only sketches on the XY plane (or an offset plane parallel to it) are
    used, so sketch (x, y) equals component (X, Y). Parts that need other
    views use `plane_sketch` with the plane name.

Fusion API names come from the Fusion API documentation. This module imports
`adsk` at the top, so only Fusion can import it. Pure logic lives in
`rack_config.py` and `kicad_clock.py`.
"""
from __future__ import annotations

import math
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import adsk.core
import adsk.fusion

from rack_config import (ConfigError, ParamSpec, evaluate_expression, load_config,
                         param_specs)

__all__ = [
    "load_config", "param_specs", "ConfigError", "Ctx", "new_dev_document",
    "seed_parameters", "read_env", "new_component", "add_occurrence", "label_occurrence",
    "plane_sketch", "offset_plane", "sk_rect", "sk_rounded_rect", "sk_circle",
    "sk_slot", "extrude", "grid_windows", "pattern_rect", "joint_origin",
    "make_joint", "ground", "measure", "compare_params", "set_category",
    "apply_ref_look", "IDENTITY", "flip_pos", "flip_range", "ctx_from_design",
    "create_missing_parameters", "nest_occurrence", "root_occurrences_of",
    "delete_root_occurrences", "show_tree",
]

IDENTITY = ((1, 0, 0), (0, 1, 0), (0, 0, 1))   # frame columns u, v, w
_AXES = ((1, 0, 0), (0, 1, 0), (0, 0, 1))
_ZERO_TOL = 1e-9
_MM_PER_CM = 10.0
_CM_PER_MM = 0.1


def _vi(expr: str) -> adsk.core.ValueInput:
    """Make a ValueInput from an expression string (the only way we pass lengths)."""
    return adsk.core.ValueInput.createByString(expr)


def _pt(x_mm: float, y_mm: float) -> adsk.core.Point3D:
    """Make a sketch point from mm numbers. Used for DRAWING only, then dimensions fix it."""
    return adsk.core.Point3D.create(x_mm * _CM_PER_MM, y_mm * _CM_PER_MM, 0.0)


_JITTER_MAX_MM = 0.1          # largest drawing offset (mm). See _jit.
_GOLDEN = 0.6180339887498949  # spreads offsets so that no two are close
_jit_count = [0]


def _jit() -> float:
    """Return a small drawing offset in mm.

    Why: Fusion adds automatic alignment constraints when new geometry shares
    a coordinate with existing geometry (found by test: a second rectangle at
    the same x or y gave 'over constrained' on its dimension). Every drawn
    coordinate gets its own tiny offset, so no two coordinates match. The
    dimensions then move the geometry to the exact value, so the final size
    is exact. The offset never reaches the result.
    """
    _jit_count[0] += 1
    return _JITTER_MAX_MM * (0.05 + 0.95 * ((_jit_count[0] * _GOLDEN) % 1.0))


def _neg_if(expr: str, value: float) -> str:
    """Return an expression that is positive. Dimensions are unsigned."""
    return expr if value >= 0 else f"-({expr})"


# ---------------------------------------------------------------- context
class Ctx:
    """State for one build: the design, its root, and parameter values.

    Why a class: parts need the design, the root component and a way to turn
    an expression into a number (to draw on the correct side). One object
    carries these, so the part functions keep a narrow interface.
    """

    def __init__(self, design: adsk.fusion.Design):
        """Store the design and read all User Parameter values."""
        self.design = design
        self.root = design.rootComponent
        self.env: Dict[str, float] = {}
        self.occurrences: Dict[str, adsk.fusion.Occurrence] = {}
        self.components: Dict[str, adsk.fusion.Component] = {}
        self.first_occ: Dict[str, adsk.fusion.Occurrence] = {}   # occurrence made with each new component
        self.slots: List[dict] = []         # seed [[slots]] rows (set by the builder)
        self.jacks: Dict[str, object] = {}  # KiCad jacks {ref: kicad_clock.Jack} (set by the builder)
        self.ref_look: Dict[str, object] = {}   # seed [ref_look] table: look of the "ref" parts (set by the builder)
        self.refresh()

    def refresh(self) -> None:
        """Re-read the parameter values. Call after you change a parameter."""
        self.env = read_env(self.design)

    def num(self, expr: str) -> float:
        """Evaluate an expression to a number in mm (radians for angles)."""
        return evaluate_expression(expr, self.env)


def ctx_from_design(design: adsk.fusion.Design) -> "Ctx":
    """Rebuild a Ctx for a design that is already built (for example after a timeout).

    Components are found by name. Occurrences are found by their HERON/label
    attribute. `slots` and `jacks` stay empty: only builders need them.
    """
    ctx = Ctx(design)
    for i in range(design.allComponents.count):
        comp = design.allComponents.item(i)
        ctx.components[comp.name] = comp
    for i in range(design.rootComponent.occurrences.count):
        occ = design.rootComponent.occurrences.item(i)
        attr = occ.attributes.itemByName("HERON", "label")
        if attr is not None:
            ctx.occurrences[attr.value] = occ
    return ctx


def read_env(design: adsk.fusion.Design) -> Dict[str, float]:
    """Return {name: value} for all User Parameters (mm, radians, or plain)."""
    env: Dict[str, float] = {}
    params = design.userParameters
    for i in range(params.count):
        p = params.item(i)
        env[p.name] = p.value * _MM_PER_CM if p.unit in ("mm", "cm") else p.value
    return env


def new_dev_document(close_untitled: bool = True) -> Tuple[adsk.core.Document, adsk.fusion.Design]:
    """Open a NEW parametric design document for development.

    Why: the lead builds the real `flight_rack`. Agents develop in an
    untitled document and close it without saving. Only untitled documents
    that this function finds are closed (name starts with "Untitled" AND the
    document was never saved).
    """
    app = adsk.core.Application.get()
    if close_untitled:
        for doc in list(app.documents):
            # Only unsaved documents named "Untitled*" are closed. A saved
            # document such as `flight_rack` is never touched.
            if doc.name.startswith("Untitled") and not doc.isSaved:
                doc.close(False)
    doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    design = adsk.fusion.Design.cast(app.activeProduct)
    design.designType = adsk.fusion.DesignTypes.ParametricDesignType
    return doc, design


# ---------------------------------------------------------------- parameters
def seed_parameters(design: adsk.fusion.Design, specs: Iterable[ParamSpec]) -> int:
    """Create or update User Parameters from specs. Return how many were set.

    Why update too: `sync_kicad.py` uses this function to change only some
    values in an existing design. An existing parameter keeps its name; its
    expression and comment change.
    """
    params = design.userParameters
    count = 0
    for spec in specs:
        unit = "" if spec.unit == "none" else spec.unit
        existing = params.itemByName(spec.name)
        if existing is None:
            params.add(spec.name, _vi(spec.expression), unit, spec.comment)
        else:
            existing.expression = spec.expression
            existing.comment = spec.comment
        count += 1
    return count


def create_missing_parameters(design: adsk.fusion.Design, specs: Iterable[ParamSpec]) -> List[str]:
    """Create the User Parameters that do not exist yet. Return their names.

    Why: Fusion is the master model (D-035). A user may have changed a
    parameter in Fusion. This function never touches an existing parameter,
    not even its comment. `seed_parameters` is for a new design only.
    """
    params = design.userParameters
    created: List[str] = []
    for spec in specs:
        if params.itemByName(spec.name) is not None:
            continue
        unit = "" if spec.unit == "none" else spec.unit
        params.add(spec.name, _vi(spec.expression), unit, spec.comment)
        created.append(spec.name)
    return created


# ---------------------------------------------------------------- components
def set_category(comp: adsk.fusion.Component, category: str) -> None:
    """Store the HERON/category attribute. apply_materials.py reads it."""
    attr = comp.attributes.itemByName("HERON", "category")
    if attr is None:
        comp.attributes.add("HERON", "category", category)
    else:
        attr.value = category


def new_component(ctx: Ctx, name: str, category: str) -> adsk.fusion.Component:
    """Create one new component (one part TYPE) at the root and tag it.

    The first occurrence sits at the origin (see `Ctx.first_occ`). Joints
    move it later.
    """
    occ = ctx.root.occurrences.addNewComponent(adsk.core.Matrix3D.create())
    comp = occ.component
    comp.name = name
    set_category(comp, category)
    # Pattern features need the component as the edit target (found by test:
    # InternalValidationError otherwise). build() activates the root again.
    occ.activate()
    ctx.first_occ[name] = occ
    ctx.components[name] = comp
    return comp


def apply_ref_look(ctx: Ctx, comp: adsk.fusion.Component) -> None:
    """Give every body of a reference component a translucent colour. No material is set.

    Why: reference envelopes are not made. They only show where a real part
    goes, so they get a see-through colour and no material (D-035). The colour
    comes from the seed table `[ref_look]`, which copies the CadQuery ref
    colour. The appearance is made once in the design from a library
    appearance, then reused. `apply_materials.py` skips the "ref" category.
    """
    look = ctx.ref_look
    if not look:
        raise ConfigError("apply_ref_look: the seed has no [ref_look] table")
    app = adsk.core.Application.get()
    appearance = ctx.design.appearances.itemByName(look["name"])
    if appearance is None:
        base = app.materialLibraries.itemByName(look["library"]).appearances.itemByName(look["base"])
        if base is None:
            raise ConfigError(f"[ref_look] base appearance not found: {look['base']}")
        appearance = ctx.design.appearances.addByCopy(base, look["name"])
        color = appearance.appearanceProperties.itemByName("Color")
        if color is None:
            raise ConfigError(f"[ref_look] appearance '{look['base']}' has no Color property")
        color.value = adsk.core.Color.create(*(round(look[k] * 255) for k in ("red", "green", "blue")), 255)
    for i in range(comp.bRepBodies.count):
        body = comp.bRepBodies.item(i)
        body.appearance = appearance
        body.opacity = look["alpha"]


def add_occurrence(ctx: Ctx, comp: adsk.fusion.Component, label: str) -> adsk.fusion.Occurrence:
    """Add one more occurrence of an existing component and register it as `label`."""
    occ = ctx.root.occurrences.addExistingComponent(comp, adsk.core.Matrix3D.create())
    label_occurrence(ctx, occ, label)
    return occ


def nest_occurrence(container: adsk.fusion.Component, comp: adsk.fusion.Component) -> adsk.fusion.Occurrence:
    """Add an occurrence of `comp` INSIDE the component `container` (a sub-assembly).

    The occurrence starts at the container origin. A rigid joint inside the
    container then places it. It is made visible on purpose: when the old
    root occurrence of the part was hidden (the rails in flight_rack_asm),
    Fusion hides the new nested occurrence too (found by test).
    """
    occ = container.occurrences.addExistingComponent(comp, adsk.core.Matrix3D.create())
    occ.isLightBulbOn = True
    return occ


def show_tree(occ: adsk.fusion.Occurrence) -> None:
    """Turn the light bulb ON for an occurrence and all nested occurrences below it.

    Why: Fusion keeps the visibility for each occurrence PATH. A part that
    was hidden at the root (the unplaced rails) comes back hidden inside a
    blade, in every blade occurrence (found by test). The nested proxies
    must be switched on one by one.
    """
    occ.isLightBulbOn = True
    for i in range(occ.childOccurrences.count):
        show_tree(occ.childOccurrences.item(i))


def root_occurrences_of(ctx: Ctx, comp: adsk.fusion.Component) -> List[adsk.fusion.Occurrence]:
    """Return the occurrences of `comp` that sit directly in the root component."""
    found = []
    for i in range(ctx.root.occurrences.count):
        occ = ctx.root.occurrences.item(i)
        if occ.component == comp:
            found.append(occ)
    return found


def delete_root_occurrences(ctx: Ctx, comp: adsk.fusion.Component) -> int:
    """Delete every root-level occurrence of `comp`. Return how many were deleted.

    Why: a part that lives inside a sub-assembly must not also sit in the
    root. Call it only after the nested occurrences exist, so the component
    itself stays in the design.
    """
    name = comp.name                      # read first: the object may be gone after the delete
    victims = root_occurrences_of(ctx, comp)
    for occ in victims:
        occ.deleteMe()
    ctx.first_occ.pop(name, None)
    return len(victims)


def label_occurrence(ctx: Ctx, occ: adsk.fusion.Occurrence, label: str) -> None:
    """Register an occurrence under a label and store the label as an attribute.

    Why: Fusion occurrence names are read-only ("part:2"). The label is the
    CadQuery part name (for example side_wall_R). Joints and checks use it.
    """
    ctx.occurrences[label] = occ
    occ.attributes.add("HERON", "label", label)


# ---------------------------------------------------------------- sketches
def plane_sketch(comp: adsk.fusion.Component, name: str,
                 plane: Optional[adsk.core.Base] = None) -> adsk.fusion.Sketch:
    """Create a named sketch. Default plane: the XY plane of the component."""
    sk = comp.sketches.add(plane if plane is not None else comp.xYConstructionPlane)
    sk.name = name
    return sk


def offset_plane(comp: adsk.fusion.Component, name: str, expr: str,
                 base: Optional[adsk.core.Base] = None) -> adsk.fusion.ConstructionPlane:
    """Make a construction plane parallel to `base` (default XY), offset by `expr`."""
    inp = comp.constructionPlanes.createInput()
    inp.setByOffset(base if base is not None else comp.xYConstructionPlane, _vi(expr))
    plane = comp.constructionPlanes.add(inp)
    plane.name = name
    return plane


def _set_dim(dim: adsk.fusion.SketchDimension, expr: str) -> None:
    """Set a driving dimension to an expression. Fusion then keeps it parametric."""
    dim.parameter.expression = expr


def _locate(ctx: Ctx, sk: adsk.fusion.Sketch, entity, coord: str, expr: str,
            text_xy_mm: Tuple[float, float]) -> None:
    """Fix the x or y coordinate of a point or line by a dimension from the sketch origin.

    coord: 'x' (horizontal distance) or 'y' (vertical distance). For a line,
    its start point is used, so a vertical line fixes x and a horizontal line
    fixes y. Why a point dimension: it survives fillets, because a fillet only
    slides the end points along the line. A value of zero is allowed by
    Fusion, and it stays parametric (the value can change later).
    """
    point = entity.startSketchPoint if isinstance(entity, adsk.fusion.SketchLine) else entity
    orient = (adsk.fusion.DimensionOrientations.HorizontalDimensionOrientation if coord == "x"
              else adsk.fusion.DimensionOrientations.VerticalDimensionOrientation)
    dim = sk.sketchDimensions.addDistanceDimension(sk.originPoint, point, orient, _pt(*text_xy_mm))
    _set_dim(dim, _neg_if(expr, ctx.num(expr)))


def _rect_lines(lines) -> Dict[str, adsk.fusion.SketchLine]:
    """Sort the 4 lines of a rectangle into left, right, bottom, top."""
    out: Dict[str, adsk.fusion.SketchLine] = {}
    vertical, horizontal = [], []
    for i in range(lines.count):
        ln = lines.item(i)
        a, b = ln.startSketchPoint.geometry, ln.endSketchPoint.geometry
        (vertical if abs(a.x - b.x) < 1e-9 else horizontal).append((ln, a, b))
    vertical.sort(key=lambda t: t[1].x)
    horizontal.sort(key=lambda t: t[1].y)
    out["left"], out["right"] = vertical[0][0], vertical[1][0]
    out["bottom"], out["top"] = horizontal[0][0], horizontal[1][0]
    return out


def _square_up(sk: adsk.fusion.Sketch, ln: Dict[str, adsk.fusion.SketchLine]) -> None:
    """Constrain the 4 lines horizontal and vertical so the rectangle cannot skew.

    Why: `addTwoPointRectangle` leaves the lines free to turn (found by test:
    the sketch was not fully constrained). With these 4 constraints plus 4
    dimensions the sketch is fully constrained.
    """
    cons = sk.geometricConstraints
    cons.addHorizontal(ln["bottom"])
    cons.addHorizontal(ln["top"])
    cons.addVertical(ln["left"])
    cons.addVertical(ln["right"])


def _dim_rect_lines(ctx: Ctx, sk: adsk.fusion.Sketch, ln: Dict[str, adsk.fusion.SketchLine],
                    x0: str, y0: str, x1: str, y1: str) -> None:
    """Dimension the 4 edge lines: left/bottom from the axes, width and height between lines."""
    n = ctx.num
    cx, cy = n(x0), n(y0)
    _locate(ctx, sk, ln["left"], "x", x0, (cx, cy - 2))
    _locate(ctx, sk, ln["bottom"], "y", y0, (cx - 2, cy))
    w, h = f"({x1}) - ({x0})", f"({y1}) - ({y0})"
    mx, my = (cx + n(x1)) / 2, (cy + n(y1)) / 2
    _set_dim(sk.sketchDimensions.addOffsetDimension(ln["left"], ln["right"], _pt(mx, cy - 4)), w)
    _set_dim(sk.sketchDimensions.addOffsetDimension(ln["bottom"], ln["top"], _pt(cx - 4, my)), h)


def sk_rect(ctx: Ctx, sk: adsk.fusion.Sketch, x0: str, y0: str, x1: str, y1: str) -> None:
    """Draw a rectangle from (x0, y0) to (x1, y1) and fix it with 4 dimensions.

    The corner expressions are in mm. x1 > x0 and y1 > y0 when evaluated.
    """
    n = ctx.num
    if not (n(x1) > n(x0) and n(y1) > n(y0)):
        raise ConfigError(f"sk_rect '{sk.name}': corners are not ordered: ({x0}, {y0}) to ({x1}, {y1})")
    lines = sk.sketchCurves.sketchLines.addTwoPointRectangle(
        _pt(n(x0) + _jit(), n(y0) + _jit()), _pt(n(x1) + _jit(), n(y1) + _jit()))
    ln = _rect_lines(lines)
    _square_up(sk, ln)
    _dim_rect_lines(ctx, sk, ln, x0, y0, x1, y1)


def sk_rounded_rect(ctx: Ctx, sk: adsk.fusion.Sketch, x0: str, y0: str, x1: str, y1: str, r: str) -> None:
    """Draw a rectangle with 4 equal corner fillets of radius `r` and fix it.

    Each fillet arc gets its own radius dimension. The size and the place use
    the edge lines, so the dimensions stay valid after the fillets.
    """
    n = ctx.num
    rv = n(r)
    if not (n(x1) > n(x0) and n(y1) > n(y0)):
        raise ConfigError(f"sk_rounded_rect '{sk.name}': corners are not ordered")
    if rv * 2 >= min(n(x1) - n(x0), n(y1) - n(y0)):
        raise ConfigError(f"sk_rounded_rect '{sk.name}': radius {rv} is too large for the rectangle")
    px0, py0, px1, py1 = n(x0) + _jit(), n(y0) + _jit(), n(x1) + _jit(), n(y1) + _jit()
    lines = sk.sketchCurves.sketchLines.addTwoPointRectangle(_pt(px0, py0), _pt(px1, py1))
    ln = _rect_lines(lines)
    _square_up(sk, ln)
    arcs = sk.sketchCurves.sketchArcs
    corners = (("bottom", "left", px0, py0), ("bottom", "right", px1, py0),
               ("top", "left", px0, py1), ("top", "right", px1, py1))
    made = []
    for horiz, vert, cx, cy in corners:
        made.append((arcs.addFillet(ln[horiz], _pt(cx, cy), ln[vert], _pt(cx, cy), rv * _CM_PER_MM), cx, cy))
    for arc, cx, cy in made:
        # text point: outside the corner, so it never sits on the arc
        tx = cx - 3.0 if cx == px0 else cx + 3.0
        ty = cy - 3.0 if cy == py0 else cy + 3.0
        dim = sk.sketchDimensions.addRadialDimension(arc, _pt(tx, ty))
        _set_dim(dim, r)
    _dim_rect_lines(ctx, sk, ln, x0, y0, x1, y1)


def sk_circle(ctx: Ctx, sk: adsk.fusion.Sketch, cx: str, cy: str, d: str) -> None:
    """Draw a circle with centre (cx, cy) and diameter `d`; fix all three by dimensions."""
    n = ctx.num
    circle = sk.sketchCurves.sketchCircles.addByCenterRadius(
        _pt(n(cx) + _jit(), n(cy) + _jit()), (n(d) / 2 + _jit()) * _CM_PER_MM)
    _set_dim(sk.sketchDimensions.addDiameterDimension(circle, _pt(n(cx) + n(d), n(cy) + n(d))), d)
    _locate(ctx, sk, circle.centerSketchPoint, "x", cx, (n(cx), n(cy) - n(d)))
    _locate(ctx, sk, circle.centerSketchPoint, "y", cy, (n(cx) - n(d), n(cy)))


def sk_slot(ctx: Ctx, sk: adsk.fusion.Sketch, cx: str, cy: str, length_cc: str, width: str,
            vertical: bool = False) -> None:
    """Draw a stadium slot. `length_cc` is the centre-to-centre length.

    Why centre-to-centre: CadQuery `slot(w, h)` uses w as the distance between
    the two arc centres and h as the diameter. The slot is centred at (cx, cy).
    It runs along X, or along Y when vertical=True.
    """
    n = ctx.num
    half = n(length_cc) / 2
    cx_n, cy_n = n(cx) + _jit(), n(cy) + _jit()
    if vertical:
        start, end = _pt(cx_n, cy_n - half), _pt(cx_n, cy_n + half)
        start_x, start_y = cx, f"({cy}) - ({length_cc}) / 2"
    else:
        start, end = _pt(cx_n - half, cy_n), _pt(cx_n + half, cy_n)
        start_x, start_y = f"({cx}) - ({length_cc}) / 2", cy
    items = sk.addCenterToCenterSlot(start, end, _vi(width), True, _vi(length_cc))
    arc = items[0]
    construction_line = items[4]
    if vertical:
        sk.geometricConstraints.addVertical(construction_line)
    else:
        sk.geometricConstraints.addHorizontal(construction_line)
    c = arc.centerSketchPoint.geometry
    here = (c.x * _MM_PER_CM, c.y * _MM_PER_CM)
    _locate(ctx, sk, arc.centerSketchPoint, "x", start_x, here)
    _locate(ctx, sk, arc.centerSketchPoint, "y", start_y, here)


def flip_pos(expr: str) -> str:
    """Map a world-Z position to the sketch-Y of an XZ-plane sketch (sketch y = -Z)."""
    return f"-({expr})"


def flip_range(lo: str, hi: str) -> Tuple[str, str]:
    """Map a world-Z range [lo, hi] to the sketch-Y range of an XZ-plane sketch.

    Why: on the XZ plane Fusion puts sketch (x, y) at world (X, -Z), and the
    sketch normal is +Y. A Z range [lo, hi] is therefore [-hi, -lo].
    """
    return f"-({hi})", f"-({lo})"


# ---------------------------------------------------------------- features
_OPS = {"new": adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
        "join": adsk.fusion.FeatureOperations.JoinFeatureOperation,
        "cut": adsk.fusion.FeatureOperations.CutFeatureOperation}


def extrude(comp: adsk.fusion.Component, sk: adsk.fusion.Sketch, distance: Optional[str],
            op: str = "new", start_offset: Optional[str] = None, flip: bool = False,
            through: bool = False, name: Optional[str] = None) -> adsk.fusion.ExtrudeFeature:
    """Extrude ALL profiles of a sketch.

    distance: expression string, for example "T_". None when through=True.
    through: cut through everything in the extrusion direction (setAllExtent).
    flip: extrude against the sketch normal (for example on the XZ plane).
    start_offset: expression for a start plane offset from the sketch plane.
    """
    profiles = adsk.core.ObjectCollection.create()
    for i in range(sk.profiles.count):
        profiles.add(sk.profiles.item(i))
    if profiles.count == 0:
        raise ConfigError(f"extrude: sketch '{sk.name}' has no closed profile")
    inp = comp.features.extrudeFeatures.createInput(profiles, _OPS[op])
    direction = (adsk.fusion.ExtentDirections.NegativeExtentDirection if flip
                 else adsk.fusion.ExtentDirections.PositiveExtentDirection)
    if through:
        inp.setAllExtent(direction)
    else:
        inp.setOneSideExtent(adsk.fusion.DistanceExtentDefinition.create(_vi(distance)), direction)
    if start_offset is not None:
        inp.startExtent = adsk.fusion.OffsetStartDefinition.create(_vi(start_offset))
    if op == "cut":
        # Cut only the bodies of THIS component. Without this, a through cut
        # also reaches bodies of other components and a later pattern of the
        # cut fails (found by test: InternalValidationError getObjectPath).
        inp.participantBodies = [comp.bRepBodies.item(i) for i in range(comp.bRepBodies.count)]
    feat = comp.features.extrudeFeatures.add(inp)
    if name:
        feat.name = name
    return feat


def pattern_rect(comp: adsk.fusion.Component, feature: adsk.fusion.Feature,
                 dirs: Sequence[Tuple[adsk.core.Base, str, str]], name: str) -> Optional[adsk.fusion.RectangularPatternFeature]:
    """Pattern a feature in up to two directions.

    dirs: list of (axis, count_expr, spacing_expr). Count expressions are
    User Parameter names (unitless). Spacing is a length expression. Returns
    None when `dirs` is empty (one instance only).
    """
    if not dirs:
        return None
    items = adsk.core.ObjectCollection.create()
    items.add(feature)
    axis1, count1, space1 = dirs[0]
    inp = comp.features.rectangularPatternFeatures.createInput(
        items, axis1, _vi(count1), _vi(space1),
        adsk.fusion.PatternDistanceType.SpacingPatternDistanceType)
    if len(dirs) > 1:
        axis2, count2, space2 = dirs[1]
        inp.setDirectionTwo(axis2, _vi(count2), _vi(space2))
    pat = comp.features.rectangularPatternFeatures.add(inp)
    pat.name = name
    return pat


def grid_windows(ctx: Ctx, comp: adsk.fusion.Component, name: str,
                 x0: str, y0: str, x1: str, y1: str, nx: str, ny: str, rib: str, r: str,
                 plane: Optional[adsk.core.Base] = None) -> Optional[adsk.fusion.Feature]:
    """Cut a regular grid of rounded windows. Mirror of heron_rack `grid_windows`.

    One window is sketched and cut through all. A rectangular pattern copies
    it. The count parameters `nx`, `ny` (names) and `rib` drive the pattern.
    CadQuery keeps a grid only if cw > 2r+1 and ch > 2r+1. The test runs here
    at build time with the current parameter values. Returns None when the
    grid is dropped. Counts that are 1 at build time make no pattern in that
    direction (README: counts are static for patterns).
    """
    n = ctx.num
    cw = f"(({x1}) - ({x0}) - ({rib}) * ({nx} - 1)) / {nx}"
    ch = f"(({y1}) - ({y0}) - ({rib}) * ({ny} - 1)) / {ny}"
    if not (n(cw) > 2 * n(r) + 1 and n(ch) > 2 * n(r) + 1):
        return None
    sk = plane_sketch(comp, name + "_sketch", plane)
    sk_rounded_rect(ctx, sk, x0, y0, f"({x0}) + {cw}", f"({y0}) + {ch}", r)
    feat = extrude(comp, sk, None, op="cut", through=True, name=name)
    dirs = []
    if round(n(nx)) > 1:
        dirs.append((comp.xConstructionAxis, nx, f"{cw} + ({rib})"))
    if round(n(ny)) > 1:
        dirs.append((comp.yConstructionAxis, ny, f"{ch} + ({rib})"))
    pattern_rect(comp, feat, dirs, name + "_pattern")
    return feat


# ---------------------------------------------------------------- joint origins
def _cross(a: Sequence[int], b: Sequence[int]) -> Tuple[int, int, int]:
    """Cross product of two integer 3-vectors."""
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _neg(a: Sequence[int]) -> Tuple[int, int, int]:
    """Negate an integer 3-vector."""
    return (-a[0], -a[1], -a[2])


def _orientation_recipe(frame: Sequence[Sequence[int]]) -> Tuple[int, bool, int, bool]:
    """Find how to build `frame` = (u, v, w) with a JointOriginInput.

    Fusion rules (tested): Z axis = zAxisEntity axis, X axis = xAxisEntity axis.
    isFlipped turns Z to -Z and Y to -Y (X is kept). angle=180 deg turns X and
    Y to -X and -Y. Returns (z_axis_index, flipped, x_axis_index, turn180).
    """
    u, v, w = (tuple(c) for c in frame)
    if _cross(u, v) != w:
        raise ConfigError(f"joint frame {frame} is not right handed")
    for za in range(3):
        for flipped in (False, True):
            for xa in range(3):
                if xa == za:
                    continue
                for turn in (False, True):
                    z0, x0 = _AXES[za], _AXES[xa]
                    y0 = _cross(z0, x0)
                    z, x, y = z0, x0, y0
                    if flipped:
                        z, y = _neg(z), _neg(y)
                    if turn:
                        x, y = _neg(x), _neg(y)
                    if (x, y, z) == (u, v, w):
                        return za, flipped, xa, turn
    raise ConfigError(f"no joint origin recipe for frame {frame}")


def _axis_of(comp: adsk.fusion.Component, index: int):
    """Return the construction axis of a component for index 0, 1, 2 (X, Y, Z)."""
    return (comp.xConstructionAxis, comp.yConstructionAxis, comp.zConstructionAxis)[index]


def joint_origin(comp: adsk.fusion.Component, name: str,
                 offset: Sequence[str] = ("0 mm", "0 mm", "0 mm"),
                 frame: Sequence[Sequence[int]] = IDENTITY,
                 point: Optional[adsk.core.Base] = None) -> adsk.fusion.JointOrigin:
    """Create a joint origin with expression offsets and a signed-axis frame.

    offset: (dx, dy, dz) expressions along the PARENT axes (the component axes
    where the joint origin lives). Fusion applies offsets along the joint
    origin's own axes, so this function converts them.
    frame: (u, v, w) columns, signed unit axis vectors. They give the joint
    origin X, Y, Z axes in the component axes, like `place(shape, o, u, v, w)`.
    The result is checked against `JointOrigin.transform`.
    """
    za, flipped, xa, turn = _orientation_recipe(frame)
    geom = adsk.fusion.JointGeometry.createByPoint(point if point is not None else comp.originConstructionPoint)
    inp = comp.jointOrigins.createInput(geom)
    inp.zAxisEntity = _axis_of(comp, za)
    inp.xAxisEntity = _axis_of(comp, xa)
    inp.isFlipped = flipped
    if turn:
        inp.angle = _vi("180 deg")
    local = []
    for col in frame:                       # local offset k = sum over axes of world_offset[axis] * col[axis]
        terms = [(f"({offset[a]})" if col[a] > 0 else f"-({offset[a]})")
                 for a in range(3) if col[a] != 0]
        local.append(terms[0])
    inp.offsetX, inp.offsetY, inp.offsetZ = _vi(local[0]), _vi(local[1]), _vi(local[2])
    jo = comp.jointOrigins.add(inp)
    jo.name = name
    _, vx, vy, vz = jo.transform.getAsCoordinateSystem()
    got = tuple(tuple(round(c) for c in (v.x, v.y, v.z)) for v in (vx, vy, vz))
    if got != tuple(tuple(c) for c in frame):
        raise ConfigError(f"joint origin '{name}': frame {got} differs from wanted {tuple(map(tuple, frame))}")
    return jo


_DIRS = {"X": adsk.fusion.JointDirections.XAxisJointDirection,
         "Y": adsk.fusion.JointDirections.YAxisJointDirection,
         "Z": adsk.fusion.JointDirections.ZAxisJointDirection}


def make_joint(ctx: Ctx, kind: str, child_occ: adsk.fusion.Occurrence,
               child_jo: adsk.fusion.JointOrigin, parent_jo: adsk.fusion.JointOrigin,
               name: str, parent_occ: Optional[adsk.fusion.Occurrence] = None,
               axis: str = "Z",
               container: Optional[adsk.fusion.Component] = None) -> adsk.fusion.Joint:
    """Make a rigid, slider or revolute joint. The child moves to the parent.

    kind: 'rigid', 'slider' or 'revolute'.
    axis: 'X', 'Y' or 'Z' of the JOINT ORIGIN frame, for slider and revolute.
    child_jo lives in the child component. parent_jo lives in the root, or in
    the component of `parent_occ`. Occurrence proxies are made here.
    container: the component that owns the joint. Default: the root. A
    sub-assembly (blade) passes itself; then `child_occ` is an occurrence
    inside the sub-assembly and `parent_jo` is a joint origin of the
    sub-assembly itself.
    """
    owner = container if container is not None else ctx.root
    one = child_jo.createForAssemblyContext(child_occ)
    two = parent_jo if parent_occ is None else parent_jo.createForAssemblyContext(parent_occ)
    inp = owner.joints.createInput(one, two)
    if kind == "rigid":
        inp.setAsRigidJointMotion()
    elif kind == "slider":
        inp.setAsSliderJointMotion(_DIRS[axis], None)
    elif kind == "revolute":
        inp.setAsRevoluteJointMotion(_DIRS[axis], None)
    else:
        raise ConfigError(f"make_joint: unknown kind '{kind}'")
    joint = owner.joints.add(inp)
    joint.name = name
    return joint


def ground(occ: adsk.fusion.Occurrence) -> None:
    """Ground one occurrence. Only the bottom plate is grounded."""
    occ.isGrounded = True


# ---------------------------------------------------------------- checks
def measure(entity) -> dict:
    """Return volume (mm^3) and bounding box (mm) of an occurrence or component.

    For an occurrence the box is in world space. For a component it is in
    component space (the local frame of the part).
    """
    bodies = entity.bRepBodies
    vol = 0.0
    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    for i in range(bodies.count):
        b = bodies.item(i)
        vol += b.volume * 1000.0
        bb = b.boundingBox
        for k, (a, c) in enumerate(((bb.minPoint.x, bb.maxPoint.x), (bb.minPoint.y, bb.maxPoint.y),
                                    (bb.minPoint.z, bb.maxPoint.z))):
            lo[k], hi[k] = min(lo[k], a * _MM_PER_CM), max(hi[k], c * _MM_PER_CM)
    return {"volume": vol, "bbox": lo + hi, "bodies": bodies.count}


def compare_params(design: adsk.fusion.Design, reference: Mapping[str, float],
                   tol_mm: float = 1e-6) -> List[Tuple[str, float, float, bool]]:
    """Compare Fusion parameter values with reference numbers.

    Returns rows (name, fusion value, reference value, ok). Length values are
    in mm, angle values in degrees, plain numbers as they are.
    """
    rows = []
    for name, ref in reference.items():
        p = design.userParameters.itemByName(name)
        if p is None:
            continue
        if p.unit in ("mm", "cm"):
            val = p.value * _MM_PER_CM
        elif p.unit == "deg":
            val = math.degrees(p.value)
        else:
            val = p.value
        rows.append((name, val, ref, abs(val - ref) <= tol_mm))
    return rows
