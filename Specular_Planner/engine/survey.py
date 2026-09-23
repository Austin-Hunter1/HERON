"""Lawnmower / along-stream grid so first-Fresnel patches tile a waterbody."""

from __future__ import annotations

import math

from shapely.geometry import Point, mapping
from shapely.ops import linemerge, substring

from engine.geometry import fresnel_axes, haversine_m, waterbody_hit


def waterbody_at(lake_gj: dict, lon: float | None, lat: float | None):
    """Return (poly, original_geom, feature). Poly is always an area (rivers are buffered)."""
    if lon is None or lat is None:
        return None, None, None
    return waterbody_hit(lake_gj, lon, lat)


def _area_m2(poly, clat: float) -> float:
    mlat = 111132.92
    mlon = 111132.92 * math.cos(math.radians(clat))
    return abs(poly.area) * mlat * mlon


def _as_polygon(g):
    if g is None or g.is_empty:
        return g
    if g.geom_type == "MultiPolygon":
        return max(g.geoms, key=lambda x: x.area)
    if g.geom_type == "Polygon":
        return g
    return g.buffer(0)


def poly_from_ring(coords):
    """Closed lon/lat ring → a polygon with area."""
    from shapely.geometry import Polygon

    ring = [(float(p[0]), float(p[1])) for p in coords]
    if len(ring) < 3:
        raise ValueError("Need at least two corners for a box, or three for a polygon.")
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    if len(ring) < 4:
        raise ValueError("Need at least two corners for a box, or three for a polygon.")
    poly = Polygon(ring)
    if not poly.is_valid:
        poly = poly.buffer(0)
    poly = _as_polygon(poly)
    if poly is None or poly.is_empty or poly.area <= 0:
        raise ValueError("That box has no area — spread the corners out.")
    return poly


def _is_line(g) -> bool:
    return g is not None and g.geom_type in ("LineString", "MultiLineString")


def feat_key(feat) -> tuple:
    p = (feat or {}).get("properties") or {}
    return (p.get("osm_id"), p.get("osm"), p.get("name"), p.get("kind"))


def _as_single_line(geom):
    if geom is None or geom.is_empty:
        return None
    if geom.geom_type == "LineString":
        return geom
    if geom.geom_type == "MultiLineString":
        merged = linemerge(geom)
        return merged
    return None


def clip_centerline(geom, lon0: float, lat0: float, lon1: float, lat1: float):
    """Cut a river LineString from the start click toward the end click."""
    line = _as_single_line(geom)
    if line is None:
        raise ValueError("That water is not a river centerline.")
    p0 = Point(lon0, lat0)
    p1 = Point(lon1, lat1)
    if line.geom_type == "MultiLineString":
        line = min(line.geoms, key=lambda g: g.distance(p0) + g.distance(p1))
    d0 = float(line.project(p0))
    d1 = float(line.project(p1))
    if abs(d1 - d0) < 1e-12:
        raise ValueError("Start and end are too close on that river.")
    seg = substring(line, d0, d1)
    if seg is None or seg.is_empty or seg.geom_type != "LineString" or len(seg.coords) < 2:
        raise ValueError("Could not cut that river stretch.")
    coords = list(seg.coords)
    length = 0.0
    for (x0, y0), (x1, y1) in zip(coords, coords[1:]):
        length += haversine_m(y0, x0, y1, x1)
    if length < 8.0:
        raise ValueError("That stretch is too short — pick start and end farther apart.")
    return seg, length


def line_length_m(geom) -> float:
    parts = list(geom.geoms) if geom.geom_type == "MultiLineString" else [geom]
    length = 0.0
    for part in parts:
        coords = list(part.coords)
        for (x0, y0), (x1, y1) in zip(coords, coords[1:]):
            length += haversine_m(y0, x0, y1, x1)
    return length


def _points_along_line(geom, spacing_m: float) -> list[tuple[float, float]]:
    parts = list(geom.geoms) if geom.geom_type == "MultiLineString" else [geom]
    out: list[tuple[float, float]] = []
    remaining = 0.0
    first = True
    for part in parts:
        coords = list(part.coords)
        if len(coords) < 2:
            continue
        for (lon0, lat0), (lon1, lat1) in zip(coords, coords[1:]):
            seg = haversine_m(lat0, lon0, lat1, lon1)
            if seg < 0.2:
                continue
            if first:
                out.append((lat0, lon0))
                first = False
                remaining = 0.0
            t = 0.0
            while remaining + (seg - t) >= spacing_m - 1e-6:
                need = spacing_m - remaining
                frac = min(1.0, (t + need) / seg)
                out.append((lat0 + frac * (lat1 - lat0), lon0 + frac * (lon1 - lon0)))
                t += need
                remaining = 0.0
            remaining += seg - t
    if out:
        last_part = parts[-1]
        last = list(last_part.coords)[-1]
        end = (last[1], last[0])
        if haversine_m(out[-1][0], out[-1][1], end[0], end[1]) > 1.0:
            out.append(end)
    return out


def _hex_rows(poly, spacing_m: float) -> list[list[tuple[float, float]]]:
    minx, miny, maxx, maxy = poly.bounds
    clat = (miny + maxy) / 2.0
    mlat = 111132.92
    mlon = max(1.0, 111132.92 * math.cos(math.radians(clat)))
    width = (maxx - minx) * mlon
    height = (maxy - miny) * mlat
    row_h = max(spacing_m * math.sqrt(3.0) / 2.0, 1.0)
    nx = width / max(spacing_m, 1.0)
    ny = height / row_h
    if nx * ny > 60000:
        return []
    rows = []
    iy = 0
    y = 0.0
    pad = spacing_m * 0.15
    while y <= height + pad:
        xoff = (iy % 2) * 0.5 * spacing_m
        x = -pad + xoff
        row = []
        while x <= width + pad:
            lon = minx + x / mlon
            lat = miny + y / mlat
            if poly.contains(Point(lon, lat)):
                row.append((lat, lon))
            x += spacing_m
        if row:
            rows.append(row)
        iy += 1
        y += row_h
    return rows


def _lawnmower(rows: list[list[tuple[float, float]]]) -> list[tuple[float, float]]:
    out = []
    for i, row in enumerate(rows):
        out.extend(row if i % 2 == 0 else list(reversed(row)))
    return out


def _snap_on_poly(dlat, dlon, slat, slon, poly):
    if poly.contains(Point(dlon, dlat)):
        return dlat, dlon, True
    last = None
    for i in range(32):
        f = i / 31.0
        la = slat + f * (dlat - slat)
        lo = slon + f * (dlon - slon)
        if poly.contains(Point(lo, la)):
            last = (la, lo)
    if last:
        return last[0], last[1], True
    return dlat, dlon, False


def build_survey(
    poly,
    *,
    h_agl: float,
    az: float,
    el: float,
    prn,
    loiter_s: float,
    spacing_m: float | None,
    max_wp: int = 200,
    centerline=None,
) -> tuple[list[dict], dict]:
    poly = _as_polygon(poly)
    clat = poly.centroid.y
    clon = poly.centroid.x
    along, across = fresnel_axes(h_agl, el if el else 50.0)
    patch = min(along, across)
    wanted = spacing_m if spacing_m and spacing_m > 0 else patch * 0.75
    area = _area_m2(poly, clat)
    along_stream = _is_line(centerline)
    if along_stream and centerline is not None and not centerline.is_empty:
        mid = centerline.interpolate(0.5, normalized=True)
        clat, clon = mid.y, mid.x
    hover_spacing = wanted
    path_spacing = min(hover_spacing, 20.0) if along_stream else hover_spacing
    coarsened = False

    def sample(sp):
        if along_stream:
            return _points_along_line(centerline, sp)
        rows = _hex_rows(poly, sp)
        if not rows:
            return []
        return _lawnmower(rows)

    splashes = sample(path_spacing)
    n = len(splashes)
    guard = 0
    while (n > max_wp or n == 0 and not along_stream) and path_spacing < 800 and guard < 12:
        guard += 1
        if n == 0:
            path_spacing = min(800.0, path_spacing * 2.5)
        else:
            coarsened = True
            path_spacing *= math.sqrt(n / max_wp) * 1.05
        splashes = sample(path_spacing)
        n = len(splashes)
    if n > max_wp and splashes:
        coarsened = True
        step = max(1, int(math.ceil(n / max_wp)))
        last = splashes[-1]
        splashes = splashes[::step]
        if splashes[-1] != last:
            splashes.append(last)
        path_spacing *= step
    if along_stream:
        hover_spacing = max(hover_spacing, path_spacing)
    hover_every = 1
    if along_stream and path_spacing > 0:
        hover_every = max(1, int(round(hover_spacing / path_spacing)))

    hovers = []
    n_splash = len(splashes)
    for i, (slat, slon) in enumerate(splashes):
        if not along_stream:
            dlat, dlon, on_water = _snap_on_poly(slat, slon, slat, slon, poly)
            if not on_water:
                continue
        science = (not along_stream) or i == 0 or i == n_splash - 1 or (i % hover_every == 0)
        hovers.append(
            {
                "lat": slat,
                "lon": slon,
                "splash_lat": slat,
                "splash_lon": slon,
                "name": f"tile {i+1}" if science else f"leg {i+1}",
                "prn": prn,
                "el": el,
                "az": az,
                "t_in_window": 0,
                "drone_on_water": True,
                "score": el or 0,
                "fallback": True,
                "loiter_s": loiter_s if science else 0.0,
                "science": science,
            }
        )

    inch_n = max(1, int(area / (0.0254 * 0.0254)))
    slim = poly.simplify(2e-5, preserve_topology=True)
    if slim.is_empty:
        slim = poly
    info = {
        "tile_m": round(hover_spacing, 2),
        "path_m": round(path_spacing, 2),
        "wanted_m": round(wanted, 2),
        "fresnel_along_m": round(along, 2),
        "fresnel_across_m": round(across, 2),
        "coarsened": coarsened,
        "max_wp": max_wp,
        "area_m2": round(area, 0),
        "n_cells": len(hovers),
        "inch_wp": inch_n,
        "along_stream": along_stream,
        "centroid": {"lat": clat, "lon": clon},
        "outline": mapping(slim),
    }
    if along_stream and centerline is not None:
        info["stretch"] = mapping(centerline)
        info["stretch_m"] = round(line_length_m(centerline), 1)
    return hovers, info
