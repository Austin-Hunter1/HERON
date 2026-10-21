"""
B1C with the BOC(6,1) part of the pilot modelled (wide band, D-071, Phase T2).

Why: narrow-band B1C (D-062) leaves 1/11 of the pilot power unused.  Wide-band
mode adds it as a third component.  These tests show that the wide-band
channel locks on a signal that has the part, that its delay estimate is
unbiased, and that it also locks when the part is absent.  They run at 16 Msps,
which holds the BOC(6,1) lobes (12 half-cycles per chip at 1.023 Mcps).
"""

from __future__ import annotations

import warnings

import numpy as np
import pytest

from utils import bds_b1c, bpsk_acquisition, sample_streaming, tracking_channel
from utils.signal_interfaces import (
    BdsB1C,
    SignalOptions,
    build_acquisition_code_params,
    build_signals,
    create_tracking_channels,
)

from . import synthetic

RATE = 16_000_000
PRN = 1
SIGNAL_ID = f"C{PRN:02d}"
DOPPLER_HZ = 1500.0
CODE_PHASE_MS = 3.17


def _track(*, model_wide: bool, signal_wide: bool, spacing: float, noise: float, buffers: int = 30):
    """Acquire and track; `model_wide` is the tracker model, `signal_wide` is the test signal."""
    signal_type = BdsB1C.with_options(SignalOptions(boc61=model_wide))
    definitions = build_signals(signal_type, prns=[PRN])
    config = bpsk_acquisition.AcquisitionConfiguration(
        coherent_duration_replica_ms=10, num_blocks=4, sample_rate=RATE,
        min_search_doppler_hz=-5000.0, max_search_doppler_hz=5000.0,
        fine_search=bpsk_acquisition.FineSearchParameters(),
    )
    rng = np.random.default_rng(5)
    kwargs = dict(
        prn=PRN, samp_rate=RATE, doppler_hz=DOPPLER_HZ, code_phase_ms=CODE_PHASE_MS,
        noise_sigma=noise, include_boc61=signal_wide,
    )
    acq = bpsk_acquisition.run_acquisition(
        sample_block=synthetic.generate_b1c_samples(
            start_sec=0.0, duration_sec=config.acq_total_duration_ms * 1e-3, rng=rng, **kwargs
        ),
        sample_block_uptime_epoch_ms=0.0, acq_config=config,
        code_parameters=build_acquisition_code_params(signal_type, definitions),
        prob_false_alarm_total=1e-6, noise_var_method="abscorrvar",
    )[SIGNAL_ID]
    assert acq.signal_detected
    loop_params = tracking_channel.TrackingLoopParameters(
        DLL_bandwidth_hz=0.5, PLL_bandwidth_hz=20.0, FLL_bandwidth_hz=50.0,
        coherent_duration_ms=10, EPL_chip_spacing=spacing,
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        adapter = create_tracking_channels(
            signal_type, signals=definitions, acquisition_results={SIGNAL_ID: acq},
            tracking_signal_ids=[SIGNAL_ID], loop_params=loop_params, output_capacity=6000,
        )[SIGNAL_ID]
        for i in range(buffers):
            adapter.process_sample_buffer(sample_streaming.SampleBuffer(
                samples=synthetic.generate_b1c_samples(
                    start_sec=i * 0.1, duration_sec=0.1, rng=rng, **kwargs),
                start_uptime_ms=i * 100.0, samp_rate=RATE))
    return adapter


def _code_error_chips(adapter) -> np.ndarray:
    outputs = adapter.outputs
    count = outputs.output_index
    truth = CODE_PHASE_MS + np.asarray(outputs.uptime_epoch_ms[:count]) * (
        1.0 + DOPPLER_HZ / bds_b1c.CARRIER_FREQ_HZ
    )
    return (np.asarray(outputs.code_phase_ms[:count]) - truth) * 1.023


@pytest.mark.parametrize("spacing", [0.5, 0.2])
def test_wide_band_model_locks_on_a_signal_with_the_boc61_part(spacing):
    adapter = _track(model_wide=True, signal_wide=True, spacing=spacing, noise=0.0)
    outputs = adapter.outputs
    count = outputs.output_index
    assert adapter.channel.loop_state.mode is tracking_channel.TrackingLoopMode.PLL
    assert abs(outputs.doppler_freq_hz[count - 1] - DOPPLER_HZ) < 5.0
    # A side peak of the BOC(6,1) correlation would show as a bias of 1/12 chip or more.
    assert np.abs(_code_error_chips(adapter)[-100:]).max() < 0.01
    # Mutation guard: the third prompt must carry its power share.  A wrong
    # subcarrier rate or phase in the model would make it near zero.
    tail = slice(count - 15, count)
    ratio = np.abs(outputs.prompt_corr[tail, 2]).mean() / np.abs(outputs.prompt_corr[tail, 1]).mean()
    # Full ratio is sqrt((1/11) / (29/44)) = 0.37.  At 16 Msps the BOC(6,1) lobes have
    # only 1.3 samples per half-cycle, so about a quarter of that is lost to sampling
    # (0.28 measured); the window is wide for that reason, yet far above zero.
    assert 0.2 < ratio < 0.42, ratio


def test_wide_band_model_also_locks_when_the_signal_has_no_boc61_part():
    """A mismatch in this direction costs power, not lock."""
    adapter = _track(model_wide=True, signal_wide=False, spacing=0.5, noise=4.0)
    assert adapter.channel.loop_state.mode is tracking_channel.TrackingLoopMode.PLL
    assert np.abs(_code_error_chips(adapter)[-100:]).max() < 0.05


def test_third_component_has_the_pilot_power_share_and_the_i_branch():
    """The test signal and the model must agree: 1/11 of the power, with the data."""
    wide = BdsB1C.with_options(SignalOptions(boc61=True))
    component = build_signals(wide, prns=[PRN])[SIGNAL_ID].code_set.components[2]
    assert component.name == "B1CP61"
    assert component.power_weight == pytest.approx(1.0 / 11.0)
    assert component.subcarrier.rate_hz == pytest.approx(6.138e6)
