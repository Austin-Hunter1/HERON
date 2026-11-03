"""Register files on a flight and turn them into engine inputs."""
import json, shutil, subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from . import detect, gpmf, dflog, store
from .config import FLIGHTS_DIR, ENGINE_DIR, LENS_DIR

SENSOR_FILES = {"GYRO.csv", "ACCL.csv"}
LOG_FILES = {"POS.csv", "ATT.csv", "GPS.csv", "IMU.csv", "XKF1.csv", "RFND.csv", "PARM.csv"}


def add_file(fid, path, linked=False, original_name=None):
    """Classify + quick-probe a file and attach it to the flight. Returns the file entry."""
    path = Path(path); kind = detect.classify(path) if path.is_file() else ("folder" if path.is_dir() else "missing")
    if kind == "csv" and path.name in SENSOR_FILES | LOG_FILES: kind = "csv:" + path.stem
    if kind == "folder":
        names = {p.name for p in path.iterdir()}
        kind = "sensor_folder" if SENSOR_FILES <= names else "log_folder" if {"POS.csv", "ATT.csv", "GPS.csv", "IMU.csv"} <= names else "folder"
    e = dict(name=original_name or path.name, kind=kind, linked=linked, path=str(path) if linked else None,
             size=path.stat().st_size if path.is_file() else None, added=store.now(), info={})
    try:
        if kind == "video": e["info"] = detect.probe_video(path)
        elif kind == "log": e["info"] = quick_log(path)
        elif kind == "mission": e["info"] = read_mission(path)
        elif kind == "outline": e["info"] = dict(features=len(load_outline(path)))
    except Exception as ex:
        e["error"] = f"{type(ex).__name__}: {ex}"
    def upd(f):
        f["files"] = [x for x in f["files"] if not (x["name"] == e["name"] and x["kind"] == e["kind"])] + [e]
        refresh(f)
    return e, store.update(fid, upd)


def remove_file(fid, name):
    def upd(f):
        for x in [x for x in f["files"] if x["name"] == name]:
            if not x.get("linked"): (FLIGHTS_DIR / fid / "inputs" / name).unlink(missing_ok=True)
        f["files"] = [x for x in f["files"] if x["name"] != name]; refresh(f)
    return store.update(fid, upd)


def refresh(f):
    """Recompute what the flight has / still needs from its files."""
    files = f["files"]; vids = [x for x in files if x["kind"] == "video" and not x.get("error")]
    parts = recording_parts(vids, f.get("settings", {}))
    main = parts[0] if parts else None
    if main:
        tot = sum(x["info"].get("video_duration_s") or x["info"].get("duration_s", 0) for x in parts)
        f["video"] = dict(main["info"], name=main["name"] if len(parts) == 1 else f"{main['name']} + {len(parts) - 1} more",
                          duration_s=round(tot, 3), browser_playable=main["info"].get("browser_playable") and len(parts) == 1,
                          parts=[dict(name=x["name"], duration_s=x["info"].get("video_duration_s") or x["info"].get("duration_s", 0),
                                      telemetry_stream=x["info"].get("telemetry_stream"), chapter=(x["info"].get("chapter") or {}).get("n"))
                                 for x in parts],
                          telemetry_stream=main["info"].get("telemetry_stream") if all(x["info"].get("telemetry_stream") is not None for x in parts) else None)
        others = [x["name"] for x in vids if x not in parts]
        f["video_unused"] = others
    else:
        f["video"] = None; f["video_unused"] = []
    if main:
        f["sizes"] = detect.processing_sizes(main["info"])
        if not f.get("lens_locked"):
            best, alts, notes = detect.match_lens(main["info"], f.get("telemetry_info"))
            f["lens"] = dict(best, notes=notes) if best else dict(id=None, notes=notes)
            f["lens_options"] = [dict(id=a["id"], name=a["name"]) for a in alts]
    logs = [x for x in files if x["kind"] in ("log", "log_folder") and not x.get("error")]
    if logs and logs[0]["kind"] == "log":
        i = logs[0]["info"]
        if not f.get("date") and i.get("utc_start"): f["date"] = i["utc_start"][:10]
        if i.get("lat") is not None: f["home"] = dict(lat=i["lat"], lon=i["lon"], alt=i.get("alt"))
    kinds = {x["kind"] for x in files}
    has_tel = bool(main and main["info"].get("telemetry_stream") is not None) or "sensor_folder" in kinds or {"csv:GYRO", "csv:ACCL"} <= kinds
    has_outline = any(x["kind"] == "outline" for x in files) or _near_boulder(f)
    f["checklist"] = dict(video=bool(main), log=bool(logs), camera_gyro=has_tel,
                          mission=any(x["kind"] == "mission" for x in files), outline=has_outline)
    f["ready"] = bool(main and logs and has_tel and has_outline)
    if f["status"] in ("new", "ready"): f["status"] = "ready" if f["ready"] else "new"


def recording_parts(vids, settings):
    """Which videos make up the recording, in order. GoPro chapters (GX01xxxx, GX02xxxx...) of the same
    recording are joined automatically unless settings.join_chapters is False; settings.video_parts
    can list names to join in a given order. Otherwise: the longest single video."""
    if not vids: return []
    by = {x["name"]: x for x in vids}
    manual = [by[n] for n in (settings.get("video_parts") or []) if n in by]
    if len(manual) >= 1 and all(detect.same_stream(manual[0]["info"], x["info"]) for x in manual): return manual
    if settings.get("join_chapters", True):
        groups = {}
        for x in vids:
            ck = x["info"].get("chapter")
            if ck: groups.setdefault(ck["recording"], []).append(x)
        best = []
        for g in groups.values():
            g = sorted(g, key=lambda x: x["info"]["chapter"]["n"])
            # keep the run of consecutive chapters that share stream settings with the first
            run = [g[0]]
            for x in g[1:]:
                if detect.same_stream(run[0]["info"], x["info"]): run.append(x)
            if sum(x["info"].get("duration_s", 0) for x in run) > sum(x["info"].get("duration_s", 0) for x in best): best = run
        if best and len(best) > 1: return best
    return [max(vids, key=lambda x: x["info"].get("duration_s", 0))]


def _near_boulder(f):
    h = f.get("home") or {}
    return h.get("lat") is not None and abs(h["lat"] - 40.078) < 0.05 and abs(h["lon"] + 105.215) < 0.06


# ------------------------------------------------------------------ quick readers
def quick_log(path):
    from pymavlink import DFReader
    log = DFReader.DFReader_binary(str(path), zero_time_base=False)
    g = log.recv_match(type=["GPS"], condition="GPS.Status>=3")
    out = dict(format="ArduPilot DataFlash")
    if g is not None:
        utc = datetime(1980, 1, 6, tzinfo=timezone.utc) + timedelta(weeks=g.GWk, milliseconds=g.GMS) - timedelta(seconds=18)
        out.update(utc_start=utc.isoformat(timespec="seconds"), lat=g.Lat, lon=g.Lng, alt=g.Alt)
    return out


def read_mission(path):
    lines = Path(path).read_text(errors="replace").splitlines()
    if lines and lines[0].startswith("QGC WPL"):
        wps = [l.split("\t") for l in lines[1:] if l.strip()]
        pts = [(float(w[8]), float(w[9])) for w in wps if len(w) > 9 and w[3] in ("16", "82") and float(w[8]) != 0]
        return dict(format="QGC WPL", items=len(wps), waypoints=len(pts), bbox=_bbox(pts))
    j = json.loads(Path(path).read_text()); items = j.get("mission", {}).get("items", [])
    return dict(format="QGC plan", items=len(items))


def _bbox(pts):
    if not pts: return None
    la, lo = zip(*pts); return [min(lo), min(la), max(lo), max(la)]


def load_outline(path):
    """GeoJSON or KML -> list of polygons [[(lon,lat),...], ...] (outer rings)."""
    path = Path(path)
    if path.suffix.lower() == ".kml":
        import re
        rings = re.findall(r"<outerBoundaryIs>.*?<coordinates>(.*?)</coordinates>", path.read_text(), re.S)
        return [[tuple(map(float, c.split(",")[:2])) for c in r.split()] for r in rings]
    j = json.loads(path.read_text()); polys = []
    feats = j["features"] if j.get("type") == "FeatureCollection" else [j]
    for ft in feats:
        g = ft.get("geometry", ft)
        if g["type"] == "Polygon": polys.append([tuple(c[:2]) for c in g["coordinates"][0]])
        elif g["type"] == "MultiPolygon": polys += [[tuple(c[:2]) for c in p[0]] for p in g["coordinates"]]
    return polys


# ------------------------------------------------------------------ prepare engine inputs
def prepare(f, log=print):
    """Build flight/data/ for the engine. Returns engine config dict (written to config.json)."""
    d = FLIGHTS_DIR / f["id"]; data = d / "data"; data.mkdir(exist_ok=True); (data / "solved").mkdir(exist_ok=True)
    files = f["files"]; v = f["video"]
    part_entries = [next(x for x in files if x["kind"] == "video" and x["name"] == p["name"]) for p in v["parts"]]
    part_paths = [store.file_path(f, e) for e in part_entries]
    vpath = stitch(d, part_paths, log) if len(part_paths) > 1 else part_paths[0]
    # flight log
    lg = next(x for x in files if x["kind"] in ("log", "log_folder"))
    t0 = None
    if lg["kind"] == "log":
        log("Converting flight log to CSV…")
        r = dflog.convert(store.file_path(f, lg), data, progress=lambda p: log(f"  log {p}%"))
        if r["missing"]: raise RuntimeError(f"flight log is missing {', '.join(r['missing'])} messages")
        t0 = r["t0_us"]; log(f"  {sum(r['counts'].values()):,} records, mission zero from {r['t0_source']}")
    else:
        src = store.file_path(f, lg)
        for n in LOG_FILES:
            if (src / n).exists(): shutil.copy2(src / n, data / n)
        t0 = f.get("settings", {}).get("t0_us")
    # camera gyro / accelerometer
    sens = next((x for x in files if x["kind"] == "sensor_folder"), None)
    gdir = data / "gopro"
    loose = {x["kind"]: x for x in files if x["kind"] in ("csv:GYRO", "csv:ACCL")}
    if len(loose) == 2 and not sens:
        gdir.mkdir(exist_ok=True)
        for k, x in loose.items(): shutil.copy2(store.file_path(f, x), gdir / (k[4:] + ".csv"))
        log("Camera gyro + accelerometer CSVs copied")
    elif sens:
        gdir.mkdir(exist_ok=True)
        for n in SENSOR_FILES: shutil.copy2(store.file_path(f, sens) / n, gdir / n)
        log("Camera gyro + accelerometer copied from folder")
    else:
        log("Extracting GoPro gyro + accelerometer (GPMF)…" + (f" from {len(part_paths)} chapters" if len(part_paths) > 1 else ""))
        offs, t = [], 0.0
        for p in v["parts"]: offs.append(t); t += p["duration_s"]
        s = gpmf.extract_parts([(pp, p["telemetry_stream"], o) for pp, p, o in zip(part_paths, v["parts"], offs)], gdir)
        f["telemetry_info"] = s; log(f"  gyro {s.get('GYRO', {}).get('rate_hz')} Hz, camera {s.get('model') or s.get('device')}")
    # lens
    lens = f.get("lens") or {}
    if not lens.get("id"): raise RuntimeError("no lens profile matches this video; choose one on the Import screen")
    ldst = data / ("lens_" + Path(lens["id"]).name); ldst.write_text(detect.read_lens(lens["id"]))
    # almanac, outline
    for n in ("current_yuma.alm",): shutil.copy2(ENGINE_DIR / "data" / n, data / n)
    if (data / "gnss").exists(): shutil.rmtree(data / "gnss")
    shutil.copytree(ENGINE_DIR / "data" / "gnss", data / "gnss")
    ol = next((x for x in files if x["kind"] == "outline"), None)
    polys = load_outline(store.file_path(f, ol)) if ol else load_outline(ENGINE_DIR / "data" / "boulder_reservoir.geojson")
    big = max(polys, key=len)
    (data / "outline.geojson").write_text(json.dumps(dict(type="FeatureCollection", features=[dict(
        type="Feature", properties={}, geometry=dict(type="Polygon", coordinates=[[list(c) for c in big]]))])))
    # config
    st = f["settings"]; base = json.loads((ENGINE_DIR / "configs" / "base.json").read_text()); base.pop("_comment", None)
    import pandas as pd
    P = pd.read_csv(data / "POS.csv", nrows=50)
    welev = st.get("water_elevation_m") or round(float(P.Alt.iloc[0]) - 1.0, 2)
    rsz = f["sizes"]["render_options"].get(st.get("render_size", "1080p"), f["sizes"]["render_options"]["1080p"])
    cfg = {**base, "clip_path": str(vpath), "clip_video_start_s": float(st.get("clip_start_in_recording_s") or 0.0),
           "clip_duration_s": round(v["duration_s"], 3), "fps": v["fps"], "aspect": v["width"] / v["height"],
           "output_name": "HERON_AR.mp4", "output_width": rsz["width"], "output_height": rsz["height"],
           "lens_profile": ldst.name, "focal_scale": float(st.get("focal_scale") or 1.0), "water_elevation_m": welev,
           "water_offset_m": float(st.get("water_offset_m") or 0.0), "gopro_dir": "gopro", "outline_geojson": "outline.geojson",
           "sfm_poses": "solved/sfm_poses.npz", "sync_search": st.get("sync_search", [-900, 900]),
           "sfm": {**base["sfm"], "frame_step": 1 if st.get("solve") == "full" else 12, "work_width": f["sizes"]["solve"]["width"]}}
    if st.get("sync"): cfg["sync"] = st["sync"]
    (d / "config.json").write_text(json.dumps(cfg, indent=2))
    env = dict(t0_us=t0)
    (d / "engine_env.json").write_text(json.dumps(env))
    return cfg


def stitch(d, paths, log=print):
    """Join GoPro chapters into one video without re-encoding (frame-exact; chapters are made to be
    joined). Video stream only: telemetry is read from each chapter separately. Re-used if present."""
    out = d / "work" / "recording.mp4"; out.parent.mkdir(exist_ok=True)
    sig = json.dumps([[str(p), Path(p).stat().st_size] for p in paths])
    mark = out.with_suffix(".json")
    if out.exists() and mark.exists() and mark.read_text() == sig:
        log("Chapters already joined"); return out
    lst = d / "work" / "chapters.txt"
    lst.write_text("".join("file '" + str(p).replace("'", "'\\''") + "'\n" for p in paths))
    gb = sum(Path(p).stat().st_size for p in paths) / 1e9
    log(f"Joining {len(paths)} chapters into one recording ({gb:.1f} GB, no re-encode)…")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-map", "0:v:0", "-c", "copy",
                    "-movflags", "+faststart", str(out)], check=True)
    mark.write_text(sig); return out


def proxy(f, log=print):
    """H.264 copy for in-app playback (HEVC / 4K / 5.3K sources don't play everywhere) + thumbnail."""
    d = FLIGHTS_DIR / f["id"]; v = f["video"]
    if len(v.get("parts") or []) > 1: src = d / "work" / "recording.mp4"
    else: src = store.file_path(f, next(x for x in f["files"] if x["kind"] == "video" and x["name"] == v["parts"][0]["name"]))
    sz = f["sizes"]["proxy"]
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(min(5, v["duration_s"] / 2)), "-i", str(src), "-frames:v", "1",
                    "-vf", f"scale={min(960, sz['width'])}:-2", str(d / "thumb.jpg")], check=True)
    if v.get("browser_playable"): return
    log(f"Making a {sz['width']}×{sz['height']} preview copy for playback…")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-an", "-vf", f"scale={sz['width']}:{sz['height']}",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                    str(d / "proxy.mp4")], check=True)
