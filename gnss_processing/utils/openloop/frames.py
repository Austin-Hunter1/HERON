"""
Coordinate frames for open-loop reflection tracking.

All functions are vectorised over the leading axis. Angles are radians unless the
name says `_deg`. WGS84 is the ellipsoid. The plan (section 2) gives the frame
conventions:

- ECEF: WGS84 Earth-centred, Earth-fixed.
- NED: local north, east, down at a geodetic point.
- Body: FRD (forward, right, down) of the drone. Euler order ZYX (yaw, pitch,
  roll), which is the ArduPilot convention (D-089).

Why a new module: `utils.navigation` has scalar `geodetic_to_ecef` and
`ecef_to_geodetic`, and `gnss_tools` orders geodetic as (lon, lat, alt). The OL
model needs arrays, rotation matrices and a stated order, so this module keeps
one clear (lat, lon, h) order everywhere.
"""

from __future__ import annotations

import numpy as np

WGS84_A = 6378137.0
WGS84_F = 1.0 / 298.257223563
WGS84_E2 = WGS84_F * (2.0 - WGS84_F)
OMEGA_EARTH = 7.2921151467e-5  # rad/s, IS-GPS-200
SPEED_OF_LIGHT = 299792458.0


def geodetic_to_ecef(lat_rad, lon_rad, h_m) -> np.ndarray:
    """ECEF position (..., 3) from geodetic latitude, longitude and ellipsoidal height."""
    lat = np.asarray(lat_rad, dtype=float)
    lon = np.asarray(lon_rad, dtype=float)
    h = np.asarray(h_m, dtype=float)
    sin_lat = np.sin(lat)
    n = WGS84_A / np.sqrt(1.0 - WGS84_E2 * sin_lat**2)
    x = (n + h) * np.cos(lat) * np.cos(lon)
    y = (n + h) * np.cos(lat) * np.sin(lon)
    z = (n * (1.0 - WGS84_E2) + h) * sin_lat
    return np.stack([x, y, z], axis=-1)


def ecef_to_geodetic(xyz) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Geodetic (lat_rad, lon_rad, h_m) from ECEF (..., 3).

    Uses a fixed-point iteration on latitude. Five iterations give better than
    0.1 mm at heights below 1000 km, which is far more than this use needs.
    """
    xyz = np.asarray(xyz, dtype=float)
    x, y, z = xyz[..., 0], xyz[..., 1], xyz[..., 2]
    lon = np.arctan2(y, x)
    p = np.hypot(x, y)
    lat = np.arctan2(z, p * (1.0 - WGS84_E2))
    h = np.zeros_like(p)
    for _ in range(6):
        sin_lat = np.sin(lat)
        n = WGS84_A / np.sqrt(1.0 - WGS84_E2 * sin_lat**2)
        h = p / np.cos(lat) - n
        lat = np.arctan2(z, p * (1.0 - WGS84_E2 * n / (n + h)))
    return lat, lon, h


def ellipsoid_normal(lat_rad, lon_rad) -> np.ndarray:
    """Unit outward ellipsoid normal (..., 3) in ECEF at geodetic lat, lon."""
    lat = np.asarray(lat_rad, dtype=float)
    lon = np.asarray(lon_rad, dtype=float)
    return np.stack(
        [np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)], axis=-1
    )


def rot_ecef_to_ned(lat_rad, lon_rad) -> np.ndarray:
    """C_e^n (..., 3, 3): rotates an ECEF vector into local NED."""
    lat = np.asarray(lat_rad, dtype=float)
    lon = np.asarray(lon_rad, dtype=float)
    sl, cl = np.sin(lat), np.cos(lat)
    so, co = np.sin(lon), np.cos(lon)
    zero = np.zeros_like(sl)
    rows = [
        np.stack([-sl * co, -sl * so, cl], axis=-1),
        np.stack([-so, co, zero], axis=-1),
        np.stack([-cl * co, -cl * so, -sl], axis=-1),
    ]
    return np.stack(rows, axis=-2)


def rot_body_to_ned(roll_rad, pitch_rad, yaw_rad) -> np.ndarray:
    """C_b^n = Rz(yaw) Ry(pitch) Rx(roll), (..., 3, 3). Body frame FRD, ZYX order."""
    r = np.asarray(roll_rad, dtype=float)
    p = np.asarray(pitch_rad, dtype=float)
    y = np.asarray(yaw_rad, dtype=float)
    cr, sr = np.cos(r), np.sin(r)
    cp, sp = np.cos(p), np.sin(p)
    cy, sy = np.cos(y), np.sin(y)
    rows = [
        np.stack([cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr], axis=-1),
        np.stack([sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr], axis=-1),
        np.stack([-sp, cp * sr, cp * cr], axis=-1),
    ]
    return np.stack(rows, axis=-2)


def elevation_azimuth(from_ecef, to_ecef) -> tuple[np.ndarray, np.ndarray]:
    """Elevation and azimuth (rad) of `to_ecef` seen from `from_ecef`, ellipsoid horizon."""
    frm = np.asarray(from_ecef, dtype=float)
    to = np.asarray(to_ecef, dtype=float)
    lat, lon, _ = ecef_to_geodetic(frm)
    ned = np.einsum("...ij,...j->...i", rot_ecef_to_ned(lat, lon), to - frm)
    horizontal = np.hypot(ned[..., 0], ned[..., 1])
    elevation = np.arctan2(-ned[..., 2], horizontal)
    azimuth = np.mod(np.arctan2(ned[..., 1], ned[..., 0]), 2 * np.pi)
    return elevation, azimuth


def unit(v) -> np.ndarray:
    """Unit vectors along the last axis."""
    v = np.asarray(v, dtype=float)
    return v / np.linalg.norm(v, axis=-1, keepdims=True)


def radii_of_curvature(lat_rad) -> tuple[np.ndarray, np.ndarray]:
    """Meridian radius M and prime-vertical radius N (m) at geodetic latitude."""
    s2 = np.sin(np.asarray(lat_rad, dtype=float)) ** 2
    w = np.sqrt(1.0 - WGS84_E2 * s2)
    m = WGS84_A * (1.0 - WGS84_E2) / w**3
    n = WGS84_A / w
    return m, n
