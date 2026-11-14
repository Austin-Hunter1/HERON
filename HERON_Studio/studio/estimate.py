"""Processing-time estimates, before a run, from the options picked and the flights processed so far.

Every step gets a (low, high) range in seconds. Steps that scale with clip length (read files, water,
reflections, render) use per-second-of-video rates measured on finished flights. The camera solve is
split into frame extraction, features, matching (per frame) and mapping, which grows faster than the
frame count: mapping = a * frames^b, fitted on every mapping run recorded (Studio solves and
tools/solve_bench.py results). A chunked solve is estimated as waves of chunk solves plus the merge.

Built-in numbers come from the first flights on the Windows / RTX 2070 machine (Nov 2026); measured
flights replace them as they come in.
"""
import json, math, re
from pathlib import Path
from .config import FLIGHTS_DIR

HOME = FLIGHTS_DIR.parent
BENCH = HOME / "bench" / "results.json"

# seconds per second of video (low, high) until there are measured flights
DEFAULT_RATES = {
    "prepare": (0.30, 0.45),        # .bin -> CSV, GPMF, preview copy; joining chapters adds a little
    "water": (1.0, 1.25),
    "reflections": (0.045, 0.06),
    "render": (17.0, 21.0),          # at 1080p, parallel chunks, 8 threads; scaled by output pixels
}
FIXED = {"sync": (25, 60), "georef": (12, 35), "model3d": (1, 5), "exports": (10, 25)}
# per solved frame (GPU = CUDA COLMAP build, CPU = pycolmap)
PER_FRAME = {"extract_frames_4k": 0.12, "features_gpu": 0.025, "features_cpu": 0.15,
             "match_gpu": 0.065, "match_cpu": 1.1}
# mapping fit seed points: (frames, seconds, source)
SEED_MAPPING = [(150, 70.0, "150-frame test, CUDA build"),
                (5704, 98073.0, "10 Nov flight, 6 fps")]
STEP_LABELS = {"prepare": "Read files", "sync": "Sync clocks", "solve": "Solve the camera", "georef": "Tie to GPS",
               "water": "Map the water", "reflections": "Reflections", "model3d": "3D model",
               "render": "Render AR video", "exports": "Exports"}


# ------------------------------------------------------------------ history
def _flights():
    for fj in sorted(FLIGHTS_DIR.glob("*/flight.json")):
        try: yield fj.parent, json.loads(fj.read_text())
        except Exception: continue


def _mapping_runs_from_log(log):
    """(frames, seconds) for every finished mapping of the largest component in a Studio job log."""
    out, n = [], None
    for line in log.splitlines():
        m = re.match(r"=== Reconstructing component 1 / \d+ with (\d+) images", line)
        if m: n = int(m.group(1)); continue
        m = re.match(r"Reconstruction done in ([0-9.]+) seconds", line)
        if m and n: out.append((n, float(m.group(1)))); n = None
    return out


def history():
    """Measured rates and mapping runs from finished flights and the solve benchmarks."""
    rates = {k: [] for k in DEFAULT_RATES}; fixed = {k: [] for k in FIXED}; mapping = list(SEED_MAPPING)
    for d, f in _flights():
        j = f.get("job") or {}; v = f.get("video") or {}; dur = v.get("duration_s")
        if not dur: continue
        px = 1.0
        try:
            cfg = json.loads((d / "config.json").read_text())
            px = cfg["output_width"] * cfg["output_height"] / (1920 * 1080)
        except Exception: pass
        for k in rates:
            t = j.get(f"t_{k}")
            if t: rates[k].append(t / dur / (px if k == "render" else 1))
        for k in fixed:
            if j.get(f"t_{k}"): fixed[k].append(j[f"t_{k}"])
        try:
            for n, s in _mapping_runs_from_log((d / "job.log").read_text(errors="replace")):
                if n > 20 and not any(abs(n - m[0]) < 2 and abs(s - m[1]) < 2 for m in mapping): mapping.append((n, s, d.name))
        except Exception: pass
    try:
        for r in json.loads(BENCH.read_text()):
            if r.get("kind") == "caps" and not r.get("cap") and r.get("placed"):
                mapping.append((r["placed"], sum(r["stages"].values()) or r["seconds"], "bench " + r["label"]))
    except Exception: pass
    return rates, fixed, mapping


def _chunk_runs():
    """(wall seconds, waves, chunk size, jobs) for each chunked solve benchmark."""
    out = []
    try:
        for r in json.loads(BENCH.read_text()):
            if r.get("chunk_wall_s") and r.get("label", "").startswith("cheap merge"):
                sizes = r["chunk_sizes"]; j = r["jobs"]
                out.append((r["chunk_wall_s"], math.ceil(len(sizes) / j), sum(sizes) / len(sizes), j))
    except Exception: pass
    return out


def _fit_power(points):
    """Least squares on log-log: seconds = a * frames^b. Returns (a, b, spread factor)."""
    pts = [(math.log(n), math.log(s)) for n, s, *_ in points if n > 0 and s > 0]
    if len(pts) < 2: return 0.0033, 2.0, 2.0
    mx = sum(x for x, _ in pts) / len(pts); my = sum(y for _, y in pts) / len(pts)
    sxx = sum((x - mx) ** 2 for x, _ in pts) or 1e-9
    b = max(1.0, min(3.0, sum((x - mx) * (y - my) for x, y in pts) / sxx)); a = math.exp(my - b * mx)
    spread = math.exp(max(abs(y - (math.log(a) + b * x)) for x, y in pts)) if len(pts) > 2 else 2.0
    return a, b, max(1.3, min(spread, 4.0))


def _range(vals, default, lo_pad=0.85, hi_pad=1.2):
    if not vals: return default
    if len(vals) == 1: return (vals[0] * 0.75, vals[0] * 1.35)
    return (min(vals) * lo_pad, max(vals) * hi_pad)


# ------------------------------------------------------------------ estimate
def solve_frames(duration_s, fps, step): return int(math.ceil(duration_s * fps / max(1, step)))


def chunk_plan(n, size, overlap):
    if not size or n <= size: return [n]
    k = int(math.ceil((n - overlap) / (size - overlap))); stride = (n - overlap) / k
    return [int(round(stride)) + overlap] * k


def estimate(video, settings, frame_step, render_px=(1920, 1080), gpu=True, extracted=False, solve_cfg=None):
    """Per-step (low, high) seconds and the total. video: dict(duration_s, fps, width, parts).
    solve_cfg: the sfm options (chunk_size, chunk_overlap, chunk_jobs, merge, track_cap)."""
    rates, fixed, mapping = history()
    dur = float(video["duration_s"]); fps = float(video["fps"]); make = settings.get("make", {})
    sc = solve_cfg or {}; out = {}; notes = []
    r = _range(rates["prepare"], DEFAULT_RATES["prepare"]); out["prepare"] = (r[0] * dur, r[1] * dur)
    out["sync"] = _range(fixed["sync"], FIXED["sync"], 0.7, 1.5)

    n = solve_frames(dur, fps, frame_step)
    src_scale = max(0.3, (video.get("width") or 3840) / 3840)
    ext = 0 if extracted else PER_FRAME["extract_frames_4k"] * src_scale * n
    feat = n * (PER_FRAME["features_gpu"] if gpu else PER_FRAME["features_cpu"])
    match = n * (PER_FRAME["match_gpu"] if gpu else PER_FRAME["match_cpu"])
    a, b, spread = _fit_power(mapping)
    cap = sc.get("track_cap")
    # measured (11 Nov benchmarks): 1,000 tracks/frame ~13 % faster, 500 ~5 %, lower caps slow bundle adjustment
    capf = 1.0 if not cap else 0.87 if int(cap) >= 1000 else 0.95 if int(cap) >= 500 else 1.15
    sizes = chunk_plan(n, sc.get("chunk_size") or 0, sc.get("chunk_overlap") or 0)
    if len(sizes) == 1:
        m = a * n ** b * capf; mlo, mhi = m / spread, m * spread
        notes.append(f"{n:,} frames in one solve")
    else:
        jobs = max(1, int(sc.get("chunk_jobs") or 1)); waves = math.ceil(len(sizes) / jobs)
        merge = sc.get("merge", "cheap")
        meas = _chunk_runs()
        if meas:      # measured chunked solves: wall time per wave, scaled to this chunk size and job count
            per = [w / mw * (sizes[0] / ms) ** b * (1.0 + 0.15 * (jobs - mj)) for w, mw, ms, mj in meas]
            plo, phi = min(per) * waves * 0.8, max(per) * waves * 1.35
        else:
            p = a * sizes[0] ** b * capf * (1.0 + 0.15 * (jobs - 1)) * waves; plo, phi = p / spread, p * spread
        # full merge: model_merger (~30 s) + one bundle adjustment, measured at ~0.0036 s per frame per iteration
        it = int(sc.get("merge_ba_iters") or 50)
        mt = 30 + (0.0036 * n * it if merge == "full" else 0)
        mlo, mhi = plo + mt * 0.6, phi + mt * 1.3
        notes.append(f"{n:,} frames in {len(sizes)} chunks of ~{sizes[0]:,}, {jobs} at a time, {merge} merge")
    out["solve"] = (ext + feat + match + mlo, ext + feat + match + mhi)
    out["georef"] = _range(fixed["georef"], FIXED["georef"], 0.7, 1.5)
    if make.get("water", True):
        r = _range(rates["water"], DEFAULT_RATES["water"]); out["water"] = (r[0] * dur, r[1] * dur)
    r = _range(rates["reflections"], DEFAULT_RATES["reflections"]); out["reflections"] = (r[0] * dur, r[1] * dur)
    if make.get("model3d", True): out["model3d"] = FIXED["model3d"]
    if make.get("ar_video", True):
        px = render_px[0] * render_px[1] / (1920 * 1080)
        r = _range(rates["render"], DEFAULT_RATES["render"]); out["render"] = (r[0] * dur * px, r[1] * dur * px)
    out["exports"] = _range(fixed["exports"], FIXED["exports"], 0.7, 1.5)
    steps = [dict(id=k, label=STEP_LABELS[k], low=round(lo), high=round(hi)) for k, (lo, hi) in out.items()]
    return dict(steps=steps, low=round(sum(s["low"] for s in steps)), high=round(sum(s["high"] for s in steps)),
                solve_frames=n, notes=notes, mapping_fit=dict(a=a, b=round(b, 2), spread=round(spread, 2), points=len(mapping)))
