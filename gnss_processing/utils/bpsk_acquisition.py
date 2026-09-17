import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple
import numpy as np
from numpy.typing import NDArray

from .bpsk_correlation import correlate__multicomponent
from .code_components import (
    Branch,
    CodeComponent,
    Subcarrier,
    build_code_set,
    subcarrier_signs,
)
import scipy.fft
import scipy.stats


@dataclass
class SignalReplicaCacheEntry:
    replica_samples: np.ndarray
    replica_fft: np.ndarray


@dataclass
class FineSearchParameters:
    """
    Localised refinement of an already-detected peak.

    The coarse search resolves delay to one sample and Doppler to one FFT bin, and
    the residual costs real signal power.  At 22 Msps against a 10.23 Mcps code one
    delay bin is 0.465 chips, so a worst-case half-bin error sits at 0.233 chips on
    the correlation triangle -- **-2.3 dB**.  The equivalent Doppler error, half of
    a 50 Hz bin over a 5 ms coherent integration, costs only -0.22 dB.  Delay is
    worth an order of magnitude more here, which is why the default window is wider
    on delay than on Doppler.

    Halfwidths are in *coarse bins*, so their physical span depends on the sample
    rate and the code: +/-3 bins is +/-1.40 chips on L5 at 22 Msps, but +/-0.14
    chips on L1 C/A at the same rate.

    The refined grid is a uniform sub-division that **contains the coarse bins**:
    offsets run over `[-halfwidth*factor, +halfwidth*factor]` in steps of
    `1 / factor` of a bin, so with halfwidth 3 and factor 2 the taps are
    -3, -2.5, -2, ... +2.5, +3 bins and the coarse bins are the even ones.  That
    subset property is what makes the fine grid directly comparable to the coarse
    one.
    """

    delay_factor: int = 2
    doppler_factor: int = 2
    delay_halfwidth_bins: int = 3
    doppler_halfwidth_bins: int = 1

    def __post_init__(self) -> None:
        if self.delay_factor < 1 or self.doppler_factor < 1:
            raise ValueError(
                f"fine search factors must be >= 1, got delay={self.delay_factor}, "
                f"doppler={self.doppler_factor}"
            )
        # A halfwidth of 0 would search only the coarse peak itself.  The true peak
        # can sit up to half a bin away, so the window has to be at least one bin
        # wide to bracket it at all.
        if self.delay_halfwidth_bins < 1 or self.doppler_halfwidth_bins < 1:
            raise ValueError(
                f"fine search halfwidths must be >= 1 bin to bracket the true peak, got "
                f"delay={self.delay_halfwidth_bins}, doppler={self.doppler_halfwidth_bins}"
            )

    @property
    def num_delay_taps(self) -> int:
        return 2 * self.delay_halfwidth_bins * self.delay_factor + 1

    @property
    def num_doppler_hypotheses(self) -> int:
        return 2 * self.doppler_halfwidth_bins * self.doppler_factor + 1


@dataclass
class AcquisitionConfiguration:
    """
    Acquisition dwell layout.

    Two lengths, and they are not the same thing:

    `coherent_duration_replica_ms` is the replica -- and therefore the FFT -- length.  The
    correlation is circular, so this must be a whole number of code periods or the
    wraparound matches the data against the wrong chips.

    `coherent_duration_sample_ms` is how much data goes into one coherent integration.
    It defaults to the replica length, which is what every caller did before it
    existed.  Setting it shorter zero-pads each block up to the replica length,
    and `num_blocks` of them are then summed by square law.

    Shortening it is how a signal whose data symbol is no longer than its code
    period gets acquired at all.  GPS L2 CM is the case in point: its 20 ms code
    period is exactly its CNAV symbol, so a 20 ms coherent integration at an
    arbitrary alignment straddles a symbol boundary.  There is a 50% chance of a
    flip, and when one happens the surviving amplitude is |2a - 1| for a boundary
    a fraction `a` of the way through -- a null when it lands mid-window.  Four
    5 ms blocks put at most one boundary inside any single block.

    The two lengths drive different things, which is the point of separating them:

        Doppler grid spacing   = 1 / replica_duration    (FFT bin width)
        Doppler response width = 1 / coherent_duration   (correlation mainlobe)

    With a short coherent length the grid is finer than the response, so the
    residual Doppler error is set by the grid rather than by the mainlobe.

    `fine_search` optionally refines each *detected* peak afterwards, over a small
    window around it -- see `FineSearchParameters`.  `None` disables it, which is
    the default: the coarse search alone is unchanged by this feature.

    The search grid decides how far off the seed handed to tracking can be:

        code phase, worst case = +/- 0.5 sample / delay_factor
        Doppler,    worst case = +/- 0.5 * fft_resolution / doppler_factor

    Both are reported by `search_resolution_summary()`.
    """

    coherent_duration_replica_ms: int
    num_blocks: int
    sample_rate: float
    min_search_doppler_hz: float
    max_search_doppler_hz: float
    # None means "one coherent integration per replica period", the behaviour
    # every existing caller relies on.
    coherent_duration_sample_ms: float | None = None
    # Refinement of detected peaks; None disables it.
    fine_search: FineSearchParameters | None = None

    def __post_init__(self):
        self.replica_length_samples = int(
            self.sample_rate * self.coherent_duration_replica_ms / 1000
        )
        self.replica_time_arr = (
            np.arange(self.replica_length_samples) / self.sample_rate
        )

        if self.coherent_duration_sample_ms is None:
            self.coherent_duration_sample_ms = float(self.coherent_duration_replica_ms)
        if self.coherent_duration_sample_ms <= 0:
            raise ValueError(
                f"coherent_duration_sample_ms must be positive, got "
                f"{self.coherent_duration_sample_ms}"
            )
        # Longer than the replica means FOLDING: the block covers a whole number of
        # replica periods and they are summed onto one before the transform.  A
        # fractional multiple is refused because it has no use and its final part
        # period would contribute at reduced weight for no stated reason.
        ratio = self.coherent_duration_sample_ms / self.coherent_duration_replica_ms
        self.fold_factor = int(round(ratio))
        if ratio > 1.0 and abs(ratio - self.fold_factor) > 1e-9:
            raise ValueError(
                f"coherent_duration_sample_ms {self.coherent_duration_sample_ms} exceeds the "
                f"replica ({self.coherent_duration_replica_ms} ms) but is not a whole multiple "
                "of it; folding sums whole replica periods, so the ratio must be an integer"
            )
        if ratio <= 1.0:
            self.fold_factor = 1
        self.coherent_length_samples = int(
            self.sample_rate * self.coherent_duration_sample_ms / 1000
        )
        if self.coherent_length_samples < 1:
            raise ValueError(
                f"coherent_duration_sample_ms {self.coherent_duration_sample_ms} is shorter than one "
                f"sample period at {self.sample_rate} Hz"
            )

        # Grid spacing comes from the FFT length; mainlobe width from how much
        # data is actually integrated.  They coincide only when the two lengths do.
        self.fft_resolution = self.sample_rate / self.replica_length_samples
        self.doppler_response_width_hz = self.sample_rate / self.coherent_length_samples

        assert self.num_blocks > 0
        self.acq_total_duration_ms = self.num_blocks * self.coherent_duration_sample_ms
        self.total_num_samples = int(
            self.acq_total_duration_ms / 1000 * self.sample_rate
        )
        self.min_doppler_fft_bin = int(self.min_search_doppler_hz / self.fft_resolution)
        self.max_doppler_fft_bin = int(self.max_search_doppler_hz / self.fft_resolution)
        self.doppler_search_bins = np.arange(
            self.min_doppler_fft_bin, self.max_doppler_fft_bin
        )
        self.num_doppler_bins = len(self.doppler_search_bins)

        # Rolling the replica's spectrum by whole bins steps Doppler by
        # `fft_resolution`, which is set by the FFT -- and therefore replica --
        # length.  Folding shortens that FFT, so whole-bin steps get `fold_factor`
        # times coarser while the mainlobe, set by how much data is integrated,
        # does not widen at all.  Left there a 1 ms replica would search a 5 ms
        # integration on a 1 kHz grid: sinc(500 Hz * 5 ms) is -18 dB, a hole in the
        # middle of every gap.  The missing steps come back as sub-bin offsets,
        # applied to the samples as a phase ramp before the fold, and the product
        # of the two is a grid of exactly the spacing an unfolded replica gives.
        self.doppler_sub_offsets_hz = (
            np.arange(self.fold_factor) * self.fft_resolution / self.fold_factor
        )
        self.doppler_step_hz = self.fft_resolution / self.fold_factor
        self.num_doppler_hypotheses = self.num_doppler_bins * self.fold_factor
        # Monotonic in Doppler: whole bins outer, sub-bin offsets inner, so
        # neighbouring indices are neighbouring frequencies -- which is what the
        # fine search's window around a peak assumes.
        self.doppler_hypotheses_hz = (
            self.min_doppler_fft_bin * self.fft_resolution
            + np.arange(self.num_doppler_hypotheses) * self.doppler_step_hz
        )

        # Worst-case seeding error: half a bin, divided by any refinement.
        delay_factor = self.fine_search.delay_factor if self.fine_search else 1
        doppler_factor = self.fine_search.doppler_factor if self.fine_search else 1
        self.code_phase_bin_seconds = 1.0 / self.sample_rate
        self.code_phase_error_seconds = 0.5 * self.code_phase_bin_seconds / delay_factor
        self.doppler_error_hz = 0.5 * self.doppler_step_hz / doppler_factor

        self.replica_cache_dict: Dict[str, SignalReplicaCacheEntry] = {}
        # One-component code sets for the fine search, built on first use.
        self.fine_code_set_cache: Dict[str, Any] = {}

    def search_resolution_summary(
        self, chip_rate_chips_per_sec: float | None = None
    ) -> str:
        """
        Human-readable statement of how tightly the search pins each unknown --
        i.e. the worst-case error in the seed handed to tracking.  The code-phase
        error is also given in metres, since one sample of code phase is a range
        error of that size.

        Code phase is stated in **chips** when the acquisition code's chip rate is
        given, and in nanoseconds otherwise.  Chips are the unit the correlator and
        the results table work in, and they are the unit in which "how far off is
        this seed" has a fixed meaning: half a chip is half a chip on any signal at
        any sample rate, where 100 ns is a fifth of a C/A chip and a whole one of
        L5.  The config does not know the chip rate -- that belongs to the code --
        so the caller supplies it.
        """
        speed_of_light_m_per_s = 299792458.0
        coarse_code_s = 0.5 * self.code_phase_bin_seconds
        coarse_dopp_hz = 0.5 * self.doppler_step_hz

        def code(seconds: float) -> str:
            metres = seconds * speed_of_light_m_per_s
            if chip_rate_chips_per_sec:
                return f"{seconds * chip_rate_chips_per_sec:.3f} chips (+/-{metres:.1f} m)"
            return f"{seconds * 1e9:.1f} ns (+/-{metres:.1f} m)"

        bin_size = (
            f"{self.code_phase_bin_seconds * chip_rate_chips_per_sec:.3f} chips"
            if chip_rate_chips_per_sec
            else f"{self.code_phase_bin_seconds * 1e9:.1f} ns"
        )
        lines = [
            f"Search grid: code phase {bin_size}/bin "
            f"({self.sample_rate / 1e6:.1f} Msps), Doppler {self.doppler_step_hz:.1f} Hz/bin",
            f"  plain search    -> code phase +/-{code(coarse_code_s)}, "
            f"Doppler +/-{coarse_dopp_hz:.1f} Hz",
        ]
        if self.fine_search is not None:
            lines.append(
                f"  fine search (delay x{self.fine_search.delay_factor}, "
                f"Doppler x{self.fine_search.doppler_factor}) -> "
                f"code phase +/-{code(self.code_phase_error_seconds)}, "
                f"Doppler +/-{self.doppler_error_hz:.1f} Hz"
            )
        return "\n".join(lines)


def code_phase_ambiguity_ms(
    result: "AcquisitionResult",
    resolution: "ambiguity_resolution.AmbiguityResolution | None" = None,
) -> float:
    """
    The period over which this channel's cumulative code phase is unambiguous.

    Tracking's `code_phase_ms` is seeded from acquisition and then just accumulates,
    so whatever acquisition left undetermined stays undetermined for the whole run.
    That matters as soon as anyone forms a **code delay**, `uptime - code_phase`:
    the seed is only known modulo this, so the delay is too, and it comes out
    negative unless one period is added back.

    Acquisition pins the phase modulo the period of the code it correlated against
    -- 1 ms on L1 C/A's `CA`, 20 ms on L2C's `CM`, 20 ms on L5's `Q x NH20`, 10 ms
    on L1C's `L1CP`.  A long-code resolution extends that: it decides which of
    `len(scores)` repetitions of the acquired code the dwell sat in, each one
    acquisition-period long, so the product is the period it has settled.  L2C is
    the case that matters -- 75 hypotheses of CM's 20 ms is CL's full 1.5 s, which
    is longer than any transit time, so an L2C code delay is not merely positive
    but is the transit time itself (plus the receiver clock offset).

    An unresolved or absent resolution leaves the acquisition period, which is the
    honest answer: nothing has been settled beyond it.
    """
    ambiguity_ms = float(result.code_phase_ambiguity_ms)
    if resolution is not None and resolution.resolved:
        ambiguity_ms *= len(resolution.scores)
    return ambiguity_ms


def code_delay_ms(
    uptime_epoch_ms: NDArray[np.float64] | float,
    code_phase_ms: NDArray[np.float64] | float,
    ambiguity_ms: float | None = None,
) -> NDArray[np.float64] | float:
    """
    Code delay from the two quantities tracking actually reports.

    `code_phase_ms` is the satellite's own transmit-time coordinate and
    `uptime_epoch_ms` is the receiver's, so their difference is transit time plus
    the receiver clock offset -- the pseudorange, in milliseconds, before anything
    absolute is known about either clock.  The two are easy to conflate because both
    advance at very nearly one millisecond per millisecond and differ only by that
    small delay; they also move in *opposite* directions as range changes, since a
    satellite getting further away makes its transmit-time coordinate fall further
    behind receiver time.

    With `ambiguity_ms`, the result is wrapped into `[0, ambiguity_ms)`.  Without
    the wrap it is negative: acquisition seeds the code phase at the phase it
    measured, so the difference starts at minus that phase.  See
    `code_phase_ambiguity_ms` for what the period is.
    """
    delay = np.asarray(uptime_epoch_ms, dtype=float) - np.asarray(code_phase_ms, dtype=float)
    if ambiguity_ms:
        delay = np.mod(delay, ambiguity_ms)
    return delay


@dataclass
class AcqSignalCodeParameters:
    rate_chips_per_sec: float
    length_chips: int
    # Stated on the signal's own chip axis, carrying 0 wherever this component
    # does not transmit -- so a chip-interleaved component (e.g. L2C's CM) is
    # already zero-filled on its sibling's chips and cannot spuriously correlate
    # against that sibling's code. See `utils.code_components`.
    sequence: NDArray[np.int8]
    # Needed by the fine search to slave the code rate to Doppler.  At 2450 Hz on
    # L5 the code drifts 0.107 chips per 5 ms block, so this is not optional there.
    carrier_freq_hz: float = 0.0
    # The component's subcarrier, if it has one.  The replica must carry it: a BOC
    # subcarrier is odd-symmetric within each chip, so correlating a BOC signal
    # against a plain BPSK replica gives roughly nothing at zero delay.  It is
    # applied from the fractional chip position, exactly as the correlator kernel
    # does -- `rate_chips_per_sec` stays the signal's true chip rate.
    subcarrier: Optional[Subcarrier] = None


@dataclass
class CorrelationResult:
    correlation_matrix: NDArray[np.float64]
    start_doppler_hz: float
    doppler_resolution_hz: float
    start_code_phase_seconds: float
    code_phase_resolution_seconds: float

    @property
    def num_doppler_bins(self) -> int:
        return self.correlation_matrix.shape[0]

    @property
    def num_code_phase_bins(self) -> int:
        return self.correlation_matrix.shape[1]

    @property
    def doppler_bins_hz(self) -> NDArray[np.float64]:
        return np.asarray(
            self.start_doppler_hz
            + np.arange(self.num_doppler_bins) * self.doppler_resolution_hz,
            dtype=np.float64,
        )

    @property
    def code_phase_bins_seconds(self) -> NDArray[np.float64]:
        return np.asarray(
            self.start_code_phase_seconds
            + np.arange(self.num_code_phase_bins) * self.code_phase_resolution_seconds,
            dtype=np.float64,
        )


@dataclass
class AcquisitionResult:
    uptime_epoch_ms: float
    signal_id: str
    peak_doppler_bin: int
    peak_code_phase_bin: int
    normalized_peak_value: float
    # The total the caller asked for, across this signal's whole search grid, and
    # the per-cell rate it was converted into.  Both are kept because only the
    # first is meaningful to a human and only the second sets the threshold.
    prob_false_alarm_total: float
    prob_false_alarm_per_cell: float
    num_detection_cells: int
    detection_threshold: float
    noise_var: float
    signal_detected: bool
    correlation_result: CorrelationResult
    config: AcquisitionConfiguration
    # Localised refinement of this peak, when `config.fine_search` is set and the
    # signal was detected.  Its axes are absolute, but the delay axis is a *local,
    # unwrapped* one about the peak: near the end of the code period it runs past
    # the period rather than wrapping, so that it stays monotonic and plottable.
    # Consumers wanting a code phase must use `acq_code_phase_seconds`, which
    # wraps; doing arithmetic on this axis directly will not.
    fine_correlation_result: CorrelationResult | None = None
    fine_peak_doppler_bin: int = 0
    fine_peak_code_phase_bin: int = 0
    # The recovered code phase is only pinned modulo the period of the code that
    # was correlated against, because that code repeats within the replica.  For
    # L5 acquired on Q x NH20 that is 20 ms; for L1 C/A it is 1 ms.
    code_phase_ambiguity_ms: float = 0.0
    # Chip rate of the code that was correlated against, so a delay axis can be
    # expressed in chips without the caller having to look it up.
    acquisition_code_rate_chips_per_sec: float = 0.0

    @property
    def peak_snr_db(self) -> float:
        """
        Peak height in dB above the *expected noise level*.

        `normalized_peak_value` is the peak divided by the noise variance, which
        under noise alone is chi-squared with `2M` degrees of freedom and so has
        mean `2M`.  Dividing that out puts 0 dB at the noise floor, which is the
        number a person can reason about; the raw ratio moves with `num_blocks`
        even when nothing about the signal changed.
        """
        return float(10.0 * np.log10(self.normalized_peak_value / (2 * self.config.num_blocks)))

    @property
    def fine_peak_snr_db(self) -> float | None:
        """
        The refined peak on the same dB-above-noise scale as `peak_snr_db`, or None
        when no fine search ran.

        Divides by the **coarse** `noise_var`: the fine grid holds only a few dozen
        cells clustered on a peak, nowhere near a fair sample of the noise, and
        detection was decided on the coarse statistics regardless.
        """
        if self.fine_correlation_result is None:
            return None
        peak = float(
            self.fine_correlation_result.correlation_matrix[
                self.fine_peak_doppler_bin, self.fine_peak_code_phase_bin
            ]
        )
        return float(10.0 * np.log10(peak / self.noise_var / (2 * self.config.num_blocks)))

    @property
    def detection_threshold_db(self) -> float:
        """The detection threshold on the same dB-above-noise scale as `peak_snr_db`."""
        return float(10.0 * np.log10(self.detection_threshold / (2 * self.config.num_blocks)))

    @property
    def acq_code_phase_ms(self) -> float:
        """Recovered code phase in ms, modulo `code_phase_ambiguity_ms`."""
        return self.acq_code_phase_seconds * 1e3

    @property
    def coarse_doppler_hz(self) -> float:
        """Doppler from the coarse grid, whether or not a fine search ran."""
        return float(self.config.doppler_hypotheses_hz[self.peak_doppler_bin])

    @property
    def coarse_code_phase_seconds(self) -> float:
        """Code phase from the coarse grid, whether or not a fine search ran."""
        return self.peak_code_phase_bin / self.config.sample_rate

    @property
    def acq_doppler_hz(self) -> float:
        """The best available Doppler: refined when a fine search ran, else coarse."""
        if self.fine_correlation_result is None:
            return self.coarse_doppler_hz
        return float(
            self.fine_correlation_result.doppler_bins_hz[self.fine_peak_doppler_bin]
        )

    @property
    def acq_code_phase_seconds(self) -> float:
        """
        The best available code phase: refined when a fine search ran, else coarse.

        Wrapped into one period of the acquisition code.  The fine grid's own delay
        axis is deliberately left unwrapped so it stays monotonic, so a peak found
        just past the end of the code period comes back here reduced -- which is
        what tracking and `resolve_acquisition_ambiguities` need.
        """
        if self.fine_correlation_result is None:
            return self.coarse_code_phase_seconds
        seconds = float(
            self.fine_correlation_result.code_phase_bins_seconds[self.fine_peak_code_phase_bin]
        )
        period_seconds = self.code_phase_ambiguity_ms * 1e-3
        return seconds % period_seconds if period_seconds > 0 else seconds


def refine_acquisition_peak(
    sample_block: NDArray[np.complex64],
    acq_config: AcquisitionConfiguration,
    code_params: "AcqSignalCodeParameters",
    code_set,
    coarse_doppler_hz: float,
    coarse_code_phase_seconds: float,
) -> tuple[NDArray[np.float64], int, int]:
    """
    Re-score a detected peak on a finer grid, over a small window around it.

    Returns `(grid, peak_doppler_index, peak_delay_index)` where `grid` is
    `(num_doppler_hypotheses, num_delay_taps)` on the same scale as the coarse
    correlation -- `|corr|^2 / N_coh` summed over blocks -- so the two are directly
    comparable at the bins they share.

    Two differences from the coarse search, both deliberate:

    * The delay axis is evaluated directly rather than by FFT, so it can sit at
      fractional samples.  `correlate__multicomponent` fills every tap from one
      pass over the samples, which is why the delay dimension is nearly free and
      only Doppler costs extra passes.
    * The code rate is slaved to Doppler (`nominal * (1 + doppler / carrier)`).
      The coarse replica runs at the nominal rate, so it does not model the code
      drifting within the dwell -- 0.107 chips per 5 ms block at 2450 Hz on L5,
      which lands the four blocks' peaks 0.107 chips apart and smears the
      non-coherent sum.  The fine search therefore recovers drift as well as
      scalloping, and only matches the coarse value where the drift is negligible.

      This is a **bias**, not just a loss, and that is the more important half.
      With the blocks' peaks spread across the dwell the coarse argmax lands on a
      compromise somewhere toward the dwell's middle, whereas the code phase every
      consumer wants is the one at `sample_block_uptime_epoch_ms` -- the dwell's
      *start*, which is where `create_tracking_channels` seeds the channel.  The
      fine search states each block's phase explicitly from that origin, so it
      reports the start.  Measured against synthetic truth, the refined estimate is
      exact at 0, 1000, 2450, 4000 and -2450 Hz, while the coarse one walks off by
      up to a full bin in the direction of the drift.  Half the dwell is the worst
      case: 0.435 chips at 5 kHz on L5 over 10 ms.

    Blocks are combined by square law with carrier phase zero in each, exactly as
    in acquisition and in `ambiguity_resolution.resolve_code_ambiguity`: only the
    rotation *within* a block matters.
    """
    fine = acq_config.fine_search
    assert fine is not None

    samples = np.ascontiguousarray(sample_block, dtype=np.complex64)
    n_coh = acq_config.coherent_length_samples
    nominal_rate = code_params.rate_chips_per_sec

    # Delay taps, in chips, relative to the coarse peak.  Uniform sub-division of a
    # sample, so the coarse bins are the multiples of `delay_factor`.
    tap_index = np.arange(
        -fine.delay_halfwidth_bins * fine.delay_factor,
        fine.delay_halfwidth_bins * fine.delay_factor + 1,
    )
    tap_seconds = tap_index / (acq_config.sample_rate * fine.delay_factor)
    taps_chips = np.ascontiguousarray(tap_seconds * nominal_rate, dtype=np.float64)

    doppler_index = np.arange(
        -fine.doppler_halfwidth_bins * fine.doppler_factor,
        fine.doppler_halfwidth_bins * fine.doppler_factor + 1,
    )
    doppler_hz = coarse_doppler_hz + doppler_index * (
        acq_config.doppler_step_hz / fine.doppler_factor
    )

    grid = np.zeros((len(doppler_hz), len(taps_chips)), dtype=np.float64)
    corr = np.zeros((len(taps_chips), code_set.num_components), dtype=np.complex64)

    for d, doppler in enumerate(doppler_hz):
        code_rate = nominal_rate * (1.0 + doppler / code_params.carrier_freq_hz)
        base_chips = coarse_code_phase_seconds * nominal_rate
        for block in range(acq_config.num_blocks):
            start = block * n_coh
            block_samples = samples[start : start + n_coh]
            if len(block_samples) < n_coh:
                break
            code_phase_chips = base_chips + (start / acq_config.sample_rate) * code_rate
            corr.fill(0.0)
            correlate__multicomponent(
                block_samples,
                acq_config.sample_rate,
                0.0,
                float(doppler),
                code_set,
                code_rate,
                code_phase_chips,
                taps_chips,
                corr,
            )
            # Same normalisation as the coarse path, so the grids are comparable.
            grid[d] += np.abs(corr[:, 0].astype(np.complex128)) ** 2 / n_coh

    peak_d, peak_t = np.unravel_index(grid.argmax(), grid.shape)
    return grid, int(peak_d), int(peak_t)


def _acquisition_component(code_params: "AcqSignalCodeParameters") -> CodeComponent:
    """
    The acquisition sequence as a single `CodeComponent`, subcarrier included.

    It must be the *acquisition* code, not the signal's tracking code set: on L5
    the acquisition code is the composite Q x NH20, and refining against plain Q
    would let the overlay's sign flips cancel across a coherent block.
    """
    return CodeComponent(
        name="acquisition",
        sequence=np.ascontiguousarray(code_params.sequence, dtype=np.int8),
        # Branch is meaningful only as a *difference* between components of one
        # signal; this set has a single component, so the choice cannot matter.
        branch=Branch.I,
        subcarrier=code_params.subcarrier,
    )


def _acquisition_code_set(code_params: "AcqSignalCodeParameters"):
    """
    A one-component `CodeSet` over the acquisition sequence, for the fine search.

    The fine search runs through `correlate__multicomponent`, so carrying the
    subcarrier here is all it takes for the refinement to model it too -- the same
    kernel tracking uses, on the same chip axis.

    `allow_partial_coverage` is required and correct here -- L2C acquires on CM
    alone, which is zero on CL's chips, and `build_code_set`'s own docstring names
    scoring one component in isolation as exactly this case.
    """
    return build_code_set(
        [_acquisition_component(code_params)],
        allow_partial_coverage=True,
        chip_rate_hz=code_params.rate_chips_per_sec,
    )


def pack_coherent_blocks(
    sample_block: NDArray[np.complex64],
    num_blocks: int,
    fft_length_samples: int,
    coherent_length_samples: int,
    doppler_hz: float = 0.0,
    sample_rate: float | None = None,
) -> NDArray[np.complex64]:
    """
    Lay each coherent block into a full-length FFT window, zero elsewhere.

    A block shorter than the replica has to be zero-padded, and *where* the data
    sits in that window matters.  Block `j` was received at samples
    `[j * coherent_length, ...)`, so the code had already advanced that far; its
    correlation peak therefore sits at lag `p + j * coherent_length`.  Padding
    every block at position 0 would leave each peak at a different lag and the
    square-law sum would smear them across the code phase axis instead of
    stacking them.

    Placing block `j` at the offset it actually occupied within the code period --
    `j * coherent_length` modulo the period -- puts every peak back at the same
    lag `p`.  Equivalently: each block is the whole dwell window with everything
    outside its own slice zeroed, so all blocks share one time origin.

    Zero-padding the *data* rather than the replica is what keeps the circular
    correlation valid: the replica stays a whole code period, so its wraparound
    is the code's own periodic extension, while the zeros contribute nothing.

    When `coherent_length == fft_length` every offset is zero and this reduces
    exactly to the reshape it replaces.

    **Folding.**  A block LONGER than the replica covers several whole code
    periods, and they are summed onto one window before the transform.  That is
    the same circular placement carried further -- sample `i` still belongs at
    `(start + i) mod fft_length`, it just wraps more than once -- and it is exactly
    equivalent to correlating the long block against a replica tiled to match,
    because a tiled replica's spectrum is non-zero only every `fold` bins and the
    transform of the fold is precisely those bins.  It costs a `fold`-times shorter
    FFT and leaves the code phase unambiguous inside one period instead of
    repeating the same answer `fold` times over.

    **`doppler_hz` must be applied here, not to the packed window.**  Wipe-off is
    indexed from the start of the *block*, so each of the folded periods carries
    its own phase `exp(-2j*pi*f*rL/fs)`, and that inter-period rotation is the
    whole difference between one coherent integration of `fold` periods and `fold`
    incoherent ones.  Ramping the window afterwards cannot express it: every period
    lands on the same index and would be given the same phase.  At 200 Hz over five
    1 ms periods those five contributions sit 72 degrees apart and sum to exactly
    zero -- the peak does not degrade, it vanishes.  For an unfolded block the two
    orders differ only by a constant phase per block, which the square law
    discards, which is why this argument was not needed before.
    """
    required = num_blocks * coherent_length_samples
    if len(sample_block) < required:
        raise ValueError(
            f"need {required} samples for {num_blocks} x {coherent_length_samples}-sample "
            f"coherent blocks, got {len(sample_block)}"
        )
    if doppler_hz and sample_rate is None:
        raise ValueError("sample_rate is required to apply a Doppler ramp")

    blocks = np.zeros((num_blocks, fft_length_samples), dtype=sample_block.dtype)
    for j in range(num_blocks):
        src = sample_block[j * coherent_length_samples : (j + 1) * coherent_length_samples]
        if doppler_hz:
            ramp = np.exp(
                -2j * np.pi * doppler_hz * np.arange(len(src)) / float(sample_rate)
            )
            src = (src * ramp).astype(sample_block.dtype)
        start = (j * coherent_length_samples) % fft_length_samples
        stop = start + coherent_length_samples

        if coherent_length_samples > fft_length_samples:
            pad = (-len(src)) % fft_length_samples
            if pad:
                src = np.concatenate([src, np.zeros(pad, dtype=src.dtype)])
            folded = src.reshape(-1, fft_length_samples).sum(axis=0)
            blocks[j] = np.roll(folded, start)
        elif stop <= fft_length_samples:
            blocks[j, start:stop] = src
        else:
            # The block straddles a code period boundary; it wraps, and the
            # circular correlation handles the wrap correctly.
            split = fft_length_samples - start
            blocks[j, start:] = src[:split]
            blocks[j, : stop - fft_length_samples] = src[split:]
    return blocks


def run_acquisition(
    sample_block: NDArray[np.complex64],
    sample_block_uptime_epoch_ms: float,
    acq_config: AcquisitionConfiguration,
    code_parameters: Dict[str, AcqSignalCodeParameters],
    prob_false_alarm_total: float,
    print_progress: bool = False,
    noise_var_method: str = "abscorrmean",
    save_corr_doppler_window_bins: Optional[int] = 1,
) -> Dict[str, AcquisitionResult]:
    """
    Perform BPSK acquisition on the given sample block for all signals defined in code_parameters.

    Returns a dictionary mapping signal IDs to their respective AcquisitionResult.

    `prob_false_alarm_total` is the chance that *any* cell in one signal's search
    grid false-alarms -- a number that keeps its meaning when the sample rate,
    replica length or Doppler range change.  It is converted to a per-cell rate
    here; callers should not pre-correct it.  Across a sweep of P satellites,
    expect about `P * prob_false_alarm_total` false acquisitions, so this is the
    knob for trading sensitivity against false alarms:

        1e-3   sensitive: ~0.03 false acquisitions per 32-PRN sweep
        1e-6   balanced (about 1 dB stricter than 1e-3 on a large grid)
        1e-9   conservative; the extra ~0.7 dB buys little, because what actually
               produces false acquisitions in GNSS is cross-correlation against a
               strong satellite, which no threshold setting addresses.

    Pre-computed replicas and FFTs are stored for each signal in the config `replica_cache_dict`.
    The values in the cache entries must be recomputed when acquisition parameters change or when the sampling rate changes.

    Options for noise variance estimation:
        "abscorrmean": compute noise variance from the mean of abs(corr)**2 matrix
        "abscorrvar": compute noise variance from the square root of the variance of abs(corr)**2 matrix

    `save_corr_doppler_window_bins` controls how much of each signal's
    delay-Doppler matrix is *kept* in the returned `CorrelationResult`; detection
    itself always uses the whole thing.  `1` (the default) keeps the peak Doppler
    bin and one either side, with the full code-delay axis -- 704 MB per signal
    becomes about 5 MB, which matters because a 32-PRN sweep otherwise retains
    ~22 GB.  The delay axis is the one worth keeping: multipath lives there, and at
    22 Msps one code-phase bin is 45 ns, about 14 m.  `None` keeps the full grid,
    which is needed for anything reading the Doppler response
    (`plotting.plot_acquisition_doppler_slices`) or the whole-grid noise
    distribution (`plotting.plot_acquisition_correlation_histogram`).

    Rows outside the searched grid are filled with NaN rather than clipped away, so
    the retained matrix is always `2 * window + 1` rows and the peak is always the
    centre row -- true even for a signal whose Doppler lands at the edge of the
    search range.  Consumers must use the `nan*` reductions.

    Sub-bin Doppler refinement is configured by `acq_config.fine_search_factors`.
    A Doppler factor of `k` repeats the search with the samples shifted down by
    `j/k` of a bin for each `j`, keeping whichever pass peaks highest -- so the
    worst-case Doppler error falls to `1/k` of half a bin, at `k` times the cost.
    It matters whenever the seed would otherwise be looser than the tracking loop
    can pull in from.
    """
    acquisition_results: Dict[str, AcquisitionResult] = {}

    #
    # Lay M coherent blocks into full-length FFT windows.  N is the replica (and
    # FFT) length; N_coh is how much data each block actually integrates, which is
    # shorter whenever the caller has capped coherent integration.
    M = acq_config.num_blocks
    N = acq_config.replica_length_samples
    N_coh = acq_config.coherent_length_samples
    # One packed set per sub-bin Doppler offset.  Without folding there is exactly
    # one, at 0 Hz, and this is the single call it always was.
    samples_by_sub_offset = [
        pack_coherent_blocks(
            sample_block, M, N, N_coh,
            doppler_hz=float(f_sub), sample_rate=acq_config.sample_rate,
        )
        for f_sub in acq_config.doppler_sub_offsets_hz
    ]
    samples = samples_by_sub_offset[0]

    # Convert the caller's total into the per-cell rate the threshold needs.
    #
    # This is deliberately not the caller's job.  The right per-cell value depends
    # on sample rate, replica duration and Doppler range, so a hard-coded one
    # silently stops meaning what it did the moment any of those change: 1e-7 per
    # cell is 0.025 expected false alarms over a 250,000-cell grid and 2.5 over a
    # 25,000,000-cell one.  The total is the quantity a person can reason about.
    #
    # Sidak, in the numerically stable form: 1 - (1 - p)**(1/n) cancels to exactly
    # 0.0 once p/n drops below float64 eps, and chi2.isf(0) is inf, so the naive
    # expression fails by detecting nothing at all rather than by erroring.
    # The fine search deliberately does NOT enter this count.  It runs only on peaks
    # already declared detections, over a neighbourhood the threshold was never
    # asked about, so inflating the cell count for it would tighten the Sidak
    # correction and quietly desensitise the coarse search that does the deciding.
    # Delay cells number one replica period, never the coherent block: a replica
    # tiled to cover several periods repeats its correlation exactly, so the extra
    # copies are the same numbers rather than further chances at a false alarm.
    num_detection_cells = (
        acq_config.num_doppler_hypotheses * acq_config.replica_length_samples
    )
    if not 0.0 < prob_false_alarm_total < 1.0:
        raise ValueError(
            f"prob_false_alarm_total must be in (0, 1), got {prob_false_alarm_total}"
        )
    prob_false_alarm_per_cell = -np.expm1(
        np.log1p(-prob_false_alarm_total) / num_detection_cells
    )
    # The threshold depends only on the per-cell rate and the number of blocks, so
    # it is one number for the whole sweep rather than a per-signal quantity.
    detection_threshold = float(scipy.stats.chi2(df=2 * M).isf(prob_false_alarm_per_cell))
    threshold_db = 10.0 * np.log10(detection_threshold / (2 * M))
    # Compute conjugated FFT of blocks, once per sub-bin Doppler hypothesis.
    # Blocks are combined non-coherently below, so applying the same phase ramp
    # from the start of each block (rather than from the start of the capture) is
    # sufficient -- only the frequency shift within a block matters.  The ramp is
    # applied across the padded window; the padding stays zero, and the block's
    # own position within the window contributes only a constant phase, which the
    # square law discards.
    # scipy.fft rather than numpy: it releases the GIL and threads across the
    # blocks with workers=-1.  Single-threaded scipy is *slower* than numpy here,
    # so the workers argument is not optional.
    conj_samples_fft_by_sub = [
        np.conj(scipy.fft.fft(packed, axis=1, workers=-1))
        for packed in samples_by_sub_offset
    ]
    conj_samples_fft = conj_samples_fft_by_sub[0]

    if print_progress:
        # The code length is what code phase is ambiguous over; state it in the
        # header when every signal shares one, rather than on every row.  In chips,
        # because that is the modulus in the unit the column is printed in.
        code_lengths_chips = {p.length_chips for p in code_parameters.values()}
        ambiguity = (
            f", mod {code_lengths_chips.pop():,}" if len(code_lengths_chips) == 1 else ""
        )
        print(
            f"Detection threshold: {threshold_db:.2f} dB above noise "
            f"(p_fa {prob_false_alarm_total:g} over {num_detection_cells:,} cells/PRN)"
        )
        print()
        # With a fine search configured every column carries `coarse => fine`, so
        # the columns are widened and the code phase gains decimals -- one sample is
        # 4.5e-5 ms at 22 Msps, invisible at the 3 decimals the plain table uses.
        showing_fine = acq_config.fine_search is not None
        # Code phase in chips: the unit the correlator counts in, the unit the
        # ambiguity is a whole number of, and the one in which a delay error means
        # the same thing on every signal.  Three decimals resolves one sample on any
        # front end fast enough to be used here.
        cp_header = "Code phase [chips" + ambiguity + "]"
        snr_w, dopp_w = (18, 21) if showing_fine else (9, 13)
        # Two 11-character chip figures either side of " => " when a fine search
        # ran, one otherwise; the header is usually the wider of the two.
        cp_w = max(len(cp_header), 26 if showing_fine else 11)
        if showing_fine:
            print("Detected signals show  coarse => fine.")
            print()
        print(f"  {'PRN':<5} {'SNR [dB]':>{snr_w}} {'Doppler [Hz]':>{dopp_w}} {cp_header:>{cp_w}}")
        print(f"  {'-' * 5} {'-' * snr_w} {'-' * dopp_w} {'-' * cp_w}")

    # Undetected PRNs are the great majority of a sweep and every one of their rows
    # says the same thing -- a noise maximum, and two dashes where the location
    # would go.  They are summarised in one line under the table instead: the count,
    # and the range their peaks span, which is the only part worth reading (a peak
    # far above the others is a marginal signal worth a longer dwell).
    undetected: list[tuple[str, float]] = []

    for signal_id, code_params in code_parameters.items():

        # The correlation is circular, so the replica's wraparound is only the
        # code's own periodic extension if the replica spans a whole number of code
        # periods.  Half a period aliases silently: a true code phase in the
        # uncovered part is reported inside the covered part, with a healthy peak
        # and signal_detected set.  Cheap to check, and it is not otherwise
        # checkable anywhere -- the config does not know the code, and the code
        # parameters do not know the replica length.
        code_period_ms = code_params.length_chips / code_params.rate_chips_per_sec * 1e3
        periods_per_replica = acq_config.coherent_duration_replica_ms / code_period_ms
        if abs(periods_per_replica - round(periods_per_replica)) > 1e-9 or periods_per_replica < 1:
            raise ValueError(
                f"{signal_id}: coherent_duration_replica_ms {acq_config.coherent_duration_replica_ms} must be a "
                f"positive whole number of code periods ({code_period_ms:g} ms for "
                f"{code_params.length_chips} chips at {code_params.rate_chips_per_sec:g} cps), "
                f"got {periods_per_replica:g}; a partial period aliases the code phase"
            )

        # Check if there is a cached replica for this signal
        if signal_id in acq_config.replica_cache_dict:
            replica_entry = acq_config.replica_cache_dict[signal_id]
            replica_samples_fft = replica_entry.replica_fft
        else:
            replica_samples = np.zeros(
                N, dtype=np.complex64
            )
            chips_arr = (
                0.0 + acq_config.replica_time_arr * code_params.rate_chips_per_sec
            )
            chip_indices = chips_arr.astype(int) % code_params.length_chips
            replica_values = code_params.sequence[chip_indices]
            if code_params.subcarrier is not None:
                # `replica_time_arr` starts at 0, so truncation is floor and the
                # remainder is the position within the chip -- the same quantity
                # the correlator kernel derives per sample.
                replica_values = replica_values * subcarrier_signs(
                    _acquisition_component(code_params),
                    code_params.rate_chips_per_sec,
                    chip_indices,
                    chips_arr - np.floor(chips_arr),
                )
            replica_samples[: acq_config.replica_length_samples] = replica_values.astype(float)
            replica_samples_fft = np.fft.fft(replica_samples)
            replica_entry = SignalReplicaCacheEntry(
                replica_samples, replica_samples_fft
            )
            acq_config.replica_cache_dict[signal_id] = replica_entry

        doppler_search_bins = acq_config.doppler_search_bins

        # Match the sample precision rather than always using float64: real
        # collects are complex64, where float64 here only doubles the write
        # bandwidth.  Tests that feed complex128 keep their precision.
        num_sub = acq_config.fold_factor
        correlation = np.zeros(
            (len(doppler_search_bins) * num_sub, N),
            dtype=np.float32 if samples.dtype == np.complex64 else np.float64,
        )

        # Whole bins outer, sub-bin offsets inner, so row index is monotonic in
        # Doppler and `doppler_hypotheses_hz` indexes straight into it.  Without
        # folding num_sub is 1 and this is the original single loop.
        for i in range(len(doppler_search_bins) * num_sub):
            bin_index, sub_index = divmod(i, num_sub)
            roll = int(doppler_search_bins[bin_index])
            # Coherent integration over the N_coh non-zero samples of each block;
            # the padding contributes nothing, so z_noise ~ CN(0, N_coh*noise_var)
            # regardless of the FFT length.
            shifted_replica_fft = np.roll(replica_samples_fft, roll)
            corr = scipy.fft.ifft(
                conj_samples_fft_by_sub[sub_index] * shifted_replica_fft[None, :],
                axis=1, workers=-1,
            )
            # non-coherent square-law summation over M blocks, normalized by the
            # number of samples actually integrated (N_coh, not N -- they differ
            # once coherent integration is shorter than the replica, and
            # normalizing by N would under-report the noise by N/N_coh)
            # y_noise / noise_var ~ ChiSquared(2M)
            #
            # einsum rather than np.sum(np.abs(corr)**2): `abs` then `**2` each
            # materialise a full (M, N) temporary, and this is a third of the
            # inner loop.  Same value to ~2e-7 relative.
            correlation[i] = (
                np.einsum("ij,ij->j", corr.real, corr.real)
                + np.einsum("ij,ij->j", corr.imag, corr.imag)
            ) / N_coh

        peak_doppler_bin_idx, peak_sample_bin_idx = np.unravel_index(
            correlation.argmax(), correlation.shape
        )
        peak_doppler_bin = int(peak_doppler_bin_idx)
        peak_sample_bin = int(peak_sample_bin_idx)
        peak_val = float(correlation[peak_doppler_bin, peak_sample_bin])

        # Estimate noise distribution
        # y_noise = X * noise_var
        # E[y_noise] = 2 * M * noise_var
        # Var[y_noise] = 4 * M * noise_var**2
        if noise_var_method == "abscorrmean":
            # Don't worry about peak power, its fine to overestimate noise a bit
            y_noise_mean = np.mean(correlation)
            noise_var = float(y_noise_mean / (2 * M))
        elif noise_var_method == "abscorrvar":
            # Another way to estimate noise stddev;  can be way off if strong signal present, but can be better estimate when lots of narrowband interference
            y_noise_var = np.var(correlation)
            noise_var = float(np.sqrt(y_noise_var / (4 * M)))
        else:
            raise ValueError(f"Unknown noise_var_method: {noise_var_method}")

        normalized_peak_value = peak_val / noise_var
        signal_detected = normalized_peak_value > detection_threshold

        # The reported Doppler axis belongs to the winning pass, so it carries the
        # same sub-bin offset as the peak.
        # --- fine search ------------------------------------------------------
        # Gated on detection by construction: a peak that did not clear the
        # threshold is a noise maximum, and refining its neighbourhood would only
        # dress up noise as a measurement.
        fine_correlation_result = None
        fine_peak_doppler_bin = 0
        fine_peak_code_phase_bin = 0
        if acq_config.fine_search is not None and signal_detected:
            fine = acq_config.fine_search
            code_set = acq_config.fine_code_set_cache.get(signal_id)
            if code_set is None:
                code_set = _acquisition_code_set(code_params)
                acq_config.fine_code_set_cache[signal_id] = code_set

            coarse_doppler_hz = float(
                acq_config.doppler_hypotheses_hz[peak_doppler_bin]
            )
            coarse_code_phase_seconds = peak_sample_bin / acq_config.sample_rate
            fine_grid, fine_peak_doppler_bin, fine_peak_code_phase_bin = refine_acquisition_peak(
                sample_block,
                acq_config,
                code_params,
                code_set,
                coarse_doppler_hz,
                coarse_code_phase_seconds,
            )
            fine_correlation_result = CorrelationResult(
                fine_grid,
                coarse_doppler_hz - fine.doppler_halfwidth_bins * acq_config.doppler_step_hz,
                acq_config.doppler_step_hz / fine.doppler_factor,
                coarse_code_phase_seconds - fine.delay_halfwidth_bins / acq_config.sample_rate,
                1.0 / (acq_config.sample_rate * fine.delay_factor),
            )

        if save_corr_doppler_window_bins is None:
            retained = correlation
            first_row_doppler_hz = float(acq_config.doppler_hypotheses_hz[0])
        else:
            # Keep `peak +/- window` Doppler rows and the whole delay axis.  Rows
            # off either end of the searched grid are NaN rather than dropped, so
            # the shape is the same for every signal and the peak is always the
            # centre row -- no consumer has to go looking for it.
            window = int(save_corr_doppler_window_bins)
            retained = np.full((2 * window + 1, N), np.nan, dtype=correlation.dtype)
            src0 = max(0, peak_doppler_bin - window)
            src1 = min(acq_config.num_doppler_hypotheses, peak_doppler_bin + window + 1)
            dst0 = max(0, window - peak_doppler_bin)
            retained[dst0 : dst0 + (src1 - src0)] = correlation[src0:src1]
            # From the UNCLAMPED first row, so the Doppler axis stays correct across
            # the NaN rows; doppler_search_bins[src0] would be wrong whenever the
            # window overhangs the grid.
            first_row_doppler_hz = (
                acq_config.doppler_hypotheses_hz[peak_doppler_bin]
                - window * acq_config.doppler_step_hz
            )

        corr_result = CorrelationResult(
            retained,
            first_row_doppler_hz,
            acq_config.doppler_step_hz,
            0.0,
            1 / acq_config.sample_rate,
        )

        acq_result = AcquisitionResult(
            sample_block_uptime_epoch_ms,
            signal_id,
            peak_doppler_bin,
            peak_sample_bin,
            normalized_peak_value,
            prob_false_alarm_total,
            prob_false_alarm_per_cell,
            num_detection_cells,
            detection_threshold,
            noise_var,
            signal_detected,
            corr_result,
            acq_config,
            code_phase_ambiguity_ms=code_period_ms,
            fine_correlation_result=fine_correlation_result,
            fine_peak_doppler_bin=fine_peak_doppler_bin,
            fine_peak_code_phase_bin=fine_peak_code_phase_bin,
            acquisition_code_rate_chips_per_sec=code_params.rate_chips_per_sec,
        )

        acquisition_results[signal_id] = acq_result

        if print_progress:
            # Only detections get a row.  Doppler and code phase are meaningless
            # without one -- the peak is then just the noise maximum -- so the rest
            # are counted up and summarised under the table.
            def _chips(seconds: float) -> str:
                return f"{seconds * code_params.rate_chips_per_sec:11,.3f}"

            fine_snr = acq_result.fine_peak_snr_db
            if not signal_detected:
                undetected.append((signal_id, acq_result.peak_snr_db))
                continue
            if fine_snr is None:
                snr = f"{acq_result.peak_snr_db:.1f}"
                doppler = f"{acq_result.acq_doppler_hz:+4.0f}"
                code_phase = _chips(acq_result.acq_code_phase_seconds)
            else:
                snr = f"{acq_result.peak_snr_db:.1f} => {fine_snr:.1f}"
                doppler = (
                    f"{acq_result.coarse_doppler_hz:+.0f} => {acq_result.acq_doppler_hz:+.0f}"
                )
                period_s = acq_result.code_phase_ambiguity_ms * 1e-3
                coarse_s = acq_result.coarse_code_phase_seconds
                code_phase = (
                    f"{_chips(coarse_s % period_s if period_s > 0 else coarse_s)} => "
                    f"{_chips(acq_result.acq_code_phase_seconds)}"
                )
            print(
                f"  {signal_id:<5} {snr:>{snr_w}} {doppler:>{dopp_w}} {code_phase:>{cp_w}}"
            )

    if print_progress and undetected:
        peaks = [snr for _, snr in undetected]
        loudest = max(undetected, key=lambda item: item[1])
        print(
            f"  {len(undetected)} PRN(s) below threshold, peaks {min(peaks):.1f}-"
            f"{max(peaks):.1f} dB (loudest {loudest[0]} at {loudest[1]:.1f})"
        )

    return acquisition_results
