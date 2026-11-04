"""
Galileo E5a spreading codes and signal constants (Galileo OS SIS ICD Issue 2.2).

E5a is on 1176.45 MHz with GPS L5 and BeiDou B2a.  It has a data component E5a-I (F/NAV) and a
pilot E5a-Q, each a BPSK(10) code of 10230 chips at 10.23 Mcps, so the primary period is 1 ms.
Each has a secondary (overlay) code that advances one chip per primary period:

* E5a-I -- the fixed 20-chip code CS20_1.  One chip lasts 1 ms, so the code lasts 20 ms, which is
  exactly one F/NAV symbol (50 sps).
* E5a-Q -- a 100-chip code CS100_n that differs for each satellite; it lasts 100 ms.

E5a-QP (the 5.115 Mcps fast-acquisition code) is out of scope (D-067, OQ-15).

The module only builds the codes.  `utils.signal_interfaces.GalE5a` turns them into a `Signal`.
It follows the pattern of `utils.bds_b2a` (D-048): the generators are here; the per-code numbers
are data in `docs/icd/e5a_code_tables.json` (read from the ICD by the user's script).  A test
compares the codes with the first 24 chips that the ICD prints for each code.

Every code is an ``int8`` array of +1 and -1.  Logic 0 is +1 and logic 1 is -1 (ICD Table 12), the
same as ``1 - 2 * bits``.  (c) European Union 2025: keep this notice with copies of the tables.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np

# --- Signal constants (ICD 2.3, 3.2) ----------------------------------------
CARRIER_FREQ_HZ = 1176.45e6
CODE_RATE_CHIPS_PER_SEC = 10.23e6
PRIMARY_CODE_LENGTH = 10230
PRIMARY_PERIOD_MS = 1
DATA_SYMBOL_RATE_SPS = 50
DATA_SYMBOL_PERIOD_MS = 20
DATA_SECONDARY_LENGTH = 20
PILOT_SECONDARY_LENGTH = 100

# --- Code generator constants (ICD 3.3, 3.4.1) -------------------------------
FIRST_PRN = 1
LAST_PRN = 50  # codes exist for 50; SVID 1 to 36 are assigned (ICD 3.6)
LAST_ASSIGNED_PRN = 36
_REGISTER_LENGTH = 14

CODE_TABLES_PATH = (
    Path(__file__).resolve().parent.parent / "docs" / "icd" / "e5a_code_tables.json"
)


@lru_cache(maxsize=1)
def load_code_tables() -> dict:
    """
    Read the ICD number tables once.

    A missing or broken file stops the generators with a clear message, because a wrong start
    value gives a code that matches no satellite.
    """
    try:
        with CODE_TABLES_PATH.open(encoding="utf-8") as handle:
            tables = json.load(handle)
    except (OSError, ValueError) as error:
        raise RuntimeError(f"cannot read the E5a code tables {CODE_TABLES_PATH}: {error}") from error
    starts = tables["e5a_primary"]["register2_start_octal_and_first24_hex"]
    for kind in ("I", "Q"):
        missing = [n for n in range(FIRST_PRN, LAST_PRN + 1) if str(n) not in starts[kind]]
        if missing:
            raise RuntimeError(f"E5a-{kind} table in {CODE_TABLES_PATH} lacks code {missing}")
    if len(tables["secondary"]["CS100_1_to_100_hex"]) != 100:
        raise RuntimeError(f"the CS100 table in {CODE_TABLES_PATH} does not hold 100 codes")
    return tables


def _check_prn(prn: int) -> int:
    prn = int(prn)
    if not FIRST_PRN <= prn <= LAST_PRN:
        raise ValueError(f"E5a PRN must be {FIRST_PRN} to {LAST_PRN} (ICD 3.6); got {prn}")
    return prn


def _bits_to_signs(bits: np.ndarray) -> np.ndarray:
    """Logic 0 -> +1, logic 1 -> -1, as a read-only int8 array."""
    signs = (1 - 2 * bits.astype(np.int8)).astype(np.int8)
    signs.setflags(write=False)
    return signs


def _tap_flags(octal_word: str) -> list[int]:
    """a_j for j = 0..14: bit j of the octal word (LSB is j = 0).  a_14 is always 1; a_0 is unused."""
    value = int(octal_word, 8)
    return [(value >> j) & 1 for j in range(_REGISTER_LENGTH + 1)]


def _register_sequence(taps_octal: str, start: list[int]) -> np.ndarray:
    """
    One 14-stage register, `PRIMARY_CODE_LENGTH` output bits (0/1).

    The output is the last stage (stage 14).  A new stage 1 is the XOR of the stages j whose tap
    a_j is 1 (j = 1..14); the stages shift from 1 toward 14.  The register is run for one
    period only, because both registers restart after 10230 chips (ICD 3.4.1).
    """
    taps = _tap_flags(taps_octal)
    tapped = [j for j in range(1, _REGISTER_LENGTH + 1) if taps[j]]
    stages = list(start)  # stages[0] is stage 1
    out = np.zeros(PRIMARY_CODE_LENGTH, dtype=np.int8)
    for k in range(PRIMARY_CODE_LENGTH):
        out[k] = stages[_REGISTER_LENGTH - 1]
        feedback = 0
        for j in tapped:
            feedback ^= stages[j - 1]
        stages = [feedback] + stages[: _REGISTER_LENGTH - 1]
    return out


def primary_code_bits(register2_start_octal: str) -> np.ndarray:
    """
    Primary code (0/1 chips) from the start value of register 2.

    Register 1 starts all ones.  Bit j-1 of the octal start word is the start value of stage j of
    register 2 (LSB = stage 1).  The code is the XOR of the two register outputs.
    """
    tables = load_code_tables()["e5a_primary"]
    value = int(register2_start_octal, 8)
    start2 = [(value >> (j - 1)) & 1 for j in range(1, _REGISTER_LENGTH + 1)]
    return _register_sequence(tables["register1_taps_octal"], [1] * _REGISTER_LENGTH) ^ _register_sequence(
        tables["register2_taps_octal"], start2
    )


@lru_cache(maxsize=None)
def _primary(kind: str, prn: int) -> np.ndarray:
    start = load_code_tables()["e5a_primary"]["register2_start_octal_and_first24_hex"][kind][str(prn)][0]
    return _bits_to_signs(primary_code_bits(start))


def generate_data_code(prn: int) -> np.ndarray:
    """E5a-I primary code: 10230 chips of +1 and -1, int8, read-only."""
    return _primary("I", _check_prn(prn))


def generate_pilot_code(prn: int) -> np.ndarray:
    """E5a-Q primary code: 10230 chips of +1 and -1, int8, read-only."""
    return _primary("Q", _check_prn(prn))


def _hex_bits(text: str, count: int) -> np.ndarray:
    """First `count` bits of a hex string, first bit first."""
    bits = "".join(format(int(c, 16), "04b") for c in text)
    return np.array([int(b) for b in bits[:count]], dtype=np.int8)


@lru_cache(maxsize=1)
def data_secondary_code() -> np.ndarray:
    """CS20_1: the 20-chip E5a-I secondary code, the same for every satellite."""
    return _bits_to_signs(_hex_bits(load_code_tables()["secondary"]["CS20_1_hex"], DATA_SECONDARY_LENGTH))


@lru_cache(maxsize=None)
def generate_pilot_secondary_code(prn: int) -> np.ndarray:
    """
    CS100_n: the 100-chip E5a-Q secondary code of satellite n, +1 and -1, int8.

    One chip lasts one primary period (1 ms), so the code repeats every 100 ms.
    """
    text = load_code_tables()["secondary"]["CS100_1_to_100_hex"][str(_check_prn(prn))]
    return _bits_to_signs(_hex_bits(text, PILOT_SECONDARY_LENGTH))
