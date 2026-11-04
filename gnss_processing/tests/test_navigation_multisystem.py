"""
Two-system navigation: a GPS + BeiDou fix with an inter-system bias (D-057), C/N0
weights, and the time-scale bookkeeping behind them.

The method is that of `test_navigation.py`: build a world with a known receiver,
clock and bias, make the pseudoranges that world produces by an independent route
(iterating on the transit time, rotating the earth longhand), and require the solver
to give back what went in.  BeiDou is the part that is new: its orbit is a function of
BDT, 14 s behind GPST, and its satellites stamp their signals with their own clock and
pilot group delay.  A wrong 14 s, a wrong sign on the group delay, or a bias that leaks
into the position all show up as a failed recovery here.
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import numpy as np
import pytest

from utils import navigation as nav
from utils import observables as obs
from utils import time_systems
from utils.nav.ephemeris import (
    BDS_SAT_TYPE_MEO,
    OMEGA_E_DOT,
    SPEED_OF_LIGHT,
    BdsEphemeris,
)

from .test_navigation import REFERENCE_ECEF, TOE, visible_constellation

C = SPEED_OF_LIGHT
BDT_TOE = TOE - time_systems.GPST_MINUS_BDT_S  # the same instant, in BDT
RECEIVER_BIAS_M = 1234.5
INTER_SYSTEM_BIAS_M = 87.5


def _beidou_ephemeris(sat_id: str, *, M0: float, Omega0: float, a0: float, tgd: float) -> BdsEphemeris:
    return BdsEphemeris(
        sat_id=sat_id, week=1084, toe=BDT_TOE, sat_type=BDS_SAT_TYPE_MEO,
        delta_a=0.0, a_dot=0.0, delta_n0=0.0, delta_n0_dot=0.0, M0=M0, e=0.001,
        omega=0.3, Omega0=Omega0, Omega_dot=-2.6e-9, i0=0.305, i_dot=0.0,
        Cis=0.0, Cic=0.0, Crs=0.0, Crc=0.0, Cus=0.0, Cuc=0.0,
        # A clock drift, so a clock evaluated 14 s off (the wrong time scale) is visible:
        # 2e-9 s/s over 14 s is 8 m.
        toc=BDT_TOE, a0=a0, a1=2.0e-9, a2=0.0, tgd_b2ap=tgd, tgd_b1cp=-2.5 * tgd + 1.0e-9,
    )


def beidou_constellation(count: int = 5) -> dict[str, BdsEphemeris]:
    """MEO satellites that are above 10 degrees at the test epoch, spread in azimuth."""
    found = []
    for k in range(200):
        eph = _beidou_ephemeris(
            f"C{k + 6:02d}", M0=((k * 0.211) % 2.0) - 1.0, Omega0=((k * 0.389) % 2.0) - 1.0,
            a0=2.0e-4 * (((k * 7) % 11) - 5) / 5.0, tgd=1.0e-9 * ((k % 5) - 2),
        )
        position = eph.orbit_state(BDT_TOE).position_ecef_m
        azimuth, elevation = nav.sky_positions(REFERENCE_ECEF, position)
        if elevation[0] > 10.0 and all(abs(((azimuth[0] - a) + 180) % 360 - 180) > 35 for a in [x[1] for x in found]):
            found.append((eph, azimuth[0]))
        if len(found) == count:
            break
    assert len(found) == count, f"only {len(found)} BeiDou satellites are visible in the fixture"
    return {e.sat_id: e for e, _ in found}


def first_beidou_id() -> str:
    """The id of one BeiDou satellite of the fixture, to give a fault to."""
    return sorted(beidou_constellation(5))[0]


def _transit_solution(eph, receive_time_gpst: float, *, native_offset_s: float):
    """Transmit time (GPST) and receive-frame position, by iteration on the transit time."""
    transit = 0.075
    for _ in range(20):
        t_tx = receive_time_gpst - transit
        position = eph.orbit_state(t_tx + native_offset_s).position_ecef_m
        theta = OMEGA_E_DOT * transit
        rotated = np.array(
            [
                position[0] * np.cos(theta) + position[1] * np.sin(theta),
                -position[0] * np.sin(theta) + position[1] * np.cos(theta),
                position[2],
            ]
        )
        transit = np.linalg.norm(rotated - REFERENCE_ECEF) / SPEED_OF_LIGHT
    return receive_time_gpst - transit


def build_two_system_truth(
    *,
    num_epochs: int = 4,
    gps_count: int = 6,
    bds_count: int = 5,
    isb_m: float = INTER_SYSTEM_BIAS_M,
    bds_range_error_m: dict[str, float] | None = None,
    visible: dict[int, set[str]] | None = None,
    beidou_signal: str = "BDS_B2A",
):
    """
    GPS + BeiDou observables in one GPST set.

    `beidou_signal` is the BeiDou signal the satellites stamp: "BDS_B2A" (pilot group
    delay `tgd_b2ap`) or "BDS_B1C" (pilot group delay `tgd_b1cp`, which the fixture gives
    a value unlike `tgd_b2ap`, so a solver that used the wrong one would show it).

    The receiver clock is `RECEIVER_BIAS_M` off.  BeiDou ranges carry `isb_m` in addition.
    Each BeiDou satellite stamps its signal with its clock error and minus its pilot
    group delay (ICD 7-4), BDT being 14 s behind GPST.
    """
    from .test_navigation import make_ephemeris

    gps = visible_constellation(gps_count)
    bds = beidou_constellation(bds_count)
    sat_ids = sorted(gps) + sorted(bds)
    ephemerides = {**gps, **bds}
    n, m = num_epochs, len(sat_ids)
    true_receive = TOE + np.arange(n) * 1.0
    transmit = np.zeros((n, m))
    for i in range(n):
        for j, sat_id in enumerate(sat_ids):
            eph = ephemerides[sat_id]
            if isinstance(eph, BdsEphemeris):
                t_tx = _transit_solution(eph, true_receive[i], native_offset_s=-time_systems.GPST_MINUS_BDT_S)
                t_bdt = t_tx - time_systems.GPST_MINUS_BDT_S
                # What the satellite stamps (BDT scale) moved to the GPST scale of the set.
                stamp_bdt = t_bdt + eph.clock_correction_s(t_bdt) + eph.group_delay_s(
                    "B2ap" if beidou_signal == "BDS_B2A" else "B1Cp"
                )
                transmit[i, j] = stamp_bdt + time_systems.GPST_MINUS_BDT_S
            else:
                t_tx = _transit_solution(eph, true_receive[i], native_offset_s=0.0)
                transmit[i, j] = t_tx + eph.clock_correction_s(t_tx)

    bias = np.full(n, RECEIVER_BIAS_M)
    nominal_receive = true_receive + bias / C
    pseudorange = C * (nominal_receive[:, None] - transmit)
    is_bds = np.array([sid.startswith("C") for sid in sat_ids])
    pseudorange[:, is_bds] += isb_m
    for sat_id, error in (bds_range_error_m or {}).items():
        pseudorange[:, sat_ids.index(sat_id)] += error
    if visible is not None:
        for i, names in visible.items():
            for j, sat_id in enumerate(sat_ids):
                if sat_id not in names:
                    pseudorange[i, j] = np.nan
                    transmit[i, j] = np.nan

    types = {sid: (beidou_signal if sid.startswith("C") else "GPS_L5") for sid in sat_ids}
    observables = obs.Observables(
        epoch_uptime_ms=np.arange(n) * 1000.0, receive_time_s=nominal_receive, sat_ids=sat_ids,
        transmit_time_s=transmit, pseudorange_m=pseudorange, week=2257,
        time_system="GPST", signal_type_ids=types,
    )
    return ephemerides, observables


SETTINGS = nav.CorrectionSettings(
    satellite_clock=True, group_delay=True, sagnac=True, troposphere=False, ionosphere=False
)


def _solve(observables, ephemerides, **kwargs):
    return nav.solve_series(
        observables, ephemerides, signal_type_id="GPS_L5", settings=SETTINGS,
        initial_position_ecef_m=REFERENCE_ECEF, **kwargs,
    )


# --- the fix ---------------------------------------------------------------

def test_a_two_system_fix_recovers_position_clock_and_inter_system_bias():
    ephemerides, observables = build_two_system_truth()
    series = _solve(observables, ephemerides)
    assert series.valid.all()
    assert np.abs(series.position_ecef_m - REFERENCE_ECEF).max() < 0.05
    assert series.clock_bias_m == pytest.approx(RECEIVER_BIAS_M, abs=0.05)
    assert series.inter_system_bias_m["C"] == pytest.approx(
        np.full(4, INTER_SYSTEM_BIAS_M), abs=0.05
    )
    assert np.all(series.num_satellites == 11)
    assert not series.inter_system_bias_fixed.any()
    assert np.nanmax(np.abs(series.residuals_m)) < 0.05


@pytest.mark.parametrize("isb_m", [-250.0, 0.0, 87.5, 3000.0])
def test_the_bias_never_leaks_into_the_position(isb_m):
    ephemerides, observables = build_two_system_truth(isb_m=isb_m)
    series = _solve(observables, ephemerides)
    assert np.abs(series.position_ecef_m - REFERENCE_ECEF).max() < 0.05
    assert series.inter_system_bias_m["C"][0] == pytest.approx(isb_m, abs=0.05)


def test_the_fix_does_not_need_the_antenna_position():
    """Seeded from nothing (the satellite centroid), it lands on the same answer."""
    ephemerides, observables = build_two_system_truth()
    seeded = _solve(observables, ephemerides)
    unseeded = nav.solve_series(
        observables, ephemerides, signal_type_id="GPS_L5", settings=SETTINGS,
        initial_position_ecef_m=None,
    )
    assert unseeded.valid.all()
    # Without an a-priori the Sagnac transit falls back to the receiver clock; the
    # bias here is small, so the two agree to centimetres.
    assert np.abs(unseeded.position_ecef_m - seeded.position_ecef_m).max() < 0.2


def test_a_beidou_only_set_in_bdt_gives_the_same_fix():
    """A BeiDou-only set may stay in BDT; the solver moves nothing it should not."""
    ephemerides, observables = build_two_system_truth(gps_count=4)
    bds_columns = [j for j, s in enumerate(observables.sat_ids) if s.startswith("C")]
    shift = -time_systems.GPST_MINUS_BDT_S
    bdt_only = obs.Observables(
        epoch_uptime_ms=observables.epoch_uptime_ms,
        receive_time_s=observables.receive_time_s + shift,
        sat_ids=[observables.sat_ids[j] for j in bds_columns],
        transmit_time_s=observables.transmit_time_s[:, bds_columns] + shift,
        pseudorange_m=observables.pseudorange_m[:, bds_columns],
        time_system="BDT",
    )
    series = nav.solve_series(
        bdt_only, ephemerides, signal_type_id="BDS_B2A", settings=SETTINGS,
        initial_position_ecef_m=REFERENCE_ECEF,
    )
    assert series.valid.all() and not series.inter_system_bias_m
    # Five satellites amplify the double-precision rounding of times of week near
    # 300 000 s (6e-11 s, about 2 cm of range) into a few centimetres of position.
    assert np.abs(series.position_ecef_m - REFERENCE_ECEF).max() < 0.15
    # One system: the clock absorbs the 87.5 m, nothing else has to.
    assert series.clock_bias_m == pytest.approx(RECEIVER_BIAS_M + INTER_SYSTEM_BIAS_M, abs=0.05)


def test_a_b1c_fix_uses_the_b1c_group_delay():
    """
    B1C satellites stamp their signal with `tgd_b1cp`, not `tgd_b2ap` (B1C ICD 7-4).  The
    solver must pick the delay by each satellite's signal type; the other one is wrong
    by a few nanoseconds, which is decimetres of range.
    """
    ephemerides, observables = build_two_system_truth(beidou_signal="BDS_B1C")
    series = _solve(observables, ephemerides)
    assert series.valid.all()
    assert np.abs(series.position_ecef_m - REFERENCE_ECEF).max() < 0.05
    assert series.inter_system_bias_m["C"] == pytest.approx(np.full(4, INTER_SYSTEM_BIAS_M), abs=0.05)
    assert np.nanmax(np.abs(series.residuals_m)) < 0.05
    # The same ranges read as B2a use the wrong delay and leave a visible residual.
    as_b2a = dataclasses.replace(
        observables, signal_type_ids={s: ("BDS_B2A" if s.startswith("C") else "GPS_L5") for s in observables.sat_ids}
    )
    wrong = _solve(as_b2a, ephemerides)
    assert np.nanmax(np.abs(wrong.residuals_m)) > 5 * np.nanmax(np.abs(series.residuals_m))


def test_a_wrong_14_seconds_would_show_in_the_residuals():
    """The guard behind the time scale: treat BDT times as GPST and the fit is ruined."""
    ephemerides, observables = build_two_system_truth()
    wrong = dataclasses.replace(observables, signal_type_ids=observables.signal_type_ids)
    # Move the BeiDou half of the set into the wrong scale.
    for j, sat_id in enumerate(wrong.sat_ids):
        if sat_id.startswith("C"):
            wrong.transmit_time_s = wrong.transmit_time_s.copy()
            wrong.transmit_time_s[:, j] -= 14.0
    series = _solve(wrong, ephemerides)
    assert (not series.valid.any()) or np.nanmax(np.abs(series.residuals_m)) > 1000.0


def test_without_an_inter_system_bias_the_residuals_would_be_large():
    """With the extra unknown the residuals are zero; with the bias left in, they are not."""
    ephemerides, observables = build_two_system_truth()
    gps_columns = [j for j, s in enumerate(observables.sat_ids) if s.startswith("G")]
    gps_only = dataclasses.replace(
        observables,
        sat_ids=[observables.sat_ids[j] for j in gps_columns],
        transmit_time_s=observables.transmit_time_s[:, gps_columns],
        pseudorange_m=observables.pseudorange_m[:, gps_columns],
    )
    series = _solve(gps_only, ephemerides)
    assert np.abs(series.position_ecef_m - REFERENCE_ECEF).max() < 0.05  # GPS alone is fine


# --- too few satellites to estimate the bias -------------------------------

def test_an_epoch_with_four_satellites_uses_the_median_bias_and_is_marked():
    gps_ids = sorted(visible_constellation(6))
    bds_ids = sorted(beidou_constellation(5))
    visible = {
        0: set(gps_ids) | set(bds_ids),
        1: set(gps_ids) | set(bds_ids),
        2: set(gps_ids[:2]) | set(bds_ids[:2]),  # four satellites, two systems
        3: set(gps_ids) | set(bds_ids),
    }
    ephemerides, observables = build_two_system_truth(visible=visible)
    series = _solve(observables, ephemerides)
    assert series.valid.all()
    assert list(series.num_satellites) == [11, 11, 4, 11]
    assert list(series.inter_system_bias_fixed) == [False, False, True, False]
    assert series.inter_system_bias_m["C"][2] == pytest.approx(INTER_SYSTEM_BIAS_M, abs=0.05)
    assert np.abs(series.position_ecef_m[2] - REFERENCE_ECEF).max() < 1.0


def test_four_satellites_of_two_systems_and_no_estimate_gives_no_fix():
    """Nothing to take the bias from: the epoch is left unsolved, not guessed."""
    gps_ids = sorted(visible_constellation(6))
    bds_ids = sorted(beidou_constellation(5))
    ephemerides, observables = build_two_system_truth(
        num_epochs=1, visible={0: set(gps_ids[:2]) | set(bds_ids[:2])}
    )
    series = _solve(observables, ephemerides)
    assert not series.valid.any()


def test_a_bias_can_be_held_for_every_epoch():
    ephemerides, observables = build_two_system_truth()
    series = _solve(observables, ephemerides, fixed_inter_system_bias_m={"C": INTER_SYSTEM_BIAS_M})
    assert series.valid.all()
    assert np.abs(series.position_ecef_m - REFERENCE_ECEF).max() < 0.05
    assert series.inter_system_bias_fixed.all()


# --- weights ---------------------------------------------------------------

def test_weight_map_corners_and_ends():
    weights = nav.WeightMap()
    assert weights(np.array([30.0, 35.0, 43.5, 52.0, 60.0])) == pytest.approx(
        [0.1, 0.1, 1.55, 3.0, 3.0]
    )
    assert weights(np.array([np.nan]))[0] == 0.1  # no estimate: low weight, not dropped
    custom = nav.WeightMap(low_cn0_dbhz=20.0, high_cn0_dbhz=40.0, low_weight=1.0, high_weight=5.0)
    assert custom(np.array([30.0]))[0] == pytest.approx(3.0)
    with pytest.raises(ValueError):
        nav.WeightMap(low_cn0_dbhz=50.0, high_cn0_dbhz=40.0)
    with pytest.raises(ValueError):
        nav.WeightMap(low_weight=2.0, high_weight=1.0)


def test_weights_pull_the_fix_away_from_a_bad_satellite():
    """One BeiDou satellite 60 m long: trusting it less moves the fix toward the truth."""
    bad_id = first_beidou_id()
    ephemerides, observables = build_two_system_truth(bds_range_error_m={bad_id: 60.0}, num_epochs=1)
    n, m = observables.pseudorange_m.shape
    bad = observables.sat_ids.index(bad_id)
    equal = _solve(observables, ephemerides)
    low = np.ones((n, m))
    low[:, bad] = 0.05
    down = _solve(observables, ephemerides, weights=low)
    high = np.ones((n, m))
    high[:, bad] = 20.0
    up = _solve(observables, ephemerides, weights=high)
    error = lambda s: np.linalg.norm(s.position_ecef_m[0] - REFERENCE_ECEF)
    assert error(down) < error(equal) < error(up)
    assert error(down) < 0.5 * error(equal)


def test_uniform_weights_do_not_change_the_answer():
    ephemerides, observables = build_two_system_truth(num_epochs=2)
    plain = _solve(observables, ephemerides)
    n, m = observables.pseudorange_m.shape
    weighted = _solve(observables, ephemerides, weights=np.full((n, m), 2.5))
    assert np.abs(plain.position_ecef_m - weighted.position_ecef_m).max() < 1e-6


def test_weights_must_match_the_observables():
    ephemerides, observables = build_two_system_truth(num_epochs=2)
    with pytest.raises(ValueError, match="weights must be shaped"):
        _solve(observables, ephemerides, weights=np.ones((2, 3)))


def test_weighted_solver_agrees_with_the_original_one_for_one_system():
    """The two code paths must give the same fix on GPS alone."""
    from .test_navigation import build_truth

    ephemerides, observables, _, _ = build_truth(num_epochs=1)
    corrected, _ = nav.correct_pseudoranges(
        observables, ephemerides, signal_type_id="GPS_L5", settings=SETTINGS,
        receiver_position_ecef_m=REFERENCE_ECEF,
    )
    positions = nav.satellite_positions(
        observables, ephemerides, receiver_position_ecef_m=REFERENCE_ECEF
    )
    original = nav.solve_epoch(corrected[0], positions[0], observables.sat_ids)
    weighted = nav.solve_epoch(
        corrected[0], positions[0], observables.sat_ids, weights=np.full(len(observables.sat_ids), 3.0)
    )
    assert np.abs(original.position_ecef_m - weighted.position_ecef_m).max() < 1e-3
    assert original.clock_bias_m == pytest.approx(weighted.clock_bias_m, abs=1e-3)


# --- RAIM with two systems -------------------------------------------------

def test_raim_drops_a_bad_beidou_satellite_in_a_two_system_fix():
    bad_id = first_beidou_id()
    ephemerides, observables = build_two_system_truth(bds_range_error_m={bad_id: 400.0}, num_epochs=1)
    series = _solve(observables, ephemerides, residual_gate_m=100.0)
    assert series.excluded_sat_ids[0] == [bad_id]
    assert np.linalg.norm(series.position_ecef_m[0] - REFERENCE_ECEF) < 0.1


# --- joining runs (D-058) --------------------------------------------------

def _split_parts(observables, receive_offset_s: float):
    """Split a combined set into a GPS part and a BeiDou part, as two separate runs would be."""
    parts = []
    for letter, offset in (("G", 0.0), ("C", receive_offset_s)):
        columns = [j for j, s in enumerate(observables.sat_ids) if s.startswith(letter)]
        parts.append(
            obs.Observables(
                epoch_uptime_ms=observables.epoch_uptime_ms,
                # Each run set its receive time separately, to a different nominal value.
                receive_time_s=observables.receive_time_s + offset,
                sat_ids=[observables.sat_ids[j] for j in columns],
                transmit_time_s=observables.transmit_time_s[:, columns],
                pseudorange_m=observables.pseudorange_m[:, columns] + C * offset,
                time_system="GPST",
            )
        )
    return parts


def test_combined_runs_give_the_same_fix_with_the_receive_time_difference_in_the_bias():
    ephemerides, observables = build_two_system_truth()
    gps_part, bds_part = _split_parts(observables, receive_offset_s=0.031)
    combined = obs.combine_observables([gps_part, bds_part], ["GPS_L5", "BDS_B2A"])
    assert combined.sat_ids == gps_part.sat_ids + bds_part.sat_ids
    assert combined.signal_type_ids[first_beidou_id()] == "BDS_B2A"
    series = _solve(combined, ephemerides)
    assert np.abs(series.position_ecef_m - REFERENCE_ECEF).max() < 0.05
    # The BeiDou run measured against a receive time 31 ms later than the GPS run's.  Joined on
    # the GPS receive time its ranges are 31 ms (9.3 thousand km) too long... and the
    # bias unknown takes exactly that, so what is reported is the true bias.
    assert series.inter_system_bias_m["C"][0] == pytest.approx(INTER_SYSTEM_BIAS_M, abs=0.05)


def test_combining_refuses_different_epoch_grids_and_duplicate_satellites():
    ephemerides, observables = build_two_system_truth()
    gps_part, bds_part = _split_parts(observables, 0.0)
    shifted = dataclasses.replace(bds_part, epoch_uptime_ms=bds_part.epoch_uptime_ms + 1.0)
    with pytest.raises(ValueError, match="different receiver instants"):
        obs.combine_observables([gps_part, shifted], ["GPS_L5", "BDS_B2A"])
    with pytest.raises(ValueError, match="more than one part"):
        obs.combine_observables([gps_part, gps_part], ["GPS_L5", "GPS_L5"])
    with pytest.raises(ValueError, match="one signal type id"):
        obs.combine_observables([gps_part, bds_part], ["GPS_L5"])


def test_combining_a_bdt_part_moves_it_into_gpst():
    ephemerides, observables = build_two_system_truth()
    gps_part, bds_part = _split_parts(observables, 0.0)
    shift = -time_systems.GPST_MINUS_BDT_S
    bdt_part = dataclasses.replace(
        bds_part, time_system="BDT", receive_time_s=bds_part.receive_time_s + shift,
        transmit_time_s=bds_part.transmit_time_s + shift,
    )
    combined = obs.combine_observables([gps_part, bdt_part], ["GPS_L5", "BDS_B2A"])
    assert combined.time_system == "GPST"
    series = _solve(combined, ephemerides)
    assert np.abs(series.position_ecef_m - REFERENCE_ECEF).max() < 0.05


# --- forming observables from anchors in two systems -----------------------

def _fake_outputs(uptime_ms: np.ndarray, code_phase_ms: np.ndarray):
    return SimpleNamespace(valid=slice(0, len(uptime_ms)), uptime_epoch_ms=uptime_ms, code_phase_ms=code_phase_ms)


def test_form_observables_puts_beidou_anchors_on_the_gpst_scale_when_mixed():
    uptime = np.arange(0.0, 3000.0, 5.0)
    outputs = {"G01": _fake_outputs(uptime, uptime + 0.4), "C13": _fake_outputs(uptime, uptime + 0.7)}
    anchors = {
        "G01": obs.TimeAnchor("G01", tow_s=1000.0, code_phase_ms=0.4),
        "C13": obs.TimeAnchor("C13", tow_s=1000.0, code_phase_ms=0.7, time_system="BDT"),
    }
    mixed = obs.form_observables(outputs, anchors, epoch_interval_ms=500.0)
    assert mixed.time_system == "GPST"
    # The same BDT reading is 14 s later on the GPST scale.
    bds, gps = mixed.sat_ids.index("C13"), mixed.sat_ids.index("G01")
    assert mixed.transmit_time_s[0, bds] - mixed.transmit_time_s[0, gps] == pytest.approx(14.0)
    bds_only = obs.form_observables({"C13": outputs["C13"]}, {"C13": anchors["C13"]}, epoch_interval_ms=500.0)
    assert bds_only.time_system == "BDT"
    assert bds_only.transmit_time_s[0, 0] == pytest.approx(1000.0)


def test_convert_time_of_week_and_ephemeris_time():
    assert time_systems.convert_time_of_week(100.0, "BDT", "GPST") == 114.0
    assert time_systems.convert_time_of_week(100.0, "GPST", "BDT") == 86.0
    assert time_systems.convert_time_of_week(100.0, "GPST", "GPST") == 100.0
    assert time_systems.convert_time_of_week(np.array([1.0, 2.0]), "GPST", "BDT").tolist() == [-13.0, -12.0]
    with pytest.raises(ValueError):
        time_systems.convert_time_of_week(1.0, "GLONASS", "GPST")
    bds = _beidou_ephemeris("C13", M0=0.1, Omega0=0.1, a0=0.0, tgd=0.0)
    gps = next(iter(visible_constellation(4).values()))
    assert obs.ephemeris_time_s(bds, 1000.0, "GPST") == 986.0
    assert obs.ephemeris_time_s(bds, 986.0, "BDT") == 986.0
    assert obs.ephemeris_time_s(gps, 1000.0, "BDT") == 1014.0


def test_cn0_on_the_epoch_grid():
    outputs = SimpleNamespace(
        cn0_valid=slice(0, 3),
        cn0_uptime_ms=np.array([0.0, 500.0, 1000.0, 0.0]),
        cn0_dbhz=np.array([[40.0, 42.0], [44.0, 46.0], [48.0, 50.0], [0.0, 0.0]]),
    )
    got = obs.cn0_on_grid(outputs, np.array([-100.0, 250.0, 1000.0, 1500.0]), component=1)
    assert np.isnan(got[0]) and np.isnan(got[3])
    assert got[1] == pytest.approx(44.0)
    assert got[2] == pytest.approx(50.0)
    empty = SimpleNamespace(cn0_valid=slice(0, 0), cn0_uptime_ms=np.zeros(4), cn0_dbhz=np.zeros((4, 2)))
    assert np.isnan(obs.cn0_on_grid(empty, np.array([1.0]), 0)).all()
