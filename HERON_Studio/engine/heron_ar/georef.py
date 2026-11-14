"""Turn the COLMAP reconstruction of the GoPro clip into per-frame camera poses in the
local ENU frame used by the overlay.

  * COLMAP keyframes (every 12th frame) give image-consistent orientation + position.
  * A similarity transform (scale, rotation, translation) maps the SfM model onto the
    drone's GPS/EKF track (Umeyama, robust), so the model is metric and georeferenced.
  * In-between frames take their rotation from the GoPro gyro relative to the two
    neighbouring keyframes (blended), so motion stays smooth at 24 fps.
  * The refined fisheye intrinsics from bundle adjustment replace the community profile.
  * Sparse lakebed points give the actual ground/water surface height.
Writes data/solved/<name>.npz
"""
import json, re
import numpy as np, pycolmap
from pathlib import Path
from .scene import Scene, DATA
from .geometry import Fisheye, clip_fps, ref_wh
from .geometry import log_so3, mission_time

def umeyama(src, dst):
    mu_s, mu_d = src.mean(0), dst.mean(0); S, D = src - mu_s, dst - mu_d
    U, sig, Vt = np.linalg.svd(D.T @ S / len(src)); E = np.eye(3)
    if np.linalg.det(U @ Vt) < 0: E[2, 2] = -1
    R = U @ E @ Vt; s = np.trace(np.diag(sig) @ E) / (S ** 2).sum(1).mean()
    return s, R, mu_d - s * R @ mu_s

def _largest(sfm_dir):
    recs = {int(p.name): pycolmap.Reconstruction(str(p)) for p in Path(sfm_dir).iterdir()
            if p.is_dir() and p.name.isdigit() and (p / "images.bin").exists()}
    rec = max(recs.values(), key=lambda r: r.num_reg_images())
    print("models:", {k: r.num_reg_images() for k, r in recs.items()}, "using", rec.num_reg_images(), "images,", rec.num_points3D(), "points")
    return rec

def _poses(rec):
    """Frame numbers, world->camera rotations and camera centres, in frame order."""
    fr, Rcw, C = [], [], []
    for im in rec.images.values():
        if not im.has_pose: continue
        T = im.cam_from_world(); R = T.rotation.matrix()
        fr.append(int(re.findall(r"\d+", im.name)[0])); Rcw.append(R); C.append(-R.T @ T.translation)
    o = np.argsort(fr)
    return np.array(fr)[o], np.array(Rcw)[o], np.array(C)[o]

def _gps(scene, vt, dt): return np.array([scene.tel.position(scene.mission_t(v) + dt)[0] for v in vt])

LATENCIES = np.arange(-1.6, 0.61, 0.05)          # the EKF position stream lags the camera

def _lat_cost(scene, cfg, fps, fr, C, dtp):
    vt = cfg["clip_video_start_s"] + fr / fps; Gt = _gps(scene, vt, dtp)
    s_, R_, t_ = umeyama(C, Gt); return float(np.median(np.linalg.norm((s_ * (R_ @ C.T)).T + t_ - Gt, axis=1)))

def _fit(scene, cfg, fps, fr, C, label="", lat=None):
    """Similarity from camera centres to the GPS track, with the EKF position latency searched (or given)."""
    vt = cfg["clip_video_start_s"] + fr / fps
    if lat is None:
        best = min((_lat_cost(scene, cfg, fps, fr, C, d), d) for d in LATENCIES)
        print(f"{label}position latency: {best[1]:+.2f} s (median fit {best[0]:.2f} m)")
    else: best = (None, lat)
    G = _gps(scene, vt, best[1])
    keep = np.ones(len(fr), bool)               # robust similarity: iterate dropping worst
    for _ in range(5):
        s, Rs, ts = umeyama(C[keep], G[keep]); res = np.linalg.norm((s * (Rs @ C.T)).T + ts - G, axis=1)
        keep = res < max(1.0, 3 * np.median(res[keep]))
    print(f"{label}similarity: scale {s:.4f}; GPS fit residual median {np.median(res[keep]):.2f} m, "
          f"90% {np.percentile(res[keep], 90):.2f} m; used {keep.sum()}/{len(keep)}")
    return s, Rs, ts, res, keep, best[1]

def _bad_fit(s, res, keep):
    """A similarity that collapses the solve onto a point (scale near 0) or leaves large residuals means
    the cameras and the GPS track don't describe the same motion: almost always a wrong clock sync."""
    med = float(np.median(res[keep])) if keep.any() else 1e9
    return s < 1e-3 or med > 6.0 or keep.mean() < 0.4

def _resync(scene, cfg, cfgp, fps, fr, C):
    """Find the clock offset at which the solved camera path best matches the GPS track (independent of
    the vibration sync). If it fits far better, write it to the config and rebuild the scene."""
    t = scene.tel.pt; rate = scene.sync["rate"]
    def fit_at(off, vt, CC):
        mt = mission_time(vt, dict(scene.sync, offset=off)); m = (mt > t[0]) & (mt < t[-1])
        if m.sum() < max(30, 0.5 * len(vt)): return 1e9
        G = np.array([scene.tel.position(x)[0] for x in mt[m]]); s_, R_, t_ = umeyama(CC[m], G)
        if s_ < 1e-3: return 1e9
        return float(np.median(np.linalg.norm((s_ * (R_ @ CC[m].T)).T + t_ - G, axis=1)))
    vt = cfg["clip_video_start_s"] + fr / fps
    sub = slice(None, None, max(1, len(vt) // 600))
    lo, hi = cfg.get("sync_search", [-900, 900])
    coarse = min((fit_at(o, vt[sub], C[sub]), o) for o in np.arange(lo, hi, 2.0))
    best = min((fit_at(o, vt, C), o) for o in np.arange(coarse[1] - 2, coarse[1] + 2, 0.1))
    old = scene.sync["offset"]
    print(f"clock check: the camera path fits the GPS best at offset {best[1]:+.1f} s (median {best[0]:.2f} m); "
          f"the sync step said {old:+.1f} s")
    if abs(best[1] - old) < 1.0: return None
    # A good fit is accepted outright. A loose one (a merged or not yet adjusted model can bend by tens of
    # metres) is accepted only when the sync step's offset is impossible: most of the video outside the log.
    # Seen on the 19 Oct flight with two joined chapters: vibration sync +786.8 s (corr 0.20), camera path -48.3 s.
    if best[0] > 4.0 and not (fit_at(old, vt, C) >= 1e9 and best[0] < 60.0): return None
    p = Path(cfgp); raw = json.loads(p.read_text())
    raw["sync"] = dict(rate=rate, offset=float(best[1]), source="camera path vs GPS (georef)", vibration_offset=old)
    p.write_text(json.dumps(raw, indent=2))
    print(f"clock sync corrected to {best[1]:+.2f} s and written to {p.name}")
    cfg["sync"] = raw["sync"]
    return Scene(cfg)

def stitch_chunks(scene, cfg, fps, dirs, cfgp=None):
    """Cheap merge of a chunked solve: each chunk gets its own similarity to GPS, all with one shared
    position latency; where two chunks overlap, the switch from one to the next is made at the frame that
    keeps the GPS error lowest. Returns the scene too (a clock re-sync rebuilds it).

    Tested on the 19 Oct and 10 Nov flights against a latency per chunk and switching mid-overlap: the
    worst jump in the camera path at a chunk seam went from 6.3 to 2.9 m and from 13 to 3.2 m (a chunk's
    own latency could slide it along the track by ~1 s, and chunk ends are sometimes far off)."""
    chunks = []
    for d in dirs:
        rec = _largest(d); fr, Rcw, C = _poses(rec); chunks.append((rec, fr, Rcw, C))
    def shared_latency():
        n = np.array([len(c[1]) for c in chunks], float)
        cost = [(sum(w * _lat_cost(scene, cfg, fps, fr, C, d) for w, (_, fr, _, C) in zip(n, chunks)) / n.sum(), d)
                for d in LATENCIES]
        return min(cost)
    m, lat = shared_latency()
    print(f"chunks: shared position latency {lat:+.2f} s (median fit {m:.2f} m)")
    fits = [_fit(scene, cfg, fps, fr, C, label=f"chunk {i}: ", lat=lat) for i, (_, fr, _, C) in enumerate(chunks)]
    if cfgp and sum(_bad_fit(f[0], f[3], f[4]) for f in fits) > len(fits) / 2:      # most chunks off: clock sync?
        big = max(range(len(chunks)), key=lambda i: len(chunks[i][1]))
        new = _resync(scene, cfg, cfgp, fps, chunks[big][1], chunks[big][3])
        if new is not None:
            scene = new; m, lat = shared_latency(); print(f"chunks: shared position latency {lat:+.2f} s (median fit {m:.2f} m)")
            fits = [_fit(scene, cfg, fps, fr, C, label=f"chunk {i}: ", lat=lat) for i, (_, fr, _, C) in enumerate(chunks)]
    pieces = []
    for i, ((rec, fr, Rcw, C), (s, Rs, ts, res, keep, _)) in enumerate(zip(chunks, fits)):
        if _bad_fit(s, res, keep): print(f"chunk {i}: doesn't fit the GPS track, left out"); continue
        P = np.array([pt.xyz for pt in rec.points3D.values()])
        pieces.append(dict(fr=fr, Rwc=np.array([Rs @ R.T for R in Rcw]), Cw=(s * (Rs @ C.T)).T + ts, res=res, keep=keep,
                           Pw=(s * (Rs @ P.T)).T + ts, err=np.array([pt.error for pt in rec.points3D.values()]),
                           cam=list(rec.cameras.values())[0], s=s, n=rec.num_reg_images()))
    if not pieces: raise RuntimeError("no chunk of the solve fits the GPS track; check the clock sync")
    pieces.sort(key=lambda pc: pc["fr"][0])
    switch, gap = [], []                        # switch[i]: first frame taken from piece i+1 rather than piece i
    for a, b in zip(pieces, pieces[1:]):
        ia = {f: j for j, f in enumerate(a["fr"])}; ib = {f: j for j, f in enumerate(b["fr"])}
        common = sorted(set(ia) & set(ib))
        if not common: switch.append(b["fr"][0]); continue
        gap += [np.linalg.norm(a["Cw"][ia[f]] - b["Cw"][ib[f]]) for f in common]
        ea = np.minimum([a["res"][ia[f]] for f in common], 20.0); eb = np.minimum([b["res"][ib[f]] for f in common], 20.0)
        cost = np.r_[0, np.cumsum(ea)] + np.r_[np.cumsum(eb[::-1])[::-1], 0]      # a up to k, b from k on
        k = int(np.argmin(cost)); switch.append(common[k] if k < len(common) else common[-1] + 1)
    sel = []
    for i, pc in enumerate(pieces):
        lo = switch[i - 1] if i else -np.inf; hi = switch[i] if i < len(switch) else np.inf
        sel += [(f, i, j) for j, f in enumerate(pc["fr"]) if lo <= f < hi]
    sel.sort(); fr = np.array([f for f, _, _ in sel])
    Rwc = np.array([pieces[i]["Rwc"][j] for _, i, j in sel]); Cw = np.array([pieces[i]["Cw"][j] for _, i, j in sel])
    res = np.array([pieces[i]["res"][j] for _, i, j in sel]); keep = np.array([pieces[i]["keep"][j] for _, i, j in sel])
    if gap: print(f"chunk overlaps: {len(gap)} frames solved twice, position disagreement median {np.median(gap):.2f} m, "
                  f"90% {np.percentile(gap, 90):.2f} m")
    print(f"stitched {len(fr)} frames from {len(pieces)} chunks; GPS fit residual median {np.median(res[keep]):.2f} m, "
          f"90% {np.percentile(res[keep], 90):.2f} m")
    big = max(pieces, key=lambda pc: pc["n"])
    return (fr, Rwc, Cw, res, keep, np.vstack([pc["Pw"] for pc in pieces]), np.concatenate([pc["err"] for pc in pieces]),
            big["cam"], float(np.median([pc["s"] for pc in pieces])), scene)

def main(sfm_dir, cfgp, out):
    """sfm_dir: COLMAP sparse folder (with 0/, 1/ ... models). out: .npz consumed by the renderer."""
    from .geometry import load_config, resolve
    cfg = load_config(cfgp); cfg.pop("sfm_poses", None); scene = Scene(cfg); fps = clip_fps(cfg)
    out = resolve(out); out.parent.mkdir(parents=True, exist_ok=True)
    chunks = Path(sfm_dir) / "chunks.json"
    if chunks.exists():                         # chunked solve, cheap merge: tie each chunk to GPS and stitch
        dirs = [Path(sfm_dir) / d for d in json.loads(chunks.read_text())["chunks"]]
        fr, Rwc, Cw, res, keep, Pw, err, cam, s, scene = stitch_chunks(scene, cfg, fps, dirs, cfgp)
    else:
        rec = _largest(sfm_dir)
        fr, Rcw, C = _poses(rec)
        s, Rs, ts, res, keep, lat = _fit(scene, cfg, fps, fr, C)
        if _bad_fit(s, res, keep):
            scene = _resync(scene, cfg, cfgp, fps, fr, C) or scene
            s, Rs, ts, res, keep, lat = _fit(scene, cfg, fps, fr, C)
        Cw = (s * (Rs @ C.T)).T + ts
        Rwc = np.array([Rs @ R.T for R in Rcw])                                   # camera->world
        cam = list(rec.cameras.values())[0]
        P = np.array([pt.xyz for pt in rec.points3D.values()]); Pw = (s * (Rs @ P.T)).T + ts
        err = np.array([pt.error for pt in rec.points3D.values()])
    if s < 1e-3 or not keep.any() or keep.mean() < 0.4:
        raise RuntimeError(f"the solve doesn't fit the GPS track (scale {s:.4g}, median {np.median(res[keep]):.2f} m). "
                           "Check the clock sync (Sync clocks result) and that the log is from this flight.")
    if np.median(res[keep]) > 6.0:
        print(f"WARNING: GPS fit median {np.median(res[keep]):.2f} m is poor; check the clock sync and the solve")
    vt = cfg["clip_video_start_s"] + fr / fps
    # intrinsics (refined) scaled to the 1920 render width
    p = np.array(cam.params); k = 1920 / cam.width
    print("refined camera:", cam.model, (p[:4] * k).round(1), p[4:].round(4))
    near = (np.linalg.norm(Pw[:, :2] - Cw.mean(0)[:2], axis=1) < 400) & (err < 2)
    zq = np.percentile(Pw[near, 2], [5, 25, 50, 75, 95])
    print("surface height of sparse points relative to configured plane (m): p5/25/50/75/95", zq.round(2))
    # in-pool points only (lakebed), low percentile ~ water level near shore
    import cv2
    pool = scene.pool.astype(np.float32)
    inpool = np.array([cv2.pointPolygonTest(pool, (float(x), float(y)), False) >= 0 for x, y in Pw[near, :2]])
    zl = np.percentile(Pw[near][inpool, 2], [2, 10, 50]) if inpool.sum() > 50 else zq[:3]
    print("lakebed (inside mapped full pool) heights p2/p10/p50:", np.round(zl, 2))
    # gyro-relative IMU rotations at keyframes for interpolation
    Rimu_k = np.array([scene.pose_imu(v, Fisheye(DATA / cfg["lens_profile"], *ref_wh(cfg)))[1].R for v in vt])
    np.savez(out, frames=fr, vt=vt, Rwc=Rwc, Cw=Cw, Rimu=Rimu_k, cam_params=np.r_[p[:4] * k, p[4:]],
             cam_model=cam.model.name if hasattr(cam.model, "name") else str(cam.model), sim_scale=s, gps_res=res,
             lakebed_p2=zl[0], lakebed_p10=zl[1], lakebed_p50=zl[2], surface_q=zq, points=Pw[near])
    # orientation difference SfM vs IMU (what the IMU got wrong)
    d = np.array([np.degrees(log_so3(Ri.T @ Rs_)) for Ri, Rs_ in zip(Rimu_k, Rwc)])
    print("IMU vs image-solved rotation (deg, camera axes x/y/z): mean", d.mean(0).round(2), " std", d.std(0).round(2), " max", np.abs(d).max(0).round(2))

