"""Flights on disk.

flights/<id>/
    flight.json        everything known about the flight (files, probe, lens, settings, results)
    inputs/            uploaded files (linked files stay where they are; flight.json has the path)
    data/              engine inputs: log CSVs, gopro/GYRO.csv+ACCL.csv, lens, almanac, outline, solved/
    config.json        engine config written by the pipeline
    work/ cache/ out/  engine working folders
    exports/           water data, DEM, site package
    proxy.mp4 thumb.jpg
"""
import json, re, shutil, threading, time, uuid
from datetime import datetime, timezone
from pathlib import Path
from .config import FLIGHTS_DIR

_lock = threading.RLock()


def now(): return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slug(s): return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "flight"


def fdir(fid):
    d = FLIGHTS_DIR / fid
    if not re.fullmatch(r"[a-z0-9-]+", fid) or not d.is_dir(): raise KeyError(fid)
    return d


def create(name, site="", date=None):
    fid = f"{slug(name)}-{uuid.uuid4().hex[:6]}"
    d = FLIGHTS_DIR / fid
    for sub in ("inputs", "data", "work", "cache", "out", "exports"): (d / sub).mkdir(parents=True, exist_ok=True)
    f = dict(id=fid, name=name, site=site, date=date, created=now(), updated=now(), files=[], status="new",
             settings=dict(solve="full", render_size="1080p", make=dict(ar_video=True, water=True, model3d=True, report=True),
                           clip_start_in_recording_s=0.0, water_elevation_m=None),
             video=None, lens=None, job=None, results={})
    save(f); return f


def load(fid):
    with _lock: return json.loads((fdir(fid) / "flight.json").read_text())


def save(f):
    with _lock:
        f["updated"] = now(); p = FLIGHTS_DIR / f["id"] / "flight.json"; tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(f, indent=2, default=_json)); tmp.replace(p)


def update(fid, fn):
    with _lock:
        f = load(fid); fn(f); save(f); return f


def all_flights():
    out = []
    for p in sorted(FLIGHTS_DIR.glob("*/flight.json")):
        try: out.append(json.loads(p.read_text()))
        except Exception: pass
    return sorted(out, key=lambda f: f.get("date") or f.get("created") or "", reverse=True)


def delete(fid):
    with _lock: shutil.rmtree(fdir(fid))


def file_path(f, entry):
    """Absolute path of a file entry (uploaded copies live in inputs/, linked files elsewhere)."""
    return Path(entry["path"]) if entry.get("linked") else FLIGHTS_DIR / f["id"] / "inputs" / entry["name"]


def _json(o):
    try:
        import numpy as np
        if isinstance(o, np.generic): return o.item()
        if isinstance(o, np.ndarray): return o.tolist()
    except Exception: pass
    if isinstance(o, Path): return str(o)
    raise TypeError(type(o))
