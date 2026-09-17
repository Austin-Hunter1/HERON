"""
LNAV: the parity chain, subframe sync, and every field against Figure 20-1.

The chained parity is what makes LNAV distinctive and what makes it easy to get
subtly wrong, so it gets its own tests: a subframe built with the wrong incoming
D29*/D30* must fail, a run of subframes must carry the chain across boundaries,
and a stream arriving inverted must still decode.

Field tests write one value at the bit position IS-GPS-200N prints and read it
back through the real parser, so start position, width, sign convention and scale
factor are each checked against the spec rather than against an encoder that
shares the parser's assumptions.
"""

import numpy as np
import pytest

from utils.nav import bitsync, lnav
from utils.nav import primitives as prim


def soft(bits: np.ndarray, *, inverted: bool = False) -> np.ndarray:
    s = 1.0 - 2.0 * np.asarray(bits, dtype=np.float64)
    return -s if inverted else s


# ---------------------------------------------------------------------------
# Word and subframe construction
# ---------------------------------------------------------------------------


def test_build_subframe_has_the_right_shape_and_header():
    bits, _, _ = lnav.build_subframe(subframe_id=3, tow_count=54321)
    assert len(bits) == lnav.SUBFRAME_BITS
    recovered, ok, _, _ = lnav._recover_subframe(bits, 0, 0)
    assert ok
    assert np.array_equal(recovered[:8], lnav.PREAMBLE_BITS)
    assert prim.unpack_field(recovered, 31, 17) == 54321
    assert prim.unpack_field(recovered, 50, 3) == 3


def test_build_subframe_rejects_bad_arguments():
    with pytest.raises(ValueError):
        lnav.build_subframe(subframe_id=6, tow_count=0)
    with pytest.raises(ValueError):
        lnav.build_subframe(subframe_id=1, tow_count=1 << 17)
    with pytest.raises(ValueError, match="300 bits"):
        lnav.build_subframe(subframe_id=1, tow_count=0, data=np.zeros(10, dtype=np.uint8))


@pytest.mark.parametrize("d29,d30", [(0, 0), (0, 1), (1, 0), (1, 1)])
def test_parity_chain_validates_only_with_the_right_seed(d29, d30):
    bits, _, _ = lnav.build_subframe(subframe_id=1, tow_count=1000, d29_star=d29, d30_star=d30)
    _, ok, _, _ = lnav._recover_subframe(bits, d29, d30)
    assert ok
    # D30* changes the transmitted data, so a wrong D30* must fail.  D29* only
    # enters three of the six parity equations, but that is still enough.
    _, wrong_ok, _, _ = lnav._recover_subframe(bits, d29, 1 - d30)
    assert not wrong_ok


def test_parity_chain_carries_across_subframes():
    frame = lnav.build_frame(first_tow_count=1000)
    assert len(frame) == 5 * lnav.SUBFRAME_BITS
    d29, d30 = 0, 0
    for i in range(5):
        window = frame[i * lnav.SUBFRAME_BITS : (i + 1) * lnav.SUBFRAME_BITS]
        _, ok, d29, d30 = lnav._recover_subframe(window, d29, d30)
        assert ok, f"subframe {i} failed with the chained parity state"


def test_a_single_flipped_bit_breaks_parity():
    bits, _, _ = lnav.build_subframe(subframe_id=2, tow_count=500)
    for i in (0, 29, 30, 150, 299):
        corrupted = bits.copy()
        corrupted[i] ^= 1
        _, ok, _, _ = lnav._recover_subframe(corrupted, 0, 0)
        assert not ok, f"flipping bit {i} went undetected"


# ---------------------------------------------------------------------------
# Synchronisation
# ---------------------------------------------------------------------------


def test_decodes_a_clean_frame():
    frame = lnav.build_frame(first_tow_count=2000)
    result = lnav.decode(soft(frame))
    assert result.synced
    assert [s.subframe_id for s in result.subframes] == [1, 2, 3, 4, 5]
    assert [s.tow_count for s in result.subframes] == [2000, 2001, 2002, 2003, 2004]
    assert not result.inverted


def test_finds_a_frame_that_does_not_start_at_offset_zero():
    rng = np.random.default_rng(0)
    frame = lnav.build_frame(first_tow_count=3000)
    stream = np.concatenate([rng.integers(0, 2, 137).astype(np.uint8), frame])
    result = lnav.decode(soft(stream))
    assert result.synced
    assert result.bit_offset == 137
    assert len(result.subframes) == 5


def test_decodes_an_inverted_stream():
    """
    The carrier's 180 degree ambiguity must not cost a frame -- and in LNAV it
    costs nothing at all, which is worth stating because it differs from CNAV.

    D_n = d_n XOR D30*, and flipping both D29* and D30* flips all six parity bits
    too, so the complement of a valid LNAV stream is *itself* a valid LNAV stream
    read with the opposite parity seed, carrying identical data.  The decoder
    therefore recovers the same subframes without ever concluding the stream was
    inverted, and `result.inverted` stays False.  CNAV has no such mechanism, which
    is why `cnav.decode` has to try both polarities explicitly.
    """
    frame = lnav.build_frame(first_tow_count=4000)
    upright = lnav.decode(soft(frame))
    result = lnav.decode(soft(frame, inverted=True))
    assert result.synced
    assert [s.subframe_id for s in result.subframes] == [1, 2, 3, 4, 5]
    assert [s.tow_count for s in result.subframes] == [4000, 4001, 4002, 4003, 4004]
    # Same data out, either way in.
    assert all(
        np.array_equal(a.bits[:24], b.bits[:24])
        for a, b in zip(upright.subframes, result.subframes)
    )


def test_rejects_noise():
    rng = np.random.default_rng(7)
    assert not lnav.decode(rng.normal(0.0, 1.0, 2000)).synced


def test_rejects_a_stream_shorter_than_one_subframe():
    assert not lnav.decode(np.zeros(299)).synced


def test_symbol_index_locates_each_subframe():
    lead = 61
    rng = np.random.default_rng(1)
    frame = lnav.build_frame(first_tow_count=5000)
    stream = np.concatenate([rng.integers(0, 2, lead).astype(np.uint8), frame])
    result = lnav.decode(soft(stream))
    for i, s in enumerate(result.subframes):
        assert s.symbol_index == lead + i * lnav.SUBFRAME_BITS


def test_tow_refers_to_the_start_of_the_next_subframe():
    frame = lnav.build_frame(first_tow_count=6000)
    s = lnav.decode(soft(frame)).subframes[0]
    assert s.tow_at_next_subframe_start_s == 6000 * 6.0
    assert s.tow_at_subframe_start_s == 6000 * 6.0 - 6.0


def test_max_subframes_limits_the_walk():
    frame = lnav.build_frame(first_tow_count=7000)
    result = lnav.decode(soft(frame), max_subframes=2)
    assert len(result.subframes) == 2


def test_alert_and_anti_spoof_flags():
    bits, _, _ = lnav.build_subframe(
        subframe_id=1, tow_count=10, alert=True, anti_spoof=True
    )
    s = lnav.decode(soft(bits)).subframes[0]
    assert s.alert and s.anti_spoof


# ---------------------------------------------------------------------------
# Field extraction
# ---------------------------------------------------------------------------


def subframe_with(subframe_id: int, mutate) -> lnav.LnavSubframe:
    """A decoded subframe whose template was mutated by `mutate` before encoding."""
    template = mutate(np.zeros(lnav.SUBFRAME_BITS, dtype=np.uint8))
    bits, _, _ = lnav.build_subframe(
        subframe_id=subframe_id, tow_count=1234, data=template
    )
    result = lnav.decode(soft(bits))
    assert result.synced, "the test fixture itself failed to decode"
    return result.subframes[0]


@pytest.mark.parametrize(
    "attribute,start,length,raw,expected",
    [
        # IS-GPS-200N Figure 20-1 sheet 1, Table 20-I.
        ("week", 61, 10, 1023, 1023),
        ("l2_codes", 71, 2, 2, 2),
        ("ura_index", 73, 4, 5, 5),
        ("health", 77, 6, 0b101010, 0b101010),
        ("l2_p_data_flag", 91, 1, 1, 1),
        ("tgd", 197, 8, -30 & 0xFF, -30 * 2.0**-31),
        ("toc", 219, 16, 18900, 18900 * 16.0),
        ("af2", 241, 8, 3, 3 * 2.0**-55),
        ("af1", 249, 16, -77 & 0xFFFF, -77 * 2.0**-43),
        ("af0", 271, 22, -123456 & 0x3FFFFF, -123456 * 2.0**-31),
    ],
)
def test_subframe_1_fields(attribute, start, length, raw, expected):
    s = subframe_with(1, lambda b: lnav.set_field(b, start, length, raw))
    assert getattr(lnav.parse_subframe_1(s), attribute) == pytest.approx(expected)


def test_subframe_1_iodc_is_split_across_words_3_and_8():
    """2 MSBs at bits 83-84, 8 LSBs at bits 211-218 -- 10 bits in total."""
    s = subframe_with(1, lambda b: lnav.set_split_field(b, 83, 2, 211, 8, 0b1011010101))
    assert lnav.parse_subframe_1(s).iodc == 0b1011010101


@pytest.mark.parametrize(
    "attribute,start,length,raw,expected",
    [
        # Figure 20-1 sheet 2, Table 20-III.
        ("iode", 61, 8, 200, 200),
        ("Crs", 69, 16, -1000 & 0xFFFF, -1000 * 2.0**-5),
        ("deln", 91, 16, 500, 500 * 2.0**-43),
        ("Cuc", 151, 16, -2000 & 0xFFFF, -2000 * 2.0**-29),
        ("Cus", 211, 16, 2000, 2000 * 2.0**-29),
        ("toe", 271, 16, 18900, 18900 * 16.0),
        ("fit_interval_flag", 287, 1, 1, 1),
        ("aodo", 288, 5, 17, 17),
    ],
)
def test_subframe_2_fields(attribute, start, length, raw, expected):
    s = subframe_with(2, lambda b: lnav.set_field(b, start, length, raw))
    assert getattr(lnav.parse_subframe_2(s), attribute) == pytest.approx(expected)


@pytest.mark.parametrize(
    "attribute,msb_start,msb_len,lsb_start,lsb_len,raw,scale,signed",
    [
        ("M0", 107, 8, 121, 24, -1234567 & ((1 << 32) - 1), 2.0**-31, True),
        ("e", 167, 8, 181, 24, 40000000, 2.0**-33, False),
        ("sqrt_a", 227, 8, 241, 24, 2700000000, 2.0**-19, False),
    ],
)
def test_subframe_2_split_fields(attribute, msb_start, msb_len, lsb_start, lsb_len, raw, scale, signed):
    s = subframe_with(
        2, lambda b: lnav.set_split_field(b, msb_start, msb_len, lsb_start, lsb_len, raw)
    )
    value = getattr(lnav.parse_subframe_2(s), attribute)
    width = msb_len + lsb_len
    signed_raw = raw - (1 << width) if signed and raw >= (1 << (width - 1)) else raw
    assert value == pytest.approx(signed_raw * scale)


def test_subframe_2_eccentricity_is_unsigned():
    s = subframe_with(2, lambda b: lnav.set_split_field(b, 167, 8, 181, 24, 1 << 31))
    assert lnav.parse_subframe_2(s).e > 0


@pytest.mark.parametrize(
    "attribute,start,length,raw,expected",
    [
        # Figure 20-1 sheet 3, Table 20-III.
        ("Cic", 61, 16, -500 & 0xFFFF, -500 * 2.0**-29),
        ("Cis", 121, 16, 500, 500 * 2.0**-29),
        ("Crc", 181, 16, -3000 & 0xFFFF, -3000 * 2.0**-5),
        ("Omega_dot", 241, 24, -8000 & 0xFFFFFF, -8000 * 2.0**-43),
        ("iode", 271, 8, 200, 200),
        ("i_dot", 279, 14, -100 & 0x3FFF, -100 * 2.0**-43),
    ],
)
def test_subframe_3_fields(attribute, start, length, raw, expected):
    s = subframe_with(3, lambda b: lnav.set_field(b, start, length, raw))
    assert getattr(lnav.parse_subframe_3(s), attribute) == pytest.approx(expected)


@pytest.mark.parametrize(
    "attribute,msb_start,lsb_start,raw",
    [
        ("Omega0", 77, 91, -900000000 & ((1 << 32) - 1)),
        ("i0", 137, 151, 600000000),
        ("omega", 197, 211, -700000000 & ((1 << 32) - 1)),
    ],
)
def test_subframe_3_split_fields(attribute, msb_start, lsb_start, raw):
    s = subframe_with(3, lambda b: lnav.set_split_field(b, msb_start, 8, lsb_start, 24, raw))
    signed_raw = raw - (1 << 32) if raw >= (1 << 31) else raw
    assert getattr(lnav.parse_subframe_3(s), attribute) == pytest.approx(signed_raw * 2.0**-31)


def test_subframe_3_fields_do_not_overlap():
    s = subframe_with(3, lambda b: lnav.set_field(b, 121, 16, 0xFFFF))
    parsed = lnav.parse_subframe_3(s)
    assert parsed.Cis != 0
    for other in ("Cic", "Omega0", "i0", "Crc", "omega", "Omega_dot", "i_dot"):
        assert getattr(parsed, other) == 0, f"{other} was disturbed by writing Cis"


def test_parsers_reject_the_wrong_subframe():
    s = subframe_with(1, lambda b: b)
    with pytest.raises(ValueError, match="subframe 2"):
        lnav.parse_subframe_2(s)
    with pytest.raises(ValueError, match="subframe 3"):
        lnav.parse_subframe_3(s)


# ---------------------------------------------------------------------------
# Ionosphere and UTC, subframe 4 page 18
# ---------------------------------------------------------------------------


def test_iono_utc_page_is_identified_by_its_sv_id():
    s = subframe_with(4, lambda b: lnav.set_field(b, 63, 6, lnav.IONO_UTC_PAGE_SV_ID))
    assert lnav.parse_iono_utc(s) is not None
    other = subframe_with(4, lambda b: lnav.set_field(b, 63, 6, 25))
    assert lnav.parse_iono_utc(other) is None
    assert lnav.parse_iono_utc(subframe_with(1, lambda b: b)) is None


# Figure 20-1 sheet 8.  The eight coefficients are not evenly spaced: word 3's
# parity sits between alpha1 and alpha2, and word 4's between beta0 and beta1.
ALPHA_STARTS = (69, 77, 91, 99)
BETA_STARTS = (107, 121, 129, 137)


def test_klobuchar_coefficient_starts_avoid_the_parity_fields():
    """
    Each coefficient must lie wholly within a word's 24 data bits.  This is the
    check that catches the tempting `69 + 8*i` spacing, which puts alpha2 on top of
    word 3's parity.
    """
    for start in ALPHA_STARTS + BETA_STARTS:
        word, offset = divmod(start - 1, lnav.WORD_BITS)
        assert offset + 8 <= lnav.DATA_BITS_PER_WORD, (
            f"a coefficient at bit {start} runs into word {word + 1}'s parity"
        )


def test_klobuchar_coefficients():
    alpha_scales = (2.0**-30, 2.0**-27, 2.0**-24, 2.0**-24)
    beta_scales = (2.0**11, 2.0**14, 2.0**16, 2.0**16)
    for i, start in enumerate(ALPHA_STARTS):
        s = subframe_with(
            4,
            lambda b, start=start: lnav.set_field(
                lnav.set_field(b, 63, 6, lnav.IONO_UTC_PAGE_SV_ID), start, 8, 9
            ),
        )
        parsed = lnav.parse_iono_utc(s)
        assert parsed.alpha[i] == pytest.approx(9 * alpha_scales[i])
        assert all(parsed.alpha[j] == 0 for j in range(4) if j != i)
        assert all(b == 0 for b in parsed.beta)
    for i, start in enumerate(BETA_STARTS):
        s = subframe_with(
            4,
            lambda b, start=start: lnav.set_field(
                lnav.set_field(b, 63, 6, lnav.IONO_UTC_PAGE_SV_ID), start, 8, 9
            ),
        )
        parsed = lnav.parse_iono_utc(s)
        assert parsed.beta[i] == pytest.approx(9 * beta_scales[i])
        assert all(parsed.beta[j] == 0 for j in range(4) if j != i)
        assert all(a == 0 for a in parsed.alpha)


def test_utc_parameters():
    def build(b):
        b = lnav.set_field(b, 63, 6, lnav.IONO_UTC_PAGE_SV_ID)
        b = lnav.set_field(b, 151, 24, 12345)                      # A1
        b = lnav.set_split_field(b, 181, 24, 211, 8, 987654321)     # A0
        b = lnav.set_field(b, 219, 8, 100)                          # t_ot
        b = lnav.set_field(b, 227, 8, 200)                          # WN_t
        b = lnav.set_field(b, 241, 8, 18)                           # delta t_LS
        b = lnav.set_field(b, 249, 8, 201)                          # WN_LSF
        b = lnav.set_field(b, 257, 8, 7)                            # DN
        b = lnav.set_field(b, 271, 8, 19)                           # delta t_LSF
        return b

    parsed = lnav.parse_iono_utc(subframe_with(4, build))
    assert parsed.A1 == pytest.approx(12345 * 2.0**-50)
    assert parsed.A0 == pytest.approx(987654321 * 2.0**-30)
    assert parsed.t_ot == pytest.approx(100 * 4096.0)
    assert parsed.week_t == 200
    assert parsed.delta_t_ls == 18
    assert parsed.week_lsf == 201
    assert parsed.day_number == 7
    assert parsed.delta_t_lsf == 19


# ---------------------------------------------------------------------------
# Ephemeris assembly
# ---------------------------------------------------------------------------


def realistic_frame(iode: int = 88, mismatch_iode: int | None = None) -> np.ndarray:
    """Subframes 1-3 carrying a plausible orbit, with a consistent issue of data."""
    s1 = np.zeros(lnav.SUBFRAME_BITS, dtype=np.uint8)
    s1 = lnav.set_field(s1, 61, 10, 233)                     # week (mod 1024)
    s1 = lnav.set_split_field(s1, 83, 2, 211, 8, iode)       # IODC, LSBs match IODE
    s1 = lnav.set_field(s1, 219, 16, 18900)                  # toc = 302400
    s1 = lnav.set_field(s1, 271, 22, round(-1.2e-4 * 2**31) & 0x3FFFFF)  # af0

    s2 = np.zeros(lnav.SUBFRAME_BITS, dtype=np.uint8)
    s2 = lnav.set_field(s2, 61, 8, iode if mismatch_iode is None else mismatch_iode)
    s2 = lnav.set_split_field(s2, 227, 8, 241, 24, round(5153.65 * 2**19))  # sqrtA
    s2 = lnav.set_split_field(s2, 167, 8, 181, 24, round(0.0089 * 2**33))   # e
    s2 = lnav.set_split_field(s2, 107, 8, 121, 24, round(0.11 * 2**31))     # M0
    s2 = lnav.set_field(s2, 271, 16, 18900)                                 # toe

    s3 = np.zeros(lnav.SUBFRAME_BITS, dtype=np.uint8)
    s3 = lnav.set_split_field(s3, 77, 8, 91, 24, round(0.33 * 2**31))    # Omega0
    s3 = lnav.set_split_field(s3, 137, 8, 151, 24, round(0.306 * 2**31))  # i0 ~55 deg
    s3 = lnav.set_split_field(s3, 197, 8, 211, 24, round(0.20 * 2**31))   # omega
    s3 = lnav.set_field(s3, 271, 8, iode)

    return lnav.build_frame(
        first_tow_count=1000,
        subframe_ids=(1, 2, 3),
        templates={1: s1, 2: s2, 3: s3},
    )


def test_assemble_ephemeris_from_a_decoded_frame():
    result = lnav.decode(soft(realistic_frame()))
    e = lnav.assemble_ephemeris(result, sat_id="G07")
    assert e is not None
    assert e.sat_id == "G07"
    assert e.week == 233
    assert e.sqrt_a == pytest.approx(5153.65, abs=1e-5)
    assert e.e == pytest.approx(0.0089, abs=1e-9)
    assert e.toe == 302400.0
    radius = np.linalg.norm(e.orbit_state(e.toe).position_ecef_m)
    assert 2.4e7 < radius < 2.8e7


def test_assemble_ephemeris_needs_all_three_subframes():
    result = lnav.decode(soft(realistic_frame()))
    for drop in (1, 2, 3):
        partial = lnav.LnavDecodeResult(
            subframes=[s for s in result.subframes if s.subframe_id != drop]
        )
        assert lnav.assemble_ephemeris(partial, "G07") is None


def test_assemble_ephemeris_rejects_mismatched_issue_of_data():
    """
    IODE in subframes 2 and 3 must agree with IODC's eight LSBs.  A mismatch means
    an upload landed mid-frame and the halves describe different orbits.
    """
    result = lnav.decode(soft(realistic_frame(mismatch_iode=99)))
    assert lnav.assemble_ephemeris(result, "G07") is None


# ---------------------------------------------------------------------------
# Bit synchronisation
# ---------------------------------------------------------------------------


def prompts_for_bits(bits, phase, *, noise=0.0, rng=None, periods=20):
    """1 ms prompt values for a bit stream, offset so the first bit starts at `phase`."""
    rng = rng or np.random.default_rng(0)
    symbols = np.repeat(1.0 - 2.0 * np.asarray(bits, dtype=float), periods)
    lead = rng.choice([-1.0, 1.0]) * np.ones(phase)
    stream = np.concatenate([lead, symbols])
    if noise:
        stream = stream + rng.normal(0.0, noise, len(stream))
    return stream.astype(complex)


@pytest.mark.parametrize("phase", [0, 1, 7, 13, 19])
def test_bit_sync_finds_the_boundary(phase):
    rng = np.random.default_rng(2)
    bits = rng.integers(0, 2, 200)
    result = bitsync.synchronise(prompts_for_bits(bits, phase, rng=rng))
    assert result.synced
    assert result.phase == phase
    assert result.confidence > 2.0


def test_bit_sync_survives_noise():
    rng = np.random.default_rng(3)
    bits = rng.integers(0, 2, 400)
    prompts = prompts_for_bits(bits, 11, noise=0.5, rng=rng)
    result = bitsync.synchronise(prompts)
    assert result.synced and result.phase == 11


def test_bit_sync_reports_no_confidence_on_noise():
    rng = np.random.default_rng(4)
    result = bitsync.synchronise(rng.normal(0, 1, 4000).astype(complex))
    assert not result.synced


def test_bit_sync_refuses_too_short_a_span():
    rng = np.random.default_rng(5)
    bits = rng.integers(0, 2, 5)
    assert not bitsync.synchronise(prompts_for_bits(bits, 0, rng=rng)).synced


def test_bit_sync_reports_no_evidence_when_the_data_never_flips():
    """
    A constant bit stream has no transitions anywhere.  Reporting phase 0 with
    infinite confidence would be a lie; the right answer is 'no evidence'.
    """
    constant = np.ones(2000, dtype=complex)
    result = bitsync.synchronise(constant)
    assert not result.synced
    assert result.confidence == 0.0


def test_fold_to_symbols_sums_whole_bits():
    rng = np.random.default_rng(6)
    bits = rng.integers(0, 2, 50)
    phase = 9
    prompts = prompts_for_bits(bits, phase, rng=rng)
    symbols = bitsync.fold_to_symbols(prompts, phase)
    assert len(symbols) == 50
    assert np.array_equal(prim.hard_decision(np.real(symbols)), bits.astype(np.uint8))
    # Coherent summation over twenty periods: magnitude 20, not 1.
    assert np.abs(symbols).min() == pytest.approx(20.0)


def test_fold_to_symbols_discards_a_leading_partial_bit():
    prompts = np.ones(45, dtype=complex)
    assert len(bitsync.fold_to_symbols(prompts, phase=7)) == 1  # (45-7)//20


def test_bit_slices_reports_what_is_usable():
    result = bitsync.BitSyncResult(7, 5.0, np.zeros(20), True)
    assert result.bit_slices(107) == (7, 5)


def test_lnav_decodes_from_folded_prompts():
    """End to end within this module: prompts -> bit sync -> fold -> subframes."""
    rng = np.random.default_rng(8)
    frame = lnav.build_frame(first_tow_count=9000)
    phase = 13
    prompts = prompts_for_bits(frame, phase, noise=0.3, rng=rng)
    sync = bitsync.synchronise(prompts)
    assert sync.synced and sync.phase == phase
    symbols = np.real(bitsync.fold_to_symbols(prompts, sync.phase))
    result = lnav.decode(symbols)
    assert result.synced
    assert [s.subframe_id for s in result.subframes] == [1, 2, 3, 4, 5]
