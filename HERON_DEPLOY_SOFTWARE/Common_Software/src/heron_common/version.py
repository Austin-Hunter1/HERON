"""Report the software version.

Each recording's metadata must record the git hash of the running code
(REQUIREMENTS O5). At run time the code may not sit in a git checkout
(for example after a packaged install), so this module tries git first
and then falls back to a ``HERON_VERSION`` environment variable.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

UNKNOWN_VERSION = "unknown"


def get_git_hash(start: Path | None = None) -> str:
    """Return the short git hash of the checkout that contains ``start``.

    Append ``-dirty`` when the working tree has uncommitted changes.
    Return ``HERON_VERSION`` from the environment when git is not
    available, and ``"unknown"`` when neither works.
    """
    env_version = os.environ.get("HERON_VERSION")
    cwd = start or Path(__file__).resolve().parent
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if out.returncode != 0:
            return env_version or UNKNOWN_VERSION
        version = out.stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if dirty.returncode == 0 and dirty.stdout.strip():
            version += "-dirty"
        return version
    except (OSError, subprocess.SubprocessError):
        return env_version or UNKNOWN_VERSION
