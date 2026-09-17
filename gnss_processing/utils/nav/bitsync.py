"""
Data bit synchronisation for signals with no overlay code to lock onto.

Only GPS L1 C/A needs this.  Every other signal in this project hands the symbol
boundary over for free:

    L2C   one CM code period *is* one symbol -- 20 ms, and the code phase already
          says where it starts.
    L5    NH10 overlay sync lands the epoch grid on symbol boundaries, which is
          exactly why the signal's coherent ceiling is I's 10 ms CNAV symbol
          rather than NH20's more tempting 20 (see
          `tracking_channel.coherent_duration_limits_ms`).
    L1C   L1CD's 10 ms code period is one CNAV-2 symbol.

L1 C/A has a 1 ms code and a 20 ms data bit and nothing to tie them together, so
the boundary has to be found statistically.  The method is the classic one: a data
bit can only change sign at a bit boundary, so accumulate a histogram of where
sign changes fall across the twenty candidate phases and take the peak.  A phase
that is not the boundary collects only transitions caused by noise.

The confidence returned is the peak divided by the runner-up, deliberately the
same shape of statistic `secondary_code.OverlaySynchroniser.confidence` reports --
the two mechanisms solve the same problem for different signals, and a reader
comparing them should not have to translate between two scales.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# 50 bps data on a 1 ms code: twenty code periods per bit.
L1CA_PERIODS_PER_BIT = 20


@dataclass(frozen=True)
class BitSyncResult:
    phase: int
    """Index of the first prompt belonging to a data bit, in [0, periods_per_bit)."""

    confidence: float
    """Peak transition count over the runner-up.  1.0 is a coin flip."""

    transitions: np.ndarray
    """Per-phase histogram, for plotting or diagnosis."""

    synced: bool

    def bit_slices(self, num_prompts: int) -> tuple[int, int]:
        """
        `(start, count)` -- where whole data bits begin and how many are available.

        Returned rather than computed by the caller because the leading partial bit
        must be discarded: summing a partial bit produces a symbol that is
        confidently wrong rather than obviously missing.
        """
        available = num_prompts - self.phase
        return self.phase, available // L1CA_PERIODS_PER_BIT


def synchronise(
    prompts: np.ndarray,
    *,
    periods_per_bit: int = L1CA_PERIODS_PER_BIT,
    confidence_threshold: float = 2.0,
    min_prompts: int | None = None,
) -> BitSyncResult:
    """
    Find the data bit boundary in a run of 1 ms prompt correlator values.

    `prompts` are complex prompt outputs, one per code period.  Only the real part
    matters -- after Costas lock the data sits on I -- but taking the sign of the
    real part of a complex input is what a caller has, so that conversion happens
    here rather than at every call site.

    A few seconds of prompts is plenty: at 50 bps a random bit stream changes sign
    about half the time, so two seconds gives ~50 transitions at the true phase
    against a handful of noise-driven ones elsewhere.  `min_prompts` defaults to
    one second, below which the histogram is too sparse to trust and the result
    comes back `synced=False` rather than confidently wrong.
    """
    prompts = np.asarray(prompts)
    if min_prompts is None:
        min_prompts = 50 * periods_per_bit  # one second
    signs = np.sign(np.real(prompts))

    transitions = np.zeros(periods_per_bit, dtype=float)
    if len(prompts) < min_prompts:
        return BitSyncResult(0, 0.0, transitions, False)

    # A transition at index i means prompts i-1 and i straddle a boundary, so it
    # votes for phase i % periods_per_bit.
    changed = np.nonzero(signs[1:] != signs[:-1])[0] + 1
    if len(changed):
        np.add.at(transitions, changed % periods_per_bit, 1.0)

    order = np.argsort(transitions)[::-1]
    peak, runner_up = transitions[order[0]], transitions[order[1]]
    confidence = float(peak / runner_up) if runner_up > 0 else float("inf")
    if peak == 0:
        # No transitions at all: either the data never flipped over this span or
        # the channel is not tracking.  Either way there is no evidence, and
        # reporting phase 0 with infinite confidence would be a lie.
        return BitSyncResult(0, 0.0, transitions, False)
    return BitSyncResult(
        phase=int(order[0]),
        confidence=confidence,
        transitions=transitions,
        synced=confidence >= confidence_threshold,
    )


def fold_to_symbols(
    prompts: np.ndarray, phase: int, *, periods_per_bit: int = L1CA_PERIODS_PER_BIT
) -> np.ndarray:
    """
    Sum whole data bits' worth of prompts into one soft symbol each.

    Coherent summation over the bit, which is the whole point of finding the
    boundary: twenty 1 ms prompts summed in phase are 13 dB better than one, and
    summed across a boundary they partly cancel instead.
    """
    prompts = np.asarray(prompts)
    usable = (len(prompts) - phase) // periods_per_bit
    if usable <= 0:
        return np.zeros(0, dtype=prompts.dtype)
    trimmed = prompts[phase : phase + usable * periods_per_bit]
    return trimmed.reshape(usable, periods_per_bit).sum(axis=1)
