"""
Satellite position, velocity and clock behind one interface (plan section 4).

Two providers:

- `BroadcastProvider` (rapid): the decoded or downloaded broadcast ephemeris
  objects of `utils.nav.ephemeris` (GPS LNAV/CNAV, Galileo, BeiDou).
- `PreciseProvider` (high): SP3 orbits and clocks through `utils.precise_orbits`.

Plus `TrackProvider`, a provider made from callables, for the synthetic world.

`transmit_state` solves the light time and the Earth rotation for a path that
ends at a point X (the zenith antenna for the direct path, the specular point for
the reflected path):

    tau_(i+1) = |R3(omega_E tau_i) T(t_r - tau_i) - X| / c

Plan section 4 shows why the rapid provider is enough for the reflection model at
UAS heights: a 1 m orbit error changes the excess path by about 1e-5 m.

Reference frames (D-092 TODO): broadcast GPS orbits are in WGS84, Galileo in
GTRF, BeiDou in BDCS (CGCS2000). All agree with ITRF to a few cm. The hook for a
frame transformation is `frame_offset_m`; it is zero now. See
`docs/NB03_CONCERNS.md`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

import numpy as np

from . import frames

C = frames.SPEED_OF_LIGHT
WEEK_S = 604800.0


class SatelliteStateProvider(Protocol):
    """Anything that gives ECEF position (m) and clock (s) at a transmit time."""

    def position_ecef(self, sat_id: str, t_transmit_gpst_s: np.ndarray) -> np.ndarray: ...

    def clock_s(self, sat_id: str, t_transmit_gpst_s: np.ndarray) -> np.ndarray: ...


def _rot3(angle_rad: np.ndarray) -> np.ndarray:
    """Rotation about Z by `angle` (N, 3, 3): the Earth-rotation (Sagnac) correction."""
    c, s = np.cos(angle_rad), np.sin(angle_rad)
    z, o = np.zeros_like(c), np.ones_like(c)
    return np.stack([np.stack([c, s, z], -1), np.stack([-s, c, z], -1),
                     np.stack([z, z, o], -1)], -2)


def transmit_state(
    provider: SatelliteStateProvider,
    sat_id: str,
    t_receive_gpst_s,
    end_point_ecef,
    *,
    iterations: int = 3,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Satellite position (N, 3), in the ECEF frame of the receive time, and the
    transit time tau (N,), for a path that ends at `end_point_ecef` (N, 3).
    """
    t = np.atleast_1d(np.asarray(t_receive_gpst_s, dtype=float))
    x = np.atleast_2d(np.asarray(end_point_ecef, dtype=float))
    tau = np.full(len(t), 0.075)
    pos = None
    for _ in range(iterations):
        sat = provider.position_ecef(sat_id, t - tau)
        pos = np.einsum("nij,nj->ni", _rot3(frames.OMEGA_EARTH * tau), sat)
        tau = np.linalg.norm(pos - x, axis=1) / C
    return pos, tau


def velocity_ecef(provider: SatelliteStateProvider, sat_id: str, t_gpst_s,
                  step_s: float = 0.5) -> np.ndarray:
    """
    Satellite velocity (N, 3) by a central difference of the position.

    The ECEF velocity includes the Earth rotation, because the positions are in
    the rotating frame. A 0.5 s step gives an error below 1 mm/s for a MEO orbit.
    """
    t = np.atleast_1d(np.asarray(t_gpst_s, dtype=float))
    return (provider.position_ecef(sat_id, t + step_s)
            - provider.position_ecef(sat_id, t - step_s)) / (2 * step_s)


@dataclass
class TrackProvider:
    """A provider from callables: `position(t)` (N, 3) and optional `clock(t)`."""

    position: Callable[[np.ndarray], np.ndarray]
    clock: Callable[[np.ndarray], np.ndarray] | None = None
    t0_gpst_s: float = 0.0

    def position_ecef(self, sat_id: str, t_transmit_gpst_s) -> np.ndarray:
        return self.position(np.atleast_1d(t_transmit_gpst_s) - self.t0_gpst_s)

    def clock_s(self, sat_id: str, t_transmit_gpst_s) -> np.ndarray:
        t = np.atleast_1d(np.asarray(t_transmit_gpst_s, dtype=float))
        return np.zeros_like(t) if self.clock is None else self.clock(t - self.t0_gpst_s)


@dataclass
class BroadcastProvider:
    """
    Rapid model: broadcast ephemerides from `utils.nav.ephemeris`.

    `ephemerides` maps a satellite id ("G05", "C27", "E11") to an ephemeris
    object with `orbit_state(t)` and `clock_correction_s(t)`. Those take time of
    week in the system's own time (GPST, or BDT for BeiDou, D-047), so this
    class converts. `frame_offset_m` is the hook for the GTRF/BDCS to ITRF
    transformation (D-092 TODO); zero now.
    """

    ephemerides: dict
    frame_offset_m: dict[str, np.ndarray] = field(default_factory=dict)

    def _system_tow(self, sat_id: str, t_gpst_s: float) -> float:
        tow = float(np.mod(t_gpst_s, WEEK_S))
        if sat_id.startswith("C"):
            tow = float(np.mod(tow - 14.0, WEEK_S))  # BDT = GPST - 14 s
        return tow

    def position_ecef(self, sat_id: str, t_transmit_gpst_s) -> np.ndarray:
        eph = self.ephemerides[sat_id]
        t = np.atleast_1d(np.asarray(t_transmit_gpst_s, dtype=float))
        pos = np.array([eph.orbit_state(self._system_tow(sat_id, ti)).position_ecef_m
                        for ti in t])
        return pos + self.frame_offset_m.get(sat_id[0], 0.0)

    def clock_s(self, sat_id: str, t_transmit_gpst_s) -> np.ndarray:
        eph = self.ephemerides[sat_id]
        t = np.atleast_1d(np.asarray(t_transmit_gpst_s, dtype=float))
        return np.array([eph.clock_correction_s(self._system_tow(sat_id, ti)) for ti in t])


@dataclass
class PreciseProvider:
    """
    High model: SP3 orbit and clock (`utils.precise_orbits`).

    `sp3` is the object from `precise_orbits.load_sp3`; its epochs are GPS
    seconds of the SP3 time base. `time_offset_s` converts continuous GPS seconds
    to that base when they differ (set by the caller that loaded the file).
    Satellite PCO from ANTEX is not applied yet (concern C-12).
    """

    sp3: object
    time_offset_s: float = 0.0

    def position_ecef(self, sat_id: str, t_transmit_gpst_s) -> np.ndarray:
        from utils import precise_orbits

        t = np.atleast_1d(np.asarray(t_transmit_gpst_s, dtype=float)) - self.time_offset_s
        return precise_orbits.interpolate_position_m(self.sp3, sat_id, t)

    def clock_s(self, sat_id: str, t_transmit_gpst_s) -> np.ndarray:
        from utils import precise_orbits

        t = np.atleast_1d(np.asarray(t_transmit_gpst_s, dtype=float)) - self.time_offset_s
        return precise_orbits.interpolate_clock_s(self.sp3, sat_id, t)
