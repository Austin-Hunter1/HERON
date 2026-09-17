"""
Tiered (secondary / overlay) codes: synchronisation and wipe-off.

Some signals modulate a short code on top of the primary one, advancing by one
overlay chip per primary code period.  GPS L5 carries Neuman-Hofman overlays --
NH10 on I, NH20 on Q -- and GPS L1C carries a 1800-symbol L1CO overlay on its
pilot.

Why it matters: until the overlay is stripped, a "dataless" pilot is not actually
dataless.  Its sign flips once per code period exactly like data, which forces a
Costas discriminator and caps coherent integration at one period.  Removing the
overlay is what buys the pilot its advantage.

ONE COUNTER
-----------
Every overlay on a signal advances on the same primary-code-period boundary, so a
single counter drives all of them: component `c` uses
`overlay_c[counter % len(overlay_c)]`.  Synchronising the counter modulo
`lcm(len(overlay_c) for all c)` therefore fixes every component's phase at once.
For L5 that is `lcm(10, 20) = 20`: locking NH20 on the pilot also locks NH10 on
the data component -- and since NH10's period is exactly the 10 ms CNAV symbol,
that delivers symbol synchronisation for free.

The synchroniser is deliberately separated from the search strategy.  Brute force
over every offset is fine for NH10/NH20, but L1C's 1800-symbol overlay makes it
O(1800^2), so `fft_search` does the same job as one circular correlation.  Both
satisfy `OverlaySearch` and neither touches the state machine.

HOW LONG TO WATCH
-----------------
The observation window is a separate question from the overlay's length, and for
L1C the two are wildly different.  A full NH20 period is 20 ms, so watching four
of them costs nothing.  A full L1C overlay period is 18 SECONDS, and waiting for
one before even attempting a search would be absurd -- the search is a matched
filter over 1800 hypotheses, and a couple of hundred symbols already separates
the true offset from every other by a wide margin.  `prompts_to_observe` sets the
window directly for that case; `periods_to_observe` remains the default for short
overlays, where thinking in whole periods is the natural thing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Protocol, Sequence

import numpy as np


class OverlaySyncStatus(Enum):
    """Where a channel is in the process of locking its overlay."""

    UNSYNCED = "UNSYNCED"  # accumulating prompts, overlay treated as data
    SYNCED = "SYNCED"  # offset known, wipe-off active


class OverlaySearch(Protocol):
    """Finds the overlay offset that best explains a run of prompt values."""

    def __call__(self, prompts: np.ndarray, overlay: np.ndarray) -> tuple[int, float]:
        """Return (best_offset, confidence), confidence being peak / runner-up."""
        ...


def brute_force_search(prompts: np.ndarray, overlay: np.ndarray) -> tuple[int, float]:
    """
    Correlate the prompt run against every cyclic shift of the overlay.

    `prompts` holds one value per primary code period, oldest first.  For a
    correctly tracked pilot, prompt n is `A * overlay[(n + offset) % L]`, so
    correlating against shift h peaks at h == offset.

    Cost is O(len(prompts) * L) -- fine for L = 10 or 20.  Confidence is the
    ratio of the best score to the next best, which is scale-free and so does not
    need a signal-level threshold.
    """
    length = len(overlay)
    scores = np.empty(length)
    for offset in range(length):
        shifted = overlay[(np.arange(len(prompts)) + offset) % length]
        scores[offset] = np.abs(np.sum(prompts * shifted))

    best = int(np.argmax(scores))
    ranked = np.sort(scores)[::-1]
    runner_up = ranked[1] if len(ranked) > 1 else 0.0
    confidence = float(ranked[0] / runner_up) if runner_up > 0 else np.inf
    return best, confidence


def fft_search(prompts: np.ndarray, overlay: np.ndarray) -> tuple[int, float]:
    """
    `brute_force_search`'s answer, by one circular correlation instead of L of them.

    Folding the prompts onto the overlay's length turns the shifted sums into a
    circular cross-correlation, so all L offsets come out of a single FFT pair:
    O(L log L) rather than O(L * len(prompts)).  For L5's 20-chip overlay that is
    slower than brute force and pointless; for L1C's 1800 it is the difference
    between a few hundred microseconds and a tenth of a second, run once per
    unsuccessful attempt.

    The fold is exact rather than an approximation.  Prompt `n` multiplies
    `overlay[(n + h) % L]` under every hypothesis, so prompts `L` apart always
    share a factor and can be summed before the correlation instead of after:

        sum_n p[n] * o[(n+h) % L]  ==  sum_m (sum_{n = m mod L} p[n]) * o[(m+h) % L]

    A window shorter than the overlay leaves most bins empty, which is the
    zero-padding case; a longer one -- L5 watches four whole periods -- fills each
    bin several times over.  Both are the same operation.

    The conjugate is free here.  `brute_force_search` sums `prompts * shifted`
    without one, and the standard correlation theorem carries a conjugate -- but
    the overlay is real, so the two results are complex conjugates of each other
    and identical in magnitude, which is all either function reports.
    """
    length = len(overlay)
    folded = np.zeros(length, dtype=complex)
    np.add.at(folded, np.arange(len(prompts)) % length, prompts)

    scores = np.abs(
        np.fft.ifft(np.conj(np.fft.fft(folded)) * np.fft.fft(overlay.astype(float)))
    )

    best = int(np.argmax(scores))
    ranked = np.sort(scores)[::-1]
    runner_up = ranked[1] if len(ranked) > 1 else 0.0
    confidence = float(ranked[0] / runner_up) if runner_up > 0 else np.inf
    return best, confidence


@dataclass
class OverlaySynchroniser:
    """
    Locks the shared overlay counter, then supplies wipe-off signs.

    `reference_overlay` is the overlay the search runs on -- the pilot's, since it
    carries no data.  `period` is the counter's modulus, the lcm over all the
    signal's overlay lengths.

    Sync is gated by the caller (on PLL lock), because before the carrier is
    tracked the prompts carry a rotating phase and the correlation is meaningless.
    """

    reference_overlay: np.ndarray
    period: int
    periods_to_observe: int = 4
    # Window length in prompts, overriding `periods_to_observe`.  Set it for an
    # overlay long enough that a whole period is not a sensible unit of patience
    # -- see HOW LONG TO WATCH in the module docstring.
    prompts_to_observe: int | None = None
    confidence_threshold: float = 2.0
    search: OverlaySearch = brute_force_search

    status: OverlaySyncStatus = OverlaySyncStatus.UNSYNCED
    counter: int = 0
    confidence: float = 0.0
    _prompts: list[complex] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.period % len(self.reference_overlay) != 0:
            raise ValueError(
                f"counter period {self.period} must be a multiple of the reference "
                f"overlay length {len(self.reference_overlay)}"
            )
        if self.prompts_to_observe is not None and self.prompts_to_observe < 1:
            raise ValueError(
                f"prompts_to_observe must be at least 1, got {self.prompts_to_observe}"
            )

    @property
    def synced(self) -> bool:
        return self.status is OverlaySyncStatus.SYNCED

    @property
    def observation_length(self) -> int:
        """Prompts collected before a search is attempted."""
        if self.prompts_to_observe is not None:
            return self.prompts_to_observe
        return self.periods_to_observe * len(self.reference_overlay)

    def signs(self, overlays: Sequence[np.ndarray | None]) -> np.ndarray:
        """
        Wipe-off sign per component for the current counter value.

        Before sync -- and for components with no overlay -- the sign is +1, so
        folding is a plain accumulation and behaviour matches a signal that has no
        tiered code at all.
        """
        out = np.ones(len(overlays), dtype=np.int8)
        if not self.synced:
            return out
        for index, overlay in enumerate(overlays):
            if overlay is not None:
                out[index] = overlay[self.counter % len(overlay)]
        return out

    def advance(self) -> None:
        """Step the counter by one primary code period."""
        self.counter = (self.counter + 1) % self.period

    def observe(self, prompt: complex) -> bool:
        """
        Feed one primary-period prompt from the pilot component.

        Returns True on the transition to SYNCED.  Once synced this is a no-op, so
        callers can invoke it unconditionally.

        On success `counter` is left holding the overlay index of *this* prompt's
        interval, matching the convention that `signs()` describes the current
        interval and `advance()` steps to the next.  Prompts are assumed to come
        from consecutive intervals; a gap would shift the recovered offset.
        """
        if self.synced:
            return False

        self._prompts.append(prompt)
        if len(self._prompts) < self.observation_length:
            return False

        prompts = np.asarray(self._prompts, dtype=complex)
        offset, confidence = self.search(prompts, self.reference_overlay)
        self.confidence = confidence

        if confidence < self.confidence_threshold:
            # Ambiguous.  Drop the oldest slice and try again on the next prompt
            # rather than discarding everything, so a marginal signal still
            # converges instead of restarting from empty each time.
            #
            # One overlay period is the natural slice and the one L5 uses.  It is
            # capped at half the window because for L1C the overlay is nine times
            # the window, so dropping a period would discard everything -- exactly
            # the behaviour this is here to avoid.
            stride = min(len(self.reference_overlay), max(1, self.observation_length // 2))
            del self._prompts[:stride]
            return False

        # `prompts[0]` was counter value `offset`, so the prompt just observed --
        # the last one -- is `offset + len(prompts) - 1`.  Leaving the counter
        # there lets the caller's usual `advance()` step to the next interval.
        self.counter = (offset + len(prompts) - 1) % self.period
        self.status = OverlaySyncStatus.SYNCED
        self._prompts.clear()
        return True


def build_synchroniser(
    overlays: Sequence[np.ndarray | None],
    reference_index: int,
    **kwargs,
) -> OverlaySynchroniser | None:
    """
    Construct a synchroniser for a signal's overlays, or None if it has none.

    The counter period is the lcm over every overlay length, so one counter serves
    all components.
    """
    present = [o for o in overlays if o is not None]
    if not present:
        return None

    reference = overlays[reference_index]
    if reference is None:
        raise ValueError(
            f"component {reference_index} has no overlay to synchronise on; "
            "the reference should be the pilot"
        )

    period = int(np.lcm.reduce([len(o) for o in present]))
    return OverlaySynchroniser(reference_overlay=reference, period=period, **kwargs)
