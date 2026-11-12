"""
The specular point and the reflected path (plan section 6).

Rapid model, `image_plane`: the surface near the specular point is a plane
(point P0, unit normal n). Mirror the receiver in the plane:

    h  = (R - P0) . n
    R' = R - 2 h n
    rho_R = |T - R'|
    S  = R' + s (T - R'),   s = ((P0 - R') . n) / ((T - R') . n)

This is closed-form and exact for a plane. It ignores the Earth curvature,
which drops the surface by d^2 / (2 R_E) at the horizontal distance d = h / tan e
(0.10 m at h = 100 m, e = 5 deg).

High model, `iterative_surface`: the gradient solver of Gleason (2019), eq. 6-7,

    dS = (T - S)/|T - S| + (R - S)/|R - S|,   S <- S + K dS,

with the projection back onto the real surface (ellipsoid + geoid + water level)
done in geodetic coordinates, and the Snell residual of eq. 14 as the stop test.
It starts from the image solution, so one to three iterations are enough.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import frames
from .surface import Surface


@dataclass
class SpecularSolution:
    """One specular solution per epoch; all arrays have N rows."""

    specular_ecef: np.ndarray
    reflected_path_m: np.ndarray
    height_above_surface_m: np.ndarray
    elevation_rad: np.ndarray
    azimuth_rad: np.ndarray
    snell_residual_rad: np.ndarray
    normal_ecef: np.ndarray
    image_ecef: np.ndarray
    iterations: np.ndarray


def snell_residual(sat_ecef, rx_ecef, s_ecef, normal) -> np.ndarray:
    """|angle(R - S, n) - angle(T - S, n)| in rad (Gleason 2019 eq. 14, local normal)."""
    d_rs = frames.unit(rx_ecef - s_ecef)
    d_ts = frames.unit(sat_ecef - s_ecef)
    a_r = np.arccos(np.clip(np.sum(d_rs * normal, axis=-1), -1, 1))
    a_t = np.arccos(np.clip(np.sum(d_ts * normal, axis=-1), -1, 1))
    return np.abs(a_r - a_t)


def image_plane(sat_ecef, rx_ecef, surface: Surface) -> SpecularSolution:
    """
    Rapid solution. The plane goes through the surface point under the receiver
    and has the surface normal there. One extra pass moves the plane to the
    specular point found by the first pass, which removes most of the normal
    tilt over the distance d (the curvature drop stays).
    """
    sat = np.atleast_2d(np.asarray(sat_ecef, dtype=float))
    rx = np.atleast_2d(np.asarray(rx_ecef, dtype=float))
    lat, lon, _ = frames.ecef_to_geodetic(rx)
    for _ in range(2):
        h_surf = surface.height_ell_m(lat, lon)
        p0 = frames.geodetic_to_ecef(lat, lon, h_surf)
        n = surface.normal(lat, lon)
        h = np.sum((rx - p0) * n, axis=1)
        image = rx - 2.0 * h[:, None] * n
        s_frac = np.sum((p0 - image) * n, axis=1) / np.sum((sat - image) * n, axis=1)
        spec = image + s_frac[:, None] * (sat - image)
        lat, lon, _ = frames.ecef_to_geodetic(spec)
    path = np.linalg.norm(sat - image, axis=1)
    el, az = _incidence(sat, spec, n)
    return SpecularSolution(spec, path, h, el, az, snell_residual(sat, rx, spec, n), n,
                            image, np.ones(len(rx), dtype=int))


def _incidence(sat, spec, normal):
    """Elevation of the satellite above the local surface plane, and its azimuth."""
    d_ts = frames.unit(sat - spec)
    el = np.arcsin(np.clip(np.sum(d_ts * normal, axis=1), -1, 1))
    _, az = frames.elevation_azimuth(spec, sat)
    return el, az


def iterative_surface(
    sat_ecef,
    rx_ecef,
    surface: Surface,
    *,
    gain: float = 1.0,
    tolerance_rad: float = 1e-9,
    max_iterations: int = 50,
    start: SpecularSolution | None = None,
) -> SpecularSolution:
    """
    High-accuracy solution on the curved surface (Gleason 2019, with a geodetic
    projection). `gain` (K) scales the step. The step is K times the tangential
    gradient times leg / cos^2(e), which makes K dimensionless and makes K = 1 a
    Newton step for every geometry (Gleason tunes K by hand for LEO).
    """
    sat = np.atleast_2d(np.asarray(sat_ecef, dtype=float))
    rx = np.atleast_2d(np.asarray(rx_ecef, dtype=float))
    first = start if start is not None else image_plane(sat, rx, surface)
    s = first.specular_ecef.copy()
    iterations = np.zeros(len(rx), dtype=int)
    for _ in range(max_iterations):
        lat, lon, _ = frames.ecef_to_geodetic(s)
        n = surface.normal(lat, lon)
        resid = snell_residual(sat, rx, s, n)
        active = resid > tolerance_rad
        if not np.any(active):
            break
        d_t = frames.unit(sat - s)
        d_r = frames.unit(rx - s)
        grad = d_t + d_r
        grad_tan = grad - np.sum(grad * n, axis=1)[:, None] * n
        leg = np.linalg.norm(rx - s, axis=1)
        # Near the solution the tangential gradient is about x cos^2(e) / leg for a
        # displacement x, so this step is a Newton step when gain = 1.
        cos2 = np.clip(1.0 - np.sum(d_t * n, axis=1) ** 2, 1e-3, 1.0)
        step = gain * grad_tan * (leg / cos2)[:, None]
        s_new = s + step
        lat2, lon2, _ = frames.ecef_to_geodetic(s_new)
        s_new = frames.geodetic_to_ecef(lat2, lon2, surface.height_ell_m(lat2, lon2))
        s = np.where(active[:, None], s_new, s)
        iterations += active
    lat, lon, _ = frames.ecef_to_geodetic(s)
    n = surface.normal(lat, lon)
    path = np.linalg.norm(sat - s, axis=1) + np.linalg.norm(rx - s, axis=1)
    h = np.sum((rx - s) * n, axis=1)
    image = rx - 2.0 * h[:, None] * n
    el, az = _incidence(sat, s, n)
    return SpecularSolution(s, path, h, el, az, snell_residual(sat, rx, s, n), n, image,
                            iterations)


SOLVERS = {"image_plane": image_plane, "iterative_curved": iterative_surface}


def fresnel_axes(wavelength_m, height_m, elevation_rad) -> tuple[np.ndarray, np.ndarray]:
    """
    First Fresnel zone semi-axes (minor b, major a), metres, for a low receiver:

        b = sqrt(lambda h / sin e),   a = b / sin e
    """
    sin_e = np.sin(np.asarray(elevation_rad, dtype=float))
    b = np.sqrt(np.asarray(wavelength_m) * np.asarray(height_m) / sin_e)
    return b, b / sin_e


def rayleigh_coherent(surface_rms_m, wavelength_m, elevation_rad) -> np.ndarray:
    """True where the Rayleigh criterion sigma < lambda / (8 sin e) holds."""
    return np.asarray(surface_rms_m) < np.asarray(wavelength_m) / (
        8.0 * np.sin(np.asarray(elevation_rad))
    )
