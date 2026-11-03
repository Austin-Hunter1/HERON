"""Water data exports and the site package the Specular Planner reads.

Inputs (per flight):  cache/watermap_*.npz  (water probability on a local East/North grid)
                      out/dem.npz           (height grid, optional)
                      out/reflections.csv   (specular points, optional)
                      data/POS.csv          (first fix = local frame origin, as the engine uses)
                      data/outline.geojson  (map outline the plan used)

Rasters are written in a transverse Mercator projection centred on that origin, which is the
engine's East/North frame to well under a centimetre over a few km, so the GeoTIFFs drop
straight into QGIS / ArcGIS. Vectors are WGS84 lon/lat.
"""
import csv, json, math, zipfile
from datetime import datetime, timezone
from pathlib import Path
import numpy as np

WGS_E2 = 6.69437999014e-3
FORMATS = {
    "water.geojson": ("Water polygons", "GeoJSON · WGS84"),
    "shoreline.geojson": ("Shoreline lines", "GeoJSON · WGS84"),
    "water.kml": ("Water polygons", "KML · Google Earth"),
    "water_mask.tif": ("Water mask raster", "GeoTIFF · 1 water, 0 land, 255 not seen"),
    "water_probability.tif": ("Water probability", "GeoTIFF · float 0–1"),
    "dem.tif": ("Height model", "GeoTIFF · metres above water"),
    "reflections.csv": ("Reflection points", "CSV · every satellite every 0.5 s"),
    "summary.json": ("Lake summary", "JSON"),
    "water.kmz": ("Google Earth / My Maps", "KMZ · water, outline, track, reflections, photo map"),
    "map.html": ("Interactive web map", "Leaflet · one file, opens in any browser"),
    "water_shapefiles.zip": ("Shapefiles", "SHP · water, shoreline, track, reflections"),
    "track_shoreline.gpx": ("Handheld GPS", "GPX · flown track + shoreline"),
    "shoreline_points.csv": ("Shoreline points", "CSV · lat, lon (Google My Maps, Excel)"),
}
GROUPS = [("Google Earth / Google Maps", ["water.kmz", "water.kml", "shoreline_points.csv"]),
          ("Web map", ["map.html", "water.geojson", "shoreline.geojson"]),
          ("GIS (QGIS, ArcGIS)", ["water_shapefiles.zip", "water_mask.tif", "water_probability.tif", "dem.tif"]),
          ("Field + data", ["track_shoreline.gpx", "reflections.csv", "summary.json"])]


class Frame:
    def __init__(self, lat0, lon0):
        self.lat0, self.lon0 = lat0, lon0; la = math.radians(lat0)
        self.rn = 6378137 * (1 - WGS_E2) / (1 - WGS_E2 * math.sin(la) ** 2) ** 1.5
        self.re = 6378137 / math.sqrt(1 - WGS_E2 * math.sin(la) ** 2) * math.cos(la)

    def ll(self, e, n): return self.lon0 + np.degrees(np.asarray(e) / self.re), self.lat0 + np.degrees(np.asarray(n) / self.rn)
    def en(self, lon, lat): return np.radians(np.asarray(lon) - self.lon0) * self.re, np.radians(np.asarray(lat) - self.lat0) * self.rn
    def crs(self):
        from rasterio.crs import CRS
        return CRS.from_proj4(f"+proj=tmerc +lat_0={self.lat0:.9f} +lon_0={self.lon0:.9f} +k=1 +x_0=0 +y_0=0 +ellps=WGS84 +units=m +no_defs")


def origin(flight_dir):
    with open(Path(flight_dir) / "data" / "POS.csv") as f:
        r = next(csv.DictReader(f)); return Frame(float(r["Lat"]), float(r["Lng"]))


def load_watermap(flight_dir):
    p = next(iter(sorted((Path(flight_dir) / "cache").glob("watermap_*.npz"))), None)
    if p is None: return None
    z = np.load(p)
    prob = z["prob"].astype(np.float32) / (255.0 if z["prob"].dtype == np.uint8 else 1.0)    # sample ships uint8
    return dict(x0=float(z["x0"]), y0=float(z["y0"]), cell=float(z["cell"]), prob=prob,
                seen=z["seen"] if "seen" in z.files else None)


def polygons(mask, x0, y0, cell, frame, simplify_m=0.35, min_area_m2=50.0):
    """Mask (rows = north-up from y0) -> list of shapely polygons in lon/lat + areas in m²."""
    from rasterio import features
    from rasterio.transform import Affine
    from shapely.geometry import shape, Polygon
    from shapely.ops import transform as stransform
    tr = Affine(cell, 0, x0, 0, cell, y0)          # row i -> y0 + i*cell (south to north)
    out = []
    for geom, val in features.shapes(mask.astype(np.uint8), mask=mask.astype(bool), transform=tr, connectivity=8):
        p = shape(geom).simplify(simplify_m, preserve_topology=True)
        if p.area < min_area_m2: continue
        area = p.area
        p = stransform(lambda e, n, z=None: frame.ll(e, n), p)
        out.append((p, area))
    return sorted(out, key=lambda x: -x[1])


def export(flight_dir, meta):
    """Write every water product to exports/. Returns the summary dict."""
    import rasterio
    from rasterio.transform import Affine
    from shapely.geometry import mapping
    d = Path(flight_dir); ex = d / "exports"; ex.mkdir(exist_ok=True)
    fr = origin(d); wm = load_watermap(d)
    if wm is None: raise RuntimeError("no water map yet; run processing first")
    prob, cell, x0, y0 = wm["prob"], wm["cell"], wm["x0"], wm["y0"]
    water = prob > 0.5
    # crop to where there is information
    info = water | (wm["seen"] if wm["seen"] is not None else water)
    ii, jj = np.where(info)
    pad = int(20 / cell)
    i0, i1 = max(0, ii.min() - pad), min(prob.shape[0], ii.max() + pad + 1); j0, j1 = max(0, jj.min() - pad), min(prob.shape[1], jj.max() + pad + 1)
    prob, water = prob[i0:i1, j0:j1], water[i0:i1, j0:j1]
    seen = wm["seen"][i0:i1, j0:j1] if wm["seen"] is not None else None
    x0c, y0c = x0 + j0 * cell, y0 + i0 * cell
    stamp = dict(flight=meta.get("name"), site=meta.get("site"), date=meta.get("date"), made=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 source="HERON Studio · camera water segmentation voted onto the ground", cell_m=cell)
    # --- vectors
    polys = polygons(water, x0c, y0c, cell, fr)
    feats = [dict(type="Feature", properties=dict(area_m2=round(a, 1), **stamp), geometry=mapping(p)) for p, a in polys]
    (ex / "water.geojson").write_text(json.dumps(dict(type="FeatureCollection", features=feats)))
    lines = []
    for p, _ in polys:
        lines.append(dict(type="Feature", properties=dict(kind="shoreline"), geometry=mapping(p.exterior)))
        lines += [dict(type="Feature", properties=dict(kind="island"), geometry=mapping(r)) for r in p.interiors]
    (ex / "shoreline.geojson").write_text(json.dumps(dict(type="FeatureCollection", features=lines)))
    (ex / "water.kml").write_text(kml(polys, stamp))
    # --- rasters (north-up: flip rows)
    tr = Affine(cell, 0, x0c, 0, -cell, y0c + prob.shape[0] * cell); crs = fr.crs()
    m8 = water.astype(np.uint8)
    if seen is not None: m8[~seen & ~water] = 255
    prof = dict(driver="GTiff", height=prob.shape[0], width=prob.shape[1], count=1, crs=crs, transform=tr, compress="deflate")
    with rasterio.open(ex / "water_mask.tif", "w", dtype="uint8", nodata=255, **prof) as r: r.write(m8[::-1], 1); r.update_tags(**{k: str(v) for k, v in stamp.items()})
    with rasterio.open(ex / "water_probability.tif", "w", dtype="float32", **prof) as r: r.write(prob[::-1], 1)
    summary = dict(stamp, water_area_m2=round(float(water.sum()) * cell * cell, 1), polygons=len(polys),
                   origin=dict(lat=fr.lat0, lon=fr.lon0), crs=crs.to_proj4())
    # --- height model
    demp = d / "out" / "dem.npz"
    if demp.exists():
        z = np.load(demp); Z = z["Z"].astype(np.float32); c2 = float(z["cell"]); wz = float(z["water_z"])
        H = Z - wz
        tr2 = Affine(c2, 0, float(z["x0"]), 0, -c2, float(z["y0"]) + Z.shape[0] * c2)
        with rasterio.open(ex / "dem.tif", "w", driver="GTiff", height=Z.shape[0], width=Z.shape[1], count=1, dtype="float32",
                           crs=crs, transform=tr2, nodata=np.nan, compress="deflate", predictor=3) as r:
            r.write(H[::-1], 1); r.update_tags(units="metres above the water surface on the flight day")
        summary.update(level_from_dem(d, z, fr))
    # --- reflections
    rp = d / "out" / "reflections.csv"
    if rp.exists():
        import shutil; shutil.copy2(rp, ex / "reflections.csv")
        rs = json.loads((d / "out" / "reflections_summary.json").read_text()) if (d / "out" / "reflections_summary.json").exists() else None
        if rs: summary["reflections"] = rs
    # map-ready formats built from the same vectors
    for fn in (kmz, html_map, shapefiles, gpx, shoreline_csv):
        try: fn(d, meta)
        except Exception as e: print(f"export {fn.__name__} failed: {e}")
    summary["files"] = sorted(p.name for p in ex.iterdir() if p.name in FORMATS)
    (ex / "summary.json").write_text(json.dumps(summary, indent=1))
    return summary


def level_from_dem(d, z, fr):
    """How far below the map outline the water edge sits, from heights along the outline."""
    try:
        o = json.loads((Path(d) / "data" / "outline.geojson").read_text())["features"][0]["geometry"]["coordinates"][0]
        lon, lat = np.array(o).T; e, n = fr.en(lon, lat)
        L = np.cumsum(np.r_[0, np.hypot(np.diff(e), np.diff(n))]); s = np.arange(0, L[-1], 1.0)
        e, n = np.interp(s, L, e), np.interp(s, L, n)
        Z = z["Z"]; c = float(z["cell"]); j = ((e - float(z["x0"])) / c).astype(int); i = ((n - float(z["y0"])) / c).astype(int)
        ok = (i >= 0) & (i < Z.shape[0]) & (j >= 0) & (j < Z.shape[1]); h = Z[i[ok], j[ok]] - float(z["water_z"]); h = h[np.isfinite(h)]
        if len(h) < 50: return {}
        q = np.percentile(h, [25, 50, 75])
        return dict(outline_above_water_m=dict(median=round(float(q[1]), 2), p25=round(float(q[0]), 2), p75=round(float(q[2]), 2), samples=int(len(h))))
    except Exception:
        return {}


def kml(polys, stamp):
    def ring(r): return " ".join(f"{x:.8f},{y:.8f},0" for x, y in r.coords)
    pm = []
    for k, (p, a) in enumerate(polys):
        inner = "".join(f"<innerBoundaryIs><LinearRing><coordinates>{ring(r)}</coordinates></LinearRing></innerBoundaryIs>" for r in p.interiors)
        pm.append(f"<Placemark><name>Water {k + 1}</name><description>{a:,.0f} m²</description><styleUrl>#w</styleUrl>"
                  f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{ring(p.exterior)}</coordinates></LinearRing></outerBoundaryIs>{inner}</Polygon></Placemark>")
    return ('<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
            f"<name>HERON water · {stamp.get('site') or ''} {stamp.get('date') or ''}</name>"
            '<Style id="w"><LineStyle><color>ffffd64f</color><width>2</width></LineStyle><PolyStyle><color>5affd64f</color></PolyStyle></Style>'
            + "".join(pm) + "</Document></kml>")


def site_package(flight_dir, meta):
    """One file for the Specular Planner: measured water + heights + where the camera looked."""
    d = Path(flight_dir); ex = d / "exports"; s = json.loads((ex / "summary.json").read_text())
    site = dict(format="heron-site", version=1, site=meta.get("site"), flight=meta.get("name"), date=meta.get("date"),
                made=s["made"], origin=s["origin"], water_area_m2=s["water_area_m2"],
                outline_above_water_m=s.get("outline_above_water_m"),
                reflections=s.get("reflections"),
                files=dict(water="water.geojson", mask="water_mask.tif", dem="dem.tif" if (ex / "dem.tif").exists() else None),
                use="Plan: replace the map outline with water.geojson when scoring reflections. To model another water level, "
                    "raise/lower the edge with dem.tif (heights are metres above the water on the flight day).")
    name = f"HERON_site_{_slug(meta.get('site') or 'site')}_{meta.get('date') or 'undated'}.heronsite"
    with zipfile.ZipFile(ex / name, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("site.json", json.dumps(site, indent=1))
        for n in ("water.geojson", "shoreline.geojson", "water_mask.tif", "dem.tif", "summary.json"):
            if (ex / n).exists(): z.write(ex / n, n)
    return ex / name


def zip_all(flight_dir, meta):
    ex = Path(flight_dir) / "exports"; name = f"HERON_water_{_slug(meta.get('site') or meta.get('name') or 'flight')}_{meta.get('date') or 'undated'}.zip"
    with zipfile.ZipFile(ex / name, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(ex.iterdir()):
            if p.name in FORMATS: z.write(p, p.name)
    return ex / name


def shift_level(flight_dir, delta_m, meta):
    """Water edge if the lake were delta_m higher (+) or lower (-) than on the flight day: cells of
    the height model at or below delta_m, joined to the measured water. Returns GeoJSON dict."""
    d = Path(flight_dir); z = np.load(d / "out" / "dem.npz"); fr = origin(d)
    H = z["Z"] - float(z["water_z"]); c = float(z["cell"])
    wet = np.nan_to_num(H, nan=1e9) <= delta_m
    if delta_m >= 0:
        wm = load_watermap(d)
        if wm is not None:
            yy, xx = np.mgrid[0:H.shape[0], 0:H.shape[1]]
            E = float(z["x0"]) + (xx + 0.5) * c; N = float(z["y0"]) + (yy + 0.5) * c
            j = ((E - wm["x0"]) / wm["cell"]).astype(int); i = ((N - wm["y0"]) / wm["cell"]).astype(int)
            ok = (i >= 0) & (i < wm["prob"].shape[0]) & (j >= 0) & (j < wm["prob"].shape[1])
            meas = np.zeros_like(wet); meas[ok] = wm["prob"][i[ok], j[ok]] > 0.5; wet |= meas
    from shapely.geometry import mapping
    polys = polygons(wet, float(z["x0"]), float(z["y0"]), c, fr, simplify_m=0.6, min_area_m2=200)
    return dict(type="FeatureCollection", features=[dict(type="Feature", properties=dict(area_m2=round(a), level_change_m=delta_m),
                                                          geometry=mapping(p)) for p, a in polys])


def _slug(s):
    import re
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")[:40] or "x"


# ------------------------------------------------------------------ map-ready formats
def track_coords(d, n=1500):
    """Flown track as [[lon, lat, alt_m], ...] (absolute GPS altitude)."""
    d = Path(d)
    if (d / "track.json").exists(): return json.loads((d / "track.json").read_text())
    p = d / "data" / "POS.csv"
    if not p.exists(): return []
    with open(p) as f: rows = list(csv.DictReader(f))
    k = max(1, len(rows) // n)
    return [[float(r["Lng"]), float(r["Lat"]), float(r["Alt"])] for r in rows[::k]]


def _vectors(d, max_refl=3000):
    d = Path(d); ex = d / "exports"
    rd = lambda p: json.loads(p.read_text()) if p.exists() else None
    water = rd(ex / "water.geojson"); outline = rd(d / "data" / "outline.geojson"); shore = rd(ex / "shoreline.geojson")
    refl = []
    rp = d / "out" / "reflections.csv"
    if rp.exists():
        with open(rp) as f: rows = list(csv.DictReader(f))
        times = sorted({r["video_s"] for r in rows}, key=float); k = max(1, -(-len(rows) // max_refl)); keep = set(times[::k])
        refl = [dict(t=float(r["video_s"]), lat=float(r["spec_lat"]), lon=float(r["spec_lon"]), sat=r["sat"], el=float(r["el_deg"]),
                     o=r["water_by_outline"] == "True", m=r["water_measured"] == "True") for r in rows if r["video_s"] in keep]
    ortho = rd(d / "ortho.json")
    return dict(water=water, outline=outline, shore=shore, track=track_coords(d), refl=refl,
                ortho=ortho if ortho and (d / "ortho.png").exists() else None)


def _esc(s): return str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def kmz(d, meta):
    """Google Earth (desktop/web) and Google My Maps. The drone photo map is a ground overlay (Earth only)."""
    d = Path(d); v = _vectors(d, 1500); parts = []
    def ring(c): return " ".join(f"{x:.7f},{y:.7f},0" for x, y, *_ in c)
    def poly(g):
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        return "".join("<Polygon><outerBoundaryIs><LinearRing><coordinates>" + ring(pp[0]) + "</coordinates></LinearRing></outerBoundaryIs>"
                       + "".join(f"<innerBoundaryIs><LinearRing><coordinates>{ring(h)}</coordinates></LinearRing></innerBoundaryIs>" for h in pp[1:])
                       + "</Polygon>" for pp in polys)
    if v["water"]:
        pm = [f"<Placemark><name>Water {i + 1}</name><description>{ft['properties'].get('area_m2', 0):,.0f} m²</description><styleUrl>#water</styleUrl>"
              f"<MultiGeometry>{poly(ft['geometry'])}</MultiGeometry></Placemark>" for i, ft in enumerate(v["water"]["features"])]
        parts.append("<Folder><name>Water the camera saw</name>" + "".join(pm) + "</Folder>")
    if v["outline"]:
        pm = [f"<Placemark><name>Map outline</name><styleUrl>#outline</styleUrl><MultiGeometry>{poly(ft['geometry'])}</MultiGeometry></Placemark>"
              for ft in v["outline"]["features"]]
        parts.append("<Folder><name>Map outline (what the plan used)</name><visibility>1</visibility>" + "".join(pm) + "</Folder>")
    if v["track"]:
        c = " ".join(f"{x:.7f},{y:.7f},{z:.1f}" for x, y, z in v["track"])
        parts.append(f"<Placemark><name>Flown track</name><styleUrl>#track</styleUrl><LineString><altitudeMode>absolute</altitudeMode>"
                     f"<coordinates>{c}</coordinates></LineString></Placemark>")
    if v["refl"]:
        pm = [f"<Placemark><name>{r['sat']}</name><description>{r['t']:.1f} s · elevation {r['el']:.0f}° · "
              f"{'on water' if r['m'] else 'map says water, camera says dry' if r['o'] else 'land'}</description>"
              f"<styleUrl>#{'rw' if r['m'] else 'ro' if r['o'] else 'rl'}</styleUrl><Point><coordinates>{r['lon']:.7f},{r['lat']:.7f},0</coordinates></Point></Placemark>"
              for r in v["refl"]]
        parts.append("<Folder><name>Reflection points</name><visibility>0</visibility>" + "".join(pm) + "</Folder>")
    if v["ortho"]:
        o = v["ortho"]
        parts.append(f"<GroundOverlay><name>Drone photo map</name><drawOrder>1</drawOrder><Icon><href>files/ortho.png</href></Icon>"
                     f"<LatLonBox><north>{o['north']}</north><south>{o['south']}</south><east>{o['east']}</east><west>{o['west']}</west></LatLonBox></GroundOverlay>")
    dot = lambda c: f"<IconStyle><color>{c}</color><scale>0.45</scale><Icon><href>http://maps.google.com/mapfiles/kml/shapes/shaded_dot.png</href></Icon></IconStyle><LabelStyle><scale>0</scale></LabelStyle>"
    doc = ('<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
           f"<name>HERON · {_esc(meta.get('site'))} {_esc(meta.get('date'))}</name>"
           '<Style id="water"><LineStyle><color>ffffd64f</color><width>2</width></LineStyle><PolyStyle><color>66ffd64f</color></PolyStyle></Style>'
           '<Style id="outline"><LineStyle><color>ff57c8ff</color><width>2.5</width></LineStyle><PolyStyle><fill>0</fill></PolyStyle></Style>'
           '<Style id="track"><LineStyle><color>ff97dc3d</color><width>2.5</width></LineStyle></Style>'
           f'<Style id="rw">{dot("ffffd64f")}</Style><Style id="ro">{dot("ff57c8ff")}</Style><Style id="rl">{dot("ffb0a393")}</Style>'
           + "".join(parts) + "</Document></kml>")
    with zipfile.ZipFile(d / "exports" / "water.kmz", "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("doc.kml", doc)
        if v["ortho"]: z.write(d / "ortho.png", "files/ortho.png")


def html_map(d, meta):
    """Self-contained Leaflet map: data, photo map and Leaflet itself inlined, so it opens offline
    (base maps need internet). Good for sharing with the team or dropping on a website."""
    import base64
    from .config import STATIC_DIR
    d = Path(d); v = _vectors(d, 3000); L = STATIC_DIR / "vendor" / "leaflet"
    ortho = None
    if v["ortho"]:
        src = d / "ortho.webp" if (d / "ortho.webp").exists() else d / "ortho.png"
        ortho = dict(v["ortho"], img=f"data:image/{src.suffix[1:]};base64," + base64.b64encode(src.read_bytes()).decode())
    data = json.dumps(dict(water=v["water"], outline=v["outline"], track=v["track"], refl=v["refl"], ortho=ortho,
                           title=f"HERON · {meta.get('site') or ''} · {meta.get('date') or ''}"))
    html = MAP_TEMPLATE.replace("/*LEAFLET_CSS*/", (L / "leaflet.css").read_text()).replace("/*LEAFLET_JS*/", (L / "leaflet.js").read_text()) \
        .replace("/*DATA*/", data.replace("</", "<\\/")).replace("{{TITLE}}", _esc(f"HERON water map · {meta.get('site') or ''} {meta.get('date') or ''}"))
    (d / "exports" / "map.html").write_text(html)


def shapefiles(d, meta):
    import io, shapefile
    d = Path(d); v = _vectors(d, 100000)
    prj = ('GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],PRIMEM["Greenwich",0],'
           'UNIT["degree",0.0174532925199433]]')
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        def save(name, build):
            b = {k: io.BytesIO() for k in ("shp", "shx", "dbf")}
            w = shapefile.Writer(shp=b["shp"], shx=b["shx"], dbf=b["dbf"]); build(w); w.close()
            for k, buf in b.items(): z.writestr(f"{name}.{k}", buf.getvalue())
            z.writestr(f"{name}.prj", prj); z.writestr(f"{name}.cpg", "UTF-8")
        if v["water"]:
            def bw(w):
                w.shapeType = shapefile.POLYGON; w.field("area_m2", "N", 14, 1); w.field("date", "C", 10)
                for ft in v["water"]["features"]:
                    g = ft["geometry"]; polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
                    rings = []
                    for pp in polys:
                        outer = pp[0] if _ccw(pp[0]) is False else pp[0][::-1]           # shapefile: outer clockwise
                        rings.append([c[:2] for c in outer]); rings += [[c[:2] for c in (h if _ccw(h) else h[::-1])] for h in pp[1:]]
                    w.poly(rings); w.record(ft["properties"].get("area_m2", 0), str(meta.get("date") or ""))
            save("water", bw)
        if v["shore"]:
            def bs(w):
                w.shapeType = shapefile.POLYLINE; w.field("kind", "C", 12)
                for ft in v["shore"]["features"]: w.line([[c[:2] for c in ft["geometry"]["coordinates"]]]); w.record(ft["properties"].get("kind", ""))
            save("shoreline", bs)
        if v["track"]:
            def bt(w):
                w.shapeType = shapefile.POLYLINEZ; w.field("name", "C", 20)
                w.linez([[list(c) for c in v["track"]]]); w.record("flown track")
            save("track", bt)
        if v["refl"]:
            def br(w):
                w.shapeType = shapefile.POINT
                for f_, t_ in (("time_s", "N"), ("sat", "C"), ("elev_deg", "N"), ("map_water", "L"), ("cam_water", "L")):
                    w.field(f_, t_, 10 if t_ != "L" else 1, 2 if f_ in ("time_s", "elev_deg") else 0)
                for r in v["refl"]: w.point(r["lon"], r["lat"]); w.record(r["t"], r["sat"], r["el"], r["o"], r["m"])
            save("reflections", br)
    (d / "exports" / "water_shapefiles.zip").write_bytes(out.getvalue())


def _ccw(ring):
    a = 0.0
    for (x1, y1, *_), (x2, y2, *_) in zip(ring, ring[1:]): a += (x2 - x1) * (y2 + y1)
    return a < 0


def gpx(d, meta):
    d = Path(d); v = _vectors(d, 1)
    seg = lambda cs: "<trkseg>" + "".join(f'<trkpt lat="{c[1]:.7f}" lon="{c[0]:.7f}">' + (f"<ele>{c[2]:.1f}</ele>" if len(c) > 2 else "") + "</trkpt>" for c in cs) + "</trkseg>"
    trks = []
    if v["track"]: trks.append(f"<trk><name>Flown track</name>{seg(v['track'])}</trk>")
    if v["shore"]:
        segs = []
        for ft in v["shore"]["features"]:
            cs = ft["geometry"]["coordinates"]; k = max(1, len(cs) // 400); segs.append(seg(cs[::k] + [cs[-1]]))
        trks.append(f"<trk><name>Shoreline {_esc(meta.get('date'))}</name>{''.join(segs)}</trk>")
    (d / "exports" / "track_shoreline.gpx").write_text('<?xml version="1.0" encoding="UTF-8"?><gpx version="1.1" creator="HERON Studio" '
                                                      'xmlns="http://www.topografix.com/GPX/1/1">' + "".join(trks) + "</gpx>")


def shoreline_csv(d, meta):
    d = Path(d); v = _vectors(d, 1)
    if not v["shore"]: return
    lines = ["ring,kind,lat,lon"]
    for i, ft in enumerate(v["shore"]["features"]):
        cs = ft["geometry"]["coordinates"]; k = max(1, len(cs) // 600)
        lines += [f"{i + 1},{ft['properties'].get('kind', '')},{c[1]:.7f},{c[0]:.7f}" for c in cs[::k]]
    (d / "exports" / "shoreline_points.csv").write_text("\n".join(lines) + "\n")


MAP_TEMPLATE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{TITLE}}</title><style>/*LEAFLET_CSS*/
html,body,#map{height:100%;margin:0;background:#0f1a22;font:14px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.box{background:rgba(12,20,27,.88);color:#e7eef3;padding:10px 12px;border-radius:10px;border:1px solid rgba(255,255,255,.12);line-height:1.5}
.box b{display:block;margin-bottom:4px}.sw{display:inline-block;width:14px;height:9px;border-radius:2px;margin-right:6px;vertical-align:middle}
.leaflet-control-layers{background:rgba(12,20,27,.92);color:#e7eef3;border:1px solid rgba(255,255,255,.12)!important}</style></head>
<body><div id="map"></div><script>/*LEAFLET_JS*/</script><script>
const D = /*DATA*/;
const map = L.map("map", { preferCanvas: true, maxZoom: 21 });
const b0 = L.geoJSON(D.water || D.outline || { type: "FeatureCollection", features: [] }).getBounds();
if (b0.isValid()) map.fitBounds(b0, { padding: [20, 20] }); else if (D.track && D.track.length) map.setView([D.track[0][1], D.track[0][0]], 15); else map.setView([0, 0], 2);
const base = {
  "Satellite": L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", { maxZoom: 21, maxNativeZoom: 19, attribution: "Imagery © Esri" }),
  "Streets": L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap" }),
  "Satellite + labels": L.layerGroup([L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", { maxZoom: 21, maxNativeZoom: 19, attribution: "Imagery © Esri" }),
    L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}", { maxZoom: 21, maxNativeZoom: 19 })]),
  "Topographic": L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}", { maxZoom: 19, attribution: "© Esri" }),
  "No base map": L.layerGroup(),
};
base["Satellite"].addTo(map);
const over = {}; const fit = [];
if (D.ortho) over["Drone photo map"] = L.imageOverlay(D.ortho.img, [[D.ortho.south, D.ortho.west], [D.ortho.north, D.ortho.east]]).addTo(map);
if (D.outline) { over["Map outline"] = L.geoJSON(D.outline, { style: { color: "#ffc857", weight: 2.5, dashArray: "7 6", fill: false } }).addTo(map); }
if (D.water) { over["Water the camera saw"] = L.geoJSON(D.water, { style: { color: "#4fd6ff", weight: 1.5, fillColor: "#4fd6ff", fillOpacity: .35 },
  onEachFeature: (f, l) => l.bindTooltip(Math.round(f.properties.area_m2).toLocaleString() + " m²") }).addTo(map); fit.push(over["Water the camera saw"]); }
if (D.track && D.track.length) over["Flown track"] = L.polyline(D.track.map(c => [c[1], c[0]]), { color: "#3ddc97", weight: 2.5 }).addTo(map);
if (D.refl && D.refl.length) over["Reflection points"] = L.layerGroup(D.refl.map(p => L.circleMarker([p.lat, p.lon], { radius: 3, weight: 0, fillOpacity: .85,
  fillColor: p.m ? "#4fd6ff" : p.o ? "#ffc857" : "#93a3b0" }).bindTooltip(`${p.sat} · ${p.el.toFixed(0)}° · ${p.t.toFixed(1)} s`)));
L.control.layers(base, over, { collapsed: false }).addTo(map);
L.control.scale({ imperial: false }).addTo(map);
const lg = L.control({ position: "bottomright" });
lg.onAdd = () => { const e = L.DomUtil.create("div", "box"); e.innerHTML = `<b>${D.title}</b><span class="sw" style="background:#4fd6ff"></span>water the camera saw<br>
  <span class="sw" style="border:2px dashed #ffc857"></span>map outline<br><span class="sw" style="background:#3ddc97"></span>flown track<br>
  <span style="opacity:.6;font-size:12px">Made with HERON Studio</span>`; return e; };
lg.addTo(map);
const cc = L.control({ position: "bottomleft" }); cc.onAdd = () => { const e = L.DomUtil.create("div", "box"); e.style.font = "12px ui-monospace,Menlo,monospace"; e.textContent = "lat, lon"; map.on("mousemove", ev => e.textContent = ev.latlng.lat.toFixed(6) + ", " + ev.latlng.lng.toFixed(6)); return e; }; cc.addTo(map);
</script></body></html>"""
