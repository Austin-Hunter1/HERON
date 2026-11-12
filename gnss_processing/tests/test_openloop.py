"""
Notebook 03 open-loop modules (plan section 12.3, tests T-1 to T-9).

No real reflection recording exists yet, so every test uses the synthetic pair of
`utils.openloop.synthetic`: one satellite on a straight line, one drone above a
flat water surface. The truth excess path there comes from a direct numerical
minimisation (Fermat), not from `utils.openloop.specular`.
"""

from __future__ import annotations

import numpy as np
import pytest

from utils.openloop import (
    acquisition,
    antennas,
    atmosphere,
    attitude,
    config,
    correlator,
    direct_ref,
    frames,
    heights,
    leakage,
    model,
    phase,
    reflectivity,
    sat_states,
    specular,
    surface,
    synthetic,
    timebase,
    trajectory,
)
from utils.signal_interfaces import GpsL1CA, GpsL5

FS_L1 = 4e6


def _setup(signal, world, *, fs, duration, solver="image_plane", **kwargs):
    """Generate a pair and build the Mode A model from the geometry (not the truth)."""
    zen, nad, truth = synthetic.generate_pair(signal, world, samp_rate=fs,
                                              duration_s=duration, **kwargs)
    ref = direct_ref.from_synthetic_truth(truth, signal)
    prov = sat_states.TrackProvider(world.sat_ecef)

    def rx(t):
        return world.nadir_ecef(t), None, world.zenith_ecef(t), None

    surf = surface.Surface(surface.NoGeoid(), world.surface_h_ell_m)
    geo = model.compute_geometry(sat_id="G05", provider=prov, rx_tracks=rx, surface=surf,
                                 timebase=timebase.TimeBase(0.0, 0.0),
                                 uptime_ms=np.arange(0.0, duration * 1e3 + 1, 20.0),
                                 solver=solver)
    m = model.ReflectionModel(ref, geo, signal.carrier_freq_hz)
    return zen, nad, truth, ref, m


# ---------------------------------------------------------------- T-2, T-3

@pytest.mark.parametrize("height,elev", [(10.0, 60.0), (100.0, 5.0)])
def test_specular_solvers_match_fermat_truth(height, elev):
    world = synthetic.SyntheticWorld(drone_height_m=height, sat_elevation_deg=elev)
    t = np.array([0.0, 1.5])
    truth, points = synthetic.truth_reflected_path_m(world, t)
    surf = surface.Surface(surface.NoGeoid(), world.surface_h_ell_m)
    img = specular.image_plane(world.sat_ecef(t), world.nadir_ecef(t), surf)
    cur = specular.iterative_surface(world.sat_ecef(t), world.nadir_ecef(t), surf,
                                     tolerance_rad=1e-6)
    np.testing.assert_allclose(img.reflected_path_m, truth, atol=1e-3)
    np.testing.assert_allclose(cur.reflected_path_m, truth, atol=1e-3)
    assert np.all(cur.snell_residual_rad < 1e-5)
    # Fermat's minimum is flat, so the point is less precise than the path.
    assert np.all(np.linalg.norm(cur.specular_ecef - points, axis=1) < 0.5)


def test_single_pass_plane_shows_curvature_error():
    """A plane through the sub-receiver point misses by about d^2 / (2 R_E) (plan 6.2)."""
    world = synthetic.SyntheticWorld(drone_height_m=100.0, sat_elevation_deg=5.0)
    t = np.array([0.0])
    truth, _ = synthetic.truth_reflected_path_m(world, t)
    rx = world.nadir_ecef(t)
    sat = world.sat_ecef(t)
    lat, lon, _ = frames.ecef_to_geodetic(rx)
    n = frames.ellipsoid_normal(lat, lon)
    p0 = frames.geodetic_to_ecef(lat, lon, world.surface_h_ell_m)
    h = np.sum((rx - p0) * n, axis=1)
    image = rx - 2 * h[:, None] * n
    one_pass = np.linalg.norm(sat - image, axis=1)
    assert 0.01 < abs(one_pass[0] - truth[0]) < 0.5


# ---------------------------------------------------------------- T-4

def test_lever_arm_transform_level_and_yawed():
    traj = trajectory.static_trajectory(40.0, -105.0, 1600.0)
    rover = antennas.AntennaGeometry("rover", np.array([0.0, 0.0, 0.0]), np.array([0, 0, -1]))
    nadir = antennas.AntennaGeometry("nadir", np.array([1.0, 0.0, 0.5]), np.array([0, 0, 1]),
                                     {"L1": 0.02})
    level = attitude.constant_attitude(0, 0, 0)
    pos, vel = antennas.phase_centre_track(traj, level, rover, nadir, "L1", np.array([0.0]))
    lat, lon, h = frames.ecef_to_geodetic(pos)
    ned = frames.rot_ecef_to_ned(np.deg2rad(40.0), np.deg2rad(-105.0)) @ (
        pos[0] - traj.position_ecef_m[0])
    np.testing.assert_allclose(ned, [1.0, 0.0, 0.52], atol=1e-9)
    yawed = attitude.constant_attitude(0, 0, 90)
    pos, _ = antennas.phase_centre_track(traj, yawed, rover, nadir, "L1", np.array([0.0]))
    ned = frames.rot_ecef_to_ned(np.deg2rad(40.0), np.deg2rad(-105.0)) @ (
        pos[0] - traj.position_ecef_m[0])
    np.testing.assert_allclose(ned, [0.0, 1.0, 0.52], atol=1e-9)


def test_missing_lever_arm_stops_with_message():
    traj = trajectory.static_trajectory(40.0, -105.0, 1600.0)
    rover = antennas.AntennaGeometry("rover", None, np.array([0, 0, -1]))
    nadir = antennas.AntennaGeometry("nadir", None, np.array([0, 0, 1]))
    with pytest.raises(ValueError, match="D-091"):
        antennas.phase_centre_track(traj, attitude.constant_attitude(), rover, nadir, "L1",
                                    np.array([0.0]))


# ---------------------------------------------------------------- trajectory

def test_rtklib_pos_reader_xyz_and_llh(tmp_path):
    xyz = tmp_path / "a.pos"
    xyz.write_text(
        "% program   : RTKLIB\n"
        "%  GPST                      x-ecef(m)      y-ecef(m)      z-ecef(m)   Q  ns   "
        "sdx(m)   sdy(m)   sdz(m)  sdxy(m)  sdyz(m)  sdzx(m) age(s)  ratio\n"
        "2026/11/10 18:00:00.000  -1288398.000 -4721697.000  4078625.000   1  10   0.0100"
        "   0.0100   0.0100   0.0000   0.0000   0.0000   1.00    5.0\n"
        "2026/11/10 18:00:00.200  -1288398.100 -4721697.000  4078625.000   2  10   0.0100"
        "   0.0100   0.0100   0.0000   0.0000   0.0000   1.00    5.0\n"
    )
    tr = trajectory.read_rtklib_pos(xyz)
    assert tr.quality.tolist() == ["fix", "float"]
    assert tr.time_gpst_s[1] - tr.time_gpst_s[0] == pytest.approx(0.2)
    pos, vel = tr.interpolate(tr.time_gpst_s[0] + 0.1)
    assert pos[0, 0] == pytest.approx(-1288398.05, abs=1e-6)
    llh = tmp_path / "b.pos"
    llh.write_text(
        "%  GPST                  latitude(deg) longitude(deg)  height(m)   Q  ns   sdn(m)"
        "   sde(m)   sdu(m)  sdne(m)  sdeu(m)  sdun(m) age(s)  ratio\n"
        "2342 64800.000   40.000000000 -105.000000000  1600.0000   1  10   0.01   0.01   0.02"
        "   0.00   0.00   0.00   0.0  0.0\n"
    )
    tr = trajectory.read_rtklib_pos(llh)
    assert tr.time_gpst_s[0] == pytest.approx(2342 * 604800 + 64800)


# ---------------------------------------------------------------- T-1, T-8

def test_mode_a_model_matches_truth_and_open_loop_recovers_phase():
    """T-1 and T-8: signs are right, the residual phase is the reflection phase only."""
    sig = GpsL1CA(5)
    world = synthetic.SyntheticWorld()
    _, nad, truth, ref, m = _setup(sig, world, fs=FS_L1, duration=0.4)
    excess_truth = np.interp(m.geometry.uptime_ms, truth.t_s * 1e3, truth.excess_path_m)
    np.testing.assert_allclose(m.geometry.excess_total_m, excess_truth, atol=2e-3)
    ol = correlator.run_open_loop(nad, samp_rate=FS_L1, start_uptime_ms=0.0, signal=sig,
                                  model=m, lags_chips=correlator.lag_grid(-1, 1, 0.1))
    z = phase.wipe(ol, sig, ref, 0)[:, 0, :]
    j0 = int(np.argmin(np.abs(ol.lags_chips)))
    assert int(np.argmax(np.mean(np.abs(z), axis=0))) == j0
    resid_cycles = np.angle(np.mean(z[:, j0])) / (2 * np.pi)
    assert resid_cycles == pytest.approx(truth.reflection_phase_offset_cycles, abs=5e-3)
    assert np.std(np.angle(z[:, j0])) < 0.02


def test_receiver_clock_cancels_in_mode_a():
    """A large clock drift must not move the residual phase (plan 8.1)."""
    sig = GpsL1CA(7)
    results = []
    for drift in (0.0, 0.45):
        world = synthetic.SyntheticWorld(clock_drift_ppm=drift)
        _, nad, truth, ref, m = _setup(sig, world, fs=FS_L1, duration=0.3)
        ol = correlator.run_open_loop(nad, samp_rate=FS_L1, start_uptime_ms=0.0, signal=sig,
                                      model=m, lags_chips=np.array([0.0]))
        z = phase.wipe(ol, sig, ref, 0)[:, 0, 0]
        results.append(np.angle(np.mean(z)))
    assert results[0] == pytest.approx(results[1], abs=0.02)


# ---------------------------------------------------------------- T-1 for L5 and overlay

def test_l5_pilot_overlay_offset_search():
    sig = GpsL5(9)
    world = synthetic.SyntheticWorld()
    fs = 21e6
    _, nad, truth, ref, m = _setup(sig, world, fs=fs, duration=0.06)
    ol = correlator.run_open_loop(nad, samp_rate=fs, start_uptime_ms=0.0, signal=sig,
                                  model=m, lags_chips=np.array([0.0]))
    off = phase.search_overlay_offset(ol, sig, ref, component=1, lag_index=0)
    assert off == 0
    z = phase.wipe(ol, sig, ref, 1, off)[:, 0, 0]
    assert np.abs(np.mean(z)) > 0.9 * np.mean(np.abs(z))


# ---------------------------------------------------------------- Phase 4

def test_ddm_detects_weak_reflection_and_hardware_delay():
    sig = GpsL1CA(11)
    world = synthetic.SyntheticWorld()
    tau_hw = 0.25 / 1.023e6  # a quarter chip
    _, nad, truth, ref, m = _setup(sig, world, fs=FS_L1, duration=0.6,
                                   reflection_amplitude=0.05, nadir_noise_sigma=1.0,
                                   hardware_delay_s=tau_hw, seed=3)
    lags = correlator.lag_grid(-6, 2, 0.05)
    ol = correlator.run_open_loop(nad, samp_rate=FS_L1, start_uptime_ms=0.0, signal=sig,
                                  model=m, lags_chips=lags)
    wiped = phase.wipe(ol, sig, ref, 0)
    ddm = acquisition.delay_doppler_map(ol, wiped=wiped, noise_chips=(-6.0, -3.0),
                                        coherent_blocks=20, search_chips=(-1.5, 1.5))
    assert ddm.detected
    est, _ = acquisition.estimate_tau_hw_s([ddm.peak_lag_chips], 1.023e6)
    assert est == pytest.approx(tau_hw, abs=0.06 / 1.023e6)


def test_ddm_no_false_detection_on_noise():
    sig = GpsL1CA(12)
    world = synthetic.SyntheticWorld()
    _, nad, truth, ref, m = _setup(sig, world, fs=FS_L1, duration=0.4,
                                   reflection_amplitude=0.0, nadir_noise_sigma=1.0, seed=5)
    lags = correlator.lag_grid(-6, 2, 0.1)
    ol = correlator.run_open_loop(nad, samp_rate=FS_L1, start_uptime_ms=0.0, signal=sig,
                                  model=m, lags_chips=lags)
    ddm = acquisition.delay_doppler_map(ol, wiped=phase.wipe(ol, sig, ref, 0),
                                        noise_chips=(-6.0, -3.0), coherent_blocks=20,
                                        search_chips=(-1.5, 1.5))
    assert not ddm.detected


# ---------------------------------------------------------------- Phase 5, T-6

def _phase_series(n, lam, excess, wrapped, cn0=45.0, t_coh_ms=20.0):
    u = np.arange(n) * t_coh_ms
    return phase.PhaseSeries(u, np.exp(1j * wrapped), wrapped, np.full(n, cn0), excess, lam,
                             t_coh_ms)


def test_scanf_dual_frequency_repairs_injected_slips():
    """T-6: a height signal plus noise, fades and injected slips on each band."""
    rng = np.random.default_rng(0)
    n = 600
    t = np.arange(n) * 0.02
    lam1, lam5 = frames.SPEED_OF_LIGHT / 1575.42e6, frames.SPEED_OF_LIGHT / 1176.45e6
    true_resid = 0.03 * np.sin(2 * np.pi * t / 6.0)  # D - D~ (m)
    model_excess = np.full(n, 34.0)
    w = []
    for lam, slip_at, c0 in ((lam1, 200, 0.3), (lam5, 400, -1.1)):
        phi = -2 * np.pi * true_resid / lam + c0 + rng.normal(0, 0.05, n)
        phi[slip_at:] += 2 * np.pi  # an injected cycle slip
        w.append(np.angle(np.exp(1j * phi)))
    s1 = _phase_series(n, lam1, model_excess, w[0])
    s5 = _phase_series(n, lam5, model_excess, w[1])
    res = phase.scanf([s1, s5])
    r = res.residual_m - res.residual_m[0] + true_resid[0]
    assert np.max(np.abs(r - true_resid)) < 0.01
    # A step of exactly 2 pi does not change a wrapped phase, so the "slips"
    # above are invisible in w and must not give slip events (C-17).
    assert not res.slip_events.any()
    single = phase.scanf([s1])
    assert np.max(np.abs(single.residual_m - single.residual_m[0] + true_resid[0]
                         - true_resid)) < 0.02


def test_scanf_rate_state_repairs_unwrap_slips():
    """
    C-17: a residual that accelerates until it moves more than half a cycle per
    epoch. Plain unwrapping slips; the filter's rate state predicts the right
    integer, and each such epoch is a slip event.
    """
    rng = np.random.default_rng(0)
    n = 600
    t = np.arange(n) * 0.02
    lam1, lam5 = frames.SPEED_OF_LIGHT / 1575.42e6, frames.SPEED_OF_LIGHT / 1176.45e6
    true_resid = 0.4 * t**2  # 0.8 m/s^2 from rest
    series = []
    for lam, c0 in ((lam1, 0.3), (lam5, -1.1)):
        phi = -2 * np.pi * true_resid / lam + c0 + rng.normal(0, 0.05, n)
        series.append(_phase_series(n, lam, np.full(n, 34.0), np.angle(np.exp(1j * phi))))
    naive = -lam1 * np.unwrap(series[0].wrapped_rad) / (2 * np.pi)
    assert np.max(np.abs(naive - naive[0] - true_resid)) > 1.0  # plain unwrap fails
    res = phase.scanf(series)
    assert np.max(np.abs(res.residual_m - res.residual_m[0] - true_resid)) < 0.015
    # L1 passes half a cycle per 20 ms epoch at lam1 / 2 / 0.02 s = 4.8 m/s (t = 6 s).
    first_l1 = np.argmax(res.slip_events[:, 0])
    assert 270 <= first_l1 <= 310
    assert not res.slip_events[:250].any()


def test_scanf_wraps_near_pi_are_not_slip_events():
    """C-17: a steady phase near +-pi wraps often; a wrap is not a slip."""
    rng = np.random.default_rng(1)
    n = 500
    lam1, lam5 = frames.SPEED_OF_LIGHT / 1575.42e6, frames.SPEED_OF_LIGHT / 1176.45e6
    model_excess = np.full(n, 34.0)
    w1 = np.angle(np.exp(1j * (0.3 + rng.normal(0, 0.15, n))))
    w5 = np.angle(np.exp(1j * (3.1 + rng.normal(0, 0.15, n))))
    res = phase.scanf([_phase_series(n, lam1, model_excess, w1),
                       _phase_series(n, lam5, model_excess, w5)])
    assert len(np.unique(res.slips[:, 1])) > 1  # the L5 phase did wrap
    assert not res.slip_events.any()
    assert np.ptp(res.residual_m) < 0.02


def test_coherence_metric_separates_coherent_and_noise():
    rng = np.random.default_rng(1)
    coherent = np.exp(1j * 0.01 * np.arange(200))
    noise = rng.normal(size=200) + 1j * rng.normal(size=200)
    assert np.median(phase.coherence_metric(coherent, 25)) > 0.95
    assert np.median(phase.coherence_metric(noise, 25)) < 0.2


def test_phase_variance_formula():
    v = phase.phase_variance_m2(45.0, 0.02, 0.19)
    x = 1 / (2 * 0.02 * 10**4.5)
    assert v == pytest.approx((0.19 / (2 * np.pi)) ** 2 * x * (1 + x))


# ---------------------------------------------------------------- Phase 7, T-7

def test_leakage_beta_recovered_when_interferometric_phase_turns():
    sig = GpsL1CA(14)
    world = synthetic.SyntheticWorld(vertical_amplitude_m=2.0, vertical_period_s=0.8)
    beta_true = 0.2 * np.exp(2j * np.pi * 0.3)
    zen, nad, truth, ref, m = _setup(sig, world, fs=FS_L1, duration=0.8,
                                     leakage_amplitude=abs(beta_true),
                                     leakage_phase_cycles=0.3)
    lags = np.array([0.0])
    ol_n = correlator.run_open_loop(nad, samp_rate=FS_L1, start_uptime_ms=0.0, signal=sig,
                                    model=m, lags_chips=lags)
    ol_z = correlator.run_open_loop(zen, samp_rate=FS_L1, start_uptime_ms=0.0, signal=sig,
                                    model=m, lags_chips=lags)
    zn = phase.wipe(ol_n, sig, ref, 0)[:, 0, 0]
    zz = phase.wipe(ol_z, sig, ref, 0)[:, 0, 0]
    est = leakage.estimate_beta(zn, zz, ol_n.uptime_ms, ol_n.model_excess_m,
                                frames.SPEED_OF_LIGHT / sig.carrier_freq_hz, window=780)
    assert np.all(est.separable)
    # The reflection leaks into the estimate by about (A_r / A_z) * diversity (concern C-9).
    # Amplitudes per sample: reflection 0.3; zenith 1 times the code correlation at
    # the reflected replica's offset (about 0.12 chip here, so about 0.88).
    a_z = np.mean(np.abs(zz)) / (FS_L1 * 1e-3)
    bound = 0.3 / a_z * est.diversity[0] + 0.01
    assert np.all(np.abs(est.beta - beta_true) < bound)


# ---------------------------------------------------------------- heights

def test_height_fit_two_satellites():
    rng = np.random.default_rng(2)
    t = np.linspace(0, 60, 300)
    arcs = []
    for e0, b in ((0.3, 0.05), (0.9, -0.12)):
        e = e0 + 0.002 * t
        arcs.append({"t_s": t, "elevation_rad": e,
                     "residual_m": 2 * 0.07 * np.sin(e) + b + rng.normal(0, 0.003, len(t))})
    fit = heights.fit_height(arcs)
    assert fit.dh_m == pytest.approx(0.07, abs=0.01)


# ---------------------------------------------------------------- atmosphere, reflectivity

def test_excess_tropo_first_order_size():
    n0 = atmosphere.refractivity(*atmosphere.standard_atmosphere(1500.0))
    d = atmosphere.excess_tropo_first_order_m(n0, 50.0, np.deg2rad(10.0))
    assert 0.1 < d < 0.4  # plan 7.2: about 0.3 m at 50 m, 10 deg
    assert atmosphere.NoIono().excess_delay_m(np.array([0.2]))[0] == 0.0


def test_nequick_without_backend_stops_with_message():
    with pytest.raises(NotImplementedError, match="D-095"):
        atmosphere.NeQuickGIono((60.0, 0.0, 0.0)).delay_m(np.zeros((1, 3)), np.ones((1, 3)),
                                                          0.0, 1575.42e6)


def test_fresnel_water_crossover_and_limits():
    eps = complex(80.0, -5.0)
    rr, rl = reflectivity.fresnel_circular(eps, np.deg2rad([2.0, 60.0]))
    assert abs(rr[0]) > abs(rl[0])          # below Brewster: mostly RHCP
    assert abs(rl[1]) > abs(rr[1])          # above Brewster: mostly LHCP
    assert 4 < np.degrees(reflectivity.brewster_elevation_rad(eps)) < 9


# ---------------------------------------------------------------- config

def test_default_config_is_valid_but_not_ready_for_real_data():
    cfg = config.load_config()
    with pytest.raises(ValueError, match="D-091"):
        config.require_real_data_values(cfg)


def test_config_reports_every_problem(tmp_path):
    cfg = config.load_config()
    cfg["models"]["geoid"] = "egm96"
    cfg["openloop"]["coherent_ms"] = -1
    problems = config.validate(cfg)
    assert any("models.geoid" in p for p in problems)
    assert any("coherent_ms" in p for p in problems)


# ---------------------------------------------------------------- surface

def test_geoid18_fallback_outside_coverage():
    assert surface.in_geoid18_coverage(40.0, -105.0)
    assert not surface.in_geoid18_coverage(48.0, 11.0)


def test_deflection_tilts_normal():
    class Sloped:
        name = "sloped"

        def undulation_m(self, lat, lon):
            return 1e-5 * np.asarray(lat) * frames.WGS84_A  # 10 urad per rad of arc

    s = surface.Surface(Sloped(), 0.0, use_deflection=True)
    xi, eta = s.deflection_rad(np.deg2rad(40.0), np.deg2rad(-105.0))
    assert xi == pytest.approx(-1e-5, rel=0.02)
    tilt = np.arccos(np.dot(s.normal(np.deg2rad(40.0), np.deg2rad(-105.0)),
                            frames.ellipsoid_normal(np.deg2rad(40.0), np.deg2rad(-105.0))))
    assert tilt == pytest.approx(1e-5, rel=0.05)


# ---------------------------------------------------------------- Phase 6: geoid (T-5, T-11)

def _proj_or_skip():
    pyproj = pytest.importorskip("pyproj")
    return pyproj


def test_geoid_grids_match_official_proj_pipelines():
    """
    T-5 / T-11 (consistency form): the hand-built pipelines of `GeoidGrid` give
    the same N as PROJ's registered transformations "NAD83(2011) to NAVD88 height"
    and "WGS 84 to EGM2008 height". Skips without pyproj or without the grids.
    """
    _proj_or_skip()
    from pyproj.transformer import TransformerGroup

    lat, lon = 40.0, -105.25
    try:
        g18 = surface.GeoidGrid("geoid18")
        egm = surface.GeoidGrid("egm2008")
        n_eff_18 = float(g18.undulation_m(np.deg2rad(lat), np.deg2rad(lon), 1600.0)[0])
        n_egm = float(egm.undulation_m(np.deg2rad(lat), np.deg2rad(lon))[0])
    except Exception as exc:  # grid not reachable
        pytest.skip(f"geoid grid not available: {exc}")
    official_18 = TransformerGroup("EPSG:6319", "EPSG:6318+5703", always_xy=True).transformers[0]
    official_egm = TransformerGroup("EPSG:4979", "EPSG:4326+3855", always_xy=True).transformers[0]
    to_nad83 = TransformerGroup("EPSG:7912", "EPSG:6319", always_xy=True).transformers[0]
    _, _, h_nad83, _ = to_nad83.transform(lon, lat, 1600.0, 2026.9)
    _, _, h_navd88 = official_18.transform(lon, lat, h_nad83)
    _, _, h_egm = official_egm.transform(lon, lat, 1600.0)
    assert n_eff_18 == pytest.approx(1600.0 - h_navd88, abs=0.01)
    assert n_egm == pytest.approx(1600.0 - h_egm, abs=0.01)
    # The ITRF -> NAD83(2011) step is about 1 m here: leaving it out is a 1 m error.
    assert 0.5 < h_nad83 - 1600.0 < 1.5


# ---------------------------------------------------------------- Phase 6: GPT3 reader

def test_gpt3_reader_uses_header_names(tmp_path):
    header = ("% lat lon p:a0 A1 B1 A2 B2 T:a0 A1 B1 A2 B2 Q:a0 A1 B1 A2 B2 "
              "dT:a0 A1 B1 A2 B2 undu Hs\n")
    rows = []
    for la in (37.5, 42.5):
        for lo in (252.5, 257.5):
            rows.append(f"{la} {lo} 85000 0 0 0 0 290 5 0 0 0 6 0 0 0 0 -6.5 0 0 0 0 -16 1500\n")
    grid = tmp_path / "gpt3_5.grd"
    grid.write_text(header + "".join(rows))
    from datetime import datetime

    g = atmosphere.GPT3Tropo(grid)
    p, t, e = g.surface_met(np.deg2rad(40.0), np.deg2rad(-105.0), 1500.0, datetime(2026, 1, 1))
    assert p == pytest.approx(850.0, rel=1e-6)          # at the grid height Hs
    assert t == pytest.approx(295.0, abs=0.01)          # a0 + A1 cos(~0)
    p2, t2, _ = g.surface_met(np.deg2rad(40.0), np.deg2rad(-105.0), 2500.0, datetime(2026, 1, 1))
    assert t2 == pytest.approx(t - 6.5, abs=0.01)       # lapse rate over 1 km
    assert 0.85 < p2 / p < 0.92                          # about 11 % per km
    assert g.surface_n0(np.deg2rad(40.0), np.deg2rad(-105.0), 1500.0,
                        datetime(2026, 1, 1)) > 200


# ---------------------------------------------------------------- Phase 6: IONEX

def test_ionex_constant_map_gives_single_layer_delay():
    from datetime import datetime, timezone
    from types import SimpleNamespace

    class Map:
        def __init__(self, epoch):
            self.epoch = epoch

        def get_lats(self):
            return np.arange(87.5, -90, -2.5)

        def get_lons(self):
            return np.arange(-180.0, 180.1, 5.0)

        def get_map_array(self):
            return np.full((len(self.get_lats()), len(self.get_lons())), 200.0)  # 20 TECU

    epochs = [datetime(2026, 11, 10, h, tzinfo=timezone.utc) for h in (0, 2)]
    ds = SimpleNamespace(header=SimpleNamespace(base_radius=6371.0, height_1=450.0,
                                                exponent=-1),
                         tec_maps=[Map(e) for e in epochs])
    iono = atmosphere.IonexIono(ds)
    rx = frames.geodetic_to_ecef(np.deg2rad(40.0), np.deg2rad(-105.0), 1600.0)[None]
    up = frames.ellipsoid_normal(np.deg2rad(40.0), np.deg2rad(-105.0))
    sat = rx + 2.0e7 * up[None]  # zenith
    d = iono.delay_m(rx, sat, 0.0, 1575.42e6, unix_s=epochs[0].timestamp() + 1800)
    assert d[0] == pytest.approx(40.3 * 20 * 1e16 / 1575.42e6**2, rel=1e-3)
    assert iono.excess_delay_m(np.array([0.3]))[0] == 0.0


# ---------------------------------------------------------------- Phase 6: Mode B, precise

def test_mode_b_absolute_code_phase_agrees_with_mode_a():
    sig = GpsL1CA(5)
    world = synthetic.SyntheticWorld()
    _, _, truth, ref, m = _setup(sig, world, fs=FS_L1, duration=0.3)
    tbase = timebase.TimeBase(0.0, 0.0, world.clock_drift_ppm * 1e-6, world.clock_offset_s)
    prov = sat_states.TrackProvider(world.sat_ecef)
    mode_b = model.absolute_code_phase_ms(m.geometry, tbase, prov, "G05")
    mode_a = m.code_phase_ms(m.geometry.uptime_ms)
    diff = mode_a - mode_b
    # The receiver clock drift (0.3 ppm) changes Mode B's time base by 0.3 ppm of
    # 0.3 s; the remaining difference is the constant start value.
    assert np.ptp(diff) < 1e-6


def test_precise_provider_lagrange_reproduces_a_smooth_orbit():
    from types import SimpleNamespace

    epochs = np.arange(0.0, 7200.0, 300.0)
    w = 2 * np.pi / 43082.0
    pos = 26.56e6 * np.stack([np.cos(w * epochs), np.sin(w * epochs), 0 * epochs], axis=1)
    sp3 = SimpleNamespace(epochs=epochs, position={"G01": pos}, clock={"G01": 0 * epochs})
    prov = sat_states.PreciseProvider(sp3)
    t = np.array([3333.3])
    exact = 26.56e6 * np.array([np.cos(w * t[0]), np.sin(w * t[0]), 0.0])
    np.testing.assert_allclose(prov.position_ecef("G01", t)[0], exact, atol=1e-3)


def test_broadcast_provider_uses_bdt_for_beidou():
    seen = []

    class Eph:
        def orbit_state(self, tow):
            seen.append(tow)
            return SimpleNamespace(position_ecef_m=np.zeros(3))

        def clock_correction_s(self, tow):
            return 0.0

    from types import SimpleNamespace

    prov = sat_states.BroadcastProvider({"C27": Eph(), "G05": Eph()})
    prov.position_ecef("C27", np.array([100.0]))
    prov.position_ecef("G05", np.array([100.0]))
    assert seen == [86.0, 100.0]


# ---------------------------------------------------------------- attitude, io

def test_ardupilot_csv_attitude_and_time_tie(tmp_path):
    gps = tmp_path / "gps.csv"
    gps.write_text("TimeUS,GWk,GMS\n1000000,2342,64800000\n2000000,2342,64801000\n")
    att = tmp_path / "att.csv"
    att.write_text("TimeUS,Roll,Pitch,Yaw\n1000000,0,0,350\n2000000,0,0,10\n")
    a = attitude.read_ardupilot_csv(att, gps)
    assert a.time_gpst_s[0] == pytest.approx(2342 * 604800 + 64800.0)
    _, _, yaw = a.at(a.time_gpst_s[0] + 0.5)
    assert np.degrees(yaw[0]) % 360 == pytest.approx(0.0, abs=1e-6)  # through north


def test_openloop_io_roundtrip(tmp_path):
    from utils.openloop import io as olio

    sig = GpsL1CA(5)
    world = synthetic.SyntheticWorld()
    _, nad, _, ref, m = _setup(sig, world, fs=FS_L1, duration=0.1)
    ol = correlator.run_open_loop(nad, samp_rate=FS_L1, start_uptime_ms=0.0, signal=sig,
                                  model=m, lags_chips=np.array([-0.5, 0.0, 0.5]))
    path = olio.save(tmp_path / "x.h5", {"G05": ol}, provenance={"models": "synthetic"},
                     products={"G05": {"cn0_dbhz": np.array([45.0])}})
    runs, products, prov = olio.load(path)
    np.testing.assert_array_equal(runs["G05"]["waveforms"], ol.waveforms)
    assert products["G05"]["cn0_dbhz"][0] == 45.0
    assert prov["models"] == "synthetic"


# ---------------------------------------------------------------- chunked file run

def test_chunked_file_run_equals_in_memory_run(tmp_path):
    """No code period is lost or doubled at the chunk joins."""
    from utils.sample_streaming import SampleParameters

    sig = GpsL1CA(5)
    world = synthetic.SyntheticWorld()
    _, nad, _, ref, m = _setup(sig, world, fs=FS_L1, duration=0.35)
    # Write a float32 complex file (no quantisation, so the two runs match exactly).
    path = tmp_path / "nadir.dat"
    np.stack([nad.real, nad.imag], axis=1).astype(np.float32).tofile(path)
    params = SampleParameters(bit_depth=32, is_complex=True, is_integer=False)
    lags = np.array([-0.5, 0.0, 0.5])
    whole = correlator.run_open_loop(nad, samp_rate=FS_L1, start_uptime_ms=0.0, signal=sig,
                                     model=m, lags_chips=lags, first_uptime_ms=5.0,
                                     last_uptime_ms=330.0)
    chunked = correlator.run_open_loop_file(path, params, samp_rate=FS_L1,
                                            file_start_uptime_ms=0.0, signal=sig, model=m,
                                            lags_chips=lags, first_uptime_ms=5.0,
                                            last_uptime_ms=330.0, chunk_ms=70.0)
    d = np.diff(chunked.periods)
    assert np.all(d == 1), (np.nonzero(d != 1), d[d != 1], chunked.periods[np.nonzero(d != 1)[0]])
    common = np.intersect1d(whole.periods, chunked.periods)
    assert len(common) >= len(whole.periods) - 1
    a = whole.waveforms[np.isin(whole.periods, common)]
    b = chunked.waveforms[np.isin(chunked.periods, common)]
    np.testing.assert_allclose(a, b, rtol=1e-3, atol=1e-2)
