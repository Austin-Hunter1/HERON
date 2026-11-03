"""Pipeline figures: one PNG per processing step, written to docs/figures/.

    python heron.py figures --config configs/survey_1080p.json [--only 1,5,9]
"""
from __future__ import annotations
import math
from pathlib import Path
import numpy as np, cv2, pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.patches import FancyBboxPatch, Polygon as MplPolygon
from scipy.signal import butter, filtfilt

from .geometry import (GoProIMU, Fisheye, CameraPose, fresnel_zone, sun_enu_dir, load_config, resolve, log_so3,
                       DATA, CACHE, MISSION_T0_US)

# ------------------------------------------------------------------ style (validated categorical slots)
SURF, INK, INK2, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#8a8984", "#e6e5e0"
BLUE, ORANGE, AQUA, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
WATER_FILL, TURN_FILL = "#dcebf9", "#f1efe8"
CONST = {"G": (BLUE, "GPS L5"), "E": (ORANGE, "Galileo E5a"), "C": (AQUA, "BeiDou B2a"), "J": (YELLOW, "QZSS L5")}
SEQ = LinearSegmentedColormap.from_list("seq_blue", ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"])
FPS = 24000 / 1001

plt.rcParams.update({
    "figure.facecolor": SURF, "axes.facecolor": SURF, "savefig.facecolor": SURF,
    "font.family": "DejaVu Sans", "font.size": 10.5, "axes.titlesize": 12, "axes.titleweight": "semibold",
    "axes.titlelocation": "left", "axes.titlepad": 10, "axes.labelcolor": INK2, "axes.edgecolor": GRID,
    "axes.labelsize": 10, "xtick.color": INK2, "ytick.color": INK2, "xtick.labelsize": 9, "ytick.labelsize": 9,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.7, "axes.spines.top": False, "axes.spines.right": False,
    "legend.frameon": False, "legend.fontsize": 9, "lines.linewidth": 2, "axes.axisbelow": True})

def _fig(w, h, title, subtitle=None):
    f = plt.figure(figsize=(w, h), dpi=150)
    import os
    if os.environ.get("HERON_FIG_NOTITLE"): return f
    f.text(0.012, 0.985, title, fontsize=16, fontweight="bold", color=INK, va="top")
    if subtitle:
        import textwrap
        f.text(0.012, 0.985 - 0.30 / h, "\n".join(textwrap.wrap(subtitle, int(w * 12.6))), fontsize=10.5, color=INK2, va="top", linespacing=1.4)
    return f

def _save(f, out, name):
    p = Path(out) / name; f.savefig(p, dpi=150); plt.close(f); print("  wrote", p); return p

def _img_ax(ax, img_bgr, title=None):
    ax.imshow(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)); ax.set_axis_off()
    if title: ax.set_title(title, loc="left", fontsize=11)

def _turns(scene):
    try:
        T = pd.read_csv(DATA / "turn_intervals.csv")
        return T[["turn_zone_start_elapsed_s", "turn_zone_end_elapsed_s"]].values - 0.545
    except Exception: return np.zeros((0, 2))

def _shade_turns(ax, scene, to_x, label=True):
    for i, (a, b) in enumerate(_turns(scene)):
        ax.axvspan(to_x(a), to_x(b), color=TURN_FILL, zorder=0, lw=0, label="turn" if (label and i == 0) else None)

class Ctx:
    """Everything the figures share (scene, lens, video), built once."""
    def __init__(self, config, work):
        from .scene import Scene
        self.cfg = load_config(config); self.scene = Scene(self.cfg); self.work = Path(work)
        c = self.cfg; self.W, self.H = c["output_width"], c["output_height"]
        self.lens = Fisheye(DATA / c["lens_profile"], self.W, self.H, c.get("focal_scale", 1.0))
        if getattr(self.scene, "sfm_cam", None) is not None:
            p = self.scene.sfm_cam; self.lens.fx, self.lens.fy, self.lens.cx, self.lens.cy = p[:4]; self.lens.D = np.array(p[4:8])
        self.video = resolve(c["clip_path"]); self.t0 = c["clip_video_start_s"]
        self.sfm = np.load(DATA / c["sfm_poses"]) if c.get("sfm_poses") else None
    def frame(self, clip_s):
        cap = cv2.VideoCapture(str(self.video)); cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(clip_s * FPS))); ok, fr = cap.read()
        if not ok: raise RuntimeError(f"cannot read {self.video} at {clip_s}s")
        return cv2.resize(fr, (self.W, self.H), interpolation=cv2.INTER_AREA) if fr.shape[1] != self.W else fr
    def pose(self, clip_s): return self.scene.pose(self.t0 + clip_s, self.lens)
    def watermap(self):
        from .watermap import WaterMap
        p = CACHE / f"watermap_{Path(self.cfg['clip_path']).stem}{self.cfg.get('watermap_tag', '')}.npz"
        return WaterMap.load(p) if p.exists() else None
    def model(self):
        import pycolmap
        sp = self.work / "sparse"; recs = [pycolmap.Reconstruction(str(p)) for p in sp.iterdir() if p.is_dir()]
        return max(recs, key=lambda r: r.num_reg_images())

# ================================================================== 0. overview
def fig00_pipeline(cx, out):
    f = _fig(13, 3.6, "HERON AR pipeline", "Each box is a module; arrows are the data handed to the next step.")
    ax = f.add_axes([0.01, 0.02, 0.98, 0.76]); ax.set_xlim(0, 13); ax.set_ylim(0, 3); ax.set_axis_off()
    steps = [("1  Clock sync", "GoPro accel vs\ndrone IMU", "sync.rate / offset", BLUE),
             ("2  Attitude", "GoPro gyro +\naccel filter", "R(t) at 200 Hz", BLUE),
             ("3  COLMAP SfM", "SIFT, sequential\nmatching, BA", "poses + 3D points", ORANGE),
             ("4  Georeference", "similarity fit to\nGPS track", "metric ENU poses", ORANGE),
             ("5  Surface", "terrain from points\n+ water map", "z(x,y), P(water)", AQUA),
             ("6  Reflection", "satellites, specular\npt, Fresnel zone", "zones on ground", AQUA),
             ("7  Render", "per-pixel paint,\nwater refine, HUD", "MP4 + CSV", YELLOW)]
    bw, gap = 1.62, 0.21
    for i, (t, d, o, c) in enumerate(steps):
        x = 0.08 + i * (bw + gap)
        ax.add_patch(FancyBboxPatch((x, 0.95), bw, 1.75, boxstyle="round,pad=0,rounding_size=0.12", fc="white", ec=GRID, lw=1.2))
        ax.add_patch(FancyBboxPatch((x, 2.55), bw, 0.15, boxstyle="round,pad=0,rounding_size=0.06", fc=c, ec="none"))
        ax.text(x + 0.12, 2.28, t, fontsize=11, fontweight="bold", color=INK, va="center")
        ax.text(x + 0.12, 1.72, d, fontsize=9.5, color=INK2, va="center", linespacing=1.35)
        ax.text(x + 0.12, 1.14, o, fontsize=8.6, color=INK, va="center", family="DejaVu Sans Mono")
        if i < len(steps) - 1:
            ax.annotate("", xy=(x + bw + gap - 0.02, 1.8), xytext=(x + bw + 0.02, 1.8),
                        arrowprops=dict(arrowstyle="-|>", color=MUTED, lw=1.4, mutation_scale=12))
    ax.text(0.08, 0.45, "inputs:  GoPro video + GPMF (GYRO, ACCL)  ·  ArduPilot log (POS, ATT, IMU, XKF1)  ·  "
            "GNSS almanacs / TLE  ·  lens profile  ·  reservoir outline", fontsize=9.5, color=INK2)
    return _save(f, out, "fig00_pipeline.png")

# ================================================================== 1. clock sync
def fig01_sync(cx, out):
    imu = cx.scene.imu; sync = cx.scene.sync
    I = pd.read_csv(DATA / "IMU.csv"); I = I[I.I == 0]
    It = (I.TimeUS.values - MISSION_T0_US) / 1e6; IA = I[["AccX", "AccY", "AccZ"]].values
    fs = 50.0; tg = np.arange(0, imu.t[-1], 1 / fs)
    gm = np.linalg.norm(np.c_[[np.interp(tg, imu.t, imu.acc[:, k]) for k in range(3)]].T, axis=1)
    td = np.arange(It[0], It[-1], 1 / fs)
    dm = np.linalg.norm(np.c_[[np.interp(td, It, IA[:, k]) for k in range(3)]].T, axis=1)
    bp = butter(2, [0.3 / (fs / 2), 4 / (fs / 2)], "band"); gmf = filtfilt(*bp, gm); dmf = filtfilt(*bp, dm)
    search = tuple(cx.cfg.get("sync_search", (-120, 0)))
    offs = np.arange(search[0], search[1], 0.1)
    cc = np.array([np.corrcoef(np.interp(tg + o, td, dmf), gmf)[0, 1] for o in offs]); o0 = offs[cc.argmax()]
    rows = []
    for s0 in np.arange(0, tg[-1] - 40, 10):
        m = (tg >= s0) & (tg < s0 + 40)
        c2 = [(np.corrcoef(np.interp(tg[m] + o, td, dmf), gmf[m])[0, 1], o) for o in np.arange(o0 - .6, o0 + .6, .005)]
        c, o = max(c2); rows.append((s0 + 20, o, c))
    r = np.array(rows)

    f = _fig(13, 8.2, "Step 1 · Clock sync: GoPro time → flight-log time",
             "The GoPro has its own clock. Its accelerometer feels the same airframe vibration as the autopilot IMU, "
             "so the two |a| signals are cross-correlated to find the mapping.")
    gs = f.add_gridspec(2, 2, left=0.06, right=0.985, top=0.86, bottom=0.08, hspace=0.42, wspace=0.18, height_ratios=[1, 1])
    ax = f.add_subplot(gs[0, :])
    vt0 = cx.t0 + 40; m = (tg > vt0) & (tg < vt0 + 12); mt = tg[m] * (1 + sync["rate"]) + sync["offset"]
    zn = lambda x: (x - x.mean()) / x.std()
    ax.plot(mt, zn(np.interp(mt, td, dmf)) + 5, color=ORANGE, lw=1.5)
    ax.plot(mt, zn(gmf[m]), color=BLUE, lw=1.5)
    ax.text(mt[0], 5 + 3.2, "drone IMU |a|   (mission clock)", color=INK, fontsize=9.5, fontweight="bold")
    ax.text(mt[0], 3.2, "GoPro ACCL |a|   (video clock → mission clock with the fit)", color=INK, fontsize=9.5, fontweight="bold")
    ax.set_yticks([]); ax.set_ylim(-4, 9.5); ax.set_xlabel("mission time (s)")
    ax.set_title("A · After alignment, 0.3–4 Hz band, normalised: the vibration bumps line up")
    ax = f.add_subplot(gs[1, 0])
    ax.plot(offs, cc, color=BLUE, lw=1.6)
    ax.axvline(o0, color=INK2, lw=1, ls=(0, (3, 3)))
    ax.annotate(f"peak at {o0:+.1f} s\nr = {cc.max():.2f}", (o0, cc.max()), xytext=(12, -8), textcoords="offset points", fontsize=9.5, color=INK)
    ax.set_xlabel("trial offset  mission − video (s)"); ax.set_ylabel("correlation"); ax.set_title("B · Coarse search over the whole recording")
    ax = f.add_subplot(gs[1, 1])
    fit = np.polyval([sync["rate"], sync["offset"]], r[:, 0])
    ax.scatter(r[:, 0], (r[:, 1] - fit) * 1e3, s=12 + 60 * np.clip(r[:, 2], 0, 1) ** 2, color=BLUE, edgecolor=SURF, lw=1, zorder=3, label="40 s window (dot size = correlation; fit weight ∝ r⁴)")
    ax.axhline(0, color=ORANGE, lw=2, label=f"linear fit: drift {sync['rate'] * 1e3:+.3f} ms per s")
    ax.set_xlabel("video time (s)"); ax.set_ylabel("offset − fit (ms)"); ax.set_title("C · Local offset in sliding windows → offset + clock drift")
    ax.legend(loc="lower left"); ax.set_ylim(-250, 250)
    return _save(f, out, "fig01_clock_sync.png")

# ================================================================== 2. attitude
def _angles(R):
    """heading (deg from north), depression of the optical axis below horizon (deg), roll (deg)."""
    fw = R[..., :, 2]; xr = R[..., :, 0]
    hd = np.degrees(np.arctan2(fw[..., 0], fw[..., 1])); dep = -np.degrees(np.arcsin(np.clip(fw[..., 2], -1, 1)))
    roll = np.degrees(np.arcsin(np.clip(xr[..., 2], -1, 1)))
    return np.unwrap(hd, period=360), dep, roll

def fig02_attitude(cx, out):
    sc = cx.scene; z = cx.sfm
    ts = np.arange(0, cx.cfg["clip_duration_s"], 0.1)
    Ri = np.array([sc.pose_imu(cx.t0 + t, cx.lens)[1].R for t in ts])
    hi, di, ri = _angles(Ri)
    kt = z["vt"] - cx.t0; hs, ds, rs = _angles(z["Rwc"])
    hs = hs + 360 * np.round((np.interp(kt, ts, hi) - hs) / 360)
    yaw = np.degrees(sc.tel.yaw_at(sc.mission_t(cx.t0 + ts)))
    yaw = yaw + 360 * np.round((hi[0] - yaw[0]) / 360)
    err = np.array([np.degrees(np.linalg.norm(log_so3(a.T @ b))) for a, b in zip(z["Rimu"], z["Rwc"])])
    mt0 = sc.mission_t(cx.t0); tx = lambda m: (m - mt0) / (1 + sc.sync["rate"])
    f = _fig(13, 9.6, "Step 2 · Camera attitude assembled from the GoPro gyro",
             "A complementary filter integrates the 200 Hz gyro, corrects tilt with gravity + the EKF's own acceleration, and pulls heading "
             "slowly to the aircraft yaw. Dots: COLMAP's image-solved camera at keyframes.")
    gs = f.add_gridspec(4, 1, left=0.07, right=0.985, top=0.885, bottom=0.06, hspace=0.38)
    panels = [("Heading of the optical axis (°)", hi, hs, yaw), ("Camera tilt below horizon (°)", di, ds, None), ("Roll (°)", ri, rs, None)]
    for k, (ttl, a, b, ref) in enumerate(panels):
        ax = f.add_subplot(gs[k]); _shade_turns(ax, sc, tx, label=(k == 0))
        if ref is not None: ax.plot(ts, ref, color=MUTED, lw=1.2, ls=(0, (4, 2)), label="aircraft yaw (log)")
        ax.plot(ts, a, color=BLUE, lw=1.6, label="gyro filter")
        ax.scatter(kt, b, s=9, color=ORANGE, edgecolor=SURF, lw=0.6, zorder=3, label="COLMAP keyframe")
        ax.set_title(ttl); ax.set_xlim(0, ts[-1])
        if k == 0: ax.legend(loc="upper left", ncol=4)
    ax = f.add_subplot(gs[3]); _shade_turns(ax, sc, tx, label=False)
    ax.plot(kt, err, color=ORANGE, lw=1.6)
    ax.set_title(f"Gyro-only vs image-solved rotation (°): median {np.median(err):.2f}°, max {err.max():.1f}° — the part COLMAP fixes")
    ax.set_xlim(0, ts[-1]); ax.set_xlabel("clip time (s)   ·   shaded = turns")
    return _save(f, out, "fig02_camera_attitude.png")

# ================================================================== 3. COLMAP features / tracks
def fig03_features(cx, out):
    rec = cx.model(); ims = sorted([im for im in rec.images.values() if im.has_pose], key=lambda i: i.name)
    k = len(ims) // 3; A, B = ims[k], ims[k + 1]
    ia = cv2.imread(str(cx.work / "images" / A.name)); ib = cv2.imread(str(cx.work / "images" / B.name))
    pa = {p.point3D_id: p.xy for p in A.points2D if p.has_point3D()}; pb = {p.point3D_id: p.xy for p in B.points2D if p.has_point3D()}
    shared = [i for i in pa if i in pb]
    f = _fig(13, 9.4, "Step 3 · COLMAP: features → matches → tracks",
             f"SIFT keypoints on every keyframe ({cx.cfg['sfm']['frame_step']}-frame spacing here). "
             f"Matched across neighbours, each track becomes one 3D point. {len(shared)} tracks are shared by these two frames.")
    gs = f.add_gridspec(2, 2, left=0.01, right=0.99, top=0.88, bottom=0.02, hspace=0.12, wspace=0.03)
    ax = f.add_subplot(gs[0, 0]); _img_ax(ax, ia, f"A · {A.name}: all {len(A.points2D)} keypoints (blue = in a 3D track)")
    kp = np.array([p.xy for p in A.points2D]); tri = np.array([p.has_point3D() for p in A.points2D])
    ax.scatter(kp[~tri, 0], kp[~tri, 1], s=3, color="#ffffff", alpha=0.75, lw=0); ax.scatter(kp[tri, 0], kp[tri, 1], s=4, color=BLUE, lw=0)
    ax = f.add_subplot(gs[0, 1]); _img_ax(ax, ib, f"B · next keyframe {B.name}")
    ax.scatter([pb[i][0] for i in shared], [pb[i][1] for i in shared], s=4, color=BLUE, lw=0)
    ax = f.add_subplot(gs[1, 0]); n = 6; seq = ims[k:k + n]; ic = cv2.imread(str(cx.work / "images" / seq[-1].name))
    _img_ax(ax, ic, f"C · Tracks over {n} keyframes (~{n * cx.cfg['sfm']['frame_step'] / FPS:.0f} s), ending at {seq[-1].name}")
    obs = [{p.point3D_id: p.xy for p in im.points2D if p.has_point3D()} for im in seq]
    full = [i for i in obs[-1] if all(i in o for o in obs)]
    for i in full[::2]:
        P = np.array([o[i] for o in obs]); ax.plot(P[:, 0], P[:, 1], color=YELLOW, lw=1.1, alpha=0.95); ax.scatter(*P[-1], s=5, color=YELLOW, lw=0)
    ax.text(0.01, 0.02, f"{len(full)} points tracked through all {n} frames", transform=ax.transAxes, color="white", fontsize=9.5, fontweight="bold")
    ax = f.add_subplot(gs[1, 1]); pos = ax.get_position(); ax.set_position([pos.x0 + 0.05, pos.y0 + 0.06, pos.width - 0.07, pos.height - 0.1])
    tl = np.array([p.track.length() for p in rec.points3D.values()])
    ax.hist(np.clip(tl, 2, 40), bins=np.arange(2, 42), color=BLUE, edgecolor=SURF, lw=0.8)
    ax.set_xlabel("track length (keyframes a point is seen in; ≥40 lumped)"); ax.set_ylabel("3D points")
    ax.set_title(f"D · Track lengths: median {np.median(tl):.0f} frames, {rec.num_points3D():,} points total")
    return _save(f, out, "fig03_colmap_features.png")

# ================================================================== 4. COLMAP sparse model (raw)
def _frustum(ax, C, R, s, color):
    corners = np.array([[-1, -0.56, 1], [1, -0.56, 1], [1, 0.56, 1], [-1, 0.56, 1]]) * s
    P = C + corners @ R.T
    for p in P: ax.plot(*np.c_[C, p], color=color, lw=0.6)
    ax.plot(*np.r_[P, P[:1]].T, color=color, lw=0.6)

def fig04_sparse(cx, out):
    rec = cx.model()
    P = np.array([p.xyz for p in rec.points3D.values()]); col = np.array([p.color for p in rec.points3D.values()]) / 255
    err = np.array([p.error for p in rec.points3D.values()])
    ims = sorted([im for im in rec.images.values() if im.has_pose], key=lambda i: i.name)
    C = np.array([im.projection_center() for im in ims]); Rcw = np.array([im.cam_from_world().rotation.matrix() for im in ims])
    # express in a level frame for viewing only: ground plane = best-fit plane of the points (PCA), no scale change
    mu = np.median(P, 0); keep = (np.linalg.norm(P - mu, axis=1) < np.percentile(np.linalg.norm(P - mu, axis=1), 92)) & (err < 2)
    U, S, Vt = np.linalg.svd(P[keep] - mu, full_matrices=False); Rv = Vt
    if np.dot(Rv[2], C.mean(0) - mu) < 0: Rv[2] *= -1
    if np.linalg.det(Rv) < 0: Rv[1] *= -1
    Pv = (P - mu) @ Rv.T; Cv = (C - mu) @ Rv.T
    f = _fig(13, 6.6, "Step 3b · The raw COLMAP reconstruction",
             f"{rec.num_reg_images()} cameras, {rec.num_points3D():,} points, mean reprojection error "
             f"{rec.compute_mean_reprojection_error():.2f} px. Units and orientation are arbitrary until step 4.")
    ax = f.add_axes([0.0, -0.02, 0.62, 0.92], projection="3d"); ax.set_facecolor(SURF)
    sel = keep & (np.random.default_rng(0).random(len(P)) < 0.6)
    ax.scatter(*Pv[sel].T, c=col[sel], s=0.6, lw=0, depthshade=False)
    ax.plot(*Cv.T, color=ORANGE, lw=1.6)
    for i in range(0, len(ims), 18): _frustum(ax, Cv[i], Rv @ Rcw[i].T, 0.022 * np.ptp(Cv[:, 0]), ORANGE)
    ax.view_init(elev=24, azim=-72); ax.set_axis_off()
    lo = np.minimum(np.percentile(Pv[sel], 1, 0), Cv.min(0)); hi = np.maximum(np.percentile(Pv[sel], 99, 0), Cv.max(0))
    ax.set_xlim(lo[0], hi[0]); ax.set_ylim(lo[1], hi[1]); ax.set_zlim(lo[2], hi[2]); ax.set_box_aspect(hi - lo, zoom=0.92)
    ax.text2D(0.07, 0.78, "A · 3D view: points in their photo colour, camera path + frustums in orange", transform=ax.transAxes,
              fontsize=12, fontweight="semibold", color=INK)
    ax = f.add_axes([0.64, 0.12, 0.34, 0.66]); ax.set_aspect("equal")
    ax.scatter(Pv[sel, 0], Pv[sel, 1], c=col[sel], s=0.5, lw=0, rasterized=True)
    ax.plot(Cv[:, 0], Cv[:, 1], color=ORANGE, lw=1.8)
    ax.scatter(*Cv[0, :2], color=ORANGE, s=30, zorder=3); ax.annotate("start", Cv[0, :2], xytext=(6, 4), textcoords="offset points", fontsize=9)
    ax.set_title("B · Top view (model units)"); ax.set_xlabel("model x"); ax.set_ylabel("model y")
    return _save(f, out, "fig04_colmap_sparse.png")

# ================================================================== 5. georeference
def fig05_georef(cx, out):
    from .georef import umeyama
    sc = cx.scene; z = cx.sfm; rec = cx.model()
    ims = sorted([im for im in rec.images.values() if im.has_pose], key=lambda i: int("".join(ch for ch in i.name if ch.isdigit())))
    Craw = np.array([im.projection_center() for im in ims]); vt = z["vt"]
    lat = np.arange(-1.6, 0.61, 0.05); med = []
    for d in lat:
        G = np.array([sc.tel.position(sc.mission_t(v) + d)[0] for v in vt]); s_, R_, t_ = umeyama(Craw, G)
        med.append(np.median(np.linalg.norm((s_ * (R_ @ Craw.T)).T + t_ - G, axis=1)))
    med = np.array(med); best = lat[med.argmin()]
    G = np.array([sc.tel.position(sc.mission_t(v) + best)[0] for v in vt]); Cw = z["Cw"]; res = np.linalg.norm(Cw - G, axis=1)
    f = _fig(13, 8.0, "Step 4 · Georeference: COLMAP model → GPS frame",
             "One similarity transform (scale, rotation, shift) maps every SfM camera onto the drone's EKF track, "
             "jointly with the log's position latency. The model becomes metric, north-up, in metres above the water.")
    gs = f.add_gridspec(2, 2, left=0.06, right=0.985, top=0.86, bottom=0.08, wspace=0.2, hspace=0.45, width_ratios=[1.5, 1])
    ax = f.add_subplot(gs[:, 0]); ax.set_aspect("equal")
    ax.add_patch(MplPolygon(sc.pool, closed=True, fc=WATER_FILL, ec="#9ec5f4", lw=1, label="reservoir (OSM)"))
    pts = z["points"]; ax.scatter(pts[::3, 0], pts[::3, 1], s=0.4, color="#b9b8b1", lw=0, rasterized=True, label="SfM points")
    trk = sc.track; ax.plot(trk[:, 0], trk[:, 1], color=MUTED, lw=1, ls=(0, (3, 2)), label="full flight (EKF)")
    ax.plot(G[:, 0], G[:, 1], color=BLUE, lw=2.2, label="GPS/EKF during clip")
    ax.scatter(Cw[:, 0], Cw[:, 1], s=4, color=ORANGE, edgecolor=SURF, lw=0.4, zorder=3, label="SfM cameras after fit")
    pad = 170; ax.set_xlim(G[:, 0].min() - pad, G[:, 0].max() + pad); ax.set_ylim(G[:, 1].min() - pad, G[:, 1].max() + pad)
    ax.set_xlabel("east (m)"); ax.set_ylabel("north (m)"); ax.set_title("A · Top-down, local ENU"); ax.legend(loc="lower left", fontsize=8.5)
    ax = f.add_subplot(gs[0, 1])
    ax.plot(lat, med, color=BLUE, lw=1.8); ax.scatter([best], [med.min()], color=ORANGE, s=30, zorder=3)
    ax.annotate(f"{best:+.2f} s → {med.min():.2f} m", (best, med.min()), xytext=(8, 10), textcoords="offset points", fontsize=9.5)
    ax.set_xlabel("position-log latency (s)"); ax.set_ylabel("median fit error (m)"); ax.set_title("B · Latency search")
    ax = f.add_subplot(gs[1, 1])
    ax.hist(res, bins=30, color=BLUE, edgecolor=SURF, lw=1)
    ax.axvline(np.median(res), color=INK2, lw=1, ls=(0, (3, 3)))
    ax.annotate(f"median {np.median(res):.2f} m\n90% {np.percentile(res, 90):.2f} m", (np.median(res), ax.get_ylim()[1] * 0.85), xytext=(8, 0),
                textcoords="offset points", fontsize=9.5, va="top")
    ax.set_xlabel("camera − GPS distance (m)"); ax.set_ylabel("keyframes"); ax.set_title(f"C · Residuals (scale {float(z['sim_scale']):.2f} m per model unit)")
    return _save(f, out, "fig05_georeference.png")

# ================================================================== 6. terrain
def fig06_terrain(cx, out):
    sc = cx.scene; T = sc.terrain; z = cx.sfm
    ext = [T.x0, T.x0 + T.W * T.cell, T.y0, T.y0 + T.H * T.cell]
    P = z["points"]; ix = ((P[:, 0] - T.x0) / T.cell).astype(int); iy = ((P[:, 1] - T.y0) / T.cell).astype(int)
    ok = (ix >= 0) & (ix < T.W) & (iy >= 0) & (iy < T.H); sup = np.zeros((T.H, T.W), np.uint8); sup[iy[ok], ix[ok]] = 1
    sup = cv2.dilate(sup, np.ones((5, 5), np.uint8)) > 0                      # cells with SfM points nearby
    wet = (T.z <= T.water_z + 0.05) & sup
    Z = np.ma.masked_where(~sup | wet, T.z)
    f = _fig(13, 7.2, "Step 5a · The reflecting surface",
             "SfM points (metres, after step 4) are binned into a 4 m height field. It is clamped at the measured water level, so "
             "open water is flat and the shore rises above it. Specular points and zones are placed on this surface.")
    gs = f.add_gridspec(1, 2, left=0.06, right=0.97, top=0.84, bottom=0.1, wspace=0.22, width_ratios=[1.35, 1])
    ax = f.add_subplot(gs[0]); ax.set_aspect("equal")
    ax.imshow(np.where(wet, 1.0, np.nan), extent=ext, origin="lower", cmap=LinearSegmentedColormap.from_list("w", [AQUA, AQUA]), alpha=0.45, interpolation="nearest")
    im = ax.imshow(Z, extent=ext, origin="lower", cmap=SEQ, vmin=0, vmax=np.percentile(Z.compressed(), 98), interpolation="nearest")
    ax.plot(*np.r_[sc.pool, sc.pool[:1]].T, color=INK2, lw=1, ls=(0, (3, 2)), label="reservoir outline (OSM, full pool)")
    ax.plot(z["Cw"][:, 0], z["Cw"][:, 1], color=ORANGE, lw=1.8, label="camera path")
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color=AQUA, alpha=0.45, label="at water level (flat reflector)"), Patch(color="#3987e5", label="shore / land above water"),
                       *ax.get_legend_handles_labels()[0]], loc="lower right", fontsize=8.5)
    yy, xx = np.where(sup); pad = 30
    ax.set_xlim(T.x0 + xx.min() * T.cell - pad, T.x0 + xx.max() * T.cell + pad); ax.set_ylim(T.y0 + yy.min() * T.cell - pad, T.y0 + yy.max() * T.cell + pad)
    cb = f.colorbar(im, ax=ax, fraction=0.035, pad=0.02); cb.set_label("height above water (m)"); cb.outline.set_visible(False)
    ax.set_xlabel("east (m)"); ax.set_ylabel("north (m)"); ax.set_title("A · Height field around the clip (blank = no SfM points)")
    ax = f.add_subplot(gs[1])
    pz = z["points"][:, 2] + cx.cfg.get("water_offset_m", 0.0) * -1
    ax.hist(np.clip(pz, -6, 12), bins=90, color=BLUE, edgecolor=SURF, lw=0.4)
    ax.axvline(0, color=ORANGE, lw=2); ax.annotate("water level used", (0, ax.get_ylim()[1] * 0.9), xytext=(6, 0), textcoords="offset points", fontsize=9.5)
    ax.set_xlabel("point height above water (m; tails lumped at the ends)"); ax.set_ylabel("points"); ax.set_title("B · Where the SfM points sit")
    return _save(f, out, "fig06_terrain.png")

# ================================================================== 7. water map (world)
def fig07_watermap(cx, out):
    from matplotlib.patches import Patch, Rectangle
    sc = cx.scene; wm = cx.watermap(); z = cx.sfm
    if wm is None: print("  (no cached water map yet - run preview/render first)"); return None
    pool = sc.pool; cmap = LinearSegmentedColormap.from_list("w", [SURF, "#86b6ef"])
    def show(ax, x0, x1, y0, y1):
        j0, j1 = max(int((x0 - wm.x0) / wm.cell), 0), int((x1 - wm.x0) / wm.cell); i0, i1 = max(int((y0 - wm.y0) / wm.cell), 0), int((y1 - wm.y0) / wm.cell)
        ax.imshow(wm.prob[i0:i1, j0:j1], extent=[wm.x0 + j0 * wm.cell, wm.x0 + j1 * wm.cell, wm.y0 + i0 * wm.cell, wm.y0 + i1 * wm.cell],
                  origin="lower", cmap=cmap, vmin=0, vmax=1, interpolation="nearest")
        ax.plot(*np.r_[pool, pool[:1]].T, color=INK2, lw=1, ls=(0, (3, 2)))
        ax.plot(z["Cw"][:, 0], z["Cw"][:, 1], color=ORANGE, lw=2)
        ax.set_xlim(x0, x1); ax.set_ylim(y0, y1); ax.set_aspect("equal"); ax.set_xlabel("east (m)"); ax.set_ylabel("north (m)")
    f = _fig(13, 7.6, "Step 5b · World-anchored water map",
             "Water is segmented on keyframes, every pixel is cast onto the solved surface, and the votes accumulate in a 0.7 m grid. "
             "Every video frame then samples this one map, so the shoreline cannot flicker or slide between frames.")
    gs = f.add_gridspec(1, 2, left=0.06, right=0.985, top=0.84, bottom=0.09, wspace=0.18, width_ratios=[1.15, 1])
    ax = f.add_subplot(gs[0]); pad = 150
    show(ax, pool[:, 0].min() - pad, pool[:, 0].max() + pad, pool[:, 1].min() - pad, pool[:, 1].max() + pad)
    C = z["Cw"]; fw = z["Rwc"][:, :2, 2].mean(0); fw /= np.linalg.norm(fw); cc = C[:, :2].mean(0) + 170 * fw
    zx0, zx1, zy0, zy1 = cc[0] - 250, cc[0] + 250, cc[1] - 250, cc[1] + 250
    ax.add_patch(Rectangle((zx0, zy0), zx1 - zx0, zy1 - zy0, fill=False, ec=INK, lw=1))
    ax.legend(handles=[Patch(color="#86b6ef", label="mapped water"), plt.Line2D([], [], color=INK2, ls=(0, (3, 2)), lw=1, label="OSM outline (full pool)"),
                       plt.Line2D([], [], color=ORANGE, lw=2, label="camera path")], loc="lower left")
    ax.set_title("A · Whole reservoir: today's water sits well inside the full-pool outline")
    ax = f.add_subplot(gs[1]); show(ax, zx0, zx1, zy0, zy1); ax.set_title("B · Zoom where the camera looks (0.7 m cells)")
    return _save(f, out, "fig07_water_map.png")

# ================================================================== 8. per-frame water
def fig08_water_frame(cx, out, t=52.0):
    from .scene import horizon_mask_for, clip_poly_front
    from .render import ground_field
    from .water_refine import refine
    sc = cx.scene; fr = cx.frame(t); _, cam, _, _ = cx.pose(t); h, w = fr.shape[:2]
    hz = horizon_mask_for(cam, w, h)
    prior = np.zeros((h, w), np.uint8); uv, ok = clip_poly_front(cam, sc.pool3)
    if ok.sum() > 3: cv2.fillPoly(prior, [np.clip(uv[ok], -4000, 6000).astype(np.int32)], 1)
    wm = cx.watermap(); G = ground_field(cam, w, h, 0.4, sc.terrain)
    pw = cv2.resize(wm.sample(G[..., 0], G[..., 1]), (w, h), interpolation=cv2.INTER_LINEAR)
    pr = refine(fr, pw, hz > 0); fin = (np.clip((pr - 0.35) / 0.3, 0, 1) * (hz > 0)) > 0.5
    def over(mask, color, alpha=0.42, edge=True):
        img = fr.copy().astype(np.float32); c = np.array(tuple(int(color[i:i + 2], 16) for i in (5, 3, 1)), np.float32)
        img[mask > 0] = img[mask > 0] * (1 - alpha) + c * alpha
        img = img.astype(np.uint8)
        if edge:
            cn, _ = cv2.findContours((mask > 0).astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
            cv2.drawContours(img, cn, -1, (255, 255, 255), 3, cv2.LINE_AA); cv2.drawContours(img, cn, -1, tuple(int(x) for x in c), 2, cv2.LINE_AA)
        return img
    f = _fig(13, 8.6, "Step 5c · Water in one frame",
             f"Clip t = {t:.1f} s. The map is sampled through the solved camera, then the edge is re-decided in a narrow band from this frame's own "
             "colours (Lab models of water vs shore + guided filter).")
    gs = f.add_gridspec(2, 2, left=0.01, right=0.99, top=0.87, bottom=0.01, hspace=0.1, wspace=0.02)
    _img_ax(f.add_subplot(gs[0, 0]), fr, "A · GoPro frame")
    _img_ax(f.add_subplot(gs[0, 1]), over(prior * hz, "#eda100"), "B · Full-pool outline projected (prior only)")
    _img_ax(f.add_subplot(gs[1, 0]), over((pw > 0.5) * hz, "#2a78d6"), "C · World water map seen by this camera")
    _img_ax(f.add_subplot(gs[1, 1]), over(fin, "#1baf7a"), "D · After per-frame edge refinement (used in the video)")
    return _save(f, out, "fig08_water_frame.png")

# ================================================================== 9. Fresnel geometry
def fig09_fresnel(cx, out, t=52.0):
    from .scene import sat_state
    sc = cx.scene; mt, cam, _, _ = cx.pose(t); rx = cam.C; dt, sats = sat_state(sc, mt, rx)
    wl = sc.wl; h = rx[2]; mask = cx.cfg["elevation_mask_deg"]
    f = _fig(13, 9.0, "Step 6 · Specular point and first Fresnel zone",
             f"Satellites from the almanacs at {dt:%H:%M:%S} UTC; receiver {h:.1f} m above the water; "
             f"L5/E5a/B2a, λ = {wl * 100:.1f} cm. Zone = ground points whose extra path length is under λ/2.")
    gs = f.add_gridspec(2, 2, left=0.06, right=0.985, top=0.87, bottom=0.07, hspace=0.42, wspace=0.2, width_ratios=[1.1, 1])
    # A side view
    ax = f.add_subplot(gs[0, 0]); el = math.radians(35); d = h / math.tan(el)
    ax.plot([-15, 400], [0, 0], color=BLUE, lw=2); ax.fill_between([-15, 400], [-h * 0.25] * 2, [0, 0], color=WATER_FILL, lw=0)
    ax.scatter([0], [h], color=INK, s=40, zorder=4); ax.annotate("receiver (GoPro / antenna)", (0, h), xytext=(6, 6), textcoords="offset points", fontsize=9)
    ax.scatter([0], [-h], color=MUTED, s=30, zorder=4); ax.annotate("mirror image", (0, -h), xytext=(6, -12), textcoords="offset points", fontsize=9, color=INK2)
    L = 80; sx = d + L * math.cos(el); sy = L * math.sin(el)
    ax.plot([d, sx], [0, sy], color=ORANGE, lw=1.8); ax.plot([0, d], [h, 0], color=ORANGE, lw=1.8)
    ax.plot([0, d], [-h, 0], color=MUTED, lw=1, ls=(0, (3, 2)))
    ax.annotate("", xy=(d + 0.8 * L * math.cos(el), 0.8 * sy), xytext=(d + 0.95 * L * math.cos(el), 0.95 * sy), arrowprops=dict(arrowstyle="-|>", color=ORANGE, lw=1.4))
    ax.text(sx - 4, sy, "from satellite\n(parallel rays)", fontsize=9, color=INK2, ha="right", va="top")
    ax.scatter([d], [0], color=ORANGE, s=45, zorder=5, edgecolor=SURF); ax.annotate("specular point", (d, 0), xytext=(-8, 8), textcoords="offset points", fontsize=9, ha="right")
    c, ring, _ = fresnel_zone(np.array([0, 2e7 * math.cos(el), 2e7 * math.sin(el)]), np.array([0, 0, h]), wl, 180)
    ext = ring[:, 1]; ax.plot([ext.min(), ext.max()], [0, 0], color=AQUA, lw=7, solid_capstyle="butt", alpha=0.9)
    ax.annotate(f"first Fresnel zone\n{np.ptp(ext):.1f} m long here", ((ext.min() + ext.max()) / 2, 0), xytext=(0, -38), textcoords="offset points", ha="center", fontsize=9, color=INK)
    ax.text(d * 0.45, h * 0.35, f"el {math.degrees(el):.0f}°", fontsize=9, color=INK2)
    ax.set_xlim(-15, d + 70); ax.set_ylim(-h * 1.2, h * 1.75); ax.set_xlabel("along the satellite azimuth (m)"); ax.set_ylabel("height (m)")
    ax.set_title("A · Side view: the reflection obeys the mirror law")
    # B top-down map now
    ax = f.add_subplot(gs[:, 1]); ax.set_aspect("equal")
    wm = cx.watermap(); R0 = 135
    if wm is not None:
        j0, j1 = int((rx[0] - R0 - wm.x0) / wm.cell), int((rx[0] + R0 + 30 - wm.x0) / wm.cell); i0, i1 = int((rx[1] - R0 - wm.y0) / wm.cell), int((rx[1] + R0 - wm.y0) / wm.cell)
        ax.imshow(wm.prob[i0:i1, j0:j1], extent=[wm.x0 + j0 * wm.cell, wm.x0 + j1 * wm.cell, wm.y0 + i0 * wm.cell, wm.y0 + i1 * wm.cell], origin="lower",
                  cmap=LinearSegmentedColormap.from_list("w", [SURF, WATER_FILL]), vmin=0, vmax=1, interpolation="bilinear", zorder=0)
    seen = set()
    for s in sorted(sats, key=lambda s: ("GEC".find(s["const"]), s["el"])):
        if s["el"] < mask: continue
        col, nm = CONST.get(s["const"], (MUTED, s["const"]))
        hloc = sc.terrain.water_z if sc.terrain is not None else 0
        c_, r_, _ = fresnel_zone(s["enu"], rx, wl, 120)
        ax.fill(r_[:, 0], r_[:, 1], color=col, alpha=0.35, lw=0); ax.plot(*np.r_[r_, r_[:1]][:, :2].T, color=col, lw=1.5, label=nm if nm not in seen else None); seen.add(nm)
        ax.plot([rx[0], c_[0]], [rx[1], c_[1]], color=col, lw=0.7, alpha=0.6)
        u = (c_[:2] - rx[:2]); u = u / (np.linalg.norm(u) + 1e-9); off = np.ptp(r_[:, :2], 0).max() / 2 + 4
        ax.text(*(c_[:2] + u * off), f"{s['sid']} {s['el']:.0f}°", fontsize=8.2, color=INK, ha="left" if u[0] >= 0 else "right", va="bottom" if u[1] >= 0 else "top")
    ax.scatter(*rx[:2], color=INK, s=40, marker="^", zorder=5); ax.annotate("drone nadir", rx[:2], xytext=(6, -12), textcoords="offset points", fontsize=9)
    ax.set_xlim(rx[0] - R0, rx[0] + R0 + 25); ax.set_ylim(rx[1] - R0, rx[1] + R0)
    ax.set_xlabel("east (m)"); ax.set_ylabel("north (m)"); ax.legend(loc="lower left"); ax.set_title(f"B · Zones on the ground at t = {t:.0f} s (blue = water)")
    # C why round
    ax = f.add_subplot(gs[1, 0]); els = np.arange(20, 90.1, 2.5); A_, B_, ang_a, ang_b = [], [], [], []
    for e in els:
        er = math.radians(e); sat = np.array([0, 2e7 * math.cos(er), 2e7 * math.sin(er)])
        c_, r_, _ = fresnel_zone(sat, np.array([0, 0, h]), wl, 180); a = np.ptp(r_[:, 1]) / 2; b = np.ptp(r_[:, 0]) / 2
        rng_ = math.hypot(c_[1], h); A_.append(a); B_.append(b)
        ang_a.append(math.degrees(2 * a * math.sin(er) / rng_)); ang_b.append(math.degrees(2 * b / rng_))
    ax.plot(els, np.array(A_) / np.array(B_), color=ORANGE, lw=2, label="on the ground: long ÷ short axis")
    ax.plot(els, np.array(ang_a) / np.array(ang_b), color=BLUE, lw=2, label="as seen from the receiver")
    ax.text(els[2], np.array(A_)[2] / np.array(B_)[2] + 0.08, "ground ellipse ≈ 1/sin(el)", fontsize=9, color=INK2)
    ax.text(els[1], 0.9, "apparent shape from the receiver = circle (1/sin(el) stretch cancels sin(el) foreshortening)", fontsize=9, color=INK2)
    ax.set_ylim(0.8, max(np.array(A_) / np.array(B_)) * 1.1); ax.set_xlabel("satellite elevation (°)"); ax.set_ylabel("aspect ratio")
    ax.legend(loc="upper right"); ax.set_title("C · Ground ellipse vs what the camera sees")
    return _save(f, out, "fig09_fresnel_geometry.png")

# ================================================================== 10. sun glint validation
def fig10_glint(cx, out, t0=36.0, t1=50.0):
    sc = cx.scene; cap = cv2.VideoCapture(str(cx.video)); fps = FPS; wm = cx.watermap()
    rows = []; best = None
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(t0 * fps))
    for k in range(int((t1 - t0) * fps)):
        ok, fr = cap.read()
        if not ok: break
        if k % 3: continue
        if fr.shape[1] != cx.W: fr = cv2.resize(fr, (cx.W, cx.H))
        t = t0 + k / fps; mt, cam, lat, lon = cx.pose(t)
        sd = sun_enu_dir(sc.tel.utc(mt), lat, lon)[0] * np.array([1, 1, -1.0])
        uv, okp = cam.project_dir(sd[None]); u = uv[0]
        if not okp[0] or not (0 < u[0] < cx.W and 0 < u[1] < cx.H): continue
        g = cv2.GaussianBlur(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY), (0, 0), 2); m = (g > 250).astype(np.uint8)
        n, lab, st, cen = cv2.connectedComponentsWithStats(m)
        cand = [(np.linalg.norm(cen[i] - u), cen[i]) for i in range(1, n) if st[i, 4] > 300]
        if not cand: continue
        e, c = min(cand, key=lambda x: x[0])
        if e > 200: continue
        P = cam.C + (-cam.C[2] / sd[2]) * sd; wet = float(wm.sample(np.array([P[0]]), np.array([P[1]]))[0]) > 0.5 if wm is not None else True
        rows.append((t, u[0], u[1], c[0], c[1], e, wet))
        if wet and best is None or wet and abs(t - (t0 + t1) / 2) < abs(best[0] - (t0 + t1) / 2): best = (t, fr.copy(), u, c)
    if not rows: print("  (no sun glint found in window)"); return None
    r = np.array(rows)
    f = _fig(13, 6.4, "Validation · Sun glint: an independent check of the camera",
             "The sun's position (NOAA formula) mirrored in the water must land on the bright glint in the frame. "
             "Nothing in the pipeline was fitted to the glint.")
    gs = f.add_gridspec(1, 2, left=0.02, right=0.985, top=0.84, bottom=0.11, wspace=0.12, width_ratios=[1.25, 1])
    t, fr, u, c = best; x0, y0 = int(np.clip(c[0] - 260, 0, cx.W - 520)), int(np.clip(c[1] - 160, 0, cx.H - 320))
    crop = fr[y0:y0 + 320, x0:x0 + 520].copy()
    ax = f.add_subplot(gs[0]); _img_ax(ax, crop, f"A · Crop at t = {t:.1f} s")
    ax.scatter([u[0] - x0], [u[1] - y0], s=260, facecolor="none", edgecolor=ORANGE, lw=2.2, label="predicted glint")
    ax.scatter([c[0] - x0], [c[1] - y0], s=30, color=BLUE, edgecolor="white", lw=1, label="observed brightest blob")
    ax.legend(loc="lower left", labelcolor="white", fontsize=9.5)
    ax = f.add_subplot(gs[1])
    wet = r[:, 6] > 0.5; med = np.median(r[wet, 5])
    for a_, b_ in zip(r[:-1], r[1:]):
        if not (a_[6] > 0.5 and b_[6] > 0.5): ax.axvspan(a_[0], b_[0], color=TURN_FILL, lw=0, zorder=0)
    ax.plot(r[:, 0], np.where(wet, r[:, 5], np.nan), color=BLUE, lw=1.8, label="mirror point on water")
    ax.plot(r[:, 0], np.where(~wet, r[:, 5], np.nan), color=MUTED, lw=1.4, ls=(0, (3, 2)), label="mirror point on land: no real glint, blob is a bright shore")
    ax.axhline(med, color=INK2, lw=1, ls=(0, (3, 3)))
    ax.annotate(f"median on water {med:.1f} px", (r[wet, 0].mean(), med), xytext=(0, 6), textcoords="offset points", fontsize=9.5, ha="center")
    ax.legend(loc="upper left", fontsize=8.5)
    ax.set_xlabel("clip time (s)"); ax.set_ylabel("prediction error (px, 1920×1080)"); ax.set_ylim(0, max(30, r[:, 5].max() * 1.2))
    ax.set_title("B · Predicted vs observed glint over time")
    return _save(f, out, "fig10_sun_glint.png")

# ================================================================== 11. final
def fig11_final(cx, out, t=52.0):
    from .render import compose_v2
    sc = cx.scene; fr = cx.frame(t); sc.water_ema = None; sc.watermap = cx.watermap()
    img, info = compose_v2(fr, sc, cx.t0 + t, cx.lens, int(round(t * FPS)), FPS, [])
    f = _fig(13, 4.6, "Step 7 · Final composite", f"Clip t = {t:.0f} s: raw GoPro frame and the rendered overlay ({info['items']} zones in view, {info['on_water']} on water).")
    _img_ax(f.add_axes([0.005, 0.01, 0.493, 0.82]), fr, "A · Raw")
    _img_ax(f.add_axes([0.502, 0.01, 0.493, 0.82]), img, "B · HERON AR")
    cv2.imwrite(str(Path(out) / "final_frame.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return _save(f, out, "fig11_final_composite.png")

FIGS = {0: fig00_pipeline, 1: fig01_sync, 2: fig02_attitude, 3: fig03_features, 4: fig04_sparse, 5: fig05_georef,
        6: fig06_terrain, 7: fig07_watermap, 8: fig08_water_frame, 9: fig09_fresnel, 10: fig10_glint, 11: fig11_final}

def make_all(config, out, work, only=None):
    from .figures_extra import EXTRA
    FIGS.update(EXTRA)
    from .figures_more import MORE
    FIGS.update(MORE)
    out = Path(out); out.mkdir(parents=True, exist_ok=True); cx = Ctx(config, work)
    keys = [int(k) for k in only.split(",")] if only else list(FIGS)
    for k in keys:
        print(f"figure {k}: {FIGS[k].__name__}", flush=True)
        try: FIGS[k](cx, out)
        except Exception as e:
            import traceback; traceback.print_exc(); print(f"  figure {k} failed: {e}")
