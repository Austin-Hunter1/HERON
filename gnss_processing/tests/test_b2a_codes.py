"""
Tests for the BeiDou B2a spreading codes (`utils.bds_b2a`) and for the
`BdsB2a` signal definition.

Three lines of defence:

* the ICD tables -- first and last 24 chips of all 63 data, pilot and pilot
  secondary codes, and the PRN tables in the module, against
  ``docs/icd/bds_b2a_code_tables.json``;
* a slow, literal copy of the MATLAB shift register, which must give the same
  chips as the fast integer code;
* code statistics (length, values, balance, correlation) and error handling.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from utils import bds_b2a
from utils.code_components import Branch
from utils.signal_interfaces import SIGNALS, BdsB2a, GpsL1CA, GpsL5, build_signals

ICD_TABLES_PATH = Path(__file__).parent.parent / "docs" / "icd" / "bds_b2a_code_tables.json"
ALL_PRNS = range(1, 64)

# Bounds for the statistics tests.  They are set from the values measured on
# 2026-10-14 (largest |sum| 220, largest periodic autocorrelation sidelobe
# 362, largest cross-correlation 480, for a peak of 10230), with margin.
MAX_CODE_IMBALANCE = 300
MAX_AUTOCORRELATION_SIDELOBE = 500
MAX_CROSS_CORRELATION = 600


@pytest.fixture(scope="module")
def icd_tables() -> dict:
    """The ICD code tables, read once per test module."""
    with ICD_TABLES_PATH.open() as handle:
        return json.load(handle)


def _octal_of_chips(signs: np.ndarray) -> str:
    """Chips (+1/-1) to octal digits, three chips per digit, first chip first."""
    bits = "".join("1" if sign < 0 else "0" for sign in signs)
    return "".join(str(int(bits[i : i + 3], 2)) for i in range(0, len(bits), 3))


def _matlab_style_code(g1_taps, g2_taps, reg2_start: str) -> np.ndarray:
    """
    Slow copy of the MATLAB generator (`generateB2aDataCode.m`, `...PilotCode.m`).

    It keeps each register as a list, shifts it one place per chip, and
    restarts register 1 after chip 8190.  It shares no code with
    `bds_b2a`, so agreement is a real check.
    """
    reg1 = [1] * 13
    reg2 = [int(char) for char in reg2_start]
    chips = []
    for chip in range(10230):
        if chip == 8190:
            reg1 = [1] * 13
        chips.append(reg1[12] ^ reg2[12])
        fb1 = 0
        for tap in g1_taps:
            fb1 ^= reg1[tap - 1]
        fb2 = 0
        for tap in g2_taps:
            fb2 ^= reg2[tap - 1]
        reg1 = [fb1] + reg1[:-1]
        reg2 = [fb2] + reg2[:-1]
    return np.array([1 - 2 * bit for bit in chips], dtype=np.int8)


# --- (a) ICD tables --------------------------------------------------------

@pytest.mark.parametrize("prn", ALL_PRNS)
def test_primary_and_secondary_codes_match_icd_octal(prn, icd_tables):
    """First and last 24 chips of every code equal ICD Tables 5-2 to 5-4."""
    cases = {
        "primary_data": bds_b2a.generate_data_code(prn),
        "primary_pilot": bds_b2a.generate_pilot_code(prn),
        "pilot_secondary": bds_b2a.generate_pilot_secondary_code(prn),
    }
    for table, code in cases.items():
        entry = icd_tables[table][str(prn)]
        assert _octal_of_chips(code[:24]) == entry["first24_octal"], (table, prn)
        assert _octal_of_chips(code[-24:]) == entry["last24_octal"], (table, prn)


def test_module_tables_equal_icd_fixture(icd_tables):
    """The PRN tables typed into the module equal the ICD fixture."""
    for prn in ALL_PRNS:
        key = str(prn)
        assert bds_b2a._DATA_REG2_INIT[prn - 1] == icd_tables["primary_data"][key]["reg2_init"]
        assert bds_b2a._PILOT_REG2_INIT[prn - 1] == icd_tables["primary_pilot"][key]["reg2_init"]
        secondary = icd_tables["pilot_secondary"][key]
        assert bds_b2a._PILOT_SECONDARY_WP[prn - 1] == (secondary["w"], secondary["p"])


def test_data_secondary_code_is_00010():
    """Logic 0 is +1 and logic 1 is -1, so 00010 is [+1, +1, +1, -1, +1]."""
    assert bds_b2a.data_secondary_code().tolist() == [1, 1, 1, -1, 1]


# --- (b) literal MATLAB shift register ------------------------------------

@pytest.mark.parametrize("prn", [1, 2, 17, 32, 33, 62, 63])
def test_fast_codes_equal_matlab_style_registers(prn):
    """The fast integer generator gives the same chips as the list-based one."""
    data = _matlab_style_code(
        bds_b2a._DATA_G1_TAPS, bds_b2a._DATA_G2_TAPS, bds_b2a._DATA_REG2_INIT[prn - 1]
    )
    pilot = _matlab_style_code(
        bds_b2a._PILOT_G1_TAPS, bds_b2a._PILOT_G2_TAPS, bds_b2a._PILOT_REG2_INIT[prn - 1]
    )
    np.testing.assert_array_equal(bds_b2a.generate_data_code(prn), data)
    np.testing.assert_array_equal(bds_b2a.generate_pilot_code(prn), pilot)


# --- (c) statistics --------------------------------------------------------

def test_code_shapes_and_values():
    """Every code has the ICD length, int8 type, and only +1 and -1."""
    for prn in ALL_PRNS:
        for code in (bds_b2a.generate_data_code(prn), bds_b2a.generate_pilot_code(prn)):
            assert code.shape == (bds_b2a.PRIMARY_CODE_LENGTH,)
            assert code.dtype == np.int8
            assert set(np.unique(code)) <= {-1, 1}
        secondary = bds_b2a.generate_pilot_secondary_code(prn)
        assert secondary.shape == (bds_b2a.PILOT_SECONDARY_LENGTH,)
        assert secondary.dtype == np.int8
        assert set(np.unique(secondary)) <= {-1, 1}


def test_codes_are_cached_and_read_only():
    """A caller cannot change the cached code by accident."""
    code = bds_b2a.generate_data_code(5)
    assert code is bds_b2a.generate_data_code(5)
    with pytest.raises(ValueError):
        code[0] = -code[0]


def test_all_126_primary_codes_are_different():
    """No two primary codes are equal, and data never equals pilot."""
    codes = [bds_b2a.generate_data_code(p).tobytes() for p in ALL_PRNS]
    codes += [bds_b2a.generate_pilot_code(p).tobytes() for p in ALL_PRNS]
    assert len(set(codes)) == 126


def test_primary_codes_are_balanced():
    """The count of +1 and -1 chips differs by little."""
    for prn in ALL_PRNS:
        for code in (bds_b2a.generate_data_code(prn), bds_b2a.generate_pilot_code(prn)):
            assert abs(int(code.sum(dtype=np.int64))) <= MAX_CODE_IMBALANCE


def _spectra(generator) -> np.ndarray:
    return np.array([np.fft.fft(generator(p).astype(float)) for p in ALL_PRNS])


@pytest.mark.parametrize("generator", [bds_b2a.generate_data_code, bds_b2a.generate_pilot_code])
def test_periodic_correlation_is_low(generator):
    """
    Autocorrelation has a clear peak, and every cross-correlation is small.

    Periodic correlation is the right measure: the receiver sees the code
    repeat every millisecond.
    """
    spectra = _spectra(generator)
    n = bds_b2a.PRIMARY_CODE_LENGTH
    for i, first in enumerate(spectra):
        auto = np.fft.ifft(first * np.conj(first)).real
        assert auto[0] == pytest.approx(n)
        assert np.abs(auto[1:]).max() <= MAX_AUTOCORRELATION_SIDELOBE
        for second in spectra[i + 1 :]:
            cross = np.fft.ifft(first * np.conj(second)).real
            assert np.abs(cross).max() <= MAX_CROSS_CORRELATION


def test_data_and_pilot_of_one_prn_do_not_correlate():
    """The data and pilot code of one satellite are far apart."""
    for prn in ALL_PRNS:
        first = np.fft.fft(bds_b2a.generate_data_code(prn).astype(float))
        second = np.fft.fft(bds_b2a.generate_pilot_code(prn).astype(float))
        cross = np.fft.ifft(first * np.conj(second)).real
        assert np.abs(cross).max() <= MAX_CROSS_CORRELATION


def test_pilot_secondary_codes_are_different_and_roughly_balanced():
    """The 63 pilot overlays differ, and each is close to balanced."""
    codes = [bds_b2a.generate_pilot_secondary_code(p) for p in ALL_PRNS]
    assert len({code.tobytes() for code in codes}) == 63
    assert all(abs(int(code.sum())) <= 30 for code in codes)  # measured max: 22


# --- (d) error handling ----------------------------------------------------

@pytest.mark.parametrize("prn", [0, 64, -1, 1000])
@pytest.mark.parametrize(
    "generator",
    [
        bds_b2a.generate_data_code,
        bds_b2a.generate_pilot_code,
        bds_b2a.generate_pilot_secondary_code,
    ],
)
def test_bad_prn_raises_clear_error(generator, prn):
    """PRN 0, 64 and other values outside 1 to 63 raise ValueError."""
    with pytest.raises(ValueError, match="1 to 63"):
        generator(prn)


def test_non_integer_prn_raises():
    with pytest.raises(ValueError, match="1 to 63"):
        bds_b2a.generate_data_code(1.5)


# --- the BdsB2a signal definition -----------------------------------------

def test_bds_b2a_is_registered():
    assert SIGNALS["BDS_B2A"] is BdsB2a


def test_bds_b2a_builds_for_prn_1_to_63():
    """`SIGNALS["BDS_B2A"]` builds for every PRN, with ids C01 to C63."""
    signals = build_signals(SIGNALS["BDS_B2A"])
    assert list(signals) == [f"C{prn:02d}" for prn in ALL_PRNS]
    for prn, (signal_id, signal) in zip(ALL_PRNS, signals.items()):
        assert signal.prn == prn
        assert signal.signal_id == signal_id


def test_bds_b2a_structure():
    """Components, branches, overlays and timing follow the ICD."""
    signal = BdsB2a(7)
    assert signal.component_names == ("B2aD", "B2aP")
    data, pilot = signal.code_set.components
    assert (data.branch, pilot.branch) == (Branch.I, Branch.Q)
    assert data.symbol_period_ms == 5
    assert pilot.symbol_period_ms is None
    assert data.overlay.tolist() == [1, 1, 1, -1, 1]
    assert len(pilot.overlay) == 100
    assert data.power_weight == pilot.power_weight == 1.0
    assert BdsB2a.primary_period_ms == 1
    assert signal.overlay_period_ms == 100
    assert BdsB2a.carrier_freq_hz == 1176.45e6
    assert BdsB2a.tracking_code_rate_chips_per_sec == 10.23e6
    assert BdsB2a.link.nominal_freq_hz == BdsB2a.carrier_freq_hz


def test_gps_signals_keep_their_ids_and_default_prns():
    """The new class attributes leave the GPS behaviour as it was."""
    assert GpsL5.sat_id_prefix == "G"
    assert list(build_signals(GpsL1CA)) == [f"G{prn:02d}" for prn in range(1, 33)]
    assert list(build_signals(GpsL5, prns=[3, 9])) == ["G03", "G09"]
