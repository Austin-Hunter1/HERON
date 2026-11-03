"""Where HERON Studio keeps things.

STUDIO_HOME holds every imported flight (videos can be several GB each), so it lives in the
user's home folder by default rather than inside the app. Override with HERON_STUDIO_HOME.
"""
import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent            # HERON_Studio/
ENGINE_DIR = Path(os.environ.get("HERON_ENGINE") or APP_DIR / "engine")
LENS_DIR = APP_DIR / "lens_profiles"
SAMPLE_DIR = APP_DIR / "sample"
STATIC_DIR = Path(__file__).resolve().parent / "static"

STUDIO_HOME = Path(os.environ.get("HERON_STUDIO_HOME") or Path.home() / "HERON Studio").expanduser()
FLIGHTS_DIR = STUDIO_HOME / "flights"
UPLOAD_TMP = STUDIO_HOME / ".uploads"

PORT = int(os.environ.get("HERON_STUDIO_PORT") or 5056)        # Specular Planner is 5055
CHUNK_BYTES = 8 * 1024 * 1024                                   # browser upload chunk size

for d in (FLIGHTS_DIR, UPLOAD_TMP):
    d.mkdir(parents=True, exist_ok=True)
