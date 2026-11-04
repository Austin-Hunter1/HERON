"""
CNAV-2: frame structure, TOI decoding, and the honest edge of what is implemented.

Subframe 1 is decoded fully, so it is tested fully.  Subframes 2 and 3 are
de-interleaved but not LDPC-decoded, and the tests pin that boundary down
deliberately: the de-interleaving must be exactly right (so the symbols are
usable the moment an LDPC decoder lands) and the ephemeris entry points must fail
loudly rather than return something plausible.
"""

import numpy as np
import pytest

from utils.nav import cnav2
from utils.nav import primitives as prim


# ---------------------------------------------------------------------------
# Frame geometry
# ---------------------------------------------------------------------------


def test_frame_arithmetic_adds_up():
    assert (
        cnav2.SUBFRAME_1_SYMBOLS + cnav2.SUBFRAME_2_SYMBOLS + cnav2.SUBFRAME_3_SYMBOLS
        == cnav2.FRAME_SYMBOLS
    )
    assert cnav2.INTERLEAVED_SYMBOLS == prim.CNAV2_INTERLEAVER_ROWS * prim.CNAV2_INTERLEAVER_COLUMNS
    # Rate 1/2 LDPC on both subframes.
    assert cnav2.SUBFRAME_2_SYMBOLS == 2 * cnav2.SUBFRAME_2_BITS
    assert cnav2.SUBFRAME_3_SYMBOLS == 2 * cnav2.SUBFRAME_3_BITS


def test_frame_lasts_eighteen_seconds_at_one_symbol_per_code_period():
    """1800 symbols at one per 10 ms L1CD code period is exactly 18 s -- and also
    exactly one L1CO overlay period, which is what makes overlay sync give frame
    sync for free."""
    assert cnav2.FRAME_SYMBOLS * cnav2.SYMBOL_PERIOD_MS / 1000.0 == cnav2.FRAME_DURATION_S


def test_two_hour_period_holds_four_hundred_frames():
    assert (cnav2.TOI_MAX + 1) * cnav2.FRAME_DURATION_S == cnav2.TWO_HOUR_PERIOD_S


# ---------------------------------------------------------------------------
# Frame assembly and splitting
# ---------------------------------------------------------------------------


def test_build_and_split_round_trip():
    rng = np.random.default_rng(0)
    sf2 = rng.choice([-1.0, 1.0], cnav2.SUBFRAME_2_SYMBOLS)
    sf3 = rng.choice([-1.0, 1.0], cnav2.SUBFRAME_3_SYMBOLS)
    frame = cnav2.build_frame(toi=123, subframe_2=sf2, subframe_3=sf3)
    assert len(frame) == cnav2.FRAME_SYMBOLS

    head, out2, out3 = cnav2.split_frame(frame)
    assert len(head) == cnav2.SUBFRAME_1_SYMBOLS
    assert np.array_equal(out2, sf2)
    assert np.array_equal(out3, sf3)


def test_subframes_2_and_3_are_interleaved_together_not_separately():
    """
    The interleaver spans both subframes as one 1748-symbol block.  De-interleaving
    them separately is the natural-looking mistake, and this test fails loudly if
    the implementation ever drifts that way: symbols belonging to subframe 3 land
    inside the interleaved stream well before subframe 3's own region.
    """
    sf2 = np.zeros(cnav2.SUBFRAME_2_SYMBOLS)
    sf3 = np.ones(cnav2.SUBFRAME_3_SYMBOLS)
    frame = cnav2.build_frame(toi=0, subframe_2=sf2, subframe_3=sf3)
    body = frame[cnav2.SUBFRAME_1_SYMBOLS :]
    # If the two were interleaved separately, every subframe-3 symbol would sit in
    # the last 548 positions.  Interleaved together, they are spread throughout.
    assert body[: cnav2.SUBFRAME_2_SYMBOLS].sum() > 0


def test_split_frame_rejects_a_wrong_length():
    with pytest.raises(ValueError, match="1800 symbols"):
        cnav2.split_frame(np.zeros(1799))


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(subframe_2=np.zeros(10)),
        dict(subframe_3=np.zeros(10)),
    ],
)
def test_build_frame_rejects_wrong_subframe_lengths(kwargs):
    with pytest.raises(ValueError):
        cnav2.build_frame(toi=0, **kwargs)


# ---------------------------------------------------------------------------
# TOI
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("toi", [0, 1, 199, 399])
def test_toi_round_trip(toi):
    frames = cnav2.decode(cnav2.build_frame(toi=toi), frame_offset=0)
    assert len(frames) == 1
    assert frames[0].toi == toi
    assert frames[0].toi_is_valid
    assert frames[0].toi_confidence > 1.5


def test_default_toi_is_rejected_as_a_time():
    """511 is what the SV sends when message generation failed."""
    frame = cnav2.decode(cnav2.build_frame(toi=cnav2.TOI_DEFAULT), frame_offset=0)[0]
    assert frame.toi == cnav2.TOI_DEFAULT
    assert not frame.toi_is_valid
    with pytest.raises(ValueError, match="message generation failure"):
        frame.tow_at_next_frame_start_s(itow=10)


def test_toi_survives_noise():
    rng = np.random.default_rng(1)
    clean = cnav2.build_frame(toi=250)
    noisy = clean + rng.normal(0.0, 0.9, len(clean))
    frame = cnav2.decode(noisy, frame_offset=0)[0]
    assert frame.toi == 250


# ---------------------------------------------------------------------------
# Time of week
# ---------------------------------------------------------------------------


def test_tow_combines_itow_and_toi():
    frame = cnav2.decode(cnav2.build_frame(toi=100), frame_offset=0)[0]
    # ITOW 5 = five two-hour periods into the week; TOI 100 = 1800 s into that one.
    assert frame.tow_at_next_frame_start_s(itow=5) == 5 * 7200.0 + 100 * 18.0
    assert frame.tow_at_frame_start_s(itow=5) == 5 * 7200.0 + 100 * 18.0 - 18.0


def test_tow_refers_to_the_next_frame():
    """
    IS-GPS-800J 3.5.2: the TOI represents SV time at the start of the *next* frame.
    Reading it as this frame's time is an 18 second error -- 5,400 km of range.
    """
    frame = cnav2.decode(cnav2.build_frame(toi=7), frame_offset=0)[0]
    delta = frame.tow_at_next_frame_start_s(itow=0) - frame.tow_at_frame_start_s(itow=0)
    assert delta == cnav2.FRAME_DURATION_S


# ---------------------------------------------------------------------------
# Frame synchronisation
# ---------------------------------------------------------------------------


def stream_of_frames(tois, *, lead: int = 0, rng=None, noise: float = 0.0):
    rng = rng or np.random.default_rng(2)
    frames = [
        cnav2.build_frame(
            toi=toi,
            subframe_2=rng.choice([-1.0, 1.0], cnav2.SUBFRAME_2_SYMBOLS),
            subframe_3=rng.choice([-1.0, 1.0], cnav2.SUBFRAME_3_SYMBOLS),
        )
        for toi in tois
    ]
    stream = np.concatenate(frames)
    if lead:
        stream = np.concatenate([rng.choice([-1.0, 1.0], lead), stream])
    if noise:
        stream = stream + rng.normal(0.0, noise, len(stream))
    return stream


@pytest.mark.parametrize("lead", [0, 1, 733, 1799])
def test_blind_frame_search_finds_the_boundary(lead):
    stream = stream_of_frames([10, 11, 12], lead=lead)
    offset, confidence = cnav2.find_frame_offset(stream)
    assert offset == lead
    assert confidence > 1.5


def test_blind_search_needs_enough_symbols_to_cover_every_offset():
    with pytest.raises(ValueError, match="blind frame search needs"):
        cnav2.find_frame_offset(np.zeros(1000))


def test_decode_walks_every_whole_frame():
    stream = stream_of_frames([50, 51, 52], lead=400)
    frames = cnav2.decode(stream)
    assert [f.toi for f in frames] == [50, 51, 52]
    assert [f.symbol_index for f in frames] == [400, 400 + 1800, 400 + 3600]


def test_decode_accepts_an_overlay_supplied_offset():
    """
    The realistic path: L1CO's period is one frame, so a channel that has synced
    its overlay already knows the offset and the blind search is unnecessary.
    """
    stream = stream_of_frames([80, 81], lead=1234)
    frames = cnav2.decode(stream, frame_offset=1234)
    assert [f.toi for f in frames] == [80, 81]


def test_decode_rejects_an_offset_outside_the_stream():
    with pytest.raises(ValueError, match="outside the symbol stream"):
        cnav2.decode(np.zeros(2000), frame_offset=5000)


def test_toi_consistency_across_frames():
    stream = stream_of_frames([300, 301, 302])
    assert cnav2.toi_is_consistent(cnav2.decode(stream, frame_offset=0))


def test_toi_consistency_wraps_at_the_two_hour_boundary():
    stream = stream_of_frames([398, 399, 0])
    assert cnav2.toi_is_consistent(cnav2.decode(stream, frame_offset=0))


def test_toi_consistency_catches_a_frame_sync_off_by_one():
    frames = cnav2.decode(stream_of_frames([300, 301, 302]), frame_offset=0)
    broken = [
        cnav2.Cnav2Frame(
            toi=f.toi if i != 1 else 350,
            toi_confidence=f.toi_confidence,
            subframe_2_symbols=f.subframe_2_symbols,
            subframe_3_symbols=f.subframe_3_symbols,
            symbol_index=f.symbol_index,
        )
        for i, f in enumerate(frames)
    ]
    assert not cnav2.toi_is_consistent(broken)


# ---------------------------------------------------------------------------
# The implemented boundary
# ---------------------------------------------------------------------------


def test_a_block_that_fails_its_crc_is_refused():
    """
    A wrong ephemeris looks reasonable and puts the satellite kilometres away, so a
    block that fails CRC-24Q must give None, not a best guess.  The default message
    of `build_frame` has no valid CRC.
    """
    frame = cnav2.decode(cnav2.build_frame(toi=1), frame_offset=0)[0]
    assert cnav2.decode_subframe_2(frame) is None
    assert cnav2.decode_subframe_3(frame) is None


def test_undecoded_subframe_symbols_are_still_handed_back():
    """The soft symbols must survive de-interleaving intact, so an LDPC decoder can
    be dropped in later without touching anything here."""
    rng = np.random.default_rng(5)
    sf2 = rng.normal(0.0, 1.0, cnav2.SUBFRAME_2_SYMBOLS)
    sf3 = rng.normal(0.0, 1.0, cnav2.SUBFRAME_3_SYMBOLS)
    frame = cnav2.decode(
        cnav2.build_frame(toi=9, subframe_2=sf2, subframe_3=sf3), frame_offset=0
    )[0]
    assert np.allclose(frame.subframe_2_symbols, sf2)
    assert np.allclose(frame.subframe_3_symbols, sf3)


# ---------------------------------------------------------------------------
# Subframes 2 and 3: systematic read plus CRC
# ---------------------------------------------------------------------------

SF2_VALUES = {
    "wn": 2400, "itow": 61, "top": 3600.0, "health_l1c": 0, "ura_ed_index": -2,
    "toe": 7200.0, "delta_a": 123.5, "a_dot": -0.0021, "delta_n0": 1.2e-9 * 0 + 3 * 2.0**-44,
    "delta_n0_dot": -5 * 2.0**-57, "M0": 0.31, "e": 0.0123, "omega": -0.77, "Omega0": 0.42,
    "i0": 0.3, "delta_Omega_dot": 2 * 2.0**-44, "i_dot": -3 * 2.0**-44,
    "Cis": 1.0e-7, "Cic": -2.0e-7, "Crs": 55.0, "Crc": 240.0, "Cus": 3.0e-6, "Cuc": -4.0e-6,
    "af0": -3.2e-5, "af1": 1.0e-11, "af2": 0.0, "tgd": 5.0e-9, "isc_l1cp": 1.0e-9,
    "isc_l1cd": -2.0e-9, "wn_op": 2400 % 256,
}


def _symbols_for(information_bits, parity_symbols, sign=1.0, rng=None):
    """Systematic soft symbols: the information bits, then arbitrary parity symbols."""
    info = sign * (1.0 - 2.0 * information_bits.astype(float))
    rng = rng or np.random.default_rng(0)
    return np.concatenate([info, rng.normal(0.0, 1.0, parity_symbols)])


def _frame_with(sf2_bits, sf3_bits, sign=1.0):
    sf2 = _symbols_for(sf2_bits, cnav2.SUBFRAME_2_SYMBOLS - cnav2.SUBFRAME_2_BITS, sign)
    sf3 = _symbols_for(sf3_bits, cnav2.SUBFRAME_3_SYMBOLS - cnav2.SUBFRAME_3_BITS, sign)
    return cnav2.decode(cnav2.build_frame(toi=7, subframe_2=sf2, subframe_3=sf3), frame_offset=0)[0]


def _sf3_bits():
    rng = np.random.default_rng(11)
    return prim.crc24q_append(rng.integers(0, 2, cnav2.SUBFRAME_3_BITS - 24).astype(np.uint8))


def test_subframe_2_fields_round_trip_through_the_whole_frame_path():
    frame = _frame_with(cnav2.build_subframe_2(SF2_VALUES), _sf3_bits())
    sf2 = cnav2.decode_subframe_2(frame)
    assert sf2 is not None and not sf2.inverted
    for name, value in SF2_VALUES.items():
        scale = cnav2.SUBFRAME_2_FIELDS[name][3]
        assert sf2[name] == pytest.approx(value, abs=scale), name


def test_subframe_3_bits_survive_and_pass_crc():
    bits = _sf3_bits()
    sf3 = cnav2.decode_subframe_3(_frame_with(cnav2.build_subframe_2(SF2_VALUES), bits))
    assert sf3 is not None
    assert np.array_equal(sf3.bits, bits)


def test_an_inverted_polarity_is_found_by_the_crc():
    frame = _frame_with(cnav2.build_subframe_2(SF2_VALUES), _sf3_bits(), sign=-1.0)
    sf2 = cnav2.decode_subframe_2(frame)
    assert sf2 is not None and sf2.inverted
    assert sf2["itow"] == 61


def test_one_wrong_symbol_costs_the_frame_and_nothing_is_guessed():
    sf2_bits = cnav2.build_subframe_2(SF2_VALUES)
    symbols = _symbols_for(sf2_bits, 600)
    symbols[100] = -symbols[100]
    frame = cnav2.decode(
        cnav2.build_frame(
            toi=7, subframe_2=symbols,
            subframe_3=_symbols_for(_sf3_bits(), cnav2.SUBFRAME_3_SYMBOLS - 274),
        ),
        frame_offset=0,
    )[0]
    assert cnav2.decode_subframe_2(frame) is None
    assert cnav2.decode_subframe_3(frame) is not None


def test_the_layout_table_has_no_overlap_and_matches_the_note_anchors():
    """toe ends at bit 49 and a_f0 begins at bit 471 (the project note); no field overlaps."""
    spans = sorted((start, start + width - 1, name) for name, (start, width, *_r) in cnav2.SUBFRAME_2_FIELDS.items())
    for (_, end, name), (start, _, nxt) in zip(spans, spans[1:]):
        assert end < start, (name, nxt)
    assert cnav2.SUBFRAME_2_FIELDS["toe"][0] + cnav2.SUBFRAME_2_FIELDS["toe"][1] - 1 == 49
    assert cnav2.SUBFRAME_2_FIELDS["af0"][0] == 471
    assert max(end for _, end, _ in spans) <= 574


def test_a_field_that_does_not_fit_is_refused():
    with pytest.raises(ValueError, match="does not fit"):
        cnav2.build_subframe_2({"itow": 300})


def test_frame_start_time_uses_the_previous_toi_and_this_itow():
    previous = cnav2.decode(cnav2.build_frame(toi=100), frame_offset=0)[0]
    this = _frame_with(cnav2.build_subframe_2(SF2_VALUES), _sf3_bits())
    sf2 = cnav2.decode_subframe_2(this)
    assert cnav2.frame_start_tow_s(previous, sf2) == 61 * 7200.0 + 100 * 18.0


def test_one_subframe_2_gives_an_ephemeris_with_the_orbit_and_the_clock():
    sf2 = cnav2.decode_subframe_2(_frame_with(cnav2.build_subframe_2(SF2_VALUES), _sf3_bits()))
    eph = cnav2.assemble_ephemeris(sf2, "G05")
    assert eph.sat_id == "G05" and eph.week == 2400 and eph.toe == 7200.0 and eph.toc == 7200.0
    assert eph.e == pytest.approx(0.0123, abs=2.0**-34)
    assert eph.isc_l1cd == pytest.approx(-2.0e-9, abs=2.0**-35)
    # The orbit must evaluate: radius from the Earth's centre is near the GPS A_REF.
    state = eph.orbit_state(7200.0)
    assert 2.0e7 < float(np.linalg.norm(state.position_ecef_m)) < 3.0e7
