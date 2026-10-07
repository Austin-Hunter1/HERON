"""Terrain: ground heights from a USGS 3DEP elevation grid, and ray blocking.

Why: the receiver can stand on a hill above the water (the 2026-10-12 test
site is about 40 m above Hillcrest Reservoir). Then:

* the height that sets the reflection point and the Fresnel zone is the
  height above the *water surface*, not above the ground under the mast;
* the hill can block the satellite, the reflected leg, or the signal
  coming in to the water.

The grid is fetched once with `fetch_dem` and saved to a small `.npz` file.
Later runs read the file and need no network. Heights are metres in the
DEM's own vertical datum (NAVD88 for 3DEP). Only differences are used.
Bare-earth data: trees and buildings are not in it.
"""

from __future__ import annotations

import dataclasses
import io
import math
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import shapely
from PIL import Image

from ground_sim.config import SimConfig
from ground_sim.simulate import Track

M_PER_DEG_LAT = 111_132.92
NODATA_BELOW_M = -1000.0  # 3DEP marks missing cells with a very large negative value


@dataclass
class Terrain:
    """An elevation grid on a regular longitude/latitude lattice."""

    elev: np.ndarray  # shape (rows, cols), row 0 = north edge, NaN = no data
    lon_min: float
    lon_max: float
    lat_min: float
    lat_max: float
    lat0: float  # receiver, the origin of the local east/north frame
    lon0: float

    # ---- coordinates -------------------------------------------------
    def _m_per_deg_lon(self) -> float:
        return M_PER_DEG_LAT * math.cos(math.radians(self.lat0))

    def elevation_en(self, east_m, north_m):
        """Bilinear elevation at local east/north metres (arrays allowed).

        Returns NaN outside the grid, so a ray that leaves the grid sees no
        terrain.
        """
        lon = self.lon0 + np.asarray(east_m, dtype=float) / self._m_per_deg_lon()
        lat = self.lat0 + np.asarray(north_m, dtype=float) / M_PER_DEG_LAT
        rows, cols = self.elev.shape
        col = (lon - self.lon_min) / (self.lon_max - self.lon_min) * cols - 0.5
        row = (self.lat_max - lat) / (self.lat_max - self.lat_min) * rows - 0.5
        inside = (col >= 0) & (col <= cols - 1) & (row >= 0) & (row <= rows - 1)
        c = np.clip(col, 0, cols - 1)
        r = np.clip(row, 0, rows - 1)
        c0 = np.minimum(np.floor(c).astype(int), cols - 2)
        r0 = np.minimum(np.floor(r).astype(int), rows - 2)
        fc, fr = c - c0, r - r0
        e = self.elev
        val = (
            e[r0, c0] * (1 - fc) * (1 - fr)
            + e[r0, c0 + 1] * fc * (1 - fr)
            + e[r0 + 1, c0] * (1 - fc) * fr
            + e[r0 + 1, c0 + 1] * fc * fr
        )
        return np.where(inside, val, np.nan)

    # ---- queries -----------------------------------------------------
    def segment_clear(self, p0, p1, step_m: float, clearance_m: float) -> bool:
        """Return True when the straight segment p0->p1 stays above the ground.

        Points are (east, north, height) in metres. A sample blocks the
        segment when the ground is more than `clearance_m` above it.
        """
        p0a, p1a = np.asarray(p0, dtype=float), np.asarray(p1, dtype=float)
        length = float(np.linalg.norm(p1a - p0a))
        count = max(int(length / step_m), 2)
        frac = np.linspace(0.0, 1.0, count)[:, None]
        pts = p0a + frac * (p1a - p0a)
        ground = self.elevation_en(pts[:, 0], pts[:, 1])
        return not bool(np.any(ground > pts[:, 2] + clearance_m))  # NaN compares False

    def ray_clear(self, origin, az_deg, el_deg, length_m, step_m, clearance_m) -> bool:
        """Return True when a ray from `origin` towards (az, el) clears the ground."""
        az, el = math.radians(az_deg), math.radians(el_deg)
        end = (
            origin[0] + length_m * math.cos(el) * math.sin(az),
            origin[1] + length_m * math.cos(el) * math.cos(az),
            origin[2] + length_m * math.sin(el),
        )
        return self.segment_clear(origin, end, step_m, clearance_m)

    def water_level_m(self, polygon) -> float | None:
        """Return the median DEM height inside a water polygon (lon/lat), or None.

        Lidar DEMs show a reservoir as a flat surface, so the median is the
        water level. None means the polygon has no valid cells in the grid.
        """
        rows, cols = self.elev.shape
        lon = self.lon_min + (np.arange(cols) + 0.5) / cols * (self.lon_max - self.lon_min)
        lat = self.lat_max - (np.arange(rows) + 0.5) / rows * (self.lat_max - self.lat_min)
        lon_g, lat_g = np.meshgrid(lon, lat)
        inside = shapely.contains_xy(polygon, lon_g, lat_g) & np.isfinite(self.elev)
        return float(np.median(self.elev[inside])) if inside.any() else None

    # ---- file --------------------------------------------------------
    def save(self, path: Path) -> None:
        """Write the grid to `path` (compressed `.npz`)."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            path,
            elev=self.elev.astype(np.float32),
            bounds=np.array([self.lon_min, self.lon_max, self.lat_min, self.lat_max]),
            origin=np.array([self.lon0, self.lat0]),
        )

    @classmethod
    def load(cls, path: Path) -> Terrain:
        """Read a grid written by `save`."""
        with np.load(path) as f:
            lon_min, lon_max, lat_min, lat_max = (float(v) for v in f["bounds"])
            lon0, lat0 = (float(v) for v in f["origin"])
            return cls(f["elev"].astype(float), lon_min, lon_max, lat_min, lat_max, lat0, lon0)


def dem_bounds(cfg: SimConfig) -> tuple[float, float, float, float]:
    """Return (lon_min, lon_max, lat_min, lat_max) of the DEM box around the receiver."""
    tcfg = cfg.terrain
    assert tcfg is not None
    dlat = tcfg.radius_m / M_PER_DEG_LAT
    dlon = dlat / math.cos(math.radians(cfg.site.latitude_deg))
    return (
        cfg.site.longitude_deg - dlon,
        cfg.site.longitude_deg + dlon,
        cfg.site.latitude_deg - dlat,
        cfg.site.latitude_deg + dlat,
    )


def fetch_dem(cfg: SimConfig) -> Path:
    """Download the elevation grid for the site and save it. Return the file path.

    Asks the 3DEP `exportImage` service (URL from the config) for one float32
    GeoTIFF that covers the box around the receiver. This is the only network
    call of the terrain code.
    """
    tcfg = cfg.terrain
    if tcfg is None:
        raise ValueError("the config has no [terrain] table")
    lon_min, lon_max, lat_min, lat_max = dem_bounds(cfg)
    side = 2.0 * tcfg.radius_m
    size = max(int(round(side / tcfg.cell_m)), 2)
    query = urllib.parse.urlencode(
        {
            "bbox": f"{lon_min},{lat_min},{lon_max},{lat_max}",
            "bboxSR": 4326,
            "imageSR": 4326,
            "size": f"{size},{size}",
            "format": "tiff",
            "pixelType": "F32",
            "interpolation": "RSP_BilinearInterpolation",
            "f": "image",
        }
    )
    with urllib.request.urlopen(f"{tcfg.source_url}/exportImage?{query}", timeout=120) as resp:  # noqa: S310
        data = resp.read()
    elev = np.array(Image.open(io.BytesIO(data)), dtype=float)
    elev[elev < NODATA_BELOW_M] = np.nan
    if not np.isfinite(elev).any():
        raise ValueError("the DEM service returned no elevation data for this site")
    terrain = Terrain(
        elev, lon_min, lon_max, lat_min, lat_max, cfg.site.latitude_deg, cfg.site.longitude_deg
    )
    terrain.save(tcfg.dem_file)
    return tcfg.dem_file


def load_terrain(cfg: SimConfig) -> Terrain:
    """Load the saved grid. Stop with a clear message when it is missing or old."""
    tcfg = cfg.terrain
    assert tcfg is not None
    if not tcfg.dem_file.is_file():
        raise FileNotFoundError(
            f"DEM file not found: {tcfg.dem_file}. Run: ground-sim --config <file> --fetch-dem"
        )
    terrain = Terrain.load(tcfg.dem_file)
    want = dem_bounds(cfg)
    have = (terrain.lon_min, terrain.lon_max, terrain.lat_min, terrain.lat_max)
    if any(abs(a - b) > 1e-6 for a, b in zip(want, have, strict=True)):
        raise ValueError(
            f"{tcfg.dem_file} has a different size than [terrain] radius_m. "
            "Run --fetch-dem again."
        )
    same_site = (
        abs(terrain.lat0 - cfg.site.latitude_deg) < 1e-7
        and abs(terrain.lon0 - cfg.site.longitude_deg) < 1e-7
    )
    if not same_site:
        raise ValueError(
            f"{tcfg.dem_file} was made for another site. Run --fetch-dem again for this site."
        )
    return terrain


def apply_terrain(
    tracks: list[Track], terrain: Terrain, cfg: SimConfig, rx_height_m: float
) -> list[Track]:
    """Return the tracks with `blocked` set on every sample.

    For every sample the satellite ray from the antenna must clear the ground.
    For samples on water, also the reflected leg (antenna to specular point)
    and the incoming leg (specular point to satellite) must clear it.
    Heights are in the DEM datum. `rx_height_m` is the antenna. The water
    surface is the `surface_m` of each sample.
    """
    tcfg = cfg.terrain
    assert tcfg is not None
    antenna = (0.0, 0.0, rx_height_m)
    out: list[Track] = []
    for tr in tracks:
        samples = []
        for s in tr.samples:
            clear = terrain.ray_clear(
                antenna, s.az_deg, s.el_deg, tcfg.max_ray_m, tcfg.step_m, tcfg.clearance_m
            )
            if clear and s.on_water:
                spec = (s.east_m, s.north_m, s.surface_m)
                clear = terrain.segment_clear(
                    antenna, spec, tcfg.step_m, tcfg.clearance_m
                ) and terrain.ray_clear(
                    spec, s.az_deg, s.el_deg, tcfg.max_ray_m, tcfg.step_m, tcfg.clearance_m
                )
            samples.append(dataclasses.replace(s, blocked=not clear))
        out.append(Track(sid=tr.sid, constellation=tr.constellation, samples=samples))
    return out

