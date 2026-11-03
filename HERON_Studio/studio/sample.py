"""The 19 Oct 2026 Boulder Reservoir flight ships with the app as a finished example."""
import json, shutil
from .config import SAMPLE_DIR, FLIGHTS_DIR, STUDIO_HOME

SAMPLE_ID = "boulder-reservoir-2026-10-19"


def install(force=False):
    src = SAMPLE_DIR / "boulder-2026-10-19"; dst = FLIGHTS_DIR / SAMPLE_ID; mark = STUDIO_HOME / ".sample_installed"
    if not src.exists() or dst.exists() or (mark.exists() and not force): return False
    shutil.copytree(src, dst); mark.write_text("1")
    return True
