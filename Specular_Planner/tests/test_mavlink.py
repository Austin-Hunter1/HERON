"""Actual MAVLink v2 encoding/decoding over an in-memory wire; no hardware I/O.

This peer exercises the protocol implementation, not ArduPilot flight dynamics.
"""
import os
os.environ["MAVLINK20"] = "1"
import queue
import threading
import time
import unittest
from unittest.mock import patch

from pymavlink import mavutil
from pymavlink.dialects.v20 import ardupilotmega as mav
from engine.flight import DEMO_PARAMS, FlightError, MavlinkLink, compare_items, flight_items
from engine.export import qgc_wpl
from test_flight import mission


class AirWriter:
    def __init__(self, peer):
        self.peer = peer

    def write(self, data):
        self.peer.incoming.put(self.peer.ground_parser.parse_char(data))


class MemoryAircraft:
    def __init__(self):
        self.incoming = queue.Queue()
        self.ground_parser = mav.MAVLink(None)
        self.air_parser = mav.MAVLink(None)
        self.mav = mav.MAVLink(self, srcSystem=255, srcComponent=190)
        self.air = mav.MAVLink(AirWriter(self), srcSystem=1, srcComponent=1)
        self.wire_lock = threading.RLock()
        self.items = {}
        self.count = 0
        self.mode = 0
        self.armed = False
        self.airborne = False
        self.current = 0
        self.last_telemetry = 0
        self.started = time.monotonic()
        self.commands = []
        self.reject = None
        self.legacy_request = False
        self.reject_mission = False
        self.drop_count = False
        self.drop_download_seq = None
        self.heartbeats = 0

    def write(self, data):
        with self.wire_lock:
            m = self.air_parser.parse_char(data)
            if m is None:
                return
            kind = m.get_type()
            if kind == "HEARTBEAT":
                self.heartbeats += 1
            elif kind == "PARAM_REQUEST_READ":
                key = m.param_id.rstrip("\x00")
                if key in DEMO_PARAMS:
                    self.air.param_value_send(key.encode(), DEMO_PARAMS[key], 9, len(DEMO_PARAMS), 0)
            elif kind == "COMMAND_LONG":
                self.commands.append(m.command)
                if m.command == self.reject:
                    self.air.command_ack_send(m.command, 2, target_system=255, target_component=190)
                    return
                if m.command == 512:
                    self.home()
                if m.command == 176:
                    self.mode = int(m.param2)
                if m.command == 400:
                    self.armed = m.param1 == 1
                if m.command == 300:
                    self.airborne = True
                self.air.command_ack_send(m.command, 0, target_system=255, target_component=190)
                self.telemetry()
            elif kind == "MISSION_COUNT":
                if self.drop_count:
                    self.drop_count = False
                    return
                if self.reject_mission:
                    self.air.mission_ack_send(255, 190, 4, 0)
                    return
                self.count = m.count
                self.items = {}
                self.request(0)
            elif kind in {"MISSION_ITEM_INT", "MISSION_ITEM"}:
                self.items[m.seq] = m
                if len(self.items) == self.count:
                    self.air.mission_ack_send(255, 190, 0, 0)
                else:
                    self.request(m.seq + 1)
            elif kind == "MISSION_REQUEST_LIST":
                self.air.mission_count_send(255, 190, len(self.items), 0)
            elif kind == "MISSION_REQUEST_INT":
                if m.seq == self.drop_download_seq:
                    self.drop_download_seq = None
                    return
                it = self.items[m.seq]
                integer = it.get_type() == "MISSION_ITEM_INT"
                x = it.x if integer else round(it.x * 1e7)
                y = it.y if integer else round(it.y * 1e7)
                cmd, frame, alt = it.command, it.frame, it.z
                if m.seq == 0:
                    alt = 1625  # ArduPilot's synthesized Home record.
                elif cmd == 20:
                    x, y, alt, frame = 0, 0, 0, 0
                self.air.mission_item_int_send(255, 190, m.seq, frame, cmd, 0, 1,
                                               it.param1, it.param2, it.param3, it.param4, x, y, alt, 0)
            elif kind == "MISSION_SET_CURRENT":
                self.current = m.seq
                self.air.mission_current_send(m.seq)

    def request(self, seq):
        method = self.air.mission_request_send if self.legacy_request else self.air.mission_request_int_send
        method(255, 190, seq, 0)

    def home(self):
        self.air.home_position_send(400860450, -1052336340, 1625000, 0, 0, 0, [1, 0, 0, 0], 0, 0, 0)

    def telemetry(self):
        with self.wire_lock:
            self.air.heartbeat_send(2, 3, 128 if self.armed else 0, self.mode, 4)
            self.air.sys_status_send(1, 1, 1, 20, 24600, 100, 92, 0, 0, 0, 0, 0, 0)
            self.air.gps_raw_int_send(0, 3, 400860450, -1052336340, 1625000, 100, 100, 0, 0, 16)
            self.air.ekf_status_report_send(1 | 2 | 4 | 16 | 32, 0, 0, 0, 0, 0)
            self.air.global_position_int_send(int((time.monotonic() - self.started) * 1000),
                                              400860450, -1052336340, 1625000,
                                              3000 if self.airborne else 0, 0, 0, 0, 0)
            self.air.extended_sys_state_send(0, 2 if self.airborne else 1)
            self.air.mission_current_send(self.current)
            self.home()

    def recv_match(self, blocking=True, timeout=0.2):
        if time.monotonic() - self.last_telemetry > 0.2:
            self.last_telemetry = time.monotonic()
            self.telemetry()
        try:
            return self.incoming.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self):
        pass


class WireTests(unittest.TestCase):
    def setUp(self):
        self.peer = MemoryAircraft()
        with patch.object(mavutil, "mavlink_connection", return_value=self.peer):
            self.link = MavlinkLink({"transport": "tcp", "host": "127.0.0.1", "port": 5760}, lambda _: None)
        self.cancel = threading.Event()
        self.items = flight_items(qgc_wpl(mission()))

    def tearDown(self):
        self.link.close()

    def test_herelink_connection_presets_and_vertical_speed(self):
        for kind, endpoint in (("herelink_hotspot", "udpin:0.0.0.0:14550"),
                               ("herelink_client", "udpout:192.168.42.129:14552")):
            with self.subTest(transport=kind):
                peer = MemoryAircraft()
                with patch.object(mavutil, "mavlink_connection", return_value=peer) as connect:
                    link = MavlinkLink({"transport": kind}, lambda _: None)
                try:
                    self.assertEqual(connect.call_args.args[0], endpoint)
                    self.assertTrue(link.snapshot()["connected"])
                    msg = mav.MAVLink_global_position_int_message(100, 0, 0, 0, 0, 300, 400, -150, 9000)
                    with link.cv:
                        link._update("GLOBAL_POSITION_INT", msg, time.monotonic())
                        self.assertEqual(link.s["vertical_speed"], 1.5)
                        self.assertEqual(link.s["speed"], 5)
                finally:
                    link.close()

    def test_real_encoded_roundtrip_and_launch_commands(self):
        self.link.refresh(self.cancel)
        s = self.link.snapshot()
        self.assertTrue(s["connected"])
        self.assertTrue(s["params_fresh"])
        self.assertTrue(s["ekf_ok"])
        self.assertEqual(s["battery_pct"], 92)
        self.link.upload(self.items, self.cancel)
        compare_items(self.items, self.link.download(self.cancel))
        self.link.mode(4, self.cancel)
        self.link.arm(self.cancel)
        self.link.set_current(1, self.cancel)
        self.link.mode(3, self.cancel)
        self.link.start(self.cancel)
        self.link.wait_airborne(self.cancel)
        self.assertTrue(self.link.snapshot()["armed"])
        self.assertIn(400, self.peer.commands)
        self.assertIn(300, self.peer.commands)
        self.assertGreater(self.peer.heartbeats, 0)
        self.link.mode(6, None)
        self.assertEqual(self.link.snapshot()["mode_id"], 6)

    def test_legacy_upload_requests_supported(self):
        self.peer.legacy_request = True
        self.link.upload(self.items, self.cancel)
        # Float-coordinate legacy protocol has lower precision than MISSION_ITEM_INT.
        compare_items(self.items, self.link.download(self.cancel))

    def test_upload_rejection(self):
        self.peer.reject_mission = True
        with self.assertRaisesRegex(FlightError, "rejected mission"):
            self.link.upload(self.items, self.cancel)

    def test_arm_denied_never_starts_mission(self):
        self.peer.reject = 400
        with self.assertRaisesRegex(FlightError, "refused command 400"):
            self.link.arm(self.cancel)
        self.assertNotIn(300, self.peer.commands)
        self.assertFalse(self.link.snapshot()["armed"])

    def test_missing_count_and_item_are_retried(self):
        self.peer.drop_count = True
        self.link.upload(self.items, self.cancel)
        self.peer.drop_download_seq = 2
        compare_items(self.items, self.link.download(self.cancel))

    def test_canceled_start_sends_no_command(self):
        self.link.mode(3, self.cancel)
        self.cancel.set()
        with self.assertRaises(FlightError):
            self.link.start(self.cancel)
        self.assertNotIn(300, self.peer.commands)

    def test_foreign_aircraft_cannot_overwrite_telemetry(self):
        with self.peer.wire_lock:
            self.peer.air.srcSystem = 2
            self.peer.air.heartbeat_send(2, 3, 128, 6, 4)
            self.peer.air.srcSystem = 1
        time.sleep(0.05)
        self.assertFalse(self.link.snapshot()["armed"])
        self.assertEqual(self.link.snapshot()["mode_id"], 0)

    def test_stale_ack_not_reused(self):
        with self.peer.wire_lock:
            self.peer.air.command_ack_send(400, 0, target_system=255, target_component=190)
        time.sleep(0.05)
        self.peer.reject = 400
        with self.assertRaisesRegex(FlightError, "refused command"):
            self.link.arm(self.cancel)


if __name__ == "__main__":
    unittest.main()
