"""Config validation tests, including the template regression (TESTING.md)."""

from pathlib import Path

import pytest
from heron_common.config import ConfigError
from heron_onboard.config import load_onboard_config, validate_onboard_dict
from heron_onboard.testing import minimal_config_dict

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


@pytest.mark.parametrize("name", sorted(p.name for p in CONFIG_DIR.glob("*.toml")))
def test_every_template_loads(name: str):
    """Every config template in the repository must validate."""
    cfg = load_onboard_config(CONFIG_DIR / name)
    assert cfg.sdr


def test_aggregate_rate(tmp_path: Path):
    """The sizing rule: sum of rate x 2 bytes x channels."""
    cfg = validate_onboard_dict(minimal_config_dict(tmp_path))
    assert cfg.aggregate_bytes_per_second == 22e6 * 2 * 2 + 10e6 * 2 * 1


def test_duplicate_sdr_serial_rejected(tmp_path: Path):
    """Two SDRs with one serial number is a config error."""
    data = minimal_config_dict(tmp_path)
    data["sdr"][1]["serial"] = "S1"
    with pytest.raises(ConfigError, match="serial"):
        validate_onboard_dict(data)


def test_duplicate_channel_index_rejected(tmp_path: Path):
    data = minimal_config_dict(tmp_path)
    data["sdr"][0]["channels"][1]["index"] = 0
    with pytest.raises(ConfigError, match="index"):
        validate_onboard_dict(data)


def test_bad_id_characters_rejected(tmp_path: Path):
    """Ids go into file paths, so only safe characters are allowed."""
    data = minimal_config_dict(tmp_path)
    data["sdr"][0]["id"] = "b210/1"
    with pytest.raises(ConfigError, match="letters"):
        validate_onboard_dict(data)


def test_missing_sdr_list_rejected():
    with pytest.raises(ConfigError, match="sdr"):
        validate_onboard_dict({})


def test_uhd_args_include_serial(tmp_path: Path):
    cfg = validate_onboard_dict(minimal_config_dict(tmp_path))
    assert cfg.sdr[0].uhd_args == "serial=S1"
    cfg.sdr[0].device_args = "num_recv_frames=256"
    assert cfg.sdr[0].uhd_args == "serial=S1,num_recv_frames=256"
