"""Every base config template must load (config regression)."""

from pathlib import Path

import pytest
from heron_base.config import load_base_config, validate_base_dict

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


@pytest.mark.parametrize("name", sorted(p.name for p in CONFIG_DIR.glob("*.toml")))
def test_every_template_loads(name: str):
    cfg = load_base_config(CONFIG_DIR / name)
    assert cfg.link.transport in ("loopback", "serial", "udp", "mavlink")


def test_defaults_are_valid():
    cfg = validate_base_dict({})
    assert cfg.link.transport == "loopback" and not cfg.gnss.enabled
