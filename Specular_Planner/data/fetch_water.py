#!/usr/bin/env python3
"""Pull OSM lakes / reservoirs / riverbanks / rivers near the SURGE pad."""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

BBOX = (39.95, -105.40, 40.22, -105.05)  # s, w, n, e
QUERY = f"""
[out:json][timeout:90];
(
  way["natural"="water"]({BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]});
  way["landuse"="reservoir"]({BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]});
  way["landuse"="basin"]({BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]});
  way["waterway"="riverbank"]({BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]});
  way["waterway"="river"]({BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]});
  way["waterway"="canal"]({BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]});
  way["waterway"="stream"]({BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]});
  relation["natural"="water"]({BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]});
);
out geom;
"""


def kind_of(tags: dict) -> str:
    if tags.get("natural") == "water":
        return tags.get("water") or "lake"
    if tags.get("landuse") in ("reservoir", "basin"):
        return tags["landuse"]
    ww = tags.get("waterway")
    if ww:
        return ww
    return "water"


def width_m(tags: dict, kind: str) -> float:
    w = tags.get("width")
    if w:
        try:
            return max(4.0, float(str(w).split()[0]))
        except ValueError:
            pass
    return {"river": 18.0, "canal": 10.0, "stream": 6.0, "drain": 4.0}.get(kind, 12.0)


def way_feature(el: dict) -> dict | None:
    tags = el.get("tags") or {}
    pts = el.get("geometry") or []
    coords = [[p["lon"], p["lat"]] for p in pts]
    if len(coords) < 2:
        return None
    kind = kind_of(tags)
    name = tags.get("name") or kind
    closed = coords[0] == coords[-1]
    area_tags = (
        tags.get("natural") == "water"
        or tags.get("landuse") in ("reservoir", "basin")
        or tags.get("waterway") == "riverbank"
        or tags.get("area") == "yes"
    )
    props = {"name": name, "kind": kind, "osm_id": el.get("id"), "osm": el.get("type")}
    if area_tags:
        if not closed:
            coords = coords + [coords[0]]
        if len(coords) < 4:
            return None
        return {
            "type": "Feature",
            "properties": props,
            "geometry": {"type": "Polygon", "coordinates": [coords]},
        }
    props["width_m"] = width_m(tags, kind)
    return {
        "type": "Feature",
        "properties": props,
        "geometry": {"type": "LineString", "coordinates": coords},
    }


def relation_features(el: dict) -> list[dict]:
    tags = el.get("tags") or {}
    kind = kind_of(tags)
    name = tags.get("name") or kind
    outers, inners = [], []
    for mem in el.get("members") or []:
        if mem.get("type") != "way":
            continue
        pts = mem.get("geometry") or []
        coords = [[p["lon"], p["lat"]] for p in pts]
        if len(coords) < 4:
            continue
        if coords[0] != coords[-1]:
            coords.append(coords[0])
        role = mem.get("role") or "outer"
        (inners if role == "inner" else outers).append(coords)
    feats = []
    for i, outer in enumerate(outers):
        rings = [outer] + inners  # simple: attach all holes to each outer
        feats.append(
            {
                "type": "Feature",
                "properties": {
                    "name": name if i == 0 else f"{name} {i+1}",
                    "kind": kind,
                    "osm_id": el.get("id"),
                    "osm": "relation",
                },
                "geometry": {"type": "Polygon", "coordinates": rings},
            }
        )
    return feats


def main():
    req = urllib.request.Request(
        "https://overpass-api.de/api/interpreter",
        data=QUERY.encode(),
        headers={"User-Agent": "HERON-specular-planner/1.0", "Content-Type": "text/plain"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = json.loads(resp.read().decode())
    feats = []
    for el in raw.get("elements") or []:
        if el.get("type") == "way":
            f = way_feature(el)
            if f:
                feats.append(f)
        elif el.get("type") == "relation":
            feats.extend(relation_features(el))
    out = {
        "type": "FeatureCollection",
        "name": "OSM water near Boulder Reservoir",
        "features": feats,
    }
    dest = Path(__file__).resolve().parent / "waterbodies.geojson"
    dest.write_text(json.dumps(out))
    n_poly = sum(1 for f in feats if f["geometry"]["type"] == "Polygon")
    n_line = sum(1 for f in feats if f["geometry"]["type"] == "LineString")
    print(f"wrote {dest}  polygons={n_poly}  lines={n_line}  total={len(feats)}")


if __name__ == "__main__":
    main()
