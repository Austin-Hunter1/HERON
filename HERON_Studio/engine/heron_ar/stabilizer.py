"""Visual ground-lock: removes the residual frame-to-frame attitude jitter (GoPro
HyperSmooth crop motion, gyro noise) so overlays stay glued to the lakebed.

For each new frame, ground features are tracked from the previous frame, cast onto
the plane with the previous *corrected* pose, and the small rotation δ that makes
them land on their tracked positions is solved for. The correction accumulates
(visual odometry in rotation) but leaks back toward the IMU/calibrated attitude with
a time constant of a few seconds, so absolute alignment still comes from the
IMU + sun-glint/skyline calibration and cannot drift away.
"""
import numpy as np, cv2
from scipy.optimize import least_squares
from .geometry import CameraPose, expm_so3

def log_so3(R):
    c = np.clip((np.trace(R) - 1) / 2, -1, 1); th = np.arccos(c)
    if th < 1e-9: return np.zeros(3)
    return th / (2 * np.sin(th)) * np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]])

class GroundLock:
    def __init__(self, leak_tau_s=3.0, fps=23.976, max_feats=350, work_w=960):
        self.leak = np.exp(-1.0 / (leak_tau_s * fps)); self.max_feats = max_feats; self.ww = work_w
        self.prev = None; self.corr = np.eye(3); self.stats = []

    def reset(self): self.prev = None; self.corr = np.eye(3)

    def __call__(self, frame_bgr, cam_imu: CameraPose, valid_mask=None):
        """Return corrected CameraPose for this frame."""
        h, w = frame_bgr.shape[:2]; s = self.ww / w
        g = cv2.cvtColor(cv2.resize(frame_bgr, (self.ww, int(h * s))), cv2.COLOR_BGR2GRAY)
        corr_pred = expm_so3(self.leak * log_so3(self.corr))           # leak toward IMU attitude
        cam = CameraPose(cam_imu.lens, cam_imu.R @ corr_pred, cam_imu.C)
        if self.prev is not None:
            pg, pcam = self.prev
            m = np.zeros_like(g); m[int(.12 * g.shape[0]):int(.95 * g.shape[0]), int(.08 * g.shape[1]):int(.92 * g.shape[1])] = 255
            if valid_mask is not None: m &= cv2.resize(valid_mask, (g.shape[1], g.shape[0]), interpolation=cv2.INTER_NEAREST) * 255
            p0 = cv2.goodFeaturesToTrack(pg, self.max_feats, 0.01, 8, mask=m)
            if p0 is not None and len(p0) > 25:
                p1, st, _ = cv2.calcOpticalFlowPyrLK(pg, g, p0, None, winSize=(21, 21), maxLevel=3)
                ok = st.ravel() == 1
                p0 = p0[ok].reshape(-1, 2) / s; p1 = p1[ok].reshape(-1, 2) / s
                G, okg = pcam.ray_ground(p0.astype(float)); G, p1 = G[okg], p1[okg]
                if len(G) > 25:
                    def f(x):
                        c2 = CameraPose(cam.lens, cam.R @ expm_so3(x * 1e-3), cam.C)
                        u, _ = c2.project_world(G); return (u - p1).ravel()
                    sol = least_squares(f, np.zeros(3), loss="soft_l1", f_scale=1.5, max_nfev=30)
                    d = sol.x * 1e-3
                    if np.linalg.norm(d) < 0.02:                         # reject >1.1 deg jumps (bad tracks)
                        corr_pred = corr_pred @ expm_so3(d)
                        cam = CameraPose(cam_imu.lens, cam_imu.R @ corr_pred, cam_imu.C)
                    self.stats.append((np.median(np.abs(f(np.zeros(3)))), np.median(np.abs(sol.fun))))
        self.corr = corr_pred; self.prev = (g, cam)
        return cam

class SurfaceHeight:
    """Track the height of the surface below (lakebed / shoreline) from image motion.

    Rotation is taken from the IMU (verified against the sun glint); only the plane
    height is solved, so absolute pointing is never altered. The lakebed is not flat
    (measured 46-56 m below the drone along the survey), and a constant plane makes
    overlays slide across it. Smoothed with a ~2 s time constant.
    """
    def __init__(self, h0, fps=23.976, tau_s=2.0, work_w=960, max_feats=300):
        self.h = h0; self.a = 1 - np.exp(-1.0 / (tau_s * fps)); self.ww = work_w; self.mf = max_feats; self.prev = None; self.log = []

    def __call__(self, frame_bgr, cam_prev_imu, cam_imu):
        from scipy.optimize import minimize_scalar
        h, w = frame_bgr.shape[:2]; s = self.ww / w
        g = cv2.cvtColor(cv2.resize(frame_bgr, (self.ww, int(h * s))), cv2.COLOR_BGR2GRAY)
        if self.prev is not None and cam_prev_imu is not None:
            pg = self.prev
            m = np.zeros_like(g); m[int(.3 * g.shape[0]):int(.95 * g.shape[0]), int(.1 * g.shape[1]):int(.9 * g.shape[1])] = 255
            p0 = cv2.goodFeaturesToTrack(pg, self.mf, 0.01, 8, mask=m)
            if p0 is not None and len(p0) > 30:
                p1, st, _ = cv2.calcOpticalFlowPyrLK(pg, g, p0, None, winSize=(21, 21), maxLevel=3)
                ok = st.ravel() == 1; p0 = p0[ok].reshape(-1, 2) / s; p1 = p1[ok].reshape(-1, 2) / s
                def err(hh):
                    dz = cam_prev_imu.C[2] - hh
                    a_ = CameraPose(cam_imu.lens, cam_prev_imu.R, cam_prev_imu.C - [0, 0, dz])
                    b_ = CameraPose(cam_imu.lens, cam_imu.R, cam_imu.C - [0, 0, dz])
                    G, okg = a_.ray_ground(p0); u, _ = b_.project_world(G)
                    return np.median(np.linalg.norm(u - p1, axis=1)[okg])
                moved = np.linalg.norm(cam_imu.C[:2] - cam_prev_imu.C[:2])
                if moved > 0.15:
                    r = minimize_scalar(err, bounds=(self.h * 0.7, self.h * 1.3), method="bounded", options=dict(xatol=0.05))
                    self.h += self.a * (r.x - self.h); self.log.append(r.x)
        self.prev = g
        return self.h
