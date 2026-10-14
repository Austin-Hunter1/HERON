"""
Runtime configuration loaded from a `.env` file at the repository root (see the
template in README.md). Importing this module loads `.env` into the process
environment, so `gnss_tools`'s own `os.environ.get(...)` reads (e.g.
`cddis_download_utils.py`'s Earthdata credentials) pick the values up too --
notebooks and scripts don't need to call `load_dotenv()` themselves.

One `.env` can serve several machines.  Each directory-valued variable may be
given a per-platform key next to the plain one, and the key for the platform
running now wins over the plain one:

    COLLECTS_PATH_WINDOWS=D:/01_Data/collects
    COLLECTS_PATH_LINUX=/media/austin/PhD_Workspace/01_Data/collects
    COLLECTS_PATH=/some/fallback            # used where no per-platform key matches

The suffixes are `_WINDOWS`, `_LINUX` and `_MACOS`.  A variable set in the shell
before Python starts always wins over everything in `.env`, so a one-off override
(`COLLECTS_PATH=... python ...`) still works.  WSL reports itself as Linux.

Each environment variable has a corresponding `get_*()` accessor below,
documented with what the variable controls. Directory-valued variables are
resolved to a `Path` and checked for existence at call time; a missing
directory emits a `warnings.warn` rather than raising, since the caller is
often about to create it (e.g. a fresh `local-data/`). A variable that has no
usable default and is simply unset does still raise -- there's no path to
warn about in that case.
"""

from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path
from typing import MutableMapping, Optional, Tuple

import dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent

# What the shell had set before `.env` was read.  `load_dotenv` never overrides an
# existing variable, and a variable the caller set on purpose should not be
# overridden by a per-platform key either.
_SHELL_ENVIRONMENT = dict(os.environ)

# Variables that name a directory on this machine and so may differ between machines.
PLATFORM_DEPENDENT_VARIABLES = ("OUTPUTS_PATH", "RESOURCES_PATH", "COLLECTS_PATH")

_PLATFORM_SUFFIXES = (("win32", "WINDOWS"), ("linux", "LINUX"), ("darwin", "MACOS"))


def platform_suffix(platform: Optional[str] = None) -> Optional[str]:
    """`WINDOWS`, `LINUX` or `MACOS` for `sys.platform` (or `platform`); `None` for anything else."""
    platform = sys.platform if platform is None else platform
    for prefix, suffix in _PLATFORM_SUFFIXES:
        if platform.startswith(prefix):
            return suffix
    return None


def apply_platform_overrides(
    environ: MutableMapping[str, str],
    shell_environment: dict,
    *,
    platform: Optional[str] = None,
    names: Tuple[str, ...] = PLATFORM_DEPENDENT_VARIABLES,
) -> None:
    """
    For each of `names`, copy `<NAME>_<PLATFORM>` over `<NAME>` in `environ`, unless
    the shell already set `<NAME>`.  Writing it into `environ` (not just into this
    module's own variables) is what lets `gnss_tools`, which reads `RESOURCES_PATH`
    from the environment itself, see the platform's value too.
    """
    suffix = platform_suffix(platform)
    if suffix is None:
        return
    for name in names:
        if name in shell_environment:
            continue
        specific = environ.get(f"{name}_{suffix}")
        if specific:
            environ[name] = specific


dotenv.load_dotenv(REPO_ROOT / ".env")
apply_platform_overrides(os.environ, _SHELL_ENVIRONMENT)

_OUTPUTS_PATH: Optional[str] = os.environ.get("OUTPUTS_PATH") or None
_RESOURCES_PATH: Optional[str] = os.environ.get("RESOURCES_PATH") or None
_COLLECTS_PATH: Optional[str] = os.environ.get("COLLECTS_PATH") or None
_EARTHDATA_USERNAME: Optional[str] = os.environ.get("EARTHDATA_USERNAME") or None
_EARTHDATA_PASSWORD: Optional[str] = os.environ.get("EARTHDATA_PASSWORD") or None


def _resolve_dir(env_var: str, value: Optional[str], *, default: Optional[Path] = None) -> Path:
    """Resolve `env_var`'s value to a `Path`, warning (not raising) if it's missing on disk."""
    if value:
        path = Path(value)
    elif default is not None:
        path = default
    else:
        raise ValueError(f"{env_var} is not set (see the `.env` template in README.md).")
    if not path.exists():
        warnings.warn(f"{env_var} points to '{path}', but that directory does not exist.")
    return path


def get_outputs_path() -> Path:
    """
    `OUTPUTS_PATH`: working directory for processing outputs (acquisition/
    tracking results, logs, etc.). Defaults to `<repo_root>/local-data` if
    unset.
    """
    return _resolve_dir("OUTPUTS_PATH", _OUTPUTS_PATH, default=REPO_ROOT / "local-data")


def get_resources_path() -> Path:
    """
    `RESOURCES_PATH`: base data directory used by `gnss_tools` for downloaded
    RINEX/orbit (SP3) data (`gnss_tools.rinex_io.cddis_download_utils`,
    `gnss_tools.misc.rinex_obs_utils`). No local default -- those helpers
    fall back to the current working directory if it's unset, which is
    rarely what you want, so this raises instead of guessing a path.
    """
    return _resolve_dir("RESOURCES_PATH", _RESOURCES_PATH)


def get_collects_path() -> Path:
    """
    `COLLECTS_PATH`: directory containing GNSS raw data collects, one
    subdirectory per experiment (`<experiment_name>/metadata.yml` plus the
    raw IQ files it references). No local default; raises if unset.
    """
    return _resolve_dir("COLLECTS_PATH", _COLLECTS_PATH)


def get_earthdata_credentials() -> Tuple[Optional[str], Optional[str]]:
    """
    `EARTHDATA_USERNAME` / `EARTHDATA_PASSWORD`: credentials for downloading
    RINEX/orbit (SP3) data from CDDIS/Earthdata
    (`gnss_tools.rinex_io.cddis_download_utils` / `earthdata_utils`). Returns
    `(username, password)`, either of which may be `None` if unset -- callers
    that need both for HTTP basic auth should check before using them.
    """
    if _EARTHDATA_USERNAME is None or _EARTHDATA_PASSWORD is None:
        warnings.warn("EARTHDATA_USERNAME and/or EARTHDATA_PASSWORD are not set.")
    return _EARTHDATA_USERNAME, _EARTHDATA_PASSWORD
