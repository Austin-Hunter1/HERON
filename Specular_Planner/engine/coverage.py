"""Swept first-Fresnel footprint: union of ellipses in time, not discrete stamps."""

from __future__ import annotations

import math

from shapely.geometry import Polygon, mapping
from shapely.ops import unary_union

from engine.geometry import offset_ll


def _lerp(a, b, u):
    return a + (b - a) * u


def _lerp_ang(a, b, u):
    d = ((b - a + 540.0) % 360.0) - 180.0
    return (a + d * u + 360.0) % 360.0


def _lerp_spec(a: dict, b: dict, u: float) -> dict:
    return {
        "prn": a["prn"],
        "lat": _lerp(a["lat"], b["lat"], u),
        "lon": _lerp(a["lon"], b["lon"], u),
        "az": _lerp_ang(a["az"], b["az"], u),
        "el": _lerp(a["el"], b["el"], u),
        "fresnel_along_m": _lerp(a["fresnel_along_m"], b["fresnel_along_m"], u),
        "fresnel_across_m": _lerp(a["fresnel_across_m"], b["fresnel_across_m"], u),
        "on_water": a["on_water"] if u < 0.5 else b["on_water"],
    }


def ellipse_poly(spec: dict, n: int = 28) -> Polygon:
    lat, lon = spec["lat"], spec["lon"]
    along, across = spec["fresnel_along_m"], spec["fresnel_across_m"]
    az = math.radians(spec["az"])
    pts = []
    for i in range(n):
        th = 2.0 * math.pi * i / n
        x = along * math.cos(th)
        y = across * math.sin(th)
        north = x * math.cos(az) - y * math.sin(az)
        east = x * math.sin(az) + y * math.cos(az)
        la, lo = offset_ll(lat, lon, north, east)
        pts.append((lo, la))
    pts.append(pts[0])
    poly = Polygon(pts)
    if not poly.is_valid:
        poly = poly.buffer(0)
    return poly


def _densify_run(run: list[dict], steps: int) -> list:
    if not run:
        return []
    out = [ellipse_poly(run[0])]
    for a, b in zip(run, run[1:]):
        for k in range(1, steps + 1):
            u = k / steps
            out.append(ellipse_poly(_lerp_spec(a, b, u)))
    return out


def _swept(polys: list) -> dict | None:
    if not polys:
        return None
    parts = [polys[0]]
    for a, b in zip(polys, polys[1:]):
        parts.append(unary_union([a, b]).convex_hull)
    geom = unary_union(parts)
    if geom.is_empty:
        return None
    if not geom.is_valid:
        geom = geom.buffer(0)
    geom = geom.simplify(8e-6, preserve_topology=True)
    if geom.is_empty:
        return None
    return mapping(geom)


def swept_coverage(frames: list[dict]) -> dict:
    """
    One merged footprint per PRN on lake and off lake.
    """
    tracks: dict[int, list[dict]] = {}
    n_lake = n_land = 0
    by_prn: dict[str, dict] = {}
    for fr in frames:
        for s in fr.get("speculars") or []:
            prn = s["prn"]
            tracks.setdefault(prn, []).append(s)
            key = str(prn)
            by_prn.setdefault(key, {"lake": 0, "land": 0})
            if s.get("on_water"):
                n_lake += 1
                by_prn[key]["lake"] += 1
            else:
                n_land += 1
                by_prn[key]["land"] += 1

    lake: dict[str, dict] = {}
    land: dict[str, dict] = {}
    for prn, seq in tracks.items():
        lake_runs: list[list[dict]] = []
        land_runs: list[list[dict]] = []
        cur = []
        cur_water = None
        for s in seq:
            w = bool(s.get("on_water"))
            if cur and w != cur_water:
                (lake_runs if cur_water else land_runs).append(cur)
                cur = []
            cur.append(s)
            cur_water = w
        if cur:
            (lake_runs if cur_water else land_runs).append(cur)

        lake_polys = []
        land_polys = []
        for run in lake_runs:
            lake_polys.extend(_densify_run(run, steps=6))
        for run in land_runs:
            land_polys.extend(_densify_run(run, steps=6))
        g_lake = _swept(lake_polys)
        g_land = _swept(land_polys)
        if g_lake:
            lake[str(prn)] = g_lake
        if g_land:
            land[str(prn)] = g_land

    return {
        "lake": lake,
        "land": land,
        "counts": {"lake": n_lake, "land": n_land, "by_prn": by_prn},
    }
