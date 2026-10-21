"""
Tests for the Galileo E5a spreading codes (`utils.gal_e5a`).

Three lines of defence:

* the ICD tables: the first 24 chips of all 50 E5a-I and 50 E5a-Q codes against
  `docs/icd/e5a_code_tables.json`, and the 100-chip secondary codes against their hex strings;
* the user's reference generator `docs/icd/verify_l1c_e5a_tables.py`, which was checked
  chip by chip against the ICD's own PDF attachments.  It shares no code with `gal_e5a`, so
  agreement over all 10230 chips of every code is a real check;
* code statistics and error handling.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

from utils import gal_e5a

ICD_DIR = Path(__file__).parent.parent / "docs" / "icd"
ALL_PRNS = range(1, 51)


@pytest.fixture(scope="module")
def tables() -> dict:
    return json.loads((ICD_DIR / "e5a_code_tables.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def reference():
    spec = importlib.util.spec_from_file_location("verify_l1c_e5a_tables", ICD_DIR / "verify_l1c_e5a_tables.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _hex_of_chips(signs: np.ndarray) -> str:
    bits = "".join("1" if s < 0 else "0" for s in signs)
    return "".join(format(int(bits[i : i + 4], 2), "X") for i in range(0, len(bits), 4))


# --------------------------------------------------------------------------
# The ICD tables
# --------------------------------------------------------------------------

@pytest.mark.parametrize("kind, generate", [("I", gal_e5a.generate_data_code), ("Q", gal_e5a.generate_pilot_code)])
def test_first_24_chips_match_the_icd_for_all_50_codes(tables, kind, generate):
    starts = tables["e5a_primary"]["register2_start_octal_and_first24_hex"][kind]
    for prn in ALL_PRNS:
        assert _hex_of_chips(generate(prn)[:24]) == starts[str(prn)][1], (kind, prn)


def test_the_icd_test_vectors_of_the_note():
    """The five vectors printed in the notes (Part 2.2)."""
    assert _hex_of_chips(gal_e5a.generate_data_code(1)[:24]) == "3CEA9D"
    assert _hex_of_chips(gal_e5a.generate_data_code(2)[:24]) == "9D8CF1"
    assert _hex_of_chips(gal_e5a.generate_data_code(50)[:24]) == "A5029C"
    assert _hex_of_chips(gal_e5a.generate_pilot_code(1)[:24]) == "515537"
    assert _hex_of_chips(gal_e5a.generate_pilot_code(2)[:24]) == "D67539"


def test_every_chip_equals_the_reference_generator(tables, reference):
    starts = tables["e5a_primary"]["register2_start_octal_and_first24_hex"]
    for kind, generate in (("I", gal_e5a.generate_data_code), ("Q", gal_e5a.generate_pilot_code)):
        for prn in (1, 2, 13, 26, 36, 50):
            expected = 1 - 2 * reference.e5a_primary_code(starts[kind][str(prn)][0]).astype(np.int8)
            assert np.array_equal(generate(prn), expected), (kind, prn)


def test_the_cs20_secondary_code_is_the_icd_word():
    code = gal_e5a.data_secondary_code()
    assert len(code) == 20
    assert "".join("1" if c < 0 else "0" for c in code) == "10000100001011101001"


def test_cs100_codes_equal_their_hex_strings(tables):
    for prn in ALL_PRNS:
        code = gal_e5a.generate_pilot_secondary_code(prn)
        text = tables["secondary"]["CS100_1_to_100_hex"][str(prn)]
        assert len(code) == 100
        assert _hex_of_chips(code) == text[:25], prn  # 25 hex digits = 100 chips exactly


def test_the_cs100_codes_are_all_different():
    assert len({gal_e5a.generate_pilot_secondary_code(p).tobytes() for p in ALL_PRNS}) == 50


# --------------------------------------------------------------------------
# Shape and statistics
# --------------------------------------------------------------------------

@pytest.mark.parametrize("generate", [gal_e5a.generate_data_code, gal_e5a.generate_pilot_code])
def test_codes_have_the_right_shape_and_values(generate):
    for prn in ALL_PRNS:
        chips = generate(prn)
        assert chips.dtype == np.int8 and len(chips) == 10230
        assert set(np.unique(chips)) == {-1, 1}


def test_codes_are_read_only_so_a_caller_cannot_change_the_cache():
    with pytest.raises(ValueError):
        gal_e5a.generate_data_code(1)[0] = 0


@pytest.mark.parametrize("generate", [gal_e5a.generate_data_code, gal_e5a.generate_pilot_code])
def test_codes_have_low_correlation(generate):
    spectra = [np.fft.fft(generate(p).astype(float)) for p in ALL_PRNS]
    for spectrum in spectra[:10]:
        auto = np.abs(np.fft.ifft(spectrum * np.conj(spectrum)))
        assert auto[0] == pytest.approx(10230.0)
        assert auto[1:].max() < 0.1 * 10230
    for i in range(0, 50, 7):
        for j in range(i + 1, 50, 9):
            assert np.abs(np.fft.ifft(spectra[i] * np.conj(spectra[j]))).max() < 0.1 * 10230


def test_codes_of_the_two_components_differ_for_every_prn():
    for prn in ALL_PRNS:
        a = gal_e5a.generate_data_code(prn).astype(float)
        b = gal_e5a.generate_pilot_code(prn).astype(float)
        assert abs(a @ b) < 0.1 * len(a)


def test_pilot_secondary_codes_are_nearly_balanced():
    for prn in ALL_PRNS:
        assert abs(int(gal_e5a.generate_pilot_secondary_code(prn).sum())) <= 40


# --------------------------------------------------------------------------
# Errors and constants
# --------------------------------------------------------------------------

@pytest.mark.parametrize("prn", [0, 51, -3])
def test_a_prn_outside_1_to_50_is_refused(prn):
    for generate in (gal_e5a.generate_data_code, gal_e5a.generate_pilot_code, gal_e5a.generate_pilot_secondary_code):
        with pytest.raises(ValueError, match="PRN"):
            generate(prn)


def test_a_broken_table_file_gives_a_clear_error(tmp_path, monkeypatch):
    gal_e5a.load_code_tables.cache_clear()
    monkeypatch.setattr(gal_e5a, "CODE_TABLES_PATH", tmp_path / "absent.json")
    try:
        with pytest.raises(RuntimeError, match="cannot read"):
            gal_e5a.load_code_tables()
    finally:
        gal_e5a.load_code_tables.cache_clear()


def test_the_constants_follow_the_icd():
    assert gal_e5a.CARRIER_FREQ_HZ == 1176.45e6
    assert gal_e5a.CODE_RATE_CHIPS_PER_SEC / gal_e5a.PRIMARY_CODE_LENGTH == 1000.0  # 1 ms primary period
    # One F/NAV symbol (20 ms) is exactly one CS20 pass of 20 primary periods.
    assert gal_e5a.DATA_SECONDARY_LENGTH * gal_e5a.PRIMARY_PERIOD_MS == gal_e5a.DATA_SYMBOL_PERIOD_MS == 1000 // gal_e5a.DATA_SYMBOL_RATE_SPS
