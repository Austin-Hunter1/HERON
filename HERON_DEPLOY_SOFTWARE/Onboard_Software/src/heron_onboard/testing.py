"""Helpers for tests and the demo mode.

``minimal_config_dict`` returns a small valid onboard config that uses
local paths, so a test or a demo runs on any machine with no SDRs.
"""

from __future__ import annotations

from pathlib import Path


def minimal_config_dict(tmp_path: Path, **overrides: dict) -> dict:
    """A valid config with one B210 (2 ch) and one B200mini (1 ch).

    ``overrides`` merge into the top-level tables, for example
    ``control={"link_grace_s": 5}``.
    """
    data: dict = {
        "general": {"log_dir": str(tmp_path / "logs")},
        "disk": {"data_root": str(tmp_path / "iq"), "min_free_gb": 0, "min_free_pct": 0},
        "capture": {"recorder_binary": str(tmp_path / "heron_recorder")},
        "sdr": [
            {
                "id": "b210_1",
                "model": "b210",
                "serial": "S1",
                "sample_rate_hz": 22e6,
                "clock_source": "internal",
                "time_source": "none",
                "channels": [
                    {
                        "id": "L5_direct",
                        "index": 0,
                        "center_freq_hz": 1176.45e6,
                        "gain_db": 45,
                        "bandwidth_hz": 20e6,
                    },
                    {
                        "id": "L5_refl",
                        "index": 1,
                        "center_freq_hz": 1176.45e6,
                        "gain_db": 45,
                        "bandwidth_hz": 20e6,
                    },
                ],
            },
            {
                "id": "b200mini_1",
                "model": "b200mini",
                "serial": "S2",
                "sample_rate_hz": 10e6,
                "clock_source": "internal",
                "time_source": "none",
                "channels": [
                    {
                        "id": "L1_refl",
                        "index": 0,
                        "center_freq_hz": 1575.42e6,
                        "gain_db": 40,
                        "bandwidth_hz": 10e6,
                    },
                ],
            },
        ],
    }
    for key, value in overrides.items():
        data.setdefault(key, {}).update(value)
    return data
