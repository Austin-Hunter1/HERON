"""
Tests for the BeiDou B1C spreading codes (`utils.bds_b1c`) and for the
`BdsB1C` signal definition.

Three lines of defence:

* the ICD tables -- first and last 24 chips of all 63 data, pilot and pilot
  secondary codes, against ``docs/icd/bds_b1c_code_tables.json`` (the file
  also holds the ``(w, p)`` pairs the generator reads);
* a slow, literal copy of the ICD formulas that shares no code with the
  module (it finds squares by modular exponentiation, not by squaring);
* code statistics (length, values, balance, correlation) and error handling.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from utils import bds_b1c
from utils.code_components import Branch, SubcarrierKind
from utils.signal_interfaces import SIGNALS, BdsB1C, BdsB2a, build_signals

ICD_TABLES_PATH = Path(__file__).parent.parent / "docs" / "icd" / "bds_b1c_code_tables.json"
ALL_PRNS = range(1, 64)

# Bounds for the statistics tests.  They are set from the values measured on
# 2026-10-14 (primary codes: largest |sum| 0, largest periodic autocorrelation
# sidelobe 282, largest cross-correlation 430 of the pairs tested, for a peak
# of 10230; secondary codes: largest |sum| 48, sidelobe 100 for a peak of
# 1800), with margin.
MAX_CODE_IMBALANCE = 100
MAX_AUTOCORRELATION_SIDELOBE = 400
MAX_CROSS_CORRELATION = 600
MAX_SECONDARY_IMBALANCE = 100
MAX_SECONDARY_SIDELOBE = 200


@pytest.fixture(scope="module")
def icd_tables() -> dict:
    """The ICD code tables, read once per test module."""
    with ICD_TABLES_PATH.open() as handle:
        return json.load(handle)


def _octal_of_chips(signs: np.ndarray) -> str:
    """Chips (+1/-1) to octal digits, three chips per digit, first chip first."""
    bits = "".join("1" if sign < 0 else "0" for sign in signs)
    return "".join(str(int(bits[i : i + 3], 2)) for i in range(0, len(bits), 3))


def _slow_weil(length: int, w: int, p: int, count: int) -> list[int]:
    """
    Literal copy of ICD eq. (5-1) to (5-3), in plain Python.

    A non-zero k is a square modulo the prime N when k**((N-1)/2) = 1 mod N
    (Euler's criterion).  The module finds the squares by squaring instead.
    """
    def legendre(k: int) -> int:
        k %= length
        return 1 if k and pow(k, (length - 1) // 2, length) == 1 else 0

    return [
        legendre((n + p - 1) % length) ^ legendre(((n + p - 1) % length + w) % length)
        for n in range(count)
    ]


GENERATORS = {
    "primary_data": (bds_b1c.generate_data_code, bds_b1c.PRIMARY_WEIL_LENGTH, 10230),
    "primary_pilot": (bds_b1c.generate_pilot_code, bds_b1c.PRIMARY_WEIL_LENGTH, 10230),
    "pilot_secondary": (
        bds_b1c.generate_pilot_secondary_code, bds_b1c.SECONDARY_WEIL_LENGTH, 1800,
    ),
}


# --------------------------------------------------------------------------
# The ICD tables
# --------------------------------------------------------------------------

@pytest.mark.parametrize("table", sorted(GENERATORS))
def test_first_and_last_24_chips_match_the_icd_for_all_63_prns(icd_tables, table):
    generate = GENERATORS[table][0]
    for prn in ALL_PRNS:
        chips = generate(prn)
        expected = icd_tables[table][str(prn)]
        assert _octal_of_chips(chips[:24]) == expected["first24_octal"], (table, prn, "first")
        assert _octal_of_chips(chips[-24:]) == expected["last24_octal"], (table, prn, "last")


def test_icd_example_prn_1_data_code_starts_with_53773116():
    """The ICD example: 101011111111011001001110 in binary, 53773116 in octal."""
    chips = bds_b1c.generate_data_code(1)
    bits = "".join("1" if chip < 0 else "0" for chip in chips[:24])
    assert bits == "101011111111011001001110"
    assert _octal_of_chips(chips[:24]) == "53773116"


def test_the_icd_file_marks_every_entry_as_checked(icd_tables):
    for table in sorted(GENERATORS):
        assert len(icd_tables[table]) == 63
        assert all(entry["checked"] for entry in icd_tables[table].values())


@pytest.mark.parametrize("table", sorted(GENERATORS))
def test_the_fast_codes_equal_the_slow_icd_formulas(icd_tables, table):
    generate, length, count = GENERATORS[table]
    for prn in (1, 2, 17, 40, 63):
        entry = icd_tables[table][str(prn)]
        slow = _slow_weil(length, entry["w"], entry["p"], count)
        assert list(1 - 2 * np.array(slow)) == list(generate(prn)), (table, prn)


# --------------------------------------------------------------------------
# Shape and statistics
# --------------------------------------------------------------------------

@pytest.mark.parametrize("table", sorted(GENERATORS))
def test_codes_have_the_right_length_type_and_values(table):
    generate, _, count = GENERATORS[table]
    for prn in ALL_PRNS:
        chips = generate(prn)
        assert chips.dtype == np.int8
        assert len(chips) == count
        assert set(np.unique(chips)) <= {-1, 1}


def test_codes_are_read_only_so_a_caller_cannot_change_the_cache():
    chips = bds_b1c.generate_data_code(1)
    with pytest.raises(ValueError):
        chips[0] = -chips[0]


@pytest.mark.parametrize("generate", [bds_b1c.generate_data_code, bds_b1c.generate_pilot_code])
def test_primary_codes_are_balanced_and_have_low_correlation(generate):
    spectra = []
    for prn in ALL_PRNS:
        chips = generate(prn).astype(float)
        assert abs(chips.sum()) <= MAX_CODE_IMBALANCE
        spectra.append(np.fft.fft(chips))
    for spectrum in spectra:
        auto = np.abs(np.fft.ifft(spectrum * np.conj(spectrum)))
        assert auto[0] == pytest.approx(10230.0)
        assert auto[1:].max() <= MAX_AUTOCORRELATION_SIDELOBE
    worst = 0.0
    for i in range(0, 63, 4):
        for j in range(i + 1, 63, 5):
            cross = np.abs(np.fft.ifft(spectra[i] * np.conj(spectra[j])))
            worst = max(worst, cross.max())
    assert worst <= MAX_CROSS_CORRELATION


def test_data_and_pilot_primary_codes_differ_for_every_prn():
    for prn in ALL_PRNS:
        data = bds_b1c.generate_data_code(prn).astype(float)
        pilot = bds_b1c.generate_pilot_code(prn).astype(float)
        assert abs(data @ pilot) < 0.1 * len(data)


def test_pilot_secondary_codes_are_balanced_and_have_a_unique_offset():
    for prn in ALL_PRNS:
        overlay = bds_b1c.generate_pilot_secondary_code(prn).astype(float)
        assert abs(overlay.sum()) <= MAX_SECONDARY_IMBALANCE
        auto = np.abs(np.fft.ifft(np.fft.fft(overlay) * np.conj(np.fft.fft(overlay))))
        assert auto[0] == pytest.approx(1800.0)
        assert auto[1:].max() <= MAX_SECONDARY_SIDELOBE


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------

@pytest.mark.parametrize("prn", [0, 64, -1, 200])
def test_a_prn_outside_1_to_63_is_refused(prn):
    for generate in (
        bds_b1c.generate_data_code, bds_b1c.generate_pilot_code,
        bds_b1c.generate_pilot_secondary_code,
    ):
        with pytest.raises(ValueError, match="PRN"):
            generate(prn)


def test_a_missing_or_broken_code_table_file_gives_a_clear_error(tmp_path):
    load = bds_b1c.load_code_tables.__wrapped__
    with pytest.raises(RuntimeError, match="cannot read"):
        load(tmp_path / "absent.json")
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({"primary_data": {"1": {"w": 1, "p": 1}}}))
    with pytest.raises(RuntimeError, match="lacks PRN"):
        load(broken)


# --------------------------------------------------------------------------
# The Signal definition
# --------------------------------------------------------------------------

def test_the_signal_is_registered_with_its_constants():
    assert SIGNALS["BDS_B1C"] is BdsB1C
    assert BdsB1C.carrier_freq_hz == 1575.42e6
    assert BdsB1C.tracking_code_rate_chips_per_sec == 1.023e6
    assert BdsB1C.primary_period_ms == 10
    assert BdsB1C.sat_id_prefix == "C"
    assert BdsB1C.default_prns == tuple(range(1, 64))


def test_components_follow_the_icd_structure():
    data, pilot = BdsB1C(7).code_set.components
    assert (data.name, pilot.name) == ("B1CD", "B1CP")
    # Data 0 degrees, pilot BOC(1,1) part 90 degrees (ICD Table 4-2).
    assert (data.branch, pilot.branch) == (Branch.I, Branch.Q)
    assert data.power_weight == pytest.approx(1 / 4)
    assert pilot.power_weight == pytest.approx(29 / 44)
    # The data has no secondary code; one symbol is one 10 ms primary period.
    assert data.overlay is None
    assert data.symbol_period_ms == 10
    # The pilot is dataless and carries the 1800-chip overlay.
    assert pilot.symbol_period_ms is None
    assert len(pilot.overlay) == 1800
    assert np.array_equal(pilot.overlay, bds_b1c.generate_pilot_secondary_code(7))
    for component in (data, pilot):
        assert component.subcarrier.kind is SubcarrierKind.BOC_SIN
        assert component.subcarrier.rate_hz == 1.023e6
        assert len(component.sequence) == 10230


def test_b1c_and_b2a_share_a_satellite_id_but_not_a_signal_id():
    assert BdsB1C.sat_id_prefix == BdsB2a.sat_id_prefix
    assert BdsB1C.signal_type_id != BdsB2a.signal_type_id
    assert set(build_signals(BdsB1C, prns=[7, 9])) == {"C07", "C09"}
