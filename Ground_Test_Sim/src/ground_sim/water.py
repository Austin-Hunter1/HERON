"""Decide which reflections fall on water, with the planner's water data.

Why: the flight planner counts a reflection only when its specular point
is inside a mapped water body (`on_water` in `Specular_Planner/engine/
planner.py`). This module uses the same test and the same data file
(`waterbodies.geojson`), so the ground test and the flight plan agree.
It also gives the part of the first Fresnel zone that covers water. This
matters at the metre scale of a ground test, where a shoreline can cut
through a zone.
"""

from __future__ import annotations

import dataclasses
import json
import math
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from shapely.geometry import Point, box, mapping
from shapely.ops import nearest_points

from ground_sim.config import SimConfig
from ground_sim.simulate import C_M_S, Track, reflect_sample

M_PER_DEG_LAT = 111_132.92  # close enough to choose a clip box; not used for results


class WaterModel:
    """Water polygons around the receiver, with the planner's water test."""

    def __init__(self, engine: SimpleNamespace, geojson_path: Path, cfg: SimConfig) -> None:
        """Load `geojson_path` and prepare the polygons near the receiver."""
        if not Path(geojson_path).is_file():
            raise FileNotFoundError(f"water file not found: {geojson_path}")
        self._geo = engine.geometry
        self._coverage = engine.coverage
        self._cfg = cfg
        # The planner's index caches by object id: keep this object alive.
        self._geojson = json.loads(Path(geojson_path).read_text(encoding="utf-8"))
        self._near = self._near_polygons(cfg.map.radius_m)

    def _near_polygons(self, radius_m: float) -> list[tuple]:
        """Return the (polygon, feature) pairs inside a box around the receiver.

        Uses the planner's `_water_index`. Line features (rivers, ditches)
        are already buffered to their width there.
        """
        site = self._cfg.site
        dlat = radius_m / M_PER_DEG_LAT
        dlon = dlat / max(math.cos(math.radians(site.latitude_deg)), 0.01)
        window = box(
            site.longitude_deg - dlon,
            site.latitude_deg - dlat,
            site.longitude_deg + dlon,
            site.latitude_deg + dlat,
        )
        tree, geoms = self._geo._water_index(self._geojson)
        return [geoms[int(i)] for i in tree.query(window) if geoms[int(i)][0].intersects(window)]

    def zone_water_fraction(
        self, lat: float, lon: float, az: float, along: float, across: float, polygon
    ):
        """Return the part (0 to 1) of the first Fresnel zone that covers `polygon`."""
        spec = {
            "lat": lat,
            "lon": lon,
            "az": az,
            "fresnel_along_m": along,
            "fresnel_across_m": across,
        }
        zone = self._coverage.ellipse_poly(spec)
        if zone.is_empty or not zone.intersects(polygon):
            return 0.0
        return float(zone.intersection(polygon).area / zone.area)

    def nearest_polygon(self):
        """Return the water polygon (lon/lat) nearest to the receiver, or None."""
        site = self._cfg.site
        tree, geoms = self._geo._water_index(self._geojson)
        if not geoms:
            return None
        here = Point(site.longitude_deg, site.latitude_deg)
        return geoms[int(tree.nearest(here))][0]

    def nearest_water_m(self) -> float | None:
        """Return the distance (metres) from the receiver to the nearest mapped water."""
        site = self._cfg.site
        poly = self.nearest_polygon()
        if poly is None:
            return None
        here = Point(site.longitude_deg, site.latitude_deg)
        spot = nearest_points(poly, here)[0]
        return float(
            self._geo.haversine_m(site.latitude_deg, site.longitude_deg, spot.y, spot.x)
        )

    def bodies(self) -> list[tuple]:
        """Return the (name, polygon) of every water body inside the map radius."""
        out = []
        for g, f in self._near:
            props = f.get("properties") or {}
            out.append((props.get("name") or f"water {props.get('osm_id', '')}".strip(), g))
        return out


@dataclass
class Surface:
    """One water body with its own surface height."""

    name: str
    polygon: object  # shapely polygon, lon/lat
    level_m: float  # surface height in the DEM datum (0 in flat mode)
    distance_m: float  # from the receiver to the nearest shore


def build_surfaces(water: WaterModel, terrain, cfg: SimConfig) -> list[Surface]:
    """Return the water bodies near the receiver, nearest first, each with its level.

    Why: the planner treats all water as one flat plane. On a hill site two
    reservoirs can lie at different heights, and the height decides where the
    reflection lands. With terrain, each level is the median DEM height in the
    body (or `water_level_m` from the config for all bodies). Without terrain
    all bodies are at level 0 and the antenna is `receiver_height_agl_m` up.
    A body with no DEM cells is left out.
    """
    site = cfg.site
    here = Point(site.longitude_deg, site.latitude_deg)
    surfaces = []
    for name, poly in water.bodies():
        if terrain is None:
            level: float | None = 0.0
        elif cfg.terrain is not None and cfg.terrain.water_level_m is not None:
            level = cfg.terrain.water_level_m
        else:
            level = terrain.water_level_m(poly)
        if level is None:
            continue
        spot = nearest_points(poly, here)[0]
        dist = water._geo.haversine_m(site.latitude_deg, site.longitude_deg, spot.y, spot.x)
        surfaces.append(Surface(name, poly, float(level), float(dist)))
    surfaces.sort(key=lambda s: s.distance_m)
    return surfaces


def map_features(surfaces: list[Surface]) -> dict:
    """Return the water bodies as a GeoJSON FeatureCollection for the map."""
    feats = [
        {
            "type": "Feature",
            "properties": {"name": s.name, "level_m": round(s.level_m, 1)},
            "geometry": mapping(s.polygon),
        }
        for s in surfaces
    ]
    return {"type": "FeatureCollection", "features": feats}


def reflect_on_water(
    passes: list[Track],
    surfaces: list[Surface],
    antenna_m: float,
    water: WaterModel,
    cfg: SimConfig,
) -> list[Track]:
    """Return the tracks with the reflection on the water body each sample hits.

    For every sample, each water body is tried in order of distance: the
    specular point is worked out for the antenna height above that body's
    surface, and the sample takes the first body that contains the point.
    A sample that hits no water keeps the geometry of the nearest body and
    `on_water = False`. The specular point is placed with a local flat
    conversion (error under 0.1 m inside 2 km).
    """
    site = cfg.site
    wavelength = C_M_S / cfg.signals.carrier_hz
    phi = math.radians(site.latitude_deg)
    m_lat = 111132.92 - 559.82 * math.cos(2 * phi) + 1.175 * math.cos(4 * phi)
    m_lon = 111412.84 * math.cos(phi) - 93.5 * math.cos(3 * phi)
    usable = [s for s in surfaces if antenna_m - s.level_m > 0.0]

    def place(s, surf):
        cand = reflect_sample(s, antenna_m - surf.level_m, wavelength)
        pt = Point(
            site.longitude_deg + cand.east_m / m_lon, site.latitude_deg + cand.north_m / m_lat
        )
        return cand, pt

    out: list[Track] = []
    for tr in passes:
        samples = []
        for s in tr.samples:
            if not usable:
                samples.append(s)
                continue
            hit = None
            for surf in usable:
                cand, pt = place(s, surf)
                if surf.polygon.covers(pt):
                    hit = (surf, cand, pt)
                    break
            if hit is None:
                cand, pt = place(s, usable[0])
                samples.append(
                    dataclasses.replace(cand, lat=pt.y, lon=pt.x, surface_m=usable[0].level_m)
                )
                continue
            surf, cand, pt = hit
            frac = water.zone_water_fraction(
                pt.y, pt.x, cand.az_deg, cand.along_m, cand.across_m, surf.polygon
            )
            samples.append(
                dataclasses.replace(
                    cand,
                    lat=pt.y,
                    lon=pt.x,
                    on_water=True,
                    water_frac=frac,
                    surface_m=surf.level_m,
                    body=surf.name,
                )
            )
        out.append(Track(sid=tr.sid, constellation=tr.constellation, samples=samples))
    return out
