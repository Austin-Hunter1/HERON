"""
Tests for the signal-agnostic code topology model.

The critical property is `pattern_period_chips`: reducing a chip phase by
anything other than a full repetition of the multiplexed pattern lands the
components at different points in their own codes once the phase runs past a
code period.  That is the class of bug this model exists to make
unrepresentable.

Every component is stated on the signal's own chip axis and carries 0 where it
does not transmit, so the second thing these tests pin down is that the
zero-filling is what carries the multiplexing -- there is no separate rate or
offset that could disagree with it.
"""

from __future__ import annotations

import numpy as np
import pytest

import gnss_tools.signals.gps_l2c as gps_l2c

from utils.code_components import (
    Branch,
    CodeComponent,
    Subcarrier,
    SubcarrierKind,
    build_code_set,
    epl_delay_bins,
)

from . import synthetic


def _code(length: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (1 - 2 * rng.integers(0, 2, length)).astype(np.int8)


def _interleave(sequences: list[np.ndarray]) -> list[np.ndarray]:
    """Mirror of `signal_interfaces._interleave`, kept local so the tests do not
    depend on the GPS catalog to exercise the topology model."""
    stride = len(sequences)
    filled = []
    for offset, sequence in enumerate(sequences):
        padded = np.zeros(len(sequence) * stride, dtype=np.int8)
        padded[offset::stride] = sequence
        filled.append(padded)
    return filled


def _l2c_code_set():
    cm, cl = _interleave(list(synthetic.get_l2c_codes(1)))
    return build_code_set(
        [
            CodeComponent(name="CM", sequence=cm, branch=Branch.Q),
            CodeComponent(name="CL", sequence=cl, branch=Branch.Q),
        ]
    )


def test_single_component_period_is_code_length():
    code_set = build_code_set(
        [CodeComponent(name="CA", sequence=_code(1023), branch=Branch.Q)]
    )
    assert code_set.pattern_period_chips == 1023
    assert code_set.num_components == 1


def test_interleaved_period_matches_hand_derived_value():
    """
    L2C: CM (10230) and CL (767250) interleaved chip by chip, so each is twice
    that long once zero-filled.  The pattern repeats every 2 * 767250 chips,
    since 10230 divides 767250.
    """
    code_set = _l2c_code_set()
    assert code_set.pattern_period_chips == 2 * gps_l2c.CODE_LENGTH_L2CL
    assert code_set.pattern_period_chips == 1534500


def test_interleaving_lives_in_the_sequences():
    """
    CM occupies even chips and CL odd ones, and each is 0 on the other's -- the
    whole of what used to be `chips_per_component_chip` / `component_offset_chips`.
    """
    cm_raw, cl_raw = synthetic.get_l2c_codes(1)
    code_set = _l2c_code_set()
    cm = code_set.components[code_set.index_of("CM")].sequence
    cl = code_set.components[code_set.index_of("CL")].sequence

    assert len(cm) == 2 * len(cm_raw)
    np.testing.assert_array_equal(cm[0::2], cm_raw)
    np.testing.assert_array_equal(cm[1::2], 0)
    np.testing.assert_array_equal(cl[1::2], cl_raw)
    np.testing.assert_array_equal(cl[0::2], 0)
    # No chip is silent across the whole signal.  CM repeats 75 times inside one
    # CL period, so it has to be tiled up to the pattern period to compare.
    assert np.all((np.tile(cm, len(cl) // len(cm)) != 0) | (cl != 0))


def test_colocated_components_share_period():
    """L5 I/Q and L1C D/P sit at the same chips, separated by carrier phase."""
    code_set = build_code_set(
        [
            CodeComponent(name="I", sequence=_code(10230, 1), branch=Branch.I),
            CodeComponent(name="Q", sequence=_code(10230, 2), branch=Branch.Q),
        ]
    )
    assert code_set.pattern_period_chips == 10230
    assert code_set.num_components == 2


def test_wrap_code_phase_preserves_component_alignment():
    """
    Wrapping by the pattern period must leave every component's derived code index
    unchanged.  Wrapping by max(len) instead -- the original bug -- does not.
    """
    code_set = _l2c_code_set()
    period = code_set.pattern_period_chips
    for raw_phase in (0.0, 3.0, 20460.0, 1534499.0):
        wrapped = code_set.wrap_code_phase_chips(raw_phase + 5 * period)
        assert wrapped == pytest.approx(raw_phase % period)
        # same chip parity => same component is transmitting
        assert int(wrapped) % 2 == int(raw_phase) % 2


def test_flat_packing_indexes_each_component_correctly():
    """
    `component_code_start_indices[c]` is a base address into `codes_flat`; a component's own
    chip index is a displacement from it.  The correlator adds the two, so this
    pins down that base + displacement recovers the original sequence.
    """
    a, b = _code(16, 1), _code(48, 2)
    code_set = build_code_set(
        [
            CodeComponent("A", a, branch=Branch.I),
            CodeComponent("B", b, branch=Branch.Q),
        ]
    )

    # Blocks must be laid end to end, otherwise the check below is vacuous.
    np.testing.assert_array_equal(code_set.component_code_start_indices, [0, len(a)])

    for i, expected in enumerate((a, b)):
        base = code_set.component_code_start_indices[i]
        length = code_set.component_code_lengths[i]
        np.testing.assert_array_equal(code_set.codes_flat[base : base + length], expected)
        # Element-wise, the form the kernel actually uses.
        for chip in range(length):
            assert code_set.codes_flat[base + chip] == expected[chip]


def test_index_of_and_metadata():
    code_set = build_code_set(
        [
            CodeComponent("D", _code(64, 1), branch=Branch.I, power_weight=0.25),
            CodeComponent("P", _code(64, 2), branch=Branch.Q, power_weight=0.75),
        ]
    )
    assert code_set.index_of("P") == 1
    np.testing.assert_allclose(code_set.power_weights, [0.25, 0.75])
    assert not code_set.has_subcarrier
    assert not code_set.has_overlay
    with pytest.raises(KeyError):
        code_set.index_of("missing")


def test_branches_are_recorded_and_compared():
    """
    `share_branch` is the operational question: components on one branch can hand
    the carrier loop between them, components in quadrature cannot.
    """
    l2c = _l2c_code_set()
    assert l2c.branches == (Branch.Q, Branch.Q)
    assert l2c.share_branch("CM", "CL")

    l5 = build_code_set(
        [
            CodeComponent("I", _code(32, 1), branch=Branch.I),
            CodeComponent("Q", _code(32, 2), branch=Branch.Q),
        ]
    )
    assert not l5.share_branch("I", "Q")
    assert l5.share_branch("Q")


def test_overlay_and_subcarrier_flags():
    boc = Subcarrier(kind=SubcarrierKind.BOC_SIN, rate_hz=1.023e6)
    code_set = build_code_set(
        [
            CodeComponent("I", _code(32, 1), branch=Branch.I, overlay=_code(10, 3)),
            CodeComponent("Q", _code(32, 2), branch=Branch.Q, subcarrier=boc),
        ],
        chip_rate_hz=1.023e6,
    )
    assert code_set.has_overlay
    assert code_set.has_subcarrier
    # BOC(1,1): one subcarrier period per chip, so two sub-chips.  The component
    # without a subcarrier carries 0, which is how the kernel skips it.
    assert code_set.subcarrier_sub_chips_per_chip.tolist() == [0, 2]


def test_rejects_float_sequences():
    """gnss_tools' L5 getters return float64 0/1; catching that early is the point."""
    with pytest.raises(ValueError, match="int8"):
        CodeComponent(name="I", sequence=np.zeros(10, dtype=np.float64), branch=Branch.I)


def test_rejects_all_zero_sequence():
    """A component that is 0 everywhere transmits nothing and is always a mistake."""
    with pytest.raises(ValueError, match="all zeros"):
        CodeComponent(name="X", sequence=np.zeros(8, dtype=np.int8), branch=Branch.I)


def test_rejects_duplicate_names():
    with pytest.raises(ValueError, match="unique"):
        build_code_set(
            [
                CodeComponent("A", _code(8, 1), branch=Branch.I),
                CodeComponent("A", _code(8, 2), branch=Branch.I),
            ]
        )


def test_rejects_uncovered_signal_chips():
    """
    Two components both on even chips leave every odd chip silent, so the
    correlator would skip signal energy there.  The check reads the sequences,
    so it catches the mistake however it was produced.
    """
    a, _ = _interleave([_code(8, 1), _code(8, 2)])
    b, _ = _interleave([_code(8, 3), _code(8, 4)])
    with pytest.raises(ValueError, match="carry no component"):
        build_code_set(
            [
                CodeComponent("A", a, branch=Branch.I),
                CodeComponent("B", b, branch=Branch.I),
            ]
        )


def test_partial_coverage_is_allowed_when_asked_for():
    """The ambiguity search correlates L2 CL alone, on odd chips -- deliberately
    a partial view of the signal."""
    _, cl = _interleave([_code(8, 1), _code(8, 2)])
    code_set = build_code_set(
        [CodeComponent("CL", cl, branch=Branch.Q)], allow_partial_coverage=True
    )
    assert code_set.num_components == 1


def test_tmboc_requires_pattern():
    with pytest.raises(ValueError, match="TMBOC requires"):
        Subcarrier(kind=SubcarrierKind.TMBOC, rate_hz=1.023e6)


def test_epl_delay_bins_order_is_early_prompt_late():
    assert epl_delay_bins(0.5) == (0.5, 0.0, -0.5)
