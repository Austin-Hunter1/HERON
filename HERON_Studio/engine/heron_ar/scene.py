"""The flight as the renderer sees it: telemetry, the camera pose for any video time (image-solved
keyframes, or GPS + IMU where there is no solve), satellites, the reservoir outline and terrain,
plus the projection and water-segmentation helpers the renderer and the figures share.
"""
from __future__ import annotations
import json, math
import numpy as np, cv2
from .geometry import *
from .gnss.gnss import load_l5_sats
from .chase_view import pool_raster

# constellation colours (RGB)
CCOL = {"G": (255, 196, 61), "E": (77, 214, 255), "C": (255, 112, 180), "J": (120, 235, 140)}
CNAME = {"G": "GPS L5", "E": "Galileo E5a", "C": "BeiDou B2a", "J": "QZSS L5"}

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

    def set_boresight(self, pitch, yaw, roll):
        """Small fixed rotation between the IMU-derived camera frame and the true optical frame.
        +pitch tilts the optical axis further DOWN, +yaw turns it RIGHT, +roll is clockwise in the image."""
        b = np.deg2rad([pitch, yaw, roll])
        self.Rb = rot_axis([1, 0, 0], -b[0]) @ rot_axis([0, 1, 0], b[1]) @ rot_axis([0, 0, 1], b[2])

    def mission_t(self, vt): return mission_time(vt, self.sync)

    def load_sfm(self, path, plane_offset_m):
        from scipy.spatial.transform import Rotation, RotationSpline
        from scipy.interpolate import CubicSpline
        z = np.load(path); vt = z["vt"]; u = np.r_[True, np.diff(vt) > 0]; vt = vt[u]         # strictly increasing times
        # A keyframe is trusted when it sits within pose_max_gps_error_m of the GPS track. Short runs are
        # merged or dropped (about 2 s) so the overlays don't flicker on and off around a marginal stretch.
        ok = np.ones(len(vt), bool)
        if "gps_res" in z.files:
            from scipy.ndimage import binary_closing, binary_opening
            ok = z["gps_res"][u] <= self.cfg.get("pose_max_gps_error_m", 10.0)
            n = max(1, int(round(1.0 / np.median(np.diff(vt))))); st = np.ones(2 * n + 1, bool)
            ok = binary_opening(binary_closing(ok, st, border_value=1), st, border_value=1)
        self.sfm = dict(vt=vt, rot=RotationSpline(vt, Rotation.from_matrix(z["Rwc"][u])), pos=CubicSpline(vt, z["Cw"][u]),
                        ok=ok, dz=plane_offset_m)

    def pose(self, vt, lens):
        if getattr(self, "sfm", None) is not None:
            return self.pose_sfm(vt, lens)
        return self.pose_imu(vt, lens)

    def pose_sfm(self, vt, lens):
        """Image-solved pose: a C2 spline through the SfM keyframes (rotation and position).
        Holding out keyframes on the 10 Nov flight, the rotation spline was within 0.009 deg (median) of the
        solved pose at 8-frame spacing, against 0.033 deg for slerp and 0.071 deg for slerp carried by the
        GoPro gyro: the gyro does not follow the stabilised video (0.3 deg rms apart per 1/6 s)."""
        S_ = self.sfm; mt = self.mission_t(vt)
        _, lat, lon, _ = self.tel.position(mt)
        t = float(np.clip(vt, S_["vt"][0], S_["vt"][-1]))
        return mt, CameraPose(lens, S_["rot"](t).as_matrix(), S_["pos"](t) - np.array([0, 0, S_["dz"]])), lat, lon

    def pose_trusted(self, vt):
        """False where the image solve disagrees with the GPS track (e.g. standing on the ground before take-off)."""
        S_ = getattr(self, "sfm", None)
        if S_ is None: return True
        k = int(np.clip(np.searchsorted(S_["vt"], vt) - 1, 0, len(S_["vt"]) - 2))
        return bool(S_["ok"][k] and S_["ok"][k + 1])

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
def poly_ok(uv, lim=8000): return np.isfinite(uv).all() and np.abs(uv).max() < lim
