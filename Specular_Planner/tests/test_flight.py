"""Run with python -m unittest discover -s tests -v. Never connects to hardware."""
import copy
import threading
import time
import unittest
from unittest.mock import patch

from flask import Flask
from engine.export import qgc_wpl
from engine.flight import DemoLink, FlightError, FlightService, compare_items, flight_items
from engine.flight_api import flight_api


def mission():
    return {"meta": {"h_agl": 30, "speed_mps": 5, "loiter_s": 1},
            "pad": {"lat": 40.086045, "lon": -105.233634},
            "hovers": [{"lat": 40.0861, "lon": -105.2335, "loiter_s": 1}]}


def finish(service):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if service.job["state"] != "running":
            return service.job
        time.sleep(0.01)
    raise AssertionError("Flight operation did not finish")


class FlightTests(unittest.TestCase):
    def setUp(self):
        self.service = FlightService()
        self.service.connect({"transport": "demo"})
        self.plan = mission()
        self.wpl = qgc_wpl(self.plan)
        self.plan_id = self.service.register_plan(self.wpl, self.plan["meta"])

    def upload(self):
        self.service.submit("upload", self.plan_id)
        self.assertEqual(finish(self.service)["state"], "complete")

    def test_upload_readback_launch_rtl(self):
        self.assertFalse(self.service.preflight(self.plan_id)["ready"])
        self.upload()
        self.assertTrue(self.service.preflight(self.plan_id)["ready"])
        self.service.submit("launch", self.plan_id, "LAUNCH")
        self.assertEqual(finish(self.service)["state"], "complete")
        self.assertTrue(self.service.snapshot()["telemetry"]["armed"])
        self.service.rtl()
        self.assertEqual(self.service.link.mode_id, 6)

    def test_confirmation_required(self):
        self.upload()
        with self.assertRaises(FlightError):
            self.service.submit("launch", self.plan_id)
        self.assertFalse(self.service.link.armed)

    def test_reconnect_invalidates_verification(self):
        self.upload()
        self.service.disconnect()
        self.service.connect({"transport": "demo"})
        self.assertIsNone(self.service.verified)
        self.assertFalse(self.service.preflight(self.plan_id)["ready"])

    def test_reboot_invalidates_verification(self):
        self.upload()
        self.service.link.generation = "new-boot"
        self.assertFalse(self.service.preflight(self.plan_id)["ready"])

    def test_new_plan_does_not_inherit_verification(self):
        self.upload()
        new_id = self.service.register_plan(self.wpl, {})
        self.assertFalse(self.service.preflight(new_id)["ready"])

    def test_changed_aircraft_mission_blocks_arm(self):
        self.upload()
        self.service.link.items[3]["alt"] += 10
        self.service.submit("launch", self.plan_id, "LAUNCH")
        self.assertEqual(finish(self.service)["state"], "error")
        self.assertFalse(self.service.link.armed)
        self.assertIsNone(self.service.verified)

    def test_wrong_field_blocks_upload(self):
        self.service.link.home["lat"] += 1
        self.service.submit("upload", self.plan_id)
        self.assertEqual(finish(self.service)["state"], "error")
        self.assertEqual(self.service.link.items, [])

    def test_armed_upload_blocked(self):
        self.service.link.armed = True
        self.service.submit("upload", self.plan_id)
        self.assertEqual(finish(self.service)["state"], "error")
        self.assertEqual(self.service.link.items, [])

    def test_stale_or_unhealthy_state_blocks_launch(self):
        self.upload()
        baseline = self.service.link.snapshot()
        changes = [
            {"connected": False}, {"battery_pct": -1}, {"battery_pct": 20},
            {"gps_fix": 2}, {"satellites": 0}, {"ekf_ok": False},
            {"sensors_ok": False}, {"landed": 0}, {"params_fresh": False},
            {"fresh": {**baseline["fresh"], "position": False}},
            {"params": {**baseline["params"], "ARMING_CHECK": 0}},
            {"params": {**baseline["params"], "FENCE_ENABLE": 0}},
            {"params": {**baseline["params"], "FENCE_TYPE": 4}},
            {"params": {**baseline["params"], "BATT_FS_LOW_ACT": 0}},
            {"params": {**baseline["params"], "SYSID_MYGCS": 42}},
            {"params": {**baseline["params"], "FENCE_RADIUS": 5}},
            {"params": {**baseline["params"], "RTL_ALT": 15000}},
        ]
        for change in changes:
            with self.subTest(change=change), patch.object(self.service.link, "snapshot", return_value={**baseline, **change}):
                self.assertFalse(self.service.preflight(self.plan_id)["ready"])

    def test_rtl_interrupts_launch_before_arm(self):
        self.upload()
        started = threading.Event()
        release = threading.Event()
        original = self.service.link.refresh

        def slow_refresh(cancel):
            started.set()
            release.wait(2)
            original(cancel)

        with patch.object(self.service.link, "refresh", side_effect=slow_refresh):
            self.service.submit("launch", self.plan_id, "LAUNCH")
            self.assertTrue(started.wait(1))
            self.service.rtl()
            release.set()
            self.assertEqual(finish(self.service)["state"], "error")
        self.assertFalse(self.service.link.armed)
        self.assertEqual(self.service.link.mode_id, 6)

    def test_duplicate_operations_rejected(self):
        self.service.operation_lock.acquire()
        try:
            with self.assertRaises(FlightError):
                self.service.submit("upload", self.plan_id)
        finally:
            self.service.operation_lock.release()

    def test_readback_tolerates_home_and_unused_rtl_fields(self):
        expected = flight_items(self.wpl)
        actual = copy.deepcopy(expected)
        actual[0]["alt"] = 1600
        actual[0]["lat"] += 0.00001
        actual[-1].update(lat=0, lon=0, frame=0)
        compare_items(expected, actual)

    def test_readback_rejects_changed_coordinate_altitude_and_frame(self):
        expected = flight_items(self.wpl)
        for key, value in (("lat", 1), ("alt", 50), ("frame", 0), ("command", 20), ("p1", 99)):
            actual = copy.deepcopy(expected)
            actual[3][key] = value
            with self.subTest(key=key), self.assertRaises(FlightError):
                compare_items(expected, actual)

    def test_nan_and_bad_altitude_rejected(self):
        for alt in (float("nan"), float("inf"), -1, 0, 200):
            plan = mission()
            plan["meta"]["h_agl"] = alt
            with self.subTest(alt=alt), self.assertRaises(FlightError):
                flight_items(qgc_wpl(plan))

    def test_cleared_roi_and_speed_survive_normalization(self):
        plan = mission()
        plan["hovers"][0].update(splash_lat=40.0865, splash_lon=-105.2335)
        expected = flight_items(qgc_wpl(plan))
        actual = copy.deepcopy(expected)
        for item in actual:
            if item["command"] == 178:
                item.update(frame=0, p3=-1)
        compare_items(expected, actual)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.service = FlightService()
        self.app.register_blueprint(flight_api(self.service))
        self.client = self.app.test_client()
        self.token = self.client.get("/api/flight/session").json["token"]

    def test_no_token_no_aircraft_command(self):
        self.assertEqual(self.client.post("/api/flight/connect", json={"transport": "demo"}).status_code, 403)
        self.assertIsNone(self.service.link)

    def test_cross_origin_blocked(self):
        response = self.client.post("/api/flight/connect", json={"transport": "demo"},
                                    headers={"X-Flight-Token": self.token, "Origin": "https://evil.example"})
        self.assertEqual(response.status_code, 403)

    def test_dns_rebinding_host_blocked(self):
        self.assertEqual(self.client.get("/api/flight/session", base_url="http://evil.example").status_code, 403)

    def test_local_demo_connection_and_status(self):
        response = self.client.post("/api/flight/connect", json={"transport": "demo"}, headers={"X-Flight-Token": self.token})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["telemetry"]["transport"], "demo")
        self.assertEqual(self.client.get("/api/flight/status").headers["Cache-Control"], "no-store")


class PlannerIntegrationTests(unittest.TestCase):
    def test_existing_plan_registers_identical_flight_mission(self):
        import tempfile
        from pathlib import Path
        import app as planner
        data = {"start": "2026-09-29T15:00:00", "h_agl": 30, "speed_mps": 5,
                "loiter_s": 1, "duration_min": 10, "mode": "click", "pad": mission()["pad"],
                "waypoints": [{"lat": 40.0861, "lon": -105.2335}], "constellations": ["G"]}
        with tempfile.TemporaryDirectory() as output, patch.object(planner, "OUTPUT", Path(output)):
            client = planner.app.test_client()
            response = client.post("/api/plan", json=data)
            self.assertEqual(response.status_code, 200, response.json)
            plan = response.json
            saved = planner.flight.plan(plan["flight_plan_id"])
            export = client.get(plan["exports"]["waypoints"])
            self.assertEqual(export.data.decode(), saved["wpl"])
            self.assertTrue(flight_items(saved["wpl"]))
            export.close()

    def test_flight_ui_ids_are_present_and_unique(self):
        import re
        from pathlib import Path
        from html.parser import HTMLParser

        class IdParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.ids = []

            def handle_starttag(self, tag, attrs):
                self.ids += [value for key, value in attrs if key == "id"]

        root = Path(__file__).resolve().parents[1]
        parser = IdParser()
        parser.feed((root / "static/index.html").read_text())
        self.assertEqual(len(parser.ids), len(set(parser.ids)))
        refs = set(re.findall(r'el\("([^"]+)"\)', (root / "static/flight.js").read_text()))
        self.assertFalse(refs - set(parser.ids), refs - set(parser.ids))


if __name__ == "__main__":
    unittest.main()
