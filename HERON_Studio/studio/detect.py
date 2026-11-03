"""Work out what a dropped file is, and for videos: resolution, frame rate, telemetry, lens.

Any GoPro resolution/aspect/frame rate is accepted. The lens profile is matched on camera
model, lens mode (Wide/Linear/...), aspect ratio and EIS, then scaled to the clip's width,
so a 4K calibration serves a 1080p or 2.7K clip of the same aspect. A profile of a different
aspect is never used (4:3 intrinsics on a 16:9 crop would be wrong).
"""
import json, re, subprocess
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from .config import LENS_DIR

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".lrv", ".360"}
LOG_EXT = {".bin", ".log"}
TLOG_EXT = {".tlog"}
MISSION_EXT = {".waypoints", ".plan", ".txt"}
OUTLINE_EXT = {".geojson", ".json", ".kml"}

ASPECTS = {"16:9": 16 / 9, "4:3": 4 / 3, "3:2": 3 / 2, "8:7": 8 / 7, "9:16": 9 / 16, "2.35:1": 2.35, "1:1": 1.0}
FIRMWARE_MODELS = {"HD5": "HERO5 Black", "HD6": "HERO6 Black", "HD7": "HERO7 Black", "HD8": "HERO8 Black",
                   "HD9": "HERO9 Black", "H21": "HERO10 Black", "H22": "HERO11 Black", "H23": "HERO12 Black",
                   "H24": "HERO13 Black", "HX": "HERO10 Black"}
VFOV_MODES = {"W": "Wide", "L": "Linear", "N": "Narrow", "S": "Superview", "X": "Max", "H": "Hyperview", "M": "Max"}


def classify(path):
    p = Path(path); ext = p.suffix.lower()
    if ext in VIDEO_EXT: return "video"
    if ext in LOG_EXT: return "log" if _is_dataflash(p) else "unknown"
    if ext in TLOG_EXT: return "tlog"
    if ext in OUTLINE_EXT: return "outline"
    if ext in MISSION_EXT:
        head = p.read_text(errors="replace")[:200]
        if head.startswith("QGC WPL") or '"mission"' in head: return "mission"
    if ext == ".csv": return "csv"
    return "unknown"


def _is_dataflash(p):
    with open(p, "rb") as f: return f.read(3) == b"\xa3\x95\x80"


def probe_video(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    j = json.loads(out); fmt = j.get("format", {}); tags = fmt.get("tags", {})
    v = next((s for s in j["streams"] if s["codec_type"] == "video" and s.get("disposition", {}).get("attached_pic") != 1), None)
    if v is None: raise ValueError("no video stream")
    w, h = int(v["width"]), int(v["height"])
    rot = int(v.get("tags", {}).get("rotate", 0) or next((sd.get("rotation", 0) for sd in v.get("side_data_list", []) if "rotation" in sd), 0))
    if abs(rot) in (90, 270): w, h = h, w
    fps = snap_fps(v.get("avg_frame_rate"), v.get("r_frame_rate"))
    gpmd = next((s for s in j["streams"] if s.get("codec_tag_string") == "gpmd" or "GoPro MET" in s.get("tags", {}).get("handler_name", "")), None)
    fw = tags.get("firmware", "") or ""
    model = next((m for k, m in FIRMWARE_MODELS.items() if fw.startswith(k)), None)
    return dict(width=w, height=h, fps=round(fps, 4), duration_s=float(fmt.get("duration") or v.get("duration") or 0),
                video_duration_s=float(v.get("duration") or fmt.get("duration") or 0), chapter=chapter_key(Path(path).name),
                codec=v.get("codec_name"), bitrate_mbps=round(int(fmt.get("bit_rate") or 0) / 1e6, 1),
                pix_fmt=v.get("pix_fmt"), rotation=rot, size_bytes=int(fmt.get("size") or 0),
                creation_time=tags.get("creation_time"), firmware=fw or None, camera_model=model,
                telemetry_stream=gpmd["index"] if gpmd else None,
                resolution_label=resolution_label(w, h), aspect=aspect_label(w, h),
                browser_playable=v.get("codec_name") == "h264" and v.get("pix_fmt") in ("yuv420p", "yuvj420p") and max(w, h) <= 1920)


STD_FPS = (23.976, 24, 25, 29.97, 30, 47.952, 48, 50, 59.94, 60, 100, 119.88, 120, 200, 239.76, 240)

def snap_fps(avg, r):
    """Container rates drift a little after editing (23.9837); snap to the camera's real rate."""
    vals = []
    for x in (avg, r):
        try:
            f = float(Fraction(x))
            if f > 0: vals.append(f)
        except Exception: pass
    if not vals: return 0.0
    f = vals[0]; best = min(STD_FPS, key=lambda s: abs(s - f))
    exact = {23.976: 24000 / 1001, 29.97: 30000 / 1001, 47.952: 48000 / 1001, 59.94: 60000 / 1001,
             119.88: 120000 / 1001, 239.76: 240000 / 1001}
    return exact.get(best, float(best)) if abs(best - f) / best < 0.002 else f


def chapter_key(name):
    """GoPro splits long recordings into ~4 GB chapters with related names:
    GX010123 / GX020123 (HEVC), GH010123 / GH020123 (H.264), GOPR0123 then GP010123 (older cameras).
    Returns {"recording": id, "n": chapter number} or None."""
    m = re.match(r"^(G[HXL])(\d{2})(\d{4})\.(MP4|MOV|LRV)$", name, re.I)
    if m: return dict(recording=f"{m.group(1).upper()}{m.group(3)}", n=int(m.group(2)))
    m = re.match(r"^GOPR(\d{4})\.(MP4|MOV)$", name, re.I)
    if m: return dict(recording=f"GP{m.group(1)}", n=0)
    m = re.match(r"^GP(\d{2})(\d{4})\.(MP4|MOV)$", name, re.I)
    if m: return dict(recording=f"GP{m.group(2)}", n=int(m.group(1)))
    return None


def same_stream(a, b):
    """Can two videos be joined frame-exactly (same size, rate, codec)?"""
    return a["width"] == b["width"] and a["height"] == b["height"] and abs(a["fps"] - b["fps"]) < 0.01 and a.get("codec") == b.get("codec")


def resolution_label(w, h):
    s = max(w, h)
    for lim, lab in ((800, "480p"), (1300, "720p"), (2000, "1080p" if min(w, h) <= 1080 else "1440p"), (2800, "2.7K"),
                     (4100, "4K"), (5000, "4.5K"), (5400, "5.3K"), (99999, "8K")):
        if s <= lim: return lab


def aspect_label(w, h):
    r = w / h
    return min(ASPECTS, key=lambda k: abs(ASPECTS[k] - r))


# ------------------------------------------------------------------ lens profiles
def _lens_sources():
    """(id, text) for every profile: loose .json files and any .zip of them in lens_profiles/."""
    import zipfile
    for f in sorted(LENS_DIR.rglob("*.json")): yield str(f.relative_to(LENS_DIR)), f.read_text
    for z in sorted(LENS_DIR.glob("*.zip")):
        zf = zipfile.ZipFile(z)
        for n in sorted(zf.namelist()):
            if n.endswith(".json"): yield n, (lambda n=n, zf=zf: zf.read(n).decode())


def read_lens(lens_id):
    """Profile text by id (works for loose files and zipped ones)."""
    p = LENS_DIR / lens_id
    if p.is_file(): return p.read_text()
    for i, rd in _lens_sources():
        if i == lens_id: return rd()
    raise KeyError(lens_id)


@lru_cache(maxsize=1)
def lens_index():
    out = []
    for lid, rd in _lens_sources():
        try: d = json.loads(rd())
        except Exception: continue
        fp = d.get("fisheye_params") or {}
        if not fp.get("camera_matrix") or d.get("digital_lens") or d.get("distortion_model") not in (None, "opencv_fisheye"): continue
        cw, ch = d["calib_dimension"]["w"], d["calib_dimension"]["h"]
        name = d.get("name") or Path(lid).stem
        if re.search(r"anamorph|lens mod|macro|kase|neewer|neweer|max lens|ultra wide lens|1\.\d+x", name, re.I): continue
        note = (d.get("note") or "") + " " + (d.get("camera_setting") or "") + " " + name
        out.append(dict(id=lid, name=name, model=(d.get("camera_model") or "").strip(),
                        lens=(d.get("lens_model") or "").strip(), calib_w=cw, calib_h=ch, aspect=aspect_label(cw, ch),
                        eis=("EIS" in note.upper() and "NO-EIS" not in note.upper() and "EIS-NO" not in note.upper()),
                        rms=(fp.get("RMS_error") or 0), heron=lid.startswith("HERON/")))
    return out


def match_lens(video, telemetry=None, limit=8):
    """Rank lens profiles for a probed video. Returns (best, alternatives, notes)."""
    telemetry = telemetry or {}
    model = video.get("camera_model") or _model_from_device(telemetry.get("model") or telemetry.get("device"))
    mode = VFOV_MODES.get((telemetry.get("vfov") or "W")[:1].upper(), "Wide")
    eis = str(telemetry.get("eise", "")).upper() in ("Y", "1", "ON", "HS", "EIS")
    notes = []
    if mode in ("Superview", "Hyperview"):
        notes.append(f"{mode} stretches the image non-linearly; the solve needs Wide or Linear footage.")
    if video.get("telemetry_stream") is None and not telemetry:
        notes.append("No camera model or gyro track in this file (an edited export?). Lens guessed; add the original GoPro MP4 or a GYRO/ACCL folder.")
    cands = [p for p in lens_index() if p["aspect"] == video["aspect"]]
    if not cands: return None, [], notes + [f"No lens profile with a {video['aspect']} aspect. Pick one by hand or add one."]
    def score(p):
        s = 0.0
        if model and p["model"].lower() == model.lower(): s += 100
        elif model and p["model"].split()[0].lower() == model.split()[0].lower(): s += 20
        if p["lens"].lower().startswith(mode.lower()): s += 40
        if p["eis"] == eis: s += 10
        s -= abs(p["calib_w"] - video["width"]) / 400.0          # same sensor mode is best
        s -= min(p["rms"], 5) * 2
        if p["heron"] and p["model"] == "HERO7 Black" and model in ("HERO7 Black", None): s += 15   # used on 19 Oct
        return s
    ranked = sorted(cands, key=score, reverse=True)[:limit]
    best = ranked[0]
    if model and best["model"].lower() != model.lower():
        notes.append(f"No {model} profile with this aspect; using {best['model']}. Check the overlay lines up.")
    if best["calib_w"] != video["width"]:
        notes.append(f"Calibrated at {best['calib_w']}×{best['calib_h']}; scaled to {video['width']}×{video['height']}.")
    return best, ranked, notes


def _model_from_device(s):
    if not s: return None
    m = re.search(r"hero\s*(\d+)", s, re.I)
    return f"HERO{m.group(1)} Black" if m else None


def processing_sizes(video):
    """Sizes the pipeline works at, keeping the clip's aspect. Source frames are never upscaled."""
    w, h = video["width"], video["height"]; a = w / h
    def fit(tw): tw = min(tw, w); return dict(width=int(tw) // 2 * 2, height=int(round(tw / a)) // 2 * 2)
    return dict(solve=fit(960), water=fit(1920), proxy=fit(1280),
                render_options={"source": dict(width=w // 2 * 2, height=h // 2 * 2), "1080p": fit(1920), "720p": fit(1280)})
