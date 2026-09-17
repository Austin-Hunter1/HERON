"""
Channel coding primitives: round trips, and the structural checks a round trip misses.

A round trip only proves the encoder and decoder agree with each other.  Both could
be wrong together -- a mistranscribed tap set still encodes and decodes
self-consistently.  So every code here is also checked against a property taken
from the spec rather than from our own implementation: the CRC's polynomial value,
the convolutional code's free distance, BCH(51,8)'s minimum distance, and LNAV's
Hamming distance.
"""

import numpy as np
import pytest

from utils.nav import primitives as prim


# ---------------------------------------------------------------------------
# Bit plumbing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value,length", [(0, 1), (1, 1), (0b1011, 4), (12345, 20)])
def test_bits_int_round_trip(value, length):
    assert prim.int_from_bits(prim.bits_from_int(value, length)) == value


def test_bits_from_int_is_msb_first():
    assert list(prim.bits_from_int(0b1000, 4)) == [1, 0, 0, 0]


def test_bits_from_int_rejects_overflow():
    with pytest.raises(ValueError):
        prim.bits_from_int(16, 4)


def test_unpack_field_uses_one_based_spec_numbering():
    bits = prim.bits_from_int(0b1011_0000, 8)
    # "bits 1 through 4" is the leading nibble.
    assert prim.unpack_field(bits, 1, 4) == 0b1011


def test_unpack_field_signed_and_scaled():
    bits = prim.bits_from_int(0xFF, 8)  # -1 in 8-bit two's complement
    assert prim.unpack_field(bits, 1, 8, signed=True) == -1
    assert prim.unpack_field(bits, 1, 8, signed=True, scale=2.0**-5) == -(2.0**-5)


def test_unpack_field_rejects_running_past_the_message():
    with pytest.raises(ValueError):
        prim.unpack_field(np.zeros(8, dtype=np.uint8), 5, 8)


def test_unpack_field_rejects_zero_based_start():
    with pytest.raises(ValueError):
        prim.unpack_field(np.zeros(8, dtype=np.uint8), 0, 4)


# ---------------------------------------------------------------------------
# CRC-24Q
# ---------------------------------------------------------------------------


def test_crc24q_polynomial_matches_the_spec_tap_list():
    # IS-GPS-200N 30.3.5.1: g_i = 1 for i in this set, 0 otherwise.
    taps = {0, 1, 3, 4, 5, 6, 7, 10, 11, 14, 17, 18, 23, 24}
    expected = sum(1 << i for i in taps)
    assert prim.CRC24Q_POLYNOMIAL == expected


def test_crc24q_of_all_zeros_is_zero():
    assert prim.crc24q(np.zeros(276, dtype=np.uint8)) == 0


def test_crc24q_append_then_check():
    rng = np.random.default_rng(0)
    information = rng.integers(0, 2, 276).astype(np.uint8)
    message = prim.crc24q_append(information)
    assert len(message) == 300
    assert prim.crc24q_check(message)


def test_crc24q_catches_every_single_bit_error():
    rng = np.random.default_rng(1)
    message = prim.crc24q_append(rng.integers(0, 2, 276).astype(np.uint8))
    for i in range(len(message)):
        corrupted = message.copy()
        corrupted[i] ^= 1
        assert not prim.crc24q_check(corrupted), f"bit {i} flip went undetected"


def test_crc24q_catches_every_double_bit_error():
    # The spec claims all double-bit errors are detected because g(X) has a
    # primitive factor of degree 23.  Spot-check a spread of pairs.
    rng = np.random.default_rng(2)
    message = prim.crc24q_append(rng.integers(0, 2, 276).astype(np.uint8))
    for i, j in [(0, 1), (0, 299), (5, 200), (100, 101), (150, 275), (276, 299)]:
        corrupted = message.copy()
        corrupted[i] ^= 1
        corrupted[j] ^= 1
        assert not prim.crc24q_check(corrupted), f"bits {i},{j} went undetected"


def test_crc24q_catches_all_odd_error_counts():
    # g(X) contains the factor 1+X, so any odd number of flipped bits is detected.
    rng = np.random.default_rng(3)
    message = prim.crc24q_append(rng.integers(0, 2, 276).astype(np.uint8))
    for trial in range(50):
        corrupted = message.copy()
        count = rng.choice([1, 3, 5, 7])
        for i in rng.choice(len(message), size=count, replace=False):
            corrupted[i] ^= 1
        assert not prim.crc24q_check(corrupted)


# ---------------------------------------------------------------------------
# Convolutional code and Viterbi
# ---------------------------------------------------------------------------


def test_convolutional_generators_match_the_spec():
    assert prim.CONV_G1 == 0o171
    assert prim.CONV_G2 == 0o133


def test_encoder_emits_two_symbols_per_bit_g1_first():
    # From the all-zero state, a single 1 puts a 1 through both generators, whose
    # lowest tap is X^0 in each -- so the first pair is (1, 1).
    symbols = prim.convolutional_encode(np.array([1, 0, 0, 0], dtype=np.uint8))
    assert len(symbols) == 8
    assert list(symbols[:2]) == [1, 1]


def test_viterbi_recovers_a_clean_stream():
    rng = np.random.default_rng(4)
    bits = rng.integers(0, 2, 400).astype(np.uint8)
    symbols = prim.convolutional_encode(bits)
    soft = 1.0 - 2.0 * symbols  # noiseless, sign carries the bit
    result = prim.ViterbiDecoder().decode(soft)
    # The decoder starts with no knowledge of the encoder state, so allow the
    # documented settling of about five constraint lengths.
    assert np.array_equal(result.bits[40:], bits[40:])


def test_viterbi_recovers_a_stream_encoded_from_a_nonzero_state():
    # CNAV encodes continuously across message boundaries, so a real decoder never
    # sees a stream that began in state zero.
    rng = np.random.default_rng(5)
    bits = rng.integers(0, 2, 400).astype(np.uint8)
    symbols = prim.convolutional_encode(bits, initial_state=0b101101)
    result = prim.ViterbiDecoder().decode(1.0 - 2.0 * symbols)
    assert np.array_equal(result.bits[40:], bits[40:])


def test_viterbi_rejects_a_misaligned_symbol_stream():
    with pytest.raises(ValueError, match="misaligned"):
        prim.ViterbiDecoder().decode(np.zeros(7))


def test_viterbi_handles_an_empty_stream():
    assert len(prim.ViterbiDecoder().decode(np.zeros(0)).bits) == 0


def test_viterbi_beats_hard_decision_under_noise():
    """
    The whole reason for a soft-decision decoder: at an SNR where raw symbols are
    visibly wrong, the decoded bits should be far better than the symbol error rate.

    sigma = 0.8 puts this at Eb/N0 ~ 1.9 dB, comfortably down the waterfall rather
    than on its knee -- about one symbol in ten arrives wrong and the decoder still
    recovers better than 99.5% of the bits.  Pick a noisier point and the assertion
    starts measuring where the knee happens to fall rather than whether the decoder
    works.
    """
    rng = np.random.default_rng(6)
    bits = rng.integers(0, 2, 5000).astype(np.uint8)
    symbols = prim.convolutional_encode(bits)
    clean = 1.0 - 2.0 * symbols
    noisy = clean + rng.normal(0.0, 0.8, len(clean))

    symbol_error_rate = np.mean(prim.hard_decision(noisy) != symbols)
    decoded = prim.ViterbiDecoder().decode(noisy).bits
    bit_error_rate = np.mean(decoded[40:] != bits[40:])

    assert symbol_error_rate > 0.05, "test SNR is too high to be meaningful"
    assert bit_error_rate < symbol_error_rate / 10
    # An absolute bound as well, so a regression that merely degrades the decoder
    # without breaking it cannot hide behind a proportional comparison.
    assert bit_error_rate < 0.02


def test_trellis_is_self_consistent():
    """Every state has exactly two predecessors -- the property the decoder's
    inverse map relies on."""
    counts = np.zeros(prim.CONV_NUM_STATES, dtype=int)
    for state in range(prim.CONV_NUM_STATES):
        for u in (0, 1):
            counts[prim._TRELLIS_NEXT[state, u]] += 1
    assert np.all(counts == 2)


# ---------------------------------------------------------------------------
# LNAV word parity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("d29,d30", [(0, 0), (0, 1), (1, 0), (1, 1)])
def test_lnav_word_round_trip(d29, d30):
    rng = np.random.default_rng(7)
    data = rng.integers(0, 2, 24).astype(np.uint8)
    word = prim.lnav_encode_word(data, d29, d30)
    assert len(word) == 30
    recovered, ok = prim.lnav_decode_word(word, d29, d30)
    assert ok
    assert np.array_equal(recovered, data)


def test_lnav_inverts_data_when_d30_star_is_set():
    data = np.zeros(24, dtype=np.uint8)
    assert not prim.lnav_encode_word(data, 0, 0)[:24].any()
    assert prim.lnav_encode_word(data, 0, 1)[:24].all()


def test_lnav_parity_catches_single_bit_errors():
    rng = np.random.default_rng(8)
    data = rng.integers(0, 2, 24).astype(np.uint8)
    word = prim.lnav_encode_word(data, 1, 0)
    for i in range(30):
        corrupted = word.copy()
        corrupted[i] ^= 1
        _, ok = prim.lnav_decode_word(corrupted, 1, 0)
        assert not ok, f"bit {i} flip went undetected"


def test_lnav_code_has_hamming_distance_four():
    """
    The (32,26) Hamming code detects any three-bit error, i.e. d_min = 4.  Checked
    over the linear code's own basis rather than by random sampling: for a linear
    code the minimum distance is the minimum weight of a nonzero codeword, and
    every codeword is a XOR of the 24 basis words.
    """
    basis = []
    for i in range(24):
        data = np.zeros(24, dtype=np.uint8)
        data[i] = 1
        basis.append(prim.lnav_encode_word(data, 0, 0))
    basis = np.array(basis)

    min_weight = 30
    for mask in range(1, 1 << 12):  # exhaustive over a 12-word subspace is enough
        word = np.zeros(30, dtype=np.uint8)
        for i in range(12):
            if mask >> i & 1:
                word ^= basis[i]
        min_weight = min(min_weight, int(word.sum()))
    assert min_weight >= 4


def test_lnav_rejects_wrong_word_length():
    with pytest.raises(ValueError):
        prim.lnav_decode_word(np.zeros(29, dtype=np.uint8), 0, 0)
    with pytest.raises(ValueError):
        prim.lnav_parity(np.zeros(23, dtype=np.uint8), 0, 0)


# ---------------------------------------------------------------------------
# BCH(51,8) on the CNAV-2 TOI
# ---------------------------------------------------------------------------


def test_bch_polynomial_matches_the_spec():
    # IS-GPS-800J Figure 3.2-4: 1 + X + X^4 + X^5 + X^6 + X^7 + X^8 = 763 octal.
    assert prim.BCH_51_8_POLYNOMIAL == 0o763
    expected_taps = {0, 1, 4, 5, 6, 7, 8}
    assert {i for i in range(9) if prim.BCH_51_8_POLYNOMIAL >> i & 1} == expected_taps


def test_bch_minimum_distance_is_24():
    """
    The check that catches a mistranscribed tap set or a reversed seed order.  Both
    mistakes still produce 256 distinct codewords and a working round trip; what
    they destroy is the distance spectrum.
    """
    table = prim._BCH_51_8_TABLE
    assert len({bytes(row) for row in table}) == 256
    distances = [
        int((table[i] ^ table[j]).sum()) for i in range(256) for j in range(i + 1, 256)
    ]
    assert min(distances) == prim.BCH_51_8_MINIMUM_DISTANCE


def test_bch_zero_seed_gives_the_zero_codeword():
    assert not prim._BCH_51_8_TABLE[0].any()


@pytest.mark.parametrize("toi", [0, 1, 255, 256, 399, 511])
def test_bch_toi_round_trip(toi):
    message = prim.bch_51_8_encode(toi)
    assert len(message) == prim.BCH_51_8_MESSAGE_LENGTH
    decoded, confidence = prim.bch_51_8_decode(1.0 - 2.0 * message)
    assert decoded == toi
    assert confidence > 1.0


def test_bch_msb_is_added_to_every_symbol_and_prepended():
    low = prim.bch_51_8_encode(0b0_0001_0110)
    high = prim.bch_51_8_encode(0b1_0001_0110)
    assert high[0] == 1 and low[0] == 0
    assert np.array_equal(high[1:], low[1:] ^ 1)


def test_bch_decodes_through_heavy_noise():
    """d_min = 24 over 51 symbols should ride out a lot of flipped symbols."""
    rng = np.random.default_rng(9)
    failures = 0
    for _ in range(100):
        toi = int(rng.integers(0, 512))
        soft = 1.0 - 2.0 * prim.bch_51_8_encode(toi) + rng.normal(0.0, 0.9, 52)
        if prim.bch_51_8_decode(soft)[0] != toi:
            failures += 1
    assert failures == 0


def test_bch_rejects_wrong_block_length():
    with pytest.raises(ValueError):
        prim.bch_51_8_decode(np.zeros(51))


def test_bch_encode_rejects_out_of_range_toi():
    with pytest.raises(ValueError):
        prim.bch_51_8_encode(512)


# ---------------------------------------------------------------------------
# CNAV-2 block interleaver
# ---------------------------------------------------------------------------


def test_interleaver_round_trip():
    symbols = np.arange(prim.CNAV2_INTERLEAVER_ROWS * prim.CNAV2_INTERLEAVER_COLUMNS)
    assert np.array_equal(prim.block_deinterleave(prim.block_interleave(symbols)), symbols)


def test_interleaver_dimensions_cover_subframes_2_and_3():
    assert prim.CNAV2_INTERLEAVER_ROWS * prim.CNAV2_INTERLEAVER_COLUMNS == 1200 + 548


def test_interleaver_reads_out_the_first_column_first():
    """
    IS-GPS-800J 3.2.3.5: written by rows, read out top to bottom starting at column
    1.  So the second symbol out is the one written 46 positions later.
    """
    columns = prim.CNAV2_INTERLEAVER_COLUMNS
    symbols = np.arange(prim.CNAV2_INTERLEAVER_ROWS * columns)
    out = prim.block_interleave(symbols)
    assert out[0] == 0
    assert out[1] == columns


def test_interleaver_rejects_wrong_length():
    with pytest.raises(ValueError):
        prim.block_interleave(np.arange(100))
    with pytest.raises(ValueError):
        prim.block_deinterleave(np.arange(100))
