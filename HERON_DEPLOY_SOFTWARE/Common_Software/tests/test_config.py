"""Tests for config loading and validation."""

from pathlib import Path

import pytest
from heron_common.config import ConfigError, load_config, validate_config
from pydantic import BaseModel, Field


class _Sub(BaseModel):
    rate: float = Field(gt=0)


class _Model(BaseModel):
    name: str
    sub: _Sub


def test_load_config_ok(tmp_path: Path):
    """A valid TOML file loads into the model."""
    p = tmp_path / "c.toml"
    p.write_text('name = "x"\n[sub]\nrate = 2.5\n')
    cfg = load_config(p, _Model)
    assert cfg.sub.rate == 2.5


def test_missing_file_is_clear(tmp_path: Path):
    """A missing file names the path in the error."""
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml", _Model)


def test_syntax_error_is_clear(tmp_path: Path):
    """A TOML syntax error names the file."""
    p = tmp_path / "bad.toml"
    p.write_text("name = \n")
    with pytest.raises(ConfigError, match="syntax"):
        load_config(p, _Model)


def test_validation_error_lists_keys():
    """Every bad key appears with its dotted path."""
    with pytest.raises(ConfigError) as exc:
        validate_config({"sub": {"rate": -1}}, _Model, "test")
    text = str(exc.value)
    assert "name" in text and "sub.rate" in text
