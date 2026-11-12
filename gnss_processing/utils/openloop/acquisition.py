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
    peak_lag_chips: float        # refined by a parabola
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


def delay_doppler_map(
    ol: OpenLoopOutput,
    *,
    wiped: np.ndarray,
    noise_chips: tuple[float, float],
    coherent_blocks: int = 1,
    p_fa_total: float = 1e-3,
    search_chips: tuple[float, float] | None = None,
) -> DdmResult:
    """
    Detect the reflection in the wiped waveforms `wiped` (K, M, J) of one component.

    `coherent_blocks` sums that many blocks coherently before |.|^2 (plan 9.2).
    `search_chips` limits the peak search (default: all lags outside the noise
    window).
    """
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
    lag = float(lags[j])
    if 0 < j < len(lags) - 1:
        y0, y1, y2 = power[m, j - 1], power[m, j], power[m, j + 1]
        den = y0 - 2 * y1 + y2
        if den < 0:
            lag += 0.5 * (y0 - y2) / den * (lags[1] - lags[0])
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
