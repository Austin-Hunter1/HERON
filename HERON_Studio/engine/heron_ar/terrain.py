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

    def ray_hit(self, C, d, iters=6):
        """Intersect rays C + s d with the height field (fixed-point iteration)."""
        h = np.full(len(d), self.water_z)
        for _ in range(iters):
            s = (h - C[2]) / d[:, 2]; P = C + s[:, None] * d
            h = self.height(P[:, 0], P[:, 1])
        s = (h - C[2]) / d[:, 2]; return C + s[:, None] * d, s > 0
