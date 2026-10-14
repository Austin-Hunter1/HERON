"""
Compare broadcast BeiDou orbits with precise ones.

The B2a ephemeris decoded from a tracking run (`utils.nav.bcnav2.assemble_ephemeris`)
and the one in a RINEX 3 broadcast file (`utils.rinex3_nav`) are two descriptions of
one orbit, and an SP3 file from an analysis centre is a third, much more accurate
one.  Differences between them are the check on the decode, the units, and the orbit
model.  A broadcast ephemeris is good to a few metres near its reference time `toe`;
differences of tens of metres at `toe`, or a growth with distance from it that is far
faster than that, mean a wrong field or scale.

Time handling is the part that is easy to get wrong.  The ephemeris takes BDT time of
week.  SP3 files tabulate GPS time.  `to_gps_seconds` does that conversion once.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import precise_orbits, time_systems
from .nav.ephemeris import BdsEphemeris


@dataclass(frozen=True)
class OrbitComparison:
    """Position differences between a broadcast ephemeris and a reference, in metres."""

    offsets_s: np.ndarray
    """Times from `toe` at which the orbits were compared."""

    difference_m: np.ndarray
    """Length of the position difference at each offset (NaN where the reference had no data)."""

    @property
    def at_toe_m(self) -> float:
        """The difference at the offset closest to zero."""
        if not np.isfinite(self.difference_m).any():
            return float("nan")
        return float(self.difference_m[np.argmin(np.abs(self.offsets_s))])

    @property
    def rms_m(self) -> float:
        finite = self.difference_m[np.isfinite(self.difference_m)]
        return float(np.sqrt(np.mean(finite**2))) if len(finite) else float("nan")

    @property
    def max_m(self) -> float:
        finite = self.difference_m[np.isfinite(self.difference_m)]
        return float(finite.max()) if len(finite) else float("nan")


def to_gps_seconds(week_bdt: int, tow_bdt_s: float) -> float:
    """Full GPS seconds (week * 604800 + time of week) of a BDT week and time of week."""
    gps_week, gps_tow = time_systems.bdt_to_gpst(week_bdt, tow_bdt_s)
    return gps_week * time_systems.SECONDS_PER_WEEK + gps_tow


def _positions(eph: BdsEphemeris, offsets_s: np.ndarray) -> np.ndarray:
    return np.array(
        [eph.orbit_state((eph.toe + dt) % time_systems.SECONDS_PER_WEEK).position_ecef_m for dt in offsets_s]
    )


def compare_with_sp3(
    eph: BdsEphemeris, sp3, offsets_s: np.ndarray, *, sat_id: str | None = None
) -> OrbitComparison:
    """
    Broadcast against SP3, at `toe + offsets_s`.

    `sp3` has `epochs` (GPS seconds) and `position[sat_id]`, as `parse_sp3.SP3Arrays`.
    The reference is a degree-10 Lagrange interpolation (`precise_orbits`).
    """
    sat_id = sat_id or eph.sat_id
    offsets_s = np.asarray(offsets_s, dtype=float)
    times = np.array([to_gps_seconds(eph.week, eph.toe + dt) for dt in offsets_s])
    truth = precise_orbits.interpolate_position_m(sp3, sat_id, times)
    return OrbitComparison(offsets_s, np.linalg.norm(_positions(eph, offsets_s) - truth, axis=1))


def compare_ephemerides(
    first: BdsEphemeris, second: BdsEphemeris, offsets_s: np.ndarray
) -> OrbitComparison:
    """
    Two broadcast ephemerides of one satellite, compared at times measured from `first.toe`.

    The times are in the BDT week of `first`.  When the two have different weeks the
    second one is evaluated in its own week's seconds.
    """
    offsets_s = np.asarray(offsets_s, dtype=float)
    week_shift = (first.week - second.week) * time_systems.SECONDS_PER_WEEK
    a = _positions(first, offsets_s)
    b = np.array(
        [
            second.orbit_state((first.toe + dt + week_shift) % time_systems.SECONDS_PER_WEEK).position_ecef_m
            for dt in offsets_s
        ]
    )
    return OrbitComparison(offsets_s, np.linalg.norm(a - b, axis=1))


# ---------------------------------------------------------------------------
# Doppler from a broadcast orbit
#
# A check that needs no archive: the orbit gives the satellite's velocity, the
# velocity gives a Doppler, and tracking measured the Doppler.  After one offset that
# is common to every satellite (the receiver oscillator), they must agree.  It is
# sensitive to the satellite velocity and the orbit model.  It is weakly sensitive to
# the time of transmission: a shift common to all satellites is mostly absorbed by the
# common offset (a 14 s shift raises the residual from 0.35 Hz to 2.8 Hz in the
# 2026-10-14 recording; a 1 s shift is invisible).
# ---------------------------------------------------------------------------

SPEED_OF_LIGHT_M_S = 299_792_458.0

# WGS 84, for the receiver's coordinates only.
_WGS84_A_M = 6_378_137.0
_WGS84_F = 1.0 / 298.257222101


def geodetic_to_ecef(latitude_deg: float, longitude_deg: float, height_m: float) -> np.ndarray:
    """ECEF coordinates of a point given as WGS 84 latitude, longitude and height."""
    lat, lon = np.radians(latitude_deg), np.radians(longitude_deg)
    e2 = _WGS84_F * (2.0 - _WGS84_F)
    n = _WGS84_A_M / np.sqrt(1.0 - e2 * np.sin(lat) ** 2)
    return np.array(
        [
            (n + height_m) * np.cos(lat) * np.cos(lon),
            (n + height_m) * np.cos(lat) * np.sin(lon),
            (n * (1.0 - e2) + height_m) * np.sin(lat),
        ]
    )


def _rotate_about_z(angle_rad: float, vector: np.ndarray) -> np.ndarray:
    c, s = np.cos(angle_rad), np.sin(angle_rad)
    return np.array([c * vector[0] - s * vector[1], s * vector[0] + c * vector[1], vector[2]])


def _range_m(eph: BdsEphemeris, receiver_ecef_m: np.ndarray, t_transmit_bdt_s: float):
    """
    Geometric range, with the receiver turned through the signal's travel time.

    The ECEF frame turns while the signal travels, so the receiver at the time of
    reception is the ECEF receiver rotated back by the earth rotation of that time.
    Returns `(range, satellite_position)`.
    """
    from .nav.ephemeris import BDS_OMEGA_E_DOT

    satellite = eph.orbit_state(t_transmit_bdt_s % time_systems.SECONDS_PER_WEEK).position_ecef_m
    travel_s = np.linalg.norm(satellite - receiver_ecef_m) / SPEED_OF_LIGHT_M_S
    for _ in range(2):
        receiver = _rotate_about_z(BDS_OMEGA_E_DOT * travel_s, receiver_ecef_m)
        travel_s = np.linalg.norm(satellite - receiver) / SPEED_OF_LIGHT_M_S
    receiver = _rotate_about_z(BDS_OMEGA_E_DOT * travel_s, receiver_ecef_m)
    return float(np.linalg.norm(satellite - receiver)), satellite


def predicted_doppler_hz(
    eph: BdsEphemeris,
    receiver_ecef_m: np.ndarray,
    t_transmit_bdt_s: float,
    carrier_hz: float,
    *,
    half_step_s: float = 0.5,
) -> float:
    """
    The carrier Doppler a static receiver sees, from the broadcast orbit.

    Positive when the satellite approaches.  The range rate is a central difference in
    transmission time, which differs from one in reception time by a factor of
    ``1 - range_rate / c``, about 1e-5: far below what this check can resolve.
    """
    later, _ = _range_m(eph, receiver_ecef_m, t_transmit_bdt_s + half_step_s)
    earlier, _ = _range_m(eph, receiver_ecef_m, t_transmit_bdt_s - half_step_s)
    range_rate = (later - earlier) / (2.0 * half_step_s)
    return float(-range_rate * carrier_hz / SPEED_OF_LIGHT_M_S)


def elevation_deg(eph: BdsEphemeris, receiver_ecef_m: np.ndarray, t_transmit_bdt_s: float) -> float:
    """Elevation of the satellite above the receiver's horizontal plane, in degrees."""
    _, satellite = _range_m(eph, receiver_ecef_m, t_transmit_bdt_s)
    line_of_sight = satellite - receiver_ecef_m
    up = receiver_ecef_m / np.linalg.norm(receiver_ecef_m)  # geocentric up: fine to a few tenths of a degree
    return float(np.degrees(np.arcsin(np.dot(line_of_sight, up) / np.linalg.norm(line_of_sight))))
