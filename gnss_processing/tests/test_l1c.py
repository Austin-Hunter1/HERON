"""
GPS L1C end to end: acquisition, tracking, and overlay sync.

L1C is the first signal here that is not BPSK and the first whose primary code
period (10 ms) is longer than one correlation interval.  `test_l1c_codes.py` proves
the code sequences against IS-GPS-800J and `test_subcarrier.py` proves the kernel;
this module is about the parts that only appear once those are assembled into a
signal:

  - acquisition folding the subcarrier into its replica, and what happens if it
    does not,
  - the 25/75 power split reaching the delay discriminator,
  - the 10 ms primary period driving overlay wipe-off, and the four-quadrant
    discriminator it buys,
  - the ceiling on coherent integration being L1CD's CNAV-2 symbol rather than the
    overlay -- which is the opposite of L5, and easy to get wrong.
"""

from __future__ import annotations

import numpy as np
import pytest

import gnss_tools.signals.gps_l1c as gps_l1c

from utils import bpsk_acquisition, sample_streaming, secondary_code, tracking_channel
from utils.bpsk_correlation import correlate__multicomponent
from utils.code_components import CodeComponent, build_code_set
from utils.signal_interfaces import (
    ACQUISITION_POLICIES,
    GpsL1C,
    GpsL5,
    TRACKING_POLICIES,
    acquisition_code,
    acquisition_code_period_ms,
    acquisition_resolves_overlay_phase,
    build_acquisition_code_params,
    build_ambiguity_search,
    build_signals,
    coherent_duration_target_ms,
    create_tracking_channels,
)

from . import synthetic

SAMP_RATE = 25e6
PRN = 1


# ---------------------------------------------------------------------------
# The signal definition
# ---------------------------------------------------------------------------


def test_the_two_components_share_a_carrier_branch():
    """
    IS-GPS-800J 3.2.1.6.1: both L1C carriers are in the same phase, and in phase
    with P(Y).  This is the one thing L1C does differently from L5, whose I and Q
    genuinely are in quadrature, and it means the carrier loop could move between
    L1CD and L1CP without a quarter-cycle re-pull.
    """
    code_set = GpsL1C(PRN).code_set
    assert code_set.names == ("L1CD", "L1CP")
    assert code_set.share_branch("L1CD", "L1CP")
    assert not GpsL5(PRN).code_set.share_branch("L5I", "L5Q")


def test_the_power_split_is_carried_into_the_code_set():
    """25/75, and the first signal here whose components are not equal power."""
    weights = GpsL1C(PRN).code_set.power_weights
    np.testing.assert_allclose(weights, [0.25, 0.75])
    assert weights.sum() == 1.0


def test_the_primary_period_is_ten_milliseconds_and_the_overlay_is_eighteen_seconds():
    signal = GpsL1C(PRN)
    assert GpsL1C.primary_period_ms == 10
    assert signal.code_set.pattern_period_chips == 10230
    assert signal.overlay_period_ms == 18_000


def test_only_the_pilot_carries_a_subcarrier_pattern():
    """L1CD is BOC(1,1) throughout; only L1CP is TMBOC, so only it has a mask."""
    code_set = GpsL1C(PRN).code_set
    assert code_set.subcarrier_sub_chips_per_chip.tolist() == [2, 2]
    assert code_set.subcarrier_pattern_sub_chips_per_chip.tolist() == [0, 12]
    assert code_set.subcarrier_pattern_lengths.tolist() == [0, 33]


# ---------------------------------------------------------------------------
# Acquisition
# ---------------------------------------------------------------------------


def test_the_acquisition_code_is_the_pilot_on_the_signals_own_chip_axis():
    """
    Acquisition carries the subcarrier as a subcarrier, not as a finer code: the
    sequence is the plain 10230-chip pilot at 1.023 Mcps, and the replica applies
    the sign from the fractional chip position when it is built.

    Keeping one chip axis is what makes every chip-valued quantity acquisition
    hands onward -- and the delay axis of its plots -- mean the same thing it does
    in tracking.
    """
    signal = GpsL1C(PRN)
    code = acquisition_code(GpsL1C, signal)
    params = build_acquisition_code_params(GpsL1C, {f"G{PRN:02d}": signal})[f"G{PRN:02d}"]
    pilot = signal.code_set.components[1]

    assert len(code) == gps_l1c.CODE_LENGTH == 10_230
    np.testing.assert_array_equal(code, pilot.sequence)
    assert params.rate_chips_per_sec == gps_l1c.CODE_RATE == 1_023_000
    assert acquisition_code_period_ms(GpsL1C, signal) == 10.0
    # The subcarrier rides along beside the sequence, and it is the pilot's own.
    assert params.subcarrier is pilot.subcarrier
    assert params.subcarrier is not None


def test_the_overlay_is_not_folded_into_the_replica():
    """
    L5 folds NH20 in and gets its overlay phase from acquisition for free.  Doing
    that here would mean an 18 second replica, so L1C acquires modulo 10 ms and
    leaves the overlay to the post-lock search.
    """
    assert not ACQUISITION_POLICIES["GPS_L1C"].include_overlay
    assert not acquisition_resolves_overlay_phase(GpsL1C, GpsL1C(PRN))
    # And nothing is ambiguous in the L2C sense: both components are 10230 chips.
    assert build_ambiguity_search(GpsL1C, GpsL1C(PRN)) is None


def test_a_replica_without_the_subcarrier_would_not_acquire_at_all():
    """
    Not an optimisation.  The subcarrier is odd-symmetric within each chip, so a
    plain BPSK replica has its two halves cancel against a BOC signal: the peak
    collapses into the noise rather than merely losing a few dB.
    """
    samples = synthetic.generate_l1c_samples(
        prn=PRN, start_sec=0.0, duration_sec=0.010, samp_rate=SAMP_RATE,
        doppler_hz=0.0, code_phase_ms=0.0, nav_bits=False, overlay=False,
    )
    signal = GpsL1C(PRN)
    pilot = signal.code_set.components[1]

    peaks = {}
    for label, subcarrier in (("boc", pilot.subcarrier), ("bpsk", None)):
        code_set = build_code_set(
            [CodeComponent(
                name="replica", sequence=pilot.sequence, branch=pilot.branch,
                subcarrier=subcarrier,
            )],
            allow_partial_coverage=True,
            chip_rate_hz=gps_l1c.CODE_RATE,
        )
        out = np.zeros((1, 1), dtype=np.complex64)
        correlate__multicomponent(
            samples, SAMP_RATE, 0.0, 0.0, code_set, gps_l1c.CODE_RATE, 0.0,
            np.array([0.0]), out,
        )
        peaks[label] = abs(complex(out[0, 0])) / len(samples)

    assert peaks["boc"] == pytest.approx(np.sqrt(0.75), abs=0.01)
    assert peaks["bpsk"] < 0.05 * peaks["boc"]


# A 45 dB-Hz signal: representative of a real collect, and deliberately not the
# noiseless case.  See `test_a_very_strong_signal_raises_cross_correlation_peaks`.
REALISTIC_NOISE_SIGMA = 20.0


def _acquire(prns, noise_sigma=REALISTIC_NOISE_SIGMA, doppler_hz=1500.0,
             code_phase_ms=3.27, dwell_ms=40, seed=1):
    samples = synthetic.generate_l1c_samples(
        prn=PRN, start_sec=0.0, duration_sec=dwell_ms * 1e-3, samp_rate=SAMP_RATE,
        doppler_hz=doppler_hz, code_phase_ms=code_phase_ms, nav_bits=True,
        noise_sigma=noise_sigma, rng=np.random.default_rng(seed),
    )
    config = bpsk_acquisition.AcquisitionConfiguration(
        coherent_duration_replica_ms=10, num_blocks=4, sample_rate=SAMP_RATE,
        min_search_doppler_hz=-5000.0, max_search_doppler_hz=5000.0,
    )
    signals = build_signals(GpsL1C, prns=prns)
    results = bpsk_acquisition.run_acquisition(
        np.ascontiguousarray(samples, dtype=np.complex64), 0.0, config,
        build_acquisition_code_params(GpsL1C, signals), prob_false_alarm_total=1e-5,
    )
    return results, config


def test_acquisition_detects_the_signal_and_reports_its_code_phase():
    """
    A full dwell through the shipped configuration.  The code phase comes back in
    seconds and Hz, and its chip axis is the signal's own, so tracking is seeded
    from it unchanged.

    Only PRN 1 is transmitted, so PRN 7's code must not produce a detection.
    """
    results, config = _acquire([PRN, 7])

    hit = results[f"G{PRN:02d}"]
    assert hit.signal_detected
    assert hit.acq_doppler_hz == pytest.approx(1500.0, abs=config.fft_resolution)
    assert hit.acq_code_phase_seconds * 1e3 == pytest.approx(3.27, abs=2e-4)
    assert hit.acquisition_code_rate_chips_per_sec == gps_l1c.CODE_RATE

    assert not results["G07"].signal_detected


def test_a_very_strong_signal_raises_cross_correlation_peaks():
    """
    Worth stating explicitly, because it looks like a bug and is not.

    Weil codes are near-orthogonal, not orthogonal: an absent PRN's replica still
    correlates with a present signal at roughly -25 dB.  The detection threshold is
    set from the NOISE floor, so once a signal is strong enough that its
    cross-correlation sidelobes exceed that floor, absent PRNs cross the threshold
    too.  A noiseless fixture is the extreme case and reports every PRN present.

    This is a real receiver phenomenon rather than an artefact -- it is why
    receivers apply cross-correlation checks against a much stronger satellite --
    but it means acquisition tests have to run at a realistic signal level.  At
    45 dB-Hz the true PRN clears the threshold by ~9 dB and the others stay under.
    """
    strong, _ = _acquire([PRN, 7, 11, 19], noise_sigma=0.0)
    realistic, _ = _acquire([PRN, 7, 11, 19])

    absent = ["G07", "G11", "G19"]
    # The true PRN dominates in both cases -- acquisition is working either way.
    assert all(
        strong[f"G{PRN:02d}"].peak_snr_db > strong[sid].peak_snr_db + 20 for sid in absent
    )
    # But only at a realistic level do the absent ones actually stay undetected.
    assert all(strong[sid].signal_detected for sid in absent)
    assert not any(realistic[sid].signal_detected for sid in absent)


# ---------------------------------------------------------------------------
# Tracking
# ---------------------------------------------------------------------------


def _track(duration_ms, *, prn=PRN, doppler_hz=800.0, code_phase_ms=2.15,
           nav_bits=True, noise_sigma=0.0, prompts_to_observe=None,
           coherent_duration_ms=1, seed=0):
    """Run a channel over synthetic L1C, seeded exactly, and return it."""
    signal = build_signals(GpsL1C, prns=[prn])[f"G{prn:02d}"]
    policy = TRACKING_POLICIES["GPS_L1C"]
    channel = tracking_channel.TrackingChannel(
        loop_params=tracking_channel.TrackingLoopParameters(
            DLL_bandwidth_hz=2.0, PLL_bandwidth_hz=20.0, FLL_bandwidth_hz=50.0,
            coherent_duration_ms=coherent_duration_ms, EPL_chip_spacing=0.1,
        ),
        signal_params=tracking_channel.TrackingSignalParameters(
            code_set=signal.code_set,
            nominal_code_rate_chips_per_sec=GpsL1C.tracking_code_rate_chips_per_sec,
            carrier_freq_hz=GpsL1C.carrier_freq_hz,
            primary_period_ms=GpsL1C.primary_period_ms,
        ),
        initial_signal_state=tracking_channel.TrackingSignalState(
            uptime_epoch_ms=0.0, code_phase_ms=code_phase_ms,
            code_rate_ms_per_sec=(1.0 + doppler_hz / GpsL1C.carrier_freq_hz) * 1e3,
            carrier_phase_cycles=0.0, carrier_rate_cyc_per_sec=doppler_hz,
        ),
        output_capacity=duration_ms + 32,
        discriminator_policy=policy.discriminator_policy,
        synced_policy=policy.synced_discriminator_policy,
        synced_coherent_duration_ms=coherent_duration_target_ms(GpsL1C, signal, 10),
        overlay_search=policy.overlay_search,
        overlay_prompts_to_observe=prompts_to_observe or policy.overlay_prompts_to_observe,
    )
    rng = np.random.default_rng(seed)
    buffer_ms = 50
    for index in range(duration_ms // buffer_ms):
        channel.process_sample_buffer(sample_streaming.SampleBuffer(
            samples=synthetic.generate_l1c_samples(
                prn=prn, start_sec=index * buffer_ms * 1e-3,
                duration_sec=buffer_ms * 1e-3, samp_rate=SAMP_RATE,
                doppler_hz=doppler_hz, code_phase_ms=code_phase_ms,
                nav_bits=nav_bits, noise_sigma=noise_sigma, rng=rng,
            ),
            start_uptime_ms=index * buffer_ms, samp_rate=SAMP_RATE,
        ))
    return channel


def test_tracking_converges_and_hands_over_to_the_pll():
    channel = _track(300)
    outputs = channel.outputs
    assert channel.loop_state.mode is tracking_channel.TrackingLoopMode.PLL
    assert outputs.doppler_freq_hz[outputs.output_index - 1] == pytest.approx(800.0, abs=0.5)


def test_the_carrier_loop_runs_on_the_pilot():
    """Three times the data component's power, and eventually dataless."""
    channel = _track(200)
    assert channel.policy.carrier_component == channel.signal_params.code_set.index_of("L1CP")


def test_both_components_come_out_at_their_transmitted_amplitudes():
    """
    The delay discriminator combines them non-coherently, weighted by power, so
    getting the split wrong would bias the code loop rather than break it.  Their
    prompt magnitudes should sit in the ratio sqrt(3).
    """
    channel = _track(200, nav_bits=False)
    outputs = channel.outputs
    magnitudes = np.abs(outputs.prompt_corr[outputs.valid, :])[-50:].mean(axis=0)
    assert magnitudes[1] / magnitudes[0] == pytest.approx(np.sqrt(3.0), rel=0.02)


def test_the_overlay_syncs_and_buys_a_four_quadrant_discriminator():
    """
    The prize for stripping L1CO.  Until then the pilot's sign flips every 10 ms
    exactly like data and the discriminator has to wrap at a quarter cycle.
    """
    channel = _track(1200, prompts_to_observe=50)
    assert channel.overlay_sync.synced
    assert channel.overlay_sync.confidence > 2.0
    assert channel.policy.costas is False


def test_wipe_off_holds_one_sign_for_all_ten_intervals_of_a_code_period():
    """
    The consequence of an overlay chip lasting a primary code period rather than a
    correlation interval.  The 10 ms epoch's prompt must be ten times the 1 ms
    interval's -- fully coherent.  A counter running per interval instead would
    apply L1CO's own pattern *inside* the period and land well short.

    The epochs are selected by their recorded duration rather than by position,
    because where the extension falls is not this test's business: L1C extends on
    PLL lock and not on overlay sync, since L1CO's chip is already a whole primary
    code period and bounds nothing that L1CD's 10 ms CNAV-2 symbol did not bound
    first.  That the ratio is 10 either side of that point is the same claim.
    """
    channel = _track(1200, prompts_to_observe=50, nav_bits=False)
    assert channel.overlay_sync.synced
    assert channel.coherent_duration_ms == 10

    outputs = channel.outputs
    valid = outputs.valid
    magnitudes = np.abs(outputs.prompt_corr[valid, 1])
    durations = outputs.epoch_duration_ms[valid]
    short = magnitudes[durations == 1][-200:]   # the 1 ms warm-up
    long = magnitudes[durations == 10][-20:]    # after the extension
    assert len(short) and len(long)
    assert long.mean() / short.mean() == pytest.approx(10.0, rel=0.02)


def test_tracking_survives_noise_and_a_realistic_seeding_error():
    channel = _track(400, prn=7, doppler_hz=-2400.0, code_phase_ms=0.72,
                     noise_sigma=3.0, seed=5)
    outputs = channel.outputs
    assert channel.loop_state.mode is tracking_channel.TrackingLoopMode.PLL
    assert outputs.doppler_freq_hz[outputs.output_index - 1] == pytest.approx(-2400.0, abs=2.0)


# ---------------------------------------------------------------------------
# The integration ceiling
# ---------------------------------------------------------------------------


def test_the_ceiling_on_integration_is_the_data_symbol_not_the_overlay():
    """
    The opposite of L5, and the easy mistake.  L5 strips NH20 and extends to 10 ms,
    limited by L5I's CNAV symbol.  L1C's overlay chip is ALREADY 10 ms, so stripping
    it removes nothing -- L1CD's 10 ms CNAV-2 symbol is the binding limit, and one
    epoch serves every component.  20 ms would look perfect on the pilot while
    quietly cancelling the data component.
    """
    signal = build_signals(GpsL1C, prns=[PRN])[f"G{PRN:02d}"]
    # A request past the ceiling is capped to it rather than honoured.
    assert coherent_duration_target_ms(GpsL1C, signal, 20, warn=False) == 10

    signal = build_signals(GpsL1C, prns=[PRN])[f"G{PRN:02d}"]
    params = tracking_channel.TrackingSignalParameters(
        code_set=signal.code_set,
        nominal_code_rate_chips_per_sec=GpsL1C.tracking_code_rate_chips_per_sec,
        carrier_freq_hz=GpsL1C.carrier_freq_hz,
        primary_period_ms=GpsL1C.primary_period_ms,
    )
    policy = TRACKING_POLICIES["GPS_L1C"].synced_discriminator_policy

    tracking_channel.validate_coherent_duration(params, policy, 10, overlay_stripped=True)
    with pytest.raises(ValueError, match="data symbol"):
        tracking_channel.validate_coherent_duration(
            params, policy, 20, overlay_stripped=True
        )


@pytest.mark.parametrize("duration_ms", [1, 2, 5, 10])
def test_every_divisor_of_the_code_period_is_a_legal_epoch(duration_ms):
    """Epochs are anchored to multiples of their own length, so the length has to
    divide the symbol period or some epoch straddles a boundary."""
    channel = _track(100, coherent_duration_ms=duration_ms)
    assert channel.outputs.output_index > 0


def test_the_synchroniser_uses_the_fft_search():
    """1800 offsets is where brute force stops being sensible; the strategy is
    policy, so it is stated in TRACKING_POLICIES rather than inferred."""
    assert TRACKING_POLICIES["GPS_L1C"].overlay_search is secondary_code.fft_search
    channel = _track(100)
    assert channel.overlay_sync.search is secondary_code.fft_search
    assert channel.overlay_sync.period == gps_l1c.OVERLAY_LENGTH == 1800


def test_extension_does_not_wait_for_the_overlay_search():
    """
    L1CO gates the DISCRIMINATOR, not the epoch length, and conflating the two cost
    L1C two seconds of 1 ms epochs.

    Two facts, both from L1C's 10 ms primary code period.  The boundary: L1CD's
    code period IS one CNAV-2 symbol, so a code phase that is a multiple of 10 ms
    is a symbol boundary from the seeded acquisition onward -- nothing about L1CO
    is needed to locate it.  The length: an L1CO chip lasts one primary code
    period, so an un-stripped overlay bounds coherent integration at 10 ms, which
    is exactly where L1CD's symbol bounds it anyway.  Neither is true of L5, whose
    NH20 chip lasts 1 ms; there the wait is real (see
    tests/test_l5.py::test_seeded_overlay_extends_only_once_the_pll_has_locked).

    `prompts_to_observe` is set past what the run can supply, so the FFT search
    cannot possibly have succeeded when the assertions below run.
    """
    channel = _track(400, prompts_to_observe=100_000, nav_bits=False)

    assert not channel.overlay_sync.synced, "the search was supposed to be starved"
    assert channel.policy.costas is True, "Costas is what the overlay does gate"
    assert channel.loop_state.mode is tracking_channel.TrackingLoopMode.PLL
    assert channel.coherent_duration_ms == 10, "extension waited on the overlay"

    # And the grid it opened is really on the symbol lattice, not merely 10 ms long.
    outputs = channel.outputs
    valid = outputs.valid
    long_epochs = outputs.epoch_duration_ms[valid] == 10
    starts = outputs.code_phase_ms[valid][long_epochs]
    # Distance to the nearest multiple of 10 ms, which is where a CNAV-2 symbol
    # begins. Wrapped through +/-5 so a start just under a multiple counts as near
    # it rather than 10 ms away from the one below.
    off_lattice_ms = np.abs(((starts + 5.0) % 10.0) - 5.0)
    assert off_lattice_ms.max() < 1e-6, (
        f"10 ms epochs sit up to {off_lattice_ms.max():g} ms off the CNAV-2 symbol "
        "lattice; the grid was never anchored"
    )
    # And the extension really did happen early, not merely by the end of the run.
    assert (outputs.epoch_duration_ms[valid] == 1).sum() < 100, (
        "spent more than 100 ms of warm-up at one interval"
    )
