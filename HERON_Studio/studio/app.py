"""HERON Studio web server (local only). Run with `python run.py`."""
import csv, io, json, shutil, subprocess, sys
from pathlib import Path
from flask import Flask, jsonify, request, send_file, abort, send_from_directory
from . import store, uploads, ingest, jobs, water, detect, sample
from .config import STATIC_DIR, FLIGHTS_DIR, STUDIO_HOME, PORT, CHUNK_BYTES, ENGINE_DIR

VERSION = "0.1.0"
app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = CHUNK_BYTES * 2


def err(msg, code=400): return jsonify(error=msg), code


@app.errorhandler(KeyError)
def _nf(e): return err(f"not found: {e}", 404)


@app.get("/")
def index(): return send_from_directory(STATIC_DIR, "index.html")


@app.get("/api/state")
def state():
    def has(mod):
        try: __import__(mod); return True
        except Exception: return False
    return jsonify(version=VERSION, home=str(STUDIO_HOME), chunk=CHUNK_BYTES, engine=(ENGINE_DIR / "heron.py").exists(),
                   ffmpeg=bool(shutil.which("ffmpeg")), colmap=has("pycolmap"), pymavlink=has("pymavlink"), rasterio=has("rasterio"),
                   running=[fid for fid, j in jobs._running.items()], formats=water.FORMATS, export_groups=water.GROUPS,
                   **_settings())


SETTINGS = STUDIO_HOME / "settings.json"


def _settings():
    try: return json.loads(SETTINGS.read_text())
    except Exception: return {}


@app.post("/api/settings")
def save_settings():
    j = request.get_json(force=True); cur = _settings()
    for k in ("google_maps_key",):          # only known keys; stored locally, never sent anywhere but Google's own loader
        if k in j: cur[k] = str(j[k])[:200]
    SETTINGS.write_text(json.dumps(cur)); return jsonify(cur)


# ------------------------------------------------------------------ flights
@app.get("/api/flights")
def flights(): return jsonify([_card(f) for f in store.all_flights()])


def _card(f):
    r = f.get("results") or {}; w = r.get("water") or {}
    return dict(id=f["id"], name=f["name"], site=f.get("site"), date=f.get("date"), status=f["status"], sample=f.get("sample", False),
                video=(f.get("video") or {}).get("resolution_label"), duration=(f.get("video") or {}).get("duration_s"),
                thumb=f"/api/flights/{f['id']}/media/thumb.jpg" if (FLIGHTS_DIR / f["id"] / "thumb.jpg").exists() else None,
                water_area_m2=w.get("water_area_m2"), job=(f.get("job") or {}).get("state"), progress=(f.get("job") or {}).get("progress"))


@app.post("/api/flights")
def new_flight():
    j = request.get_json(force=True); f = store.create(j.get("name") or "Untitled flight", j.get("site", ""), j.get("date"))
    return jsonify(f)


@app.get("/api/flights/<fid>")
def get_flight(fid): return jsonify(store.load(fid))


@app.patch("/api/flights/<fid>")
def patch_flight(fid):
    j = request.get_json(force=True)
    def upd(f):
        for k in ("name", "site", "date"):
            if k in j: f[k] = j[k]
        if "settings" in j:
            s = f["settings"]
            for k, v in j["settings"].items():
                if k == "make": s["make"].update(v)
                else: s[k] = v
        ingest.refresh(f)
    return jsonify(store.update(fid, upd))


@app.delete("/api/flights/<fid>")
def delete_flight(fid):
    if fid in jobs._running: return err("cancel processing first")
    store.delete(fid); return jsonify(ok=True)


# ------------------------------------------------------------------ files
@app.post("/api/uploads")
def up_start():
    j = request.get_json(force=True); store.fdir(j["flight"])
    return jsonify(uploads.start(j["flight"], j["name"], j["size"]))


@app.get("/api/uploads/<uid>")
def up_status(uid): return jsonify(uploads.status(uid))


@app.put("/api/uploads/<uid>")
def up_chunk(uid):
    off = int(request.args.get("offset", 0)); n = request.content_length or 0
    return jsonify(uploads.write_chunk(uid, off, request.stream, n))


@app.post("/api/uploads/<uid>/finish")
def up_finish(uid):
    m = uploads.status(uid); _, meta = uploads._meta(uid)
    p = uploads.finish(uid, FLIGHTS_DIR / meta["flight"] / "inputs")
    e, f = ingest.add_file(meta["flight"], p)
    return jsonify(file=e, flight=f)


@app.delete("/api/uploads/<uid>")
def up_cancel(uid): uploads.cancel(uid); return jsonify(ok=True)


@app.post("/api/flights/<fid>/link")
def link(fid):
    """Desktop app: use a file where it is (SD card, Movies folder) instead of copying gigabytes."""
    j = request.get_json(force=True); p = Path(j["path"]).expanduser()
    if not p.exists(): return err(f"not found: {p}")
    if j.get("copy") and p.is_file():
        dst = FLIGHTS_DIR / fid / "inputs" / uploads.safe_name(p.name); shutil.copy2(p, dst); e, f = ingest.add_file(fid, dst)
    elif j.get("copy") and p.is_dir():
        dst = FLIGHTS_DIR / fid / "inputs" / uploads.safe_name(p.name); shutil.copytree(p, dst, dirs_exist_ok=True); e, f = ingest.add_file(fid, dst)
    else:
        e, f = ingest.add_file(fid, p, linked=True)
    return jsonify(file=e, flight=f)


@app.delete("/api/flights/<fid>/files/<path:name>")
def rm_file(fid, name): return jsonify(ingest.remove_file(fid, name))


@app.get("/api/lenses")
def lenses():
    q = (request.args.get("q") or "").lower(); a = request.args.get("aspect")
    out = [p for p in detect.lens_index() if (not a or p["aspect"] == a) and all(t in p["name"].lower() for t in q.split())]
    return jsonify(out[:200])


@app.post("/api/flights/<fid>/lens")
def set_lens(fid):
    j = request.get_json(force=True); p = next((x for x in detect.lens_index() if x["id"] == j["id"]), None)
    if not p: return err("unknown lens")
    def upd(f):
        v = f.get("video") or {}
        notes = [] if p["aspect"] == v.get("aspect") else [f"Profile is {p['aspect']}, video is {v.get('aspect')}: the overlay will be wrong."]
        if p["calib_w"] != v.get("width"): notes.append(f"Calibrated at {p['calib_w']}×{p['calib_h']}; scaled to {v.get('width')}×{v.get('height')}.")
        f["lens"] = dict(p, notes=notes, chosen_by_user=True); f["lens_locked"] = True
    return jsonify(store.update(fid, upd))


# ------------------------------------------------------------------ processing
@app.post("/api/flights/<fid>/process")
def process(fid):
    f = store.load(fid); j = request.get_json(silent=True) or {}
    if f.get("sample"): return err("the sample flight is already processed")
    if not f.get("ready"): return err("missing files: " + ", ".join(k for k, v in (f.get("checklist") or {}).items() if not v and k != "mission"))
    try: jobs.start(fid, j.get("start_at"), j.get("only"))
    except RuntimeError as e: return err(str(e), 409)
    return jsonify(ok=True)


@app.post("/api/flights/<fid>/cancel")
def cancel(fid): jobs.cancel(fid); return jsonify(ok=True)


@app.get("/api/flights/<fid>/job")
def job(fid):
    f = store.load(fid); return jsonify(job=f.get("job"), status=f["status"], log=jobs.log_tail(fid, int(request.args.get("n", 60))))


# ------------------------------------------------------------------ media + data
MEDIA = {"thumb.jpg": "thumb.jpg", "proxy.mp4": "proxy.mp4", "ar.mp4": "out/HERON_AR.mp4", "ortho.png": "ortho.png", "ortho.webp": "ortho.webp"}


@app.get("/api/flights/<fid>/media/<name>")
def media(fid, name):
    d = store.fdir(fid)
    if name == "video.mp4":           # best playable copy of the source
        f = store.load(fid); v = f.get("video") or {}
        if (d / "proxy.mp4").exists(): return send_file(d / "proxy.mp4", conditional=True)
        if (d / "work" / "recording.mp4").exists(): return send_file(d / "work" / "recording.mp4", conditional=True)
        first = (v.get("parts") or [{}])[0].get("name", v.get("name"))
        e = next((x for x in f["files"] if x["kind"] == "video" and x["name"] == first), None)
        if e: return send_file(store.file_path(f, e), conditional=True)
        abort(404)
    if name == "still.jpg":
        p = next(iter(sorted((d / "out").glob("preview_*.jpg"))), None) or (d / "still.jpg")
        if p.exists(): return send_file(p)
        abort(404)
    rel = MEDIA.get(name)
    if not rel or not (d / rel).exists(): abort(404)
    return send_file(d / rel, conditional=True)


@app.get("/api/flights/<fid>/geo/<what>")
def geo(fid, what):
    d = store.fdir(fid)
    if what == "outline": p = d / "data" / "outline.geojson"
    elif what == "water": p = d / "exports" / "water.geojson"
    elif what == "track": return jsonify(_track(d, store.load(fid)))
    elif what == "ortho":
        p = d / "ortho.json"
        if not p.exists(): abort(404)
        return send_file(p, mimetype="application/json")
    elif what == "level":
        try: return jsonify(water.shift_level(d, float(request.args.get("delta", 0)), store.load(fid)))
        except FileNotFoundError: return err("no 3D model for this flight", 404)
    elif what == "reflections": return jsonify(_refl(d, int(request.args.get("max", 4000))))
    else: abort(404)
    if not p.exists(): abort(404)
    return send_file(p, mimetype="application/geo+json")


def _track(d, f=None):
    p = d / "data" / "POS.csv"; cache = d / "track.json"
    if p.exists() and p.stat().st_size > 50_000:
        with open(p) as fh: rows = list(csv.DictReader(fh))
        step = max(1, len(rows) // 1500)
        coords = [[float(r["Lng"]), float(r["Lat"]), float(r["Alt"])] for r in rows[::step]]
    elif cache.exists():
        coords = json.loads(cache.read_text())
    else:
        lg = next((x for x in (f or {}).get("files", []) if x["kind"] == "log" and not x.get("error")), None) if f else None
        if not lg: return dict(type="FeatureCollection", features=[])
        from pymavlink import DFReader
        log = DFReader.DFReader_binary(str(store.file_path(f, lg)), zero_time_base=False); coords = []; n = 0
        while True:
            m = log.recv_match(type=["POS"])
            if m is None: break
            n += 1
            if n % 5 == 0: coords.append([m.Lng, m.Lat, m.Alt])
        step = max(1, len(coords) // 1500); coords = coords[::step]; cache.write_text(json.dumps(coords))
    return dict(type="FeatureCollection", features=[dict(type="Feature", properties=dict(kind="track"), geometry=dict(type="LineString", coordinates=coords))])


def _refl(d, mx):
    p = d / "out" / "reflections.csv"
    if not p.exists(): p = d / "exports" / "reflections.csv"
    if not p.exists(): return []
    with open(p) as f: rows = list(csv.DictReader(f))
    times = sorted({r["video_s"] for r in rows}, key=float); k = max(1, -(-len(rows) // mx))   # thin by time, keep every satellite
    keep = set(times[::k])
    return [dict(t=float(r["video_s"]), lat=float(r["spec_lat"]), lon=float(r["spec_lon"]), sat=r["sat"], el=float(r["el_deg"]),
                 o=r["water_by_outline"] == "True", m=r["water_measured"] == "True") for r in rows if r["video_s"] in keep]


@app.get("/api/flights/<fid>/model3d")
def model3d(fid):
    """Downsampled height grid for the 3D view (metres above the flight-day water)."""
    import numpy as np
    d = store.fdir(fid); p = d / "out" / "dem.npz"
    if not p.exists(): return err("no 3D model yet", 404)
    z = np.load(p); Z = (z["Z"].astype(np.float32) - float(z["water_z"])); c = float(z["cell"])
    fr = water.origin(d); x0, y0 = float(z["x0"]), float(z["y0"])
    # keep the ground within ~300 m of the flown track (far-range points are sparse and noisy), crop to it
    trk = _track(d, store.load(fid))["features"]
    if trk:
        from scipy.ndimage import distance_transform_edt
        cc = np.array(trk[0]["geometry"]["coordinates"]); e, n = fr.en(cc[:, 0], cc[:, 1])
        seed = np.ones(Z.shape, bool); j = ((e - x0) / c).astype(int); i = ((n - y0) / c).astype(int)
        ok = (i >= 0) & (i < Z.shape[0]) & (j >= 0) & (j < Z.shape[1]); seed[i[ok], j[ok]] = False
        if ok.any():
            Z = np.where(distance_transform_edt(seed) * c <= float(request.args.get("radius", 300)), Z, np.nan)
            ii, jj = np.where(np.isfinite(Z))
            if len(ii):
                Z = Z[ii.min():ii.max() + 1, jj.min():jj.max() + 1]; x0 += jj.min() * c; y0 += ii.min() * c
    mx = int(request.args.get("max", 700)); k = max(1, int(np.ceil(max(Z.shape) / mx)))
    H, W = Z.shape[0] // k * k, Z.shape[1] // k * k
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        Zs = np.nanmedian(Z[:H, :W].reshape(H // k, k, W // k, k).transpose(0, 2, 1, 3).reshape(H // k, W // k, -1), axis=2)
    # drop spikes (trees, stray points): clamp to the 0.5-99 % range, then a 3x3 median that ignores holes
    ok = np.isfinite(Zs)
    if ok.sum() > 100:
        lo_, hi_ = np.percentile(Zs[ok], [0.5, 99.0]); Zs = np.where(ok, np.clip(Zs, lo_, hi_), np.nan)
        # hole-aware Gaussian (normalised convolution): smooths sparse-solve speckle, fills 1-2 cell gaps
        from scipy.ndimage import gaussian_filter
        fill = ok.mean() if ok.any() else 1
        sig = 1.6 if fill < 0.6 else 0.8
        wgt = gaussian_filter(ok.astype(np.float32), sig); num = gaussian_filter(np.where(ok, Zs, 0).astype(np.float32), sig)
        Zs = np.where(wgt > 0.35, num / np.maximum(wgt, 1e-6), np.nan)
    cs = c * k
    lo, la = fr.ll(np.array([x0, x0 + Zs.shape[1] * cs]), np.array([y0, y0 + Zs.shape[0] * cs]))
    q = np.where(np.isfinite(Zs), np.clip(np.round(Zs * 100), -32000, 32000), -32768).astype("<i2")
    import base64
    track = trk
    tr = []
    if track:
        cc = np.array(track[0]["geometry"]["coordinates"]); e, n = fr.en(cc[:, 0], cc[:, 1])
        wel = (json.loads((d / "config.json").read_text()).get("water_elevation_m") if (d / "config.json").exists() else None) or float(np.median(cc[:, 2]) - 40)
        tr = np.c_[e - x0, n - y0, cc[:, 2] - wel - float(z["water_z"])].round(2).tolist()
    return jsonify(w=Zs.shape[1], h=Zs.shape[0], cell=cs, heights_cm_b64=base64.b64encode(q.tobytes()).decode(),
                   bounds=dict(west=float(lo[0]), east=float(lo[1]), south=float(la[0]), north=float(la[1])),
                   origin_en=[x0, y0], track_en=tr, ortho=(d / "ortho.png").exists(),
                   ortho_box=json.loads((d / "ortho.json").read_text()) if (d / "ortho.json").exists() else None)


# ------------------------------------------------------------------ exports
@app.get("/api/flights/<fid>/exports")
def exports(fid):
    d = store.fdir(fid) / "exports"
    items = [dict(name=p.name, size=p.stat().st_size, label=water.FORMATS.get(p.name, ("Site package" if p.suffix == ".heronsite" else "Archive", ""))[0],
                  kind=water.FORMATS.get(p.name, ("", "Specular Planner" if p.suffix == ".heronsite" else "ZIP"))[1])
             for p in sorted(d.iterdir()) if p.is_file()] if d.exists() else []
    return jsonify(items)


@app.post("/api/flights/<fid>/exports/build")
def build_exports(fid):
    d = store.fdir(fid); f = store.load(fid)
    try:
        s = water.export(d, f); pkg = water.site_package(d, f)
    except Exception as e: return err(str(e))
    store.update(fid, lambda g: g["results"].update(water=s, site_package=pkg.name))
    return jsonify(s)


@app.get("/api/flights/<fid>/exports/<name>")
def download(fid, name):
    d = store.fdir(fid) / "exports"; f = store.load(fid)
    if name == "all.zip": p = water.zip_all(store.fdir(fid), f)
    elif name == "level.geojson":
        g = water.shift_level(store.fdir(fid), float(request.args.get("delta", 0)), f)
        return send_file(io.BytesIO(json.dumps(g).encode()), mimetype="application/geo+json", as_attachment=True,
                         download_name=f"water_level_{float(request.args.get('delta', 0)):+.2f}m.geojson")
    else: p = d / Path(name).name
    if not p.exists(): abort(404)
    return send_file(p, as_attachment=True, download_name=p.name)


def main():
    jobs.recover(); sample.install()
    port = PORT
    if "--port" in sys.argv: port = int(sys.argv[sys.argv.index("--port") + 1])
    print(f"HERON Studio {VERSION} on http://127.0.0.1:{port}  ·  flights in {STUDIO_HOME}", flush=True)
    app.run(host="127.0.0.1", port=port, threaded=True, debug=False)
