"""
Acquiring the reflection: a small delay-Doppler map around the model (plan 9).

This step does not search all code phases as notebook 01 does. It checks that
the reflection is where the model says, and measures what the model does not
know (the hardware delay tau_hw, plan 9.4).

    Y(tau_j, f_m) = (1/N_i) sum_k |Z_k(tau_j, f_m)|^2          (incoherent sum)

Noise floor: the mean of Y over the noise lags (far from the reflection and the
direct leakage). Under noise only, N_i Y / sigma_N^2 follows Gamma(N_i, 1), so
for a total false-alarm probability p_fa over J lags and M bins:

    gamma = (sigma_N^2 / N_i) F^-1_Gamma(N_i,1)(1 - p_fa / (J M))

The Gamma law needs independent cells; lags closer than one chip are correlated,
so the J M count makes the threshold a little conservative. The same idea is in
`utils.bpsk_acquisition` (D-085); this module uses the formula directly because
that code works on its own FFT grid, not on given lags.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import gamma

from .correlator import OpenLoopOutput


@dataclass
class DdmResult:
    """The small DDM and the detection decision."""

    power: np.ndarray            # (M, J) incoherent mean |Z|^2
    lags_chips: np.ndarray
    doppler_offsets_hz: np.ndarray
    noise_power: float
    threshold: float
    peak_lag_chips: float        # refined between the lags by `peak_method`
    peak_doppler_hz: float
    snr_db: float
    detected: bool
    num_incoherent: int


def coherent_sum(z: np.ndarray, n: int) -> np.ndarray:
    """Sum `n` consecutive blocks along axis 0 (the tail that does not fill a group is dropped)."""
    k = (z.shape[0] // n) * n
    return z[:k].reshape((k // n, n) + z.shape[1:]).sum(axis=1)


def noise_lag_mask(lags: np.ndarray, noise_chips: tuple[float, float]) -> np.ndarray:
    """True for the lags inside the configured noise window."""
    return (lags >= noise_chips[0]) & (lags <= noise_chips[1])


PEAK_METHODS = {"parabola", "early_late"}

# Two lag steps are "equal" when they differ by less than this fraction.
_STEP_RTOL = 1e-6


def _check_increasing(lags: np.ndarray) -> np.ndarray:
    """The lags as floats; raise when they do not increase strictly (np.interp needs that)."""
    lags = np.asarray(lags, dtype=float)
    if len(lags) > 1 and not np.all(np.diff(lags) > 0):
        raise ValueError("the lags must increase strictly; sort them before the peak fit")
    return lags


def _uniform(steps: np.ndarray) -> bool:
    """True when all the steps are equal (within _STEP_RTOL)."""
    return bool(np.all(np.abs(steps - steps[0]) <= _STEP_RTOL * abs(steps[0])))


def parabola_peak_lag(lags: np.ndarray, power: np.ndarray, peak_index: int) -> float:
    """
    The peak lag from a parabola through the top three lags of the POWER.

    It looks only at the tip, so a second path near the peak moves it less than
    `early_late_peak_lag` does. For a sharp triangle (L1 C/A) it is biased by an
    amount that depends on where the peak sits between two lags: up to about
    0.1 of the lag step (0.026 chip at 0.25, 0.004 chip at 0.05). Keep the lag
    step fine when this method is used. At the edge of the lags, or when the
    three values do not curve down, the grid lag is returned unchanged.

    The step is the LOCAL step around the peak. The lags may hold more than one
    grid (for example the noise lags at 1 chip and the search lags at 0.1 chip in
    one array); a step taken from the first two lags was then wrong by the ratio
    of the two grids (seen on the 2026-11-10 ARGO data). When the two steps beside
    the peak differ, the peak sits where two grids meet, and the grid lag is
    returned unchanged, as at an edge.
    """
    lags = _check_increasing(lags)
    lag = float(lags[peak_index])
    if 0 < peak_index < len(lags) - 1:
        steps = np.diff(lags[peak_index - 1:peak_index + 2])
        if not _uniform(steps):
            return lag
        y0, y1, y2 = power[peak_index - 1], power[peak_index], power[peak_index + 1]
        den = y0 - 2 * y1 + y2
        if den < 0:
            lag += 0.5 * (y0 - y2) / den * float(steps[0])
    return lag


def early_late_peak_lag(lags: np.ndarray, amplitude: np.ndarray, peak_index: int,
                        half_spacing_chips: float) -> float:
    """
    The peak lag of a symmetric waveform, between the grid lags.

    Finds x where the early and the late values are equal:

        A(x - d) = A(x + d),     d = half_spacing_chips

    with A linearly interpolated between the grid lags. For any symmetric peak
    this is exact when x - d and x + d fall on the straight slopes, so the shape
    of the tip does not matter: sharp (L1 C/A at a wide bandwidth) or rounded
    (L5 through the front-end filter). A parabola through the three top samples
    is NOT exact for a triangle: on a 0.25-chip grid it moved an L1 C/A peak by
    up to 0.026 chip (26 ns) in the x2 data check of 2026-11-12.

    Give the AMPLITUDE (|Z|, or the square root of a power), not the power: the
    slopes of the power are curved. A constant noise floor does not move x.

    The search is within one grid step of `peak_index`. When x - d or x + d is
    outside the lags, or there is no sign change (noise), the grid lag is
    returned unchanged.

    The step is the LOCAL step around the peak (see `parabola_peak_lag`). The
    lags that the interpolation uses, from lo - d to hi + d, must lie on one
    uniform grid; when they do not, the grid lag is returned unchanged.
    """
    lags = _check_increasing(lags)
    a = np.asarray(amplitude, dtype=float)
    if not 0 < peak_index < len(lags) - 1:
        return float(lags[peak_index])
    step = float(lags[peak_index + 1] - lags[peak_index])
    lo, hi = lags[peak_index] - step, lags[peak_index] + step
    if lo - half_spacing_chips < lags[0] or hi + half_spacing_chips > lags[-1]:
        return float(lags[peak_index])
    # The interpolation brackets lo - d and hi + d by their neighbour lags, so
    # those neighbours must be on the same uniform grid as well.
    first = max(int(np.searchsorted(lags, lo - half_spacing_chips, side="right")) - 1, 0)
    last = min(int(np.searchsorted(lags, hi + half_spacing_chips, side="left")), len(lags) - 1)
    if not _uniform(np.diff(lags[first:last + 1])):
        return float(lags[peak_index])

    def g(x):
        return np.interp(x - half_spacing_chips, lags, a) - np.interp(x + half_spacing_chips, lags, a)

    g_lo, g_hi = g(lo), g(hi)
    if not g_lo < 0 < g_hi:
        return float(lags[peak_index])
    for _ in range(50):  # bisection; g is piecewise linear and rises through the peak
        mid = 0.5 * (lo + hi)
        if g(mid) < 0:
            lo = mid
        else:
            hi = mid
    return float(0.5 * (lo + hi))


def delay_doppler_map(
    ol: OpenLoopOutput,
    *,
    wiped: np.ndarray,
    noise_chips: tuple[float, float],
    coherent_blocks: int = 1,
    p_fa_total: float = 1e-3,
    search_chips: tuple[float, float] | None = None,
    peak_method: str = "parabola",
    peak_half_spacing_chips: float = 0.5,
) -> DdmResult:
    """
    Detect the reflection in the wiped waveforms `wiped` (K, M, J) of one component.

    `coherent_blocks` sums that many blocks coherently before |.|^2 (plan 9.2).
    `search_chips` limits the peak search (default: all lags outside the noise
    window).

    `peak_method` (`acquisition.peak_method` in the config, D-103) refines the
    peak lag between the grid lags:

    - "parabola": through the top three lags of the power. Looks only at the tip,
      so a second path near the peak (the direct leakage, C-9) moves it less.
      Biased for a sharp peak on a coarse grid (L1 C/A: up to 0.004 chip on a
      0.05-chip grid, 0.026 chip on a 0.25-chip grid).
    - "early_late": `early_late_peak_lag` with `peak_half_spacing_chips`. Exact
      for any symmetric peak, but a second path near the peak makes the slopes
      unequal and pulls it (up to 0.05 chip for leakage at 0.3 amplitude and
      0.117 chip, the synthetic notebook case).
    """
    if peak_method not in PEAK_METHODS:
        raise ValueError(f"peak_method {peak_method!r}; allowed: {sorted(PEAK_METHODS)}")
    zc = coherent_sum(wiped, coherent_blocks)
    power = np.mean(np.abs(zc) ** 2, axis=0)  # (M, J)
    n_i = zc.shape[0]
    lags = ol.lags_chips
    noise_mask = noise_lag_mask(lags, noise_chips)
    if not np.any(noise_mask):
        raise ValueError("no lag inside the noise window; widen openloop.lags_chips")
    noise = float(np.mean(power[:, noise_mask]))
    search = ~noise_mask if search_chips is None else (lags >= search_chips[0]) & (
        lags <= search_chips[1])
    cells = int(search.sum()) * power.shape[0]
    thr = noise / n_i * float(gamma.ppf(1.0 - p_fa_total / max(cells, 1), a=n_i))
    sub = np.where(search[None, :], power, -np.inf)
    m, j = np.unravel_index(int(np.argmax(sub)), sub.shape)
    peak = power[m, j]
    if peak_method == "early_late":
        lag = early_late_peak_lag(lags, np.sqrt(np.maximum(power[m], 0.0)), int(j),
                                  peak_half_spacing_chips)
    else:
        lag = parabola_peak_lag(lags, power[m], int(j))
    snr = 10 * np.log10(max(peak - noise, 1e-30) / noise)
    return DdmResult(power, lags, ol.doppler_offsets_hz, noise, thr, lag,
                     float(ol.doppler_offsets_hz[m]), float(snr), bool(peak > thr), n_i)


def estimate_tau_hw_s(peak_lags_chips, code_rate_chips_per_sec: float,
                      model_bias_chips=None) -> tuple[float, float]:
    """
    Hardware delay from the DDM peaks of several satellites (plan 9.4).

    tau_hw = mean(peak lag - model bias) / R_c. The spread across satellites is
    returned too: a large spread means a geometry error, not a hardware delay.
    """
    lags = np.asarray(peak_lags_chips, dtype=float)
    if model_bias_chips is not None:
        lags = lags - np.asarray(model_bias_chips, dtype=float)
    return float(np.mean(lags) / code_rate_chips_per_sec), float(
        np.std(lags) / code_rate_chips_per_sec)
