"""MCP script text: bring `flight_rack_asm` up to date with clock board rev D (D-040, D-042).

Why this file exists: Fusion is the master model (D-035), so the design is
not rebuilt. This script changes only what the clock board changes need:

Phase "params":
  1. Sets the parameters whose seed values changed (board height and top
     edge, the two mated SDR SMA positions, the J1 envelope).
  2. Creates the missing parameters (USB hole and opening sizes, the USB
     axis offset, the J1 body offset, the GPS ANT position).
  3. Deletes SDR_REAR_SMA_AUX when nothing uses it (replaced by SDR_GPS_BX).
  4. Sets the CLK_J<n>_KX / _KY jack parameters from the rev D KiCad file.
  5. Suppresses the rear plate "windows_above" grid when its windows no longer
     fit (the board top moved up; heron_rack.py drops that grid too).
Phase "parts":
  6. Replaces REF_clock_board_revC with REF_clock_board_revD (USB holes, J1 offset).
  7. Adds the USB openings to the existing rear plate (rear_plate.usb_openings).
Phase "holes" (D-043, after the mid-edge holes H5, H6 were added to rev D):
  8. Rebuilds REF_clock_board_revD (now with 6 mounting holes).
  9. Cuts the 2 mid-edge holes in the existing rear plate (rear_plate.center_holes).

Copy this text into one MCP "script" call per phase. Change only the
constants. It refuses another document name and (for "params") unsaved
changes. It writes a log and never saves: the caller checks, then the user
decides to save.
"""
import importlib
import sys
import time
import traceback

import adsk.core
import adsk.fusion

FUSION_DIR = "/Users/nathan/Documents/GitHub/HERON/Hardware/payload_rack/fusion"
REQUIRED_DOC = "flight_rack_asm"
PHASE = "params"                    # "params", "parts" or "holes"
LOG_PATH = "/private/tmp/claude-501/-Users-nathan-Documents-GitHub-HERON/c093830d-555e-4140-bb2c-9258e935612c/scratchpad/update_revd.log"
CHANGED = ["CLK_W", "CLK_H", "CLK_TOP_Z", "SDR_REAR_SMA_REF_A", "SDR_REAR_SMA_REF_B",
           "CLKREF_JST_W", "CLKREF_JST_H", "CLKREF_JST_D"]
OLD_REF, NEW_REF = "REF_clock_board_revC", "REF_clock_board_revD"
PURGE = ("fusion_lib", "kicad_clock", "rack_config", "check_ref", "build_flight_rack", "parts")


def _unhealthy(design):
    return [(design.timeline.item(i).name, design.timeline.item(i).errorOrWarningMessage)
            for i in range(design.timeline.count) if design.timeline.item(i).healthState != 0]


def _params(design, builder, fl, log):
    cfg = fl.load_config(builder.SEED_PATH)
    specs = {s.name: s for s in fl.param_specs(cfg)}
    for name in CHANGED:
        p = design.userParameters.itemByName(name)
        log(f"set {name}: {p.expression if p else None} -> {specs[name].expression}")
    fl.seed_parameters(design, [specs[n] for n in CHANGED])
    created = fl.create_missing_parameters(design, list(specs.values()) + builder.kicad_specs(cfg))
    log(f"created {created}")
    aux = design.userParameters.itemByName("SDR_REAR_SMA_AUX")
    if aux is not None:
        if aux.dependentParameters.count == 0 and aux.isDeletable:
            aux.deleteMe()
            log("deleted SDR_REAR_SMA_AUX")
        else:
            log("kept SDR_REAR_SMA_AUX (in use)")
    literals = [s for s in builder.kicad_specs(cfg) if s.is_literal]
    for s in literals:
        p = design.userParameters.itemByName(s.name)
        if p is not None and abs(p.value * 10.0 - s.literal) > 1e-9:
            log(f"jack {s.name}: {p.value * 10.0:.3f} -> {s.literal:.3f}")
    fl.seed_parameters(design, literals)
    # 5. windows above the clock board: suppress when they no longer fit
    ctx = builder._context(design, cfg)
    n = ctx.num
    ch = n("((H_ - REARWIN_MARGIN_Z) - (CLK_TOP_Z + REARWIN_CLK_GAP) - (REARWIN_R_RIB) * (REARWIN_R_NY - 1)) / REARWIN_R_NY")
    if not ch > 2 * n("GRID_WIN_R") + 1:
        plate = ctx.components["LZR_rear_plate"]
        for coll in (plate.features.extrudeFeatures, plate.features.rectangularPatternFeatures):
            for i in range(coll.count):
                f = coll.item(i)
                if f.name.startswith("windows_above"):
                    f.timelineObject.isSuppressed = True
                    log(f"suppressed {f.name} (window height {ch:.2f} mm)")
        for i in range(plate.sketches.count):
            sk = plate.sketches.item(i)
            if sk.name.startswith("windows_above") and sk.timelineObject:
                sk.timelineObject.isSuppressed = True
                log(f"suppressed sketch {sk.name}")


def _holes(design, builder, fl, log):
    """Rebuild the rev D board reference (6 holes) and cut the 2 new rear plate holes."""
    root = design.rootComponent
    j = root.joints.itemByName("J_" + NEW_REF)
    if j is not None:
        j.deleteMe(); log(f"deleted joint J_{NEW_REF}")
    jo = root.jointOrigins.itemByName("JO_" + NEW_REF)
    if jo is not None:
        jo.deleteMe(); log(f"deleted joint origin JO_{NEW_REF}")
    for i in range(root.occurrences.count - 1, -1, -1):
        occ = root.occurrences.item(i)
        if occ.component.name == NEW_REF:
            occ.deleteMe(); log(f"deleted occurrence of {NEW_REF}")
    cfg = fl.load_config(builder.SEED_PATH)
    report = builder.extend(design, [NEW_REF], [NEW_REF], cfg=cfg, log=log)
    log(f"extend report {report}")
    ctx = builder._context(design, cfg)
    plate = ctx.components["LZR_rear_plate"]
    if plate.features.extrudeFeatures.itemByName("clock_center_holes") is None:
        rear_plate = importlib.import_module("parts.rear_plate")
        design.activateRootComponent()
        rear_plate.center_holes(ctx, plate)
        log("added clock_center_holes to LZR_rear_plate")
    design.activateRootComponent()


def _parts(design, builder, fl, log):
    root = design.rootComponent
    j = root.joints.itemByName("J_" + OLD_REF)
    if j is not None:
        j.deleteMe(); log(f"deleted joint J_{OLD_REF}")
    jo = root.jointOrigins.itemByName("JO_" + OLD_REF)
    if jo is not None:
        jo.deleteMe(); log(f"deleted joint origin JO_{OLD_REF}")
    for i in range(root.occurrences.count - 1, -1, -1):
        occ = root.occurrences.item(i)
        if occ.component.name == OLD_REF:
            occ.deleteMe(); log(f"deleted occurrence of {OLD_REF}")
    cfg = fl.load_config(builder.SEED_PATH)
    report = builder.extend(design, [NEW_REF], [NEW_REF], cfg=cfg, log=log)
    log(f"extend report {report}")
    ctx = builder._context(design, cfg)
    plate = ctx.components["LZR_rear_plate"]
    if plate.features.extrudeFeatures.itemByName("usb_openings") is None:
        rear_plate = importlib.import_module("parts.rear_plate")
        design.activateRootComponent()
        rear_plate.usb_openings(ctx, plate)
        log("added usb_openings to LZR_rear_plate")
    design.activateRootComponent()


def run(_context: str):
    """Check the document, reload the HERON modules, run PHASE, write the log."""
    app = adsk.core.Application.get()
    name = app.activeDocument.name
    if name != REQUIRED_DOC:
        raise RuntimeError(f"Refused: the active document is '{name}', not '{REQUIRED_DOC}'.")
    if PHASE == "params" and app.activeDocument.isModified:
        raise RuntimeError(f"Refused: '{name}' has unsaved changes. Save or discard them first.")
    if FUSION_DIR not in sys.path:
        sys.path.insert(0, FUSION_DIR)
    for mod in list(sys.modules):
        if mod.split(".")[0] in PURGE:
            del sys.modules[mod]
    start = time.time()
    with open(LOG_PATH, "a", encoding="utf-8") as handle:
        def log(message: str) -> None:
            handle.write(f"{time.time() - start:6.1f} s  {message}\n")
            handle.flush()
            print(message)
        log(f"=== update_revd phase {PHASE} on '{name}'")
        try:
            builder = importlib.import_module("build_flight_rack")
            fl = builder.fl
            design = adsk.fusion.Design.cast(app.activeProduct)
            {"params": _params, "parts": _parts, "holes": _holes}[PHASE](design, builder, fl, log)
            log(f"timeline items with a problem: {_unhealthy(design)}")
        except Exception:
            log(traceback.format_exc())
            raise
        log("DONE")
