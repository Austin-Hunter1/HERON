"""Ordered, explicitly targeted hover and reflection-transect observations."""
from __future__ import annotations

import math
import re
from datetime import timedelta

from engine.geometry import haversine_m, point_in_polygon, drone_to_hit_splash

# A hover arrives facing its direction of travel, then turns to the satellite heading during a short
# settle hold before its observation starts, so the whole observation is at the right heading.
SETTLE_YAW_RATE = 30.0      # deg/s for that turn (CONDITION_YAW rate)
SETTLE_MARGIN_S = 3.0       # extra hold after the turn for the heading to settle
SAMPLE_YAW_RATE = 15.0


def _travel_bearing(a, b):
    lat0, lat1 = math.radians(a["lat"]), math.radians(b["lat"])
    dlon = math.radians(b["lon"] - a["lon"])
    y = math.sin(dlon) * math.cos(lat1)
    x = math.cos(lat0) * math.sin(lat1) - math.sin(lat0) * math.cos(lat1) * math.cos(dlon)
    if abs(x) < 1e-12 and abs(y) < 1e-12:
        return None
    return math.degrees(math.atan2(y, x)) % 360


def number(value, label, low, high):
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number.") from None
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{label} must be between {low:g} and {high:g}.")
    return value


def normalize_stages(stages):
    if not isinstance(stages, list) or not 1 <= len(stages) <= 20:
        raise ValueError("Add between 1 and 20 science phases.")
    out = []
    for i, raw in enumerate(stages):
        if not isinstance(raw, dict):
            raise ValueError("Each science phase must be an object.")
        kind = raw.get("type")
        if kind not in ("hover", "passes"):
            raise ValueError("Choose Hover or Passes for each science phase.")
        name = str(raw.get("name") or f"Phase {i + 1}")[:80]
        ids = raw.get("satellites", [])
        if isinstance(ids, str):
            ids = re.split(r"[\s,]+", ids.strip().upper())
        if not isinstance(ids, list) or not 1 <= len(ids) <= 12:
            raise ValueError(f"{name}: enter 1–12 satellite IDs, primary first.")
        ids = list(dict.fromkeys(str(s).upper().strip() for s in ids))
        if any(not re.fullmatch(r"[GECJ]\d{2,3}", s) for s in ids):
            raise ValueError(f"{name}: use satellite IDs such as G09, E05, C12.")
        points = raw.get("points")
        required = 1 if kind == "hover" else 2
        if not isinstance(points, list) or len(points) != required:
            raise ValueError(f"{name}: select {required} reflection point{'s' if required > 1 else ''} on the map.")
        clean = []
        for p in points:
            if not isinstance(p, dict):
                raise ValueError(f"{name}: invalid map point.")
            clean.append({"lat": number(p.get("lat"), "Latitude", -85, 85),
                          "lon": number(p.get("lon"), "Longitude", -180, 180)})
        stage = dict(type=kind, name=name, satellites=ids, points=clean, record=raw.get("record", True) is not False)
        if kind == "hover":
            stage["duration_s"] = number(raw.get("duration_s", 120), "Hover duration (s)", 1, 1200)
            if stage["duration_s"] != int(stage["duration_s"]):
                raise ValueError("Hover duration must be a whole number of seconds.")
        else:
            stage["speed_mps"] = number(raw.get("speed_mps", 3), "Pass speed (m/s)", 1, 25)
            count = number(raw.get("count", 3), "Pass count", 1, 20)
            if count != int(count):
                raise ValueError("Pass count must be a whole number.")
            stage["count"] = int(count)
            stage["spacing_m"] = number(raw.get("spacing_m", 25), "Pass spacing (m)", 0, 300)
            length = haversine_m(clean[0]["lat"], clean[0]["lon"], clean[1]["lat"], clean[1]["lon"])
            if not 10 <= length <= 2000:
                raise ValueError(f"{name}: pass endpoints must be 10–2000 m apart.")
        if kind == "passes" and raw.get("water_m") not in (None, ""):
            stage["water_m"] = number(raw.get("water_m"), "Water-side length (m)", 5, 1500)
            stage["land_m"] = number(raw.get("land_m", 100), "Land-side length (m)", 5, 500)
        out.append(stage)
    return out


def build_science(sats, lake, stages, start, pad, altitude, transit_speed, mask, offset, max_wp=400, any_water=None):
    # Imported lazily to keep the ordinary planner independent of this builder.
    from engine.planner import _visible, WATER_H

    stages = normalize_stages(stages)
    # Passes cross the shore of the selected outline (a flight's low-water edge when chosen).
    # A hover only needs its reflection on water: the selected outline or any mapped water.
    shore = lake
    number(altitude, "Altitude (m)", 2, 122)
    number(transit_speed, "Transit speed (m/s)", 1, 25)
    available = {s.sid for s in sats}
    for stage in stages:
        missing = set(stage["satellites"]) - available
        if missing:
            raise ValueError(f"{stage['name']}: {', '.join(sorted(missing))} not in the enabled L5 orbit data. Check constellations and orbit files.")

    hovers, summaries, lanes = [], [], []
    t = max(20.0, altitude / 2.5)
    current = dict(pad)

    def visible_at(stage, pos, when):
        rows = {row[0].sid: row for row in _visible(sats, start + timedelta(seconds=when),
                    pos["lat"], pos["lon"], WATER_H + altitude, mask)}
        missing = set(stage["satellites"]) - rows.keys()
        if missing:
            stamp = (start + timedelta(seconds=when)).strftime("%H:%M:%S UTC")
            raise ValueError(f"{stage['name']}: {', '.join(sorted(missing))} unavailable or below the elevation mask at {stamp}. Change the time or targets.")
        return rows

    def solve(stage, splash, speed):
        pos = dict(splash)
        for _ in range(30):
            arrival = t + haversine_m(current['lat'], current['lon'], pos['lat'], pos['lon']) / speed
            row = visible_at(stage, pos, arrival)[stage['satellites'][0]]
            lat, lon, _, _ = drone_to_hit_splash(row[1], splash['lat'], splash['lon'], WATER_H, altitude)
            new = dict(lat=lat, lon=lon)
            delta = haversine_m(pos['lat'], pos['lon'], lat, lon)
            pos = new
            if delta < .01:
                break
        else:
            raise ValueError(f"{stage['name']}: could not solve reflection timing; try a different target or speed.")
        arrival = t + haversine_m(current['lat'], current['lon'], pos['lat'], pos['lon']) / speed
        return pos, arrival

    def append(stage, phase, pos, splash, arrival, dwell, speed, pass_id=None, role="sample", yaw=None,
               yaw_rate=SAMPLE_YAW_RATE):
        nonlocal t, current
        rows = visible_at(stage, pos, arrival + dwell / 2)
        row = rows[stage['satellites'][0]]
        visible_at(stage, pos, arrival + dwell)
        hovers.append(dict(lat=pos['lat'], lon=pos['lon'], splash_lat=splash['lat'],
            splash_lon=splash['lon'], name=stage['name'], prn=stage['satellites'][0],
            target_sids=stage['satellites'], el=row[3], az=row[2],
            yaw_deg=((row[2]-offset) % 360) if yaw is None else yaw % 360, yaw_rate=yaw_rate, role=role,
            t_in_window=0, geometry_s=arrival, arrive_s=arrival, loiter_s=dwell,
            speed_mps=speed, phase_id=phase, pass_id=pass_id, science=True,
            preserve_waypoint=True, fallback=False, score=row[3],
            drone_on_water=point_in_polygon(pos['lon'], pos['lat'], lake)))
        t = arrival + dwell
        current = pos
        if len(hovers) > max_wp:
            raise ValueError(f"Science mission exceeds {max_wp} waypoints. Shorten observations or use fewer passes.")

    for i, stage in enumerate(stages):
        first = len(hovers)
        phase_start = t
        if stage['type'] == 'hover':
            splash = stage['points'][0]
            if not (point_in_polygon(splash['lon'], splash['lat'], lake)
                    or (any_water is not None and point_in_polygon(splash['lon'], splash['lat'], any_water))):
                raise ValueError(f"{stage['name']}: choose a reflection point on water.")
            pos, arrival = solve(stage, splash, transit_speed)
            # 1) Arrive facing the way the aircraft is flying.
            travel = _travel_bearing(current, pos)
            append(stage, i, pos, splash, arrival, 0, transit_speed, role="arrive",
                   yaw=travel if travel is not None else None, yaw_rate=SETTLE_YAW_RATE)
            # 2) Turn to the satellite heading and let it settle. Recording starts when this hold ends.
            first_yaw = (visible_at(stage, pos, t)[stage['satellites'][0]][2] - offset) % 360
            turn = abs(((first_yaw - hovers[-1]['yaw_deg']) + 180) % 360 - 180)
            settle = math.ceil(turn / SETTLE_YAW_RATE + SETTLE_MARGIN_S)
            append(stage, i, pos, splash, t, settle, transit_speed, role="settle", yaw=first_yaw,
                   yaw_rate=SETTLE_YAW_RATE)
            record_from = len(hovers)            # the settle hold's WP is len(hovers); samples follow
            # 3) The observation itself, a heading update every 10 s.
            arrival = t
            remaining = stage['duration_s']
            while remaining > 1e-7:
                dwell = min(10.0, remaining)
                append(stage, i, pos, splash, arrival, dwell, transit_speed)
                arrival = t
                remaining -= dwell
        else:
            a, b = stage['points']
            if "water_m" in stage:
                # Locate the crossing on the selected outline, then extend along the drawn bearing.
                wet_a = point_in_polygon(a['lon'], a['lat'], shore)
                wet_b = point_in_polygon(b['lon'], b['lat'], shore)
                if wet_a == wet_b:
                    raise ValueError(f"{stage['name']}: pick one point inside the selected water outline and one outside it.")
                if not wet_a:
                    a, b = b, a
                lo, hi = 0.0, 1.0
                for _ in range(40):
                    u = (lo + hi) / 2
                    lat = a['lat'] + u * (b['lat'] - a['lat'])
                    lon = a['lon'] + u * (b['lon'] - a['lon'])
                    if point_in_polygon(lon, lat, shore): lo = u
                    else: hi = u
                u = (lo + hi) / 2
                center = dict(lat=a['lat'] + u*(b['lat']-a['lat']), lon=a['lon'] + u*(b['lon']-a['lon']))
                scale = haversine_m(a['lat'],a['lon'],b['lat'],b['lon'])
                dlat, dlon = (b['lat']-a['lat'])/scale, (b['lon']-a['lon'])/scale
                a = dict(lat=center['lat']-dlat*stage['water_m'],lon=center['lon']-dlon*stage['water_m'])
                b = dict(lat=center['lat']+dlat*stage['land_m'],lon=center['lon']+dlon*stage['land_m'])
            lat0 = (a['lat'] + b['lat']) / 2
            sx = 111320 * math.cos(math.radians(lat0))
            sy = 111132.92
            dx, dy = (b['lon']-a['lon'])*sx, (b['lat']-a['lat'])*sy
            length = math.hypot(dx, dy)
            pieces = max(1, math.ceil(length / min(50, stage['speed_mps'] * 10)))
            for n in range(stage['count']):
                # Centre the parallel lanes on the line selected by the user.
                shift = (n - (stage['count']-1)/2) * stage['spacing_m']
                points = [dict(lat=a['lat'] + (dy*j/pieces + dx/length*shift)/sy,
                               lon=a['lon'] + (dx*j/pieces - dy/length*shift)/sx)
                          for j in range(pieces+1)]
                wet = [point_in_polygon(p['lon'], p['lat'], shore) for p in (points[0], points[-1])]
                if wet[0] == wet[1]:
                    raise ValueError(f"{stage['name']}, pass {n+1}: place one endpoint over water and the other over land; reduce spacing if an offset pass misses the shore.")
                if n % 2:
                    points.reverse()
                lanes.append([[p['lon'], p['lat']] for p in points])
                for j, splash in enumerate(points):
                    # Only the run-in to the first pass is at transit speed; the short hop to
                    # the next pass stays at pass speed.
                    speed = transit_speed if (j == 0 and n == 0) else stage['speed_mps']
                    pos, arrival = solve(stage, splash, speed)
                    append(stage, i, pos, splash, arrival, 0, speed, f"{i}:{n}")
        if stage['type'] == 'hover':
            # The NUC starts on the settle hold's MISSION_ITEM_REACHED, sent when that hold ends.
            trigger_wp, rec_first = record_from, record_from
        else:
            trigger_wp, rec_first = first + 1, first
        summaries.append(dict(**stage, first_wp=first+1, last_wp=len(hovers), record_from_wp=trigger_wp,
            arrive_s=hovers[first]['arrive_s'],
            start_s=hovers[rec_first]['arrive_s'], end_s=t, transit_start_s=phase_start,
            primary=stage['satellites'][0]))
    speeds = [transit_speed] + [w['speed_mps'] for w in hovers]
    changes = sum(a != b for a, b in zip(speeds, speeds[1:]))
    if 8 + 2 * len(hovers) + changes > 800:
        raise ValueError("Science mission exceeds 800 mission items. Shorten observations or use fewer passes.")
    return hovers, dict(pattern='science', phases=summaries, aim_sid=hovers[0]['prn'],
        aim_el=hovers[0]['el'], aim_az=hovers[0]['az'], n_passes=sum(s.get('count', 0) for s in stages),
        lanes={'type':'MultiLineString','coordinates':lanes}, warnings=[])
