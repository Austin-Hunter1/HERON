"""Loader text for the Fusion MCP script tool.

Why this file exists: the MCP "script" tool needs a self-contained text with
a `run(_context)` function. This text adds the repo folders to sys.path,
purges cached HERON modules (so edits show up at once), imports the entry
module, and calls its entry function. Copy this text into the MCP call.
Change only the three constants below. Do not catch exceptions here.
"""
import importlib
import sys

FUSION_DIR = "/Users/nathan/Documents/GitHub/HERON/Hardware/payload_rack/fusion"
EXTRA_PATHS = []           # more folders (for example scratch test scripts)
ENTRY = "build_flight_rack"  # module to import
FUNC = "main"                # function to call (no arguments)
PURGE = ("fusion_lib", "kicad_clock", "rack_config", "check_ref", "build_flight_rack", "parts")


def run(_context: str):
    """Fusion entry point: reload the HERON modules, then call ENTRY.FUNC."""
    for path in [FUSION_DIR] + list(EXTRA_PATHS):
        if path not in sys.path:
            sys.path.insert(0, path)
    for name in list(sys.modules):
        if name.split(".")[0] in PURGE or name.split(".")[0] == ENTRY:
            del sys.modules[name]
    module = importlib.import_module(ENTRY)
    getattr(module, FUNC)()
