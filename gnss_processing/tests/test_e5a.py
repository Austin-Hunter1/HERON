"""
Galileo E5a acquisition and tracking on synthetic signals.

E5a has the structure of BeiDou B2a and GPS L5: two equal-power BPSK(10) components in carrier
quadrature, data on I and a dataless pilot on Q, a 1 ms primary period, and a secondary code on
each.  So these tests follow `test_b2a.py`.  What differs, and what is looked at first:

* the data overlay CS20_1 (20 chips) is exactly one 20 ms symbol, so coherent integration stops
  at 20 ms (B2a: 5 ms);
* the pilot overlay is 100 ms, as for B2a, and acquisition does not fold it in;
* E5a acquires only modulo 1 ms, so the symbol edge can sit anywhere on the 20 ms code phase
  lattice (B2a: 5 ms).  The symbol-grid sweep here is 0 to 19 ms.

All numbers come from the ICD notes and `utils.gal_e5a`.
"""

from __future__ import annotations

import warnings

import numpy as np
import pytest

from utils import bpsk_acquisition, gal_e5a, sample_streaming, secondary_code, tracking_channel
from utils.bpsk_correlation import correlate__multicomponent
from utils.nav import symbols
from utils.signal_interfaces import (
    ACQUISITION_POLICIES,
    SIGNALS,
    TRACKING_POLICIES,
    GalE5a,
    acquisition_code,
    acquisition_resolves_overlay_phase,
    build_acquisition_code_params,
    build_signals,
    coherent_duration_target_ms,
    create_tracking_channels,
)

from . import synthetic

SAMP_RATE = 25_000_000
PRN = 1
SIGNAL_ID = f"E{PRN:02d}"
SEED_DOPPLER_HZ = 1500.0
SEED_CODE_PHASE_MS = 0.31


@pytest.fixture(scope="module")
def definition():
    return build_signals(GalE5a, prns=[PRN])[SIGNAL_ID]


def _correlate(samples, code_set, code_phase_chips, bins):
    out = np.zeros((len(bins), code_set.num_components), dtype=np.complex64)
    correlate__multicomponent(
        samples, SAMP_RATE, 0.0, 0.0, code_set, gal_e5a.CODE_RATE_CHIPS_PER_SEC,
        code_phase_chips, np.asarray(bins, dtype=np.float64), out,
    )
    return out


# --------------------------------------------------------------------------
# The signal definition and the policies
# --------------------------------------------------------------------------

def test_the_signal_is_registered_with_its_constants():
    assert SIGNALS["GAL_E5A"] is GalE5a
    assert GalE5a.carrier_freq_hz == 1176.45e6 and GalE5a.primary_period_ms == 1
    assert GalE5a.sat_id_prefix == "E"
    assert GalE5a.default_prns == tuple(range(1, 37))
    assert GalE5a.link.id.value == "L5"
    assert set(build_signals(GalE5a, prns=[7, 50])) == {"E07", "E50"}


def test_components_follow_the_icd_structure(definition):
    data, pilot = GalE5a(7).code_set.components
    assert (data.name, pilot.name) == ("E5aI", "E5aQ")
    assert (data.branch.value, pilot.branch.value) == ("I", "Q")
    assert data.symbol_period_ms == 20 and pilot.symbol_period_ms is None
    assert np.array_equal(data.overlay, gal_e5a.data_secondary_code())
    assert np.array_equal(pilot.overlay, gal_e5a.generate_pilot_secondary_code(7))
    assert len(data.overlay) == 20 and len(pilot.overlay) == 100
    assert data.subcarrier is None and pilot.subcarrier is None
    assert data.power_weight == pilot.power_weight  # equal power


def test_acquisition_policy_uses_the_pilot_without_the_overlay(definition):
    policy = ACQUISITION_POLICIES["GAL_E5A"]
    assert policy.component_name == "E5aQ" and policy.include_overlay is False
    assert policy.ambiguous_component is None
    assert len(acquisition_code(GalE5a, definition)) == 10230
    assert acquisition_resolves_overlay_phase(GalE5a, definition) is False


def test_tracking_policy_runs_the_carrier_on_the_pilot():
    policy = TRACKING_POLICIES["GAL_E5A"]
    assert policy.discriminator_policy.carrier_component == 1
    assert policy.discriminator_policy.code_components == (0, 1)
    assert policy.discriminator_policy.costas is True
    assert policy.synced_discriminator_policy.costas is False


def test_the_data_symbol_caps_coherent_integration_at_20_ms(definition):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert coherent_duration_target_ms(GalE5a, definition, 20) == 20
        assert coherent_duration_target_ms(GalE5a, definition, 10) == 10
    with pytest.warns(RuntimeWarning, match="20 ms"):
        assert coherent_duration_target_ms(GalE5a, definition, 40) == 20


# --------------------------------------------------------------------------
# Signal structure, seen through the correlator
# --------------------------------------------------------------------------

def test_aligned_replica_recovers_both_components_in_quadrature(definition):
    samples = synthetic.generate_e5a_samples(
        prn=PRN, start_sec=0.0, duration_sec=1e-3, samp_rate=SAMP_RATE,
        doppler_hz=0.0, code_phase_ms=0.0, nav_bits=False, overlay=False,
    )
    out = _correlate(samples, definition.code_set, 0.0, [0.0])[0]
    assert np.abs(out[0]) == pytest.approx(np.abs(out[1]), rel=0.01)
    assert np.abs(out[0]) > 0.9 * len(samples)
    # The other code correlates at about 1 % of the peak, which tilts the angle by about a degree.
    assert abs(abs(np.angle(out[1] / out[0], deg=True)) - 90.0) < 3.0


def test_components_do_not_leak_into_other_satellites(definition):
    samples = synthetic.generate_e5a_samples(
        prn=PRN, start_sec=0.0, duration_sec=1e-3, samp_rate=SAMP_RATE,
        doppler_hz=0.0, code_phase_ms=0.0, nav_bits=False, overlay=False,
    )
    right = _correlate(samples, definition.code_set, 0.0, [0.0])[0]
    other = build_signals(GalE5a, prns=[11])["E11"]
    assert np.abs(_correlate(samples, other.code_set, 0.0, [0.0])[0]).max() < 0.1 * np.abs(right).max()


def test_the_data_symbol_is_one_pass_of_cs20():
    """Data signs change only on 20 ms of code phase; the pilot overlay moves every 1 ms."""
    samples = synthetic.generate_e5a_samples(
        prn=PRN, start_sec=0.0, duration_sec=0.045, samp_rate=SAMP_RATE,
        doppler_hz=0.0, code_phase_ms=0.0, nav_bits=True,
    )
    code_d, code_p = synthetic.get_e5a_codes(PRN)
    sec_d, sec_p = gal_e5a.data_secondary_code(), gal_e5a.generate_pilot_secondary_code(PRN)
    chip_index = ((np.arange(len(samples)) / SAMP_RATE) * gal_e5a.CODE_RATE_CHIPS_PER_SEC).astype(np.int64)
    period = chip_index // 10230
    pilot = samples.imag / (code_p[chip_index % 10230] * sec_p[period % 100])
    assert np.allclose(pilot, 1.0)
    data = samples.real / (code_d[chip_index % 10230] * sec_d[period % 20])
    for start in (0, 20):
        block = data[(period >= start) & (period < start + 20)]
        assert np.allclose(block, block[0])
    assert data[period == 0][0] == -data[period == 20][0]


# --------------------------------------------------------------------------
# Acquisition
# --------------------------------------------------------------------------

def _acquisition_config(fine=None):
    return bpsk_acquisition.AcquisitionConfiguration(
        coherent_duration_replica_ms=1, num_blocks=10, sample_rate=SAMP_RATE,
        min_search_doppler_hz=-5000.0, max_search_doppler_hz=5000.0, fine_search=fine,
    )


def _acquire(doppler_hz, code_phase_ms, prn=PRN, noise_sigma=3.0, seed=0, config=None, samples=None):
    definitions = build_signals(GalE5a, prns=[prn])
    config = config or _acquisition_config(bpsk_acquisition.FineSearchParameters())
    if samples is None:
        samples = synthetic.generate_e5a_samples(
            prn=prn, start_sec=0.0, duration_sec=config.acq_total_duration_ms * 1e-3,
            samp_rate=SAMP_RATE, doppler_hz=doppler_hz, code_phase_ms=code_phase_ms,
            noise_sigma=noise_sigma, rng=np.random.default_rng(seed),
        )
    return bpsk_acquisition.run_acquisition(
        sample_block=samples, sample_block_uptime_epoch_ms=0.0, acq_config=config,
        code_parameters=build_acquisition_code_params(GalE5a, definitions),
        prob_false_alarm_total=1e-6, noise_var_method="abscorrvar",
    )[f"E{prn:02d}"]


def test_acquisition_detects_and_locates_the_signal():
    result = _acquire(1234.0, 0.31)
    assert result.signal_detected
    assert result.acq_code_phase_seconds * 1e3 == pytest.approx(0.31, abs=2e-3 / SAMP_RATE * 1e3 + 1e-6)
    assert result.acq_doppler_hz == pytest.approx(1234.0, abs=250.0)


@pytest.mark.parametrize("code_phase_ms", [0.0, 0.5, 0.9])
def test_acquisition_finds_any_code_phase_within_the_period(code_phase_ms):
    result = _acquire(-2750.0, code_phase_ms, seed=2)
    assert result.signal_detected
    found = result.acq_code_phase_seconds * 1e3
    assert min(abs(found - code_phase_ms), abs(abs(found - code_phase_ms) - 1.0)) < 0.01


def test_acquisition_rejects_an_absent_satellite():
    config = _acquisition_config()
    samples = synthetic.generate_e5a_samples(
        prn=PRN, start_sec=0.0, duration_sec=config.acq_total_duration_ms * 1e-3,
        samp_rate=SAMP_RATE, doppler_hz=800.0, code_phase_ms=0.2,
        noise_sigma=8.0, rng=np.random.default_rng(3),
    )
    present = _acquire(0.0, 0.0, config=config, samples=samples)
    absent = _acquire(0.0, 0.0, prn=11, config=config, samples=samples)
    assert present.signal_detected and not absent.signal_detected
    assert present.normalized_peak_value > 5 * absent.normalized_peak_value


# --------------------------------------------------------------------------
# Overlay search
# --------------------------------------------------------------------------

@pytest.mark.parametrize("offset", [0, 1, 37, 63, 99])
def test_overlay_search_finds_the_pilot_offset(offset):
    overlay = gal_e5a.generate_pilot_secondary_code(PRN)
    rng = np.random.default_rng(offset)
    n = 4 * len(overlay)
    prompts = overlay[(np.arange(n) + offset) % 100] * (1.0 + 0.0j) + rng.normal(0.0, 1.0, n)
    found, confidence = secondary_code.brute_force_search(prompts, overlay)
    assert found == offset and confidence > 2.0


def test_every_pilot_overlay_has_a_unique_offset():
    for prn in (1, 17, 33, 50):
        overlay = gal_e5a.generate_pilot_secondary_code(prn).astype(float)
        auto = np.abs(np.fft.ifft(np.fft.fft(overlay) * np.conj(np.fft.fft(overlay))))
        assert auto[0] == pytest.approx(100.0) and auto[1:].max() < 0.5 * auto[0]


# --------------------------------------------------------------------------
# Tracking
# --------------------------------------------------------------------------

def _loop_params(coherent_duration_ms=20):
    return tracking_channel.TrackingLoopParameters(
        DLL_bandwidth_hz=2.0, PLL_bandwidth_hz=20.0, FLL_bandwidth_hz=50.0,
        coherent_duration_ms=coherent_duration_ms, EPL_chip_spacing=0.5,
    )


def _acquire_and_track(noise_sigma=3.0, buffers=24, buffer_ms=50, seed=4, nav_bits=True,
                       symbol_phase_periods=0, coherent_duration_ms=20):
    definitions = build_signals(GalE5a, prns=[PRN])
    config = _acquisition_config(bpsk_acquisition.FineSearchParameters())
    rng = np.random.default_rng(seed)
    kwargs = dict(
        prn=PRN, samp_rate=SAMP_RATE, doppler_hz=SEED_DOPPLER_HZ, code_phase_ms=SEED_CODE_PHASE_MS,
        noise_sigma=noise_sigma, nav_bits=nav_bits, symbol_phase_periods=symbol_phase_periods,
    )
    acq = bpsk_acquisition.run_acquisition(
        sample_block=synthetic.generate_e5a_samples(
            start_sec=0.0, duration_sec=config.acq_total_duration_ms * 1e-3, rng=rng, **kwargs),
        sample_block_uptime_epoch_ms=0.0, acq_config=config,
        code_parameters=build_acquisition_code_params(GalE5a, definitions),
        prob_false_alarm_total=1e-6, noise_var_method="abscorrvar",
    )[SIGNAL_ID]
    assert acq.signal_detected
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # loops are narrowed at the handover
        adapter = create_tracking_channels(
            GalE5a, signals=definitions, acquisition_results={SIGNAL_ID: acq},
            tracking_signal_ids=[SIGNAL_ID], loop_params=_loop_params(coherent_duration_ms),
            output_capacity=6000,
        )[SIGNAL_ID]
        for i in range(buffers):
            adapter.process_sample_buffer(sample_streaming.SampleBuffer(
                samples=synthetic.generate_e5a_samples(
                    start_sec=i * buffer_ms * 1e-3, duration_sec=buffer_ms * 1e-3, rng=rng, **kwargs),
                start_uptime_ms=i * buffer_ms, samp_rate=SAMP_RATE))
    return adapter


@pytest.fixture(scope="module")
def tracked():
    return _acquire_and_track()


def test_the_channel_finds_the_100_chip_overlay_itself(tracked):
    sync = tracked.channel.overlay_sync
    assert sync.synced and sync.confidence > 2.0 and sync.period == 100


def test_tracking_converges_and_locks(tracked):
    outputs = tracked.outputs
    assert abs(outputs.doppler_freq_hz[outputs.output_index - 1] - SEED_DOPPLER_HZ) < 5.0
    assert tracked.channel.loop_state.mode is tracking_channel.TrackingLoopMode.PLL


def test_synced_channel_extends_to_20_ms(tracked):
    channel = tracked.channel
    assert channel.coherent_duration_ms == 20
    assert channel.policy.costas is False
    params = channel.loop_params
    for bandwidth in (params.PLL_bandwidth_hz, params.DLL_bandwidth_hz, params.FLL_bandwidth_hz):
        assert bandwidth * params.coherent_duration_ms * 1e-3 <= 0.1 + 1e-9


def test_wipe_off_gives_20_fold_coherent_gain():
    adapter = _acquire_and_track(noise_sigma=0.0, buffers=30)
    outputs = adapter.outputs
    count = outputs.output_index
    duration = np.asarray(outputs.epoch_duration_ms[:count])
    pilot = np.abs(outputs.prompt_corr[:count, 1])
    first_long = int(np.nonzero(duration == 20.0)[0][0])
    short = pilot[(duration == 1.0) & (np.arange(count) > first_long - 40)][-30:-2]
    long_ = pilot[first_long + 2 : count]
    assert len(short) > 10 and len(long_) > 5
    assert long_.mean() / short.mean() == pytest.approx(20.0, rel=0.05)


def test_the_data_component_survives_the_epoch_it_shares(tracked):
    outputs = tracked.outputs
    count = outputs.output_index
    tail = slice(count - 6, count)
    ratio = np.abs(outputs.prompt_corr[tail, 0]).mean() / np.abs(outputs.prompt_corr[tail, 1]).mean()
    assert 0.8 < ratio < 1.25


def test_the_prompt_values_carry_the_data_bits():
    truth = np.where(np.random.default_rng(1).integers(0, 2, 400) == 1, -1.0, 1.0)
    adapter = _acquire_and_track(noise_sigma=0.0, buffers=60, nav_bits=truth)
    stream = symbols.extract(adapter.outputs, "GAL_E5A")
    assert stream.epochs_per_symbol == 1 and stream.symbol_period_ms == 20
    assert stream.data_in_quadrature is True and len(stream.values) > 60
    # The carrier needs the first few 20 ms symbols to settle onto the quadrature axis.
    settling = 12
    settled = stream.values[settling:]
    assert np.abs(settled.imag).mean() > 10 * np.abs(settled.real).mean()
    hard = np.sign(stream.soft)[settling:]
    first = int(round(stream.code_phase_ms[settling] / 20.0))
    expected = truth[(first + np.arange(len(hard))) % len(truth)]
    agreement = np.mean(hard == expected)
    assert max(agreement, 1.0 - agreement) == 1.0


@pytest.mark.parametrize("shift", [0, 1, 7, 13, 19])
def test_epochs_start_on_the_true_symbol_boundary_whatever_the_lattice_phase(shift):
    """
    E5a acquires only modulo 1 ms, so the symbol edge is anywhere on the 20 ms lattice.  With the
    symbols slid by `shift` periods, the 20 ms epochs must start at code phase = -shift (mod 20)
    and the data must come out at the pilot's full magnitude (D-059 for B2a).
    """
    adapter = _acquire_and_track(noise_sigma=0.0, buffers=30, symbol_phase_periods=shift)
    outputs = adapter.outputs
    count = outputs.output_index
    duration = np.asarray(outputs.epoch_duration_ms[:count])
    long_epochs = np.nonzero(duration == 20.0)[0]
    assert len(long_epochs) > 8, "the channel never extended to 20 ms epochs"
    phases = np.round(np.asarray(outputs.code_phase_ms[:count])[long_epochs]).astype(int)
    assert np.all(phases % 20 == (-shift) % 20), (shift, np.unique(phases % 20))
    tail = slice(count - 5, count)
    ratio = np.abs(outputs.prompt_corr[tail, 0]).mean() / np.abs(outputs.prompt_corr[tail, 1]).mean()
    assert 0.9 < ratio < 1.1, f"shift {shift}: data/pilot magnitude {ratio:.2f}"


# --------------------------------------------------------------------------
# Tables that other modules keep per signal type
# --------------------------------------------------------------------------

def test_symbol_and_band_tables_describe_e5a():
    from utils import observables

    signal = GalE5a(PRN)
    data_component = signal.code_set.components[symbols.DATA_COMPONENT["GAL_E5A"]]
    assert data_component.name == "E5aI"
    assert data_component.symbol_period_ms == symbols.SYMBOL_PERIOD_MS["GAL_E5A"] == 20
    assert symbols.NEEDS_BIT_SYNC["GAL_E5A"] is False and symbols.HAS_OVERLAY["GAL_E5A"] is True
    carrier = TRACKING_POLICIES["GAL_E5A"].discriminator_policy.carrier_component
    assert symbols.DATA_IN_QUADRATURE["GAL_E5A"] is (signal.code_set.components[carrier].branch != data_component.branch)
    assert observables.BAND_FREQ_HZ["GAL_E5A"] == GalE5a.carrier_freq_hz
