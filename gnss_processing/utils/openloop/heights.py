"""
Height above the surface from the excess path (plan section 11.1).

Carrier observable, per satellite and continuous arc:

    D^ - D~ = 2 dh sin e + b

dh is the correction to the a-priori height (the same for every satellite),
b one constant per arc (the carrier ambiguity and the hardware phase). This is
the time-differenced method of Bai et al. (2012, eq. 13) written as one least-
squares problem. One arc alone needs the elevation to change, or b and dh are
not separable; the condition number is returned so the notebook can say so.

Code observable (absolute, noisy):

    D_code = D~ + (lag_peak - tau_hw R_c) * l_c
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class HeightFit:
    """Least-squares result."""

    dh_m: float
    dh_rate_mps: float
    biases_m: np.ndarray
    residual_rms_m: float
    sigma_dh_m: float
    condition: float


def fit_height(arcs: list[dict], *, fit_rate: bool = False) -> HeightFit:
    """
    `arcs`: list of dicts with `t_s`, `residual_m` (D^ - D~) and `elevation_rad`.

    Unknowns: dh (and its rate if `fit_rate`), plus one bias per arc.
    """
    rows, obs = [], []
    n_arcs = len(arcs)
    n_par = (2 if fit_rate else 1) + n_arcs
    t_ref = np.mean(np.concatenate([a["t_s"] for a in arcs]))
    for j, arc in enumerate(arcs):
        s = 2 * np.sin(np.asarray(arc["elevation_rad"]))
        for i in range(len(arc["t_s"])):
            row = np.zeros(n_par)
            row[0] = s[i]
            if fit_rate:
                row[1] = s[i] * (arc["t_s"][i] - t_ref)
            row[(2 if fit_rate else 1) + j] = 1.0
            rows.append(row)
            obs.append(arc["residual_m"][i])
    a = np.array(rows)
    y = np.array(obs)
    x, *_ = np.linalg.lstsq(a, y, rcond=None)
    res = y - a @ x
    dof = max(len(y) - n_par, 1)
    sigma2 = float(res @ res / dof)
    cov = sigma2 * np.linalg.pinv(a.T @ a)
    return HeightFit(float(x[0]), float(x[1]) if fit_rate else 0.0,
                     x[(2 if fit_rate else 1):], float(np.sqrt(np.mean(res**2))),
                     float(np.sqrt(cov[0, 0])), float(np.linalg.cond(a)))


def code_excess_m(model_excess_m, peak_lag_chips, tau_hw_s: float,
                  code_rate_chips_per_sec: float) -> np.ndarray:
    """Absolute excess path from the retracked code lag (plan 11.1 item 1)."""
    chip_m = 299792458.0 / code_rate_chips_per_sec
    return np.asarray(model_excess_m) + (
        np.asarray(peak_lag_chips) - tau_hw_s * code_rate_chips_per_sec) * chip_m


def water_level_m(antenna_h_ell_m, height_above_surface_m, geoid_n_m) -> np.ndarray:
    """H_orth = h_ell(antenna) - h - N (plan 11.1 item 4)."""
    return np.asarray(antenna_h_ell_m) - np.asarray(height_above_surface_m) - np.asarray(geoid_n_m)
