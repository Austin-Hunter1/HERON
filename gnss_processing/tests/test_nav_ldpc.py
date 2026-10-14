"""
The B-CNAV2 64-ary LDPC(96,48) code: field arithmetic, the H matrix, the encoder
and the soft decoder.

The ICD gives two independent checks, and both are used: the two GF(64) maps and a
worked encoding example (48 symbols in, 96 symbols out).  Everything else is
checked against the code's own algebra (a codeword has a zero syndrome) and by
repairing damaged frames.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from utils.nav import ldpc

ICD_TABLES_PATH = Path(__file__).parent.parent / "docs" / "icd" / "bds_b2a_ldpc_tables.json"


@pytest.fixture(scope="module")
def icd() -> dict:
    with ICD_TABLES_PATH.open() as handle:
        return json.load(handle)


def _example(icd) -> tuple[np.ndarray, np.ndarray]:
    message = np.array([int(s, 2) for s in icd["example_input_symbols"]])
    codeword = np.array([int(s, 2) for s in icd["example_output_symbols"]])
    return message, codeword


def _random_message(seed: int) -> np.ndarray:
    return np.random.default_rng(seed).integers(0, ldpc.GF_ORDER, ldpc.NUM_MESSAGE_SYMBOLS)


def _soft(codeword: np.ndarray) -> np.ndarray:
    """Noiseless soft bits for a codeword: 0 is +1 and 1 is -1."""
    return 1.0 - 2.0 * ldpc.bits_from_symbols(codeword)


# ---------------------------------------------------------------------------
# GF(64)
# ---------------------------------------------------------------------------


def test_field_tables_equal_the_icd_maps(icd):
    exp, log = ldpc.gf_tables()
    assert exp.tolist() == icd["gf_power_to_vector"]
    assert [-1] + [int(log[v]) for v in range(1, 64)] == icd["gf_vector_to_power"]


def test_multiplication_obeys_the_field_laws():
    rng = np.random.default_rng(1)
    a, b, c = (rng.integers(0, 64, 200) for _ in range(3))
    assert np.array_equal(ldpc.gf_multiply(a, b), ldpc.gf_multiply(b, a))
    assert np.array_equal(
        ldpc.gf_multiply(a, b ^ c), ldpc.gf_multiply(a, b) ^ ldpc.gf_multiply(a, c)
    )
    assert np.array_equal(
        ldpc.gf_multiply(ldpc.gf_multiply(a, b), c), ldpc.gf_multiply(a, ldpc.gf_multiply(b, c))
    )
    assert np.all(ldpc.gf_multiply(a, 0) == 0)
    assert np.array_equal(ldpc.gf_multiply(a, 1), a)


def test_every_nonzero_element_has_an_inverse():
    for value in range(1, 64):
        assert ldpc.gf_multiply(value, ldpc.gf_inverse(value)) == 1
    with pytest.raises(ValueError):
        ldpc.gf_inverse(0)


def test_x_to_the_6_is_x_plus_1():
    """The field is built on 1 + x + x^6, so alpha^6 = alpha + 1 (binary 000011)."""
    exp, _ = ldpc.gf_tables()
    assert exp[6] == 0b000011


def test_bit_symbol_conversion_is_msb_first():
    assert ldpc.symbols_from_bits(np.array([0, 0, 1, 0, 1, 0, 1, 1, 0, 0, 1, 0])).tolist() == [10, 50]
    symbols = np.arange(64)
    assert np.array_equal(ldpc.symbols_from_bits(ldpc.bits_from_symbols(symbols)), symbols)
    with pytest.raises(ValueError):
        ldpc.symbols_from_bits(np.zeros(7))


# ---------------------------------------------------------------------------
# H and the encoder
# ---------------------------------------------------------------------------


def test_h_is_sparse_with_four_entries_per_row():
    h = ldpc.parity_check_matrix()
    assert h.shape == (48, 96)
    assert np.all((h != 0).sum(axis=1) == 4)
    assert np.all(h[:, :] < 64)


def test_module_h_tables_equal_the_icd_fixture(icd):
    assert ldpc._H_INDEX_PRINTED == tuple(tuple(row) for row in icd["h_index"])
    assert ldpc._H_ELEMENT_PRINTED == tuple(tuple(row) for row in icd["h_element"])


def test_check_part_of_h_is_invertible():
    """The encoder needs H2^-1; this fails if a table entry is wrong."""
    h2 = ldpc.parity_check_matrix()[:, 48:]
    inverse = ldpc._gf_matrix_inverse(h2)
    product = np.bitwise_xor.reduce(ldpc.gf_multiply(h2[:, :, None], inverse[None, :, :]), axis=1)
    assert np.array_equal(product, np.eye(48, dtype=int))


def test_encoder_reproduces_the_icd_example(icd):
    message, expected = _example(icd)
    codeword = ldpc.encode(message)
    assert codeword.tolist() == expected.tolist()


def test_code_is_systematic_and_every_codeword_has_zero_syndrome():
    for seed in range(5):
        message = _random_message(seed)
        codeword = ldpc.encode(message)
        assert np.array_equal(codeword[:48], message)
        assert not ldpc.syndrome(codeword).any()


def test_a_single_symbol_error_breaks_the_syndrome():
    codeword = ldpc.encode(_random_message(2))
    for position in (0, 47, 48, 95):
        damaged = codeword.copy()
        damaged[position] ^= 0b000101
        assert ldpc.syndrome(damaged).any()


def test_encoder_rejects_bad_input():
    with pytest.raises(ValueError):
        ldpc.encode(np.zeros(47, dtype=int))
    with pytest.raises(ValueError):
        ldpc.encode(np.full(48, 64))


def test_code_is_linear():
    a, b = _random_message(3), _random_message(4)
    assert np.array_equal(ldpc.encode(a) ^ ldpc.encode(b), ldpc.encode(a ^ b))


# ---------------------------------------------------------------------------
# Decoder
# ---------------------------------------------------------------------------


def test_decoder_returns_a_clean_codeword_at_once(icd):
    _, codeword = _example(icd)
    result = ldpc.decode(_soft(codeword))
    assert result.converged
    assert result.iterations == 0
    assert result.symbols_changed == 0
    assert np.array_equal(result.symbols, codeword)
    assert np.array_equal(result.message_bits, ldpc.bits_from_symbols(codeword[:48]))


def test_decoder_gives_the_same_answer_for_any_input_scale(icd):
    """Only ratios of reliabilities matter, as for any min-sum decoder."""
    _, codeword = _example(icd)
    rng = np.random.default_rng(5)
    noisy = _soft(codeword) + rng.normal(0, 0.7, 576)
    first = ldpc.decode(noisy)
    second = ldpc.decode(noisy * 1000.0)
    assert np.array_equal(first.symbols, second.symbols)


@pytest.mark.parametrize("sigma", [0.5, 0.7])
def test_decoder_repairs_noisy_frames(sigma):
    """
    At sigma 0.7 about 8% of the individual bits are wrong, so about 40% of the
    6-bit symbols are, and a hard decision of the message is hopeless.  The soft
    decoder recovers the codeword.
    """
    rng = np.random.default_rng(int(sigma * 10))
    for seed in range(6):
        codeword = ldpc.encode(_random_message(seed))
        noisy = _soft(codeword) + rng.normal(0, sigma, 576)
        hard_symbols = ldpc.symbols_from_bits((noisy < 0).astype(int))
        assert not np.array_equal(hard_symbols, codeword)  # a plain read fails
        result = ldpc.decode(noisy)
        assert result.converged
        assert np.array_equal(result.symbols, codeword)
        assert result.symbols_changed > 0


def test_decoder_repairs_wrong_symbols_marked_unreliable():
    """Eight whole symbols flipped, with small magnitudes the decoder can distrust."""
    rng = np.random.default_rng(6)
    codeword = ldpc.encode(_random_message(6))
    soft = _soft(codeword)
    for symbol in rng.choice(96, size=8, replace=False):
        span = slice(6 * symbol, 6 * symbol + 6)
        soft[span] = -0.2 * soft[span]  # wrong sign, low confidence
    result = ldpc.decode(soft)
    assert result.converged
    assert np.array_equal(result.symbols, codeword)


def test_decoder_gives_up_cleanly_on_noise():
    """Pure noise: no codeword, `converged` is False, and the shape is still right."""
    rng = np.random.default_rng(7)
    result = ldpc.decode(rng.normal(0, 1, 576), max_iterations=5)
    assert not result.converged
    assert result.iterations == 5
    assert result.symbols.shape == (96,)


def test_decoder_rejects_a_wrong_length():
    with pytest.raises(ValueError):
        ldpc.decode(np.zeros(575))
