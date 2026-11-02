"""Transit heading: face a lead-in on the first pass line, line up before WP1, face home before RTL."""
import math
import unittest

from engine.export import (
    CONDITION_YAW, NAV_RTL, NAV_WAYPOINT, _bearing, annotate_wpl, heron_meta_from_wpl,
    parse_wpl, plan_route_line, qgc_wpl, route_from_wpl, validate_wpl,
)
from engine.flight import flight_items

PAD = {"lat": 40.086045, "lon": -105.233634}


def survey():
    pts = [(0, 40.07898675, -105.22804400), (0, 40.07817920, -105.22801260),
           (1, 40.07826118, -105.22726039), (1, 40.07904811, -105.22752149)]
    return {
        "meta": {"h_agl": 120, "speed_mps": 8, "loiter_s": 0, "hold_s": 0, "roi_mode": "body_yaw",
                 "start": "2026-10-25T23:00:00Z"},
        "pad": dict(PAD),
        "hovers": [{"lat": la, "lon": lo, "loiter_s": 0, "pass_id": pid, "yaw_deg": 301.0} for pid, la, lo in pts],
    }


def fly_to(items):
    return [i for i in items if i["command"] == NAV_WAYPOINT and i["seq"] > 0]


class TransitHeadingTests(unittest.TestCase):
    def test_no_splines_only_plain_waypoints(self):
        items = parse_wpl(qgc_wpl(survey()))
        self.assertFalse([i for i in items if i["command"] == 82])
        self.assertEqual(len(fly_to(items)), 5)  # lead-in + 4 survey points

    def test_faces_lead_in_then_survey_heading_then_home(self):
        plan = survey()
        items = parse_wpl(qgc_wpl(plan))
        yaws = [i for i in items if i["command"] == CONDITION_YAW]
        lead = fly_to(items)[0]
        self.assertAlmostEqual(yaws[0]["p1"], _bearing(PAD["lat"], PAD["lon"], lead["lat"], lead["lon"]), places=3)
        self.assertAlmostEqual(yaws[1]["p1"], 301.0)
        self.assertEqual(yaws[1]["p2"], 30)
        last = plan["hovers"][-1]
        self.assertAlmostEqual(yaws[-1]["p1"], _bearing(last["lat"], last["lon"], PAD["lat"], PAD["lon"]), places=3)
        self.assertEqual(items[-1]["command"], NAV_RTL)

    def test_lead_in_is_50m_back_on_pass_one(self):
        lead, wp1, wp2 = fly_to(parse_wpl(qgc_wpl(survey())))[:3]
        self.assertAlmostEqual(_bearing(wp1["lat"], wp1["lon"], wp2["lat"], wp2["lon"]),
                               _bearing(lead["lat"], lead["lon"], wp1["lat"], wp1["lon"]), delta=0.5)
        d = math.hypot((lead["lat"] - wp1["lat"]) * 111132.92,
                       (lead["lon"] - wp1["lon"]) * 111132.92 * math.cos(math.radians(wp1["lat"])))
        self.assertAlmostEqual(d, 50.0, delta=0.5)

    def test_passes_upload_checks(self):
        plan = survey()
        wpl = qgc_wpl(plan)
        report = validate_wpl(wpl, plan)
        self.assertTrue(report["ok"], report)
        self.assertFalse([w for w in report["warnings"] if "fly-to" in w])
        flight_items(wpl)

    def test_round_trip_drops_lead_in(self):
        plan = survey()
        saved = annotate_wpl(qgc_wpl(plan), plan["meta"]["start"], plan)
        self.assertTrue(heron_meta_from_wpl(saved).get("lead_in"))
        route = route_from_wpl(saved, lead_in=True)
        self.assertEqual(len(route["waypoints"]), 4)
        self.assertAlmostEqual(route["waypoints"][0]["yaw_deg"], 301.0)

    def test_short_transit_or_no_body_yaw_is_unchanged(self):
        plan = survey()
        plan["meta"]["roi_mode"] = "none"
        self.assertEqual(len(fly_to(parse_wpl(qgc_wpl(plan)))), 4)
        plan = survey()
        plan["pad"] = {"lat": plan["hovers"][0]["lat"] + 0.0003, "lon": plan["hovers"][0]["lon"]}
        self.assertEqual(len(fly_to(parse_wpl(qgc_wpl(plan)))), 4)

    def test_map_line_is_straight_with_lead_in(self):
        self.assertEqual(len(plan_route_line(survey())), 1 + 1 + 4 + 1)


if __name__ == "__main__":
    unittest.main()
