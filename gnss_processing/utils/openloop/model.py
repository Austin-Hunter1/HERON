"""
The open-loop model of the reflection (plan section 8).

Mode A, direct-referenced (default, D-097, D-100):

    chi_R(u) = chi_D(u) - 1e3 (D(u) / c + tau_hw)          code phase, ms
    phi_R(u) = phi_D(u) - D(u) / lambda + phi_hw            carrier phase, cycles
    f_R(u)   = f_D(u)   - D'(u) / lambda                     Doppler, Hz

with D = (rho_R - rho_D) + d_rho_trop the excess path (the excess ionosphere is
zero, plan 7.1). chi_D, phi_D, f_D come from the direct channel
(`direct_ref.DirectReference`). A longer path gives a smaller code phase and a
smaller carrier phase (the sign conventions of the tracking code).

The geometry is evaluated at anchor times (`model_rate_hz`, plan 8.5). The excess
path D is then a cubic spline in uptime; its derivative gives the Doppler, so
phase and Doppler stay consistent.

Mode B, absolute (plan 8.2), is in `absolute_code_phase_ms` and
`absolute_carrier_cycles`: a cross-check only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
from scipy.interpolate import CubicSpline

from . import frames, sat_states, specular
from .direct_ref import DirectReference
from .surface import Surface
from .timebase import TimeBase

C = frames.SPEED_OF_LIGHT

# rx_tracks(t_gpst) -> (nadir_pos, nadir_vel, zenith_pos, zenith_vel), each (N, 3)
ReceiverTracks = Callable[[np.ndarray], tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]


@dataclass
class GeometrySeries:
    """The geometry at the anchor times. Every array has one row per anchor."""

    uptime_ms: np.ndarray
    gpst_s: np.ndarray
    sat_direct_ecef: np.ndarray
    sat_reflect_ecef: np.ndarray
    nadir_ecef: np.ndarray
    zenith_ecef: np.ndarray
    direct_path_m: np.ndarray
    reflected_path_m: np.ndarray
    excess_geometric_m: np.ndarray
    excess_tropo_m: np.ndarray
    excess_iono_m: np.ndarray
    specular: specular.SpecularSolution
    solver: str
    extras: dict = field(default_factory=dict)

    @property
    def excess_total_m(self) -> np.ndarray:
        return self.excess_geometric_m + self.excess_tropo_m + self.excess_iono_m

    @property
    def elevation_deg(self) -> np.ndarray:
        return np.degrees(self.specular.elevation_rad)

    @property
    def height_m(self) -> np.ndarray:
        return self.specular.height_above_surface_m


def compute_geometry(
    *,
    sat_id: str,
    provider: sat_states.SatelliteStateProvider,
    rx_tracks: ReceiverTracks,
    surface: Surface,
    timebase: TimeBase,
    uptime_ms: np.ndarray,
    solver: str = "image_plane",
    troposphere=None,
    ionosphere=None,
    when=None,
) -> GeometrySeries:
    """
    The excess path at `uptime_ms` (the anchor grid).

    The transmit time of the reflected path is solved to the specular point,
    which is found with the satellite at the direct-path transmit time first
    (the two differ by under 1 us at UAS heights, plan section 4).
    """
    u = np.asarray(uptime_ms, dtype=float)
    t = timebase.gpst(u)
    nadir, _, zenith, _ = rx_tracks(t)
    sat_d, _ = sat_states.transmit_state(provider, sat_id, t, zenith)
    solve = specular.SOLVERS[solver]
    first = solve(sat_d, nadir, surface)
    sat_r, _ = sat_states.transmit_state(provider, sat_id, t, first.specular_ecef)
    spec = solve(sat_r, nadir, surface)
    direct = np.linalg.norm(sat_d - zenith, axis=1)
    reflected = spec.reflected_path_m
    lat, lon, h_s = frames.ecef_to_geodetic(spec.specular_ecef)
    tropo = np.zeros(len(u))
    if troposphere is not None:
        tropo = np.asarray(troposphere.excess_delay_m(
            spec.elevation_rad, spec.height_above_surface_m, float(np.mean(lat)),
            float(np.mean(lon)), float(np.mean(h_s)), when), dtype=float)
    iono = np.zeros(len(u))
    if ionosphere is not None:
        iono = np.asarray(ionosphere.excess_delay_m(spec.elevation_rad), dtype=float)
    return GeometrySeries(u, t, sat_d, sat_r, nadir, zenith, direct, reflected,
                          reflected - direct, tropo, iono, spec, solver)


@dataclass
class ReflectionModel:
    """
    Mode A model for one satellite, one band, one nadir channel.

    `tau_hw_s` and `phi_hw_cycles` are the inter-channel hardware delay and phase
    (plan 9.4). `excess_bias_m` is an optional slow correction from a first pass
    (plan 10.5): it is added to D.
    """

    direct: DirectReference
    geometry: GeometrySeries
    carrier_freq_hz: float
    tau_hw_s: float = 0.0
    phi_hw_cycles: float = 0.0
    excess_bias_m: float = 0.0

    def __post_init__(self) -> None:
        u = self.geometry.uptime_ms * 1e-3
        self._excess = CubicSpline(u, self.geometry.excess_total_m)
        self.wavelength_m = C / self.carrier_freq_hz

    def excess_m(self, uptime_ms) -> np.ndarray:
        return self._excess(np.asarray(uptime_ms, dtype=float) * 1e-3) + self.excess_bias_m

    def excess_rate_mps(self, uptime_ms) -> np.ndarray:
        return self._excess(np.asarray(uptime_ms, dtype=float) * 1e-3, 1)

    def code_phase_ms(self, uptime_ms) -> np.ndarray:
        return (self.direct.code_phase_at(uptime_ms)
                - 1e3 * (self.excess_m(uptime_ms) / C + self.tau_hw_s))

    def carrier_cycles(self, uptime_ms) -> np.ndarray:
        return (self.direct.carrier_at(uptime_ms) - self.excess_m(uptime_ms) / self.wavelength_m
                + self.phi_hw_cycles)

    def doppler_hz(self, uptime_ms) -> np.ndarray:
        return self.direct.doppler_at(uptime_ms) - self.excess_rate_mps(uptime_ms) / self.wavelength_m

    def period_starts(self, first_uptime_ms: float, last_uptime_ms: float,
                      primary_period_ms: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
        """
        Code periods p and their start uptimes u_p, where chi_R(u_p) = p * T_p.

        Blocks start on code-period boundaries of the *reflected* replica, so a
        block never straddles an overlay or data transition (the tracking code
        does the same for the direct channel). Found by inverse interpolation on
        a 0.25 ms grid, then two Newton steps.
        """
        grid = np.arange(first_uptime_ms, last_uptime_ms, 0.25)
        chi = self.code_phase_ms(grid) / primary_period_ms
        ok = np.isfinite(chi)
        grid, chi = grid[ok], chi[ok]
        p = np.arange(int(np.ceil(chi[0])), int(np.floor(chi[-1])))
        u = np.interp(p, chi, grid)
        for _ in range(2):
            rate = 1e-3 * (1.0 + self.doppler_hz(u) / self.carrier_freq_hz) / primary_period_ms
            u = u - (self.code_phase_ms(u) / primary_period_ms - p) / rate
        return p, u


def absolute_code_phase_ms(
    geometry: GeometrySeries,
    timebase: TimeBase,
    provider: sat_states.SatelliteStateProvider,
    sat_id: str,
    *,
    ionosphere_m: np.ndarray | None = None,
    troposphere_m: np.ndarray | None = None,
) -> np.ndarray:
    """
    Mode B (plan 8.2): the reflected code phase in ms of transmit time, from the
    geometry and the clocks alone (no direct channel):

        chi = 1e3 (t_rx - (rho_R + I + T) / c + dt_sat + dt_rx)

    A tracking code phase has an unknown whole-ms start value, so compare Mode A
    and Mode B after the removal of their mean difference.
    """
    t_rx = geometry.gpst_s
    dt_sat = provider.clock_s(sat_id, t_rx - geometry.reflected_path_m / C)
    dt_rx = timebase.receiver_clock_s(geometry.uptime_ms)
    extra = 0.0
    if ionosphere_m is not None:
        extra = extra + ionosphere_m
    if troposphere_m is not None:
        extra = extra + troposphere_m
    return 1e3 * (t_rx - (geometry.reflected_path_m + extra) / C + dt_sat + dt_rx)
