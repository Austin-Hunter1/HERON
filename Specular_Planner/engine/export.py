"""ArduPilot Copter / Mission Planner waypoint export (QGC WPL 110)."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone

# Mission Planner and QGroundControl skip this line. The aircraft never sees it.
_TAKEOFF_LINE = re.compile(r"^#\s*HERON\s+takeoff\s+(\S+)\s*$", re.IGNORECASE)
_META_LINE = re.compile(r"^#\s*HERON\s+meta\s+(\{.*\})\s*$", re.IGNORECASE)
_TAKEOFF_NAME = re.compile(r"HERON_(\d{8})_(\d{4})")

# MAVLink mission commands for aurealia based on the stock values (i need to verify these)
NAV_WAYPOINT = 16
NAV_LOITER_TIME = 19
NAV_RTL = 20
NAV_TAKEOFF = 22
DO_SET_ROI = 201
DO_CHANGE_SPEED = 178
CONDITION_YAW = 115

# Heading on a long transit: face a lead-in point on the first pass line, turn to the
# survey heading over the last LEAD_IN_M, so the antenna is aimed before WP1.
LEAD_IN_M = 50.0
MIN_LEAD_TRANSIT_M = 100.0  # shorter transits keep the old behaviour
TRANSIT_YAW_RATE = 30.0     # deg/s for the face-forward and line-up turns

FRAME_GLOBAL = 0
FRAME_RELATIVE_ALT = 3


def _bearing(lat0, lon0, lat1, lon1):
    dlat = lat1 - lat0
    dlon = (lon1 - lon0) * math.cos(math.radians((lat0 + lat1) / 2.0))
    if abs(dlat) < 1e-12 and abs(dlon) < 1e-12:
        return None
    return (math.degrees(math.atan2(dlon, dlat)) + 360.0) % 360.0

def _dist_m(a, b):
    return math.hypot((a[0] - b[0]) * 111132.92,
                      (a[1] - b[1]) * 111132.92 * math.cos(math.radians((a[0] + b[0]) / 2.0)))


def lead_in_point(pad: dict, hovers: list[dict]) -> tuple[float, float] | None:
    """Point LEAD_IN_M before WP1 on the first pass line (or the pad-to-WP1 line), or None."""
    if not hovers:
        return None
    h0 = (float(hovers[0]["lat"]), float(hovers[0]["lon"]))
    p = (float(pad["lat"]), float(pad["lon"]))
    if _dist_m(p, h0) < MIN_LEAD_TRANSIT_M:
        return None
    k = math.cos(math.radians(h0[0]))
    def xy(lat, lon):
        return ((lon - h0[1]) * 111132.92 * k, (lat - h0[0]) * 111132.92)
    if len(hovers) > 1 and hovers[0].get("pass_id") is not None and hovers[1].get("pass_id") == hovers[0]["pass_id"]:
        (ax, ay), (bx, by) = (0.0, 0.0), xy(float(hovers[1]["lat"]), float(hovers[1]["lon"]))
    else:
        (ax, ay), (bx, by) = xy(*p), (0.0, 0.0)
    n = math.hypot(bx - ax, by - ay)
    if n < 1.0:
        return None
    x, y = -(bx - ax) / n * LEAD_IN_M, -(by - ay) / n * LEAD_IN_M
    return (h0[0] + y / 111132.92, h0[1] + x / (111132.92 * k))


def _lead(plan: dict):
    """Lead-in for body-yaw plans. Imports record whether their file had one."""
    meta = plan.get("meta") or {}
    if meta.get("roi_mode") != "body_yaw":
        return None
    if "lead_in" in meta and not meta["lead_in"]:
        return None
    return lead_in_point(plan["pad"], plan.get("hovers") or [plan["hover"]])


def plan_route_line(plan: dict, lead=None) -> list[list[float]]:
    """Straight-leg map line: pad, lead-in if any, waypoints, pad."""
    pad = plan["pad"]
    hovers = plan.get("hovers") or [plan["hover"]]
    lead = lead if lead is not None else _lead(plan)
    pts = [[float(pad["lat"]), float(pad["lon"])]]
    if lead:
        pts.append([lead[0], lead[1]])
    pts += [[float(h["lat"]), float(h["lon"])] for h in hovers]
    pts.append([float(pad["lat"]), float(pad["lon"])])
    return pts


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
    delay. Aim commands come before the waypoint they belong to, because
    ArduPilot runs them with the next navigation command. Then RTL.

    Body yaw on a long transit: the nose faces a lead-in point on the first
    pass line, turns to the survey heading on the 50 m run-in, and faces home
    before RTL.
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
    roi_mode = meta.get("roi_mode", "per_point")
    # ArduPilot runs a DO/CONDITION with the next NAV command. Put the aim before the
    # waypoint so it applies on the way there, which is the leg the heading was computed for.
    if roi_mode == "body_yaw":
        lines.append(_row(idx, 0, FRAME_GLOBAL, DO_SET_ROI, 0, 0, 0, 0, 0, 0, 0))
        idx += 1
    lead = _lead(plan)
    if lead:
        out_yaw = _bearing(float(pad["lat"]), float(pad["lon"]), lead[0], lead[1])
        if out_yaw is not None:
            lines.append(_row(idx, 0, FRAME_RELATIVE_ALT, CONDITION_YAW, out_yaw, TRANSIT_YAW_RATE, 0, 0, 0, 0, 0))
            idx += 1
        lines.append(_row(idx, 0, FRAME_RELATIVE_ALT, NAV_WAYPOINT, 0, 0, 0, 0, lead[0], lead[1], h))
        idx += 1
    prev = None
    used_roi = False
    for k, hov in enumerate(hovers):
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
        splash = _splash_ll(hov) if roi_mode == "per_point" else None
        if splash:
            slat, slon = splash
            lines.append(_row(idx, 0, FRAME_RELATIVE_ALT, DO_SET_ROI, 0, 0, 0, 0, slat, slon, 0))
            idx += 1
            used_roi = True
        if roi_mode == "body_yaw":
            yaw = float(hov["yaw_deg"])
            if not math.isfinite(yaw):
                raise ValueError("Invalid antenna heading.")
            # The run-in is only LEAD_IN_M long, so the line-up turn uses the faster rate.
            rate = TRANSIT_YAW_RATE if (k == 0 and lead) else 15
            lines.append(_row(idx, 0, FRAME_RELATIVE_ALT, CONDITION_YAW, yaw % 360, rate, 0, 0, 0, 0, 0))
            idx += 1
        lines.append(_row(idx, 0, FRAME_RELATIVE_ALT, NAV_WAYPOINT, delay, 0, 0, 0, lat, lon, h))
        idx += 1
        prev = (lat, lon)
    if used_roi:
        lines.append(_row(idx, 0, FRAME_GLOBAL, DO_SET_ROI, 0, 0, 0, 0, 0, 0, 0))
        idx += 1
    # Nose toward home on the return. WP_YAW_BEHAVIOR 2 does not turn during RTL,
    # so the heading has to be commanded before that item.
    if roi_mode == "body_yaw" and prev is not None:
        home_yaw = _bearing(prev[0], prev[1], float(pad["lat"]), float(pad["lon"]))
        if home_yaw is not None:
            lines.append(_row(idx, 0, FRAME_RELATIVE_ALT, CONDITION_YAW, home_yaw, 15, 0, 0, 0, 0, 0))
            idx += 1
    lines.append(_row(idx, 0, FRAME_RELATIVE_ALT, NAV_RTL, 0, 0, 0, 0, pad["lat"], pad["lon"], 0))
    return "\n".join(lines) + "\n"


def items_wpl(items: list[dict]) -> str:
    """WPL 110 text for mission items read from an aircraft. Row 0 is Home."""
    lines = ["QGC WPL 110"]
    for i, it in enumerate(items):
        # MISSION_ITEM_INT reports the _INT frame variants; WPL files use the plain ones.
        frame = FRAME_GLOBAL if i == 0 else {5: FRAME_GLOBAL, 6: FRAME_RELATIVE_ALT}.get(int(it["frame"]), int(it["frame"]))
        lines.append(_row(i, 1 if i == 0 else 0, frame, int(it["command"]), it["p1"], it["p2"], it["p3"], it["p4"],
                          it["lat"], it["lon"], it["alt"]))
    return "\n".join(lines) + "\n"


def takeoff_from_wpl(text: str) -> datetime | None:
    """Planned takeoff saved in a HERON waypoint file, or None for other files."""
    if not text:
        return None
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        match = _TAKEOFF_LINE.match(line.strip().lstrip("\ufeff"))
        if not match:
            continue
        raw = match.group(1).replace("Z", "+00:00")
        try:
            when = datetime.fromisoformat(raw)
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return when.astimezone(timezone.utc)
    return None


def takeoff_from_name(filename: str) -> datetime | None:
    """HERON_20261018_2130.waypoints was named from the planned takeoff."""
    match = _TAKEOFF_NAME.search(str(filename or "").replace("\\", "/").split("/")[-1])
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def heron_meta_from_wpl(text: str) -> dict:
    """Track satellites and area rings saved beside the takeoff time."""
    if not text:
        return {}
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        match = _META_LINE.match(line.strip().lstrip("\ufeff"))
        if not match:
            continue
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return {}


def _mission_meta_line(plan: dict | None) -> str | None:
    if not plan:
        return None
    hovers = plan.get("hovers") or []
    track = [h.get("prn") or "" for h in hovers]
    areas = []
    for i, tgt in enumerate(plan.get("targets") or []):
        ring = tgt.get("coordinates") or []
        if len(ring) < 3:
            continue
        areas.append({
            "name": tgt.get("name") or f"area {i + 1}",
            "ring": [[round(float(p[0]), 6), round(float(p[1]), 6)] for p in ring],
        })
    payload = {}
    if plan.get("pad") and (plan.get("hovers") or plan.get("hover")) and _lead(plan):
        payload["lead_in"] = True
    if any(track):
        payload["track"] = track
    if areas:
        payload["areas"] = areas
    if not payload:
        return None
    return "# HERON meta " + json.dumps(payload, separators=(",", ":"))


def annotate_wpl(wpl: str, start, plan: dict | None = None) -> str:
    """Keep the planned takeoff, track satellite, and area in the saved file. Upload strips these lines."""
    if isinstance(start, datetime):
        when = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
    elif start:
        when = takeoff_from_wpl(f"# HERON takeoff {start}")
    else:
        when = None
    lines = [
        ln for ln in wpl.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if not _TAKEOFF_LINE.match(ln.strip()) and not _META_LINE.match(ln.strip())
    ]
    notes = []
    if when is not None:
        notes.append("# HERON takeoff " + when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    meta = _mission_meta_line(plan)
    if meta:
        notes.append(meta)
    if not notes:
        return wpl
    at = 1 if lines and lines[0].strip() == "QGC WPL 110" else 0
    lines[at:at] = notes
    text = "\n".join(lines)
    return text if text.endswith("\n") else text + "\n"


def normalize_wpl(text: str) -> str:
    """Canonical QGC WPL 110 text. Mission Planner rows are tabs; some exports use spaces."""
    if text is None or len(text) > 2_000_000:
        raise ValueError("Waypoint file is too large.")
    raw = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.strip() for ln in raw.split("\n") if ln.strip() and not ln.strip().startswith("#")]
    if not lines:
        raise ValueError("empty waypoint file")
    if lines[0] != "QGC WPL 110":
        raise ValueError(f"need 'QGC WPL 110' header, got {lines[0]!r}")
    if len(lines) > 802:
        raise ValueError("Waypoint file has too many items.")
    out = ["QGC WPL 110"]
    for i, ln in enumerate(lines[1:], start=1):
        parts = ln.split("\t") if "\t" in ln else ln.split()
        if len(parts) != 12:
            raise ValueError(f"line {i}: expected 12 fields, got {len(parts)}")
        out.append("\t".join(parts))
    return "\n".join(out) + "\n"


def _near_m(lat1: float, lon1: float, lat2: float, lon2: float, tol_m: float = 3.0) -> bool:
    mean = math.radians((lat1 + lat2) / 2.0)
    north = (lat1 - lat2) * 111132.92
    east = (lon1 - lon2) * 111132.92 * math.cos(mean)
    return math.hypot(north, east) <= tol_m


def route_from_wpl(text: str, lead_in: bool = False) -> dict:
    """
    Launch point, cruise, and fly-to points from a Mission Planner file.

    A waypoint sitting on Home before the route leaves is the pad hold, not a
    survey point. Yaw and ROI rows belong to the waypoint they are written beside.
    lead_in=True (from the HERON meta line) drops the run-in point HERON adds
    before WP1 so it is not read as a survey point.
    """
    items = parse_wpl(normalize_wpl(text))
    if not items:
        raise ValueError("That waypoint file has no mission items.")
    home = items[0]
    if abs(home["lat"]) <= 1e-7 and abs(home["lon"]) <= 1e-7:
        raise ValueError("Home in that file is 0,0. It needs a real launch point.")
    pad = {"lat": home["lat"], "lon": home["lon"], "name": "Pad"}
    warnings: list[str] = []
    h_agl = None
    speed = None
    hold_s = 0.0
    hovers: list[dict] = []
    current = None
    left_home = False
    # Older files write the aim point before its waypoint. Current files write it after.
    aim_leads = None
    pending_yaw = None
    pending_splash = None
    skip_lead = bool(lead_in)
    lead_ll = None

    def _stash_aim(kind, value):
        nonlocal aim_leads, pending_yaw, pending_splash, current
        if current is None or aim_leads:
            if kind == "yaw":
                pending_yaw = value
            else:
                pending_splash = value
            aim_leads = True
            return
        if kind == "yaw":
            hovers[current]["yaw_deg"] = value
        else:
            hovers[current]["splash_lat"], hovers[current]["splash_lon"] = value
        if aim_leads is None:
            aim_leads = False

    for it in items[1:]:
        cmd = it["command"]
        if cmd == NAV_TAKEOFF:
            h_agl = float(it["alt"])
        elif cmd == DO_CHANGE_SPEED and float(it["p2"]) > 0 and speed is None:
            speed = float(it["p2"])
        elif cmd in (NAV_WAYPOINT, NAV_LOITER_TIME) and (abs(it["lat"]) > 1e-7 or abs(it["lon"]) > 1e-7):
            delay = max(0.0, float(it["p1"]))
            at_home = _near_m(it["lat"], it["lon"], pad["lat"], pad["lon"])
            if at_home and not hovers and not left_home and delay >= 8:
                hold_s = delay
                continue
            if skip_lead and not hovers:
                skip_lead = False
                lead_ll = (float(it["lat"]), float(it["lon"]))
                left_home = True
                pending_yaw = None  # the face-the-lead-in heading, not a survey heading
                continue
            left_home = True
            hovers.append(
                {
                    "lat": float(it["lat"]),
                    "lon": float(it["lon"]),
                    "alt": float(it["alt"]),
                    "loiter_s": delay,
                    "name": f"WP{len(hovers) + 1}",
                }
            )
            current = len(hovers) - 1
            if pending_yaw is not None:
                hovers[current]["yaw_deg"] = pending_yaw
                pending_yaw = None
            if pending_splash is not None:
                hovers[current]["splash_lat"], hovers[current]["splash_lon"] = pending_splash
                pending_splash = None
        elif cmd == CONDITION_YAW:
            _stash_aim("yaw", float(it["p1"]) % 360.0)
        elif cmd == DO_SET_ROI and (it["lat"] or it["lon"]):
            _stash_aim("splash", (float(it["lat"]), float(it["lon"])))
        elif cmd == NAV_RTL:
            break
    if not hovers:
        raise ValueError("That waypoint file has no fly-to points.")
    if h_agl is None or h_agl <= 0:
        h_agl = next((h["alt"] for h in hovers if h["alt"] > 0), None)
        if h_agl is None:
            raise ValueError("That file has no takeoff altitude.")
        warnings.append("No takeoff altitude in the file; playback uses the first waypoint altitude.")
    alts = [h["alt"] for h in hovers if h["alt"] > 0]
    if alts and max(abs(a - h_agl) for a in alts) > 0.5:
        warnings.append("Waypoint altitudes differ; playback uses the takeoff altitude. The file itself is unchanged.")
    if speed is None:
        speed = 8.0
        warnings.append("No speed command in the file; playback uses 8 m/s.")
    delays = [h["loiter_s"] for h in hovers]
    has_yaw = any("yaw_deg" in h for h in hovers)
    has_splash = any("splash_lat" in h for h in hovers)
    return {
        "pad": pad,
        "h_agl": float(h_agl),
        "speed_mps": float(speed),
        "hold_s": float(hold_s),
        "loiter_s": float(delays[0]),
        "loiter_mixed": any(abs(d - delays[0]) > 0.5 for d in delays),
        "roi_mode": "body_yaw" if has_yaw else "per_point" if has_splash else "none",
        "waypoints": hovers,
        "lead_in": lead_ll,
        "warnings": warnings,
    }


def parse_wpl(text: str) -> list[dict]:
    lines = [ln.strip() for ln in text.replace("\r\n", "\n").split("\n") if ln.strip()]
    if not lines:
        raise ValueError("empty waypoint file")
    if lines[0] != "QGC WPL 110":
        raise ValueError(f"need 'QGC WPL 110' header, got {lines[0]!r}")
    items = []
    for i, ln in enumerate(lines[1:]):
        if ln.startswith("#"):
            continue
        parts = ln.split("\t") if "\t" in ln else ln.split()
        if len(parts) != 12:
            raise ValueError(f"line {i + 1}: expected 12 fields, got {len(parts)}")
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
        active_roi = sum(1 for it in items if it["command"] == DO_SET_ROI and (it["lat"] or it["lon"]))
        if active_roi:
            warnings.append(f"{active_roi} fixed-ground ROI commands; body yaw depends on mount configuration.")
        if any(it["command"] == CONDITION_YAW for it in items):
            warnings.append("Body-yaw setpoints approximate moving reflection direction; antenna tilt and vehicle yaw response require validation.")
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
        expect = len(unique_hovers) + (1 if hold else 0) + (1 if _lead(plan) else 0)
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
        "n_yaw": sum(1 for it in items if it["command"] == CONDITION_YAW),
        "n_roi_targets": sum(1 for it in items if it["command"] == DO_SET_ROI and (it["lat"] or it["lon"])),
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
        if s.get("sweep_bearing_deg") is not None:
            lines.append(f"Sweep: {s['sweep_bearing_deg']}°; {s['n_passes']} passes; lane spacing {s['lane_spacing_m']} m; checkpoint maximum {s['max_leg_m']} m.")
        if s.get("footprint_m") is not None:
            pct = f"{s['coverage_pct']}% coverage" if s.get("coverage_pct") is not None else "custom spacing"
            lines.append(f"Reflection strip about {s['footprint_m']} m wide; {pct}.")
        if m.get("roi_mode") == "body_yaw":
            lines.append("On the way to each waypoint the aircraft yaws to that leg's reflection azimuth. The gimbal is not commanded.")
        elif m.get("roi_mode", "per_point") == "none":
            lines.append("No ROI commands. Antenna pointing is not controlled by this mission.")
        else:
            lines.append("On the way to each waypoint the gimbal looks at that splash. It is one ground point per leg, not a continuous track.")
        if s.get("sampled_tracking_error_m") is not None:
            lines.append(f"Sampled straight-leg reflection error: {s['sampled_tracking_error_m']} m. Geometric preview only; turns and aircraft dynamics are not modeled.")
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
            el = hov.get("el")
            extra = f"  aim {prn}" + (f" el {el:.0f}" if el is not None else "")
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
