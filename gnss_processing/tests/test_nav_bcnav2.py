"""
B-CNAV2 (the B2a navigation message): frame structure, frame search, CRC and LDPC
handling, the message parsers, and one run through the whole synthetic chain.

The frame layout and the field positions in the parser tests are written out here as
plain numbers copied from the ICD figures, not taken from the module, so a mistake in
the module's layout tables cannot cancel out in its own test.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from utils import observables
from utils.nav import bcnav2
from utils.nav import primitives as prim
from utils.nav import symbols

PRN = 1


def _frame_bits(*, prn=PRN, message_type=10, sow_count=1000, payload=None) -> np.ndarray:
    """The 600 transmitted bits of one frame."""
    return bcnav2.encode_frame(
        bcnav2.build_message(prn=prn, message_type=message_type, sow_count=sow_count, payload=payload)
    )


def _stream(*frames: np.ndarray, lead: int = 0, tail: int = 0, seed: int = 0) -> np.ndarray:
    """Soft symbols: random junk, the frames back to back, then more junk."""
    rng = np.random.default_rng(seed)
    junk = lambda n: rng.choice([-1.0, 1.0], n)
    return np.concatenate([junk(lead), bcnav2.soft_symbols(np.concatenate(frames)), junk(tail)])


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def test_frame_arithmetic_adds_up():
    assert bcnav2.PREAMBLE_SYMBOLS + bcnav2.CODEWORD_SYMBOLS == bcnav2.FRAME_SYMBOLS == 600
    assert bcnav2.MESSAGE_BITS == 6 + 6 + 18 + 234 + 24 == 288
    # Rate 1/2: 288 bits are 48 six-bit symbols, coded to 96 symbols = 576 bits.
    assert bcnav2.CODEWORD_SYMBOLS == 2 * bcnav2.MESSAGE_BITS


def test_frame_lasts_three_seconds_at_200_sps():
    assert bcnav2.FRAME_SYMBOLS * bcnav2.SYMBOL_PERIOD_MS / 1000.0 == bcnav2.FRAME_DURATION_S == 3.0


def test_preamble_is_0xe24de8():
    assert "".join(str(b) for b in bcnav2.PREAMBLE_BITS) == "111000100100110111101000"


def test_a_week_holds_201600_frames():
    assert (bcnav2.SOW_COUNT_MAX + 1) * bcnav2.SOW_SCALE_S == 604800


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------


def test_encoded_frame_starts_with_the_preamble_and_a_systematic_codeword():
    message = bcnav2.build_message(prn=9, message_type=30, sow_count=777)
    transmitted = bcnav2.encode_frame(message)
    assert len(transmitted) == 600
    assert np.array_equal(transmitted[:24], bcnav2.PREAMBLE_BITS)
    # Systematic: the first 288 codeword bits are the message itself.
    assert np.array_equal(transmitted[24 : 24 + 288], message)


def test_built_message_has_a_valid_crc_over_its_first_264_bits():
    message = bcnav2.build_message(prn=9, message_type=30, sow_count=777)
    assert prim.crc24q_check(message)
    assert prim.crc24q(message[:264]) == prim.int_from_bits(message[264:])


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(prn=0, message_type=10, sow_count=0),
        dict(prn=64, message_type=10, sow_count=0),
        dict(prn=1, message_type=64, sow_count=0),
        dict(prn=1, message_type=10, sow_count=1 << 18),
        dict(prn=1, message_type=10, sow_count=0, payload=np.zeros(233)),
    ],
)
def test_build_message_rejects_bad_fields(kwargs):
    with pytest.raises(ValueError):
        bcnav2.build_message(**kwargs)


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------


def test_frame_round_trip():
    result = bcnav2.decode(_stream(_frame_bits(prn=12, message_type=11, sow_count=54321)))
    assert result.synced
    (frame,) = result.frames
    assert (frame.prn, frame.message_type, frame.sow_count) == (12, 11, 54321)
    assert frame.symbol_index == 0
    assert frame.inverted is False
    assert frame.sow_at_frame_start_s == 54321 * 3.0
    assert frame.sow_is_valid
    assert len(frame.payload) == 234


@pytest.mark.parametrize("lead", [0, 1, 97, 599, 1234])
def test_frame_offset_is_found(lead):
    result = bcnav2.decode(_stream(_frame_bits(), lead=lead, tail=50, seed=lead))
    assert [f.symbol_index for f in result.frames] == [lead]


def test_polarity_flip_is_handled():
    stream = _stream(_frame_bits(sow_count=4242), lead=33, tail=20)
    upright = bcnav2.decode(stream).frames
    flipped = bcnav2.decode(-stream).frames
    assert [f.inverted for f in upright] == [False]
    assert [f.inverted for f in flipped] == [True]
    assert flipped[0].sow_count == upright[0].sow_count == 4242
    assert np.array_equal(flipped[0].bits, upright[0].bits)


def test_noisy_frame_is_repaired_by_the_soft_decoder():
    rng = np.random.default_rng(3)
    clean = _stream(_frame_bits(sow_count=999), lead=40, tail=10)
    for seed in range(4):
        noisy = clean + np.random.default_rng(seed).normal(0, 0.6, len(clean))
        (frame,) = bcnav2.decode(noisy).frames
        assert frame.sow_count == 999
        assert frame.symbol_index == 40
        assert frame.symbols_corrected > 0
    del rng


def test_consecutive_frames_are_all_found_and_consistent():
    frames = [_frame_bits(message_type=t, sow_count=100 + i) for i, t in enumerate((10, 11, 30))]
    result = bcnav2.decode(_stream(*frames, lead=17, tail=5))
    assert [f.message_type for f in result.frames] == [10, 11, 30]
    assert [f.symbol_index for f in result.frames] == [17, 617, 1217]
    assert result.sow_is_consistent()
    assert [f.sow_count for f in result.of_type(11)] == [101]


def test_sow_consistency_catches_a_gap_and_wraps_at_the_end_of_the_week():
    bad = bcnav2.decode(_stream(_frame_bits(sow_count=100), _frame_bits(sow_count=105)))
    assert not bad.sow_is_consistent()
    last = bcnav2.SOW_COUNT_MAX
    wrapped = bcnav2.decode(_stream(_frame_bits(sow_count=last), _frame_bits(sow_count=0)))
    assert wrapped.sow_is_consistent()


def test_a_bad_crc_is_rejected_even_though_the_ldpc_code_is_satisfied():
    """
    Damage one information bit and re-encode: the frame is a perfectly valid LDPC
    codeword, so only the CRC can say the content is wrong.
    """
    message = bcnav2.build_message(prn=PRN, message_type=10, sow_count=1000)
    message[100] ^= 1
    assert not prim.crc24q_check(message)
    result = bcnav2.decode(_stream(bcnav2.encode_frame(message)))
    assert result.candidates_tried == 1
    assert not result.synced


def test_expected_prn_filters_other_satellites():
    stream = _stream(_frame_bits(prn=5, sow_count=1), _frame_bits(prn=PRN, sow_count=2))
    assert [f.prn for f in bcnav2.decode(stream).frames] == [5, PRN]
    assert [f.prn for f in bcnav2.decode(stream, expected_prn=PRN).frames] == [PRN]


def test_noise_alone_gives_no_frames():
    noise = np.random.default_rng(8).normal(0, 1, 6000)
    result = bcnav2.decode(noise)
    assert not result.synced


def test_a_truncated_frame_is_not_decoded():
    stream = _stream(_frame_bits())[:-10]
    assert not bcnav2.decode(stream).synced


def test_short_streams_are_handled():
    assert not bcnav2.decode(np.zeros(5)).synced
    assert not bcnav2.decode(np.zeros(0)).synced


# ---------------------------------------------------------------------------
# Message parsers: bit positions are the ICD's (1-based, in the 288-bit message)
# ---------------------------------------------------------------------------


def _frame_from(payload_fields: list[tuple[int, int, int]], *, message_type: int) -> bcnav2.Bcnav2Frame:
    """A decoded-looking frame whose data bits are set at the given (start, length, raw)."""
    message = bcnav2.build_message(prn=PRN, message_type=message_type, sow_count=1)
    for start, length, raw in payload_fields:
        message = bcnav2.set_data_field(message, start, length, raw)
    return bcnav2.Bcnav2Frame(
        prn=PRN, message_type=message_type, sow_count=1, bits=message,
        symbol_index=0, inverted=False, ldpc_iterations=0, symbols_corrected=0,
    )


def _twos(value: int, length: int) -> int:
    return value & ((1 << length) - 1)


def test_type_10_fields():
    frame = _frame_from(
        [
            (31, 13, 1234),          # WN
            (44, 1, 1), (45, 1, 0), (46, 1, 1),   # DIF, SIF, AIF (B2a)
            (47, 4, 9),              # SISMAI
            (51, 1, 0), (52, 1, 1), (53, 1, 0),   # DIF, SIF, AIF (B1C)
            (54, 8, 200),            # IODE
            (62, 11, 1000),          # toe
            (73, 2, 0b11),           # SatType: MEO
            (75, 26, _twos(-12345, 26)),   # delta A
            (101, 25, _twos(-777, 25)),    # A dot
            (126, 17, _twos(-300, 17)),    # delta n0
            (143, 23, _twos(54321, 23)),   # delta n0 dot
            (166, 33, _twos(-123456789, 33)),  # M0
            (199, 33, 987654321),    # e (unsigned)
            (232, 33, _twos(2_000_000_000, 33)),  # omega
        ],
        message_type=10,
    )
    t = bcnav2.parse_type_10(frame)
    assert (t.wn, t.iode, t.sismai, t.sat_type) == (1234, 200, 9, 3)
    assert (t.dif_b2a, t.sif_b2a, t.aif_b2a) == (1, 0, 1)
    assert (t.dif_b1c, t.sif_b1c, t.aif_b1c) == (0, 1, 0)
    assert t.toe_s == 1000 * 300
    assert t.delta_a_m == pytest.approx(-12345 * 2.0**-9)
    assert t.a_dot_mps == pytest.approx(-777 * 2.0**-21)
    assert t.delta_n0_pi_per_s == pytest.approx(-300 * 2.0**-44)
    assert t.delta_n0_dot_pi_per_s2 == pytest.approx(54321 * 2.0**-57)
    assert t.m0_pi == pytest.approx(-123456789 * 2.0**-32)
    assert t.e == pytest.approx(987654321 * 2.0**-34)
    assert t.omega_pi == pytest.approx(2_000_000_000 * 2.0**-32)


def test_type_11_fields():
    frame = _frame_from(
        [
            (31, 2, 1),
            (33, 1, 1), (34, 1, 1), (35, 1, 0),
            (36, 4, 5),
            (40, 1, 1), (41, 1, 0), (42, 1, 1),
            (43, 33, _twos(-1_500_000_000, 33)),   # Omega0
            (76, 33, _twos(700_000_000, 33)),      # i0
            (109, 19, _twos(-4000, 19)),           # Omega dot
            (128, 15, _twos(321, 15)),             # i dot
            (143, 16, _twos(-100, 16)),            # Cis
            (159, 16, _twos(200, 16)),             # Cic
            (175, 24, _twos(-30000, 24)),          # Crs
            (199, 24, _twos(40000, 24)),           # Crc
            (223, 21, _twos(-5000, 21)),           # Cus
            (244, 21, _twos(6000, 21)),            # Cuc
        ],
        message_type=11,
    )
    t = bcnav2.parse_type_11(frame)
    assert (t.hs, t.sismai) == (1, 5)
    assert (t.dif_b2a, t.sif_b2a, t.aif_b2a) == (1, 1, 0)
    assert (t.dif_b1c, t.sif_b1c, t.aif_b1c) == (1, 0, 1)
    assert t.omega0_pi == pytest.approx(-1_500_000_000 * 2.0**-32)
    assert t.i0_pi == pytest.approx(700_000_000 * 2.0**-32)
    assert t.omega_dot_pi_per_s == pytest.approx(-4000 * 2.0**-44)
    assert t.i_dot_pi_per_s == pytest.approx(321 * 2.0**-44)
    assert t.cis_rad == pytest.approx(-100 * 2.0**-30)
    assert t.cic_rad == pytest.approx(200 * 2.0**-30)
    assert t.crs_m == pytest.approx(-30000 * 2.0**-8)
    assert t.crc_m == pytest.approx(40000 * 2.0**-8)
    assert t.cus_rad == pytest.approx(-5000 * 2.0**-30)
    assert t.cuc_rad == pytest.approx(6000 * 2.0**-30)


def test_type_30_fields():
    alpha_raw = [1000, _twos(-20, 8), 30, 40, 50, _twos(-60, 8), _twos(70, 8), _twos(-80, 8), _twos(90, 8)]
    alpha_start = [146, 156, 164, 172, 180, 188, 196, 204, 212]
    alpha_len = [10] + [8] * 8
    frame = _frame_from(
        [
            (31, 2, 0), (36, 4, 3),
            (43, 11, 2000),                  # toc
            (54, 25, _twos(-1_000_000, 25)),  # a0
            (79, 22, _twos(5000, 22)),        # a1
            (101, 11, _twos(-9, 11)),         # a2
            (112, 10, 0x2C8),                 # IODC
            (122, 12, _twos(-500, 12)),       # TGD B2ap
            (134, 12, _twos(300, 12)),        # ISC B2ad
            (220, 12, _twos(-700, 12)),       # TGD B1Cp
        ]
        + list(zip(alpha_start, alpha_len, alpha_raw)),
        message_type=30,
    )
    t = bcnav2.parse_type_30(frame)
    assert (t.hs, t.sismai, t.iodc) == (0, 3, 0x2C8)
    assert t.toc_s == 2000 * 300
    assert t.a0_s == pytest.approx(-1_000_000 * 2.0**-34)
    assert t.a1_s_per_s == pytest.approx(5000 * 2.0**-50)
    assert t.a2_s_per_s2 == pytest.approx(-9 * 2.0**-66)
    assert t.tgd_b2ap_s == pytest.approx(-500 * 2.0**-34)
    assert t.isc_b2ad_s == pytest.approx(300 * 2.0**-34)
    assert t.tgd_b1cp_s == pytest.approx(-700 * 2.0**-34)
    expected_alpha = [
        1000 * 2.0**-3, -20 * 2.0**-3, 30 * 2.0**-3, 40 * 2.0**-3,
        -50 * 2.0**-3,  # alpha5: the ICD prints this scale factor as -2^-3
        -60 * 2.0**-3, 70 * 2.0**-3, -80 * 2.0**-3, 90 * 2.0**-3,
    ]
    assert list(t.bdgim_alpha) == pytest.approx(expected_alpha)


def test_parsers_reject_the_wrong_message_type():
    frame = _frame_from([], message_type=11)
    with pytest.raises(ValueError, match="type 10"):
        bcnav2.parse_type_10(frame)
    with pytest.raises(ValueError, match="type 30"):
        bcnav2.parse_type_30(frame)
    with pytest.raises(ValueError, match="type 11"):
        bcnav2.parse_type_11(_frame_from([], message_type=10))


def test_ephemeris_and_clock_pair_when_iode_equals_the_iodc_low_byte():
    """ICD 7.4.3: matched when IODE == IODC & 0xFF; the two high bits of IODC are free."""
    eph = bcnav2.parse_type_10(_frame_from([(54, 8, 0xC8)], message_type=10))
    for iodc, expected in ((0xC8, True), (0x2C8, True), (0x1C8, True), (0xC9, False)):
        clock = bcnav2.parse_type_30(_frame_from([(112, 10, iodc)], message_type=30))
        assert bcnav2.iode_matches_iodc(eph, clock) is expected


def test_a_round_trip_through_the_whole_message_chain():
    """Build a type 10 frame with real field values, transmit it, decode, parse."""
    payload_fields = [(31, 13, 800), (54, 8, 17), (62, 11, 500), (73, 2, 3), (199, 33, 123456)]
    message = bcnav2.build_message(prn=PRN, message_type=10, sow_count=222)
    for start, length, raw in payload_fields:
        message = bcnav2.set_data_field(message, start, length, raw)
    message = prim.crc24q_append(message[:264])  # re-seal after editing the data
    stream = bcnav2.soft_symbols(bcnav2.encode_frame(message))
    (frame,) = bcnav2.decode(stream).frames
    t = bcnav2.parse_type_10(frame)
    assert (t.wn, t.iode, t.toe_s, t.sat_type) == (800, 17, 500 * 300, 3)
    assert t.e == pytest.approx(123456 * 2.0**-34)


# ---------------------------------------------------------------------------
# Time anchor
# ---------------------------------------------------------------------------


def test_time_anchor_carries_bdt_and_the_frame_start():
    stream = SimpleNamespace(code_phase_ms=np.arange(2000, dtype=float) * 5.0)
    frame = bcnav2.Bcnav2Frame(
        prn=21, message_type=10, sow_count=1000, bits=np.zeros(288, dtype=np.uint8),
        symbol_index=640, inverted=False, ldpc_iterations=0, symbols_corrected=0,
    )
    anchor = observables.anchor_from_bcnav2(stream, frame, week=1500)
    assert anchor.sat_id == "C21"
    assert anchor.time_system == "BDT"
    assert anchor.week == 1500
    assert anchor.tow_s == 3000.0  # SOW names this frame's own first chip
    assert anchor.code_phase_ms == 640 * 5.0
    # 3 s later in code phase is 3 s later in time.
    assert anchor.transmit_time_s(640 * 5.0 + 3000.0) == pytest.approx(3003.0)
    with pytest.raises(ValueError, match="past the end"):
        observables.anchor_from_bcnav2(stream, SimpleNamespace(**{**frame.__dict__, "symbol_index": 5000}))


def test_gps_anchors_still_default_to_gpst():
    anchor = observables.TimeAnchor(sat_id="G01", tow_s=1.0, code_phase_ms=0.0)
    assert anchor.time_system == "GPST"


# ---------------------------------------------------------------------------
# The whole chain: synthetic B2a signal -> tracking -> symbols -> frame
# ---------------------------------------------------------------------------


def test_frame_survives_the_synthetic_signal_and_the_tracking_loops():
    """
    Transmit two real frames as the data of a synthetic B2a signal, acquire and track
    it, extract the symbols, and decode.  The 180 degree carrier ambiguity and the
    settling of the carrier loop are handled by the decoder, as on real data.
    """
    from .test_b2a import _acquire_and_track

    frames = [
        _frame_bits(message_type=10, sow_count=5000),
        _frame_bits(message_type=11, sow_count=5001),
    ]
    first_frame_symbol = 120  # 0.6 s in: after the loops have pulled in and synced
    nav_symbols = np.roll(bcnav2.soft_symbols(np.concatenate(frames)), first_frame_symbol)

    adapter = _acquire_and_track(noise_sigma=2.0, buffers=76, nav_bits=nav_symbols)
    stream = symbols.extract(adapter.outputs, "BDS_B2A")
    result = bcnav2.decode(stream.soft, expected_prn=PRN)

    assert result.synced, "no frame decoded from the tracked symbols"
    frame = result.frames[0]
    assert (frame.prn, frame.message_type, frame.sow_count) == (PRN, 10, 5000)

    # The anchor ties the SOW to the code phase of the frame's first symbol.  The
    # frame starts at symbol 120 of the transmitted sequence = 600 ms of code phase.
    anchor = observables.anchor_from_bcnav2(stream, frame)
    assert anchor.code_phase_ms == pytest.approx(first_frame_symbol * 5.0, abs=0.01)
    assert anchor.tow_s == 5000 * 3.0
