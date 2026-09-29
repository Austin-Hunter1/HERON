"""Local ArduCopter flight service. No connection or aircraft writes at import time.

One receiver owns the MAVLink stream; transactions use a sequence-numbered inbox.
The deterministic demo uses the same mission validation and preflight policy.
"""
from __future__ import annotations

import copy
import hashlib
import math
import secrets
import threading
import time
from collections import deque

from engine.export import parse_wpl, validate_wpl


class FlightError(RuntimeError):
    pass


PARAMETERS = (
    "ARMING_CHECK", "FENCE_ENABLE", "FENCE_TYPE", "FENCE_ACTION", "FENCE_RADIUS",
    "FENCE_ALT_MAX", "FENCE_MARGIN", "BATT_FS_LOW_ACT", "BATT_FS_CRT_ACT",
    "FS_GCS_ENABLE", "FS_THR_ENABLE", "SYSID_MYGCS", "RTL_ALT",
)
DEMO_PARAMS = dict(zip(PARAMETERS, (1, 1, 3, 1, 1000, 122, 5, 2, 1, 1, 1, 255, 1500)))
FRESH_SECONDS = 5.0
NAV_COMMANDS = {16, 22}
ALLOWED_COMMANDS = {16, 20, 22, 178, 201}


def distance(a, b):
    p, q = math.radians(a["lat"]), math.radians(b["lat"])
    dp, dl = q - p, math.radians(b["lon"] - a["lon"])
    v = math.sin(dp / 2) ** 2 + math.cos(p) * math.cos(q) * math.sin(dl / 2) ** 2
    return 6371000 * 2 * math.asin(min(1, math.sqrt(max(0, v))))


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
    def __init__(self):
        self.lock = threading.RLock()
        self.operation_lock = threading.Lock()
        self.cancel = threading.Event()
        self.link = None
        self.plans = {}
        self.verified = None
        self.job = {"state": "idle", "action": None, "message": "Connect an aircraft or try the demo."}
        self.events = deque(maxlen=30)
        self.events_lock = threading.Lock()

    def event(self, message):
        with self.events_lock:
            self.events.append({"time": time.time(), "text": str(message)[:300]})

    def register_plan(self, wpl, meta):
        # Keep planning/export usable even when a plan is outside live-flight limits.
        plan_id = secrets.token_hex(16)
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
            self.cancel.clear()
            if kind == "demo":
                self.link = DemoLink(self.event)
            else:
                self.link = MavlinkLink(options, self.event)
            self.verified = None
            self.event("Connected to simulated aircraft." if kind == "demo" else "Aircraft heartbeat received.")
            self.job = {"state": "idle", "action": None, "message": "Connected. Refresh aircraft checks before launch."}
            return self.snapshot()

    def disconnect(self):
        if self.job["state"] == "running":
            raise FlightError("An operation is running. Use Return home to interrupt launch.")
        with self.operation_lock:
            if self.link:
                self.link.close()
            self.link = None
            self.verified = None
            self.event("Disconnected. Aircraft failsafes remain on the flight controller.")
            self.job = {"state": "idle", "action": None, "message": "Disconnected. Connect an aircraft or try the demo."}
        return self.snapshot()

    def preflight(self, plan_id=None):
        link = self.link
        s = link.snapshot() if link else {}
        checks = []

        def check(key, ok, message):
            checks.append({"key": key, "ok": bool(ok), "message": message})

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
        check("arming", p.get("ARMING_CHECK") == 1, "All ArduPilot pre-arm checks are enabled")
        fence_type = int(p.get("FENCE_TYPE", 0))
        check("fence", p.get("FENCE_ENABLE") == 1 and fence_type & 3 == 3 and p.get("FENCE_ACTION", 0) > 0,
              "Altitude and circular fences are enabled with an action")
        check("failsafes", all(p.get(k, 0) > 0 for k in ("BATT_FS_LOW_ACT", "BATT_FS_CRT_ACT", "FS_GCS_ENABLE", "FS_THR_ENABLE")),
              "Battery, ground-station and RC failsafe actions are enabled")
        check("gcs", p.get("SYSID_MYGCS") == 255, "Aircraft monitors this ground station (system 255)")
        items = None
        try:
            items = flight_items(self.plan(plan_id)["wpl"])
            check("mission", True, f"Mission has {len(items)} items, TAKEOFF and RTL")
        except (FlightError, ValueError) as exc:
            check("mission", False, str(exc) if plan_id else "Build a mission before uploading")
        if items:
            check("pad", home and distance(items[0], home) < 30, "Planned launch pad is within 30 m of actual Home")
            margin = max(5, p.get("FENCE_MARGIN", 5))
            radius = p.get("FENCE_RADIUS", 0) - margin
            ceiling = min(122, p.get("FENCE_ALT_MAX", 0) - margin)
            nav = [i for i in items[1:] if i["command"] in NAV_COMMANDS]
            check("bounds", home and all(distance(home, i) < radius and 2 <= i["alt"] <= ceiling for i in nav),
                  "Route fits inside the circular and altitude fences, including margin")
            check("rtl_alt", 0 <= p.get("RTL_ALT", -1) / 100 <= ceiling,
                  "Configured return altitude fits below the altitude fence")
        verified = self.verified
        check("verified", verified and verified["plan_id"] == plan_id and link and verified["generation"] == s.get("generation"),
              "This mission was uploaded and read back from this aircraft")
        return {"ready": all(c["ok"] for c in checks), "checks": checks}

    def snapshot(self, plan_id=None):
        with self.lock:
            s = self.link.snapshot() if self.link else {"connected": False, "transport": None}
            with self.events_lock:
                events = list(self.events)
            return {"telemetry": s, "job": dict(self.job), "verified": copy.deepcopy(self.verified),
                    "preflight": self.preflight(plan_id), "events": events}

    def submit(self, action, plan_id=None, confirmation=None):
        if action not in {"refresh", "upload", "launch"}:
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
            self.phase("Reading aircraft status and safety parameters…")
            link.refresh(self.cancel)
            if action in {"upload", "launch"}:
                items = flight_items(self.plan(plan_id)["wpl"])
                s = link.snapshot()
                if s.get("armed") or s.get("landed") != 1 or not s.get("fresh", {}).get("landed"):
                    raise FlightError("Mission changes require a disarmed aircraft on the ground.")
                if not s.get("home") or distance(items[0], s["home"]) >= 30:
                    raise FlightError("Mission launch pad does not match actual aircraft Home. Rebuild at this field.")
            if action == "upload":
                self.verified = None
                self.phase("Uploading mission…")
                link.upload(items, self.cancel)
                self.phase("Reading mission back from aircraft…")
                compare_items(items, link.download(self.cancel))
                self.guard()
                self.verified = {"plan_id": plan_id, "generation": link.snapshot()["generation"],
                                 "count": len(items), "hash": self.plan(plan_id)["hash"]}
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
            self.event(message)
        finally:
            self.operation_lock.release()

    def _require_ready(self, plan_id, allow_armed=False):
        self.guard()
        failed = [c["message"] for c in self.preflight(plan_id)["checks"] if not c["ok"]
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
    def __init__(self, event):
        self.event = event
        self.items = []
        self.generation = secrets.token_hex(8)
        self.connected = True
        self.armed = False
        self.mode_id = 0
        self.current = 0
        self.home = {"lat": 40.086045, "lon": -105.233634, "alt_msl": 1625}
        self.position = dict(self.home)
        self.alt = 0
        self.last = time.monotonic()
        self.flying = False
        self.dwell = 0
        self.vertical_speed = 0

    def snapshot(self):
        now = time.monotonic()
        dt = min(1, now - self.last)
        self.last = now
        previous_alt = self.alt
        speed = 20  # Accelerated demonstration, clearly marked in UI.
        if self.flying and self.armed:
            it = self.items[self.current] if self.current < len(self.items) else {"command": 20}
            if self.mode_id == 6 or it["command"] == 20:
                self.mode_id = 6
                d = distance(self.position, self.home)
                f = min(1, speed * dt / max(d, 0.001))
                for key in ("lat", "lon"):
                    self.position[key] += (self.home[key] - self.position[key]) * f
                if d < 2:
                    self.alt = max(0, self.alt - dt * 15)
                    if self.alt == 0:
                        self.armed = False
                        self.flying = False
                        self.event("Demo aircraft landed and disarmed.")
            elif it["command"] in NAV_COMMANDS:
                self.alt += max(-15 * dt, min(15 * dt, it["alt"] - self.alt))
                d = distance(self.position, it)
                f = min(1, speed * dt / max(d, 0.001))
                for key in ("lat", "lon"):
                    self.position[key] += (it[key] - self.position[key]) * f
                if d < 2 and abs(self.alt - it["alt"]) < 1:
                    self.dwell += dt
                    if self.dwell >= min(3, it["p1"]):
                        self.current += 1
                        self.dwell = 0
            else:
                self.current += 1
        if dt > 0.001:
            self.vertical_speed = (self.alt - previous_alt) / dt
        fresh = {k: self.connected for k in ("landed", "gps", "ekf", "health", "battery", "home", "position", "attitude")}
        return {"connected": self.connected, "transport": "demo", "supported": True, "system_id": 1,
                "generation": self.generation, "armed": self.armed, "mode_id": self.mode_id,
                "mode": {0: "STABILIZE", 3: "AUTO", 4: "GUIDED", 6: "RTL"}.get(self.mode_id),
                "landed": 2 if self.alt > 0 else 1, "gps_fix": 3, "satellites": 16,
                "ekf_ok": True, "sensors_ok": True, "battery_pct": 92, "battery_voltage": 24.6,
                "home": dict(self.home), "position": dict(self.position), "relative_alt": self.alt,
                "speed": speed if self.flying else 0, "heading": 0, "roll": 0, "pitch": 0,
                "vertical_speed": self.vertical_speed,
                "mission_current": self.current, "params": dict(DEMO_PARAMS), "params_fresh": True,
                "fresh": fresh, "heartbeat_age": 0, "status_text": "SIMULATED AIRCRAFT · accelerated demonstration"}

    def refresh(self, cancel):
        self._check(cancel)

    def _check(self, cancel):
        if cancel and cancel.is_set():
            raise FlightError("Demo operation canceled.")

    def upload(self, items, cancel):
        self._check(cancel)
        self.items = copy.deepcopy(items)

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
        self.conn = mavutil.mavlink_connection(endpoint, baud=baud, source_system=255,
                                              source_component=190, dialect="ardupilotmega", autoreconnect=False)
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
                     vertical_speed=-m.vz / 100,
                     heading=None if m.hdg == 65535 else m.hdg / 100)
            self.stamps["position"] = now
        elif kind == "HOME_POSITION":
            s["home"] = {"lat": m.latitude / 1e7, "lon": m.longitude / 1e7, "alt_msl": m.altitude / 1000}
            self.stamps["home"] = now
        elif kind == "GPS_RAW_INT":
            s.update(gps_fix=m.fix_type, satellites=0 if m.satellites_visible == 255 else m.satellites_visible)
            self.stamps["gps"] = now
        elif kind == "SYS_STATUS":
            needed = m.onboard_control_sensors_enabled & m.onboard_control_sensors_present
            s["sensors_ok"] = needed != 0 and (needed & m.onboard_control_sensors_health) == needed
            s.update(battery_pct=m.battery_remaining, battery_voltage=None if m.voltage_battery == 65535 else m.voltage_battery / 1000)
            self.stamps.update(health=now, battery=now)
        elif kind == "EKF_STATUS_REPORT":
            required = 1 | 2 | 4 | 16 | 32
            s["ekf_ok"] = m.flags & required == required and not m.flags & (128 | 1024)
            self.stamps["ekf"] = now
        elif kind == "EXTENDED_SYS_STATE":
            s["landed"] = m.landed_state
            self.stamps["landed"] = now
        elif kind == "ATTITUDE":
            s.update(roll=math.degrees(m.roll), pitch=math.degrees(m.pitch))
            self.stamps["attitude"] = now
        elif kind == "MISSION_CURRENT":
            s["mission_current"] = m.seq
        elif kind == "STATUSTEXT":
            s["status_text"] = m.text
            self.event(m.text)
        elif kind == "PARAM_VALUE":
            key = m.param_id.decode() if isinstance(m.param_id, bytes) else m.param_id
            key = key.rstrip("\x00")
            if key in PARAMETERS and math.isfinite(m.param_value):
                s["params"][key] = m.param_value
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
            self._send("command_long_send", self.system, self.component, 511, 0,
                       msg_id, 500000, 0, 0, 0, 0, 0)

    def refresh(self, cancel):
        started = time.monotonic()
        for attempt in range(3):
            for key in PARAMETERS:
                if self.param_times.get(key, 0) < started:
                    self._send("param_request_read_send", self.system, self.component, key.encode(), -1, cancel=cancel)
            self._send("command_long_send", self.system, self.component, 512, 0, 242, 0, 0, 0, 0, 0, 0, cancel=cancel)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if cancel.is_set():
                    raise FlightError("Operation canceled.")
                if all(self.param_times.get(k, 0) >= started for k in PARAMETERS) and self.stamps.get("home", 0) >= started:
                    return
                time.sleep(0.1)
        missing = [k for k in PARAMETERS if self.param_times.get(k, 0) < started]
        raise FlightError("Aircraft did not provide fresh Home/parameters: " + ", ".join(missing))

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
