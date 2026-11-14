"""Height field of the lakebed/shore from the SfM point cloud (ENU, z relative to the
configured plane), clamped below by the measured water level."""
import numpy as np, cv2
from scipy.interpolate import griddata

class Terrain:
    def __init__(self, P, water_z, cell=4.0, pad=60):
        P = P[np.isfinite(P).all(1)]
        self.x0, self.y0 = P[:, 0].min() - pad, P[:, 1].min() - pad
        x1, y1 = P[:, 0].max() + pad, P[:, 1].max() + pad
        self.cell = cell; W = int((x1 - self.x0) / cell) + 1; H = int((y1 - self.y0) / cell) + 1
        ix = ((P[:, 0] - self.x0) / cell).astype(int); iy = ((P[:, 1] - self.y0) / cell).astype(int)
        z = np.full((H, W), np.nan); key = iy * W + ix
        o = np.argsort(key); ks, zs = key[o], P[o, 2]
        u, st = np.unique(ks, return_index=True)
        for a, b, k in zip(st, np.r_[st[1:], len(ks)], u): z[k // W, k % W] = np.median(zs[a:b])
        yy, xx = np.mgrid[0:H, 0:W]; good = np.isfinite(z)
        z = griddata((yy[good], xx[good]), z[good], (yy, xx), method="linear")
        zn = griddata((yy[good], xx[good]), np.nan_to_num(np.where(good, z, 0))[good], (yy, xx), method="nearest")
        z = np.where(np.isfinite(z), z, zn)
        z = cv2.GaussianBlur(z.astype(np.float32), (0, 0), 1.2)
        self.z = np.maximum(z, water_z); self.water_z = water_z; self.H, self.W = H, W

    def height(self, x, y):
        fx = (np.asarray(x) - self.x0) / self.cell; fy = (np.asarray(y) - self.y0) / self.cell
        inside = (fx >= 0) & (fx < self.W - 1) & (fy >= 0) & (fy < self.H - 1)
        fx = np.clip(fx, 0, self.W - 1.001); fy = np.clip(fy, 0, self.H - 1.001)
        i, j = fy.astype(int), fx.astype(int); a, b = fy - i, fx - j
        z = (self.z[i, j] * (1 - a) * (1 - b) + self.z[i + 1, j] * a * (1 - b) + self.z[i, j + 1] * (1 - a) * b + self.z[i + 1, j + 1] * a * b)
        return np.where(inside, z, self.water_z)

    def ray_hit(self, C, d, iters=6, tol=1e-3):
        """Intersect rays C + s d with the height field (fixed-point iteration).
        A ray stops iterating once its surface height moves by less than tol (m): over open water
        that is after the first pass, so only rays that land on sloping ground pay for the rest."""
        h = np.full(len(d), float(self.water_z)); act = np.arange(len(d))
        for _ in range(iters):
            s = (h[act] - C[2]) / d[act, 2]
            hn = self.height(C[0] + s * d[act, 0], C[1] + s * d[act, 1])
            moved = np.abs(hn - h[act]) > tol; h[act] = hn; act = act[moved]
            if not len(act): break
        s = (h - C[2]) / d[:, 2]; return C + s[:, None] * d, s > 0
