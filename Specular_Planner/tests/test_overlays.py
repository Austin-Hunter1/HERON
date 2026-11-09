"""Map layers from earlier flights: listing and file serving."""
import json

import app as planner


def test_lists_imported_flights():
    c = planner.app.test_client()
    items = c.get("/api/overlays").get_json()["overlays"]
    assert items, "data/overlays should hold at least the Boulder Reservoir survey"
    it = items[0]
    (s, w), (n, e) = it["ortho"]["bounds"]
    assert s < n and w < e
    assert c.get(it["ortho"]["url"]).status_code == 200
    water = json.loads(c.get(it["water"]["url"]).data)
    assert water["features"] and water["features"][0]["geometry"]["type"] == "Polygon"


def test_boulder_survey_sits_on_the_reservoir_shore():
    it = next(i for i in planner.app.test_client().get("/api/overlays").get_json()["overlays"] if i["date"] == "2026-10-19")
    (s, w), (n, e) = it["ortho"]["bounds"]
    assert 40.07 < s < n < 40.10 and -105.24 < w < e < -105.21


def test_no_path_escape():
    c = planner.app.test_client()
    assert c.get("/overlays/..%2F..%2Fapp.py/x").status_code == 404
    assert c.get("/overlays/2026-10-19-boulder-reservoir-survey/..%2F..%2F..%2Fapp.py").status_code == 404


def test_plan_on_flight_water():
    """Picking the 10/19 segmented water tiles that polygon, not the whole mapped reservoir."""
    c = planner.app.test_client()
    it = next(i for i in c.get("/api/overlays").get_json()["overlays"] if i["date"] == "2026-10-19")
    ring = json.loads(c.get(it["water"]["url"]).data)["features"][0]["geometry"]["coordinates"][0]
    from shapely.geometry import Polygon
    poly = Polygon(ring)
    inside = poly.representative_point()
    base = {"mode": "survey", "click_lat": inside.y, "click_lon": inside.x, "duration_min": 20,
            "survey_style": "continuous", "max_wp": 2000}
    own = c.post("/api/plan", json={**base, "water_overlay": it["id"]})
    assert own.status_code == 200, own.get_json()
    js = own.get_json()
    # every hover/waypoint the planner puts out should sit near the flight's water, not across the reservoir
    pts = [(h["lon"], h["lat"]) for h in js.get("hovers", []) if "lon" in h]
    assert pts, js.keys()
    far = [p for p in pts if poly.buffer(0.002).disjoint(__import__("shapely.geometry", fromlist=["Point"]).Point(p))]
    assert not far
    assert c.post("/api/plan", json={**base, "water_overlay": "nope"}).status_code == 400


def test_preview_flies_through_the_lead_in_like_the_drone():
    """Body-yaw missions start with a lead-in waypoint. The Plan preview must fly pad -> lead-in -> survey,
    and that lead-in must be the file's first waypoint (WP1 in Mission Planner and Fly)."""
    import math
    from engine.export import parse_wpl
    c = planner.app.test_client()
    it = next(i for i in c.get("/api/overlays").get_json()["overlays"] if i["date"] == "2026-10-19")
    from shapely.geometry import shape
    ring = json.loads(c.get(it["water"]["url"]).data)["features"][0]["geometry"]
    p = shape(ring).representative_point()
    plan = c.post("/api/plan", json={"mode": "survey", "click_lat": p.y, "click_lon": p.x, "survey_style": "continuous",
                                     "max_wp": 2000, "water_overlay": it["id"], "roi_mode": "body_yaw"}).get_json()
    assert len(plan["route_line"]) == len(plan["hovers"]) + 3          # pad, lead-in, WPs, pad
    lead = plan["route_line"][1]
    items = parse_wpl(c.get(plan["exports"]["waypoints"]).data.decode())
    wp1 = next(i for i in items if i["command"] == 16 and i["seq"] > 0)
    assert abs(wp1["lat"] - lead[0]) < 1e-6 and abs(wp1["lon"] - lead[1]) < 1e-6
    lead_frames = [f for f in plan["frames"] if f["phase"] == "lead"]
    assert lead_frames, "preview never flies the lead-in leg"
    to1 = next(f for f in plan["frames"] if f["phase"] == "to_1")      # first sample of the run-in starts at the lead-in
    d = math.hypot((to1["drone"]["lat"] - lead[0]) * 111133, (to1["drone"]["lon"] - lead[1]) * 85000)
    assert d < 2.0, d
