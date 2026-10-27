"""MCP script text: build, check or remove the laser and print layouts in `flight_rack_asm`.

Why this file exists: `fab_layout` must run only on the right document, and
it must leave a log. Copy this whole text into one MCP "script" call. Change
only the constants. The script refuses another document name. For "build" and
"remove" it also refuses a document with unsaved changes, so the saved state
stays the fallback. It never saves: the caller checks, then the user saves.
"""
import importlib
import sys
import time
import traceback

import adsk.core
import adsk.fusion

FUSION_DIR = "/Users/nathan/Documents/GitHub/HERON/Hardware/payload_rack/fusion"
REQUIRED_DOC = "flight_rack_asm"
ACTION = "build"                   # "build", "check" or "remove"
REQUIRE_UNMODIFIED = True          # refuse unsaved changes for build/remove (set False only in a test)
LOG_PATH = "/tmp/fab_layout.log"   # set to a scratch folder
PURGE = ("fusion_lib", "kicad_clock", "rack_config", "fab_plan", "fab_layout", "parts")


def run(_context: str):
    """Check the document, reload the HERON modules, run ACTION, write the log."""
    app = adsk.core.Application.get()
    name = app.activeDocument.name
    if name != REQUIRED_DOC:
        raise RuntimeError(f"Refused: the active document is '{name}', not '{REQUIRED_DOC}'.")
    if ACTION != "check" and REQUIRE_UNMODIFIED and app.activeDocument.isModified:
        raise RuntimeError(f"Refused: '{name}' has unsaved changes. Save or discard them first.")
    if FUSION_DIR not in sys.path:
        sys.path.insert(0, FUSION_DIR)
    for mod in list(sys.modules):
        if mod.split(".")[0] in PURGE:
            del sys.modules[mod]
    start = time.time()
    with open(LOG_PATH, "a", encoding="utf-8") as handle:
        def log(message: str) -> None:
            """Write one line with the elapsed time, flush it, and print it."""
            handle.write(f"{time.time() - start:6.1f} s  {message}\n")
            handle.flush()
            print(message)
        log(f"=== fab_layout.{ACTION} on '{name}'")
        try:
            layout = importlib.import_module("fab_layout")
            design = adsk.fusion.Design.cast(app.activeProduct)
            getattr(layout, ACTION)(design, log=log)
            if ACTION == "build":
                layout.check(design, log=log)
        except Exception:
            log(traceback.format_exc())
            raise
        log("DONE")
