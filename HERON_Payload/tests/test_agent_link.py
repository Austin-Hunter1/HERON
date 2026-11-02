"""The NUC agent speaking real MAVLink 2 bytes, with simulated radios and a scripted Cube."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ["MAVLINK20"] = "1"
from pymavlink.dialects.v20 import ardupilotmega as mav  # noqa: E402

from heron_payload import protocol as P  # noqa: E402
from heron_payload.agent import Agent  # noqa: E402
from heron_payload.backends import FakeBackend  # noqa: E402
from heron_payload.config import presets  # noqa: E402
from heron_payload.controller import Controller  # noqa: E402


class Capture:
    def __init__(self):
        self.parser = mav.MAVLink(None)
        self.out = []

    def write(self, data):
        self.out.extend(self.parser.parse_buffer(data) or [])


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def decode(sender, method, *args):
    """Encode with a real MAVLink sender, then parse the bytes back like a receiver would."""
    cap = Capture()
    sender.file = cap
    getattr(sender, method)(*args)
    return cap.out[0]


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.backend = FakeBackend()
        self.controller = Controller(self.backend, clock=self.clock)
        self.controller.execute("prepare", dict(config=presets()[0]))
        self.down = Capture()
        conn = SimpleNamespace(mav=mav.MAVLink(self.down, srcSystem=1, srcComponent=191))
        self.agent = Agent(conn, self.controller, Path(self.tmp.name) / "active.json", clock=self.clock)
        self.gcs = mav.MAVLink(None, srcSystem=255, srcComponent=190)
        self.cube = mav.MAVLink(None, srcSystem=1, srcComponent=1)

    def tearDown(self):
        self.tmp.cleanup()

    def cmd(self, action, p2=0, p3=0, rid=77, conf=0):
        m = decode(self.gcs, "command_long_send", 1, 191, P.COMMAND, conf, P.ACTION_CODES[action], p2, p3, rid, 0, 0, 0)
        self.agent.receive(m)
        acks = [x for x in self.down.out if x.get_type() == "COMMAND_ACK"]
        return acks[-1]

    def cube_msg(self, method, *args):
        self.agent.receive(decode(self.cube, method, *args))

    def test_heartbeat_and_status_values(self):
        self.agent.periodic()
        kinds = [m.get_type() for m in self.down.out]
        self.assertIn("HEARTBEAT", kinds)
        values = {m.name: m.value for m in self.down.out if m.get_type() == "NAMED_VALUE_FLOAT"}
        self.assertEqual(set(values), set(P.STATUS_FIELDS))
        self.assertEqual(P.STATES[int(values[P.F_STATE])], "READY")
        texts = [m.text for m in self.down.out if m.get_type() == "STATUSTEXT"]
        self.assertIn(P.SETUP_PREFIX + presets()[0]["name"], texts)
        hb = next(m for m in self.down.out if m.get_type() == "HEARTBEAT")
        self.assertEqual((hb.get_srcComponent(), hb.type), (191, 18))

    def test_start_stop_with_ack_and_retry_dedupe(self):
        starts = []
        real = self.backend.start
        self.backend.start = lambda *a: (starts.append(1), real(*a))
        ack = self.cmd("start", rid=501)
        self.assertEqual((ack.command, ack.result, ack.result_param2), (P.COMMAND, P.RESULT_ACCEPTED, 501))
        self.assertEqual((ack.target_system, ack.target_component), (255, 190))
        again = self.cmd("start", rid=501, conf=1)          # ack was lost; GCS retries the same request
        self.assertEqual(again.result, P.RESULT_ACCEPTED)
        self.assertEqual(len(starts), 1)
        self.assertEqual(self.controller.state, "RECORDING")
        self.assertEqual(self.cmd("stop", rid=502).result, P.RESULT_ACCEPTED)
        self.assertEqual(self.controller.state, "READY")

    def test_refusal_has_reason_text(self):
        ack = self.cmd("arm", 9, 4, rid=600)
        self.assertEqual(ack.result, P.RESULT_DENIED)
        texts = [m.text for m in self.down.out if m.get_type() == "STATUSTEXT"]
        self.assertTrue(any(t.startswith("HERON: Start waypoint") for t in texts), texts)

    def test_commands_for_other_components_are_ignored(self):
        m = decode(self.gcs, "command_long_send", 1, 1, P.COMMAND, 0, 1, 0, 0, 5, 0, 0, 0)
        self.agent.receive(m)
        self.assertEqual(self.controller.state, "READY")
        self.assertFalse([x for x in self.down.out if x.get_type() == "COMMAND_ACK"])

    def test_full_auto_flight_from_cube_messages(self):
        self.assertEqual(self.cmd("arm", 4, 9, rid=700).result, P.RESULT_ACCEPTED)
        self.assertEqual(self.controller.state, "AUTO_ARMED")
        auto = 3  # ArduCopter custom_mode AUTO
        armed = mav.MAV_MODE_FLAG_SAFETY_ARMED | mav.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED
        self.cube_msg("heartbeat_send", 2, 3, armed, auto, 4)       # QUADROTOR, ARDUPILOTMEGA
        self.cube_msg("mission_current_send", 4)
        self.assertEqual(self.controller.state, "AUTO_ARMED")
        self.cube_msg("mission_item_reached_send", 4)
        self.assertEqual(self.controller.state, "RECORDING")
        self.agent.periodic()
        texts = [m.text for m in self.down.out if m.get_type() == "STATUSTEXT"]
        self.assertTrue(any("REC START at item 4" in t for t in texts), texts)
        self.cube_msg("mission_item_reached_send", 9)
        self.assertEqual(self.controller.state, "READY")

    def test_gcs_heartbeat_relayed_as_system_1_is_not_the_cube(self):
        self.cube_msg("heartbeat_send", 6, 8, 0, 0, 4)   # a GCS type heartbeat using sys id 1
        self.assertIsNone(self.controller.cube["mode"])


if __name__ == "__main__":
    unittest.main()
