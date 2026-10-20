"""MCP script text: add the reference envelopes (phase 5) to the saved design `flight_rack_asm`.

Why this file exists: `build_flight_rack.extend` must run only on the right
document, and it must leave a log, because a long call can hide its output.
Copy this whole text into one MCP "script" call. Change only the constants.
The script refuses to run when the active document has another name. It
creates only missing User Parameters, never changes an existing one, and it
never saves the document. The caller saves it after a check.
It also refuses to run when the document has unsaved changes: the caller then
sees at once if a run changed the design, and a saved state stays the fallback.
"""
import importlib
import sys
import time
import traceback

import adsk.core

FUSION_DIR = "/Users/nathan/Documents/GitHub/HERON/Hardware/payload_rack/fusion"
REQUIRED_DOC = "flight_rack_asm"   # the only document name that this script accepts
REQUIRE_UNMODIFIED = True          # refuse a document with unsaved changes (set False only in a test)
LOG_PATH = "/private/tmp/claude-501/-Users-nathan-Documents-GitHub-HERON/4ad36852-d905-45f4-95f4-0bac895b6ffd/scratchpad/extend_refs.log"
# The four reference components (category "ref") and their 11 placements: the clock board,
# the NUC envelope, the SSD and 8 SMP adapters (4 SDR slots x ref_a, ref_b).
# The SDR board stand-ins (REF_B210_1 ...) are NOT added: the user chose no boards.
PARTS = ["REF_clock_board_revD", "REF_smp_adapter", "REF_NUC_envelope", "REF_SSD"]
LABELS = (["REF_clock_board_revD", "REF_NUC_envelope", "REF_SSD"]
          + [f"REF_adapter_{slot}_{key}" for slot in ("B210_1", "B210_2", "B200_1", "B200_2")
             for key in ("ref_a", "ref_b")])
PURGE = ("fusion_lib", "kicad_clock", "rack_config", "check_ref", "build_flight_rack", "parts")


def run(_context: str):
    """Check the document name, reload the HERON modules, call `extend`, write the log."""
    app = adsk.core.Application.get()
    name = app.activeDocument.name
    if name != REQUIRED_DOC:
        raise RuntimeError(f"Refused: the active document is '{name}', not '{REQUIRED_DOC}'.")
    if REQUIRE_UNMODIFIED and app.activeDocument.isModified:
        raise RuntimeError(f"Refused: '{name}' has unsaved changes. Save or discard them first.")
    if FUSION_DIR not in sys.path:
        sys.path.insert(0, FUSION_DIR)
    for mod in list(sys.modules):
        if mod.split(".")[0] in PURGE:
            del sys.modules[mod]
    start = time.time()
    with open(LOG_PATH, "a", encoding="utf-8") as handle:
        def log(message: str) -> None:
            """Write one line with the elapsed time and flush it at once."""
            handle.write(f"{time.time() - start:6.1f} s  {message}\n")
            handle.flush()
        log(f"=== extend on '{name}'")
        try:
            builder = importlib.import_module("build_flight_rack")
            design = builder.fl.adsk.fusion.Design.cast(app.activeProduct)
            report = builder.extend(design, PARTS, LABELS, log=log)
            log(f"report {report}")
            bad = [design.timeline.item(i).name for i in range(design.timeline.count)
                   if design.timeline.item(i).healthState != 0]
            log(f"timeline items with a problem: {bad}")
        except Exception:
            log(traceback.format_exc())
            raise
        log("DONE")
