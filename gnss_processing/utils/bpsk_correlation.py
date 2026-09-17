import numpy as np
import numba as nb
from numpy.typing import NDArray

from .code_components import CodeSet


@nb.njit(inline="always")
def _subcarrier_sign(
        subcarrier_sub_chips_per_chip,
        subcarrier_pattern_sub_chips_per_chip,
        subcarrier_patterns_flat,
        subcarrier_pattern_start_indices,
        subcarrier_pattern_lengths,
        subcarrier_phase_offset_sub_chips,
        component,
        chip_index,
        fractional_chip,
    ):
    """
    Sign of the subcarrier riding component `component` at `fractional_chip`.

    A subcarrier is a square wave on each chip, so its sign depends only on WHERE
    INSIDE THE CHIP the sample falls -- the one quantity a BPSK correlation throws
    away.  Split the chip into `S` sub-chips and the wave alternates on each:

        sub_chip = int(fractional_chip * S + phase_offset)
        sign     = -1 if sub_chip is odd else +1

    `S` is 2 for BOC(1,1) and 12 for BOC(6,1).  `phase_offset` is 0 for the usual
    sine phasing and 1/2 for cosine phasing, which is the same wave advanced by a
    quarter of a subcarrier period -- half a sub-chip.

    TMBOC differs only in choosing `S` per chip from a repeating mask, so GPS
    L1C-P runs BOC(6,1) on 4 of every 33 chips and BOC(1,1) on the other 29.

    A component with `subcarrier_sub_chips_per_chip == 0` has no subcarrier and
    gets +1, so one signal may mix BOC and plain BPSK components.

    `chip_index` selects the TMBOC rate and `fractional_chip` gives the position
    within the subcarrier's own cycle.  Under the double estimator those come from
    two different delays, which is deliberate: which chips carry BOC(6,1) is a
    property of the code, while the phase of the wave riding them is not.

    `inline="always"` because this sits in the innermost loop of the innermost
    loop; measured, it costs 1% against writing the same arithmetic by hand, and
    it keeps one statement of what a subcarrier is.
    """
    sub_chips = subcarrier_sub_chips_per_chip[component]
    if sub_chips == 0:
        return 1
    pattern_length = subcarrier_pattern_lengths[component]
    if pattern_length > 0:
        selected = subcarrier_patterns_flat[
            subcarrier_pattern_start_indices[component]
            + chip_index % pattern_length
        ]
        if selected == 1:
            sub_chips = subcarrier_pattern_sub_chips_per_chip[component]
    offset = subcarrier_phase_offset_sub_chips[component]
    if int(fractional_chip * sub_chips + offset) % 2 == 1:
        return -1
    return 1


def _make_correlate_kernel(with_subcarrier: bool):
    """
    Build one of the two correlation kernels.

    Both are the same loop; the only difference is whether each symbol is signed
    by its subcarrier.  Numba folds `with_subcarrier` at compile time because it
    is captured from this enclosing scope, so the branches below do not exist in
    the generated code -- the subcarrier-free kernel never loads a subcarrier
    array or computes a fractional chip position.

    That specialisation is worth having, not a micro-optimisation: measured on a
    two-component, three-bin, 22 Msps workload, checking `sub_chips != 0` at
    runtime instead costs **37%** on the three BPSK signals, which pay for a
    feature only GPS L1C uses.  Generating both from one source is what keeps
    that speed without keeping two copies of the loop in sync by hand.
    """

    @nb.jit(nopython=True, parallel=False)
    def kernel(
            samples: nb.complex64[:],  # type: ignore
            codes_flat: nb.int8[:],  # type: ignore
            component_code_start_indices: nb.int64[:],  # type: ignore
            component_code_lengths: nb.int64[:],  # type: ignore
            subcarrier_sub_chips_per_chip: nb.int64[:],  # type: ignore
            subcarrier_pattern_sub_chips_per_chip: nb.int64[:],  # type: ignore
            subcarrier_patterns_flat: nb.int8[:],  # type: ignore
            subcarrier_pattern_start_indices: nb.int64[:],  # type: ignore
            subcarrier_pattern_lengths: nb.int64[:],  # type: ignore
            subcarrier_phase_offset_sub_chips: nb.float64[:],  # type: ignore
            initial_code_phase_chips: nb.float64,  # type: ignore
            initial_subcarrier_phase_chips: nb.float64,  # type: ignore
            chips_per_sample: nb.float64,  # type: ignore
            bin_offsets_chips: nb.float64[:],  # type: ignore
            subcarrier_bin_offsets_chips: nb.float64[:],  # type: ignore
            conj_carr_sample: nb.complex64,  # type: ignore
            conj_carr_rotation: nb.complex64,  # type: ignore
            corr_values: nb.complex64[:, :]  # type: ignore
        ) -> None:
        """
        Accumulate one correlation interval for every (delay bin, code component) pair.

        Every component is stated on the signal's own chip axis, so component `c`
        contributes `codes_flat[start[c] + chip_index % component_code_lengths[c]]`
        at chip `chip_index` -- no per-component rate or offset to apply.  A
        component that is not transmitting at that chip carries 0 there and
        accumulates nothing, which is how time-division multiplexing (L2C's CM/CL)
        is expressed; see `utils.code_components`.

        Codes are packed end to end in `codes_flat` because their lengths differ by
        three orders of magnitude (1023 vs 1534500).  Component `c`'s code lives at
        `codes_flat[component_code_start_indices[c] : ... + component_code_lengths[c]]`.

        `component_code_start_indices` addresses `codes_flat`; `bin_offsets_chips`
        is a position on the chip axis.  Different quantities, named apart.

        `corr_values` is accumulated in place and is NOT zeroed here: a correlation
        interval may be spread across several sample buffers.
        """
        num_samples = len(samples)
        num_bins = len(bin_offsets_chips)
        num_components = len(component_code_start_indices)

        code_phase_chips = initial_code_phase_chips
        subcarrier_phase_chips = initial_subcarrier_phase_chips
        for i in range(num_samples):
            carrierless = samples[i] * conj_carr_sample
            for j in range(num_bins):
                # floor, not truncation: a late bin near a code wrap produces a
                # slightly negative chip index, which must map to the previous
                # chip.  `fractional_chip` is then in [0, 1) on both sides of the
                # wrap, which is what keeps the subcarrier continuous across it.
                binned_code_phase_chips = code_phase_chips + bin_offsets_chips[j]
                chip_index = int(np.floor(binned_code_phase_chips))
                if with_subcarrier:
                    # The subcarrier rides its OWN delay, not the code's.  Tying
                    # the two is what gives a BOC correlation its side peaks; the
                    # double estimator separates them and tracks each (see
                    # `TapLayout`).  With the two phases equal and the two offset
                    # arrays identical this is bit-for-bit the tied case.
                    binned_subcarrier_phase_chips = (
                        subcarrier_phase_chips + subcarrier_bin_offsets_chips[j]
                    )
                    fractional_chip = (
                        binned_subcarrier_phase_chips
                        - np.floor(binned_subcarrier_phase_chips)
                    )
                for c in range(num_components):
                    symbol = codes_flat[
                        component_code_start_indices[c]
                        + chip_index % component_code_lengths[c]
                    ]
                    if with_subcarrier and symbol != 0:
                        symbol = symbol * _subcarrier_sign(
                            subcarrier_sub_chips_per_chip,
                            subcarrier_pattern_sub_chips_per_chip,
                            subcarrier_patterns_flat,
                            subcarrier_pattern_start_indices,
                            subcarrier_pattern_lengths,
                            subcarrier_phase_offset_sub_chips,
                            c,
                            chip_index,
                            fractional_chip,
                        )
                    if symbol == 1:
                        corr_values[j, c] += carrierless
                    elif symbol == -1:
                        corr_values[j, c] -= carrierless
                    elif symbol != 0:
                        corr_values[j, c] += carrierless * symbol
            conj_carr_sample *= conj_carr_rotation
            code_phase_chips += chips_per_sample
            subcarrier_phase_chips += chips_per_sample

    return kernel


numba_correlate__multicomponent__complex64 = _make_correlate_kernel(False)
numba_correlate__multicomponent_subcarrier__complex64 = _make_correlate_kernel(True)


def correlate__multicomponent(
    samples: np.ndarray,
    samp_rate: float,
    initial_carr_phase_cycles: float,
    doppler_freq_hz: float,
    code_set: CodeSet,
    code_rate_chips_per_sec: float,
    initial_code_phase_chips: float,
    bin_offsets_chips: NDArray[np.float64],
    output: NDArray[np.complex64],
    subcarrier_offset_chips: float = 0.0,
    subcarrier_bin_offsets_chips: NDArray[np.float64] | None = None,
) -> None:
    """
    Correlate `samples` against every component of `code_set`.

    `output` has shape (num_bins, num_components) and is accumulated in place.
    `code_rate_chips_per_sec` and `initial_code_phase_chips` are on the signal's
    combined chip clock -- 1.023 Mcps for L1 C/A and for L2C's interleaved CM/CL
    stream, 10.23 Mcps for L5.

    `subcarrier_offset_chips` and `subcarrier_bin_offsets_chips` displace the
    subcarrier's delay from the code's.  Both default to "tied to the code", which
    is ordinary BOC tracking and is what every signal but a double-estimator
    channel wants; the defaults are exact, so a tied channel is bit-for-bit
    unchanged by their existence.
    """
    wrapped_code_phase_chips = code_set.wrap_code_phase_chips(initial_code_phase_chips)
    chips_per_sample = code_rate_chips_per_sec / samp_rate

    conj_carr_sample = np.exp(-2j * np.pi * initial_carr_phase_cycles)
    conj_carr_rotation = np.exp(-2j * np.pi * doppler_freq_hz / samp_rate)

    # Only the FRACTIONAL part of the subcarrier phase is read, and the wrap above
    # moves the code phase by a whole number of chips, so applying the offset after
    # it is safe and keeps the tied case exactly equal.
    subcarrier_phase_chips = wrapped_code_phase_chips + subcarrier_offset_chips
    if subcarrier_bin_offsets_chips is None:
        subcarrier_bin_offsets_chips = bin_offsets_chips

    # Both kernels take the same arguments; the subcarrier-free specialisation
    # simply never reads the subcarrier ones, because its branch is folded away at
    # compile time (see `_make_correlate_kernel`).
    kernel = (
        numba_correlate__multicomponent_subcarrier__complex64
        if code_set.has_subcarrier
        else numba_correlate__multicomponent__complex64
    )
    kernel(
        samples,
        code_set.codes_flat,
        code_set.component_code_start_indices,
        code_set.component_code_lengths,
        code_set.subcarrier_sub_chips_per_chip,
        code_set.subcarrier_pattern_sub_chips_per_chip,
        code_set.subcarrier_patterns_flat,
        code_set.subcarrier_pattern_start_indices,
        code_set.subcarrier_pattern_lengths,
        code_set.subcarrier_phase_offset_sub_chips,
        wrapped_code_phase_chips,
        subcarrier_phase_chips,
        chips_per_sample,
        bin_offsets_chips,
        subcarrier_bin_offsets_chips,
        conj_carr_sample,
        conj_carr_rotation,
        output,
    )
