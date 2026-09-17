"""
Correlator unit tests.

These pin down the properties a multi-component correlator must satisfy, and in
particular the ones that were violated before the code topology became data:

  - an aligned replica recovers the full sample count,
  - a +/-0.5 chip offset recovers roughly half,
  - alignment survives running past each component's code period,
  - each component lands in its own accumulator,
  - a chip index just below zero maps to the *previous* code period.

That last one is not an edge case in practice.  Correlation intervals are aligned
to integer ms of code phase and L1 C/A's code period is 1 ms, so every interval
starts at code phase ~ 0 chips and the late bin is negative on every epoch.  Truncating
toward zero (rather than flooring) read the wrong chip every time, and for L2C it
read the wrong *component*, since the chip's parity selects CM or CL.

Expected values are built from explicitly floored replicas rather than from
another implementation, so these stand on their own.
"""

from __future__ import annotations

import numpy as np
import pytest

import gnss_tools.signals.gps_l1ca as gps_l1ca
import gnss_tools.signals.gps_l2c as gps_l2c

from utils.bpsk_correlation import correlate__multicomponent
from utils.code_components import Branch, CodeComponent, build_code_set

from . import synthetic

SAMP_RATE = 5e6
EPL_BINS = np.array([0.5, 0.0, -0.5])  # early, prompt, late


def _interleave(sequences):
    """CM on even chips, CL on odd, zero-filled apart -- what the signal catalog
    hands the correlator."""
    stride = len(sequences)
    filled = []
    for offset, sequence in enumerate(sequences):
        padded = np.zeros(len(sequence) * stride, dtype=np.int8)
        padded[offset::stride] = sequence
        filled.append(padded)
    return filled


def _l1ca_code_set(prn: int = 1):
    return build_code_set(
        [CodeComponent(name="CA", sequence=synthetic.get_l1ca_code(prn), branch=Branch.Q)]
    )


def _l2c_code_set(prn: int = 1):
    cm, cl = _interleave(list(synthetic.get_l2c_codes(prn)))
    return build_code_set(
        [
            CodeComponent(name="CM", sequence=cm, branch=Branch.Q),
            CodeComponent(name="CL", sequence=cl, branch=Branch.Q),
        ]
    )


def _correlate(samples, code_set, code_rate, code_phase_chips, bins, carr_phase=0.0, doppler=0.0):
    out = np.zeros((len(bins), code_set.num_components), dtype=np.complex64)
    correlate__multicomponent(
        samples, SAMP_RATE, carr_phase, doppler, code_set,
        code_rate, code_phase_chips, np.asarray(bins, dtype=np.float64), out,
    )
    return out


def _noise(seed: int, n: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (rng.normal(0, 1, n) + 1j * rng.normal(0, 1, n)).astype(np.complex64)


def _l1ca_signal(prn, duration_ms, code_phase_chips):
    n = int(SAMP_RATE * duration_ms * 1e-3)
    t = np.arange(n) / SAMP_RATE
    code = synthetic.get_l1ca_code(prn)
    chips = code_phase_chips + t * gps_l1ca.CODE_RATE
    return code[chips.astype(np.int64) % len(code)].astype(np.complex64), n


def _l2c_signal(prn, duration_ms, code_phase_chips):
    n = int(SAMP_RATE * duration_ms * 1e-3)
    t = np.arange(n) / SAMP_RATE
    cm, cl = synthetic.get_l2c_codes(prn)
    k = (code_phase_chips + t * gps_l2c.CODE_RATE_L2CLM).astype(np.int64)
    chips = np.where(k % 2 == 0, cm[(k // 2) % len(cm)], cl[(k // 2) % len(cl)])
    return chips.astype(np.complex64), n


def test_l1ca_aligned_replica_recovers_all_samples():
    samples, n = _l1ca_signal(prn=1, duration_ms=1, code_phase_chips=378.5)
    result = _correlate(samples, _l1ca_code_set(), gps_l1ca.CODE_RATE, 378.5, [0.0])
    assert abs(result[0, 0]) == pytest.approx(n, rel=1e-3)


def test_l1ca_half_chip_offset_halves_correlation():
    samples, n = _l1ca_signal(prn=1, duration_ms=1, code_phase_chips=378.5)
    early, _, late = _correlate(samples, _l1ca_code_set(), gps_l1ca.CODE_RATE, 378.5, EPL_BINS)[:, 0]
    assert abs(early) == pytest.approx(n / 2, rel=0.05)
    assert abs(late) == pytest.approx(n / 2, rel=0.05)


def test_l1ca_wrong_prn_does_not_correlate():
    samples, n = _l1ca_signal(prn=1, duration_ms=1, code_phase_chips=378.5)
    result = _correlate(samples, _l1ca_code_set(prn=7), gps_l1ca.CODE_RATE, 378.5, [0.0])
    assert abs(result[0, 0]) < 0.1 * n


@pytest.mark.parametrize(
    "start_chip",
    [
        0,        # origin
        20460,    # one full CM period into the stream
        1534480,  # just short of the full interleaved period
        3,        # odd start: CL leads
    ],
)
def test_l2c_interleaved_recovers_both_components(start_chip):
    """
    Each component is indexed by the raw chip index into its own zero-filled
    sequence, and the stream repeats every lcm of those lengths -- 2 * 767250
    here, not 767250.  Wrapping by the shorter value collapsed correlation to
    the noise floor.
    """
    samples, n = _l2c_signal(prn=1, duration_ms=20, code_phase_chips=start_chip)
    result = _correlate(
        samples, _l2c_code_set(), gps_l2c.CODE_RATE_L2CLM, float(start_chip), [0.0]
    )
    # Each component occupies half the chips, so each recovers ~n/2.
    assert abs(result[0, 0]) == pytest.approx(n / 2, rel=1e-3), "CM did not correlate"
    assert abs(result[0, 1]) == pytest.approx(n / 2, rel=1e-3), "CL did not correlate"


def test_l2c_components_are_independent():
    """CM and CL must land in separate accumulators, not be summed or aliased."""
    samples, n = _l2c_signal(prn=1, duration_ms=20, code_phase_chips=0)
    cm, _ = _interleave(list(synthetic.get_l2c_codes(1)))
    _, wrong_cl = _interleave(list(synthetic.get_l2c_codes(7)))
    mismatched_set = build_code_set(
        [
            CodeComponent(name="CM", sequence=cm, branch=Branch.Q),
            CodeComponent(name="CL", sequence=wrong_cl, branch=Branch.Q),
        ]
    )

    matched = _correlate(samples, _l2c_code_set(), gps_l2c.CODE_RATE_L2CLM, 0.0, [0.0])
    mismatched = _correlate(samples, mismatched_set, gps_l2c.CODE_RATE_L2CLM, 0.0, [0.0])

    assert abs(matched[0, 0]) == pytest.approx(abs(mismatched[0, 0]), rel=1e-6), "CL leaked into CM"
    assert abs(mismatched[0, 1]) < 0.05 * n, "mismatched CL should not correlate"


def test_aligned_epoch_late_bin_reads_the_previous_code_period():
    """
    The case that actually matters in tracking, not a rare edge: at an aligned
    interval start the late bin is negative and must wrap to the previous period.
    """
    code = synthetic.get_l1ca_code(1)
    samples = _noise(seed=17, n=5000)
    code_phase, offset = 0.000504818182, -0.5  # a real aligned-interval start

    result = _correlate(samples, _l1ca_code_set(), gps_l1ca.CODE_RATE, code_phase, [offset])

    chips = code_phase + np.arange(len(samples)) * (gps_l1ca.CODE_RATE / SAMP_RATE) + offset
    indices = np.floor(chips).astype(np.int64) % len(code)
    assert indices[0] == len(code) - 1, "first sample should wrap to the previous period"
    np.testing.assert_allclose(
        result[0, 0], np.sum(samples * code[indices]), rtol=1e-5, atol=1e-3
    )


def test_interleaved_negative_chip_selects_the_correct_component():
    """
    For L2C the consequence is worse than an off-by-one chip: chip -1 is odd,
    so it belongs to CL.  Truncating to 0 would credit CM instead.
    """
    cm, cl = synthetic.get_l2c_codes(1)
    samples = _noise(seed=13, n=200)
    offset = -0.5

    result = _correlate(samples, _l2c_code_set(), gps_l2c.CODE_RATE_L2CLM, 0.0, [offset])

    chips = np.floor(
        np.arange(len(samples)) * (gps_l2c.CODE_RATE_L2CLM / SAMP_RATE) + offset
    ).astype(np.int64)
    assert chips[0] == -1 and chips[0] % 2 != 0, "first chip should be odd => CL"
    expected_cm = np.sum(np.where(chips % 2 == 0, cm[(chips // 2) % len(cm)], 0) * samples)
    expected_cl = np.sum(np.where(chips % 2 != 0, cl[(chips // 2) % len(cl)], 0) * samples)

    np.testing.assert_allclose(result[0, 0], expected_cm, rtol=1e-5, atol=1e-3)
    np.testing.assert_allclose(result[0, 1], expected_cl, rtol=1e-5, atol=1e-3)


@pytest.mark.parametrize("doppler_hz", [0.0, 1234.0, -2500.0])
def test_carrier_wipeoff_matches_a_direct_replica(doppler_hz):
    """Carrier phase and Doppler rotation are applied as an explicit conjugate mix."""
    code = synthetic.get_l1ca_code(1)
    samples = _noise(seed=5, n=2000)
    code_phase, carr_phase = 378.5, 0.125

    result = _correlate(
        samples, _l1ca_code_set(), gps_l1ca.CODE_RATE, code_phase, [0.0],
        carr_phase=carr_phase, doppler=doppler_hz,
    )

    n = len(samples)
    t = np.arange(n) / SAMP_RATE
    chips = code_phase + np.arange(n) * (gps_l1ca.CODE_RATE / SAMP_RATE)
    replica = code[np.floor(chips).astype(np.int64) % len(code)]
    carrier = np.exp(-2j * np.pi * (carr_phase + doppler_hz * t))
    np.testing.assert_allclose(
        result[0, 0], np.sum(samples * carrier * replica), rtol=1e-4, atol=1e-2
    )


def test_output_is_accumulated_not_overwritten():
    """A correlation interval can span several sample buffers."""
    samples, _ = _l1ca_signal(prn=1, duration_ms=1, code_phase_chips=378.5)
    code_set = _l1ca_code_set()
    half = len(samples) // 2
    chips_per_sample = gps_l1ca.CODE_RATE / SAMP_RATE

    whole = _correlate(samples, code_set, gps_l1ca.CODE_RATE, 378.5, [0.0])

    split = np.zeros((1, 1), dtype=np.complex64)
    correlate__multicomponent(
        samples[:half], SAMP_RATE, 0.0, 0.0, code_set,
        gps_l1ca.CODE_RATE, 378.5, np.array([0.0]), split,
    )
    correlate__multicomponent(
        samples[half:], SAMP_RATE, 0.0, 0.0, code_set,
        gps_l1ca.CODE_RATE, 378.5 + half * chips_per_sample, np.array([0.0]), split,
    )

    np.testing.assert_allclose(split[0, 0], whole[0, 0], rtol=1e-4, atol=1e-2)


def test_kernel_reads_each_component_from_its_own_block():
    """
    Guards the flat-buffer indexing: `component_code_start_indices[c] + component_chip`.

    Codes of different lengths are packed end to end, so component 1's chips live
    partway into the buffer.  Each code here carries a single distinctive symbol,
    so if the base address were dropped or misapplied the marker would surface on
    the wrong chip.

    With one unit sample and no carrier, the correlator output for a bin *is* the
    code symbol it selected, which makes the comparison exact.
    """
    length_a, length_b = 7, 11
    seq_a = np.ones(length_a, dtype=np.int8)
    seq_a[3] = -1  # A's only -1
    seq_b = -np.ones(length_b, dtype=np.int8)
    seq_b[5] = 1  # B's only +1
    filled_a, filled_b = _interleave([seq_a, seq_b])

    code_set = build_code_set(
        [
            CodeComponent("A", filled_a, branch=Branch.I),
            CodeComponent("B", filled_b, branch=Branch.Q),
        ]
    )
    assert code_set.component_code_start_indices.tolist() == [0, 2 * length_a]

    one_sample = np.ones(1, dtype=np.complex64)
    for chip in range(2 * length_a * length_b):
        out = np.zeros((1, 2), dtype=np.complex64)
        correlate__multicomponent(
            one_sample, 1.0, 0.0, 0.0, code_set, 1.0, float(chip), np.array([0.0]), out
        )
        for component, sequence in enumerate((filled_a, filled_b)):
            expected = int(sequence[chip % len(sequence)])
            assert int(np.real(out[0, component])) == expected, (
                f"chip {chip}, component {component}"
            )


def test_a_subcarrier_code_set_routes_to_the_subcarrier_kernel():
    """
    The two kernels are separate functions chosen by `has_subcarrier`, so the only
    thing that proves the dispatch is that the same samples give different answers.

    A BOC(1,1) replica against BPSK noise is not merely a different number: the
    subcarrier is odd-symmetric within each chip, so it removes roughly half the
    correlation a BPSK replica would have accumulated.  See `tests/test_subcarrier.py`
    for the kernel's own correctness.
    """
    from utils.code_components import Subcarrier, SubcarrierKind

    code = synthetic.get_l1ca_code(1)
    bpsk_set = build_code_set([CodeComponent(name="P", sequence=code, branch=Branch.Q)])
    boc_set = build_code_set(
        [
            CodeComponent(
                name="P",
                sequence=code,
                branch=Branch.Q,
                subcarrier=Subcarrier(kind=SubcarrierKind.BOC_SIN, rate_hz=1.023e6),
            )
        ],
        chip_rate_hz=gps_l1ca.CODE_RATE,
    )
    assert not bpsk_set.has_subcarrier
    assert boc_set.has_subcarrier

    samples = _noise(1, 4000)
    bpsk = _correlate(samples, bpsk_set, gps_l1ca.CODE_RATE, 0.0, [0.0])
    boc = _correlate(samples, boc_set, gps_l1ca.CODE_RATE, 0.0, [0.0])
    assert not np.allclose(bpsk, boc)
