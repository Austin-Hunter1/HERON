"""
Phase 1 of the notebook 03 plan: a synthetic direct + reflected signal pair.

No real reflection recording exists yet. This module makes a zenith channel and
a nadir channel from one known geometry, so every later step (model, correlator,
acquisition, phase filter, leakage estimate) can be checked against the truth.

What the generator does, and why:

- The geometry is a small "world": one satellite on a straight-line track, one
  drone above a flat (ellipsoidal) water surface at a known height. A straight
  line is not an orbit, but over seconds it is a good local model, and it keeps
  the truth easy to compute.
- The truth excess path is found by a direct numerical minimisation of the
  reflected path over the surface (Fermat's principle). It does not use
  `utils.openloop.specular`, so a test that compares the two is a real check.
- The zenith channel has the direct signal. The nadir channel has the reflection,
  plus an optional leak of the direct signal (D-098), plus noise. Both channels
  share one sample clock, as on the payload (D-025, D-097).
- The receiver clock adds a common phase and code drift to both channels. In the
  direct-referenced model (plan section 8.1) that drift must cancel.

Sign conventions are those of `tests/synthetic.py` and the tracking code:
code phase in ms of code grows with time; the carrier is exp(+j 2 pi phi) with
phi in cycles; a longer path gives a smaller code phase and a smaller phase.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import minimize

from . import frames

C = frames.SPEED_OF_LIGHT


@dataclass
class SyntheticWorld:
    """
    One satellite, one drone, one flat water surface.

    All values have defaults so a test can change one thing at a time.
    `drone_height_m` is the height of the nadir antenna phase centre above the
    surface; `vertical_amplitude_m` and `vertical_period_s` make it move up and
    down, so the excess Doppler is not zero.
    """

    site_lat_deg: float = 40.0
    site_lon_deg: float = -105.0
    surface_h_ell_m: float = 1500.0
    drone_height_m: float = 30.0
    vertical_amplitude_m: float = 0.5
    vertical_period_s: float = 4.0
    horizontal_velocity_ned_mps: tuple[float, float, float] = (2.0, 0.0, 0.0)
    zenith_above_nadir_m: float = 0.25
    sat_elevation_deg: float = 35.0
    sat_azimuth_deg: float = 120.0
    sat_range_m: float = 21_000_000.0
    sat_velocity_ned_mps: tuple[float, float, float] = (1200.0, -2500.0, 300.0)
    clock_drift_ppm: float = 0.3
    clock_offset_s: float = 2.0e-4

    def site_ecef(self) -> np.ndarray:
        """Surface point under the drone at t = 0."""
        return frames.geodetic_to_ecef(
            np.deg2rad(self.site_lat_deg), np.deg2rad(self.site_lon_deg), self.surface_h_ell_m
        )

    def _ned_to_ecef(self) -> np.ndarray:
        return frames.rot_ecef_to_ned(
            np.deg2rad(self.site_lat_deg), np.deg2rad(self.site_lon_deg)
        ).T

    def nadir_ecef(self, t_s) -> np.ndarray:
        """Nadir antenna phase centre (N, 3) at times `t_s` (s from the start)."""
        t = np.atleast_1d(np.asarray(t_s, dtype=float))
        up = self.drone_height_m + self.vertical_amplitude_m * np.sin(
            2 * np.pi * t / self.vertical_period_s
        )
        ned = np.outer(t, self.horizontal_velocity_ned_mps)
        ned[:, 2] -= up
        return self.site_ecef() + ned @ self._ned_to_ecef().T

    def zenith_ecef(self, t_s) -> np.ndarray:
        """Zenith antenna phase centre: straight above the nadir antenna (level drone)."""
        up = np.array([0.0, 0.0, -self.zenith_above_nadir_m]) @ self._ned_to_ecef().T
        return self.nadir_ecef(t_s) + up

    def sat_ecef(self, t_s) -> np.ndarray:
        """Satellite position (N, 3): a straight line through the start point."""
        t = np.atleast_1d(np.asarray(t_s, dtype=float))
        el = np.deg2rad(self.sat_elevation_deg)
        az = np.deg2rad(self.sat_azimuth_deg)
        los_ned = np.array([np.cos(el) * np.cos(az), np.cos(el) * np.sin(az), -np.sin(el)])
        start = self.site_ecef() + (self.sat_range_m * los_ned) @ self._ned_to_ecef().T
        vel = np.asarray(self.sat_velocity_ned_mps) @ self._ned_to_ecef().T
        return start + np.outer(t, vel)

    def sat_velocity_ecef(self) -> np.ndarray:
        """Satellite velocity (3,), constant on the straight-line track."""
        return np.asarray(self.sat_velocity_ned_mps) @ self._ned_to_ecef().T


def truth_reflected_path_m(world: SyntheticWorld, t_s) -> tuple[np.ndarray, np.ndarray]:
    """
    Reflected path length and specular point by direct minimisation (Fermat).

    The surface is the ellipsoid raised by `surface_h_ell_m`. The free variables
    are latitude and longitude; the minimum of |T - S| + |S - R| is the specular
    path. This uses no code from `utils.openloop.specular` on purpose.
    """
    t = np.atleast_1d(np.asarray(t_s, dtype=float))
    sats = world.sat_ecef(t)
    rxs = world.nadir_ecef(t)
    paths = np.empty(len(t))
    points = np.empty((len(t), 3))
    lat0, lon0 = np.deg2rad(world.site_lat_deg), np.deg2rad(world.site_lon_deg)
    scale = 1.0 / frames.WGS84_A
    for i in range(len(t)):

        def path(x, i=i):
            s = frames.geodetic_to_ecef(lat0 + x[0] * scale, lon0 + x[1] * scale,
                                        world.surface_h_ell_m)
            return np.linalg.norm(sats[i] - s) + np.linalg.norm(rxs[i] - s)

        result = minimize(path, x0=np.zeros(2), method="Nelder-Mead",
                          options={"xatol": 1e-5, "fatol": 1e-7, "maxiter": 4000})
        paths[i] = result.fun
        points[i] = frames.geodetic_to_ecef(lat0 + result.x[0] * scale,
                                            lon0 + result.x[1] * scale,
                                            world.surface_h_ell_m)
    return paths, points


@dataclass
class SyntheticTruth:
    """Truth for one synthetic pair, on a coarse time grid (`t_s`)."""

    t_s: np.ndarray
    direct_path_m: np.ndarray
    reflected_path_m: np.ndarray
    specular_ecef: np.ndarray
    receiver_clock_s: np.ndarray
    direct_code_phase_ms: np.ndarray
    direct_carrier_cycles: np.ndarray
    direct_doppler_hz: np.ndarray
    reflected_code_phase_ms: np.ndarray
    reflected_carrier_cycles: np.ndarray
    symbols: np.ndarray
    reflection_phase_offset_cycles: float
    extras: dict = field(default_factory=dict)

    @property
    def excess_path_m(self) -> np.ndarray:
        return self.reflected_path_m - self.direct_path_m


def _component_waveform(signal, chips: np.ndarray, symbols: np.ndarray) -> np.ndarray:
    """
    Baseband code signal (no carrier) of every component of `signal`, summed.

    A single-component signal is real. With two components, branch I is real and
    branch Q is imaginary, as in `tests/synthetic.py`. Overlays advance once per
    primary code period; data symbols advance once per symbol period.
    """
    chip_index = np.floor(chips).astype(np.int64)
    code_length = len(signal.code_set.components[0].sequence)
    period_index = chip_index // code_length
    components = signal.code_set.components
    out = np.zeros(len(chips), dtype=np.complex64)
    for component in components:
        value = component.sequence[chip_index % len(component.sequence)].astype(np.float32)
        if component.overlay is not None:
            value = value * component.overlay[period_index % len(component.overlay)]
        if component.symbol_period_ms:
            symbol_index = period_index // (component.symbol_period_ms // signal.primary_period_ms)
            value = value * symbols[symbol_index % len(symbols)]
        weight = np.sqrt(component.power_weight)
        if len(components) > 1 and str(component.branch).upper().endswith("Q"):
            out += 1j * weight * value
        else:
            out += weight * value
    return out


def generate_pair(
    signal,
    world: SyntheticWorld,
    *,
    samp_rate: float,
    duration_s: float,
    reflection_amplitude: float = 0.3,
    reflection_phase_offset_cycles: float = 0.137,
    leakage_amplitude: float = 0.0,
    leakage_phase_cycles: float = 0.0,
    zenith_noise_sigma: float = 0.0,
    nadir_noise_sigma: float = 0.0,
    initial_code_phase_ms: float = 0.4,
    hardware_delay_s: float = 0.0,
    truth_rate_hz: float = 20.0,
    rng: np.random.Generator | None = None,
    seed: int = 1,
) -> tuple[np.ndarray, np.ndarray, SyntheticTruth]:
    """
    Make (zenith_samples, nadir_samples, truth) for one satellite.

    `reflection_phase_offset_cycles` is the phase change at the reflection (plus
    any hardware phase). It is a constant here; the OL phase processing must find
    it only as part of the ambiguity. `hardware_delay_s` delays the nadir channel
    against the zenith channel, to test the calibration of section 9.4.
    """
    rng = rng if rng is not None else np.random.default_rng(seed)
    wavelength = C / signal.carrier_freq_hz

    # Truth on a coarse grid, then a cubic interpolation to every sample.
    t_truth = np.arange(0.0, duration_s + 1.0 / truth_rate_hz, 1.0 / truth_rate_hz)
    direct = np.linalg.norm(world.sat_ecef(t_truth) - world.zenith_ecef(t_truth), axis=1)
    reflected, specular = truth_reflected_path_m(world, t_truth)
    clock = world.clock_offset_s + world.clock_drift_ppm * 1e-6 * t_truth

    from scipy.interpolate import CubicSpline

    direct_spline = CubicSpline(t_truth, direct)
    excess_spline = CubicSpline(t_truth, reflected - direct)
    nominal = signal.tracking_code_rate_chips_per_sec
    num_symbols = int(duration_s * 1000) + 100
    symbols = 1.0 - 2.0 * (rng.random(num_symbols) < 0.5)

    # Made in chunks of 2^21 samples: one 2 s run of L5 at 20 Msps would otherwise
    # need several GB for the int64 chip indices and float64 phases.
    num = int(round(samp_rate * duration_s))
    zenith = np.empty(num, dtype=np.complex64)
    nadir = np.empty(num, dtype=np.complex64)
    chunk = 1 << 21
    for a in range(0, num, chunk):
        b = min(a + chunk, num)
        t = np.arange(a, b) / samp_rate
        direct_s = direct_spline(t)
        excess_s = excess_spline(t)
        clock_s = world.clock_offset_s + world.clock_drift_ppm * 1e-6 * t
        # Received code phase (ms): the transmit time in ms of code, plus a start
        # value. The receiver clock runs fast by `clock_s`, so the code looks advanced.
        code_ms_direct = initial_code_phase_ms + 1e3 * (t - direct_s / C + clock_s)
        code_ms_reflected = code_ms_direct - 1e3 * (excess_s / C + hardware_delay_s)
        carrier_direct = -direct_s / wavelength + signal.carrier_freq_hz * clock_s
        carrier_reflected = carrier_direct - excess_s / wavelength + reflection_phase_offset_cycles

        zenith_code = _component_waveform(signal, code_ms_direct * 1e-3 * nominal, symbols)
        z = zenith_code * np.exp(2j * np.pi * carrier_direct)
        nadir_code = _component_waveform(signal, code_ms_reflected * 1e-3 * nominal, symbols)
        n = reflection_amplitude * nadir_code * np.exp(2j * np.pi * carrier_reflected)
        if leakage_amplitude:
            n = n + leakage_amplitude * zenith_code * np.exp(
                2j * np.pi * (carrier_direct + leakage_phase_cycles)
            )
        if zenith_noise_sigma > 0:
            z = z + zenith_noise_sigma * (rng.standard_normal(b - a)
                                          + 1j * rng.standard_normal(b - a))
        if nadir_noise_sigma > 0:
            n = n + nadir_noise_sigma * (rng.standard_normal(b - a)
                                         + 1j * rng.standard_normal(b - a))
        zenith[a:b] = z
        nadir[a:b] = n

    carrier_direct_truth = -direct / wavelength + signal.carrier_freq_hz * clock
    truth = SyntheticTruth(
        t_s=t_truth,
        direct_path_m=direct,
        reflected_path_m=reflected,
        specular_ecef=specular,
        receiver_clock_s=clock,
        direct_code_phase_ms=initial_code_phase_ms + 1e3 * (t_truth - direct / C + clock),
        direct_carrier_cycles=carrier_direct_truth,
        direct_doppler_hz=CubicSpline(t_truth, carrier_direct_truth)(t_truth, 1),
        reflected_code_phase_ms=initial_code_phase_ms
        + 1e3 * (t_truth - reflected / C + clock - hardware_delay_s),
        reflected_carrier_cycles=-reflected / wavelength + signal.carrier_freq_hz * clock
        + reflection_phase_offset_cycles,
        symbols=symbols,
        reflection_phase_offset_cycles=reflection_phase_offset_cycles,
        extras={"hardware_delay_s": hardware_delay_s,
                "leakage_amplitude": leakage_amplitude,
                "leakage_phase_cycles": leakage_phase_cycles,
                "reflection_amplitude": reflection_amplitude},
    )
    return zenith.astype(np.complex64), nadir.astype(np.complex64), truth
