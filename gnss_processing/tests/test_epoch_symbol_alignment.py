"""
The epoch grid must open on a real data symbol boundary, not on a convenient one.

Both regressions here are the same mistake seen from two sides: code that decides
where a symbol starts by looking at the code phase lattice alone, on signals where
that lattice says nothing about the data.

`TrackingChannel` anchors its epoch grid on multiples of the symbol period in
*code phase*.  That is a symbol boundary only once something has tied the code
phase counter to the data -- an overlay's phase, or a primary code period at least
as long as the symbol.  GPS L1 C/A has neither: a 1 ms code under a 20 ms bit, and
an acquisition code phase known only modulo one code period, so the counter's
origin is an arbitrary code period and its 20 ms lattice sits an unknown 0-19 ms
away from the bit lattice.  Anchoring on it regardless misplaces every epoch by
that offset, and the offset survives into the pseudorange as a whole number of
milliseconds -- 300 km each, which is why this is worth its own file.

The fixtures deliberately generate signals whose bit boundary is NOT on a multiple
of 20 ms of code phase.  Leaving it there (the generator default) makes the buggy
and the correct anchor agree, which is exactly how this went unnoticed.
"""

from __future__ import annotations

import numpy as np
import pytest

from utils import sample_streaming, tracking_channel, tracking_io
from utils.nav import symbols as nav_symbols
from utils.signal_interfaces import (
    TRACKING_POLICIES,
    GpsL1CA,
    GpsL2C,
    build_signals,
    create_tracking_channels,
    requires_bit_sync,
)

from . import synthetic

SAMP_RATE = 5_000_000
PRN = 1
SAT = f"G{PRN:02d}"
# Coprime with 5 and with 20, so neither a 5 ms epoch grid nor a 20 ms one can
# land on the true boundary by accident.
BIT_PHASE_PERIODS = 7


def _acquisition_result(code_phase_ms: float, doppler_hz: float):
    """The handful of fields `create_tracking_channels` reads off an acquisition."""

    class _Result:
        uptime_epoch_ms = 0.0
        acq_code_phase_seconds = code_phase_ms * 1e-3
        acq_doppler_hz = doppler_hz
        signal_detected = True

    return _Result()


def _track(
    signal_type,
    generator,
    *,
    coherent_duration_ms: int,
    duration_ms: int,
    bit_phase_periods: int = 0,
    doppler_hz: float = 0.0,
    code_phase_ms: float = 0.0,
    buffer_duration_ms: int = 200,
):
    """
    Track through `create_tracking_channels`, which is the pipeline's own path.

    Going through the factory rather than building a `TrackingChannel` directly is
    the point: the factory is what turns a requested epoch length into "start at
    one interval, extend once the loops are locked and the boundary is known" --
    for every signal, not only the ones that have to measure that boundary -- and a
    test that bypassed it would not exercise the fix at all.
    """
    signals = build_signals(signal_type, prns=[PRN])
    loop_params = tracking_channel.TrackingLoopParameters(
        PLL_bandwidth_hz=15.0,
        FLL_bandwidth_hz=50.0,
        DLL_bandwidth_hz=2.0,
        EPL_chip_spacing=0.5,
        coherent_duration_ms=coherent_duration_ms,
        prompt_corr_circ_length_threshold=0.9,
    )
    channels = create_tracking_channels(
        signal_type,
        signals=signals,
        acquisition_results={SAT: _acquisition_result(code_phase_ms, doppler_hz)},
        tracking_signal_ids=[SAT],
        loop_params=loop_params,
        output_capacity=duration_ms // coherent_duration_ms + 64,
        start_mode_pll=True,  # no pull-in to wait through; the signal is noiseless
    )
    adapter = channels[SAT]

    extra = {"bit_phase_periods": bit_phase_periods} if bit_phase_periods else {}
    for i in range(duration_ms // buffer_duration_ms):
        samples = generator(
            prn=PRN,
            start_sec=i * buffer_duration_ms * 1e-3,
            duration_sec=buffer_duration_ms * 1e-3,
            samp_rate=SAMP_RATE,
            doppler_hz=doppler_hz,
            code_phase_ms=code_phase_ms,
            nav_bits=True,
            **extra,
        )
        adapter.process_sample_buffer(
            sample_streaming.SampleBuffer(
                samples=samples,
                start_uptime_ms=i * buffer_duration_ms,
                samp_rate=SAMP_RATE,
            )
        )
    return adapter


# ---------------------------------------------------------------------------
# Which signals have to find the boundary the hard way
# ---------------------------------------------------------------------------


def test_only_l1ca_needs_bit_sync():
    """
    The predicate that decides all of this, stated against all four signals.

    L2C is the one worth naming: it needs no bit sync AND has no overlay, and
    conflating those two facts is the second bug this file guards.
    """
    from utils.signal_interfaces import GpsL1C, GpsL5

    needs = {
        st.signal_type_id: requires_bit_sync(st, st(prn=PRN))
        for st in (GpsL1CA, GpsL2C, GpsL5, GpsL1C)
    }
    assert needs == {
        "GPS_L1CA": True,
        "GPS_L2C": False,
        "GPS_L5": False,
        "GPS_L1C": False,
    }


def test_overlay_table_is_not_the_complement_of_bit_sync():
    """
    L2C needs neither bit sync nor an overlay.  Deriving one table from the other
    is what made `utils.nav.symbols` wait forever for a sync L2C never signals.
    """
    assert nav_symbols.HAS_OVERLAY["GPS_L2C"] is False
    assert nav_symbols.NEEDS_BIT_SYNC["GPS_L2C"] is False
    for signal_type_id in nav_symbols.HAS_OVERLAY:
        if signal_type_id == "GPS_L2C":
            continue
        assert nav_symbols.HAS_OVERLAY[signal_type_id] == (
            not nav_symbols.NEEDS_BIT_SYNC[signal_type_id]
        )


# ---------------------------------------------------------------------------
# L1 C/A: the boundary has to be measured before the grid can be lengthened
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def l1ca_offset_channel():
    """
    8 s of L1 C/A whose bit boundary is 7 code periods off the 20 ms lattice.

    Long enough for the channel to fill its bit-sync window (one second minimum,
    two seconds nominal) and then run extended for a good while afterwards.
    """
    return _track(
        GpsL1CA,
        synthetic.generate_l1ca_samples,
        coherent_duration_ms=5,
        duration_ms=8000,
        bit_phase_periods=BIT_PHASE_PERIODS,
    )


def test_channel_recovers_the_true_bit_phase(l1ca_offset_channel):
    """The measured offset must be the one the generator actually used."""
    channel = l1ca_offset_channel.channel
    assert channel._bit_sync is not None
    assert channel._bit_sync.synced
    assert channel._symbol_phase_offset_ms == BIT_PHASE_PERIODS


def test_grid_starts_short_and_extends_to_the_requested_length(l1ca_offset_channel):
    """
    A requested 5 ms epoch becomes "start at 1 ms, extend to 5 ms once the boundary
    is known".  The short epochs are not a fallback -- they are the only length at
    which the boundary is measurable, since folding destroys the 1 ms structure the
    histogram is built from.
    """
    outputs = l1ca_offset_channel.outputs
    durations = outputs.epoch_duration_ms[outputs.valid]
    assert durations[0] == 1.0
    assert durations[-1] == 5.0


def test_extended_epochs_never_straddle_a_bit_boundary(l1ca_offset_channel):
    """
    The property that actually matters.  Every extended epoch must tile the bit
    grid, which means its start sits a whole number of epochs from the true
    boundary -- not from code phase zero.

    Before the fix this held with the offset removed, which is precisely the error:
    the grid tiled a lattice that was not the data's.
    """
    outputs = l1ca_offset_channel.outputs
    valid = outputs.valid
    code_phase = outputs.code_phase_ms[valid]
    extended = outputs.epoch_duration_ms[valid] == 5.0

    offsets = (np.round(code_phase[extended]) - BIT_PHASE_PERIODS) % 5
    assert np.all(offsets == 0)
    # And the buggy anchor is genuinely a different answer, so this test would
    # have failed before the fix rather than passing for free.
    assert np.any(np.round(code_phase[extended]) % 5 != 0)


def test_symbols_land_on_the_true_bit_boundaries(l1ca_offset_channel):
    """
    What the pseudorange ultimately rests on: the code phase `utils.nav.symbols`
    reports for a symbol start is the code phase at which that data bit really
    began.  An error here is an error in transmit time, and at 1 ms per code period
    it is 300 km of range.
    """
    stream = nav_symbols.extract(l1ca_offset_channel.outputs, "GPS_L1CA")
    assert len(stream) > 100
    residual = (np.round(stream.code_phase_ms) - BIT_PHASE_PERIODS) % 20
    assert np.all(residual == 0)


# Every epoch length L1 C/A permits: the divisors of the 20 ms bit.  1 ms needs no
# extension at all, 20 ms leaves the nav layer a single bin to bit-sync over, and
# the middle ones are the ordinary case.
@pytest.mark.parametrize("coherent_duration_ms", [1, 2, 4, 5, 10, 20])
def test_symbol_boundary_is_found_at_every_permitted_epoch_length(coherent_duration_ms):
    """
    The regression, stated as the measurement it broke.

    Where a bit starts is a property of the signal, not of the receiver's
    integration time, so every one of these must give the same answer.  On a real
    2021 collect they did not: 5 ms epochs put it 1-2 ms out per satellite and
    scattered the position fix by 283 km.
    """
    adapter = _track(
        GpsL1CA,
        synthetic.generate_l1ca_samples,
        coherent_duration_ms=coherent_duration_ms,
        duration_ms=8000,
        bit_phase_periods=BIT_PHASE_PERIODS,
    )
    outputs = adapter.outputs
    durations = outputs.epoch_duration_ms[outputs.valid]
    assert durations[0] == 1.0
    assert durations[-1] == coherent_duration_ms

    stream = nav_symbols.extract(outputs, "GPS_L1CA")
    assert len(stream) > 100
    residual = (np.round(stream.code_phase_ms) - BIT_PHASE_PERIODS) % 20
    assert np.all(residual == 0)


def _track_without_the_factory(coherent_duration_ms: int, duration_ms: int = 6000):
    """
    Build the channel by hand, the way `tests/test_coherent_integration.py` does.

    That path is legitimate for measuring coherent gain and stays supported, so the
    channel does not refuse it -- which is exactly why the nav layer has to.
    """
    signal = build_signals(GpsL1CA, prns=[PRN])[SAT]
    policy = TRACKING_POLICIES["GPS_L1CA"]
    channel = tracking_channel.TrackingChannel(
        loop_params=tracking_channel.TrackingLoopParameters(
            PLL_bandwidth_hz=15.0,
            FLL_bandwidth_hz=50.0,
            DLL_bandwidth_hz=2.0,
            EPL_chip_spacing=0.5,
            coherent_duration_ms=coherent_duration_ms,
            prompt_corr_circ_length_threshold=0.9,
        ),
        signal_params=tracking_channel.TrackingSignalParameters(
            code_set=signal.code_set,
            nominal_code_rate_chips_per_sec=GpsL1CA.tracking_code_rate_chips_per_sec,
            carrier_freq_hz=GpsL1CA.carrier_freq_hz,
            primary_period_ms=GpsL1CA.primary_period_ms,
        ),
        initial_signal_state=tracking_channel.TrackingSignalState(
            uptime_epoch_ms=0.0,
            code_phase_ms=0.0,
            code_rate_ms_per_sec=1e3,
            carrier_phase_cycles=0.0,
            carrier_rate_cyc_per_sec=0.0,
        ),
        output_capacity=duration_ms + 64,
        discriminator_policy=policy.discriminator_policy,
    )
    channel.loop_state.mode = tracking_channel.TrackingLoopMode.PLL
    for i in range(duration_ms // 200):
        channel.process_sample_buffer(
            sample_streaming.SampleBuffer(
                samples=synthetic.generate_l1ca_samples(
                    prn=PRN,
                    start_sec=i * 0.2,
                    duration_sec=0.2,
                    samp_rate=SAMP_RATE,
                    doppler_hz=0.0,
                    code_phase_ms=0.0,
                    nav_bits=True,
                    bit_phase_periods=BIT_PHASE_PERIODS,
                ),
                start_uptime_ms=i * 200,
                samp_rate=SAMP_RATE,
            )
        )
    return channel


def test_extract_refuses_a_grid_the_channel_never_anchored():
    """
    The guard.  A hand-built multi-interval L1 C/A channel anchors on the code
    phase lattice, and nothing in its outputs says so -- the arrays have the same
    shape and entirely plausible contents as a correctly anchored run.  Refusing is
    the only honest answer: the alternative is a position fix that converges,
    reports a healthy DOP, and sits 300 km from the truth.
    """
    channel = _track_without_the_factory(coherent_duration_ms=5)
    outputs = channel.outputs
    assert not outputs.bit_synced[outputs.valid].any()
    with pytest.raises(ValueError, match="never anchored its grid"):
        nav_symbols.extract(outputs, "GPS_L1CA")


def test_one_millisecond_epochs_need_no_channel_bit_sync():
    """
    The other side of the guard: at one code period per epoch the boundary is
    still resolvable here, so a hand-built channel is accepted and this module
    finds the phase itself.  Cutting that path off would be over-correction.
    """
    channel = _track_without_the_factory(coherent_duration_ms=1)
    outputs = channel.outputs
    assert not outputs.bit_synced[outputs.valid].any()
    stream = nav_symbols.extract(outputs, "GPS_L1CA")
    assert len(stream) > 100
    assert stream.bit_sync is not None and stream.bit_sync.synced
    residual = (np.round(stream.code_phase_ms) - BIT_PHASE_PERIODS) % 20
    assert np.all(residual == 0)


def test_bit_synced_survives_the_hdf5_round_trip(tmp_path, l1ca_offset_channel):
    """
    The flag is only useful if it reaches the notebook that consumes it, which
    reads a file rather than a live channel.  A tracking file that dropped it would
    put every reloaded L1 C/A run back on the wrong side of the guard.
    """
    path = tracking_io.save_tracking_results(
        tmp_path / "run.h5",
        {SAT: l1ca_offset_channel},
        collect_id="test",
        signal_type_id="GPS_L1CA",
        samp_rate=SAMP_RATE,
    )
    reloaded = tracking_io.load_tracking_results(path).results[SAT].outputs
    original = l1ca_offset_channel.outputs
    # False over the short epochs before the extension, True from it onwards --
    # the file has to preserve where that edge falls, not merely that it exists.
    assert np.array_equal(reloaded.bit_synced, original.bit_synced[original.valid])
    assert not reloaded.bit_synced[0]
    assert reloaded.bit_synced[-1]

    stream = nav_symbols.extract(reloaded, "GPS_L1CA")
    residual = (np.round(stream.code_phase_ms) - BIT_PHASE_PERIODS) % 20
    assert np.all(residual == 0)


# ---------------------------------------------------------------------------
# L2C: no overlay to wait for
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def l2c_channel():
    """3 s of L2C.  One CNAV symbol is one 20 ms CM period, so this is 150 symbols."""
    return _track(
        GpsL2C,
        synthetic.generate_l2c_samples,
        coherent_duration_ms=5,
        duration_ms=3000,
    )


def test_l2c_never_reports_an_overlay_sync(l2c_channel):
    """
    The premise of the bug: L2C has no tiered code, so `overlay_synced` is False
    for every epoch of every run.  It is not a flag that is late -- it never comes.
    """
    outputs = l2c_channel.outputs
    assert not outputs.overlay_synced[outputs.valid].any()


def test_l2c_yields_symbols_without_an_overlay_sync(l2c_channel):
    """
    Gating the usable epochs on `overlay_synced` discarded every one of them, and
    the failure was silent: an empty stream, no error, and a navigation notebook
    reporting "no usable symbols" for every satellite it had just tracked cleanly.
    """
    stream = nav_symbols.extract(l2c_channel.outputs, "GPS_L2C")
    assert len(stream) > 100
    # The CM code period IS the symbol, so the code phase locates the boundary
    # with nothing to search for -- the reason L2C needs neither mechanism.
    assert np.all(np.round(stream.code_phase_ms) % 20 == 0)


def test_l2c_symbols_carry_the_generated_data(l2c_channel):
    """
    A stream of the right length made of noise would pass the test above.  The
    generator alternates the symbol every CM period, so the recovered signs must
    alternate too.
    """
    stream = nav_symbols.extract(l2c_channel.outputs, "GPS_L2C")
    signs = np.sign(stream.soft)
    assert np.all(signs[1:] != signs[:-1])
