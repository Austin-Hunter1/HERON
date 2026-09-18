"""Load and validate TOML config files.

All tunable values live in TOML files (REQUIREMENTS S1). Each program
defines a pydantic model for its config. This module loads the file,
validates it against the model, and fails with one clear message that
lists every bad or missing key.

Example::

    from heron_common.config import load_config
    cfg = load_config(Path("onboard.toml"), OnboardConfig)
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class ConfigError(Exception):
    """The config file is missing, unreadable, or fails validation."""


def load_toml(path: Path) -> dict:
    """Read a TOML file and return it as a dict.

    Raise ``ConfigError`` when the file does not exist or has a syntax
    error. The message names the file so the operator can find it.
    """
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Config file {path} has a TOML syntax error: {exc}") from exc


def validate_config(data: dict, model: type[T], source: str = "<dict>") -> T:
    """Validate a dict against a pydantic model.

    Convert pydantic's error list into one readable ``ConfigError``.
    Each line names the key path and the problem, for example
    ``capture.segment_seconds: Input should be greater than 0``.
    """
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        lines = []
        for err in exc.errors():
            key = ".".join(str(part) for part in err["loc"]) or "<root>"
            lines.append(f"  {key}: {err['msg']}")
        joined = "\n".join(lines)
        raise ConfigError(f"Config {source} is not valid:\n{joined}") from exc


def load_config(path: Path, model: type[T]) -> T:
    """Load a TOML file and validate it against ``model``.

    This is the one entry point programs use at start-up. It raises
    ``ConfigError`` on any problem, so the caller can print the message
    and exit with a non-zero code.
    """
    data = load_toml(path)
    return validate_config(data, model, source=str(path))
