"""Ground side of the HERON payload: a small status strip and Start / Stop / Auto in Fly and Simulate.

Radio configuration is NOT done here; it lives in the Payload Setup app on the NUC
(http://<nuc>:8080 over Ethernet). This module only speaks the plain MAVLink protocol in
HERON_Payload/heron_payload/protocol.py:

  Fly       -> COMMAND_LONG 31010 to component 191 through the Cube; status from NAMED_VALUE_FLOAT.
  Simulate  -> an in-process simulated payload that follows the simulated aircraft.
"""
from __future__ import annotations

import collections
import random
import secrets
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from flask import Blueprint, jsonify, request

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "HERON_Payload"))
from heron_payload import protocol as P  # noqa: E402
from heron_payload.backends import FakeBackend  # noqa: E402
from heron_payload.config import presets  # noqa: E402
from heron_payload.controller import Controller  # noqa: E402

OFFLINE_AFTER_S = 3.5


def _status(connected, values, setup=None, fault=None, message=None, simulated=False, age=None):
    state = P.STATES[int(values.get(P.F_STATE, 0))] if connected else "OFFLINE"
    left = values.get(P.F_LEFT, -1)
    return {"connected": connected, "simulated": simulated, "state": state,
            "elapsed_s": round(values.get(P.F_ELAPSED, 0)), "rate_mbps": round(values.get(P.F_RATE, 0), 1),
            "minutes_left": None if left is None or left < 0 else round(left),
            "free_gb": round(values.get(P.F_FREE, 0), 1), "overflows": int(values.get(P.F_OVF, 0)),
            "auto": P.unpack_auto(values.get(P.F_AUTO, 0)), "setup": setup, "fault": fault if state == "FAULT" else None,
            "message": message, "age_s": None if age is None else round(age, 1)}


class PayloadMonitor:
    """Reads payload messages that FlightService's MAVLink link collects from component 191."""

    def __init__(self, flight, clock=time.monotonic):
        self.flight, self.clock = flight, clock
        self.lock = threading.Condition()
        self.link = None
        self.values, self.setup, self.fault, self.message = {}, None, None, None
        self.seen = None
        self.acks = {}
        self.reasons = collections.deque(maxlen=8)
        self.asked_status = False
        self.closed = threading.Event()
        threading.Thread(target=self._loop, daemon=True, name="payload-monitor").start()

    def _reset(self, link):
        self.link = link
        self.values, self.setup, self.fault, self.message, self.seen = {}, None, None, None, None
        self.acks.clear()
        self.asked_status = False

    def _loop(self):
        while not self.closed.wait(0.1):
            try:
                self.poll()
            except Exception:
                pass

    def poll(self):
        link = getattr(self.flight, "link", None)
        with self.lock:
            if link is not self.link:
                self._reset(link)
        frames = getattr(link, "payload_frames", None)
        if frames is None:
            return
        with link.cv:
            batch = list(frames)
            frames.clear()
        with self.lock:
            for m in batch:
                self._take(m)
            self.lock.notify_all()
        if self.seen is not None and not self.asked_status:
            self.asked_status = True
            threading.Thread(target=self._ask_status, daemon=True).start()

    def _ask_status(self):
        try:
            self.command("status")
        except Exception:
            self.asked_status = False

    def _take(self, m):
        kind = m.get_type()
        now = self.clock()
        if kind == "HEARTBEAT":
            self.seen = now
        elif kind == "NAMED_VALUE_FLOAT":
            name = m.name.decode() if isinstance(m.name, bytes) else m.name
            name = name.rstrip("\x00")
            if name in P.STATUS_FIELDS:
                self.values[name] = float(m.value)
                self.seen = now
        elif kind == "STATUSTEXT":
            text = m.text.decode(errors="replace") if isinstance(m.text, bytes) else m.text
            text = text.rstrip("\x00")
            if text.startswith(P.SETUP_PREFIX):
                self.setup = text[len(P.SETUP_PREFIX):] or None
                if self.setup == "none":
                    self.setup = None
            elif text.startswith("HERON fault: "):
                self.fault = text[len("HERON fault: "):]
            elif text.startswith("HERON: "):
                self.reasons.append((now, text[len("HERON: "):]))
                self.message = text[len("HERON: "):]
            elif text.startswith(P.TEXT_PREFIX):
                self.message = text[len(P.TEXT_PREFIX):].strip()
                if m.severity <= 3:
                    self.fault = self.message
        elif kind == "COMMAND_ACK" and m.command == P.COMMAND:
            self.acks[int(getattr(m, "result_param2", 0))] = int(m.result)

    def status(self):
        with self.lock:
            age = None if self.seen is None else self.clock() - self.seen
            connected = age is not None and age < OFFLINE_AFTER_S
            return _status(connected, self.values, self.setup, self.fault, self.message, age=age)

    def command(self, action, start_seq=None, stop_seq=None, tries=3, wait_s=1.5):
        link = self.link
        if link is None or not hasattr(link, "payload_frames"):
            raise ValueError("Connect to the aircraft first.")
        if action != "status" and not self.status()["connected"]:
            raise ValueError("No signal from the payload. Check the NUC is powered and wired to TELEM2.")
        code = P.ACTION_CODES[action]
        request_id = random.randint(1, 16_777_215)
        asked = self.clock()
        for attempt in range(tries):
            link._send("command_long_send", link.system, P.COMPONENT, P.COMMAND, attempt, float(code),
                       float(start_seq or 0), float(stop_seq or 0), float(request_id), 0, 0, 0)
            deadline = self.clock() + wait_s
            with self.lock:
                while request_id not in self.acks and self.clock() < deadline:
                    self.lock.wait(0.1)
                if request_id in self.acks:
                    result = self.acks.pop(request_id)
                    if result != P.RESULT_ACCEPTED:
                        reason = next((r for t, r in reversed(self.reasons) if t >= asked), None)
                        raise ValueError(reason or "The payload refused the command.")
                    return self.status()
        raise ValueError("The payload did not answer. It may be busy starting the radios; check the strip before retrying.")


class SimPayload:
    """A simulated payload for the Simulate tab: preloaded with the default setup, follows the sim aircraft."""

    def __init__(self, sim):
        self.sim = sim
        self.controller = Controller(FakeBackend())
        self.controller.execute("prepare", {"config": presets()[0]})
        self.controller.ground_seen = time.monotonic()
        self.last_reached = None
        self.link = None
        self.closed = threading.Event()
        threading.Thread(target=self._loop, daemon=True, name="sim-payload").start()

    def _loop(self):
        while not self.closed.wait(0.1):
            try:
                link = self.sim.link
                if link is not self.link:
                    self.link, self.last_reached = link, None
                    if self.controller.state in ("RECORDING", "AUTO_ARMED"):
                        self.controller.execute("stop")
                if link:
                    s = link.snapshot()
                    reached = s.get("mission_reached")
                    event = reached if reached is not None and reached != self.last_reached else None
                    self.last_reached = reached
                    self.controller.cube_update(armed=s.get("armed"), mode=s.get("mode"),
                                                current=s.get("mission_current"), reached=event)
                self.controller.tick()
            except Exception as exc:
                self.controller.event("Simulation error: " + str(exc))

    def status(self):
        snap = self.controller.snapshot()
        last = snap["events"][-1]["text"] if snap["events"] else None
        return _status(True, P.status_values(snap), snap.get("setup"), snap.get("fault"), last, simulated=True)

    def command(self, action, start_seq=None, stop_seq=None):
        data = {"start_seq": start_seq, "stop_seq": stop_seq} if action == "arm" else {}
        if action != "status":
            self.controller.execute(action, data)
        return self.status()


def survey_waypoints(service, plan_id):
    """Navigation waypoints the recording can start/stop at, preferring the mission on the drone."""
    try:
        return _survey_waypoints(service, plan_id)
    except Exception:
        return {"source": None, "waypoints": []}


def _survey_waypoints(service, plan_id):
    onboard = getattr(service, "onboard", None) or {}
    rows, source, pid = [], None, None
    if onboard.get("plan_id"):                         # a HERON plan written to (and verified on) the drone
        pid, source = onboard["plan_id"], "drone"
        rows = [i for i in service._items(pid) if i["seq"] > 0 and i["command"] == 16 and (i["lat"] or i["lon"])]
    elif onboard.get("path") and all("command" in p for p in onboard["path"]):
        rows, source = [p for p in onboard["path"] if p["command"] == 16], "drone"
    elif plan_id:
        pid, source = plan_id, "plan"
        rows = [i for i in service._items(plan_id) if i["seq"] > 0 and i["command"] == 16 and (i["lat"] or i["lon"])]
    if pid:
        try:
            if float(service.plan(pid)["meta"].get("hold_s") or 0) >= 8:
                rows = [r for r in rows if r["seq"] != 3]     # the exporter's launch-pad hold is not survey
        except Exception:
            pass
    wps = [r["seq"] for r in rows]
    # "number" matches the WP1, WP2... labels on the map; "seq" is the ArduPilot mission item number.
    return {"source": source if wps else None, "waypoints": wps,
            "numbers": {str(seq): n + 1 for n, seq in enumerate(wps)},
            "start_seq": wps[0] if wps else None, "stop_seq": wps[-1] if len(wps) > 1 else None}


def payload_api(flight, sim):
    bp = Blueprint("payload", __name__, url_prefix="/api/payload")
    token = secrets.token_urlsafe(24)
    fly_payload = PayloadMonitor(flight)
    sim_payload = SimPayload(sim)
    bp.monitors = {"fly": fly_payload, "sim": sim_payload}

    @bp.before_request
    def guard():
        if request.remote_addr not in ("127.0.0.1", "::1") or urlsplit(request.host_url).hostname not in ("localhost", "127.0.0.1", "::1"):
            return jsonify(error="Payload controls only work on this computer."), 403
        if request.method == "POST":
            if request.headers.get("Origin") not in (None, request.host_url.rstrip("/")):
                return jsonify(error="Cross-origin payload commands are blocked."), 403
            if not secrets.compare_digest(request.headers.get("X-Payload-Token", ""), token):
                return jsonify(error="Reload the page before sending payload commands."), 403

    @bp.after_request
    def no_cache(resp):
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @bp.errorhandler(ValueError)
    def refused(exc):
        return jsonify(error=str(exc)), 409

    def pick(value):
        if value not in ("fly", "sim"):
            raise ValueError("mode must be fly or sim")
        return bp.monitors[value], (flight if value == "fly" else sim)

    @bp.get("/session")
    def session():
        return jsonify(token=token)

    @bp.get("/status")
    def status():
        payload, service = pick(request.args.get("mode", "fly"))
        out = payload.status()
        out["waypoints"] = survey_waypoints(service, request.args.get("plan_id") or None)
        return jsonify(out)

    @bp.post("/command")
    def command():
        data = request.get_json(silent=True) or {}
        payload, service = pick(data.get("mode"))
        action = data.get("action")
        if action not in ("start", "stop", "arm", "cancel_auto"):
            raise ValueError("Unknown payload action.")
        start = stop = None
        if action == "arm":
            start, stop = data.get("start_seq"), data.get("stop_seq")
            wps = survey_waypoints(service, data.get("plan_id") or None)["waypoints"]
            if start not in wps or stop not in wps or start >= stop:
                raise ValueError("Pick a start waypoint before the stop waypoint from this mission.")
        out = payload.command(action, start, stop)
        out["waypoints"] = survey_waypoints(service, data.get("plan_id") or None)
        return jsonify(out)

    return bp
