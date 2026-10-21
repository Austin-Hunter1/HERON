"""
Numbers for comparing two tracking runs of one signal, with and without BOC(6,1).

Why this module exists: the user wants to toggle the BOC(6,1) part of B1C and L1C
and compare how well each model tracks (D-077).  Both runs track the same
recording, so every difference below comes from the model.  The metrics need
only the tracking files, not a navigation solution:

- ``cn0_mean_dbhz``: mean C/N0 of the pilot component.  Wide band should gain
  up to the BOC(6,1) share of the power (about 0.4 dB for B1C and L1C).
- ``cmc_std_m``: code-minus-carrier scatter after a local polynomial fit, in
  metres.  It shows the noise and the multipath of the code, with the carrier as
  a quiet reference.  A better code discriminator lowers it.
- ``pll_fraction`` and ``lock_losses``: how much of the run holds phase lock,
  and how often it is lost.

All settings are arguments, with defaults in `MetricSettings`, not constants in
the code.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import observables, tracking_io
from .signal_interfaces import ACQUISITION_POLICIES, SIGNALS

SPEED_OF_LIGHT_M_PER_S = 299_792_458.0


@dataclass(frozen=True)
class MetricSettings:
    """Tunable values of the metrics."""

    cmc_window_s: float = 10.0
    """Length of each window of the local polynomial fit that removes the slow trend."""
    cmc_polynomial_order: int = 2
    """Order of that fit.  Ionosphere divergence and range-rate errors are slow."""
    cmc_min_window_epochs: int = 20
    """A window with fewer epochs is skipped, so a fit never has too few points."""
    cn0_skip_s: float = 2.0
    """Time at the start of the run that the mean C/N0 ignores (loops still settling)."""
    use_subcarrier_correction: bool = True
    """Add the subcarrier loop's offset to the code phase (`observables.refined_code_phase_ms`)."""


@dataclass(frozen=True)
class ChannelMetrics:
    """The numbers for one satellite in one run."""

    signal_id: str
    epochs: int
    pll_fraction: float
    lock_losses: int
    cn0_mean_dbhz: float
    cmc_std_m: float
    cmc_epochs: int


def count_lock_losses(pll_mode: np.ndarray) -> int:
    """Number of changes from PLL back to FLL: each one is a loss of phase lock."""
    mode = np.asarray(pll_mode, dtype=bool)
    return int(np.count_nonzero(mode[:-1] & ~mode[1:]))


def cmc_std_m(
    uptime_ms: np.ndarray,
    code_phase_ms: np.ndarray,
    carrier_phase_cycles: np.ndarray,
    carrier_freq_hz: float,
    settings: MetricSettings = MetricSettings(),
) -> tuple[float, int]:
    """
    RMS of the code-minus-carrier residual in metres, and how many epochs it used.

    Both quantities are turned into a range change.  The code delay is
    ``code_phase_ms - uptime_ms``: it grows when the satellite comes closer, as does
    the carrier phase.  So ``range = -delay * c`` for the code and
    ``range = -phase * wavelength`` for the carrier.  Their difference has the
    ionosphere, the code noise and the multipath; a local polynomial removes the
    slow part and the rest is the scatter.  Returns ``(nan, 0)`` if no window has
    enough epochs.
    """
    uptime_s = np.asarray(uptime_ms, dtype=float) * 1e-3
    wavelength_m = SPEED_OF_LIGHT_M_PER_S / carrier_freq_hz
    code_range = -(np.asarray(code_phase_ms, dtype=float) - np.asarray(uptime_ms, dtype=float)) * (
        SPEED_OF_LIGHT_M_PER_S * 1e-3
    )
    carrier_range = -np.asarray(carrier_phase_cycles, dtype=float) * wavelength_m
    cmc = code_range - carrier_range
    if len(uptime_s) == 0:
        return float("nan"), 0

    squares: list[np.ndarray] = []
    window_index = np.floor((uptime_s - uptime_s[0]) / settings.cmc_window_s).astype(int)
    for index in np.unique(window_index):
        selected = window_index == index
        if selected.sum() < max(settings.cmc_min_window_epochs, settings.cmc_polynomial_order + 2):
            continue
        local_time = uptime_s[selected] - uptime_s[selected].mean()
        fit = np.polynomial.polynomial.polyfit(local_time, cmc[selected], settings.cmc_polynomial_order)
        residual = cmc[selected] - np.polynomial.polynomial.polyval(local_time, fit)
        squares.append(residual**2)
    if not squares:
        return float("nan"), 0
    all_squares = np.concatenate(squares)
    return float(np.sqrt(all_squares.mean())), int(all_squares.size)


def run_metrics(
    run: tracking_io.TrackingRun, settings: MetricSettings = MetricSettings()
) -> dict[str, ChannelMetrics]:
    """The metrics of every satellite in one tracking run."""
    signal_type = SIGNALS[run.signal_type_id]
    pilot_name = ACQUISITION_POLICIES[run.signal_type_id].component_name
    # Component names and order are the same for every PRN, so any PRN answers.
    pilot_index = signal_type(signal_type.default_prns[0]).code_set.index_of(pilot_name)
    results: dict[str, ChannelMetrics] = {}
    for signal_id in run.signal_ids:
        outputs = run[signal_id].outputs
        valid = outputs.valid  # a slice: the epochs written
        uptime_ms = np.asarray(outputs.uptime_epoch_ms, dtype=float)[valid]
        if settings.use_subcarrier_correction:
            code_phase_ms = observables.refined_code_phase_ms(
                outputs, signal_type.tracking_code_rate_chips_per_sec
            )[valid]
        else:
            code_phase_ms = np.asarray(outputs.code_phase_ms, dtype=float)[valid]
        cmc, cmc_epochs = cmc_std_m(
            uptime_ms, code_phase_ms,
            np.asarray(outputs.carr_phase_cycles, dtype=float)[valid],
            signal_type.carrier_freq_hz, settings,
        )
        pll = np.asarray(outputs.pll_mode, dtype=bool)[valid]
        cn0_time_s = np.asarray(outputs.cn0_uptime_ms, dtype=float)[outputs.cn0_valid] * 1e-3
        cn0 = np.asarray(outputs.cn0_dbhz, dtype=float)[outputs.cn0_valid, pilot_index]
        keep = cn0_time_s >= (cn0_time_s[0] + settings.cn0_skip_s) if len(cn0_time_s) else cn0_time_s > 0
        results[signal_id] = ChannelMetrics(
            signal_id=signal_id,
            epochs=len(uptime_ms),
            pll_fraction=float(pll.mean()) if pll.size else float("nan"),
            lock_losses=count_lock_losses(pll),
            cn0_mean_dbhz=float(np.nanmean(cn0[keep])) if keep.any() else float("nan"),
            cmc_std_m=cmc,
            cmc_epochs=cmc_epochs,
        )
    return results


@dataclass(frozen=True)
class Comparison:
    """One satellite in two runs, and the change from the first to the second."""

    signal_id: str
    default: ChannelMetrics
    variant: ChannelMetrics

    @property
    def cn0_change_db(self) -> float:
        return self.variant.cn0_mean_dbhz - self.default.cn0_mean_dbhz

    @property
    def cmc_ratio(self) -> float:
        """Variant scatter over default scatter; below 1 means the variant is quieter."""
        return self.variant.cmc_std_m / self.default.cmc_std_m


def compare_runs(
    default_run: tracking_io.TrackingRun,
    variant_run: tracking_io.TrackingRun,
    settings: MetricSettings = MetricSettings(),
) -> list[Comparison]:
    """Pair the satellites that both runs tracked and compute their metrics."""
    if default_run.signal_type_id != variant_run.signal_type_id:
        raise ValueError(
            f"the runs track different signals: {default_run.signal_type_id} and "
            f"{variant_run.signal_type_id}"
        )
    first = run_metrics(default_run, settings)
    second = run_metrics(variant_run, settings)
    shared = sorted(set(first) & set(second))
    return [Comparison(sid, first[sid], second[sid]) for sid in shared]


def format_comparison(comparisons: list[Comparison], default_label: str, variant_label: str) -> str:
    """A fixed-width text table of a comparison, for the terminal and for reports."""
    header = (
        f"{'sat':<5} {'C/N0 ' + default_label:>12} {'C/N0 ' + variant_label:>12} {'dC/N0 dB':>9} "
        f"{'CMC ' + default_label + ' m':>12} {'CMC ' + variant_label + ' m':>12} "
        f"{'CMC ratio':>9} {'losses':>9}"
    )
    lines = [header]
    for item in comparisons:
        lines.append(
            f"{item.signal_id:<5} {item.default.cn0_mean_dbhz:>12.2f} {item.variant.cn0_mean_dbhz:>12.2f} "
            f"{item.cn0_change_db:>+9.2f} {item.default.cmc_std_m:>12.3f} {item.variant.cmc_std_m:>12.3f} "
            f"{item.cmc_ratio:>9.2f} {item.default.lock_losses:>4d}/{item.variant.lock_losses:<4d}"
        )
    return "\n".join(lines)
