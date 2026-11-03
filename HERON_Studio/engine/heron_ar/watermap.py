"""World-anchored water map.

Water is segmented on keyframes across the whole clip and each result is cast onto the
solved ground surface, voting into a 0.5 m east/north grid. The vote is cleaned once
(morphology, small-blob removal) and then every frame samples the SAME map through its
camera, so the shoreline is stable from frame to frame (no flicker), stays glued to the
ground, and is finally snapped to the image edge with a guided filter.
"""
import numpy as np, cv2
from pathlib import Path

class WaterMap:
    def __init__(self, x0, y0, cell, prob, seen=None):
        self.x0, self.y0, self.cell = x0, y0, cell; self.prob = prob.astype(np.float32)
        self.seen = seen            # bool grid: cells the camera actually looked at (None for old caches)
        self.H, self.W = prob.shape

    def sample(self, x, y):
        fx = (x - self.x0) / self.cell; fy = (y - self.y0) / self.cell
        ok = np.isfinite(fx) & np.isfinite(fy) & (fx >= 0) & (fx < self.W - 1) & (fy >= 0) & (fy < self.H - 1)
        out = np.zeros(np.shape(x), np.float32)
        a, b = fx[ok], fy[ok]; i, j = b.astype(int), a.astype(int); u, v = b - i, a - j
        P = self.prob
        out[ok] = (P[i, j] * (1 - u) * (1 - v) + P[i + 1, j] * u * (1 - v) + P[i, j + 1] * (1 - u) * v + P[i + 1, j + 1] * u * v)
        return out

    def save(self, p):
        extra = {} if self.seen is None else dict(seen=self.seen)
        np.savez(p, x0=self.x0, y0=self.y0, cell=self.cell, prob=self.prob, **extra)

    @staticmethod
    def load(p):
        z = np.load(p); return WaterMap(float(z["x0"]), float(z["y0"]), float(z["cell"]), z["prob"], z["seen"] if "seen" in z.files else None)

def build(scene, lens, cap, fps, clip_start_s, nframes, ground_field, step_s=0.75, cell=0.7, scale=0.25, log=print):
    from .water_seg import segment_water_v2
    from .scene import horizon_mask_for, clip_poly_front
    pool = scene.pool; pad = 500
    x0, y0 = pool[:, 0].min() - pad, pool[:, 1].min() - pad
    x1, y1 = pool[:, 0].max() + pad, pool[:, 1].max() + pad
    W, H = int((x1 - x0) / cell) + 1, int((y1 - y0) / cell) + 1
    wv = np.zeros((H, W), np.float32); tv = np.zeros((H, W), np.float32)
    w, h = lens.w, lens.h
    frames = np.arange(0, nframes, max(1, int(step_s * fps)))
    for n, fi in enumerate(frames):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(fi)); ok, fr = cap.read()
        if not ok: continue
        fr = cv2.resize(fr, (w, h), interpolation=cv2.INTER_AREA)
        _, cam, _, _ = scene.pose(clip_start_s + fi / fps, lens)
        hz = horizon_mask_for(cam, w, h)
        prior = np.zeros((h, w), np.uint8)
        uv, okp = clip_poly_front(cam, scene.pool3)
        if okp.sum() > 3: cv2.fillPoly(prior, [np.clip(uv[okp], -4000, 6000).astype(np.int32)], 1)
        prior = cv2.dilate(prior, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (61, 61)))
        m = segment_water_v2(fr, prior, hz, 0.3)
        G = ground_field(cam, w, h, scale, scene.terrain)
        ms = cv2.resize(m, (G.shape[1], G.shape[0]), interpolation=cv2.INTER_AREA).astype(np.float32)
        # weight near-field pixels more (finer ground sampling, less haze)
        r = np.hypot(G[..., 0] - cam.C[0], G[..., 1] - cam.C[1])
        ok = np.isfinite(G[..., 0]) & (r < 1100)
        ix = ((G[..., 0][ok] - x0) / cell).astype(int); iy = ((G[..., 1][ok] - y0) / cell).astype(int)
        good = (ix >= 0) & (ix < W) & (iy >= 0) & (iy < H)
        wt = (1.0 / (1 + (r[ok][good] / 120) ** 2)).astype(np.float32)
        np.add.at(wv, (iy[good], ix[good]), ms[ok][good] * wt); np.add.at(tv, (iy[good], ix[good]), wt)
        if n % 40 == 0: log(f"water map: frame {n + 1}/{len(frames)}", flush=True) if log is print else log(f"water map: frame {n + 1}/{len(frames)}")
    # fill sparse far-field cells by normalised blur
    k = 3
    num = cv2.GaussianBlur(wv, (0, 0), k); den = cv2.GaussianBlur(tv, (0, 0), k)
    prob = np.where(den > 1e-3, num / np.maximum(den, 1e-6), 0)
    exact = np.where(tv > 0.5, wv / np.maximum(tv, 1e-6), prob)                  # keep detail where densely observed
    prob = cv2.GaussianBlur(exact.astype(np.float32), (0, 0), 1.0)
    b = (prob > 0.5).astype(np.uint8)
    b = cv2.morphologyEx(b, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    b = cv2.morphologyEx(b, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    nlab, lab, st, _ = cv2.connectedComponentsWithStats(b, 8)
    keep = st[:, 4] > (400 / cell ** 2); keep[0] = False
    b = keep[lab].astype(np.float32)
    # fill small holes (glint, foam)
    inv = (1 - b).astype(np.uint8); nlab, lab, st, _ = cv2.connectedComponentsWithStats(inv, 8)
    holes = st[:, 4] < (150 / cell ** 2); holes[0] = False
    b[holes[lab]] = 1
    prob = cv2.GaussianBlur(b, (0, 0), 0.8)                                       # soft 0..1 edge (~0.5 m)
    return WaterMap(x0, y0, cell, prob, seen=den > 1e-3)
