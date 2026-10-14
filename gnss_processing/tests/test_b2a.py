"""
BeiDou B2a acquisition and tracking on synthetic signals.

B2a has the same structure as GPS L5: two equal-power BPSK(10) components in
carrier quadrature, data on I and a dataless pilot on Q, a 1 ms primary period,
and a secondary (overlay) code on each.  So these tests follow `test_l5.py`.
Three things differ, and the tests below look at them first:

* the pilot overlay is 100 ms long, and acquisition does not fold it in, so the
  overlay phase comes from the post-lock search;
* the data symbol lasts 5 ms, so coherent integration stops at 5 ms, not 10 ms;
* the data overlay ``00010`` is one whole symbol long.

All numbers come from the ICD (BDS-SIS-ICD-B2a-1.0) and `utils.bds_b2a`.
"""

from __future__ import annotations

import dataclasses
import warnings

import numpy as np
import pytest

from utils import bds_b2a, bpsk_acquisition, sample_streaming, secondary_code, tracking_channel
from utils.bpsk_correlation import correlate__multicomponent
from utils.nav import bcnav2, symbols
from utils.signal_interfaces import (
    ACQUISITION_POLICIES,
    TRACKING_POLICIES,
    BdsB2a,
    acquisition_code,
    acquisition_resolves_overlay_phase,
    build_acquisition_code_params,
    build_signals,
    coherent_duration_target_ms,
    create_tracking_channels,
)

from . import synthetic

# 10.23 Mcps needs at least ~2 samples per chip.
SAMP_RATE = 25_000_000
PRN = 1
SIGNAL_ID = f"C{PRN:02d}"
SEED_DOPPLER_HZ = 1500.0
SEED_CODE_PHASE_MS = 0.31
# The synthetic advances the overlay once per primary period from code phase 0, so
# the pilot overlay index at code phase c ms is c % 100.  The first interval starts
# at ceil(0.31) = 1 ms.
CORRECT_SEEDED_COUNTER = 1


@pytest.fixture(scope="module")
def definition():
    return build_signals(BdsB2a, prns=[PRN])[SIGNAL_ID]


def _correlate(samples, code_set, code_phase_chips, bins, doppler=0.0):
    out = np.zeros((len(bins), code_set.num_components), dtype=np.complex64)
    correlate__multicomponent(
        samples, SAMP_RATE, 0.0, doppler, code_set,
        bds_b2a.CODE_RATE_CHIPS_PER_SEC, code_phase_chips,
        np.asarray(bins, dtype=np.float64), out,
    )
    return out


# --------------------------------------------------------------------------
# Policies
# --------------------------------------------------------------------------

def test_acquisition_policy_uses_the_pilot_without_the_overlay(definition):
    """OQ-4: pilot only; the 100 ms overlay is not folded into the replica."""
    policy = ACQUISITION_POLICIES["BDS_B2A"]
    assert policy.component_name == "B2aP"
    assert policy.include_overlay is False
    assert policy.ambiguous_component is None
    assert len(acquisition_code(BdsB2a, definition)) == bds_b2a.PRIMARY_CODE_LENGTH
    assert acquisition_resolves_overlay_phase(BdsB2a, definition) is False


def test_tracking_policy_runs_the_carrier_on_the_pilot():
    policy = TRACKING_POLICIES["BDS_B2A"]
    assert policy.discriminator_policy.carrier_component == 1
    assert policy.discriminator_policy.code_components == (0, 1)
    assert policy.discriminator_policy.costas is True
    assert policy.synced_discriminator_policy.carrier_component == 1
    assert policy.synced_discriminator_policy.costas is False


def test_the_data_symbol_caps_coherent_integration_at_5_ms(definition):
    """B2aD's 5 ms symbol, not the 100 ms pilot overlay, sets the ceiling."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert coherent_duration_target_ms(BdsB2a, definition, 5) == 5
        assert coherent_duration_target_ms(BdsB2a, definition, 1) == 1
    with pytest.warns(RuntimeWarning, match="5 ms"):
        assert coherent_duration_target_ms(BdsB2a, definition, 10) == 5


# --------------------------------------------------------------------------
# Signal structure, seen through the correlator
# --------------------------------------------------------------------------

def test_aligned_replica_recovers_both_components_in_quadrature(definition):
    """
    At code phase 0 a clean B2a signal correlates fully on both components, and
    the data and pilot prompts are 90 degrees apart.
    """
    samples = synthetic.generate_b2a_samples(
        prn=PRN, start_sec=0.0, duration_sec=1e-3, samp_rate=SAMP_RATE,
        doppler_hz=0.0, code_phase_ms=0.0, nav_bits=False, overlay=False,
    )
    out = _correlate(samples, definition.code_set, 0.0, [0.0])[0]
    assert np.abs(out[0]) == pytest.approx(np.abs(out[1]), rel=0.01)
    assert np.abs(out[0]) > 0.9 * len(samples)
    relative_phase = np.angle(out[1] / out[0], deg=True)
    assert abs(abs(relative_phase) - 90.0) < 1.0, relative_phase


def test_components_do_not_leak_into_each_other(definition):
    """Data code against a pilot-only signal, and the reverse, gives near zero."""
    samples = synthetic.generate_b2a_samples(
        prn=PRN, start_sec=0.0, duration_sec=1e-3, samp_rate=SAMP_RATE,
        doppler_hz=0.0, code_phase_ms=0.0, nav_bits=False, overlay=False,
    )
    out = _correlate(samples, definition.code_set, 0.0, [0.0])[0]
    for component in (0, 1):
        # A pure-I or pure-Q view of the prompt should hold nearly all of it.
        assert np.abs(out[component]) > 0.9 * len(samples)
    other = build_signals(BdsB2a, prns=[11])["C11"]
    wrong = _correlate(samples, other.code_set, 0.0, [0.0])[0]
    assert np.abs(wrong).max() < 0.1 * np.abs(out).max()


def test_secondary_codes_flip_on_primary_period_boundaries():
    """Overlay and data signs change only at whole milliseconds of code phase."""
    samples = synthetic.generate_b2a_samples(
        prn=PRN, start_sec=0.0, duration_sec=0.012, samp_rate=SAMP_RATE,
        doppler_hz=0.0, code_phase_ms=0.0, nav_bits=True,
    )
    per_ms = SAMP_RATE // 1000
    code_d, code_p = synthetic.get_b2a_codes(PRN)
    sec_d, sec_p = synthetic.get_b2a_overlays(PRN)
    # The same expression as the generator, so chip edges round the same way.
    chips = (np.arange(len(samples)) / SAMP_RATE) * bds_b2a.CODE_RATE_CHIPS_PER_SEC
    chip_index = chips.astype(np.int64)
    period = chip_index // bds_b2a.PRIMARY_CODE_LENGTH
    pilot = samples.imag / (code_p[chip_index % 10230] * sec_p[period % 100])
    assert np.allclose(pilot, 1.0)
    data = samples.real / (code_d[chip_index % 10230] * sec_d[period % 5])
    # What is left of the data is the symbol: constant for five whole periods.
    for symbol_start in range(0, 10, 5):
        block = data[symbol_start * per_ms : (symbol_start + 5) * per_ms]
        assert np.allclose(block, block[0])
    assert data[0] == -data[5 * per_ms]  # the default pattern alternates


# --------------------------------------------------------------------------
# Acquisition
# --------------------------------------------------------------------------

def _acquisition_config(fine_search=None, doppler_hz=(-5000.0, 5000.0)):
    """
    Pilot only, 1 ms replica, ten 1 ms blocks summed non-coherently.

    The replica is one primary period because the 100 ms pilot overlay is not
    folded in.  A 1 ms block holds one whole period, so the overlay can only flip
    the sign between blocks, and the non-coherent sum is blind to that.
    """
    return bpsk_acquisition.AcquisitionConfiguration(
        coherent_duration_replica_ms=1,
        num_blocks=10,
        sample_rate=SAMP_RATE,
        min_search_doppler_hz=doppler_hz[0],
        max_search_doppler_hz=doppler_hz[1],
        fine_search=fine_search,
    )


def _acquire(doppler_hz, code_phase_ms, prn=PRN, noise_sigma=3.0, seed=0, fine=True,
             config=None, samples=None):
    definitions = build_signals(BdsB2a, prns=[prn])
    config = config or _acquisition_config(
        bpsk_acquisition.FineSearchParameters() if fine else None
    )
    if samples is None:
        samples = synthetic.generate_b2a_samples(
            prn=prn, start_sec=0.0, duration_sec=config.acq_total_duration_ms * 1e-3,
            samp_rate=SAMP_RATE, doppler_hz=doppler_hz, code_phase_ms=code_phase_ms,
            noise_sigma=noise_sigma, rng=np.random.default_rng(seed),
        )
    results = bpsk_acquisition.run_acquisition(
        sample_block=samples, sample_block_uptime_epoch_ms=0.0, acq_config=config,
        code_parameters=build_acquisition_code_params(BdsB2a, definitions),
        prob_false_alarm_total=1e-6, noise_var_method="abscorrvar",
    )
    return results[f"C{prn:02d}"]


def test_acquisition_detects_and_locates_the_signal():
    result = _acquire(1234.0, 0.31)
    assert result.signal_detected
    # One sample of code phase.
    assert result.acq_code_phase_seconds * 1e3 == pytest.approx(
        0.31, abs=2e-3 / SAMP_RATE * 1e3 + 1e-6
    )
    # A 1 ms replica gives 1 kHz FFT bins, and the fine search halves them, so the
    # Doppler is right to within 250 Hz.  That is also the edge of what a 1 ms FLL
    # can pull in, which is why tracking is seeded from it.
    assert result.acq_doppler_hz == pytest.approx(1234.0, abs=250.0)


@pytest.mark.parametrize("code_phase_ms", [0.0, 0.5, 0.9])
def test_acquisition_finds_any_code_phase_within_the_period(code_phase_ms):
    result = _acquire(-2750.0, code_phase_ms, seed=2)
    assert result.signal_detected
    found = result.acq_code_phase_seconds * 1e3
    error = min(abs(found - code_phase_ms), abs(abs(found - code_phase_ms) - 1.0))
    assert error < 0.01


def test_acquisition_rejects_an_absent_satellite():
    """A PRN 11 replica must not detect a PRN 1 signal."""
    config = _acquisition_config()
    samples = synthetic.generate_b2a_samples(
        prn=PRN, start_sec=0.0, duration_sec=config.acq_total_duration_ms * 1e-3,
        samp_rate=SAMP_RATE, doppler_hz=800.0, code_phase_ms=0.2,
        noise_sigma=8.0, rng=np.random.default_rng(3),
    )
    present = _acquire(0.0, 0.0, prn=PRN, config=config, samples=samples)
    absent = _acquire(0.0, 0.0, prn=11, config=config, samples=samples)
    assert present.signal_detected, "the transmitted PRN should acquire"
    assert not absent.signal_detected, "an absent PRN should not acquire"
    assert present.normalized_peak_value > 5 * absent.normalized_peak_value


def test_acquisition_is_blind_to_the_overlay_and_the_data():
    """Different data and no overlay give the same answer: pilot sums are sign-blind."""
    plain = _acquire(900.0, 0.4, noise_sigma=0.0)
    for kwargs in (dict(nav_bits=False, overlay=False), dict(nav_bits=True, overlay=True)):
        config = _acquisition_config(bpsk_acquisition.FineSearchParameters())
        samples = synthetic.generate_b2a_samples(
            prn=PRN, start_sec=0.0, duration_sec=config.acq_total_duration_ms * 1e-3,
            samp_rate=SAMP_RATE, doppler_hz=900.0, code_phase_ms=0.4, **kwargs,
        )
        result = _acquire(900.0, 0.4, config=config, samples=samples)
        assert result.signal_detected
        assert result.acq_code_phase_seconds == pytest.approx(
            plain.acq_code_phase_seconds, abs=1e-7
        )


# --------------------------------------------------------------------------
# Overlay search
# --------------------------------------------------------------------------

@pytest.mark.parametrize("offset", [0, 1, 37, 63, 99])
def test_overlay_search_finds_the_pilot_offset(offset):
    """The shipped brute-force search finds every cyclic offset of the 100-chip code."""
    overlay = bds_b2a.generate_pilot_secondary_code(PRN)
    rng = np.random.default_rng(offset)
    n = 4 * len(overlay)
    prompts = overlay[(np.arange(n) + offset) % len(overlay)] * (1.0 + 0.0j)
    prompts = prompts + rng.normal(0.0, 1.0, n)
    found, confidence = secondary_code.brute_force_search(prompts, overlay)
    assert found == offset
    assert confidence > 2.0


def test_every_pilot_overlay_has_a_unique_offset():
    """No cyclic shift of one pilot overlay looks like another: the search is unambiguous."""
    for prn in (1, 17, 33, 63):
        overlay = bds_b2a.generate_pilot_secondary_code(prn).astype(float)
        auto = np.abs(np.fft.ifft(np.fft.fft(overlay) * np.conj(np.fft.fft(overlay))))
        assert auto[0] == pytest.approx(100.0)
        assert auto[1:].max() < 0.5 * auto[0]


# --------------------------------------------------------------------------
# Acquisition handing off to tracking
# --------------------------------------------------------------------------

def _loop_params(coherent_duration_ms=5):
    return tracking_channel.TrackingLoopParameters(
        DLL_bandwidth_hz=2.0, PLL_bandwidth_hz=20.0, FLL_bandwidth_hz=50.0,
        coherent_duration_ms=coherent_duration_ms, EPL_chip_spacing=0.5,
    )


def _acquire_and_track(
    noise_sigma=3.0, buffers=16, buffer_ms=50, seed=4, nav_bits=True, symbol_phase_periods=0
):
    """Acquire, seed a channel from the result, and track `buffers` buffers."""
    definitions = build_signals(BdsB2a, prns=[PRN])
    config = _acquisition_config(bpsk_acquisition.FineSearchParameters())
    rng = np.random.default_rng(seed)
    kwargs = dict(
        prn=PRN, samp_rate=SAMP_RATE, doppler_hz=SEED_DOPPLER_HZ,
        code_phase_ms=SEED_CODE_PHASE_MS, noise_sigma=noise_sigma, nav_bits=nav_bits,
        symbol_phase_periods=symbol_phase_periods,
    )
    acq = bpsk_acquisition.run_acquisition(
        sample_block=synthetic.generate_b2a_samples(
            start_sec=0.0, duration_sec=config.acq_total_duration_ms * 1e-3, rng=rng, **kwargs
        ),
        sample_block_uptime_epoch_ms=0.0, acq_config=config,
        code_parameters=build_acquisition_code_params(BdsB2a, definitions),
        prob_false_alarm_total=1e-6, noise_var_method="abscorrvar",
    )[SIGNAL_ID]
    assert acq.signal_detected

    adapter = create_tracking_channels(
        BdsB2a, signals=definitions, acquisition_results={SIGNAL_ID: acq},
        tracking_signal_ids=[SIGNAL_ID], loop_params=_loop_params(5),
        output_capacity=6000,
    )[SIGNAL_ID]
    for i in range(buffers):
        adapter.process_sample_buffer(sample_streaming.SampleBuffer(
            samples=synthetic.generate_b2a_samples(
                start_sec=i * buffer_ms * 1e-3, duration_sec=buffer_ms * 1e-3,
                rng=rng, **kwargs),
            start_uptime_ms=i * buffer_ms, samp_rate=SAMP_RATE))
    return adapter


@pytest.fixture(scope="module")
def tracked():
    """One acquire-and-track run shared by the tests below (it takes seconds)."""
    return _acquire_and_track()


def test_acquisition_leaves_the_overlay_to_the_post_lock_search(tracked):
    """The channel found the 100-chip overlay itself, and with real confidence."""
    sync = tracked.channel.overlay_sync
    assert sync.synced, "the pilot overlay never locked"
    assert sync.confidence > 2.0
    assert sync.period == 100


def test_tracking_converges_and_locks(tracked):
    outputs = tracked.outputs
    count = outputs.output_index
    assert abs(outputs.doppler_freq_hz[count - 1] - SEED_DOPPLER_HZ) < 5.0
    assert tracked.channel.loop_state.mode is tracking_channel.TrackingLoopMode.PLL


def test_synced_channel_switches_to_pilot_tracking_at_5_ms(tracked):
    channel = tracked.channel
    assert channel.coherent_duration_ms == 5, "accumulation did not extend to the symbol"
    assert channel.policy.costas is False, "the pilot should drop Costas wrapping"
    assert channel.policy.code_components == (0, 1)
    assert channel.loop_params.coherent_duration_ms == 5
    params = channel.loop_params
    for bandwidth_hz in (params.PLL_bandwidth_hz, params.DLL_bandwidth_hz, params.FLL_bandwidth_hz):
        assert bandwidth_hz * params.coherent_duration_ms * 1e-3 <= 0.1 + 1e-9


def test_wipe_off_gives_5_fold_coherent_gain():
    """
    Five coherent periods must multiply the pilot prompt by ~5, not by sqrt(5):
    the latter is what wrong overlay signs would give.
    """
    adapter = _acquire_and_track(noise_sigma=0.0)
    outputs = adapter.outputs
    count = outputs.output_index
    duration = np.asarray(outputs.epoch_duration_ms[:count])
    pilot = np.abs(outputs.prompt_corr[:count, 1])
    first_long = int(np.nonzero(duration == 5.0)[0][0])
    # Short epochs that follow PLL lock, but stay clear of the pull-in.
    short = pilot[(duration == 1.0) & (np.arange(count) > first_long - 40)][-30:-2]
    long_ = pilot[first_long + 2 : count]
    assert len(short) > 10 and len(long_) > 10
    gain = long_.mean() / short.mean()
    assert gain == pytest.approx(5.0, rel=0.05), f"expected ~5x, got {gain:.2f}x"


def test_the_data_component_survives_the_epoch_it_shares(tracked):
    """At 5 ms an epoch is one symbol, so data comes out at the pilot's magnitude."""
    outputs = tracked.outputs
    count = outputs.output_index
    tail = slice(count - 15, count)
    prompt_d = np.abs(outputs.prompt_corr[tail, 0]).mean()
    prompt_p = np.abs(outputs.prompt_corr[tail, 1]).mean()
    assert 0.8 < prompt_d / prompt_p < 1.25, f"{prompt_d:.0f} vs {prompt_p:.0f}"


def test_the_prompt_values_carry_the_data_bits():
    """
    Track a known random symbol sequence, extract the symbols, and match them to
    the transmitted ones (up to the 180 degree carrier ambiguity and the symbol
    index of the first epoch).
    """
    truth = np.where(np.random.default_rng(1).integers(0, 2, 400) == 1, -1.0, 1.0)
    adapter = _acquire_and_track(noise_sigma=0.0, buffers=40, nav_bits=truth)
    stream = symbols.extract(adapter.outputs, "BDS_B2A")

    assert stream.epochs_per_symbol == 1
    assert stream.symbol_period_ms == 5
    assert stream.data_in_quadrature is True
    assert len(stream.values) > 100
    # Data lies on the quadrature axis, so the real part is only noise.
    assert np.abs(stream.values.imag).mean() > 10 * np.abs(stream.values.real).mean()

    # The first symbols follow the switch from Costas to the full-angle pilot
    # discriminator, and the carrier is still settling onto the quadrature axis (the
    # first |imag| is 1.5% of the steady value).  Judge the settled symbols.
    settling_symbols = 3
    hard = np.sign(stream.soft)[settling_symbols:]
    first_symbol = int(round(stream.code_phase_ms[settling_symbols] / bds_b2a.DATA_SYMBOL_PERIOD_MS))
    expected = truth[(first_symbol + np.arange(len(hard))) % len(truth)]
    agreement = np.mean(hard == expected)
    # Either all agree, or all disagree: the carrier has a 180 degree ambiguity.
    assert max(agreement, 1.0 - agreement) == 1.0


# --------------------------------------------------------------------------
# Overlay phase supplied by a seed
# --------------------------------------------------------------------------

def _seeded_channel(initial_overlay_counter):
    signal = build_signals(BdsB2a, prns=[PRN])[SIGNAL_ID]
    policy = TRACKING_POLICIES["BDS_B2A"]
    return tracking_channel.TrackingChannel(
        loop_params=dataclasses.replace(_loop_params(1)),
        signal_params=tracking_channel.TrackingSignalParameters(
            code_set=signal.code_set,
            nominal_code_rate_chips_per_sec=bds_b2a.CODE_RATE_CHIPS_PER_SEC,
            carrier_freq_hz=bds_b2a.CARRIER_FREQ_HZ, primary_period_ms=1,
        ),
        initial_signal_state=tracking_channel.TrackingSignalState(
            uptime_epoch_ms=0.0, code_phase_ms=SEED_CODE_PHASE_MS,
            code_rate_ms_per_sec=(1.0 + SEED_DOPPLER_HZ / bds_b2a.CARRIER_FREQ_HZ) * 1e3,
            carrier_phase_cycles=0.0, carrier_rate_cyc_per_sec=SEED_DOPPLER_HZ,
        ),
        output_capacity=4000,
        discriminator_policy=policy.discriminator_policy,
        synced_policy=policy.synced_discriminator_policy,
        synced_coherent_duration_ms=coherent_duration_target_ms(BdsB2a, signal, 5),
        initial_overlay_counter=initial_overlay_counter,
    )


def _drive(channel, buffers=14, buffer_ms=50):
    rng = np.random.default_rng(4)
    for i in range(buffers):
        channel.process_sample_buffer(sample_streaming.SampleBuffer(
            samples=synthetic.generate_b2a_samples(
                prn=PRN, start_sec=i * buffer_ms * 1e-3, duration_sec=buffer_ms * 1e-3,
                samp_rate=SAMP_RATE, doppler_hz=SEED_DOPPLER_HZ,
                code_phase_ms=SEED_CODE_PHASE_MS, noise_sigma=3.0, rng=rng),
            start_uptime_ms=i * buffer_ms, samp_rate=SAMP_RATE))


def test_a_correct_seeded_counter_locks_and_extends():
    channel = _seeded_channel(CORRECT_SEEDED_COUNTER)
    assert channel.overlay_sync.synced
    assert channel.coherent_duration_ms == 1, "must not extend before the PLL locks"
    _drive(channel)
    assert channel.loop_state.mode is tracking_channel.TrackingLoopMode.PLL
    assert channel.coherent_duration_ms == 5


def test_a_wrong_seeded_counter_never_locks():
    """Guards the counter convention: an off-by-seven pilot overlay must fail."""
    wrong = _seeded_channel((CORRECT_SEEDED_COUNTER + 7) % 100)
    _drive(wrong)
    assert wrong.coherent_duration_ms == 1
    assert wrong.loop_state.mode is tracking_channel.TrackingLoopMode.FLL


# --------------------------------------------------------------------------
# Tables that other modules keep per signal type
# --------------------------------------------------------------------------

def test_symbol_and_band_tables_describe_b2a():
    """`nav.symbols` and `observables` know the signal, and agree with its definition."""
    from utils import observables

    signal = BdsB2a(PRN)
    data_component = signal.code_set.components[symbols.DATA_COMPONENT["BDS_B2A"]]
    assert data_component.name == "B2aD"
    assert data_component.symbol_period_ms == symbols.SYMBOL_PERIOD_MS["BDS_B2A"] == 5
    assert symbols.NEEDS_BIT_SYNC["BDS_B2A"] is False
    assert symbols.HAS_OVERLAY["BDS_B2A"] is True
    # The data is in quadrature to the carrier reference exactly when the carrier
    # runs on the pilot and the pilot is on the other branch.
    carrier = TRACKING_POLICIES["BDS_B2A"].discriminator_policy.carrier_component
    assert symbols.DATA_IN_QUADRATURE["BDS_B2A"] is (
        signal.code_set.components[carrier].branch != data_component.branch
    )
    assert observables.BAND_FREQ_HZ["BDS_B2A"] == BdsB2a.carrier_freq_hz


# --------------------------------------------------------------------------
# Where a symbol starts on the code phase lattice
# --------------------------------------------------------------------------

@pytest.mark.parametrize("shift", [0, 1, 2, 3, 4])
def test_epochs_start_on_the_true_symbol_boundary_whatever_the_lattice_phase(shift):
    """
    Acquisition fixes B2a's code phase only modulo 1 ms, so the symbol boundary is
    anywhere on the 5 ms code phase lattice.  The tracker found it by the overlay
    counter: with the symbols slid by `shift` periods the 5 ms epochs must start at
    code phase = -shift (mod 5) and the data component must come out at the pilot's
    full magnitude.  Anchoring on code phase multiples of 5 ms instead puts every
    epoch `shift` ms into a symbol, and the data loses amplitude on every transition.
    """
    adapter = _acquire_and_track(noise_sigma=0.0, buffers=16, symbol_phase_periods=shift)
    outputs = adapter.outputs
    count = outputs.output_index
    duration = np.asarray(outputs.epoch_duration_ms[:count])
    long_epochs = np.nonzero(duration == 5.0)[0]
    assert len(long_epochs) > 20, "the channel never extended to 5 ms epochs"
    phases = np.round(np.asarray(outputs.code_phase_ms[:count])[long_epochs]).astype(int)
    assert np.all(phases % 5 == (-shift) % 5), (shift, np.unique(phases % 5))
    tail = slice(count - 15, count)
    ratio = np.abs(outputs.prompt_corr[tail, 0]).mean() / np.abs(outputs.prompt_corr[tail, 1]).mean()
    assert 0.9 < ratio < 1.1, f"shift {shift}: data/pilot magnitude {ratio:.2f}"


@pytest.mark.parametrize("shift", [0, 3])
def test_time_anchor_of_a_decoded_frame_is_exact_at_any_symbol_phase(shift):
    """
    The decoded SOW names the frame's first symbol, so the code phase there must be
    the code phase at which that symbol truly starts.  A grid that is `shift` ms off
    would put the anchor, and every pseudorange built on it, `shift` ms (300 km each)
    out.
    """
    from utils import observables

    frames = [_nav_frame(10, 5000), _nav_frame(11, 5001)]
    first_frame_symbol = 120
    nav_symbols = np.roll(bcnav2.soft_symbols(np.concatenate(frames)), first_frame_symbol)
    adapter = _acquire_and_track(
        noise_sigma=2.0, buffers=76, nav_bits=nav_symbols, symbol_phase_periods=shift
    )
    stream = symbols.extract(adapter.outputs, "BDS_B2A")
    result = bcnav2.decode(stream.soft, expected_prn=PRN)
    assert result.synced
    anchor = observables.anchor_from_bcnav2(stream, result.frames[0])
    # Symbol 120 starts at primary period 600 - shift: the period index runs `shift`
    # ahead of the code phase label.
    assert anchor.code_phase_ms == pytest.approx(first_frame_symbol * 5.0 - shift, abs=0.01)
    assert anchor.tow_s == 5000 * 3.0


def _nav_frame(message_type: int, sow_count: int) -> np.ndarray:
    return bcnav2.encode_frame(
        bcnav2.build_message(prn=PRN, message_type=message_type, sow_count=sow_count)
    )
