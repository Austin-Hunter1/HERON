#!/usr/bin/env python3
"""Cache Esri satellite tiles around Boulder Reservoir so the planner map loads offline.

Zoom 12–16 covers the default map view. Zoom 17–18 covers the reservoir, the
north-shore pad, and the over-water mission. Zoom 19 is the tighter mission box.
Re-run any time; tiles already on disk are skipped.
"""

from __future__ import annotations

import math
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# south, west, north, east
# Wide enough that the default zoom-14 view is imagery, plus a short pan.
CONTEXT = (40.012, -105.318, 40.152, -105.138)
# Reservoir, SURGE pad on the north shore, and the east-water hover.
MISSION = (40.056, -105.254, 40.101, -105.192)
# Sharper imagery where waypoints are actually placed.
DETAIL = (40.062, -105.246, 40.094, -105.201)

ZONES = [(z, CONTEXT) for z in range(12, 17)] + [(17, MISSION), (18, MISSION), (19, DETAIL)]

URL = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
UA = "HERON-Specular-Planner/1.0 (Boulder Reservoir offline map cache)"
OUT = Path(__file__).resolve().parent / "map_tiles"
WORKERS = 12


def tile_range(lat: float, lon: float, z: int) -> tuple[int, int]:
    n = 2**z
    x = int((lon + 180.0) / 360.0 * n)
    lat_r = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n)
    return x, y


def tiles_for(box: tuple[float, float, float, float], z: int) -> list[tuple[int, int, int]]:
    south, west, north, east = box
    x0, y_south = tile_range(south, west, z)
    x1, y_north = tile_range(north, east, z)
    xs = range(min(x0, x1), max(x0, x1) + 1)
    ys = range(min(y_south, y_north), max(y_south, y_north) + 1)
    return [(z, x, y) for x in xs for y in ys]


def fetch(z: int, x: int, y: int) -> str:
    dest = OUT / str(z) / str(y) / f"{x}.jpg"
    if dest.is_file() and dest.stat().st_size > 800:
        return "skip"
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(URL.format(z=z, y=y, x=x), headers={"User-Agent": UA})
    last = "fail"
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=40) as resp:
                data = resp.read()
            if not data.startswith(b"\xff\xd8"):
                return "bad"
            tmp = dest.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.replace(dest)
            return "ok"
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return "missing"
            last = f"http {exc.code}"
        except Exception as exc:
            last = str(exc)
        time.sleep(0.35 * (attempt + 1))
    print(f"failed z{z}/{y}/{x}: {last}", flush=True)
    return "fail"


def main() -> None:
    wanted = [tile for z, box in ZONES for tile in tiles_for(box, z)]
    print(f"Boulder Reservoir imagery: {len(wanted)} tiles → {OUT}", flush=True)
    counts = {"ok": 0, "skip": 0, "fail": 0, "missing": 0, "bad": 0}
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(fetch, z, x, y) for z, x, y in wanted]
        for fut in as_completed(futures):
            counts[fut.result()] += 1
            done += 1
            if done % 200 == 0 or done == len(wanted):
                mb = sum(p.stat().st_size for p in OUT.rglob("*.jpg")) / 1e6 if OUT.exists() else 0
                print(
                    f"{done}/{len(wanted)}  new {counts['ok']}  had {counts['skip']}  "
                    f"fail {counts['fail']}  {mb:.0f} MB",
                    flush=True,
                )
    mb = sum(p.stat().st_size for p in OUT.rglob("*.jpg")) / 1e6 if OUT.exists() else 0
    print(f"Cached {counts['ok'] + counts['skip']} tiles, {mb:.0f} MB.", flush=True)
    if counts["fail"]:
        raise SystemExit(f"{counts['fail']} tiles failed; run this again to retry.")


if __name__ == "__main__":
    main()
