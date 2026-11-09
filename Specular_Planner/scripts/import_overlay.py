#!/usr/bin/env python3
"""Add a previous flight's orthomosaic and segmented water to the planner's map.

    .venv/bin/python scripts/import_overlay.py ../../heron-ar/docs/twin \
        --origin 40.0859711,-105.2328258 --name "Boulder Reservoir survey" --date 2026-10-19

Reads the heron-ar twin export (ortho.jpg, scene.json, water.png, in local East/North metres
around the flight's home point) and writes data/overlays/<id>/:

    ortho.webp        the orthomosaic, cropped to where it has imagery, transparent elsewhere
    water.geojson     where the water actually was (heron-ar water segmentation), only where
                      the camera saw it
    meta.json         name, date, bounds

--origin is the first POS row of the flight's log (heron-ar uses it as the local frame origin);
pass --pos path/to/POS.csv instead to read it from there.
Needs numpy, opencv-python and pillow (pip install numpy opencv-python-headless pillow).
"""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
WGS_A, WGS_E2 = 6378137.0, 6.69437999014e-3
BACKGROUND = np.array([196, 191, 182], np.float32)    # heron-ar fills empty ortho cells with this


class Frame:
    """Same local frame heron-ar uses (geometry.LocalFrame.en_from_ll), inverted."""

    def __init__(self, lat0, lon0):
        self.lat0, self.lon0 = lat0, lon0
        s = math.sin(math.radians(lat0))
        self.rn = WGS_A * (1 - WGS_E2) / (1 - WGS_E2 * s * s) ** 1.5
        self.re = WGS_A / math.sqrt(1 - WGS_E2 * s * s) * math.cos(math.radians(lat0))

    def ll(self, e, n):
        return self.lat0 + math.degrees(n / self.rn), self.lon0 + math.degrees(e / self.re)


def coverage_alpha(rgb):
    """Opaque where the ortho has imagery: drop the flat background that touches the border."""
    near = (np.abs(rgb.astype(np.float32) - BACKGROUND).max(2) < 10).astype(np.uint8)
    n, lab = cv2.connectedComponents(near, connectivity=4)
    edge = set(np.unique(np.r_[lab[0], lab[-1], lab[:, 0], lab[:, -1]])) - {0}
    bg = np.isin(lab, list(edge)) & (near > 0)
    bg = cv2.morphologyEx(bg.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)) > 0
    alpha = np.where(bg, 0, 255).astype(np.uint8)
    return cv2.erode(alpha, np.ones((3, 3), np.uint8))   # trim the dark seam along the edge


def water_polygons(water_png, alpha, size, min_area_m2=200.0):
    """Polygons of segmented water, only where the camera actually saw (ortho has imagery).

    water_png: heron-ar water.png (row 0 north, 255 = water); alpha: ortho coverage mask on
    the same size x size metre window. Returns [(area_m2, [outer ring, hole, ...])] in pixels
    of the alpha grid, with ring points as (col, row).
    """
    n = alpha.shape[0]
    w = cv2.resize(water_png, (n, n), interpolation=cv2.INTER_LINEAR) > 127
    m = (w & (alpha > 0)).astype(np.uint8)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    m = cv2.morphologyEx(cv2.morphologyEx(m, cv2.MORPH_OPEN, k), cv2.MORPH_CLOSE, k)
    px = size / n
    contours, hier = cv2.findContours(m, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    out = []
    if hier is None:
        return out
    for i, c in enumerate(contours):
        if hier[0][i][3] != -1:
            continue                                   # holes are attached to their parent below
        area = cv2.contourArea(c) * px * px
        if area < min_area_m2:
            continue
        rings = [cv2.approxPolyDP(c, 1.0 / px, True)[:, 0, :]]
        j = hier[0][i][2]
        while j != -1:
            if cv2.contourArea(contours[j]) * px * px >= min_area_m2:
                rings.append(cv2.approxPolyDP(contours[j], 1.0 / px, True)[:, 0, :])
                area -= cv2.contourArea(contours[j]) * px * px
            j = hier[0][j][0]
        out.append((area, rings))
    return sorted(out, key=lambda x: -x[0])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("twin", type=Path, help="heron-ar twin export folder (ortho.jpg, scene.json)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--origin", help="lat,lon of the local frame origin (first POS row)")
    g.add_argument("--pos", type=Path, help="POS.csv of the flight; its first row is the origin")
    ap.add_argument("--name", required=True)
    ap.add_argument("--date", required=True, help="flight date, YYYY-MM-DD")
    ap.add_argument("--id", help="folder name (default: from date and name)")
    ap.add_argument("--max-px", type=int, default=2048, help="longest side of the saved image")
    a = ap.parse_args()

    if a.pos:
        row = a.pos.read_text().splitlines()[:2]
        cols = row[0].split(",")
        vals = row[1].split(",")
        lat0, lon0 = float(vals[cols.index("Lat")]), float(vals[cols.index("Lng")])
    else:
        lat0, lon0 = (float(v) for v in a.origin.split(","))
    F = Frame(lat0, lon0)
    scene = json.loads((a.twin / "scene.json").read_text())
    ox0, oy0 = scene["origin"]
    size = float(scene["size"])

    # ---- orthomosaic: row 0 is north (heron-ar flips it), columns run west -> east
    bgr = cv2.imread(str(a.twin / "ortho.jpg"), cv2.IMREAD_COLOR)
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    N = rgb.shape[0]
    px = size / N
    alpha = coverage_alpha(rgb)
    ys, xs = np.nonzero(alpha)
    r0, r1, c0, c1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    rgba = np.dstack([rgb, alpha])[r0:r1, c0:c1]
    west, east = ox0 + c0 * px, ox0 + c1 * px
    north, south = oy0 + size - r0 * px, oy0 + size - r1 * px
    scale = min(1.0, a.max_px / max(rgba.shape[:2]))
    if scale < 1:
        rgba = cv2.resize(rgba, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    s_lat, w_lon = F.ll(west, south)
    n_lat, e_lon = F.ll(east, north)

    # ---- segmented water (heron-ar water map), clipped to where the camera saw
    feats, water_m2 = [], 0.0
    wpng = a.twin / "water.png"
    if wpng.exists():
        for area, rings in water_polygons(cv2.imread(str(wpng), cv2.IMREAD_GRAYSCALE), alpha, size):
            water_m2 += area
            coords = []
            for r in rings:
                ll = [F.ll(ox0 + (cx + 0.5) * px, oy0 + size - (cy + 0.5) * px) for cx, cy in r]
                ring = [[round(lon, 7), round(lat, 7)] for lat, lon in ll]
                coords.append(ring + [ring[0]])
            feats.append({"type": "Feature", "properties": {"area_m2": round(area)},
                          "geometry": {"type": "Polygon", "coordinates": coords}})

    ident = a.id or (a.date + "-" + re.sub(r"[^a-z0-9]+", "-", a.name.lower()).strip("-"))
    out = ROOT / "data" / "overlays" / ident
    out.mkdir(parents=True, exist_ok=True)
    from PIL import Image
    Image.fromarray(rgba, "RGBA").save(out / "ortho.webp", "WEBP", quality=82, method=6)
    (out / "water.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    for old in ("area.geojson", "measured.geojson"):
        (out / old).unlink(missing_ok=True)
    meta = {
        "id": ident, "name": a.name, "date": a.date,
        "ortho": {"file": "ortho.webp", "bounds": [[s_lat, w_lon], [n_lat, e_lon]],
                  "gsd_m": round(px / scale, 2)},
        "water": {"file": "water.geojson", "m2": round(water_m2)},
        "origin": [lat0, lon0],
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    kb = (out / "ortho.webp").stat().st_size // 1024
    print(f"{out.relative_to(ROOT)}: ortho {rgba.shape[1]}x{rgba.shape[0]} px ({kb} KB, {px / scale:.2f} m/px), "
          f"water {water_m2 / 1e4:.1f} ha in {len(feats)} polygon(s)")


if __name__ == "__main__":
    main()
