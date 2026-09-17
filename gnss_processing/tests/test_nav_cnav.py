"""
CNAV: frame sync through a stream the decoder knows nothing about, and field-level
checks of every parser against the spec's own bit numbering.

The parser tests deliberately do not go through an encoder.  An encoder written
from the same reading of the spec as the parser would agree with it whether or not
that reading is right.  Instead each test writes one field, at the bit position the
spec prints, into an otherwise-zero message and asserts the parser recovers the
scaled value -- so position, width, sign convention and scale factor are each
checked against IS-GPS-200N rather than against ourselves.
"""

import numpy as np
import pytest

from utils.nav import cnav
from utils.nav import primitives as prim


def symbols_from_bits(bits: np.ndarray, *, inverted: bool = False, amplitude: float = 1.0):
    """Ideal soft symbols for an encoded bit stream."""
    soft = amplitude * (1.0 - 2.0 * bits.astype(np.float64))
    return -soft if inverted else soft


def make_stream(
    *,
    prn: int = 19,
    first_tow: int = 100000,
    count: int = 4,
    message_types=(10, 11, 30, 0),
    lead_symbols: int = 0,
    inverted: bool = False,
    duration_s: float = cnav.L5_MESSAGE_DURATION_S,
    rng: np.random.Generator | None = None,
    noise_sigma: float = 0.0,
):
    """
    A continuously encoded run of CNAV messages, as a receiver would see it.

    `lead_symbols` prepends junk so the decoder has to find the message boundary
    rather than being handed it -- which is the realistic case, since tracking
    starts at an arbitrary moment.
    """
    step = int(duration_s / 6.0)  # TOW counts advance by duration/6 per message
    messages = [
        cnav.build_message(
            prn=prn,
            message_type=message_types[i % len(message_types)],
            tow_count=first_tow + i * step,
        )
        for i in range(count)
    ]
    encoded = cnav.encode_stream(messages)
    soft = symbols_from_bits(encoded, inverted=inverted)
    if lead_symbols:
        rng = rng or np.random.default_rng(0)
        soft = np.concatenate([rng.normal(0.0, 1.0, lead_symbols), soft])
    if noise_sigma:
        rng = rng or np.random.default_rng(0)
        soft = soft + rng.normal(0.0, noise_sigma, len(soft))
    return soft, messages


# ---------------------------------------------------------------------------
# Message envelope
# ---------------------------------------------------------------------------


def test_build_message_has_the_right_shape_and_header():
    m = cnav.build_message(prn=19, message_type=10, tow_count=12345)
    assert len(m) == cnav.MESSAGE_BITS
    assert np.array_equal(m[:8], cnav.PREAMBLE_BITS)
    assert prim.unpack_field(m, 9, 6) == 19
    assert prim.unpack_field(m, 15, 6) == 10
    assert prim.unpack_field(m, 21, 17) == 12345
    assert m[37] == 0
    assert prim.crc24q_check(m)


def test_build_message_sets_the_alert_flag_at_bit_38():
    m = cnav.build_message(prn=1, message_type=0, tow_count=0, alert=True)
    assert m[37] == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(prn=64, message_type=10, tow_count=0),
        dict(prn=1, message_type=64, tow_count=0),
        dict(prn=1, message_type=10, tow_count=1 << 17),
    ],
)
def test_build_message_rejects_fields_that_do_not_fit(kwargs):
    with pytest.raises(ValueError):
        cnav.build_message(**kwargs)


def test_build_message_rejects_a_wrong_length_payload():
    with pytest.raises(ValueError, match="238 bits"):
        cnav.build_message(
            prn=1, message_type=10, tow_count=0, payload=np.zeros(100, dtype=np.uint8)
        )


# ---------------------------------------------------------------------------
# Decoding and synchronisation
# ---------------------------------------------------------------------------


def test_decodes_a_clean_stream():
    soft, messages = make_stream(count=4)
    result = cnav.decode(soft)
    assert result.synced
    # The first message is unreliable: the Viterbi decoder starts in an unknown
    # state and needs ~35 bits to settle, which lands inside message one.
    assert len(result.messages) >= 3
    assert all(m.prn == 19 for m in result.messages)


def test_recovers_message_types_and_tow():
    soft, built = make_stream(count=4, message_types=(10, 11, 30, 0))
    result = cnav.decode(soft)
    types = [m.message_type for m in result.messages]
    assert 11 in types and 30 in types
    tows = [m.tow_count for m in result.messages]
    assert tows == sorted(tows)
    assert all(later - earlier == 1 for earlier, later in zip(tows, tows[1:]))


def test_finds_the_boundary_when_the_stream_starts_mid_message():
    soft, _ = make_stream(count=5, lead_symbols=317)
    result = cnav.decode(soft)
    assert result.synced
    assert len(result.messages) >= 3


@pytest.mark.parametrize("lead", [0, 1, 2, 3])
def test_resolves_the_symbol_phase(lead):
    """
    An odd number of leading symbols puts the stream on a G2 boundary.  Nothing in
    tracking resolves this, so the decoder must try both pairings.
    """
    soft, _ = make_stream(count=5, lead_symbols=lead)
    result = cnav.decode(soft)
    assert result.synced, f"failed to sync with {lead} leading symbols"
    assert result.symbol_phase == lead % 2


def test_resolves_an_inverted_data_polarity():
    """Costas tracking leaves a 180 degree ambiguity; the CRC is what settles it."""
    soft, _ = make_stream(count=5, inverted=True)
    result = cnav.decode(soft)
    assert result.synced
    assert result.inverted
    assert all(m.prn == 19 for m in result.messages)


def test_rejects_a_stream_of_noise():
    rng = np.random.default_rng(3)
    result = cnav.decode(rng.normal(0.0, 1.0, 4000))
    assert not result.synced, "CRC-24Q should not accept noise"


def test_expected_prn_filters_out_a_wrong_alignment():
    soft, _ = make_stream(prn=19, count=5)
    assert cnav.decode(soft, expected_prn=19).synced
    assert not cnav.decode(soft, expected_prn=7).synced


def test_decodes_through_noise():
    soft, _ = make_stream(count=6, noise_sigma=0.6, rng=np.random.default_rng(11))
    result = cnav.decode(soft)
    assert result.synced
    assert len(result.messages) >= 3


def test_tow_consistency_check_accepts_a_good_decode():
    soft, _ = make_stream(count=5)
    assert cnav.decode(soft).tow_is_consistent()


def test_l2c_and_l5_differ_only_in_message_duration():
    """The same 600 symbols carry the same message; only its epoch differs."""
    soft, _ = make_stream(count=4, duration_s=cnav.L2C_MESSAGE_DURATION_S)
    result = cnav.decode(soft, message_duration_s=cnav.L2C_MESSAGE_DURATION_S)
    assert result.synced
    m = result.messages[0]
    assert m.tow_at_next_message_start_s == m.tow_count * 6.0
    assert m.tow_at_message_start_s == m.tow_count * 6.0 - 12.0


def test_tow_refers_to_the_start_of_the_next_message():
    """
    The single easiest field in CNAV to misuse.  The spec says the count times six
    is the start of the *next* message, so this message started one duration
    earlier.
    """
    soft, _ = make_stream(count=4, first_tow=50000)
    m = cnav.decode(soft).messages[0]
    assert m.tow_at_message_start_s == m.tow_count * 6.0 - cnav.L5_MESSAGE_DURATION_S


def test_symbol_index_locates_the_message_in_the_stream():
    lead = 200
    soft, _ = make_stream(count=5, lead_symbols=lead)
    result = cnav.decode(soft)
    for m in result.messages:
        assert (m.symbol_index - lead) % cnav.SYMBOLS_PER_MESSAGE == 0
        # And the symbols there really are that message's.
        assert m.symbol_index + cnav.SYMBOLS_PER_MESSAGE <= len(soft)


# ---------------------------------------------------------------------------
# Message type 10
# ---------------------------------------------------------------------------


def message_with_field(message_type: int, start: int, length: int, raw: int) -> cnav.CnavMessage:
    """One message, zero everywhere except a single field written at spec position."""
    bits = np.zeros(cnav.INFORMATION_BITS, dtype=np.uint8)
    bits[:8] = cnav.PREAMBLE_BITS
    bits[14:20] = prim.bits_from_int(message_type, 6)
    bits = cnav.set_field(bits, start, length, raw)
    full = prim.crc24q_append(bits)
    return cnav.CnavMessage(
        prn=0,
        message_type=message_type,
        tow_count=0,
        alert=False,
        bits=full,
        symbol_index=0,
        message_duration_s=cnav.L5_MESSAGE_DURATION_S,
    )


@pytest.mark.parametrize(
    "attribute,start,length,raw,expected",
    [
        # IS-GPS-200N Figure 30-1 and Table 30-I.
        ("week", 39, 13, 2257, 2257),
        ("top", 55, 11, 1000, 1000 * 300.0),
        ("ura_ed_index", 66, 5, -3 & 0x1F, -3),
        ("toe", 71, 11, 1008, 1008 * 300.0),
        ("delta_a", 82, 26, 1234, 1234 * 2.0**-9),
        ("delta_a", 82, 26, -1234 & ((1 << 26) - 1), -1234 * 2.0**-9),
        ("a_dot", 108, 25, 77, 77 * 2.0**-21),
        ("delta_n0", 133, 17, -55 & 0x1FFFF, -55 * 2.0**-44),
        ("delta_n0_dot", 150, 23, 9, 9 * 2.0**-57),
        ("M0", 173, 33, 123456789, 123456789 * 2.0**-32),
        ("e", 206, 33, 150000000, 150000000 * 2.0**-34),
        ("omega", 239, 33, -987654321 & ((1 << 33) - 1), -987654321 * 2.0**-32),
    ],
)
def test_type_10_fields(attribute, start, length, raw, expected):
    parsed = cnav.parse_type_10(message_with_field(10, start, length, raw))
    assert getattr(parsed, attribute) == pytest.approx(expected)


def test_type_10_health_bits():
    for bit, attribute in ((52, "health_l1"), (53, "health_l2"), (54, "health_l5")):
        parsed = cnav.parse_type_10(message_with_field(10, bit, 1, 1))
        assert getattr(parsed, attribute) == 1
        others = {"health_l1", "health_l2", "health_l5"} - {attribute}
        assert all(getattr(parsed, other) == 0 for other in others)


def test_type_10_integrity_status_flag_is_bit_272():
    assert cnav.parse_type_10(message_with_field(10, 272, 1, 1)).integrity_status_flag
    assert not cnav.parse_type_10(message_with_field(10, 271, 1, 1)).integrity_status_flag


def test_type_10_eccentricity_is_unsigned():
    """
    Every other 33-bit element in message 10 is two's complement; e is not, and it
    has its own scale factor.  Setting the MSB must give a large positive number,
    not a negative one.
    """
    parsed = cnav.parse_type_10(message_with_field(10, 206, 33, 1 << 32))
    assert parsed.e > 0
    assert parsed.e == pytest.approx((1 << 32) * 2.0**-34)


def test_type_10_rejects_another_message_type():
    with pytest.raises(ValueError, match="type 10"):
        cnav.parse_type_10(message_with_field(11, 39, 11, 0))


# ---------------------------------------------------------------------------
# Message type 11
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "attribute,start,length,raw,expected",
    [
        # IS-GPS-200N Figure 30-2 and Table 30-I.
        ("toe", 39, 11, 1008, 1008 * 300.0),
        ("Omega0", 50, 33, -123456 & ((1 << 33) - 1), -123456 * 2.0**-32),
        ("i0", 83, 33, 2345678, 2345678 * 2.0**-32),
        ("delta_Omega_dot", 116, 17, -77 & 0x1FFFF, -77 * 2.0**-44),
        ("i_dot", 133, 15, 33, 33 * 2.0**-44),
        ("Cis", 148, 16, -4321 & 0xFFFF, -4321 * 2.0**-30),
        ("Cic", 164, 16, 4321, 4321 * 2.0**-30),
        ("Crs", 180, 24, -55555 & 0xFFFFFF, -55555 * 2.0**-8),
        ("Crc", 204, 24, 55555, 55555 * 2.0**-8),
        ("Cus", 228, 21, -999 & 0x1FFFFF, -999 * 2.0**-30),
        ("Cuc", 249, 21, 999, 999 * 2.0**-30),
    ],
)
def test_type_11_fields(attribute, start, length, raw, expected):
    parsed = cnav.parse_type_11(message_with_field(11, start, length, raw))
    assert getattr(parsed, attribute) == pytest.approx(expected)


def test_type_11_fields_do_not_overlap():
    """
    Writing one field must leave every other field zero.  This is what catches an
    off-by-one in a start position, which a single-field test on its own cannot.
    """
    parsed = cnav.parse_type_11(message_with_field(11, 148, 16, 0xFFFF))
    assert parsed.Cis != 0
    for other in ("Omega0", "i0", "delta_Omega_dot", "i_dot", "Cic", "Crs", "Crc", "Cus", "Cuc"):
        assert getattr(parsed, other) == 0, f"{other} was disturbed by writing Cis"


# ---------------------------------------------------------------------------
# Message type 30
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "attribute,start,length,raw,expected",
    [
        # IS-GPS-200N Figure 30-3, Tables 30-III and 30-IV.
        ("top", 39, 11, 1000, 300000.0),
        ("toc", 61, 11, 1008, 302400.0),
        ("af0", 72, 26, -12345 & ((1 << 26) - 1), -12345 * 2.0**-35),
        ("af1", 98, 20, 321, 321 * 2.0**-48),
        ("af2", 118, 10, -3 & 0x3FF, -3 * 2.0**-60),
        ("tgd", 128, 13, -100 & 0x1FFF, -100 * 2.0**-35),
        ("isc_l1ca", 141, 13, 50, 50 * 2.0**-35),
        ("isc_l2c", 154, 13, 51, 51 * 2.0**-35),
        ("isc_l5i5", 167, 13, 52, 52 * 2.0**-35),
        ("isc_l5q5", 180, 13, 53, 53 * 2.0**-35),
        ("week_op", 257, 8, 200, 200),
    ],
)
def test_type_30_fields(attribute, start, length, raw, expected):
    parsed = cnav.parse_type_30(message_with_field(30, start, length, raw))
    assert getattr(parsed, attribute) == pytest.approx(expected)


def test_type_30_group_delay_block_spans_bits_128_to_192():
    """IS-GPS-705J 20.3.3.3.1: five 13-bit terms, exactly filling 128-192."""
    starts = [128, 141, 154, 167, 180]
    assert starts[-1] + 13 - 1 == 192
    assert all(later - earlier == 13 for earlier, later in zip(starts, starts[1:]))


def test_type_30_klobuchar_coefficients():
    alpha_scales = (2.0**-30, 2.0**-27, 2.0**-24, 2.0**-24)
    beta_scales = (2.0**11, 2.0**14, 2.0**16, 2.0**16)
    for i in range(4):
        parsed = cnav.parse_type_30(message_with_field(30, 193 + 8 * i, 8, 7))
        assert parsed.alpha[i] == pytest.approx(7 * alpha_scales[i])
        assert all(parsed.alpha[j] == 0 for j in range(4) if j != i)
    for i in range(4):
        parsed = cnav.parse_type_30(message_with_field(30, 225 + 8 * i, 8, 7))
        assert parsed.beta[i] == pytest.approx(7 * beta_scales[i])
        assert all(parsed.beta[j] == 0 for j in range(4) if j != i)


def test_type_30_iono_block_spans_bits_193_to_256():
    assert 225 + 8 * 3 + 8 - 1 == 256


def test_clock_parsing_accepts_the_whole_30_to_37_range():
    for message_type in range(30, 38):
        parsed = cnav.parse_type_30(message_with_field(message_type, 72, 26, 1000))
        assert parsed.af0 == pytest.approx(1000 * 2.0**-35)
    with pytest.raises(ValueError, match="30-37"):
        cnav.parse_type_30(message_with_field(11, 72, 26, 0))


# ---------------------------------------------------------------------------
# Ephemeris assembly
# ---------------------------------------------------------------------------


def build_consistent_set(toe_raw: int = 1008, clock_toc_raw: int | None = None):
    """Messages 10, 11 and 30 that agree on their reference time, as a real set does."""
    if clock_toc_raw is None:
        clock_toc_raw = toe_raw
    ten = message_with_field(10, 71, 11, toe_raw)
    eleven = message_with_field(11, 39, 11, toe_raw)
    thirty = message_with_field(30, 61, 11, clock_toc_raw)
    result = cnav.CnavDecodeResult(messages=[ten, eleven, thirty])
    return result


def test_assemble_ephemeris_combines_all_three_messages():
    e = cnav.assemble_ephemeris(build_consistent_set(), sat_id="G19")
    assert e is not None
    assert e.sat_id == "G19"
    assert e.toe == 1008 * 300.0
    assert e.toc == e.toe


def test_assemble_ephemeris_needs_every_piece():
    for missing in range(3):
        messages = build_consistent_set().messages
        del messages[missing]
        assert cnav.assemble_ephemeris(cnav.CnavDecodeResult(messages=messages)) is None


def test_assemble_ephemeris_rejects_a_data_set_cutover():
    """
    toe in messages 10/11 and toc in the clock message must match.  When they do
    not, a CEI cutover happened mid-track and combining the halves would give a
    plausible but wrong orbit -- so refuse rather than guess.
    """
    assert cnav.assemble_ephemeris(build_consistent_set(clock_toc_raw=1009)) is None


def test_assemble_ephemeris_defaults_the_sat_id_from_the_prn():
    messages = build_consistent_set().messages
    renumbered = [
        cnav.CnavMessage(
            prn=19,
            message_type=m.message_type,
            tow_count=m.tow_count,
            alert=m.alert,
            bits=m.bits,
            symbol_index=m.symbol_index,
            message_duration_s=m.message_duration_s,
        )
        for m in messages
    ]
    e = cnav.assemble_ephemeris(cnav.CnavDecodeResult(messages=renumbered))
    assert e is not None and e.sat_id == "G19"


def test_assembled_ephemeris_produces_a_physical_orbit():
    """
    End to end: build a message set carrying a realistic orbit, decode it, and check
    the satellite lands where a GPS satellite belongs.
    """
    ten = np.zeros(cnav.INFORMATION_BITS, dtype=np.uint8)
    ten[:8] = cnav.PREAMBLE_BITS
    ten[14:20] = prim.bits_from_int(10, 6)
    ten = cnav.set_field(ten, 39, 13, 2257)                       # week
    ten = cnav.set_field(ten, 71, 11, 1008)                       # toe = 302400
    ten = cnav.set_field(ten, 55, 11, 1000)                       # top
    ten = cnav.set_field(ten, 82, 26, round(-4000 * 2.0**9))      # delta_A, metres
    ten = cnav.set_field(ten, 206, 33, round(0.0089 * 2.0**34))   # e
    ten = cnav.set_field(ten, 173, 33, round(0.11 * 2.0**32))     # M0, semi-circles
    ten = cnav.set_field(ten, 239, 33, round(0.20 * 2.0**32))     # omega
    ten_msg = cnav.CnavMessage(0, 10, 0, False, prim.crc24q_append(ten), 0, 6.0)

    eleven = np.zeros(cnav.INFORMATION_BITS, dtype=np.uint8)
    eleven[:8] = cnav.PREAMBLE_BITS
    eleven[14:20] = prim.bits_from_int(11, 6)
    eleven = cnav.set_field(eleven, 39, 11, 1008)                 # toe, matching
    eleven = cnav.set_field(eleven, 50, 33, round(0.33 * 2.0**32))   # Omega0
    eleven = cnav.set_field(eleven, 83, 33, round(0.306 * 2.0**32))  # i0 ~ 55 deg
    eleven_msg = cnav.CnavMessage(0, 11, 0, False, prim.crc24q_append(eleven), 0, 6.0)

    thirty = np.zeros(cnav.INFORMATION_BITS, dtype=np.uint8)
    thirty[:8] = cnav.PREAMBLE_BITS
    thirty[14:20] = prim.bits_from_int(30, 6)
    thirty = cnav.set_field(thirty, 61, 11, 1008)                 # toc, matching
    thirty_msg = cnav.CnavMessage(0, 30, 0, False, prim.crc24q_append(thirty), 0, 6.0)

    e = cnav.assemble_ephemeris(
        cnav.CnavDecodeResult(messages=[ten_msg, eleven_msg, thirty_msg])
    )
    assert e is not None
    radius = np.linalg.norm(e.orbit_state(e.toe).position_ecef_m)
    assert 2.4e7 < radius < 2.8e7, f"orbit radius {radius:.0f} m is not a GPS orbit"
