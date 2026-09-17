"""
An overlay chip lasts one primary code period, not one correlation interval.

Every signal in the catalog with a tiered code -- L5 alone, until GPS L1C --
happens to have a 1 ms primary code period, which is also `CORRELATION_INTERVAL_MS`.
That coincidence let the two be conflated: the counter advanced once per interval
and the synchroniser was fed one interval's prompt.  L1C breaks it, with a 10 ms
primary period carrying an 1800-symbol overlay.

These tests use a synthetic signal built specifically so the two differ -- a 4 ms
primary code period with a 5-chip overlay -- because no real signal here does.
The failure they guard against is quiet rather than loud: with the counter running
four times too fast, wipe-off applies a different sign to each millisecond of a
period that carries one, so a coherent epoch spanning the period partly cancels
and the overlay search never sees the pattern it is looking for.
"""

from __future__ import annotations

import numpy as np
import pytest

import gnss_tools.signals.gps_l1ca as gps_l1ca

from utils import secondary_code, tracking_channel
from utils.code_components import Branch, CodeComponent, build_code_set
from utils.sample_streaming import SampleBuffer

from . import synthetic

SAMP_RATE = 5e6
PRIMARY_PERIOD_MS = 4
CODE_LENGTH = 1023 * PRIMARY_PERIOD_MS  # 4092 chips at 1.023 Mcps -> 4 ms
OVERLAY = np.array([1, 1, -1, 1, -1], dtype=np.int8)  # counter period 5 -> 20 ms


def _code(prn: int = 1) -> np.ndarray:
    """A 4 ms primary code: the L1 C/A code tiled four times.

    Its content is irrelevant -- what matters is that one pass through it takes
    four correlation intervals, so the overlay chip and the interval come apart.
    """
    return np.tile(synthetic.get_l1ca_code(prn), PRIMARY_PERIOD_MS).astype(np.int8)


def _signal_params() -> tracking_channel.TrackingSignalParameters:
    return tracking_channel.TrackingSignalParameters(
        code_set=build_code_set(
            [CodeComponent(name="P", sequence=_code(), branch=Branch.Q, overlay=OVERLAY)]
        ),
        nominal_code_rate_chips_per_sec=gps_l1ca.CODE_RATE,
        carrier_freq_hz=gps_l1ca.CARRIER_FREQ,
        primary_period_ms=PRIMARY_PERIOD_MS,
    )


def _generate(duration_ms: float, doppler_hz: float = 0.0, code_phase_ms: float = 0.0):
    """Baseband samples for the synthetic signal, with the overlay applied."""
    code = _code()
    n = int(round(SAMP_RATE * duration_ms * 1e-3))
    t = np.arange(n) / SAMP_RATE

    code_rate = gps_l1ca.CODE_RATE * (1.0 + doppler_hz / gps_l1ca.CARRIER_FREQ)
    chips = code_phase_ms * 1e-3 * gps_l1ca.CODE_RATE + t * code_rate
    chip_index = chips.astype(np.int64)

    period_index = chip_index // CODE_LENGTH
    samples = (
        code[chip_index % CODE_LENGTH] * OVERLAY[period_index % len(OVERLAY)]
    ) * np.exp(2j * np.pi * doppler_hz * t)
    return samples.astype(np.complex64)


def _channel(coherent_duration_ms: int = 1, **kwargs) -> tracking_channel.TrackingChannel:
    loop_params = tracking_channel.TrackingLoopParameters(
        DLL_bandwidth_hz=2.0, PLL_bandwidth_hz=20.0, FLL_bandwidth_hz=50.0,
        coherent_duration_ms=coherent_duration_ms,
    )
    channel = tracking_channel.TrackingChannel(
        loop_params=loop_params,
        signal_params=_signal_params(),
        initial_signal_state=tracking_channel.TrackingSignalState(
            uptime_epoch_ms=0.0, code_phase_ms=0.0,
            code_rate_ms_per_sec=1e3, carrier_phase_cycles=0.0,
            carrier_rate_cyc_per_sec=0.0,
        ),
        output_capacity=4000,
        **kwargs,
    )
    # The overlay search is gated on PLL lock, and pull-in is not what is under
    # test here; the signal is noiseless and seeded exactly.
    channel.loop_state.mode = tracking_channel.TrackingLoopMode.PLL
    return channel


def _run(channel, samples, buffer_ms: float = 20.0):
    step = int(round(SAMP_RATE * buffer_ms * 1e-3))
    for start in range(0, len(samples) - step + 1, step):
        channel.process_sample_buffer(
            SampleBuffer(
                samples=samples[start : start + step],
                start_uptime_ms=start / SAMP_RATE * 1e3,
                samp_rate=SAMP_RATE,
            )
        )


def test_the_counter_advances_once_per_primary_period_not_once_per_interval():
    """
    The signal's overlay chip spans four correlation intervals.  Over 20 ms -- one
    full overlay period -- the counter must therefore make exactly one lap of its
    five chips, not four laps.
    """
    channel = _channel()
    channel.overlay_sync.status = secondary_code.OverlaySyncStatus.SYNCED
    channel.overlay_sync.counter = 0

    seen = []
    original_signs = channel.overlay_sync.signs

    def record(overlays):
        seen.append(channel.overlay_sync.counter)
        return original_signs(overlays)

    channel.overlay_sync.signs = record
    _run(channel, _generate(20.0))

    assert len(seen) == 20, "one sign lookup per 1 ms correlation interval"
    # Four identical values per overlay chip, stepping once per 4 ms period.
    assert seen == [chip for chip in range(5) for _ in range(PRIMARY_PERIOD_MS)]


def test_wipe_off_holds_one_sign_across_the_whole_primary_period():
    """
    The operational consequence: a coherent epoch as long as the primary period
    must accumulate, not cancel.  With the counter running four times too fast the
    overlay's own sign pattern is applied *within* the period, and a 4 ms epoch
    lands at a fraction of its proper magnitude.
    """
    samples = _generate(200.0)

    one_ms = _channel(coherent_duration_ms=1)
    one_ms.overlay_sync.status = secondary_code.OverlaySyncStatus.SYNCED
    _run(one_ms, samples)

    four_ms = _channel(coherent_duration_ms=PRIMARY_PERIOD_MS)
    four_ms.overlay_sync.status = secondary_code.OverlaySyncStatus.SYNCED
    _run(four_ms, samples)

    interval_magnitude = np.abs(one_ms.outputs.prompt_corr[one_ms.outputs.valid, 0]).mean()
    epoch_magnitude = np.abs(four_ms.outputs.prompt_corr[four_ms.outputs.valid, 0]).mean()

    # Coherent means N times, not sqrt(N) times and not a partial cancellation.
    assert epoch_magnitude == pytest.approx(PRIMARY_PERIOD_MS * interval_magnitude, rel=0.02)


def test_the_search_is_fed_one_prompt_per_overlay_chip():
    """
    The synchroniser correlates prompts against the overlay one-for-one, so a
    prompt has to be a whole overlay chip.  Handing it interval fragments would
    make the recovered offset an interval index -- four times the true one, modulo
    the overlay length -- and quietly mis-phase every subsequent wipe-off.
    """
    observed = []

    class Recorder:
        """Stands in for the search, capturing what the synchroniser accumulated."""

        def __call__(self, prompts, overlay):
            observed.append(np.asarray(prompts).copy())
            return 0, 99.0

    channel = _channel(overlay_search=Recorder(), overlay_prompts_to_observe=5)
    _run(channel, _generate(60.0))

    assert observed, "the search was never run"
    prompts = observed[0]
    assert len(prompts) == 5, "one prompt per overlay chip, not per interval"

    # Each prompt is a whole 4 ms period, so it is four intervals' worth of
    # magnitude -- and it carries that chip's overlay sign, which is the pattern
    # the search exists to find.
    magnitudes = np.abs(prompts)
    assert magnitudes.min() > 0.9 * magnitudes.max(), "no prompt partly cancelled"
    recovered = np.sign(np.real(prompts / prompts[0])) * OVERLAY[0]
    np.testing.assert_array_equal(recovered.astype(np.int8), OVERLAY)


def test_a_one_millisecond_primary_period_is_unaffected():
    """
    L5's primary period IS one correlation interval, and the whole point of
    deriving the position from code phase is that this case stays exactly what it
    was: reset, one interval, observe, advance, every time.
    """
    params = tracking_channel.TrackingSignalParameters(
        code_set=build_code_set(
            [
                CodeComponent(
                    name="P", sequence=synthetic.get_l1ca_code(1),
                    branch=Branch.Q, overlay=OVERLAY,
                )
            ]
        ),
        nominal_code_rate_chips_per_sec=gps_l1ca.CODE_RATE,
        carrier_freq_hz=gps_l1ca.CARRIER_FREQ,
        primary_period_ms=1,
    )
    channel = tracking_channel.TrackingChannel(
        loop_params=tracking_channel.TrackingLoopParameters(
            DLL_bandwidth_hz=2.0, PLL_bandwidth_hz=20.0, FLL_bandwidth_hz=50.0,
        ),
        signal_params=params,
        initial_signal_state=tracking_channel.TrackingSignalState(
            uptime_epoch_ms=0.0, code_phase_ms=0.0, code_rate_ms_per_sec=1e3,
            carrier_phase_cycles=0.0, carrier_rate_cyc_per_sec=0.0,
        ),
        output_capacity=100,
    )
    assert channel._intervals_per_primary_period == 1
