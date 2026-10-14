"""
The 64-ary LDPC codes of BeiDou: LDPC(96,48) of B-CNAV2 (BDS-SIS-ICD-B2a-1.0, 6.2.2 and
Annex) and LDPC(200,100) and LDPC(88,44) of B-CNAV1 (BDS-SIS-ICD-B1C-1.0, 6.2.2).

All three work over the same field GF(2^6), so one class, `LdpcCode`, holds the logic
and each code is one instance (`BCNAV2_CODE`, `BCNAV1_SUBFRAME2_CODE`,
`BCNAV1_SUBFRAME3_CODE`).  The module-level functions (`encode`, `decode`, `syndrome`,
`parity_check_matrix`) and constants (`NUM_VARIABLES` and so on) are the B-CNAV2 code,
kept so that its users need no change.  The text below describes LDPC(96,48); the
others differ only in size and tables.

One B-CNAV2 frame carries 288 message bits.  They are grouped into 48 symbols of
6 bits (MSB first), each symbol an element of the Galois field GF(2^6) built on
``p(x) = 1 + x + x^6``.  The encoder appends 48 check symbols, so one codeword is 96
symbols = 576 bits.  The code is **systematic**: the first 48 symbols of the
codeword are the message itself.

What is here, and why each part exists:

* GF(64) arithmetic (`gf_multiply`, `gf_inverse`) -- built from the polynomial and
  compared with the two maps the ICD prints, so a wrong polynomial cannot hide.
* The sparse parity-check matrix H(48,96) (`parity_check_matrix`), made from the
  index and element tables of the ICD.
* `encode` -- ``c = [m, p]`` with ``p = m (H2^-1 H1)^T``, as the Annex says.  Nothing
  in a real recording lets us check a decoder against a known message, so the
  encoder is what the tests are made from (as in `primitives.py`).
* `decode` -- a soft-input min-sum decoder.  Hard-decision decoding throws away the
  reliability that the tracking loop already measured; the soft decoder uses it,
  so it repairs frames that a plain CRC check would reject.

Bit and sign conventions are those of `utils.nav.primitives`: bits are 0/1 MSB first;
a soft value's *sign* carries the bit, positive for 0 and negative for 1.

About the ICD tables.  The ICD prints each of the two tables as 12 rows of 16 numbers.
One group of four numbers is one row of H.  The text says "read from top to bottom in
the same column", but not which group is which row of H.  This module reads group g of
printed row r as row ``12 * g + r``.  Any other order only permutes the rows of H, which
does not change the code; the tests check that the ICD encoding example is reproduced.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from . import ldpc_b1c_tables

GF_BITS = 6
GF_ORDER = 1 << GF_BITS  # 64
# p(x) = 1 + x + x^6 written with x^i in bit i.
GF_PRIMITIVE_POLYNOMIAL = 0b1000011

CHECK_DEGREE = 4  # non-zero elements per row of H, in all three codes

# The printed tables: 12 rows of four groups of four numbers (see the module
# docstring).  They are data, not logic; a test compares them with the ICD copy in
# ``docs/icd/bds_b2a_ldpc_tables.json``.
_H_INDEX_PRINTED = (
    (19, 46, 49, 76, 5, 29, 53, 71, 17, 30, 64, 72, 22, 36, 59, 82),
    (22, 41, 68, 94, 20, 44, 54, 75, 9, 41, 61, 86, 6, 47, 60, 89),
    (8, 40, 60, 87, 15, 26, 66, 81, 19, 24, 67, 95, 2, 26, 50, 72),
    (5, 38, 70, 89, 16, 34, 64, 92, 21, 45, 55, 74, 0, 24, 48, 78),
    (23, 37, 58, 83, 15, 43, 56, 91, 18, 47, 48, 77, 14, 42, 57, 90),
    (6, 30, 54, 76, 14, 27, 67, 80, 17, 35, 65, 93, 7, 46, 61, 88),
    (1, 25, 49, 79, 12, 45, 69, 79, 18, 25, 66, 94, 23, 40, 69, 95),
    (8, 36, 51, 84, 3, 38, 56, 86, 0, 29, 62, 85, 2, 39, 57, 87),
    (11, 33, 59, 81, 20, 43, 74, 93, 13, 32, 63, 91, 11, 35, 52, 83),
    (16, 31, 65, 73, 4, 28, 52, 70, 1, 28, 63, 84, 12, 33, 62, 90),
    (21, 42, 75, 92, 7, 31, 55, 77, 9, 37, 50, 85, 10, 34, 53, 82),
    (4, 39, 71, 88, 13, 44, 68, 78, 3, 27, 51, 73, 10, 32, 58, 80),
)

_H_ELEMENT_PRINTED = (
    (1, 45, 15, 6, 1, 44, 53, 24, 45, 15, 6, 1, 30, 24, 1, 44),
    (18, 15, 32, 61, 3, 55, 9, 34, 35, 31, 50, 44, 45, 15, 6, 1),
    (24, 1, 44, 53, 30, 24, 1, 44, 32, 42, 47, 37, 6, 1, 45, 15),
    (44, 53, 24, 1, 39, 36, 34, 33, 44, 53, 24, 1, 44, 53, 24, 1),
    (45, 15, 6, 1, 6, 1, 45, 15, 24, 1, 44, 53, 9, 41, 57, 58),
    (32, 61, 18, 40, 1, 45, 15, 6, 22, 14, 2, 50, 24, 1, 44, 30),
    (30, 24, 1, 44, 15, 46, 45, 44, 45, 15, 6, 1, 1, 44, 30, 24),
    (24, 1, 44, 53, 15, 6, 1, 45, 53, 24, 1, 44, 7, 38, 23, 54),
    (1, 45, 15, 6, 44, 53, 24, 1, 57, 25, 9, 41, 35, 13, 51, 60),
    (33, 45, 36, 34, 6, 1, 45, 15, 6, 1, 45, 15, 6, 1, 45, 15),
    (44, 35, 31, 50, 26, 27, 37, 5, 24, 1, 44, 30, 33, 42, 14, 5),
    (24, 1, 44, 30, 24, 1, 44, 30, 1, 44, 53, 24, 1, 44, 30, 24),
)

_PRINTED_GROUPS = 4  # groups of four numbers in each printed row


# ---------------------------------------------------------------------------
# GF(2^6)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def gf_tables() -> tuple[np.ndarray, np.ndarray]:
    """
    ``(exp, log)`` tables of GF(64) for the primitive element alpha = x.

    ``exp[i]`` is alpha^i as a 6-bit vector (i = 0..62); ``log[v]`` is the power of
    the non-zero vector ``v``.  ``log[0]`` is set to -1 and never used.
    """
    exp = np.zeros(GF_ORDER - 1, dtype=np.int64)
    log = np.full(GF_ORDER, -1, dtype=np.int64)
    value = 1
    for power in range(GF_ORDER - 1):
        exp[power] = value
        log[value] = power
        value <<= 1
        if value & GF_ORDER:
            value ^= GF_PRIMITIVE_POLYNOMIAL
    if value != 1:
        raise RuntimeError("the polynomial is not primitive: alpha^63 is not 1")
    return exp, log


@lru_cache(maxsize=1)
def _multiplication_table() -> np.ndarray:
    """64 x 64 table of products, so vector code can multiply by indexing."""
    exp, log = gf_tables()
    table = np.zeros((GF_ORDER, GF_ORDER), dtype=np.int64)
    a, b = np.meshgrid(np.arange(1, GF_ORDER), np.arange(1, GF_ORDER), indexing="ij")
    table[1:, 1:] = exp[(log[a] + log[b]) % (GF_ORDER - 1)]
    return table


def gf_multiply(a, b):
    """Product in GF(64).  Accepts ints or integer arrays (broadcast)."""
    return _multiplication_table()[np.asarray(a), np.asarray(b)]


def gf_inverse(a: int) -> int:
    """Multiplicative inverse of a non-zero element."""
    if not 0 < a < GF_ORDER:
        raise ValueError(f"{a} has no inverse in GF({GF_ORDER})")
    exp, log = gf_tables()
    return int(exp[(-log[a]) % (GF_ORDER - 1)])


# ---------------------------------------------------------------------------
# Bits <-> symbols
# ---------------------------------------------------------------------------


def symbols_from_bits(bits: np.ndarray) -> np.ndarray:
    """Group bits into 6-bit symbols, MSB first.  The length must be a multiple of 6."""
    bits = np.asarray(bits, dtype=np.int64)
    if len(bits) % GF_BITS:
        raise ValueError(f"{len(bits)} bits is not a whole number of {GF_BITS}-bit symbols")
    weights = 1 << np.arange(GF_BITS - 1, -1, -1)
    return (bits.reshape(-1, GF_BITS) * weights).sum(axis=1)


def bits_from_symbols(symbols: np.ndarray) -> np.ndarray:
    """Inverse of `symbols_from_bits`: each symbol as 6 bits, MSB first, uint8."""
    symbols = np.asarray(symbols, dtype=np.int64)
    shifts = np.arange(GF_BITS - 1, -1, -1)
    return ((symbols[:, None] >> shifts) & 1).astype(np.uint8).reshape(-1)


# ---------------------------------------------------------------------------
# One code
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LdpcResult:
    """What one decoding attempt produced."""

    symbols: np.ndarray
    """The codeword estimate (a valid codeword iff `converged`)."""

    converged: bool
    """True when every parity check is satisfied."""

    iterations: int
    """Min-sum iterations run (0 when the received word was already a codeword)."""

    symbols_changed: int
    """How many symbols differ from the plain hard decision of the input."""

    num_message_symbols: int = 48
    """How many leading symbols are the message (the code's k)."""

    @property
    def message_symbols(self) -> np.ndarray:
        return self.symbols[: self.num_message_symbols]

    @property
    def message_bits(self) -> np.ndarray:
        """The message bits, MSB first."""
        return bits_from_symbols(self.message_symbols)


@dataclass(frozen=True, eq=False)
class LdpcCode:
    """
    One systematic 64-ary LDPC code, defined by the ICD's printed tables.

    `printed_index` and `printed_element` are rows of ``4 * CHECK_DEGREE`` numbers:
    four groups, one group per row of H.  Group g of printed row r is row
    ``len(printed_index) * g + r`` of H.  The check-node maths needs the same number
    of non-zero entries in every row, which holds for all three codes (degree 4).

    The class compares by identity (`eq=False`) so that its cached methods can use
    the instance as a cache key.
    """

    name: str
    printed_index: tuple
    printed_element: tuple

    @property
    def num_checks(self) -> int:
        """n - k: rows of H, and the number of check symbols."""
        return len(self.printed_index) * _PRINTED_GROUPS

    @property
    def num_message_symbols(self) -> int:
        """k: the code is systematic, so the message is the first k symbols."""
        return self.num_checks

    @property
    def num_variables(self) -> int:
        """n: symbols in one codeword."""
        return 2 * self.num_checks

    @lru_cache(maxsize=None)
    def _edges(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        The non-zero entries of H as three flat arrays, ordered check by check:
        ``(variable, element, check)`` for each entry.
        """
        printed_rows = len(self.printed_index)
        variable = np.zeros(self.num_checks * CHECK_DEGREE, dtype=np.int64)
        element = np.zeros(self.num_checks * CHECK_DEGREE, dtype=np.int64)
        for printed_row in range(printed_rows):
            if (
                len(self.printed_index[printed_row]) != _PRINTED_GROUPS * CHECK_DEGREE
                or len(self.printed_element[printed_row]) != _PRINTED_GROUPS * CHECK_DEGREE
            ):
                raise RuntimeError(f"{self.name}: printed row {printed_row} has the wrong length")
            for group in range(_PRINTED_GROUPS):
                check = printed_rows * group + printed_row
                for slot in range(CHECK_DEGREE):
                    edge = check * CHECK_DEGREE + slot
                    column = CHECK_DEGREE * group + slot
                    variable[edge] = self.printed_index[printed_row][column]
                    element[edge] = self.printed_element[printed_row][column]
        check = np.repeat(np.arange(self.num_checks), CHECK_DEGREE)
        if variable.max() >= self.num_variables or element.min() < 1 or element.max() >= GF_ORDER:
            raise RuntimeError(f"{self.name}: the H tables hold an index or element out of range")
        return variable, element, check

    @lru_cache(maxsize=None)
    def parity_check_matrix(self) -> np.ndarray:
        """H as a dense array of GF(64) elements (0 where H is zero)."""
        variable, element, check = self._edges()
        matrix = np.zeros((self.num_checks, self.num_variables), dtype=np.int64)
        matrix[check, variable] = element
        return matrix

    def syndrome(self, codeword: np.ndarray) -> np.ndarray:
        """``H c`` over GF(64): all zero for a codeword."""
        variable, element, check = self._edges()
        products = gf_multiply(element, np.asarray(codeword, dtype=np.int64)[variable])
        out = np.zeros(self.num_checks, dtype=np.int64)
        np.bitwise_xor.at(out, check, products)
        return out

    @lru_cache(maxsize=None)
    def _check_generator(self) -> np.ndarray:
        """``H2^-1 H1``: maps the message symbols to the check symbols."""
        k = self.num_message_symbols
        h = self.parity_check_matrix()
        h1, h2 = h[:, :k], h[:, k:]
        inverse = _gf_matrix_inverse(h2)
        products = gf_multiply(inverse[:, :, None], h1[None, :, :])
        return np.bitwise_xor.reduce(products, axis=1)

    def encode(self, message_symbols: np.ndarray) -> np.ndarray:
        """
        The codeword for k message symbols: the message, then the check symbols.

        Each symbol is an int from 0 to 63.
        """
        k = self.num_message_symbols
        message = np.asarray(message_symbols, dtype=np.int64)
        if message.shape != (k,):
            raise ValueError(f"{self.name}: expected {k} message symbols, got {message.shape}")
        if message.min() < 0 or message.max() >= GF_ORDER:
            raise ValueError(f"message symbols must be 0..{GF_ORDER - 1}")
        products = gf_multiply(self._check_generator(), message[None, :])
        checks = np.bitwise_xor.reduce(products, axis=1)
        return np.concatenate([message, checks])

    @lru_cache(maxsize=None)
    def _decoder_tables(self) -> dict[str, np.ndarray]:
        """Index tables that turn the check-node maths into array gathers."""
        _, element, _ = self._edges()
        values = np.arange(GF_ORDER)
        multiply = _multiplication_table()
        # x ^ z for all pairs: row z lists x ^ z for x = 0..63.
        xor_table = values[None, :] ^ values[:, None]
        # Edge e carries element h.  The variable-side symbol x is seen by the check as
        # z = h * x, so the check works on `scaled[z] = message[h^-1 * z]` and gives
        # its answer back as `answer[x] = result[h * x]`.
        inverse_row = np.array([gf_inverse(int(h)) for h in element])
        return {
            "xor": xor_table,
            "to_check": multiply[inverse_row[:, None], values[None, :]],
            "from_check": multiply[element[:, None], values[None, :]],
        }

    def _channel_costs(self, soft_bits: np.ndarray) -> np.ndarray:
        """
        Per-symbol cost of each of the 64 values from the received soft bits.

        The cost of a candidate symbol is the total reliability of the bits in which
        it disagrees with the hard decision -- the ICD's LLR with the common factor
        ``2/sigma^2`` dropped, which min-sum does not need.
        """
        soft = np.asarray(soft_bits, dtype=np.float64).reshape(self.num_variables, GF_BITS)
        hard = (soft < 0).astype(np.uint8)
        mismatch = _bit_matrix()[None, :, :] != hard[:, None, :]  # (n, 64, 6)
        return (mismatch * np.abs(soft)[:, None, :]).sum(axis=2)

    def decode(self, soft_bits: np.ndarray, *, max_iterations: int = 50) -> LdpcResult:
        """
        Decode one codeword's soft bits (``6 * n``, one value per bit, positive for 0).

        Magnitudes are the reliabilities; only their ratios matter.

        The algorithm is min-sum over GF(64).  Each check has four variables, so a
        check node needs only a few 64 x 64 min-plus products, and the whole iteration
        is done for all checks at once with array gathers.  The ICD's "extended" form
        keeps only the n_m best values of each message to save work; at this size the
        full vectors are cheap, so nothing is truncated.

        Returns the best estimate even when it did not converge (`converged` False);
        the caller's CRC is the final judge either way.
        """
        soft = np.asarray(soft_bits, dtype=np.float64)
        if soft.shape != (self.num_variables * GF_BITS,):
            raise ValueError(
                f"{self.name}: expected {self.num_variables * GF_BITS} soft bits, got {soft.shape}"
            )

        variable, _, _ = self._edges()
        tables = self._decoder_tables()
        channel = self._channel_costs(soft)
        hard_decision = channel.argmin(axis=1)  # cost 0 for the hard-decision symbol
        num_edges = len(variable)
        num_checks = self.num_checks
        k = self.num_message_symbols

        def result(symbols, converged, iterations):
            return LdpcResult(
                symbols=symbols, converged=converged, iterations=iterations,
                symbols_changed=int(np.count_nonzero(symbols != hard_decision)),
                num_message_symbols=k,
            )

        check_to_variable = np.zeros((num_edges, GF_ORDER))
        best = hard_decision
        for iteration in range(max_iterations + 1):
            total = channel.copy()
            np.add.at(total, variable, check_to_variable)
            best = total.argmin(axis=1)
            if not self.syndrome(best).any():
                return result(best, True, iteration)
            if iteration == max_iterations:
                break

            # Variable to check: everything the variable knows except what this check
            # told it.  Costs are re-based to a minimum of zero to stay bounded.
            variable_to_check = total[variable] - check_to_variable
            variable_to_check -= variable_to_check.min(axis=1, keepdims=True)

            # Check node.  `w[c, k]` is edge k of check c as seen by the check.
            w = np.take_along_axis(variable_to_check, tables["to_check"], axis=1)
            w = w.reshape(num_checks, CHECK_DEGREE, GF_ORDER)
            xor = tables["xor"]
            # Forward and backward sums; edge j's answer is the sum of all the others.
            forward = [w[:, 0]]
            for j in range(1, CHECK_DEGREE - 1):
                forward.append(_xor_convolve(forward[-1], w[:, j], xor))
            backward = [w[:, CHECK_DEGREE - 1]]
            for j in range(CHECK_DEGREE - 2, 0, -1):
                backward.append(_xor_convolve(backward[-1], w[:, j], xor))
            backward.reverse()  # backward[j - 1] = sum of edges j .. last
            others = np.empty_like(w)
            others[:, 0] = backward[0]
            for j in range(1, CHECK_DEGREE - 1):
                others[:, j] = _xor_convolve(forward[j - 1], backward[j], xor)
            others[:, CHECK_DEGREE - 1] = forward[-1]

            others = others.reshape(num_edges, GF_ORDER)
            check_to_variable = np.take_along_axis(others, tables["from_check"], axis=1)
            check_to_variable -= check_to_variable.min(axis=1, keepdims=True)

        return result(best, False, max_iterations)


def _gf_matrix_inverse(matrix: np.ndarray) -> np.ndarray:
    """Gauss-Jordan inverse of a square GF(64) matrix."""
    n = len(matrix)
    work = np.concatenate([matrix.astype(np.int64), np.eye(n, dtype=np.int64)], axis=1)
    for column in range(n):
        pivot = next((r for r in range(column, n) if work[r, column]), None)
        if pivot is None:
            raise ValueError("the matrix is singular over GF(64)")
        work[[column, pivot]] = work[[pivot, column]]
        work[column] = gf_multiply(gf_inverse(int(work[column, column])), work[column])
        for row in range(n):
            if row != column and work[row, column]:
                work[row] ^= gf_multiply(work[row, column], work[column])
    return work[:, n:]


@lru_cache(maxsize=1)
def _bit_matrix() -> np.ndarray:
    """``[x, b]`` = bit b (MSB first) of symbol x, for x = 0..63."""
    return bits_from_symbols(np.arange(GF_ORDER)).reshape(GF_ORDER, GF_BITS)


def _xor_convolve(a: np.ndarray, b: np.ndarray, xor_table: np.ndarray) -> np.ndarray:
    """
    Min-sum "sum" of two cost vectors over GF(64) addition: ``out[z] = min over
    x ^ y = z of a[x] + b[y]``.  Batched over the leading axis.
    """
    gathered = b[:, xor_table]  # (batch, z, x): b[x ^ z]
    return (a[:, None, :] + gathered).min(axis=2)


# ---------------------------------------------------------------------------
# The three codes
# ---------------------------------------------------------------------------

BCNAV2_CODE = LdpcCode("B-CNAV2 LDPC(96,48)", _H_INDEX_PRINTED, _H_ELEMENT_PRINTED)
BCNAV1_SUBFRAME2_CODE = LdpcCode(
    "B-CNAV1 subframe 2 LDPC(200,100)",
    ldpc_b1c_tables.H100_INDEX_PRINTED, ldpc_b1c_tables.H100_ELEMENT_PRINTED,
)
BCNAV1_SUBFRAME3_CODE = LdpcCode(
    "B-CNAV1 subframe 3 LDPC(88,44)",
    ldpc_b1c_tables.H44_INDEX_PRINTED, ldpc_b1c_tables.H44_ELEMENT_PRINTED,
)

# The B-CNAV2 code under the names this module had before B-CNAV1 was added.
NUM_VARIABLES = BCNAV2_CODE.num_variables  # n: codeword symbols
NUM_CHECKS = BCNAV2_CODE.num_checks  # n - k
NUM_MESSAGE_SYMBOLS = BCNAV2_CODE.num_message_symbols  # k = 48
parity_check_matrix = BCNAV2_CODE.parity_check_matrix
syndrome = BCNAV2_CODE.syndrome
encode = BCNAV2_CODE.encode
decode = BCNAV2_CODE.decode
