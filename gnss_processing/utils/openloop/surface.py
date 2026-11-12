"""
The reflecting surface: ellipsoid, geoid, water level and normal (plan section 5).

    h_ell(lat, lon) = H_orth(lat, lon) + N(lat, lon)

Geoid models (D-092): GEOID18 by default, EGM2008 only outside GEOID18 coverage
(with a warning), a constant N for the rapid model, or none. Grids come from
`pyproj` and the PROJ grid files (D-094).

Datum chain (D-092, D-093): GEOID18 converts NAD83(2011) ellipsoidal heights to
NAVD88. The trajectory is ITRF/WGS84, so `GeoidGrid` with GEOID18 first moves the
point from ITRF2014 to NAD83(2011) with pyproj, then applies N. Without that step
the height is wrong by about 1 m in Colorado.

The surface normal: the ellipsoid normal (rapid), or the plumb line from the
deflection of the vertical (high), with

    xi = -(1/M) dN/dlat,   eta = -(1/(N_phi cos lat)) dN/dlon
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from . import frames

# PROJ grid file names (PROJ-data). They download on first use when PROJ network
# access is on (`pyproj.network.set_network_enabled(True)`), or can be put in the
# PROJ data directory by hand.
GEOID18_GRID = "us_noaa_g2018u0.tif"
EGM2008_GRID = "us_nga_egm08_25.tif"

# GEOID18 coverage: CONUS, Puerto Rico, US Virgin Islands (rough boxes).
GEOID18_BOXES = (
    (24.0, 58.0, -131.0, -60.0),   # CONUS
    (17.0, 19.0, -68.0, -64.0),    # PR and USVI
)


class GeoidModel(Protocol):
    """Geoid undulation N (m) at geodetic lat, lon (rad), for an ITRF/WGS84 height."""

    name: str

    def undulation_m(self, lat_rad, lon_rad) -> np.ndarray: ...


@dataclass
class NoGeoid:
    """N = 0: the water surface is the ellipsoid plus the water level."""

    name: str = "none"

    def undulation_m(self, lat_rad, lon_rad) -> np.ndarray:
        return np.zeros(np.broadcast(np.asarray(lat_rad), np.asarray(lon_rad)).shape)


@dataclass
class ConstantGeoid:
    """
    Rapid model: one N for the whole flight area.

    For GEOID18 this N must already include the ITRF to NAD83(2011) height
    difference, so that h_ell(ITRF) = H_NAVD88 + N. `GeoidGrid.constant_at`
    makes such a value from the grid at the site.
    """

    n_m: float
    name: str = "constant"

    def undulation_m(self, lat_rad, lon_rad) -> np.ndarray:
        return np.full(np.broadcast(np.asarray(lat_rad), np.asarray(lon_rad)).shape,
                       float(self.n_m))


def in_geoid18_coverage(lat_deg: float, lon_deg: float) -> bool:
    """True when the point is inside the GEOID18 boxes."""
    return any(a <= lat_deg <= b and c <= lon_deg <= d for a, b, c, d in GEOID18_BOXES)


class GeoidGrid:
    """
    High model: a PROJ geoid grid through pyproj (D-092, D-094).

    `undulation_m` returns the value that converts an ITRF2014 ellipsoidal
    height to the orthometric height of the model's datum:

        N_eff = h_ITRF - H

    For GEOID18 that includes the ITRF2014 -> NAD83(2011) step; for EGM2008 it
    does not (EGM2008 is referred to WGS84, which is aligned to ITRF at the cm
    level).
    """

    def __init__(self, model: str = "geoid18", epoch_year: float = 2026.9,
                 enable_network: bool = True) -> None:
        try:
            import pyproj  # noqa: F401
            import pyproj.network
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise ImportError(
                "pyproj is needed for the geoid grids (D-094). Install it in the "
                "notebook environment: `uv add pyproj` in gnss_processing/."
            ) from exc
        if enable_network:
            # PROJ then fetches the grid tiles it needs from cdn.proj.org and caches
            # them in its user data directory (concern C-10: not RESOURCES_PATH yet).
            pyproj.network.set_network_enabled(True)
        self.model = model.lower()
        self.name = self.model
        self.epoch_year = float(epoch_year)
        self._build()

    def _build(self) -> None:
        from pyproj import Transformer

        if self.model == "geoid18":
            # ITRF2014 geographic 3D -> NAD83(2011) geographic 3D -> NAVD88 height.
            self._to_nad83 = Transformer.from_crs("EPSG:7912", "EPSG:6319", always_xy=True)
            self._to_orth = Transformer.from_pipeline(
                f"+proj=pipeline +step +proj=axisswap +order=2,1 +step +proj=unitconvert "
                f"+xy_in=deg +xy_out=rad +step +proj=vgridshift +grids={GEOID18_GRID} "
                f"+multiplier=1 +step +proj=unitconvert +xy_in=rad +xy_out=deg "
                f"+step +proj=axisswap +order=2,1"
            )
        elif self.model == "egm2008":
            self._to_nad83 = None
            self._to_orth = Transformer.from_pipeline(
                f"+proj=pipeline +step +proj=axisswap +order=2,1 +step +proj=unitconvert "
                f"+xy_in=deg +xy_out=rad +step +proj=vgridshift +grids={EGM2008_GRID} "
                f"+multiplier=1 +step +proj=unitconvert +xy_in=rad +xy_out=deg "
                f"+step +proj=axisswap +order=2,1"
            )
        else:
            raise ValueError(f"geoid grid model must be geoid18 or egm2008, not {self.model!r}")

    def undulation_m(self, lat_rad, lon_rad, h_m: float = 0.0) -> np.ndarray:
        lat = np.degrees(np.atleast_1d(np.asarray(lat_rad, dtype=float)))
        lon = np.degrees(np.atleast_1d(np.asarray(lon_rad, dtype=float)))
        h = np.full(lat.shape, float(h_m))
        lat2, lon2, h2 = lat, lon, h
        if self._to_nad83 is not None:
            t = np.full(lat.shape, self.epoch_year)
            lon2, lat2, h2, _ = self._to_nad83.transform(lon, lat, h, t)
        # With +multiplier=1, vgridshift gives z + grid value. A zero input height
        # therefore reads the grid value N itself, whatever the PROJ version's
        # default sign is. Then H = h_datum - N and N_eff = h_ITRF - H.
        _, _, n = self._to_orth.transform(lat2, lon2, np.zeros_like(h2))
        n = np.asarray(n, dtype=float)
        if not np.all(np.isfinite(n)) or np.any(np.abs(n) > 200):
            raise ValueError(f"{self.model}: the grid gave no value here (outside the grid, "
                             "or the grid file is not available to PROJ)")
        return h - (h2 - n) if self._to_nad83 is not None else n

    def constant_at(self, lat_deg: float, lon_deg: float, h_m: float = 0.0) -> ConstantGeoid:
        """A `ConstantGeoid` with this grid's N_eff at one site (rapid model)."""
        n = float(self.undulation_m(np.deg2rad(lat_deg), np.deg2rad(lon_deg), h_m)[0])
        return ConstantGeoid(n, name=f"constant({self.model})")


def make_geoid(name: str, *, site_lat_deg: float | None = None, site_lon_deg: float | None = None,
               constant_n_m: float | None = None, epoch_year: float = 2026.9) -> GeoidModel:
    """
    Build the geoid model that config names (D-092).

    "geoid18" outside the GEOID18 coverage falls back to "egm2008" with a warning.
    """
    name = name.lower()
    if name == "none":
        return NoGeoid()
    if name == "constant":
        if constant_n_m is None:
            raise ValueError("geoid 'constant' needs surface.constant_geoid_n_m in config")
        return ConstantGeoid(constant_n_m)
    if name == "geoid18" and site_lat_deg is not None and not in_geoid18_coverage(
        site_lat_deg, site_lon_deg
    ):
        warnings.warn("site outside GEOID18 coverage: EGM2008 is used (D-092). The datum "
                      "of the water level must then be EGM2008-based, not NAVD88.")
        name = "egm2008"
    return GeoidGrid(name, epoch_year)


@dataclass
class Surface:
    """
    The reflecting surface: geoid + water level (orthometric), or a constant
    ellipsoidal height.

    `water_level_m` is H_orth in the geoid's datum (NAVD88 for GEOID18, D-093).
    `use_deflection` tilts the normal by the deflection of the vertical from the
    geoid gradient (high model).
    """

    geoid: GeoidModel
    water_level_m: float
    use_deflection: bool = False

    def height_ell_m(self, lat_rad, lon_rad) -> np.ndarray:
        """Ellipsoidal (ITRF) height of the surface at lat, lon."""
        return self.water_level_m + self.geoid.undulation_m(lat_rad, lon_rad)

    def normal(self, lat_rad, lon_rad) -> np.ndarray:
        """Unit surface normal (..., 3) in ECEF."""
        n_ell = frames.ellipsoid_normal(lat_rad, lon_rad)
        if not self.use_deflection:
            return n_ell
        xi, eta = self.deflection_rad(lat_rad, lon_rad)
        lat = np.asarray(lat_rad, dtype=float)
        lon = np.asarray(lon_rad, dtype=float)
        rot = np.swapaxes(frames.rot_ecef_to_ned(lat, lon), -1, -2)  # NED -> ECEF
        # The plumb line (up) tilts toward north by -xi and toward east by -eta.
        up_ned = np.stack([-np.asarray(xi), -np.asarray(eta), -np.ones_like(xi)], axis=-1)
        # Up is minus down; NED "down" component -1 means up.
        up = np.einsum("...ij,...j->...i", rot, up_ned)
        return frames.unit(up)

    def deflection_rad(self, lat_rad, lon_rad, step_rad: float = 2e-5):
        """Deflection of the vertical (xi, eta) in rad from a finite difference of N."""
        lat = np.asarray(lat_rad, dtype=float)
        lon = np.asarray(lon_rad, dtype=float)
        m, n_phi = frames.radii_of_curvature(lat)
        dn_dlat = (self.geoid.undulation_m(lat + step_rad, lon)
                   - self.geoid.undulation_m(lat - step_rad, lon)) / (2 * step_rad)
        dn_dlon = (self.geoid.undulation_m(lat, lon + step_rad)
                   - self.geoid.undulation_m(lat, lon - step_rad)) / (2 * step_rad)
        return -dn_dlat / m, -dn_dlon / (n_phi * np.cos(lat))
