"""
Observables and the navigation solution, against a scenario with a known answer.

The method throughout: build a world where the truth is known exactly -- a
receiver at a chosen position with a chosen clock offset, satellites on real
Keplerian orbits -- then generate the pseudoranges that world would produce and
check the solver recovers what went in.

That is worth more than checking each function in isolation, because the failures
that matter here are sign conventions and coordinate orderings.  A flipped Sagnac
rotation, a swapped (lat, lon), or a satellite clock correction subtracted instead
of added all leave every individual function looking reasonable and the fix
quietly tens of metres out.  A closed-loop test catches all three.

`_light_time_solution` deliberately constructs the truth by iterating on transit
time, which is the *opposite* of what `utils.navigation` does -- it reads the
transmit time from measured code phase and never iterates.  Two independent routes
to the same geometry is the point.
"""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from utils import navigation as nav
from utils import observables as obs
from utils.nav.ephemeris import OMEGA_E_DOT, SPEED_OF_LIGHT, LnavEphemeris

# CU Boulder, near enough for a test.
REFERENCE_LAT = 40.0076
REFERENCE_LON = -105.2659
REFERENCE_ALT = 1655.0
REFERENCE_ECEF = nav.geodetic_to_ecef(REFERENCE_LAT, REFERENCE_LON, REFERENCE_ALT)

TOE = 302400.0


def make_ephemeris(sat_id: str, *, M0: float, Omega0: float, af0: float = 0.0) -> LnavEphemeris:
    """A realistic GPS orbit, differing only in where the satellite sits on it."""
    return LnavEphemeris(
        sat_id=sat_id,
        week=2257,
        toe=TOE,
        sqrt_a=5153.65,
        e=0.004,
        i0=0.9617 / np.pi,
        i_dot=0.0,
        Omega0=Omega0,
        Omega_dot=-8.1e-9 / np.pi,
        omega=0.3,
        M0=M0,
        deln=0.0,
        Cuc=0.0, Cus=0.0, Crc=0.0, Crs=0.0, Cic=0.0, Cis=0.0,
        toc=TOE,
        af0=af0,
        af1=0.0,
        af2=0.0,
        tgd=0.0,
    )


def visible_constellation(count: int = 6) -> dict[str, LnavEphemeris]:
    """
    A well-spread set of visible satellites, chosen by minimising PDOP.

    Taking the first few that clear the horizon is not good enough: a dense sweep
    of orbital elements tends to produce satellites clustered overhead, and four of
    those give a geometry matrix with a condition number in the hundreds of
    thousands.  The fix would then be hundreds of metres out for reasons that have
    nothing to do with the code under test.

    So candidates are enumerated, then chosen greedily to minimise PDOP -- which
    also makes the fixture resemble a real sky, where a receiver picks up
    satellites at a spread of elevations and azimuths.
    """
    candidates: list[LnavEphemeris] = []
    for k in range(60):
        eph = make_ephemeris(
            f"G{k + 1:02d}",
            M0=((k * 0.137) % 2.0) - 1.0,
            Omega0=((k * 0.313) % 2.0) - 1.0,
            af0=1e-5 * ((k % 25) - 12),
        )
        position = eph.orbit_state(TOE).position_ecef_m
        _, elevation = nav.sky_positions(REFERENCE_ECEF, position)
        if elevation[0] > 10.0:
            candidates.append(eph)

    if len(candidates) < count:
        raise RuntimeError(
            f"only {len(candidates)} synthetic satellites are visible; the fixture "
            "cannot build a constellation"
        )

    def unit_vector(eph: LnavEphemeris) -> np.ndarray:
        delta = eph.orbit_state(TOE).position_ecef_m - REFERENCE_ECEF
        return delta / np.linalg.norm(delta)

    def pdop(chosen: list[LnavEphemeris]) -> float:
        G = np.column_stack(
            [-np.array([unit_vector(e) for e in chosen]), np.ones(len(chosen))]
        )
        try:
            return float(np.sqrt(np.trace(np.linalg.inv(G.T @ G)[:3, :3])))
        except np.linalg.LinAlgError:
            return float("inf")

    chosen: list[LnavEphemeris] = []
    remaining = list(candidates)
    while len(chosen) < count and remaining:
        best = min(
            remaining,
            key=lambda e: pdop(chosen + [e]) if len(chosen) >= 3 else -unit_vector(e)[2],
        )
        chosen.append(best)
        remaining.remove(best)
    return {e.sat_id: e for e in chosen}


def test_the_fixture_constellation_has_usable_geometry():
    """
    A guard on the fixture itself.  If the synthetic sky is degenerate, every
    solver test below fails for a reason that has nothing to do with the solver.
    """
    ephemerides = visible_constellation()
    assert len(ephemerides) == 6
    directions = []
    for eph in ephemerides.values():
        delta = eph.orbit_state(TOE).position_ecef_m - REFERENCE_ECEF
        directions.append(-delta / np.linalg.norm(delta))
    G = np.column_stack([np.array(directions), np.ones(len(directions))])
    assert np.linalg.cond(G) < 100.0, f"condition number {np.linalg.cond(G):.0f}"


def _light_time_solution(eph: LnavEphemeris, receive_time_s: float) -> tuple[float, np.ndarray]:
    """
    Truth generator: the transmit time and receive-frame position for one signal.

    Iterates on transit time until the geometric range matches -- the classical
    formulation, and independent of how `utils.navigation` gets there.  The
    Sagnac rotation is written out longhand here for the same reason.
    """
    transit = 0.07
    for _ in range(20):
        t_tx = receive_time_s - transit
        position = eph.orbit_state(t_tx).position_ecef_m
        theta = OMEGA_E_DOT * transit
        rotated = np.array(
            [
                position[0] * np.cos(theta) + position[1] * np.sin(theta),
                -position[0] * np.sin(theta) + position[1] * np.cos(theta),
                position[2],
            ]
        )
        transit = np.linalg.norm(rotated - REFERENCE_ECEF) / SPEED_OF_LIGHT
    return receive_time_s - transit, rotated


def build_truth(
    *,
    receiver_clock_bias_m: float = 1234.5,
    clock_drift_ppm: float = 0.0,
    num_epochs: int = 5,
    epoch_interval_s: float = 1.0,
    apply_satellite_clock: bool = True,
):
    """
    A complete synthetic scenario: ephemerides, observables, and the truth.

    The receiver clock is offset from GPS time by `receiver_clock_bias_m / c` and
    optionally drifts, which is exactly the situation a real sampled front end is
    in -- its uptime counter is not GPS time.
    """
    ephemerides = visible_constellation()
    sat_ids = sorted(ephemerides)
    n, m = num_epochs, len(sat_ids)

    true_receive = TOE + np.arange(n) * epoch_interval_s
    transmit = np.zeros((n, m))
    geometric = np.zeros((n, m))
    for i in range(n):
        for j, sat_id in enumerate(sat_ids):
            t_tx, rotated = _light_time_solution(ephemerides[sat_id], true_receive[i])
            transmit[i, j] = t_tx
            geometric[i, j] = np.linalg.norm(rotated - REFERENCE_ECEF)

    # The satellite's own clock runs off GPS time, so what it stamps on the signal
    # is t_sv = t + dt_sv.  That is what a receiver measures as the transmit time.
    sv_time = transmit.copy()
    if apply_satellite_clock:
        for i in range(n):
            for j, sat_id in enumerate(sat_ids):
                sv_time[i, j] = transmit[i, j] + ephemerides[sat_id].clock_correction_s(
                    transmit[i, j]
                )

    # The receiver's clock is offset (and possibly drifting) from GPS time.
    elapsed = true_receive - true_receive[0]
    bias_m = receiver_clock_bias_m + clock_drift_ppm * 1e-6 * SPEED_OF_LIGHT * elapsed
    nominal_receive = true_receive + bias_m / SPEED_OF_LIGHT

    observables = obs.Observables(
        epoch_uptime_ms=elapsed * 1000.0,
        receive_time_s=nominal_receive,
        sat_ids=sat_ids,
        transmit_time_s=sv_time,
        pseudorange_m=SPEED_OF_LIGHT * (nominal_receive[:, None] - sv_time),
        week=2257,
    )
    return ephemerides, observables, geometric, bias_m


# ---------------------------------------------------------------------------
# Time anchoring
# ---------------------------------------------------------------------------


def test_anchor_converts_code_phase_to_transmit_time():
    anchor = obs.TimeAnchor(sat_id="G01", tow_s=302400.0, code_phase_ms=5000.0)
    # 1000 ms further along in code phase is exactly 1 s later in satellite time.
    assert anchor.transmit_time_s(6000.0) == pytest.approx(302401.0)
    assert anchor.transmit_time_s(4000.0) == pytest.approx(302399.0)


def test_anchor_is_exact_not_approximate():
    """Code phase advances at the satellite's own rate, so this is a definition,
    not a first-order approximation -- check it to the nanosecond over an hour."""
    anchor = obs.TimeAnchor(sat_id="G01", tow_s=0.0, code_phase_ms=0.0)
    assert anchor.transmit_time_s(3.6e6) == pytest.approx(3600.0, abs=1e-9)


def test_form_observables_rejects_unanchored_channels():
    with pytest.raises(ValueError, match="time anchor"):
        obs.form_observables({"G01": object()}, {})


# ---------------------------------------------------------------------------
# Satellite positions and Sagnac
# ---------------------------------------------------------------------------


def test_satellite_positions_match_the_light_time_construction():
    ephemerides, observables, _, _ = build_truth()
    positions = nav.satellite_positions(observables, ephemerides, apply_sagnac=True)
    for j, sat_id in enumerate(observables.sat_ids):
        _, expected = _light_time_solution(ephemerides[sat_id], observables.receive_time_s[0])
        # The truth generator rotates by the true transit; the module rotates by the
        # measured one.  They differ by the receiver clock bias over c, which is
        # microseconds -- millimetres of rotation.
        assert np.linalg.norm(positions[0, j] - expected) < 0.1


def test_sagnac_matters_and_has_the_right_sign():
    """
    A flipped rotation is the classic bug: it produces a displacement of the same
    magnitude in the wrong direction, so a magnitude check alone would pass.  This
    compares against the longhand rotation in the truth generator.
    """
    ephemerides, observables, _, _ = build_truth()
    with_sagnac = nav.satellite_positions(observables, ephemerides, apply_sagnac=True)
    without = nav.satellite_positions(observables, ephemerides, apply_sagnac=False)

    displacement = np.linalg.norm(with_sagnac[0] - without[0], axis=1)
    assert np.all(displacement > 5.0), "Sagnac should move a satellite tens of metres"
    assert np.all(displacement < 200.0)

    for j, sat_id in enumerate(observables.sat_ids):
        _, expected = _light_time_solution(ephemerides[sat_id], observables.receive_time_s[0])
        assert np.linalg.norm(with_sagnac[0, j] - expected) < 0.1
        # And the uncorrected one is genuinely further away, not just different.
        assert np.linalg.norm(without[0, j] - expected) > 5.0


# ---------------------------------------------------------------------------
# The fix
# ---------------------------------------------------------------------------


def test_position_and_clock_are_recovered():
    ephemerides, observables, _, bias_m = build_truth(receiver_clock_bias_m=1234.5)
    series = nav.solve_series(
        observables,
        ephemerides,
        signal_type_id="GPS_L5",
        settings=nav.CorrectionSettings(
            satellite_clock=True,
            group_delay=True,
            sagnac=True,
            troposphere=False,
            ionosphere=False,
        ),
        initial_position_ecef_m=REFERENCE_ECEF,
    )
    assert series.valid.all()
    error = np.linalg.norm(series.position_ecef_m - REFERENCE_ECEF, axis=1)
    # About 3 cm, and *not* for the reason this comment used to give.  It is not the
    # Sagnac transit: `solve_series` is given an a-priori, so that rotation is
    # computed geometrically and the receiver clock bias never enters it.  The floor
    # is the same to two decimals with the bias set to zero and with group delay
    # off, so it is neither -- it is the difference between the truth generator's
    # iterated light-time construction and the module's measured-transmit-time
    # route, which is exactly the independence that makes this test worth having.
    assert error.max() < 0.1, f"worst position error {error.max():.6f} m"
    assert np.allclose(series.clock_bias_m, bias_m, atol=0.1)


def test_omitting_the_satellite_clock_ruins_the_fix():
    """
    The correction is tens of kilometres.  This is the test that catches it being
    applied with the wrong sign, because a sign error doubles the error rather than
    removing it.
    """
    ephemerides, observables, _, _ = build_truth()
    settings = nav.CorrectionSettings(
        satellite_clock=False, group_delay=False, sagnac=True,
        troposphere=False, ionosphere=False,
    )
    series = nav.solve_series(
        observables, ephemerides, signal_type_id="GPS_L5",
        settings=settings, initial_position_ecef_m=REFERENCE_ECEF,
    )
    error = np.linalg.norm(series.position_ecef_m - REFERENCE_ECEF, axis=1)
    assert error.min() > 100.0, "an uncorrected satellite clock cannot give a good fix"


def test_omitting_sagnac_costs_tens_of_metres():
    ephemerides, observables, _, _ = build_truth()
    good = nav.solve_series(
        observables, ephemerides, signal_type_id="GPS_L5",
        settings=nav.CorrectionSettings(True, True, True, False, False),
        initial_position_ecef_m=REFERENCE_ECEF,
    )
    bad = nav.solve_series(
        observables, ephemerides, signal_type_id="GPS_L5",
        settings=nav.CorrectionSettings(True, True, False, False, False),
        initial_position_ecef_m=REFERENCE_ECEF,
    )
    good_error = np.linalg.norm(good.position_ecef_m[0] - REFERENCE_ECEF)
    bad_error = np.linalg.norm(bad.position_ecef_m[0] - REFERENCE_ECEF)
    assert good_error < 0.1
    assert bad_error > 5.0


def test_residuals_are_tiny_when_everything_is_consistent():
    ephemerides, observables, _, _ = build_truth()
    series = nav.solve_series(
        observables, ephemerides, signal_type_id="GPS_L5",
        settings=nav.CorrectionSettings(True, True, True, False, False),
        initial_position_ecef_m=REFERENCE_ECEF,
    )
    residuals = series.residuals_m[np.isfinite(series.residuals_m)]
    assert np.abs(residuals).max() < 0.1


def test_solve_epoch_refuses_fewer_than_four_satellites():
    ephemerides, observables, _, _ = build_truth()
    positions = nav.satellite_positions(observables, ephemerides)
    assert (
        nav.solve_epoch(
            observables.pseudorange_m[0][:3], positions[0][:3], observables.sat_ids[:3]
        )
        is None
    )


def test_a_four_satellite_fix_is_flagged_as_not_overdetermined():
    ephemerides, observables, _, _ = build_truth()
    corrected, _ = nav.correct_pseudoranges(
        observables, ephemerides, signal_type_id="GPS_L5",
        settings=nav.CorrectionSettings(True, True, True, False, False),
    )
    positions = nav.satellite_positions(observables, ephemerides)
    solution = nav.solve_epoch(corrected[0][:4], positions[0][:4], observables.sat_ids[:4])
    assert solution is not None
    assert not solution.is_overdetermined
    # With four satellites and four unknowns the residuals are zero by
    # construction and say nothing about measurement quality.
    assert np.abs(solution.residuals_m).max() < 1e-6


# ---------------------------------------------------------------------------
# Clock solution
# ---------------------------------------------------------------------------


def test_clock_drift_is_recovered():
    """
    The 'clock solution': the receiver's sample clock runs at a slightly wrong
    rate, and the slope of the estimated bias measures it in ppm.
    """
    ephemerides, observables, _, _ = build_truth(
        receiver_clock_bias_m=500.0, clock_drift_ppm=2.5, num_epochs=20, epoch_interval_s=1.0
    )
    series = nav.solve_series(
        observables, ephemerides, signal_type_id="GPS_L5",
        settings=nav.CorrectionSettings(True, True, True, False, False),
        initial_position_ecef_m=REFERENCE_ECEF,
    )
    drift_ppm, rms = series.clock_drift_ppm()
    assert drift_ppm == pytest.approx(2.5, abs=0.01)
    assert rms < 0.1


def test_clock_drift_is_nan_without_enough_fixes():
    series = nav.SolutionSeries(
        epoch_uptime_ms=np.zeros(1),
        receive_time_s=np.zeros(1),
        position_ecef_m=np.full((1, 3), np.nan),
        clock_bias_m=np.full(1, np.nan),
        num_satellites=np.zeros(1, dtype=int),
        residuals_m=np.full((1, 1), np.nan),
        sat_ids=["G01"],
    )
    drift, rms = series.clock_drift_ppm()
    assert np.isnan(drift) and np.isnan(rms)


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def test_dop_values_are_ordered_sensibly():
    ephemerides, observables, _, _ = build_truth()
    corrected, _ = nav.correct_pseudoranges(
        observables, ephemerides, signal_type_id="GPS_L5",
        settings=nav.CorrectionSettings(True, True, True, False, False),
    )
    positions = nav.satellite_positions(observables, ephemerides)
    solution = nav.solve_epoch(corrected[0], positions[0], observables.sat_ids)
    dop = solution.dop
    assert dop.gdop >= dop.pdop >= dop.hdop
    assert dop.gdop >= dop.tdop
    assert 0 < dop.pdop < 100
    # VDOP exceeds HDOP for any ground receiver: every satellite is above, so the
    # vertical direction is far less well constrained than the horizontal.
    assert dop.vdop > dop.hdop


def test_geodetic_round_trip_keeps_latitude_first():
    """
    gnss_tools orders geodetic coordinates (lon, lat, alt).  These two wrappers
    exist to keep that from leaking, and this is the test that says so.
    """
    lat, lon, height = nav.ecef_to_geodetic(REFERENCE_ECEF)
    assert lat == pytest.approx(REFERENCE_LAT, abs=1e-6)
    assert lon == pytest.approx(REFERENCE_LON, abs=1e-6)
    assert height == pytest.approx(REFERENCE_ALT, abs=1e-3)


def test_sky_positions_put_satellites_above_the_horizon():
    ephemerides = visible_constellation()
    for eph in ephemerides.values():
        position = eph.orbit_state(TOE).position_ecef_m
        azimuth, elevation = nav.sky_positions(REFERENCE_ECEF, position)
        assert 0.0 <= azimuth[0] + 360.0 * (azimuth[0] < 0) <= 360.0
        assert elevation[0] > 10.0


def test_enu_about_the_true_position_is_near_zero():
    ephemerides, observables, _, _ = build_truth()
    series = nav.solve_series(
        observables, ephemerides, signal_type_id="GPS_L5",
        settings=nav.CorrectionSettings(True, True, True, False, False),
        initial_position_ecef_m=REFERENCE_ECEF,
    )
    enu = series.enu_about(REFERENCE_ECEF)
    assert np.abs(enu[series.valid]).max() < 0.1


# ---------------------------------------------------------------------------
# Atmosphere
# ---------------------------------------------------------------------------


def test_troposphere_is_metres_at_zenith_and_grows_toward_the_horizon():
    zenith = obs.saastamoinen_delay_m(
        np.deg2rad(90.0), height_m=1655.0, latitude_rad=np.deg2rad(40.0)
    )
    low = obs.saastamoinen_delay_m(
        np.deg2rad(10.0), height_m=1655.0, latitude_rad=np.deg2rad(40.0)
    )
    assert 1.5 < zenith < 2.6, f"zenith delay {zenith:.2f} m is not physical"
    assert low > 4 * zenith
    assert low < 30.0


def test_troposphere_decreases_with_site_height():
    sea_level = obs.saastamoinen_delay_m(
        np.deg2rad(90.0), height_m=0.0, latitude_rad=np.deg2rad(40.0)
    )
    mountain = obs.saastamoinen_delay_m(
        np.deg2rad(90.0), height_m=3000.0, latitude_rad=np.deg2rad(40.0)
    )
    assert mountain < sea_level


def test_klobuchar_is_nanoseconds_and_larger_at_low_elevation():
    alpha = (1.02e-8, 2.24e-8, -4.17e-8, -1.79e-7)
    beta = (8.60e4, 9.83e4, -6.55e4, -5.24e5)
    kwargs = dict(
        latitude_semicircles=40.0 / 180.0,
        longitude_semicircles=-105.0 / 180.0,
        azimuth_semicircles=0.5,
        gps_time_of_week_s=302400.0,
    )
    high = obs.klobuchar_delay_s(alpha, beta, elevation_semicircles=80.0 / 180.0, **kwargs)
    low = obs.klobuchar_delay_s(alpha, beta, elevation_semicircles=10.0 / 180.0, **kwargs)
    assert 0 < high < 5e-8, "a zenith ionospheric delay is a few nanoseconds"
    assert low > high
    assert low < 5e-7


def test_klobuchar_scales_with_the_inverse_square_of_frequency():
    """
    The model is defined for L1.  On L5 the delay is (f_L1/f_L5)^2 larger -- about
    1.79 -- and ignoring that leaves nearly half the ionosphere in an L5 fix.
    """
    alpha = (1.02e-8, 2.24e-8, -4.17e-8, -1.79e-7)
    beta = (8.60e4, 9.83e4, -6.55e4, -5.24e5)
    kwargs = dict(
        latitude_semicircles=40.0 / 180.0,
        longitude_semicircles=-105.0 / 180.0,
        elevation_semicircles=45.0 / 180.0,
        azimuth_semicircles=0.5,
        gps_time_of_week_s=302400.0,
    )
    l1 = obs.klobuchar_delay_s(alpha, beta, frequency_hz=obs.L1_FREQ_HZ, **kwargs)
    l5 = obs.klobuchar_delay_s(alpha, beta, frequency_hz=obs.L5_FREQ_HZ, **kwargs)
    assert l5 / l1 == pytest.approx((obs.L1_FREQ_HZ / obs.L5_FREQ_HZ) ** 2)


def test_atmospheric_corrections_shorten_the_pseudorange():
    """
    Both delays make the signal arrive late, so the measured pseudorange is too
    long and the correction subtracts.  A sign error here shows up as a systematic
    height error of several metres, which is easy to mistake for bad geometry.
    """
    ephemerides, observables, _, _ = build_truth()
    plain, _ = nav.correct_pseudoranges(
        observables, ephemerides, signal_type_id="GPS_L5",
        settings=nav.CorrectionSettings(True, True, True, False, False),
        receiver_position_ecef_m=REFERENCE_ECEF,
    )
    with_atmos, terms = nav.correct_pseudoranges(
        observables, ephemerides, signal_type_id="GPS_L5",
        settings=nav.CorrectionSettings(True, True, True, True, True),
        receiver_position_ecef_m=REFERENCE_ECEF,
        iono_alpha=(1.02e-8, 2.24e-8, -4.17e-8, -1.79e-7),
        iono_beta=(8.60e4, 9.83e4, -6.55e4, -5.24e5),
    )
    assert np.all(with_atmos < plain)
    assert np.all(terms["troposphere"] < 0)
    assert np.all(terms["ionosphere"] < 0)
    # Elevation comes back for free and is what the notebook's skyplot uses.
    assert np.all(terms["elevation_deg"][np.isfinite(terms["elevation_deg"])] > 0)


def test_correction_terms_have_the_expected_magnitude_hierarchy():
    """
    Satellite clock: tens of kilometres.  Troposphere: metres.  Seeing that spread
    is most of what the notebook's correction section is for.
    """
    ephemerides, observables, _, _ = build_truth()
    _, terms = nav.correct_pseudoranges(
        observables, ephemerides, signal_type_id="GPS_L5",
        settings=nav.CorrectionSettings(True, True, True, True, True),
        receiver_position_ecef_m=REFERENCE_ECEF,
        iono_alpha=(1.02e-8, 2.24e-8, -4.17e-8, -1.79e-7),
        iono_beta=(8.60e4, 9.83e4, -6.55e4, -5.24e5),
    )
    clock = np.abs(terms["satellite_clock"])
    tropo = np.abs(terms["troposphere"])
    assert clock.max() > 1000.0
    assert 1.0 < tropo.max() < 50.0
    assert clock.max() > tropo.max()


# ---------------------------------------------------------------------------
# Group delay
# ---------------------------------------------------------------------------


def test_group_delay_uses_the_band_specific_inter_signal_correction():
    from utils.nav.ephemeris import CnavEphemeris

    eph = CnavEphemeris(
        sat_id="G01", week=2257, toe=TOE, top=TOE - 3600,
        delta_a=0.0, a_dot=0.0, delta_n0=0.0, delta_n0_dot=0.0,
        M0=0.0, e=0.004, omega=0.3, Omega0=0.0, delta_Omega_dot=0.0,
        i0=0.3, i_dot=0.0,
        Cis=0.0, Cic=0.0, Crs=0.0, Crc=0.0, Cus=0.0, Cuc=0.0,
        tgd=1e-8, isc_l5i5=3e-9, isc_l1ca=1e-9, isc_l2c=2e-9,
    )
    # IS-GPS-705J 20.3.3.3.1.2.1: (dt_sv)_L5I5 = dt_sv - T_GD + ISC_L5I5
    assert obs.signal_group_delay_s(eph, "GPS_L5") == pytest.approx(-1e-8 + 3e-9)
    assert obs.signal_group_delay_s(eph, "GPS_L1CA") == pytest.approx(-1e-8 + 1e-9)
    assert obs.signal_group_delay_s(eph, "GPS_L2C") == pytest.approx(-1e-8 + 2e-9)


def test_lnav_ephemeris_contributes_tgd_but_no_inter_signal_correction():
    """LNAV broadcasts T_GD alone -- a real limitation of the legacy message."""
    eph = make_ephemeris("G01", M0=0.0, Omega0=0.0)
    eph = LnavEphemeris(**{**eph.__dict__, "tgd": 5e-9})
    assert obs.signal_group_delay_s(eph, "GPS_L5") == pytest.approx(-5e-9)


# ---------------------------------------------------------------------------
# Clock-only solution, position held
# ---------------------------------------------------------------------------


def test_clock_only_recovers_the_bias_from_a_known_position():
    ephemerides, observables, _, bias_m = build_truth(receiver_clock_bias_m=800.0)
    series = nav.solve_clock_series(
        observables, ephemerides, REFERENCE_ECEF,
        signal_type_id="GPS_L5",
        settings=nav.CorrectionSettings(True, True, True, False, False),
    )
    assert np.allclose(series.clock_bias_m, bias_m, atol=0.1)
    assert np.all(series.position_ecef_m == REFERENCE_ECEF)


def test_clock_only_works_with_fewer_than_four_satellites():
    """
    The whole point: fixing the position removes three unknowns, so one satellite
    is enough. This is how a timing receiver at a surveyed site runs.
    """
    ephemerides, observables, _, bias_m = build_truth(receiver_clock_bias_m=800.0)
    for count in (1, 2, 3):
        trimmed = obs.Observables(
            epoch_uptime_ms=observables.epoch_uptime_ms,
            receive_time_s=observables.receive_time_s,
            sat_ids=observables.sat_ids[:count],
            transmit_time_s=observables.transmit_time_s[:, :count],
            pseudorange_m=observables.pseudorange_m[:, :count],
            week=observables.week,
        )
        series = nav.solve_clock_series(
            trimmed, ephemerides, REFERENCE_ECEF,
            signal_type_id="GPS_L5",
            settings=nav.CorrectionSettings(True, True, True, False, False),
        )
        assert np.allclose(series.clock_bias_m, bias_m, atol=0.1), f"{count} satellite(s)"
        assert np.all(series.num_satellites == count)


def test_clock_only_residuals_are_not_forced_to_zero():
    """
    Four satellites against four unknowns give zero residuals by construction.  Hold
    the position and the same four give three degrees of freedom -- so a real
    measurement error shows up instead of being absorbed.
    """
    ephemerides, observables, _, _ = build_truth()
    four = obs.Observables(
        epoch_uptime_ms=observables.epoch_uptime_ms,
        receive_time_s=observables.receive_time_s,
        sat_ids=observables.sat_ids[:4],
        transmit_time_s=observables.transmit_time_s[:, :4],
        pseudorange_m=observables.pseudorange_m[:, :4].copy(),
        week=observables.week,
    )
    # Put 10 m of error on one satellite.
    four.pseudorange_m[:, 1] += 10.0
    settings = nav.CorrectionSettings(True, True, True, False, False)

    corrected, _ = nav.correct_pseudoranges(
        four, ephemerides, signal_type_id="GPS_L5", settings=settings
    )
    positions = nav.satellite_positions(four, ephemerides)
    fix = nav.solve_epoch(corrected[0], positions[0], four.sat_ids)
    assert np.abs(fix.residuals_m).max() < 1e-6, "an exactly determined fix hides it"

    held = nav.solve_clock_series(
        four, ephemerides, REFERENCE_ECEF, signal_type_id="GPS_L5", settings=settings
    )
    assert np.abs(held.residuals_m[0, 1]) > 5.0, "holding the position must reveal it"


def test_clock_only_recovers_drift():
    ephemerides, observables, _, _ = build_truth(
        receiver_clock_bias_m=400.0, clock_drift_ppm=1.5, num_epochs=20
    )
    series = nav.solve_clock_series(
        observables, ephemerides, REFERENCE_ECEF,
        signal_type_id="GPS_L5",
        settings=nav.CorrectionSettings(True, True, True, False, False),
    )
    drift_ppm, rms = series.clock_drift_ppm()
    assert drift_ppm == pytest.approx(1.5, abs=0.01)
    assert rms < 0.1


def test_clock_only_marks_epochs_with_no_satellites():
    ephemerides, observables, _, _ = build_truth()
    observables.pseudorange_m[2, :] = np.nan
    series = nav.solve_clock_series(
        observables, ephemerides, REFERENCE_ECEF,
        signal_type_id="GPS_L5",
        settings=nav.CorrectionSettings(True, True, True, False, False),
    )
    assert not series.valid[2]
    assert series.num_satellites[2] == 0



def test_sagnac_transit_does_not_import_the_receiver_clock_bias():
    """
    The bias the fix is solving for must not reach the Sagnac rotation.

    It used to.  The transit came from `receive_time_s - t_gps`, and that clock is
    offset by exactly the bias being estimated -- a few microseconds in this
    synthetic world, but *milliseconds* on real data, where the receiver clock is a
    sample counter anchored to a nominal 75 ms transit.  At ~1.9 m of satellite
    displacement per millisecond it moved the fix by metres.

    What makes it invisible without a test like this one is that the error is
    common to every satellite, so it is a rigid rotation of the whole constellation:
    ranges are preserved, residuals are untouched, and every diagnostic the notebook
    prints looks perfectly healthy while the fix sits metres away.

    So the check is a property, not a tolerance: shift the receiver clock by a large
    offset and the satellite positions must not move at all.
    """
    ephemerides, observables, _, _ = build_truth()
    shifted = dataclasses.replace(
        observables, receive_time_s=observables.receive_time_s + 5.0e-3  # 5 ms
    )

    geometric = [
        nav.satellite_positions(o, ephemerides, apply_sagnac=True,
                                receiver_position_ecef_m=REFERENCE_ECEF)
        for o in (observables, shifted)
    ]
    assert np.allclose(geometric[0], geometric[1], atol=1e-9), (
        "a receiver-clock shift changed the Sagnac-rotated satellite positions"
    )

    # And the fallback, with no a-priori, demonstrably does import it -- so the
    # test above is testing the fix rather than an insensitivity that was always
    # there.
    from_clock = [
        nav.satellite_positions(o, ephemerides, apply_sagnac=True)
        for o in (observables, shifted)
    ]
    moved = np.linalg.norm(from_clock[0] - from_clock[1], axis=2)
    assert moved.min() > 5.0, f"expected metres of movement, saw {moved.min():.3f} m"
