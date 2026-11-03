"""HERON AR renderer: GNSS-R specular points + first Fresnel zones laid on the
water plane of real GoPro flight video, with segmented lake, satellites in the
sky, compass tape, mini-map and sky-plot HUD.

    python3 render_ar.py --config config_ar.json [--preview 10,60,120] [--max-seconds N]

See README_AR.md for the method and its error budget.
"""
from __future__ import annotations
import argparse, csv, json, math, subprocess, shutil, sys, time
from pathlib import Path
import numpy as np, cv2
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from .geometry import *
from .geometry import expm_so3
from .gnss.gnss import load_l5_sats
from .chase_view import ChaseCam, render_chase, pool_raster
from .zone_shader import shade_zone
from .stabilizer import GroundLock, SurfaceHeight

FONT_DIR = ROOT / "fonts"
def font(name, px):
    for p in [FONT_DIR / name, Path("/usr/share/fonts/opentype/inter") / name,
              Path("/System/Library/Fonts/Supplemental/Arial.ttf")]:
        try: return ImageFont.truetype(str(p), px)
        except OSError: pass
    return ImageFont.load_default()

# constellation colours (RGB)
CCOL = {"G": (255, 196, 61), "E": (77, 214, 255), "C": (255, 112, 180), "J": (120, 235, 140)}
CNAME = {"G": "GPS L5", "E": "Galileo E5a", "C": "BeiDou B2a", "J": "QZSS L5"}
WATER = (64, 190, 255)
INK = (235, 242, 248)
DIM = (160, 176, 190)
PANEL = (10, 16, 24)

class Scene:
    def __init__(self, cfg):
        self.cfg = cfg
        self.tel = Telemetry(cfg["water_elevation_m"]); self.F = self.tel.frame
        self.imu = GoProIMU(DATA / cfg.get('gopro_dir', 'gopro1'))
        self.sync = cfg.get("sync") or estimate_sync(self.imu, search=tuple(cfg.get("sync_search", (-120, 0))))
        t0, t1 = cfg["clip_video_start_s"] - 2, cfg["clip_video_start_s"] + cfg["clip_duration_s"] + 2
        warm = 60.0      # let the attitude filter settle before the clip
        self.att_t, self.att_R, _ = camera_attitude(self.imu, self.tel, self.sync, yaw_offset_deg=cfg["yaw_offset_deg"],
                                                    t_range=(max(0, t0 - warm), t1))
        # constant camera-to-IMU boresight correction (small calibration rotation, camera frame)
        self.set_boresight(cfg.get("boresight_pitch_deg", 0), cfg.get("boresight_yaw_deg", 0), cfg.get("boresight_roll_deg", 0))
        self.sats = [s for s in load_l5_sats(DATA) if s.health == 0 and s.constellation in cfg["constellations"]]
        self.wl = 299792458.0 / cfg["frequency_hz"]
        res = json.loads((DATA / cfg.get("outline_geojson", "boulder_reservoir.geojson")).read_text())["features"][0]["geometry"]["coordinates"][0]
        res = np.array(res); self.pool = self.F.en_from_ll(res[:, 0], res[:, 1])
        self.pool3 = densify(np.c_[self.pool, np.zeros(len(self.pool))], 6.0)
        tr = np.arange(-30, 860, 1.0); self.track = np.array([self.tel.position(t)[0] for t in tr])
        self.water_ema = None
        self.pool_raster = pool_raster(self.pool)
        self.chase = None
        self.terrain = None
        if cfg.get('sfm_poses'):
            from .terrain import Terrain
            self.load_sfm(DATA / cfg['sfm_poses'], cfg.get('water_offset_m', 0.0))
            zz = np.load(DATA / cfg['sfm_poses']); self.terrain = Terrain(zz['points'] + [0, 0, -cfg.get('water_offset_m', 0.0)], 0.0)
            self.sfm_cam = zz['cam_params']
        self.lock = None
        self.hs = None
        self.prev_cam = None

    def set_boresight(self, pitch, yaw, roll):
        """Small fixed rotation between the IMU-derived camera frame and the true optical frame.
        +pitch tilts the optical axis further DOWN, +yaw turns it RIGHT, +roll is clockwise in the image."""
        b = np.deg2rad([pitch, yaw, roll])
        self.Rb = rot_axis([1, 0, 0], -b[0]) @ rot_axis([0, 1, 0], b[1]) @ rot_axis([0, 0, 1], b[2])

    def mission_t(self, vt): return vt * (1 + self.sync["rate"]) + self.sync["offset"]

    def load_sfm(self, path, plane_offset_m):
        z = np.load(path); self.sfm = dict(vt=z["vt"], Rwc=z["Rwc"], Cw=z["Cw"], Rimu=z["Rimu"], dz=plane_offset_m)

    def pose(self, vt, lens):
        if getattr(self, "sfm", None) is not None:
            return self.pose_sfm(vt, lens)
        return self.pose_imu(vt, lens)

    def pose_sfm(self, vt, lens):
        """Image-solved pose: SfM keyframes, gyro-relative rotation in between."""
        S_ = self.sfm; mt = self.mission_t(vt)
        _, cam_i, lat, lon = self.pose_imu(vt, lens)
        k = int(np.clip(np.searchsorted(S_["vt"], vt) - 1, 0, len(S_["vt"]) - 2))
        t0, t1 = S_["vt"][k], S_["vt"][k + 1]; a = float(np.clip((vt - t0) / (t1 - t0), 0, 1))
        R0 = S_["Rwc"][k] @ (S_["Rimu"][k].T @ cam_i.R); R1 = S_["Rwc"][k + 1] @ (S_["Rimu"][k + 1].T @ cam_i.R)
        from .geometry import log_so3
        R = R0 @ expm_so3(a * log_so3(R0.T @ R1))
        C = (1 - a) * S_["Cw"][k] + a * S_["Cw"][k + 1]; C = C - np.array([0, 0, S_["dz"]])
        return mt, CameraPose(lens, R, C), lat, lon

    def pose_imu(self, vt, lens):
        mt = self.mission_t(vt)
        C, lat, lon, h = self.tel.position(mt)
        R = interp_rot(self.att_t, self.att_R, vt) @ self.Rb
        return mt, CameraPose(lens, R, C), lat, lon

def densify(P, step):
    out = []
    for i in range(len(P) - 1):
        n = max(1, int(np.linalg.norm(P[i + 1] - P[i]) / step))
        out.extend(P[i] + (P[i + 1] - P[i]) * k / n for k in range(n))
    out.append(P[-1]); return np.array(out)

def clip_poly_front(cam, P3):
    """Project a closed world polygon, dropping vertices behind the camera."""
    Pc = (P3 - cam.C) @ cam.R; keep = Pc[:, 2] > 0.5
    uv, ok = cam.lens.project_cam(Pc); return uv, keep & ok

def sat_state(scene, mt, rx):
    dt = scene.tel.utc(mt); out = []
    for s in scene.sats:
        xyz = np.array(s.ecef(dt))
        if not np.isfinite(xyz).all(): continue
        e = scene.F.ecef2enu(xyz); d = e - rx; rng = np.linalg.norm(d)
        el = math.degrees(math.asin(d[2] / rng)); az = math.degrees(math.atan2(d[0], d[1])) % 360
        out.append(dict(sid=s.sid, const=s.constellation, enu=e, el=el, az=az, dir=d / rng))
    return dt, out

# ------------------------------------------------------------------ water segmentation
def segment_water(bgr, cam, scene, horizon_mask):
    h, w = bgr.shape[:2]
    b, g, r = [bgr[:, :, i].astype(np.int16) for i in range(3)]
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[:, :, 0].astype(np.int16), hsv[:, :, 1].astype(np.int16), hsv[:, :, 2].astype(np.int16)
    blue = (b - r > 22) & (b > 95) & (hue > 84) & (hue < 128) & (sat > 38)          # open / deep water
    neutral = (b - r > -4) & (sat < 45) & (val > 70) & (val < 225)                  # shallow, sediment-tinted water
    glint = (val > 235) & (sat < 40)                                                 # sun glint / glitter
    prior = np.zeros((h, w), np.uint8)
    uv, ok = clip_poly_front(cam, scene.pool3)
    if ok.sum() > 3:
        pts = np.clip(uv[ok], -4000, 6000).astype(np.int32); cv2.fillPoly(prior, [pts], 1)
    prior = cv2.dilate(prior, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (41, 41)))
    valid = (prior & horizon_mask).astype(bool)
    if scene.cfg.get("seg_v2", True):
        from .water_seg import segment_water_v2
        m = segment_water_v2(bgr, prior, horizon_mask, scene.cfg.get("seg_scale", 0.3)).astype(np.float32)
        if scene.water_ema is None or scene.water_ema.shape != m.shape: scene.water_ema = m
        else: scene.water_ema = 0.5 * scene.water_ema + 0.5 * m
        return (scene.water_ema > 0.5).astype(np.uint8), prior
    cand = ((blue | neutral | glint) & valid).astype(np.uint8)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    cand = cv2.morphologyEx(cand, cv2.MORPH_OPEN, k)
    cand = cv2.morphologyEx(cand, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))
    # keep only regions anchored by confidently blue water (grey ground is never accepted on its own)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(cand, 8)
    bluecount = np.bincount(lab.ravel(), weights=(blue & valid).ravel().astype(np.float32), minlength=n)
    area = stats[:, cv2.CC_STAT_AREA]
    keep = (bluecount > np.maximum(0.12 * area, 1500)) & (area > (h * w) * 4e-4); keep[0] = False
    m = keep[lab].astype(np.float32)
    # temporal smoothing (frame-to-frame motion is small at 24 fps)
    if scene.water_ema is None or scene.water_ema.shape != m.shape: scene.water_ema = m
    else: scene.water_ema = 0.55 * scene.water_ema + 0.45 * m
    return (scene.water_ema > 0.5).astype(np.uint8), prior

_hz_rays = {}
def horizon_mask_for(cam, w, h):
    """1 where the pixel ray hits the water plane (below horizon)."""
    ys, xs = np.mgrid[0:h:8, 0:w:8]
    k = (id(cam.lens), w, h)
    if k not in _hz_rays: _hz_rays[k] = cam.lens.unproject(np.c_[xs.ravel(), ys.ravel()])
    d = _hz_rays[k] @ cam.R.T
    below = (d[:, 2] < -0.004).reshape(ys.shape).astype(np.uint8)
    return cv2.resize(below, (w, h), interpolation=cv2.INTER_NEAREST)

# ------------------------------------------------------------------ drawing helpers
class Canvas:
    """RGBA overlay drawn with OpenCV (fast AA lines/fills) + PIL for text."""
    def __init__(self, w, h):
        self.w, self.h = w, h; self.layers = []
    def new(self): a = np.zeros((self.h, self.w, 4), np.uint8); self.layers.append(a); return a

def blend(base_rgb, rgba):
    """Alpha 'over' composite, restricted to the layer's bounding box (integer maths)."""
    x, y, bw, bh = cv2.boundingRect(rgba[:, :, 3])
    if bw == 0 or bh == 0: return base_rgb
    out = base_rgb.copy() if not base_rgb.flags.writeable else base_rgb
    sub = out[y:y + bh, x:x + bw]; L = rgba[y:y + bh, x:x + bw]
    a = L[:, :, 3:4].astype(np.uint16)
    sub[:] = ((sub.astype(np.uint16) * (255 - a) + L[:, :, :3].astype(np.uint16) * a + 127) // 255).astype(np.uint8)
    return out

def col(c, a=255): return (int(c[0]), int(c[1]), int(c[2]), int(a))

def poly_ok(uv, lim=8000): return np.isfinite(uv).all() and np.abs(uv).max() < lim

def text(d: ImageDraw.ImageDraw, xy, s, f, fill=INK, anchor="la", shadow=True):
    if shadow: d.text((xy[0] + 1, xy[1] + 1), s, font=f, fill=(0, 0, 0, 170), anchor=anchor)
    d.text(xy, s, font=f, fill=fill if len(fill) == 4 else (*fill, 255), anchor=anchor)

def rounded(d, box, r, fill, outline=None, width=1):
    d.rounded_rectangle(box, r, fill=fill, outline=outline, width=width)

# ------------------------------------------------------------------ frame composition
def compose(frame_bgr, scene, vt, lens, fidx, fps, rows):
    cfg = scene.cfg; h, w = frame_bgr.shape[:2]; S = w / 1920.0
    mt, cam, lat, lon = scene.pose(vt, lens)
    if cfg.get("surface_tracking", True):
        # IMU attitude is kept (sun-glint verified); only the surface height under the drone is
        # measured from image motion so the patches stay put on the uneven lakebed.
        if scene.hs is None: scene.hs = SurfaceHeight(cam.C[2], fps); scene.prev_cam = None
        hh = scene.hs(frame_bgr, scene.prev_cam, cam); scene.prev_cam = cam
        cam = CameraPose(lens, cam.R, np.array([cam.C[0], cam.C[1], hh]))
    rx = cam.C; dt, sats = sat_state(scene, mt, rx)
    hz = horizon_mask_for(cam, w, h)
    water, prior = segment_water(frame_bgr, cam, scene, hz)
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    pulse = (fidx / fps) % 2.0 / 2.0

    # ---- water tint + shoreline glow
    tint = np.zeros((h, w, 4), np.uint8)
    tint[water > 0] = col(WATER, 62)
    edge = cv2.morphologyEx(water, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))
    glow = cv2.GaussianBlur(edge.astype(np.float32), (0, 0), 3.0 * S) * 1.6
    tint[..., :3] = np.where(edge[..., None] > 0, (180, 235, 255), tint[..., :3])
    tint[..., 3] = np.clip(np.maximum(tint[..., 3], edge * 230 + np.clip(glow * 120, 0, 120)), 0, 255).astype(np.uint8)
    out = blend(rgb, tint)

    # ---- world grid on the water plane (context, fades with range)
    g = np.zeros((h, w, 4), np.uint8); step = cfg["grid_m"]
    cx, cy = np.round(rx[:2] / step) * step; R_ = cfg["grid_extent_m"]
    segs = []
    for k in np.arange(-R_, R_ + 1, step):
        segs.append(densify(np.array([[cx + k, cy - R_, 0], [cx + k, cy + R_, 0]], float), 6.0))
        segs.append(densify(np.array([[cx - R_, cy + k, 0], [cx + R_, cy + k, 0]], float), 6.0))
    for seg in segs:
        uv, ok = clip_poly_front(cam, seg)
        ui = np.clip(uv, -1, 1e5).astype(np.int64); inside = ok & (ui[:, 0] >= 0) & (ui[:, 0] < w) & (ui[:, 1] >= 0) & (ui[:, 1] < h)
        below = np.zeros(len(seg), bool); below[inside] = hz[ui[inside, 1], ui[inside, 0]] > 0
        al = (70 * np.clip(1 - np.linalg.norm(seg[:, :2] - rx[:2], axis=1) / R_, 0, 1)).astype(int)
        good = below[:-1] & below[1:] & (al[:-1] > 4)
        for i in np.where(good)[0]:
            cv2.line(g, (int(ui[i, 0]), int(ui[i, 1])), (int(ui[i + 1, 0]), int(ui[i + 1, 1])), (200, 225, 240, int(al[i])), 1, cv2.LINE_AA)
    out = blend(out, g)

    # ---- mapped full-pool shoreline (dashed)
    sh = np.zeros((h, w, 4), np.uint8)
    uv, ok = clip_poly_front(cam, scene.pool3)
    for i in range(0, len(uv) - 1):
        if ok[i] and ok[i + 1] and (i // 3) % 2 == 0 and poly_ok(uv[i:i + 2]):
            cv2.line(sh, tuple(np.int32(uv[i])), tuple(np.int32(uv[i + 1])), (255, 255, 255, 120), max(1, int(1.5 * S)), cv2.LINE_AA)
    out = blend(out, sh)

    # ---- horizon line
    hl = np.zeros((h, w, 4), np.uint8)
    az = np.radians(np.arange(0, 360, 0.5)); dip = math.acos(6371000 / (6371000 + rx[2]))
    dd = np.c_[np.sin(az) * math.cos(dip), np.cos(az) * math.cos(dip), -np.full_like(az, math.sin(dip))]
    huv, hok = cam.project_dir(dd); hok &= (dd @ cam.R)[:, 2] > 0
    for i in range(len(huv) - 1):
        if hok[i] and hok[i + 1] and abs(huv[i, 0] - huv[i + 1, 0]) < w / 3:
            cv2.line(hl, tuple(np.int32(huv[i])), tuple(np.int32(huv[i + 1])), (255, 255, 255, 90), 1, cv2.LINE_AA)
    out = blend(out, hl)

    # ---- specular points and Fresnel zones
    foot = np.zeros((h, w, 4), np.uint8); items = []
    for s in sats:
        if s["el"] < cfg["elevation_mask_deg"]: continue
        # reflect off the actual local surface: water level over water, solved lakebed height on land
        hloc = scene.terrain.water_z if scene.terrain is not None else 0.0
        for _ in range(3 if scene.terrain is not None else 0):
            c_, _, _ = fresnel_zone(s["enu"] - [0, 0, hloc], rx - [0, 0, hloc], scene.wl, 8)
            hloc = float(scene.terrain.height(c_[0], c_[1]))
        off = np.array([0, 0, hloc]); camh = CameraPose(lens, cam.R, cam.C - off)
        center, ring, resid = fresnel_zone(s["enu"] - off, rx - off, scene.wl, 96)
        uvr, okr = clip_poly_front(camh, ring); uvc, okc = clip_poly_front(camh, center[None])
        inview = okr.all() and poly_ok(uvr) and cv2.intersectConvexConvex(
            cv2.convexHull(uvr.astype(np.float32)), np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float32))[0] > 1
        dist = float(np.linalg.norm(center[:2] - rx[:2]))
        a_ = ring[:, :2] - center[:2]; azr = math.radians(s["az"])
        along = np.array([math.sin(azr), math.cos(azr)]); across = np.array([math.cos(azr), -math.sin(azr)])
        L, Wd = 2 * np.abs(a_ @ along).max(), 2 * np.abs(a_ @ across).max()
        wf = None
        if inview:
            mask = np.zeros((h, w), np.uint8); cv2.fillPoly(mask, [np.int32(uvr)], 1)
            vis = mask & hz; n = vis.sum()
            if n > 20: wf = float((water & vis).sum() / n)
        pool_in = cv2.pointPolygonTest(scene.pool.astype(np.float32), (float(center[0]), float(center[1])), False) >= 0
        rows.append(dict(video_s=round(vt, 4), mission_s=round(mt, 4), utc=dt.isoformat(), sat=s["sid"], el_deg=round(s["el"], 3),
                         az_deg=round(s["az"], 3), spec_e_m=round(center[0], 3), spec_n_m=round(center[1], 3),
                         range_from_nadir_m=round(dist, 2), zone_along_m=round(L, 2), zone_across_m=round(Wd, 2),
                         in_view=bool(inview), px=round(float(uvc[0, 0]), 1) if okc[0] else "", py=round(float(uvc[0, 1]), 1) if okc[0] else "",
                         water_fraction_image=round(wf, 3) if wf is not None else "", inside_mapped_full_pool=bool(pool_in)))
        rows[-1]["surface_height_m_rel_plane"] = round(hloc, 2)
        if inview: items.append(dict(s=s, center=center, uvr=uvr, uvc=uvc[0] if okc[0] else None, wf=wf, L=L, Wd=Wd, dist=dist, camh=camh, off=off))
    # far first so near footprints paint on top. Each zone is shaded per pixel on the ground
    # plane (exact path-excess field), so it foreshortens and keeps the ground texture.
    items.sort(key=lambda it: -it["dist"])
    fimg = out.astype(np.float32)
    for it in items:
        s_ = it["s"]; ring3 = fresnel_zone(s_["enu"] - it["off"], rx - it["off"], scene.wl, 96)[1]
        azr = math.radians(s_["az"]); al = np.array([math.sin(azr), math.cos(azr)]); ac = np.array([math.cos(azr), -math.sin(azr)])
        pc_ = (ring3[:, :2] - it["center"][:2]) @ ac; pa_ = (ring3[:, :2] - it["center"][:2]) @ al
        on_water = it["wf"] is not None and it["wf"] > 0.5
        zopts = dict(fill_min=0.10, fill_max=0.45 if on_water else 0.34, tint=1.0, grid_m=None, minor_m=pc_.max() - pc_.min(),
                     grid_px=1.1 * S, grid_alpha=0.5, rim_px=2.3 * S, axis_ext=max(pa_.max(), -pa_.min()) * 1.35, dash_m=3.0,
                     ripple=pulse if on_water else None, style="paint", rim_frac=0.07, rim_alpha=0.95,
                     fill_alpha=0.30 if on_water else 0.24, paint_gain=1.05)
        shade_zone(fimg, it["camh"], s_["enu"] - it["off"], s_["dir"], s_["az"], it["center"], it["uvr"], scene.wl, CCOL[s_["const"]], zopts)
    out = np.clip(fimg, 0, 255).astype(np.uint8)

    # ---- incident rays: specular point -> satellite (short 3D segment rising from the surface)
    ray = np.zeros((h, w, 4), np.uint8)
    for it in items:
        c = CCOL[it["s"]["const"]]; d = it["s"]["dir"]
        seg = it["center"] + it["off"] + np.linspace(0, cfg["ray_length_m"], 24)[:, None] * d[None]
        su, so = clip_poly_front(cam, seg)
        for i in range(len(seg) - 1):
            if so[i] and so[i + 1] and poly_ok(su[i:i + 2]):
                cv2.line(ray, tuple(np.int32(su[i])), tuple(np.int32(su[i + 1])), col(c, int(200 * (1 - i / 24))), max(1, int(1.4 * S)), cv2.LINE_AA)
    out = blend(out, ray)

    # ---- satellites visible in the sky part of the frame
    sky_items = []
    for s in sats:
        if s["el"] < 0: continue
        Pc = s["dir"] @ cam.R
        if Pc[2] <= 0: continue
        uv, ok = cam.lens.project_cam(Pc[None])
        if ok[0] and 0 <= uv[0, 0] < w and 0 <= uv[0, 1] < h: sky_items.append((s, uv[0]))
    # sun + predicted sun-glint direction (mirror of the sun about the water plane)
    sun_d, sun_az, sun_el = sun_enu_dir(dt, lat, lon)
    glint_d = sun_d * np.array([1, 1, -1.0])
    gl_uv, gl_ok = cam.project_dir(glint_d[None]); gl_ok &= (glint_d @ cam.R)[2] > 0
    glint_px = gl_uv[0] if gl_ok[0] and 0 <= gl_uv[0, 0] < w and 0 <= gl_uv[0, 1] < h else None

    # ================= chase view (true ground ellipses)
    chase_info = None
    if cfg.get("chase_view", True):
        PW, PH = int(cfg["chase_w"] * S), int(cfg["chase_h"] * S)
        if scene.chase is None or scene.chase.W != PW: scene.chase = ChaseCam(PW, PH, cfg["chase_hfov_deg"])
        panel, plabels, dpos = render_chase(scene.chase, frame_bgr, cam, scene, sats, {it["s"]["sid"] for it in items}, CCOL, cfg)
        px0, py0 = w - PW - int(16 * S), h - int(272 * S) - int(16 * S) - PH - int(14 * S)
        chase_info = (px0, py0, PW, PH, plabels, dpos)

    # ================= PIL text/HUD layer
    if chase_info is not None:
        px0, py0, PW, PH, plabels, dpos = chase_info
        out = out.copy(); out[py0:py0 + PH, px0:px0 + PW] = cv2.cvtColor(panel, cv2.COLOR_BGR2RGB)
    pil = Image.fromarray(out); hud = Image.new("RGBA", (w, h), (0, 0, 0, 0)); d = ImageDraw.Draw(hud, "RGBA")
    F = lambda n, px: font(n, int(px * S))
    fB, fM, fS, fXS = F("Inter-SemiBold.otf", 22), F("Inter-Medium.otf", 17), F("Inter-Regular.otf", 15), F("Inter-Regular.otf", 13)
    fT = F("InterDisplay-Bold.otf", 26)

    # footprint labels with leader pins
    placed = [(0, 0, int(560 * S), int(150 * S))] + ([(chase_info[0] - 8, chase_info[1] - 8, w, chase_info[1] + chase_info[3] + 8)] if chase_info else []) + [ (w - int(330 * S), h - int(330 * S), w, h), (0, h - int(320 * S), int(360 * S), h),
              (0, 0, w, int(46 * S))]
    for it in sorted(items, key=lambda it: it["dist"]):
        if it["uvc"] is None: continue
        x, y = it["uvc"]
        if not (0 <= x < w and 0 <= y < h): continue
        c = CCOL[it["s"]["const"]]; s = it["s"]
        tag = "WATER" if (it["wf"] is not None and it["wf"] > 0.5) else ("LAND" if it["wf"] is not None else "")
        l1 = f'{s["sid"]}'; l2 = f'el {s["el"]:.0f}°  ·  {it["L"]:.0f}×{it["Wd"]:.0f} m'
        tw = max(d.textlength(l1, font=fB) + (d.textlength(tag, font=fXS) + 22 * S if tag else 0), d.textlength(l2, font=fXS)) + 20 * S
        th = 50 * S; best = None
        for dx, dy in [(18, -80), (18, 40), (-18 - tw / S, -80), (-18 - tw / S, 40), (30, -130), (-30 - tw / S, -130), (40, 80), (-40 - tw / S, 80)]:
            bx, by = x + dx * S, y + dy * S
            box = (bx, by, bx + tw, by + th)
            if box[0] < 6 or box[1] < 50 * S or box[2] > w - 6 or box[3] > h - 6: continue
            hit = any(box[0] < p[2] and box[2] > p[0] and box[1] < p[3] and box[3] > p[1] for p in placed)
            if not hit: best = box; break
        if best is None: continue
        placed.append(tuple(int(v) for v in best))
        bx, by, ex, ey = best
        ax_ = bx if bx > x else ex; ay_ = by + th / 2
        d.line([(x, y), (ax_, ay_)], fill=(*c, 150), width=max(1, int(1.2 * S)))
        rounded(d, best, int(8 * S), fill=(*PANEL, 178), outline=(*c, 230), width=max(1, int(1.4 * S)))
        d.rectangle([bx, by + 8 * S, bx + 3 * S, ey - 8 * S], fill=(*c, 255))
        text(d, (bx + 12 * S, by + 6 * S), l1, fB, c)
        if tag:
            tx = bx + 12 * S + d.textlength(l1, font=fB) + 10 * S; tc = WATER if tag == "WATER" else (196, 170, 130)
            rounded(d, (tx, by + 10 * S, tx + d.textlength(tag, font=fXS) + 12 * S, by + 28 * S), int(5 * S), fill=(*tc, 235))
            text(d, (tx + 6 * S, by + 11 * S), tag, fXS, (8, 18, 28), shadow=False)
        text(d, (bx + 12 * S, by + 31 * S), l2, fXS, DIM)

    # satellites in sky
    for s, (x, y) in sky_items:
        c = CCOL[s["const"]]; r = 9 * S
        d.polygon([(x, y - r), (x + r, y), (x, y + r), (x - r, y)], outline=(*c, 255), fill=(*c, 90), width=max(2, int(2 * S)))
        for k, rr in enumerate([16, 24]):
            d.ellipse([x - rr * S, y - rr * S, x + rr * S, y + rr * S], outline=(*c, 110 - 40 * k), width=1)
        l2 = f'el {s["el"]:.0f}°  az {s["az"]:.0f}°  ·  in view'; lw = d.textlength(l2, font=fXS)
        lx = x + 26 * S if x + 26 * S + lw < w - 8 else x - 26 * S - lw
        ly = min(max(y - 14 * S, 56 * S), h - 40 * S)
        text(d, (lx, ly), f'{s["sid"]}', fM, c); text(d, (lx, ly + 18 * S), l2, fXS, INK)
    if glint_px is not None:
        x, y = glint_px; r = 14 * S
        for k in range(8):
            a = k * math.pi / 4; d.line([(x + math.cos(a) * r * .6, y + math.sin(a) * r * .6), (x + math.cos(a) * r * 1.2, y + math.sin(a) * r * 1.2)], fill=(255, 236, 160, 220), width=max(1, int(1.5 * S)))
        d.ellipse([x - r * .45, y - r * .45, x + r * .45, y + r * .45], outline=(255, 236, 160, 230), width=max(1, int(1.5 * S)))
        text(d, (x + 20 * S, y - 8 * S), "sun glint · predicted (check)", fXS, (255, 236, 160))

    # ---- chase panel frame + labels
    if chase_info is not None:
        px0, py0, PW, PH, plabels, dpos = chase_info
        rounded(d, (px0 - 2, py0 - 2, px0 + PW + 2, py0 + PH + 2), int(10 * S), fill=None, outline=(255, 255, 255, 120), width=max(1, int(1.5 * S)))
        rounded(d, (px0 + 8 * S, py0 + 8 * S, px0 + 8 * S + d.textlength("CHASE VIEW  ·  true ground ellipses", font=fXS) + 16 * S, py0 + 30 * S), int(6 * S), fill=(*PANEL, 190))
        text(d, (px0 + 16 * S, py0 + 10 * S), "CHASE VIEW  ·  true ground ellipses", fXS, INK, shadow=False)
        text(d, (px0 + PW - 10 * S, py0 + PH - 22 * S), "ground texture = current GoPro frame re-projected", F("Inter-Regular.otf", 11), DIM, anchor="ra")
        fL = F("Inter-SemiBold.otf", 13); taken = []
        for s_, uc, strong in sorted(plabels, key=lambda t: not t[2]):
            if uc is None or not strong: continue
            x, y = px0 + uc[0], py0 + uc[1]
            if not (px0 + 4 < x < px0 + PW - 40 and py0 + 34 * S < y < py0 + PH - 30 * S): continue
            tx, ty = x + 6 * S, y - 16 * S
            if any(abs(tx - a) < 34 * S and abs(ty - b) < 15 * S for a, b in taken): continue
            taken.append((tx, ty)); text(d, (tx, ty), s_["sid"], fL, CCOL[s_["const"]])
        if dpos is not None:
            text(d, (px0 + dpos[0] + 12 * S, py0 + dpos[1] - 8 * S), "HERON", F("Inter-SemiBold.otf", 12), INK)

    # ---- compass tape along top
    fw = cam.R[:, 2]; hdg = math.degrees(math.atan2(fw[0], fw[1])) % 360
    tape_w = int(820 * S); tx0 = (w - tape_w) // 2; ty = int(18 * S); span = 120.0
    rounded(d, (tx0, ty - 6 * S, tx0 + tape_w, ty + 34 * S), int(10 * S), fill=(*PANEL, 140))
    for deg in range(0, 360, 5):
        dd_ = ((deg - hdg + 180) % 360) - 180
        if abs(dd_) > span / 2: continue
        x = tx0 + tape_w / 2 + dd_ / span * tape_w; big = deg % 30 == 0
        d.line([(x, ty), (x, ty + (12 if big else 6) * S)], fill=(220, 230, 240, 200 if big else 110), width=1)
        if big:
            lab = {0: "N", 90: "E", 180: "S", 270: "W"}.get(deg, str(deg))
            text(d, (x, ty + 14 * S), lab, fXS, INK, anchor="ma", shadow=False)
    for s in sats:
        if s["el"] < cfg["elevation_mask_deg"]: continue
        dd_ = ((s["az"] - hdg + 180) % 360) - 180
        if abs(dd_) > span / 2: continue
        x = tx0 + tape_w / 2 + dd_ / span * tape_w; c = CCOL[s["const"]]
        d.polygon([(x - 4 * S, ty - 6 * S), (x + 4 * S, ty - 6 * S), (x, ty + 1 * S)], fill=(*c, 255))
    d.polygon([(w / 2 - 7 * S, ty + 34 * S), (w / 2 + 7 * S, ty + 34 * S), (w / 2, ty + 26 * S)], fill=(255, 255, 255, 255))
    text(d, (w / 2, ty + 37 * S), f"{hdg:05.1f}°", fXS, INK, anchor="ma")

    # ---- title / telemetry panel (top-left)
    nw = sum(1 for it in items if it["wf"] is not None and it["wf"] > 0.5)
    rounded(d, (16 * S, 16 * S, 556 * S, 146 * S), int(14 * S), fill=(*PANEL, 165))
    text(d, (32 * S, 26 * S), "HERON  ·  GNSS-R SPECULAR FORECAST", fT, INK)
    text(d, (32 * S, 62 * S), f'{dt.strftime("%Y-%m-%d  %H:%M:%S")} UTC   ·   video {vt:6.1f} s', fS, DIM)
    spd = np.linalg.norm(scene.tel.velocity(mt)[:2])
    text(d, (32 * S, 86 * S), f'h {rx[2]:4.1f} m above water plane   ·   {spd:3.1f} m/s   ·   cam {hdg:05.1f}° / {math.degrees(math.asin(-fw[2])):.0f}° down', fS, DIM)
    text(d, (32 * S, 112 * S), f'{len(items)} first Fresnel zones in view', fM, INK)
    tx = 32 * S + d.textlength(f'{len(items)} first Fresnel zones in view', font=fM) + 12 * S
    rounded(d, (tx, 112 * S, tx + d.textlength(f"{nw} on water", font=fXS) + 16 * S, 133 * S), int(6 * S), fill=(*WATER, 235))
    text(d, (tx + 8 * S, 114 * S), f"{nw} on water", fXS, (8, 18, 28), shadow=False)

    # ---- legend (bottom-centre)
    lx = w / 2 - 300 * S; ly = h - 40 * S
    rounded(d, (lx - 14 * S, ly - 10 * S, lx + 614 * S, ly + 26 * S), int(10 * S), fill=(*PANEL, 140))
    for k in cfg["constellations"]:
        d.ellipse([lx, ly + 2 * S, lx + 12 * S, ly + 14 * S], fill=(*CCOL[k], 255)); text(d, (lx + 18 * S, ly - 1 * S), CNAME[k], fXS, INK, shadow=False)
        lx += d.textlength(CNAME[k], font=fXS) + 40 * S
    d.rectangle([lx, ly + 2 * S, lx + 16 * S, ly + 14 * S], fill=(*WATER, 120), outline=(*WATER, 255)); text(d, (lx + 22 * S, ly - 1 * S), "segmented water", fXS, INK, shadow=False)

    # ---- mini-map (bottom-left)
    mm = int(272 * S); mx0, my0 = int(16 * S), h - mm - int(16 * S); scale = mm / cfg["minimap_span_m"]
    rounded(d, (mx0, my0, mx0 + mm, my0 + mm), int(14 * S), fill=(*PANEL, 175))
    def m2p(p):  # north-up, centred on aircraft
        return (mx0 + mm / 2 + (p[0] - rx[0]) * scale, my0 + mm / 2 - (p[1] - rx[1]) * scale)
    off = lambda p: (m2p(p)[0] - mx0, m2p(p)[1] - my0)
    L_fov = Image.new("RGBA", (mm, mm), (0, 0, 0, 0)); L_pool = Image.new("RGBA", (mm, mm), (0, 0, 0, 0))
    mapimg = Image.new("RGBA", (mm, mm), (0, 0, 0, 0)); md = ImageDraw.Draw(mapimg, "RGBA")
    ImageDraw.Draw(L_pool).polygon([off(p) for p in scene.pool], fill=(*WATER, 70), outline=(*WATER, 200))
    # camera ground footprint (frame border rays to plane, clipped at 600 m)
    border = np.r_[np.c_[np.linspace(0, w, 24), np.zeros(24)], np.c_[np.full(14, w), np.linspace(0, h, 14)],
                   np.c_[np.linspace(w, 0, 24), np.full(24, h)], np.c_[np.zeros(14), np.linspace(h, 0, 14)]]
    gp, gok = cam.ray_ground(border)
    rays = cam.lens.unproject(border) @ cam.R.T
    far = np.linalg.norm(gp[:, :2] - rx[:2], axis=1) > 600
    gp = np.where((gok & ~far)[:, None], gp,
                  rx + np.c_[rays[:, :2] / np.maximum(np.linalg.norm(rays[:, :2], axis=1)[:, None], 1e-6) * 600, np.zeros(len(rays))])
    ImageDraw.Draw(L_fov).polygon([off(p) for p in gp], fill=(255, 255, 255, 34), outline=(255, 255, 255, 150))
    md.line([off(p) for p in scene.track], fill=(255, 255, 255, 90), width=1)
    for s in sats:
        if s["el"] < cfg["elevation_mask_deg"]: continue
        c0, _, _ = fresnel_zone(s["enu"], rx, scene.wl, 8); q = off(c0)
        if 0 <= q[0] < mm and 0 <= q[1] < mm:
            c = CCOL[s["const"]]; md.ellipse([q[0] - 3.5 * S, q[1] - 3.5 * S, q[0] + 3.5 * S, q[1] + 3.5 * S], fill=(*c, 255))
    md.polygon([(mm / 2, mm / 2 - 7 * S), (mm / 2 + 5 * S, mm / 2 + 6 * S), (mm / 2 - 5 * S, mm / 2 + 6 * S)], fill=(255, 255, 255, 255))
    mapimg = Image.alpha_composite(Image.alpha_composite(L_pool, L_fov), mapimg)
    rmask = Image.new("L", (mm, mm), 0); ImageDraw.Draw(rmask).rounded_rectangle((0, 0, mm - 1, mm - 1), int(14 * S), fill=255)
    hud.paste(mapimg, (mx0, my0), Image.composite(mapimg, Image.new("RGBA", (mm, mm), (0, 0, 0, 0)), rmask))
    text(d, (mx0 + 12 * S, my0 + 8 * S), "MAP  ·  north up", fXS, DIM, shadow=False)
    sb = 100 * scale; d.line([(mx0 + 14 * S, my0 + mm - 16 * S), (mx0 + 14 * S + sb, my0 + mm - 16 * S)], fill=(255, 255, 255, 200), width=2)
    text(d, (mx0 + 18 * S + sb, my0 + mm - 25 * S), "100 m", fXS, DIM, shadow=False)

    # ---- sky plot (bottom-right)
    sp = int(272 * S); sx0, sy0 = w - sp - int(16 * S), h - sp - int(16 * S); cxp, cyp = sx0 + sp / 2, sy0 + sp / 2 + 8 * S; rad = sp * 0.40
    rounded(d, (sx0, sy0, sx0 + sp, sy0 + sp), int(14 * S), fill=(*PANEL, 175))
    for e in [0, 30, 60]:
        rr = rad * (90 - e) / 90; d.ellipse([cxp - rr, cyp - rr, cxp + rr, cyp + rr], outline=(255, 255, 255, 60 if e else 110), width=1)
    for a in range(0, 360, 45):
        d.line([(cxp, cyp), (cxp + rad * math.sin(math.radians(a)), cyp - rad * math.cos(math.radians(a)))], fill=(255, 255, 255, 35), width=1)
    text(d, (cxp, cyp - rad - 16 * S), "N", fXS, DIM, anchor="ma", shadow=False)
    # camera field of view on the sky dome (edge rays, elevation >= 0 part)
    dirs = cam.lens.unproject(border) @ cam.R.T
    fov = []
    for v in dirs:
        el = math.degrees(math.asin(np.clip(v[2], -1, 1))); az_ = math.degrees(math.atan2(v[0], v[1]))
        rr = rad * (90 - max(el, -20)) / 90 if el >= 0 else rad * (1 + min(-el, 20) / 90 * 0.0)
        fov.append((cxp + rr * math.sin(math.radians(az_)), cyp - rr * math.cos(math.radians(az_))))
    d.polygon(fov, fill=(255, 255, 255, 22), outline=(255, 255, 255, 90))
    inview_ids = {it["s"]["sid"] for it in items}
    for s in sats:
        if s["el"] < 0: continue
        rr = rad * (90 - s["el"]) / 90; x = cxp + rr * math.sin(math.radians(s["az"])); y = cyp - rr * math.cos(math.radians(s["az"]))
        c = CCOL[s["const"]]; big = s["sid"] in inview_ids
        if s["el"] < cfg["elevation_mask_deg"]: c = (110, 120, 130)
        rr2 = (5.5 if big else 3.5) * S
        d.ellipse([x - rr2, y - rr2, x + rr2, y + rr2], fill=(*c, 255 if big else 150), outline=(255, 255, 255, 220) if big else None)
        if big: text(d, (x + 7 * S, y - 7 * S), s["sid"], F("Inter-Medium.otf", 11), c, shadow=False)
    sx, sy = cxp + rad * (90 - sun_el) / 90 * math.sin(math.radians(sun_az)), cyp - rad * (90 - sun_el) / 90 * math.cos(math.radians(sun_az))
    d.ellipse([sx - 6 * S, sy - 6 * S, sx + 6 * S, sy + 6 * S], outline=(255, 236, 160, 230), width=2)
    text(d, (sx0 + 12 * S, sy0 + 8 * S), "SKY  ·  footprints in view ringed", fXS, DIM, shadow=False)

    # ---- honesty line
    text(d, (w / 2, h - 66 * S), "Predicted geometry (flat water plane, archived orbits). Not a measurement of received reflections.",
         fXS, (200, 210, 220), anchor="ma")

    pil = Image.alpha_composite(pil.convert("RGBA"), hud).convert("RGB")
    return cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2BGR), dict(items=len(items), on_water=nw, hdg=hdg, glint=glint_px, water_px=int(water.sum()))

# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--config", default=str(ROOT / "config_ar.json"))
    ap.add_argument("--preview", default=""); ap.add_argument("--max-seconds", type=float, default=None)
    ap.add_argument("--out", default=None); ap.add_argument("--frame-start", type=int, default=0); ap.add_argument("--frame-end", type=int, default=None)
    a = ap.parse_args(); cfg = json.loads(Path(a.config).read_text())
    scene = Scene(cfg); print("sync", scene.sync, flush=True)
    cap = cv2.VideoCapture(str(Path(cfg["clip_path"]).expanduser())); assert cap.isOpened(), cfg["clip_path"]
    sfps = cap.get(cv2.CAP_PROP_FPS); sfps = 24000 / 1001 if abs(sfps - 23.976) < 0.05 else sfps; n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    W, H = cfg["output_width"], cfg["output_height"]
    lens = Fisheye(DATA / cfg["lens_profile"], W, H, cfg.get("focal_scale", 1.0))
    if getattr(scene, "sfm_cam", None) is not None:          # bundle-adjusted intrinsics from the camera solve
        p = scene.sfm_cam; k = W / 1920.0; lens.fx, lens.fy, lens.cx, lens.cy = p[0] * k, p[1] * k, p[2] * k, p[3] * k; lens.D = np.array(p[4:8])
    rows = []
    if a.preview:
        for ts in [float(x) for x in a.preview.split(",")]:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(ts * sfps))); ok, fr = cap.read(); assert ok
            fr = cv2.resize(fr, (W, H), interpolation=cv2.INTER_AREA); scene.water_ema = None; scene.chase = None; scene.lock = None; scene.hs = None
            vt = cfg["clip_video_start_s"] + ts * cfg.get("clip_time_scale", 1.0)
            img, info = compose(fr, scene, vt, lens, int(ts * sfps), sfps, rows)
            p = OUT / f"preview_{ts:06.1f}.jpg"; p.parent.mkdir(exist_ok=True); cv2.imwrite(str(p), img, [cv2.IMWRITE_JPEG_QUALITY, 92]); print(p, info)
        return
    total = n if a.max_seconds is None else min(n, int(a.max_seconds * sfps))
    if a.frame_end is not None: total = min(total, a.frame_end)
    if a.frame_start: cap.set(cv2.CAP_PROP_POS_FRAMES, a.frame_start)
    dest = Path(a.out or OUT / cfg["output_name"]); dest.parent.mkdir(exist_ok=True)
    ff = shutil.which("ffmpeg") or cfg.get("ffmpeg")
    proc = subprocess.Popen([ff, "-y", "-hide_banner", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
                             "-r", f"{sfps:.6f}", "-i", "-", "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                             "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dest)], stdin=subprocess.PIPE)
    t0 = time.time(); stats = []
    for i in range(a.frame_start, total):
        ok, fr = cap.read()
        if not ok: break
        fr = cv2.resize(fr, (W, H), interpolation=cv2.INTER_AREA)
        vt = cfg["clip_video_start_s"] + (i / sfps) * cfg.get("clip_time_scale", 1.0)
        img, info = compose(fr, scene, vt, lens, i, sfps, rows); stats.append(info)
        proc.stdin.write(img.tobytes())
        if i % int(sfps * 10) == 0: print(f"{i}/{total}  {time.time() - t0:.0f}s  {info}", flush=True)
    proc.stdin.close(); assert proc.wait() == 0
    with (dest.with_suffix(".csv")).open("w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=rows[0].keys()); wr.writeheader(); wr.writerows(rows)
    rep = dict(frames=len(stats), fps=sfps, sync=scene.sync, config=cfg,
               footprints_in_view_min=min(s["items"] for s in stats), footprints_in_view_max=max(s["items"] for s in stats),
               on_water_mean=float(np.mean([s["on_water"] for s in stats])))
    dest.with_suffix(".json").write_text(json.dumps(rep, indent=2, default=str)); print(dest, rep)

if __name__ == "__main__":
    main()
