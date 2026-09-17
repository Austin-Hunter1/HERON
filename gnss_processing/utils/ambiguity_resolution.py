"""
Resolving the long-code ambiguity acquisition leaves behind.

Acquisition searches one period of whatever code it correlates against, so it pins
the code phase only within that period.  When the signal carries longer structure
on top, what remains is a small integer, not a fresh search over chips:

    L2C   acquires on CM (20 ms); CL runs 1.5 s        ->  75 hypotheses
    L5    acquires on I x NH10 (10 ms); NH20 runs 20 ms ->   2 hypotheses

WHY HYPOTHESES AND NOT AN FFT
-----------------------------
The tempting alternative is to zero-pad the dwell out to the long code's period,
FFT it against the full replica, and read the peak.  That computes the correlation
at every sample lag -- about 7.5e6 of them for CL at 5 Msps -- in order to use 75.
Testing the hypotheses directly costs one pass over the CL code, ~7.7e5
multiply-accumulates, roughly 100x less work.  It also needs 767 KB of int8 code
per PRN instead of a 60 MB replica spectrum, and, because it exposes 75 cells to
the noise rather than 7.5e6, it needs about 3 dB less SNR for the same
false-alarm rate.  The direct search wins on all three counts, and wins by more
the better it is implemented.

COHERENT STRUCTURE MUST MATCH ACQUISITION'S
-------------------------------------------
Each hypothesis is scored with the same coherent block structure the acquisition
used.  A Doppler estimate good to half a bin costs `sinc(df * T)` on a coherent
integration of length T, so reusing acquisition's T means the hypothesis search
takes exactly the loss the acquisition already survived -- no extra Doppler
accuracy is needed anywhere.  Scoring a 20 ms coherent integration off a 5 ms
acquisition's +/-100 Hz grid would put `df * T` at 2.0, past the second null.

Blocks are combined by square law, so data and overlay sign flips between blocks
are harmless, exactly as in acquisition.

CONFIDENCE
----------
Reported as best score over runner-up.  That ratio is scale-free, so it needs no
signal-level threshold -- the same reasoning as `secondary_code.brute_force_search`,
which resolves the same kind of ambiguity after lock rather than before it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .bpsk_correlation import correlate__multicomponent
from .code_components import CodeSet

# Peak must beat the runner-up by this much to be believed.  A present signal
# clears it by a wide margin (measured 10-19x on real L5); an absent one sits near
# 1.0 because both hypotheses are scoring noise.
DEFAULT_CONFIDENCE_THRESHOLD = 2.0


@dataclass(frozen=True)
class AmbiguityResolution:
    """Which repetition of the long code the dwell sits in."""

    best_index: int
    confidence: float  # best / runner-up; inf when there is only one hypothesis
    scores: np.ndarray
    offset_chips: float  # best_index * stride, to be added to the acquired phase
    resolved: bool


def resolve_code_ambiguity(
    samples: np.ndarray,
    samp_rate: float,
    *,
    code_set: CodeSet,
    scored_component: int,
    acquired_code_phase_sec: float,
    doppler_hz: float,
    carrier_freq_hz: float,
    nominal_code_rate_chips_per_sec: float,
    coherent_length_samples: int,
    num_blocks: int,
    num_hypotheses: int,
    hypothesis_stride_chips: float,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> AmbiguityResolution:
    """
    Score every candidate offset of the long code against the acquisition dwell.

    `code_set` is correlated as the signal actually transmits it, and only
    `scored_component` is read.  For L2C that means passing the real CM/CL set:
    the hypothesis stride is a whole CM period, so CM correlates identically under
    every hypothesis and only CL discriminates -- which is exactly the property
    being tested.

    `hypothesis_stride_chips` is the acquisition code's period on the signal's chip
    clock: 20460 for L2C (20 ms at 1.023 Mcps), 102300 for L5 (10 ms at 10.23 Mcps).
    """
    if not 0 <= scored_component < code_set.num_components:
        raise ValueError(
            f"scored_component {scored_component} out of range for "
            f"{code_set.num_components} components {code_set.names}"
        )
    if num_hypotheses < 1:
        raise ValueError("num_hypotheses must be >= 1")
    required = num_blocks * coherent_length_samples
    if len(samples) < required:
        raise ValueError(
            f"need {required} samples for {num_blocks} x {coherent_length_samples}-sample "
            f"coherent blocks, got {len(samples)}"
        )

    samples = np.ascontiguousarray(samples, dtype=np.complex64)

    # Code rate is slaved to carrier Doppler.  Over a 20 ms dwell at L5 this is
    # most of a chip, so ignoring it would smear the very peak being scored.
    code_rate = nominal_code_rate_chips_per_sec * (1.0 + doppler_hz / carrier_freq_hz)
    base_chips = acquired_code_phase_sec * nominal_code_rate_chips_per_sec

    prompt_only = np.zeros(1, dtype=np.float64)  # single delay bin, on-time
    corr = np.zeros((1, code_set.num_components), dtype=np.complex64)

    scores = np.zeros(num_hypotheses, dtype=np.float64)
    for hypothesis in range(num_hypotheses):
        offset_chips = hypothesis * hypothesis_stride_chips
        total = 0.0
        for block in range(num_blocks):
            start = block * coherent_length_samples
            block_samples = samples[start : start + coherent_length_samples]
            # Carrier phase is arbitrary per block because blocks are combined by
            # square law; only the rotation *within* a block has to be right.
            code_phase_chips = (
                base_chips + (start / samp_rate) * code_rate + offset_chips
            )
            corr.fill(0.0)
            correlate__multicomponent(
                block_samples,
                samp_rate,
                0.0,
                doppler_hz,
                code_set,
                code_rate,
                code_phase_chips,
                prompt_only,
                corr,
            )
            total += float(abs(complex(corr[0, scored_component])) ** 2)
        scores[hypothesis] = total

    ranked = np.sort(scores)[::-1]
    best_index = int(np.argmax(scores))
    runner_up = float(ranked[1]) if num_hypotheses > 1 else 0.0
    confidence = float(ranked[0] / runner_up) if runner_up > 0 else float("inf")

    return AmbiguityResolution(
        best_index=best_index,
        confidence=confidence,
        scores=scores,
        offset_chips=best_index * hypothesis_stride_chips,
        resolved=confidence >= confidence_threshold,
    )
