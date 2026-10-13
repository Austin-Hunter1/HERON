"""
`utils.period_ambiguity`: the satellites' geometry corrects a satellite whose code
phase is off by whole code periods.

Same method as `test_navigation`: build a world with a known answer, break it in
one specific way, and check the search finds exactly that break.  Every check
that a fix is right compares against the TRUTH pseudoranges up to one constant
per epoch, because a shift every satellite shares is a receiver clock offset and
cannot (and need not) be told apart.
"""

from __future__ import annotations

import numpy as np
import pytest

from tests.test_navigation import REFERENCE_ECEF, build_truth
from utils import navigation as nav
from utils import period_ambiguity as pa

PERIOD_MS = 20.0
SETTINGS = nav.CorrectionSettings(True, True, True, False, False)
# A kilometre off, as a real a-priori would be.
A_PRIORI = REFERENCE_ECEF + np.array([600.0, -500.0, 400.0])


def config(**overrides):
    return pa.PeriodSearchConfig(period_ms=PERIOD_MS, **overrides)


def resolve(observables, ephemerides, **overrides):
    return pa.resolve_period_shifts(
        observables, ephemerides, signal_type_id="GPS_L5", a_priori_ecef_m=A_PRIORI,
        config=config(**overrides), settings=SETTINGS,
    )


def corrupt(observables, shifts):
    return pa.apply_period_shifts(observables, np.asarray(shifts), PERIOD_MS)


def assert_same_up_to_clock(fixed, truth):
    """Every satellite off by the same amount, per epoch: that is a clock offset."""
    difference = fixed.pseudorange_m - truth.pseudorange_m
    spread = np.nanmax(difference, axis=1) - np.nanmin(difference, axis=1)
    assert spread.max() < 1e-3


@pytest.fixture(scope="module")
def world():
    ephemerides, observables, _, _ = build_truth(num_epochs=10, satellite_count=8)
    return ephemerides, observables


def shifts_for(world, mapping):
    _, observables = world
    shifts = np.zeros(observables.pseudorange_m.shape)
    for sat_id, k in mapping.items():
        shifts[:, observables.sat_ids.index(sat_id)] = k
    return shifts


def test_a_consistent_set_is_left_alone(world):
    ephemerides, observables = world
    result = resolve(observables, ephemerides)
    assert not result.shifts.any()
    assert {r.status for r in result.runs} == {"unchanged"}


def test_one_satellite_a_period_off_is_found_and_fixed(world):
    ephemerides, observables = world
    sat = observables.sat_ids[3]
    broken = corrupt(observables, shifts_for(world, {sat: +1}))
    result = resolve(broken, ephemerides)
    assert result.count_shifted() == 1
    fixed = pa.apply_period_shifts(broken, result.shifts, PERIOD_MS)
    assert_same_up_to_clock(fixed, observables)


def test_several_satellites_off_in_both_directions_are_fixed(world):
    ephemerides, observables = world
    ids = observables.sat_ids
    mapping = {ids[1]: +1, ids[2]: -1, ids[5]: +1, ids[6]: +1}
    broken = corrupt(observables, shifts_for(world, mapping))
    result = resolve(broken, ephemerides)
    fixed = pa.apply_period_shifts(broken, result.shifts, PERIOD_MS)
    assert_same_up_to_clock(fixed, observables)


def test_the_reference_satellite_being_wrong_is_fixed_up_to_the_clock(world):
    ephemerides, observables = world
    broken = corrupt(observables, shifts_for(world, {observables.sat_ids[0]: +1}))
    result = resolve(broken, ephemerides)
    fixed = pa.apply_period_shifts(broken, result.shifts, PERIOD_MS)
    assert_same_up_to_clock(fixed, observables)


def test_the_fixed_set_solves_to_the_true_position(world):
    ephemerides, observables = world
    ids = observables.sat_ids
    broken = corrupt(observables, shifts_for(world, {ids[2]: +1, ids[4]: -1}))
    result = resolve(broken, ephemerides)
    fixed = pa.apply_period_shifts(broken, result.shifts, PERIOD_MS)
    series = nav.solve_series(
        fixed, ephemerides, signal_type_id="GPS_L5", settings=SETTINGS,
        initial_position_ecef_m=A_PRIORI,
    )
    error = np.linalg.norm(series.position_ecef_m - REFERENCE_ECEF, axis=1)
    # The unshifted positions the search used are a few metres off, the solve is not.
    assert np.nanmax(error) < 1.0


def test_an_error_that_is_not_a_whole_period_is_not_forced_onto_one(world):
    ephemerides, observables = world
    broken = corrupt(observables, shifts_for(world, {observables.sat_ids[3]: 0.5}))
    result = resolve(broken, ephemerides)
    assert not result.shifts.any()
    assert all(r.status == "unresolved" for r in result.runs)


def test_too_few_satellites_are_not_searched():
    ephemerides, observables, _, _ = build_truth(num_epochs=4, satellite_count=5)
    broken = corrupt(observables, np.eye(5, dtype=int)[None, 2].repeat(4, axis=0))
    result = pa.resolve_period_shifts(
        broken, ephemerides, signal_type_id="GPS_L5", a_priori_ecef_m=A_PRIORI,
        config=config(), settings=SETTINGS,
    )
    assert not result.shifts.any()
    assert result.runs[0].status == "unchanged"


def test_runs_follow_which_satellites_are_present(world):
    ephemerides, observables = world
    ids = observables.sat_ids
    broken = corrupt(observables, shifts_for(world, {ids[3]: +1}))
    # Another satellite drops out for epochs 4-6, as it would across a hole.
    pseudorange = broken.pseudorange_m.copy()
    pseudorange[4:7, 6] = np.nan
    broken = type(broken)(**{**broken.__dict__, "pseudorange_m": pseudorange})
    result = resolve(broken, ephemerides, probes_per_run=1)
    assert [(r.first_epoch, r.last_epoch) for r in result.runs] == [(0, 3), (4, 6), (7, 9)]
    assert result.count_shifted() == 3
    assert not result.shifts[4:7, 6].any()


def test_too_large_a_search_is_skipped_not_truncated(world):
    ephemerides, observables = world
    broken = corrupt(observables, shifts_for(world, {observables.sat_ids[3]: +1}))
    result = resolve(broken, ephemerides, max_combinations=100)
    assert not result.shifts.any()


def test_search_epoch_reports_how_much_better_the_best_is():
    ephemerides, observables, _, _ = build_truth(num_epochs=1, satellite_count=8)
    positions = nav.satellite_positions(
        observables, ephemerides, apply_sagnac=True, receiver_position_ecef_m=REFERENCE_ECEF
    )
    corrected, _ = nav.correct_pseudoranges(
        observables, ephemerides, signal_type_id="GPS_L5", settings=SETTINGS,
        receiver_position_ecef_m=REFERENCE_ECEF,
    )
    vectors = positions[0] - REFERENCE_ECEF
    ranges = np.linalg.norm(vectors, axis=1)
    found = pa.search_epoch(corrected[0] - ranges, vectors / ranges[:, None], config())
    assert not found.shifts.any()
    assert found.rms_m < 1.0
    assert found.margin > 100.0


def test_apply_rejects_a_shifts_array_of_the_wrong_shape(world):
    _, observables = world
    with pytest.raises(ValueError, match="shifts is"):
        pa.apply_period_shifts(observables, np.zeros((2, 2), dtype=int), PERIOD_MS)


def test_apply_leaves_missing_measurements_missing(world):
    _, observables = world
    pseudorange = observables.pseudorange_m.copy()
    pseudorange[0, 0] = np.nan
    gappy = type(observables)(**{**observables.__dict__, "pseudorange_m": pseudorange})
    shifted = pa.apply_period_shifts(
        gappy, np.ones(pseudorange.shape, dtype=int), PERIOD_MS
    )
    assert np.isnan(shifted.pseudorange_m[0, 0])
