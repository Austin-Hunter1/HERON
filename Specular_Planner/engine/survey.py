"""Lawnmower / along-stream grid so first-Fresnel patches tile a waterbody."""

from __future__ import annotations

import math

from shapely.geometry import Point, LineString, mapping
from shapely.affinity import affine_transform, rotate
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


def sweep_rows(poly, spacing_m, bearing_deg=None, max_leg_m=500.0):
    """Clipped parallel legs in local metres; bearing is clockwise from north.

    Keep lane spacing independent of navigation checkpoint spacing. Intersections
    preserve separate spans around holes/concavities; connecting legs are transit.
    """
    clat, clon = poly.centroid.y, poly.centroid.x
    my = 111132.92
    mx = max(1.0, my * math.cos(math.radians(clat)))
    local = affine_transform(poly, [mx, 0, 0, my, -clon*mx, -clat*my])
    if bearing_deg is None:
        corners = list(local.minimum_rotated_rectangle.exterior.coords)
        edges = [(b[0]-a[0], b[1]-a[1]) for a,b in zip(corners,corners[1:])]
        dx,dy = max(edges, key=lambda d: math.hypot(*d))
        bearing_deg = math.degrees(math.atan2(dx,dy)) % 180
    bearing_deg = float(bearing_deg) % 180
    angle = 90-bearing_deg
    aligned = rotate(local, -angle, origin=(0,0))
    xmin,ymin,xmax,ymax = aligned.bounds
    count = max(1, math.ceil((ymax-ymin)/spacing_m - 1e-8))
    if count > 10000:
        raise ValueError('Too many sweep lanes; increase lane spacing.')
    pitch = (ymax-ymin)/count
    rows=[]
    c,s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    def ll(x,y):
        return (clat+(s*x+c*y)/my, clon+(c*x-s*y)/mx)
    for i in range(count):
        y=ymin+(i+0.5)*pitch
        hit=aligned.intersection(LineString([(xmin-1,y),(xmax+1,y)]))
        def lines(g):
            if g.geom_type=='LineString': return [g]
            return [line for sub in getattr(g,'geoms',[]) for line in lines(sub)]
        spans=sorted(lines(hit),key=lambda g:g.bounds[0],reverse=bool(i%2))
        for span in spans:
            left,_,right,_=span.bounds
            if right-left<0.01: continue
            n=max(1,math.ceil((right-left)/max_leg_m - 1e-8))
            xs=[left+(right-left)*j/n for j in range(n+1)]
            if i%2: xs.reverse()
            rows.append([ll(x,y) for x in xs])
    return rows, bearing_deg, pitch


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
    if poly.buffer(1e-10).covers(Point(dlon, dlat)):
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
    sweep_bearing=None,
    survey_style="hover",
    max_leg_m=500.0,
    coverage_pct=100.0,
) -> tuple[list[dict], dict]:
    poly = _as_polygon(poly)
    clat = poly.centroid.y
    clon = poly.centroid.x
    along_stream = _is_line(centerline)
    continuous = survey_style == "continuous" and not along_stream
    along, across = fresnel_axes(h_agl, el if el else 50.0)
    # fresnel_axes gives semi-axes; the strip a pass leaves is at least the full minor axis wide.
    footprint_m = 2.0 * min(along, across)
    auto_m = footprint_m * 100.0 / coverage_pct
    if not continuous and not along_stream:
        # A square grid of stationary footprints only closes up at spacing <= width / sqrt(2).
        auto_m /= math.sqrt(2.0)
    wanted = spacing_m if spacing_m and spacing_m > 0 else auto_m
    area = _area_m2(poly, clat)
    if along_stream and centerline is not None and not centerline.is_empty:
        mid = centerline.interpolate(0.5, normalized=True)
        clat, clon = mid.y, mid.x
    hover_spacing = wanted
    path_spacing = min(hover_spacing, 20.0) if along_stream else hover_spacing
    coarsened = False

    def sample(sp):
        if along_stream:
            return _points_along_line(centerline, sp)
        rows, _, _ = sweep_rows(poly, sp, sweep_bearing, sp)
        return [point for row in rows for point in row]

    sweep_meta = {}
    pass_ids = []
    if continuous:
        rows, bearing, pitch = sweep_rows(poly, wanted, sweep_bearing, max_leg_m)
        splashes = [point for row in rows for point in row]
        pass_ids = [j for j,row in enumerate(rows) for _ in row]
        if len(splashes) > max_wp:
            raise ValueError(f"This sweep needs {len(splashes)} waypoints and the limit is {max_wp}. Raise the waypoint limit or the distance between waypoints (More settings), or lower Coverage.")
        sweep_meta = {"sweep_bearing_deg": round(bearing, 2), "lane_spacing_m": round(pitch, 2),
                      "n_passes": len(rows), "max_leg_m": max_leg_m, "survey_style": survey_style}
    else:
        splashes = sample(path_spacing)
    n = len(splashes)
    guard = 0
    # Only river centrelines may thin out: their extra path points are steering, not samples.
    while not continuous and (n > max_wp and along_stream or n == 0 and not along_stream) and path_spacing < 800 and guard < 12:
        guard += 1
        if n == 0:
            path_spacing = min(800.0, path_spacing * 2.5)
        else:
            coarsened = True
            path_spacing *= math.sqrt(n / max_wp) * 1.05
        splashes = sample(path_spacing)
        n = len(splashes)
    if n > max_wp:
        raise ValueError(f"This survey needs {n} waypoints and the limit is {max_wp}. Raise the waypoint limit or lower Coverage; Keep moving needs far fewer than Stop and hover.")
    if not continuous:
        hover_spacing = max(hover_spacing, path_spacing)
    if along_stream:
        hover_spacing = max(hover_spacing, path_spacing)
    hover_every = 1
    if along_stream and path_spacing > 0:
        hover_every = max(1, int(round(hover_spacing / path_spacing)))

    hovers = []
    n_splash = len(splashes)
    for i, (slat, slon) in enumerate(splashes):
        if not along_stream and not continuous:
            dlat, dlon, on_water = _snap_on_poly(slat, slon, slat, slon, poly)
            if not on_water:
                continue
        science = (not along_stream) or i == 0 or i == n_splash - 1 or (i % hover_every == 0)
        hovers.append(
            {
                "pass_id": pass_ids[i] if continuous else None,
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
                "loiter_s": loiter_s if science and not continuous else 0.0,
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
        "footprint_m": round(footprint_m, 2),
        "coverage_pct": None if spacing_m and spacing_m > 0 else round(coverage_pct, 1),
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
    info.update(sweep_meta)
    if along_stream and centerline is not None:
        info["stretch"] = mapping(centerline)
        info["stretch_m"] = round(line_length_m(centerline), 1)
    return hovers, info


def _local_frame(clat: float, clon: float):
    my = 111132.92
    mx = max(1.0, my * math.cos(math.radians(clat)))
    to_xy = lambda lat, lon: ((lon - clon) * mx, (lat - clat) * my)
    to_ll = lambda x, y: (clat + y / my, clon + x / mx)
    return to_xy, to_ll, mx, my


def shore_stretch(poly, a=None, b=None, long_way=False):
    """Shoreline (outer ring) of the lake in local metres, optionally cut between two picks.

    Returns (LineString in metres, to_ll, local polygon, closed). With no picks the whole ring is used.
    """
    poly = _as_polygon(poly)
    if poly.geom_type == "MultiPolygon":
        poly = max(poly.geoms, key=lambda g: g.area)
    clat, clon = poly.centroid.y, poly.centroid.x
    _to_xy, to_ll, mx, my = _local_frame(clat, clon)
    local = affine_transform(poly, [mx, 0, 0, my, -clon * mx, -clat * my])
    ring = LineString(local.exterior.coords)
    L = ring.length
    if a is None or b is None:
        return ring, to_ll, local, True
    pa = ring.project(Point(_to_xy(*a)))
    pb = ring.project(Point(_to_xy(*b)))
    if abs(pa - pb) < 1.0:
        raise ValueError("Pick two different points on the shore.")
    lo, hi = sorted((pa, pb))
    inner = hi - lo
    outer = L - inner
    use_inner = (inner <= outer) != bool(long_way)
    if use_inner:
        seg = substring(ring, lo, hi)
        if pa > pb:
            seg = LineString(list(seg.coords)[::-1])
    else:
        # Wrap through the ring's start point.
        part1 = list(substring(ring, hi, L).coords)
        part2 = list(substring(ring, 0, lo).coords)
        seg = LineString(part1 + part2[1:])
        if pa < pb:
            seg = LineString(list(seg.coords)[::-1])
    return seg, to_ll, local, False


def _smooth_shore(local, r=8.0):
    """Round off jagged mapped shoreline so offset lanes come out smooth (closing, then opening)."""
    g = local.buffer(r, quad_segs=12).buffer(-2 * r, quad_segs=12).buffer(r, quad_segs=12)
    if g.is_empty:
        g = local.buffer(0)
    if g.geom_type == "MultiPolygon":
        g = max(g.geoms, key=lambda x: x.area)
    return g


def _rings(g):
    if g.is_empty:
        return []
    polys = list(g.geoms) if g.geom_type == "MultiPolygon" else [g]
    return [LineString(p.exterior.coords) for p in polys if p.geom_type == "Polygon" and p.area > 1.0]


def _arc(ring, a_pt, b_pt, mid_pt):
    """Sub-arc of a closed ring from near A to near B, choosing the side that passes the stretch midpoint."""
    L = ring.length
    pa, pb = ring.project(a_pt), ring.project(b_pt)
    lo, hi = sorted((pa, pb))
    inner = substring(ring, lo, hi)
    outer = LineString(list(substring(ring, hi, L).coords) + list(substring(ring, 0, lo).coords)[1:])
    seg = inner if inner.distance(mid_pt) <= outer.distance(mid_pt) else outer
    if Point(seg.coords[0]).distance(a_pt) > Point(seg.coords[-1]).distance(a_pt):
        seg = LineString(list(seg.coords)[::-1])
    return seg


def _densify(line, max_seg):
    pts = list(line.coords)
    out = [pts[0]]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        d = math.hypot(x1 - x0, y1 - y0)
        n = max(1, int(math.ceil(d / max_seg - 1e-9)))
        for k in range(1, n + 1):
            out.append((x0 + (x1 - x0) * k / n, y0 + (y1 - y0) * k / n))
    return out


def build_edge_survey(
    poly,
    *,
    h_agl: float,
    el: float,
    loiter_s: float,
    lanes: int = 2,
    lane_m: float = 15.0,
    a=None,
    b=None,
    long_way: bool = False,
    survey_style: str = "continuous",
    max_wp: int = 200,
    max_leg_m: float = 120.0,
):
    """Lanes that follow the shoreline: one on the edge itself, plus N on the land side and N on the water side.

    The reflection point rides each lane parallel to the edge, so inner lanes see coherent water,
    outer lanes see land, and the edge sits between them. Points are REFLECTION targets; the planner
    offsets the drone toward the satellite afterwards.
    """
    lanes = int(lanes)
    if not (1 <= lanes <= 6 and 3 <= lane_m <= 200):
        raise ValueError("Use 1–6 lanes each side and 3–200 m between lanes.")
    stretch, to_ll, local, closed = shore_stretch(poly, a, b, long_way)
    smooth = _smooth_shore(local, r=min(10.0, max(3.0, lane_m * 0.6)))
    along, across = fresnel_axes(h_agl, el if el else 50.0)
    footprint_m = 2.0 * min(along, across)
    continuous = survey_style == "continuous"
    step = min(max_leg_m, 150.0) if continuous else max(3.0, footprint_m)
    a_pt, b_pt = Point(stretch.coords[0]), Point(stretch.coords[-1])
    mid_pt = stretch.interpolate(0.5, normalized=True)
    # Signed offsets: negative = over land (outside), positive = over water (inside). Far land first.
    # Zero is the shoreline itself: the reflection rides right along the edge too.
    offsets = [-k * lane_m for k in range(lanes, 0, -1)] + [0.0] + [k * lane_m for k in range(1, lanes + 1)]

    lane_lines = []
    for d in offsets:
        grown = smooth if d == 0 else smooth.buffer(-d, quad_segs=16)  # shrink into the water, grow onto land
        rings = _rings(grown)
        if not rings:
            continue
        ring = min(rings, key=lambda r: r.distance(mid_pt))
        if closed:
            # Whole shore: start each loop at the point nearest the previous lane's end.
            line = ring
        else:
            line = _arc(ring, a_pt, b_pt, mid_pt)
        # ~2 m tolerance keeps the curve smooth to well under a Fresnel footprint with far fewer waypoints.
        line = line.simplify(2.0)
        if line.length < 10.0:
            continue
        lane_lines.append((d, line))
    if not lane_lines:
        raise ValueError("No room for shoreline lanes there. Pick a longer stretch or use fewer lanes.")

    hovers, drawn, strips = [], [], []
    last = None
    for li, (d, line) in enumerate(lane_lines):
        pts = list(line.coords)
        if closed:
            ring = LineString(pts)
            start_d = ring.project(Point(last)) if last else 0.0
            L = ring.length
            pts = list(substring(ring, start_d, L).coords) + list(substring(ring, 0, start_d).coords)[1:]
        elif li % 2 == 1:
            pts = pts[::-1]
        dense = _densify(LineString(pts), step)
        last = dense[-1]
        drawn.append([list(to_ll(x, y)[::-1]) for x, y in dense])
        for j, (x, y) in enumerate(dense):
            lat, lon = to_ll(x, y)
            hovers.append({
                "pass_id": li if continuous else None,
                "lat": lat, "lon": lon, "splash_lat": lat, "splash_lon": lon,
                "name": (f"shore · {j+1}" if d == 0 else f"{'water' if d > 0 else 'land'} {abs(d):.0f}m · {j+1}"),
                "prn": None, "el": el, "az": None, "t_in_window": 0,
                "drone_on_water": True, "score": el or 0, "fallback": True,
                "loiter_s": 0.0 if continuous else loiter_s,
                "science": True, "edge_offset_m": d,
            })
        # Coverage targets: the lane's footprint strip, in short chunks so each is a simple polygon.
        lane = LineString(dense)
        chunk = 150.0
        n = max(1, int(math.ceil(lane.length / chunk)))
        for k in range(n):
            part = substring(lane, lane.length * k / n, lane.length * (k + 1) / n)
            if part.length < 1.0:
                continue
            band = part.buffer(max(2.0, footprint_m / 2.0), cap_style="flat")
            if band.geom_type != "Polygon":
                continue
            ring_ll = [list(to_ll(x, y)[::-1]) for x, y in band.exterior.coords]
            strips.append({"name": f"lane {li+1}.{k+1}", "coordinates": ring_ll})
    if len(hovers) > max_wp:
        raise ValueError(
            f"These shoreline lanes need {len(hovers)} waypoints and the limit is {max_wp}. "
            "Pick a shorter stretch, use fewer lanes, raise the waypoint limit, or use Keep moving."
        )
    shore_ll = [list(to_ll(x, y)[::-1]) for x, y in stretch.coords]
    mid_ll = to_ll(mid_pt.x, mid_pt.y)
    info = {
        "pattern": "edge",
        "edge_lanes": lanes,
        "edge_lane_m": lane_m,
        "edge_offsets_m": [d for d, _ in lane_lines],
        "n_passes": len(lane_lines),
        "shore_m": round(stretch.length, 1),
        "lanes_km": round(sum(l.length for _, l in lane_lines) / 1000, 2),
        "whole_shore": closed,
        "footprint_m": round(footprint_m, 2),
        "fresnel_along_m": round(along, 2),
        "fresnel_across_m": round(across, 2),
        "n_cells": len(hovers),
        "max_wp": max_wp,
        "along_stream": False,
        "survey_style": survey_style,
        "shore": {"type": "LineString", "coordinates": shore_ll},
        "lanes": {"type": "MultiLineString", "coordinates": drawn},
        "strips": strips,
        "centroid": {"lat": mid_ll[0], "lon": mid_ll[1]},
    }
    return hovers, info


def build_edge_cross_survey(
    poly,
    *,
    h_agl: float,
    el: float,
    loiter_s: float,
    pass_m: float = 25.0,
    land_m: float = 40.0,
    water_m: float = 40.0,
    a=None,
    b=None,
    long_way: bool = False,
    survey_style: str = "continuous",
    max_wp: int = 200,
):
    """Back-and-forth passes square to the shoreline: water to land, step along the shore, land to water.

    Each pass carries the reflection point straight across the edge, so the jump from coherent (water)
    to diffuse (land) shows up once per pass. Normals come from a smoothed shoreline so neighbouring
    passes stay roughly parallel instead of fanning on every wiggle. Points are REFLECTION targets.
    """
    if not (3 <= pass_m <= 300 and 5 <= land_m <= 300 and 5 <= water_m <= 500):
        raise ValueError("Pass spacing 3–300 m, land reach 5–300 m, water reach 5–500 m.")
    stretch, to_ll, local, closed = shore_stretch(poly, a, b, long_way)
    smooth = _smooth_shore(local, r=min(10.0, max(3.0, pass_m * 0.5)))
    # A heavier smoothing only for pass directions.
    calm = _smooth_shore(local, r=max(15.0, min(60.0, (land_m + water_m) / 3.0)))
    along, across = fresnel_axes(h_agl, el if el else 50.0)
    footprint_m = 2.0 * min(along, across)
    continuous = survey_style == "continuous"
    a_pt, b_pt = Point(stretch.coords[0]), Point(stretch.coords[-1])
    mid_pt = stretch.interpolate(0.5, normalized=True)
    rings = _rings(smooth)
    if not rings:
        raise ValueError("Could not trace that shoreline.")
    ring = min(rings, key=lambda r: r.distance(mid_pt))
    shore = ring if closed else _arc(ring, a_pt, b_pt, mid_pt)
    calm_rings = _rings(calm) or rings
    calm_ring = min(calm_rings, key=lambda r: r.distance(mid_pt))
    L = shore.length
    n = max(1, int(L // pass_m) + (0 if closed else 1))
    ds = [L * i / n for i in range(n)] if closed else ([L * i / (n - 1) for i in range(n)] if n > 1 else [L / 2])
    half = max(pass_m, 15.0)
    sample = (land_m + water_m) if continuous else max(3.0, footprint_m)

    hovers, drawn, strips = [], [], []
    k = 0
    passes_m = 0.0
    for d in ds:
        p = shore.interpolate(d)
        c = calm_ring.project(p)
        q0 = calm_ring.interpolate(c - half if closed or c - half > 0 else 0)
        q1 = calm_ring.interpolate(c + half)
        tx, ty = q1.x - q0.x, q1.y - q0.y
        tl = math.hypot(tx, ty)
        if tl < 1e-6:
            continue
        nx, ny = -ty / tl, tx / tl
        if not smooth.contains(Point(p.x + nx * 3.0, p.y + ny * 3.0)):
            nx, ny = -nx, -ny
            if not smooth.contains(Point(p.x + nx * 3.0, p.y + ny * 3.0)):
                continue
        # Water leg stops where the ray leaves the water again (narrow arms, islands).
        ray = LineString([(p.x - nx * 0.5, p.y - ny * 0.5), (p.x + nx * water_m, p.y + ny * water_m)])
        wet = ray.intersection(smooth)
        parts = [wet] if wet.geom_type == "LineString" else [g for g in getattr(wet, "geoms", []) if g.geom_type == "LineString"]
        touching = [g for g in parts if g.distance(p) < 1.5]
        reach = min(water_m, max((max(p.distance(Point(cc)) for cc in g.coords) for g in touching), default=0.0))
        if reach < 5.0:
            continue
        span = land_m + reach
        npts = max(2, int(math.ceil(span / sample - 1e-6)) + 1)
        offs = [reach - span * j / (npts - 1) for j in range(npts)]  # water first
        if k % 2 == 1:
            offs = offs[::-1]  # next pass comes back land to water
        pts = [(p.x + nx * o, p.y + ny * o, o) for o in offs]
        drawn.append([list(to_ll(x, y)[::-1]) for x, y, _ in pts])
        for j, (x, y, o) in enumerate(pts):
            lat, lon = to_ll(x, y)
            hovers.append({
                "pass_id": k if continuous else None,
                "lat": lat, "lon": lon, "splash_lat": lat, "splash_lon": lon,
                "name": f"pass {k+1} · {'water' if o > 0 else 'land'} {abs(o):.0f}m",
                "prn": None, "el": el, "az": None, "t_in_window": 0,
                "drone_on_water": True, "score": el or 0, "fallback": True,
                "loiter_s": 0.0 if continuous else loiter_s,
                "science": True, "edge_offset_m": round(o, 1),
            })
        hw = max(2.0, footprint_m / 2.0)
        (xs, ys, _), (xe, ye, _) = pts[0], pts[-1]
        band = [(xs - ny * hw, ys + nx * hw), (xe - ny * hw, ye + nx * hw), (xe + ny * hw, ye - nx * hw), (xs + ny * hw, ys - nx * hw)]
        ring_ll = [list(to_ll(x, y)[::-1]) for x, y in band]
        strips.append({"name": f"pass {k+1}", "coordinates": ring_ll + [ring_ll[0]]})
        passes_m += span
        k += 1
    if not hovers:
        raise ValueError("No usable passes there. Pick a longer stretch or shorten the reach.")
    if len(hovers) > max_wp:
        raise ValueError(
            f"These passes need {len(hovers)} waypoints and the limit is {max_wp}. "
            "Space the passes farther apart, pick a shorter stretch, or use Keep moving."
        )
    shore_ll = [list(to_ll(x, y)[::-1]) for x, y in stretch.coords]
    mid_ll = to_ll(mid_pt.x, mid_pt.y)
    info = {
        "pattern": "edge",
        "edge_style": "across",
        "edge_pass_m": round(L / max(1, len(ds) - (0 if closed else 1)), 1),
        "edge_land_m": land_m,
        "edge_water_m": water_m,
        "n_passes": k,
        "shore_m": round(L, 1),
        "lanes_km": round(passes_m / 1000, 2),
        "whole_shore": closed,
        "footprint_m": round(footprint_m, 2),
        "fresnel_along_m": round(along, 2),
        "fresnel_across_m": round(across, 2),
        "n_cells": len(hovers),
        "max_wp": max_wp,
        "along_stream": False,
        "survey_style": survey_style,
        "shore": {"type": "LineString", "coordinates": shore_ll},
        "lanes": {"type": "MultiLineString", "coordinates": drawn},
        "strips": strips,
        "centroid": {"lat": mid_ll[0], "lon": mid_ll[1]},
    }
    return hovers, info
