"""Fixed receiver on the shore: where reflections land on nearby lakes over a time window."""

from __future__ import annotations

import json
import math
import urllib.request
from datetime import datetime, timedelta, timezone

from engine.geometry import _water_index, az_el, haversine_m, lla_to_ecef, specular_point
from engine.planner import _visible, sky_snapshot

# Legion Park overlook I found from topographic map (updated to include LIDAR data and geoid height form NOAA api!)
LEGION_OVERLOOK = {
    "name": "Legion Park overlook",
    "lat": 40.01515,
    "lon": -105.19140,
    "ground_m": 1629.4,
    "antenna_m": 1.5,
    "water_m": 1591.3,
    "geoid_m": -16.37,
}



## LIDAR DATA AND GEOID HEIGHT FROM NOAA API
def usgs_elevation(lat: float, lon: float) -> float | None:
    """USGS 3DEP ground elevation (NAVD88 metres, lidar where available)."""
    url = (
        "https://epqs.nationalmap.gov/v1/json"
        f"?x={lon}&y={lat}&wkid=4326&units=Meters&includeDate=false"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "HERON-planner"})
    with urllib.request.urlopen(req, timeout=15) as r:
        value = float(json.load(r)["value"])
    return None if value < -1000 else value


def geoid_height(lat: float, lon: float) -> float | None:
    url = f"https://geodesy.noaa.gov/api/geoid/ght?lat={lat}&lon={lon}&model=14"
    req = urllib.request.Request(url, headers={"User-Agent": "HERON-planner"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return float(json.load(r)["geoidHeight"])


def nearby_water(lake: dict, lat: float, lon: float, max_m: float):
    """Polygon lakes, ponds, and reservoirs within max_m of the receiver. Rivers and ditches are left out."""
    from shapely.geometry import Point

    tree, geoms = _water_index(lake)
    here = Point(lon, lat)
    # fine the neares lakes
    metres_per_deg = 111132.92 * math.cos(math.radians(lat))
    found = []
    for i in tree.query(here.buffer(max_m / metres_per_deg)):
        geom, feat = geoms[int(i)]
        if feat["geometry"]["type"] not in ("Polygon", "MultiPolygon"):
            continue
        if geom.distance(here) * 111132.92 > max_m:
            continue
        props = feat.get("properties") or {}
        found.append((geom, props.get("name") or props.get("kind") or "water"))
    found.sort(key=lambda item: item[0].area)
    return found


def ground_reflections(
    sats,
    lake: dict,
    *,
    lat: float,
    lon: float,
    ground_m: float,
    antenna_m: float,
    water_m: float,
    geoid_m: float,
    start: datetime,
    end: datetime,
    step_s: int = 30,
    mask: float = 2.0,
    water_lat: float | None = None,
    water_lon: float | None = None,
) -> dict:
    from shapely.geometry import Point
    from shapely.prepared import prep

    h = ground_m + antenna_m - water_m
    if h < 1.0:
        raise ValueError("The antenna has to be above the water surface.")
    if end <= start:
        raise ValueError("End time must be after start time.")
    if (end - start) > timedelta(hours=12):
        raise ValueError("Keep the window to 12 hours or less.")
    if not 0.5 <= mask <= 60:
        raise ValueError("Elevation mask must be between 0.5° and 60°.")
    reach_m = min(6000.0, max(800.0, h / math.tan(math.radians(mask)) * 1.25))
    waters = nearby_water(lake, lat, lon, reach_m)
    if not waters:
        raise ValueError("No mapped lake within reach of the receiver.")
    prepared = [(prep(geom), geom, name) for geom, name in waters]
    water_h = water_m + geoid_m
    rx_h = water_h + h
    step = max(5, int(step_s))
    rx = lla_to_ecef(lat, lon, rx_h)

    points = []
    by_sat: dict[str, dict] = {}
    t = start
    while t <= end:
        for sat, xyz, _az, _el in _visible(sats, t, lat, lon, rx_h, mask):
            slat, slon, extra, _samples = specular_point(xyz, lat, lon, water_h, h, min_el=0.3)
            if extra != extra:
                continue
            splash = Point(slon, slat)
            water_name = next((name for pre, _geom, name in prepared if pre.contains(splash)), None)
            if water_name is None:
                continue
            az, el = az_el(xyz, rx, lat, lon)
            dist = haversine_m(lat, lon, slat, slon)
            row = {
                "utc": t.isoformat(),
                "sid": sat.sid,
                "constellation": sat.constellation,
                "el": round(el, 1),
                "az": round(az, 1),
                "lat": round(slat, 6),
                "lon": round(slon, 6),
                "dist_m": round(dist),
                "extra_m": round(extra, 1),
                "water": water_name,
            }
            points.append(row)
            s = by_sat.setdefault(sat.sid, {"sid": sat.sid, "constellation": sat.constellation, "n": 0,
                                            "first": row["utc"], "last": row["utc"],
                                            "el_min": el, "el_max": el, "dist_min": dist, "dist_max": dist,
                                            "az_first": az, "az_last": az, "waters": set()})
            s["n"] += 1
            s["waters"].add(water_name)
            s["last"] = row["utc"]
            s["az_last"] = az
            s["el_min"] = min(s["el_min"], el)
            s["el_max"] = max(s["el_max"], el)
            s["dist_min"] = min(s["dist_min"], dist)
            s["dist_max"] = max(s["dist_max"], dist)
        t += timedelta(seconds=step)

    sky = []
    sky_step = 120
    t = start
    while t <= end + timedelta(seconds=sky_step - 1):
        at = min(t, end)
        sky.append({"t": (at - start).total_seconds(), "sats": sky_snapshot(sats, at, lat, lon, rx_h, mask)})
        t += timedelta(seconds=sky_step)

    sats_out = []
    for s in sorted(by_sat.values(), key=lambda s: s["first"]):
        sats_out.append({
            "sid": s["sid"], "constellation": s["constellation"], "samples": s["n"],
            "first_utc": s["first"], "last_utc": s["last"],
            "minutes": round(s["n"] * step / 60.0, 1),
            "el_min": round(s["el_min"], 1), "el_max": round(s["el_max"], 1),
            "dist_min_m": round(s["dist_min"]), "dist_max_m": round(s["dist_max"]),
            "az_first": round(s["az_first"]), "az_last": round(s["az_last"]),
            "waters": sorted(s["waters"]),
        })
    times = sorted({p["utc"] for p in points})
    hit = []
    for name in dict.fromkeys(p["water"] for p in points):
        if name not in hit:
            hit.append(name)
    return {
        "receiver": {"lat": lat, "lon": lon, "ground_m": ground_m, "antenna_m": antenna_m,
                     "height_above_water_m": round(h, 2)},
        "water": {
            "name": ", ".join(hit) if hit else "nearby lakes",
            "names": hit,
            "surface_m": water_m,
            "geojson": {
                "type": "FeatureCollection",
                "features": [
                    {"type": "Feature", "properties": {"name": name}, "geometry": geom.__geo_interface__}
                    for geom, name in waters
                ],
            },
        },
        "start_utc": start.astimezone(timezone.utc).isoformat(),
        "end_utc": end.astimezone(timezone.utc).isoformat(),
        "step_s": step,
        "mask": mask,
        "points": points,
        "sats": sats_out,
        "sky": sky,
        "minutes_with_reflection": round(len(times) * step / 60.0, 1),
    }
