"""
The direct signal in the nadir antenna: estimate and flags only (D-098, plan 9.3).

The direct signal leaks into the nadir antenna through its back lobe. At UAS
heights it sits less than one L1 C/A chip from the reflection, so it adds to the
reflection waveform. Model of one nadir correlation:

    Z_N,k = beta Z_Z,k + a_k exp(j dphi_k) + w_k

Z_Z,k is the ZENITH channel correlated with the SAME reflected replica (same
blocks, same lag). Because the clocks are common, beta is a slow complex gain.
Over a window W in which the reflection phase turns many times relative to
Z_Z (the interferometric frequency (f_R - f_D) is not zero), least squares gives

    beta^ = sum Z_N conj(Z_Z) / sum |Z_Z|^2

The phase of beta^ follows the B200-to-B210 LO phase, so its plot is the check
of D-097. The subtraction Z_N - beta^ Z_Z is later work (D-098); this module
estimates and flags only.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class LeakageEstimate:
    """One beta per window, plus the flags."""

    uptime_ms: np.ndarray
    beta: np.ndarray                 # complex, per window
    turns_in_window: np.ndarray      # interferometric phase turns in each window
    diversity: np.ndarray            # |mean exp(j 2 pi D / lambda)|: 0 is best, 1 is worst
    separable: np.ndarray            # bool: enough turns and low diversity value
    leak_to_reflection_power: np.ndarray
    strong: np.ndarray               # bool: leakage power above the config fraction


def estimate_beta(z_nadir: np.ndarray, z_zenith: np.ndarray, uptime_ms: np.ndarray,
                  excess_m: np.ndarray, wavelength_m: float, *, window: int,
                  min_turns: float = 2.0, max_diversity: float = 0.3,
                  strong_fraction: float = 0.1) -> LeakageEstimate:
    """
    Estimate beta per window of `window` coherent blocks at one lag.

    `excess_m` is the model excess path at each block: the interferometric phase
    turns in a window are sum |dD| / lambda (the total, not the net, change, so a
    drone that moves up and down still counts). With fewer than
    `min_turns` turns, beta and the reflection cannot be separated; that window
    is flagged and not trusted.
    """
    n = (len(z_nadir) // window) * window
    zn = z_nadir[:n].reshape(-1, window)
    zz = z_zenith[:n].reshape(-1, window)
    ex = excess_m[:n].reshape(-1, window)
    beta = np.sum(zn * np.conj(zz), axis=1) / np.maximum(np.sum(np.abs(zz) ** 2, axis=1), 1e-30)
    turns = np.sum(np.abs(np.diff(ex, axis=1)), axis=1) / wavelength_m
    # The reflection term enters beta^ through the mean of its phase relative to
    # Z_Z, which is exp(-j 2 pi D / lambda). Its magnitude bounds the bias:
    #   |beta^ - beta| <~ (A_reflection / A_zenith) * diversity.
    # A drone that moves up and down spends time at the turning points, so this
    # decays more slowly than 1 / turns (stationary phase); the turn count alone
    # is not enough.
    diversity = np.abs(np.mean(np.exp(2j * np.pi * ex / wavelength_m), axis=1))
    leak = np.abs(beta) ** 2 * np.mean(np.abs(zz) ** 2, axis=1)
    resid = np.mean(np.abs(zn - beta[:, None] * zz) ** 2, axis=1)
    ratio = leak / np.maximum(resid, 1e-30)
    separable = (turns >= min_turns) & (diversity < max_diversity)
    return LeakageEstimate(uptime_ms[:n].reshape(-1, window)[:, 0], beta, turns, diversity,
                           separable, ratio, ratio > strong_fraction)
