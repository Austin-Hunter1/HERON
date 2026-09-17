"""
The double estimator: tracking a BOC signal's code and subcarrier separately.

A BOC signal is a spreading code multiplied by a square-wave subcarrier, and a
conventional channel correlates against the product with ONE delay.  That coupling
is where the side peaks come from: the composite |ACF| has stable secondary maxima
at +/-0.53 chip on L1C, and a delay loop that lands on one stays there, 155 m out,
with a perfectly healthy prompt.

The double estimator gives the code and the subcarrier their own delays.  The code
axis is then a plain BPSK triangle -- one peak, a chip wide, unambiguous but
shallow -- and the subcarrier axis is about six times steeper but repeats.  The
coarse estimate picks which repeat the fine one is on, exactly as a code
pseudorange resolves a carrier-phase integer.

The ambiguity interval is 0.5 chip, not 1.  The signed subcarrier correlation does
repeat every chip, but the discriminator is non-coherent -- it combines magnitudes
-- so it cannot tell the +1 peak at 0 from the -1 peak half a chip later.
"""

from __future__ import annotations

import numpy as np
import pytest

from utils import sample_streaming, tracking_channel as tc
from utils.signal_interfaces import (
    GpsL1C,
    GpsL1CA,
    TRACKING_POLICIES,
    build_signals,
    coherent_duration_target_ms,
)

from . import synthetic

SAMP_RATE = 25e6
PRN = 1
TRUE_CODE_PHASE_MS = 3.27
DOPPLER_HZ = 1200.0
CHIP_MS = 1e3 / GpsL1C.tracking_code_rate_chips_per_sec
CHIP_M = 293.05


# ---------------------------------------------------------------------------
# Tap layout
# ---------------------------------------------------------------------------


def test_the_classic_layout_ties_the_subcarrier_to_the_code():
    """Three taps, and every one of them puts the subcarrier where the code is."""
    layout = tc.epl_tap_layout(0.5)
    assert layout.num_taps == 3
    assert not layout.tracks_subcarrier
    assert layout.code_offsets_chips == layout.subcarrier_offsets_chips
    assert layout.index(tc.EARLY) == 0
    assert layout.index(tc.PROMPT) == 1
    assert layout.index(tc.LATE) == 2


def test_the_double_estimator_layout_puts_its_taps_on_two_axes():
    """
    Neither pair is displaced along the other's axis.  That is what keeps the two
    discriminators independent: the code taps see the code axis at the subcarrier's
    current delay, and the subcarrier taps see the subcarrier axis at the code's.
    """
    layout = tc.double_estimator_tap_layout(0.5, 0.04)
    assert layout.num_taps == 5
    assert layout.tracks_subcarrier

    code = layout.code_offsets
    sub = layout.subcarrier_offsets
    assert sub[layout.index(tc.EARLY)] == 0.0
    assert sub[layout.index(tc.LATE)] == 0.0
    assert code[layout.index(tc.SUBCARRIER_EARLY)] == 0.0
    assert code[layout.index(tc.SUBCARRIER_LATE)] == 0.0
    assert code[layout.index(tc.EARLY)] == pytest.approx(+0.5)
    assert code[layout.index(tc.LATE)] == pytest.approx(-0.5)
    assert sub[layout.index(tc.SUBCARRIER_EARLY)] == pytest.approx(+0.04)
    assert sub[layout.index(tc.SUBCARRIER_LATE)] == pytest.approx(-0.04)


def test_a_layout_without_a_prompt_is_rejected():
    with pytest.raises(ValueError, match="prompt"):
        tc.TapLayout((0.0, 1.0), (0.0, 0.0), ((tc.EARLY, 0), (tc.LATE, 1)))


def test_taps_are_addressed_by_role_not_by_position():
    """
    The prompt is tap 1 in the classic layout and tap 0 in the double-estimator
    one.  Anything that reached for a fixed index would read an early tap as a
    prompt and never say so.
    """
    assert tc.epl_tap_layout(0.5).index(tc.PROMPT) == 1
    assert tc.double_estimator_tap_layout(0.5, 0.04).index(tc.PROMPT) == 0
    with pytest.raises(KeyError, match="subcarrier_early"):
        tc.epl_tap_layout(0.5).index(tc.SUBCARRIER_EARLY)


# ---------------------------------------------------------------------------
# The ambiguity interval
# ---------------------------------------------------------------------------


def _signal_params(signal_type):
    signal = build_signals(signal_type, prns=[PRN])[f"G{PRN:02d}"]
    return tc.TrackingSignalParameters(
        code_set=signal.code_set,
        nominal_code_rate_chips_per_sec=signal_type.tracking_code_rate_chips_per_sec,
        carrier_freq_hz=signal_type.carrier_freq_hz,
        primary_period_ms=signal_type.primary_period_ms,
    )


def test_the_ambiguity_interval_is_half_the_signed_period():
    """
    L1C's base subcarrier is BOC(1,1) -- two sub-chips per chip -- so the signed
    correlation repeats every chip and the magnitude every half chip.  Measured on
    the real L1CP code, |R| peaks at 0 and +/-0.5 with nulls at +/-0.24, which is
    what makes the code loop's job "stay inside +/-0.25 chip".

    TMBOC's BOC(6,1) chips do NOT shorten it: they sharpen the peak, but the 29
    chips in 33 that are BOC(1,1) set where it repeats.
    """
    assert _signal_params(GpsL1C).subcarrier_ambiguity_chips == pytest.approx(0.5)


def test_a_signal_with_no_subcarrier_has_no_ambiguity_interval():
    assert _signal_params(GpsL1CA).subcarrier_ambiguity_chips == 0.0


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_the_two_subcarrier_settings_enable_the_loop_together():
    for kwargs in (
        dict(subcarrier_bandwidth_hz=2.0),
        dict(subcarrier_chip_spacing=0.04),
    ):
        with pytest.raises(ValueError, match="together"):
            tc.TrackingLoopParameters(2.0, 20.0, 50.0, 1, 0.5, **kwargs)


def test_the_double_estimator_needs_a_subcarrier_to_track():
    loop = tc.TrackingLoopParameters(
        2.0, 20.0, 50.0, 1, 0.5,
        subcarrier_bandwidth_hz=2.0, subcarrier_chip_spacing=0.04,
    )
    state = tc.TrackingSignalState(0.0, 0.0, 1e3, 0.0, 0.0)
    with pytest.raises(ValueError, match="no[nt]e|subcarrier to track"):
        tc.TrackingChannel(loop, _signal_params(GpsL1CA), state)


# ---------------------------------------------------------------------------
# Tracking
# ---------------------------------------------------------------------------


def _track(seed_error_chips, *, double_estimator, duration_ms=1500, noise_sigma=0.0):
    signal = build_signals(GpsL1C, prns=[PRN])[f"G{PRN:02d}"]
    policy = TRACKING_POLICIES["GPS_L1C"]
    loop = tc.TrackingLoopParameters(
        DLL_bandwidth_hz=0.5 if double_estimator else 2.0,
        PLL_bandwidth_hz=20.0,
        FLL_bandwidth_hz=50.0,
        coherent_duration_ms=1,
        EPL_chip_spacing=0.5 if double_estimator else 0.1,
        subcarrier_bandwidth_hz=2.0 if double_estimator else 0.0,
        subcarrier_chip_spacing=0.04 if double_estimator else 0.0,
    )
    state = tc.TrackingSignalState(
        uptime_epoch_ms=0.0,
        code_phase_ms=TRUE_CODE_PHASE_MS + seed_error_chips * CHIP_MS,
        code_rate_ms_per_sec=(1.0 + DOPPLER_HZ / GpsL1C.carrier_freq_hz) * 1e3,
        carrier_phase_cycles=0.0,
        carrier_rate_cyc_per_sec=DOPPLER_HZ,
    )
    channel = tc.TrackingChannel(
        loop, _signal_params(GpsL1C), state, output_capacity=duration_ms + 16,
        discriminator_policy=policy.discriminator_policy,
        synced_policy=policy.synced_discriminator_policy,
        synced_coherent_duration_ms=coherent_duration_target_ms(GpsL1C, signal, 10),
        overlay_search=policy.overlay_search,
        overlay_prompts_to_observe=policy.overlay_prompts_to_observe,
    )
    rng = np.random.default_rng(0)
    step_ms = 100
    for block in range(duration_ms // step_ms):
        samples = synthetic.generate_l1c_samples(
            prn=PRN, start_sec=block * step_ms * 1e-3, duration_sec=step_ms * 1e-3,
            samp_rate=SAMP_RATE, doppler_hz=DOPPLER_HZ,
            code_phase_ms=TRUE_CODE_PHASE_MS, noise_sigma=noise_sigma,
            nav_bits=True, rng=rng,
        )
        channel.process_sample_buffer(
            sample_streaming.SampleBuffer(samples, block * step_ms, SAMP_RATE)
        )
    outputs = channel.outputs
    valid = outputs.valid
    uptime = outputs.uptime_epoch_ms[valid]
    expected = TRUE_CODE_PHASE_MS + (1 + DOPPLER_HZ / GpsL1C.carrier_freq_hz) * uptime
    code_only = (outputs.code_phase_ms[valid] - expected) / CHIP_MS
    _track.last_uptime_ms = uptime
    return code_only, code_only + outputs.subcarrier_offset_chips[valid]


def _settled(values, window_ms=300):
    """
    The last `window_ms` of the run, selected by uptime rather than by a count of
    epochs.

    The epoch length is not constant -- the channel opens at one interval and
    extends to 10 ms once it locks -- so a fixed slice of the last N epochs means a
    different amount of wall time depending on when the extension happened.  It
    reached back over the whole run, convergence included, once L1C stopped waiting
    for its overlay search before extending.
    """
    uptime = _track.last_uptime_ms
    return values[uptime >= uptime[-1] - window_ms]


def test_a_single_loop_locks_onto_a_side_peak_and_stays_there():
    """
    The failure the technique exists for.  Seeded 0.53 chip out -- one BOC side
    peak -- a conventional channel settles there and reports a healthy prompt the
    whole time, so nothing downstream can tell.
    """
    code_only, _ = _track(0.53, double_estimator=False)
    settled = _settled(code_only)
    assert settled.mean() == pytest.approx(0.53, abs=0.05)
    assert settled.std() < 0.01, "it is not drifting back -- it is locked"


def test_the_double_estimator_recovers_from_the_same_side_peak():
    """
    Same seed, same signal.  The code loop rides the plain code triangle, which has
    one peak, so there is nothing to lock onto but the truth; the subcarrier loop
    then supplies the precision.
    """
    _, combined = _track(0.53, double_estimator=True)
    settled = _settled(combined)
    assert abs(settled.mean()) < 0.02, f"{settled.mean() * CHIP_M:.1f} m from truth"


@pytest.mark.parametrize("seed", [0.0, 0.10, 0.53])
def test_the_combined_estimate_beats_the_code_loop_alone(seed):
    """
    The code loop is deliberately narrow -- it only has to stay inside +/-0.25 chip
    -- so on its own it is slow and coarse.  The delay actually estimated is the
    sum, and that is what has to be accurate.
    """
    code_only, combined = _track(seed, double_estimator=True)
    assert abs(_settled(combined).mean()) <= abs(_settled(code_only).mean()) + 1e-9
    assert abs(_settled(combined).mean()) < 0.02


def test_the_subcarrier_offset_stays_inside_one_ambiguity_interval():
    """
    Wrapping IS the ambiguity resolution, so the offset must never wander out of
    [-T/2, T/2).  If it did, the reported delay would be a whole subcarrier cycle
    -- 147 m -- from the truth while every correlator still looked healthy.
    """
    signal = build_signals(GpsL1C, prns=[PRN])[f"G{PRN:02d}"]
    del signal
    code_only, combined = _track(0.53, double_estimator=True)
    offsets = combined - code_only
    assert np.all(np.abs(offsets) <= 0.25 + 1e-9)


def test_a_tied_channel_is_unaffected_by_the_new_machinery():
    """
    Every signal but L1C still ties the subcarrier to the code, and the defaults
    for that path are exact rather than merely close -- which is what lets the
    BPSK goldens stay bit-identical through this change.
    """
    code_only, combined = _track(0.0, double_estimator=False)
    np.testing.assert_array_equal(code_only, combined)
