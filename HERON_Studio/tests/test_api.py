"""End-to-end API checks against a running Studio (python run.py --no-browser).

    python tests/test_api.py <video> <log.bin> [outline.geojson]

Uploads in 8 MB chunks, interrupts one upload half way and resumes it, then checks detection,
exports and the model endpoints on the sample flight.
"""
import json, os, sys, urllib.request
BASE = os.environ.get("STUDIO", "http://127.0.0.1:5056")


def call(method, path, body=None, raw=None, headers=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    h = {"Content-Type": "application/json"} if raw is None else {"Content-Type": "application/octet-stream"}
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={**h, **(headers or {})})
    with urllib.request.urlopen(req) as r:
        b = r.read(); return json.loads(b) if r.headers.get_content_type() == "application/json" else b


def upload(fid, path, stop_after=None):
    size = os.path.getsize(path); u = call("POST", "/api/uploads", dict(flight=fid, name=os.path.basename(path), size=size))
    off = u["received"]; chunk = 8 * 1024 * 1024; n = 0
    with open(path, "rb") as f:
        f.seek(off)
        while off < size:
            b = f.read(chunk); r = call("PUT", f"/api/uploads/{u['id']}?offset={off}", raw=b); off = r["received"]; n += 1
            if stop_after and n >= stop_after: return None
    return call("POST", f"/api/uploads/{u['id']}/finish")


def main():
    video, log = sys.argv[1], sys.argv[2]; outline = sys.argv[3] if len(sys.argv) > 3 else None
    f = call("POST", "/api/flights", dict(name="API test", site="Test site"))
    fid = f["id"]; print("flight", fid)
    big = os.path.getsize(video) > 16 * 1024 * 1024
    if big:
        assert upload(fid, video, stop_after=1) is None; print("  interrupted after 1 chunk")
    r = upload(fid, video); print("  video:", r["file"]["kind"], {k: r["file"]["info"].get(k) for k in ("width", "height", "fps", "resolution_label", "aspect", "telemetry_stream")})
    r = upload(fid, log); print("  log:", r["file"]["kind"], r["file"]["info"])
    if outline: r = upload(fid, outline); print("  outline:", r["file"]["kind"], r["file"]["info"])
    f = r["flight"]; print("  checklist", f["checklist"], "ready", f["ready"]); print("  lens", f["lens"]["name"] if f.get("lens") else None, f["lens"].get("notes"))
    print("  sizes", f["sizes"]["solve"], f["sizes"]["render_options"])
    s = "boulder-reservoir-2026-10-19"
    ex = call("GET", f"/api/flights/{s}/exports"); print("sample exports:", [e["name"] for e in ex])
    m = call("GET", f"/api/flights/{s}/model3d?max=400"); print("model3d", m["w"], m["h"], round(m["cell"], 2), len(m["track_en"]))
    lv = call("GET", f"/api/flights/{s}/geo/level?delta=1.1"); print("level +1.1 m polygons", len(lv["features"]), sum(x["properties"]["area_m2"] for x in lv["features"]))
    rf = call("GET", f"/api/flights/{s}/geo/reflections?max=500"); print("reflections", len(rf), sum(r["o"] for r in rf), sum(r["m"] for r in rf))
    z = call("GET", f"/api/flights/{s}/exports/all.zip"); print("all.zip", len(z), "bytes")
    call("DELETE", f"/api/flights/{fid}"); print("deleted test flight")


if __name__ == "__main__":
    main()
