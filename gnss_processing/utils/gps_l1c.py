"""
GPS L1C spreading codes, overlay codes and signal constants (IS-GPS-800J).

L1C has a data component (L1CD) and a pilot (L1CP) on 1575.42 MHz, in the same carrier phase.
Both have a 10230-chip ranging code at 1.023 Mcps, so the code period is 10 ms.  L1CD is
BOC(1,1).  L1CP is TMBOC(6,1,4/33): BOC(6,1) on 4 of every 33 spreading symbols and BOC(1,1)
on the others.  The pilot also carries the 1800-bit overlay code L1CO, one bit per code period,
so the overlay lasts 18 s.  The data has no overlay.

Why this module is here: the pinned `gnss-tools` checkout has only the L1CP Weil code, and
that returns 10223 chips (the 7-chip expansion sequence is missing).  This module gives the
complete set that `utils.signal_interfaces.GpsL1C` and the tests need.  The module follows
the same pattern as `utils.bds_b1c` (D-048): the generators are here, the per-PRN numbers are
data in `docs/icd/l1c_code_tables.json`, and the tests compare the result with the check chips
that the ICD prints.

Only PRN 1 to 63 are built (D-067, OQ-16).  The ICD gives PRN 64 to 210 in section 6.3.1 with a
different overlay generator.  Those tables are not in the repository yet.  A PRN above 63 raises
an error that says so; it does not return a wrong code.

Every sequence is an ``int8`` array of 0 and 1.  Chip value 0 is +1 on the carrier and chip value
1 is -1 (IS-GPS-800 3.2.1.5).  `utils.signal_interfaces` applies ``1 - 2 * chip``.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np

# --- Signal constants (IS-GPS-800 3.2) -------------------------------------
CARRIER_FREQ = 1575.42e6
CODE_RATE = 1.023e6  # chips per second
CODE_LENGTH = 10230  # chips
PRIMARY_PERIOD_MS = 10
DATA_SYMBOL_RATE = 100  # symbols per second: one CNAV-2 symbol per code period
OVERLAY_LENGTH = 1800  # bits, one per code period
OVERLAY_RATE = 100  # overlay bits per second
# Power split.  Table 3.2-1 gives -158.25 dBW for L1CP and -163 dBW for L1CD out of -157 dBW:
# 75 % and 25 %.  They add to one exactly.
L1CD_POWER_FRACTION = 0.25
L1CP_POWER_FRACTION = 0.75
# --- Subcarriers (IS-GPS-800 3.3) ------------------------------------------
BOC_SUBCARRIER_RATE = 1.023e6  # BOC(1,1)
TMBOC_SUBCARRIER_RATE = 6.138e6  # BOC(6,1)
TMBOC_PATTERN_LENGTH = 33
# BOC(6,1) on the spreading symbols whose index modulo 33 is one of these.
TMBOC_PATTERN_INDICES = (0, 4, 6, 29)
TMBOC_PATTERN = np.zeros(TMBOC_PATTERN_LENGTH, dtype=np.int8)
TMBOC_PATTERN[list(TMBOC_PATTERN_INDICES)] = 1
TMBOC_PATTERN.setflags(write=False)

# --- Code generator constants (IS-GPS-800 3.2.2.1) -------------------------
FIRST_PRN = 1
NUM_PRNS = 63  # built here; the ICD defines up to 210 (see the module docstring)
LEGENDRE_LENGTH = 10223
# Inserted before the p-th Weil value (3.2.2.1.1).
EXPANSION_SEQUENCE = np.array([0, 1, 1, 0, 1, 0, 0], dtype=np.int8)
EXPANSION_SEQUENCE.setflags(write=False)
_OVERLAY_REGISTER_LENGTH = 11

CODE_TABLES_PATH = (
    Path(__file__).resolve().parent.parent / "docs" / "icd" / "l1c_code_tables.json"
)


@lru_cache(maxsize=1)
def load_code_tables() -> dict:
    """
    Read the ICD number tables once.

    A missing or broken file stops the generators with a clear message.  A wrong number would
    give a code that matches no satellite and fails without a sign of why.
    """
    try:
        with CODE_TABLES_PATH.open(encoding="utf-8") as handle:
            tables = json.load(handle)
    except (OSError, ValueError) as error:
        raise RuntimeError(f"cannot read the L1C code tables {CODE_TABLES_PATH}: {error}") from error
    for key in ("ranging", "overlay"):
        missing = [prn for prn in range(FIRST_PRN, NUM_PRNS + 1) if str(prn) not in tables.get(key, {})]
        if missing:
            raise RuntimeError(f"L1C code table '{key}' in {CODE_TABLES_PATH} lacks PRN {missing}")
    return tables


def __getattr__(name: str):
    """The per-PRN index lists are read from the table file only when someone asks for them."""
    if name in ("L1CP_WEIL_INDEX", "L1CP_INSERTION_INDEX", "L1CD_WEIL_INDEX", "L1CD_INSERTION_INDEX"):
        component = "p" if name.startswith("L1CP") else "d"
        field = "weil" if "WEIL" in name else "insertion"
        ranging = load_code_tables()["ranging"]
        return [int(ranging[str(prn)][component][field]) for prn in range(FIRST_PRN, NUM_PRNS + 1)]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _check_prn(prn: int) -> int:
    """Raise a clear error for a PRN that this module does not build."""
    prn = int(prn)
    if not FIRST_PRN <= prn <= NUM_PRNS:
        raise ValueError(
            f"PRN must be {FIRST_PRN} to {NUM_PRNS}; got {prn}. "
            "PRN 64 to 210 (IS-GPS-800 6.3.1) are not built yet"
        )
    return prn


# --- Ranging codes ---------------------------------------------------------
@lru_cache(maxsize=1)
def generate_legendre_sequence() -> np.ndarray:
    """
    Legendre sequence L(t), t = 0..10222: 1 where t is a non-zero square modulo 10223, else 0.

    L(0) = 0 is a special case of the ICD (3.2.2.1).  The general rule would give 1 and flip
    one chip of every code.  The array is read-only, because it is cached.
    """
    sequence = np.zeros(LEGENDRE_LENGTH, dtype=np.int8)
    squares = (np.arange(1, LEGENDRE_LENGTH, dtype=np.int64) ** 2) % LEGENDRE_LENGTH
    sequence[squares] = 1
    sequence.setflags(write=False)
    return sequence


def generate_weil_code_sequence(weil_index: int) -> np.ndarray:
    """Weil code W(t; w) = L(t) xor L((t + w) mod 10223), 10223 chips (0/1)."""
    legendre = generate_legendre_sequence()
    shifted = legendre[(np.arange(LEGENDRE_LENGTH) + int(weil_index)) % LEGENDRE_LENGTH]
    return (legendre ^ shifted).astype(np.int8)


def _ranging_code(weil_index: int, insertion_index: int) -> np.ndarray:
    """
    One L1CP or L1CD code (0/1 chips): the Weil code with the 7-chip expansion sequence put in
    before the p-th Weil value, p = 1..10223.  The sequence is inserted, not written over seven
    chips: the old gnss-tools code skipped this step and gave a code 7 chips short.
    """
    weil = generate_weil_code_sequence(weil_index)
    code = np.concatenate(
        [weil[: insertion_index - 1], EXPANSION_SEQUENCE, weil[insertion_index - 1 :]]
    ).astype(np.int8)
    if len(code) != CODE_LENGTH:
        raise RuntimeError(f"an L1C code has {len(code)} chips, not {CODE_LENGTH}")
    return code


@lru_cache(maxsize=None)
def _component_code(prn: int, component: str) -> np.ndarray:
    entry = load_code_tables()["ranging"][str(prn)][component]
    code = _ranging_code(int(entry["weil"]), int(entry["insertion"]))
    code.setflags(write=False)
    return code


def generate_code_sequence_L1CP(prn: int) -> np.ndarray:
    """L1CP ranging code of one PRN (0/1 chips, 10230, read-only)."""
    return _component_code(_check_prn(prn), "p")


def get_GPS_L1CP_code_sequence(prn: int) -> np.ndarray:
    """L1CP ranging code of one PRN.  Same as `generate_code_sequence_L1CP`."""
    return generate_code_sequence_L1CP(prn)


def get_GPS_L1CD_code_sequence(prn: int) -> np.ndarray:
    """L1CD ranging code of one PRN (0/1 chips, 10230, read-only)."""
    return _component_code(_check_prn(prn), "d")


# --- Overlay code ----------------------------------------------------------
@lru_cache(maxsize=None)
def _overlay(prn: int) -> np.ndarray:
    """
    L1CO overlay code from the 11-stage register of Figure 3.2-2.

    `s1_octal` holds the taps m11..m0 (m11 = m0 = 1).  `init11_octal` holds the 11 initial stage
    values, stage 11 as the MSB.  The output is stage 11, so the first 11 output bits are the
    initial condition, MSB first.  A new stage 1 is the XOR of the stages j whose tap m_j is 1.
    The sequence has period 2047 and is cut to 1800 bits.
    """
    entry = load_code_tables()["overlay"][str(prn)]
    taps = [(int(entry["s1_octal"], 8) >> j) & 1 for j in range(_OVERLAY_REGISTER_LENGTH + 1)]
    value = int(entry["init11_octal"], 8)
    stages = [(value >> i) & 1 for i in range(_OVERLAY_REGISTER_LENGTH)]  # stages[0] is stage 1
    tapped = [j for j in range(1, _OVERLAY_REGISTER_LENGTH + 1) if taps[j]]
    out = np.zeros(OVERLAY_LENGTH, dtype=np.int8)
    for k in range(OVERLAY_LENGTH):
        out[k] = stages[_OVERLAY_REGISTER_LENGTH - 1]
        feedback = 0
        for j in tapped:
            feedback ^= stages[j - 1]
        stages = [feedback] + stages[: _OVERLAY_REGISTER_LENGTH - 1]
    out.setflags(write=False)
    return out


def get_GPS_L1CO_overlay_sequence(prn: int) -> np.ndarray:
    """L1CO overlay code of one PRN's pilot (0/1 bits, 1800, read-only)."""
    return _overlay(_check_prn(prn))
