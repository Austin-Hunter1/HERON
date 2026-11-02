"""Waypoint-file import: read a Mission Planner file and rebuild the playback."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]

from datetime import datetime, timezone

from engine.export import annotate_wpl, heron_meta_from_wpl, normalize_wpl, qgc_wpl, route_from_wpl, run_card, takeoff_from_name, takeoff_from_wpl
from engine.flight import flight_items


def mission():
    return {
        "meta": {"h_agl": 30, "speed_mps": 5, "loiter_s": 1, "hold_s": 12, "roi_mode": "per_point"},
        "pad": {"lat": 40.086045, "lon": -105.233634},
        "hovers": [
            {"lat": 40.0861, "lon": -105.2335, "loiter_s": 1, "splash_lat": 40.0824, "splash_lon": -105.2268},
            {"lat": 40.0840, "lon": -105.2300, "loiter_s": 4},
        ],
    }


class RouteParseTests(unittest.TestCase):
    def test_round_trip_keeps_home_hold_speed_and_splash(self):
        route = route_from_wpl(qgc_wpl(mission()))
        self.assertAlmostEqual(route["pad"]["lat"], 40.086045, places=6)
        self.assertAlmostEqual(route["h_agl"], 30)
        self.assertAlmostEqual(route["speed_mps"], 5)
        self.assertAlmostEqual(route["hold_s"], 12)
        self.assertEqual(route["roi_mode"], "per_point")
        self.assertEqual(len(route["waypoints"]), 2)
        self.assertAlmostEqual(route["waypoints"][0]["splash_lat"], 40.0824, places=5)
        self.assertAlmostEqual(route["waypoints"][1]["loiter_s"], 4)
        self.assertTrue(route["loiter_mixed"])
        self.assertFalse(route["warnings"])

    def test_body_yaw_stays_with_its_waypoint(self):
        plan = mission()
        plan["meta"]["roi_mode"] = "body_yaw"
        plan["meta"]["hold_s"] = 0
        plan["hovers"][0]["yaw_deg"] = 123
        plan["hovers"][1]["yaw_deg"] = 10
        route = route_from_wpl(qgc_wpl(plan))
        self.assertEqual(route["roi_mode"], "body_yaw")
        self.assertEqual(route["hold_s"], 0)
        self.assertAlmostEqual(route["waypoints"][0]["yaw_deg"], 123)
        self.assertAlmostEqual(route["waypoints"][1]["yaw_deg"], 10)
        self.assertNotIn("splash_lat", route["waypoints"][0])

    def test_space_separated_rows(self):
        text = qgc_wpl(mission()).replace("\t", " ")
        route = route_from_wpl(text)
        self.assertEqual(len(route["waypoints"]), 2)
        self.assertTrue(normalize_wpl(text).split("\n")[1].count("\t") >= 11)

    def test_rejects_empty_and_bad_header(self):
        with self.assertRaisesRegex(ValueError, "empty"):
            route_from_wpl("   \n")
        with self.assertRaisesRegex(ValueError, "QGC WPL 110"):
            route_from_wpl("not a mission\n")

    def test_older_files_pair_the_aim_point_written_before_each_waypoint(self):
        route = route_from_wpl((ROOT / "HERON_20260921_1500.waypoints").read_text())
        self.assertGreater(len(route["waypoints"]), 10)
        first = route["waypoints"][0]
        self.assertAlmostEqual(first["lat"], 40.08559641, places=5)
        self.assertAlmostEqual(first["splash_lat"], 40.08542015, places=5)
        last = route["waypoints"][-1]
        self.assertAlmostEqual(last["lat"], 40.08663331, places=5)
        self.assertAlmostEqual(last["splash_lat"], 40.08654606, places=5)
        self.assertTrue(all("splash_lat" in h for h in route["waypoints"]))

    def test_saved_file_keeps_the_planned_takeoff_and_upload_ignores_it(self):
        plan = mission()
        plan["meta"]["start"] = "2026-10-18T21:30:00+00:00"
        saved = annotate_wpl(qgc_wpl(plan), plan["meta"]["start"])
        self.assertIn("# HERON takeoff 2026-10-18T21:30:00Z", saved.split("\n")[1])
        when = takeoff_from_wpl(saved)
        self.assertEqual(when, datetime(2026, 10, 18, 21, 30, tzinfo=timezone.utc))
        clean = normalize_wpl(saved)
        self.assertNotIn("#", clean)
        self.assertEqual(clean, qgc_wpl(plan))
        route = route_from_wpl(saved)
        self.assertEqual(len(route["waypoints"]), 2)
        self.assertEqual(takeoff_from_name("HERON_20261018_2130.waypoints"), when)
        self.assertIsNone(takeoff_from_name("mission.waypoints"))
        tracked = mission()
        tracked["hovers"][0]["prn"] = "G12"
        tracked["hovers"][1]["prn"] = "G12"
        tracked["targets"] = [{"name": "box", "coordinates": [[-105.24, 40.08], [-105.22, 40.08], [-105.22, 40.09], [-105.24, 40.08]]}]
        noted = annotate_wpl(qgc_wpl(tracked), tracked["meta"].get("start"), tracked)
        meta = heron_meta_from_wpl(noted)
        self.assertEqual(meta["track"], ["G12", "G12"])
        self.assertEqual(meta["areas"][0]["name"], "box")
        self.assertEqual(len(meta["areas"][0]["ring"]), 4)
        self.assertNotIn("#", normalize_wpl(noted))

    def test_run_card_allows_a_track_satellite_with_no_elevation(self):
        text = run_card({
            "meta": {
                "start": "2026-10-18T21:30:00+00:00",
                "h_agl": 50,
                "n_targets": 1,
                "n_hovers": 1,
                "transit_m": 100,
                "total_s": 60,
                "hold_s": 0,
                "almanac_age_days": 1,
                "l5_prns_on_water": [],
                "target_hits": {},
                "loiter_s": 1,
                "mode": "file",
            },
            "hovers": [{
                "name": "WP1", "lat": 40.0, "lon": -105.0,
                "prn": "G12", "el": None, "arrive_s": 10, "loiter_s": 1,
            }],
        })
        self.assertIn("aim G12", text)
        self.assertNotIn("el None", text)

    def test_rejects_home_at_origin(self):
        text = qgc_wpl(mission()).split("\n")
        fields = text[1].split("\t")
        fields[8] = "0.00000000"
        fields[9] = "0.00000000"
        text[1] = "\t".join(fields)
        with self.assertRaisesRegex(ValueError, "0,0"):
            route_from_wpl("\n".join(text))


class ImportApiTests(unittest.TestCase):
    def test_import_redraws_plan_and_registers_the_same_file(self):
        import app as planner

        built = {
            "start": "2026-09-29T15:00:00",
            "h_agl": 30,
            "speed_mps": 5,
            "loiter_s": 1,
            "duration_min": 10,
            "mode": "click",
            "pad": mission()["pad"],
            "waypoints": [{"lat": 40.0861, "lon": -105.2335}, {"lat": 40.0840, "lon": -105.2300}],
            "constellations": ["G"],
        }
        with tempfile.TemporaryDirectory() as output:
            with patch.object(planner, "OUTPUT", Path(output)):
                client = planner.app.test_client()
                made = client.post("/api/plan", json=built)
                self.assertEqual(made.status_code, 200, made.json)
                export = client.get(made.json["exports"]["waypoints"])
                wpl = export.data.decode()
                export.close()
                loaded = client.post(
                    "/api/plan/import",
                    json={
                        "wpl": wpl,
                        "filename": "already.waypoints",
                        "start": "2026-09-29T16:00:00",
                        "constellations": ["G"],
                        "elev_mask": 20,
                    },
                )
                self.assertEqual(loaded.status_code, 200, loaded.json)
                plan = loaded.json
                self.assertEqual(plan["meta"]["mode"], "file")
                self.assertEqual(plan["meta"]["source_file"], "already.waypoints")
                self.assertEqual(plan["meta"]["n_hovers"], 2)
                self.assertAlmostEqual(plan["pad"]["lat"], built["pad"]["lat"], places=6)
                self.assertAlmostEqual(plan["hovers"][1]["lon"], -105.2300, places=5)
                self.assertGreater(len(plan["frames"]), 2)
                self.assertEqual(plan["frames"][0]["phase"], "climb")
                self.assertEqual(plan["frames"][-1]["phase"], "land")
                saved = planner.flight.plan(plan["flight_plan_id"])
                self.assertIn("# HERON takeoff 2026-09-29T15:00:00Z", wpl)
                self.assertTrue(plan["meta"]["start"].startswith("2026-09-29T15:00"))
                self.assertNotIn("# HERON", saved["wpl"])
                self.assertEqual(saved["wpl"], normalize_wpl(wpl))
                self.assertEqual(planner.sim.plan(plan["flight_plan_id"])["wpl"], saved["wpl"])
                self.assertTrue(flight_items(saved["wpl"]))
                exported = client.get(plan["exports"]["waypoints"])
                exported_text = exported.data.decode()
                exported.close()
                self.assertIn("# HERON takeoff 2026-09-29T15:00:00Z", exported_text)
                self.assertEqual(normalize_wpl(exported_text), saved["wpl"])

    def test_bad_file_is_rejected(self):
        import app as planner

        client = planner.app.test_client()
        response = client.post("/api/plan/import", json={"wpl": "hello"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("QGC WPL 110", response.json["error"])
