"""Resumable chunked uploads (GoPro files are often 4 GB, too big for one request).

    POST /api/uploads            {flight, name, size}          -> {id, received}
    PUT  /api/uploads/<id>?offset=N   raw bytes                -> {received}
    GET  /api/uploads/<id>                                     -> {received, size}   (resume point)
    POST /api/uploads/<id>/finish                              -> file entry

The partial file lives in STUDIO_HOME/.uploads/<id>.part with a small JSON beside it, so an
interrupted upload (closed laptop, app restart) carries on from the last chunk.
"""
import json, os, re, uuid
from pathlib import Path
from .config import UPLOAD_TMP

SAFE = re.compile(r"[^A-Za-z0-9._ ()+-]")


def safe_name(name):
    name = Path(name.replace("\\", "/")).name
    name = SAFE.sub("_", name).strip(". ") or "file"
    return name[:180]


def _meta(uid):
    if not re.fullmatch(r"[a-f0-9]{32}", uid): raise KeyError(uid)
    p = UPLOAD_TMP / f"{uid}.json"
    if not p.exists(): raise KeyError(uid)
    return p, json.loads(p.read_text())


def start(flight_id, name, size):
    # resume an unfinished upload of the same file to the same flight
    for p in UPLOAD_TMP.glob("*.json"):
        try: m = json.loads(p.read_text())
        except Exception: continue
        if m["flight"] == flight_id and m["name"] == safe_name(name) and m["size"] == int(size):
            return dict(id=m["id"], received=_received(m["id"]), chunk=None)
    uid = uuid.uuid4().hex
    m = dict(id=uid, flight=flight_id, name=safe_name(name), size=int(size))
    (UPLOAD_TMP / f"{uid}.json").write_text(json.dumps(m)); (UPLOAD_TMP / f"{uid}.part").touch()
    return dict(id=uid, received=0)


def _received(uid): return (UPLOAD_TMP / f"{uid}.part").stat().st_size


def status(uid):
    _, m = _meta(uid); return dict(received=_received(uid), size=m["size"], name=m["name"])


def write_chunk(uid, offset, stream, length):
    _, m = _meta(uid); part = UPLOAD_TMP / f"{uid}.part"; have = part.stat().st_size
    if offset != have: return dict(received=have, error="offset mismatch")     # client re-sends from 'received'
    if have + length > m["size"]: return dict(received=have, error="too much data")
    with open(part, "ab") as f:
        left = length
        while left > 0:
            b = stream.read(min(1 << 20, left))
            if not b: break
            f.write(b); left -= len(b)
    return dict(received=part.stat().st_size)


def finish(uid, dest_dir):
    p, m = _meta(uid); part = UPLOAD_TMP / f"{uid}.part"
    if part.stat().st_size != m["size"]: raise ValueError(f"incomplete: {part.stat().st_size}/{m['size']} bytes")
    dest_dir = Path(dest_dir); dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / m["name"]; i = 1
    while dest.exists(): dest = dest_dir / f"{Path(m['name']).stem} ({i}){Path(m['name']).suffix}"; i += 1
    os.replace(part, dest); p.unlink()
    return dest


def cancel(uid):
    p, _ = _meta(uid); (UPLOAD_TMP / f"{uid}.part").unlink(missing_ok=True); p.unlink(missing_ok=True)
