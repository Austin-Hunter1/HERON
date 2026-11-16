"""
The open-loop model of the reflection (plan section 8).

Mode A, direct-referenced (default, D-097, D-100):

    chi_R(u) = chi_D(u) - 1e3 (D_g(u) / c + tau_hw)        code phase, ms
    phi_R(u) = phi_D(u) - D_p(u) / lambda + phi_hw          carrier phase, cycles
    f_R(u)   = f_D(u)   - D_p'(u) / lambda                   Doppler, Hz

with the excess path split as in D-105 (the ionosphere delays the code and
advances the carrier by the same amount):

    D_g = (rho_R - rho_D) + d_rho_trop + d_rho_iono          group (code) excess
    D_p = (rho_R - rho_D) + d_rho_trop - d_rho_iono          phase (carrier) excess

The excess ionosphere is zero today (plan 7.1), so D_g = D_p in practice; the
split keeps the signs right if a model ever gives it a value. chi_D, phi_D, f_D
come from the direct channel (`direct_ref.DirectReference`). A longer path gives
a smaller code phase and a smaller carrier phase (the sign conventions of the
tracking code).

The geometry is evaluated at anchor times (`model_rate_hz`, plan 8.5). Each
excess is then a cubic spline in uptime; the derivative of D_p gives the Doppler,
so phase and Doppler stay consistent.

Mode B, absolute (plan 8.2), is in `absolute_code_phase_ms`: a cross-check
only. Only the code phase is built; the Mode B carrier phase is not (concern
C-15). The clock and delay signs follow D-105 (see `absolute_code_phase_ms`).
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
    def excess_group_m(self) -> np.ndarray:
        """Group (code) excess path: geometry + troposphere + ionosphere (D-105)."""
        return self.excess_geometric_m + self.excess_tropo_m + self.excess_iono_m

    @property
    def excess_phase_m(self) -> np.ndarray:
        """Phase (carrier) excess path: the ionosphere enters with the opposite sign (D-105)."""
        return self.excess_geometric_m + self.excess_tropo_m - self.excess_iono_m

    @property
    def excess_total_m(self) -> np.ndarray:
        """The group (code) excess; kept for the callers that read this name."""
        return self.excess_group_m

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
    (plan 10.5): it is non-dispersive, so it is added to both D_g and D_p.
    """

    direct: DirectReference
    geometry: GeometrySeries
    carrier_freq_hz: float
    tau_hw_s: float = 0.0
    phi_hw_cycles: float = 0.0
    excess_bias_m: float = 0.0

    def __post_init__(self) -> None:
        u = self.geometry.uptime_ms * 1e-3
        self._excess_group = CubicSpline(u, self.geometry.excess_group_m)
        self._excess_phase = CubicSpline(u, self.geometry.excess_phase_m)
        self.wavelength_m = C / self.carrier_freq_hz

    def excess_m(self, uptime_ms) -> np.ndarray:
        """Group (code) excess D_g (m)."""
        return self._excess_group(np.asarray(uptime_ms, dtype=float) * 1e-3) + self.excess_bias_m

    def excess_phase_m(self, uptime_ms) -> np.ndarray:
        """Phase (carrier) excess D_p (m)."""
        return self._excess_phase(np.asarray(uptime_ms, dtype=float) * 1e-3) + self.excess_bias_m

    def excess_rate_mps(self, uptime_ms) -> np.ndarray:
        """Rate of the phase excess D_p' (m/s): the carrier rate, which gives the Doppler."""
        return self._excess_phase(np.asarray(uptime_ms, dtype=float) * 1e-3, 1)

    def code_phase_ms(self, uptime_ms) -> np.ndarray:
        return (self.direct.code_phase_at(uptime_ms)
                - 1e3 * (self.excess_m(uptime_ms) / C + self.tau_hw_s))

    def carrier_cycles(self, uptime_ms) -> np.ndarray:
        return (self.direct.carrier_at(uptime_ms) - self.excess_phase_m(uptime_ms) / self.wavelength_m
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
    reflected_iono_m: np.ndarray | None = None,
    reflected_tropo_m: np.ndarray | None = None,
    group_delay_s: float = 0.0,
) -> np.ndarray:
    """
    Mode B (plan 8.2): the reflected code phase in ms of transmit time, from the
    geometry and the clocks alone (no direct channel). With D-105 (the code phase
    is the transmit time, eta = t_tx(t - tau)) and t the SYSTEM time of the samples:

        chi = 1e3 (t - (rho_R + I_R + T_R) / c - tau_grp + dt_tx(t - rho_R / c))

    - t is `geometry.gpst_s`. `TimeBase.gpst` has already removed the receiver
      clock (t = T - dt_rx), so dt_rx does NOT appear again here. An older version
      added it a second time; on real data that left a slope equal to the clock
      drift in Mode A minus Mode B.
    - `reflected_iono_m` and `reflected_tropo_m` are the FULL delays of the
      reflected path (code: + ionosphere), not the excess over the direct path.
    - `group_delay_s` is the satellite group delay tau_grp, as the
      `observables.signal_group_delay_s` value (-T_GD + ISC) with the sign of a
      clock term: it is ADDED to dt_tx, as in `satellite_clock_corrections`.
    - dt_tx is the provider's clock (with relativity) at the system transmit time.

    `timebase` must be the one the geometry was computed with; a mismatch raises.
    A tracking code phase has an unknown whole-ms start value, so compare Mode A
    and Mode B after the removal of their mean difference: a constant (for example
    an omitted group delay) then cancels, a slope or a curve does not.
    """
    t = geometry.gpst_s
    expected = timebase.gpst(geometry.uptime_ms)
    if not np.allclose(expected, t, rtol=0.0, atol=1e-6):
        raise ValueError(
            "absolute_code_phase_ms: `timebase` does not match the geometry's GPS time "
            f"(largest difference {np.max(np.abs(expected - t)):.3g} s); pass the TimeBase "
            "that compute_geometry used")
    dt_tx = provider.clock_s(sat_id, t - geometry.reflected_path_m / C) + group_delay_s
    path = np.asarray(geometry.reflected_path_m, dtype=float)
    if reflected_iono_m is not None:
        path = path + reflected_iono_m
    if reflected_tropo_m is not None:
        path = path + reflected_tropo_m
    return 1e3 * (t - path / C + dt_tx)
