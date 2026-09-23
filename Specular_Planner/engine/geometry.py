"""Geodesy, elevation, flat-water specular point, point-in-polygon."""

from __future__ import annotations

import math

WGS84_A = 6378137.0
WGS84_E2 = 6.69437999014e-3
C = 299792458.0
SDR_RATE = 22e6
L5_HZ = 1176.45e6
L5_LAMBDA = C / L5_HZ


def lla_to_ecef(lat_deg: float, lon_deg: float, h: float) -> tuple[float, float, float]:
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    sin_lat, cos_lat = math.sin(lat), math.cos(lat)
    n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
    x = (n + h) * cos_lat * math.cos(lon)
    y = (n + h) * cos_lat * math.sin(lon)
    z = (n * (1.0 - WGS84_E2) + h) * sin_lat
    return x, y, z


def ecef_to_lla(x: float, y: float, z: float) -> tuple[float, float, float]:
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    lat = math.atan2(z, p * (1.0 - WGS84_E2))
    for _ in range(8):
        sin_lat = math.sin(lat)
        n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
        h = p / math.cos(lat) - n
        lat = math.atan2(z, p * (1.0 - WGS84_E2 * n / (n + h)))
    return math.degrees(lat), math.degrees(lon), h


def _enu_matrix(lat_deg: float, lon_deg: float):
    lat, lon = math.radians(lat_deg), math.radians(lon_deg)
    sl, cl = math.sin(lat), math.cos(lat)
    so, co = math.sin(lon), math.cos(lon)
    # rows: east, north, up
    return (
        (-so, co, 0.0),
        (-sl * co, -sl * so, cl),
        (cl * co, cl * so, sl),
    )


def ecef_to_enu(dx, dy, dz, lat_deg, lon_deg) -> tuple[float, float, float]:
    m = _enu_matrix(lat_deg, lon_deg)
    return (
        m[0][0] * dx + m[0][1] * dy + m[0][2] * dz,
        m[1][0] * dx + m[1][1] * dy + m[1][2] * dz,
        m[2][0] * dx + m[2][1] * dy + m[2][2] * dz,
    )


def enu_to_ecef_delta(east: float, north: float, up: float, lat_deg: float, lon_deg: float):
    """Local ENU metres → ECEF delta. Rows of the ENU matrix are the E, N, U axes."""
    m = _enu_matrix(lat_deg, lon_deg)
    return (
        m[0][0] * east + m[1][0] * north + m[2][0] * up,
        m[0][1] * east + m[1][1] * north + m[2][1] * up,
        m[0][2] * east + m[1][2] * north + m[2][2] * up,
    )


def enu_between(
    lat0: float, lon0: float, h0: float, lat1: float, lon1: float, h1: float
) -> tuple[float, float, float]:
    a = lla_to_ecef(lat0, lon0, h0)
    b = lla_to_ecef(lat1, lon1, h1)
    return ecef_to_enu(b[0] - a[0], b[1] - a[1], b[2] - a[2], lat0, lon0)


def az_el(sat_ecef, rx_ecef, lat_deg, lon_deg) -> tuple[float, float]:
    dx = sat_ecef[0] - rx_ecef[0]
    dy = sat_ecef[1] - rx_ecef[1]
    dz = sat_ecef[2] - rx_ecef[2]
    e, n, u = ecef_to_enu(dx, dy, dz, lat_deg, lon_deg)
    horiz = math.hypot(e, n)
    el = math.degrees(math.atan2(u, horiz))
    az = math.degrees(math.atan2(e, n)) % 360.0
    return az, el


def specular_point(
    sat_ecef: tuple[float, float, float],
    drone_lat: float,
    drone_lon: float,
    water_h: float,
    h_agl: float,
) -> tuple[float, float, float, float]:
    """
    Bounce lat/lon on a flat water plane at water_h.
    Returns (lat, lon, extra_path_m, extra_samples_22msps).
    """
    drone_ecef = lla_to_ecef(drone_lat, drone_lon, water_h + h_agl)
    az, el = az_el(sat_ecef, drone_ecef, drone_lat, drone_lon)
    if el <= 5.0:
        return drone_lat, drone_lon, float("nan"), float("nan")

    rho = h_agl / math.tan(math.radians(el))
    az_r = math.radians(az)
    lat, lon = offset_ll(
        drone_lat,
        drone_lon,
        rho * math.cos(az_r),
        rho * math.sin(az_r),
        water_h,
    )

    def path(la, lo):
        sp = lla_to_ecef(la, lo, water_h)
        d_sat = math.dist(sat_ecef, sp)
        d_rx = math.dist(drone_ecef, sp)
        return d_sat + d_rx, sp

    best, _ = path(lat, lon)
    step = 25.0
    for _ in range(14):
        improved = False
        for north, east in ((step, 0), (-step, 0), (0, step), (0, -step)):
            la, lo = offset_ll(lat, lon, north, east, water_h)
            cand, _ = path(la, lo)
            if cand < best:
                best, lat, lon = cand, la, lo
                improved = True
        if not improved:
            step *= 0.5
            if step < 0.4:
                break

    extra_m, sp = path(lat, lon)
    direct = math.dist(sat_ecef, drone_ecef)
    extra = extra_m - direct
    samples = extra / C * SDR_RATE
    return lat, lon, extra, samples


def point_in_ring(lon: float, lat: float, ring: list[list[float]]) -> bool:
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        intersect = ((yi > lat) != (yj > lat)) and (
            lon < (xj - xi) * (lat - yi) / (yj - yi + 1e-18) + xi
        )
        if intersect:
            inside = not inside
        j = i
    return inside


_WATER_INDEX: dict[int, tuple] = {}


def _feature_poly(feat: dict):
    from shapely.geometry import shape

    g = shape(feat["geometry"])
    if g.is_empty:
        return None
    if not g.is_valid:
        g = g.buffer(0)
    if g.geom_type in ("LineString", "MultiLineString"):
        half = float((feat.get("properties") or {}).get("width_m") or 12.0) / 2.0
        lat = g.centroid.y
        deg = half / max(20.0, 111132.92 * math.cos(math.radians(lat)))
        g = g.buffer(deg)
    return None if g.is_empty else g


def _water_index(geojson: dict):
    key = id(geojson)
    hit = _WATER_INDEX.get(key)
    if hit is not None:
        return hit
    from shapely.strtree import STRtree

    feats = geojson.get("features") or []
    geoms = []
    for feat in feats:
        g = _feature_poly(feat)
        if g is not None:
            geoms.append((g, feat))
    tree = STRtree([g for g, _ in geoms])
    packed = (tree, geoms)
    _WATER_INDEX[key] = packed
    return packed


def point_in_polygon(lon: float, lat: float, geojson: dict) -> bool:
    """True if the point is on any mapped water (lake, pond, river buffer, …)."""
    feats = geojson.get("features") or []
    if not feats:
        return False
    if len(feats) == 1 and (feats[0].get("geometry") or {}).get("type") in ("Polygon", "MultiPolygon"):
        geom = feats[0]["geometry"]
        coords = geom["coordinates"]
        rings = [coords] if geom["type"] == "Polygon" else coords
        for poly in rings:
            if point_in_ring(lon, lat, poly[0]):
                holes = poly[1:]
                if any(point_in_ring(lon, lat, hole) for hole in holes):
                    continue
                return True
        return False
    from shapely.geometry import Point

    tree, geoms = _water_index(geojson)
    p = Point(lon, lat)
    for i in tree.query(p):
        g, _feat = geoms[int(i)]
        if g.covers(p):
            return True
    return False


def waterbody_containing(geojson: dict, lon: float, lat: float):
    """Smallest mapped water polygon (or buffered river) covering the click."""
    poly, _orig, _feat = waterbody_hit(geojson, lon, lat)
    return poly


def waterbody_hit(geojson: dict, lon: float, lat: float):
    """(buffered poly, original shape, feature) for the clicked waterbody."""
    from shapely.geometry import Point, shape

    tree, geoms = _water_index(geojson)
    p = Point(lon, lat)
    hits = []
    for i in tree.query(p.buffer(1.2e-4)):
        g, feat = geoms[int(i)]
        if g.buffer(1.2e-4).covers(p):
            try:
                orig = shape(feat["geometry"])
            except Exception:
                orig = g
            hits.append((g.area, g, orig, feat))
    if not hits:
        return None, None, None
    hits.sort(key=lambda h: h[0])
    _area, poly, orig, feat = hits[0]
    return poly, orig, feat


def offset_ll(
    lat: float, lon: float, north_m: float, east_m: float, h: float = 0.0
) -> tuple[float, float]:
    """Metres east/north in the local ENU frame, then WGS84 lat/lon."""
    x, y, z = lla_to_ecef(lat, lon, h)
    dx, dy, dz = enu_to_ecef_delta(east_m, north_m, 0.0, lat, lon)
    nlat, nlon, _nh = ecef_to_lla(x + dx, y + dy, z + dz)
    return nlat, nlon


def drone_for_splash(
    splash_lat: float,
    splash_lon: float,
    az_deg: float,
    el_deg: float,
    h_agl: float,
    h: float = 0.0,
) -> tuple[float, float]:
    """Drone lat/lon so the specular sits on (splash_lat, splash_lon)."""
    if el_deg <= 5.0:
        return splash_lat, splash_lon
    rho = h_agl / math.tan(math.radians(el_deg))
    az = math.radians(az_deg)
    return offset_ll(
        splash_lat, splash_lon, -rho * math.cos(az), -rho * math.sin(az), h
    )


def drone_to_hit_splash(
    sat_ecef: tuple[float, float, float],
    splash_lat: float,
    splash_lon: float,
    water_h: float,
    h_agl: float,
    max_iter: int = 10,
) -> tuple[float, float, float, float]:
    """
    Place the drone so the Fermat bounce lands on the intended splash.
    Uses az/el at the aircraft (same as Play), then walks out residual miss.
    Returns (drone_lat, drone_lon, az_deg, el_deg).
    """
    rx = lla_to_ecef(splash_lat, splash_lon, water_h + h_agl)
    az, el = az_el(sat_ecef, rx, splash_lat, splash_lon)
    if el <= 5.0:
        return splash_lat, splash_lon, az, el
    dlat, dlon = drone_for_splash(splash_lat, splash_lon, az, el, h_agl, water_h)
    for _ in range(max_iter):
        rx = lla_to_ecef(dlat, dlon, water_h + h_agl)
        az, el = az_el(sat_ecef, rx, dlat, dlon)
        if el <= 5.0:
            break
        dlat, dlon = drone_for_splash(splash_lat, splash_lon, az, el, h_agl, water_h)
        got_lat, got_lon, _, _ = specular_point(sat_ecef, dlat, dlon, water_h, h_agl)
        east, north, _up = enu_between(
            got_lat, got_lon, water_h, splash_lat, splash_lon, water_h
        )
        if math.hypot(east, north) < 0.4:
            break
        dlat, dlon = offset_ll(dlat, dlon, north, east, water_h + h_agl)
    rx = lla_to_ecef(dlat, dlon, water_h + h_agl)
    az, el = az_el(sat_ecef, rx, dlat, dlon)
    return dlat, dlon, az, el


def fresnel_axes(h_agl: float, el_deg: float) -> tuple[float, float]:
    """
    First Fresnel zone on a flat water plane, distant GNSS transmitter.
    Returns (along-track m, across-track m). Along is in the sat azimuth plane.
    a_across = sqrt(λ h / sin ε), a_along = sqrt(λ h / sin³ ε).
    """
    s = math.sin(math.radians(max(el_deg, 8.0)))
    across = math.sqrt(L5_LAMBDA * h_agl / s)
    along = math.sqrt(L5_LAMBDA * h_agl / (s * s * s))
    return along, across


def ring_centroid(ring: list[list[float]]) -> tuple[float, float]:
    """ring is [[lon, lat], ...]."""
    pts = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
    lon = sum(p[0] for p in pts) / len(pts)
    lat = sum(p[1] for p in pts) / len(pts)
    return lon, lat


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
