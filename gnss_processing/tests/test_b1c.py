"""
BeiDou B1C acquisition and tracking on synthetic signals (narrow-band model).

B1C is a data component on I and the BOC(1,1) part of the pilot on Q, both at
1.023 Mcps with a 10 ms primary period.  The pilot has a 1800-chip overlay (18 s)
and the data has none.  So these tests follow `test_b2a.py` for the I/Q
structure and the L1C design for the long overlay (FFT search, 200 prompts).
GPS L1C itself cannot be built from the current gnss-tools checkout (the
baseline), so nothing here depends on it.

All numbers come from the ICD (BDS-SIS-ICD-B1C-1.0) and `utils.bds_b1c`.
"""

from __future__ import annotations

import warnings

import numpy as np
import pytest

from utils import bds_b1c, bpsk_acquisition, sample_streaming, secondary_code, tracking_channel
from utils.bpsk_correlation import correlate__multicomponent
from utils.nav import symbols
from utils.signal_interfaces import (
    ACQUISITION_POLICIES,
    TRACKING_POLICIES,
    BdsB1C,
    acquisition_code,
    acquisition_resolves_overlay_phase,
    build_acquisition_code_params,
    build_signals,
    coherent_duration_target_ms,
    create_tracking_channels,
)

from . import synthetic

# The BOC(1,1) lobes need about 4 Msps; 8 Msps is the rate of the course test set.
SAMP_RATE = 8_000_000
PRN = 1
SIGNAL_ID = f"C{PRN:02d}"
SEED_DOPPLER_HZ = 1500.0
SEED_CODE_PHASE_MS = 3.17


@pytest.fixture(scope="module")
def definition():
    return build_signals(BdsB1C, prns=[PRN])[SIGNAL_ID]


def _correlate(samples, code_set, code_phase_chips, bins, doppler=0.0):
    out = np.zeros((len(bins), code_set.num_components), dtype=np.complex64)
    correlate__multicomponent(
        samples, SAMP_RATE, 0.0, doppler, code_set,
        bds_b1c.CODE_RATE_CHIPS_PER_SEC, code_phase_chips,
        np.asarray(bins, dtype=np.float64), out,
    )
    return out


# --------------------------------------------------------------------------
# Policies
# --------------------------------------------------------------------------

def test_acquisition_policy_uses_the_pilot_without_the_overlay(definition):
    policy = ACQUISITION_POLICIES["BDS_B1C"]
    assert policy.component_name == "B1CP"
    assert policy.include_overlay is False
    assert policy.ambiguous_component is None
    assert len(acquisition_code(BdsB1C, definition)) == bds_b1c.PRIMARY_CODE_LENGTH
    assert acquisition_resolves_overlay_phase(BdsB1C, definition) is False


def test_tracking_policy_runs_the_carrier_on_the_pilot_with_the_fft_search():
    policy = TRACKING_POLICIES["BDS_B1C"]
    assert policy.discriminator_policy.carrier_component == 1
    assert policy.discriminator_policy.code_components == (0, 1)
    assert policy.discriminator_policy.costas is True
    assert policy.synced_discriminator_policy.carrier_component == 1
    assert policy.synced_discriminator_policy.costas is False
    assert policy.overlay_search is secondary_code.fft_search
    assert policy.overlay_prompts_to_observe == 200


def test_the_data_symbol_caps_coherent_integration_at_10_ms(definition):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert coherent_duration_target_ms(BdsB1C, definition, 10) == 10


# --------------------------------------------------------------------------
# Signal structure, seen through the correlator
# --------------------------------------------------------------------------

def test_aligned_replica_recovers_both_components_in_quadrature(definition):
    """The synthetic and the correlator agree on the BOC(1,1) sign convention."""
    samples = synthetic.generate_b1c_samples(
        prn=PRN, start_sec=0.0, duration_sec=10e-3, samp_rate=SAMP_RATE,
        doppler_hz=0.0, code_phase_ms=0.0, nav_bits=False, overlay=False,
    )
    out = _correlate(samples, definition.code_set, 0.0, [0.0])[0]
    n = len(samples)
    # Amplitudes are sqrt(1/4) and sqrt(29/44) of the unit-power total.
    assert np.abs(out[0]) == pytest.approx(0.5 * n, rel=0.02)
    assert np.abs(out[1]) == pytest.approx(np.sqrt(29 / 44) * n, rel=0.02)
    relative_phase = np.angle(out[1] / out[0], deg=True)
    # Not exactly 90: the other component's code correlates at ~4% of the peak (the
    # cross-correlation bound), and the pilot is 1.6 times stronger than the data.
    assert abs(abs(relative_phase) - 90.0) < 5.0, relative_phase


def test_a_cosine_phased_replica_would_not_match(definition):
    """Guards the convention: the ICD subcarrier is sine-phased (first half-chip positive)."""
    from dataclasses import replace

    from utils.code_components import SubcarrierKind

    cosine = [
        replace(c, subcarrier=replace(c.subcarrier, kind=SubcarrierKind.BOC_COS))
        for c in definition.code_set.components
    ]
    from utils.code_components import build_code_set

    wrong_set = build_code_set(cosine, chip_rate_hz=bds_b1c.CODE_RATE_CHIPS_PER_SEC)
    samples = synthetic.generate_b1c_samples(
        prn=PRN, start_sec=0.0, duration_sec=10e-3, samp_rate=SAMP_RATE,
        doppler_hz=0.0, code_phase_ms=0.0, nav_bits=False, overlay=False,
    )
    right = np.abs(_correlate(samples, definition.code_set, 0.0, [0.0])[0, 1])
    wrong = np.abs(_correlate(samples, wrong_set, 0.0, [0.0])[0, 1])
    assert wrong < 0.2 * right


def test_components_do_not_leak_into_other_satellites(definition):
    samples = synthetic.generate_b1c_samples(
        prn=PRN, start_sec=0.0, duration_sec=10e-3, samp_rate=SAMP_RATE,
        doppler_hz=0.0, code_phase_ms=0.0, nav_bits=False, overlay=False,
    )
    right = _correlate(samples, definition.code_set, 0.0, [0.0])[0]
    other = build_signals(BdsB1C, prns=[11])["C11"]
    wrong = _correlate(samples, other.code_set, 0.0, [0.0])[0]
    assert np.abs(wrong).max() < 0.1 * np.abs(right).max()


def test_overlay_and_data_flip_only_on_primary_period_boundaries():
    """Sign changes fall at whole 10 ms of code phase, and the data has no overlay."""
    code_d, code_p = synthetic.get_b1c_codes(PRN)
    overlay = bds_b1c.generate_pilot_secondary_code(PRN)
    samples = synthetic.generate_b1c_samples(
        prn=PRN, start_sec=0.0, duration_sec=0.03, samp_rate=SAMP_RATE,
        doppler_hz=0.0, code_phase_ms=0.0, nav_bits=True,
    )
    chips = (np.arange(len(samples)) / SAMP_RATE) * bds_b1c.CODE_RATE_CHIPS_PER_SEC
    chip_index = chips.astype(np.int64)
    period = chip_index // bds_b1c.PRIMARY_CODE_LENGTH
    within = chips - chip_index
    boc = np.where((within * 2).astype(np.int64) % 2 == 1, -1.0, 1.0)
    pilot = samples.imag / (np.sqrt(29 / 44) * code_p[chip_index % 10230] * boc * overlay[period])
    assert np.allclose(pilot, 1.0, atol=1e-5)
    data = samples.real / (0.5 * code_d[chip_index % 10230] * boc)
    for p in range(3):
        block = data[period == p]
        assert np.allclose(block, block[0], atol=1e-5)
    assert data[period == 0][0] == -data[period == 1][0]  # the default pattern alternates


# --------------------------------------------------------------------------
# Acquisition
# --------------------------------------------------------------------------

def _acquisition_config(fine_search=None, doppler_hz=(-5000.0, 5000.0)):
    """Pilot only, a 10 ms replica (one primary period), four blocks summed."""
    return bpsk_acquisition.AcquisitionConfiguration(
        coherent_duration_replica_ms=10,
        num_blocks=4,
        sample_rate=SAMP_RATE,
        min_search_doppler_hz=doppler_hz[0],
        max_search_doppler_hz=doppler_hz[1],
        fine_search=fine_search,
    )


def _acquire(doppler_hz, code_phase_ms, prn=PRN, noise_sigma=3.0, seed=0, fine=True,
             config=None, samples=None):
    definitions = build_signals(BdsB1C, prns=[prn])
    config = config or _acquisition_config(
        bpsk_acquisition.FineSearchParameters() if fine else None
    )
    if samples is None:
        samples = synthetic.generate_b1c_samples(
            prn=prn, start_sec=0.0, duration_sec=config.acq_total_duration_ms * 1e-3,
            samp_rate=SAMP_RATE, doppler_hz=doppler_hz, code_phase_ms=code_phase_ms,
            noise_sigma=noise_sigma, rng=np.random.default_rng(seed),
        )
    results = bpsk_acquisition.run_acquisition(
        sample_block=samples, sample_block_uptime_epoch_ms=0.0, acq_config=config,
        code_parameters=build_acquisition_code_params(BdsB1C, definitions),
        prob_false_alarm_total=1e-6, noise_var_method="abscorrvar",
    )
    return results[f"C{prn:02d}"]


def test_acquisition_detects_and_locates_the_signal():
    result = _acquire(1234.0, 3.17)
    assert result.signal_detected
    # Within one sample of code phase.
    assert result.acq_code_phase_seconds * 1e3 == pytest.approx(3.17, abs=2e-3 / SAMP_RATE * 1e3 + 1e-6)
    # A 10 ms replica gives 100 Hz bins; the fine search halves them.
    assert result.acq_doppler_hz == pytest.approx(1234.0, abs=100.0)


@pytest.mark.parametrize("code_phase_ms", [0.0, 4.5, 9.9])
def test_acquisition_finds_any_code_phase_within_the_period(code_phase_ms):
    result = _acquire(-2750.0, code_phase_ms, seed=2)
    assert result.signal_detected
    found = result.acq_code_phase_seconds * 1e3
    error = min(abs(found - code_phase_ms), abs(abs(found - code_phase_ms) - 10.0))
    assert error < 0.01


def test_acquisition_rejects_an_absent_satellite():
    config = _acquisition_config()
    samples = synthetic.generate_b1c_samples(
        prn=PRN, start_sec=0.0, duration_sec=config.acq_total_duration_ms * 1e-3,
        samp_rate=SAMP_RATE, doppler_hz=800.0, code_phase_ms=2.2,
        noise_sigma=8.0, rng=np.random.default_rng(3),
    )
    present = _acquire(0.0, 0.0, prn=PRN, config=config, samples=samples)
    absent = _acquire(0.0, 0.0, prn=11, config=config, samples=samples)
    assert present.signal_detected, "the transmitted PRN should acquire"
    assert not absent.signal_detected, "an absent PRN should not acquire"
    assert present.normalized_peak_value > 5 * absent.normalized_peak_value


def test_acquisition_is_blind_to_the_overlay_and_the_data():
    plain = _acquire(900.0, 4.4, noise_sigma=0.0)
    for kwargs in (dict(nav_bits=False, overlay=False), dict(nav_bits=True, overlay=True)):
        config = _acquisition_config(bpsk_acquisition.FineSearchParameters())
        samples = synthetic.generate_b1c_samples(
            prn=PRN, start_sec=0.0, duration_sec=config.acq_total_duration_ms * 1e-3,
            samp_rate=SAMP_RATE, doppler_hz=900.0, code_phase_ms=4.4, **kwargs,
        )
        result = _acquire(900.0, 4.4, config=config, samples=samples)
        assert result.signal_detected
        assert result.acq_code_phase_seconds == pytest.approx(
            plain.acq_code_phase_seconds, abs=1e-7
        )


# --------------------------------------------------------------------------
# Overlay search
# --------------------------------------------------------------------------

@pytest.mark.parametrize("offset", [0, 1, 777, 1799])
def test_the_fft_search_finds_the_pilot_offset(offset):
    """The shipped FFT search finds cyclic offsets of the 1800-chip code in 200 prompts."""
    overlay = bds_b1c.generate_pilot_secondary_code(PRN)
    rng = np.random.default_rng(offset)
    n = 200
    prompts = overlay[(np.arange(n) + offset) % len(overlay)] * (1.0 + 0.0j)
    prompts = prompts + rng.normal(0.0, 1.0, n)
    found, confidence = secondary_code.fft_search(prompts, overlay)
    assert found == offset
    assert confidence > 2.0


# --------------------------------------------------------------------------
# Acquisition handing off to tracking
# --------------------------------------------------------------------------

def _loop_params(coherent_duration_ms=10):
    return tracking_channel.TrackingLoopParameters(
        DLL_bandwidth_hz=0.5, PLL_bandwidth_hz=20.0, FLL_bandwidth_hz=50.0,
        coherent_duration_ms=coherent_duration_ms, EPL_chip_spacing=0.5,
    )


def _acquire_and_track(noise_sigma=3.0, buffers=40, buffer_ms=100, seed=4, nav_bits=True, loop_params=None):
    """Acquire, seed a channel from the result, and track `buffers` buffers."""
    definitions = build_signals(BdsB1C, prns=[PRN])
    config = _acquisition_config(bpsk_acquisition.FineSearchParameters())
    rng = np.random.default_rng(seed)
    kwargs = dict(
        prn=PRN, samp_rate=SAMP_RATE, doppler_hz=SEED_DOPPLER_HZ,
        code_phase_ms=SEED_CODE_PHASE_MS, noise_sigma=noise_sigma, nav_bits=nav_bits,
    )
    acq = bpsk_acquisition.run_acquisition(
        sample_block=synthetic.generate_b1c_samples(
            start_sec=0.0, duration_sec=config.acq_total_duration_ms * 1e-3, rng=rng, **kwargs
        ),
        sample_block_uptime_epoch_ms=0.0, acq_config=config,
        code_parameters=build_acquisition_code_params(BdsB1C, definitions),
        prob_false_alarm_total=1e-6, noise_var_method="abscorrvar",
    )[SIGNAL_ID]
    assert acq.signal_detected

    with warnings.catch_warnings():
        # The handover to 10 ms narrows the loops; that is the intended behaviour.
        warnings.simplefilter("ignore", RuntimeWarning)
        adapter = create_tracking_channels(
            BdsB1C, signals=definitions, acquisition_results={SIGNAL_ID: acq},
            tracking_signal_ids=[SIGNAL_ID], loop_params=loop_params or _loop_params(),
            output_capacity=6000,
        )[SIGNAL_ID]
    for i in range(buffers):
        adapter.process_sample_buffer(sample_streaming.SampleBuffer(
            samples=synthetic.generate_b1c_samples(
                start_sec=i * buffer_ms * 1e-3, duration_sec=buffer_ms * 1e-3,
                rng=rng, **kwargs),
            start_uptime_ms=i * buffer_ms, samp_rate=SAMP_RATE))
    return adapter


@pytest.fixture(scope="module")
def tracked():
    """One acquire-and-track run shared by the tests below (it takes seconds)."""
    return _acquire_and_track()


def test_the_channel_finds_the_1800_chip_overlay_itself(tracked):
    sync = tracked.channel.overlay_sync
    assert sync.synced, "the pilot overlay never locked"
    assert sync.confidence > 2.0
    assert sync.period == 1800


def test_tracking_converges_and_locks(tracked):
    outputs = tracked.outputs
    count = outputs.output_index
    assert abs(outputs.doppler_freq_hz[count - 1] - SEED_DOPPLER_HZ) < 5.0
    assert tracked.channel.loop_state.mode is tracking_channel.TrackingLoopMode.PLL


def test_the_code_phase_estimate_is_unbiased_in_a_clean_channel():
    """The BOC(1,1) delay loop must sit on the main peak, not on a side peak."""
    adapter = _acquire_and_track(noise_sigma=0.0, buffers=12)
    outputs = adapter.outputs
    count = outputs.output_index
    code_phase = np.asarray(outputs.code_phase_ms[:count])
    uptime = np.asarray(outputs.uptime_epoch_ms[:count])
    # Doppler 1500 Hz stretches the code by 1500 / 1575.42e6 of the elapsed time.
    truth = SEED_CODE_PHASE_MS + uptime * (1.0 + SEED_DOPPLER_HZ / bds_b1c.CARRIER_FREQ_HZ)
    error_chips = (code_phase[-20:] - truth[-20:]) * 1.023
    assert np.abs(error_chips).max() < 0.02
    # With no noise the prompts also show the ICD power split: data and pilot differ
    # by sqrt((1/4) / (29/44)) = 0.616, with the data in quadrature.
    tail = slice(count - 15, count)
    ratio = np.abs(outputs.prompt_corr[tail, 0]).mean() / np.abs(outputs.prompt_corr[tail, 1]).mean()
    assert ratio == pytest.approx(np.sqrt((1 / 4) / (29 / 44)), rel=0.03)


def _double_estimator_loop_params(subcarrier_chip_spacing):
    """The loops of notebook 01 for B1C: a code loop plus a subcarrier loop (the BOC discriminator)."""
    return tracking_channel.TrackingLoopParameters(
        DLL_bandwidth_hz=0.5, PLL_bandwidth_hz=20.0, FLL_bandwidth_hz=50.0,
        coherent_duration_ms=10, EPL_chip_spacing=0.5,
        subcarrier_bandwidth_hz=2.0, subcarrier_chip_spacing=subcarrier_chip_spacing,
    )


@pytest.mark.parametrize("subcarrier_chip_spacing", [0.1, 0.2])
def test_the_boc_discriminator_tracks_b1c_on_the_main_peak(subcarrier_chip_spacing):
    """
    With the double estimator (a code loop and a subcarrier loop, as for GPS L1C) the
    BOC(1,1) channel locks, finds the overlay, and sits on the main peak.  The code
    taps stay 0.5 chip apart and only the subcarrier taps are tight.
    """
    adapter = _acquire_and_track(
        noise_sigma=0.0, buffers=14, loop_params=_double_estimator_loop_params(subcarrier_chip_spacing)
    )
    assert adapter.channel.signal_params.subcarrier_ambiguity_chips == 0.5
    outputs = adapter.outputs
    count = outputs.output_index
    layout = outputs.tap_layout
    assert layout.tracks_subcarrier
    assert max(abs(o) for o in layout.code_offsets_chips) == pytest.approx(0.5)
    assert max(abs(o) for o in layout.subcarrier_offsets_chips) == pytest.approx(subcarrier_chip_spacing)
    code_phase = np.asarray(outputs.code_phase_ms[:count])
    uptime = np.asarray(outputs.uptime_epoch_ms[:count])
    truth = SEED_CODE_PHASE_MS + uptime * (1.0 + SEED_DOPPLER_HZ / bds_b1c.CARRIER_FREQ_HZ)
    assert np.abs((code_phase[-20:] - truth[-20:]) * 1.023).max() < 0.02
    assert np.abs(np.asarray(outputs.subcarrier_offset_chips[count - 20:count])).max() < 0.02
    assert adapter.channel.loop_state.mode is tracking_channel.TrackingLoopMode.PLL


def test_the_prompt_values_carry_the_data_bits():
    """Track a known random symbol sequence and match the extracted symbols."""
    truth = np.where(np.random.default_rng(1).integers(0, 2, 600) == 1, -1.0, 1.0)
    adapter = _acquire_and_track(noise_sigma=0.0, buffers=60, nav_bits=truth)
    stream = symbols.extract(adapter.outputs, "BDS_B1C")

    assert stream.epochs_per_symbol == 1
    assert stream.symbol_period_ms == 10
    assert stream.data_in_quadrature is True
    assert len(stream.values) > 100
    assert np.abs(stream.values.imag).mean() > 10 * np.abs(stream.values.real).mean()

    settling_symbols = 3
    hard = np.sign(stream.soft)[settling_symbols:]
    first_symbol = int(round(stream.code_phase_ms[settling_symbols] / bds_b1c.DATA_SYMBOL_PERIOD_MS))
    expected = truth[(first_symbol + np.arange(len(hard))) % len(truth)]
    agreement = np.mean(hard == expected)
    # Either all agree, or all disagree: the carrier has a 180 degree ambiguity.
    assert max(agreement, 1.0 - agreement) == 1.0


def test_the_unmodelled_boc61_part_costs_little():
    """
    Narrow-band mode ignores the pilot's BOC(6,1) part (1/11 of the power, D-062).
    With that part in the signal the channel must still lock and sync.
    """
    # The BOC(6,1) part needs a sample rate above ~14 Msps, so this check builds its
    # own, shorter run at 16 Msps.
    rate = 16_000_000
    definitions = build_signals(BdsB1C, prns=[PRN])
    config = bpsk_acquisition.AcquisitionConfiguration(
        coherent_duration_replica_ms=10, num_blocks=4, sample_rate=rate,
        min_search_doppler_hz=-5000.0, max_search_doppler_hz=5000.0,
        fine_search=bpsk_acquisition.FineSearchParameters(),
    )
    rng = np.random.default_rng(5)
    kwargs = dict(
        prn=PRN, samp_rate=rate, doppler_hz=SEED_DOPPLER_HZ, code_phase_ms=SEED_CODE_PHASE_MS,
        noise_sigma=4.0, include_boc61=True,
    )
    acq = bpsk_acquisition.run_acquisition(
        sample_block=synthetic.generate_b1c_samples(
            start_sec=0.0, duration_sec=config.acq_total_duration_ms * 1e-3, rng=rng, **kwargs
        ),
        sample_block_uptime_epoch_ms=0.0, acq_config=config,
        code_parameters=build_acquisition_code_params(BdsB1C, definitions),
        prob_false_alarm_total=1e-6, noise_var_method="abscorrvar",
    )[SIGNAL_ID]
    assert acq.signal_detected
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        adapter = create_tracking_channels(
            BdsB1C, signals=definitions, acquisition_results={SIGNAL_ID: acq},
            tracking_signal_ids=[SIGNAL_ID], loop_params=_loop_params(), output_capacity=6000,
        )[SIGNAL_ID]
    for i in range(30):
        adapter.process_sample_buffer(sample_streaming.SampleBuffer(
            samples=synthetic.generate_b1c_samples(
                start_sec=i * 0.1, duration_sec=0.1, rng=rng, **kwargs),
            start_uptime_ms=i * 100.0, samp_rate=rate))
    outputs = adapter.outputs
    count = outputs.output_index
    assert adapter.channel.loop_state.mode is tracking_channel.TrackingLoopMode.PLL
    assert abs(outputs.doppler_freq_hz[count - 1] - SEED_DOPPLER_HZ) < 5.0


# --------------------------------------------------------------------------
# Tables that other modules keep per signal type
# --------------------------------------------------------------------------

def test_symbol_and_band_tables_describe_b1c():
    from utils import observables

    signal = BdsB1C(PRN)
    data_component = signal.code_set.components[symbols.DATA_COMPONENT["BDS_B1C"]]
    assert data_component.name == "B1CD"
    assert data_component.symbol_period_ms == symbols.SYMBOL_PERIOD_MS["BDS_B1C"] == 10
    assert symbols.NEEDS_BIT_SYNC["BDS_B1C"] is False
    assert symbols.HAS_OVERLAY["BDS_B1C"] is True
    carrier = TRACKING_POLICIES["BDS_B1C"].discriminator_policy.carrier_component
    assert symbols.DATA_IN_QUADRATURE["BDS_B1C"] is (
        signal.code_set.components[carrier].branch != data_component.branch
    )
    assert observables.BAND_FREQ_HZ["BDS_B1C"] == BdsB1C.carrier_freq_hz
