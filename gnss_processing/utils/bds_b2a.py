"""
BeiDou B2a (open service) spreading codes and signal constants.

B2a is a BPSK(10) pair on 1176.45 MHz: a data component on the in-phase
branch and a dataless pilot on the quadrature branch, with equal power
(BDS-SIS-ICD-B2a-1.0, section 4.2.2 and Table 4-2).  Each component has a
10230-chip primary code that repeats every 1 ms, under a secondary
(overlay) code that advances one chip per primary period:

* data  -- the fixed 5-chip code ``00010``; one chip is one 200 sps symbol;
* pilot -- a 100-chip truncated Weil code that differs for each satellite.

This module only builds the codes.  `utils.signal_interfaces.BdsB2a` turns
them into a `Signal`.  The module needs nothing from `gnss-tools`, so the
upstream project can take it as it is later (D-048).

Every code is an ``int8`` array of +1 and -1.  Logic 0 is +1 and logic 1 is
-1 (ICD 4.3).  This is the same as ``1 - 2 * bits``.

Why the PRN tables are in this file: a code generator must work with no file
on disk.  The tests compare these tables with the ICD copy in
``docs/icd/bds_b2a_code_tables.json``, so a typing error cannot hide.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np

# --- Signal constants (ICD 4.2, 5.2) --------------------------------------
CARRIER_FREQ_HZ = 1176.45e6
CODE_RATE_CHIPS_PER_SEC = 10.23e6
PRIMARY_CODE_LENGTH = 10230
PRIMARY_PERIOD_MS = 1
DATA_SYMBOL_RATE_SPS = 200
DATA_SYMBOL_PERIOD_MS = 5
DATA_SECONDARY_LENGTH = 5
PILOT_SECONDARY_LENGTH = 100

# --- Code-generator constants (ICD 5.2.1, 5.2.2) --------------------------
FIRST_PRN = 1
LAST_PRN = 63
_REGISTER_LENGTH = 13
# Register 1 restarts from all ones after this many chips.  The two registers
# restart together at every primary period.
_REGISTER1_RESET_CHIPS = 8190
# Feedback stages (1 = first stage) of the generator polynomials, the
# x**0 term left out: data g1 = 1+x+x^5+x^11+x^13, and so on.
_DATA_G1_TAPS = (1, 5, 11, 13)
_DATA_G2_TAPS = (3, 5, 9, 11, 12, 13)
_PILOT_G1_TAPS = (3, 6, 7, 13)
_PILOT_G2_TAPS = (1, 5, 7, 8, 12, 13)
# The data secondary code, MSB first (Table 5-1).
_DATA_SECONDARY_BITS = "00010"
# The pilot secondary code is a truncated Weil code of this prime length.
_WEIL_LENGTH = 1021

_DATA_REG2_INIT = (
    "1000000100101",  # PRN 1
    "1000000110100",  # PRN 2
    "1000010101101",  # PRN 3
    "1000101001111",  # PRN 4
    "1000101010101",  # PRN 5
    "1000110101110",  # PRN 6
    "1000111101110",  # PRN 7
    "1000111111011",  # PRN 8
    "1001100101001",  # PRN 9
    "1001111011010",  # PRN 10
    "1010000110101",  # PRN 11
    "1010001000100",  # PRN 12
    "1010001010101",  # PRN 13
    "1010001011011",  # PRN 14
    "1010001011100",  # PRN 15
    "1010010100011",  # PRN 16
    "1010011110111",  # PRN 17
    "1010100000001",  # PRN 18
    "1010100111110",  # PRN 19
    "1010110101011",  # PRN 20
    "1010110110001",  # PRN 21
    "1011001010011",  # PRN 22
    "1011001100010",  # PRN 23
    "1011010011000",  # PRN 24
    "1011010110110",  # PRN 25
    "1011011110010",  # PRN 26
    "1011011111111",  # PRN 27
    "1011100010010",  # PRN 28
    "1011100111100",  # PRN 29
    "1011110100001",  # PRN 30
    "1011111001000",  # PRN 31
    "1011111010100",  # PRN 32
    "1011111101011",  # PRN 33
    "1011111110011",  # PRN 34
    "1100001010001",  # PRN 35
    "1100010010100",  # PRN 36
    "1100010110111",  # PRN 37
    "1100100010001",  # PRN 38
    "1100100011001",  # PRN 39
    "1100110101011",  # PRN 40
    "1100110110001",  # PRN 41
    "1100111010010",  # PRN 42
    "1101001010101",  # PRN 43
    "1101001110100",  # PRN 44
    "1101011001011",  # PRN 45
    "1101101010111",  # PRN 46
    "1110000110100",  # PRN 47
    "1110010000011",  # PRN 48
    "1110010001011",  # PRN 49
    "1110010100011",  # PRN 50
    "1110010101000",  # PRN 51
    "1110100111011",  # PRN 52
    "1110110010111",  # PRN 53
    "1111001001000",  # PRN 54
    "1111010010100",  # PRN 55
    "1111010011001",  # PRN 56
    "1111011011010",  # PRN 57
    "1111011111000",  # PRN 58
    "1111011111111",  # PRN 59
    "1111110110101",  # PRN 60
    "0010000000010",  # PRN 61
    "1101111110101",  # PRN 62
    "0001111010010",  # PRN 63
)

_PILOT_REG2_INIT = (
    "1000000100101",  # PRN 1
    "1000000110100",  # PRN 2
    "1000010101101",  # PRN 3
    "1000101001111",  # PRN 4
    "1000101010101",  # PRN 5
    "1000110101110",  # PRN 6
    "1000111101110",  # PRN 7
    "1000111111011",  # PRN 8
    "1001100101001",  # PRN 9
    "1001111011010",  # PRN 10
    "1010000110101",  # PRN 11
    "1010001000100",  # PRN 12
    "1010001010101",  # PRN 13
    "1010001011011",  # PRN 14
    "1010001011100",  # PRN 15
    "1010010100011",  # PRN 16
    "1010011110111",  # PRN 17
    "1010100000001",  # PRN 18
    "1010100111110",  # PRN 19
    "1010110101011",  # PRN 20
    "1010110110001",  # PRN 21
    "1011001010011",  # PRN 22
    "1011001100010",  # PRN 23
    "1011010011000",  # PRN 24
    "1011010110110",  # PRN 25
    "1011011110010",  # PRN 26
    "1011011111111",  # PRN 27
    "1011100010010",  # PRN 28
    "1011100111100",  # PRN 29
    "1011110100001",  # PRN 30
    "1011111001000",  # PRN 31
    "1011111010100",  # PRN 32
    "1011111101011",  # PRN 33
    "1011111110011",  # PRN 34
    "1100001010001",  # PRN 35
    "1100010010100",  # PRN 36
    "1100010110111",  # PRN 37
    "1100100010001",  # PRN 38
    "1100100011001",  # PRN 39
    "1100110101011",  # PRN 40
    "1100110110001",  # PRN 41
    "1100111010010",  # PRN 42
    "1101001010101",  # PRN 43
    "1101001110100",  # PRN 44
    "1101011001011",  # PRN 45
    "1101101010111",  # PRN 46
    "1110000110100",  # PRN 47
    "1110010000011",  # PRN 48
    "1110010001011",  # PRN 49
    "1110010100011",  # PRN 50
    "1110010101000",  # PRN 51
    "1110100111011",  # PRN 52
    "1110110010111",  # PRN 53
    "1111001001000",  # PRN 54
    "1111010010100",  # PRN 55
    "1111010011001",  # PRN 56
    "1111011011010",  # PRN 57
    "1111011111000",  # PRN 58
    "1111011111111",  # PRN 59
    "1111110110101",  # PRN 60
    "1010010000110",  # PRN 61
    "0010111111000",  # PRN 62
    "0001101010101",  # PRN 63
)

_PILOT_SECONDARY_WP = (
    (123, 138),  # PRN 1
    (55, 570),  # PRN 2
    (40, 351),  # PRN 3
    (139, 77),  # PRN 4
    (31, 885),  # PRN 5
    (175, 247),  # PRN 6
    (350, 413),  # PRN 7
    (450, 180),  # PRN 8
    (478, 3),  # PRN 9
    (8, 26),  # PRN 10
    (73, 17),  # PRN 11
    (97, 172),  # PRN 12
    (213, 30),  # PRN 13
    (407, 1008),  # PRN 14
    (476, 646),  # PRN 15
    (4, 158),  # PRN 16
    (15, 170),  # PRN 17
    (47, 99),  # PRN 18
    (163, 53),  # PRN 19
    (280, 179),  # PRN 20
    (322, 925),  # PRN 21
    (353, 114),  # PRN 22
    (375, 10),  # PRN 23
    (510, 584),  # PRN 24
    (332, 60),  # PRN 25
    (7, 3),  # PRN 26
    (13, 684),  # PRN 27
    (16, 263),  # PRN 28
    (18, 545),  # PRN 29
    (25, 22),  # PRN 30
    (50, 546),  # PRN 31
    (81, 190),  # PRN 32
    (118, 303),  # PRN 33
    (127, 234),  # PRN 34
    (132, 38),  # PRN 35
    (134, 822),  # PRN 36
    (164, 57),  # PRN 37
    (177, 668),  # PRN 38
    (208, 697),  # PRN 39
    (249, 93),  # PRN 40
    (276, 18),  # PRN 41
    (349, 66),  # PRN 42
    (439, 318),  # PRN 43
    (477, 133),  # PRN 44
    (498, 98),  # PRN 45
    (88, 70),  # PRN 46
    (155, 132),  # PRN 47
    (330, 26),  # PRN 48
    (3, 354),  # PRN 49
    (21, 58),  # PRN 50
    (84, 41),  # PRN 51
    (111, 182),  # PRN 52
    (128, 944),  # PRN 53
    (153, 205),  # PRN 54
    (197, 23),  # PRN 55
    (199, 1),  # PRN 56
    (214, 792),  # PRN 57
    (256, 641),  # PRN 58
    (265, 83),  # PRN 59
    (291, 7),  # PRN 60
    (324, 111),  # PRN 61
    (326, 96),  # PRN 62
    (340, 92),  # PRN 63
)


def _check_prn(prn: int) -> None:
    """Raise a clear error when `prn` is not a B2a PRN (1 to 63)."""
    if not isinstance(prn, (int, np.integer)) or not FIRST_PRN <= prn <= LAST_PRN:
        raise ValueError(
            f"BeiDou B2a PRN must be an integer from {FIRST_PRN} to {LAST_PRN}, got {prn!r}"
        )


def _bits_to_signs(bits: np.ndarray) -> np.ndarray:
    """Map logic 0 to +1 and logic 1 to -1, as an int8 array (ICD 4.3)."""
    return (1 - 2 * bits.astype(np.int8)).astype(np.int8)


def _tap_mask(taps: tuple[int, ...]) -> int:
    """Bit mask of the feedback stages.  Stage k is bit k-1 of the state."""
    mask = 0
    for stage in taps:
        mask |= 1 << (stage - 1)
    return mask


def _state_from_string(start: str) -> int:
    """Turn a table entry s1..s13 (stage 1 first) into a register state."""
    if len(start) != _REGISTER_LENGTH or set(start) - {"0", "1"}:
        raise ValueError(f"bad register start state {start!r}")
    return sum(int(char) << index for index, char in enumerate(start))


def _lfsr_primary_code(
    g1_taps: tuple[int, ...], g2_taps: tuple[int, ...], reg2_start: str
) -> np.ndarray:
    """
    Run the two 13-stage registers for one primary period (ICD 5.2.1).

    The registers shift towards stage 13.  The new stage 1 is the XOR of the
    tap stages.  The chip is stage 13 of register 1 XOR stage 13 of register
    2.  Register 1 restarts after `_REGISTER1_RESET_CHIPS` chips; that is how
    the ICD cuts the 8191-chip sequence short.  Integer bit operations keep
    the loop fast enough to build all 126 codes in about a second.
    """
    mask1, mask2 = _tap_mask(g1_taps), _tap_mask(g2_taps)
    top = _REGISTER_LENGTH - 1
    full = (1 << _REGISTER_LENGTH) - 1
    state1, state2 = full, _state_from_string(reg2_start)
    bits = np.zeros(PRIMARY_CODE_LENGTH, dtype=np.uint8)
    for chip in range(PRIMARY_CODE_LENGTH):
        if chip == _REGISTER1_RESET_CHIPS:
            state1 = full
        bits[chip] = ((state1 >> top) ^ (state2 >> top)) & 1
        feedback1 = (state1 & mask1).bit_count() & 1
        feedback2 = (state2 & mask2).bit_count() & 1
        state1 = ((state1 << 1) & full) | feedback1
        state2 = ((state2 << 1) & full) | feedback2
    return bits


@lru_cache(maxsize=None)
def _data_code(prn: int) -> np.ndarray:
    """Cached data primary code; callers get a read-only array."""
    bits = _lfsr_primary_code(_DATA_G1_TAPS, _DATA_G2_TAPS, _DATA_REG2_INIT[prn - 1])
    code = _bits_to_signs(bits)
    code.setflags(write=False)
    return code


@lru_cache(maxsize=None)
def _pilot_code(prn: int) -> np.ndarray:
    """Cached pilot primary code; callers get a read-only array."""
    bits = _lfsr_primary_code(_PILOT_G1_TAPS, _PILOT_G2_TAPS, _PILOT_REG2_INIT[prn - 1])
    code = _bits_to_signs(bits)
    code.setflags(write=False)
    return code


def generate_data_code(prn: int) -> np.ndarray:
    """
    B2a data primary code for one PRN: 10230 chips of +1 and -1, int8.

    The array is read-only because it is cached.  Copy it before you change it.
    """
    _check_prn(prn)
    return _data_code(int(prn))


def generate_pilot_code(prn: int) -> np.ndarray:
    """
    B2a pilot primary code for one PRN: 10230 chips of +1 and -1, int8.

    The array is read-only because it is cached.  Copy it before you change it.
    """
    _check_prn(prn)
    return _pilot_code(int(prn))


def data_secondary_code() -> np.ndarray:
    """
    B2a data secondary code ``00010`` as +1 and -1.  All satellites use it.

    One chip lasts one primary period, so the whole code is one 5 ms symbol.
    """
    bits = np.array([int(char) for char in _DATA_SECONDARY_BITS], dtype=np.uint8)
    return _bits_to_signs(bits)


@lru_cache(maxsize=1)
def _legendre_sequence() -> np.ndarray:
    """
    Legendre sequence of length `_WEIL_LENGTH`: L(0) = 0, L(k) = 1 when k is a
    non-zero square modulo the length, else 0 (ICD 5.2.2).
    """
    sequence = np.zeros(_WEIL_LENGTH, dtype=np.uint8)
    squares = (np.arange(1, _WEIL_LENGTH, dtype=np.int64) ** 2) % _WEIL_LENGTH
    sequence[np.unique(squares)] = 1
    return sequence


def _weil_secondary_bits(w: int, p: int) -> np.ndarray:
    """
    Truncated Weil code c(n; w, p) = W((n + p - 1) mod N; w), n = 0..99.

    W(k; w) = L(k) XOR L((k + w) mod N).  Eq. (5-3) to (5-5) of the ICD.
    """
    legendre = _legendre_sequence()
    k = (np.arange(PILOT_SECONDARY_LENGTH) + p - 1) % _WEIL_LENGTH
    return legendre[k] ^ legendre[(k + w) % _WEIL_LENGTH]


def generate_pilot_secondary_code(prn: int) -> np.ndarray:
    """
    B2a pilot secondary code for one PRN: 100 chips of +1 and -1, int8.

    One chip lasts one primary period, so the code repeats every 100 ms.
    """
    _check_prn(prn)
    w, p = _PILOT_SECONDARY_WP[int(prn) - 1]
    return _bits_to_signs(_weil_secondary_bits(w, p))
