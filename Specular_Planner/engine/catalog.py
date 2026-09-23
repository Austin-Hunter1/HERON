"""Almanac files on disk: where they came from, check remote, refresh."""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import ssl
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
GNSS = DATA / "gnss"
STAMP = DATA / ".almanac_sync"
DAY_S = 24 * 3600
UA = "Mozilla/5.0 (HERON specular planner; GNSS almanac fetch)"
CTX = ssl.create_default_context()

# NEED TO UPDATE SOURCES BASED ON AUSTIN's REFERENCE: CDDIS
SOURCES = [
    {
        "id": "gps-yuma",
        "name": "GPS YUMA",
        "agency": "U.S. Coast Guard NAVCEN",
        "used": "GPS L5",
        "planning": True,
        "kind": "text",
        "page": "https://www.navcen.uscg.gov/gps-nanu-almanacs",
        "url": "https://www.navcen.uscg.gov/sites/default/files/gps/almanac/current_yuma.alm",
        "rel": "current_yuma.alm",
    },
    {
        "id": "galileo-xml",
        "name": "Galileo almanac",
        "agency": "European GNSS Service Centre",
        "used": "Galileo E5a",
        "planning": True,
        "kind": "xml",
        "page": "https://www.gsc-europa.eu/gsc-products/almanac",
        "rel": "gnss/galileo_*.xml",
        "glob": "galileo_*.xml",
    },
    {
        "id": "beidou-tle",
        "name": "BeiDou TLE",
        "agency": "Celestrak",
        "used": "BeiDou-3 B2a",
        "planning": True,
        "kind": "tle",
        "page": "https://celestrak.org/NORAD/elements/table.php?GROUP=beidou",
        "url": "https://celestrak.org/NORAD/elements/gp.php?GROUP=beidou&FORMAT=tle",
        "rel": "gnss/celestrak_beidou.tle",
    },
    {
        "id": "qzss-tle",
        "name": "QZSS TLE",
        "agency": "Celestrak",
        "used": "QZSS L5 (on disk; Boulder sees it on the horizon)",
        "kind": "tle",
        "page": "https://celestrak.org/NORAD/elements/table.php?NAME=QZS",
        "url": "https://celestrak.org/NORAD/elements/gp.php?NAME=QZS&FORMAT=tle",
        "rel": "gnss/celestrak_qzss.tle",
    },
    {
        "id": "celestrak-gps",
        "name": "GPS TLE",
        "agency": "Celestrak",
        "used": None,
        "kind": "tle",
        "page": "https://celestrak.org/NORAD/elements/table.php?GROUP=gps-ops",
        "url": "https://celestrak.org/NORAD/elements/gp.php?GROUP=gps-ops&FORMAT=tle",
        "rel": "gnss/celestrak_gps.tle",
    },
    {
        "id": "celestrak-galileo",
        "name": "Galileo TLE",
        "agency": "Celestrak",
        "used": None,
        "kind": "tle",
        "page": "https://celestrak.org/NORAD/elements/table.php?GROUP=galileo",
        "url": "https://celestrak.org/NORAD/elements/gp.php?GROUP=galileo&FORMAT=tle",
        "rel": "gnss/celestrak_galileo.tle",
    },
    {
        "id": "celestrak-gnss",
        "name": "Mixed GNSS TLE",
        "agency": "Celestrak",
        "used": None,
        "kind": "tle",
        "page": "https://celestrak.org/NORAD/elements/table.php?GROUP=gnss",
        "url": "https://celestrak.org/NORAD/elements/gp.php?GROUP=gnss&FORMAT=tle",
        "rel": "gnss/celestrak_gnss.tle",
    },
    {
        "id": "bkg-rinex",
        "name": "IGS mixed nav",
        "agency": "BKG / IGS",
        "used": None,
        "kind": "binary",
        "page": "https://igs.bkg.bund.de/root_ftp/IGS/BRDC/",
        "rel": "gnss/BRDC*.rnx.gz",
        "glob": "BRDC*.rnx.gz",
    },
]


def by_id(sid: str) -> dict:
    for src in SOURCES:
        if src["id"] == sid:
            return src
    raise KeyError(sid)


def planning_ids() -> list[str]:
    return [s["id"] for s in SOURCES if s.get("planning")]


def get_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, context=CTX, timeout=90) as r:
        return r.read()


def _latest(folder: Path, pattern: str) -> Path | None:
    if not folder.exists():
        return None
    hits = sorted(folder.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return hits[0] if hits else None


def local_file(src: dict) -> Path | None:
    glob = src.get("glob")
    if glob:
        return _latest(GNSS, glob)
    return DATA / src["rel"]


def _safe_under_data(path: Path) -> Path:
    resolved = path.resolve()
    if DATA.resolve() not in resolved.parents and resolved != DATA.resolve():
        raise ValueError("path outside data/")
    return resolved


def galileo_remote() -> tuple[str, str]:
    html = get_bytes("https://www.gsc-europa.eu/gsc-products/almanac").decode("utf-8", "replace")
    hrefs = re.findall(
        r'href="(/sites/default/files/sites/all/files/\d{4}-\d{2}-\d{2}\.xml)"',
        html,
    )
    if not hrefs:
        raise RuntimeError("no Galileo XML link on the GSC page")
    rel = hrefs[0]
    stamp = Path(rel).stem
    return "https://www.gsc-europa.eu" + rel, f"galileo_{stamp}.xml"


def bkg_remote() -> tuple[str, str]:
    now = dt.datetime.now(dt.timezone.utc)
    doy = f"{now.timetuple().tm_yday:03d}"
    year = now.year
    name = f"BRDC00WRD_S_{year}{doy}0000_01D_MN.rnx.gz"
    url = f"https://igs.bkg.bund.de/root_ftp/IGS/BRDC/{year}/{doy}/{name}"
    return url, name


def remote_for(src: dict) -> tuple[str, str]:
    if src["id"] == "galileo-xml":
        return galileo_remote()
    if src["id"] == "bkg-rinex":
        return bkg_remote()
    url = src["url"]
    name = Path(src["rel"]).name
    return url, name


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_info(path: Path | None) -> dict:
    if path is None or not path.exists():
        return {
            "file": None,
            "bytes": 0,
            "mtime": None,
            "age_hours": None,
            "sha256": None,
        }
    st = path.stat()
    age = (dt.datetime.now().timestamp() - st.st_mtime) / 3600.0
    return {
        "file": path.name,
        "bytes": st.st_size,
        "mtime": dt.datetime.fromtimestamp(st.st_mtime, dt.timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        ),
        "age_hours": round(age, 1),
        "sha256": None,
    }


def last_sync_age_s() -> float:
    if not STAMP.exists():
        return DAY_S * 99
    return time.time() - STAMP.stat().st_mtime


def due_daily() -> bool:
    for src in SOURCES:
        if not src.get("planning"):
            continue
        path = local_file(src)
        if path is None or not path.exists():
            return True
    return last_sync_age_s() >= DAY_S


def describe(sats=None) -> list[dict]:
    counts: dict[str, int] = {}
    if sats:
        for s in sats:
            key = Path(s.source).name if s.source else ""
            if key:
                counts[key] = counts.get(key, 0) + 1
    rows = []
    for src in SOURCES:
        if not src.get("planning"):
            continue
        path = local_file(src)
        info = _file_info(path)
        n = counts.get(path.name, 0) if path else 0
        rows.append(
            {
                **{k: src[k] for k in ("id", "name", "agency", "used", "kind", "page")},
                **info,
                "sats": n,
                "missing": path is None or not path.exists(),
            }
        )
    return rows


def _remote_copy(sid: str) -> tuple[dict, bytes | None, str | None]:
    src = by_id(sid)
    local = local_file(src)
    try:
        url, remote_name = remote_for(src)
        data = get_bytes(url)
    except Exception as e:
        return (
            {"id": sid, "ok": False, "state": "error", "message": str(e)},
            None,
            None,
        )
    meta = {"id": sid, "ok": True, "remote_file": remote_name, "remote_bytes": len(data), "url": url}
    if local is None or not local.exists():
        meta.update(state="missing", message="not on disk")
        return meta, data, remote_name
    if _sha(local.read_bytes()) == _sha(data):
        meta.update(state="current", message="already current", file=local.name, bytes=local.stat().st_size)
        return meta, data, remote_name
    why = "newer remote file" if local.name != remote_name else "remote content differs"
    meta.update(state="stale", message=f"{why} ({remote_name})")
    return meta, data, remote_name


def check_source(sid: str) -> dict:
    meta, _data, _name = _remote_copy(sid)
    return meta


def _write_source(src: dict, name: str, data: bytes) -> Path:
    dest = GNSS / name if src.get("glob") else DATA / src["rel"]
    dest = _safe_under_data(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest


def sync_source(sid: str) -> dict:
    """Fetch remote. Write only if missing or the bytes differ."""
    src = by_id(sid)
    meta, data, name = _remote_copy(sid)
    if meta.get("state") in ("current", "error") or data is None or name is None:
        return meta
    dest = _write_source(src, name, data)
    return {
        "id": sid,
        "ok": True,
        "state": "updated",
        "message": f"pulled {dest.name} ({len(data)} B)",
        "file": dest.name,
        "bytes": len(data),
        "url": meta.get("url"),
    }


def refresh_source(sid: str) -> dict:
    return sync_source(sid)


def sync_planning() -> dict:
    results = []
    ok = True
    for sid in planning_ids():
        try:
            row = sync_source(sid)
        except Exception as e:
            row = {"id": sid, "ok": False, "state": "error", "message": str(e)}
        results.append({k: v for k, v in row.items() if not k.startswith("_")})
        if not row.get("ok"):
            ok = False
    if ok:
        STAMP.parent.mkdir(parents=True, exist_ok=True)
        STAMP.write_text(dt.datetime.now(dt.timezone.utc).isoformat(), encoding="utf-8")
    n_up = sum(1 for r in results if r.get("state") == "updated")
    n_cur = sum(1 for r in results if r.get("state") == "current")
    return {
        "ok": ok,
        "updated": n_up,
        "current": n_cur,
        "results": results,
    }


def refresh_all() -> list[dict]:
    return sync_planning()["results"]


def preview(sid: str, limit: int = 120_000) -> dict:
    src = by_id(sid)
    path = local_file(src)
    if path is None or not path.exists():
        raise FileNotFoundError(sid)
    path = _safe_under_data(path)
    raw = path.read_bytes()
    binary = src["kind"] == "binary" or b"\x00" in raw[:800]
    if binary:
        return {
            "id": sid,
            "file": path.name,
            "binary": True,
            "bytes": len(raw),
            "text": f"{path.name} is binary ({len(raw)} bytes). Download it instead of viewing.",
        }
    text = raw.decode("utf-8", "replace")
    cut = text[:limit]
    return {
        "id": sid,
        "file": path.name,
        "binary": False,
        "bytes": len(raw),
        "truncated": len(text) > limit,
        "text": cut,
    }


def download_path(sid: str) -> Path:
    path = local_file(by_id(sid))
    if path is None or not path.exists():
        raise FileNotFoundError(sid)
    return _safe_under_data(path)
