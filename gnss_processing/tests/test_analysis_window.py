"""
`max_uptime_ms`: an analysis that uses only the first part of a tracked run.

The 2026-10-14 recording has a USB overflow at about 111 s that breaks every channel.
The grid builders stop the measurement epochs in front of it when asked to.
"""

from __future__ import annotations

import numpy as np
import pytest

from utils import observables as obs

from .test_navigation_multisystem import _fake_outputs


def _channels():
    uptime = np.arange(0.0, 20_000.0, 5.0)
    return {"G01": _fake_outputs(uptime, uptime + 0.4), "G02": _fake_outputs(uptime, uptime + 0.7)}


def test_the_common_grid_stops_at_the_limit():
    grid = obs.common_epoch_grid([_channels()], 100.0, max_uptime_ms=10_000.0)
    assert grid[0] == 0.0
    assert grid[-1] == 9_900.0


def test_without_a_limit_the_grid_covers_the_run():
    grid = obs.common_epoch_grid([_channels()], 100.0)
    assert grid[-1] > 19_000.0


def test_a_limit_before_the_run_starts_is_an_error():
    outputs = {"G01": _fake_outputs(np.arange(5_000.0, 9_000.0, 5.0), np.arange(5_000.0, 9_000.0, 5.0))}
    with pytest.raises(ValueError, match="do not overlap"):
        obs.common_epoch_grid([outputs], 100.0, max_uptime_ms=1_000.0)


def test_form_observables_stops_at_the_limit():
    channels = _channels()
    anchors = {
        sid: obs.TimeAnchor(sat_id=sid, tow_s=1000.0, code_phase_ms=0.0) for sid in channels
    }
    result = obs.form_observables(channels, anchors, epoch_interval_ms=100.0, max_uptime_ms=10_000.0)
    assert result.epoch_uptime_ms[-1] == 9_900.0
