"""
GPS L1C spreading codes, checked against IS-GPS-800J itself.

The code *tables* are the one part of this that cannot be derived from first
principles -- they are 210 rows of Weil indices, insertion indices and LFSR seeds
transcribed out of a PDF, and a single wrong digit produces a code that still looks
like a code, still correlates against itself, and simply never acquires a real
satellite.

IS-GPS-800J anticipates that and publishes, for every PRN, the first and last 24
chips of each ranging code and the first and last 11 bits of each overlay code, in
octal.  `tests/data/is_gps_800j_l1c_checksums.json` holds those values and this
module reproduces every one of them: 420 ranging codes and 210 overlay codes.

That check also pins the two conventions the ICD states in prose and the tables
only imply -- the direction of the LFSR recurrence, and the fact that the expansion
sequence is *inserted* rather than overwriting seven chips.
"""

from __future__ import annotations

import json
import pathlib

import numpy as np
import pytest

import gnss_tools.signals.gps_l1c as gps_l1c

CHECKSUMS = json.loads(
    (pathlib.Path(__file__).parent / "data" / "is_gps_800j_l1c_checksums.json").read_text()
)
PRNS = range(1, gps_l1c.NUM_PRNS + 1)


def _octal(bits: np.ndarray) -> str:
    """Pack bits MSB-first into octal, as the ICD tables present them."""
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return f"{value:0{len(bits) // 3}o}"


# ---------------------------------------------------------------------------
# Against the specification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prn", PRNS)
def test_ranging_codes_match_the_published_first_and_last_chips(prn):
    """IS-GPS-800J Tables 3.2-2 and 6.3-1, both components, every PRN."""
    expected_p_init, expected_p_final, expected_d_init, expected_d_final = CHECKSUMS[
        "ranging"
    ][str(prn)]

    pilot = gps_l1c.get_GPS_L1CP_code_sequence(prn)
    data = gps_l1c.get_GPS_L1CD_code_sequence(prn)

    assert _octal(pilot[:24]) == expected_p_init
    assert _octal(pilot[-24:]) == expected_p_final
    assert _octal(data[:24]) == expected_d_init
    assert _octal(data[-24:]) == expected_d_final


@pytest.mark.parametrize("prn", PRNS)
def test_overlay_codes_match_the_published_first_and_last_bits(prn):
    """
    IS-GPS-800J Tables 3.2-3 and 6.3-2.

    The final 11 bits are the stronger half by far: they are what the LFSR lands on
    after 1800 clocks, so they confirm the recurrence direction and the tap set, not
    merely the seed.
    """
    expected_init, expected_final = CHECKSUMS["overlay"][str(prn)]
    overlay = gps_l1c.get_GPS_L1CO_overlay_sequence(prn)

    # 11 bits are presented as a 4-digit (12-bit) octal with a leading zero.
    assert _octal(np.concatenate([[0], overlay[:11]])) == expected_init
    assert _octal(np.concatenate([[0], overlay[-11:]])) == expected_final


# ---------------------------------------------------------------------------
# Shape, and the bug the shape used to hide
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prn", PRNS)
def test_every_ranging_code_is_10230_chips(prn):
    """
    The regression this guards is specific.  The previous implementation built the
    length-10223 Weil code and returned it, never applying the expansion sequence --
    seven chips short of a code period, and therefore never alignable with a real
    satellite for more than a few milliseconds.
    """
    assert len(gps_l1c.get_GPS_L1CP_code_sequence(prn)) == gps_l1c.CODE_LENGTH == 10230
    assert len(gps_l1c.get_GPS_L1CD_code_sequence(prn)) == 10230


def test_the_expansion_sequence_is_inserted_not_overwritten():
    """
    `p` says the expansion sequence goes *before* the p-th Weil value, so the Weil
    code either side of the splice must survive intact.  Overwriting seven chips
    instead would give a code of the right length that is wrong from p onward.
    """
    prn = 1
    weil = gps_l1c.generate_weil_code_sequence(gps_l1c.L1CP_WEIL_INDEX[prn - 1])
    p = gps_l1c.L1CP_INSERTION_INDEX[prn - 1]
    code = gps_l1c.get_GPS_L1CP_code_sequence(prn)

    np.testing.assert_array_equal(code[: p - 1], weil[: p - 1])
    np.testing.assert_array_equal(code[p - 1 : p + 6], gps_l1c.EXPANSION_SEQUENCE)
    np.testing.assert_array_equal(code[p + 6 :], weil[p - 1 :])


@pytest.mark.parametrize("prn", PRNS)
def test_every_overlay_is_1800_bits(prn):
    assert len(gps_l1c.get_GPS_L1CO_overlay_sequence(prn)) == gps_l1c.OVERLAY_LENGTH


def test_sequences_are_zero_one_int8():
    """
    Matching `gps_l1ca` and `gps_l2c`, so `utils.signal_interfaces` applies the same
    `1 - 2 * seq` mapping to every signal.  `gps_l5`'s float64 getters are the odd
    ones out and the reason `CodeComponent` rejects non-int8 sequences at all.
    """
    for sequence in (
        gps_l1c.get_GPS_L1CP_code_sequence(1),
        gps_l1c.get_GPS_L1CD_code_sequence(1),
        gps_l1c.get_GPS_L1CO_overlay_sequence(1),
    ):
        assert sequence.dtype == np.int8
        assert set(np.unique(sequence)) <= {0, 1}


# ---------------------------------------------------------------------------
# Properties a usable ranging code has to have
# ---------------------------------------------------------------------------


def test_the_legendre_sequence_is_the_quadratic_residues_with_zero_excluded():
    """
    L(0) = 0 is stated as a special case even though 0 is trivially a residue.
    Taking the general rule instead flips one chip of every L1C code for every PRN.
    """
    legendre = gps_l1c.generate_legendre_sequence()
    assert len(legendre) == 10223
    assert legendre[0] == 0

    residues = {(x * x) % 10223 for x in range(1, 10223)}
    assert set(np.flatnonzero(legendre)) == residues
    # A prime modulus splits the non-zero elements evenly into residues and
    # non-residues, so the sequence is very nearly balanced.
    assert legendre.sum() == (10223 - 1) // 2


@pytest.mark.parametrize("prn", [1, 7, 32, 63, 64, 137, 210])
def test_ranging_codes_are_nearly_balanced(prn):
    """A spreading code with a DC bias leaks power into the carrier."""
    for code in (
        gps_l1c.get_GPS_L1CP_code_sequence(prn),
        gps_l1c.get_GPS_L1CD_code_sequence(prn),
    ):
        assert abs((1 - 2 * code.astype(int)).sum()) < 100  # of 10230


def test_codes_are_distinct_across_prns_and_components():
    """
    The whole point of the per-PRN tables.  A transcription error that duplicated a
    row would show up here rather than as an unexplained acquisition failure.
    """
    seen = {}
    for prn in PRNS:
        for name, code in (
            ("L1CP", gps_l1c.get_GPS_L1CP_code_sequence(prn)),
            ("L1CD", gps_l1c.get_GPS_L1CD_code_sequence(prn)),
        ):
            key = code.tobytes()
            assert key not in seen, f"{name} PRN {prn} duplicates {seen[key]}"
            seen[key] = f"{name} PRN {prn}"


@pytest.mark.parametrize("prn", [1, 7, 32, 63])
def test_cross_correlation_stays_well_below_the_peak(prn):
    """
    Codes have to be near-orthogonal at every relative shift, or a strong satellite
    masks a weak one.  Checked against PRN 1 (or 2, for PRN 1 itself) over every
    shift via FFT, and against the code's own 10230-chip autocorrelation peak.
    """
    other = 2 if prn == 1 else 1
    a = 1 - 2 * gps_l1c.get_GPS_L1CP_code_sequence(prn).astype(float)
    b = 1 - 2 * gps_l1c.get_GPS_L1CP_code_sequence(other).astype(float)

    cross = np.abs(np.fft.ifft(np.fft.fft(a) * np.conj(np.fft.fft(b))))
    assert cross.max() < 0.1 * len(a)


@pytest.mark.parametrize("prn", [1, 32, 63, 64, 210])
def test_overlay_codes_are_a_truncated_maximal_sequence(prn):
    """
    2047 = 2**11 - 1 is the period of a maximal 11-stage LFSR, truncated to 1800.
    A maximal sequence has 1024 ones to 1023 zeros; truncation leaves it close to
    balanced, and a badly wrong tap set would not be.
    """
    overlay = gps_l1c.get_GPS_L1CO_overlay_sequence(prn)
    assert abs(2 * int(overlay.sum()) - len(overlay)) < 120  # of 1800


# ---------------------------------------------------------------------------
# Modulation parameters
# ---------------------------------------------------------------------------


def test_the_tmboc_pattern_is_four_of_every_thirty_three():
    """
    IS-GPS-800J 3.3: BOC(6,1) on the spreading symbols whose index mod 33 is
    0, 4, 6 or 29.  10230 / 33 = 310 exactly, so the pattern tiles the code period
    and the last block is not truncated.
    """
    assert gps_l1c.TMBOC_PATTERN_INDICES == (0, 4, 6, 29)
    assert gps_l1c.TMBOC_PATTERN.sum() == 4
    assert len(gps_l1c.TMBOC_PATTERN) == 33
    assert gps_l1c.CODE_LENGTH % gps_l1c.TMBOC_PATTERN_LENGTH == 0

    # The ICD spells the first few out: t = 0, 4, 6, 29, 33, 37, 39, 62, ...
    tiled = np.tile(gps_l1c.TMBOC_PATTERN, 2)
    assert np.flatnonzero(tiled).tolist() == [0, 4, 6, 29, 33, 37, 39, 62]


def test_the_power_split_matches_the_published_signal_levels():
    """
    Table 3.2-1 gives L1C at -157 dBW, L1CP at -158.25 and L1CD at -163.  Those are
    75% and 25% of the total, and they have to sum to it.
    """
    assert gps_l1c.L1CP_POWER_FRACTION == pytest.approx(10 ** (-1.25 / 10), abs=1e-3)
    assert gps_l1c.L1CD_POWER_FRACTION == pytest.approx(10 ** (-6.0 / 10), abs=2e-3)
    assert gps_l1c.L1CD_POWER_FRACTION + gps_l1c.L1CP_POWER_FRACTION == 1.0


def test_the_overlay_period_is_one_bit_per_code_period():
    """18 s of overlay = 1800 bits x 10 ms, and 10 ms is one L1CP code period.
    That equality is what lets one counter drive the wipe-off."""
    code_period_ms = gps_l1c.CODE_LENGTH / gps_l1c.CODE_RATE * 1e3
    assert code_period_ms == gps_l1c.PRIMARY_PERIOD_MS == 10
    assert gps_l1c.OVERLAY_LENGTH * code_period_ms == 18_000
    assert 1e3 / gps_l1c.OVERLAY_RATE == code_period_ms
    # CNAV-2 symbols are the same 10 ms, so an epoch that fits one fits both.
    assert 1e3 / gps_l1c.DATA_SYMBOL_RATE == code_period_ms


def test_prn_bounds_are_enforced():
    for bad in (0, 211, -1):
        with pytest.raises(ValueError, match="PRN must be"):
            gps_l1c.generate_code_sequence_L1CP(bad)
