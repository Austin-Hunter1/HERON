"""
BeiDou B1C (open service) spreading codes and signal constants.

B1C sits on 1575.42 MHz with GPS L1 C/A and L1C.  It has a data component and
a dataless pilot (BDS-SIS-ICD-B1C-1.0, section 4.2.2, Table 4-2):

* data  -- BOC(1,1), in phase (0 degrees), 1/4 of the power;
* pilot -- a QMBOC part: BOC(1,1) at 90 degrees with 29/44 of the power, and
  BOC(6,1) at 0 degrees with 1/11 of the power.

Each component has a 10230-chip primary code at 1.023 Mcps, so the primary
period is 10 ms.  The pilot also has a 1800-chip secondary (overlay) code,
one chip per primary period, so it repeats every 18 s.  The data has no
secondary code (Table 5-1).

All three codes are truncated Weil codes (ICD 5.2).  This module builds them
from the per-PRN pairs ``(w, p)`` in ``docs/icd/bds_b1c_code_tables.json``.
That file is the one place that holds the pairs.  The tests compare the first
and last 24 chips of every code with the ICD octal values in the same file.

The module does not build the BOC(6,1) part of the pilot (narrow-band mode,
decision D-062).  That part holds 1/11 of the power, so it costs about 0.4 dB.
Tracking it needs about 27 MHz of bandwidth.  The HERON collects use less.

Every code is an ``int8`` array of +1 and -1.  Logic 0 is +1 and logic 1 is
-1 (ICD 4.3).  This is the same as ``1 - 2 * bits``.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np

# --- Signal constants (ICD 4.1, 4.2, 5.2) ----------------------------------
CARRIER_FREQ_HZ = 1575.42e6
CODE_RATE_CHIPS_PER_SEC = 1.023e6
PRIMARY_CODE_LENGTH = 10230
PRIMARY_PERIOD_MS = 10
DATA_SYMBOL_RATE_SPS = 100
DATA_SYMBOL_PERIOD_MS = 10
PILOT_SECONDARY_LENGTH = 1800
# The subcarrier of the BOC(1,1) parts runs at the chip rate (f = 1.023 MHz);
# a BOC(1,1) chip has two half-cycles ("sub-chips").
BOC11_SUBCARRIER_RATE_HZ = 1.023e6
# The BOC(6,1) part of the pilot (QMBOC): the subcarrier runs at 6 times the chip rate.
BOC61_SUBCARRIER_RATE_HZ = 6.138e6

# --- Power split (ICD Table 4-2) -------------------------------------------
DATA_POWER_FRACTION = 1.0 / 4.0
PILOT_BOC11_POWER_FRACTION = 29.0 / 44.0
PILOT_BOC61_POWER_FRACTION = 1.0 / 11.0

# --- Code-generator constants (ICD 5.2.1, 5.2.2) ---------------------------
FIRST_PRN = 1
LAST_PRN = 63
# Prime lengths of the Legendre sequences behind the Weil codes.
PRIMARY_WEIL_LENGTH = 10243
SECONDARY_WEIL_LENGTH = 3607

# The ICD code-pair file.  It is next to the other ICD copies, so one edit
# changes every user.
CODE_TABLES_PATH = (
    Path(__file__).resolve().parent.parent / "docs" / "icd" / "bds_b1c_code_tables.json"
)
_TABLE_KEYS = ("primary_data", "primary_pilot", "pilot_secondary")


def _check_prn(prn: int) -> None:
    """Raise a clear error for a PRN outside 1 to 63."""
    if not FIRST_PRN <= int(prn) <= LAST_PRN:
        raise ValueError(
            f"B1C PRN must be {FIRST_PRN} to {LAST_PRN} (ICD 5.1); got {prn}"
        )


@lru_cache(maxsize=1)
def load_code_tables(path: Path | None = None) -> dict:
    """
    Read the ICD code-pair file once.

    The file is the single source of the ``(w, p)`` pairs.  A missing or
    broken file stops the code generator with a clear message, because a
    wrong pair would give a code that matches no satellite.
    """
    table_path = Path(path) if path is not None else CODE_TABLES_PATH
    try:
        with table_path.open() as handle:
            tables = json.load(handle)
    except (OSError, ValueError) as error:
        raise RuntimeError(f"cannot read the B1C code tables {table_path}: {error}") from error
    for key in _TABLE_KEYS:
        missing = [prn for prn in range(FIRST_PRN, LAST_PRN + 1) if str(prn) not in tables.get(key, {})]
        if missing:
            raise RuntimeError(f"B1C code table '{key}' in {table_path} lacks PRN {missing}")
    return tables


def _pair(table: str, prn: int) -> tuple[int, int]:
    """The ``(w, p)`` pair of one PRN in one table."""
    _check_prn(prn)
    entry = load_code_tables()[table][str(int(prn))]
    return int(entry["w"]), int(entry["p"])


@lru_cache(maxsize=2)
def _legendre_sequence(length: int) -> np.ndarray:
    """
    Legendre sequence of a prime `length`: L(0) = 0, L(k) = 1 when k is a
    non-zero square modulo the length, else 0 (ICD 5.2).
    """
    sequence = np.zeros(length, dtype=np.uint8)
    squares = (np.arange(1, length, dtype=np.int64) ** 2) % length
    sequence[np.unique(squares)] = 1
    return sequence


def _truncated_weil_bits(length: int, w: int, p: int, num_chips: int) -> np.ndarray:
    """
    Truncated Weil code c(n; w, p) = W((n + p - 1) mod N; w), n = 0..num_chips-1.

    W(k; w) = L(k) XOR L((k + w) mod N), with N = `length`.  Eq. (5-1) to (5-3)
    of the ICD.
    """
    legendre = _legendre_sequence(length)
    k = (np.arange(num_chips, dtype=np.int64) + p - 1) % length
    return legendre[k] ^ legendre[(k + w) % length]


def _bits_to_signs(bits: np.ndarray) -> np.ndarray:
    """Logic 0 -> +1, logic 1 -> -1, as a read-only int8 array."""
    signs = (1 - 2 * bits.astype(np.int8)).astype(np.int8)
    signs.setflags(write=False)
    return signs


@lru_cache(maxsize=None)
def _primary(table: str, prn: int) -> np.ndarray:
    """Cached primary code of one PRN from one table."""
    w, p = _pair(table, prn)
    return _bits_to_signs(
        _truncated_weil_bits(PRIMARY_WEIL_LENGTH, w, p, PRIMARY_CODE_LENGTH)
    )


def generate_data_code(prn: int) -> np.ndarray:
    """B1C data primary code: 10230 chips of +1 and -1, int8, read-only."""
    return _primary("primary_data", int(prn))


def generate_pilot_code(prn: int) -> np.ndarray:
    """B1C pilot primary code: 10230 chips of +1 and -1, int8, read-only."""
    return _primary("primary_pilot", int(prn))


@lru_cache(maxsize=None)
def generate_pilot_secondary_code(prn: int) -> np.ndarray:
    """
    B1C pilot secondary code for one PRN: 1800 chips of +1 and -1, int8.

    One chip lasts one primary period (10 ms), so the code repeats every 18 s.
    """
    w, p = _pair("pilot_secondary", int(prn))
    return _bits_to_signs(
        _truncated_weil_bits(SECONDARY_WEIL_LENGTH, w, p, PILOT_SECONDARY_LENGTH)
    )
