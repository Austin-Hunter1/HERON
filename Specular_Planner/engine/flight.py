"""Local ArduCopter flight service. No connection or aircraft writes at import time.

One receiver owns the MAVLink stream; transactions use a sequence-numbered inbox.
The deterministic demo uses the same mission validation and preflight policy.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import secrets
import threading
import time
from collections import deque
from pathlib import Path

from engine.export import items_wpl, parse_wpl, validate_wpl


class FlightError(RuntimeError):
    pass


PARAMETERS = (
    "ARMING_CHECK", "FENCE_ENABLE", "FENCE_TYPE", "FENCE_ACTION", "FENCE_RADIUS",
    "FENCE_ALT_MAX", "FENCE_MARGIN", "BATT_FS_LOW_ACT", "BATT_FS_CRT_ACT",
    "FS_GCS_ENABLE", "FS_THR_ENABLE", "SYSID_MYGCS", "RTL_ALT",
)
DEMO_PARAMS = dict(zip(PARAMETERS, (1, 1, 3, 1, 1000, 122, 5, 2, 1, 1, 1, 255, 1500)))
# ArduPilot 4.7 renamed these. Either name fills the original key, converted to its old meaning.
ARMING_BITS = {2: "barometer", 4: "compass", 8: "GPS lock", 16: "inertial sensors", 32: "parameters",
               64: "RC", 128: "board voltage", 256: "battery", 512: "airspeed", 1024: "logging",
               2048: "safety switch", 4096: "GPS configuration", 8192: "system", 16384: "mission",
               32768: "rangefinder", 65536: "camera"}
PARAM_ALIASES = {
    # A negative ARMING_CHECK carries the skip bitmask so launch checks can name what is off.
    "ARMING_SKIPCHK": ("ARMING_CHECK", lambda v: 1 if v == 0 else -v),
    "ARMING_SKIPCHKS": ("ARMING_CHECK", lambda v: 1 if v == 0 else -v),
    "MAV_GCS_SYSID": ("SYSID_MYGCS", lambda v: v),
    "RTL_ALT_M": ("RTL_ALT", lambda v: v * 100),
}
LIVE_TRANSPORTS = {"serial", "udp", "tcp", "herelink_hotspot", "herelink_client"}
AUTO_BAUD = 115200
SEARCHING = "Searching USB for a flight controller…"
# Official ArduPilot (5740) and PX4 (5741) USB ids. This bench board reports 1209:5741 as ArduPilot.
AUTOPILOT_USB = {(0x1209, 0x5740), (0x1209, 0x5741)}
FRESH_SECONDS = 5.0
NAV_COMMANDS = {16, 22}
ALLOWED_COMMANDS = {16, 20, 22, 178, 201}
# Launch never skips these: without them HERON cannot tell what or where the aircraft will fly.
REQUIRED_CHECKS = {"link", "vehicle", "landed", "mission", "pad", "verified"}
DEFAULT_SKIPPED_CHECKS = {"arming", "fence_circle", "route_radius", "failsafes"}
# MAV_SEVERITY: 0-3 error, 4 warning, 5-6 info, 7 debug.
HERON_INFO, HERON_ERROR = 6, 3


def port_is_autopilot(vid, pid, *labels) -> bool:
    """True for a flight-controller USB port. A generic serial adapter stays false."""
    if (vid, pid) in AUTOPILOT_USB:
        return True
    text = " ".join(labels).lower()
    return any(word in text for word in ("ardupilot", "pixhawk", "cubepilot", "cubeorange"))


def serial_port_records():
    """USB serial ports. autopilot marks the port auto-connect may open."""
    from serial.tools import list_ports
    records = []
    for port in list_ports.comports():
        labels = (port.description or "", port.manufacturer or "", port.product or "")
        records.append({
            "device": port.device,
            "description": port.description or port.device,
            "autopilot": port_is_autopilot(port.vid, port.pid, *labels),
        })
    return records


def distance(a, b):
    p, q = math.radians(a["lat"]), math.radians(b["lat"])
    dp, dl = q - p, math.radians(b["lon"] - a["lon"])
    v = math.sin(dp / 2) ** 2 + math.cos(p) * math.cos(q) * math.sin(dl / 2) ** 2
    return 6371000 * 2 * math.asin(min(1, math.sqrt(max(0, v))))


def bearing(a, b):
    p, q = math.radians(a["lat"]), math.radians(b["lat"])
    dl = math.radians(b["lon"] - a["lon"])
    x = math.sin(dl) * math.cos(q)
    y = math.cos(p) * math.sin(q) - math.sin(p) * math.cos(q) * math.cos(dl)
    return math.degrees(math.atan2(x, y)) % 360


def flight_items(wpl):
    report = validate_wpl(wpl)
    if not report["ok"]:
        raise FlightError("; ".join(report["errors"]))
    items = parse_wpl(wpl)
    if len(items) > 700 or len(items) < 4:
        raise FlightError("Mission must contain 4–700 items including Home and RTL.")
    for it in items:
        if not all(math.isfinite(v) for v in it.values()):
            raise FlightError("Mission contains a non-finite number.")
        if it["command"] not in ALLOWED_COMMANDS:
            raise FlightError(f"Unsupported mission command {it['command']}.")
        if not (-90 <= it["lat"] <= 90 and -180 <= it["lon"] <= 180):
            raise FlightError("Mission coordinates are out of range.")
        if it["seq"] and it["command"] in NAV_COMMANDS:
            if it["frame"] != 3 or not 2 <= it["alt"] <= 122:
                raise FlightError("Flight altitudes must be 2–122 m above Home (frame 3).")
        if it["command"] == 178 and not 0.5 <= it["p2"] <= 25:
            raise FlightError("Mission speed must be 0.5–25 m/s.")
    if items[-1]["command"] != 20 or sum(i["command"] == 22 for i in items) != 1:
        raise FlightError("Mission must have one initial TAKEOFF and finish with RTL.")
    return items


def has_position(item):
    """ArduPilot reports Home (row 0) as 0,0 until it has ever had a GPS fix."""
    return bool(item) and (abs(item["lat"]) > 1e-7 or abs(item["lon"]) > 1e-7)


def onboard_rois(items):
    """DO_SET_ROI aim points, each tied to the next waypoint flown while it applies (a 0,0 ROI clears it)."""
    rois = []
    for n, it in enumerate(items[1:], 1):
        if it["command"] != 201 or not has_position(it):
            continue
        wp = next((j["seq"] for j in items[n + 1:] if j["command"] in NAV_COMMANDS and has_position(j)), None)
        rois.append({"seq": it["seq"], "lat": it["lat"], "lon": it["lon"], "wp": wp})
    return rois


def mission_summary(items, home=None, home_source="plan"):
    """What the aircraft will fly: Home, TAKEOFF, waypoints, RTL. Times are estimates."""
    if home is None and has_position(items[0]):
        home = items[0]
    takeoff = next((i["alt"] for i in items if i["command"] == 22), 0)
    wps = [i for i in items[1:] if i["command"] == 16]
    speed = next((i["p2"] for i in reversed(items) if i["command"] == 178 and i["p2"] > 0), 5.0)
    path = ([home] if home else []) + wps + ([home] if home else [])
    length = sum(distance(a, b) for a, b in zip(path, path[1:]))
    dwell = sum(max(0, i["p1"]) for i in wps)
    climb = max([takeoff] + [i["alt"] for i in wps])
    return {
        "n_wp": len(wps), "takeoff_alt": takeoff, "max_alt": climb, "length_m": round(length),
        "speed": speed, "est_s": round(length / speed + dwell + climb / 2.5 + climb / 1.0),
        "path": [{"seq": i["seq"], "lat": i["lat"], "lon": i["lon"], "alt": i["alt"]} for i in wps],
        "home": {"lat": home["lat"], "lon": home["lon"]} if home else None,
        "home_source": home_source if home else None,
    }


def compare_items(expected, actual):
    """Compare executable fields, tolerating wire precision and Copter normalization.

ArduPilot synthesizes row zero from actual Home. Check Home separately; never
pretend the WPL Home row changes the aircraft's return point.
"""
    if len(expected) != len(actual):
        raise FlightError(f"Read-back has {len(actual)} items; expected {len(expected)}.")
    for a, b in zip(expected[1:], actual[1:]):
        if a["seq"] != b["seq"] or a["command"] != b["command"]:
            raise FlightError(f"Read-back differs at item {a['seq']} (command/order).")
        cmd = a["command"]
        fields = []
        if cmd in NAV_COMMANDS or (cmd == 201 and (a["lat"] or a["lon"])):
            if {0: 0, 5: 0, 3: 3, 6: 3}.get(b["frame"]) != a["frame"]:
                raise FlightError(f"Read-back altitude frame differs at item {a['seq']}.")
            fields += [("lat", 0.000001), ("lon", 0.000001), ("alt", 0.05)]
        if cmd in NAV_COMMANDS:
            fields += [("p1", 0.01), ("p2", 0.01), ("p3", 0.01), ("p4", 0.01)]
        elif cmd == 178:
            fields += [("p1", 0.01), ("p2", 0.01)]
        elif cmd == 201:
            fields += [("lat", 0.000001), ("lon", 0.000001), ("alt", 0.05)]
        # RTL has no position arguments on Copter; speed ignores p3/p4.
        for key, tol in fields:
            if key in {"lat", "lon"}:
                tol = max(tol, b.get("coordinate_tolerance", tol))
            if not math.isfinite(b[key]) or abs(a[key] - b[key]) > tol:
                raise FlightError(f"Read-back differs at item {a['seq']} ({key}).")


class FlightService:
    def __init__(self, transports=None, settings_path=None, skipped=()):
        self.settings_path = settings_path
        self.skipped = set(skipped) - REQUIRED_CHECKS
        if settings_path:
            try:
                saved = json.loads(Path(settings_path).read_text(encoding="utf-8"))
                self.skipped = set(saved.get("skipped_checks", [])) - REQUIRED_CHECKS
            except (OSError, ValueError, AttributeError):
                pass
        self.lock = threading.RLock()
        self.operation_lock = threading.Lock()
        self.cancel = threading.Event()
        self.transports = set(transports) if transports else None
        self.link = None
        self.link_info = None
        self.plans = {}
        self.items_cache = {}
        self.verified = None
        self.onboard = None
        self.job = {"state": "idle", "action": None, "message": "Connect an aircraft or try the demo."}
        self.events = deque(maxlen=150)
        self.event_seq = 0
        self.events_lock = threading.Lock()
        self.auto = {"enabled": False, "status": "", "paused": None, "ports": []}
        self.auto_retry = {}
        self.auto_wake = threading.Event()

    def event(self, message, source="heron", severity=HERON_INFO):
        with self.events_lock:
            self.event_seq += 1
            self.events.append({"id": self.event_seq, "time": time.time(), "text": str(message)[:300],
                                "source": source, "severity": int(severity)})

    def set_check(self, key, enabled):
        if key in REQUIRED_CHECKS:
            raise FlightError("That launch check cannot be turned off.")
        with self.lock:
            (self.skipped.discard if enabled else self.skipped.add)(str(key))
            if self.settings_path:
                Path(self.settings_path).write_bytes(
                    json.dumps({"skipped_checks": sorted(self.skipped)}, indent=2).encode("utf-8"))
        return self.snapshot()

    def start_auto_connect(self):
        """Connect to the first flight controller that appears on USB. Live service only."""
        self.auto.update(enabled=True, status=SEARCHING)
        threading.Thread(target=self._auto_loop, daemon=True, name="flight-autoconnect").start()

    def set_auto(self, enabled):
        with self.lock:
            self.auto.update(enabled=bool(enabled), paused=None,
                             status=SEARCHING if enabled else "Auto-connect is off.")
            self.auto_retry.clear()
        self.auto_wake.set()
        return self.snapshot()

    def _auto_loop(self):
        while True:
            try:
                self._auto_step()
            except Exception as exc:
                self.auto["status"] = f"Auto-connect error: {exc}"
            self.auto_wake.wait(2)
            self.auto_wake.clear()

    def _auto_step(self):
        try:
            ports = serial_port_records()
        except ImportError:
            self.auto.update(ports=[], status="Install requirements.txt to use USB.")
            return
        self.auto["ports"] = ports
        found = [p["device"] for p in ports if p["autopilot"]]
        if self.auto["paused"] not in found:
            self.auto["paused"] = None
        link = self.link
        if link is not None:
            # A pulled cable closes the link. Drop it so the next plug-in reconnects.
            if isinstance(link, MavlinkLink) and link.closed.is_set() and self.job["state"] != "running":
                self.disconnect(manual=False)
                self.event("Aircraft link closed. Waiting for the flight controller to come back.")
            return
        if not self.auto["enabled"]:
            return
        now = time.monotonic()
        ready = [d for d in found if d != self.auto["paused"] and now >= self.auto_retry.get(d, 0)]
        if not ready:
            if not found:
                self.auto["status"] = SEARCHING
            elif self.auto["paused"]:
                self.auto["status"] = f"Disconnected from {self.auto['paused']}. Press Connect or replug it to reconnect."
            return
        device = ready[0]
        self.auto["status"] = f"Found a flight controller on {device}. Connecting…"
        try:
            self.connect({"transport": "serial", "device": device, "baud": AUTO_BAUD, "system_id": 1})
        except Exception as exc:
            self.auto_retry[device] = time.monotonic() + 10
            self.auto["status"] = f"{device}: {exc} Retrying in 10 s."
            return
        self.auto["status"] = f"Connected on {device}."
        try:
            self.submit("refresh")
        except FlightError:
            pass

    def register_plan(self, wpl, meta, plan_id=None):
        # Keep planning/export usable even when a plan is outside live-flight limits.
        plan_id = plan_id or secrets.token_hex(16)
        with self.lock:
            self.plans[plan_id] = {"wpl": wpl, "meta": copy.deepcopy(meta),
                                   "hash": hashlib.sha256(wpl.encode()).hexdigest()}
            while len(self.plans) > 20:
                self.plans.pop(next(iter(self.plans)))
        return plan_id

    def plan(self, plan_id):
        with self.lock:
            if plan_id not in self.plans:
                raise FlightError("Build the mission again; this plan is no longer in this server session.")
            return self.plans[plan_id]

    def connect(self, options):
        with self.operation_lock:
            if self.link is not None:
                raise FlightError("Disconnect the current aircraft before changing connections.")
            kind = options.get("transport", "demo")
            if self.transports is not None and kind not in self.transports:
                raise FlightError("This page cannot open that connection type.")
            self.cancel.clear()
            if kind == "demo":
                self.link = DemoLink(self.event, options.get("home"))
            else:
                self.link = MavlinkLink(options, self.event)
            self.link_info = {"transport": kind, "device": options.get("device") if kind == "serial" else None,
                              "baud": int(options.get("baud", AUTO_BAUD)) if kind == "serial" else None}
            self.verified = None
            self.onboard = None
            self.event("Connected to simulated aircraft." if kind == "demo" else "Aircraft heartbeat received.")
            self.job = {"state": "idle", "action": None, "message": "Connected. Refresh aircraft checks before launch."}
            return self.snapshot()

    def disconnect(self, manual=True):
        with self.lock:
            if manual and self.link_info and self.link_info.get("device"):
                # Stay off this port until it is replugged or Connect is pressed.
                self.auto["paused"] = self.link_info["device"]
            demo = bool(self.link and self.link.snapshot().get("transport") == "demo")
            running = self.job["state"] == "running"
        # A real link stays up until the running command ends. The demo can leave immediately.
        if running and not demo:
            raise FlightError("An operation is running. Use Return home to interrupt launch.")
        if running:
            self.cancel.set()
        with self.operation_lock:
            if self.link:
                self.link.close()
            self.link = None
            self.link_info = None
            self.verified = None
            self.onboard = None
            self.event("Disconnected. Aircraft failsafes remain on the flight controller.")
            self.job = {"state": "idle", "action": None, "message": "Disconnected. Connect an aircraft or try the demo."}
        return self.snapshot()

    def preflight(self, plan_id=None):
        link = self.link
        s = link.snapshot() if link else {}
        checks = []

        def check(key, ok, message):
            checks.append({"key": key, "ok": bool(ok), "message": message,
                           "required": key in REQUIRED_CHECKS, "enabled": key not in self.skipped})

        fresh = s.get("fresh", {})
        check("link", s.get("connected"), "Aircraft heartbeat is current")
        check("vehicle", s.get("supported"), "ArduPilot Copter detected")
        check("landed", not s.get("armed", True) and s.get("landed") == 1 and fresh.get("landed"),
              "Aircraft is disarmed and on the ground")
        check("gps", fresh.get("gps") and s.get("gps_fix", 0) >= 3 and s.get("satellites", 0) >= 6,
              "Fresh 3D GPS fix with at least 6 satellites")
        check("ekf", fresh.get("ekf") and s.get("ekf_ok"), "Navigation estimate is healthy")
        check("sensors", fresh.get("health") and s.get("sensors_ok"), "Enabled aircraft sensors report healthy")
        check("battery", fresh.get("battery") and s.get("battery_pct", -1) >= 30,
              "Fresh battery reading of at least 30%")
        home, pos = s.get("home"), s.get("position")
        check("home", fresh.get("home") and fresh.get("position") and home and pos and distance(home, pos) < 30,
              "Actual Home is within 30 m of the aircraft")
        p = s.get("params", {})
        check("params", s.get("params_fresh"), "Flight safety parameters have been read recently")
        arming = p.get("ARMING_CHECK")
        skipped = ", ".join(name for bit, name in ARMING_BITS.items() if int(-arming) & bit) if arming and arming < 0 else ""
        check("arming", arming == 1, "All ArduPilot pre-arm checks are enabled"
              + (f": skipped {skipped or 'some checks'}. Clear ARMING_SKIPCHK in Mission Planner" if arming and arming < 0 else ""))
        fence_type = int(p.get("FENCE_TYPE", 0))
        fence_on = p.get("FENCE_ENABLE") == 1 and p.get("FENCE_ACTION", 0) > 0
        check("fence_alt", fence_on and fence_type & 1, "Altitude fence is enabled with an action")
        check("fence_circle", fence_on and fence_type & 2, "Circular fence is enabled with an action")
        check("failsafes", all(p.get(k, 0) > 0 for k in ("BATT_FS_LOW_ACT", "BATT_FS_CRT_ACT", "FS_GCS_ENABLE", "FS_THR_ENABLE")),
              "Battery, ground-station and RC failsafe actions are enabled")
        check("gcs", p.get("SYSID_MYGCS") == 255, "Aircraft monitors this ground station (system 255)")
        items = None
        try:
            items = flight_items(self.plan(plan_id)["wpl"])
            check("mission", True, f"Mission has {len(items)} items, TAKEOFF and RTL")
        except (FlightError, ValueError) as exc:
            check("mission", False, str(exc) if plan_id else "Build a mission before uploading")
        # A mission read from the drone has no pad: row 0 is only a Home placeholder and it takes off where it is.
        if items and not self._from_aircraft(plan_id):
            check("pad", home and distance(items[0], home) < 30, "Planned launch pad is within 30 m of actual Home")
        if items:
            margin = max(5, p.get("FENCE_MARGIN", 5))
            radius = p.get("FENCE_RADIUS", 0) - margin
            ceiling = min(122, p.get("FENCE_ALT_MAX", 0) - margin)
            nav = [i for i in items[1:] if i["command"] in NAV_COMMANDS]
            top = max((i["alt"] for i in nav), default=0)
            far = max((distance(home, i) for i in nav), default=0) if home else 0
            check("route_alt", top <= ceiling and all(2 <= i["alt"] for i in nav),
                  "Route stays below the altitude fence, including margin"
                  + (f": route flies at {top:g} m but the fence allows {ceiling:g} m; lower Above Home on Plan"
                     if top > ceiling else ""))
            check("route_radius", home and far < radius,
                  "Route stays inside the circular fence, including margin"
                  + (f": route reaches {far:.0f} m from Home but the fence allows {radius:g} m"
                     if home and far >= radius else "" if home else ": needs a GPS Home"))
            check("rtl_alt", 0 <= p.get("RTL_ALT", -1) / 100 <= ceiling,
                  "Configured return altitude fits below the altitude fence")
        verified = self.verified
        check("verified", verified and verified["plan_id"] == plan_id and link and verified["generation"] == s.get("generation"),
              "This mission was uploaded and read back from this aircraft")
        return {"ready": all(c["ok"] or not c["enabled"] for c in checks), "checks": checks}

    def _target(self, telemetry):
        """The mission item the aircraft is flying to, when the verified mission is known."""
        verified = self.verified
        seq = telemetry.get("mission_current")
        if not verified or seq is None:
            return None
        items = self._items(verified["plan_id"])
        if not 0 < seq < len(items) or items[seq]["command"] not in NAV_COMMANDS:
            return None
        it = items[seq]
        wps = [i["seq"] for i in items if i["command"] == 16 and i["seq"] > 0]
        return {"seq": seq, "lat": it["lat"], "lon": it["lon"], "alt": it["alt"],
                "wp": sum(1 for s in wps if s <= seq), "total": len(wps)}

    def _items(self, plan_id):
        if plan_id not in self.plans:
            return []
        entry = self.items_cache.get(plan_id)
        if entry is None:
            try:
                items = flight_items(self.plans[plan_id]["wpl"])
                entry = (items, mission_summary(items))
            except (FlightError, ValueError):
                entry = ([], None)
            self.items_cache[plan_id] = entry
            while len(self.items_cache) > 20:
                self.items_cache.pop(next(iter(self.items_cache)))
        return entry[0]

    def _from_aircraft(self, plan_id):
        with self.lock:
            return self.plans.get(plan_id, {}).get("meta", {}).get("source") == "aircraft"

    def _summary(self, plan_id):
        items = self._items(plan_id)
        entry = self.items_cache.get(plan_id)
        if not entry or not entry[1]:
            return None
        if self._from_aircraft(plan_id):
            home = (self.link.snapshot() if self.link else {}).get("home")
            if home:
                return dict(mission_summary(items, home, "aircraft"), plan_id=plan_id)
            return dict(entry[1], plan_id=plan_id, home_source="mission" if entry[1]["home"] else None)
        return dict(entry[1], plan_id=plan_id)

    def _set_onboard(self, items, plan_id, source):
        """Remember the mission read back from the aircraft, flyable or not."""
        onboard = {"source": source, "count": len(items), "plan_id": plan_id, "error": None,
                   "generation": self.link.snapshot().get("generation") if self.link else None,
                   "path": [{"seq": i["seq"], "lat": i["lat"], "lon": i["lon"], "alt": i["alt"], "command": i["command"]}
                            for i in items[1:] if i["command"] in NAV_COMMANDS and (i["lat"] or i["lon"])],
                   "roi": onboard_rois(items)}
        if plan_id:
            onboard.update(self._summary(plan_id) or {})
        self.onboard = onboard
        return onboard

    def snapshot(self, plan_id=None):
        with self.lock:
            s = self.link.snapshot() if self.link else {"connected": False, "transport": None}
            with self.events_lock:
                events = list(self.events)
            auto = {k: copy.deepcopy(v) for k, v in self.auto.items()}
            onboard = self.onboard
            if onboard and onboard.get("generation") != s.get("generation"):
                onboard = None
            return {"telemetry": s, "job": dict(self.job), "verified": copy.deepcopy(self.verified),
                    "preflight": self.preflight(plan_id), "events": events, "auto": auto,
                    "link": copy.deepcopy(self.link_info), "target": self._target(s),
                    "mission": self._summary(plan_id) if plan_id else None, "onboard": copy.deepcopy(onboard)}

    def submit(self, action, plan_id=None, confirmation=None):
        if action not in {"refresh", "upload", "launch", "read"}:
            raise FlightError("Unknown flight action.")
        with self.lock:
            if not self.link or not self.link.snapshot().get("connected"):
                raise FlightError("Connect to an aircraft first.")
            if self.job["state"] == "running" or not self.operation_lock.acquire(blocking=False):
                raise FlightError("Another aircraft operation is still running.")
            if action == "launch" and confirmation != "LAUNCH":
                self.operation_lock.release()
                raise FlightError("Confirm launch before arming.")
            self.cancel.clear()
            self.job = {"state": "running", "action": action, "message": f"Starting {action}…"}
        threading.Thread(target=self._run, args=(action, plan_id), daemon=True, name="flight-operation").start()
        return {"accepted": True}

    def phase(self, message):
        self.guard()
        with self.lock:
            self.job["message"] = message
        self.event(message)

    def guard(self):
        if self.cancel.is_set():
            raise FlightError("Launch/upload interrupted by Return home.")
        if not self.link or not self.link.snapshot().get("connected"):
            raise FlightError("Aircraft link is stale. No further launch commands were sent.")

    def _run(self, action, plan_id):
        try:
            link = self.link
            if action == "read":
                result = self._read(link)
                flyable = bool(self.onboard and self.onboard["plan_id"])
                with self.lock:
                    self.job.update(state="complete" if flyable else "error", message=result)
                self.event(result, severity=HERON_INFO if flyable else 4)
                return
            self.phase("Reading aircraft status and safety parameters…")
            # Upload works before GPS Home exists; launch re-checks the pad against Home.
            link.refresh(self.cancel, need_home=action == "launch")
            if action in {"upload", "launch"}:
                items = flight_items(self.plan(plan_id)["wpl"])
                s = link.snapshot()
                if s.get("armed") or s.get("landed") != 1 or not s.get("fresh", {}).get("landed"):
                    raise FlightError("Mission changes require a disarmed aircraft on the ground.")
                if not self._from_aircraft(plan_id) and (s.get("home") or action == "launch") and (
                        not s.get("home") or distance(items[0], s["home"]) >= 30):
                    raise FlightError("Mission launch pad does not match actual aircraft Home. Rebuild at this field.")
            if action == "upload":
                self.verified = None
                self.phase("Uploading mission…")
                link.upload(items, self.cancel)
                self.phase("Reading mission back from aircraft…")
                readback = link.download(self.cancel)
                compare_items(items, readback)
                self.guard()
                self.verified = {"plan_id": plan_id, "generation": link.snapshot()["generation"],
                                 "count": len(items), "hash": self.plan(plan_id)["hash"]}
                self._set_onboard(readback, plan_id, "heron")
                result = "Mission uploaded and verified against aircraft read-back."
            elif action == "launch":
                self._require_ready(plan_id)
                self.phase("Verifying stored mission again before launch…")
                try:
                    compare_items(items, link.download(self.cancel))
                except Exception:
                    self.verified = None
                    raise
                link.refresh(self.cancel)
                self._require_ready(plan_id)
                self.phase("Selecting GUIDED mode…")
                link.mode(4, self.cancel)
                self._require_ready(plan_id)
                self.phase("Arming aircraft…")
                link.arm(self.cancel)
                self._require_ready(plan_id, allow_armed=True)
                if link.snapshot().get("mode_id") != 4:
                    raise FlightError("Flight mode changed during arming; launch stopped.")
                self.phase("Resetting mission to TAKEOFF…")
                link.set_current(1, self.cancel)
                if link.snapshot().get("mode_id") != 4:
                    raise FlightError("Pilot changed flight mode; launch stopped.")
                self.phase("Starting AUTO mission…")
                link.mode(3, self.cancel)
                link.start(self.cancel)
                self.phase("Waiting for aircraft to report takeoff…")
                link.wait_airborne(self.cancel)
                result = "Aircraft reports takeoff. Monitoring mission; Return home remains available."
            else:
                result = "Aircraft status and safety parameters refreshed."
            with self.lock:
                self.job.update(state="complete", message=result)
            self.event(result)
        except Exception as exc:
            message = str(exc)
            if action == "launch":
                message += " Check aircraft state; use Return home or the RC override if needed."
            with self.lock:
                self.job.update(state="error", message=message)
            self.event(message, severity=HERON_ERROR)
        finally:
            self.operation_lock.release()

    def _read(self, link):
        self.phase("Reading mission from aircraft…")
        items = link.download(self.cancel)
        self.guard()
        if len(items) < 2:
            self.onboard = None
            raise FlightError("The aircraft has no mission stored.")
        plan_id, error = None, None
        try:
            if any(int(i.get("autocontinue", 1)) != 1 for i in items):
                raise FlightError("Mission has items that wait for the pilot (autocontinue off).")
            wpl = items_wpl(items)
            checked = flight_items(wpl)
            compare_items(checked, items)
            plan_id = self.register_plan(wpl, {"source": "aircraft"})
        except (FlightError, ValueError) as exc:
            error = f"HERON can show this mission but will not fly it: {exc}"
        with self.lock:
            if plan_id:
                # The aircraft holds exactly this mission. Launch still downloads and compares it again.
                self.verified = {"plan_id": plan_id, "generation": link.snapshot()["generation"],
                                 "count": len(items), "hash": self.plans[plan_id]["hash"]}
            onboard = self._set_onboard(items, plan_id, "aircraft")
            onboard["error"] = error
        n = len(onboard["path"])
        return error or f"Read {len(items) - 1} mission items from the aircraft ({n} waypoints). Ready to fly after launch checks."

    def _require_ready(self, plan_id, allow_armed=False):
        self.guard()
        failed = [c["message"] for c in self.preflight(plan_id)["checks"] if not c["ok"] and c["enabled"]
                  and not (allow_armed and c["key"] == "landed")]
        if allow_armed:
            s = self.link.snapshot()
            if not s.get("armed") or s.get("landed") != 1 or not s.get("fresh", {}).get("landed"):
                failed.append("Aircraft must remain armed and on the ground before starting AUTO")
        if failed:
            raise FlightError("Launch blocked: " + "; ".join(failed))

    def rtl(self):
        if not self.link or not self.link.snapshot().get("connected"):
            raise FlightError("No current aircraft link. Use your RC override; the app cannot deliver RTL.")
        self.cancel.set()
        self.link.mode(6, None)
        self.event("Aircraft confirmed RTL mode. Return and landing are not yet complete.")
        return {"message": "Aircraft confirmed RTL mode."}


class DemoLink:
    """UI simulator only: no serial ports, sockets, or MAVLink commands."""
    SPEED = 20  # Accelerated demonstration, clearly marked in UI.

    def __init__(self, event, home=None):
        self.event = event
        self.items = []
        self.generation = secrets.token_hex(8)
        self.connected = True
        self.armed = False
        self.mode_id = 0
        self.current = 0
        self.home = {"lat": 40.086045, "lon": -105.233634, "alt_msl": 1625}
        try:
            lat, lon = float(home["lat"]), float(home["lon"])
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                self.home.update(lat=lat, lon=lon)
        except (TypeError, KeyError, ValueError):
            pass
        self.position = dict(self.home)
        self.alt = 0
        self.last = time.monotonic()
        self.flying = False
        self.dwell = 0
        self.vertical_speed = 0
        self.heading = 0.0
        self.roll = 0.0
        self.pitch = 0.0
        self.ground_speed = 0.0
        self.battery = 96.0
        self.step_lock = threading.Lock()
        self.say("ArduCopter V4.7.0 (simulated)")
        self.say("EKF3 IMU0 is using GPS")

    def say(self, text, severity=6):
        """Imitate an ArduPilot STATUSTEXT so the Messages panel behaves as with real hardware."""
        self.event(text, "ardupilot", severity)

    def _fly_to(self, target, dt):
        """Move toward target like a multirotor: yaw to the leg, bank in turns, nose down in cruise."""
        d = distance(self.position, target)
        turn = 0.0
        if d > 2:
            want = bearing(self.position, target)
            error = (want - self.heading + 540) % 360 - 180
            turn = max(-120 * dt, min(120 * dt, error))
            self.heading = (self.heading + turn) % 360
        f = min(1, self.SPEED * dt / max(d, 0.001))
        for key in ("lat", "lon"):
            self.position[key] += (target[key] - self.position[key]) * f
        self.ground_speed = min(self.SPEED, d / dt) if dt > 0 and d > 0.5 else 0
        rate = turn / dt if dt > 0 else 0
        k = min(1, dt * 3)
        self.roll += (max(-25, min(25, rate * 0.3)) - self.roll) * k
        self.pitch += (-8 * self.ground_speed / self.SPEED - self.pitch) * k
        return d

    def snapshot(self):
        with self.step_lock:
            return self._step()

    def _step(self):
        now = time.monotonic()
        dt = min(1, now - self.last)
        self.last = now
        previous_alt = self.alt
        if self.flying and self.armed:
            self.battery = max(0, self.battery - dt * 0.05)
            it = self.items[self.current] if self.current < len(self.items) else {"command": 20}
            if self.mode_id == 6 or it["command"] == 20:
                self.mode_id = 6
                d = self._fly_to(self.home, dt)
                if d < 2:
                    self.alt = max(0, self.alt - dt * 15)
                    if self.alt == 0:
                        self.armed = False
                        self.flying = False
                        self.say("Disarming motors")
                        self.event("Demo aircraft landed and disarmed.")
            elif it["command"] in NAV_COMMANDS:
                self.alt += max(-15 * dt, min(15 * dt, it["alt"] - self.alt))
                d = self._fly_to(it, dt)
                if d < 2 and abs(self.alt - it["alt"]) < 1:
                    self.dwell += dt
                    if self.dwell >= min(3, it["p1"]):
                        self.say(f"Reached command #{self.current}")
                        self.current += 1
                        self.dwell = 0
            else:
                self.current += 1
        else:
            self.ground_speed = 0
            self.roll *= 0.5
            self.pitch *= 0.5
        if dt > 0.001:
            self.vertical_speed = (self.alt - previous_alt) / dt
        fresh = {k: self.connected for k in ("landed", "gps", "ekf", "health", "battery", "home", "position", "attitude")}
        return {"connected": self.connected, "transport": "demo", "supported": True, "system_id": 1,
                "generation": self.generation, "armed": self.armed, "mode_id": self.mode_id,
                "mode": {0: "STABILIZE", 3: "AUTO", 4: "GUIDED", 6: "RTL"}.get(self.mode_id),
                "landed": 2 if self.alt > 0 else 1, "gps_fix": 3, "satellites": 16,
                "ekf_ok": True, "sensors_ok": True, "battery_pct": round(self.battery),
                "battery_voltage": 21.0 + 4.2 * self.battery / 100,
                "battery_current": round(0.4 if not self.armed else 6.0 + abs(self.vertical_speed), 1),
                "home": dict(self.home), "position": dict(self.position), "relative_alt": self.alt,
                "speed": self.ground_speed, "heading": self.heading, "roll": self.roll, "pitch": self.pitch,
                "course": self.heading if self.ground_speed >= 0.5 else None,
                "vertical_speed": self.vertical_speed,
                "mission_current": self.current, "params": dict(DEMO_PARAMS), "params_fresh": True,
                "fresh": fresh, "heartbeat_age": 0, "status_text": "SIMULATED AIRCRAFT · accelerated demonstration"}

    def refresh(self, cancel, need_home=True):
        self._check(cancel)

    def _check(self, cancel):
        if cancel and cancel.is_set():
            raise FlightError("Demo operation canceled.")

    def upload(self, items, cancel):
        self._check(cancel)
        self.items = copy.deepcopy(items)
        self.say(f"Mission: {len(items) - 1} commands received")

    def download(self, cancel):
        self._check(cancel)
        return copy.deepcopy(self.items)

    def mode(self, mode, cancel):
        self._check(cancel)
        self.mode_id = mode
        if mode == 6 and self.armed:
            self.flying = True

    def arm(self, cancel):
        self._check(cancel)
        self.armed = True
        self.say("Arming motors")

    def set_current(self, seq, cancel):
        self._check(cancel)
        self.current = seq

    def start(self, cancel):
        self._check(cancel)
        if self.mode_id != 3:
            raise FlightError("Aircraft is no longer in AUTO.")
        self.flying = True

    def wait_airborne(self, cancel):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self._check(cancel)
            if self.snapshot()["relative_alt"] >= 2:
                return
            time.sleep(0.1)
        raise FlightError("Demo takeoff timed out.")

    def close(self):
        self.connected = False


class MavlinkLink:
    def __init__(self, options, event):
        import os
        os.environ["MAVLINK20"] = "1"
        try:
            from pymavlink import mavutil
        except ImportError as exc:
            raise FlightError("Install the updated requirements.txt to enable MAVLink connections.") from exc
        self.mavutil = mavutil
        self.event = event
        self.cv = threading.Condition(threading.RLock())
        self.tx = threading.RLock()
        self.inbox = deque(maxlen=4096)
        self.counter = 0
        self.legacy_coordinates = False
        self.closed = threading.Event()
        self.stamps = {}
        self.param_times = {}
        self.s = {"transport": options["transport"], "armed": False, "params": {},
                  "generation": secrets.token_hex(8), "supported": False}
        self.system = int(options.get("system_id", 1))
        self.component = 1
        if not 1 <= self.system <= 254:
            raise FlightError("Aircraft system ID must be 1–254.")
        kind = options["transport"]
        if kind == "serial":
            from serial.tools import list_ports
            endpoint = options.get("device", "")
            if endpoint not in {p.device for p in list_ports.comports()}:
                raise FlightError("Select an available serial port. Refresh ports after plugging in USB.")
            baud = int(options.get("baud", 115200))
            if baud not in {57600, 115200, 230400, 460800, 921600}:
                raise FlightError("Unsupported baud rate.")
        elif kind in {"udp", "tcp", "herelink_hotspot", "herelink_client"}:
            import ipaddress
            default_host = "127.0.0.1" if kind == "tcp" else "192.168.42.129" if kind == "herelink_client" else "0.0.0.0"
            host = options.get("host", default_host)
            ipaddress.IPv4Address(host)
            port = int(options.get("port", 5760 if kind == "tcp" else 14552 if kind == "herelink_client" else 14550))
            if not 1024 <= port <= 65535:
                raise FlightError("Port must be 1024–65535.")
            protocol = "tcp" if kind == "tcp" else "udpout" if kind == "herelink_client" else "udpin"
            endpoint = f"{protocol}:{host}:{port}"
            baud = 115200
        else:
            raise FlightError("Choose Demo, USB/serial, ELRS UDP, HereLink or SITL TCP.")
        # USB and SITL carry full-rate telemetry. Radio links stay at 2 Hz.
        self.fast = kind in {"serial", "tcp"}
        try:
            self.conn = mavutil.mavlink_connection(endpoint, baud=baud, source_system=255,
                                                  source_component=190, dialect="ardupilotmega", autoreconnect=False)
        except OSError as exc:
            if "denied" in str(exc).lower():
                raise FlightError(f"{endpoint} is open in another program. Close Mission Planner or QGroundControl first.") from exc
            raise FlightError(f"Could not open {endpoint}: {exc}") from exc
        self.reader = threading.Thread(target=self._receive, daemon=True, name="mavlink-reader")
        self.reader.start()
        try:
            self._wait_state(lambda s: s.get("connected") and s.get("supported"), None, 12, "ArduPilot Copter heartbeat")
            self._request_streams()
        except Exception:
            self.close()
            raise

    def _send(self, method, *args, cancel=None):
        with self.tx:
            if self.closed.is_set():
                raise FlightError("Aircraft connection is closed.")
            if cancel and cancel.is_set():
                raise FlightError("Operation interrupted by Return home.")
            if cancel and not self.snapshot().get("connected"):
                raise FlightError("Aircraft heartbeat expired; no further commands sent.")
            getattr(self.conn.mav, method)(*args)

    def _receive(self):
        heartbeat_at = 0
        try:
            while not self.closed.is_set():
                now = time.monotonic()
                if now - heartbeat_at >= 1:
                    self._send("heartbeat_send", 6, 8, 0, 0, 4)  # GCS heartbeat for onboard GCS failsafe.
                    heartbeat_at = now
                msg = self.conn.recv_match(blocking=True, timeout=0.2)
                if msg is None or msg.get_type() == "BAD_DATA":
                    continue
                if msg.get_srcSystem() != self.system or msg.get_srcComponent() != self.component:
                    continue
                kind = msg.get_type()
                # Do not consume another GCS's transaction messages.
                if getattr(msg, "target_system", 0) not in (0, 255) or getattr(msg, "target_component", 0) not in (0, 190):
                    continue
                with self.cv:
                    self.counter += 1
                    self.inbox.append((self.counter, msg))
                    self._update(kind, msg, now)
                    self.cv.notify_all()
        except Exception as exc:
            if not self.closed.is_set():
                self.event(f"Aircraft connection failed: {exc}")
                self.closed.set()

    def _update(self, kind, m, now):
        s = self.s
        if kind == "HEARTBEAT":
            self.stamps["heartbeat"] = now
            s.update(armed=bool(m.base_mode & 128), mode_id=m.custom_mode,
                     mode=self.mavutil.mode_string_v10(m), system_id=self.system,
                     supported=m.autopilot == 3 and m.type in {2, 3, 4, 13, 14, 15, 29})
        elif kind == "GLOBAL_POSITION_INT":
            boot = m.time_boot_ms
            if boot + 5000 < s.get("boot_ms", 0):
                s["generation"] = secrets.token_hex(8)
                self.param_times.clear()
                self.stamps.clear()
                self.event("Aircraft restarted; mission verification invalidated.")
            s.update(boot_ms=boot, position={"lat": m.lat / 1e7, "lon": m.lon / 1e7, "alt_msl": m.alt / 1000},
                     relative_alt=m.relative_alt / 1000, speed=math.hypot(m.vx, m.vy) / 100,
                     vertical_speed=-m.vz / 100)
            self.stamps["position"] = now
        elif kind == "HOME_POSITION":
            s["home"] = {"lat": m.latitude / 1e7, "lon": m.longitude / 1e7, "alt_msl": m.altitude / 1000}
            self.stamps["home"] = now
        elif kind == "GPS_RAW_INT":
            # Course over ground is meaningless when hovering; ArduPilot sends it once moving on a 3D fix.
            s.update(gps_fix=m.fix_type, satellites=0 if m.satellites_visible == 255 else m.satellites_visible,
                     course=m.cog / 100 if m.fix_type >= 3 and m.cog != 65535 and 50 <= m.vel != 65535 else None)
            self.stamps["gps"] = now
        elif kind == "SYS_STATUS":
            needed = m.onboard_control_sensors_enabled & m.onboard_control_sensors_present
            s["sensors_ok"] = needed != 0 and (needed & m.onboard_control_sensors_health) == needed
            # current_battery is centiamps; -1 means the power monitor does not report it.
            raw_current = m.current_battery
            s.update(battery_pct=m.battery_remaining,
                     battery_voltage=None if m.voltage_battery == 65535 else m.voltage_battery / 1000,
                     battery_current=None if raw_current < 0 or raw_current == 65535 else raw_current / 100)
            self.stamps.update(health=now, battery=now)
        elif kind == "EKF_STATUS_REPORT":
            required = 1 | 2 | 4 | 16 | 32
            s["ekf_ok"] = m.flags & required == required and not m.flags & (128 | 1024)
            self.stamps["ekf"] = now
        elif kind == "EXTENDED_SYS_STATE":
            s["landed"] = m.landed_state
            self.stamps["landed"] = now
        elif kind == "ATTITUDE":
            # Compass/AHRS heading, as on Mission Planner's HUD. Works without GPS.
            s.update(roll=math.degrees(m.roll), pitch=math.degrees(m.pitch), heading=math.degrees(m.yaw) % 360)
            self.stamps["attitude"] = now
        elif kind == "MISSION_CURRENT":
            s["mission_current"] = m.seq
        elif kind == "STATUSTEXT":
            s["status_text"] = m.text
            self.event(m.text, "ardupilot", m.severity)
        elif kind == "PARAM_VALUE":
            key = m.param_id.decode() if isinstance(m.param_id, bytes) else m.param_id
            key = key.rstrip("\x00")
            value = m.param_value
            if key in PARAM_ALIASES and math.isfinite(value):
                key, convert = PARAM_ALIASES[key]
                value = convert(value)
            if key in PARAMETERS and math.isfinite(value):
                s["params"][key] = value
                self.param_times[key] = now

    def snapshot(self):
        with self.cv:
            now = time.monotonic()
            s = copy.deepcopy(self.s)
            age = now - self.stamps.get("heartbeat", -100)
            s.update(connected=not self.closed.is_set() and age < FRESH_SECONDS,
                     heartbeat_age=round(age, 1), generation=self.s["generation"],
                     fresh={k: now - self.stamps.get(k, -100) < (60 if k == "home" else FRESH_SECONDS)
                            for k in ("landed", "position", "home", "gps", "ekf", "battery", "health", "attitude")},
                     params_fresh=all(now - self.param_times.get(k, -100) < 60 for k in PARAMETERS))
            return s

    def _mark(self):
        with self.cv:
            return self.counter

    def _wait(self, cursor, predicate, cancel, timeout=3):
        deadline = time.monotonic() + timeout
        with self.cv:
            while time.monotonic() < deadline:
                if cancel and cancel.is_set():
                    raise FlightError("Operation interrupted by Return home.")
                if self.closed.is_set():
                    raise FlightError("Aircraft link closed.")
                for seq, msg in self.inbox:
                    if seq > cursor and predicate(msg):
                        return seq, msg
                self.cv.wait(min(0.15, max(0, deadline - time.monotonic())))
        raise TimeoutError("Aircraft response timed out.")

    def _wait_state(self, predicate, cancel, timeout, label):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cancel and cancel.is_set():
                raise FlightError("Operation interrupted by Return home.")
            s = self.snapshot()
            if predicate(s):
                return
            if self.closed.is_set():
                break
            time.sleep(0.1)
        raise FlightError(f"No confirmation of {label}. Inspect aircraft status before retrying.")

    def _request_streams(self):
        # Request 2 Hz rather than saturating an ELRS telemetry link.
        self._send("request_data_stream_send", self.system, self.component, 0, 2, 1)
        for msg_id in (1, 24, 30, 33, 42, 193, 245):
            interval = 100000 if self.fast and msg_id in (30, 33) else 500000
            self._send("command_long_send", self.system, self.component, 511, 0,
                       msg_id, interval, 0, 0, 0, 0, 0)

    def refresh(self, cancel, need_home=True):
        started = time.monotonic()
        for attempt in range(3):
            for key in PARAMETERS:
                if self.param_times.get(key, 0) < started:
                    self._send("param_request_read_send", self.system, self.component, key.encode(), -1, cancel=cancel)
            for alias, (key, _) in PARAM_ALIASES.items():
                if self.param_times.get(key, 0) < started:
                    self._send("param_request_read_send", self.system, self.component, alias.encode(), -1, cancel=cancel)
            self._send("command_long_send", self.system, self.component, 512, 0, 242, 0, 0, 0, 0, 0, 0, cancel=cancel)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if cancel.is_set():
                    raise FlightError("Operation canceled.")
                if all(self.param_times.get(k, 0) >= started for k in PARAMETERS) and (
                        not need_home or self.stamps.get("home", 0) >= started):
                    return
                time.sleep(0.1)
        missing = [k for k in PARAMETERS if self.param_times.get(k, 0) < started]
        if missing:
            raise FlightError("Aircraft did not provide fresh parameters: " + ", ".join(missing))
        raise FlightError("Aircraft has no GPS Home position yet. Wait for a 3D GPS fix outdoors, then try again.")

    def upload(self, items, cancel):
        self.legacy_coordinates = False
        cursor = self._mark()
        self._send("mission_count_send", self.system, self.component, len(items), 0, cancel=cancel)
        sent, last_item, retries = set(), None, 0
        deadline = time.monotonic() + max(30, len(items) * 3)
        while time.monotonic() < deadline:
            try:
                cursor, msg = self._wait(cursor, lambda m: m.get_type() in {"MISSION_REQUEST_INT", "MISSION_REQUEST", "MISSION_ACK"}
                                         and getattr(m, "mission_type", 0) == 0, cancel)
            except TimeoutError:
                retries += 1
                if retries > 4:
                    raise FlightError("Upload timed out. Stored mission may be incomplete; upload again before flight.")
                if last_item is None:
                    self._send("mission_count_send", self.system, self.component, len(items), 0, cancel=cancel)
                else:
                    self._send_item(items[last_item[0]], last_item[1], cancel)
                continue
            retries = 0
            if msg.get_type() == "MISSION_ACK":
                if msg.type != 0:
                    raise FlightError(f"Aircraft rejected mission (MAVLink result {msg.type}).")
                if sent != set(range(len(items))):
                    raise FlightError("Aircraft acknowledged before requesting the complete mission.")
                return
            if not 0 <= msg.seq < len(items):
                raise FlightError("Aircraft requested an invalid mission sequence.")
            last_item = (msg.seq, msg.get_type() == "MISSION_REQUEST_INT")
            self._send_item(items[msg.seq], last_item[1], cancel)
            sent.add(msg.seq)
        raise FlightError("Mission upload exceeded its deadline.")

    def _send_item(self, it, use_int, cancel):
        state = self.snapshot()
        if state.get("armed") or state.get("landed") != 1 or not state.get("fresh", {}).get("landed"):
            raise FlightError("Aircraft state changed during upload. Upload stopped; verify the mission again before flight.")
        x, y = it["lat"], it["lon"]
        if use_int:
            x, y = round(x * 1e7), round(y * 1e7)
        else:
            self.legacy_coordinates = True
        self._send("mission_item_int_send" if use_int else "mission_item_send", self.system, self.component,
                   it["seq"], it["frame"], it["command"], 0, 1, it["p1"], it["p2"], it["p3"], it["p4"],
                   x, y, it["alt"], 0, cancel=cancel)

    def _exchange(self, method, args, predicate, cancel):
        for _ in range(4):
            cursor = self._mark()
            self._send(method, *args, cancel=cancel)
            try:
                return self._wait(cursor, predicate, cancel)[1]
            except TimeoutError:
                continue
        raise FlightError("Aircraft mission read-back timed out.")

    def download(self, cancel):
        msg = self._exchange("mission_request_list_send", (self.system, self.component, 0),
                             lambda m: m.get_type() == "MISSION_COUNT" and getattr(m, "mission_type", 0) == 0, cancel)
        if not 1 <= msg.count <= 700:
            raise FlightError("Aircraft has no mission or an unsupported mission size.")
        result = []
        for seq in range(msg.count):
            m = self._exchange("mission_request_int_send", (self.system, self.component, seq, 0),
                               lambda m: m.get_type() in {"MISSION_ITEM_INT", "MISSION_ITEM"} and m.seq == seq
                               and getattr(m, "mission_type", 0) == 0, cancel)
            scale = 1e7 if m.get_type() == "MISSION_ITEM_INT" else 1
            result.append(dict(seq=m.seq, command=m.command, frame=m.frame, current=m.current,
                               autocontinue=m.autocontinue, p1=m.param1, p2=m.param2, p3=m.param3, p4=m.param4,
                               lat=m.x / scale, lon=m.y / scale, alt=m.z,
                               coordinate_tolerance=0.00001 if self.legacy_coordinates or scale == 1 else 0.000001))
        self._send("mission_ack_send", self.system, self.component, 0, 0, cancel=cancel)
        return result

    def command(self, command, params, cancel):
        cursor = self._mark()
        self._send("command_long_send", self.system, self.component, command, 0,
                   *(list(params) + [0] * (7 - len(params))), cancel=cancel)
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            try:
                cursor, ack = self._wait(cursor, lambda m: m.get_type() == "COMMAND_ACK" and m.command == command,
                                         cancel, min(3, max(0.1, deadline - time.monotonic())))
            except TimeoutError:
                # Never blindly retry arm/start after an ambiguous acknowledgment.
                continue
            if ack.result == 0:
                return
            if ack.result != 5:
                raise FlightError(f"Aircraft refused command {command} (result {ack.result}). {self.s.get('status_text', '')}")
        raise FlightError(f"Command {command} acknowledgment missing; aircraft state is uncertain.")

    def mode(self, mode, cancel):
        self.command(176, [1, mode], cancel)
        self._wait_state(lambda s: s.get("connected") and s.get("mode_id") == mode, cancel, 6, "flight mode")

    def arm(self, cancel):
        self.command(400, [1, 0], cancel)  # Never force-arm.
        self._wait_state(lambda s: s.get("connected") and s.get("armed"), cancel, 6, "arming")

    def set_current(self, seq, cancel):
        cursor = self._mark()
        self._send("mission_set_current_send", self.system, self.component, seq, cancel=cancel)
        self._wait(cursor, lambda m: m.get_type() == "MISSION_CURRENT" and m.seq == seq, cancel, 6)

    def start(self, cancel):
        if self.snapshot().get("mode_id") != 3:
            raise FlightError("Aircraft is no longer in AUTO; mission start canceled.")
        self.command(300, [], cancel)

    def wait_airborne(self, cancel):
        self._wait_state(lambda s: s.get("connected") and s.get("fresh", {}).get("landed")
                         and s.get("fresh", {}).get("position") and s.get("landed") == 2
                         and s.get("relative_alt", 0) >= 2, cancel, 25, "takeoff")

    def close(self):
        self.closed.set()
        self.reader.join(timeout=1)
        self.conn.close()
