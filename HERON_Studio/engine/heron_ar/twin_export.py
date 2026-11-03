"""Export the solved scene as a compact package for the 3D digital-twin viewer (docs/twin/).

    python -c "from heron_ar.twin_export import export; export('configs/survey_1080p.json', 'work/survey_1080p', 'out/twin')"

Writes: scene.json (terrain heights, poses at 10 Hz, lens, satellite az/el, outlines, points),
ortho.jpg (GoPro orthomosaic texture), water.png (world water map), clip.mp4 (low-res video).
"""
from __future__ import annotations
import json, math, subprocess, shutil
from pathlib import Path
import numpy as np, cv2


def export(config, work, out, half=430.0, step_pose=0.1, step_sat=2.0, video_w=640):
    from .figures import Ctx
    from .figures_extra import orthomosaic
    from .scene import sat_state
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    cx = Ctx(config, work); sc = cx.scene; z = cx.sfm
    C0 = z["Cw"].mean(0); ox0, oy0 = C0[0] - half, C0[1] - half; size = 2 * half
    # ---------------- terrain heights (4 m grid)
    n = int(size / 4) + 1; xs = ox0 + np.arange(n) * 4.0; ys = oy0 + np.arange(n) * 4.0
    XX, YY = np.meshgrid(xs, ys); H = sc.terrain.height(XX, YY).astype(np.float32)
    # ---------------- ortho texture
    img, ox, oy, cell = orthomosaic(cx)
    N = 2048; u = (ox0 + (np.arange(N) + 0.5) * size / N - ox) / cell; v = (oy0 + (np.arange(N) + 0.5) * size / N - oy) / cell
    mu, mv = np.meshgrid(u.astype(np.float32), v.astype(np.float32))
    tex = cv2.remap(img, mu, mv, cv2.INTER_AREA if False else cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
    a = tex[..., 3:4].astype(np.float32) / 255; base = np.array([196, 191, 182], np.float32)
    rgb = tex[..., :3].astype(np.float32) * a + base * (1 - a)
    cv2.imwrite(str(out / "ortho.jpg"), cv2.cvtColor(np.flipud(rgb).astype(np.uint8), cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 84])
    # ---------------- water map (2 m)
    wm = cx.watermap(); m = int(size / 2); gx = ox0 + (np.arange(m) + 0.5) * 2; gy = oy0 + (np.arange(m) + 0.5) * 2
    WX, WY = np.meshgrid(gx, gy); W = (wm.sample(WX, WY) * 255).astype(np.uint8)
    cv2.imwrite(str(out / "water.png"), np.flipud(W))
    # shoreline contours (in metres)
    cs, _ = cv2.findContours((W > 127).astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    shore = [[[round(float(ox0 + (p[0][0] + 0.5) * 2), 1), round(float(oy0 + (p[0][1] + 0.5) * 2), 1)] for p in c] for c in cs if len(c) > 30]
    # ---------------- poses at 10 Hz
    dur = cx.cfg["clip_duration_s"]; ts = np.arange(0, dur, step_pose); poses = []
    for t in ts:
        mt, cam, _, _ = cx.pose(t)
        poses.append([round(float(v), 3) for v in cam.C] + [round(float(v), 5) for v in cam.R.ravel()])
    utc0 = sc.tel.utc(sc.mission_t(cx.t0)).isoformat()
    # ---------------- satellites (az/el every 2 s)
    sats = {}
    for t in np.arange(0, dur + step_sat, step_sat):
        mt, cam, _, _ = cx.pose(min(t, dur - 0.01)); _, ss = sat_state(sc, mt, cam.C)
        for s in ss:
            d = sats.setdefault(s["sid"], {"c": s["const"], "az": [], "el": []})
            d["az"].append(round(s["az"], 3)); d["el"].append(round(s["el"], 3))
    # ---------------- points
    P = z["points"].copy(); P[:, 2] += -cx.cfg.get("water_offset_m", 0.0)
    rec = cx.model(); from .georef import umeyama
    ims = sorted([im for im in rec.images.values() if im.has_pose], key=lambda i: int("".join(ch for ch in i.name if ch.isdigit())))
    Craw = np.array([im.projection_center() for im in ims]); s_, R_, t_ = umeyama(Craw, z["Cw"])
    Pr = np.array([p.xyz for p in rec.points3D.values()]); col = np.array([p.color for p in rec.points3D.values()])
    err = np.array([p.error for p in rec.points3D.values()]); Pw = (s_ * (R_ @ Pr.T)).T + t_ + [0, 0, -cx.cfg.get("water_offset_m", 0.0)]
    k = (err < 2) & (np.abs(Pw[:, 0] - C0[0]) < half) & (np.abs(Pw[:, 1] - C0[1]) < half)
    idx = np.where(k)[0]; idx = idx[np.random.default_rng(0).permutation(len(idx))[:30000]]
    pts = np.c_[np.round(Pw[idx], 2), col[idx]].ravel().tolist()
    # ---------------- lens (1920x1080 pixels)
    L = cx.lens; lens = dict(w=cx.W, h=cx.H, fx=L.fx, fy=L.fy, cx=L.cx, cy=L.cy, k=[float(v) for v in L.D])
    turns = [[float(a), float(b)] for a, b in (np.array(_turns()) - sc.mission_t(cx.t0)) if b > 0 and a < dur]
    scene = dict(origin=[ox0, oy0], size=size, grid=4.0, n=n, heights=np.round(H, 2).ravel().tolist(), water_res=2.0,
                 pose_dt=step_pose, poses=poses, sat_dt=step_sat, sats=sats, wavelength=sc.wl, mask=cx.cfg["elevation_mask_deg"],
                 utc0=utc0, lens=lens, shore=shore, osm=np.round(sc.pool, 1).tolist(), points=pts, turns=turns,
                 path=np.round(z["Cw"] + [0, 0, -cx.cfg.get("water_offset_m", 0.0)], 2).tolist(), duration=dur)
    (out / "scene.json").write_text(json.dumps(scene, separators=(",", ":")))
    # ---------------- low-res video
    subprocess.run([shutil.which("ffmpeg"), "-y", "-loglevel", "error", "-i", str(cx.video), "-t", f"{dur:.2f}", "-vf", f"scale={video_w}:-2",
                    "-c:v", "libx264", "-preset", "slow", "-crf", "31", "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart",
                    "-g", "24", str(out / "clip.mp4")], check=True)
    for f in out.iterdir(): print(f.name, f.stat().st_size // 1024, "KB")

def _turns():
    import pandas as pd
    from .geometry import DATA
    T = pd.read_csv(DATA / "turn_intervals.csv")
    return T[["turn_zone_start_elapsed_s", "turn_zone_end_elapsed_s"]].values - 0.545
