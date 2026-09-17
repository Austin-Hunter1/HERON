"""
The whole chain, on a synthetic signal: samples -> tracking -> symbols -> time.

Everything else in the nav test suite checks one stage in isolation against the
interface spec.  This file checks that the stages fit together, which is the part
no amount of unit testing reaches -- in particular that

  * the tracking channel's epoch grid really does land on symbol boundaries once
    the overlay is synced, which is the assumption `utils.nav.symbols` is built on;
  * `code_phase_ms` at a decoded message's first symbol is the anchor that turns a
    time of week into a transmit time;
  * the Viterbi decoder's symbol-phase and polarity searches cope with what a real
    channel hands them.

L5 is the signal that can be checked against a real collect elsewhere, so it gets
the fullest treatment here.  L1 C/A goes through its own path -- statistical bit
sync rather than an overlay -- so it is exercised too.
"""

from __future__ import annotations

import numpy as np
import pytest

from utils import sample_streaming, tracking_channel
from utils.nav import cnav, lnav
from utils.nav import primitives as prim
from utils.nav import symbols as nav_symbols
from utils.signal_interfaces import (
    GpsL1CA,
    GpsL5,
    TRACKING_POLICIES,
    build_signals,
    coherent_duration_target_ms,
)

from . import synthetic

L5_SAMP_RATE = 25_000_000
L1CA_SAMP_RATE = 5_000_000
PRN = 1


def track(
    *,
    signal_type,
    samp_rate: float,
    generator,
    nav_symbols_sequence: np.ndarray,
    duration_ms: int,
    coherent_duration_ms: int,
    # The length to extend to once the channel is synced. Defaults to no extension,
    # which is what the L1 C/A cases want: their epochs stay one code period long
    # and `utils.nav.symbols` finds the bit boundary itself, which is the path
    # those tests exist to cover.
    extend_to_ms: int | None = None,
    doppler_hz: float = 0.0,
    code_phase_ms: float = 0.0,
    noise_sigma: float = 0.0,
    buffer_duration_ms: int = 200,
    seed: int = 0,
):
    """
    Generate, track, and return the channel -- the same construction the notebook
    uses, via TRACKING_POLICIES, so this cannot drift from the real pipeline.
    """
    signal = build_signals(signal_type, prns=[PRN])[f"G{PRN:02d}"]
    policy = TRACKING_POLICIES[signal_type.signal_type_id]

    loop_params = tracking_channel.TrackingLoopParameters(
        PLL_bandwidth_hz=15.0,
        FLL_bandwidth_hz=50.0,
        DLL_bandwidth_hz=2.0,
        EPL_chip_spacing=0.5,
        coherent_duration_ms=coherent_duration_ms,
        prompt_corr_circ_length_threshold=0.9,
    )
    initial_state = tracking_channel.TrackingSignalState(
        uptime_epoch_ms=0.0,
        code_phase_ms=code_phase_ms,
        code_rate_ms_per_sec=(1.0 + doppler_hz / signal_type.carrier_freq_hz) * 1e3,
        carrier_phase_cycles=0.0,
        carrier_rate_cyc_per_sec=doppler_hz,
    )
    signal_params = tracking_channel.TrackingSignalParameters(
        code_set=signal.code_set,
        nominal_code_rate_chips_per_sec=signal_type.tracking_code_rate_chips_per_sec,
        carrier_freq_hz=signal_type.carrier_freq_hz,
        primary_period_ms=signal_type.primary_period_ms,
    )
    channel = tracking_channel.TrackingChannel(
        loop_params=loop_params,
        signal_params=signal_params,
        initial_signal_state=initial_state,
        output_capacity=duration_ms + 64,
        discriminator_policy=policy.discriminator_policy,
        synced_policy=policy.synced_discriminator_policy,
        synced_coherent_duration_ms=coherent_duration_target_ms(
            signal_type, signal, extend_to_ms or coherent_duration_ms
        ),
        overlay_search=policy.overlay_search,
        overlay_prompts_to_observe=policy.overlay_prompts_to_observe,
    )

    rng = np.random.default_rng(seed)
    for i in range(duration_ms // buffer_duration_ms):
        samples = generator(
            prn=PRN,
            start_sec=i * buffer_duration_ms * 1e-3,
            duration_sec=buffer_duration_ms * 1e-3,
            samp_rate=samp_rate,
            doppler_hz=doppler_hz,
            code_phase_ms=code_phase_ms,
            noise_sigma=noise_sigma,
            nav_bits=nav_symbols_sequence,
            rng=rng,
        )
        channel.process_sample_buffer(
            sample_streaming.SampleBuffer(
                samples=samples,
                start_uptime_ms=i * buffer_duration_ms,
                samp_rate=samp_rate,
            )
        )
    return channel


def cnav_symbol_sequence(*, prn: int, first_tow: int, count: int) -> np.ndarray:
    """A run of CNAV messages as +/-1 channel symbols, ready to modulate."""
    messages = [
        cnav.build_message(
            prn=prn,
            message_type=(10, 11, 30, 0)[i % 4],
            tow_count=first_tow + i,
        )
        for i in range(count)
    ]
    return 1.0 - 2.0 * cnav.encode_stream(messages).astype(float)


# ---------------------------------------------------------------------------
# The tracking-side assumptions the symbol extractor rests on
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def l5_channel():
    """
    26 s of L5.  The length is set by what the decoder needs, not by the tracking:
    a CNAV message is 6 s, the channel spends the first second or so pulling in and
    syncing NH20, and the stream has to hold at least two *whole* messages after
    that for the TOW-advance check to have two points to compare.  Module-scoped,
    because tracking 26 s at 25 Msps is the expensive part of this file.
    """
    sequence = cnav_symbol_sequence(prn=PRN, first_tow=100000, count=8)
    return track(
        signal_type=GpsL5,
        samp_rate=L5_SAMP_RATE,
        generator=synthetic.generate_l5_samples,
        nav_symbols_sequence=sequence,
        duration_ms=26000,
        coherent_duration_ms=1,
        extend_to_ms=10,
    )


def test_l5_epoch_grid_extends_to_one_cnav_symbol(l5_channel):
    """
    The premise of `utils.nav.symbols` for L5: once NH is stripped and the PLL has
    locked, the epoch becomes 10 ms -- exactly one CNAV symbol on L5I.
    """
    outputs = l5_channel.outputs
    durations = outputs.epoch_duration_ms[outputs.valid]
    # The channel must START at 1 ms: until NH20 is stripped, an accumulation
    # longer than one overlay chip spans a sign change, and
    # `validate_coherent_duration` refuses to build such a channel.
    assert durations[0] == 1.0
    assert durations[-1] == 10.0
    signal = build_signals(GpsL5, prns=[PRN])[f"G{PRN:02d}"]
    assert coherent_duration_target_ms(GpsL5, signal, 20, warn=False) == 10


def test_l5_extended_epochs_are_symbol_aligned(l5_channel):
    """
    Every 10 ms epoch must start on a multiple of 10 ms of code phase.  If it did
    not, each symbol would straddle two CNAV symbols and cancel -- and the decoder
    would simply never sync, with nothing to point at.
    """
    outputs = l5_channel.outputs
    valid = outputs.valid
    long_epochs = outputs.epoch_duration_ms[valid] == 10.0
    code_phase = outputs.code_phase_ms[valid][long_epochs]
    offsets = code_phase % 10.0
    # Code phase is a filtered estimate, so allow a sub-chip wobble about the grid.
    assert np.all((offsets < 0.05) | (offsets > 9.95)), (
        f"worst offset from the 10 ms grid: {np.abs(((offsets + 5) % 10) - 5).max():.4f} ms"
    )


def test_l5_overlay_syncs_and_the_flag_is_recorded(l5_channel):
    outputs = l5_channel.outputs
    synced = outputs.overlay_synced[outputs.valid]
    assert not synced[0], "the channel cannot be synced before it has observed anything"
    assert synced[-1], "NH20 should have synced well inside 14 s"


# ---------------------------------------------------------------------------
# Symbol extraction
# ---------------------------------------------------------------------------


def test_l5_symbol_extraction_produces_one_symbol_per_epoch(l5_channel):
    stream = nav_symbols.extract(l5_channel.outputs, "GPS_L5")
    assert stream.symbol_period_ms == 10
    assert stream.epochs_per_symbol == 1  # extended to 10 ms epochs, 10 ms symbols
    assert len(stream) > 2000, "24 s at 100 sps should leave several messages"
    assert len(stream.uptime_ms) == len(stream)
    assert len(stream.code_phase_ms) == len(stream)


def test_symbol_uptimes_advance_by_one_symbol_period(l5_channel):
    stream = nav_symbols.extract(l5_channel.outputs, "GPS_L5")
    steps = np.diff(stream.uptime_ms)
    assert np.allclose(steps, stream.symbol_period_ms, atol=0.05)


def test_extract_rejects_an_unknown_signal(l5_channel):
    with pytest.raises(ValueError, match="no symbol mapping"):
        nav_symbols.extract(l5_channel.outputs, "GALILEO_E1")


def test_extract_rejects_an_epoch_that_does_not_divide_the_symbol(l5_channel):
    """
    A 3 ms coherent duration cannot tile a 10 ms symbol.  Better to say so than to
    hand back symbols that quietly straddle boundaries.
    """
    outputs = l5_channel.outputs
    doctored = outputs.epoch_duration_ms.copy()
    outputs.epoch_duration_ms[outputs.valid] = 3.0
    try:
        with pytest.raises(ValueError, match="does not divide"):
            nav_symbols.extract(outputs, "GPS_L5")
    finally:
        outputs.epoch_duration_ms[:] = doctored


# ---------------------------------------------------------------------------
# End to end: samples in, time of week out
# ---------------------------------------------------------------------------


def test_l5_cnav_decodes_from_tracked_samples(l5_channel):
    stream = nav_symbols.extract(l5_channel.outputs, "GPS_L5")
    result = cnav.decode(
        stream.soft,
        message_duration_s=cnav.L5_MESSAGE_DURATION_S,
        expected_prn=PRN,
    )
    assert result.synced, "no CNAV message survived the whole chain"
    assert result.tow_is_consistent()
    tows = [m.tow_count for m in result.messages]
    assert tows == sorted(tows)
    assert all(100000 <= t < 100008 for t in tows), tows
    assert {m.message_type for m in result.messages} <= {0, 10, 11, 30}


def test_decoded_tow_anchors_a_transmit_time(l5_channel):
    """
    The point of the whole exercise: a decoded message plus `code_phase_ms` gives
    the transmit time at every later epoch, which is what a pseudorange is built
    from.  Check the anchor is self-consistent -- code phase between two messages
    must advance by exactly the message duration in satellite time.
    """
    stream = nav_symbols.extract(l5_channel.outputs, "GPS_L5")
    result = cnav.decode(stream.soft, expected_prn=PRN)
    assert len(result.messages) >= 2

    first, second = result.messages[0], result.messages[1]
    # `symbol_index` indexes the channel-symbol stream directly -- the same array
    # handed to the decoder.  A CNAV message is 300 bits and 600 channel symbols,
    # and on L5 one channel symbol is one 10 ms epoch, so 600 symbols is 6 s.
    first_symbol = first.symbol_index
    second_symbol = second.symbol_index
    assert second_symbol - first_symbol == cnav.SYMBOLS_PER_MESSAGE
    assert second_symbol < len(stream)

    code_phase_advance_ms = (
        stream.code_phase_ms[second_symbol] - stream.code_phase_ms[first_symbol]
    )
    tow_advance_s = second.tow_at_message_start_s - first.tow_at_message_start_s
    assert code_phase_advance_ms == pytest.approx(tow_advance_s * 1000.0, abs=1.0)


def test_l5_cnav_decodes_through_noise():
    sequence = cnav_symbol_sequence(prn=PRN, first_tow=122222, count=4)
    channel = track(
        signal_type=GpsL5,
        samp_rate=L5_SAMP_RATE,
        generator=synthetic.generate_l5_samples,
        nav_symbols_sequence=sequence,
        duration_ms=14000,
        coherent_duration_ms=1,
        extend_to_ms=10,
        doppler_hz=1500.0,
        code_phase_ms=0.31,
        noise_sigma=1.0,
        seed=17,
    )
    stream = nav_symbols.extract(channel.outputs, "GPS_L5")
    result = cnav.decode(stream.soft, expected_prn=PRN)
    assert result.synced
    assert all(122222 <= m.tow_count < 122226 for m in result.messages)


# ---------------------------------------------------------------------------
# L1 C/A: the other symbol path, through statistical bit sync
# ---------------------------------------------------------------------------


def test_l1ca_lnav_decodes_from_tracked_samples():
    """
    L1 C/A has no overlay, so this exercises the bit-sync path: 1 ms epochs, a
    boundary found from the transition histogram, then twenty summed per symbol.
    """
    frame = lnav.build_frame(first_tow_count=54321)
    sequence = 1.0 - 2.0 * frame.astype(float)  # 300 bits x 5 subframes at 50 bps
    channel = track(
        signal_type=GpsL1CA,
        samp_rate=L1CA_SAMP_RATE,
        generator=synthetic.generate_l1ca_samples,
        nav_symbols_sequence=sequence,
        duration_ms=34000,  # 30 s of frame plus lock-in
        coherent_duration_ms=1,
        doppler_hz=800.0,
        code_phase_ms=0.42,
    )

    stream = nav_symbols.extract(channel.outputs, "GPS_L1CA")
    assert stream.bit_sync is not None and stream.bit_sync.synced
    assert stream.epochs_per_symbol == 20
    assert stream.symbol_period_ms == 20

    result = lnav.decode(stream.soft)
    assert result.synced, "no LNAV subframe survived the whole chain"
    ids = [s.subframe_id for s in result.subframes]
    assert ids == sorted(set(ids)) or len(ids) >= 3
    tows = [s.tow_count for s in result.subframes]
    assert all(later - earlier == 1 for earlier, later in zip(tows, tows[1:]))
    assert tows[0] >= 54321


def test_l1ca_symbols_are_coherently_summed():
    """
    Twenty 1 ms prompts summed in phase, not one taken and nineteen discarded.
    The magnitude is the evidence.
    """
    frame = lnav.build_frame(first_tow_count=1000)
    sequence = 1.0 - 2.0 * frame.astype(float)
    channel = track(
        signal_type=GpsL1CA,
        samp_rate=L1CA_SAMP_RATE,
        generator=synthetic.generate_l1ca_samples,
        nav_symbols_sequence=sequence,
        duration_ms=10000,
        coherent_duration_ms=1,
    )
    outputs = channel.outputs
    stream = nav_symbols.extract(outputs, "GPS_L1CA")
    assert stream.bit_sync.synced

    epoch_magnitude = np.abs(outputs.prompt_corr[outputs.valid, 0]).mean()
    symbol_magnitude = np.abs(stream.values).mean()
    assert symbol_magnitude > 10 * epoch_magnitude
