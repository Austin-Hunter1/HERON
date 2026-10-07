"""Import Jack Abrams' Specular_Planner engine without copying it.

Why: the engine lives in its own folder and has its own imports
(`from engine.almanac import ...`). We add that folder to `sys.path`
once. The folder comes from the config, so it can move.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


def load_engine(planner_dir: Path) -> SimpleNamespace:
    """Return the engine modules as `SimpleNamespace(geometry=..., gnss=..., coverage=...)`."""
    planner_dir = Path(planner_dir)
    if not (planner_dir / "engine" / "geometry.py").is_file():
        raise FileNotFoundError(
            f"Specular_Planner engine not found in {planner_dir}. "
            "Check [paths] planner_dir in the config."
        )
    if str(planner_dir) not in sys.path:
        sys.path.insert(0, str(planner_dir))
    from engine import coverage, geometry, gnss  # type: ignore[import-not-found]

    return SimpleNamespace(geometry=geometry, gnss=gnss, coverage=coverage)
