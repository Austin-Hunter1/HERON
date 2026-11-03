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
import json, re, sys
import numpy as np, pycolmap
from pathlib import Path
from .scene import Scene, DATA
from .geometry import Fisheye, expm_so3, interp_rot, clip_fps, ref_wh
from .geometry import log_so3

def umeyama(src, dst):
    mu_s, mu_d = src.mean(0), dst.mean(0); S, D = src - mu_s, dst - mu_d
    U, sig, Vt = np.linalg.svd(D.T @ S / len(src)); E = np.eye(3)
    if np.linalg.det(U @ Vt) < 0: E[2, 2] = -1
    R = U @ E @ Vt; s = np.trace(np.diag(sig) @ E) / (S ** 2).sum(1).mean()
    return s, R, mu_d - s * R @ mu_s

def main(sfm_dir, cfgp, out):
    """sfm_dir: COLMAP sparse folder (with 0/, 1/ ... models). out: .npz consumed by the renderer."""
    from .geometry import load_config, resolve
    cfg = load_config(cfgp); cfg.pop("sfm_poses", None); scene = Scene(cfg); fps = clip_fps(cfg)
    out = resolve(out); out.parent.mkdir(parents=True, exist_ok=True)
    recs = {int(p.name): pycolmap.Reconstruction(str(p)) for p in Path(sfm_dir).iterdir() if p.is_dir()}
    rec = max(recs.values(), key=lambda r: r.num_reg_images())
    print("models:", {k: r.num_reg_images() for k, r in recs.items()}, "using", rec.num_reg_images(), "images,", rec.num_points3D(), "points")
    fr, Rcw, C = [], [], []
    for im in rec.images.values():
        if not im.has_pose: continue
        f = int(re.findall(r"\d+", im.name)[0]); T = im.cam_from_world()
        R = T.rotation.matrix(); t = T.translation
        fr.append(f); Rcw.append(R); C.append(-R.T @ t)
    o = np.argsort(fr); fr = np.array(fr)[o]; Rcw = np.array(Rcw)[o]; C = np.array(C)[o]
    vt = cfg["clip_video_start_s"] + fr / fps
    # the EKF position stream lags the camera; solve that latency together with the similarity
    best = None
    for dtp in np.arange(-1.6, 0.61, 0.05):
        Gt = np.array([scene.tel.position(scene.mission_t(v) + dtp)[0] for v in vt])
        s_, R_, t_ = umeyama(C, Gt); r_ = np.median(np.linalg.norm((s_ * (R_ @ C.T)).T + t_ - Gt, axis=1))
        if best is None or r_ < best[0]: best = (r_, dtp)
    print(f"position latency: {best[1]:+.2f} s (median fit {best[0]:.2f} m)")
    G = np.array([scene.tel.position(scene.mission_t(v) + best[1])[0] for v in vt])
    # robust similarity: iterate dropping worst
    keep = np.ones(len(fr), bool)
    for _ in range(5):
        s, Rs, ts = umeyama(C[keep], G[keep]); res = np.linalg.norm((s * (Rs @ C.T)).T + ts - G, axis=1)
        keep = res < max(1.0, 3 * np.median(res[keep]))
    print(f"similarity: scale {s:.4f}; GPS fit residual median {np.median(res[keep]):.2f} m, 90% {np.percentile(res[keep], 90):.2f} m; used {keep.sum()}/{len(keep)}")
    Cw = (s * (Rs @ C.T)).T + ts
    Rwc = np.array([Rs @ R.T for R in Rcw])                                       # camera->world
    # intrinsics (refined) scaled to the 1920 render width
    cam = list(rec.cameras.values())[0]; p = np.array(cam.params); k = 1920 / cam.width
    print("refined camera:", cam.model, (p[:4] * k).round(1), p[4:].round(4))
    # ground points -> surface height
    P = np.array([pt.xyz for pt in rec.points3D.values()]); Pw = (s * (Rs @ P.T)).T + ts
    err = np.array([pt.error for pt in rec.points3D.values()])
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

