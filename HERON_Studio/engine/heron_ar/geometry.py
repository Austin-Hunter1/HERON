"""HERON AR geometry core.

World frame: local East/North/Up metres, origin at recorded home lat/lon and at
the water-surface elevation (z = 0 is the reflecting plane).
Camera frame: OpenCV convention, x right, y down, z forward (optical axis).

Pipeline
  1. Clock sync: GoPro accelerometer vs drone IMU specific-force magnitude
     (0.3-4 Hz band), sliding-window cross-correlation, linear fit
     (offset + clock-rate term). Translational vibration passes through the
     gimbal unchanged, unlike yaw, which the gimbal smooths and delays.
  2. Camera attitude: complementary (Mahony-style) filter on the GoPro's own
     200 Hz gyro + accelerometer. Tilt is corrected with gravity PLUS the
     aircraft's kinematic acceleration from the EKF velocity, so banked turns
     do not tilt the horizon. Heading is weakly pulled toward aircraft yaw
     (gimbal in yaw-follow) with a long time constant; the gyro carries all
     short-term motion including gimbal lag.
  3. Lens: OpenCV fisheye model, Gyroflow community profile for HERO7 Black
     4K 16:9 Wide (HyperSmooth), scaled to the working resolution.
  4. Satellites: planner orbit sources (GPS/Galileo almanacs, BeiDou/QZSS TLE).
  5. Specular point + first Fresnel zone: exact flat-plane mirror geometry.
"""
from __future__ import annotations
import json, math
import os
from pathlib import Path
from datetime import datetime, timezone, timedelta
import numpy as np
import pandas as pd
from scipy.signal import butter, filtfilt

ROOT = Path(__file__).resolve().parent.parent          # repository root
# Per-flight folders can be redirected with environment variables (HERON Studio does this);
# without them everything stays in the repository as before.
DATA = Path(os.environ.get("HERON_DATA") or ROOT / "data")
CACHE = Path(os.environ.get("HERON_CACHE") or ROOT / "cache"); OUT = Path(os.environ.get("HERON_OUT") or ROOT / "out")

def resolve(p):
    """Paths in configs are relative to the repository root unless absolute."""
    p = Path(p).expanduser(); return p if p.is_absolute() else ROOT / p

def log_so3(R):
    c = np.clip((np.trace(R) - 1) / 2, -1, 1); th = np.arccos(c)
    if th < 1e-9: return np.zeros(3)
    return th / (2 * np.sin(th)) * np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]])
MISSION_T0_US = int(os.environ.get("HERON_T0_US") or 275844777)   # mission-time zero used by all HERON exports
WGS_E2 = 6.69437999014e-3

# ---------------------------------------------------------------- geodesy
def lla2ecef(lat, lon, h):
    lat, lon = np.deg2rad(lat), np.deg2rad(lon)
    n = 6378137 / np.sqrt(1 - WGS_E2 * np.sin(lat) ** 2)
    return np.array([(n + h) * np.cos(lat) * np.cos(lon), (n + h) * np.cos(lat) * np.sin(lon),
                     (n * (1 - WGS_E2) + h) * np.sin(lat)])

def enu_basis(lat, lon):
    lat, lon = np.deg2rad([lat, lon]); s, c = np.sin, np.cos
    return np.array([[-s(lon), c(lon), 0],
                     [-s(lat) * c(lon), -s(lat) * s(lon), c(lat)],
                     [c(lat) * c(lon), c(lat) * s(lon), s(lat)]])

class LocalFrame:
    def __init__(self, lat0, lon0, h0):
        self.lat0, self.lon0, self.h0 = lat0, lon0, h0
        self.B = enu_basis(lat0, lon0); self.o = lla2ecef(lat0, lon0, h0)
    def enu(self, lat, lon, h):
        return self.B @ (lla2ecef(lat, lon, h) - self.o)
    def ecef2enu(self, xyz):
        return self.B @ (np.asarray(xyz) - self.o)
    def en_from_ll(self, lon, lat):
        """Fast local tangent approximation for polygons (sub-cm over a few km)."""
        lat0 = np.deg2rad(self.lat0)
        rn = 6378137 * (1 - WGS_E2) / (1 - WGS_E2 * np.sin(lat0) ** 2) ** 1.5
        re = 6378137 / np.sqrt(1 - WGS_E2 * np.sin(lat0) ** 2)
        return np.c_[np.deg2rad(np.asarray(lon) - self.lon0) * re * np.cos(lat0),
                     np.deg2rad(np.asarray(lat) - self.lat0) * rn]

# ---------------------------------------------------------------- rotations
def skew(v):
    return np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])

def expm_so3(w):
    th = np.linalg.norm(w)
    if th < 1e-12: return np.eye(3) + skew(w)
    k = skew(w / th)
    return np.eye(3) + math.sin(th) * k + (1 - math.cos(th)) * k @ k

def rot_axis(axis, ang):
    return expm_so3(np.asarray(axis, float) / np.linalg.norm(axis) * ang)

# GoPro IMU components -> camera axes (x right, y down, z forward).
# Established from data: gravity has ~0 on component_1 (image-horizontal);
# component_0 carries sin(depression); horizon near the top edge => c0 = -forward.
IMU2CAM = np.array([[0, 1, 0],
                    [0, 0, -1],
                    [-1, 0, 0]], float)

# ---------------------------------------------------------------- telemetry
class Telemetry:
    def __init__(self, water_elev_m=1578.0):
        P = pd.read_csv(DATA / "POS.csv"); A = pd.read_csv(DATA / "ATT.csv")
        G = pd.read_csv(DATA / "GPS.csv"); K = pd.read_csv(DATA / "XKF1.csv"); K = K[K.C == 1]
        R = pd.read_csv(DATA / "RFND.csv")
        self.pt = (P.TimeUS.values - MISSION_T0_US) / 1e6
        self.lat, self.lon, self.alt = P.Lat.values, P.Lng.values, P.Alt.values
        self.frame = LocalFrame(self.lat[0], self.lon[0], water_elev_m)
        self.water_elev = water_elev_m
        self.at = (A.TimeUS.values - MISSION_T0_US) / 1e6
        self.yaw = np.unwrap(np.deg2rad(A.Yaw.values))
        self.roll, self.pitch = np.deg2rad(A.Roll.values), np.deg2rad(A.Pitch.values)
        self.gt = (G.TimeUS.values - MISSION_T0_US) / 1e6
        self.gps_s = G.GWk.values * 604800.0 + G.GMS.values / 1000.0
        self.kt = (K.TimeUS.values - MISSION_T0_US) / 1e6
        self.vned = K[["VN", "VE", "VD"]].values
        self.rt = (R.TimeUS.values - MISSION_T0_US) / 1e6; self.rdist = R.Dist.values; self.rstat = R.Stat.values
        # kinematic acceleration (ENU) from EKF velocity, lightly smoothed
        tt = np.arange(self.kt[0], self.kt[-1], 0.02)
        v = np.c_[[np.interp(tt, self.kt, self.vned[:, i]) for i in range(3)]].T
        venu = np.c_[v[:, 1], v[:, 0], -v[:, 2]]
        b, a = butter(2, 1.0 / 25)
        venu = filtfilt(b, a, venu, axis=0)
        self.acc_t = tt; self.acc_enu = np.gradient(venu, tt, axis=0)
        self.vel_enu = venu

    def position(self, t):
        lat = np.interp(t, self.pt, self.lat); lon = np.interp(t, self.pt, self.lon); h = np.interp(t, self.pt, self.alt)
        return self.frame.enu(lat, lon, h), lat, lon, h

    def yaw_at(self, t):
        return np.interp(t, self.at, self.yaw)

    def kin_acc(self, t):
        return np.c_[[np.interp(t, self.acc_t, self.acc_enu[:, i]) for i in range(3)]].T

    def velocity(self, t):
        return np.array([np.interp(t, self.acc_t, self.vel_enu[:, i]) for i in range(3)])

    def utc(self, t):
        g = np.interp(t, self.gt, self.gps_s)
        return datetime(1980, 1, 6, tzinfo=timezone.utc) + timedelta(seconds=float(g) - 18)

    def rangefinder(self, t):
        i = np.searchsorted(self.rt, t)
        i = np.clip(i, 0, len(self.rt) - 1)
        return self.rdist[i] if self.rstat[i] == 4 else np.nan

# ---------------------------------------------------------------- GoPro IMU + sync
class GoProIMU:
    def __init__(self, folder=DATA / "gopro1"):
        g = pd.read_csv(folder / "GYRO.csv"); a = pd.read_csv(folder / "ACCL.csv")
        self.t = g.nominal_video_time_s.values
        self.gyro = g[["component_0", "component_1", "component_2"]].values @ IMU2CAM.T
        self.acc = a[["component_0", "component_1", "component_2"]].values @ IMU2CAM.T
        self.fs = 1 / np.median(np.diff(self.t))

def estimate_sync(imu: GoProIMU, verbose=False, search=(-120, 0)):
    """mission_t = video_t * (1 + rate) + offset, from accel-magnitude correlation."""
    I = pd.read_csv(DATA / "IMU.csv"); I = I[I.I == 0]
    It = (I.TimeUS.values - MISSION_T0_US) / 1e6; IA = I[["AccX", "AccY", "AccZ"]].values
    fs = 50.0; tg = np.arange(0, imu.t[-1], 1 / fs)
    gm = np.linalg.norm(np.c_[[np.interp(tg, imu.t, imu.acc[:, k]) for k in range(3)]].T, axis=1)
    td = np.arange(It[0], It[-1], 1 / fs)
    dm = np.linalg.norm(np.c_[[np.interp(td, It, IA[:, k]) for k in range(3)]].T, axis=1)
    bp = butter(2, [0.3 / (fs / 2), 4 / (fs / 2)], "band"); gm = filtfilt(*bp, gm); dm = filtfilt(*bp, dm)
    def corr(o, m):
        """Correlation at offset o over mask m, only where the log covers the video (else -1)."""
        m = m & (tg + o >= td[0]) & (tg + o <= td[-1])
        if m.sum() < fs * 20: return -1.0
        a, b = np.interp(tg[m] + o, td, dm), gm[m]
        if a.std() < 1e-9 or b.std() < 1e-9: return -1.0
        c = np.corrcoef(a, b)[0, 1]; return float(c) if np.isfinite(c) else -1.0
    # coarse global search (needs >= 60 s of overlap)
    full = np.ones(len(tg), bool)
    best = max(((corr(o, full) if ((tg + o >= td[0]) & (tg + o <= td[-1])).sum() >= fs * 60 else -1.0, o)
                for o in np.arange(search[0], search[1], 0.1)))
    if best[0] <= 0: raise RuntimeError("clock sync: the video and the flight log never move together; is this the right log?")
    o0 = best[1]; rows = []
    for s0 in np.arange(0, tg[-1] - 40, 10):
        m = (tg >= s0) & (tg < s0 + 40)
        cc = [(corr(o, m), o) for o in np.arange(o0 - .6, o0 + .6, .005)]
        c, o = max(cc)
        if c > 0: rows.append((s0 + 20, o, c))
    r = np.array(rows); w = np.clip(r[:, 2], 0, 1) ** 4
    p = np.polyfit(r[:, 0], r[:, 1], 1, w=w); res = r[:, 1] - np.polyval(p, r[:, 0])
    info = dict(rate=float(p[0]), offset=float(p[1]), weighted_residual_s=float(np.sqrt(np.average(res ** 2, weights=w))),
                windows=len(r), mean_corr=float(r[:, 2].mean()))
    if verbose: print(info)
    return info

# ---------------------------------------------------------------- attitude filter
def camera_attitude(imu: GoProIMU, tel: Telemetry, sync, yaw_offset_deg=0.0,
                    kp_tilt=0.4, k_yaw=1 / 25.0, t_range=None, gyro_bias=(0.0, 0.0, 0.0)):
    """Return (video_t, R_wc) where R_wc maps camera vectors to ENU world."""
    vt = imu.t
    if t_range is not None:
        m = (vt >= t_range[0]) & (vt <= t_range[1]); idx = np.where(m)[0]
    else:
        idx = np.arange(len(vt))
    mt = vt * (1 + sync["rate"]) + sync["offset"]
    # light low-pass on accel (vibration), none on gyro
    b, a = butter(2, 5 / (imu.fs / 2)); acc = filtfilt(b, a, imu.acc, axis=0)
    gyro = imu.gyro.copy()
    # GoPro gyro is factory calibrated; in flight its mean rate is ~0 while the
    # aircraft holds heading. (Pre-takeoff 'stationary' data is NOT used: the
    # powered gimbal slowly drifts on the ground, which would bias the estimate.)
    bias = np.asarray(gyro_bias, float); gyro -= bias
    kin = tel.kin_acc(mt[idx])
    yaw_tgt = tel.yaw_at(mt[idx]) + np.deg2rad(yaw_offset_deg)
    up = np.array([0, 0, 1.0])
    # init from accel tilt + aircraft yaw
    f0 = acc[idx[0]] / np.linalg.norm(acc[idx[0]])           # 'up' in camera frame
    def from_up_yaw(u_cam, yaw):
        # forward horizontal heading = yaw ; build R_wc
        z = np.array([0, 0, 1.0])
        # camera forward vector expressed in world: choose rotation aligning u_cam->z, then heading
        R1 = align(u_cam, z)                                  # cam->tilted-world (yaw unknown)
        fw = R1 @ np.array([0, 0, 1.0]); hd = math.atan2(fw[0], fw[1])
        return rot_axis(z, hd - yaw) @ R1
    R = from_up_yaw(f0, yaw_tgt[0])
    out = np.empty((len(idx), 3, 3)); dt = 1 / imu.fs
    for n, i in enumerate(idx):
        f_w = kin[n] + 9.80665 * up
        v_hat = R.T @ (f_w / np.linalg.norm(f_w))
        meas = acc[i] / np.linalg.norm(acc[i])
        e = np.cross(meas, v_hat)
        fw = R[:, 2]; hd = math.atan2(fw[0], fw[1])
        ey = (yaw_tgt[n] - hd + math.pi) % (2 * math.pi) - math.pi
        w = gyro[i] + kp_tilt * e + (-k_yaw * ey) * (R.T @ up)
        R = R @ expm_so3(w * dt)
        u, _, vh = np.linalg.svd(R); R = u @ vh
        out[n] = R
    return vt[idx], out, dict(gyro_bias=bias.tolist())

def align(a, b):
    a = a / np.linalg.norm(a); b = b / np.linalg.norm(b); v = np.cross(a, b); c = np.dot(a, b)
    if np.linalg.norm(v) < 1e-12: return np.eye(3)
    return np.eye(3) + skew(v) + skew(v) @ skew(v) * (1 / (1 + c))

def interp_rot(t_src, Rs, t):
    """Nearest-neighbour + small-angle slerp between samples (200 Hz source)."""
    i = np.clip(np.searchsorted(t_src, t) - 1, 0, len(t_src) - 2)
    a = (t - t_src[i]) / (t_src[i + 1] - t_src[i])
    R0, R1 = Rs[i], Rs[i + 1]
    d = R0.T @ R1; w = np.array([d[2, 1] - d[1, 2], d[0, 2] - d[2, 0], d[1, 0] - d[0, 1]]) / 2
    return R0 @ expm_so3(w * a)

# ---------------------------------------------------------------- lens
class Fisheye:
    def __init__(self, profile_json, width, height, f_scale=1.0):
        d = json.loads(Path(profile_json).read_text())
        K = np.array(d["fisheye_params"]["camera_matrix"], float)
        cw, ch = d["calib_dimension"]["w"], d["calib_dimension"]["h"]
        s = width / cw
        self.fx, self.fy = K[0, 0] * s * f_scale, K[1, 1] * s * f_scale
        self.cx, self.cy = K[0, 2] * s, K[1, 2] * s
        self.D = np.array(d["fisheye_params"]["distortion_coeffs"], float)
        self.w, self.h = width, height
        self.name = Path(profile_json).stem

    def project_cam(self, Pc):
        """Pc: (N,3) camera-frame vectors. Returns (N,2) pixels and valid mask (in front, within lens range)."""
        Pc = np.atleast_2d(Pc); x, y, z = Pc[:, 0], Pc[:, 1], Pc[:, 2]
        r = np.hypot(x, y); th = np.arctan2(r, z)
        k1, k2, k3, k4 = self.D; t2 = th * th
        thd = th * (1 + k1 * t2 + k2 * t2 ** 2 + k3 * t2 ** 3 + k4 * t2 ** 4)
        sc = np.where(r > 1e-12, thd / np.maximum(r, 1e-12), 1.0)
        u = self.fx * x * sc + self.cx; v = self.fy * y * sc + self.cy
        valid = th < np.deg2rad(95)
        return np.c_[u, v], valid

    def unproject(self, uv):
        uv = np.atleast_2d(uv); mx = (uv[:, 0] - self.cx) / self.fx; my = (uv[:, 1] - self.cy) / self.fy
        thd = np.hypot(mx, my); th = thd.copy(); k1, k2, k3, k4 = self.D
        for _ in range(20):
            t2 = th * th; f = th * (1 + k1 * t2 + k2 * t2 ** 2 + k3 * t2 ** 3 + k4 * t2 ** 4) - thd
            df = 1 + 3 * k1 * t2 + 5 * k2 * t2 ** 2 + 7 * k3 * t2 ** 3 + 9 * k4 * t2 ** 4; th -= f / df
        s = np.where(thd > 1e-12, np.sin(th) / np.maximum(thd, 1e-12), 1.0)
        return np.c_[mx * s, my * s, np.cos(th)]

class CameraPose:
    """World->pixel for one frame."""
    def __init__(self, lens: Fisheye, R_wc, C):
        self.lens, self.R, self.C = lens, R_wc, np.asarray(C, float)
    def project_world(self, P):
        P = np.atleast_2d(P); Pc = (P - self.C) @ self.R          # R_wc^T (P-C) row-wise
        uv, ok = self.lens.project_cam(Pc); return uv, ok & (Pc[:, 2] > -1e9)
    def project_dir(self, d_world):
        Pc = np.atleast_2d(d_world) @ self.R; return self.lens.project_cam(Pc)
    def ray_ground(self, uv):
        d = self.lens.unproject(uv) @ self.R.T
        s = -self.C[2] / d[:, 2]; ok = s > 0
        return self.C + s[:, None] * d, ok

# ---------------------------------------------------------------- reflection geometry
def fresnel_zone(sat, rx, wavelength, n=72):
    mirror = sat.copy(); mirror[2] *= -1
    a = rx[2] / (rx[2] - mirror[2]); center = rx + a * (mirror - rx); center[2] = 0
    base = np.linalg.norm(sat - center) + np.linalg.norm(rx - center)
    ang = np.arange(n) * 2 * np.pi / n; dirs = np.c_[np.cos(ang), np.sin(ang), np.zeros(n)]
    lo = np.zeros(n); hi = np.full(n, 2.0)
    def delta(rad):
        q = center + rad[:, None] * dirs
        return np.linalg.norm(sat - q, axis=1) + np.linalg.norm(rx - q, axis=1) - base
    for _ in range(20):
        small = delta(hi) < wavelength / 2
        if not small.any(): break
        hi[small] *= 2
    for _ in range(36):
        mid = (lo + hi) / 2; small = delta(mid) < wavelength / 2
        lo = np.where(small, mid, lo); hi = np.where(small, hi, mid)
    ring = center + ((lo + hi) / 2)[:, None] * dirs
    return center, ring, float(np.max(abs(delta((lo + hi) / 2) - wavelength / 2)))

# ---------------------------------------------------------------- sun (NOAA solar position, ~0.01 deg)
def sun_enu_dir(dt: datetime, lat, lon):
    jd = dt.timestamp() / 86400.0 + 2440587.5; T = (jd - 2451545.0) / 36525.0
    L0 = (280.46646 + T * (36000.76983 + 0.0003032 * T)) % 360
    M = 357.52911 + T * (35999.05029 - 0.0001537 * T); e = 0.016708634 - T * (0.000042037 + 0.0000001267 * T)
    Mr = math.radians(M)
    C = (math.sin(Mr) * (1.914602 - T * (0.004817 + 0.000014 * T)) + math.sin(2 * Mr) * (0.019993 - 0.000101 * T)
         + math.sin(3 * Mr) * 0.000289)
    om = 125.04 - 1934.136 * T; lam = L0 + C - 0.00569 - 0.00478 * math.sin(math.radians(om))
    eps0 = 23 + (26 + (21.448 - T * (46.815 + T * (0.00059 - T * 0.001813))) / 60) / 60
    eps = math.radians(eps0 + 0.00256 * math.cos(math.radians(om)))
    dec = math.asin(math.sin(eps) * math.sin(math.radians(lam)))
    y = math.tan(eps / 2) ** 2; L0r = math.radians(L0)
    eqt = 4 * math.degrees(y * math.sin(2 * L0r) - 2 * e * math.sin(Mr) + 4 * e * y * math.sin(Mr) * math.cos(2 * L0r)
                           - 0.5 * y * y * math.sin(4 * L0r) - 1.25 * e * e * math.sin(2 * Mr))
    mins = dt.hour * 60 + dt.minute + dt.second / 60 + dt.microsecond / 6e7
    tst = mins + eqt + 4 * lon; ha = math.radians(tst / 4 - 180)
    la = math.radians(lat)
    cz = math.sin(la) * math.sin(dec) + math.cos(la) * math.cos(dec) * math.cos(ha)
    zen = math.acos(max(-1, min(1, cz)))
    az = math.degrees(math.atan2(math.sin(ha), math.cos(ha) * math.sin(la) - math.tan(dec) * math.cos(la))) + 180
    el = 90 - math.degrees(zen)
    el += 1.02 / math.tan(math.radians(el + 10.3 / (el + 5.11))) / 60 if el > -1 else 0   # refraction
    a, e_ = math.radians(az), math.radians(el)
    return np.array([math.sin(a) * math.cos(e_), math.cos(a) * math.cos(e_), math.sin(e_)]), az, el

# ---------------------------------------------------------------- configs
def clip_fps(cfg):
    """Frame rate of the clip (config 'fps'); the 19 Oct clips are 23.976."""
    return float(cfg.get("fps") or 24000 / 1001)

def ref_wh(cfg):
    """Reference image size the solved intrinsics are stored at: 1920 wide, clip aspect ('aspect' = w/h)."""
    return 1920, int(round(1920 / float(cfg.get("aspect") or 16 / 9)))

def load_config(path):
    """Load a JSON config; an 'extends' key merges a parent config (child wins, dicts merged)."""
    path = resolve(path); cfg = json.loads(path.read_text())
    parent = cfg.pop("extends", None); cfg.pop("_comment", None)
    if parent:
        base = load_config(path.parent / parent)
        for k, v in cfg.items():
            base[k] = {**base[k], **v} if isinstance(v, dict) and isinstance(base.get(k), dict) else v
        cfg = base
    cfg["_path"] = str(path); return cfg
