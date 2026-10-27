"""MCP script text: save the laser-cut DXF files from `flight_rack_asm` (D-044).

Why this file exists: the laser shop needs 1:1 DXF outlines for CorelDRAW,
and they must come from the Fusion master. Copy this whole text into one MCP
"script" call. Change only the constants.

What it writes into OUT_DIR:
  * one file for each laser part type, `<part>_x<count>.dxf`, in the part
    local frame (count = pieces in FAB_laser_layout);
  * `laser_layout_all.dxf`: the whole FAB_laser_layout sheet, all pieces in
    their layout places.

How: for each piece, a temporary sketch on the XY plane projects the bottom
face (z = 0). All laser cuts go through the sheet, so the bottom face holds
every outline and hole. The script checks that the top face has the same
area (no partial pocket is lost). It deletes the temporary sketches and
never saves. No kerf compensation is applied (Q-016).
"""
import os
import time

import adsk.core
import adsk.fusion

REQUIRED_DOC = "flight_rack_asm"
LAYOUT = "FAB_laser_layout"
OUT_DIR = "/Users/nathan/Documents/GitHub/HERON/Hardware/payload_rack/out/fusion_laser_dxf"
NAME_PREFIX = "LZR_"          # component prefix of laser parts; removed from the file name
AREA_TOL = 1e-6               # cm2, top and bottom face area must agree


def _flat_faces(body):
    """Return (bottom, top) planar faces of a flat part: the largest faces at min z and max z."""
    bb = body.boundingBox
    z0, z1 = bb.minPoint.z, bb.maxPoint.z
    bottom = top = None
    for face in body.faces:
        fb = face.boundingBox
        if abs(fb.maxPoint.z - fb.minPoint.z) > 1e-7:
            continue
        if abs(fb.minPoint.z - z0) < 1e-7 and (bottom is None or face.area > bottom.area):
            bottom = face
        if abs(fb.minPoint.z - z1) < 1e-7 and (top is None or face.area > top.area):
            top = face
    if bottom is None or top is None:
        raise RuntimeError(f"{body.name}: no flat bottom or top face found")
    if abs(bottom.area - top.area) > AREA_TOL:
        raise RuntimeError(f"{body.name}: top and bottom face areas differ "
                           f"({top.area:.6f} vs {bottom.area:.6f} cm2); a cut does not go through")
    return bottom, top


def _save(comp, faces, path, log):
    """Project the faces into a temporary sketch of `comp`, save it as DXF, delete the sketch."""
    sk = comp.sketches.add(comp.xYConstructionPlane)
    try:
        for face in faces:
            sk.project(face)
        if not sk.saveAsDXF(path):
            raise RuntimeError(f"saveAsDXF failed: {path}")
        log(f"wrote {os.path.basename(path)} ({sk.sketchCurves.count} curves)")
    finally:
        sk.deleteMe()


def run(_context: str):
    """Check the document, write the per-part files and the full sheet."""
    app = adsk.core.Application.get()
    if app.activeDocument.name != REQUIRED_DOC:
        raise RuntimeError(f"Refused: the active document is '{app.activeDocument.name}', not '{REQUIRED_DOC}'.")
    design = adsk.fusion.Design.cast(app.activeProduct)
    root = design.rootComponent
    cocc = next((o for o in root.occurrences if o.component.name == LAYOUT), None)
    if cocc is None:
        raise RuntimeError(f"'{LAYOUT}' is missing. Build it with run_fab_layout.py first.")
    os.makedirs(OUT_DIR, exist_ok=True)
    start = time.time()

    def log(msg):
        """Print one line with the elapsed time."""
        print(f"{time.time() - start:5.1f} s  {msg}")

    # Pieces: nested occurrences and linked body copies. The sheet sketch lives in
    # the ROOT, so the faces are root-context proxies (a container sketch refused
    # them: "getObjectPath", found by test). The sheet DXF is in world coordinates.
    counts, sheet_faces, part_bodies = {}, [], {}
    for occ in cocc.childOccurrences:
        name = occ.component.name
        counts[name] = counts.get(name, 0) + 1
        for body in occ.bRepBodies:
            sheet_faces.append(_flat_faces(body)[0])
        part_bodies.setdefault(name, occ.component.bRepBodies.item(0))
    for body in cocc.bRepBodies:
        attr = (body.nativeObject or body).attributes.itemByName("HERON", "part")
        name = attr.value if attr else body.name
        counts[name] = counts.get(name, 0) + 1
        sheet_faces.append(_flat_faces(body)[0])
        part_bodies.setdefault(name, design.allComponents.itemByName(name).bRepBodies.item(0))
    for name, body in sorted(part_bodies.items()):
        short = name[len(NAME_PREFIX):] if name.startswith(NAME_PREFIX) else name
        comp = design.allComponents.itemByName(name)
        _save(comp, [_flat_faces(body)[0]], os.path.join(OUT_DIR, f"{short}_x{counts[name]}.dxf"), log)
    _save(root, sheet_faces, os.path.join(OUT_DIR, "laser_layout_all.dxf"), log)
    log(f"pieces {counts}")
    log("DONE")
