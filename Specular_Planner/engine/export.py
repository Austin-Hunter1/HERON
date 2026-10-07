"""ArduPilot Copter / Mission Planner waypoint export (QGC WPL 110)."""

from __future__ import annotations

import math

# MAVLink mission commands for aurealia based on the stock values (i need to verify these)
NAV_WAYPOINT = 16
NAV_LOITER_TIME = 19
NAV_RTL = 20
NAV_TAKEOFF = 22
DO_SET_ROI = 201
DO_CHANGE_SPEED = 178

FRAME_GLOBAL = 0
FRAME_RELATIVE_ALT = 3

# CHECK WAYPOINT CAPACITY ON CUBE!!
SOFT_WP_CAP = 100
HARD_WP_CAP = 700


def _row(idx, current, frame, cmd, p1, p2, p3, p4, lat, lon, alt) -> str:
    return (
        f"{idx}\t{current}\t{frame}\t{cmd}\t"
        f"{float(p1):.6f}\t{float(p2):.6f}\t{float(p3):.6f}\t{float(p4):.6f}\t"
        f"{float(lat):.8f}\t{float(lon):.8f}\t{float(alt):.2f}\t1"
    )


def _splash_ll(hov: dict) -> tuple[float, float] | None:
    """Bounce target for Copter ROI yaw. Skip if missing or under the aircraft."""
    if hov.get("splash_lat") is None or hov.get("splash_lon") is None:
        return None
    slat, slon = float(hov["splash_lat"]), float(hov["splash_lon"])
    dlat = slat - float(hov["lat"])
    dlon = (slon - float(hov["lon"])) * math.cos(math.radians((slat + float(hov["lat"])) / 2.0))
    if math.hypot(dlat * 111132.92, dlon * 111132.92) < 2.0:
        return None
    return slat, slon


def qgc_wpl(plan: dict) -> str:
    """
    Mission Planner Copter file.

    Seq 0 is HOME (MP convention). Then TAKEOFF, cruise speed, optional pad
    delay, then for water surveys DO_SET_ROI (yaw at bounce) + WAYPOINT
    (Delay = loiter seconds), then clear ROI and RTL.
    """
    meta = plan["meta"]
    h = float(meta["h_agl"])
    pad = plan["pad"]
    hovers = plan.get("hovers") or [plan["hover"]]
    hold_s = int(round(float(meta.get("hold_s") or 0)))
    speed = max(0.5, float(meta.get("speed_mps") or 8.0))
    default_loiter = float(meta.get("loiter_s") or 0)

    lines = ["QGC WPL 110"]
    # Home: global frame, not flown. MP reads this as home / RTL origin so can takeoff ez
    lines.append(_row(0, 1, FRAME_GLOBAL, NAV_WAYPOINT, 0, 0, 0, 0, pad["lat"], pad["lon"], 0))
    lines.append(_row(1, 0, FRAME_RELATIVE_ALT, NAV_TAKEOFF, 0, 0, 0, 0, pad["lat"], pad["lon"], h))
    # Type 1 = ground speed. Copter ignores type and uses param2 m/s. i think?
    lines.append(_row(2, 0, FRAME_RELATIVE_ALT, DO_CHANGE_SPEED, 1, speed, 0, 0, 0, 0, 0))
    idx = 3
    if hold_s >= 8:
        lines.append(
            _row(idx, 0, FRAME_RELATIVE_ALT, NAV_WAYPOINT, hold_s, 0, 0, 0, pad["lat"], pad["lon"], h)
        )
        idx += 1
    prev = None
    used_roi = False
    for hov in hovers:
        delay = int(round(float(hov.get("loiter_s") if hov.get("loiter_s") is not None else default_loiter)))
        delay = max(0, min(delay, 65535))
        lat, lon = float(hov["lat"]), float(hov["lon"])
        if prev and abs(prev[0] - lat) < 1e-7 and abs(prev[1] - lon) < 1e-7:
            # Same spot as last item: add dwell onto it instead of a duplicate WP.
            for j in range(len(lines) - 1, -1, -1):
                parts = lines[j].split("\t")
                if int(float(parts[3])) == NAV_WAYPOINT:
                    parts[4] = f"{float(parts[4]) + delay:.6f}"
                    lines[j] = "\t".join(parts)
                    break
            continue
        splash = _splash_ll(hov)
        if splash:
            slat, slon = splash
            # Copter yaws at this lat/lon while flying; does not wait on heading. need to verify this too, roi may not be best way to do this.
            lines.append(_row(idx, 0, FRAME_RELATIVE_ALT, DO_SET_ROI, 0, 0, 0, 0, slat, slon, 0))
            idx += 1
            used_roi = True
        lines.append(_row(idx, 0, FRAME_RELATIVE_ALT, NAV_WAYPOINT, delay, 0, 0, 0, lat, lon, h))
        idx += 1
        prev = (lat, lon)
    if used_roi:
        lines.append(_row(idx, 0, FRAME_GLOBAL, DO_SET_ROI, 0, 0, 0, 0, 0, 0, 0))
        idx += 1
    lines.append(_row(idx, 0, FRAME_RELATIVE_ALT, NAV_RTL, 0, 0, 0, 0, pad["lat"], pad["lon"], 0))
    return "\n".join(lines) + "\n"


def parse_wpl(text: str) -> list[dict]:
    lines = [ln.strip() for ln in text.replace("\r\n", "\n").split("\n") if ln.strip()]
    if not lines:
        raise ValueError("empty waypoint file")
    if lines[0] != "QGC WPL 110":
        raise ValueError(f"need 'QGC WPL 110' header, got {lines[0]!r}")
    items = []
    for i, ln in enumerate(lines[1:]):
        parts = ln.split("\t")
        if len(parts) != 12:
            raise ValueError(f"line {i + 1}: expected 12 tab-separated fields, got {len(parts)}")
        seq, current, frame, cmd = (int(float(parts[j])) for j in range(4))
        p1, p2, p3, p4, lat, lon, alt, auto = (float(parts[j]) for j in range(4, 12))
        items.append(
            {
                "seq": seq,
                "current": current,
                "frame": frame,
                "command": cmd,
                "p1": p1,
                "p2": p2,
                "p3": p3,
                "p4": p4,
                "lat": lat,
                "lon": lon,
                "alt": alt,
                "autocontinue": auto,
            }
        )
    return items


def validate_wpl(text: str, plan: dict | None = None) -> dict:
    """Sanity-check a WPL 110 string the way Mission Planner / pymavlink read it."""
    errors: list[str] = []
    warnings: list[str] = []
    try:
        items = parse_wpl(text)
    except ValueError as e:
        return {"ok": False, "errors": [str(e)], "warnings": [], "n": 0, "n_nav": 0}

    if not items:
        errors.append("no mission items")
    else:
        if items[0]["seq"] != 0:
            errors.append("first item seq must be 0 (home)")
        if items[0]["current"] != 1:
            errors.append("home row current flag must be 1")
        if items[0]["frame"] != FRAME_GLOBAL:
            errors.append("home must use frame 0 (global)")
        if any(it["current"] != 0 for it in items[1:]):
            errors.append("only home may have current=1")
        seqs = [it["seq"] for it in items]
        if seqs != list(range(len(items))):
            errors.append("seq numbers must be 0..N with no gaps")
        if any(it["autocontinue"] != 1 for it in items):
            errors.append("autocontinue must be 1")
        cmds = [it["command"] for it in items]
        if len(items) < 3 or cmds[1] != NAV_TAKEOFF:
            errors.append("item 1 must be TAKEOFF (22)")
        if NAV_RTL not in cmds:
            errors.append("mission needs RTL (20)")
        elif cmds[-1] != NAV_RTL:
            warnings.append("RTL is not the last item")
        if cmds.count(NAV_TAKEOFF) != 1:
            warnings.append("expected exactly one TAKEOFF")
        if any(it["command"] == NAV_LOITER_TIME for it in items):
            warnings.append("LOITER_TIME items are redundant on Copter; use WAYPOINT delay")
        n_roi = sum(1 for it in items if it["command"] == DO_SET_ROI)
        if n_roi:
            warnings.append(f"{n_roi} DO_SET_ROI — Copter yaws at each bounce; does not pause for heading")
        for it in items[1:]:
            if it["command"] in (NAV_WAYPOINT, NAV_TAKEOFF, NAV_LOITER_TIME) and it["frame"] != FRAME_RELATIVE_ALT:
                errors.append(f"seq {it['seq']}: nav item should use relative alt (frame 3)")
            if not (-90.0 <= it["lat"] <= 90.0 and -180.0 <= it["lon"] <= 180.0):
                errors.append(f"seq {it['seq']}: lat/lon out of range")
            if it["command"] == NAV_WAYPOINT and it["p1"] < 0:
                errors.append(f"seq {it['seq']}: delay cannot be negative")
            if it["command"] in (NAV_WAYPOINT, NAV_TAKEOFF) and it["alt"] < 0:
                errors.append(f"seq {it['seq']}: altitude cannot be negative")

    n_nav = sum(1 for it in items if it["command"] == NAV_WAYPOINT and it["seq"] > 0)
    n_fly = n_nav + sum(1 for it in items if it["command"] in (NAV_TAKEOFF, NAV_RTL, NAV_LOITER_TIME))
    if n_fly > HARD_WP_CAP:
        errors.append(f"{n_fly} nav commands exceeds Copter hard cap (~{HARD_WP_CAP})")
    elif n_fly > SOFT_WP_CAP:
        warnings.append(f"{n_fly} nav commands — some boards only store {SOFT_WP_CAP}. Raise WP_MAX or coarsen the grid.")

    if plan:
        hovers = plan.get("hovers") or []
        pad = plan["pad"]
        flown = [it for it in items if it["command"] == NAV_WAYPOINT and it["seq"] > 0]
        # Pad-hold WP is extra; remaining should match unique hover positions.
        unique_hovers = []
        for hov in hovers:
            pt = (round(float(hov["lat"]), 7), round(float(hov["lon"]), 7))
            if not unique_hovers or unique_hovers[-1] != pt:
                unique_hovers.append(pt)
        hold = int(round(float((plan.get("meta") or {}).get("hold_s") or 0))) >= 8
        expect = len(unique_hovers) + (1 if hold else 0)
        if len(flown) != expect:
            warnings.append(f"file has {len(flown)} fly-to WPs, plan has {len(unique_hovers)} unique hovers")
        if items:
            home = items[0]
            if abs(home["lat"] - float(pad["lat"])) > 1e-5 or abs(home["lon"] - float(pad["lon"])) > 1e-5:
                errors.append("home is not the pad")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "n": len(items),
        "n_nav": n_nav,
        "n_takeoff": sum(1 for it in items if it["command"] == NAV_TAKEOFF),
        "n_rtl": sum(1 for it in items if it["command"] == NAV_RTL),
        "n_speed": sum(1 for it in items if it["command"] == DO_CHANGE_SPEED),
        "n_roi": sum(1 for it in items if it["command"] == DO_SET_ROI),
    }


def run_card(plan: dict) -> str:
    m = plan["meta"]
    if m.get("mode") in ("click", "manual"):
        lines = [
            "HERON run card — clicked waypoints",
            f"Takeoff UTC: {m['start']}",
            f"AGL: {m['h_agl']} m   speed {m['speed_mps']} m/s   hover {m['loiter_s']:.0f} s / WP",
            f"WPs: {m['n_hovers']}   route {m['transit_m']:.0f} m   ~{m['total_s'] / 60:.1f} min",
            f"Almanac age: {m['almanac_age_days']} d",
            "",
        ]
        for i, hov in enumerate(plan.get("hovers") or []):
            lines.append(f"  WP{i+1}  {hov['lat']:.6f}  {hov['lon']:.6f}")
        lines.append("")
        lines.append("Load the .waypoints file in Mission Planner (Copter). Altitudes are relative to home.")
        return "\n".join(lines) + "\n"
    if m.get("mode") == "survey" or (m.get("mode") == "areas" and m.get("survey")):
        s = m.get("survey") or {}
        title = "HERON run card — lake tile survey" if m.get("mode") == "survey" else "HERON run card — measure box survey"
        lines = [
            title,
            f"Takeoff UTC: {m['start']}",
            f"AGL: {m['h_agl']} m ({m['h_agl'] * 3.28084:.0f} ft)",
            f"Area: {s.get('area_m2', 0):.0f} m²",
            f"Tile spacing: {s.get('tile_m')} m   Fresnel ~{s.get('fresnel_across_m')}×{s.get('fresnel_along_m')} m",
            f"Waypoints: {m['n_hovers']}   route {m['transit_m']:.0f} m   ~{m['total_s'] / 60:.1f} min",
            f"Dwell each WP: {m['loiter_s']:.0f} s" + (" (fly-through, no stop)" if float(m.get("loiter_s") or 0) < 0.05 else ""),
            f"Aim sat: {s.get('aim_sid')}  el {s.get('aim_el')}°  az {s.get('aim_az')}°"
            + (f"  (wanted {m.get('target_el')}°)" if m.get("target_el") is not None else ""),
            f"Almanac age: {m['almanac_age_days']} d",
        ]
        if s.get("coarsened"):
            lines.append(
                f"Spacing opened to {s.get('tile_m')} m so WP count stays ≤ {s.get('max_wp')} "
                f"(inch grid would be ~{s.get('inch_wp')} points and is not flyable)."
            )
        lines.append("")
        lines.append("Mission Planner: Flight Plan → Load WP File. Copter, relative altitude.")
        lines.append("Each WAYPOINT Delay is the hover. DO_SET_ROI yaws at the bounce (not the next WP).")
        lines.append("Copter does not wait for heading. Leave WP_YAW_BEHAVIOR default; ROI points the nose.")
        return "\n".join(lines) + "\n"
    lines = [
        "HERON run card",
        f"Takeoff UTC: {m['start']}",
        f"AGL: {m['h_agl']} m ({m['h_agl'] * 3.28084:.0f} ft)",
        f"Measure areas: {m['n_targets']}   hovers: {m['n_hovers']}",
        f"Route length: {m['transit_m']:.0f} m   total ~{m['total_s'] / 60:.1f} min",
        f"Pad hold before first WP: {m.get('hold_s', 0):.0f} s",
        f"Almanac age: {m['almanac_age_days']} d",
        f"L5 PRNs on water: {m['l5_prns_on_water']}",
        f"Hits in drawn areas: {m['target_hits']}",
    ]
    if m.get("fallback_hovers"):
        lines.append(f"No sat lined up for: {m['fallback_hovers']} (hovered over centroid)")
    lines.append("")
    for i, hov in enumerate(plan.get("hovers") or []):
        prn = hov.get("prn")
        arrive = hov.get("arrive_s")
        loiter = hov.get("loiter_s") or m["loiter_s"]
        geom = hov.get("t_in_window")
        extra = ""
        if prn:
            extra = f"  aim {prn} el {hov.get('el'):.0f}"
        else:
            extra = "  (centroid fallback)"
        when = f"  arrive +{arrive:.0f}s" if arrive is not None else ""
        geom_s = f"  geometry +{geom}s" if geom is not None else ""
        lines.append(
            f"  WP{i+1} {hov.get('name')}  {hov['lat']:.6f} {hov['lon']:.6f}"
            + extra
            + when
            + geom_s
            + f"  loiter {loiter:.0f}s"
        )
    lines.append("")
    lines.append("Load the .waypoints file in Mission Planner (Copter). Altitudes are relative to home.")
    return "\n".join(lines) + "\n"
