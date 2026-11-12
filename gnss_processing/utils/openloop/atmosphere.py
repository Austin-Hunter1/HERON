"""
Ionosphere and troposphere for the direct and the reflected path (plan section 7).

Each effect has one interface with two methods:

- `delay_m(...)`: the delay on one path, receiver to satellite (absolute mode,
  direct path).
- `excess_delay_m(...)`: the reflected-minus-direct delay. This is the term the
  direct-referenced model (Mode A) needs.

Ionosphere (D-095). The extra leg of the reflected path is under the drone and
has no free electrons; the two rays cross the ionosphere less than 2 km apart.
So `excess_delay_m` is zero for every ionosphere model. The absolute models are:

- `KlobucharIono`   -- GPS broadcast model (wraps `utils.observables`).
- `NeQuickGIono`    -- the decided rapid model (D-095). `NeQuickGIono.jrc(...)`
  calls the EU JRC reference C library through the `nequick` package; any
  other backend callable also works. See concern C-7 in `docs/NB03_CONCERNS.md`.
- `IonexIono`       -- a global ionosphere map (IONEX), single-layer model.

Troposphere (D-096, no met sensor). First-order excess delay of the reflected
leg (plan section 7.2), no ray bending:

    d_rho_trop ~ 2e-6 N0 h / sin(e)

Rapid: N0 from a standard atmosphere. High: N0 from GPT3 surface p, T, e.
This first-order value is NOT checked against ray tracing yet (concern C-6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

import numpy as np

from . import frames

K1, K2, K3 = 77.6, 23.3, 3.75e5  # refractivity constants (K/hPa, K/hPa, K^2/hPa)


# --------------------------------------------------------------------------
# Troposphere
# --------------------------------------------------------------------------

def standard_atmosphere(height_m) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Pressure (hPa), temperature (K) and water-vapour pressure (hPa) of a standard
    atmosphere at `height_m` (Berg 1948, as in Saastamoinen with 50 % humidity).
    """
    h = np.asarray(height_m, dtype=float)
    p = 1013.25 * (1.0 - 2.2557e-5 * h) ** 5.2568
    t = 291.15 - 0.0065 * h
    e = 0.5 * 6.108 * np.exp((17.15 * t - 4684.0) / (t - 38.45))
    return p, t, e


def refractivity(p_hpa, t_k, e_hpa) -> np.ndarray:
    """Surface refractivity N0 = 77.6 p/T + 3.75e5 e/T^2 (Smith-Weintraub form)."""
    p, t, e = (np.asarray(x, dtype=float) for x in (p_hpa, t_k, e_hpa))
    return K1 * p / t + K3 * e / t**2


def excess_tropo_first_order_m(n0, height_m, elevation_rad) -> np.ndarray:
    """
    First-order excess tropospheric delay of the reflected path (plan 7.2):

        [T(0) - T(h)] + 1e-6 N0 h / sin e  ~  2e-6 N0 h / sin e

    The slant delay through the bottom layer of thickness h is 1e-6 N0 h / sin e,
    and the reflected path crosses that layer twice more than the direct path
    (once down, once up). No ray bending; bending reduces this at low elevation.
    """
    return 2e-6 * np.asarray(n0) * np.asarray(height_m) / np.sin(np.asarray(elevation_rad))


@dataclass
class SaastamoinenTropo:
    """Rapid troposphere: Saastamoinen absolute delay, standard-atmosphere N0."""

    name: str = "saastamoinen"

    def surface_n0(self, lat_rad, lon_rad, height_m, when: datetime | None = None):
        return refractivity(*standard_atmosphere(height_m))

    def delay_m(self, elevation_rad, lat_rad, height_m, when: datetime | None = None):
        from utils import observables as obs

        return obs.saastamoinen_delay_m(np.asarray(elevation_rad), height_m=float(height_m),
                                        latitude_rad=float(lat_rad))

    def excess_delay_m(self, elevation_rad, height_above_surface_m, lat_rad, lon_rad,
                       surface_height_m, when: datetime | None = None):
        n0 = self.surface_n0(lat_rad, lon_rad, surface_height_m, when)
        return excess_tropo_first_order_m(n0, height_above_surface_m, elevation_rad)


class GPT3Tropo:
    """
    High troposphere: GPT3 surface meteorology (D-096).

    Reads the 5 x 5 or 1 x 1 degree GPT3 grid file (`gpt3_5.grd` / `gpt3_1.grd`,
    TU Wien, vmf.geo.tuwien.ac.at). The column names come from the file's own
    header line (it starts with '%'), so the reader does not depend on a fixed
    column order. For each quantity X the file gives a0, A1, B1, A2, B2:

        X(doy) = a0 + A1 cos(2 pi doy/365.25) + B1 sin(...) + A2 cos(4 pi ...) + B2 sin(...)

    Pressure is reduced from the grid height Hs to the site height with the
    virtual temperature; temperature with the lapse rate dT. This reader is NOT
    checked against the official GPT3 code yet (concern C-6). The mapping
    functions of GPT3/VMF3 are not ported; the absolute delay here uses
    Saastamoinen with the GPT3 surface values.
    """

    name = "gpt3"

    def __init__(self, grid_path: str | Path) -> None:
        self.path = Path(grid_path)
        if not self.path.exists():
            raise FileNotFoundError(
                f"GPT3 grid not found: {self.path}. Download gpt3_5.grd from "
                "https://vmf.geo.tuwien.ac.at/codes/ and set models.gpt3_grid in "
                "configs/openloop.yml."
            )
        header = None
        rows = []
        for line in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("%"):
                header = line[1:].replace(":", " ").split()
                continue
            if line.strip():
                rows.append([float(v) for v in line.split()])
        self.data = np.array(rows)
        self.columns = self._column_map(header)

    @staticmethod
    def _column_map(header: list[str] | None) -> dict[str, int]:
        """Map 'p_a0', 'p_A1', ... 'T_a0', ..., 'lat', 'lon', 'Hs' to column indices."""
        if not header:
            raise ValueError("GPT3 grid has no '%' header line with the column names")
        out: dict[str, int] = {}
        col = 0
        current = None
        for token in header:
            if token in ("lat", "lon", "undu", "Hs"):
                out[token] = col
                col += 1
                current = None
            elif token in ("a0", "A1", "B1", "A2", "B2") and current is not None:
                out[f"{current}_{token}"] = col
                col += 1
            else:
                current = token
        return out

    def _seasonal(self, rows: np.ndarray, name: str, doy: float) -> np.ndarray:
        w = 2 * np.pi * doy / 365.25
        c = self.columns
        return (rows[:, c[f"{name}_a0"]] + rows[:, c[f"{name}_A1"]] * np.cos(w)
                + rows[:, c[f"{name}_B1"]] * np.sin(w) + rows[:, c[f"{name}_A2"]] * np.cos(2 * w)
                + rows[:, c[f"{name}_B2"]] * np.sin(2 * w))

    def surface_met(self, lat_rad, lon_rad, height_m, when: datetime):
        """p (hPa), T (K), e (hPa) at one site, by the nearest grid node (first version)."""
        lat = np.degrees(float(lat_rad))
        lon = np.mod(np.degrees(float(lon_rad)), 360.0)
        c = self.columns
        glat = self.data[:, c["lat"]]
        glon = np.mod(self.data[:, c["lon"]], 360.0)
        i = int(np.argmin((glat - lat) ** 2 + ((glon - lon + 180) % 360 - 180) ** 2))
        row = self.data[i:i + 1]
        doy = when.timetuple().tm_yday + when.hour / 24.0
        p0 = self._seasonal(row, "p", doy)[0] / 100.0  # Pa -> hPa
        t0 = self._seasonal(row, "T", doy)[0]
        q = self._seasonal(row, "Q", doy)[0] / 1000.0  # g/kg -> kg/kg
        dt = self._seasonal(row, "dT", doy)[0] / 1000.0  # K/km -> K/m
        hs = row[0, c["Hs"]]
        redh = float(height_m) - hs
        tv = t0 * (1 + 0.6077 * q)
        p = p0 * np.exp(-9.80665 * 0.0289644 / (8.3143 * tv) * redh)
        t = t0 + dt * redh
        e = q * p / (0.622 + 0.378 * q)
        return float(p), float(t), float(e)

    def surface_n0(self, lat_rad, lon_rad, height_m, when: datetime | None = None):
        if when is None:
            raise ValueError("GPT3 needs the date")
        return refractivity(*self.surface_met(lat_rad, lon_rad, height_m, when))

    def delay_m(self, elevation_rad, lat_rad, height_m, when: datetime | None = None,
                lon_rad: float = 0.0):
        from utils import observables as obs

        p, t, e = self.surface_met(lat_rad, lon_rad, height_m, when)
        es = 6.108 * np.exp((17.15 * t - 4684.0) / (t - 38.45))
        return obs.saastamoinen_delay_m(np.asarray(elevation_rad), height_m=float(height_m),
                                        latitude_rad=float(lat_rad), pressure_hpa=p,
                                        temperature_k=t, relative_humidity=min(e / es, 1.0))

    def excess_delay_m(self, elevation_rad, height_above_surface_m, lat_rad, lon_rad,
                       surface_height_m, when: datetime | None = None):
        n0 = self.surface_n0(lat_rad, lon_rad, surface_height_m, when)
        return excess_tropo_first_order_m(n0, height_above_surface_m, elevation_rad)


@dataclass
class NoTropo:
    """No troposphere (to show what the model is worth)."""

    name: str = "none"

    def delay_m(self, elevation_rad, *args, **kwargs):
        return np.zeros_like(np.asarray(elevation_rad, dtype=float))

    def excess_delay_m(self, elevation_rad, *args, **kwargs):
        return np.zeros_like(np.asarray(elevation_rad, dtype=float))


# --------------------------------------------------------------------------
# Ionosphere
# --------------------------------------------------------------------------

IONO_K = 40.3  # m^3/s^2: delay_m = 40.3 TEC / f^2, TEC in el/m^2
TECU = 1e16


class _NoExcessIono:
    """The excess ionospheric delay of the reflected path is zero (plan 7.1)."""

    def excess_delay_m(self, elevation_rad, *args, **kwargs):
        return np.zeros_like(np.asarray(elevation_rad, dtype=float))


@dataclass
class NoIono(_NoExcessIono):
    name: str = "none"

    def delay_m(self, rx_ecef, sat_ecef, t_gpst_s, frequency_hz):
        return np.zeros(len(np.atleast_2d(sat_ecef)))


@dataclass
class KlobucharIono(_NoExcessIono):
    """GPS broadcast Klobuchar model; alpha and beta from the message or RINEX header."""

    alpha: tuple[float, float, float, float]
    beta: tuple[float, float, float, float]
    name: str = "klobuchar"

    def delay_m(self, rx_ecef, sat_ecef, t_gpst_s, frequency_hz):
        from utils import observables as obs

        rx = np.atleast_2d(rx_ecef)
        sat = np.atleast_2d(sat_ecef)
        el, az = frames.elevation_azimuth(rx, sat)
        lat, lon, _ = frames.ecef_to_geodetic(rx)
        tow = np.mod(np.atleast_1d(t_gpst_s), 604800.0)
        out = np.empty(len(sat))
        for i in range(len(sat)):
            out[i] = frames.SPEED_OF_LIGHT * obs.klobuchar_delay_s(
                self.alpha, self.beta,
                latitude_semicircles=float(np.degrees(lat[min(i, len(lat) - 1)])) / 180.0,
                longitude_semicircles=float(np.degrees(lon[min(i, len(lon) - 1)])) / 180.0,
                elevation_semicircles=float(np.degrees(el[i])) / 180.0,
                azimuth_semicircles=float(np.degrees(az[i])) / 180.0,
                gps_time_of_week_s=float(tow[min(i, len(tow) - 1)]),
                frequency_hz=frequency_hz,
            )
        return out


def _import_jrc_nequick():
    """The `NeQuick` class of the `nequick` package, or a clear error."""
    try:
        from nequick import NeQuick
    except ImportError as exc:
        raise ImportError(
            "NeQuick-G (D-095) uses the `nequick` package (EU JRC reference C code, "
            "https://pypi.org/project/nequick/). It is not installed. PyPI has only a "
            "source release, so the install needs a C compiler (on Windows: the MSVC "
            "Build Tools). Install it with `pip install nequick --no-deps` (its "
            "numpy<2 pin is for a helper that this code does not use), or set "
            "models.ionosphere to klobuchar/ionex. Mode A does not need it. See "
            "docs/NB03_CONCERNS.md, C-7."
        ) from exc
    return NeQuick


def nequick_jrc_backend(gps_minus_utc_s: float, model_class=None) -> Callable:
    """
    A `NeQuickGIono` backend that calls the EU JRC NeQuick-G C library through
    the `nequick` package (D-095).

    The library needs:

    - the month and the UT hour: from GPST seconds minus `gps_minus_utc_s`
      (`models.gps_minus_utc_s` in the config; 18 s since 2017-01-01);
    - WGS-84 geodetic positions in the order (longitude deg, latitude deg,
      height m). This is the order of the C binding and of the JRC header
      `set_receiver_position`. The package README shows (lat, lon, alt), which
      is NOT what the binding reads;
    - the Galileo broadcast coefficients ai0, ai1, ai2.

    It returns the slant TEC in TECU along the straight line from the receiver
    to the satellite. The binding drops fractions of a second from the time;
    that changes the TEC by much less than the model error.

    `model_class` replaces `nequick.NeQuick` (tests use a fake one).
    """
    from utils.time_systems import GPS_EPOCH

    def backend(rx_ecef, sat_ecef, t_gpst_s, az_coefficients):
        cls = model_class or _import_jrc_nequick()
        model = cls(*(float(a) for a in az_coefficients))
        rx = np.atleast_2d(rx_ecef)
        sat = np.atleast_2d(sat_ecef)
        t = np.atleast_1d(np.asarray(t_gpst_s, dtype=float))
        n = max(len(rx), len(sat), len(t))
        rx_lat, rx_lon, rx_h = frames.ecef_to_geodetic(np.broadcast_to(rx, (n, 3)))
        sv_lat, sv_lon, sv_h = frames.ecef_to_geodetic(np.broadcast_to(sat, (n, 3)))
        t = np.broadcast_to(t, (n,))
        out = np.empty(n)
        for i in range(n):
            utc = GPS_EPOCH + timedelta(seconds=float(t[i]) - gps_minus_utc_s)
            out[i] = model.compute_stec(
                utc,
                float(np.degrees(rx_lon[i])), float(np.degrees(rx_lat[i])), float(rx_h[i]),
                float(np.degrees(sv_lon[i])), float(np.degrees(sv_lat[i])), float(sv_h[i]),
            )
        return out

    return backend


@dataclass
class NeQuickGIono(_NoExcessIono):
    """
    NeQuick-G for all constellations (D-095).

    Slant TEC = integral of N_e along the straight line receiver -> satellite;
    delay = 40.3 STEC / f^2. NeQuick-G needs the positions, the time (month and
    UT) and the effective ionisation level Az = ai0 + ai1 mu + ai2 mu^2 from the
    Galileo broadcast coefficients.

    `backend(rx_ecef, sat_ecef, t_gpst_s, az_coefficients) -> STEC [TECU]` does
    the integration. `NeQuickGIono.jrc(...)` uses the EU JRC reference C library
    (`nequick_jrc_backend`). Without a backend the call stops with a clear
    message (concern C-7).
    """

    az_coefficients: tuple[float, float, float] | None = None
    backend: Callable | None = None
    name: str = "nequick_g"

    @classmethod
    def jrc(cls, az_coefficients, gps_minus_utc_s: float) -> "NeQuickGIono":
        """NeQuick-G with the EU JRC C library of the `nequick` package."""
        return cls(tuple(az_coefficients), nequick_jrc_backend(gps_minus_utc_s))

    def delay_m(self, rx_ecef, sat_ecef, t_gpst_s, frequency_hz):
        if self.backend is None:
            raise NotImplementedError(
                "NeQuick-G (D-095) needs a backend. Use NeQuickGIono.jrc(...), which "
                "calls the `nequick` package, or set models.ionosphere to "
                "klobuchar/ionex. In the direct-referenced model (Mode A) the "
                "ionosphere cancels, so Mode A does not need it (C-7)."
            )
        if self.az_coefficients is None:
            raise ValueError("NeQuick-G needs the Galileo ai0, ai1, ai2 coefficients")
        stec = np.asarray(self.backend(np.atleast_2d(rx_ecef), np.atleast_2d(sat_ecef),
                                       np.atleast_1d(t_gpst_s), self.az_coefficients))
        return IONO_K * stec * TECU / frequency_hz**2


class IonexIono(_NoExcessIono):
    """
    Global ionosphere map (IONEX) with the single-layer model.

    VTEC at the ionospheric pierce point (bilinear in lat/lon, linear in time
    between maps), mapped to slant with

        M(e) = 1 / sqrt(1 - (R cos e / (R + H))^2)

    R and H come from the file header. The rotation of the map with the Sun
    between epochs is not applied (first version).
    """

    name = "ionex"

    def __init__(self, dataset) -> None:
        self.dataset = dataset
        header = dataset.header
        self.radius_m = float(header.base_radius) * 1e3
        self.height_m = float(getattr(header, "height_1", 450.0)) * 1e3
        self.exponent = float(getattr(header, "exponent", -1.0))
        maps = sorted(dataset.tec_maps, key=lambda m: m.epoch)
        self.epochs = np.array([m.epoch.timestamp() for m in maps])
        self.lats = maps[0].get_lats()
        self.lons = maps[0].get_lons()
        order = np.argsort(self.lats)
        self.lats = self.lats[order]
        self.grids = np.array([m.get_map_array()[order] for m in maps]) * 10.0**self.exponent

    def _vtec(self, lat_deg, lon_deg, unix_s):
        from scipy.interpolate import RegularGridInterpolator

        out = np.empty(len(lat_deg))
        for i, (la, lo, t) in enumerate(zip(lat_deg, lon_deg, unix_s)):
            k = int(np.clip(np.searchsorted(self.epochs, t) - 1, 0, len(self.epochs) - 2))
            w = (t - self.epochs[k]) / (self.epochs[k + 1] - self.epochs[k])
            vals = []
            for g in (self.grids[k], self.grids[k + 1]):
                f = RegularGridInterpolator((self.lats, self.lons), g, bounds_error=False,
                                            fill_value=np.nan)
                vals.append(float(f([[la, ((lo + 180) % 360) - 180]])[0]))
            out[i] = (1 - w) * vals[0] + w * vals[1]
        return out

    def delay_m(self, rx_ecef, sat_ecef, t_gpst_s, frequency_hz, unix_s=None):
        rx = np.atleast_2d(rx_ecef)
        sat = np.atleast_2d(sat_ecef)
        el, az = frames.elevation_azimuth(rx, sat)
        lat, lon, _ = frames.ecef_to_geodetic(rx)
        z = np.pi / 2 - el
        zp = np.arcsin(self.radius_m / (self.radius_m + self.height_m) * np.sin(z))
        psi = z - zp
        lat_ipp = np.arcsin(np.sin(lat) * np.cos(psi) + np.cos(lat) * np.sin(psi) * np.cos(az))
        lon_ipp = lon + np.arcsin(np.sin(psi) * np.sin(az) / np.cos(lat_ipp))
        if unix_s is None:
            raise ValueError("IonexIono.delay_m needs unix_s (UTC seconds) for the map time")
        vtec = self._vtec(np.degrees(lat_ipp), np.degrees(lon_ipp), np.atleast_1d(unix_s))
        stec = vtec / np.cos(zp)
        return IONO_K * stec * TECU / frequency_hz**2


@dataclass
class AtmosphereModels:
    """The pair of models that config selects."""

    ionosphere: object = field(default_factory=NoIono)
    troposphere: object = field(default_factory=SaastamoinenTropo)
