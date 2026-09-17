"""
BOC / TMBOC subcarrier correlation.

A subcarrier is a square wave riding each chip, so unlike everything else in the
code topology its effect depends on *where inside a chip* a sample falls.  These
tests pin that down at three levels:

  - the sign the kernel applies at a given fractional chip position,
  - the shape that produces in the autocorrelation function, which is the whole
    reason BOC exists and the reason a BOC delay-lock loop needs a narrower
    early/late spacing than a BPSK one,
  - the arithmetic that has to stay identical to the BPSK kernel wherever no
    subcarrier is present.

Expected values are built from first principles -- `sign(sin(2*pi*f_s*t))` and
explicitly floored replicas -- rather than from another implementation, so these
stand on their own.  The ACF values in particular are the textbook ones for
BOC(1,1): 1 at zero delay, zero crossings at +/-1/3 chip, minima of -0.5 at
+/-0.5 chip, and back to zero at +/-1 chip.
"""

from __future__ import annotations

import numpy as np
import pytest

from utils.bpsk_correlation import correlate__multicomponent
from utils.code_components import (
    Branch,
    CodeComponent,
    Subcarrier,
    SubcarrierKind,
    build_code_set,
    subcarrier_signs,
)

CHIP_RATE = 1.023e6

# GPS L1C-P, IS-GPS-800: BOC(6,1) on 4 of every 33 chips, BOC(1,1) on the other 29.
TMBOC_PATTERN = np.zeros(33, dtype=np.int8)
TMBOC_PATTERN[[0, 4, 6, 29]] = 1


def _boc_set(sequence, rate_hz, chip_rate_hz=CHIP_RATE, kind=SubcarrierKind.BOC_SIN):
    return build_code_set(
        [
            CodeComponent(
                name="P",
                sequence=sequence,
                branch=Branch.I,
                subcarrier=Subcarrier(kind=kind, rate_hz=rate_hz),
            )
        ],
        chip_rate_hz=chip_rate_hz,
    )


_SAMP_RATE = 20 * CHIP_RATE


# ---------------------------------------------------------------------------
# The sign, sample by sample
# ---------------------------------------------------------------------------


def _signs_across_one_chip(code_set, samples_per_chip, chip=0):
    """The correlator's output for a single unit sample at each position in a chip.

    With one sample, no carrier and a +1 chip, the output *is* the sign the kernel
    applied, which makes the comparison exact rather than statistical.

    Samples sit at the MIDDLE of each interval.  Landing one exactly on a sub-chip
    boundary would make the expected value a coin toss decided by floating-point
    rounding, which says nothing about the kernel; a real sample never has that
    property either.
    """
    signs = []
    for k in range(samples_per_chip):
        out = np.zeros((1, 1), dtype=np.complex64)
        correlate__multicomponent(
            np.ones(1, dtype=np.complex64), 1.0, 0.0, 0.0, code_set,
            0.0, chip + (k + 0.5) / samples_per_chip, np.array([0.0]), out,
        )
        signs.append(int(np.real(out[0, 0])))
    return signs


@pytest.mark.parametrize(
    "subcarrier_rate_hz, sub_chips",
    [(CHIP_RATE, 2), (2 * CHIP_RATE, 4), (6 * CHIP_RATE, 12)],
)
def test_the_subcarrier_sign_is_the_sine_it_is_named_for(subcarrier_rate_hz, sub_chips):
    """
    Sine-phased BOC(n,1) is `sign(sin(2*pi*n*R_c*t))`: positive over the first
    half-period of the subcarrier, negative over the second, `2n` half-periods
    to a chip.  The kernel derives this from the fractional chip position alone,
    which is only well defined because the sub-chips divide the chip evenly.
    """
    code_set = _boc_set(np.ones(1, dtype=np.int8), subcarrier_rate_hz)
    assert code_set.subcarrier_sub_chips_per_chip.tolist() == [sub_chips]

    samples_per_chip = 4 * sub_chips
    expected = [
        1 if int((k + 0.5) / samples_per_chip * sub_chips) % 2 == 0 else -1
        for k in range(samples_per_chip)
    ]
    # Stated independently as the sign of the sine, at the middle of each sample.
    from_sine = [
        int(np.sign(np.sin(2.0 * np.pi * (sub_chips / 2.0) * (k + 0.5) / samples_per_chip)))
        for k in range(samples_per_chip)
    ]
    assert expected == from_sine

    assert _signs_across_one_chip(code_set, samples_per_chip) == expected


def test_a_boc_subcarrier_flips_sign_at_the_half_chip():
    """BOC(1,1) is the simplest case and worth stating outright: first half of the
    chip positive, second half negative."""
    code_set = _boc_set(np.ones(1, dtype=np.int8), CHIP_RATE)
    assert _signs_across_one_chip(code_set, 8) == [1, 1, 1, 1, -1, -1, -1, -1]


def test_the_subcarrier_rides_the_code_rather_than_replacing_it():
    """A -1 chip inverts the whole subcarrier over that chip; the two multiply."""
    code = np.array([1, -1], dtype=np.int8)
    code_set = _boc_set(code, CHIP_RATE)
    assert _signs_across_one_chip(code_set, 4, chip=0) == [1, 1, -1, -1]
    assert _signs_across_one_chip(code_set, 4, chip=1) == [-1, -1, 1, 1]


# ---------------------------------------------------------------------------
# TMBOC: two subcarriers, chosen per chip
# ---------------------------------------------------------------------------


def test_tmboc_selects_its_second_subcarrier_on_exactly_the_masked_chips():
    """
    TMBOC(6,1,4/33) puts BOC(6,1) on 4 of every 33 chips and BOC(1,1) on the rest.
    The mask is what picks between them, and getting it wrong is invisible in any
    aggregate: the correlation would still peak, just 4/33 of the power short.
    So this checks every chip of a full mask period, individually.
    """
    length = 33 * 4
    code_set = build_code_set(
        [
            CodeComponent(
                name="L1CP",
                sequence=np.ones(length, dtype=np.int8),
                branch=Branch.Q,
                subcarrier=Subcarrier(
                    kind=SubcarrierKind.TMBOC,
                    rate_hz=CHIP_RATE,
                    pattern=TMBOC_PATTERN,
                    pattern_rate_hz=6 * CHIP_RATE,
                ),
            )
        ],
        chip_rate_hz=CHIP_RATE,
    )
    assert code_set.subcarrier_sub_chips_per_chip.tolist() == [2]
    assert code_set.subcarrier_pattern_sub_chips_per_chip.tolist() == [12]

    boc11 = _signs_across_one_chip(_boc_set(np.ones(1, dtype=np.int8), CHIP_RATE), 24)
    boc61 = _signs_across_one_chip(_boc_set(np.ones(1, dtype=np.int8), 6 * CHIP_RATE), 24)
    assert boc11 != boc61  # otherwise this test proves nothing

    masked = 0
    for chip in range(length):
        expected = boc61 if TMBOC_PATTERN[chip % 33] else boc11
        masked += int(TMBOC_PATTERN[chip % 33])
        assert _signs_across_one_chip(code_set, 24, chip=chip) == expected, f"chip {chip}"
    assert masked == 4 * 4  # 4 of every 33, over 4 mask periods


def test_the_tmboc_mask_must_tile_the_code():
    """A mask that does not divide the code length would be truncated at the wrap,
    so the last block would carry the wrong subcarrier -- rejected at build time."""
    with pytest.raises(ValueError, match="does not divide"):
        build_code_set(
            [
                CodeComponent(
                    name="P",
                    sequence=np.ones(100, dtype=np.int8),  # 100 % 33 != 0
                    branch=Branch.I,
                    subcarrier=Subcarrier(
                        kind=SubcarrierKind.TMBOC, rate_hz=CHIP_RATE,
                        pattern=TMBOC_PATTERN, pattern_rate_hz=6 * CHIP_RATE,
                    ),
                )
            ],
            chip_rate_hz=CHIP_RATE,
        )


# ---------------------------------------------------------------------------
# The autocorrelation function
# ---------------------------------------------------------------------------


def _acf(code_set, code, delays_chips, samples_per_chip=256):
    """
    Correlate a noiseless BOC(1,1) transmission of `code` against itself at each delay.

    `samples_per_chip` is a power of two so that `1 / samples_per_chip` is exact in
    binary and the accumulated code phase lands on chip boundaries exactly; at 60
    samples per chip roughly half of them fall a rounding error short and read the
    previous chip, which costs about 1.3% of the peak.
    """
    chips = np.repeat(code.astype(np.float64), samples_per_chip)
    # The transmitted signal carries the subcarrier too; build it the same way the
    # kernel will read it, from the fractional chip position.
    sub_chips = int(code_set.subcarrier_sub_chips_per_chip[0])
    within_chip = np.tile(
        np.arange(samples_per_chip) / samples_per_chip, code.size
    )
    signs = np.where((within_chip * sub_chips).astype(int) % 2 == 1, -1.0, 1.0)
    samples = (chips * signs).astype(np.complex64)

    out = np.zeros((len(delays_chips), 1), dtype=np.complex64)
    correlate__multicomponent(
        samples, float(samples_per_chip), 0.0, 0.0, code_set,
        1.0, 0.0, np.asarray(delays_chips, dtype=np.float64), out,
    )
    return np.real(out[:, 0]) / chips.size


def test_the_boc_autocorrelation_has_its_textbook_shape():
    """
    BOC(1,1)'s ACF is piecewise linear: 1 at zero delay, falling to -0.5 at half a
    chip, back to 0 at a full chip.  It crosses zero at 1/3 chip.

    This is the operational fact behind L1C's early/late spacing.  A BPSK ACF is
    linear out to a full chip, so 0.5-chip early/late taps sit on its slope; on
    BOC(1,1) those same taps sit past the zero crossing, on the *negative*
    shoulder, where the discriminator has the wrong sign.
    """
    # A long code keeps the neighbouring-chip cross terms, which average to zero
    # only over the whole code, down near 1/sqrt(10230) ~ 0.01.
    rng = np.random.default_rng(0)
    code = rng.choice(np.array([-1, 1], dtype=np.int8), size=10230)
    code_set = _boc_set(code, CHIP_RATE)

    delays = np.array([0.0, 1 / 3, 0.5, 1.0])
    acf = _acf(code_set, code, delays)
    assert acf[0] == pytest.approx(1.0, abs=0.02)
    assert acf[1] == pytest.approx(0.0, abs=0.04)
    assert acf[2] == pytest.approx(-0.5, abs=0.04)
    assert acf[3] == pytest.approx(0.0, abs=0.04)

    # A half-chip early/late pair straddles the minimum, not the peak.
    early, late = _acf(code_set, code, np.array([0.5, -0.5]))
    assert early < 0 and late < 0


def test_a_boc_replica_does_not_correlate_with_a_plain_bpsk_signal():
    """
    The subcarrier is odd-symmetric within each chip, so against a signal that is
    constant across the chip its two halves cancel.  This is why acquisition must
    fold the subcarrier into its replica rather than reusing the plain code: a BPSK
    replica on a BOC signal, and the reverse, both collapse at zero delay.
    """
    rng = np.random.default_rng(1)
    code = rng.choice(np.array([-1, 1], dtype=np.int8), size=1023)
    samples_per_chip = 64  # exact in binary; see `_acf`
    bpsk_signal = np.repeat(code.astype(np.complex64), samples_per_chip)

    boc_set = _boc_set(code, CHIP_RATE)
    bpsk_set = build_code_set([CodeComponent("P", code, Branch.I)])

    out_boc = np.zeros((1, 1), dtype=np.complex64)
    correlate__multicomponent(
        bpsk_signal, float(samples_per_chip), 0.0, 0.0, boc_set, 1.0, 0.0,
        np.array([0.0]), out_boc,
    )
    out_bpsk = np.zeros((1, 1), dtype=np.complex64)
    correlate__multicomponent(
        bpsk_signal, float(samples_per_chip), 0.0, 0.0, bpsk_set, 1.0, 0.0,
        np.array([0.0]), out_bpsk,
    )

    matched = abs(complex(out_bpsk[0, 0]))
    mismatched = abs(complex(out_boc[0, 0]))
    assert matched == pytest.approx(bpsk_signal.size, rel=1e-6)
    assert mismatched < 0.02 * matched


# ---------------------------------------------------------------------------
# Agreement with the BPSK kernel
# ---------------------------------------------------------------------------


def test_a_component_without_a_subcarrier_is_untouched_by_the_other_kernel():
    """
    A signal may mix the two -- and `has_subcarrier` routes the WHOLE code set to
    the subcarrier kernel, so the plain component's arithmetic has to survive the
    trip.  Zero sub-chips per chip is what carries it, and this is the only test
    that would catch that path being wrong.
    """
    rng = np.random.default_rng(2)
    plain = rng.choice(np.array([-1, 1], dtype=np.int8), size=257)
    boc = rng.choice(np.array([-1, 1], dtype=np.int8), size=257)
    samples = (rng.standard_normal(3000) + 1j * rng.standard_normal(3000)).astype(np.complex64)
    bins = np.array([0.25, 0.0, -0.25])

    mixed = build_code_set(
        [
            CodeComponent("plain", plain, Branch.I),
            CodeComponent(
                "boc", boc, Branch.Q,
                subcarrier=Subcarrier(kind=SubcarrierKind.BOC_SIN, rate_hz=CHIP_RATE),
            ),
        ],
        chip_rate_hz=CHIP_RATE,
    )
    assert mixed.has_subcarrier
    assert mixed.subcarrier_sub_chips_per_chip.tolist() == [0, 2]

    alone = build_code_set([CodeComponent("plain", plain, Branch.I)])

    out_mixed = np.zeros((3, 2), dtype=np.complex64)
    correlate__multicomponent(
        samples, _SAMP_RATE, 0.1, 250.0, mixed, CHIP_RATE, 3.75, bins, out_mixed,
    )
    out_alone = np.zeros((3, 1), dtype=np.complex64)
    correlate__multicomponent(
        samples, _SAMP_RATE, 0.1, 250.0, alone, CHIP_RATE, 3.75, bins, out_alone,
    )
    np.testing.assert_array_equal(out_mixed[:, 0], out_alone[:, 0])


def test_the_subcarrier_stays_continuous_across_the_code_wrap():
    """
    Late bins go slightly negative at the start of every correlation interval --
    the case that made flooring rather than truncation necessary in the BPSK
    kernel.  The subcarrier has the same exposure: the fractional part has to come
    from the same floor, or the sign inverts on exactly those samples.
    """
    code = np.ones(4, dtype=np.int8)
    code_set = _boc_set(code, CHIP_RATE)
    # Just below zero is the last quarter of chip 3, which BOC(1,1) makes negative.
    out = np.zeros((1, 1), dtype=np.complex64)
    correlate__multicomponent(
        np.ones(1, dtype=np.complex64), 1.0, 0.0, 0.0, code_set, 0.0,
        -0.25, np.array([0.0]), out,
    )
    assert int(np.real(out[0, 0])) == -1


# ---------------------------------------------------------------------------
# Configuration errors
# ---------------------------------------------------------------------------


def test_a_subcarrier_needs_the_chip_rate_to_be_expressible():
    """The rate is in Hz; the kernel needs sub-chips per chip.  Nothing else in
    the code set knows the chip rate, so it has to be supplied."""
    with pytest.raises(ValueError, match="chip_rate_hz is required"):
        build_code_set(
            [
                CodeComponent(
                    "P", np.ones(8, dtype=np.int8), Branch.I,
                    subcarrier=Subcarrier(kind=SubcarrierKind.BOC_SIN, rate_hz=CHIP_RATE),
                )
            ]
        )


def test_a_subcarrier_that_does_not_divide_the_chip_is_rejected():
    """
    BOC(n,m) is only periodic on the chip axis when 2n/m is a whole number, and the
    kernel derives the sign from the fractional chip position and nothing else.

    Note an ODD count is fine -- BOC(1.5,1) gives three sub-chips per chip, and the
    subcarrier simply starts each chip on the opposite half-cycle.  What is not
    fine is a fractional one, or fewer than two (no subcarrier at all).
    """
    _boc_set(np.ones(8, dtype=np.int8), 1.5 * CHIP_RATE)  # 3 sub-chips: accepted

    with pytest.raises(ValueError, match="sub-chips per chip"):
        _boc_set(np.ones(8, dtype=np.int8), 1.25 * CHIP_RATE)  # 2.5
    with pytest.raises(ValueError, match="sub-chips per chip"):
        _boc_set(np.ones(8, dtype=np.int8), 0.5 * CHIP_RATE)  # 1


def test_a_pattern_on_a_plain_boc_is_rejected():
    """A per-chip mask selects between two rates; a single-rate BOC has nothing to
    select, so a pattern there is a mistake rather than a no-op."""
    with pytest.raises(ValueError, match="per-chip"):
        Subcarrier(
            kind=SubcarrierKind.BOC_SIN, rate_hz=CHIP_RATE,
            pattern=TMBOC_PATTERN, pattern_rate_hz=6 * CHIP_RATE,
        )


def test_the_pattern_is_a_mask_not_a_sequence():
    """+/-1 would look plausible and select the pattern rate on every chip."""
    with pytest.raises(ValueError, match="0/1"):
        build_code_set(
            [
                CodeComponent(
                    "P", np.ones(33, dtype=np.int8), Branch.I,
                    subcarrier=Subcarrier(
                        kind=SubcarrierKind.TMBOC, rate_hz=CHIP_RATE,
                        pattern=np.where(TMBOC_PATTERN == 1, 1, -1).astype(np.int8),
                        pattern_rate_hz=6 * CHIP_RATE,
                    ),
                )
            ],
            chip_rate_hz=CHIP_RATE,
        )


# ---------------------------------------------------------------------------
# Phasing, and the two kernel specialisations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sub_chips", [2, 3, 4, 12])
@pytest.mark.parametrize(
    "kind, wave",
    [(SubcarrierKind.BOC_SIN, np.sin), (SubcarrierKind.BOC_COS, np.cos)],
    ids=["sine", "cosine"],
)
def test_the_subcarrier_sign_is_the_square_wave_it_claims_to_be(kind, wave, sub_chips):
    """
    State the subcarrier the way the definition does -- `sign(sin(2*pi*f*t))` for
    sine phasing, `sign(cos(...))` for cosine -- and require the parity rule to
    agree everywhere inside a chip.

    The rules differ only by half a sub-chip, which is a quarter of a subcarrier
    period: cosine phasing opens and closes each chip on a half-width segment.
    Odd `sub_chips` is included because that is where the two disagree at the chip
    boundary as well as inside it, so a formula that merely looked symmetric would
    not survive.
    """
    component = CodeComponent(
        name="P",
        sequence=np.ones(1, dtype=np.int8),
        branch=Branch.I,
        subcarrier=Subcarrier(kind=kind, rate_hz=sub_chips / 2 * CHIP_RATE),
    )
    fractional_chip = np.linspace(0.0, 1.0, 4001, endpoint=False)

    signs = subcarrier_signs(
        component, CHIP_RATE, np.zeros_like(fractional_chip, dtype=int), fractional_chip
    )
    # Subcarrier cycles elapsed since the chip opened.
    expected = np.sign(wave(2 * np.pi * fractional_chip * sub_chips / 2))
    expected[expected == 0] = 1  # the wave's own zero crossings belong to the next half

    np.testing.assert_array_equal(signs, expected.astype(np.int8))


def test_cosine_phasing_is_sine_phasing_shifted_by_half_a_sub_chip():
    """
    The relationship the offset encodes, stated directly rather than through the
    trigonometry: advancing the sine-phased wave by half a sub-chip gives the
    cosine-phased one.
    """
    sub_chips = 4
    rate_hz = sub_chips / 2 * CHIP_RATE
    fractional_chip = np.linspace(0.0, 1.0, 401, endpoint=False)
    chips = np.zeros_like(fractional_chip, dtype=int)

    def signs_for(kind, offset=0.0):
        component = CodeComponent(
            name="P", sequence=np.ones(1, dtype=np.int8), branch=Branch.I,
            subcarrier=Subcarrier(kind=kind, rate_hz=rate_hz),
        )
        return subcarrier_signs(
            component, CHIP_RATE, chips, (fractional_chip + offset) % 1.0
        )

    shifted = signs_for(SubcarrierKind.BOC_SIN, offset=0.5 / sub_chips)
    np.testing.assert_array_equal(signs_for(SubcarrierKind.BOC_COS), shifted)


def test_both_kernel_specialisations_agree_on_a_subcarrier_free_signal():
    """
    The two kernels are generated from one source with the subcarrier branch folded
    at compile time, so they must produce identical numbers on a signal that has no
    subcarrier -- the case where the folded-away code would have been a no-op.

    Bit-identical, not merely close: the same additions happen in the same order,
    and anything less would mean the fold changed the arithmetic.
    """
    rng = np.random.default_rng(7)
    sequence = (rng.integers(0, 2, 1023).astype(np.int8) * 2 - 1)
    samples = (
        rng.standard_normal(20_000) + 1j * rng.standard_normal(20_000)
    ).astype(np.complex64)
    bins = np.array([0.5, 0.0, -0.5])

    plain = build_code_set(
        [CodeComponent(name="P", sequence=sequence, branch=Branch.I)],
        chip_rate_hz=CHIP_RATE,
    )
    # Same code set, forced down the subcarrier kernel by giving a second component
    # a subcarrier; component 0 must be unaffected by its neighbour's.
    mixed = build_code_set(
        [
            CodeComponent(name="P", sequence=sequence, branch=Branch.I),
            CodeComponent(
                name="Q", sequence=sequence, branch=Branch.Q,
                subcarrier=Subcarrier(
                    kind=SubcarrierKind.BOC_SIN, rate_hz=CHIP_RATE
                ),
            ),
        ],
        chip_rate_hz=CHIP_RATE,
    )
    assert not plain.has_subcarrier and mixed.has_subcarrier

    out_plain = np.zeros((3, 1), dtype=np.complex64)
    out_mixed = np.zeros((3, 2), dtype=np.complex64)
    for code_set, out in ((plain, out_plain), (mixed, out_mixed)):
        correlate__multicomponent(
            samples, _SAMP_RATE, 0.0, 0.0, code_set, CHIP_RATE, 0.0, bins, out
        )

    np.testing.assert_array_equal(out_plain[:, 0], out_mixed[:, 0])
