"""World-anchored water map.

Keyframes across the whole clip are cast onto the solved ground surface and each ground cell
collects what the camera saw there: how finely textured it is, how often it glints, its mean
colour. Water is then decided once, per cell, from all views:

  smooth   from the air, land is finely textured and standing water is not, however shallow it is
           (the bed seen through it is blurred by ripples). Near field only.
  grey     water mirrors the sky, so averaged over many views it is far less saturated than the
           ground. Smooth AND grey is water; smooth but coloured is bare mud or sand.
  glint    sun glitter happens on water (and a little on wet sand, which is not grey).
  blue     open water far from the camera, where texture can no longer be told apart.

Every frame samples the SAME map through its camera, so the shoreline is stable from frame to
frame and stays glued to the ground; water_refine then snaps it to the frame's own pixels.
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
        np.savez(p, x0=self.x0, y0=self.y0, cell=self.cell, prob=self.prob, version=VERSION, **extra)

    @staticmethod
    def load(p):
        z = np.load(p); return WaterMap(float(z["x0"]), float(z["y0"]), float(z["cell"]), z["prob"], z["seen"] if "seen" in z.files else None)

    @staticmethod
    def current(p):
        """True if the file was built by this version of build() (older maps called shallow water land)."""
        try: return int(np.load(p)["version"]) == VERSION
        except (OSError, KeyError, ValueError): return False

VERSION = 2

def _otsu(v):
    """Threshold between the two modes of v, or None if it has only one."""
    lo, hi = np.percentile(v, [0.5, 99.5])
    if hi - lo < 1e-6: return None
    q = np.clip((v - lo) / (hi - lo) * 255, 0, 255).astype(np.uint8)
    t, _ = cv2.threshold(q, 0, 255, cv2.THRESH_OTSU); below = (q <= t).mean()
    return lo + t / 255 * (hi - lo) if 0.03 < below < 0.97 else None

def build(scene, lens, cap, fps, clip_start_s, nframes, ground_field, step_s=0.75, cell=0.7, scale=0.25, near_m=300.0, far_m=1100.0, log=print):
    """near_m: range within which ground texture is resolved well enough to use; far_m: range voted at all."""
    w, h = lens.w, lens.h
    frames = [int(fi) for fi in np.arange(0, nframes, max(1, int(step_s * fps))) if scene.pose_trusted(clip_start_s + fi / fps)]
    if not frames: raise RuntimeError("water map: no frame has a trusted camera pose")
    C = np.array([scene.pose(clip_start_s + fi / fps, lens)[1].C for fi in frames])
    x0, y0 = C[:, :2].min(0) - far_m; W, H = (np.ptp(C[:, :2], axis=0) + 2 * far_m) // cell + 1; W, H = int(W), int(H); N = W * H
    seen, near, tex, glint = (np.zeros(N, np.float32) for _ in range(4)); col = np.zeros((N, 3), np.float32)
    for n, fi in enumerate(frames):
        cap.set(cv2.CAP_PROP_POS_FRAMES, fi); ok, fr = cap.read()
        if not ok: continue
        fr = cv2.resize(fr, (w, h), interpolation=cv2.INTER_AREA)
        cam = scene.pose(clip_start_s + fi / fps, lens)[1]
        G = ground_field(cam, w, h, scale, scene.terrain); gs = (G.shape[1], G.shape[0])
        gray = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY).astype(np.float32)
        fine = cv2.resize(np.abs(gray - cv2.GaussianBlur(gray, (0, 0), 2.0)), gs, interpolation=cv2.INTER_AREA)     # fine-texture energy
        sm = cv2.resize(fr, gs, interpolation=cv2.INTER_AREA); hsv = cv2.cvtColor(sm, cv2.COLOR_BGR2HSV)
        r = np.hypot(G[..., 0] - cam.C[0], G[..., 1] - cam.C[1])
        ok = np.isfinite(G[..., 0]) & (r < far_m)
        ix = ((G[..., 0][ok] - x0) / cell).astype(int); iy = ((G[..., 1][ok] - y0) / cell).astype(int)
        good = (ix >= 0) & (ix < W) & (iy >= 0) & (iy < H)
        # one vote per cell per frame (the mean of its pixels), so near frames with many pixels per cell don't outvote the rest
        k, inv, cnt = np.unique(iy[good] * W + ix[good], return_inverse=True, return_counts=True)
        mean = lambda v: np.bincount(inv, weights=v[ok][good], minlength=len(k)) / cnt
        seen[k] += 1; glint[k] += mean(((hsv[..., 2] > 240) & (hsv[..., 1] < 50)).astype(np.float32)) > 0.2
        for c in range(3): col[k, c] += mean(sm[..., c])
        nr = mean(r) < near_m; near[k[nr]] += 1; tex[k[nr]] += mean(fine)[nr]
        if n % 40 == 0: log(f"water map: frame {n + 1}/{len(frames)}", **({"flush": True} if log is print else {}))
    shape = (H, W); seen, near = seen.reshape(shape), near.reshape(shape); sn = np.maximum(seen, 1)
    blur = lambda a: cv2.GaussianBlur(a.astype(np.float32), (0, 0), 1.5 / cell)                                      # ~1.5 m
    col = col.reshape(H, W, 3) / sn[..., None]; tex = blur(tex.reshape(shape) / np.maximum(near, 1))
    glinty = blur(glint.reshape(shape) / sn) > 0.03; blue = col[..., 0] - col[..., 2] > 15
    sat = cv2.cvtColor(np.clip(col, 0, 255).astype(np.uint8), cv2.COLOR_BGR2HSV)[..., 1].astype(np.float32)
    # both thresholds come from this flight's own ground (two modes each: water and land), not from fixed colours
    well = near >= 3; t, ts = (_otsu(np.log(tex[well] + 0.3)), _otsu(sat[well])) if well.sum() > 1000 else (None, None)
    grey = sat < ts if ts is not None else np.ones(shape, bool)
    smooth = well & (np.log(tex + 0.3) < t) if t is not None else np.zeros(shape, bool)
    b = (((smooth | glinty) & grey | blue) & (seen >= 2)).astype(np.uint8)
    el = lambda m: cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * int(m / cell) + 1,) * 2)
    b = cv2.morphologyEx(cv2.morphologyEx(b, cv2.MORPH_OPEN, el(2)), cv2.MORPH_CLOSE, el(3))
    # A body of water has glint or open blue somewhere in it and lies near the water level; smooth ground alone
    # (tarmac, a roof, bare flat mud) has neither.
    nlab, lab, st, _ = cv2.connectedComponentsWithStats(b, 8)
    anchor = np.bincount(lab.ravel(), weights=(glinty | blue).ravel(), minlength=nlab) * cell ** 2
    keep = (anchor > 50) & (st[:, 4] * cell ** 2 > 400); keep[0] = False
    if scene.terrain is not None:
        for i in np.nonzero(keep)[0]:
            yy, xx = np.nonzero(lab[st[i, 1]:st[i, 1] + st[i, 3], st[i, 0]:st[i, 0] + st[i, 2]] == i); q = slice(None, None, max(1, len(xx) // 5000))
            hgt = scene.terrain.height(x0 + (xx[q] + st[i, 0] + 0.5) * cell, y0 + (yy[q] + st[i, 1] + 0.5) * cell)
            keep[i] = np.median(hgt) < scene.terrain.water_z + 2.5
    b = keep[lab].astype(np.float32)
    # fill small holes (glint, foam)
    inv = (1 - b).astype(np.uint8); nlab, lab, st, _ = cv2.connectedComponentsWithStats(inv, 8)
    holes = st[:, 4] < (150 / cell ** 2); holes[0] = False
    b[holes[lab]] = 1
    log(f"water map: {b.sum() * cell ** 2 / 1e4:.1f} ha of water in {(seen >= 2).sum() * cell ** 2 / 1e4:.1f} ha seen"
        + ("" if t is not None else " (ground texture not usable: glint and colour only)"))
    return WaterMap(x0, y0, cell, cv2.GaussianBlur(b, (0, 0), 0.8), seen=seen >= 2)                                 # soft 0..1 edge (~0.5 m)
