"""
B-CNAV1 (the B1C navigation message): subframe 1 BCH codes, the two LDPC codes, the
block interleaver, frame search, CRC handling, the parsers, the ephemeris assembly,
and one run through the whole synthetic chain.

The frame layout and the field positions are written out here as plain numbers copied
from the ICD figures, not taken from the module, so a mistake in the module's tables
cannot cancel out in its own test.
"""

from __future__ import annotations

import json
from itertools import combinations
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from utils import observables
from utils.nav import bcnav1, ldpc, ldpc_b1c_tables, symbols
from utils.nav import primitives as prim
from utils.nav.ephemeris import BdsEphemeris

ICD_LDPC_PATH = Path(__file__).parent.parent / "docs" / "icd" / "bds_b1c_ldpc_tables.json"
PRN = 1


@pytest.fixture(scope="module")
def icd() -> dict:
    with ICD_LDPC_PATH.open() as handle:
        return json.load(handle)


def _twos(value: int, length: int) -> int:
    return value & ((1 << length) - 1)


def _random_subframe2(seed=0) -> np.ndarray:
    return bcnav1.build_subframe2(np.random.default_rng(seed).integers(0, 2, 576))


def _subframe2_at(how: int, seed=0) -> np.ndarray:
    """A subframe 2 with random content and a chosen hour of week."""
    information = np.random.default_rng(seed).integers(0, 2, 576).astype(np.uint8)
    return bcnav1.build_subframe2(bcnav1.set_data_field(information, 14, 8, how))


def _frame_bits(*, prn=PRN, soh_count=10, subframe2=None, subframe3=None, seed=0) -> np.ndarray:
    return bcnav1.encode_frame(
        prn=prn, soh_count=soh_count,
        subframe2_bits=_random_subframe2(seed) if subframe2 is None else subframe2,
        subframe3_bits=bcnav1.build_subframe3() if subframe3 is None else subframe3,
    )


def _stream(*frames: np.ndarray, lead: int = 0, tail: int = 0, seed: int = 0) -> np.ndarray:
    """Soft symbols: random junk, the frames back to back, then more junk."""
    rng = np.random.default_rng(seed)
    junk = lambda n: rng.choice([-1.0, 1.0], n)
    return np.concatenate([junk(lead), bcnav1.soft_symbols(np.concatenate(frames)), junk(tail)])


# ---------------------------------------------------------------------------
# Geometry (ICD Figure 6-1)
# ---------------------------------------------------------------------------


def test_frame_arithmetic_adds_up():
    assert 72 + 1200 + 528 == bcnav1.FRAME_SYMBOLS == 1800
    assert bcnav1.FRAME_SYMBOLS * bcnav1.SYMBOL_PERIOD_MS / 1000.0 == bcnav1.FRAME_DURATION_S == 18.0
    assert 14 + 600 + 264 == 878  # bits before coding
    assert 21 + 51 == bcnav1.SUBFRAME1_SYMBOLS
    # Rate 1/2 for the two LDPC codes: 600 bits to 1200, 264 to 528.
    assert bcnav1.SUBFRAME2_SYMBOLS == 2 * bcnav1.SUBFRAME2_BITS
    assert bcnav1.SUBFRAME3_SYMBOLS == 2 * bcnav1.SUBFRAME3_BITS
    assert (bcnav1.SOH_COUNT_MAX + 1) * 18 == 3600  # one hour holds 200 frames


# ---------------------------------------------------------------------------
# Subframe 1: BCH(21,6) and BCH(51,8)
# ---------------------------------------------------------------------------


def _figure_6_2_register(message: list[int], taps_after_stage: list[int], stages: int, n: int):
    """
    Literal simulation of ICD Figure 6-2, written without the module's recurrence.

    A shift register of `stages` D elements.  For the first k clocks gate 1 feeds the
    message into the first stage; then gate 2 feeds back the XOR of the output
    (last stage) and the stages named in `taps_after_stage` (1-based, counted from the
    input).  The output is the last stage.  The codeword is the message followed by
    what the output gives while the feedback is closed.
    """
    register = [0] * stages
    out = []
    for bit in message:  # gate 1 closed: the message loads
        register = [bit] + register[:-1]
    while len(out) < n:
        feedback = register[-1]
        for stage in taps_after_stage:
            feedback ^= register[stage - 1]
        out.append(register[-1])
        register = [feedback] + register[:-1]
    return out


def test_bch_21_6_matches_the_register_of_figure_6_2():
    # x^6 + x^4 + x^2 + x + 1: taps after stages 2, 4 and 5 (x^4, x^2, x), as drawn.
    for prn in (1, 7, 33, 63):
        message = [int(b) for b in prim.bits_from_int(prn, 6)]
        literal = _figure_6_2_register(message, [2, 4, 5], 6, 21)
        assert literal[:6] == message  # the circuit sends the message first
        assert list(bcnav1.encode_prn_field(prn)) == literal, prn


def test_bch_51_8_matches_the_register_of_figure_6_2():
    for soh in (0, 1, 77, 199):
        message = [int(b) for b in prim.bits_from_int(soh, 8)]
        literal = _figure_6_2_register(message, [1, 4, 5, 6, 7], 8, 51)
        assert list(bcnav1.encode_soh_field(soh)) == literal, soh


@pytest.mark.parametrize(
    "encode, count, length, distance",
    [(bcnav1.encode_prn_field, 64, 21, 8), (bcnav1.encode_soh_field, 256, 51, 24)],
)
def test_the_bch_codes_have_the_error_correction_of_table_6_1(encode, count, length, distance):
    """
    Table 6-1 gives t = 3 for (21,6) and t = 11 for (51,8).  A linear code corrects t
    errors when its minimum distance is at least 2t + 1.  The measured distances are 8
    and 24, so a wrong reading of the polynomial would show here.
    """
    words = np.array([encode(m) for m in range(count)])
    assert words.shape == (count, length)
    assert min(int(w.sum()) for w in words[1:]) == distance
    assert len({tuple(w) for w in words}) == count
    # Linear: the XOR of two codewords is a codeword.
    known = {tuple(w) for w in words}
    for a, b in combinations(range(1, 6), 2):
        assert tuple(words[a] ^ words[b]) in known


def test_bch_decoding_repairs_up_to_t_flipped_symbols():
    rng = np.random.default_rng(5)
    for prn in (3, 40, 62):
        soft = bcnav1.soft_symbols(bcnav1.encode_prn_field(prn))
        for flips in ((2,), (0, 9, 20)):
            damaged = soft.copy()
            damaged[list(flips)] *= -1
            decoded, score = bcnav1.decode_prn_field(damaged)
            assert decoded == prn and 0 < score < 1
    for soh in (0, 91, 199):
        soft = bcnav1.soft_symbols(bcnav1.encode_soh_field(soh))
        flips = rng.choice(51, 11, replace=False)  # t = 11
        soft[flips] *= -1
        assert bcnav1.decode_soh_field(soft)[0] == soh


def test_a_clean_codeword_scores_one_and_noise_scores_low():
    assert bcnav1.decode_prn_field(bcnav1.soft_symbols(bcnav1.encode_prn_field(5)))[1] == pytest.approx(1.0)
    rng = np.random.default_rng(1)
    scores = [bcnav1.decode_soh_field(rng.normal(size=51))[1] for _ in range(200)]
    # The best of 256 words still scores about 0.5 on pure noise, which is why the frame
    # search treats the score as a filter and lets the LDPC and the CRC decide.
    assert np.mean(scores) < 0.55 and max(scores) < 0.8


def test_bch_fields_reject_values_that_do_not_fit():
    with pytest.raises(ValueError):
        bcnav1.encode_prn_field(64)
    with pytest.raises(ValueError):
        bcnav1.encode_soh_field(256)


# ---------------------------------------------------------------------------
# The two LDPC codes: the ICD encoding examples are the golden vectors
# ---------------------------------------------------------------------------


def _symbols(strings) -> np.ndarray:
    return np.array([int(s, 2) for s in strings])


@pytest.mark.parametrize(
    "code, message_key, codeword_key",
    [
        (ldpc.BCNAV1_SUBFRAME2_CODE, "example2_input", "example2_output"),
        (ldpc.BCNAV1_SUBFRAME3_CODE, "example3_input", "example3_output"),
    ],
)
def test_the_icd_encoding_examples_are_reproduced(icd, code, message_key, codeword_key):
    codeword = _symbols(icd[codeword_key])
    assert np.array_equal(code.encode(_symbols(icd[message_key])), codeword)
    assert not code.syndrome(codeword).any()


def test_the_tables_in_the_module_equal_the_icd_copy(icd):
    assert ldpc_b1c_tables.H100_INDEX_PRINTED == tuple(map(tuple, icd["h100_index"]))
    assert ldpc_b1c_tables.H100_ELEMENT_PRINTED == tuple(map(tuple, icd["h100_element"]))
    assert ldpc_b1c_tables.H44_INDEX_PRINTED == tuple(map(tuple, icd["h44_index"]))
    assert ldpc_b1c_tables.H44_ELEMENT_PRINTED == tuple(map(tuple, icd["h44_element"]))


def test_the_codes_have_the_sizes_of_the_icd():
    for code, n, k in (
        (ldpc.BCNAV2_CODE, 96, 48),
        (ldpc.BCNAV1_SUBFRAME2_CODE, 200, 100),
        (ldpc.BCNAV1_SUBFRAME3_CODE, 88, 44),
    ):
        assert (code.num_variables, code.num_message_symbols) == (n, k)
        h = code.parity_check_matrix()
        assert h.shape == (n - k, n)
        assert np.all((h != 0).sum(axis=1) == 4)  # four non-zero elements in every row
    # Every column of the information part and the check part is used.
    for code in (ldpc.BCNAV1_SUBFRAME2_CODE, ldpc.BCNAV1_SUBFRAME3_CODE):
        assert np.all((code.parity_check_matrix() != 0).sum(axis=0) >= 1)


@pytest.mark.parametrize("code", [ldpc.BCNAV1_SUBFRAME2_CODE, ldpc.BCNAV1_SUBFRAME3_CODE])
def test_random_messages_encode_to_codewords_and_decode_through_noise(code):
    rng = np.random.default_rng(3)
    for _ in range(3):
        message = rng.integers(0, 64, code.num_message_symbols)
        codeword = code.encode(message)
        assert not code.syndrome(codeword).any()
        assert np.array_equal(codeword[: code.num_message_symbols], message)  # systematic
        soft = 1.0 - 2.0 * ldpc.bits_from_symbols(codeword) + rng.normal(0, 0.7, 6 * code.num_variables)
        decoded = code.decode(soft)
        assert decoded.converged
        assert np.array_equal(decoded.symbols, codeword)
        assert np.array_equal(decoded.message_symbols, message)
        assert len(decoded.message_bits) == 6 * code.num_message_symbols


def test_ldpc_rejects_wrong_sizes():
    with pytest.raises(ValueError, match="expected 100"):
        ldpc.BCNAV1_SUBFRAME2_CODE.encode(np.zeros(48, dtype=int))
    with pytest.raises(ValueError, match="expected 528"):
        ldpc.BCNAV1_SUBFRAME3_CODE.decode(np.zeros(576))


def test_the_old_b_cnav2_names_still_work():
    assert (ldpc.NUM_VARIABLES, ldpc.NUM_CHECKS, ldpc.NUM_MESSAGE_SYMBOLS) == (96, 48, 48)
    message = np.arange(48) % 64
    assert np.array_equal(ldpc.encode(message), ldpc.BCNAV2_CODE.encode(message))


# ---------------------------------------------------------------------------
# The block interleaver (ICD 6.2.2.4)
# ---------------------------------------------------------------------------


def _literal_interleaver(sub2: list, sub3: list) -> list:
    """The ICD text, step by step, with plain lists and ICD row numbers (1 to 36)."""
    rows = {r: [None] * 48 for r in range(1, 37)}
    s2, s3 = iter(sub2), iter(sub3)
    row = 1
    while row <= 33:
        for _ in range(2):  # two rows of subframe 2
            rows[row] = [next(s2) for _ in range(48)]
            row += 1
        rows[row] = [next(s3) for _ in range(48)]  # then one row of subframe 3
        row += 1
    for row in (34, 35, 36):  # the last three rows take the rest of subframe 2
        rows[row] = [next(s2) for _ in range(48)]
    out = []
    for column in range(48):  # read by columns, top to bottom
        for row in range(1, 37):
            out.append(rows[row][column])
    return out


def test_the_interleaver_follows_the_icd_text():
    sub2 = [("a", i) for i in range(1200)]
    sub3 = [("b", i) for i in range(528)]
    expected = _literal_interleaver(sub2, sub3)
    got = bcnav1.interleave(np.arange(1200), np.arange(1200, 1728))
    names = [("a", v) if v < 1200 else ("b", v - 1200) for v in got]
    assert names == expected


def test_the_interleaver_places_known_symbols_where_the_icd_says():
    got = bcnav1.interleave(np.arange(1200), 10_000 + np.arange(528))
    # Column 1 holds, top to bottom: rows 1, 2 of subframe 2, row 3 of subframe 3 ...
    assert got[0] == 0 and got[1] == 48 and got[2] == 10_000
    # The last subframe 3 symbol is in row 33, column 48.
    assert got[47 * 36 + 32] == 10_000 + 527
    # Rows 34 to 36 hold the last 144 symbols of subframe 2.
    assert got[33] == 1056 and got[35] == 1056 + 96


def test_deinterleaving_undoes_interleaving():
    rng = np.random.default_rng(0)
    sub2, sub3 = rng.normal(size=1200), rng.normal(size=528)
    back2, back3 = bcnav1.deinterleave(bcnav1.interleave(sub2, sub3))
    assert np.array_equal(back2, sub2) and np.array_equal(back3, sub3)
    with pytest.raises(ValueError):
        bcnav1.deinterleave(np.zeros(1000))
    with pytest.raises(ValueError):
        bcnav1.interleave(np.zeros(10), np.zeros(528))


# ---------------------------------------------------------------------------
# Frames
# ---------------------------------------------------------------------------


def test_built_subframes_carry_a_valid_crc():
    sub2, sub3 = _random_subframe2(), bcnav1.build_subframe3()
    assert len(sub2) == 600 and prim.crc24q_check(sub2)
    assert prim.crc24q(sub2[:576]) == prim.int_from_bits(sub2[576:])  # over the first 576 bits
    assert len(sub3) == 264 and prim.crc24q_check(sub3)
    assert prim.crc24q(sub3[:240]) == prim.int_from_bits(sub3[240:])  # over the first 240 bits
    with pytest.raises(ValueError):
        bcnav1.build_subframe2(np.zeros(10))
    with pytest.raises(ValueError):
        bcnav1.build_subframe3(np.zeros(10))


def test_an_encoded_frame_has_the_layout_of_figure_6_1():
    bits = _frame_bits(prn=21, soh_count=33)
    assert len(bits) == 1800
    assert np.array_equal(bits[:21], bcnav1.encode_prn_field(21))
    assert np.array_equal(bits[21:72], bcnav1.encode_soh_field(33))
    sub2, sub3 = bcnav1.deinterleave(bits[72:])
    assert not ldpc.BCNAV1_SUBFRAME2_CODE.syndrome(ldpc.symbols_from_bits(sub2)).any()
    assert not ldpc.BCNAV1_SUBFRAME3_CODE.syndrome(ldpc.symbols_from_bits(sub3)).any()
    # Systematic: the first 600 bits of subframe 2 are the message with its CRC.
    assert np.array_equal(sub2[:600], _random_subframe2())


def test_frame_round_trip():
    stream = _stream(_frame_bits(prn=9, soh_count=42))
    (frame,) = bcnav1.decode(stream, expected_prn=9).frames
    assert (frame.prn, frame.soh_count, frame.symbol_index, frame.inverted) == (9, 42, 0, False)
    assert np.array_equal(frame.subframe2_bits, _random_subframe2())
    assert frame.page_id == 1
    assert frame.prn_score == pytest.approx(1.0) and frame.soh_score == pytest.approx(1.0)


@pytest.mark.parametrize("lead", [0, 1, 17, 300, 1799])
def test_frame_offset_is_found(lead):
    (frame,) = bcnav1.decode(_stream(_frame_bits(), lead=lead, tail=40, seed=lead), expected_prn=PRN).frames
    assert frame.symbol_index == lead


def test_the_prn_does_not_have_to_be_known():
    (frame,) = bcnav1.decode(_stream(_frame_bits(prn=44), lead=123, tail=9)).frames
    assert (frame.prn, frame.symbol_index) == (44, 123)


def test_polarity_flip_is_handled():
    """The carrier loop's 180 degree ambiguity: the PRN codeword resolves it."""
    stream = -_stream(_frame_bits(), lead=77, tail=5)
    result = bcnav1.decode(stream, expected_prn=PRN)
    assert result.synced and result.inverted
    assert result.frames[0].symbol_index == 77


def test_noisy_frame_is_repaired_by_the_soft_decoder():
    rng = np.random.default_rng(4)
    stream = _stream(_frame_bits(), lead=200, tail=50)
    noisy = stream + rng.normal(0.0, 0.9, len(stream))
    result = bcnav1.decode(noisy, expected_prn=PRN)
    assert result.synced
    assert result.frames[0].symbol_index == 200
    assert result.frames[0].ldpc_iterations > 0  # the decoder had something to repair


def test_consecutive_frames_are_all_found_and_consistent():
    frames = [_frame_bits(soh_count=10 + i, subframe2=_subframe2_at(7, i)) for i in range(3)]
    result = bcnav1.decode(_stream(*frames, lead=333, tail=100), expected_prn=PRN)
    assert [f.symbol_index for f in result.frames] == [333, 2133, 3933]
    assert [f.soh_count for f in result.frames] == [10, 11, 12]
    assert result.sow_is_consistent()


def test_time_consistency_catches_a_gap_and_wraps_at_the_end_of_the_week():
    result = bcnav1.decode(
        _stream(*[_frame_bits(soh_count=10 + i, subframe2=_subframe2_at(7, i)) for i in range(2)]),
        expected_prn=PRN,
    )
    assert result.sow_is_consistent()
    result.frames[1] = type(result.frames[1])(**{**result.frames[1].__dict__, "soh_count": 13})
    assert not result.sow_is_consistent()
    # The same hour, 18 s per frame: the 200th frame of an hour starts the next hour.
    # HOW and SOH are what carry the time, so check the arithmetic directly.
    a = bcnav1.decode(_stream(_frame_bits(soh_count=199, subframe2=_subframe2_at(167))), expected_prn=PRN).frames[0]
    b = bcnav1.decode(_stream(_frame_bits(soh_count=0, subframe2=_subframe2_at(0, 1))), expected_prn=PRN).frames[0]
    assert a.tow_at_frame_start_s == 167 * 3600 + 199 * 18 == 604_782
    assert b.tow_at_frame_start_s == 0
    wrapped = bcnav1.Bcnav1DecodeResult(frames=[a, type(b)(**{**b.__dict__, "symbol_index": 1800})])
    assert wrapped.sow_is_consistent()


def test_a_bad_subframe_2_crc_is_rejected_though_the_ldpc_code_is_satisfied():
    information = _random_subframe2()[:576].copy()
    sealed = prim.crc24q_append(information)
    sealed[100] ^= 1  # corrupt after sealing: still a valid LDPC message, but a bad CRC
    result = bcnav1.decode(_stream(_frame_bits(subframe2=sealed)), expected_prn=PRN)
    assert not result.synced
    assert result.candidates_tried == 1  # it reached the LDPC decoder and failed the CRC


def test_a_bad_subframe_3_crc_keeps_the_frame_without_subframe_3():
    sub3 = bcnav1.build_subframe3().copy()
    sub3[50] ^= 1
    (frame,) = bcnav1.decode(_stream(_frame_bits(subframe3=sub3)), expected_prn=PRN).frames
    assert frame.subframe3_bits is None and frame.page_id is None
    assert bcnav1.parse_page_1(frame) is None
    assert bcnav1.parse_subframe2(frame).wn == frame.wn


def test_expected_prn_filters_other_satellites():
    stream = _stream(_frame_bits(prn=17))
    assert not bcnav1.decode(stream, expected_prn=PRN).synced
    assert bcnav1.decode(stream, expected_prn=17).synced


def test_a_soh_above_199_is_not_a_frame():
    stream = _stream(_frame_bits(soh_count=230))
    assert not bcnav1.decode(stream, expected_prn=PRN).synced


def test_noise_alone_gives_no_frames():
    noise = np.random.default_rng(8).normal(size=6000)
    assert not bcnav1.decode(noise, expected_prn=PRN).synced


def test_short_or_truncated_streams_are_handled():
    assert not bcnav1.decode(np.zeros(10)).synced
    assert not bcnav1.decode(_stream(_frame_bits())[:1799], expected_prn=PRN).synced
    assert not bcnav1.decode(np.array([]), expected_prn=PRN).synced


# ---------------------------------------------------------------------------
# Parsers: bit positions are the ICD's (1-based, in the 600- and 264-bit subframes)
# ---------------------------------------------------------------------------


def _subframe2_frame(fields: list[tuple[int, int, int]], *, soh_count=7, prn=PRN) -> bcnav1.Bcnav1Frame:
    information = np.array([0] * 576, dtype=np.uint8)
    for start, length, raw in fields:
        information = bcnav1.set_data_field(information, start, length, raw)
    return bcnav1.Bcnav1Frame(
        prn=prn, soh_count=soh_count, subframe2_bits=bcnav1.build_subframe2(information),
        subframe3_bits=None, symbol_index=0, inverted=False, prn_score=1.0, soh_score=1.0,
        ldpc_iterations=0,
    )


# Positions copied from ICD Figures 6-6, 6-12, 6-13, 6-14 and the group delay row.
SUBFRAME2_FIELDS = [
    (1, 13, 1234),                      # WN
    (14, 8, 100),                       # HOW
    (22, 10, 0x2C8),                    # IODC
    (32, 8, 0xC8),                      # IODE
    (40, 11, 1000),                     # toe
    (51, 2, 0b11),                      # SatType: MEO
    (53, 26, _twos(-12345, 26)),        # delta A
    (79, 25, _twos(-777, 25)),          # A dot
    (104, 17, _twos(-300, 17)),         # delta n0
    (121, 23, _twos(54321, 23)),        # delta n0 dot
    (144, 33, _twos(-123456789, 33)),   # M0
    (177, 33, 987654321),               # e
    (210, 33, _twos(2_000_000_000, 33)),  # omega
    (243, 33, _twos(-1_500_000_000, 33)),  # Omega0
    (276, 33, _twos(700_000_000, 33)),  # i0
    (309, 19, _twos(-4000, 19)),        # Omega dot
    (328, 15, _twos(321, 15)),          # i dot
    (343, 16, _twos(-100, 16)),         # Cis
    (359, 16, _twos(200, 16)),          # Cic
    (375, 24, _twos(-300000, 24)),      # Crs
    (399, 24, _twos(400000, 24)),       # Crc
    (423, 21, _twos(-50, 21)),          # Cus
    (444, 21, _twos(60, 21)),           # Cuc
    (465, 11, 700),                     # toc
    (476, 25, _twos(-1000, 25)),        # a0
    (501, 22, _twos(300, 22)),          # a1
    (523, 11, _twos(-5, 11)),           # a2
    (534, 12, _twos(-100, 12)),         # TGD B2ap
    (546, 12, _twos(55, 12)),           # ISC B1Cd
    (558, 12, _twos(-70, 12)),          # TGD B1Cp
]


def test_subframe_2_fields():
    s = bcnav1.parse_subframe2(_subframe2_frame(SUBFRAME2_FIELDS))
    assert (s.wn, s.how, s.iodc, s.iode, s.sat_type) == (1234, 100, 0x2C8, 0xC8, 3)
    assert s.toe_s == 1000 * 300 and s.toc_s == 700 * 300
    assert s.delta_a_m == pytest.approx(-12345 * 2.0**-9)
    assert s.a_dot_mps == pytest.approx(-777 * 2.0**-21)
    assert s.delta_n0_pi_per_s == pytest.approx(-300 * 2.0**-44)
    assert s.delta_n0_dot_pi_per_s2 == pytest.approx(54321 * 2.0**-57)
    assert s.m0_pi == pytest.approx(-123456789 * 2.0**-32)
    assert s.e == pytest.approx(987654321 * 2.0**-34)
    assert s.omega_pi == pytest.approx(2_000_000_000 * 2.0**-32)
    assert s.omega0_pi == pytest.approx(-1_500_000_000 * 2.0**-32)
    assert s.i0_pi == pytest.approx(700_000_000 * 2.0**-32)
    assert s.omega_dot_pi_per_s == pytest.approx(-4000 * 2.0**-44)
    assert s.i_dot_pi_per_s == pytest.approx(321 * 2.0**-44)
    assert (s.cis_rad, s.cic_rad) == (pytest.approx(-100 * 2.0**-30), pytest.approx(200 * 2.0**-30))
    assert (s.crs_m, s.crc_m) == (pytest.approx(-300000 * 2.0**-8), pytest.approx(400000 * 2.0**-8))
    assert (s.cus_rad, s.cuc_rad) == (pytest.approx(-50 * 2.0**-30), pytest.approx(60 * 2.0**-30))
    assert s.a0_s == pytest.approx(-1000 * 2.0**-34)
    assert s.a1_s_per_s == pytest.approx(300 * 2.0**-50)
    assert s.a2_s_per_s2 == pytest.approx(-5 * 2.0**-66)
    assert s.tgd_b2ap_s == pytest.approx(-100 * 2.0**-34)
    assert s.isc_b1cd_s == pytest.approx(55 * 2.0**-34)
    assert s.tgd_b1cp_s == pytest.approx(-70 * 2.0**-34)


def test_page_1_fields():
    info = np.zeros(240, dtype=np.uint8)
    for start, length, raw in [
        (1, 6, 1), (7, 2, 2), (9, 1, 1), (10, 1, 0), (11, 1, 1),   # PageID, HS, DIF, SIF, AIF
        (12, 4, 9), (16, 5, 17), (21, 22, 0x2AAAA),                # SISMAI, SISAIoe, SISAIoc
        (43, 10, 800),                                             # BDGIM alpha1
        (53, 8, _twos(-20, 8)),                                    # alpha2
    ]:
        info = bcnav1.set_data_field(info, start, length, raw)
    frame = bcnav1.Bcnav1Frame(
        prn=PRN, soh_count=0, subframe2_bits=_random_subframe2(), subframe3_bits=bcnav1.build_subframe3(info),
        symbol_index=0, inverted=False, prn_score=1.0, soh_score=1.0, ldpc_iterations=0,
    )
    page = bcnav1.parse_page_1(frame)
    assert (page.hs, page.dif_b1c, page.sif_b1c, page.aif_b1c) == (2, 1, 0, 1)
    assert (page.sismai, page.sisaioe, page.sisaioc) == (9, 17, 0x2AAAA)
    assert page.bdgim_alpha[0] == pytest.approx(800 * 2.0**-3)
    assert page.bdgim_alpha[1] == pytest.approx(-20 * 2.0**-3)
    assert len(page.bdgim_alpha) == 9


def test_other_pages_are_not_page_1():
    info = bcnav1.set_data_field(np.zeros(240, dtype=np.uint8), 1, 6, 3)
    frame = bcnav1.Bcnav1Frame(
        prn=PRN, soh_count=0, subframe2_bits=_random_subframe2(), subframe3_bits=bcnav1.build_subframe3(info),
        symbol_index=0, inverted=False, prn_score=1.0, soh_score=1.0, ldpc_iterations=0,
    )
    assert frame.page_id == 3
    assert bcnav1.parse_page_1(frame) is None


def test_ephemeris_and_clock_match_when_iode_equals_the_iodc_low_byte():
    for iodc, expected in ((0xC8, True), (0x2C8, True), (0x1C8, True), (0xC9, False)):
        s = bcnav1.parse_subframe2(_subframe2_frame([(22, 10, iodc), (32, 8, 0xC8)]))
        assert bcnav1.iode_matches_iodc(s) is expected


# ---------------------------------------------------------------------------
# From decoded frames to a BdsEphemeris
# ---------------------------------------------------------------------------


def _decoded(frame_bits: np.ndarray, **kwargs):
    return bcnav1.decode(_stream(frame_bits), **kwargs)


def test_ephemeris_is_assembled_from_one_subframe_2():
    result = bcnav1.Bcnav1DecodeResult(frames=[_subframe2_frame(SUBFRAME2_FIELDS, prn=17)])
    eph = bcnav1.assemble_ephemeris(result)
    assert isinstance(eph, BdsEphemeris)
    assert eph.sat_id == "C17"
    assert (eph.week, eph.toe, eph.toc, eph.sat_type, eph.iode, eph.iodc) == (1234, 300_000, 210_000, 3, 0xC8, 0x2C8)
    assert eph.e == pytest.approx(987654321 * 2.0**-34)
    assert eph.semi_major_axis_at_toe_m == pytest.approx(27_906_100 - 12345 * 2.0**-9)
    assert eph.a0 == pytest.approx(-1000 * 2.0**-34)
    assert eph.tgd_b1cp == pytest.approx(-70 * 2.0**-34)
    assert eph.isc_b1cd == pytest.approx(55 * 2.0**-34)
    assert eph.tgd_b2ap == pytest.approx(-100 * 2.0**-34)
    assert eph.health == 0
    # ICD 7-4 and 7-5 (B1C ICD): pilot minus TGD_B1Cp; data also minus ISC_B1Cd.
    assert eph.group_delay_s("B1Cp") == pytest.approx(70 * 2.0**-34)
    assert eph.group_delay_s("B1Cd") == pytest.approx((70 - 55) * 2.0**-34)
    assert observables.signal_group_delay_s(eph, "BDS_B1C") == eph.group_delay_s("B1Cp")
    assert observables.signal_group_delay_s(eph, "BDS_B2A") == eph.group_delay_s("B2ap")
    with pytest.raises(ValueError, match="B2a and B1C"):
        observables.signal_group_delay_s(eph, "GPS_L5")
    with pytest.raises(ValueError):
        eph.group_delay_s("X")


def test_an_unmatched_iode_gives_no_ephemeris_and_the_newest_match_wins():
    bad = _subframe2_frame([(22, 10, 0x1C9), (32, 8, 0xC8)] + SUBFRAME2_FIELDS[4:])
    good_old = _subframe2_frame([(40, 11, 500)] + SUBFRAME2_FIELDS[:4] + SUBFRAME2_FIELDS[5:])
    good_new = _subframe2_frame(SUBFRAME2_FIELDS)
    assert bcnav1.assemble_ephemeris(bcnav1.Bcnav1DecodeResult(frames=[bad])) is None
    result = bcnav1.Bcnav1DecodeResult(frames=[good_old, good_new, bad])
    assert bcnav1.assemble_ephemeris(result).toe == 1000 * 300  # newest *matched* frame
    assert bcnav1.assemble_ephemeris(bcnav1.Bcnav1DecodeResult()) is None


def test_health_comes_from_a_page_1_when_there_is_one():
    page = bcnav1.set_data_field(bcnav1.set_data_field(np.zeros(240, dtype=np.uint8), 1, 6, 1), 7, 2, 1)
    frame = _subframe2_frame(SUBFRAME2_FIELDS)
    frame = type(frame)(**{**frame.__dict__, "subframe3_bits": bcnav1.build_subframe3(page)})
    assert bcnav1.assemble_ephemeris(bcnav1.Bcnav1DecodeResult(frames=[frame])).health == 1


def test_a_geo_satellite_is_refused():
    fields = [f for f in SUBFRAME2_FIELDS if f[0] != 51] + [(51, 2, 0b01)]
    with pytest.raises(ValueError, match="GEO"):
        bcnav1.assemble_ephemeris(bcnav1.Bcnav1DecodeResult(frames=[_subframe2_frame(fields)]))


def test_a_round_trip_through_the_whole_message_chain():
    """Build a subframe 2 with real field values, transmit it, decode, assemble."""
    information = np.zeros(576, dtype=np.uint8)
    for start, length, raw in SUBFRAME2_FIELDS:
        information = bcnav1.set_data_field(information, start, length, raw)
    stream = _stream(_frame_bits(prn=17, soh_count=60, subframe2=bcnav1.build_subframe2(information)), lead=250)
    result = bcnav1.decode(stream, expected_prn=17)
    eph = bcnav1.assemble_ephemeris(result)
    assert eph.sat_id == "C17" and eph.week == 1234
    assert eph.e == pytest.approx(987654321 * 2.0**-34)
    assert result.frames[0].tow_at_frame_start_s == 100 * 3600 + 60 * 18


# ---------------------------------------------------------------------------
# Time anchor
# ---------------------------------------------------------------------------


def test_time_anchor_carries_bdt_and_the_frame_start():
    stream = SimpleNamespace(code_phase_ms=np.arange(5000, dtype=float) * 10.0)
    frame = _subframe2_frame([(1, 13, 1500), (14, 8, 12)], soh_count=44, prn=21)
    frame = type(frame)(**{**frame.__dict__, "symbol_index": 640})
    anchor = observables.anchor_from_bcnav1(stream, frame)
    assert anchor.sat_id == "C21"
    assert anchor.time_system == "BDT"
    assert anchor.week == 1500
    assert anchor.tow_s == 12 * 3600 + 44 * 18  # HOW and SOH name this frame's first symbol
    assert anchor.code_phase_ms == 640 * 10.0
    # 18 s later in code phase is 18 s later in time.
    assert anchor.transmit_time_s(640 * 10.0 + 18_000.0) == pytest.approx(anchor.tow_s + 18.0)
    with pytest.raises(ValueError, match="past the end"):
        observables.anchor_from_bcnav1(stream, type(frame)(**{**frame.__dict__, "symbol_index": 9000}))


# ---------------------------------------------------------------------------
# The whole chain: synthetic B1C signal -> tracking -> symbols -> frame
# ---------------------------------------------------------------------------


def test_frame_survives_the_synthetic_signal_and_the_tracking_loops():
    """
    Transmit one real frame as the data of a synthetic B1C signal, acquire and track
    it, extract the symbols, and decode.  The 180 degree carrier ambiguity is handled
    by the decoder.  The frame starts 3 s in, after the overlay search (2 s of
    prompts) has locked, so the whole frame is inside the usable stream.
    """
    from .test_b1c import _acquire_and_track

    rng = np.random.default_rng(3)
    information = np.zeros(576, dtype=np.uint8)
    for start, length, raw in SUBFRAME2_FIELDS:
        information = bcnav1.set_data_field(information, start, length, raw)
    frame_bits = _frame_bits(prn=PRN, soh_count=5, subframe2=bcnav1.build_subframe2(information))
    first_frame_symbol = 300
    lead = rng.integers(0, 2, first_frame_symbol)
    nav_symbols = bcnav1.soft_symbols(np.concatenate([lead, frame_bits, frame_bits]))

    adapter = _acquire_and_track(noise_sigma=2.0, buffers=230, nav_bits=nav_symbols)
    stream = symbols.extract(adapter.outputs, "BDS_B1C")
    result = bcnav1.decode(stream.soft, expected_prn=PRN)

    assert result.synced, "no frame decoded from the tracked symbols"
    frame = result.frames[0]
    assert (frame.prn, frame.soh_count) == (PRN, 5)
    eph = bcnav1.assemble_ephemeris(result)
    assert eph.toe == 1000 * 300 and eph.week == 1234

    # The anchor ties the time of week to the code phase of the frame's first symbol:
    # symbol 300 of the transmitted sequence is 3000 ms of code phase.
    anchor = observables.anchor_from_bcnav1(stream, frame)
    assert anchor.code_phase_ms == pytest.approx(first_frame_symbol * 10.0, abs=0.01)
    assert anchor.tow_s == 100 * 3600 + 5 * 18
