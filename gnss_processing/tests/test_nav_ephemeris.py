"""
The two orbit models, checked against something other than themselves.

`LnavEphemeris` duplicates an algorithm `gnss_tools.misc.gps_ephemeris` already
implements, so the LNAV tests here are differential: same parameters into both,
positions must agree to well under a millimetre.  That is what keeps the local
copy honest.

`CnavEphemeris` has no reference implementation anywhere in this project, so it is
checked structurally instead -- against the orbit it is supposed to describe.  A
CNAV parameter set built to describe the *same* orbit as an LNAV one must predict
the same position, which exercises the delta_a / a_dot / delta_n0_dot arithmetic
against an independent formulation of the same physics.
"""

import numpy as np
import pytest
from gnss_tools.misc import gps_ephemeris as gt_eph
from gnss_tools.time.gtime import GTime

from utils.nav import ephemeris as eph


# A realistic GPS ephemeris: near-circular, 55 degree inclination, 12 hour period.
REFERENCE = dict(
    sat_id="G01",
    week=2257,
    toe=302400.0,
    sqrt_a=5153.65,
    e=0.0089,
    i0=0.9617 / np.pi,  # ~55 degrees, stored as semi-circles
    i_dot=1.1e-10 / np.pi,
    Omega0=1.0472 / np.pi,
    Omega_dot=-8.1e-9 / np.pi,
    omega=0.6283 / np.pi,
    M0=0.3491 / np.pi,
    deln=4.9e-9 / np.pi,
    Cuc=-3.5e-6,
    Cus=8.1e-6,
    Crc=215.6,
    Crs=-64.2,
    Cic=1.2e-7,
    Cis=-8.9e-8,
    toc=302400.0,
    af0=-1.23e-4,
    af1=-9.1e-12,
    af2=0.0,
    tgd=-5.6e-9,
)


def make_lnav(**overrides):
    return eph.LnavEphemeris(**{**REFERENCE, **overrides})


# ---------------------------------------------------------------------------
# LNAV against gnss_tools
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("dt", [-7200.0, -1800.0, 0.0, 1800.0, 7200.0])
def test_lnav_position_matches_gnss_tools(dt):
    e = make_lnav()
    t = e.toe + dt

    ours = e.orbit_state(t).position_ecef_m

    params = gt_eph.compute_gps_orbital_params_from_ephemeris(
        GTime(whole_seconds=int(t), frac_seconds=t - int(t)),
        e.semi_major_axis_m,
        e.e,
        e.i0 * np.pi,
        e.i_dot * np.pi,
        e.omega * np.pi,
        e.M0 * np.pi,
        e.toe,
        e.Omega0 * np.pi,
        e.Omega_dot * np.pi,
        e.deln * np.pi,
        e.Cus,
        e.Cuc,
        e.Crs,
        e.Crc,
        e.Cis,
        e.Cic,
    )
    theirs = np.array(
        gt_eph.compute_ecf_position_from_orbital_parameters(
            params.i, params.r, params.Omega, params.Phi
        )
    )

    assert np.allclose(ours, theirs, atol=1e-4), (
        f"disagreement of {np.linalg.norm(ours - theirs):.6f} m at dt={dt}"
    )


def test_lnav_clock_correction_matches_gnss_tools():
    e = make_lnav()
    t = e.toe + 1234.0
    state = e.orbit_state(t)
    params = gt_eph.compute_gps_orbital_params_from_ephemeris(
        GTime(whole_seconds=int(t), frac_seconds=t - int(t)),
        e.semi_major_axis_m, e.e, e.i0 * np.pi, e.i_dot * np.pi, e.omega * np.pi,
        e.M0 * np.pi, e.toe, e.Omega0 * np.pi, e.Omega_dot * np.pi, e.deln * np.pi,
        e.Cus, e.Cuc, e.Crs, e.Crc, e.Cis, e.Cic,
    )
    theirs = gt_eph.compute_gps_transmitter_clock_bias_with_relativity_correction(
        GTime(whole_seconds=int(t), frac_seconds=t - int(t)),
        int(e.toc), e.af0, e.af1, e.af2, e.semi_major_axis_m, e.e, params.E,
    )
    ours = e.clock_correction_s(t, state)
    assert ours == pytest.approx(theirs, abs=1e-15)


def test_lnav_orbit_radius_is_physical():
    state = make_lnav().orbit_state(REFERENCE["toe"])
    radius = np.linalg.norm(state.position_ecef_m)
    assert 2.4e7 < radius < 2.8e7, "a GPS satellite sits about 26,560 km from the centre"


def test_lnav_completes_half_an_orbit_in_six_hours():
    e = make_lnav()
    start = e.orbit_state(e.toe).position_ecef_m
    half = e.orbit_state(e.toe + 6 * 3600.0).position_ecef_m
    # Half a sidereal orbit puts the satellite roughly opposite in inertial space;
    # in ECEF the earth has also turned, so just assert it went a long way.
    assert np.linalg.norm(half - start) > 4.0e7


# ---------------------------------------------------------------------------
# Week crossover
# ---------------------------------------------------------------------------


def test_time_from_epoch_folds_across_the_week_boundary():
    assert eph._wrap_time_from_epoch(302401.0) == pytest.approx(302401.0 - 604800.0)
    assert eph._wrap_time_from_epoch(-302401.0) == pytest.approx(-302401.0 + 604800.0)
    assert eph._wrap_time_from_epoch(100.0) == 100.0


def test_position_is_continuous_across_the_week_boundary():
    """
    An ephemeris near the end of a week, evaluated either side of the rollover,
    must not jump.  Without the fold this test moves the satellite half an orbit.
    """
    e = make_lnav(toe=604000.0, toc=604000.0)
    before = e.orbit_state(604700.0).position_ecef_m
    after = e.orbit_state(100.0).position_ecef_m  # 604900 s, wrapped into next week
    # 200 s of orbital motion is a few hundred km, not tens of thousands.
    assert np.linalg.norm(after - before) < 1.0e6


# ---------------------------------------------------------------------------
# CNAV
# ---------------------------------------------------------------------------


def cnav_matching(lnav: eph.LnavEphemeris, **overrides) -> eph.CnavEphemeris:
    """
    A CNAV parameter set describing the same orbit as `lnav`, at toe.

    The rates that CNAV adds are set to zero, which is exactly the case where the
    two models must agree: same semi-major axis, same mean motion, same everything.
    Any disagreement is arithmetic in the CNAV-specific code, not physics.
    """
    fields = dict(
        sat_id=lnav.sat_id,
        week=lnav.week,
        toe=lnav.toe,
        top=lnav.toe - 3600.0,
        delta_a=lnav.semi_major_axis_m - eph.CNAV_A_REF,
        a_dot=0.0,
        delta_n0=lnav.deln,
        delta_n0_dot=0.0,
        M0=lnav.M0,
        e=lnav.e,
        omega=lnav.omega,
        Omega0=lnav.Omega0,
        delta_Omega_dot=lnav.Omega_dot - eph.CNAV_OMEGA_DOT_REF,
        i0=lnav.i0,
        i_dot=lnav.i_dot,
        Cis=lnav.Cis,
        Cic=lnav.Cic,
        Crs=lnav.Crs,
        Crc=lnav.Crc,
        Cus=lnav.Cus,
        Cuc=lnav.Cuc,
        toc=lnav.toc,
        af0=lnav.af0,
        af1=lnav.af1,
        af2=lnav.af2,
        tgd=lnav.tgd,
    )
    return eph.CnavEphemeris(**{**fields, **overrides})


@pytest.mark.parametrize("dt", [-3600.0, 0.0, 3600.0])
def test_cnav_with_zero_rates_matches_lnav(dt):
    lnav = make_lnav()
    cnav = cnav_matching(lnav)
    ours = cnav.orbit_state(lnav.toe + dt).position_ecef_m
    theirs = lnav.orbit_state(lnav.toe + dt).position_ecef_m
    assert np.allclose(ours, theirs, atol=1e-6)


def test_cnav_semi_major_axis_is_referenced_to_a_ref():
    lnav = make_lnav()
    cnav = cnav_matching(lnav)
    assert cnav.semi_major_axis_at_toe_m == pytest.approx(lnav.semi_major_axis_m)
    assert abs(cnav.delta_a) < 5000.0, "delta_A should be a small correction to A_REF"


def test_cnav_node_rate_is_referenced_to_omega_dot_ref():
    lnav = make_lnav()
    cnav = cnav_matching(lnav)
    assert cnav.Omega_dot == pytest.approx(lnav.Omega_dot)


def test_cnav_semi_major_axis_rate_moves_the_satellite():
    """`a_dot` must actually be applied -- a dropped term is invisible at toe."""
    lnav = make_lnav()
    still = cnav_matching(lnav)
    drifting = cnav_matching(lnav, a_dot=0.05)  # 5 cm/s, a realistic magnitude
    dt = 3600.0
    separation = np.linalg.norm(
        drifting.orbit_state(lnav.toe + dt).position_ecef_m
        - still.orbit_state(lnav.toe + dt).position_ecef_m
    )
    # 0.05 m/s over an hour is 180 m of semi-major axis.
    assert 100.0 < separation < 400.0


def test_cnav_mean_motion_rate_uses_the_half_factor():
    """
    delta_n_A = delta_n0 + (1/2) delta_n0_dot tk.  Dropping the half is the classic
    transcription slip; it shows up as exactly a factor of two in the along-track
    displacement, so compare against an independent evaluation of the intended
    formula rather than against a magnitude guess.
    """
    lnav = make_lnav()
    dn_dot = 1e-14  # semi-circles/s^2
    cnav = cnav_matching(lnav, delta_n0_dot=dn_dot)
    tk = 3600.0

    A = cnav.semi_major_axis_at_toe_m
    n0 = np.sqrt(eph.MU_EARTH / A**3)
    expected_n = n0 + (cnav.delta_n0 + 0.5 * dn_dot * tk) * np.pi
    expected_Mk = cnav.M0 * np.pi + expected_n * tk

    # Recover the mean anomaly the implementation actually used, via Kepler.
    state = cnav.orbit_state(cnav.toe + tk)
    Ek = state.eccentric_anomaly_rad
    actual_Mk = Ek - cnav.e * np.sin(Ek)
    assert actual_Mk == pytest.approx(expected_Mk, abs=1e-12)


def test_cnav_clock_correction_includes_relativity():
    lnav = make_lnav()
    cnav = cnav_matching(lnav)
    t = lnav.toe + 500.0
    assert cnav.clock_correction_s(t) == pytest.approx(lnav.clock_correction_s(t), abs=1e-15)


def test_relativistic_term_is_not_negligible():
    """It is tens of nanoseconds -- metres of range.  A dropped term would be a
    systematic bias, so check it is actually being applied."""
    e = make_lnav()
    t = e.toe + 900.0
    state = e.orbit_state(t)
    with_relativity = e.clock_correction_s(t, state)
    polynomial_only = e.af0 + e.af1 * (t - e.toc)
    assert abs(with_relativity - polynomial_only) > 1e-9


def test_the_clock_splits_into_a_polynomial_and_a_relativistic_term():
    """
    The split exists because the two halves have different audiences.

    A receiver forming a pseudorange wants both.  A comparison against an IGS
    precise clock wants only the polynomial, because SP3 excludes the periodic
    relativistic correction exactly as the broadcast parameters do -- and including
    it there buries the clock error under a sinusoid an order of magnitude larger.
    """
    ephemeris = eph.LnavEphemeris(
        sat_id="G01", week=2258, toe=475200.0, sqrt_a=5153.6, e=0.0127,
        i0=0.3, i_dot=0.0, Omega0=0.1, Omega_dot=-2.5e-9, omega=0.2, M0=0.4,
        deln=1.5e-9, Cuc=0.0, Cus=0.0, Crc=0.0, Crs=0.0, Cic=0.0, Cis=0.0,
        toc=475200.0, af0=1.0e-4, af1=2.0e-12, af2=0.0,
    )
    t = 475200.0 + 1800.0
    state = ephemeris.orbit_state(t)

    # The polynomial is exactly what its three coefficients say.
    assert ephemeris.clock_polynomial_s(t) == pytest.approx(1.0e-4 + 2.0e-12 * 1800.0)
    # And the two halves account for the whole of the correction.
    assert ephemeris.clock_correction_s(t, state=state) == pytest.approx(
        ephemeris.clock_polynomial_s(t) + ephemeris.relativistic_correction_s(state)
    )


def test_the_relativistic_term_is_periodic_and_metres_in_size():
    """Its amplitude is F*e*sqrt(A), which for a typical eccentricity is tens of
    nanoseconds -- metres of range, and the reason it cannot be left in a
    comparison against a product that omits it."""
    ephemeris = eph.LnavEphemeris(
        sat_id="G01", week=2258, toe=0.0, sqrt_a=5153.6, e=0.0127,
        i0=0.3, i_dot=0.0, Omega0=0.1, Omega_dot=-2.5e-9, omega=0.2, M0=0.0,
        deln=0.0, Cuc=0.0, Cus=0.0, Crc=0.0, Crs=0.0, Cic=0.0, Cis=0.0,
        toc=0.0, af0=0.0, af1=0.0, af2=0.0,
    )
    over_an_orbit = np.array([
        ephemeris.relativistic_correction_s(ephemeris.orbit_state(t))
        for t in np.linspace(0.0, 12 * 3600.0, 200)
    ])
    peak_to_peak_m = np.ptp(over_an_orbit) * eph.SPEED_OF_LIGHT
    # 2 * F * e * sqrt(A) * c, to within the sampling of the orbit above.
    expected = abs(2 * eph.RELATIVISTIC_F * 0.0127 * 5153.6) * eph.SPEED_OF_LIGHT
    assert peak_to_peak_m == pytest.approx(expected, rel=0.01)
    assert 15.0 < peak_to_peak_m < 20.0
