"""Pipeline steps that need the HERON engine loaded. Run in a child process:

    python -m studio.engine_tasks reflections <flight_dir>
    python -m studio.engine_tasks model3d     <flight_dir>

The engine is put on sys.path from config.ENGINE_DIR and reads per-flight folders from the
HERON_* environment variables set by jobs.py.
"""
import csv, json, math, sys
from pathlib import Path
import numpy as np

from .config import ENGINE_DIR
sys.path.insert(0, str(ENGINE_DIR))


def _scene(flight_dir):
    from heron_ar.geometry import load_config, Fisheye, DATA, clip_fps, ref_wh
    from heron_ar.scene import Scene
    cfg = load_config(Path(flight_dir) / "config.json"); sc = Scene(cfg)
    lens = Fisheye(DATA / cfg["lens_profile"], *ref_wh(cfg), cfg.get("focal_scale", 1.0))
    return cfg, sc, lens


def reflections(flight_dir, step_s=0.5):
    """Every satellite above the mask, every step_s: where does its reflection land, and is that
    water by the map outline vs by what the camera saw?"""
    from matplotlib.path import Path as MPath
    from heron_ar.geometry import fresnel_zone, CACHE
    from heron_ar.scene import sat_state
    from heron_ar.watermap import WaterMap
    flight_dir = Path(flight_dir); cfg, sc, lens = _scene(flight_dir)
    wmp = next(iter(sorted(CACHE.glob("watermap_*.npz"))), None)
    wm = WaterMap.load(wmp) if wmp else None
    pool = MPath(sc.pool); rows = []; F = sc.F
    t = 0.0; n = 0
    while t < cfg["clip_duration_s"]:
        vt = cfg["clip_video_start_s"] + t
        mt, cam, lat, lon = sc.pose(vt, lens); dt, sats = sat_state(sc, mt, cam.C)
        for s in sats:
            if s["el"] < cfg["elevation_mask_deg"]: continue
            c_, ax, _ = fresnel_zone(s["enu"], cam.C, sc.wl, 8)
            on = None if wm is None else bool(wm.sample(np.array([c_[0]]), np.array([c_[1]]))[0] > 0.5)
            seen = None
            if wm is not None and wm.seen is not None:
                j = int((c_[0] - wm.x0) / wm.cell); i = int((c_[1] - wm.y0) / wm.cell)
                seen = bool(0 <= i < wm.H and 0 <= j < wm.W and wm.seen[i, j])
            la, lo = _ll(F, c_[0], c_[1])
            rows.append(dict(video_s=round(t, 2), mission_s=round(mt, 3), utc=dt.isoformat(timespec="milliseconds"),
                             sat=s["sid"], constellation=s["const"], el_deg=round(s["el"], 2), az_deg=round(s["az"], 2),
                             spec_e_m=round(c_[0], 2), spec_n_m=round(c_[1], 2), spec_lat=round(la, 8), spec_lon=round(lo, 8),
                             drone_e_m=round(cam.C[0], 2), drone_n_m=round(cam.C[1], 2), drone_up_m=round(cam.C[2], 2),
                             water_by_outline=bool(pool.contains_point(c_[:2])), water_measured=on, camera_saw=seen))
        t += step_s; n += 1
        if n % 40 == 0: print(f"reflections {100 * t / cfg['clip_duration_s']:.0f}%", flush=True)
    out = flight_dir / "out" / "reflections.csv"
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    R = np.array([(r["water_by_outline"], bool(r["water_measured"])) for r in rows], bool)
    st = dict(samples=len(rows), step_s=step_s, outline_water=int(R[:, 0].sum()), measured_water=int(R[:, 1].sum()),
              outline_but_dry=int((R[:, 0] & ~R[:, 1]).sum()))
    (flight_dir / "out" / "reflections_summary.json").write_text(json.dumps(st, indent=1)); print(st)


def _ll(F, e, n):
    """Inverse of LocalFrame.en_from_ll (same local linearisation the engine uses for outlines)."""
    from heron_ar.geometry import WGS_E2
    lat0 = math.radians(F.lat0)
    rn = 6378137 * (1 - WGS_E2) / (1 - WGS_E2 * math.sin(lat0) ** 2) ** 1.5
    re = 6378137 / math.sqrt(1 - WGS_E2 * math.sin(lat0) ** 2)
    return F.lat0 + math.degrees(n / rn), F.lon0 + math.degrees(e / (re * math.cos(lat0)))


def model3d(flight_dir, cell=1.0):
    """Height grid from the solved 3D points (z = 0 at the config water elevation).
    Dense flights (the 19 Oct sample) ship a finer grid; this is the sparse version for new flights."""
    from heron_ar.geometry import DATA
    flight_dir = Path(flight_dir); cfg = json.loads((flight_dir / "config.json").read_text())
    z = np.load(DATA / cfg["sfm_poses"]); P = z["points"] if "points" in z.files else None
    if P is None or len(P) < 100: print("not enough 3D points for a model"); return
    P = P[np.isfinite(P).all(1)]; lo, hi = np.percentile(P[:, 2], [1, 99]); P = P[(P[:, 2] > lo - 2) & (P[:, 2] < hi + 2)]
    x0, y0 = P[:, 0].min(), P[:, 1].min(); W = int((P[:, 0].max() - x0) / cell) + 1; H = int((P[:, 1].max() - y0) / cell) + 1
    ix = ((P[:, 0] - x0) / cell).astype(int); iy = ((P[:, 1] - y0) / cell).astype(int)
    S = np.zeros((H, W)); N = np.zeros((H, W), int); np.add.at(S, (iy, ix), P[:, 2]); np.add.at(N, (iy, ix), 1)
    Z = np.where(N > 0, S / np.maximum(N, 1), np.nan).astype(np.float32)
    wz, how = water_level_from_edge(Z, x0, y0, cell)
    if wz is None: wz, how = float(cfg.get("water_offset_m", 0.0)), "config"
    print(f"water level in the model: {wz:+.2f} m ({how})")
    np.savez_compressed(flight_dir / "out" / "dem.npz", Z=Z, N=N, x0=x0, y0=y0, cell=cell, water_z=wz, wet=(Z <= wz + 0.05))
    print(f"model: {len(P):,} points -> {W}x{H} grid at {cell} m")


def water_level_from_edge(Z, x0, y0, cell):
    """The water surface itself has almost no 3D points (no texture), but the ground right at the
    measured water edge sits at water level. Median height of the model along that edge."""
    import cv2
    from heron_ar.geometry import CACHE
    from heron_ar.watermap import WaterMap
    p = next(iter(sorted(CACHE.glob("watermap_*.npz"))), None)
    if p is None: return None, None
    wm = WaterMap.load(p); m = (wm.prob > 0.5).astype(np.uint8)
    edge = m - cv2.erode(m, np.ones((3, 3), np.uint8))                   # water cells touching land
    ii, jj = np.nonzero(edge); e = wm.x0 + (jj + 0.5) * wm.cell; n = wm.y0 + (ii + 0.5) * wm.cell
    # median ground height within ~3 m of each edge cell (checked on 19 Oct: 4 cm from the dense estimate)
    hs = []
    for k in range(len(e)):
        j = int((e[k] - x0) / cell); i = int((n[k] - y0) / cell); r = max(1, int(3 / cell))
        if 0 <= i < Z.shape[0] and 0 <= j < Z.shape[1]:
            w = Z[max(0, i - r):i + r + 1, max(0, j - r):j + r + 1]; w = w[np.isfinite(w)]
            if len(w): hs.append(np.percentile(w, 50))
    if len(hs) < 30: return None, None
    hs = np.array(hs); q = np.percentile(hs, [25, 50, 75])
    return float(q[1]), f"median of {len(hs)} edge samples, middle half {q[0]:+.2f}…{q[2]:+.2f} m"


if __name__ == "__main__":
    {"reflections": reflections, "model3d": model3d}[sys.argv[1]](sys.argv[2])
