"""
Load and check `configs/pipeline_gui.yml`.

All tunable values of the pipeline window are in that file, so a team member can
change a default without a code change.  The loader checks every value and
stops with a message that names the bad key.  It does not guess a value.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "configs" / "pipeline_gui.yml"
NOTEBOOK_IDS = ("00", "01", "02")


class ConfigError(ValueError):
    """A value in the pipeline config is missing or not valid."""


@dataclass(frozen=True)
class ReportConfig:
    """How the PDF report is laid out, and which text it copies."""

    page_size_in: tuple[float, float]
    dpi: int
    figure_grid: tuple[int, int]
    per_satellite_grid: tuple[int, int]
    per_satellite_pattern: str
    excerpts: dict[str, list[str]]
    max_excerpt_lines: int


@dataclass(frozen=True)
class PipelineConfig:
    """All settings of the pipeline window.  See `configs/pipeline_gui.yml`."""

    collects_dir: Path | None
    outputs_dir: Path | None
    reports_subdir: str
    cell_timeout_s: int
    notebook_python: str | None
    notebook_kernel: str | None
    start_offset_ms: float
    track_duration_s: float | None
    signals: dict[str, list[str]]
    signals_off_by_default: list[str]
    navigation_unsupported: list[str]
    report: ReportConfig


def _require(data: dict, key: str, where: str = "") -> Any:
    """Return `data[key]`, or stop with the full name of the missing key."""
    if not isinstance(data, dict) or key not in data:
        raise ConfigError(f"missing key '{where}{key}' in the pipeline config.")
    return data[key]


def _number(value: Any, key: str, *, positive: bool = False, allow_none: bool = False) -> float | None:
    """Check that `value` is a number (bool is not a number here)."""
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"'{key}' must be a number, got {value!r}.")
    if positive and value <= 0:
        raise ConfigError(f"'{key}' must be more than 0, got {value!r}.")
    if not positive and value < 0:
        raise ConfigError(f"'{key}' must not be negative, got {value!r}.")
    return float(value)


def _pair(value: Any, key: str, kind: type) -> tuple:
    """Check a two-item list, for example a page size or a figure grid."""
    if not isinstance(value, list) or len(value) != 2:
        raise ConfigError(f"'{key}' must be a list of two values, got {value!r}.")
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)) or item <= 0:
            raise ConfigError(f"'{key}' must hold two positive numbers, got {value!r}.")
        if kind is int and int(item) != item:
            raise ConfigError(f"'{key}' must hold two whole numbers, got {value!r}.")
    return tuple(kind(item) for item in value)


def _names(value: Any, key: str) -> list[str]:
    """Check a list of strings."""
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"'{key}' must be a list of names, got {value!r}.")
    return list(value)


def _optional_path(value: Any, key: str) -> Path | None:
    """`None` stays `None` (use the `.env` value); a string becomes a `Path`."""
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"'{key}' must be a folder path or null, got {value!r}.")
    return Path(value)


def _optional_text(value: Any, key: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"'{key}' must be text or null, got {value!r}.")
    return value


def _parse_report(data: Any) -> ReportConfig:
    """Check the `report:` block."""
    w = "report."
    excerpts_raw = _require(data, "excerpts", w)
    if not isinstance(excerpts_raw, dict):
        raise ConfigError("'report.excerpts' must map a notebook id to a list of headings.")
    excerpts: dict[str, list[str]] = {}
    for nb_id, headings in excerpts_raw.items():
        nb_id = str(nb_id)
        if nb_id not in NOTEBOOK_IDS:
            raise ConfigError(f"'report.excerpts' has key {nb_id!r}; use one of {NOTEBOOK_IDS}.")
        excerpts[nb_id] = _names(headings, f"report.excerpts.{nb_id}")

    pattern = _require(data, "per_satellite_pattern", w)
    if not isinstance(pattern, str):
        raise ConfigError("'report.per_satellite_pattern' must be a regular expression string.")
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ConfigError(
            f"'report.per_satellite_pattern' is not a valid regular expression: {exc}."
        ) from None

    return ReportConfig(
        page_size_in=_pair(_require(data, "page_size_in", w), "report.page_size_in", float),
        dpi=int(_number(_require(data, "dpi", w), "report.dpi", positive=True)),
        figure_grid=_pair(_require(data, "figure_grid", w), "report.figure_grid", int),
        per_satellite_grid=_pair(
            _require(data, "per_satellite_grid", w), "report.per_satellite_grid", int
        ),
        per_satellite_pattern=pattern,
        excerpts=excerpts,
        max_excerpt_lines=int(
            _number(_require(data, "max_excerpt_lines", w), "report.max_excerpt_lines", positive=True)
        ),
    )


def parse_config(data: Any) -> PipelineConfig:
    """
    Make a `PipelineConfig` from the parsed YAML, and check every value.

    Why a separate function: the tests give it a dictionary, with no file.
    """
    if not isinstance(data, dict):
        raise ConfigError("the pipeline config must be a YAML mapping (key: value lines).")

    signals_raw = _require(data, "signals")
    if not isinstance(signals_raw, dict) or not signals_raw:
        raise ConfigError("'signals' must map a band (L1, L2, L5) to a list of SIGNAL_ID names.")
    signals = {str(band): _names(names, f"signals.{band}") for band, names in signals_raw.items()}
    known = {name for names in signals.values() for name in names}

    off = _names(_require(data, "signals_off_by_default"), "signals_off_by_default")
    no_nav = _names(_require(data, "navigation_unsupported"), "navigation_unsupported")
    for key, names in (("signals_off_by_default", off), ("navigation_unsupported", no_nav)):
        unknown = sorted(set(names) - known)
        if unknown:
            raise ConfigError(f"'{key}' names {unknown}, which are not in 'signals'.")

    subdir = _require(data, "reports_subdir")
    if not isinstance(subdir, str) or not subdir.strip():
        raise ConfigError(f"'reports_subdir' must be a folder name, got {subdir!r}.")

    return PipelineConfig(
        collects_dir=_optional_path(_require(data, "collects_dir"), "collects_dir"),
        outputs_dir=_optional_path(_require(data, "outputs_dir"), "outputs_dir"),
        reports_subdir=subdir,
        cell_timeout_s=int(_number(_require(data, "cell_timeout_s"), "cell_timeout_s", positive=True)),
        notebook_python=_optional_text(_require(data, "notebook_python"), "notebook_python"),
        notebook_kernel=_optional_text(_require(data, "notebook_kernel"), "notebook_kernel"),
        start_offset_ms=_number(_require(data, "start_offset_ms"), "start_offset_ms"),
        track_duration_s=_number(
            _require(data, "track_duration_s"), "track_duration_s", positive=True, allow_none=True
        ),
        signals=signals,
        signals_off_by_default=off,
        navigation_unsupported=no_nav,
        report=_parse_report(_require(data, "report")),
    )


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> PipelineConfig:
    """Read and check the config file.  A missing file is a clear error."""
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"pipeline config not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
    try:
        return parse_config(data)
    except ConfigError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
