"""
GPS L5 acquisition and tracking.

L5 is the first signal in the repository whose two components share every chip
chip: I and Q are separated by carrier phase, not by time.  These tests pin down
that the correlator keeps them apart, that the 90 degree relationship between
them falls out on its own, and that acquisition hands the loops a Doppler they
can actually pull in.
"""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

import gnss_tools.signals.gps_l5 as gps_l5

from utils import bpsk_acquisition, secondary_code
from utils.bpsk_correlation import correlate__multicomponent
from utils.signal_interfaces import (
    GpsL1CA,
    GpsL5,
    TRACKING_POLICIES,
    build_acquisition_code_params,
    build_signals,
    coherent_duration_target_ms,
)

from . import synthetic

# 10.23 Mcps needs at least ~2 samples/chip; the correlator steps the chip index
# once per sample, so a lower rate would skip chips outright.
SAMP_RATE = 25_000_000
PRN = 1


@pytest.fixture(scope="module")
def l5_definition():
    return build_signals(GpsL5, prns=[PRN])[f"G{PRN:02d}"]


def _correlate(samples, code_set, code_phase_chips, bins, carr_phase=0.0, doppler=0.0):
    out = np.zeros((len(bins), code_set.num_components), dtype=np.complex64)
    correlate__multicomponent(
        samples, SAMP_RATE, carr_phase, doppler, code_set,
        gps_l5.CODE_RATE, code_phase_chips, np.asarray(bins, dtype=np.float64), out,
    )
    return out


# --------------------------------------------------------------------------
# Signal definition
# --------------------------------------------------------------------------

def test_components_are_colocated_not_interleaved(l5_definition):
    """
    Unlike L2C, both L5 components occupy every chip -- no zero-filling, equal
    lengths -- and are separated by carrier phase instead.  That quadrature is
    why the carrier loop cannot move between them without a re-pull, which is
    what keeps it on Q from the first epoch.
    """
    code_set = l5_definition.code_set
    assert code_set.names == ("L5I", "L5Q")
    np.testing.assert_array_equal(code_set.component_code_lengths, [10230, 10230])
    assert np.all(code_set.codes_flat != 0), "L5 transmits both components at every chip"
    assert not code_set.share_branch("L5I", "L5Q")


def test_primary_period_is_one_millisecond(l5_definition):
    """
    10230 chips at 10.23 Mcps.  This equals the aligned correlator's existing
    interval granularity, which is what makes the tiered-code layer cheap to add.
    """
    code_set = l5_definition.code_set
    period_ms = code_set.pattern_period_chips / gps_l5.CODE_RATE * 1e3
    assert period_ms == pytest.approx(1.0)
    assert l5_definition.primary_period_ms == 1


def test_codes_are_int8(l5_definition):
    """The gnss_tools L5 getters return float64 0/1, unlike the L1CA/L2C ones."""
    assert l5_definition.code_set.codes_flat.dtype == np.int8
    assert set(np.unique(l5_definition.code_set.codes_flat)) <= {-1, 1}


def test_carrier_loop_runs_on_the_pilot(l5_definition):
    """
    Q drives the carrier loop from the start.  Switching to it later, at overlay
    sync, would step the reference 90 degrees and force the PLL to re-pull.
    Costas stays on until the overlay is stripped -- NH20 flips Q every 1 ms, so
    the pilot is not yet effectively dataless.
    """
    policy = TRACKING_POLICIES[GpsL5.signal_type_id].discriminator_policy
    assert policy.carrier_component == l5_definition.code_set.index_of("L5Q")
    assert policy.code_components == (0, 1), "delay discriminator should combine I and Q"
    assert policy.costas is True


# --------------------------------------------------------------------------
# Correlation
# --------------------------------------------------------------------------

def test_aligned_replica_recovers_both_components(l5_definition):
    samples = synthetic.generate_l5_samples(
        prn=PRN, start_sec=0.0, duration_sec=1e-3, samp_rate=SAMP_RATE,
        doppler_hz=0.0, code_phase_ms=0.0, nav_bits=False, overlay=False,
    )
    result = _correlate(samples, l5_definition.code_set, 0.0, [0.0])
    n = len(samples)
    assert abs(result[0, 0]) == pytest.approx(n, rel=1e-3), "I did not correlate"
    assert abs(result[0, 1]) == pytest.approx(n, rel=1e-3), "Q did not correlate"


def test_components_are_in_quadrature(l5_definition):
    """
    The correlator needs no quadrature handling: correlating the complex baseband
    against each real code independently puts I on the real axis and Q on the
    imaginary axis by itself.
    """
    samples = synthetic.generate_l5_samples(
        prn=PRN, start_sec=0.0, duration_sec=1e-3, samp_rate=SAMP_RATE,
        doppler_hz=0.0, code_phase_ms=0.0, nav_bits=False, overlay=False,
    )
    corr_i, corr_q = _correlate(samples, l5_definition.code_set, 0.0, [0.0])[0]

    phase_difference_deg = np.degrees(np.angle(corr_q / corr_i))
    assert phase_difference_deg == pytest.approx(90.0, abs=1.0)


def test_components_do_not_leak_into_each_other(l5_definition):
    """A signal carrying only I must leave the Q accumulator near zero."""
    code_i, _ = synthetic.get_l5_codes(PRN)
    n = int(SAMP_RATE * 1e-3)
    chips = (np.arange(n) * (gps_l5.CODE_RATE / SAMP_RATE)).astype(np.int64)
    i_only = code_i[chips % len(code_i)].astype(np.complex64)

    corr_i, corr_q = _correlate(i_only, l5_definition.code_set, 0.0, [0.0])[0]
    assert abs(corr_i) == pytest.approx(n, rel=1e-3)
    assert abs(corr_q) < 0.05 * n, "Q picked up energy from an I-only signal"


def test_wrong_prn_does_not_correlate():
    samples = synthetic.generate_l5_samples(
        prn=PRN, start_sec=0.0, duration_sec=1e-3, samp_rate=SAMP_RATE,
        doppler_hz=0.0, code_phase_ms=0.0, nav_bits=False, overlay=False,
    )
    other = build_signals(GpsL5, prns=[11])["G11"]
    result = _correlate(samples, other.code_set, 0.0, [0.0])
    n = len(samples)
    assert abs(result[0, 0]) < 0.1 * n
    assert abs(result[0, 1]) < 0.1 * n


def test_overlay_flips_fall_on_interval_boundaries(l5_definition):
    """
    Neuman-Hofman advances once per primary code period, which is exactly 1 ms --
    the correlation interval length.  So a 1 ms accumulation never straddles an
    overlay transition and keeps full magnitude, even though the overlay is
    present.  That is what lets Stage 2 treat NH as if it were data.
    """
    for period in range(4):
        samples = synthetic.generate_l5_samples(
            prn=PRN, start_sec=period * 1e-3, duration_sec=1e-3, samp_rate=SAMP_RATE,
            doppler_hz=0.0, code_phase_ms=0.0, nav_bits=False, overlay=True,
        )
        result = _correlate(samples, l5_definition.code_set, 0.0, [0.0])
        n = len(samples)
        assert abs(result[0, 0]) == pytest.approx(n, rel=1e-3), f"I lost magnitude at period {period}"
        assert abs(result[0, 1]) == pytest.approx(n, rel=1e-3), f"Q lost magnitude at period {period}"


def test_early_late_are_symmetric_when_aligned(l5_definition):
    samples = synthetic.generate_l5_samples(
        prn=PRN, start_sec=0.0, duration_sec=1e-3, samp_rate=SAMP_RATE,
        doppler_hz=0.0, code_phase_ms=0.0, nav_bits=False, overlay=False,
    )
    early, prompt, late = _correlate(
        samples, l5_definition.code_set, 0.0, [0.5, 0.0, -0.5]
    )[:, 1]
    assert abs(prompt) > abs(early) and abs(prompt) > abs(late)
    assert abs(early) == pytest.approx(abs(late), rel=0.05)


# --------------------------------------------------------------------------
# Acquisition
# --------------------------------------------------------------------------

def _acquisition_config(
    fine_search: bpsk_acquisition.FineSearchParameters | None = None,
    doppler_hz: tuple[float, float] = (-5000, 5000),
) -> bpsk_acquisition.AcquisitionConfiguration:
    """
    The composite-Q dwell: replica = primary x NH20 = 20 ms, coherent = 5 ms x 4.

    Folding NH20 into the replica removes the overlay as a limit on coherent
    integration, and the recovered code phase then carries the shared overlay
    counter outright -- mod 20 for NH20, and mod 10 for NH10 -- so nothing is left
    to resolve.  Q is the dataless pilot, which is what makes its composite exact:
    I's CNAV symbol period equals NH10's, so data reduces to a sign on each whole
    composite period and can shift the recovered index by one.

    Coherent integration still stops at 5 ms, bounded by I's 10 ms symbol once the
    epoch is shared across components.
    """
    return bpsk_acquisition.AcquisitionConfiguration(
        coherent_duration_replica_ms=20,
        coherent_duration_sample_ms=5.0,
        num_blocks=4,
        sample_rate=SAMP_RATE,
        min_search_doppler_hz=doppler_hz[0],
        max_search_doppler_hz=doppler_hz[1],
        fine_search=fine_search,
    )



def _acquire(
    true_doppler_hz, code_phase_ms, fine_search=None, prn=PRN, noise_sigma=2.0, seed=0,
    start_sec=0.0, nav_bits=True, doppler_hz=(-5000, 5000), window_bins=1,
):
    definitions = build_signals(GpsL5, prns=[prn])
    config = _acquisition_config(fine_search, doppler_hz)
    samples = synthetic.generate_l5_samples(
        prn=prn, start_sec=start_sec, duration_sec=config.acq_total_duration_ms * 1e-3,
        samp_rate=SAMP_RATE, doppler_hz=true_doppler_hz, code_phase_ms=code_phase_ms,
        nav_bits=nav_bits, noise_sigma=noise_sigma, rng=np.random.default_rng(seed),
    )
    results = bpsk_acquisition.run_acquisition(
        sample_block=samples,
        sample_block_uptime_epoch_ms=0.0,
        acq_config=config,
        code_parameters=build_acquisition_code_params(GpsL5, definitions),
        prob_false_alarm_total=1e-6,
        noise_var_method="abscorrvar",
        save_corr_doppler_window_bins=window_bins,
    )
    return results[f"G{prn:02d}"], config


def test_acquisition_detects_and_locates_the_signal():
    code_phase_ms = 0.31
    result, _ = _acquire(1234.0, code_phase_ms, fine_search=bpsk_acquisition.FineSearchParameters())
    assert result.signal_detected
    # Code phase resolves to one sample.
    assert result.acq_code_phase_seconds * 1e3 == pytest.approx(
        code_phase_ms, abs=2e-3 / SAMP_RATE * 1e3 + 1e-6
    )


def test_acquisition_rejects_an_absent_satellite():
    """
    A PRN 11 replica must not detect a PRN 1 signal.

    L5 codes cross-correlate at about -29 dB, so this is a statement about
    operating point as much as about the codes: at an unrealistically strong
    signal (noise_sigma ~ 2) the cross-correlation peak does clear the detection
    threshold, which is the familiar cross-correlation false lock rather than a
    defect.  noise_sigma = 8 is a realistic level where the true peak is ~30x the
    mismatched one.
    """
    config = _acquisition_config()
    samples = synthetic.generate_l5_samples(
        prn=PRN, start_sec=0.0, duration_sec=config.acq_total_duration_ms * 1e-3,
        samp_rate=SAMP_RATE, doppler_hz=800.0, code_phase_ms=0.2,
        noise_sigma=8.0, rng=np.random.default_rng(3),
    )

    peaks = {}
    for prn in (PRN, 11):
        definitions = build_signals(GpsL5, prns=[prn])
        peaks[prn] = bpsk_acquisition.run_acquisition(
            sample_block=samples, sample_block_uptime_epoch_ms=0.0, acq_config=config,
            code_parameters=build_acquisition_code_params(GpsL5, definitions),
            prob_false_alarm_total=1e-6, noise_var_method="abscorrvar",
        )[f"G{prn:02d}"]

    assert peaks[PRN].signal_detected, "the transmitted PRN should acquire"
    assert not peaks[11].signal_detected, "an absent PRN should not acquire"
    assert (
        peaks[PRN].normalized_peak_value > 10 * peaks[11].normalized_peak_value
    ), "matched peak should dominate the cross-correlation peak"


def test_l5_codes_have_low_cross_correlation():
    """Underpins the rejection test above: distinct PRNs must be near-orthogonal."""
    code_i, code_q = synthetic.get_l5_codes(PRN)
    for other_prn in (2, 7, 11, 20):
        other_i, other_q = synthetic.get_l5_codes(other_prn)
        for mine, theirs, label in ((code_i, other_i, "I"), (code_q, other_q, "Q")):
            peak = np.abs(
                np.fft.ifft(np.fft.fft(mine) * np.conj(np.fft.fft(theirs)))
            ).max() / len(mine)
            assert peak < 0.1, f"PRN {PRN} vs {other_prn} {label}: {peak:.3f}"


def test_composite_replica_separates_grid_from_response():
    """
    Folding NH10 into the replica is what buys the finer Doppler grid.

    Grid spacing follows the 20 ms replica (50 Hz); the mainlobe follows the 5 ms
    coherent length (200 Hz).  The grid is therefore four times as fine as the
    response, and the worst-case seeding error drops from the 500 Hz of a 1 ms
    replica to 25 Hz -- comfortably inside a 1 ms FLL's +/-250 Hz unambiguous range, which is
    what the half-bin search previously existed to rescue.
    """
    _, config = _acquire(0.0, 0.0, fine_search=None)
    assert config.fft_resolution == pytest.approx(50.0)
    assert config.doppler_response_width_hz == pytest.approx(200.0)


def test_composite_code_phase_carries_the_overlay_counter():
    """
    The payoff of acquiring on primary x NH20: the code phase lands inside a 20 ms
    code, so its integer millisecond *is* the shared overlay counter -- NH20
    directly, and NH10 as that mod 10.  Both Neuman-Hofman phases and CNAV symbol
    sync fall out of acquisition, with no hypothesis search and no post-lock search.
    The generator advances the overlay once per primary period, so starting the
    dwell 3 ms in must show up as counter 3.
    """
    code_phase_ms = 0.31
    # Data on: the whole point of acquiring on the pilot is that CNAV cannot
    # perturb this.  On the I composite it could -- I's symbol period equals NH10's,
    # so data becomes a per-period sign and a straddling dwell scored a neighbouring
    # index (8.31 ms for a true 7.31 ms).  Q carries no data, so the counter is exact.
    for start_ms, expected_index in ((0.0, 0), (3.0, 3), (7.0, 7), (13.0, 13)):
        result, _ = _acquire(
            1234.0, code_phase_ms, fine_search=None, start_sec=start_ms * 1e-3
        )
        assert result.signal_detected
        phase_ms = result.acq_code_phase_seconds * 1e3
        assert int(np.floor(phase_ms)) == expected_index, (
            f"start {start_ms} ms -> phase {phase_ms:.4f} ms, "
            f"expected overlay index {expected_index}"
        )
        # and the fractional part is still the primary code phase
        assert phase_ms % 1.0 == pytest.approx(code_phase_ms, abs=2e-3)


FINE = bpsk_acquisition.FineSearchParameters


def test_fine_grid_contains_the_coarse_bins():
    """
    The fine grid is a uniform sub-division that lands *on* the coarse bins, not a
    shifted grid beside them.  Everything else here compares fine against coarse at
    a shared bin, so this property is what makes those comparisons meaningful.
    """
    result, config = _acquire(0.0, 0.31, fine_search=FINE())
    fine = result.fine_correlation_result
    assert fine is not None

    delays = fine.code_phase_bins_seconds
    dopplers = fine.doppler_bins_hz
    assert len(delays) == FINE().num_delay_taps == 13
    assert len(dopplers) == FINE().num_doppler_hypotheses == 5

    # Step is one bin over the factor, and the centre coincides with the coarse peak.
    assert (delays[1] - delays[0]) == pytest.approx(1 / (SAMP_RATE * 2))
    assert (dopplers[1] - dopplers[0]) == pytest.approx(config.fft_resolution / 2)
    assert delays[len(delays) // 2] == pytest.approx(result.coarse_code_phase_seconds)
    assert dopplers[len(dopplers) // 2] == pytest.approx(result.coarse_doppler_hz)


def test_fine_and_coarse_agree_bin_for_bin_when_there_is_no_drift():
    """
    The fine grid is evaluated in the time domain, the coarse one by FFT.  At zero
    Doppler there is no code drift for the fine search to correct, so the two must
    produce the *same number* at a shared bin.  Nothing else catches a normalisation
    error between the two paths.
    """
    result, _ = _acquire(
        0.0, 0.31, noise_sigma=0.0,
        fine_search=FINE(delay_factor=1, doppler_factor=1,
                         delay_halfwidth_bins=1, doppler_halfwidth_bins=1),
    )
    centre = float(result.fine_correlation_result.correlation_matrix[1, 1])
    coarse_peak = result.normalized_peak_value * result.noise_var
    assert centre == pytest.approx(coarse_peak, rel=1e-3)


def test_fine_search_corrects_code_drift_the_coarse_search_cannot():
    """
    The coarse replica runs at the nominal chip rate, so it does not model the code
    drifting within the dwell -- 0.107 chips per 5 ms block at 2450 Hz on L5, which
    leaves the four blocks' peaks displaced from each other.  The fine search slaves
    the code rate to Doppler and recovers it.

    Compared **peak to peak**, not bin to bin: at the coarse peak's own bin the fine
    value is lower, because the coarse value there is inflated by exactly the
    smearing being corrected.
    """
    true_code_phase_ms = 0.31
    coarse_only, _ = _acquire(2450.0, true_code_phase_ms, noise_sigma=0.0)
    refined, _ = _acquire(
        2450.0, true_code_phase_ms, noise_sigma=0.0,
        fine_search=FINE(delay_factor=4, doppler_factor=1,
                         delay_halfwidth_bins=4, doppler_halfwidth_bins=1),
    )
    true_seconds = true_code_phase_ms * 1e-3
    coarse_error = abs(coarse_only.coarse_code_phase_seconds - true_seconds) * SAMP_RATE
    fine_error = abs(refined.acq_code_phase_seconds - true_seconds) * SAMP_RATE

    assert coarse_error > 0.5, "expected the coarse search to be displaced by drift"
    assert fine_error < 0.3, f"fine search should recover it, got {fine_error:.3f} samples"

    fine_peak = float(refined.fine_correlation_result.correlation_matrix.max())
    coarse_peak = refined.normalized_peak_value * refined.noise_var
    assert fine_peak > coarse_peak


@pytest.mark.parametrize("offset_samples", [0.0, 0.25, 0.5, -0.25, -0.5])
def test_fine_search_bounds_the_delay_error_by_half_a_fine_step(offset_samples):
    """The refined estimate must land within half a fine step of the truth."""
    code_phase_ms = (7750 + offset_samples) / SAMP_RATE * 1e3
    result, _ = _acquire(
        0.0, code_phase_ms, noise_sigma=0.0,
        fine_search=FINE(delay_factor=4, doppler_factor=1,
                         delay_halfwidth_bins=3, doppler_halfwidth_bins=1),
    )
    error_samples = abs(result.acq_code_phase_seconds - code_phase_ms * 1e-3) * SAMP_RATE
    assert error_samples <= 0.5 / 4 + 1e-6


def test_fine_search_near_the_code_period_boundary_wraps_only_the_scalar():
    """
    A peak a sample short of the code period leaves the window hanging over the end.
    The correlation is still right (the kernel wraps code phase internally) and the
    stored axis stays monotonic -- deliberately unwrapped, or it could not be
    plotted -- while the reported scalar comes back inside one period.
    """
    config = _acquisition_config()
    period_seconds = config.coherent_duration_replica_ms * 1e-3
    # One sample short of the end of the 20 ms composite code.
    code_phase_ms = (period_seconds - 1.0 / SAMP_RATE) * 1e3

    result, _ = _acquire(
        0.0, code_phase_ms, noise_sigma=0.0,
        fine_search=FINE(delay_halfwidth_bins=3, doppler_halfwidth_bins=1),
    )
    assert result.signal_detected

    axis = result.fine_correlation_result.code_phase_bins_seconds
    assert np.all(np.diff(axis) > 0), "the stored delay axis must stay monotonic"
    assert axis[-1] > period_seconds, "this case is only interesting if the window overhangs"

    wrapped = result.acq_code_phase_seconds
    assert 0.0 <= wrapped < period_seconds, f"scalar must be wrapped, got {wrapped}"


def test_fine_search_only_runs_on_a_detection():
    """Refining a noise maximum would dress noise up as a measurement."""
    definitions = build_signals(GpsL5, prns=[PRN])
    config = _acquisition_config(FINE())
    rng = np.random.default_rng(3)
    noise = (
        rng.normal(0, 5, config.total_num_samples)
        + 1j * rng.normal(0, 5, config.total_num_samples)
    ).astype(np.complex64)
    results = bpsk_acquisition.run_acquisition(
        sample_block=noise,
        sample_block_uptime_epoch_ms=0.0,
        acq_config=config,
        code_parameters=build_acquisition_code_params(GpsL5, definitions),
        prob_false_alarm_total=1e-9,
        noise_var_method="abscorrvar",
    )
    result = results[f"G{PRN:02d}"]
    assert not result.signal_detected
    assert result.fine_correlation_result is None
    assert result.acq_doppler_hz == result.coarse_doppler_hz


def test_fine_search_leaves_the_detection_statistics_alone():
    """
    Detection is decided on the coarse grid, whose false-alarm statistics were
    derived for it.  Enabling a post-detection refinement must not move the
    threshold, the cell count, or the value being thresholded.
    """
    plain, _ = _acquire(1234.0, 0.31)
    refined, _ = _acquire(1234.0, 0.31, fine_search=FINE())
    assert refined.signal_detected == plain.signal_detected
    assert refined.num_detection_cells == plain.num_detection_cells
    assert refined.detection_threshold == pytest.approx(plain.detection_threshold)
    assert refined.normalized_peak_value == pytest.approx(plain.normalized_peak_value)


def test_fine_search_is_off_by_default_and_leaves_estimates_coarse():
    result, _ = _acquire(1234.0, 0.31)
    assert result.fine_correlation_result is None
    assert result.acq_doppler_hz == result.coarse_doppler_hz
    assert result.acq_code_phase_seconds == result.coarse_code_phase_seconds


def test_fine_search_reports_its_error_ranges():
    plain = _acquisition_config()
    fine = _acquisition_config(FINE(delay_factor=4, doppler_factor=4))

    assert plain.doppler_error_hz == pytest.approx(0.5 * plain.fft_resolution)
    assert fine.doppler_error_hz == pytest.approx(0.5 * fine.fft_resolution / 4)
    assert plain.code_phase_error_seconds == pytest.approx(0.5 / SAMP_RATE)
    assert fine.code_phase_error_seconds == pytest.approx(0.5 / SAMP_RATE / 4)

    assert "fine search" not in plain.search_resolution_summary()
    assert "delay x4" in fine.search_resolution_summary()


def test_fine_search_rejects_a_window_too_narrow_to_bracket_the_peak():
    """A halfwidth of 0 searches only the coarse peak, which can be half a bin off."""
    with pytest.raises(ValueError, match="halfwidths must be >= 1"):
        FINE(delay_halfwidth_bins=0)


def test_retained_correlation_keeps_full_delay_and_a_doppler_window():
    """
    Multipath lives in the delay dimension, so that axis is kept whole and the
    Doppler axis is cut down to the peak and its neighbours.  Retaining the whole
    grid is ~700 MB per PRN, which a 32-PRN sweep cannot afford.
    """
    windowed, config = _acquire(1234.0, 0.31, window_bins=1)
    full, _ = _acquire(1234.0, 0.31, window_bins=None)

    assert windowed.correlation_result.correlation_matrix.shape == (
        3, config.replica_length_samples
    )
    assert full.correlation_result.correlation_matrix.shape == (
        config.num_doppler_bins, config.replica_length_samples
    )
    assert windowed.acq_doppler_hz == full.acq_doppler_hz
    assert windowed.acq_code_phase_seconds == full.acq_code_phase_seconds


def test_retained_doppler_axis_stays_absolute_and_centred_on_the_peak():
    """
    The stored matrix is a window, but its axis must still read true Doppler --
    the same discipline `start_code_phase_seconds` follows.  Reporting it relative
    to the window would silently move every plotted peak to 0 Hz.
    """
    result, config = _acquire(1234.0, 0.31, window_bins=1)
    doppler_hz = result.correlation_result.doppler_bins_hz

    assert len(doppler_hz) == 3
    assert doppler_hz[1] == pytest.approx(result.acq_doppler_hz)
    assert doppler_hz[0] == pytest.approx(result.acq_doppler_hz - config.fft_resolution)
    assert doppler_hz[2] == pytest.approx(result.acq_doppler_hz + config.fft_resolution)


@pytest.mark.parametrize("edge", ["low", "high"])
def test_doppler_window_overhanging_the_grid_is_nan_filled_not_clipped(edge):
    """
    A peak at the edge of the search range leaves the window with nowhere to take a
    row from.  NaN-filling rather than clipping keeps the shape fixed and the peak
    in the centre, which is what lets consumers index the centre row instead of
    hunting for the peak.
    """
    true_doppler_hz = 1000.0
    # Put the searched range so the peak lands in the first (or last) bin.
    span = 400.0
    doppler_hz = (
        (true_doppler_hz, true_doppler_hz + span) if edge == "low"
        else (true_doppler_hz - span, true_doppler_hz + 1.0)
    )
    result, config = _acquire(true_doppler_hz, 0.31, doppler_hz=doppler_hz, window_bins=1)
    assert result.signal_detected

    matrix = result.correlation_result.correlation_matrix
    assert matrix.shape[0] == 3, "shape must not depend on where the peak landed"

    peak_index = result.peak_doppler_bin
    overhangs_low = peak_index == 0
    overhangs_high = peak_index == config.num_doppler_bins - 1
    assert overhangs_low or overhangs_high, "test did not place the peak at an edge"

    if overhangs_low:
        assert np.all(np.isnan(matrix[0])), "row below the grid must be NaN"
    if overhangs_high:
        assert np.all(np.isnan(matrix[2])), "row above the grid must be NaN"
    assert np.all(np.isfinite(matrix[1])), "the peak row must still hold data"

    # The axis stays correct across the NaN rows, so it is still evenly spaced and
    # still centred on the peak.
    doppler_bins_hz = result.correlation_result.doppler_bins_hz
    assert doppler_bins_hz[1] == pytest.approx(result.acq_doppler_hz)
    assert np.allclose(np.diff(doppler_bins_hz), config.fft_resolution)


def test_correlation_histogram_survives_nan_rows():
    """
    NaN-filling is the cost of the fixed shape, and `np.mean` of anything holding a
    NaN is NaN -- so the one plot that reduces over the whole matrix has to use the
    nan-aware forms or it silently produces nothing.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from utils import plotting

    result, _ = _acquire(1000.0, 0.31, doppler_hz=(1000.0, 1400.0), window_bins=1)
    assert np.isnan(result.correlation_result.correlation_matrix).any()

    fig = plt.figure()
    ax = plotting.plot_acquisition_correlation_histogram(fig, result, num_blocks=4)
    heights = [p.get_height() for p in ax.containers[0]]
    plt.close(fig)
    assert np.isfinite(heights).all() and sum(heights) > 0


# --------------------------------------------------------------------------
# Acquisition handing off to tracking
# --------------------------------------------------------------------------

def test_acquisition_seeds_tracking_to_convergence():
    """
    End to end on the path the notebook takes: acquire, seed a channel from the
    result, track, and confirm the loops pull in and both components accumulate.
    """
    from utils import sample_streaming, tracking_channel
    from utils.signal_interfaces import create_tracking_channels

    true_doppler_hz, code_phase_ms, prn = 1234.0, 0.31, PRN
    acq_result, _ = _acquire(true_doppler_hz, code_phase_ms, fine_search=bpsk_acquisition.FineSearchParameters(), prn=prn)
    assert acq_result.signal_detected

    definitions = build_signals(GpsL5, prns=[prn])
    loop_params = tracking_channel.TrackingLoopParameters(
        DLL_bandwidth_hz=2.0, PLL_bandwidth_hz=20.0, FLL_bandwidth_hz=50.0,
        # The length to extend TO. Every channel opens at one correlation interval
        # whatever this says, so asking for 1 ms here would ask for no extension.
        coherent_duration_ms=10, EPL_chip_spacing=0.5,
    )
    channels = create_tracking_channels(
        GpsL5,
        signals=definitions,
        acquisition_results={f"G{prn:02d}": acq_result},
        tracking_signal_ids=[f"G{prn:02d}"],
        loop_params=loop_params,
        output_capacity=400,
    )
    adapter = channels[f"G{prn:02d}"]

    buffer_ms, num_buffers = 50, 4
    rng = np.random.default_rng(9)
    for i in range(num_buffers):
        samples = synthetic.generate_l5_samples(
            prn=prn, start_sec=i * buffer_ms * 1e-3, duration_sec=buffer_ms * 1e-3,
            samp_rate=SAMP_RATE, doppler_hz=true_doppler_hz, code_phase_ms=code_phase_ms,
            noise_sigma=2.0, rng=rng,
        )
        adapter.process_sample_buffer(
            sample_streaming.SampleBuffer(
                samples=samples, start_uptime_ms=i * buffer_ms, samp_rate=SAMP_RATE
            )
        )

    outputs = adapter.outputs
    count = outputs.output_index
    # Far fewer than one epoch per millisecond.  Acquiring on Q x NH20 hands the
    # channel its overlay counter, so wipe-off is live from the first interval and
    # the accumulation lengthens to 10 ms as soon as the PLL locks -- after which
    # epochs are emitted a tenth as often.
    assert count > 25, "too few epochs to judge convergence"

    final_doppler_error = outputs.doppler_freq_hz[count - 1] - true_doppler_hz
    assert abs(final_doppler_error) < 5.0, f"Doppler did not converge: {final_doppler_error:+.2f} Hz"
    assert adapter.channel.loop_state.mode is tracking_channel.TrackingLoopMode.PLL

    # I and Q are equal power, so while both are integrated over the same 1 ms
    # they should be comparable.  Measured before overlay sync, since afterwards
    # Q integrates over 20 ms while I is capped at 10 ms by its CNAV symbols --
    # see test_data_component_cannot_be_integrated_past_its_symbol.
    early_epochs = slice(20, 60)
    prompt_i = np.abs(outputs.prompt_corr[early_epochs, 0]).mean()
    prompt_q = np.abs(outputs.prompt_corr[early_epochs, 1]).mean()
    assert prompt_i > 0 and prompt_q > 0
    assert 0.5 < prompt_i / prompt_q < 2.0, f"I/Q imbalance: {prompt_i:.0f} vs {prompt_q:.0f}"


# --------------------------------------------------------------------------
# Overlay wipe-off and extended coherent integration
# --------------------------------------------------------------------------

def _track_until_synced(noise_sigma=3.0, buffers=14, buffer_ms=50, seed=4):
    """Acquire, then track long enough for the overlay to lock."""
    from utils import sample_streaming, tracking_channel
    from utils.signal_interfaces import create_tracking_channels

    true_doppler_hz, code_phase_ms = 1500.0, 0.31
    definitions = build_signals(GpsL5, prns=[PRN])
    config = _acquisition_config()
    rng = np.random.default_rng(seed)
    acq = bpsk_acquisition.run_acquisition(
        sample_block=synthetic.generate_l5_samples(
            prn=PRN, start_sec=0.0, duration_sec=config.acq_total_duration_ms * 1e-3,
            samp_rate=SAMP_RATE, doppler_hz=true_doppler_hz,
            code_phase_ms=code_phase_ms, noise_sigma=noise_sigma, rng=rng),
        sample_block_uptime_epoch_ms=0.0, acq_config=config,
        code_parameters=build_acquisition_code_params(GpsL5, definitions),
        prob_false_alarm_total=1e-6, noise_var_method="abscorrvar",
            )[f"G{PRN:02d}"]
    assert acq.signal_detected

    loop_params = tracking_channel.TrackingLoopParameters(
        DLL_bandwidth_hz=2.0, PLL_bandwidth_hz=20.0, FLL_bandwidth_hz=50.0,
        coherent_duration_ms=10, EPL_chip_spacing=0.5,
    )
    adapter = create_tracking_channels(
        GpsL5,
        signals=definitions, acquisition_results={f"G{PRN:02d}": acq},
        tracking_signal_ids=[f"G{PRN:02d}"], loop_params=loop_params,
        output_capacity=4000,
    )[f"G{PRN:02d}"]

    # Acquiring on Q x NH20 hands the channel its overlay counter, so it would start
    # synced and never run the search.  Un-sync it deliberately: the post-lock
    # search is still the fallback whenever a caller skips acquisition's answer or
    # the signal is too weak to trust it, and these tests are what cover it.
    adapter.channel.overlay_sync.status = secondary_code.OverlaySyncStatus.UNSYNCED
    adapter.channel.overlay_sync.counter = 0
    adapter.channel._set_policy(TRACKING_POLICIES[GpsL5.signal_type_id].discriminator_policy)
    # Back to the 1 ms warm-up the channel really opened with -- `loop_params`
    # names the length it will extend TO, not the one it starts at.
    start_params = dataclasses.replace(
        loop_params, coherent_duration_ms=tracking_channel.CORRELATION_INTERVAL_MS
    )
    adapter.channel.coherent_duration_ms = start_params.coherent_duration_ms
    adapter.channel.loop_params = start_params

    synced_at_epoch = None
    for i in range(buffers):
        was_synced = adapter.channel.overlay_sync.synced
        adapter.process_sample_buffer(sample_streaming.SampleBuffer(
            samples=synthetic.generate_l5_samples(
                prn=PRN, start_sec=i * buffer_ms * 1e-3, duration_sec=buffer_ms * 1e-3,
                samp_rate=SAMP_RATE, doppler_hz=true_doppler_hz,
                code_phase_ms=code_phase_ms, noise_sigma=noise_sigma, rng=rng),
            start_uptime_ms=i * buffer_ms, samp_rate=SAMP_RATE))
        if not was_synced and adapter.channel.overlay_sync.synced and synced_at_epoch is None:
            synced_at_epoch = adapter.outputs.output_index
    return adapter, synced_at_epoch, true_doppler_hz


def test_overlay_syncs_and_switches_to_pilot_tracking():
    adapter, synced_at, _ = _track_until_synced()
    channel = adapter.channel

    assert channel.overlay_sync.synced, "overlay never locked"
    assert synced_at is not None
    assert channel.overlay_sync.confidence > 2.0

    # Everything that must move together at the transition.
    assert channel.coherent_duration_ms == 10, "coherent accumulation did not extend"
    assert channel.policy.costas is False, "pilot should drop Costas wrapping"
    assert channel.policy.code_components == (0, 1), (
        "delay discriminator should combine I and Q -- a 10 ms epoch is exactly one "
        "CNAV symbol, so I no longer cancels and is worth ~3 dB non-coherently"
    )
    assert channel.loop_params.coherent_duration_ms == 10, "loop filter not retuned"


def test_wipe_off_gives_n_fold_coherent_gain():
    """
    The point of the whole layer.  Folding 10 primary periods coherently must
    multiply the prompt by ~10, not by sqrt(10) -- the latter is what you would
    get if the overlay signs were wrong and the folds added incoherently.
    """
    adapter, synced_at, _ = _track_until_synced(noise_sigma=0.0)
    outputs = adapter.outputs
    count = outputs.output_index

    # Stay clear of the transition on both sides.
    before = np.abs(outputs.prompt_corr[synced_at - 40 : synced_at - 2, 1]).mean()
    after = np.abs(outputs.prompt_corr[count - 15 : count, 1]).mean()

    gain = after / before
    assert gain == pytest.approx(10.0, rel=0.05), f"expected ~10x, got {gain:.1f}x"
    assert gain > 2 * np.sqrt(10), "gain is incoherent -- overlay signs are wrong"


def test_coherent_gain_holds_across_noise_levels():
    for noise_sigma in (0.0, 2.0, 4.0):
        adapter, synced_at, _ = _track_until_synced(noise_sigma=noise_sigma)
        outputs = adapter.outputs
        count = outputs.output_index
        before = np.abs(outputs.prompt_corr[synced_at - 40 : synced_at - 2, 1]).mean()
        after = np.abs(outputs.prompt_corr[count - 15 : count, 1]).mean()
        assert after / before == pytest.approx(10.0, rel=0.1), (
            f"sigma={noise_sigma}: gain {after/before:.1f}x"
        )


def test_tracking_still_converges_after_the_switch():
    adapter, _, true_doppler_hz = _track_until_synced()
    outputs = adapter.outputs
    count = outputs.output_index
    final_error = outputs.doppler_freq_hz[count - 1] - true_doppler_hz
    assert abs(final_error) < 5.0, f"Doppler diverged after switch: {final_error:+.2f} Hz"


def test_extending_integration_narrows_the_loops():
    """
    A discrete loop needs Bn*T well under ~0.25.  Extending the epoch 20x
    multiplies T by 20, so the bandwidths must come down or the loop turns
    under-damped and passes measurement noise into the estimates.
    """
    adapter, _, _ = _track_until_synced()
    params = adapter.channel.loop_params
    update_period_sec = params.coherent_duration_ms * 1e-3
    for bandwidth_hz in (
        params.PLL_bandwidth_hz, params.DLL_bandwidth_hz, params.FLL_bandwidth_hz
    ):
        assert bandwidth_hz * update_period_sec <= 0.1 + 1e-9


def test_the_data_component_survives_the_epoch_it_shares():
    """
    One epoch serves the whole signal, so its length is the shortest limit among
    the components -- I's 10 ms CNAV symbol, not Q's absent one.  At 10 ms each
    epoch is exactly one symbol, so I comes out at full magnitude alongside the
    pilot.  At NH20's tempting 20 ms it would span two symbols and half cancel,
    which is what capping the epoch at 10 ms buys.

    I and Q are equal power, so full magnitude means comparable to Q.
    """
    adapter, _, _ = _track_until_synced(noise_sigma=0.0)
    outputs = adapter.outputs
    count = outputs.output_index
    tail = slice(count - 15, count)
    prompt_i = np.abs(outputs.prompt_corr[tail, 0]).mean()
    prompt_q = np.abs(outputs.prompt_corr[tail, 1]).mean()
    assert 0.5 < prompt_i / prompt_q < 2.0, (
        f"I should survive a symbol-length epoch, got I={prompt_i:.0f} Q={prompt_q:.0f}"
    )


# --------------------------------------------------------------------------
# Overlay phase supplied by acquisition, rather than searched for after lock
# --------------------------------------------------------------------------

def _seeded_channel(initial_overlay_counter, doppler_hz=1500.0, code_phase_ms=0.31):
    from utils import tracking_channel

    signal = build_signals(GpsL5, prns=[PRN])[f"G{PRN:02d}"]
    policy = TRACKING_POLICIES[GpsL5.signal_type_id]
    return tracking_channel.TrackingChannel(
        loop_params=tracking_channel.TrackingLoopParameters(
            DLL_bandwidth_hz=2.0, PLL_bandwidth_hz=20.0, FLL_bandwidth_hz=50.0,
            coherent_duration_ms=1, EPL_chip_spacing=0.5,
        ),
        signal_params=tracking_channel.TrackingSignalParameters(
            code_set=signal.code_set,
            nominal_code_rate_chips_per_sec=gps_l5.CODE_RATE,
            carrier_freq_hz=gps_l5.CARRIER_FREQ, primary_period_ms=1,
        ),
        initial_signal_state=tracking_channel.TrackingSignalState(
            uptime_epoch_ms=0.0, code_phase_ms=code_phase_ms,
            code_rate_ms_per_sec=(1.0 + doppler_hz / gps_l5.CARRIER_FREQ) * 1e3,
            carrier_phase_cycles=0.0, carrier_rate_cyc_per_sec=doppler_hz,
        ),
        output_capacity=4000,
        discriminator_policy=policy.discriminator_policy,
        synced_policy=policy.synced_discriminator_policy,
        synced_coherent_duration_ms=coherent_duration_target_ms(GpsL5, signal, 10),
        initial_overlay_counter=initial_overlay_counter,
    )


def _drive(channel, doppler_hz=1500.0, code_phase_ms=0.31, buffers=14, buffer_ms=50,
           noise_sigma=3.0, seed=4):
    from utils import sample_streaming

    rng = np.random.default_rng(seed)
    extended_at_mode = None
    for i in range(buffers):
        before = channel.coherent_duration_ms
        channel.process_sample_buffer(sample_streaming.SampleBuffer(
            samples=synthetic.generate_l5_samples(
                prn=PRN, start_sec=i * buffer_ms * 1e-3, duration_sec=buffer_ms * 1e-3,
                samp_rate=SAMP_RATE, doppler_hz=doppler_hz,
                code_phase_ms=code_phase_ms, noise_sigma=noise_sigma, rng=rng),
            start_uptime_ms=i * buffer_ms, samp_rate=SAMP_RATE))
        if before == 1 and channel.coherent_duration_ms > 1 and extended_at_mode is None:
            extended_at_mode = channel.loop_state.mode
    return extended_at_mode


# The synthetic advances the overlay once per primary period from code phase 0, so
# the NH20 index at code phase c ms is c % 20.  The first interval starts at
# ceil(0.31) = 1 ms.
CORRECT_SEEDED_COUNTER = 1

# Seeded at 2.34 ms the first interval is ceil(2.34) = 3 ms, so NH20 index 3.
CORRECT_SEEDED_COUNTER_234 = 3


def _record_epoch_openings(channel):
    """Log the code phase at which each epoch's first interval opens."""
    openings = []
    original = channel._complete_interval

    def wrapped():
        opening = channel._epoch_interval_count == 0
        before = channel._epoch_grid_anchored
        code_phase_ms = channel.corr_interval.start_code_phase_ms
        original()
        # An interval dropped while waiting for the anchor leaves the count at 0
        # and the flag still clear; a real opening either anchors or continues an
        # already-anchored grid.
        if opening and channel._epoch_interval_count == 1:
            openings.append((code_phase_ms, before))

    channel._complete_interval = wrapped
    return openings


def test_epochs_open_on_a_symbol_boundary_not_on_the_seeded_code_phase():
    """
    Acquisition seeds an arbitrary code phase, so an epoch grid begun there is
    offset by an arbitrary number of milliseconds and straddles L5 I's 10 ms CNAV
    symbol.  Anchoring on the symbol period -- rather than merely on the epoch
    length, which would also avoid straddling -- additionally puts epoch zero on
    symbol zero, so symbols can later be read off the epoch index.

    Seeded at 2.34 ms with 5 ms epochs: intervals run 3, 4, ... and the first
    epoch opens at 10 ms, not at 3 ms and not at 5 ms.
    """
    from utils import tracking_channel

    channel = _seeded_channel(CORRECT_SEEDED_COUNTER_234, code_phase_ms=2.34)
    channel.coherent_duration_ms = 5
    channel.loop_params = tracking_channel.TrackingLoopParameters(
        DLL_bandwidth_hz=2.0, PLL_bandwidth_hz=20.0, FLL_bandwidth_hz=50.0,
        coherent_duration_ms=5, EPL_chip_spacing=0.5,
    )
    channel._epoch_grid_anchored = False
    openings = _record_epoch_openings(channel)

    _drive(channel, code_phase_ms=2.34, buffers=4)

    assert openings, "no epoch ever opened"
    symbol_ms = channel._epoch_anchor_period_ms
    assert symbol_ms == 10, "L5's shortest symbol is I's 10 ms CNAV symbol"

    first_code_phase, was_anchored = openings[0]
    assert not was_anchored, "the first opening should be the one that anchors the grid"
    assert first_code_phase % symbol_ms == 0, (
        f"first epoch opened at code phase {first_code_phase} ms, "
        f"which is not a multiple of the {symbol_ms} ms symbol period"
    )
    assert first_code_phase == 10, (
        f"expected the first epoch at 10 ms (seed 2.34 -> intervals from 3), "
        f"got {first_code_phase}"
    )
    # Every later epoch stays on the lattice.  The channel may extend from 5 ms to
    # 10 ms part-way through, and both grids are anchored at multiples of 10, so
    # every opening is a multiple of the shorter epoch length either way.
    assert all(code_phase % 5 == 0 for code_phase, _ in openings), (
        f"an epoch opened off the 5 ms lattice: {openings}"
    )
    assert all(not was_anchored_before for _, was_anchored_before in openings[:1])


def test_changing_epoch_length_lands_on_a_symbol_boundary():
    """
    The post-lock extension must not simply continue from wherever the shorter
    epochs happened to end, or the longer grid inherits the offset anchoring exists
    to remove.  It waits at the shorter length instead of switching and discarding
    intervals, so the transition costs no output.
    """
    channel = _seeded_channel(CORRECT_SEEDED_COUNTER_234, code_phase_ms=2.34)
    openings = _record_epoch_openings(channel)

    _drive(channel, code_phase_ms=2.34, buffers=14)

    assert channel.coherent_duration_ms > 1, "expected the channel to extend"
    symbol_ms = channel._epoch_anchor_period_ms
    # Find the first opening after the length changed: spacing jumps to the new
    # epoch length, and that opening must sit on a symbol boundary.
    spacings = [b - a for (a, _), (b, _) in zip(openings, openings[1:])]
    changed = next(
        (i for i, gap in enumerate(spacings) if gap == channel.coherent_duration_ms),
        None,
    )
    assert changed is not None, "never saw the extended epoch cadence"
    assert openings[changed][0] % symbol_ms == 0


def _track_for_cn0(coherent_duration_ms, buffers=6, buffer_ms=50, period_ms=100):
    """Track a synthetic L5 signal with the C/N0 estimator running."""
    from utils import sample_streaming, tracking_channel

    channel = _seeded_channel(CORRECT_SEEDED_COUNTER, code_phase_ms=0.31)
    channel.cn0_params = tracking_channel.CN0EstimatorParameters(
        period_ms=period_ms, overlap_fraction=0.5
    )
    channel.coherent_duration_ms = coherent_duration_ms
    channel._synced_coherent_duration_ms = coherent_duration_ms  # keep the epoch fixed
    channel.loop_params = tracking_channel.TrackingLoopParameters(
        DLL_bandwidth_hz=2.0, PLL_bandwidth_hz=20.0, FLL_bandwidth_hz=50.0,
        coherent_duration_ms=coherent_duration_ms, EPL_chip_spacing=0.5,
    )
    channel._epoch_grid_anchored = False
    channel._cn0_power = np.zeros((period_ms, channel.outputs.num_components))
    channel._cn0_fill = channel._cn0_write = channel._cn0_since_estimate = 0
    channel.outputs.cn0_capacity = 64
    channel.outputs.cn0_dbhz = np.zeros((64, channel.outputs.num_components))
    channel.outputs.cn0_uptime_ms = np.zeros(64)
    channel.outputs.cn0_index = 0
    _drive(channel, code_phase_ms=0.31, buffers=buffers, buffer_ms=buffer_ms)
    return channel


def test_cn0_input_is_one_interval_regardless_of_coherent_duration():
    """
    The requirement the whole feature exists for.  C/N0 is a density, so its
    samples must be one correlation interval long whatever the epoch is; if the
    estimator were fed epochs, the count would fall with the epoch length and the
    reported dB-Hz would be wrong by that factor.
    """
    short = _track_for_cn0(1)
    long = _track_for_cn0(5)

    assert long.outputs.output_index < short.outputs.output_index, (
        "5 ms epochs should produce fewer epochs than 1 ms ones -- otherwise this "
        "test is not exercising what it claims"
    )
    assert short.outputs.cn0_index == long.outputs.cn0_index, (
        f"C/N0 estimate count must follow duration, not epochs: "
        f"{short.outputs.cn0_index} at 1 ms vs {long.outputs.cn0_index} at 5 ms"
    )
    assert short.outputs.cn0_index > 0


def test_cn0_estimates_arrive_on_the_configured_hop():
    channel = _track_for_cn0(1, period_ms=100)
    stamps = channel.outputs.cn0_uptime_ms[channel.outputs.cn0_valid]
    assert len(stamps) >= 3
    # 50% overlap of a 100-interval window -> one estimate per 50 ms.
    assert np.allclose(np.diff(stamps), 50.0, atol=1.0)


def test_cn0_counts_intervals_dropped_by_epoch_anchoring():
    """
    A grid waiting for a symbol boundary discards intervals for tiling reasons, not
    because they are bad correlations.  Excluding them would delay the first
    estimate and put a hole at the start of every channel.
    """
    channel = _track_for_cn0(5, period_ms=100)
    stamps = channel.outputs.cn0_uptime_ms[channel.outputs.cn0_valid]
    assert len(stamps) > 0
    # Seeded at 0.31 ms the first interval is at 1 ms and the 5 ms grid waits for
    # code phase 10; those 9 intervals still feed the estimator, so the first
    # estimate lands one window after tracking starts, not one window after 10 ms.
    assert stamps[0] < 115.0, f"first estimate at {stamps[0]} ms is too late"


def test_seeded_overlay_starts_synced_without_extending():
    """
    Wipe-off and coherent extension are separate concerns, and only the first is
    available at epoch zero.

    Knowing the overlay phase makes wipe-off correct immediately -- it is a sign
    multiply -- and lets the pilot discriminator drop Costas wrapping.  Extending
    the accumulation is limited by Doppler error instead: acquisition seeds to half
    a bin, 50 Hz on L5's 100 Hz grid, and at 20 ms that is df*T = 1.0, a null.
    """
    channel = _seeded_channel(CORRECT_SEEDED_COUNTER)

    assert channel.overlay_sync.synced, "a supplied counter means no search is needed"
    assert channel.coherent_duration_ms == 1, "must not extend before the loops have pulled in"
    assert channel.policy.costas is False, "the pilot discriminator is available at once"


def test_seeded_overlay_extends_only_once_the_pll_has_locked():
    from utils import tracking_channel

    channel = _seeded_channel(CORRECT_SEEDED_COUNTER)
    extended_at_mode = _drive(channel)

    assert extended_at_mode is tracking_channel.TrackingLoopMode.PLL, (
        "coherent integration lengthened while still in FLL"
    )
    assert channel.coherent_duration_ms == 10
    assert channel.loop_params.PLL_bandwidth_hz < 20.0, "loop filter was not retuned"


def test_a_wrong_seeded_counter_never_locks():
    """
    Guards the counter convention itself.

    With the overlay stripped the discriminator uses the full four-quadrant angle,
    so a mis-phased wipe-off is not absorbed the way Costas would absorb it: the
    prompts flip sign pseudo-randomly, the phase-coherence gate never opens, and
    the channel stays in FLL.  An off-by-one here would otherwise be easy to miss.
    """
    from utils import tracking_channel

    wrong = _seeded_channel((CORRECT_SEEDED_COUNTER + 5) % 20)
    _drive(wrong)

    assert wrong.coherent_duration_ms == 1
    assert wrong.loop_state.mode is tracking_channel.TrackingLoopMode.FLL

    right = _seeded_channel(CORRECT_SEEDED_COUNTER)
    _drive(right)
    assert right.loop_state.mode is tracking_channel.TrackingLoopMode.PLL


def test_seeding_a_counter_needs_a_tiered_code():
    from utils import tracking_channel

    signal = build_signals(GpsL1CA, prns=[PRN])[f"G{PRN:02d}"]
    with pytest.raises(ValueError, match="no tiered code"):
        tracking_channel.TrackingChannel(
            loop_params=tracking_channel.TrackingLoopParameters(
                DLL_bandwidth_hz=2.0, PLL_bandwidth_hz=20.0, FLL_bandwidth_hz=50.0,
                coherent_duration_ms=1,
            ),
            signal_params=tracking_channel.TrackingSignalParameters(
                code_set=signal.code_set,
                nominal_code_rate_chips_per_sec=1.023e6,
                carrier_freq_hz=GpsL1CA.carrier_freq_hz,
            ),
            initial_signal_state=tracking_channel.TrackingSignalState(
                uptime_epoch_ms=0.0, code_phase_ms=0.0, code_rate_ms_per_sec=1e3,
                carrier_phase_cycles=0.0, carrier_rate_cyc_per_sec=0.0,
            ),
            initial_overlay_counter=3,
        )


def test_extended_epochs_start_on_a_code_phase_the_epoch_length_divides():
    """
    A single interval is safe anywhere, but an epoch of N intervals is not: begun
    at an arbitrary code phase it can span a data symbol boundary and cancel
    itself.  Epoch starts are therefore anchored to multiples of N ms of code
    phase, which for L5 also puts every epoch at NH20 index 0.
    """
    from utils import sample_streaming, tracking_channel

    channel = _seeded_channel(CORRECT_SEEDED_COUNTER)
    epoch_starts = []
    real = channel.run_loop_filter

    def spy():
        if channel.coherent_duration_ms > 1:
            # start of the epoch that just closed
            epoch_starts.append(
                channel.corr_interval.start_code_phase_ms
                - (channel.coherent_duration_ms - tracking_channel.CORRELATION_INTERVAL_MS)
            )
        return real()

    channel.run_loop_filter = spy
    _drive(channel)

    assert channel.coherent_duration_ms == 10, "never extended"
    assert epoch_starts, "no extended epochs ran"
    epoch_ms = 10
    offenders = [s for s in epoch_starts if s % epoch_ms != 0]
    assert not offenders, f"epochs began off the {epoch_ms} ms grid: {offenders[:5]}"
    # Consecutive extended epochs are exactly one epoch apart.
    assert np.all(np.diff(epoch_starts) == epoch_ms)
