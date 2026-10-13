"""
How `utils.observables` treats the hole a lost-and-reacquired channel leaves.

A merged tracking record (`tracking_supervisor.merge_segments`) has a hole in
`uptime_epoch_ms` where the channel was down, and the code phase after the hole
carries the code periods the dropped samples took with them.  Interpolating
straight across would invent a measurement; these tests pin down that nothing is
invented, and that the default (no gap handling) is still the old behaviour.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from utils import observables as obs

EPOCH_MS = 5.0
JUMP_MS = 0.4  # code periods the dropped samples took with them


def make_outputs(hole_start_ms: float = 1000.0, hole_end_ms: float = 1800.0):
    """
    A record with a 5 ms epoch spacing, one hole, and a code-phase jump at it.

    Code phase runs at exactly 1 ms per ms before the hole, and JUMP_MS ahead of
    that after it -- the shape `try_reacquire` leaves behind.
    """
    before = np.arange(0.0, hole_start_ms + EPOCH_MS, EPOCH_MS)
    after = np.arange(hole_end_ms, 3000.0 + EPOCH_MS, EPOCH_MS)
    uptime = np.concatenate([before, after])
    code_phase = uptime.copy()
    code_phase[len(before):] += JUMP_MS
    return SimpleNamespace(
        valid=slice(0, len(uptime)),
        uptime_epoch_ms=uptime,
        code_phase_ms=code_phase,
    )


def test_default_interpolates_across_the_hole():
    """The old behaviour stays the default, so existing callers do not change."""
    outputs = make_outputs()
    grid = np.array([500.0, 1400.0, 2000.0])
    result = obs._interpolate_code_phase(outputs, grid)
    assert np.all(np.isfinite(result))


def test_hole_returns_nan_and_only_inside_it():
    outputs = make_outputs()
    grid = np.array([500.0, 999.0, 1200.0, 1799.0, 2000.0])
    result = obs._interpolate_code_phase(outputs, grid, max_gap_factor=3.0)
    assert np.isfinite(result[[0, 1, 4]]).all()
    assert np.isnan(result[[2, 3]]).all()


def test_values_outside_the_hole_are_unchanged_by_gap_handling():
    outputs = make_outputs()
    grid = np.arange(100.0, 900.0, 37.0)
    plain = obs._interpolate_code_phase(outputs, grid)
    gated = obs._interpolate_code_phase(outputs, grid, max_gap_factor=3.0)
    np.testing.assert_array_equal(plain, gated)


def test_guard_extends_the_nan_region_past_the_far_edge():
    outputs = make_outputs()
    grid = np.array([1805.0, 1900.0, 2050.0])
    unguarded = obs._interpolate_code_phase(outputs, grid, max_gap_factor=3.0)
    guarded = obs._interpolate_code_phase(
        outputs, grid, max_gap_factor=3.0, guard_after_gap_ms=200.0
    )
    assert np.isfinite(unguarded).all()
    # 1805 and 1900 are within 200 ms of the first post-hole epoch (1800).
    assert np.isnan(guarded[:2]).all()
    assert np.isfinite(guarded[2])


def test_ordinary_epoch_jitter_is_not_a_hole():
    """The threshold is relative to the median spacing; a slightly late epoch
    must not blank out the measurements around it."""
    outputs = make_outputs()
    uptime = outputs.uptime_epoch_ms.copy()
    keep = np.ones(len(uptime), dtype=bool)
    keep[50] = False  # one epoch missing -> a 10 ms interval, 2x the median
    outputs = SimpleNamespace(
        valid=slice(0, int(keep.sum())),
        uptime_epoch_ms=uptime[keep],
        code_phase_ms=outputs.code_phase_ms[keep],
    )
    grid = np.array([uptime[50]])
    result = obs._interpolate_code_phase(outputs, grid, max_gap_factor=3.0)
    assert np.isfinite(result).all()


def test_too_short_a_record_still_returns_nan():
    outputs = SimpleNamespace(
        valid=slice(0, 1),
        uptime_epoch_ms=np.array([0.0]),
        code_phase_ms=np.array([0.0]),
    )
    result = obs._interpolate_code_phase(outputs, np.array([0.0]), max_gap_factor=3.0)
    assert np.isnan(result).all()


@pytest.mark.parametrize("factor", [None, 3.0])
def test_form_observables_passes_the_options_through(factor):
    outputs = make_outputs()
    anchors = {"G01": obs.TimeAnchor(sat_id="G01", tow_s=100.0, code_phase_ms=0.0)}
    grid = np.array([500.0, 1400.0, 2000.0])
    result = obs.form_observables(
        {"G01": outputs}, anchors, epoch_uptime_ms=grid, max_gap_factor=factor,
    )
    in_hole = result.transmit_time_s[1, 0]
    assert np.isfinite(in_hole) == (factor is None)
    assert np.isfinite(result.transmit_time_s[[0, 2], 0]).all()
