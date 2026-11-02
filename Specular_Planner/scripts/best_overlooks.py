#!/usr/bin/env python3
"""Rank shore spots by the highest satellite elevation that still reflects onto a big lake.

A satellite at elevation el reflects at distance h / tan(el), where h is how far the
antenna is above the water. The best spot is high ground close to a lake wide enough
that the bounce lands on open water, not on the bank.

Heights come from USGS 3DEP (lidar, meters above sea level). The geoid cancels
because both the ground and the water are shifted by the same amount.

    .venv/bin/python scripts/best_overlooks.py
"""

from __future__ import annotations

import json
import math
import urllib.request

import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from shapely.geometry import LineString, Point, shape
from shapely.ops import nearest_points, transform

ROOT = Path(__file__).resolve().parents[1]
LAKES = ROOT / "data" / "waterbodies.geojson"
CACHE = ROOT / "data" / "overlook_elev_cache.json"
OUT = ROOT / "data" / "best_overlooks.json"

MIN_HA = 10.0
INSET_M = 40.0
ANTENNA_M = 1.5
MIN_HEIGHT_M = 12.0
BEARINGS = 12
OFFSETS_M = (40, 120, 280)
USGS = "https://epqs.nationalmap.gov/v1/json?x={lon}&y={lat}&wkid=4326&units=Meters&includeDate=false"


def dest(lat: float, lon: float, az_deg: float, dist_m: float) -> tuple[float, float]:
    r = 6371000.0
    az, la, lo = math.radians(az_deg), math.radians(lat), math.radians(lon)
    dr = dist_m / r
    la2 = math.asin(math.sin(la) * math.cos(dr) + math.cos(la) * math.sin(dr) * math.cos(az))
    lo2 = lo + math.atan2(
        math.sin(az) * math.sin(dr) * math.cos(la),
        math.cos(dr) - math.sin(la) * math.sin(la2),
    )
    return math.degrees(la2), math.degrees(lo2)


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def usgs(lat: float, lon: float, cache: dict) -> float | None:
    key = f"{lat:.5f},{lon:.5f}"
    if key in cache:
        return cache[key]
    url = USGS.format(lat=lat, lon=lon)
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "HERON-planner"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                value = float(json.load(resp)["value"])
            cache[key] = None if value < -1000 else value
            return cache[key]
        except Exception:
            continue
    cache[key] = None
    return None


def local_meters(geom, lat0, lon0):
    mlon = 111320.0 * math.cos(math.radians(lat0))
    mlat = 111132.0
    return transform(lambda x, y: ((x - lon0) * mlon, (y - lat0) * mlat), geom)


def from_meters(x, y, lat0, lon0) -> tuple[float, float]:
    mlon = 111320.0 * math.cos(math.radians(lat0))
    return lat0 + y / 111132.0, lon0 + x / mlon


def load_lakes() -> list[dict]:
    raw = json.loads(LAKES.read_text())
    rows = []
    for feat in raw["features"]:
        geom = shape(feat["geometry"])
        if geom.geom_type not in ("Polygon", "MultiPolygon"):
            continue
        c = geom.centroid
        area = geom.area * 111320.0 * math.cos(math.radians(c.y)) * 111132.0
        if area < MIN_HA * 1e4:
            continue
        props = feat.get("properties") or {}
        name = props.get("name") or "unnamed"
        rows.append({"name": name, "kind": props.get("kind") or "water", "ha": area / 1e4, "geom": geom, "lat": c.y, "lon": c.x})
    rows.sort(key=lambda r: r["ha"], reverse=True)
    kept = []
    for row in rows:
        if any(haversine_m(row["lat"], row["lon"], k["lat"], k["lon"]) < 80 and k["name"] != "unnamed" for k in kept):
            continue
        if row["name"] == "unnamed" and any(haversine_m(row["lat"], row["lon"], k["lat"], k["lon"]) < 80 for k in kept):
            continue
        kept.append(row)
    return kept


def candidates(geom) -> list[tuple[float, float]]:
    origin = geom.centroid if geom.contains(geom.centroid) else geom.representative_point()
    out = []
    for i in range(BEARINGS):
        az = i * (360.0 / BEARINGS)
        far = dest(origin.y, origin.x, az, 8000)
        line = LineString([(origin.x, origin.y), (far[1], far[0])])
        hit = line.intersection(geom.boundary)
        if hit.is_empty:
            continue
        pts = list(hit.geoms) if hit.geom_type.startswith("Multi") or hit.geom_type == "GeometryCollection" else [hit]
        coords = []
        for p in pts:
            if p.geom_type == "Point":
                coords.append((p.y, p.x))
            elif p.geom_type == "LineString":
                coords.append((p.coords[0][1], p.coords[0][0]))
        if not coords:
            continue
        shore = max(coords, key=lambda p: haversine_m(origin.y, origin.x, p[0], p[1]))
        for off in OFFSETS_M:
            lat, lon = dest(shore[0], shore[1], az, off)
            if geom.contains(Point(lon, lat)):
                continue
            out.append((lat, lon))
    return out


def score_point(lat, lon, ground, water_h, geom_m, inset, lat0, lon0) -> dict | None:
    h = ground + ANTENNA_M - water_h
    if h < MIN_HEIGHT_M or inset.is_empty:
        return None
    stand = local_meters(Point(lon, lat), lat0, lon0)
    if geom_m.contains(stand):
        return None
    near = nearest_points(stand, inset)[1]
    d_near = stand.distance(near)
    if d_near < 15:
        return None
    el_max = math.degrees(math.atan(h / d_near))
    direction = math.degrees(math.atan2(near.x - stand.x, near.y - stand.y))
    far_lat, far_lon = dest(lat, lon, direction, 4000)
    ray = local_meters(LineString([(lon, lat), (far_lon, far_lat)]), lat0, lon0)
    crossed = ray.intersection(geom_m)
    d_far = crossed.distance(stand) + (crossed.length if not crossed.is_empty else 0)
    # length of the ray inside the lake is the water run past the near edge
    inside = ray.intersection(geom_m)
    run = inside.length if not inside.is_empty else 0
    if run < 80:
        return None
    d_exit = d_near + run
    el_min = math.degrees(math.atan(h / d_exit))
    nlat, nlon = from_meters(near.x, near.y, lat0, lon0)
    return {
        "lat": round(lat, 5),
        "lon": round(lon, 5),
        "ground_m": round(ground, 1),
        "height_above_water_m": round(h, 1),
        "el_max_deg": round(el_max, 1),
        "el_min_deg": round(el_min, 1),
        "near_m": round(d_near),
        "run_m": round(run),
        "aim_lat": round(nlat, 5),
        "aim_lon": round(nlon, 5),
    }


def main() -> None:
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    lakes = load_lakes()
    print(f"{len(lakes)} lakes at least {MIN_HA:.0f} ha")
    jobs = []
    for lake in lakes:
        water = lake["geom"].representative_point()
        jobs.append(("water", lake["name"], water.y, water.x))
        for lat, lon in candidates(lake["geom"]):
            jobs.append(("stand", lake["name"], lat, lon))
    pending = [(k, n, la, lo) for k, n, la, lo in jobs if f"{la:.5f},{lo:.5f}" not in cache]
    print(f"{len(jobs)} points, {len(pending)} new USGS lookups")
    with ThreadPoolExecutor(16) as pool:
        futs = {pool.submit(usgs, la, lo, cache): (la, lo) for _k, _n, la, lo in pending}
        done = 0
        for fut in as_completed(futs):
            fut.result()
            done += 1
            if done % 40 == 0:
                CACHE.write_text(json.dumps(cache))
                print(f"  {done}/{len(pending)}")
    CACHE.write_text(json.dumps(cache))

    ranked = []
    for lake in lakes:
        water = lake["geom"].representative_point()
        water_h = usgs(water.y, water.x, cache)
        if water_h is None:
            continue
        lat0, lon0 = lake["lat"], lake["lon"]
        geom_m = local_meters(lake["geom"], lat0, lon0)
        inset = geom_m.buffer(-INSET_M)
        if inset.is_empty:
            continue
        best = None
        for lat, lon in candidates(lake["geom"]):
            ground = usgs(lat, lon, cache)
            if ground is None:
                continue
            row = score_point(lat, lon, ground, water_h, geom_m, inset, lat0, lon0)
            if row and (best is None or row["el_max_deg"] > best["el_max_deg"]):
                best = row
        if best:
            best.update({"lake": lake["name"], "ha": round(lake["ha"], 1), "water_m": round(water_h, 1), "lake_lat": round(lat0, 5), "lake_lon": round(lon0, 5)})
            ranked.append(best)
            print(f"{best['el_max_deg']:5.1f}°  {best['height_above_water_m']:6.1f} m above  {lake['name']} ({lake['ha']:.0f} ha)")
    ranked.sort(key=lambda r: r["el_max_deg"], reverse=True)
    _meta, peaks = build_angle_layer(ranked)
    ranked = apply_peaks(ranked, peaks)
    OUT.write_text(json.dumps(ranked, indent=2) + "\n")
    print(f"wrote {OUT} ({len(ranked)} sites)")
    write_map(ranked)


MAP = ROOT / "data" / "best_overlooks.html"
DEM = ROOT / "data" / "dem_boulder.tif"
LAYER = ROOT / "data" / "angle_layer.png"
TERRAIN = ROOT / "data" / "terrain_layer.png"
LAYER_META = ROOT / "data" / "angle_layer.json"


def _tiff_value(blob: bytes, endian: str, typ: int, cnt: int, val: int):
    import struct

    size = {3: 2, 4: 4, 12: 8}[typ]
    fmt = {3: "H", 4: "I", 12: "d"}[typ]
    if size * cnt <= 4 and typ != 12:
        raw = struct.pack(endian + "I", val)[: size * cnt]
        return struct.unpack(endian + fmt * cnt, raw)
    raw = blob[val : val + size * cnt]
    return struct.unpack(endian + fmt * cnt, raw)


def read_dem_tiff(path: Path) -> tuple[np.ndarray, float, float, float]:
    """Uncompressed tiled float32 DEM. Returns (elev, west, north, degrees_per_pixel)."""
    import struct

    blob = path.read_bytes()
    endian = "<" if blob[:2] == b"II" else ">"
    ntags = struct.unpack(endian + "H", blob[8:10])[0]
    tags = {}
    p = 10
    for _ in range(ntags):
        tag, typ, cnt, val = struct.unpack(endian + "HHII", blob[p : p + 12])
        tags[tag] = (typ, cnt, val)
        p += 12
    width = _tiff_value(blob, endian, *tags[256])[0]
    height = _tiff_value(blob, endian, *tags[257])[0]
    tw = _tiff_value(blob, endian, *tags[322])[0]
    th = _tiff_value(blob, endian, *tags[323])[0]
    offsets = _tiff_value(blob, endian, *tags[324])
    counts = _tiff_value(blob, endian, *tags[325])
    elev = np.zeros((height, width), dtype=np.float32)
    tiles_x = (width + tw - 1) // tw
    for i, (off, nbytes) in enumerate(zip(offsets, counts)):
        tile = np.frombuffer(blob[off : off + nbytes], dtype="<f4").reshape(th, tw)
        r, c = divmod(i, tiles_x)
        elev[r * th : (r + 1) * th, c * tw : (c + 1) * tw] = tile[: min(th, height - r * th), : min(tw, width - c * tw)]
    scale = _tiff_value(blob, endian, *tags[33550])
    tie = _tiff_value(blob, endian, *tags[33922])
    return elev[:height, :width], float(tie[3]), float(tie[4]), float(scale[0]), float(scale[1])


def fetch_dem(west, south, east, north) -> tuple[np.ndarray, float, float, float]:
    span = max(east - west, north - south)
    pixels = int(span / 0.00065)
    url = (
        "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage"
        f"?bbox={west},{south},{east},{north}&bboxSR=4326&imageSR=4326&size={pixels},{pixels}"
        "&format=tiff&pixelType=F32&f=image"
    )
    if DEM.exists() and DEM.stat().st_size > 1000:
        return read_dem_tiff(DEM)
    print(f"downloading DEM {pixels}px …")
    req = urllib.request.Request(url, headers={"User-Agent": "HERON-planner"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        DEM.write_bytes(resp.read())
    return read_dem_tiff(DEM)


def write_png(path: Path, rgba: np.ndarray) -> None:
    import struct
    import zlib

    height, width, _ = rgba.shape
    raw = b"".join(b"\x00" + rgba[i].tobytes() for i in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


ANGLE_STOPS = (
    (1, (255, 236, 40)),
    (2, (255, 176, 0)),
    (4, (255, 90, 20)),
    (7, (230, 30, 80)),
    (11, (170, 30, 230)),
    (16, (40, 90, 255)),
    (22, (0, 190, 200)),
    (30, (80, 230, 70)),
)
TERRAIN_STOPS = (
    (1500, (18, 70, 45)),
    (1580, (50, 150, 70)),
    (1660, (190, 210, 50)),
    (1760, (245, 170, 30)),
    (1900, (220, 70, 40)),
    (2100, (150, 50, 140)),
    (2350, (245, 240, 250)),
)


def _ramp(values: np.ndarray, stops: tuple, alpha: int) -> np.ndarray:
    rgba = np.zeros(values.shape + (4,), dtype=np.uint8)
    show = np.isfinite(values)
    if not show.any():
        return rgba
    xs = np.array([s[0] for s in stops], dtype=np.float32)
    cols = np.array([s[1] for s in stops], dtype=np.float32)
    v = np.clip(values[show], xs[0], xs[-1])
    idx = np.clip(np.searchsorted(xs, v, side="right") - 1, 0, len(xs) - 2)
    u = (v - xs[idx]) / np.maximum(xs[idx + 1] - xs[idx], 1e-6)
    rgb = cols[idx] + (cols[idx + 1] - cols[idx]) * u[:, None]
    rgba[show, :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    rgba[show, 3] = alpha
    return rgba


def _outline(rgba: np.ndarray) -> np.ndarray:
    """A white edge so each colored patch reads against the satellite photo."""
    on = rgba[..., 3] > 0
    edge = np.zeros(on.shape, dtype=bool)
    edge[1:, :] |= on[:-1, :]
    edge[:-1, :] |= on[1:, :]
    edge[:, 1:] |= on[:, :-1]
    edge[:, :-1] |= on[:, 1:]
    edge &= ~on
    rgba[edge] = (255, 255, 255, 230)
    return rgba


def color_angles(el: np.ndarray) -> np.ndarray:
    """Spread the usual 1–10° ground across several colors. Green is a high satellite."""
    return _outline(_ramp(np.where(el >= 1.0, el, np.nan), ANGLE_STOPS, 235))


def color_terrain(elev: np.ndarray) -> np.ndarray:
    return _ramp(elev, TERRAIN_STOPS, 210)


def _peaks(lake_geoms, owner, best_el, elev, lat, lon, xs, ys, west, south, mlon) -> list[dict]:
    """The brightest cell of each lake: highest satellite that still reflects on its water."""
    peaks = []
    for lake_i, (lake, wh, geom_m, inset) in enumerate(lake_geoms):
        mine = owner == lake_i
        if not mine.any():
            continue
        scores = np.where(mine, best_el, -np.inf)
        idx = int(np.nanargmax(scores))
        if not np.isfinite(best_el.ravel()[idx]):
            continue
        px, py = float(xs.ravel()[idx]), float(ys.ravel()[idx])
        stand = Point(px, py)
        near_pt = nearest_points(stand, inset)[1]
        d_near = stand.distance(near_pt)
        h = float(elev.ravel()[idx]) + ANTENNA_M - wh
        direction = math.degrees(math.atan2(near_pt.x - stand.x, near_pt.y - stand.y))
        ray = LineString([
            (px, py),
            (px + math.sin(math.radians(direction)) * 4000, py + math.cos(math.radians(direction)) * 4000),
        ])
        inside = ray.intersection(geom_m)
        run = inside.length if not inside.is_empty else 0
        d_exit = d_near + run
        peaks.append({
            "lake": lake["name"],
            "lake_lat": round(lake["lat"], 5),
            "lake_lon": round(lake["lon"], 5),
            "lat": round(float(lat.ravel()[idx]), 5),
            "lon": round(float(lon.ravel()[idx]), 5),
            "ground_m": round(float(elev.ravel()[idx]), 1),
            "height_above_water_m": round(h, 1),
            "el_max_deg": round(float(best_el.ravel()[idx]), 1),
            "el_min_deg": round(math.degrees(math.atan(h / d_exit)), 1) if d_exit > 1 else None,
            "near_m": round(d_near),
            "run_m": round(run),
            "aim_lat": round(south + near_pt.y / 111132.0, 5),
            "aim_lon": round(west + near_pt.x / mlon, 5),
        })
        print(f"  peak {peaks[-1]['el_max_deg']:5.1f}°  {peaks[-1]['lat']}, {peaks[-1]['lon']}  {lake['name']}")
    return peaks


def build_angle_layer(ranked: list[dict]) -> tuple[dict, list[dict]]:
    """Color every land pixel near a big lake by the highest satellite that still reflects there."""
    from shapely import contains_xy, distance, points
    from shapely.ops import transform

    lakes = load_lakes()
    water = {(r["lake"], round(r["lake_lat"], 3), round(r["lake_lon"], 3)): r["water_m"] for r in ranked}
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    prepared = []
    for lake in lakes:
        key = (lake["name"], round(lake["lat"], 3), round(lake["lon"], 3))
        wh = water.get(key)
        if wh is None:
            pt = lake["geom"].representative_point()
            wh = usgs(pt.y, pt.x, cache)
        if wh is None:
            continue
        prepared.append((lake, wh))
    minx = min(l["geom"].bounds[0] for l, _ in prepared) - 0.015
    miny = min(l["geom"].bounds[1] for l, _ in prepared) - 0.015
    maxx = max(l["geom"].bounds[2] for l, _ in prepared) + 0.015
    maxy = max(l["geom"].bounds[3] for l, _ in prepared) + 0.015
    elev, west, north, step_x, step_y = fetch_dem(minx, miny, maxx, maxy)
    elev = elev.copy()
    elev[elev < 500] = np.nan
    rows, cols = elev.shape
    south = north - rows * step_y
    east = west + cols * step_x
    row_i, col_i = np.indices(elev.shape)
    lat = north - (row_i + 0.5) * step_y
    lon = west + (col_i + 0.5) * step_x
    lat0 = float((north + south) / 2)
    mlon = 111320.0 * math.cos(math.radians(lat0))
    xs = (lon - west) * mlon
    ys = (lat - south) * 111132.0
    best_el = np.full(elev.shape, np.nan, dtype=np.float32)
    owner = np.full(elev.shape, -1, dtype=np.int16)
    on_water = np.zeros(elev.shape, dtype=bool)
    lake_geoms = []
    reach = 1600.0
    for lake, wh in prepared:
        geom_m = transform(
            lambda x, y, z=None: ((x - west) * mlon, (y - south) * 111132.0),
            lake["geom"],
        )
        inset = geom_m.buffer(-INSET_M)
        if inset.is_empty:
            continue
        minx_m, miny_m, maxx_m, maxy_m = geom_m.bounds
        near = (
            (xs > minx_m - reach)
            & (xs < maxx_m + reach)
            & (ys > miny_m - reach)
            & (ys < maxy_m + reach)
            & np.isfinite(elev)
        )
        if not near.any():
            continue
        lake_i = len(lake_geoms)
        lake_geoms.append((lake, wh, geom_m, inset))
        flat = np.flatnonzero(near)
        d = np.empty(flat.size, dtype=np.float64)
        chunk = 8000
        for start in range(0, flat.size, chunk):
            sel = flat[start : start + chunk]
            d[start : start + chunk] = distance(inset, points(xs.ravel()[sel], ys.ravel()[sel]))
        h = elev.ravel()[flat] + ANTENNA_M - wh
        el = np.degrees(np.arctan2(h, np.maximum(d, 1.0)))
        el[(h < 2) | (d > reach) | (d < 15)] = np.nan
        current = best_el.ravel()
        better = np.isfinite(el) & (
            ~np.isfinite(current[flat]) | (el > np.nan_to_num(current[flat], nan=-1e9))
        )
        current[flat[better]] = el[better]
        owner.ravel()[flat[better]] = lake_i
        on_water.ravel()[flat] |= contains_xy(lake["geom"], lon.ravel()[flat], lat.ravel()[flat])
        print(f"  {lake['name']}: {int(np.isfinite(el).sum())} land pixels")
    best_el[on_water] = np.nan
    owner[on_water] = -1
    peaks = _peaks(lake_geoms, owner, best_el, elev, lat, lon, xs, ys, west, south, mlon)
    write_png(LAYER, color_angles(best_el))
    write_png(TERRAIN, color_terrain(elev))
    import base64

    coded = np.clip(np.nan_to_num(best_el, nan=0) * 4, 0, 255).astype(np.uint8)
    heights = np.clip(np.rint(np.nan_to_num(elev, nan=0)), 0, 65535).astype("<u2")
    meta = {
        "south": south,
        "west": west,
        "north": north,
        "east": east,
        "rows": int(rows),
        "cols": int(cols),
        "samples": base64.b64encode(coded.tobytes()).decode(),
        "heights": base64.b64encode(heights.tobytes()).decode(),
    }
    LAYER_META.write_text(json.dumps(meta))
    print(f"wrote {LAYER} ({int(np.isfinite(best_el).sum())} colored pixels)")
    print(f"wrote {TERRAIN}")
    return meta, peaks


def apply_peaks(ranked: list[dict], peaks: list[dict]) -> list[dict]:
    """Replace each lake's sampled shore point with the brightest cell on the angle layer."""
    by = {(p["lake"], round(p["lake_lat"], 3), round(p["lake_lon"], 3)): p for p in peaks}
    out = []
    for row in ranked:
        key = (row["lake"], round(row["lake_lat"], 3), round(row["lake_lon"], 3))
        peak = by.get(key)
        if peak is None:
            out.append(row)
            continue
        merged = dict(row)
        merged.update({k: peak[k] for k in (
            "lat", "lon", "ground_m", "height_above_water_m", "el_max_deg",
            "el_min_deg", "near_m", "run_m", "aim_lat", "aim_lon",
        )})
        out.append(merged)
    out.sort(key=lambda r: r["el_max_deg"], reverse=True)
    return out


def _data_png(path: Path) -> str:
    import base64

    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def write_map(ranked: list[dict]) -> None:
    """One HTML file: lake list, color layers, and the map code. Open data/best_overlooks.html."""
    payload = json.dumps(ranked)
    meta = json.loads(LAYER_META.read_text()) if LAYER_META.exists() else {}
    vendor = ROOT / "static" / "vendor" / "leaflet"
    leaflet_css = (vendor / "leaflet.css").read_text()
    leaflet_js = (vendor / "leaflet.js").read_text()
    layer = json.dumps(
        {k: meta[k] for k in ("south", "west", "north", "east", "rows", "cols") if k in meta}
        | {k: meta[k] for k in ("samples", "heights") if meta.get(k)}
    )
    page = (
        """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Best reflection overlooks</title>
<style>
/*LEAFLET_CSS*/
</style>
<style>"""
    )
    page += """
  html, body { margin: 0; height: 100%; background: #121614; color: #e7eee9; font: 14px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
  #map { position: absolute; inset: 0; }
  #panel { position: absolute; z-index: 500; top: 16px; left: 16px; width: 320px; max-height: calc(100% - 32px); overflow: auto; background: rgba(18, 22, 20, 0.92); border: 1px solid rgba(255,255,255,0.12); border-radius: 12px; }
  #panel h1 { margin: 0; padding: 14px 16px 0; font-size: 16px; }
  #panel p { margin: 6px 16px 12px; color: #b7c4bb; font-size: 12px; }
  button.row { display: grid; grid-template-columns: 36px 1fr auto; gap: 8px; width: 100%; padding: 8px 16px; border: 0; border-top: 1px solid rgba(255,255,255,0.08); background: transparent; color: inherit; text-align: left; cursor: pointer; }
  button.row.on { background: rgba(255,255,255,0.06); }
  button.row b { font-variant-numeric: tabular-nums; }
  button.row span { color: #b7c4bb; font-size: 12px; }
  .swatch { width: 14px; height: 14px; margin-top: 3px; border-radius: 50%; border: 2px solid #fff; }
  .modes { display: flex; gap: 6px; padding: 0 16px 12px; }
  .modes button { flex: 1; padding: 6px 0; border: 1px solid rgba(255,255,255,0.16); border-radius: 8px; background: transparent; color: #e7eee9; cursor: pointer; }
  .modes button.on { background: #fff; color: #121614; }
  .legend { display: flex; align-items: center; gap: 8px; padding: 0 16px 8px; color: #b7c4bb; font-size: 11px; }
  .legend .bar { flex: 1; height: 10px; border-radius: 5px; }
  .legend .angle { background: linear-gradient(90deg, #ffec28, #ffb000 8%, #ff5a14 18%, #e61e50 32%, #aa1ee6 48%, #285aff 66%, #00bec8 82%, #50e646); }
  .legend .terrain { background: linear-gradient(90deg, #12462d, #329646 14%, #bed232 32%, #f5aa1e 48%, #dc4628 66%, #96328c 84%, #f5f0fa); }
  #readout { position: absolute; z-index: 600; display: none; pointer-events: none; padding: 8px 10px; background: rgba(18, 22, 20, 0.94); border: 1px solid rgba(255,255,255,0.18); border-radius: 8px; font-size: 13px; line-height: 1.35; white-space: nowrap; }
  #readout b { font-variant-numeric: tabular-nums; }
</style>
</head>
<body>
<div id="map"></div>
<div id="readout"></div>
<aside id="panel">
  <h1>Best reflection overlooks</h1>
  <p>Bright color is the highest satellite that still reflects onto a nearby lake. The terrain layer is the ground height everywhere. Hover anywhere to read it.</p>
  <div class="modes"><button type="button" id="showBoth" class="on">Both</button><button type="button" id="showAngle">Angles</button><button type="button" id="showTerrain">Terrain</button></div>
  <div class="legend"><span>1°</span><span class="bar angle"></span><span>30°</span></div>
  <div class="legend"><span>1500 m</span><span class="bar terrain"></span><span>2350 m</span></div>
  <div id="list"></div>
</aside>
<script>
/*LEAFLET_JS*/
</script>
<script>
const spots = __SPOTS__;
function color(el) {
  const stops = [[1,"#ffec28"],[2,"#ffb000"],[4,"#ff5a14"],[7,"#e61e50"],[11,"#aa1ee6"],[16,"#285aff"],[22,"#00bec8"],[30,"#50e646"]];
  if (el <= stops[0][0]) return stops[0][1];
  for (let i = 1; i < stops.length; i++) {
    if (el <= stops[i][0]) return stops[i][1];
  }
  return stops[stops.length - 1][1];
}
const map = L.map("map", { zoomControl: false }).setView([40.08, -105.24], 11);
L.control.zoom({ position: "bottomright" }).addTo(map);
L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", { maxZoom: 19, attribution: "Esri" }).addTo(map);
const layer = __LAYER__;
const bounds = layer.west ? [[layer.south, layer.west], [layer.north, layer.east]] : null;
const terrainOverlay = bounds ? L.imageOverlay(__TERRAIN__, bounds, { opacity: 0.82 }) : null;
const angleOverlay = bounds ? L.imageOverlay(__ANGLE__, bounds, { opacity: 1 }) : null;
if (bounds) {
  terrainOverlay.addTo(map);
  angleOverlay.addTo(map);
  map.fitBounds(bounds, { paddingTopLeft: [340, 20], paddingBottomRight: [20, 20] });
}
function showLayer(which) {
  ["showBoth", "showAngle", "showTerrain"].forEach((id) => document.getElementById(id).classList.toggle("on", id === which));
  if (!bounds) return;
  if (map.hasLayer(terrainOverlay)) map.removeLayer(terrainOverlay);
  if (map.hasLayer(angleOverlay)) map.removeLayer(angleOverlay);
  if (which !== "showAngle") terrainOverlay.addTo(map);
  if (which !== "showTerrain") angleOverlay.addTo(map);
}
document.getElementById("showBoth").onclick = () => showLayer("showBoth");
document.getElementById("showAngle").onclick = () => showLayer("showAngle");
document.getElementById("showTerrain").onclick = () => showLayer("showTerrain");
const angleBytes = layer.samples ? Uint8Array.from(atob(layer.samples), c => c.charCodeAt(0)) : null;
const heightBytes = layer.heights ? Uint8Array.from(atob(layer.heights), c => c.charCodeAt(0)) : null;
const readout = document.getElementById("readout");
function cellAt(latlng) {
  if (!layer.west) return null;
  const c = Math.floor((latlng.lng - layer.west) / ((layer.east - layer.west) / layer.cols));
  const r = Math.floor((layer.north - latlng.lat) / ((layer.north - layer.south) / layer.rows));
  if (c < 0 || r < 0 || c >= layer.cols || r >= layer.rows) return null;
  return r * layer.cols + c;
}
map.on("mousemove", (e) => {
  const i = cellAt(e.latlng);
  if (i == null) { readout.style.display = "none"; return; }
  const h = heightBytes ? (heightBytes[i * 2] | (heightBytes[i * 2 + 1] << 8)) : 0;
  const deg = angleBytes ? angleBytes[i] / 4 : 0;
  if (h < 500 && deg < 1) { readout.style.display = "none"; return; }
  const lines = [];
  if (h >= 500) lines.push("<b>" + h + " m</b> terrain");
  if (deg >= 1) lines.push("<b>" + deg.toFixed(1) + "°</b> satellite");
  readout.innerHTML = lines.join("<br>");
  const x = Math.min(e.containerPoint.x + 16, map.getSize().x - 170);
  const y = Math.min(e.containerPoint.y + 16, map.getSize().y - 64);
  readout.style.left = x + "px";
  readout.style.top = y + "px";
  readout.style.display = "block";
});
map.on("mouseout", () => { readout.style.display = "none"; });
const list = document.getElementById("list");
const markers = spots.map((s, i) => {
  const marker = L.circleMarker([s.lat, s.lon], { radius: 4, color: "#fff", weight: 1, fillColor: color(s.el_max_deg), fillOpacity: 0.95 }).addTo(map);
  marker.bindPopup(`<b>${s.lake}</b><br>${s.el_max_deg}° highest satellite<br><b>${s.lat.toFixed(5)}, ${s.lon.toFixed(5)}</b><br>Aim ${s.aim_lat.toFixed(5)}, ${s.aim_lon.toFixed(5)}<br>${s.height_above_water_m} m above the water<br>Ground ${s.ground_m} m, water ${s.water_m} m<br>Open water from ${s.near_m} m to ${s.near_m + s.run_m} m`);
  const btn = document.createElement("button");
  btn.className = "row";
  btn.innerHTML = `<i class="swatch" style="background:${color(s.el_max_deg)}"></i><span><b>${s.lake}</b><br>${s.lat.toFixed(5)}, ${s.lon.toFixed(5)}<br>${s.height_above_water_m} m above water · ground ${s.ground_m} m</span><b>${s.el_max_deg}°</b>`;
  btn.onclick = () => { spots.forEach((_, j) => list.children[j].classList.remove("on")); btn.classList.add("on"); map.fitBounds([[s.lat, s.lon], [s.aim_lat, s.aim_lon]], { paddingTopLeft: [360, 50], paddingBottomRight: [40, 40], maxZoom: 16 }); marker.openPopup(); };
  list.appendChild(btn);
  return marker;
});
</script>
</body>
</html>
"""
    page = (
        page.replace("/*LEAFLET_CSS*/", leaflet_css)
        .replace("/*LEAFLET_JS*/", leaflet_js)
        .replace("__SPOTS__", payload)
        .replace("__LAYER__", layer)
        .replace("__TERRAIN__", json.dumps(_data_png(TERRAIN)))
        .replace("__ANGLE__", json.dumps(_data_png(LAYER)))
    )
    MAP.write_text(page)
    print(f"wrote {MAP}")


if __name__ == "__main__":
    import sys

    if "--map" in sys.argv and OUT.exists():
        ranked = json.loads(OUT.read_text())
        if "--layer" in sys.argv or not LAYER.exists():
            _meta, peaks = build_angle_layer(ranked)
            ranked = apply_peaks(ranked, peaks)
            OUT.write_text(json.dumps(ranked, indent=2) + "\n")
        write_map(ranked)
    else:
        main()
