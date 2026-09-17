"""
Coherent integration shorter than the replica.

The replica -- and therefore the FFT -- must span a whole code period, because the
correlation is circular.  How much *data* goes into one coherent integration is a
separate choice, and capping it is the only way to acquire a signal whose data
symbol is no longer than its code period.

GPS L2 CM is that case: its 20 ms code period is exactly its CNAV symbol, so a
full-period coherent integration at an arbitrary alignment straddles a symbol
boundary.  The interesting part is *how* that fails.  At the true Doppler the two
halves subtract, but the search maximises over Doppler, and a mid-window sign flip
has a spectral null at DC with its energy displaced to roughly +/-1/(2T).  So the
peak does not vanish -- it moves, and acquisition reports a confidently detected
signal at a Doppler that is wrong by a bin.  A silently biased tracking seed is a
good deal worse than a missed detection.
"""

from __future__ import annotations

import numpy as np
import pytest

from utils import bpsk_acquisition as bpsk_acq
from utils.signal_interfaces import (
    GpsL1CA,
    GpsL1C,
    GpsL2C,
    GpsL5,
    build_acquisition_code_params,
    build_signals,
)

from . import synthetic

FS = 5_000_000
PRN = 1
# Boundary at the window edge vs dead centre.  _nav_bits flips at every multiple
# of 20 ms, so a dwell starting at 10 ms puts a flip exactly mid-window.
START_NO_FLIP_SEC = 0.000
START_FLIP_MID_SEC = 0.010
CODE_PHASE_MS = 0.61


def _config(**overrides) -> bpsk_acq.AcquisitionConfiguration:
    params = dict(
        coherent_duration_replica_ms=20,
        num_blocks=1,
        sample_rate=FS,
        min_search_doppler_hz=-2000,
        max_search_doppler_hz=2000,
    )
    params.update(overrides)
    return bpsk_acq.AcquisitionConfiguration(**params)


# --------------------------------------------------------------------------
# Block packing
# --------------------------------------------------------------------------

def test_packing_reduces_to_reshape_when_coherent_equals_replica():
    """The pre-existing path must be untouched, bit for bit."""
    rng = np.random.default_rng(0)
    length, num_blocks = 64, 3
    samples = (
        rng.normal(size=length * num_blocks) + 1j * rng.normal(size=length * num_blocks)
    ).astype(np.complex64)

    packed = bpsk_acq.pack_coherent_blocks(samples, num_blocks, length, length)
    np.testing.assert_array_equal(packed, samples.reshape(num_blocks, length))


def test_short_blocks_sit_where_they_were_received():
    """
    Block j lands at offset j*coherent within the code period, not at 0.

    This is the whole reason the padding is not just `block[:n_coh]`: block j was
    received j*n_coh samples later, so its correlation peak sits that much further
    along the code.  Padding every block at 0 leaves four peaks at four different
    lags and the square-law sum smears them instead of stacking them.
    """
    length, coherent, num_blocks = 20, 5, 4
    samples = np.arange(1, num_blocks * coherent + 1).astype(np.complex64)

    packed = bpsk_acq.pack_coherent_blocks(samples, num_blocks, length, coherent)

    for j in range(num_blocks):
        expected = np.zeros(length, dtype=np.complex64)
        expected[j * coherent : (j + 1) * coherent] = samples[
            j * coherent : (j + 1) * coherent
        ]
        np.testing.assert_array_equal(packed[j], expected)


def test_blocks_wrap_at_the_code_period_boundary():
    """A block straddling the period wraps; circular correlation handles it."""
    length, coherent, num_blocks = 20, 8, 3
    samples = np.arange(1, num_blocks * coherent + 1).astype(np.complex64)

    packed = bpsk_acq.pack_coherent_blocks(samples, num_blocks, length, coherent)

    # Block 2 starts at (2*8) % 20 = 16 and runs 8 samples: 16..19, then 0..3.
    np.testing.assert_array_equal(packed[2][16:20], samples[16:20])
    np.testing.assert_array_equal(packed[2][0:4], samples[20:24])
    assert np.all(packed[2][4:16] == 0), "the rest of the window must stay zero"


def test_packing_rejects_a_short_sample_block():
    with pytest.raises(ValueError, match="need 20 samples"):
        bpsk_acq.pack_coherent_blocks(np.zeros(10, dtype=np.complex64), 4, 20, 5)


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

def test_coherent_defaults_to_the_replica_length():
    config = _config()
    assert config.coherent_duration_sample_ms == 20.0
    assert config.coherent_length_samples == config.replica_length_samples
    assert config.doppler_response_width_hz == pytest.approx(config.fft_resolution)


def test_short_coherent_gives_a_grid_finer_than_the_response():
    """
    Grid spacing follows the FFT length, mainlobe width follows the coherent
    length.  Separating them is what leaves the Doppler grid 4x oversampled, so
    the residual error stays grid-limited rather than mainlobe-limited.
    """
    config = _config(coherent_duration_sample_ms=5.0, num_blocks=4)
    assert config.fft_resolution == pytest.approx(50.0)
    assert config.doppler_response_width_hz == pytest.approx(200.0)
    # Same dwell as one 20 ms block, so the two are directly comparable.
    assert config.acq_total_duration_ms == pytest.approx(20.0)


def test_coherent_longer_than_the_replica_folds():
    """
    A coherent block spanning several replica periods sums them onto one window
    before the transform, rather than correlating against a tiled replica.  The
    two are the same computation; the fold is the one that does not repeat its
    answer once per period.
    """
    config = _config(coherent_duration_sample_ms=100.0)      # 5 x the 20 ms replica
    assert config.fold_factor == 5
    assert config.coherent_length_samples == 5 * config.replica_length_samples
    # Whole-bin steps coarsen with the shorter FFT, and sub-bin offsets put the
    # search grid back exactly where an unfolded replica would have placed it.
    assert len(config.doppler_sub_offsets_hz) == 5
    assert config.doppler_step_hz == pytest.approx(config.fft_resolution / 5)
    assert config.doppler_response_width_hz == pytest.approx(config.doppler_step_hz)


def test_a_fractional_multiple_of_the_replica_is_rejected():
    """Folding sums whole periods; a part period would count for less, silently."""
    with pytest.raises(ValueError, match="not a whole multiple"):
        _config(coherent_duration_sample_ms=25.0)


def test_coherent_shorter_than_a_sample_is_rejected():
    with pytest.raises(ValueError, match="shorter than one sample"):
        _config(coherent_duration_sample_ms=1e-6)


# --------------------------------------------------------------------------
# Acquiring L2 CM across a CNAV symbol boundary
# --------------------------------------------------------------------------

def _acquire(start_sec, noise_sigma, *, coherent=None, num_blocks=1, seed=7):
    config = _config(coherent_duration_sample_ms=coherent, num_blocks=num_blocks)
    samples = synthetic.generate_l2c_samples(
        prn=PRN,
        start_sec=start_sec,
        duration_sec=config.acq_total_duration_ms * 1e-3,
        samp_rate=FS,
        doppler_hz=0.0,
        code_phase_ms=CODE_PHASE_MS,
        nav_bits=True,
        noise_sigma=noise_sigma,
        rng=np.random.default_rng(seed),
    )
    signals = build_signals(GpsL2C, prns=[PRN])
    result = bpsk_acq.run_acquisition(
        sample_block=samples,
        sample_block_uptime_epoch_ms=0.0,
        acq_config=config,
        code_parameters=build_acquisition_code_params(GpsL2C, signals),
        prob_false_alarm_total=1e-6,
        noise_var_method="abscorrvar",
    )[f"G{PRN:02d}"]
    return result, config


def _expected_code_phase_ms(start_sec: float) -> float:
    """Code phase at the dwell start, wrapped into one 20 ms CM period."""
    return (CODE_PHASE_MS + start_sec * 1e3) % 20.0


def test_a_symbol_flip_mid_window_biases_the_doppler_estimate():
    """
    The failure that motivates the whole feature, and it is not a missed
    detection: the peak survives, moves a Doppler bin, and is reported as a
    confident acquisition.
    """
    clean, _ = _acquire(START_NO_FLIP_SEC, 20.0)
    flipped, _ = _acquire(START_FLIP_MID_SEC, 20.0)

    assert clean.signal_detected and flipped.signal_detected

    assert clean.acq_doppler_hz == pytest.approx(0.0, abs=1e-6)
    assert abs(flipped.acq_doppler_hz) >= 50.0, (
        "a mid-window symbol flip should displace the peak off the true Doppler"
    )
    # Same num_blocks, so the normalised peaks are directly comparable.
    assert flipped.normalized_peak_value < 0.7 * clean.normalized_peak_value


def test_short_coherent_blocks_recover_the_doppler_across_a_flip():
    """Four 5 ms blocks over the same 20 ms of data put the Doppler back."""
    flipped, _ = _acquire(START_FLIP_MID_SEC, 20.0)
    split, _ = _acquire(START_FLIP_MID_SEC, 20.0, coherent=5.0, num_blocks=4)

    assert split.signal_detected
    assert split.acq_doppler_hz == pytest.approx(0.0, abs=1e-6)
    assert abs(split.acq_doppler_hz) < abs(flipped.acq_doppler_hz)


def test_short_coherent_blocks_still_resolve_the_code_phase():
    """
    Guards the offset placement end to end: if the blocks were padded at 0 the
    four peaks would land at four different lags and the reported code phase
    would be whichever one noise happened to favour.
    """
    split, _ = _acquire(START_FLIP_MID_SEC, 20.0, coherent=5.0, num_blocks=4)
    expected_ms = _expected_code_phase_ms(START_FLIP_MID_SEC)
    assert split.acq_code_phase_seconds * 1e3 == pytest.approx(expected_ms, abs=1e-3)


def test_splitting_does_not_disturb_a_flip_free_dwell():
    """Shortening coherent integration is safe when there is nothing to avoid."""
    whole, _ = _acquire(START_NO_FLIP_SEC, 20.0)
    split, _ = _acquire(START_NO_FLIP_SEC, 20.0, coherent=5.0, num_blocks=4)

    assert split.signal_detected
    assert split.acq_doppler_hz == pytest.approx(whole.acq_doppler_hz, abs=1e-6)
    assert split.acq_code_phase_seconds == pytest.approx(
        whole.acq_code_phase_seconds, abs=2.0 / FS
    )


# ---------------------------------------------------------------------------
# The chip axis acquisition reports on
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "signal_type", [GpsL1CA, GpsL2C, GpsL5, GpsL1C], ids=lambda s: s.signal_type_id
)
def test_acquisition_reports_the_signals_own_chip_rate(signal_type):
    """
    `AcqSignalCodeParameters.rate_chips_per_sec` is the rate a delay in seconds is
    converted to chips with -- by `plot_acquisition_code_phase_slices`, and by the
    fine search when it places its taps.  It has to be the same chip rate tracking
    uses, or "chips" means two different lengths in one pipeline.

    This is a regression test.  An earlier design folded L1C's subcarrier into the
    replica on a 12x sub-chip axis, which made this rate 12.276 Mcps: correct for
    the replica, and wrong for every consumer of the number, so the acquisition
    delay plot came out in sub-chips with an axis labelled chips.
    """
    signals = build_signals(signal_type, prns=[PRN])
    params = build_acquisition_code_params(signal_type, signals)[f"G{PRN:02d}"]
    assert params.rate_chips_per_sec == signal_type.tracking_code_rate_chips_per_sec


def _acquire_l1c():
    """One clean L1C dwell, enough for a delay slice to be drawn from."""
    samp_rate = 25e6
    samples = synthetic.generate_l1c_samples(
        prn=PRN, start_sec=0.0, duration_sec=0.040, samp_rate=samp_rate,
        doppler_hz=1500.0, code_phase_ms=3.27, nav_bits=True,
        noise_sigma=0.5, rng=np.random.default_rng(1),
    )
    config = bpsk_acq.AcquisitionConfiguration(
        coherent_duration_replica_ms=10, num_blocks=4, sample_rate=samp_rate,
        min_search_doppler_hz=-5000.0, max_search_doppler_hz=5000.0,
    )
    signals = build_signals(GpsL1C, prns=[PRN])
    results = bpsk_acq.run_acquisition(
        np.ascontiguousarray(samples, dtype=np.complex64), 0.0, config,
        build_acquisition_code_params(GpsL1C, signals), prob_false_alarm_total=1e-5,
    )
    return results, config


def test_the_acquisition_delay_plot_axis_is_in_whole_chips():
    """
    Read the delay axis back off the rendered line rather than trusting the rate:
    the window is stated in chips, so a plot drawn on a sub-chip axis silently
    shows 1/12th of what was asked for -- which is how the bug above stayed
    invisible.  One chip of L1C is 1/1.023 us, so a +/-3 chip window spans about
    5.9 us of delay.
    """
    matplotlib = pytest.importorskip("matplotlib")
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from utils import plotting

    results, _ = _acquire_l1c()
    fig = plt.figure()
    try:
        axes = plotting.plot_acquisition_code_phase_slices(
            fig, results, window_chips=3.0
        )
        spans = [
            line.get_xdata().max() - line.get_xdata().min()
            for line in axes.get_lines()
            if len(line.get_xdata()) > 2
        ]
    finally:
        plt.close(fig)

    assert spans, "no delay slice was drawn"
    # Within a chip of the full +/-3 window, and nowhere near the 0.5 chips a
    # 12x sub-chip axis would have shown.
    assert max(spans) == pytest.approx(6.0, abs=1.0)


def test_the_acquisition_delay_axis_puts_a_late_reflection_on_the_right():
    """
    The axis says "code delay", so a signal that arrives LATER must plot to the
    RIGHT.  That is not what the correlation grid gives: it is indexed by code
    phase, which runs the other way -- a signal delayed by `d` chips peaks at index
    `L - d` -- so the plot negates it.

    Nothing about a single curve reveals which sign is being drawn, and both
    readings look equally plausible, so the direction is pinned here with a signal
    whose answer is known by construction: a direct path plus a copy of itself
    delayed by a known fraction of a chip.  Get the sign wrong and every statement
    the figure exists to support inverts -- early and late swap, and multipath is
    read on the side it can never appear on.
    """
    matplotlib = pytest.importorskip("matplotlib")
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from utils import plotting

    samp_rate = 25e6
    chip_rate = GpsL1CA.tracking_code_rate_chips_per_sec
    echo_chips = 0.6

    signals = build_signals(GpsL1CA, prns=[PRN])
    params = build_acquisition_code_params(GpsL1CA, signals)
    code = params[f"G{PRN:02d}"]
    config = bpsk_acq.AcquisitionConfiguration(
        coherent_duration_replica_ms=1, num_blocks=4, sample_rate=samp_rate,
        min_search_doppler_hz=-1000.0, max_search_doppler_hz=1000.0,
    )

    def code_at(t_s, delay_s):
        chips = ((t_s - delay_s) * code.rate_chips_per_sec).astype(np.int64)
        return code.sequence[chips % code.length_chips].astype(np.complex64)

    direct_s = 0.3e-3
    t = np.arange(int(samp_rate * config.acq_total_duration_ms * 1e-3)) / samp_rate
    samples = code_at(t, direct_s) + 0.6 * code_at(t, direct_s + echo_chips / chip_rate)

    results = bpsk_acq.run_acquisition(
        np.ascontiguousarray(samples, dtype=np.complex64), 0.0, config, params,
        prob_false_alarm_total=1e-3,
    )

    fig = plt.figure()
    try:
        axes = plotting.plot_acquisition_code_phase_slices(fig, results, window_chips=1.5)
        curves = [line for line in axes.get_lines() if len(line.get_xdata()) > 2]
        assert curves, "no delay slice was drawn"
        x, y = curves[0].get_xdata(), curves[0].get_ydata()
    finally:
        plt.close(fig)

    # The shoulder the echo puts on the correlation, either side of the main peak.
    late = y[x > 0.15].max()
    early = y[x < -0.15].max()
    assert late > early + 1.0, (
        f"a {echo_chips}-chip LATE reflection left more energy at negative delay "
        f"({early:.1f} dB) than at positive ({late:.1f} dB): the axis is inverted, "
        "so it is code phase rather than code delay"
    )


# --------------------------------------------------------------------------
# Folding a coherent block onto one replica period
# --------------------------------------------------------------------------
#
# Correlating N code periods of data against a replica tiled N times computes
# something exactly periodic in one period: a tiled replica's spectrum is
# non-zero only every Nth bin, and those bins are precisely the transform of the
# folded data.  So the long correlation is the folded one, repeated N times.
# Folding says so directly -- a shorter FFT, an unambiguous code phase, and a
# false-alarm correction over the cells that are actually distinct.


def _folding_pair(sample_rate=2_046_000, replica_ms=1, fold=5):
    """The same dwell, once with a tiled replica and once folded."""
    common = dict(
        num_blocks=2, sample_rate=sample_rate,
        min_search_doppler_hz=-1000, max_search_doppler_hz=1000,
    )
    tiled = bpsk_acq.AcquisitionConfiguration(
        coherent_duration_replica_ms=replica_ms * fold,
        coherent_duration_sample_ms=float(replica_ms * fold), **common)
    folded = bpsk_acq.AcquisitionConfiguration(
        coherent_duration_replica_ms=replica_ms,
        coherent_duration_sample_ms=float(replica_ms * fold), **common)
    return tiled, folded


def test_folding_leaves_the_doppler_grid_where_it_was():
    """
    Whole-bin steps coarsen by `fold`, sub-bin offsets divide them back down, and
    the product is the grid the tiled replica searched -- same spacing, same
    hypothesis count, same worst-case seeding error.
    """
    tiled, folded = _folding_pair()
    assert folded.fft_resolution == pytest.approx(tiled.fft_resolution * 5)
    assert folded.doppler_step_hz == pytest.approx(tiled.doppler_step_hz)
    assert folded.num_doppler_hypotheses == tiled.num_doppler_hypotheses
    assert folded.doppler_error_hz == pytest.approx(tiled.doppler_error_hz)
    np.testing.assert_allclose(
        folded.doppler_hypotheses_hz, tiled.doppler_hypotheses_hz, atol=1e-9
    )


def test_folding_counts_only_the_distinct_delay_cells():
    """The repeated copies are the same numbers, not further chances to false-alarm."""
    tiled, folded = _folding_pair()
    assert folded.replica_length_samples * 5 == tiled.replica_length_samples


def test_folded_packing_matches_summing_the_periods_by_hand():
    """`pack_coherent_blocks` folds; state what that means arithmetically."""
    rng = np.random.default_rng(0)
    L, fold, blocks = 64, 5, 2
    x = (rng.normal(size=L * fold * blocks)
         + 1j * rng.normal(size=L * fold * blocks)).astype(np.complex64)
    packed = bpsk_acq.pack_coherent_blocks(x, blocks, L, L * fold)
    for j in range(blocks):
        chunk = x[j * L * fold : (j + 1) * L * fold]
        np.testing.assert_allclose(packed[j], chunk.reshape(fold, L).sum(axis=0),
                                   rtol=1e-6, atol=1e-5)


def test_the_doppler_ramp_is_applied_before_the_fold():
    """
    The inter-period rotation is the whole difference between one coherent
    integration of `fold` periods and `fold` incoherent ones.  Wiping off after
    the fold cannot express it: at a Doppler whose per-period step is 360/fold
    degrees the contributions cancel exactly.
    """
    fs, L, fold = 1000.0, 100, 5
    period_s = L / fs
    doppler = 1.0 / (fold * period_s)          # 2 Hz -> 72 deg per period
    x = np.ones(L * fold, dtype=np.complex64)  # constant signal at baseband
    carried = (x * np.exp(2j * np.pi * doppler * np.arange(len(x)) / fs)).astype(np.complex64)

    right = bpsk_acq.pack_coherent_blocks(carried, 1, L, L * fold,
                                          doppler_hz=doppler, sample_rate=fs)
    assert np.abs(right[0]).mean() == pytest.approx(float(fold), rel=1e-6)

    # Fold first, then ramp: every period lands on the same index with the same
    # phase, so the rotation that should have separated them is simply absent.
    wrong = bpsk_acq.pack_coherent_blocks(carried, 1, L, L * fold)[0]
    assert np.abs(wrong).mean() < 1e-3 * fold


# ---------------------------------------------------------------------------
# Code phase vs code delay
# ---------------------------------------------------------------------------


class _FakeResolution:
    """Enough of an `AmbiguityResolution` for the ambiguity arithmetic."""

    def __init__(self, num_hypotheses: int, resolved: bool):
        self.scores = np.zeros(num_hypotheses)
        self.resolved = resolved


def _result_with_ambiguity(ambiguity_ms: float):
    result = object.__new__(bpsk_acq.AcquisitionResult)
    result.code_phase_ambiguity_ms = ambiguity_ms
    return result


def test_code_delay_is_uptime_minus_code_phase_and_starts_negative():
    """
    The two quantities are easy to conflate: both advance at nearly one millisecond
    per millisecond and differ only by the delay between them.  Tracking seeds the
    code phase at the phase acquisition measured, so the raw difference starts at
    exactly minus that -- which is why it needs the ambiguity added back.
    """
    acquired_phase_ms = 0.7737
    uptime = np.array([0.0, 1.0, 2.0])
    code_phase = uptime + acquired_phase_ms  # what tracking actually holds

    raw = bpsk_acq.code_delay_ms(uptime, code_phase)
    assert np.allclose(raw, -acquired_phase_ms)

    wrapped = bpsk_acq.code_delay_ms(uptime, code_phase, ambiguity_ms=1.0)
    assert np.all(wrapped >= 0.0) and np.all(wrapped < 1.0)
    assert np.allclose(wrapped, 1.0 - acquired_phase_ms)


def test_code_phase_and_code_delay_move_in_opposite_directions():
    """A satellite moving away makes its transmit-time coordinate fall further
    behind receiver time, so a rising delay is a falling code phase."""
    uptime = np.arange(5.0)
    receding = uptime + 0.5 - np.arange(5) * 0.01   # code phase advancing slower
    assert np.all(np.diff(bpsk_acq.code_delay_ms(uptime, receding)) > 0)


def test_an_unresolved_long_code_leaves_the_acquisition_ambiguity():
    result = _result_with_ambiguity(20.0)
    assert bpsk_acq.code_phase_ambiguity_ms(result) == 20.0
    assert bpsk_acq.code_phase_ambiguity_ms(
        result, _FakeResolution(75, resolved=False)
    ) == 20.0


def test_resolving_the_long_code_extends_the_ambiguity_to_its_whole_period():
    """L2C acquires on CM's 20 ms period and resolves which of CL's 75 blocks it
    sat in, so the code phase is settled over CL's full 1.5 s -- longer than any
    transit time, which is what makes an L2C code delay a transit time outright."""
    result = _result_with_ambiguity(20.0)
    assert bpsk_acq.code_phase_ambiguity_ms(
        result, _FakeResolution(75, resolved=True)
    ) == pytest.approx(1500.0)


def test_a_wrapped_l2c_code_delay_lands_in_the_gps_transit_band():
    """
    The check that ties the convention to physics.  GPS transit time runs about
    67 ms overhead to 86 ms at the horizon.  Only L2C's resolved ambiguity is
    longer than that, so only there should the wrapped delay be a transit time --
    and if the sign convention were inverted it would land outside the band.
    """
    result = _result_with_ambiguity(20.0)
    ambiguity = bpsk_acq.code_phase_ambiguity_ms(result, _FakeResolution(75, True))
    # A code phase seeded 1424.2875 ms ahead of uptime, as an L2C channel is.
    delay = bpsk_acq.code_delay_ms(0.0, 1424.2875, ambiguity)
    assert 67.0 < delay < 86.0
