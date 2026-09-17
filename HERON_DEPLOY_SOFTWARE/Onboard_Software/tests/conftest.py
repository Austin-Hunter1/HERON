"""Shared fixtures for the onboard tests."""

from pathlib import Path

import pytest
from heron_onboard.config import OnboardConfig, validate_onboard_dict
from heron_onboard.testing import minimal_config_dict

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


@pytest.fixture
def config(tmp_path: Path) -> OnboardConfig:
    return validate_onboard_dict(minimal_config_dict(tmp_path))


@pytest.fixture
def config_dir() -> Path:
    return CONFIG_DIR
