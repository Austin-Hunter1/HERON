"""
One closed-loop tracking channel, driven by per-signal configuration.

This replaces the previous pair of near-identical channels (one for single-code
BPSK, one for L2C's interleaved CM/CL).  They had drifted apart in their
discriminators and state updates, which is how several bugs went unnoticed; the
code topology now lives in `utils.code_components` and which component drives which
loop lives in `LoopDiscriminatorPolicy`, so signals differ only by data.

Architecture, unchanged from before:
  1. `AlignedCorrelator` accumulates over a code-phase-aligned interval.
  2. The channel consumes only COMPLETE intervals.
  3. Discriminators and loop filters update carrier/code state to the value it
     takes at the start of the next correlation epoch.
"""

from __future__ import annotations

import math
import warnings
from collections import deque
from dataclasses import dataclass, field
from enum import Enum

import numpy as np
from scipy.constants import speed_of_light

from utils import sample_streaming

from . import secondary_code
from .bpsk_correlation import correlate__multicomponent
from .code_components import CodeSet, epl_delay_bins
from .nav import bitsync


@dataclass
class TrackingSignalState:
    uptime_epoch_ms: float
    code_phase_ms: float
    code_rate_ms_per_sec: float
    carrier_phase_cycles: float
    carrier_rate_cyc_per_sec: float
    # How far the subcarrier's delay sits from the code's, in chips.  Zero means
    # tied, which is ordinary BOC tracking and every signal but a double-estimator
    # channel.  It does not propagate: both delays advance at the same code rate,
    # so their difference is constant between loop updates.
    subcarrier_offset_chips: float = 0.0

    def propagate_phase(self, new_uptime_epoch_ms: float) -> tuple[float, float]:
        "Return code phase (ms) and carrier phase (cycles) at new uptime epoch, without modifying state."
        dt_sec = (new_uptime_epoch_ms - self.uptime_epoch_ms) * 1e-3
        new_code_phase_ms = self.code_phase_ms + dt_sec * self.code_rate_ms_per_sec
        new_carrier_phase_cycles = self.carrier_phase_cycles + dt_sec * self.carrier_rate_cyc_per_sec
        return new_code_phase_ms, new_carrier_phase_cycles

    def propagate_to_uptime_ms(self, new_uptime_epoch_ms: float) -> "TrackingSignalState":
        new_code_phase_ms, new_carrier_phase_cycles = self.propagate_phase(new_uptime_epoch_ms)
        return TrackingSignalState(
            uptime_epoch_ms=new_uptime_epoch_ms,
            code_phase_ms=new_code_phase_ms,
            code_rate_ms_per_sec=self.code_rate_ms_per_sec,
            carrier_phase_cycles=new_carrier_phase_cycles,
            carrier_rate_cyc_per_sec=self.carrier_rate_cyc_per_sec,
            subcarrier_offset_chips=self.subcarrier_offset_chips,
        )


@dataclass(frozen=True)
class TrackingSignalParameters:
    """Static description of the signal being tracked."""

    code_set: CodeSet
    nominal_code_rate_chips_per_sec: float
    carrier_freq_hz: float
    # Duration of one full pass through the primary code.  Currently only used to
    # document the signal; tiered-code integration will accumulate in units of it.
    primary_period_ms: int = 1

    @property
    def num_components(self) -> int:
        return self.code_set.num_components

    @property
    def chip_period_sec(self) -> float:
        return 1.0 / self.nominal_code_rate_chips_per_sec

    @property
    def subcarrier_ambiguity_chips(self) -> float:
        """
        Spacing of the repeats a NON-COHERENT subcarrier discriminator sees, in
        chips; 0 when the signal has no subcarrier.

        The signed subcarrier correlation repeats every `2/S` chips for a wave of
        `S` sub-chips per chip.  An early-minus-late-POWER discriminator combines
        magnitudes, so it cannot tell the +1 peak from the -1 peak half a period
        later and its ambiguity is half that: `1/S`.  For BOC(1,1) and for L1C's
        TMBOC -- whose base rate is also BOC(1,1) -- that is 0.5 chip, confirmed
        against the measured |R|, which peaks at 0 and +/-0.5 with nulls at +/-0.24.

        The TMBOC pattern rate does not enter: BOC(6,1) sharpens the peak but the
        29 chips in 33 that are BOC(1,1) are what set where it repeats.
        """
        base = {
            int(n) for n in self.code_set.subcarrier_sub_chips_per_chip if n != 0
        }
        if not base:
            return 0.0
        if len(base) > 1:
            raise ValueError(
                f"components disagree on subcarrier rate ({sorted(base)} sub-chips "
                "per chip), so the signal has no single ambiguity interval; the "
                "double estimator would need one loop per rate"
            )
        return 1.0 / base.pop()

    @property
    def chip_length_m(self) -> float:
        return speed_of_light * self.chip_period_sec


@dataclass(frozen=True)
class LoopDiscriminatorPolicy:
    """
    Which correlator components drive which loop.

    `carrier_component` selects the single component the phase/frequency
    discriminators read.  `code_components` are combined non-coherently (and
    power-weighted) for the delay discriminator; with one component of unit weight
    that reduces exactly to the classic single-component form.

    `costas` false means the component is a dataless pilot, so the phase
    discriminator can use the full four-quadrant angle instead of wrapping at
    +/-1/4 cycle -- worth ~6 dB, but only valid once any tiered code has been
    stripped, since an overlay flips sign exactly like data.
    """

    carrier_component: int = 0
    code_components: tuple[int, ...] = (0,)
    costas: bool = True

    def __post_init__(self) -> None:
        if not self.code_components:
            raise ValueError("code_components must not be empty")


# Roles a correlator tap can play.  Named rather than positional because a
# double-estimator layout has five taps on two axes, and "index 1" stops meaning
# anything the moment the layout is not the classic early/prompt/late triple.
# Data bit synchronisation, for the one signal that needs it (GPS L1 C/A).  Two
# seconds of 1 ms prompts gives about fifty transitions at the true phase against
# a handful at every other, which `utils.nav.bitsync` turns into a confidence
# ratio.  Re-testing every 200 prompts rather than every one keeps the
# O(window) histogram off the per-interval path; the window slides, so a channel
# that locks late is not held back by the noise it produced while pulling in.
BIT_SYNC_WINDOW_PROMPTS = 2000
BIT_SYNC_MIN_PROMPTS = 1000
BIT_SYNC_RETRY_PROMPTS = 200

PROMPT = "prompt"
EARLY = "early"
LATE = "late"
SUBCARRIER_EARLY = "subcarrier_early"
SUBCARRIER_LATE = "subcarrier_late"


@dataclass(frozen=True)
class TapLayout:
    """
    Where the correlator taps sit, as (code delay, subcarrier delay) pairs.

    A BOC signal is a code multiplied by a subcarrier, and the two delays need not
    be the same number.  Tying them is what gives a BOC correlation its side peaks;
    separating them is the double estimator (`double_estimator_tap_layout`).  Every
    tap therefore carries both offsets, and an ordinary BPSK layout is simply the
    case where the subcarrier offsets equal the code offsets.

    Taps are addressed by ROLE, not by index.  `epl_delay_bins`'s docstring has
    said since the beginning that the layout is a tuple rather than a count plus a
    step because BOC would need more taps; this is that promise kept.
    """

    code_offsets_chips: tuple[float, ...]
    subcarrier_offsets_chips: tuple[float, ...]
    roles: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        if len(self.code_offsets_chips) != len(self.subcarrier_offsets_chips):
            raise ValueError(
                f"a tap has one code offset and one subcarrier offset, got "
                f"{len(self.code_offsets_chips)} and {len(self.subcarrier_offsets_chips)}"
            )
        if not self.code_offsets_chips:
            raise ValueError("a tap layout needs at least one tap")
        seen = dict(self.roles)
        if len(seen) != len(self.roles):
            raise ValueError(f"duplicate tap role in {self.roles}")
        for role, index in self.roles:
            if not 0 <= index < self.num_taps:
                raise ValueError(
                    f"role {role!r} points at tap {index}, but the layout has "
                    f"{self.num_taps}"
                )
        if PROMPT not in seen:
            raise ValueError("every layout needs a prompt tap")

    @property
    def num_taps(self) -> int:
        return len(self.code_offsets_chips)

    @property
    def tracks_subcarrier(self) -> bool:
        """True when the layout carries taps displaced along the subcarrier axis."""
        names = dict(self.roles)
        return SUBCARRIER_EARLY in names and SUBCARRIER_LATE in names

    def index(self, role: str) -> int:
        for name, position in self.roles:
            if name == role:
                return position
        raise KeyError(f"no {role!r} tap in this layout; have {[n for n, _ in self.roles]}")

    @property
    def code_offsets(self) -> np.ndarray:
        return np.ascontiguousarray(self.code_offsets_chips, dtype=np.float64)

    @property
    def subcarrier_offsets(self) -> np.ndarray:
        return np.ascontiguousarray(self.subcarrier_offsets_chips, dtype=np.float64)


def epl_tap_layout(chip_spacing: float) -> TapLayout:
    """Classic early/prompt/late, subcarrier tied to the code."""
    offsets = epl_delay_bins(chip_spacing)
    return TapLayout(
        code_offsets_chips=offsets,
        subcarrier_offsets_chips=offsets,
        roles=((EARLY, 0), (PROMPT, 1), (LATE, 2)),
    )


def double_estimator_tap_layout(
    code_spacing_chips: float, subcarrier_spacing_chips: float
) -> TapLayout:
    """
    Five taps on two axes: early/late along the code, early/late along the
    subcarrier, and one prompt shared by both.

    The code taps see the plain BPSK triangle -- one peak, a chip wide -- and the
    subcarrier taps see a wave six times steeper but repeating.  Neither pair is
    displaced along the other's axis, which is what keeps the two discriminators
    independent.

    THE DOUBLE ESTIMATOR
    --------------------
    Hodgart, M.S., P.D. Blunt and M. Unwin, "Double Estimator -- A New Receiver
    Principle for Tracking BOC Signals", Inside GNSS, Spring 2008, pp. 26-36.
    Also Hodgart and Blunt, "Dual estimate receiver of binary offset carrier
    modulated signals for global navigation satellite systems", Electronics
    Letters 43(16), 2007, and Hodgart, Blunt and Unwin, ION GNSS 2007.

    The code and the subcarrier of course leave the satellite with the SAME
    delay -- they are multiplied together there, and nothing separates them in
    flight.  Estimating them independently is deliberate over-parameterisation.

    A conventional receiver constrains its search to the line tau_code =
    tau_subcarrier, and along that line the correlation is the multi-peaked BOC
    ACF: R(tau, tau) = Lambda(tau) * S(tau), whose extra peaks are just S's own,
    weighted by the code triangle.  That is where false lock comes from -- not
    from anything about the signal, but from the shape of that one slice.

    Relax the constraint and the surface separates: Lambda alone has one peak and
    no ambiguity, S alone is steep but periodic.  Two easy one-dimensional
    problems in place of one hard one.  The constraint is then re-imposed at the
    end, which is what `subcarrier_ambiguity_chips` and the rounding in
    `run_loop_filter` do -- the coarse estimate says which cycle of the fine one
    is the right one.

    The same trade as resolving an RTK carrier-phase integer against a code
    pseudorange: two measurements of one quantity, one unambiguous and noisy, one
    precise and ambiguous, combined by rounding their difference.  A subcarrier
    cycle instead of a carrier cycle.

    The alternative that keeps one delay is bump jumping (Fine, P. and W. Wilson,
    "Tracking algorithm for GPS offset carrier signals", ION NTM 1999): very-early
    and very-late taps detect that the loop settled on a side peak and jump it.
    That recovers from false lock; this cannot have one.
    """
    if code_spacing_chips <= 0.0 or subcarrier_spacing_chips <= 0.0:
        raise ValueError("tap spacings must be positive")
    return TapLayout(
        code_offsets_chips=(0.0, code_spacing_chips, -code_spacing_chips, 0.0, 0.0),
        subcarrier_offsets_chips=(
            0.0, 0.0, 0.0, subcarrier_spacing_chips, -subcarrier_spacing_chips,
        ),
        roles=(
            (PROMPT, 0), (EARLY, 1), (LATE, 2),
            (SUBCARRIER_EARLY, 3), (SUBCARRIER_LATE, 4),
        ),
    )


@dataclass
class DelayDopplerCorrelatorConfig:
    """Correlator tap layout, plus an optional bank of Doppler hypotheses."""

    tap_layout: TapLayout
    num_dopplers: int = 1
    doppler_offset_hz: float = 0.0
    doppler_step_hz: float = 0.0

    @property
    def num_delays(self) -> int:
        return self.tap_layout.num_taps


class AlignedCorrelator:
    """
    Request-driven correlator engine.

    The tracking channel owns all dynamic signal and epoch state.  The correlator
    owns only static configuration and performs in-place accumulation for the
    epoch that the channel requests.
    """

    def __init__(self, config: DelayDopplerCorrelatorConfig, num_components: int):
        self.config = config
        # Materialised once: the kernel wants contiguous float64 and these are read
        # on every buffer.
        self._code_offsets = config.tap_layout.code_offsets
        self._subcarrier_offsets = config.tap_layout.subcarrier_offsets
        shape = (config.num_delays, config.num_dopplers, num_components)
        # `corr_grid` holds one correlation interval -- for a signal with a tiered
        # code, one primary code period.  `epoch_grid` is where those intervals are
        # folded, sign-corrected, to build a longer coherent accumulation.  With no
        # tiered code the fold is a plain copy of a single interval, so the two are
        # equivalent and the epoch is one interval long.
        self.corr_grid = np.zeros(shape, dtype=np.complex64)
        self.epoch_grid = np.zeros(shape, dtype=np.complex64)
        # Every delay/doppler bin accumulates the same number of samples per call,
        # so a single running count is sufficient.
        self.corr_count = 0
        self.epoch_count = 0

    def reset(self) -> None:
        self.corr_grid.fill(0.0)
        self.corr_count = 0

    def reset_epoch(self) -> None:
        self.epoch_grid.fill(0.0)
        self.epoch_count = 0

    def fold(self, signs: np.ndarray) -> None:
        """
        Add the finished interval into the epoch accumulator, wiping off the
        tiered code by multiplying each component by its overlay chip.

        Signs are +/-1 int8, so complex64 stays complex64 and a sign of +1 is
        exact -- an un-synced or overlay-free signal folds bit-identically to a
        plain accumulation.

        Must only ever be called on a COMPLETE interval; folding a partially
        accumulated one would corrupt the epoch with a short correlation.
        """
        self.epoch_grid += self.corr_grid * signs[None, None, :]
        self.epoch_count += self.corr_count

    def accumulate(
        self,
        buffer: sample_streaming.SampleBuffer,
        accum_start_uptime_ms: float,
        accum_stop_uptime_ms: float,
        signal_params: TrackingSignalParameters,
        signal_state: TrackingSignalState,
    ) -> None:
        if not (accum_start_uptime_ms < accum_stop_uptime_ms):
            raise ValueError("accumulation stop must be after accumulation start")

        # Determine start and end samples, trimmed to the buffer.
        # Bounds have to be both floor or ceiling; ceiling gives the exact half-open
        # window [start, stop), so consecutive intervals partition the stream.
        accum_start_sample_index = max(
            0,
            int(np.ceil((accum_start_uptime_ms - buffer.start_uptime_ms) * buffer.samp_rate / 1000)),
        )
        accum_stop_sample_index = min(
            len(buffer.samples),
            int(np.ceil((accum_stop_uptime_ms - buffer.start_uptime_ms) * buffer.samp_rate / 1000)),
        )
        if accum_start_sample_index >= accum_stop_sample_index:
            # The window does not overlap this buffer: it can begin inside the final
            # sample period, or be shorter than one sample period. The channel will mark
            # the interval PARTIAL and resume from the interval start on the next buffer.
            return

        actual_accum_start_uptime_ms = (
            buffer.start_uptime_ms + accum_start_sample_index / buffer.samp_rate * 1000
        )
        samples = buffer.samples[accum_start_sample_index:accum_stop_sample_index]
        num_accum_samples = len(samples)

        # propagation uses correlation doppler because we may be accumulating a partial interval
        # (in that case, we want to use the same doppler for propagation as we do for correlation)
        # note: code rate will be decoupled from doppler
        dt_sec = (actual_accum_start_uptime_ms - signal_state.uptime_epoch_ms) * 1e-3
        code_phase_ms = signal_state.code_phase_ms + dt_sec * signal_state.code_rate_ms_per_sec
        code_phase_chips = code_phase_ms * 1e-3 * signal_params.nominal_code_rate_chips_per_sec
        code_rate_chips_per_sec = (
            signal_state.code_rate_ms_per_sec * 1e-3 * signal_params.nominal_code_rate_chips_per_sec
        )
        for i_dopp in range(self.config.num_dopplers):
            corr_doppler_offset_hz = (
                self.config.doppler_offset_hz + i_dopp * self.config.doppler_step_hz
            )
            corr_doppler_hz = signal_state.carrier_rate_cyc_per_sec + corr_doppler_offset_hz
            corr_carrier_phase_cycles = signal_state.carrier_phase_cycles + dt_sec * corr_doppler_hz
            correlate__multicomponent(
                samples,
                buffer.samp_rate,
                corr_carrier_phase_cycles,
                corr_doppler_hz,
                signal_params.code_set,
                code_rate_chips_per_sec,
                code_phase_chips,
                self._code_offsets,
                self.corr_grid[:, i_dopp, :],
                signal_state.subcarrier_offset_chips,
                self._subcarrier_offsets,
            )
        self.corr_count += num_accum_samples


class CorrelatorStatus(Enum):
    """
    Correlator result status flag for a given correlation interval.

    Correlation interval can be complete or partial.
    Partial intervals can be at front, end, or both sides of the accumulation window.
    """

    CLEARED = "UNDEFINED"
    PARTIAL = "PARTIAL"
    COMPLETE = "COMPLETE"


class TrackingLoopMode(Enum):
    FLL = "FLL"
    PLL = "PLL"


@dataclass
class TrackingLoopState:
    mode: TrackingLoopMode
    history_size: int = 10

    def __post_init__(self):
        self.prompt_corr_history = np.zeros(self.history_size, dtype=complex)
        self.history_index = 0
        self.history_filled = False

    def update_history(self, prompt_corr: complex) -> None:
        self.prompt_corr_history[self.history_index] = prompt_corr
        self.history_index += 1
        if self.history_index >= self.history_size:
            self.history_index = 0
            self.history_filled = True

    def get_last_prompt_corr(self) -> complex | None:
        if self.history_index == 0:
            if self.history_filled:
                return self.prompt_corr_history[-1]
            return None
        return self.prompt_corr_history[self.history_index - 1]

    def compute_prompt_corr_history_circ_length(self, costas: bool = False) -> float:
        iq = (
            self.prompt_corr_history
            if self.history_filled
            else self.prompt_corr_history[: self.history_index]
        )
        if len(iq) == 0:
            return 0.0
        angles = np.angle(iq)
        if costas:
            angles *= 2.0
        return float(np.abs(np.mean(np.exp(1j * angles))))


def estimate_cn0_vsm(prompt_power: np.ndarray, integration_time_s: float) -> float:
    """
    Carrier-to-noise density from prompt power, by the variance summing method.

    VSM (Van Dierendonck) separates signal from noise using the second and fourth
    moments of the prompt magnitude, which is all it needs -- no knowledge of the
    data bits, the carrier phase error or the overlay sign, because none of those
    survive `|P|**2`.  That is what makes it usable before the secondary code is
    synchronised, and why its input needs no wipe-off.

        mu2 = E[|P|^2]                    mu4 = E[|P|^4]
        Pd  = sqrt(2*mu2^2 - mu4)         Pn  = mu2 - Pd
        C/N0 = (Pd / Pn) / T

    `integration_time_s` must be the integration time of the samples given, and the
    samples must all share it: C/N0 is a *density*, so the whole result scales with
    this number.  Feeding it epochs of mixed length silently reports the wrong
    answer, which is why the channel collects one correlation interval at a time.

    Returns NaN rather than raising where the estimator degenerates -- the
    discriminant goes negative at low C/N0, and the noise term vanishes at very
    high -- because a NaN leaves a gap in the record while an exception would take
    down a whole tracking run over one bad window.
    """
    if prompt_power.size == 0:
        return float("nan")
    mu2 = float(np.mean(prompt_power))
    mu4 = float(np.mean(prompt_power ** 2))
    discriminant = 2.0 * mu2 ** 2 - mu4
    if discriminant <= 0.0:
        return float("nan")
    signal_power = float(np.sqrt(discriminant))
    noise_power = mu2 - signal_power
    if noise_power <= 0.0:
        return float("nan")
    return float(10.0 * np.log10((signal_power / noise_power) / integration_time_s))


@dataclass
class CN0EstimatorParameters:
    """
    How the C/N0 estimate is windowed.

    `period_ms` samples -- one correlation interval each -- go into every estimate,
    and a new one is emitted every `hop_ms = period_ms * (1 - overlap_fraction)`.
    Longer periods trade time resolution for a steadier number: measured on the
    rooftop collect, the estimate's 1-sigma spread is about 0.6-0.9 dB at 100 ms,
    0.4-0.9 dB at 200 ms and 0.1-0.2 dB at 1000 ms.

    Note the period must be well under the tracked duration or there is nothing to
    plot: at the 1000 ms default, one second of tracking yields exactly one point.
    """

    period_ms: int = 1000
    overlap_fraction: float = 0.5
    enabled: bool = True

    def __post_init__(self) -> None:
        # VSM's fourth moment is the fragile one, and below roughly a hundred
        # samples it stops being trustworthy.  Refusing beats quietly returning a
        # number nobody can tell is wrong.
        if self.period_ms < 100:
            raise ValueError(
                f"period_ms must be at least 100 correlation intervals for the "
                f"fourth-moment estimate to be meaningful, got {self.period_ms}"
            )
        if not 0.0 <= self.overlap_fraction < 1.0:
            raise ValueError(
                f"overlap_fraction must be in [0, 1), got {self.overlap_fraction}"
            )
        self.hop_ms = int(round(self.period_ms * (1.0 - self.overlap_fraction)))
        if self.hop_ms < 1:
            raise ValueError(
                f"period_ms {self.period_ms} at overlap {self.overlap_fraction} gives a "
                f"hop of {self.hop_ms} intervals; it must be at least 1"
            )


# One correlation interval, in milliseconds.
#
# !!! DO NOT CHANGE THIS. !!!
#
# It is not a tuning knob, and nothing in the repository is correct at any other
# value.  Three separate things depend on it being 1:
#
#   1. It is the granularity at which a wipe-off sign can be applied, so it must
#      DIVIDE the overlay chip -- one primary code period -- or an interval would
#      span a sign flip and cancel itself.  It need not equal it: L5's period is
#      1 ms and L1C's is 10 ms, and the channel folds ten intervals per overlay
#      chip on L1C (see `_intervals_per_primary_period`).  Assuming equality was a
#      real defect, and one that hid for a long time because L5 was the only
#      signal with an overlay and the two coincide there.  L2C's period is 20 ms
#      and always was -- it simply has no overlay for the mismatch to show up in.
#   2. Interval boundaries land on integer milliseconds of code phase, and every
#      period that matters (code, data symbol, overlay chip) is a whole number of
#      milliseconds.  That is what guarantees no interval straddles a boundary,
#      and it only holds while this divides all of them.  At 3 ms, an L2C interval
#      [18, 21) would cross the CNAV symbol boundary at 20.
#   3. `coherent_duration_ms` is expressed in milliseconds and converted to a
#      count of intervals by dividing by this.
#
# To integrate longer, raise `coherent_duration_ms`.  Intervals are folded --
# with overlay wipe-off between them -- to build the epoch, which is exactly why
# extending integration never means lengthening the interval.
CORRELATION_INTERVAL_MS = 1


@dataclass
class TrackingLoopParameters:
    """
    Loop bandwidths and how long one coherent accumulation lasts.

    `coherent_duration_ms` is the whole story on integration length: it is the
    epoch the discriminators see, the update period the loop gains are built for,
    and (divided by `CORRELATION_INTERVAL_MS`) the number of intervals folded into
    each epoch.  It must divide the shortest interval over which the components
    driving the loops hold a constant sign -- see `validate_coherent_duration`,
    which the channel calls on construction.
    """

    DLL_bandwidth_hz: float
    PLL_bandwidth_hz: float
    FLL_bandwidth_hz: float
    coherent_duration_ms: int = CORRELATION_INTERVAL_MS
    EPL_chip_spacing: float = 0.5
    prompt_corr_circ_length_threshold: float = 0.9
    # Double estimator.  Both must be positive to enable it: the channel then lays
    # out five taps instead of three and runs a second delay loop on the subcarrier
    # axis.  The code loop can afford to be narrow, because all it has to do is
    # stay inside half an ambiguity interval -- +/-0.25 chip on L1C -- while the
    # subcarrier loop carries the precision.
    subcarrier_bandwidth_hz: float = 0.0
    subcarrier_chip_spacing: float = 0.0

    @property
    def intervals_per_epoch(self) -> int:
        return self.coherent_duration_ms // CORRELATION_INTERVAL_MS

    def __post_init__(self):
        if (
            self.coherent_duration_ms < CORRELATION_INTERVAL_MS
            or self.coherent_duration_ms % CORRELATION_INTERVAL_MS
        ):
            raise ValueError(
                f"coherent_duration_ms must be a positive multiple of "
                f"{CORRELATION_INTERVAL_MS} ms, got {self.coherent_duration_ms}"
            )
        update_period_seconds = self.coherent_duration_ms * 1e-3
        # 1st-order DLL
        # Gain = 4 * Bn * T, where Bn is the DLL bandwidth in Hz and T is the update period in seconds
        self.DLL_filter_coeff = 4.0 * update_period_seconds * self.DLL_bandwidth_hz

        # 2nd-order PLL
        # Gain = 2 * zeta * omega_n * T
        zeta = 1.0 / np.sqrt(2.0)
        omega_n = 2.0 * self.PLL_bandwidth_hz / (zeta + 1.0 / (4.0 * zeta))
        self.PLL_filter_coeffs = (
            2.0 * zeta * omega_n * update_period_seconds
            - 1.5 * omega_n**2 * update_period_seconds**2,
            omega_n**2 * update_period_seconds,
        )

        # 1st-order FLL
        # Gain = 4 * Bn * T, where Bn is the FLL bandwidth in Hz and T is the update period in seconds
        self.FLL_filter_coeff = 4.0 * self.FLL_bandwidth_hz * update_period_seconds

        # 1st-order subcarrier DLL, same form as the code one.
        if (self.subcarrier_bandwidth_hz > 0.0) != (self.subcarrier_chip_spacing > 0.0):
            raise ValueError(
                "subcarrier_bandwidth_hz and subcarrier_chip_spacing enable the "
                "double estimator together; got "
                f"{self.subcarrier_bandwidth_hz} Hz and {self.subcarrier_chip_spacing} chips"
            )
        self.subcarrier_filter_coeff = (
            4.0 * update_period_seconds * self.subcarrier_bandwidth_hz
        )

    @property
    def tracks_subcarrier(self) -> bool:
        return self.subcarrier_bandwidth_hz > 0.0 and self.subcarrier_chip_spacing > 0.0


class SignalTrackingOutputs:
    """
    Per-epoch tracking history.

    Correlator outputs are always (capacity, num_components), even for
    single-component signals.  Assigning a whole component vector per epoch is what
    makes it impossible to silently broadcast one component across all columns.
    """

    def __init__(
        self,
        capacity: int,
        num_components: int = 1,
        cn0_capacity: int = 0,
        tap_layout: "TapLayout | None" = None,
    ):
        self.capacity = capacity
        self.num_components = num_components
        self.tap_layout = tap_layout if tap_layout is not None else epl_tap_layout(0.5)
        self.uptime_epoch_ms = np.zeros(capacity, dtype=float)
        self.carr_phase_errors_cycles = np.zeros(capacity, dtype=float)
        self.code_phase_errors_chips = np.zeros(capacity, dtype=float)
        # One array for every tap, addressed by role.  `early_corr`/`prompt_corr`/
        # `late_corr` remain as views onto it, so a layout that happens to be the
        # classic triple is indistinguishable from the old three arrays.
        self.corr = np.zeros(
            (capacity, self.tap_layout.num_taps, num_components), dtype=complex
        )
        # Displacement of the subcarrier's delay from the code's, per epoch; flat
        # zero unless a double-estimator layout is tracking it.
        self.subcarrier_offset_chips = np.zeros(capacity, dtype=float)
        self.carr_phase_cycles = np.zeros(capacity, dtype=float)
        self.doppler_freq_hz = np.zeros(capacity, dtype=float)
        self.code_phase_ms = np.zeros(capacity, dtype=float)
        self.delta_omega = np.zeros(capacity, dtype=float)
        self.prompt_corr_circ_length = np.zeros(capacity, dtype=float)
        # Which carrier loop actually filtered this epoch.  Recorded because the
        # FLL->PLL handover is invisible in the correlator outputs alone, and
        # reading a lock transient correctly means knowing which loop was running.
        self.pll_mode = np.zeros(capacity, dtype=bool)
        # How long this epoch's coherent accumulation actually was.  It is not
        # constant across a run: `_maybe_extend_coherent_duration` lengthens it once
        # the overlay is stripped and the PLL has locked.  Without it a consumer has
        # to infer epoch length by differencing timestamps, which is fragile exactly
        # where it matters -- across the extension boundary.
        self.epoch_duration_ms = np.zeros(capacity, dtype=float)
        # Whether the epoch grid was anchored to a data bit boundary this channel
        # MEASURED, as opposed to a multiple of the symbol period in code phase.
        # The two coincide for every signal whose code phase locates the boundary,
        # and on L1 C/A they do not -- so a consumer that wants to treat an epoch
        # boundary as a symbol boundary has to know which of the two it is looking
        # at.  Nothing in the outputs distinguishes them otherwise: a misanchored
        # grid and a correct one produce arrays of identical shape and plausible
        # content, differing only by whole milliseconds of range.
        self.bit_synced = np.zeros(capacity, dtype=bool)
        # Whether the tiered (overlay) code was being wiped off for this epoch.
        # A navigation-message decoder needs this: before sync the epochs are not
        # symbol-aligned and the overlay is still flipping the data component's
        # sign, so those epochs are not symbols and must be dropped.
        self.overlay_synced = np.zeros(capacity, dtype=bool)
        self.output_index = 0

        # C/N0 lands on its own cadence -- one estimate per hop of correlation
        # intervals, not one per epoch -- so it gets its own arrays and its own
        # index rather than being forced onto the epoch axis.
        self.cn0_capacity = cn0_capacity
        self.cn0_dbhz = np.zeros((cn0_capacity, num_components), dtype=float)
        self.cn0_uptime_ms = np.zeros(cn0_capacity, dtype=float)
        self.cn0_index = 0

    def tap(self, role: str) -> np.ndarray:
        """All epochs of one named tap, shape (capacity, num_components)."""
        return self.corr[:, self.tap_layout.index(role), :]

    @property
    def early_corr(self) -> np.ndarray:
        return self.tap(EARLY)

    @property
    def prompt_corr(self) -> np.ndarray:
        return self.tap(PROMPT)

    @property
    def late_corr(self) -> np.ndarray:
        return self.tap(LATE)

    @property
    def valid(self) -> slice:
        """
        Slice covering only the epochs actually written. The arrays above are
        pre-allocated to `capacity`; a run shorter than that leaves the tail
        zero-filled, which silently draws a spurious line/point back through
        (0, 0) if plotted unsliced.
        """
        return slice(0, self.output_index)

    @property
    def cn0_valid(self) -> slice:
        """Slice covering only the C/N0 estimates actually written."""
        return slice(0, self.cn0_index)


@dataclass
class CorrelationInterval:
    start_code_phase_ms: int
    duration_ms: int

    @property
    def stop_code_phase_ms(self) -> int:
        return self.start_code_phase_ms + self.duration_ms

    def increment(self) -> None:
        self.start_code_phase_ms += self.duration_ms

    def compute_start_and_stop_uptime_ms(
        self, signal_state: TrackingSignalState
    ) -> tuple[float, float]:
        code_phase_ms = signal_state.code_phase_ms
        code_rate_ms_per_sec = signal_state.code_rate_ms_per_sec
        start_uptime_ms = (
            self.start_code_phase_ms - code_phase_ms
        ) / code_rate_ms_per_sec * 1e3 + signal_state.uptime_epoch_ms
        stop_uptime_ms = (
            self.stop_code_phase_ms - code_phase_ms
        ) / code_rate_ms_per_sec * 1e3 + signal_state.uptime_epoch_ms
        return start_uptime_ms, stop_uptime_ms


def _wrap_cycles(cycles: float, half_range: float) -> float:
    """Wrap a phase in cycles to +/-half_range."""
    period = 2.0 * half_range
    return float(np.mod(cycles + half_range, period) - half_range)


# A discrete loop stays well damped only while its noise bandwidth times its
# update period stays small; the usual guidance is Bn*T <~ 0.25, with 0.1 leaving
# comfortable margin.  Extending coherent integration multiplies T directly, so
# this is the binding constraint on how far integration can be pushed.
MAX_BANDWIDTH_TIME_PRODUCT = 0.1


def coherent_duration_limits_ms(
    signal_params: TrackingSignalParameters,
    overlay_stripped: bool,
) -> list[tuple[int, str]]:
    """
    Every bound on one coherent accumulation, with what imposes it.

    An accumulation is only coherent while the thing being accumulated keeps one
    sign.  Two things flip it, and which one binds depends on the state:

      un-stripped overlay   one chip per primary code period.  Until the tiered
                            code is synchronised and wiped off, nothing longer
                            than one period is safe.
      data symbol           `symbol_period_ms` on the component.  A dataless
                            pilot has none, so only Doppler bounds it.

    Every component contributes a bound, not only the ones driving the loops.
    One epoch serves the whole signal -- each component accumulates over it and
    each is emitted -- so a length that suits the pilot but overruns a data
    component leaves that component's output quietly self-cancelling.  L5 is the
    case in point: its Q pilot is dataless and would tolerate any length, but I
    carries 10 ms CNAV symbols, so 10 ms is the signal's limit and 20 ms would
    wreck I while looking perfectly healthy on Q.

    Returned as a list rather than reduced to the minimum because the two callers
    want different things from it: `validate_coherent_duration` names the
    component a bad length actually violates, while `clamp_coherent_duration_ms`
    needs a length legal for all of them at once.  An empty list means nothing
    bounds the accumulation but Doppler -- every component a dataless pilot with
    its overlay already stripped.
    """
    limits: list[tuple[int, str]] = []
    for component in signal_params.code_set.components:
        if component.overlay is not None and not overlay_stripped:
            limits.append((
                signal_params.primary_period_ms,
                f"{component.name!r}'s overlay chip (not yet stripped)",
            ))
        elif component.symbol_period_ms is not None:
            limits.append((
                component.symbol_period_ms, f"{component.name!r}'s data symbol"
            ))
        # else: dataless pilot with no overlay in the way, so nothing to bound it
    return limits


def clamp_coherent_duration_ms(
    signal_params: TrackingSignalParameters,
    requested_ms: int,
    overlay_stripped: bool,
) -> int:
    """
    Round `requested_ms` down onto the lengths this signal can actually use.

    Two constraints, and rounding down satisfies both at once.  The length must
    not exceed any component's limit, and it must DIVIDE each of them: epochs are
    anchored to multiples of their own length in code phase, so with N dividing
    the symbol period S the epochs tile each symbol exactly, while N = 8 against
    S = 20 puts the epoch [16, 24) across the boundary at 20 however it is
    aligned.  "Divides every limit" is "divides their gcd", which is why the
    search below is over divisors of one number rather than over every component.

    The result is at least one correlation interval, because 1 divides
    everything -- so there is always a legal answer to fall back to and this
    never has to report failure.
    """
    limits = coherent_duration_limits_ms(signal_params, overlay_stripped)
    if not limits:
        return requested_ms
    grid_ms = math.gcd(*(limit for limit, _ in limits)) if len(limits) > 1 else limits[0][0]
    duration_ms = min(requested_ms, grid_ms)
    while grid_ms % duration_ms:
        duration_ms -= 1
    return duration_ms


def validate_coherent_duration(
    signal_params: TrackingSignalParameters,
    policy: LoopDiscriminatorPolicy,
    coherent_duration_ms: int,
    overlay_stripped: bool,
) -> None:
    """
    Reject a coherent integration that would span a sign change.

    A hard error, and deliberately so: by the time a length reaches here it has
    already been through `clamp_coherent_duration_ms`, so anything it rejects is
    an internal inconsistency rather than a user asking for too much.  What a
    user asked for is capped with a warning, in `signal_interfaces`; what the
    channel then builds out of that is checked here and must be right.

    See `coherent_duration_limits_ms` for where the bounds come from.
    """
    for limit_ms, reason in coherent_duration_limits_ms(signal_params, overlay_stripped):
        if coherent_duration_ms > limit_ms:
            raise ValueError(
                f"coherent_duration_ms {coherent_duration_ms} exceeds {limit_ms} ms, "
                f"the period of {reason}; the accumulation would span a sign change"
            )
        if limit_ms % coherent_duration_ms:
            raise ValueError(
                f"coherent_duration_ms {coherent_duration_ms} does not divide {limit_ms} ms, "
                f"the period of {reason}; epochs are anchored to multiples of their own "
                f"length, so some epoch would straddle a boundary"
            )


def _retune_for_update_period(
    loop_params: TrackingLoopParameters,
    update_period_ms: float,
    max_bandwidth_time_product: float = MAX_BANDWIDTH_TIME_PRODUCT,
) -> TrackingLoopParameters:
    """
    Rebuild the loop filter for a longer update period, narrowing bandwidths as
    needed to keep it stable.

    Two separate things have to happen when coherent integration is extended:

    1. The gains must be recomputed, because all three are proportional to the
       update period.  A 20 ms epoch driving gains built for 1 ms is a 20x
       mistuning.
    2. The bandwidths must be capped.  Holding a 20 Hz PLL across a 20 ms epoch
       gives Bn*T = 0.4, well past the guideline, and the loop becomes
       under-damped: it passes measurement noise straight into the estimates.
       Measured on L5 at 20 ms, Doppler jitter grew from 0.022 to 0.102 Hz as
       noise rose, while the capped loop held flat near 0.047 Hz.  (Coherent gain
       itself is unaffected -- the loss is in the loop, not the accumulation.)

    Narrowing is affordable precisely because the integration is longer: the
    extra coherent gain is what pays for the reduced bandwidth.  Each loop is
    capped independently, so a loop already slow enough is left alone.
    """
    update_period_sec = update_period_ms * 1e-3
    bandwidth_cap_hz = max_bandwidth_time_product / update_period_sec
    return TrackingLoopParameters(
        DLL_bandwidth_hz=min(loop_params.DLL_bandwidth_hz, bandwidth_cap_hz),
        PLL_bandwidth_hz=min(loop_params.PLL_bandwidth_hz, bandwidth_cap_hz),
        FLL_bandwidth_hz=min(loop_params.FLL_bandwidth_hz, bandwidth_cap_hz),
        coherent_duration_ms=int(update_period_ms),
        EPL_chip_spacing=loop_params.EPL_chip_spacing,
        prompt_corr_circ_length_threshold=loop_params.prompt_corr_circ_length_threshold,
        subcarrier_bandwidth_hz=min(loop_params.subcarrier_bandwidth_hz, bandwidth_cap_hz),
        subcarrier_chip_spacing=loop_params.subcarrier_chip_spacing,
    )


class TrackingChannel:
    """
    Tracking channel architecture:
    1. AlignedCorrelator accumulates and emits code-aligned correlations.
    2. TrackingChannel consumes COMPLETE results only.
    3. Loop discriminators + filters update carrier/code to reflect state at start of correlation epochs.
    """

    def __init__(
        self,
        loop_params: TrackingLoopParameters,
        signal_params: TrackingSignalParameters,
        initial_signal_state: TrackingSignalState,
        output_capacity: int = 60000,
        discriminator_policy: LoopDiscriminatorPolicy | None = None,
        correlator_config: DelayDopplerCorrelatorConfig | None = None,
        synced_policy: LoopDiscriminatorPolicy | None = None,
        synced_coherent_duration_ms: int = CORRELATION_INTERVAL_MS,
        initial_overlay_counter: int | None = None,
        cn0_params: CN0EstimatorParameters | None = None,
        overlay_search: secondary_code.OverlaySearch | None = None,
        overlay_prompts_to_observe: int | None = None,
    ) -> None:
        # The bandwidth-time product has to hold for the epoch this channel STARTS
        # with, not only for one it may later extend to.
        # `_retune_for_update_period` enforces it on the extension and nothing
        # enforced it here, so a caller asking for a long epoch with bandwidths
        # chosen for a short one got a loop far past the guideline -- at 10 ms the
        # default 50 Hz FLL is Bn*T = 0.5, five times the cap. Such a loop does not
        # lock at ANY carrier-to-noise ratio, and nothing in the outputs says why:
        # the channel simply runs to completion having never left FLL.
        #
        # Narrowed rather than rejected, because the configuration is reasonable and
        # only its bandwidths are not -- and narrowing is what the extension path
        # already does. Warned about rather than done silently, because these are
        # numbers the caller chose deliberately and the tracking behaviour changes.
        capped = _retune_for_update_period(loop_params, loop_params.coherent_duration_ms)
        narrowed = [
            (name, getattr(loop_params, name), getattr(capped, name))
            for name in (
                "DLL_bandwidth_hz", "PLL_bandwidth_hz",
                "FLL_bandwidth_hz", "subcarrier_bandwidth_hz",
            )
            if getattr(capped, name) < getattr(loop_params, name)
        ]
        if narrowed:
            detail = ", ".join(
                f"{name} {was:g} -> {now:g} Hz" for name, was, now in narrowed
            )
            warnings.warn(
                f"coherent_duration_ms={loop_params.coherent_duration_ms} caps loop "
                f"bandwidths at {MAX_BANDWIDTH_TIME_PRODUCT / (loop_params.coherent_duration_ms * 1e-3):g} Hz "
                f"(Bn*T <= {MAX_BANDWIDTH_TIME_PRODUCT:g}); narrowing {detail}. Choose "
                "bandwidths for the epoch length, or shorten the epoch.",
                RuntimeWarning,
                stacklevel=2,
            )
            loop_params = capped

        self.loop_params = loop_params
        self.signal_params = signal_params
        self.policy = discriminator_policy or LoopDiscriminatorPolicy()

        num_components = signal_params.num_components
        for index in (self.policy.carrier_component, *self.policy.code_components):
            if not (0 <= index < num_components):
                raise ValueError(
                    f"discriminator policy references component {index}, but the signal "
                    f"has {num_components} ({signal_params.code_set.names})"
                )

        self.signal_state = initial_signal_state
        self.loop_state = TrackingLoopState(mode=TrackingLoopMode.FLL)
        # --- C/N0 estimator ---------------------------------------------------
        # Sized from the epoch budget: output_capacity epochs of the *initial*
        # coherent duration is the run length in ms, and a channel that later
        # extends covers the same wall time in fewer epochs, so this stays an
        # over-estimate rather than truncating the record.
        self.cn0_params = cn0_params if cn0_params is not None else CN0EstimatorParameters()
        run_duration_ms = output_capacity * loop_params.coherent_duration_ms
        cn0_capacity = (
            run_duration_ms // self.cn0_params.hop_ms + 2 if self.cn0_params.enabled else 0
        )
        # One correlation interval per slot; filled before the epoch machinery
        # touches anything, so the samples are always CORRELATION_INTERVAL_MS long
        # whatever the coherent duration happens to be.
        self._cn0_power = np.zeros(
            (self.cn0_params.period_ms, num_components), dtype=float
        )
        self._cn0_fill = 0          # how many slots hold real data yet
        self._cn0_write = 0         # ring write position
        self._cn0_since_estimate = 0

        # Built after the correlator config below, so the outputs know the layout.
        self.outputs: SignalTrackingOutputs

        if correlator_config is None:
            if loop_params.tracks_subcarrier:
                if not signal_params.code_set.has_subcarrier:
                    raise ValueError(
                        "the double estimator needs a subcarrier to track, but "
                        f"{signal_params.code_set.names} has none"
                    )
                layout = double_estimator_tap_layout(
                    loop_params.EPL_chip_spacing, loop_params.subcarrier_chip_spacing
                )
            else:
                layout = epl_tap_layout(loop_params.EPL_chip_spacing)
            correlator_config = DelayDopplerCorrelatorConfig(tap_layout=layout)
        self._prompt_tap = correlator_config.tap_layout.index(PROMPT)
        self.outputs = SignalTrackingOutputs(
            capacity=output_capacity,
            num_components=num_components,
            cn0_capacity=cn0_capacity,
            tap_layout=correlator_config.tap_layout,
        )
        self.correlator = AlignedCorrelator(correlator_config, num_components=num_components)
        self.correlator_status = CorrelatorStatus.CLEARED

        start_code_phase_ms, _ = self.signal_state.propagate_phase(
            self.signal_state.uptime_epoch_ms
        )
        self.corr_interval = CorrelationInterval(
            start_code_phase_ms=int(np.ceil(start_code_phase_ms)),
            duration_ms=CORRELATION_INTERVAL_MS,
        )

        # Carry the state from the acquisition epoch onto that first boundary, so
        # `signal_state` is the prior AT AN EPOCH START from the very first epoch.
        # Acquisition reports a code phase mid-period, so without this the first
        # epoch would be the one exception to that invariant.
        first_epoch_uptime_ms, _ = self.corr_interval.compute_start_and_stop_uptime_ms(
            self.signal_state
        )
        self.signal_state = self.signal_state.propagate_to_uptime_ms(first_epoch_uptime_ms)

        # Weights for non-coherent code combining, aligned to policy.code_components.
        self._set_policy(self.policy)

        # --- tiered (overlay) code state -------------------------------------
        # Until the overlay is synced the channel folds one interval per epoch
        # with unit signs, which is arithmetically identical to having no overlay
        # at all -- so signals without one are completely unaffected.
        self._overlays = tuple(c.overlay for c in signal_params.code_set.components)
        overlay_sync_kwargs: dict = {}
        if overlay_search is not None:
            overlay_sync_kwargs["search"] = overlay_search
        if overlay_prompts_to_observe is not None:
            overlay_sync_kwargs["prompts_to_observe"] = overlay_prompts_to_observe
        self.overlay_sync = secondary_code.build_synchroniser(
            self._overlays,
            reference_index=self.policy.carrier_component,
            **overlay_sync_kwargs,
        )
        # An overlay chip lasts one PRIMARY CODE PERIOD, which is not the same
        # thing as one correlation interval.  They coincide on L5 -- the only
        # signal with an overlay until L1C -- and nowhere else: L1C's primary
        # period is 10 ms, so its overlay advances once per ten intervals.
        self._intervals_per_primary_period = (
            signal_params.primary_period_ms // CORRELATION_INTERVAL_MS
        )
        # The search wants one prompt per overlay chip, so intervals are summed
        # across the primary period before being handed over.  At one interval per
        # period this is a copy and the sum is exact.
        self._overlay_prompt = 0j
        self.coherent_duration_ms = loop_params.coherent_duration_ms
        self._epoch_interval_count = 0

        # --- epoch grid anchoring --------------------------------------------
        # Epochs open on a data symbol boundary, so an epoch never straddles one
        # and epoch zero coincides with symbol zero -- which is what later makes
        # the symbols demodulable without a separate alignment search.
        #
        # The period is the shortest symbol among *all* components, not the one
        # driving the loops: on L5 the carrier loop runs on the dataless Q pilot,
        # which has no symbol of its own, while I's 10 ms CNAV symbol is what the
        # shared epoch has to respect.  A signal that is pilot-only everywhere has
        # no boundary to find, and falls back to anchoring on the epoch length.
        symbol_periods = [
            c.symbol_period_ms
            for c in signal_params.code_set.components
            if c.symbol_period_ms is not None
        ]
        self._epoch_anchor_period_ms: int | None = min(symbol_periods) if symbol_periods else None
        # Cleared whenever the epoch length changes, so the new grid re-anchors.
        self._epoch_grid_anchored = False

        # --- data bit synchronisation ----------------------------------------
        # The grid above anchors on the CODE PHASE lattice, and a multiple of the
        # symbol period in code phase is a symbol boundary only once something has
        # tied that lattice to the data.  Two things can: an overlay, whose phase
        # sync pins it, or a primary code period at least as long as the symbol --
        # L2C's 20 ms CM period is exactly one symbol, L1C's 10 ms one CNAV-2
        # symbol, so on both the boundary is wherever the code period starts.
        #
        # L1 C/A has neither.  A 1 ms code, a 20 ms bit, and an acquisition code
        # phase known only modulo one code period: the counter's origin is an
        # arbitrary code period, so its 20 ms lattice is offset from the bit
        # lattice by an unknown 0-19 ms.  Anchoring on it regardless puts every
        # epoch boundary in the wrong place by that offset, which survives all the
        # way into the pseudoranges as a whole number of milliseconds -- 300 km
        # each.  The offset has to be measured first; see `_observe_bit_sync`.
        self._bit_sync_needed = (
            self.overlay_sync is None
            and self._epoch_anchor_period_ms is not None
            and self._epoch_anchor_period_ms > signal_params.primary_period_ms
        )
        self._bit_sync: bitsync.BitSyncResult | None = None
        self._bit_prompts: deque[complex] = deque(maxlen=BIT_SYNC_WINDOW_PROMPTS)
        self._bit_prompt_origin_code_phase_ms = 0
        self._bit_next_code_phase_ms: int | None = None
        self._bit_since_attempt = 0
        # Set when the extension below opens the longer grid on the measured
        # boundary.  Deliberately NOT the same thing as "bit sync has converged": a
        # channel left at one code period per epoch knows the boundary and still
        # has epochs starting at every code period, so its grid says nothing about
        # where a symbol begins.  Only the extension makes epoch zero symbol zero.
        self._bit_grid_anchored = False
        # Where the symbol boundary sits relative to the code phase lattice's own
        # multiples of the anchor period.  Zero for every signal whose code phase
        # already locates the boundary, which is what leaves them untouched.
        self._symbol_phase_offset_ms = 0
        self._unit_signs = np.ones(num_components, dtype=np.int8)
        self._synced_policy = synced_policy
        self._synced_coherent_duration_ms = synced_coherent_duration_ms
        # The longest accumulation that stays coherent while the overlay is STILL
        # FLIPPING, or None if nothing bounds it.  A second, independent gate on the
        # extension, and separating it from `_symbol_boundary_known` is the point:
        # on L5 both say "wait for the overlay" and the two were indistinguishable,
        # but on L1C both say "go" and conflating them cost 2 s of 1 ms epochs.
        #
        # L1CO's chip lasts one primary code period -- 10 ms -- so it bounds nothing
        # the 10 ms CNAV-2 symbol did not already bound.  NH20's chip lasts 1 ms, so
        # on L5 it binds hard and the wait is real.
        unstripped_limits = coherent_duration_limits_ms(
            signal_params, overlay_stripped=False
        )
        self._unstripped_limit_ms = min(
            (limit for limit, _ in unstripped_limits), default=None
        )
        # Loop gains scale with the update period, so extending the coherent
        # accumulation demands a retuned filter.  Built up front to keep the
        # transition free of allocation and surprises.
        self._synced_loop_params = (
            _retune_for_update_period(loop_params, synced_coherent_duration_ms)
            if synced_coherent_duration_ms > loop_params.coherent_duration_ms
            else loop_params
        )
        # And say so if that retune narrowed anything.  Every channel now opens at
        # one correlation interval, where Bn*T is small for any sane bandwidth, so
        # the check above this constructor's body can no longer catch a caller whose
        # bandwidths do not suit the epoch they actually asked for -- it only ever
        # sees the 1 ms warm-up.  The extension is where their numbers really land.
        narrowed_on_extend = [
            (name, getattr(loop_params, name), getattr(self._synced_loop_params, name))
            for name in (
                "DLL_bandwidth_hz", "PLL_bandwidth_hz",
                "FLL_bandwidth_hz", "subcarrier_bandwidth_hz",
            )
            if getattr(self._synced_loop_params, name) < getattr(loop_params, name)
        ]
        if narrowed_on_extend:
            detail = ", ".join(
                f"{name} {was:g} -> {now:g} Hz" for name, was, now in narrowed_on_extend
            )
            warnings.warn(
                f"extending to {synced_coherent_duration_ms} ms caps loop bandwidths at "
                f"{MAX_BANDWIDTH_TIME_PRODUCT / (synced_coherent_duration_ms * 1e-3):g} Hz "
                f"(Bn*T <= {MAX_BANDWIDTH_TIME_PRODUCT:g}); narrowing {detail} at the "
                "handover. The longer accumulation is what pays for the narrower loop, "
                "but the loop after the extension is not the one configured.",
                RuntimeWarning,
                stacklevel=2,
            )

        # (validation follows)
        # Both configurations are checked up front, so a bad choice fails at
        # construction rather than part-way through a run.
        validate_coherent_duration(
            signal_params, self.policy, loop_params.coherent_duration_ms,
            overlay_stripped=initial_overlay_counter is not None,
        )
        if synced_coherent_duration_ms > loop_params.coherent_duration_ms:
            validate_coherent_duration(
                signal_params, synced_policy or self.policy,
                synced_coherent_duration_ms, overlay_stripped=True,
            )

        # An overlay phase recovered by acquisition skips the post-lock search
        # entirely: wipe-off and the pilot discriminator are available from the
        # first interval.  Coherent integration still waits for PLL lock.
        if initial_overlay_counter is not None:
            if self.overlay_sync is None:
                raise ValueError(
                    "initial_overlay_counter given for a signal with no tiered code "
                    f"({signal_params.code_set.names})"
                )
            self.overlay_sync.counter = (
                int(initial_overlay_counter) % self.overlay_sync.period
            )
            self.overlay_sync.status = secondary_code.OverlaySyncStatus.SYNCED
            self._on_overlay_synced()

        # Hidden option/flag variables
        self._ignore_loop_updates = False

    def _set_policy(self, policy: LoopDiscriminatorPolicy) -> None:
        self.policy = policy
        self._code_weights = self.signal_params.code_set.power_weights[
            list(policy.code_components)
        ]

    def _combine_code_magnitude(self, corr_vector: np.ndarray) -> float:
        """
        Power-weighted non-coherent magnitude across the delay-discriminator components.

        For a single unit-weight component this is exactly abs(corr), so
        single-component signals are unaffected by the generalisation.
        """
        selected = corr_vector[list(self.policy.code_components)]
        return float(np.sqrt(np.sum(self._code_weights * np.abs(selected) ** 2)))

    def _on_overlay_synced(self) -> None:
        """
        Switch to pilot tracking now that the tiered code can be stripped.

        Wipe-off begins immediately -- it is a sign multiply, and once the overlay
        phase is known it is always right.  The discriminator can also drop Costas
        wrapping here: with the overlay removed a pilot really is dataless, so the
        full four-quadrant angle is available and the squaring loss goes away.

        What does NOT happen here is lengthening the coherent accumulation.  That
        is limited by Doppler error rather than by the overlay, so it waits for
        lock -- see `_maybe_extend_coherent_duration`.
        """
        if self._synced_policy is not None:
            self._set_policy(self._synced_policy)

        # Whatever is part-way through the epoch was folded without wipe-off, so
        # mixing it into the first coherent accumulation would partly cancel it.
        # Start the first wiped-off epoch clean.
        self.correlator.reset_epoch()
        self._epoch_interval_count = 0
        # And re-anchor, because this reset lands MID-GRID.  Sync is detected on the
        # last interval of a primary code period, so the next interval to be folded
        # sits one interval past a period boundary -- fine when the epoch is one
        # interval long, and a permanently offset grid when it is not.  L1C is the
        # case: its epoch may already be 10 ms here (nothing about an un-stripped
        # L1CO stops it), and without this the grid would restart at code phase 9,
        # 19, 29 ... and straddle every CNAV-2 symbol from then on.
        if self.coherent_duration_ms > CORRELATION_INTERVAL_MS:
            self._epoch_grid_anchored = False

    @property
    def _symbol_boundary_known(self) -> bool:
        """
        Whether the epoch grid has a real symbol boundary to anchor to yet.

        One question, three mechanisms -- a code period that is itself a symbol, an
        overlay's phase, or a measured bit boundary -- and the extension is gated on
        whichever one this signal uses.  Keeping them behind a single property is
        what stops the gate from silently meaning "has an overlay", which is how a
        signal with no overlay at all comes to be treated as permanently
        unsynchronised.

        ORDER MATTERS, and the cheapest mechanism has to be asked about first.  A
        code period at least as long as the symbol locates the boundary outright,
        from the seeded code phase, at epoch zero -- and it does so whether or not
        the signal also carries an overlay.  Asking about the overlay first made
        L1C wait for a 1800-hypothesis FFT search it did not need: L1CD's 10 ms
        code period IS one CNAV-2 symbol, so L1C knows where its symbols start
        before it has any idea where it is in L1CO.  What the overlay actually
        gates on L1C is the four-quadrant discriminator, and that is switched in
        `_on_overlay_synced`, not here.
        """
        if (
            self._epoch_anchor_period_ms is not None
            and self._epoch_anchor_period_ms <= self.signal_params.primary_period_ms
        ):
            return True
        if self.overlay_sync is not None:
            return self.overlay_sync.synced
        if self._bit_sync_needed:
            return self._bit_sync is not None and self._bit_sync.synced
        return True

    def _observe_bit_sync(self, prompt: complex) -> None:
        """
        Accumulate 1 ms prompts and look for the data bit boundary among them.

        The recovered phase is converted straight into a code phase offset, so
        every anchor test downstream stays plain arithmetic on the code phase
        lattice and nothing has to carry a prompt index around.

        `bitsync.synchronise` reads its input as consecutive code periods, so a
        gap in the stream would silently re-map every phase in the buffer.  The
        buffer is therefore restarted on any discontinuity rather than trusted.
        """
        period_ms = self._epoch_anchor_period_ms
        assert period_ms is not None  # implied by _bit_sync_needed
        start_ms = self.corr_interval.start_code_phase_ms

        if self._bit_next_code_phase_ms != start_ms:
            self._bit_prompts.clear()
            self._bit_since_attempt = 0
        if not self._bit_prompts:
            self._bit_prompt_origin_code_phase_ms = start_ms
        elif len(self._bit_prompts) == self._bit_prompts.maxlen:
            # Appending is about to evict the oldest prompt, so the origin the
            # recovered phase is measured from moves up with it.
            self._bit_prompt_origin_code_phase_ms += CORRELATION_INTERVAL_MS
        self._bit_prompts.append(prompt)
        self._bit_next_code_phase_ms = start_ms + CORRELATION_INTERVAL_MS
        self._bit_since_attempt += 1

        if len(self._bit_prompts) < BIT_SYNC_MIN_PROMPTS:
            return
        if self._bit_since_attempt < BIT_SYNC_RETRY_PROMPTS:
            return
        self._bit_since_attempt = 0

        result = bitsync.synchronise(
            np.fromiter(self._bit_prompts, dtype=complex, count=len(self._bit_prompts)),
            periods_per_bit=period_ms // CORRELATION_INTERVAL_MS,
            min_prompts=BIT_SYNC_MIN_PROMPTS,
        )
        if not result.synced:
            return
        self._bit_sync = result
        self._symbol_phase_offset_ms = (
            self._bit_prompt_origin_code_phase_ms
            + result.phase * CORRELATION_INTERVAL_MS
        ) % period_ms

    def _maybe_extend_coherent_duration(self) -> None:
        """
        Lengthen the coherent accumulation, once the carrier is actually tracked.

        Coherent integration of length T loses `sinc(df * T)` to a Doppler error
        df, so a 20 ms accumulation needs df well inside 25 Hz.  Acquisition seeds
        it to half a Doppler bin -- 50 Hz on L5's 100 Hz grid -- where df*T is 1.0,
        a null.  The FLL is what removes that error, so extension waits on PLL
        lock, not on overlay sync.

        While sync was itself gated on PLL these two could be done together and the
        distinction never showed.  It matters as soon as the overlay phase arrives
        from acquisition instead, because then sync happens at epoch zero with the
        loops still pulling in.

        The overlay is not irrelevant here, only secondary: it gates the extension
        exactly when wipe-off is what buys the length, which the guard below tests
        directly rather than assuming from the presence of a tiered code.

        The loop filter is retuned in the same step: every gain scales with the
        update period, so a 20 ms epoch driving gains built for 1 ms is a 20x
        mistuning.  The two must not be separated.
        """
        # Only ever extend.  `signal_interfaces.create_tracking_channels` opens
        # every channel at one interval, so this cannot fire on that path -- but
        # `TrackingChannel` is constructed directly too, and a channel *started*
        # multi-interval with a shorter target would otherwise be CONTRACTED here
        # the moment it synced, onto `_synced_loop_params`, which in that case is
        # the un-retuned filter built for the longer epoch.  The gains would then
        # be wrong by the ratio as well.
        if self._synced_coherent_duration_ms <= self.coherent_duration_ms:
            return
        if not self._symbol_boundary_known:
            return
        if self.loop_state.mode is not TrackingLoopMode.PLL:
            return
        # The target has to be coherent at the CURRENT overlay state, not merely at
        # the stripped one the constructor validated.  Where wipe-off is what buys
        # the length -- L5 -- this is what makes the extension wait for it; where it
        # is not -- L1C, whose overlay chip is already a whole primary code period
        # -- it lets the extension happen without ever consulting the overlay.
        if (
            self.overlay_sync is not None
            and not self.overlay_sync.synced
            and self._unstripped_limit_ms is not None
            and self._synced_coherent_duration_ms > self._unstripped_limit_ms
        ):
            return

        # Switch exactly on a symbol boundary, so the longer grid is anchored the
        # moment it starts.
        #
        # Waiting here rather than switching now and dropping intervals until the
        # boundary is what keeps the transition lossless: the shorter epochs keep
        # producing output right up to the boundary.  Only a grid with no shorter
        # length to fall back on -- one configured multi-interval from the start --
        # has to discard intervals instead (see `_complete_interval`).
        anchor_ms = self._epoch_anchor_period_ms or self._synced_coherent_duration_ms
        next_interval_code_phase_ms = (
            self.corr_interval.start_code_phase_ms + self.corr_interval.duration_ms
        )
        offset_ms = self._symbol_phase_offset_ms
        if (next_interval_code_phase_ms - offset_ms) % anchor_ms != 0:
            return

        self.coherent_duration_ms = self._synced_coherent_duration_ms
        self.loop_params = self._synced_loop_params
        self.correlator.reset_epoch()
        self._epoch_interval_count = 0
        self._epoch_grid_anchored = True
        if self._bit_sync_needed:
            self._bit_grid_anchored = True

    def run_loop_filter(self) -> None:
        # Update signal state to reflect parameters at current correlation epoch.
        #
        # This is the epoch's real time on the sample stream, not its code phase.
        # The two are different quantities that merely start out numerically close,
        # and taking one for the other was a live defect: the correlator propagates
        # carrier phase as `carrier_phase + dt_sec * doppler`, so a `dt_sec` wrong by
        # the acquired code phase `d` injects `d * doppler` cycles.  A constant would
        # be harmless, but the loop is busy correcting Doppler, so the error moves by
        # `d * delta_doppler` and the FLL -- which divides by dt -- reads it back as
        # an apparent frequency error `d / T` times larger than the correction that
        # caused it.  That is positive feedback with gain `d / CORRELATION_INTERVAL_MS`.
        #
        # Code phase never showed it: the same `d` sits inside `code_phase_ms` and
        # cancels, so replicas stayed chip-aligned while the phase angles went to
        # noise.  L1 C/A and L5 survived because acquisition reports `d < 1 ms` for
        # them, keeping the gain under 1.  L2C acquires on CM's 20 ms period, so `d`
        # runs to 20 ms and real signals diverged above about 4 ms -- roughly 80% of
        # possible code phases.  Every L2C scenario used 0.61 ms, so nothing caught it.
        # `signal_state` is the PRIOR for the epoch that just closed, and it already
        # sits at that epoch's start -- the previous run propagated it there, and the
        # constructor placed the first one.  Nothing has to be propagated before
        # correcting; the corrections apply to it directly.
        uptime_epoch_ms = self.signal_state.uptime_epoch_ms

        # How long this epoch ran, in stream time.
        #
        # Epoch boundaries are defined in CODE PHASE -- that is the alignment
        # requirement -- so finding when one occurs needs the code rate.
        # `compute_start_and_stop_uptime_ms` is the single place that conversion
        # happens; everywhere else a dt is a difference of uptimes, because code phase
        # is an estimated state like carrier phase, not a clock.  `corr_interval` is
        # still the last interval of the epoch -- the caller increments it afterwards
        # -- so its stop is where the next epoch begins.
        #
        # The FLL turns a phase difference into a rate and so needs this before the
        # corrections below exist, hence the prior.  Strictly it wants the PREVIOUS
        # epoch's span, since the two prompts it differences are one epoch apart; the
        # two differ only on the single epoch where `coherent_duration_ms` changes,
        # and that transition is gated on PLL lock, where `delta_omega` is logged but
        # does not drive the loop.
        _, next_epoch_uptime_ms = self.corr_interval.compute_start_and_stop_uptime_ms(
            self.signal_state
        )
        epoch_span_sec = (next_epoch_uptime_ms - uptime_epoch_ms) * 1e-3

        # Grid is (delay, doppler, component); assume a single doppler for now.
        # Keep the full component vectors for output, and drive the loops with the
        # components the policy selects.  The epoch grid is the folded, overlay
        # wiped-off accumulation -- one interval long until the overlay syncs.
        epoch = self.correlator.epoch_grid[:, 0]
        layout = self.correlator.config.tap_layout
        early_all = epoch[layout.index(EARLY)]
        prompt_all = epoch[layout.index(PROMPT)]
        late_all = epoch[layout.index(LATE)]
        prompt = prompt_all[self.policy.carrier_component]

        # Compute loop discriminators
        # Costas wrapping (+/-1/4 cycle) makes a 180 degree data or overlay flip
        # invisible; a dataless pilot can use the full +/-1/2 cycle range instead.
        half_range = 0.25 if self.policy.costas else 0.5

        # Phase discriminator for PLL
        delta_theta = _wrap_cycles(np.angle(prompt) / (2.0 * np.pi), half_range)

        # Frequency discriminator for FLL
        last_prompt = self.loop_state.get_last_prompt_corr()
        if last_prompt is None or last_prompt == 0.0:
            delta_omega = 0.0
        else:
            # Wrapped the same way as the phase discriminator so bit flips are not
            # read as frequency error, then divided by the epoch spacing to give Hz.
            delta_omega = (
                _wrap_cycles(np.angle(prompt / last_prompt) / (2.0 * np.pi), half_range)
                / epoch_span_sec
            )

        # Code discriminator for DLL
        EPL_chip_spacing = self.loop_params.EPL_chip_spacing
        early_mag = self._combine_code_magnitude(early_all)
        late_mag = self._combine_code_magnitude(late_all)
        prompt_mag = self._combine_code_magnitude(prompt_all)
        denom = early_mag + late_mag + 2.0 * prompt_mag
        if denom < 1e-12:
            delta_eta = 0.0
        else:
            delta_eta = (2 - EPL_chip_spacing) * (early_mag - late_mag) / denom

        # ---- subcarrier delay: the double estimator's second loop -------------
        #
        # The code taps above ride a plain BPSK triangle -- one peak, a chip wide,
        # unambiguous, and shallow.  These ride the subcarrier, which near zero is
        # about six times steeper on L1C and so carries the precision, but repeats.
        #
        # `subcarrier_ambiguity_chips` is the period of what this discriminator
        # actually sees.  The signed subcarrier correlation repeats every chip, but
        # the discriminator is non-coherent -- it combines MAGNITUDES, so it cannot
        # tell the +1 peak at 0 from the -1 peak at half a chip and its period is
        # half that.  Measured on L1CP: |R| peaks at 0 and +/-0.5, nulls at +/-0.24.
        # The code loop therefore has to hold +/-0.25 chip, not +/-0.5.
        delta_subcarrier_chips = 0.0
        if layout.tracks_subcarrier:
            sub_early_mag = self._combine_code_magnitude(epoch[layout.index(SUBCARRIER_EARLY)])
            sub_late_mag = self._combine_code_magnitude(epoch[layout.index(SUBCARRIER_LATE)])
            sub_denom = sub_early_mag + sub_late_mag + 2.0 * prompt_mag
            if sub_denom >= 1e-12:
                spacing = self.loop_params.subcarrier_chip_spacing
                delta_subcarrier_chips = (
                    (2 - spacing) * (sub_early_mag - sub_late_mag) / sub_denom
                )

        # Apply loop filters to discriminators
        self.loop_state.update_history(prompt)
        circ_length = self.loop_state.compute_prompt_corr_history_circ_length(
            costas=self.policy.costas
        )

        filt_code_phase_error_chips = self.loop_params.DLL_filter_coeff * delta_eta
        # Captured before the branch below, which may switch the mode: the epoch
        # that triggers the handover is still filtered by the FLL, so that is the
        # mode this epoch should be logged under.
        epoch_used_pll = self.loop_state.mode is TrackingLoopMode.PLL
        if self.loop_state.mode == TrackingLoopMode.FLL:
            if (
                self.loop_state.history_filled
                and circ_length > self.loop_params.prompt_corr_circ_length_threshold
            ):
                self.loop_state.mode = TrackingLoopMode.PLL
            filt_carr_phase_error_cycles = 0.0
            filt_doppler_freq_error_hz = self.loop_params.FLL_filter_coeff * delta_omega
        else:
            filt_carr_phase_error_cycles = self.loop_params.PLL_filter_coeffs[0] * delta_theta
            filt_doppler_freq_error_hz = self.loop_params.PLL_filter_coeffs[1] * delta_theta

        if self._ignore_loop_updates:
            filt_carr_phase_error_cycles = 0.0
            filt_doppler_freq_error_hz = 0.0
            filt_code_phase_error_chips = 0.0

        # Fold the subcarrier correction in and wrap it back into one ambiguity
        # interval.  Wrapping IS the ambiguity resolution: the offset is kept in
        # [-T/2, T/2), so the delay actually reported is the code loop's estimate
        # plus a residual the subcarrier loop measured far more precisely than the
        # code loop could.  The same move as resolving a carrier-phase integer
        # against a code pseudorange, one cycle of the subcarrier instead of one of
        # the carrier.
        # The offset is a DIFFERENCE, tau_subcarrier - tau_code, so a correction to
        # the code phase moves it too unless it is subtracted back out here.  Miss
        # that and the code loop drags the subcarrier along with it: the taps then
        # walk a diagonal of the 2-D surface instead of its two axes, and the code
        # discriminator acquires a stable zero away from the truth (measured: it
        # settles 0.23 chip off).  Independence is the whole premise.
        subcarrier_offset_chips = self.signal_state.subcarrier_offset_chips
        if layout.tracks_subcarrier:
            subcarrier_offset_chips += (
                self.loop_params.subcarrier_filter_coeff * delta_subcarrier_chips
                - filt_code_phase_error_chips
            )
            ambiguity = self.signal_params.subcarrier_ambiguity_chips
            if ambiguity > 0.0:
                subcarrier_offset_chips -= ambiguity * round(
                    subcarrier_offset_chips / ambiguity
                )

        # State update and propagation
        # Rate (Doppler) is updated first, then code/carrier phase is updated and propagated based on updated rate.
        doppler_freq_hz = self.signal_state.carrier_rate_cyc_per_sec + filt_doppler_freq_error_hz
        # Code rate is slaved to carrier doppler, expressed as ms of code phase per second.
        code_rate_ms_per_sec = (1.0 + doppler_freq_hz / self.signal_params.carrier_freq_hz) * 1e3

        # ---- posterior: the prior plus the filtered corrections. -------------
        # Still at this epoch's start, because that is where the prior was.  This is
        # the estimate describing the epoch just measured, and it is what gets logged.
        carrier_phase_cycles = (
            self.signal_state.carrier_phase_cycles + filt_carr_phase_error_cycles
        )
        self.signal_state.subcarrier_offset_chips = subcarrier_offset_chips
        code_phase_ms = (
            self.signal_state.code_phase_ms
            + filt_code_phase_error_chips
            / self.signal_params.nominal_code_rate_chips_per_sec
            * 1e3
        )

        idx = self.outputs.output_index
        # Outputs beyond capacity are silently dropped (output_index stops advancing).
        if idx < self.outputs.capacity:
            self.outputs.uptime_epoch_ms[idx] = uptime_epoch_ms
            self.outputs.carr_phase_errors_cycles[idx] = delta_theta
            self.outputs.code_phase_errors_chips[idx] = delta_eta
            self.outputs.corr[idx] = epoch
            self.outputs.subcarrier_offset_chips[idx] = subcarrier_offset_chips
            self.outputs.carr_phase_cycles[idx] = carrier_phase_cycles
            self.outputs.doppler_freq_hz[idx] = doppler_freq_hz
            self.outputs.code_phase_ms[idx] = code_phase_ms
            self.outputs.delta_omega[idx] = delta_omega
            self.outputs.prompt_corr_circ_length[idx] = circ_length
            self.outputs.pll_mode[idx] = epoch_used_pll
            self.outputs.epoch_duration_ms[idx] = self.coherent_duration_ms
            self.outputs.overlay_synced[idx] = (
                self.overlay_sync is not None and self.overlay_sync.synced
            )
            self.outputs.bit_synced[idx] = self._bit_grid_anchored
            self.outputs.output_index += 1

        # ---- prior for the next epoch: the posterior, propagated to its start. ----
        # Where the next epoch begins is re-derived from the POSTERIOR, because the
        # corrections just moved the state and the corrected rate is what the NCO runs
        # at across the span.  From there the propagation is purely time-driven:
        # `propagate_to_uptime_ms` advances code phase and carrier phase by
        # rate x dt over an uptime difference.  Neither is asserted to a boundary
        # value -- both are estimates carried forward.
        posterior = TrackingSignalState(
            uptime_epoch_ms=uptime_epoch_ms,
            code_phase_ms=code_phase_ms,
            code_rate_ms_per_sec=code_rate_ms_per_sec,
            carrier_phase_cycles=carrier_phase_cycles,
            carrier_rate_cyc_per_sec=doppler_freq_hz,
            subcarrier_offset_chips=subcarrier_offset_chips,
        )
        _, next_epoch_uptime_ms = self.corr_interval.compute_start_and_stop_uptime_ms(
            posterior
        )
        self.signal_state = posterior.propagate_to_uptime_ms(next_epoch_uptime_ms)

        # Checked every epoch rather than only on the FLL->PLL edge, so that a
        # channel started directly in PLL, or synced from acquisition after lock,
        # still picks it up.  It is a no-op once applied.
        self._maybe_extend_coherent_duration()

    def _record_interval_for_cn0(self) -> None:
        """
        Feed one correlation interval's prompt power to the C/N0 estimator.

        Called at the very top of `_complete_interval`, which is deliberate on two
        counts.  It runs before `fold()`, so the sample is one raw interval and is
        unaffected by `coherent_duration_ms` -- the estimate's integration time has
        to be the interval, not the epoch.  And it runs before the epoch-anchoring
        return, because an interval dropped while waiting for a symbol boundary is
        a perfectly good correlation; it is discarded for epoch-tiling reasons, and
        excluding it would punch a hole in the C/N0 record at the start of every
        channel.
        """
        params = self.cn0_params
        if not params.enabled or self.outputs.cn0_capacity == 0:
            return

        # Every component, since magnitude costs nothing to keep and comparing one
        # against another is the point.  By role: the prompt is not tap 1 in a
        # double-estimator layout.
        prompt = self.correlator.corr_grid[self._prompt_tap, 0, :]
        self._cn0_power[self._cn0_write] = np.abs(prompt) ** 2
        self._cn0_write = (self._cn0_write + 1) % params.period_ms
        self._cn0_fill = min(self._cn0_fill + 1, params.period_ms)
        self._cn0_since_estimate += 1

        if self._cn0_fill < params.period_ms or self._cn0_since_estimate < params.hop_ms:
            return
        self._cn0_since_estimate = 0

        idx = self.outputs.cn0_index
        if idx >= self.outputs.cn0_capacity:
            return
        integration_time_s = CORRELATION_INTERVAL_MS * 1e-3
        for component in range(self.outputs.num_components):
            self.outputs.cn0_dbhz[idx, component] = estimate_cn0_vsm(
                self._cn0_power[:, component], integration_time_s
            )
        # Stamped at the end of the window the estimate covers.
        self.outputs.cn0_uptime_ms[idx] = self.signal_state.uptime_epoch_ms
        self.outputs.cn0_index += 1

    def _complete_interval(self) -> None:
        """
        Fold one finished correlation interval into the epoch, and run the loop
        filter once the epoch is full.

        This is the whole of the tiered-code machinery, and it sits *on top of*
        the interval logic rather than inside it: the PARTIAL/COMPLETE handling in
        `process_sample_buffer` is untouched, and only ever hands complete
        intervals here.  A signal with no overlay folds with unit signs and an
        epoch of one interval, which is arithmetically the old behaviour.
        """
        self._record_interval_for_cn0()

        if self.overlay_sync is None:
            signs = self._unit_signs
        else:
            # Where this interval sits inside the primary code period, read off the
            # code phase rather than counted.  Intervals are whole milliseconds of
            # code phase and the code repeats every `primary_period_ms`, so this is
            # exact -- and unlike a running count it cannot drift when an interval
            # is dropped (a gap in the stream, or the epoch-anchoring wait below).
            position = (
                self.corr_interval.start_code_phase_ms // CORRELATION_INTERVAL_MS
            ) % self._intervals_per_primary_period
            last_interval_of_period = position == self._intervals_per_primary_period - 1

            # Feed the synchroniser the raw prompt, before wipe-off: it is looking
            # for the overlay's own sign pattern, which wipe-off would remove.
            # Gated on PLL lock, because an un-locked carrier leaves the prompts
            # rotating and the correlation meaningless.
            #
            # One prompt per OVERLAY CHIP is what the search is defined on, so the
            # intervals of a primary period are summed first.  Feeding it fragments
            # instead would make the recovered offset an interval index rather than
            # an overlay index, and cost 10*log10(intervals) dB per sample besides.
            if not self.overlay_sync.synced and self.loop_state.mode is TrackingLoopMode.PLL:
                if position == 0:
                    self._overlay_prompt = 0j
                self._overlay_prompt += complex(
                    self.correlator.corr_grid[
                        self._prompt_tap, 0, self.policy.carrier_component
                    ]
                )
                if last_interval_of_period:
                    if self.overlay_sync.observe(self._overlay_prompt):
                        self._on_overlay_synced()

            # Signs are read *after* a possible sync so that the interval which
            # triggered it is itself wiped off correctly, rather than folded raw
            # into the freshly reset epoch.  Every interval of a primary period
            # carries the same overlay chip, so only the last one advances.
            signs = self.overlay_sync.signs(self._overlays)
            if last_interval_of_period:
                self.overlay_sync.advance()

        # The bit-sync equivalent of the overlay feed above, and gated the same
        # way: an un-locked carrier leaves the prompts rotating, and the sign of a
        # rotating prompt says nothing about the data.
        #
        # Only while the epoch is one interval long.  Folding is what destroys the
        # 1 ms structure the histogram is built from, so a longer epoch could not
        # answer this question however many of them went by -- one of the two
        # reasons every channel starts at a single interval and extends afterwards
        # (the other, which applies to all signals, is FLL pull-in; see
        # `signal_interfaces.create_tracking_channels`).
        if (
            self._bit_sync_needed
            and self._bit_sync is None
            and self.coherent_duration_ms <= CORRELATION_INTERVAL_MS
            and self.loop_state.mode is TrackingLoopMode.PLL
        ):
            self._observe_bit_sync(
                complex(
                    self.correlator.corr_grid[
                        self._prompt_tap, 0, self.policy.carrier_component
                    ]
                )
            )

        # Open the epoch grid on a data symbol boundary.
        #
        # `validate_coherent_duration` rejects an epoch length that does not divide
        # every component's symbol period, but divisibility only keeps epochs
        # inside those boundaries if the grid is *anchored*.  The seeded code phase
        # is wherever acquisition landed, so a grid begun there is offset by an
        # arbitrary number of milliseconds: on L5 a 5 ms epoch started at code
        # phase 3 crosses the 10 ms CNAV symbol on every other epoch, and I
        # collapses whenever consecutive symbols differ while the dataless Q looks
        # perfect.
        #
        # Anchoring on the symbol period rather than merely on the epoch length is
        # the stronger choice, and it is deliberate: both avoid straddling, but
        # only this one makes epoch zero coincide with symbol zero, so the symbols
        # can later be read off the epoch index without a separate search.
        #
        # Anchoring happens once per grid -- at construction and again after every
        # change of epoch length -- not at every epoch.  Once the first epoch is on
        # a boundary, the epoch length divides the symbol period and the tiling
        # stays on it.  Intervals before the boundary are dropped: at most
        # `symbol_period - 1` of them, once.  The caller resets the interval grid
        # immediately after this returns, and the epoch accumulator is already
        # empty whenever the interval count is zero.
        #
        # A caveat worth stating: this locates the boundary in *code phase*, so it
        # is a true symbol boundary only for a signal whose acquisition recovers
        # symbol phase -- L5 via the NH20 counter, L2C via CM's 20 ms period.  GPS
        # L1 C/A carries no symbol phase in its 1 ms code, so its grid is anchored
        # deterministically but arbitrarily, and real bit synchronisation remains a
        # separate step this repository does not implement.
        # A single-interval epoch is exempt: it cannot straddle anything, because
        # every period that matters is a whole number of milliseconds and intervals
        # are integer-ms aligned.  Waiting for a boundary would only throw away
        # intervals, and symbol alignment is still recoverable afterwards since
        # every symbol boundary is also an epoch boundary.  So the wait here only
        # ever applies to a channel configured multi-interval from construction:
        # the post-lock extension anchors by waiting at the shorter length instead
        # (see `_maybe_extend_coherent_duration`), which discards nothing.
        if not self._epoch_grid_anchored:
            if self.coherent_duration_ms <= CORRELATION_INTERVAL_MS:
                self._epoch_grid_anchored = True
            else:
                anchor_ms = self._epoch_anchor_period_ms or self.coherent_duration_ms
                offset_ms = self._symbol_phase_offset_ms
                if (self.corr_interval.start_code_phase_ms - offset_ms) % anchor_ms:
                    return
                self._epoch_grid_anchored = True

        self.correlator.fold(signs)
        self._epoch_interval_count += 1

        if self._epoch_interval_count >= self.coherent_duration_ms // CORRELATION_INTERVAL_MS:
            self.run_loop_filter()
            self.correlator.reset_epoch()
            self._epoch_interval_count = 0

    def process_sample_buffer(self, buffer: sample_streaming.SampleBuffer) -> None:
        while True:
            # Determine code period integration bounds for current signal state
            (
                corr_interval_start_uptime_ms,
                corr_interval_stop_uptime_ms,
            ) = self.corr_interval.compute_start_and_stop_uptime_ms(self.signal_state)

            # If previous correlation interval status was partial, check whether this will complete the interval
            # If it will not, then we can ignore the last correlation (reset correlator) and start new accum.
            if (
                self.correlator_status == CorrelatorStatus.PARTIAL
                and buffer.start_uptime_ms > corr_interval_stop_uptime_ms
            ):
                self.corr_interval.increment()
                self.correlator.reset()
                self.correlator_status = CorrelatorStatus.CLEARED
                continue

            # Accumulate (either a new interval or continuation of a partial interval)
            self.correlator.accumulate(
                buffer,
                accum_start_uptime_ms=corr_interval_start_uptime_ms,
                accum_stop_uptime_ms=corr_interval_stop_uptime_ms,
                signal_params=self.signal_params,
                signal_state=self.signal_state,
            )

            # Check if correlation was completed (i.e. if accum. stop time is within current buffer)
            if corr_interval_stop_uptime_ms < buffer.stop_uptime_ms:
                # An interval that fell entirely behind the stream (non-contiguous buffers)
                # accumulates nothing; skip it rather than running the loop filter on zeros.
                if self.correlator.corr_count > 0:
                    self.correlator_status = CorrelatorStatus.COMPLETE
                    self._complete_interval()

                self.corr_interval.increment()
                self.correlator.reset()
                self.correlator_status = CorrelatorStatus.CLEARED
            else:
                self.correlator_status = CorrelatorStatus.PARTIAL
                break
