"""
Assisted acquisition (D-080): the sky mask, the prediction, and the two-stage search.

The search tests use a synthetic dwell with a known receiver clock offset: a strong
G01, a weak G02 that a normal search misses, and no G03.  The assisted search must
measure the offset from G01, find G02 in its narrow stage-2 window, and not find a
satellite that is not there.  The CDDIS download is not tested here (it needs the
network); `predict_satellites` is tested with a simple straight-line orbit.
"""

from __future__ import annotations

from datetime import datetime

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402
from utils import assisted_acquisition as aa  # noqa: E402
from utils import bpsk_acquisition as acq  # noqa: E402
from utils import navigation, plotting  # noqa: E402
from utils.signal_interfaces import (  # noqa: E402
    GpsL1CA,
    build_acquisition_code_params,
    build_signals,
)

FS = 5e6
OFFSET_HZ = -2600.0  # the receiver clock offset in the synthetic dwell
TRUE = {"G01": (1200.0, 0.3e-3, 0.1), "G02": (-900.0, 0.7e-3, 0.025)}  # Doppler, delay, amplitude


# ----- Sky mask --------------------------------------------------------------


def test_a_sector_can_cross_north():
    sector = aa.BlockedSector(300.0, 60.0, 45.0)
    assert sector.covers(330.0, 10.0) and sector.covers(30.0, 45.0)
    assert not sector.covers(90.0, 10.0)
    assert not sector.covers(0.0, 50.0), "above the barrier is open"


def test_the_default_mask_is_the_entire_sky():
    mask = aa.SkyMask()
    assert mask.is_open(123.0, 0.5)
    assert mask.describe() == "entire sky"


def test_the_mask_closes_low_and_blocked_directions():
    mask = aa.SkyMask.from_values(10.0, [(90.0, 180.0, 60.0)])
    assert not mask.is_open(0.0, 5.0)
    assert not mask.is_open(120.0, 50.0)
    assert mask.is_open(120.0, 70.0) and mask.is_open(0.0, 20.0)


def test_sector_text():
    assert aa.parse_blocked_sectors("") == []
    assert aa.parse_blocked_sectors("300-60:60; 120-180:30") == [(300.0, 60.0, 60.0), (120.0, 180.0, 30.0)]
    for bad in ("300-60", "a-b:c", "10-20:95"):
        with pytest.raises(ValueError):
            aa.parse_blocked_sectors(bad)


def test_a_bad_sector_tuple_is_refused():
    with pytest.raises(ValueError, match="az_from_deg"):
        aa.SkyMask.from_values(0.0, [(10.0, 20.0)])


# ----- Time ------------------------------------------------------------------


def test_the_start_time_comes_from_the_collect_id():
    assert aa.collect_start_utc("20261014_011745_l1_novatel_120s") == datetime(2026, 10, 14, 1, 17, 45)
    with pytest.raises(ValueError, match="ASSIST_TIME_UTC"):
        aa.collect_start_utc("l1_rhcp")


def test_utc_text():
    assert aa.parse_utc("2026-10-14 01:17:45") == datetime(2026, 10, 14, 1, 17, 45)
    assert aa.parse_utc("2026-10-14T01:17:45Z") == datetime(2026, 10, 14, 1, 17, 45)
    with pytest.raises(ValueError):
        aa.parse_utc("yesterday")


# ----- Prediction ------------------------------------------------------------


class _StraightOrbit:
    """A satellite moving in a straight line: enough to check geometry and sign."""

    def __init__(self, position, velocity):
        self.p, self.v = np.asarray(position, float), np.asarray(velocity, float)

    def orbit_state(self, t):
        class State:
            pass

        state = State()
        state.position_ecef_m = self.p + self.v * (t - 1000.0)
        return state


def test_an_approaching_satellite_has_a_positive_doppler_and_the_right_elevation():
    receiver = navigation.geodetic_to_ecef(0.0, 0.0, 0.0)
    overhead = receiver + np.array([20_000e3, 0.0, 0.0])  # straight up at lat 0 lon 0
    eph = {"G01": _StraightOrbit(overhead, [-1000.0, 0.0, 0.0])}  # coming down at 1 km/s
    pred = aa.predict_satellites(["G01", "G02"], eph, 1000.0, receiver, 1.57542e9, aa.SkyMask())
    assert set(pred) == {"G01"}, "a satellite with no ephemeris is left out"
    assert pred["G01"].elevation_deg == pytest.approx(90.0, abs=0.01)
    assert pred["G01"].doppler_hz == pytest.approx(1000.0 / aa.SPEED_OF_LIGHT_M_S * 1.57542e9, rel=1e-6)
    assert pred["G01"].open_sky


def test_the_mask_marks_a_low_satellite_closed():
    receiver = navigation.geodetic_to_ecef(0.0, 0.0, 0.0)
    eph = {"G01": _StraightOrbit(receiver + np.array([0.0, 0.0, 20_000e3]), [0.0, 0.0, 0.0])}
    pred = aa.predict_satellites(["G01"], eph, 1000.0, receiver, 1.57542e9, aa.SkyMask(min_elevation_deg=5.0))
    assert pred["G01"].elevation_deg == pytest.approx(0.0, abs=0.01)
    assert not pred["G01"].open_sky


# ----- Search ----------------------------------------------------------------


@pytest.fixture(scope="module")
def setup():
    signals = build_signals(GpsL1CA, prns=[1, 2, 3])
    params = build_acquisition_code_params(GpsL1CA, signals)
    base = acq.AcquisitionConfiguration(
        coherent_duration_replica_ms=1, coherent_duration_sample_ms=5, num_blocks=4,
        sample_rate=FS, min_search_doppler_hz=-5000.0, max_search_doppler_hz=5000.0,
    )
    settings = aa.AssistSettings(dwell_multiplier=10, stage1_satellites=1,
                                 doppler_halfwidth_hz=250.0, full_doppler_hz=5000.0)
    n = aa.required_samples(base, settings)
    t = np.arange(n) / FS
    rng = np.random.default_rng(0)
    x = rng.normal(size=n) + 1j * rng.normal(size=n)
    for sid, (doppler, delay, amp) in TRUE.items():
        code = params[sid]
        chips = ((t - delay) * code.rate_chips_per_sec).astype(np.int64)
        x = x + amp * code.sequence[chips % code.length_chips] * np.exp(2j * np.pi * (doppler + OFFSET_HZ) * t)
    return x.astype(np.complex64), base, params, settings


def _predictions(g03_open: bool):
    return {
        "G01": aa.SatellitePrediction("G01", 10.0, 70.0, TRUE["G01"][0], True),
        "G02": aa.SatellitePrediction("G02", 100.0, 40.0, TRUE["G02"][0], True),
        "G03": aa.SatellitePrediction("G03", 200.0, 20.0, 300.0, g03_open),
    }


def test_snapped_window_covers_the_request_for_either_sign(setup):
    _, base, _, _ = setup
    for low, high in ((-2850.0, -2350.0), (350.0, 850.0), (-250.0, 250.0)):
        a, b = aa.snapped_window(base, low, high)
        config = acq.AcquisitionConfiguration(
            coherent_duration_replica_ms=1, coherent_duration_sample_ms=5, num_blocks=4,
            sample_rate=FS, min_search_doppler_hz=a, max_search_doppler_hz=b,
        )
        hz = config.doppler_hypotheses_hz
        assert hz.size > 0
        assert hz.min() <= low and hz.max() + config.doppler_step_hz >= high


def test_a_normal_search_misses_the_weak_satellite(setup):
    x, base, params, _ = setup
    normal = acq.run_acquisition(x[: base.total_num_samples].copy(), 0.0, base, params,
                                 prob_false_alarm_total=1e-5, noise_var_method="abscorrvar")
    assert normal["G01"].signal_detected
    assert not normal["G02"].signal_detected, "the case must be hard for the normal search"


def test_the_assisted_search_finds_it_and_measures_the_offset(setup):
    x, base, params, settings = setup
    results, report = aa.run_assisted_acquisition(
        x, base, params, _predictions(g03_open=False), aa.SkyMask(), settings, 1e-5
    )
    assert report.stage1_ids == ["G01"] and report.stage2_ids == ["G02"]
    assert report.offset_hz == pytest.approx(OFFSET_HZ, abs=150.0)
    assert set(results) == {"G01", "G02"}, "a satellite under the mask is not searched"
    g02 = results["G02"]
    assert g02.signal_detected
    assert g02.acq_doppler_hz == pytest.approx(TRUE["G02"][0] + OFFSET_HZ, abs=150.0)
    assert g02.config.num_blocks == base.num_blocks * settings.dwell_multiplier
    assert g02.detection_threshold_db < results["G01"].detection_threshold_db
    lines = "\n".join(report.lines(results))
    assert "blocked by mask" in lines and "acquired" in lines


def test_an_open_satellite_that_is_not_there_is_not_found(setup):
    x, base, params, settings = setup
    results, report = aa.run_assisted_acquisition(
        x, base, params, _predictions(g03_open=True), aa.SkyMask(), settings, 1e-5
    )
    assert "G03" in report.stage2_ids
    assert not results["G03"].signal_detected


def test_with_no_stage_1_detection_stage_2_searches_full_width(setup):
    x, base, params, settings = setup
    preds = _predictions(g03_open=True)
    # G03 (absent) is the highest, so stage 1 finds nothing.
    preds["G03"] = aa.SatellitePrediction("G03", 200.0, 85.0, 300.0, True)
    results, report = aa.run_assisted_acquisition(x, base, params, preds, aa.SkyMask(), settings, 1e-5)
    assert report.stage1_ids == ["G03"] and report.offset_hz is None
    assert "no offset" in report.stage2_window
    g03_row = next(line for line in report.lines(results) if line.strip().startswith("G03"))
    assert "1, 2" in g03_row, "a stage-1 miss is searched again in stage 2"
    assert results["G01"].signal_detected
    assert results["G01"].config.max_search_doppler_hz == pytest.approx(5000.0)


def test_too_few_samples_is_an_error(setup):
    x, base, params, settings = setup
    with pytest.raises(ValueError, match="samples"):
        aa.run_assisted_acquisition(x[:1000], base, params, _predictions(False), aa.SkyMask(), settings, 1e-5)


@pytest.mark.parametrize("kw", [dict(dwell_multiplier=0), dict(stage1_satellites=0),
                                dict(doppler_halfwidth_hz=0.0), dict(dwell_multiplier=2.5)])
def test_bad_settings_are_refused(kw):
    with pytest.raises(ValueError):
        aa.AssistSettings(**kw)


# ----- Plots -----------------------------------------------------------------


def test_the_plots_show_per_prn_thresholds_and_the_sky(setup):
    x, base, params, settings = setup
    mask = aa.SkyMask.from_values(10.0, [(180.0, 240.0, 30.0)])
    results, report = aa.run_assisted_acquisition(
        x, base, params, _predictions(g03_open=False), mask, settings, 1e-5
    )
    fig = plt.figure()
    try:
        ax = plotting.plot_acquisition_peak_snr(fig, results)
        labels = [t.get_text() for t in ax.get_legend().get_texts()]
        assert any("per PRN" in label for label in labels)
    finally:
        plt.close(fig)

    fig = plt.figure()
    try:
        ax = plotting.plot_acquisition_code_phase_slices(fig, results, signal_ids=sorted(results))
        labels = [t.get_text() for t in ax.get_legend().get_texts()]
        assert sum("detection threshold" in label for label in labels) == 2
    finally:
        plt.close(fig)

    fig = plt.figure()
    try:
        ax = plotting.plot_assisted_skyplot(fig, report, results)
        labels = {t.get_text() for t in ax.get_legend().get_texts()}
        assert {"acquired", "blocked, not searched", "blocked"} <= labels
        assert "1 of 2" not in ax.get_title() and "2 of 2 searched acquired" in ax.get_title()
    finally:
        plt.close(fig)
