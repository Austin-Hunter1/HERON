"""Run with python -m unittest discover -s tests -v. Never connects to hardware."""
import copy
import threading
import time
import unittest
from unittest.mock import patch

from flask import Flask
from engine.export import qgc_wpl
from engine.flight import DemoLink, FlightError, FlightService, compare_items, flight_items, port_is_autopilot
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

    def test_launch_waits_for_the_planned_time_unless_skipped(self):
        from datetime import datetime, timedelta, timezone
        self.service.plans[self.plan_id]["meta"]["start"] = (
            datetime.now(timezone.utc) + timedelta(seconds=30)
        ).isoformat()
        self.upload()
        self.service.submit("launch", self.plan_id, "LAUNCH", wait_until_start=True)
        deadline = time.monotonic() + 3
        while "Waiting" not in self.service.job["message"] and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertIn("Waiting", self.service.job["message"])
        self.assertFalse(self.service.link.armed)
        self.service.rtl()
        self.assertEqual(finish(self.service)["state"], "error")
        self.service.submit("launch", self.plan_id, "LAUNCH", wait_until_start=False)
        self.assertEqual(finish(self.service)["state"], "complete")
        self.assertTrue(self.service.link.armed)

    def test_upload_readback_launch_rtl(self):
        self.assertFalse(self.service.preflight(self.plan_id)["ready"])
        self.upload()
        self.assertTrue(self.service.preflight(self.plan_id)["ready"])
        self.service.submit("launch", self.plan_id, "LAUNCH")
        self.assertEqual(finish(self.service)["state"], "complete")
        self.assertTrue(self.service.snapshot()["telemetry"]["armed"])
        self.service.rtl()
        self.assertEqual(self.service.link.mode_id, 6)

    def test_read_existing_mission_then_fly_it(self):
        self.service.link.upload(flight_items(self.wpl), None)
        self.service.submit("read")
        self.assertEqual(finish(self.service)["state"], "complete")
        onboard = self.service.snapshot()["onboard"]
        self.assertEqual((onboard["source"], onboard["n_wp"], onboard["error"]), ("aircraft", 1, None))
        read_id = onboard["plan_id"]
        self.assertTrue(self.service.preflight(read_id)["ready"])
        self.service.submit("launch", read_id, "LAUNCH")
        self.assertEqual(finish(self.service)["state"], "complete")
        self.assertTrue(self.service.link.armed)
        self.assertEqual(self.service.link.mode_id, 3)
        deadline = time.monotonic() + 5
        while self.service.snapshot()["target"] is None and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertIsNotNone(self.service.snapshot()["target"])

    def test_takeoff_pulses_the_next_waypoint_not_the_launch_pad(self):
        self.upload()
        items = flight_items(self.wpl)
        pad = self.plan["pad"]
        # Aircraft armed somewhere other than the pad stored in the mission.
        self.service.link.home.update(lat=pad["lat"] + 0.01, lon=pad["lon"])
        self.service.link.position.update(lat=pad["lat"] + 0.01, lon=pad["lon"])
        self.service.link.current = 1  # NAV_TAKEOFF, whose lat/lon is the planned pad
        target = self.service.snapshot()["target"]
        self.assertTrue(target["taking_off"])
        self.assertEqual(target["wp"], 1)
        self.assertGreater(abs(target["lat"] - pad["lat"]) + abs(target["lon"] - pad["lon"]), 1e-4)
        self.assertAlmostEqual(target["lat"], 40.0861, places=5)
        self.service.link.current = 2  # DO_CHANGE_SPEED still before the first waypoint
        target = self.service.snapshot()["target"]
        self.assertTrue(target["taking_off"])
        self.assertAlmostEqual(target["lat"], 40.0861, places=5)
        wp_seq = next(i["seq"] for i in items if i["command"] == 16 and i["seq"] > 0)
        self.service.link.current = wp_seq
        target = self.service.snapshot()["target"]
        self.assertFalse(target["taking_off"])
        self.assertEqual(target["seq"], wp_seq)
        self.service.link.current = items[-1]["seq"]  # RTL, after the last waypoint
        target = self.service.snapshot()["target"]
        self.assertTrue(target["returning"])
        self.assertFalse(target["taking_off"])
        self.assertAlmostEqual(target["lat"], pad["lat"] + 0.01, places=5)

    def test_read_mission_reports_roi_aim_points(self):
        plan = mission()
        plan["hovers"][0].update(splash_lat=40.0863, splash_lon=-105.2333)
        items = flight_items(qgc_wpl(plan))
        self.service.link.upload(items, None)
        self.service.submit("read")
        self.assertEqual(finish(self.service)["state"], "complete")
        onboard = self.service.snapshot()["onboard"]
        wp = next(i["seq"] for i in items if i["command"] == 16 and abs(i["lat"] - 40.0861) < 1e-6)
        self.assertEqual([(r["lat"], r["wp"]) for r in onboard["roi"]], [(40.0863, wp)])

    def test_read_mission_with_unset_home_uses_aircraft_home(self):
        items = flight_items(self.wpl)
        items[0] = dict(items[0], lat=0.0, lon=0.0)  # drone that has never had a GPS fix
        self.service.link.upload(items, None)
        self.service.submit("read")
        self.assertEqual(finish(self.service)["state"], "complete")
        read_id = self.service.snapshot()["onboard"]["plan_id"]
        mission = self.service.snapshot(read_id)["mission"]
        self.assertEqual(mission["home_source"], "aircraft")
        self.assertAlmostEqual(mission["home"]["lat"], 40.086045, places=5)
        self.assertLess(mission["length_m"], 100)
        self.service.submit("launch", read_id, "LAUNCH")
        self.assertEqual(finish(self.service)["state"], "complete")

    def test_read_mission_has_no_pad_distance_check(self):
        items = flight_items(self.wpl)
        for it in items[1:]:
            if it["lat"] or it["lon"]:
                it["lat"] += 0.005  # about 550 m north of the drone
        self.service.link.upload(items, None)
        self.service.submit("read")
        finish(self.service)
        read_id = self.service.snapshot()["onboard"]["plan_id"]
        preflight = self.service.preflight(read_id)
        self.assertNotIn("pad", [c["key"] for c in preflight["checks"]])
        self.assertTrue(preflight["ready"])

    def test_read_unflyable_mission_is_shown_but_blocked(self):
        items = flight_items(self.wpl)
        items[-1] = dict(items[-1], command=21)
        self.service.link.upload(items, None)
        self.service.submit("read")
        self.assertEqual(finish(self.service)["state"], "error")
        onboard = self.service.snapshot()["onboard"]
        self.assertIsNone(onboard["plan_id"])
        self.assertIn("will not fly", onboard["error"])
        self.assertTrue(onboard["path"])

    def test_read_empty_aircraft(self):
        self.service.submit("read")
        self.assertEqual(finish(self.service)["state"], "error")
        self.assertIsNone(self.service.snapshot()["onboard"])

    def test_upload_records_onboard_mission(self):
        self.upload()
        onboard = self.service.snapshot()["onboard"]
        self.assertEqual((onboard["source"], onboard["plan_id"]), ("heron", self.plan_id))

    def test_confirmation_required(self):
        self.upload()
        with self.assertRaises(FlightError):
            self.service.submit("launch", self.plan_id)
        self.assertFalse(self.service.link.armed)

    def test_usb_flight_controller_detection(self):
        self.assertTrue(port_is_autopilot(0x1209, 0x5741, "ArduPilot (COM3)", "ArduPilot Project", ""))
        self.assertFalse(port_is_autopilot(0x10C4, 0xEA60, "Silicon Labs CP210x USB to UART Bridge", "", ""))

    def test_demo_exit_during_operation(self):
        self.service.job["state"] = "running"
        self.service.disconnect()
        self.assertIsNone(self.service.link)
        self.assertEqual(self.service.job["state"], "idle")

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

    def test_far_from_pad_still_uploads_and_launches_where_it_sits(self):
        self.service.link.position["lat"] += 0.01  # about 1.1 km from the planned pad
        self.upload()
        pad = next(c for c in self.service.preflight(self.plan_id)["checks"] if c["key"] == "pad")
        self.assertTrue(pad["ok"])
        self.assertIn("m from the planned pad", pad["message"])
        self.service.set_check("route_radius", False)
        self.service.submit("launch", self.plan_id, "LAUNCH")
        self.assertEqual(finish(self.service)["state"], "complete")
        self.assertAlmostEqual(self.service.link.home["lat"], 40.096045, places=6)

    def test_stale_home_before_arming_does_not_block(self):
        # Powered up at the car, carried to the shore: arming moves Home to the aircraft.
        self.service.link.home["lat"] += 0.01
        self.upload()
        self.assertTrue(self.service.preflight(self.plan_id)["ready"])
        self.service.submit("launch", self.plan_id, "LAUNCH")
        self.assertEqual(finish(self.service)["state"], "complete")
        link = self.service.link
        self.assertLess(abs(link.home["lat"] - 40.086045), 1e-6)

    def test_aircraft_a_little_off_the_pad_still_launches(self):
        self.service.link.position["lat"] += 0.0004  # about 45 m north of the planned pad
        self.upload()
        self.assertTrue(self.service.preflight(self.plan_id)["ready"])
        self.service.submit("launch", self.plan_id, "LAUNCH")
        self.assertEqual(finish(self.service)["state"], "complete")
        self.assertAlmostEqual(self.service.link.home["lat"], 40.086445, places=6)

    def test_locked_home_elsewhere_disarms_before_auto(self):
        self.upload()
        link = self.service.link
        original = link.arm

        def arm_with_locked_home(cancel):
            original(cancel)
            link.home["lat"] += 0.01  # Home stays where DO_SET_HOME locked it
        with patch.object(link, "arm", side_effect=arm_with_locked_home):
            self.service.submit("launch", self.plan_id, "LAUNCH")
            job = finish(self.service)
        self.assertEqual(job["state"], "error")
        self.assertIn("Home is", job["message"])
        self.assertFalse(link.armed)
        self.assertNotEqual(link.mode_id, 3)

    def test_sim_flies_the_mission_speed_not_a_fixed_demo_speed(self):
        link = self.service.link
        items = flight_items(self.wpl)
        speed_seq = next(i["seq"] for i in items if i["command"] == 178)
        link.upload(items, None)
        link.armed, link.flying, link.mode_id, link.alt = True, True, 3, 30
        link.current = speed_seq
        link.snapshot()
        self.assertEqual(link.speed, 5)
        far = {"lat": link.position["lat"] + 0.01, "lon": link.position["lon"]}
        link.last = time.monotonic() - 0.5
        link._fly_to(far, 0.5)
        self.assertAlmostEqual(link.ground_speed, 5, places=3)

    def test_skip_all_arming_checks_is_not_read_as_all_enabled(self):
        from engine.flight import PARAM_ALIASES
        key, convert = PARAM_ALIASES["ARMING_SKIPCHK"]
        self.assertEqual((key, convert(0)), ("ARMING_CHECK", 1))
        self.assertLess(convert(-1), 0)
        self.assertLess(convert(8), 0)

    def test_armed_upload_blocked(self):
        self.service.link.armed = True
        self.service.submit("upload", self.plan_id)
        self.assertEqual(finish(self.service)["state"], "error")
        self.assertEqual(self.service.link.items, [])

    def test_ground_check_does_not_fail_once_airborne(self):
        self.upload()
        baseline = self.service.link.snapshot()
        flying = {**baseline, "armed": True, "landed": 2, "relative_alt": 30}
        with patch.object(self.service.link, "snapshot", return_value=flying):
            landed = next(c for c in self.service.preflight(self.plan_id)["checks"] if c["key"] == "landed")
        self.assertTrue(landed["ok"])
        self.assertIn("in the air", landed["message"])

    def test_disarmed_aircraft_on_the_ground_without_a_landed_message(self):
        self.upload()
        baseline = self.service.link.snapshot()
        sitting = {**baseline, "armed": False, "landed": None,
                   "relative_alt": -1.2, "vertical_speed": 0,
                   "fresh": {**baseline["fresh"], "landed": False, "position": True}}
        with patch.object(self.service.link, "snapshot", return_value=sitting):
            landed = next(c for c in self.service.preflight(self.plan_id)["checks"] if c["key"] == "landed")
        self.assertTrue(landed["ok"])
        self.assertIn("on the ground", landed["message"])

    def test_stale_or_unhealthy_state_blocks_launch(self):
        self.upload()
        baseline = self.service.link.snapshot()
        changes = [
            {"connected": False}, {"battery_pct": -1}, {"battery_pct": 20},
            {"gps_fix": 2}, {"satellites": 0}, {"ekf_ok": False},
            {"sensors_ok": False},
            {"landed": 0, "relative_alt": 20, "fresh": {**baseline["fresh"], "landed": False}},
            {"fresh": {**baseline["fresh"], "position": False}},
            {"params": {**baseline["params"], "ARMING_CHECK": 0}},
            {"params": {**baseline["params"], "ARMING_CHECK": -4108}},
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

    def test_skipped_checks_do_not_block_but_required_ones_stay(self):
        self.upload()
        baseline = self.service.link.snapshot()
        small = {**baseline, "params": {**baseline["params"], "FENCE_TYPE": 1, "FENCE_RADIUS": 5}}
        with patch.object(self.service.link, "snapshot", return_value=small):
            self.assertFalse(self.service.preflight(self.plan_id)["ready"])
            self.service.set_check("fence_circle", False)
            self.service.set_check("route_radius", False)
            report = self.service.preflight(self.plan_id)
            self.assertTrue(report["ready"])
            skipped = {c["key"] for c in report["checks"] if not c["enabled"]}
            self.assertEqual(skipped, {"fence_circle", "route_radius"})
        self.service.set_check("landed", False)
        with self.assertRaises(FlightError):
            self.service.set_check("verified", False)

    def test_ardupilot_messages_keep_source_and_severity(self):
        self.service.event("PreArm: Compass not calibrated", "ardupilot", 2)
        last = self.service.snapshot()["events"][-1]
        self.assertEqual((last["source"], last["severity"]), ("ardupilot", 2))

    def test_rtl_interrupts_launch_before_arm(self):
        self.upload()
        started = threading.Event()
        release = threading.Event()
        original = self.service.link.refresh

        def slow_refresh(cancel, need_home=True):
            started.set()
            release.wait(2)
            original(cancel, need_home)

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

    def test_live_reflections_follow_the_aircraft_from_launch(self):
        from datetime import datetime, timezone
        import app as planner
        client = planner.app.test_client()
        body = {
            "lat": 40.0824, "lon": -105.2268, "alt": 30,
            "launched": "2026-09-29T15:00:00", "elapsed_s": 0,
            "constellations": ["G"], "mask": 5, "aim": "G01",
        }
        first = client.post("/api/reflections", json=body)
        self.assertEqual(first.status_code, 200, first.json)
        self.assertTrue(first.json["speculars"])
        sky = datetime.fromisoformat(first.json["time"])
        self.assertLess(abs((datetime.now(timezone.utc) - sky).total_seconds()), 5)
        self.assertFalse(first.json["time"].startswith("2026-09-29"))
        moved = client.post("/api/reflections", json=dict(body, lat=40.09, lon=-105.20, elapsed_s=30))
        prn = first.json["speculars"][0]["prn"]
        there = next(s for s in moved.json["speculars"] if s["prn"] == prn)
        here = first.json["speculars"][0]
        self.assertGreater(abs(here["lat"] - there["lat"]) + abs(here["lon"] - there["lon"]), 1e-4)
        ground = client.post("/api/reflections", json=dict(body, alt=1))
        self.assertEqual(ground.json["speculars"], [])
        self.assertTrue(ground.json["sats"])
        self.assertTrue(any(row["above_mask"] for row in ground.json["sats"]))


if __name__ == "__main__":
    unittest.main()
