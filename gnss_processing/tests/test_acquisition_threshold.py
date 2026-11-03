"""
The acquisition threshold can be set directly in dB above noise, and every PRN
searched can be plotted against it (D-079).

The dB threshold is the inverse of the p_fa one, so a p_fa run and a dB run at
the equivalent value must decide the same.  The two all-PRN plots must show
every PRN, not only the acquired ones, with the threshold the run used.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402
from utils import bpsk_acquisition as bpsk_acq  # noqa: E402
from utils import plotting  # noqa: E402
from utils.signal_interfaces import (  # noqa: E402
    GpsL1CA,
    build_acquisition_code_params,
    build_signals,
)

SAMP_RATE = 5e6
PRNS = [1, 2, 3, 4]
TRUE_PRN = 1


@pytest.fixture(scope="module")
def dwell():
    """Four PRNs searched; only PRN 1 is in the samples, at a known code phase."""
    signals = build_signals(GpsL1CA, prns=PRNS)
    params = build_acquisition_code_params(GpsL1CA, signals)
    code = params[f"G{TRUE_PRN:02d}"]
    config = bpsk_acq.AcquisitionConfiguration(
        coherent_duration_replica_ms=1, num_blocks=4, sample_rate=SAMP_RATE,
        min_search_doppler_hz=-1000.0, max_search_doppler_hz=1000.0,
    )
    t = np.arange(int(SAMP_RATE * config.acq_total_duration_ms * 1e-3)) / SAMP_RATE
    chips = ((t - 0.3e-3) * code.rate_chips_per_sec).astype(np.int64)
    rng = np.random.default_rng(7)
    noise = (rng.normal(size=t.size) + 1j * rng.normal(size=t.size)) * 2.0
    samples = code.sequence[chips % code.length_chips] + noise
    return np.ascontiguousarray(samples, dtype=np.complex64), config, params


def _acquire(dwell, **kw):
    samples, config, params = dwell
    kw.setdefault("prob_false_alarm_total", 1e-5)
    return bpsk_acq.run_acquisition(samples, 0.0, config, params, **kw)


def test_a_db_threshold_is_used_as_given(dwell):
    results = _acquire(dwell, detection_threshold_db=7.5)
    for result in results.values():
        assert result.detection_threshold_db == pytest.approx(7.5)
        assert result.signal_detected == (result.peak_snr_db > 7.5)


def test_the_db_threshold_is_the_inverse_of_p_fa(dwell):
    by_p_fa = _acquire(dwell, prob_false_alarm_total=1e-3)
    reference = by_p_fa["G01"]
    by_db = _acquire(dwell, detection_threshold_db=reference.detection_threshold_db)
    for sid, result in by_db.items():
        assert result.detection_threshold == pytest.approx(reference.detection_threshold)
        assert result.signal_detected == by_p_fa[sid].signal_detected
        # The implied total is reported, so the result still states its cost.
        assert result.prob_false_alarm_total == pytest.approx(1e-3, rel=1e-6)


def test_a_db_threshold_admits_a_peak_that_p_fa_refuses(dwell):
    by_p_fa = _acquire(dwell, prob_false_alarm_total=1e-5)
    # G02 is not in the samples, so its peak is the highest noise cell, below the line.
    assert not by_p_fa["G02"].signal_detected
    peak = by_p_fa["G02"].peak_snr_db
    lowered = _acquire(dwell, detection_threshold_db=peak - 0.1)
    assert lowered["G02"].signal_detected
    assert lowered["G01"].signal_detected


def test_the_db_threshold_replaces_p_fa_which_is_not_checked(dwell):
    results = _acquire(dwell, prob_false_alarm_total=5.0, detection_threshold_db=8.0)
    assert results["G01"].detection_threshold_db == pytest.approx(8.0)


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_a_non_finite_db_threshold_is_refused(dwell, bad):
    with pytest.raises(ValueError, match="finite"):
        _acquire(dwell, detection_threshold_db=bad)


def test_a_bad_p_fa_is_still_refused_without_a_db_threshold(dwell):
    with pytest.raises(ValueError, match="prob_false_alarm_total"):
        _acquire(dwell, prob_false_alarm_total=0.0)


def test_the_peak_bar_chart_shows_every_prn_and_the_threshold(dwell):
    results = _acquire(dwell, detection_threshold_db=8.0)
    fig = plt.figure()
    try:
        ax = plotting.plot_acquisition_peak_snr(fig, results)
        labels = [t.get_text() for t in ax.get_xticklabels()]
        heights = sorted(p.get_height() for p in ax.patches)
        threshold_lines = [line.get_ydata()[0] for line in ax.get_lines()]
        title = ax.get_title()
    finally:
        plt.close(fig)
    assert labels == sorted(results)
    assert heights == pytest.approx(sorted(r.peak_snr_db for r in results.values()))
    assert threshold_lines == pytest.approx([8.0])
    detected = sum(r.signal_detected for r in results.values())
    assert f"{detected} of {len(PRNS)} acquired" in title


def test_the_delay_plot_can_draw_every_prn(dwell):
    results = _acquire(dwell, detection_threshold_db=8.0)
    fig = plt.figure()
    try:
        ax = plotting.plot_acquisition_code_phase_slices(
            fig, results, signal_ids=sorted(results), title="all"
        )
        texts = [t.get_text() for t in ax.get_legend().get_texts()]
        title = ax.get_title()
    finally:
        plt.close(fig)
    assert title == "all"
    for sid in results:
        assert any(text.startswith(sid) for text in texts), sid
    assert any("threshold (8.00 dB)" in text for text in texts)
    if not all(r.signal_detected for r in results.values()):
        assert "dotted: below threshold" in texts


def test_the_default_delay_plot_still_shows_acquired_prns_only(dwell):
    results = _acquire(dwell, detection_threshold_db=8.0)
    fig = plt.figure()
    try:
        ax = plotting.plot_acquisition_code_phase_slices(fig, results)
        legend = ax.get_legend()
        texts = [t.get_text() for t in legend.get_texts()] if legend else []
    finally:
        plt.close(fig)
    for sid, result in results.items():
        assert any(t.startswith(sid) for t in texts) == result.signal_detected
