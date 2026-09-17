"""
How long one coherent accumulation may last, and what rejects a bad choice.

There is one knob, `coherent_duration_ms`, and one fixed granularity,
`CORRELATION_INTERVAL_MS = 1`.  The interval is not a user choice: an overlay chip
lasts one primary code period -- 1 ms for every signal here -- so a longer interval
could span a sign change.  Integration is built out of intervals, folded with
wipe-off, which is why extending it never means lengthening the interval.

An accumulation is coherent only while what it accumulates keeps one sign, so the
limit is the shortest such interval across *every* component -- one epoch serves the
whole signal, so a length that suits the pilot but overruns a data component leaves
that component quietly self-cancelling:

    un-stripped overlay   one primary code period
    data symbol           `symbol_period_ms` on the component
    dataless pilot        no limit of its own (only Doppler, not checked here)

L5 is the case in point: Q alone would take NH20's 20 ms, but I carries 10 ms CNAV
symbols, so 10 ms is the signal's limit.

The requirement is divisibility, not just "fits".  Epochs are anchored to multiples
of their own length in code phase, so N = 10 tiles a 20 ms symbol exactly while
N = 8 leaves the epoch [16, 24) across the boundary at 20 however it is aligned.
"""

from __future__ import annotations

import warnings

import numpy as np
import pytest

from utils import tracking_channel
from utils.signal_interfaces import (
    TRACKING_POLICIES,
    GpsL1C,
    GpsL1CA,
    GpsL2C,
    GpsL5,
    build_signals,
    coherent_duration_target_ms,
    create_tracking_channels,
)


def _channel(signal_type, coherent_duration_ms, synced_coherent_duration_ms=None):
    signal = build_signals(signal_type, prns=[1])["G01"]
    policy = TRACKING_POLICIES[signal_type.signal_type_id]
    return tracking_channel.TrackingChannel(
        loop_params=tracking_channel.TrackingLoopParameters(
            DLL_bandwidth_hz=2.0, PLL_bandwidth_hz=20.0, FLL_bandwidth_hz=50.0,
            coherent_duration_ms=coherent_duration_ms,
        ),
        signal_params=tracking_channel.TrackingSignalParameters(
            code_set=signal.code_set,
            nominal_code_rate_chips_per_sec=signal_type.tracking_code_rate_chips_per_sec,
            carrier_freq_hz=signal_type.carrier_freq_hz,
            primary_period_ms=signal_type.primary_period_ms,
        ),
        initial_signal_state=tracking_channel.TrackingSignalState(
            uptime_epoch_ms=0.0, code_phase_ms=0.5, code_rate_ms_per_sec=1e3,
            carrier_phase_cycles=0.0, carrier_rate_cyc_per_sec=0.0,
        ),
        output_capacity=10,
        discriminator_policy=policy.discriminator_policy,
        synced_policy=policy.synced_discriminator_policy,
        synced_coherent_duration_ms=(
            synced_coherent_duration_ms or coherent_duration_ms
        ),
    )


# --------------------------------------------------------------------------
# The knob itself
# --------------------------------------------------------------------------

def test_one_interval_is_the_default():
    params = tracking_channel.TrackingLoopParameters(
        DLL_bandwidth_hz=2.0, PLL_bandwidth_hz=20.0, FLL_bandwidth_hz=50.0
    )
    assert params.coherent_duration_ms == tracking_channel.CORRELATION_INTERVAL_MS
    assert params.intervals_per_epoch == 1


def test_integration_is_a_whole_number_of_intervals():
    for bad in (0, -1):
        with pytest.raises(ValueError, match="positive multiple"):
            tracking_channel.TrackingLoopParameters(
                DLL_bandwidth_hz=2.0, PLL_bandwidth_hz=20.0, FLL_bandwidth_hz=50.0,
                coherent_duration_ms=bad,
            )


def test_loop_gains_follow_the_integration_length():
    """Every gain is proportional to the update period, which is the epoch."""
    short = tracking_channel.TrackingLoopParameters(
        DLL_bandwidth_hz=2.0, PLL_bandwidth_hz=20.0, FLL_bandwidth_hz=50.0,
        coherent_duration_ms=1,
    )
    long = tracking_channel.TrackingLoopParameters(
        DLL_bandwidth_hz=2.0, PLL_bandwidth_hz=20.0, FLL_bandwidth_hz=50.0,
        coherent_duration_ms=10,
    )
    assert long.DLL_filter_coeff == pytest.approx(10 * short.DLL_filter_coeff)
    assert long.intervals_per_epoch == 10


# --------------------------------------------------------------------------
# Rejection
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "signal_type, coherent_ms",
    [
        (GpsL1CA, 1), (GpsL1CA, 4), (GpsL1CA, 5),
        (GpsL1CA, 20),                      # exactly one nav bit
        (GpsL2C, 1), (GpsL2C, 10),
        (GpsL2C, 20),                       # exactly one CNAV symbol
    ],
)
def test_integration_dividing_the_symbol_is_accepted(signal_type, coherent_ms):
    channel = _channel(signal_type, coherent_ms)
    assert channel.coherent_duration_ms == coherent_ms


@pytest.mark.parametrize(
    "signal_type, coherent_ms, expected",
    [
        (GpsL1CA, 3, "does not divide 20"),
        (GpsL1CA, 30, "exceeds 20"),
        (GpsL2C, 8, "does not divide 20"),
        (GpsL2C, 40, "exceeds 20"),
    ],
)
def test_integration_that_would_span_a_symbol_is_rejected(signal_type, coherent_ms, expected):
    with pytest.raises(ValueError, match=expected):
        _channel(signal_type, coherent_ms)


def test_an_unstripped_overlay_caps_integration_at_one_primary_period():
    """
    Before Neuman-Hofman is synced, the overlay flips every 1 ms and nothing
    longer is coherent -- regardless of the 10 ms CNAV symbol behind it.
    """
    with pytest.raises(ValueError, match="overlay chip"):
        _channel(GpsL5, 5, synced_coherent_duration_ms=10)

    assert _channel(GpsL5, 1, synced_coherent_duration_ms=10)


def test_the_synced_configuration_is_checked_up_front():
    """
    A bad post-sync length must fail at construction, not part-way through a run
    when the channel finally reaches lock.

    NH20's 20 ms is the length the pilot alone would allow, and it is rejected --
    the epoch is shared, and I's CNAV symbol is 10 ms.  Switching the loops onto
    the pilot does not exempt the epoch from the data component riding in it.
    """
    assert coherent_duration_target_ms(GpsL5, build_signals(GpsL5, prns=[1])["G01"], 20) == 10
    assert _channel(GpsL5, 1, synced_coherent_duration_ms=10)

    with pytest.raises(ValueError, match="exceeds 10 ms"):
        _channel(GpsL5, 1, synced_coherent_duration_ms=20)


def test_a_dataless_pilot_has_no_symbol_limit():
    """L2 CL and L5 Q carry no data, so only Doppler bounds them."""
    for signal_type, pilot in ((GpsL2C, "L2CL"), (GpsL5, "L5Q")):
        signal = build_signals(signal_type, prns=[1])["G01"]
        component = signal.code_set.components[signal.code_set.index_of(pilot)]
        assert component.symbol_period_ms is None


# ---------------------------------------------------------------------------
# The loop filter has to suit the epoch, not only the epoch the channel may
# eventually extend to
# ---------------------------------------------------------------------------
#
# `_retune_for_update_period` narrows bandwidths when a channel EXTENDS, for a
# reason that applies just as much at construction: every gain scales with the
# update period, and a bandwidth chosen for a 1 ms epoch is far past the
# guideline on a 10 ms one.  Nothing enforced it on the starting configuration,
# so a caller who asked for a long epoch with ordinary bandwidths got a loop that
# never locked -- at any carrier-to-noise ratio, with nothing in the outputs to
# say why.  Found on a real collect, where L1C's natural 10 ms epoch made it the
# default rather than an unusual choice.


def _loop_params(coherent_duration_ms, **overrides):
    settings = dict(
        DLL_bandwidth_hz=2.0, PLL_bandwidth_hz=20.0, FLL_bandwidth_hz=50.0,
        coherent_duration_ms=coherent_duration_ms,
        EPL_chip_spacing=0.5, prompt_corr_circ_length_threshold=0.9,
    )
    settings.update(overrides)
    return tracking_channel.TrackingLoopParameters(**settings)


def test_a_one_millisecond_epoch_leaves_the_bandwidths_alone():
    """The common case must be untouched, and must not warn."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")          # any warning fails the test
        channel = _channel(GpsL1CA, 1)
    assert channel.loop_params.PLL_bandwidth_hz == 20.0
    assert channel.loop_params.FLL_bandwidth_hz == 50.0


@pytest.mark.parametrize("coherent_ms, cap_hz", [(5, 20.0), (10, 10.0), (20, 5.0)])
def test_a_long_epoch_narrows_every_loop_past_the_cap(coherent_ms, cap_hz):
    """
    Bn*T <= MAX_BANDWIDTH_TIME_PRODUCT, applied per loop, at the epoch the channel
    starts with.  The cap is the product divided by the epoch, so it tightens as
    the epoch lengthens.

    L1 C/A is the signal that can be asked for a long epoch from the very first
    one: it carries no overlay to strip, so nothing but its 20 ms data bit limits
    the accumulation.
    """
    with pytest.warns(RuntimeWarning, match="caps loop bandwidths"):
        channel = _channel(GpsL1CA, coherent_ms)
    assert channel.loop_params.PLL_bandwidth_hz == min(20.0, cap_hz)
    assert channel.loop_params.FLL_bandwidth_hz == min(50.0, cap_hz)
    # A loop already slow enough is left where it is.
    assert channel.loop_params.DLL_bandwidth_hz == min(2.0, cap_hz)
    product = channel.loop_params.FLL_bandwidth_hz * coherent_ms * 1e-3
    assert product <= tracking_channel.MAX_BANDWIDTH_TIME_PRODUCT + 1e-12


def test_a_long_epoch_actually_locks():
    """
    The regression itself, stated as behaviour rather than as numbers.

    L1C at its natural 10 ms epoch, on a strong signal, seeded from truth so
    acquisition plays no part.  Before the cap was applied here this locked 0% of
    the time -- not marginally, but never, and at every signal level tested.
    """
    from utils import sample_streaming
    from . import synthetic

    samp_rate, duration_ms, buffer_ms = 5_000_000, 6000, 200

    class _Acq:
        uptime_epoch_ms = 0.0
        acq_code_phase_seconds = 0.0
        acq_doppler_hz = 0.0
        signal_detected = True

    with pytest.warns(RuntimeWarning, match="caps loop bandwidths"):
        channels = create_tracking_channels(
            GpsL1C,
            signals=build_signals(GpsL1C, prns=[1]),
            acquisition_results={"G01": _Acq()},
            tracking_signal_ids=["G01"],
            loop_params=_loop_params(
                10, DLL_bandwidth_hz=0.5,
                subcarrier_bandwidth_hz=2.0, subcarrier_chip_spacing=0.04,
            ),
            output_capacity=duration_ms + 64,
        )
    adapter = channels["G01"]

    # 45 dB-Hz: unit signal power against complex noise of 2*sigma^2 over the band.
    sigma = np.sqrt(samp_rate / (2 * 10 ** 4.5))
    rng = np.random.default_rng(0)
    for i in range(duration_ms // buffer_ms):
        samples = synthetic.generate_l1c_samples(
            prn=1, start_sec=i * buffer_ms * 1e-3, duration_sec=buffer_ms * 1e-3,
            samp_rate=samp_rate, doppler_hz=0.0, code_phase_ms=0.0,
            noise_sigma=sigma, nav_bits=True, rng=rng,
        )
        adapter.process_sample_buffer(sample_streaming.SampleBuffer(
            samples=samples, start_uptime_ms=i * buffer_ms, samp_rate=samp_rate))

    outputs = adapter.outputs
    locked = outputs.pll_mode[outputs.valid].mean()
    assert locked > 0.8, f"L1C at 10 ms locked only {locked:.1%} of epochs"
    # And the pilot must come back at its 3/4 share of the power, which is the
    # check that it is the signal being tracked and not the noise floor.
    cn0 = outputs.cn0_dbhz[outputs.cn0_valid]
    pilot = float(np.nanmean(cn0[:, adapter.component_index("L1CP")]))
    assert pilot == pytest.approx(45.0 + 10 * np.log10(0.75), abs=1.5)


# ---------------------------------------------------------------------------
# The requested duration is a CAP the channel extends TO, never the one it
# opens with
# ---------------------------------------------------------------------------


class _Acq:
    """The handful of fields `create_tracking_channels` reads off an acquisition."""

    uptime_epoch_ms = 0.0
    acq_code_phase_seconds = 0.0
    acq_doppler_hz = 0.0
    signal_detected = True


def _factory_channel(signal_type, requested_ms, prns=(1,)):
    signals = build_signals(signal_type, prns=list(prns))
    sat = f"G{prns[0]:02d}"
    channels = create_tracking_channels(
        signal_type,
        signals=signals,
        acquisition_results={sat: _Acq()},
        tracking_signal_ids=[sat],
        loop_params=_loop_params(requested_ms),
        output_capacity=1000,
    )
    return channels[sat].channel


@pytest.mark.parametrize("signal_type", [GpsL1CA, GpsL2C, GpsL5, GpsL1C])
def test_every_signal_opens_at_one_correlation_interval(signal_type):
    """
    Not only the ones that have to find their bit boundary first.

    The reason is the FLL, and it applies to all four: acquisition leaves a
    Doppler residual of up to half a search bin, the frequency discriminator wraps
    at `half_range / T`, and a channel opened at 10 ms therefore has a pull-in
    range of +/-25 to +/-50 Hz -- which the residual can sit outside.  At one
    interval the range is ten times wider and the sinc loss is negligible, so the
    loop that has not converged yet gets the conditions it needs.

    L5 is the case that used to be wrong: its overlay phase comes from acquisition,
    so it knew its boundary at epoch zero and opened straight at the full length
    with the loops still pulling in.
    """
    channel = _factory_channel(signal_type, 10)
    assert channel.coherent_duration_ms == tracking_channel.CORRELATION_INTERVAL_MS
    # The loop filter has to agree, or the first epochs run 10x mistuned gains.
    assert channel.loop_params.coherent_duration_ms == (
        tracking_channel.CORRELATION_INTERVAL_MS
    )
    # ...and 10 ms is what it is heading for.
    assert channel._synced_coherent_duration_ms == 10


@pytest.mark.parametrize(
    "signal_type, requested_ms, expected_ms",
    [
        # Under the ceiling and a divisor: honoured exactly.
        (GpsL5, 5, 5),
        (GpsL1C, 5, 5),
        (GpsL1CA, 5, 5),
        (GpsL2C, 20, 20),
        # Past the ceiling: capped to the shortest data symbol.
        (GpsL5, 20, 10),
        (GpsL1C, 20, 10),
        (GpsL1CA, 40, 20),
        (GpsL2C, 40, 20),
        # Not a divisor of it: rounded DOWN to one.
        (GpsL5, 3, 2),
        (GpsL1CA, 15, 10),
        (GpsL2C, 8, 5),
    ],
)
def test_the_requested_duration_is_capped_not_exceeded(
    signal_type, requested_ms, expected_ms
):
    """
    A user tracking weak signals chooses a short integration deliberately, so a
    channel that quietly integrates for longer than they asked is as wrong as one
    that integrates for less.  L5 asked for 5 ms used to extend to 10 anyway,
    because the length lived in the signal's policy rather than in the request.
    """
    signal = build_signals(signal_type, prns=[1])["G01"]
    assert (
        coherent_duration_target_ms(signal_type, signal, requested_ms, warn=False)
        == expected_ms
    )


@pytest.mark.parametrize(
    "signal_type, requested_ms, expected_ms",
    [(GpsL5, 20, 10), (GpsL5, 3, 2), (GpsL2C, 8, 5)],
)
def test_a_capped_request_says_so(signal_type, requested_ms, expected_ms):
    """
    Warned, not raised, and not silent.  Raising would refuse a run over a knob
    the caller can only get right by knowing each signal's symbol structure;
    staying quiet would leave them believing the loop is something it is not.
    """
    signal = build_signals(signal_type, prns=[1])["G01"]
    with pytest.warns(RuntimeWarning, match=f"capped at {expected_ms} ms"):
        assert (
            coherent_duration_target_ms(signal_type, signal, requested_ms)
            == expected_ms
        )


def test_l2c_now_extends_like_every_other_signal():
    """
    L2C had no overlay, so the old `synced_coherent_duration_ms` default left it at
    one interval forever however long an integration was asked for -- the one
    signal whose configured length was ignored outright rather than merely
    overridden.  CM's 20 ms period IS one symbol, so its boundary is known from the
    code phase and there was never anything to wait for but lock.
    """
    channel = _factory_channel(GpsL2C, 20)
    assert channel.coherent_duration_ms == 1
    assert channel._synced_coherent_duration_ms == 20
