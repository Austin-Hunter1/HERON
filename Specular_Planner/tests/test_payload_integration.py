"""Planner payload strip <-> real NUC agent, over real MAVLink 2 bytes (no hardware)."""
import collections
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ["MAVLINK20"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "HERON_Payload"))
from flask import Flask  # noqa: E402
from pymavlink.dialects.v20 import ardupilotmega as mav  # noqa: E402

from engine.flight import FlightService  # noqa: E402
from engine.payload import PayloadMonitor, payload_api  # noqa: E402
from heron_payload.agent import Agent  # noqa: E402
from heron_payload.backends import FakeBackend  # noqa: E402
from heron_payload.config import presets  # noqa: E402
from heron_payload.controller import Controller  # noqa: E402


class Pipe:
    def __init__(self, deliver):
        self.parser = mav.MAVLink(None)
        self.deliver = deliver

    def write(self, data):
        for m in self.parser.parse_buffer(data) or []:
            self.deliver(m)


class LiveLinkTests(unittest.TestCase):
    """Wires the agent and the planner monitor together the way the Cube routes them."""

    def setUp(self):
        self.tmp = __import__("tempfile").TemporaryDirectory()
        self.controller = Controller(FakeBackend())
        self.controller.execute("prepare", dict(config=presets()[0]))
        self.drop_next_ack = False
        frames, cv = collections.deque(maxlen=512), threading.Condition()

        def to_ground(m):                      # what flight.py keeps from component 191
            if m.get_type() == "COMMAND_ACK" and self.drop_next_ack:
                self.drop_next_ack = False
                return
            with cv:
                frames.append(m)

        self.agent = Agent(SimpleNamespace(mav=mav.MAVLink(Pipe(to_ground), srcSystem=1, srcComponent=191)),
                           self.controller, Path(self.tmp.name) / "active.json")
        uplink = mav.MAVLink(Pipe(self.agent.receive), srcSystem=255, srcComponent=190)
        self.link = SimpleNamespace(system=1, payload_frames=frames, cv=cv,
                                    _send=lambda method, *a: getattr(uplink, method)(*a))
        self.flight = SimpleNamespace(link=self.link)
        self.monitor = PayloadMonitor(self.flight)
        self.stop = threading.Event()

        def run():
            while not self.stop.wait(0.05):
                self.agent.periodic()
        threading.Thread(target=run, daemon=True).start()
        deadline = time.monotonic() + 4
        while not self.monitor.status()["connected"] and time.monotonic() < deadline:
            time.sleep(0.05)

    def tearDown(self):
        self.stop.set()
        self.monitor.closed.set()
        self.tmp.cleanup()

    def test_status_reaches_the_strip(self):
        s = self.monitor.status()
        self.assertTrue(s["connected"])
        self.assertEqual(s["state"], "READY")
        deadline = time.monotonic() + 3
        while self.monitor.status()["setup"] is None and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(self.monitor.status()["setup"], presets()[0]["name"])

    def test_record_and_stop_survive_a_lost_ack(self):
        self.drop_next_ack = True
        s = self.monitor.command("start")
        self.assertEqual(self.controller.state, "RECORDING")
        self.assertEqual(sum(1 for e in self.controller.events if e["text"].startswith("REC START")), 1)
        time.sleep(1.2)
        self.assertEqual(self.monitor.status()["state"], "RECORDING")
        self.monitor.command("stop")
        self.assertEqual(self.controller.state, "READY")

    def test_refusal_reason_is_shown(self):
        with self.assertRaisesRegex(ValueError, "Start waypoint must come before"):
            self.monitor.command("arm", 9, 4)

    def test_arm_shows_waypoints(self):
        self.monitor.command("arm", 4, 28)
        deadline = time.monotonic() + 3
        while self.monitor.status()["state"] != "ARMED" and time.monotonic() < deadline:
            time.sleep(0.05)
        s = self.monitor.status()
        self.assertEqual((s["state"], s["auto"]), ("ARMED", dict(start_seq=4, stop_seq=28)))

    def test_offline_when_payload_goes_quiet(self):
        self.stop.set()
        self.monitor.clock = lambda: time.monotonic() + 10
        self.assertEqual(self.monitor.status()["state"], "OFFLINE")
        with self.assertRaisesRegex(ValueError, "No signal"):
            self.monitor.command("start")


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.flight, self.sim = FlightService({"tcp"}), FlightService({"demo"})
        self.bp = payload_api(self.flight, self.sim)
        app = Flask(__name__)
        app.register_blueprint(self.bp)
        self.client = app.test_client()
        self.token = self.client.get("/api/payload/session").json["token"]

    def tearDown(self):
        for m in self.bp.monitors.values():
            m.closed.set()

    def post(self, data, **kw):
        return self.client.post("/api/payload/command", json=data, headers={"X-Payload-Token": self.token}, **kw)

    def test_guards(self):
        self.assertEqual(self.client.post("/api/payload/command", json={"mode": "sim", "action": "start"}).status_code, 403)
        self.assertEqual(self.client.get("/api/payload/status", environ_base={"REMOTE_ADDR": "10.0.0.9"}).status_code, 403)

    def test_simulated_payload_manual_record(self):
        s = self.client.get("/api/payload/status?mode=sim").json
        self.assertEqual((s["state"], s["simulated"]), ("READY", True))
        self.assertEqual(self.post({"mode": "sim", "action": "start"}).json["state"], "RECORDING")
        self.assertEqual(self.post({"mode": "sim", "action": "stop"}).json["state"], "READY")

    def test_arm_needs_mission_waypoints(self):
        r = self.post({"mode": "sim", "action": "arm", "start_seq": 4, "stop_seq": 9})
        self.assertEqual(r.status_code, 409)
        self.assertIn("waypoint", r.json["error"])

    def test_fly_without_aircraft(self):
        s = self.client.get("/api/payload/status?mode=fly").json
        self.assertEqual(s["state"], "OFFLINE")
        r = self.post({"mode": "fly", "action": "start"})
        self.assertEqual(r.status_code, 409)


if __name__ == "__main__":
    unittest.main()
