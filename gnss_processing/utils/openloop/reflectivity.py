"""
Fresnel reflection coefficients and the coherent link budget (plan 9.1).

    R_vv = (eps sin e - sqrt(eps - cos^2 e)) / (eps sin e + sqrt(eps - cos^2 e))
    R_hh = (sin e - sqrt(eps - cos^2 e)) / (sin e + sqrt(eps - cos^2 e))
    R_RR = (R_vv + R_hh) / 2   (co-polar: RHCP in, RHCP out)
    R_RL = (R_vv - R_hh) / 2   (cross-polar: RHCP in, LHCP out)

Coherent power (Ruf 2026 eq. 2 with a roughness loss):

    P = P_T G_T lambda^2 G_R / ((4 pi)^2 (r_TS + r_SR)^2) |R|^2 exp(-(2 k sigma sin e)^2)

`eps` is the complex relative permittivity with the e^{jwt} convention
(eps = eps' - j eps''); fresh water at L-band is about 80 - 5j (config value).
"""

from __future__ import annotations

import numpy as np

BOLTZMANN_DBW = -228.6  # dBW/K/Hz


def fresnel_linear(eps: complex, elevation_rad) -> tuple[np.ndarray, np.ndarray]:
    """R_vv and R_hh at grazing elevation `elevation_rad`."""
    e = np.asarray(elevation_rad, dtype=float)
    s, c2 = np.sin(e), np.cos(e) ** 2
    root = np.sqrt(eps - c2 + 0j)
    r_vv = (eps * s - root) / (eps * s + root)
    r_hh = (s - root) / (s + root)
    return r_vv, r_hh


def fresnel_circular(eps: complex, elevation_rad) -> tuple[np.ndarray, np.ndarray]:
    """R_RR (co-polar) and R_RL (cross-polar, LHCP) at elevation `elevation_rad`."""
    r_vv, r_hh = fresnel_linear(eps, elevation_rad)
    return 0.5 * (r_vv + r_hh), 0.5 * (r_vv - r_hh)


def brewster_elevation_rad(eps: complex) -> float:
    """Elevation where |R_vv| is smallest: the LHCP/RHCP cross-over."""
    e = np.linspace(np.deg2rad(0.5), np.deg2rad(45), 4000)
    r_vv, _ = fresnel_linear(eps, e)
    return float(e[np.argmin(np.abs(r_vv))])


def roughness_loss(surface_rms_m, wavelength_m, elevation_rad) -> np.ndarray:
    """exp(-(2 k sigma sin e)^2): the coherent power left after surface roughness."""
    k = 2 * np.pi / np.asarray(wavelength_m)
    return np.exp(-(2 * k * np.asarray(surface_rms_m) * np.sin(np.asarray(elevation_rad))) ** 2)


def coherent_cn0_dbhz(*, eirp_dbw: float, wavelength_m: float, rx_gain_dbi,
                      path_m, reflectivity, roughness, system_temp_k: float) -> np.ndarray:
    """
    Expected C/N0 (dB-Hz) of the coherent reflection.

    C = EIRP + G_R + 20 log10(lambda / (4 pi (r_TS + r_SR))) + 10 log10(|R|^2 L_rough)
    N0 = k T_sys
    """
    path_loss = 20 * np.log10(wavelength_m / (4 * np.pi * np.asarray(path_m)))
    c = (eirp_dbw + np.asarray(rx_gain_dbi) + path_loss
         + 10 * np.log10(np.abs(reflectivity) ** 2 * roughness + 1e-30))
    return c - (BOLTZMANN_DBW + 10 * np.log10(system_temp_k))
