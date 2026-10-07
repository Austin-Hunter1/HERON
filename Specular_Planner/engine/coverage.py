"""Swept first-Fresnel footprint: union of ellipses in time, not discrete stamps."""

from __future__ import annotations

import math

from shapely.geometry import Polygon, mapping
from shapely.ops import unary_union

from engine.geometry import offset_ll, water_near


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


def _swept(polys: list):
    """One continuous strip through consecutive ellipses (hull of each neighbour pair)."""
    if not polys:
        return None
    parts = [polys[0]]
    for a, b in zip(polys, polys[1:]):
        parts.append(unary_union([a, b]).convex_hull)
    geom = unary_union(parts)
    if not geom.is_valid:
        geom = geom.buffer(0)
    return None if geom.is_empty else geom


def _tidy(geom):
    if geom is None or geom.is_empty:
        return None
    geom = geom.simplify(8e-6, preserve_topology=True)
    if geom.is_empty or geom.geom_type not in ("Polygon", "MultiPolygon", "GeometryCollection"):
        return None
    if geom.geom_type == "GeometryCollection":
        polys = [g for g in geom.geoms if g.geom_type in ("Polygon", "MultiPolygon")]
        if not polys:
            return None
        geom = unary_union(polys)
    return mapping(geom)


def _split_on_gaps(seq: list[dict], key=None) -> list[list[dict]]:
    """Break a PRN's samples where a frame is missing or ``key`` changes value."""
    runs: list[list[dict]] = []
    cur: list[dict] = []
    for s in seq:
        if cur and (s["frame_index"] != cur[-1]["frame_index"] + 1
                    or (key is not None and key(s) != key(cur[-1]))):
            runs.append(cur)
            cur = []
        cur.append(s)
    if cur:
        runs.append(cur)
    return runs


def _union_runs(runs):
    geoms = [_swept(_densify_run(run, steps=2)) for run in runs]
    geoms = [g for g in geoms if g is not None]
    return unary_union(geoms) if geoms else None


def swept_coverage(frames: list[dict], targets=None, water=None) -> dict:
    """
    One merged footprint per PRN on water and off water.

    With ``water`` (the mapped-water GeoJSON) each PRN is swept as one
    continuous track and cut at the real shoreline, so a footprint that
    crosses the shore is split there instead of leaving a gap between the
    last wet sample and the first dry one. Without it, samples are grouped
    by their centre's ``on_water`` flag.
    """
    tracks: dict[str, list[dict]] = {}
    n_lake = n_land = 0
    by_prn: dict[str, dict] = {}
    for frame_index, fr in enumerate(frames):
        for s in fr.get("speculars") or []:
            s = dict(s, frame_index=frame_index)
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
        if water is not None:
            track = _union_runs(_split_on_gaps(seq))
            if track is None:
                continue
            wet_area = water_near(water, track)
            g_lake = _tidy(track.intersection(wet_area))
            g_land = _tidy(track.difference(wet_area))
        else:
            runs = _split_on_gaps(seq, key=lambda s: bool(s.get("on_water")))
            g_lake = _tidy(_union_runs([r for r in runs if r[0].get("on_water")]))
            g_land = _tidy(_union_runs([r for r in runs if not r[0].get("on_water")]))
        if g_lake:
            lake[str(prn)] = g_lake
        if g_land:
            land[str(prn)] = g_land

    result = {
        "lake": lake,
        "land": land,
        "counts": {"lake": n_lake, "land": n_land, "by_prn": by_prn},
    }
    if targets:
        area = unary_union([Polygon(t["coordinates"]) for t in targets])
        patches = []
        for a, b in zip(frames, frames[1:]):
            if not a.get("measuring"):
                continue
            chosen = next((s for s in a["speculars"] if s.get("aimed")), None)
            end = next((s for s in b["speculars"] if chosen and s["prn"] == chosen["prn"]), None)
            if chosen and end:
                g = _swept(_densify_run([chosen, end], steps=2))
                if g is not None:
                    patches.append(g)
        covered = unary_union(patches).intersection(area) if patches else Polygon()
        missing = area.difference(covered)
        result["target"] = {"percent": round(100 * covered.area / area.area, 2) if area.area else 0,
                            "uncovered": mapping(missing),
                            "basis": "Selected-satellite first-Fresnel footprint during survey passes; geometric estimate, not received signal."}
    return result
