"""Unit checks that need no server:  python -m pytest tests/  (or python tests/test_units.py)"""
import json, os, sys, tempfile
from pathlib import Path
import numpy as np

APP = Path(__file__).resolve().parent.parent; sys.path.insert(0, str(APP))
os.environ.setdefault("HERON_STUDIO_HOME", tempfile.mkdtemp())
from studio import gpmf, detect, water, uploads


def test_orin_reorder():
    d = np.arange(6.).reshape(2, 3) + 1
    assert (gpmf.to_hero7_order(d, "YxZ") == d).all()                 # HERO7 itself: unchanged
    out = gpmf.to_hero7_order(d, "ZXY")                                 # HERO7 col0 = Y, col1 = -X, col2 = Z
    assert out[0].tolist() == [3, -2, 1]


def test_fps_snap():
    assert abs(detect.snap_fps("1439000/60000", None) - 24000 / 1001) < 1e-9     # 23.983 edited export -> 23.976
    assert detect.snap_fps("25/1", None) == 25.0
    assert abs(detect.snap_fps("60000/1001", None) - 59.94006) < 1e-4


def test_resolution_and_aspect():
    assert detect.resolution_label(1920, 1080) == "1080p" and detect.resolution_label(1920, 1440) == "1440p"
    assert detect.resolution_label(5312, 2988) == "5.3K" and detect.aspect_label(2704, 2028) == "4:3"
    s = detect.processing_sizes(dict(width=3840, height=2160))
    assert s["solve"] == dict(width=960, height=540) and s["render_options"]["720p"] == dict(width=1280, height=720)
    s = detect.processing_sizes(dict(width=1280, height=720))
    assert s["render_options"]["1080p"]["width"] == 1280                  # never upscale


def test_lens_never_crosses_aspect():
    for w, h, a in ((3840, 2160, "16:9"), (2704, 2028, "4:3"), (5312, 4648, "8:7")):
        best, alts, _ = detect.match_lens(dict(width=w, height=h, aspect=a, camera_model="HERO11 Black"), {"vfov": "W"})
        if best: assert all(x["aspect"] == a for x in alts)


def test_gopro_chapters():
    assert detect.chapter_key("GX010123.MP4") == dict(recording="GX0123", n=1)
    assert detect.chapter_key("GX020123.MP4")["recording"] == detect.chapter_key("GX010123.MP4")["recording"]
    assert detect.chapter_key("GOPR0042.MP4")["n"] == 0 and detect.chapter_key("GP010042.MP4")["recording"] == "GP0042"
    assert detect.chapter_key("HERON_GoPro1_full_720p.mov") is None
    from studio import ingest
    mk = lambda n, d: dict(name=n, info=dict(width=3840, height=2160, fps=29.97, codec="hevc", duration_s=d, chapter=detect.chapter_key(n)))
    vids = [mk("GX020007.MP4", 390), mk("GX010007.MP4", 531), mk("other.mov", 100)]
    assert [x["name"] for x in ingest.recording_parts(vids, {})] == ["GX010007.MP4", "GX020007.MP4"]
    assert [x["name"] for x in ingest.recording_parts(vids, {"join_chapters": False})] == ["GX010007.MP4"]


def test_safe_names():
    assert uploads.safe_name("../../etc/passwd") == "passwd"
    assert uploads.safe_name("C:\\GoPro\\GX010123.MP4") == "GX010123.MP4"


def test_polygons_roundtrip():
    fr = water.Frame(40.0859711, -105.2328258)
    m = np.zeros((200, 200), bool); m[50:150, 40:120] = True                 # 80 x 100 cells of 0.5 m = 40 x 50 m
    polys = water.polygons(m, 100.0, -200.0, 0.5, fr, simplify_m=0.1, min_area_m2=1)
    assert len(polys) == 1 and abs(polys[0][1] - 2000) < 5
    lon, lat = polys[0][0].exterior.coords[0]
    e, n = fr.en(lon, lat); assert 115 < e < 165 and -180 < n < -120


def test_sample_exports_open():
    import zipfile, io, shapefile, xml.dom.minidom as md
    ex = APP / "sample" / "boulder-2026-10-19" / "exports"
    if not ex.exists(): return
    md.parseString(zipfile.ZipFile(ex / "water.kmz").read("doc.kml")); md.parseString((ex / "track_shoreline.gpx").read_text())
    z = zipfile.ZipFile(ex / "water_shapefiles.zip")
    r = shapefile.Reader(shp=io.BytesIO(z.read("water.shp")), shx=io.BytesIO(z.read("water.shx")), dbf=io.BytesIO(z.read("water.dbf")))
    assert len(r) >= 1 and -105.3 < r.bbox[0] < -105.2
    assert "L.map(" in (ex / "map.html").read_text()


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"): v(); print("ok", k)
