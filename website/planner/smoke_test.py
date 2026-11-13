"""Smoke test of web_backend.py under CPython, before it runs in a browser.

Run it with a Python that has the planner's requirements (flask, shapely,
sgp4), for example the planner's own venv:

    <planner venv python> website/planner/smoke_test.py <ROOT>

ROOT is a folder that holds Specular_Planner/ and HERON_Payload/.

It drives the same requests the page sends: plan a mission, connect the
demo aircraft in the Simulate tab, upload, launch, then fast-forward the
virtual clock and check that the aircraft flies and the payload records.
It patches threading the same way as in the browser, so a pass here
means the patches work with the current planner code.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import web_backend as wb  # noqa: E402


def call(method, url, body=None, headers=None):
    """One request through the backend; return (status, parsed JSON or bytes)."""
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"} if body is not None else {}
    h.update(headers or {})
    status, _, raw = wb.handle(method, url, h, data)
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw


def _posix_paths() -> None:
    """On Windows, read NUC paths as POSIX paths, as Pyodide (and the NUC) do."""
    import os
    import pathlib

    if os.name == "nt":
        import heron_payload.config as cfg

        cfg.Path = pathlib.PurePosixPath


def main() -> None:
    """Run the scenario and print what happens; exit 1 on a failed step."""
    root = sys.argv[1]
    print("source:", wb.boot(root, before_import=_posix_paths))
    s, d = call("GET", "/api/defaults")
    assert s == 200, (s, d)
    print("defaults ok; water bodies:", d["n_water"], "target:", d["target"])
    # Two clicked waypoints over the default target water, as the Plan page sends them.
    wps = [{"lat": 40.0820, "lon": -105.2275}, {"lat": 40.0828, "lon": -105.2260}]
    body = {
        "start": d["start"],
        "duration_min": d["duration_min"],
        "pad": d["pad"],
        "hover": d["hover"],
        "mode": "click",
        "h_agl": 60,
        "waypoints": wps,
    }
    s, plan = call("POST", "/api/plan", body)
    assert s == 200, (s, plan)
    plan_id = plan.get("flight_plan_id")
    print("plan ok:", plan_id, "keys:", sorted(plan)[:12])
    s, tok = call("GET", "/api/sim/session")
    hdr = {"X-Flight-Token": tok["token"]}
    s, r = call("POST", "/api/sim/connect", {"transport": "demo"}, hdr)
    assert s == 200, (s, r)
    s, r = call("POST", "/api/sim/upload", {"plan_id": plan_id}, hdr)
    print("upload:", s, r)
    s, st = call("GET", f"/api/sim/status?plan_id={plan_id}")
    print("job after upload:", st["job"])
    failed = [c["key"] for c in st["preflight"]["checks"] if not c["ok"] and c["enabled"]]
    print("failed launch checks:", failed)
    s, r = call(
        "POST",
        "/api/sim/launch",
        {"plan_id": plan_id, "confirmation": "LAUNCH", "wait_until_start": True},
        hdr,
    )
    s, st = call("GET", f"/api/sim/status?plan_id={plan_id}")
    print("job after launch:", st["job"])
    assert st["job"]["state"] == "complete", st["job"]
    for _ in range(12):
        wb._offset[0] += 20  # fast-forward 20 s
        wb.tick()
        s, st = call("GET", f"/api/sim/status?plan_id={plan_id}")
        t = st["telemetry"]
        s, pay = call("GET", f"/api/payload/status?mode=sim&plan_id={plan_id}")
        print(
            f"alt {t['relative_alt']:6.1f} m  wp {t['mission_current']:3}  mode {t['mode']:6}"
            f"  armed {t['armed']!s:5}  payload {pay.get('state')}"
        )
    print("smoke test passed")


if __name__ == "__main__":
    main()
