"""Band validation for metadata.yml (D-022)."""

from pathlib import Path

import pytest
from heron_common.config import ConfigError
from heron_onboard.config import validate_onboard_dict
from heron_onboard.testing import minimal_config_dict


def test_unknown_band_rejected(tmp_path: Path):
    """A channel band must exist in the [bands] table."""
    data = minimal_config_dict(tmp_path)
    data["sdr"][0]["channels"][0]["band"] = "L2"
    with pytest.raises(ConfigError, match=r"not in \[bands\]"):
        validate_onboard_dict(data)


def test_same_band_different_centre_rejected(tmp_path: Path):
    """metadata.yml keeps one intermediate frequency per band."""
    data = minimal_config_dict(tmp_path)
    data["sdr"][0]["channels"][1]["center_freq_hz"] = 1176.0e6
    with pytest.raises(ConfigError, match="different center_freq_hz"):
        validate_onboard_dict(data)


def test_channel_without_band_is_allowed(tmp_path: Path):
    data = minimal_config_dict(tmp_path)
    data["sdr"][0]["channels"][0].pop("band")
    assert validate_onboard_dict(data).sdr[0].channels[0].band == ""
