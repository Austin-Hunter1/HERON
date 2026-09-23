"""Build a multi-hover plan aimed at drawn 'measure here' polygons."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

from engine.gnss import DEFAULT_CONSTELLATIONS, almanac_age_days, filter_sats
from engine.coverage import swept_coverage
from engine.survey import (
    build_survey,
    clip_centerline,
    feat_key,
    poly_from_ring,
    waterbody_at,
)
from engine.geometry import (
    az_el,
    drone_for_splash,
    drone_to_hit_splash,
    fresnel_axes,
    haversine_m,
    ecef_to_lla,
    lla_to_ecef,
    point_in_polygon,
    point_in_ring,
    ring_centroid,
    specular_point,
)

DEFAULT_PAD = {"lat": 40.086045, "lon": -105.233634, "name": "SURGE pad (north shore)"}
DEFAULT_HOVER = {"lat": 40.08240, "lon": -105.22680, "name": "Over-water hover"}
WATER_H = 1578.0 # NEED TO UPDATE THIS TO BE DYNAMIC BASED ON THE WATER HEIGHT
DEFAULT_H_AGL = 120.0
DEFAULT_SPEED = 8.0
DEFAULT_LOITER = 30
DEFAULT_MASK = 20.0

# Small default "measure here" over open water so first compute has a target.
DEFAULT_TARGET = {
    "name": "east water",
    "coordinates": [
        [-105.2282, 40.0816],
        [-105.2254, 40.0816],
        [-105.2254, 40.0833],
        [-105.2282, 40.0833],
        [-105.2282, 40.0816],
    ],
}


def load_lake(path: Path) -> dict:
    import json

    return json.loads(Path(path).read_text())


def sky_snapshot(sats, dt, lat, lon, h_ell, mask: float = 20.0, used_sids=None) -> list[dict]:
    """Subsatellite lat/lon + az/el from a receiver for every healthy loaded sat."""
    used = {str(s) for s in (used_sids or []) if s}
    rx = lla_to_ecef(lat, lon, h_ell)
    rows = []
    for sat in sats:
        if sat.health != 0:
            continue
        xyz = sat.ecef(dt)
        if xyz[0] != xyz[0]:
            continue
        slat, slon, halt = ecef_to_lla(*xyz)
        az, el = az_el(xyz, rx, lat, lon)
        rows.append(
            {
                "sid": sat.sid,
                "constellation": sat.constellation,
                "lat": round(slat, 4),
                "lon": round(slon, 4),
                "alt_km": round(halt / 1000.0, 1),
                "az": round(az, 1),
                "el": round(el, 1),
                "above_mask": bool(el >= mask),
                "used": sat.sid in used,
            }
        )
    rows.sort(key=lambda r: (-r["el"], r["sid"]))
    return rows


def _visible(sats, dt, lat, lon, h_ell, mask):
    rx = lla_to_ecef(lat, lon, h_ell)
    rows = []
    for sat in sats:
        if sat.health != 0:
            continue
        xyz = sat.ecef(dt)
        if xyz[0] != xyz[0]:
            continue
        az, el = az_el(xyz, rx, lat, lon)
        if el < mask:
            continue
        rows.append((sat, xyz, az, el))
    rows.sort(key=lambda r: r[3], reverse=True)
    return rows


def _choose_sat(vis, track_sid=None, target_el=None):
    """Visible sat nearest target elevation, or highest if target_el is unset.

    Once a PRN is locked, keep it while it stays above the mask so the drone
    tracks one specular geometry across the water.
    """
    if not vis:
        return None
    if track_sid:
        locked = next((r for r in vis if r[0].sid == track_sid), None)
        if locked is not None:
            return locked
    if target_el is None:
        return vis[0]
    want = float(target_el)
    return min(vis, key=lambda row: abs(float(row[3]) - want))


def _normalize_targets(targets) -> list[dict]:
    out = []
    if not targets:
        return [dict(DEFAULT_TARGET)]
    for i, t in enumerate(targets):
        coords = t.get("coordinates") or []
        if len(coords) < 3:
            continue
        ring = [list(p) for p in coords]
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        out.append({"name": t.get("name") or f"area {i+1}", "coordinates": ring})
    return out or [dict(DEFAULT_TARGET)]


def _pick_hover_for_target(sats, lake, target, start, duration_min, h_agl, mask):
    """Place the drone so a high-El L5 splash sits in the drawn area."""
    clon, clat = ring_centroid(target["coordinates"])
    best = None
    for t in range(0, duration_min * 60 + 1, 60):
        dt = start + timedelta(seconds=t)
        vis = _visible(sats, dt, clat, clon, WATER_H + h_agl, mask)
        for sat, xyz, az, el in vis:
            dlat, dlon = drone_for_splash(clat, clon, az, el, h_agl, WATER_H)
            vis2 = _visible(sats, dt, dlat, dlon, WATER_H + h_agl, mask)
            match = next((r for r in vis2 if r[0].sid == sat.sid), None)
            if match is None:
                continue
            sat2, xyz2, az2, el2 = match
            slat, slon, extra, samples = specular_point(xyz2, dlat, dlon, WATER_H, h_agl)
            if not point_in_ring(slon, slat, target["coordinates"]):
                continue
            raw_lat, raw_lon = dlat, dlon
            dlat, dlon, drone_ok = _snap_drone_on_lake(dlat, dlon, slat, slon, lake)
            if drone_ok and (dlat, dlon) != (raw_lat, raw_lon):
                slat2, slon2, _, _ = specular_point(xyz2, dlat, dlon, WATER_H, h_agl)
                if not point_in_ring(slon2, slat2, target["coordinates"]):
                    dlat, dlon = raw_lat, raw_lon
                    drone_ok = point_in_polygon(dlon, dlat, lake)
            score = el2 + (8.0 if drone_ok else 0.0)
            cand = {
                "lat": dlat,
                "lon": dlon,
                "name": target["name"],
                "prn": sat2.sid,
                "el": el2,
                "az": az2,
                "t_in_window": t,
                "drone_on_water": drone_ok,
                "score": score,
            }
            if best is None or cand["score"] > best["score"]:
                best = cand
    if best is None:
        return {
            "lat": clat,
            "lon": clon,
            "name": target["name"],
            "prn": None,
            "el": None,
            "az": None,
            "t_in_window": 0,
            "drone_on_water": point_in_polygon(clon, clat, lake),
            "score": 0,
            "fallback": True,
        }
    best["fallback"] = False
    return best


def _snap_drone_on_lake(dlat, dlon, slat, slon, lake):
    """If the aimed hover is over dirt, sit on the last water point toward the splash."""
    if point_in_polygon(dlon, dlat, lake):
        return dlat, dlon, True
    last = None
    for i in range(25):
        f = i / 24.0
        la = slat + f * (dlat - slat)
        lo = slon + f * (dlon - slon)
        if point_in_polygon(lo, la, lake):
            last = (la, lo)
    if last:
        return last[0], last[1], True
    return dlat, dlon, False


def _merge_hovers(hovers, min_m=50.0):
    ordered = sorted(hovers, key=lambda h: (h.get("t_in_window") or 0, -(h.get("el") or 0)))
    out = []
    for h in ordered:
        if out and haversine_m(out[-1]["lat"], out[-1]["lon"], h["lat"], h["lon"]) < min_m:
            out[-1]["name"] = out[-1]["name"] + "+" + h["name"]
            if (h.get("el") or 0) > (out[-1].get("el") or 0):
                out[-1]["prn"] = h.get("prn")
                out[-1]["el"] = h.get("el")
                out[-1]["az"] = h.get("az")
            continue
        out.append(dict(h))
    return out


def _offset_survey_hovers(
    sats,
    hovers,
    pad,
    start,
    h_agl,
    speed,
    loiter_s,
    mask,
    poly,
    along_stream,
    target_el=None,
):
    """Place each drone WP so the bounce is on that cell at the time we actually arrive."""
    from shapely.geometry import Point

    climb = max(20.0, h_agl / 2.5)
    min_leg = 3.0
    t = climb
    cur_lat, cur_lon = pad["lat"], pad["lon"]
    first = hovers[0]
    vis0 = _visible(
        sats,
        start + timedelta(seconds=climb),
        first["splash_lat"],
        first["splash_lon"],
        WATER_H + h_agl,
        mask,
    )
    seed = _choose_sat(vis0, None, target_el)
    track_sid = seed[0].sid if seed is not None else None
    sids = []
    for hov in hovers:
        slat = hov["splash_lat"]
        slon = hov["splash_lon"]
        dlat, dlon = slat, slon
        sat_row = None
        az = el = None
        for _ in range(2):
            fly = max(min_leg, haversine_m(cur_lat, cur_lon, dlat, dlon) / max(speed, 1.0))
            dt = start + timedelta(seconds=t + fly)
            vis = _visible(sats, dt, slat, slon, WATER_H + h_agl, mask)
            chosen = _choose_sat(vis, track_sid, target_el)
            if chosen is None:
                sat_row = None
                dlat, dlon = slat, slon
                break
            sat_row = chosen
            _sat, xyz, az_sat, el_sat = chosen
            dlat, dlon, az, el = drone_to_hit_splash(xyz, slat, slon, WATER_H, h_agl)
            if el is None:
                az, el = az_sat, el_sat
        if sat_row is None or az is None:
            hov["prn"] = None
            hov["el"] = None
            hov["az"] = None
            hov["fallback"] = True
            hov["score"] = 0
            hov["lat"] = slat
            hov["lon"] = slon
            hov["drone_on_water"] = True
        else:
            sat = sat_row[0]
            track_sid = sat.sid
            hov["prn"] = sat.sid
            hov["el"] = el
            hov["az"] = az
            hov["fallback"] = False
            hov["score"] = el
            hov["lat"] = dlat
            hov["lon"] = dlon
            hov["drone_on_water"] = bool(poly.contains(Point(dlon, dlat)))
            sids.append(sat.sid)
        dwell = hov.get("loiter_s")
        if dwell is None:
            dwell = loiter_s
        fly = max(min_leg, haversine_m(cur_lat, cur_lon, hov["lat"], hov["lon"]) / max(speed, 1.0))
        hov["t_in_window"] = 0
        t += fly + dwell
        cur_lat, cur_lon = hov["lat"], hov["lon"]
    return sids


def _attach_survey_aim(info: dict, hovers: list[dict], name: str) -> dict:
    first = hovers[0]
    last = hovers[-1]
    uniq = []
    for hov in hovers:
        sid = hov.get("prn")
        if sid and sid not in uniq:
            uniq.append(sid)
    info["name"] = name
    info["live_offset"] = True
    info["aim_sid"] = first.get("prn")
    info["aim_az"] = None if first.get("az") is None else round(float(first["az"]), 1)
    info["aim_el"] = None if first.get("el") is None else round(float(first["el"]), 1)
    info["aim_utc"] = None
    info["sids"] = uniq
    info["last_sid"] = last.get("prn")
    info["last_el"] = None if last.get("el") is None else round(float(last["el"]), 1)
    info["last_az"] = None if last.get("az") is None else round(float(last["az"]), 1)
    return info


def _peek_sat(sats, dt, lat, lon, h_agl, mask, target_el=None):
    vis = _visible(sats, dt, lat, lon, WATER_H + h_agl, mask)
    if not vis:
        return 0.0, 50.0, None
    sat, _xyz, az, el = _choose_sat(vis, None, target_el)
    return az, el, sat.sid


def _river_splash_at(t, segs, hovers, line, phase):
    """Splash on the river centerline at this mission time, plus the sat to aim."""
    if line is None:
        return None, None, None
    from shapely.geometry import Point

    if phase.startswith("hover_"):
        idx = int(phase.split("_")[1]) - 1
        if 0 <= idx < len(hovers):
            hov = hovers[idx]
            return hov["splash_lat"], hov["splash_lon"], hov.get("prn")
        return None, None, None
    if not phase.startswith("to_"):
        return None, None, None
    idx = int(phase.split("_")[1]) - 1
    if idx < 1 or idx >= len(hovers):
        return None, None, None
    prev, hov = hovers[idx - 1], hovers[idx]
    t0, t1, *_ = _seg_at(t, segs)
    frac = 0.0 if t1 <= t0 else min(1.0, max(0.0, (t - t0) / (t1 - t0)))
    d0 = line.project(Point(prev["splash_lon"], prev["splash_lat"]))
    d1 = line.project(Point(hov["splash_lon"], hov["splash_lat"]))
    pt = line.interpolate(d0 + frac * (d1 - d0))
    return pt.y, pt.x, hov.get("prn")


def _sample_times(segs, total_s: float, step_s: float = 8.0) -> list[float]:
    """Regular samples plus each fly/hover start so short dwells still exist."""
    step = max(2.0, float(step_s or 8))
    times = {0.0, float(total_s)}
    t = 0.0
    while t < total_s:
        times.add(t)
        t += step
    for t0, _t1, phase, *_rest in segs:
        p = str(phase)
        if p.startswith("hover") or p.startswith("to_") or p in ("return", "climb"):
            times.add(float(t0))
    return sorted(x for x in times if 0.0 <= x <= total_s + 1e-9)


def _timeline(pad, hovers, h_agl, speed, loiter_s, min_leg_s=15.0):
    """Hold on the pad until the first sat geometry is reachable, then WP loiters."""
    climb = max(20.0, h_agl / 2.5)
    segs = [(0.0, climb, "climb", pad["lat"], pad["lon"])]
    t = climb
    cur_lat, cur_lon = pad["lat"], pad["lon"]
    if hovers:
        first_fly = max(
            min_leg_s,
            haversine_m(cur_lat, cur_lon, hovers[0]["lat"], hovers[0]["lon"]) / max(speed, 1.0),
        )
        want = hovers[0].get("t_in_window") or 0
        hold = max(0.0, want - (t + first_fly))
        if hold >= 8:
            segs.append((t, t + hold, "hold", pad["lat"], pad["lon"]))
            t += hold
    for i, hov in enumerate(hovers):
        fly = max(min_leg_s, haversine_m(cur_lat, cur_lon, hov["lat"], hov["lon"]) / max(speed, 1.0))
        arrive = t + fly
        want = hov.get("t_in_window") or 0
        extra = max(0.0, want - arrive) if want else 0.0
        dwell = hov.get("loiter_s") if hov.get("loiter_s") is not None else loiter_s
        dwell = dwell + extra
        segs.append((t, t + fly, f"to_{i+1}", None, None))
        t = arrive
        if dwell >= 0.05:
            hov["arrive_s"] = arrive
            hov["loiter_s"] = dwell
            segs.append((t, t + dwell, f"hover_{i+1}", hov["lat"], hov["lon"]))
            t += dwell
        else:
            hov["arrive_s"] = arrive
            hov["loiter_s"] = 0.0
        cur_lat, cur_lon = hov["lat"], hov["lon"]
    fly_home = max(min_leg_s, haversine_m(cur_lat, cur_lon, pad["lat"], pad["lon"]) / max(speed, 1.0))
    segs.append((t, t + fly_home, "return", None, None))
    t += fly_home
    segs.append((t, t + 25.0, "land", pad["lat"], pad["lon"]))
    t += 25.0
    return segs, t


def _seg_at(t, segs):
    chosen = segs[-1]
    for seg in segs:
        t0, t1, phase, lat, lon = seg
        if t0 <= t < t1 or (seg is segs[-1] and t >= t0):
            chosen = seg
            break
    return chosen


def _alt_gs(t, segs, h_agl, speed_mps):
    """Height above the water plane, ground speed, vertical speed for this instant."""
    t0, t1, phase, _lat, _lon = _seg_at(t, segs)
    dur = max(t1 - t0, 1e-6)
    u = min(1.0, max(0.0, (t - t0) / dur))
    if phase == "climb":
        return h_agl * u, 0.0, h_agl / dur
    if phase == "land":
        return h_agl * (1.0 - u), 0.0, -h_agl / dur
    if phase == "hold" or phase.startswith("hover"):
        return h_agl, 0.0, 0.0
    return h_agl, speed_mps, 0.0


def _pos_at(t, segs, pad, hovers):
    t0, t1, phase, lat, lon = _seg_at(t, segs)
    if lat is not None:
        return lat, lon, phase, None
    if phase == "return":
        a, b = hovers[-1], pad
    elif phase.startswith("to_"):
        idx = int(phase.split("_")[1]) - 1
        a = pad if idx == 0 else hovers[idx - 1]
        b = hovers[idx]
    else:
        return pad["lat"], pad["lon"], phase, None
    frac = 0.0 if t1 <= t0 else min(1.0, max(0.0, (t - t0) / (t1 - t0)))
    plat = a["lat"] + frac * (b["lat"] - a["lat"])
    plon = a["lon"] + frac * (b["lon"] - a["lon"])
    return plat, plon, phase, _bearing(a["lat"], a["lon"], b["lat"], b["lon"])


def _sid_sort(sid: str):
    order = {"G": 0, "E": 1, "C": 2, "J": 3}
    digits = "".join(ch for ch in str(sid) if ch.isdigit())
    return (order.get(str(sid)[:1], 9), int(digits or 0))


def _bearing(lat0, lon0, lat1, lon1):
    dlat = lat1 - lat0
    dlon = (lon1 - lon0) * math.cos(math.radians((lat0 + lat1) / 2.0))
    if abs(dlat) < 1e-12 and abs(dlon) < 1e-12:
        return None
    return (math.degrees(math.atan2(dlon, dlat)) + 360.0) % 360.0


def _aim_wp(phase, pad, hovers):
    """Point the nose at the bounce on water surveys, else the next WP."""
    if not hovers:
        return pad
    if phase in ("return", "land"):
        return pad

    def splash_or_drone(hov):
        slat, slon = hov.get("splash_lat"), hov.get("splash_lon")
        if slat is None or slon is None:
            return hov
        if haversine_m(hov["lat"], hov["lon"], slat, slon) < 2.0:
            return hov
        return {"lat": slat, "lon": slon}

    if phase in ("climb", "hold"):
        return splash_or_drone(hovers[0])
    if phase.startswith("to_"):
        idx = int(phase.split("_")[1]) - 1
        if 0 <= idx < len(hovers):
            return splash_or_drone(hovers[idx])
        return pad
    if phase.startswith("hover_"):
        idx = int(phase.split("_")[1]) - 1
        if 0 <= idx < len(hovers):
            return splash_or_drone(hovers[idx])
        return pad
    return splash_or_drone(hovers[0])


def _heading_to_aim(lat, lon, phase, pad, hovers, last_hdg: float) -> float:
    aim = _aim_wp(phase, pad, hovers)
    look = _bearing(lat, lon, aim["lat"], aim["lon"])
    return look if look is not None else last_hdg


def plan(
    sats,
    lake: dict,
    *,
    start: datetime,
    duration_min: int,
    pad: dict,
    hover: dict | None = None,
    targets=None,
    h_agl: float = DEFAULT_H_AGL,
    speed_mps: float = DEFAULT_SPEED,
    loiter_s: float = DEFAULT_LOITER,
    elev_mask: float = DEFAULT_MASK,
    l5_only: bool = True,
    constellations=None,
    step_s: int = 15,
    mode: str = "areas",
    click_lat: float | None = None,
    click_lon: float | None = None,
    click_lat2: float | None = None,
    click_lon2: float | None = None,
    tile_m: float | None = None,
    max_wp: int = 200,
    waypoints=None,
    target_el: float | None = None,
) -> dict:
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if constellations is None:
        consts = DEFAULT_CONSTELLATIONS
    else:
        consts = tuple(constellations)
    sats = filter_sats(sats, consts)
    if not sats:
        raise ValueError("Turn on at least one constellation that has orbit data.")

    survey_info = None
    survey_line = None
    if mode in ("click", "manual"):
        hovers = []
        for i, w in enumerate(waypoints or []):
            lat = float(w["lat"])
            lon = float(w["lon"])
            hovers.append(
                {
                    "lat": lat,
                    "lon": lon,
                    "name": w.get("name") or f"WP{i+1}",
                    "prn": None,
                    "el": None,
                    "az": None,
                    "t_in_window": 0,
                    "drone_on_water": point_in_polygon(lon, lat, lake),
                    "score": 0,
                    "fallback": False,
                    "loiter_s": loiter_s,
                }
            )
        if not hovers:
            raise ValueError("Click waypoints on the map first.")
        target_list = []
        segs, total_s = _timeline(pad, hovers, h_agl, speed_mps, loiter_s, min_leg_s=3.0)
    elif mode == "survey":
        poly, orig, feat = waterbody_at(lake, click_lon, click_lat)
        if poly is None:
            raise ValueError("Click a lake, pond, or river.")
        survey_loiter = loiter_s
        line = orig if orig is not None and orig.geom_type in ("LineString", "MultiLineString") else None
        if line is not None:
            if click_lat2 is None or click_lon2 is None:
                raise ValueError("Click the start of the river, then the end.")
            _poly2, _orig2, feat2 = waterbody_at(lake, click_lon2, click_lat2)
            if feat2 is None or feat_key(feat) != feat_key(feat2):
                raise ValueError("Start and end need to be on the same river.")
            line, _stretch_m = clip_centerline(line, click_lon, click_lat, click_lon2, click_lat2)
        survey_line = line
        hovers, survey_info = build_survey(
            poly,
            h_agl=h_agl,
            az=0.0,
            el=0.0,
            prn=None,
            loiter_s=survey_loiter,
            spacing_m=tile_m,
            max_wp=max(20, min(int(max_wp), 400)),
            centerline=line,
        )
        if not hovers:
            raise ValueError("No water cells on that feature at this spacing.")
        sids = _offset_survey_hovers(
            sats,
            hovers,
            pad,
            start,
            h_agl,
            speed_mps,
            survey_loiter,
            elev_mask,
            poly,
            along_stream=bool(line),
            target_el=target_el,
        )
        if line is not None:
            lat0 = line.centroid.y
            half_m = 18.0
            deg = half_m / max(20.0, 111132.92 * math.cos(math.radians(lat0)))
            use = line.buffer(deg)
            if use.geom_type == "MultiPolygon":
                use = max(use.geoms, key=lambda g: g.area)
            step_s = min(int(step_s), 5)
        else:
            use = max(poly.geoms, key=lambda g: g.area) if poly.geom_type == "MultiPolygon" else poly
            if getattr(use, "geom_type", None) != "Polygon":
                use = use.buffer(0)
            if use.geom_type != "Polygon":
                raise ValueError("Could not build a tile polygon for that water.")
            simp = use.simplify(2e-5, preserve_topology=True)
            if simp is not None and not simp.is_empty:
                use = simp
        if use.geom_type != "Polygon":
            raise ValueError("Could not build a tile polygon for that water.")
        wname = ((feat or {}).get("properties") or {}).get("name") or "waterbody"
        _attach_survey_aim(survey_info, hovers, wname)
        target_list = [{"name": wname, "coordinates": [list(p) for p in use.exterior.coords]}]
        segs, total_s = _timeline(pad, hovers, h_agl, speed_mps, survey_loiter, min_leg_s=0.8)
        loiter_s = survey_loiter
    else:
        if not targets:
            raise ValueError("Draw a box on the map first.")
        from shapely.ops import unary_union

        target_list = _normalize_targets(targets)
        survey_loiter = loiter_s
        cap = max(20, min(int(max_wp), 400))
        all_hovers = []
        polys = []
        infos = []
        for tgt in target_list:
            poly = poly_from_ring(tgt["coordinates"])
            clon, clat = ring_centroid(tgt["coordinates"])
            az0, el0, _sid = _peek_sat(sats, start, clat, clon, h_agl, elev_mask, target_el)
            part, info = build_survey(
                poly,
                h_agl=h_agl,
                az=az0,
                el=el0,
                prn=_sid,
                loiter_s=survey_loiter,
                spacing_m=tile_m,
                max_wp=cap,
            )
            if not part:
                raise ValueError(f"No tiles fit in {tgt['name']}. Draw a larger box.")
            for hov in part:
                hov["name"] = f"{tgt['name']} {hov['name']}"
            all_hovers.extend(part)
            polys.append(poly)
            infos.append(info)
        hovers = all_hovers
        use = unary_union(polys) if len(polys) > 1 else polys[0]
        sids = _offset_survey_hovers(
            sats,
            hovers,
            pad,
            start,
            h_agl,
            speed_mps,
            survey_loiter,
            elev_mask,
            use,
            along_stream=False,
            target_el=target_el,
        )
        survey_info = infos[0]
        if len(infos) > 1:
            survey_info["n_cells"] = sum(i.get("n_cells") or 0 for i in infos)
            survey_info["area_m2"] = sum(i.get("area_m2") or 0 for i in infos)
            survey_info["coarsened"] = any(i.get("coarsened") for i in infos)
        name = target_list[0]["name"] if len(target_list) == 1 else f"{len(target_list)} areas"
        _attach_survey_aim(survey_info, hovers, name)
        segs, total_s = _timeline(pad, hovers, h_agl, speed_mps, survey_loiter, min_leg_s=0.8)
        loiter_s = survey_loiter

    transit_m = 0.0
    prev = pad
    for hov in hovers:
        transit_m += haversine_m(prev["lat"], prev["lon"], hov["lat"], hov["lon"])
        prev = hov
    transit_m += haversine_m(prev["lat"], prev["lon"], pad["lat"], pad["lon"])

    if survey_line is not None:
        step_s = min(int(step_s), 3)

    frames = []
    end = float(total_s)
    best_prns = {}
    target_hits = {tgt["name"]: 0 for tgt in target_list}
    last_hdg = 0.0
    used_sids = {h.get("prn") for h in hovers if h.get("prn")}

    for t in _sample_times(segs, end, step_s):
        dt = start + timedelta(seconds=t)
        dlat, dlon, phase, _ = _pos_at(t, segs, pad, hovers)
        alt, gs, vs = _alt_gs(t, segs, h_agl, speed_mps)
        if survey_line is not None and alt >= 5.0:
            pin_lat, pin_lon, pin_sid = _river_splash_at(t, segs, hovers, survey_line, phase)
            if pin_lat is not None:
                vis_pin = _visible(sats, dt, pin_lat, pin_lon, WATER_H + alt, elev_mask)
                chosen = _choose_sat(vis_pin, pin_sid, target_el)
                if chosen is not None:
                    dlat, dlon, _, _ = drone_to_hit_splash(
                        chosen[1], pin_lat, pin_lon, WATER_H, alt
                    )
        hdg = _heading_to_aim(dlat, dlon, phase, pad, hovers, last_hdg)
        last_hdg = hdg
        vis = _visible(sats, dt, dlat, dlon, WATER_H + alt, elev_mask)
        specs = []
        aim_sid = None
        if phase.startswith("hover_") or phase.startswith("to_"):
            try:
                idx = int(phase.split("_")[1]) - 1
                if survey_line is not None and phase.startswith("to_") and idx < 1:
                    aim_sid = None
                elif 0 <= idx < len(hovers):
                    aim_sid = hovers[idx].get("prn")
            except ValueError:
                pass
        if alt >= 5.0:
            for sat, xyz, az, el in vis:
                slat, slon, extra, samples = specular_point(xyz, dlat, dlon, WATER_H, alt)
                on_water = point_in_polygon(slon, slat, lake)
                along, across = fresnel_axes(alt, el)
                in_targets = []
                for tgt in target_list:
                    if point_in_ring(slon, slat, tgt["coordinates"]):
                        in_targets.append(tgt["name"])
                        if phase.startswith("hover"):
                            target_hits[tgt["name"]] += 1
                if on_water and phase.startswith("hover"):
                    best_prns[sat.sid] = max(best_prns.get(sat.sid, 0), el)
                aimed = bool(aim_sid) and sat.sid == aim_sid
                specs.append(
                    {
                        "prn": sat.sid,
                        "constellation": sat.constellation,
                        "l5": True,
                        "az": round(az, 1),
                        "el": round(el, 1),
                        "lat": round(slat, 6),
                        "lon": round(slon, 6),
                        "on_water": on_water,
                        "in_target": bool(in_targets),
                        "aimed": aimed,
                        "targets": in_targets,
                        "extra_m": None if extra != extra else round(extra, 1),
                        "samples": None if samples != samples else round(samples, 1),
                        "fresnel_along_m": round(along, 1),
                        "fresnel_across_m": round(across, 1),
                    }
                )
        frames.append(
            {
                "t": t,
                "iso": dt.isoformat(),
                "phase": phase,
                "drone": {
                    "lat": round(dlat, 6),
                    "lon": round(dlon, 6),
                    "hdg": round(hdg, 1),
                    "alt_agl": round(alt, 1),
                    "alt_m": round(WATER_H + alt, 1),
                    "speed_mps": round(gs, 1),
                    "vs_mps": round(vs, 2),
                },
                "speculars": specs,
                "sats": sky_snapshot(
                    sats, dt, dlat, dlon, WATER_H + max(alt, 0.0), elev_mask, used_sids
                ),
            }
        )

    used_sids |= set(best_prns)
    for fr in frames:
        for row in fr.get("sats") or []:
            if row["sid"] in used_sids:
                row["used"] = True

    path = [{"name": "Pad", "lat": pad["lat"], "lon": pad["lon"], "alt_agl": 0}]
    for i, hov in enumerate(hovers):
        path.append(
            {
                "name": f"WP{i+1} {hov['name']}",
                "lat": hov["lat"],
                "lon": hov["lon"],
                "alt_agl": h_agl,
                "loiter_s": hov.get("loiter_s", loiter_s),
                "arrive_s": hov.get("arrive_s"),
                "aim_prn": hov.get("prn"),
                "aim_el": hov.get("el"),
                "t_in_window": hov.get("t_in_window"),
            }
        )
    path.append({"name": "RTL", "lat": pad["lat"], "lon": pad["lon"], "alt_agl": 0})

    first_hover = hovers[0] if hovers else (hover or DEFAULT_HOVER)

    return {
        "meta": {
            "start": start.isoformat(),
            "duration_min": duration_min,
            "h_agl": h_agl,
            "water_h": WATER_H,
            "speed_mps": speed_mps,
            "elev_mask": elev_mask,
            "target_el": None if target_el is None else round(float(target_el), 1),
            "l5_only": True,
            "constellations": list(consts),
            "almanac_age_days": round(almanac_age_days(sats, start), 2),
            "hover_in_lake": all(h.get("drone_on_water") for h in hovers),
            "transit_m": round(transit_m, 1),
            "outbound_s": round(segs[1][1], 1) if len(segs) > 1 else 0,
            "loiter_s": loiter_s,
            "total_s": round(total_s, 1),
            "n_targets": len(target_list),
            "n_hovers": len(hovers),
            "target_hits": target_hits,
            "l5_prns_on_water": sorted(best_prns.keys(), key=_sid_sort),
            "best_el_by_prn": {str(k): round(v, 1) for k, v in sorted(best_prns.items(), key=lambda kv: _sid_sort(kv[0]))},
            "fallback_hovers": [h["name"] for h in hovers if h.get("fallback")],
            "hold_s": next((t1 - t0 for t0, t1, ph, *_ in segs if ph == "hold"), 0),
            "mode": mode,
            "survey": survey_info,
        },
        "pad": pad,
        "hover": first_hover,
        "hovers": hovers,
        "targets": target_list,
        "path": path,
        "frames": frames,
        "coverage": swept_coverage(frames),
    }


def default_window() -> tuple[datetime, int]:
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    minute = 0 if now.minute < 15 else 30 if now.minute < 45 else 60
    start = now.replace(minute=0) + timedelta(minutes=minute)
    if start <= now:
        start += timedelta(minutes=30)
    return start, 40
