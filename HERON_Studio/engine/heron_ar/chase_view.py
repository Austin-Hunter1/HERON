"""Chase view: a virtual camera behind and above the drone, looking at the water plane.

From the receiver (the GoPro) every first Fresnel zone looks circular, because the
zone is a plane section of an ellipsoid with a focus at the receiver. From any
other viewpoint the true ground ellipses (up to ~11:1 at 6 deg elevation) appear.

The ground in this view is textured by re-projecting the *current GoPro frame*
onto the water plane, so the panel shows the real lakebed/water the drone sees,
with the stylised map (reservoir outline) filling in outside the GoPro footprint.
"""
from __future__ import annotations
import math
import numpy as np, cv2
from .geometry import fresnel_zone

def look_at(eye, target, up=(0, 0, 1.0)):
    f = target - eye; f = f / np.linalg.norm(f)
    r = np.cross(f, up); r = r / np.linalg.norm(r)
    d = np.cross(f, r)                       # image-down
    return np.c_[r, d, f]                    # columns: cam x,y,z in world (R_wc)

class ChaseCam:
    def __init__(self, W, H, hfov_deg=64.0):
        self.W, self.H = W, H
        self.f = W / 2 / math.tan(math.radians(hfov_deg) / 2)
        ys, xs = np.mgrid[0:H, 0:W].astype(np.float32)
        self.rays_c = np.stack([(xs - W / 2) / self.f, (ys - H / 2) / self.f, np.ones_like(xs)], -1).reshape(-1, 3)
        self.eye = None; self.tgt = None; self.hd = None

    def update(self, rx, heading_rad, cfg):
        # smooth only the heading (position follows the drone exactly)
        if self.eye is None or self.hd is None: self.hd = heading_rad
        else: self.hd += 0.08 * ((heading_rad - self.hd + math.pi) % (2 * math.pi) - math.pi)
        fwd = np.array([math.sin(self.hd), math.cos(self.hd), 0.0])
        right = np.array([fwd[1], -fwd[0], 0.0])
        eye = rx - cfg["chase_back_m"] * fwd + cfg["chase_side_m"] * right
        eye[2] = rx[2] + cfg["chase_up_m"]
        tgt = rx + cfg["chase_look_ahead_m"] * fwd; tgt[2] = rx[2] * cfg.get("chase_target_height_frac", 0.4)
        self.eye, self.tgt = eye, tgt
        self.R = look_at(self.eye, self.tgt)

    def project(self, P):
        P = np.atleast_2d(P); c = (P - self.eye) @ self.R
        z = c[:, 2]; ok = z > 1.0
        uv = np.c_[self.f * c[:, 0] / np.maximum(z, 1e-6) + self.W / 2, self.f * c[:, 1] / np.maximum(z, 1e-6) + self.H / 2]
        return uv, ok

def densify(P, step):
    out = []
    for i in range(len(P) - 1):
        n = max(1, int(np.linalg.norm(P[i + 1] - P[i]) / step))
        out.extend(P[i] + (P[i + 1] - P[i]) * k / n for k in range(n))
    out.append(P[-1]); return np.array(out)

def _poly(img, uv, ok, color, thick=1, closed=True):
    if ok.sum() < 2: return
    pts = np.int32(np.clip(uv[ok], -3000, 6000))
    cv2.polylines(img, [pts], closed, color, thick, cv2.LINE_AA)

def render_chase(chase: ChaseCam, frame_bgr, cam, scene, sats, items_ids, ccol, cfg, water_mask=None):
    """Return a BGR panel image (H x W)."""
    W, H = chase.W, chase.H; rx = cam.C
    fw = cam.R[:, 2]; hd = math.atan2(fw[0], fw[1])
    chase.update(rx, hd, cfg)
    # ---- ground texture: chase ray -> water plane -> GoPro pixel
    d = chase.rays_c @ chase.R.T
    s = -chase.eye[2] / d[:, 2]; hit = (d[:, 2] < -1e-4) & (s > 0)
    G = chase.eye + np.where(hit, s, 0)[:, None] * d
    uv, ok = cam.project_world(G)
    Pc = (G - cam.C) @ cam.R
    gh, gw = frame_bgr.shape[:2]
    inside = hit & ok & (Pc[:, 2] > 0) & (uv[:, 0] >= 0) & (uv[:, 0] < gw - 1) & (uv[:, 1] >= 0) & (uv[:, 1] < gh - 1)
    # distance from the GoPro limits re-projection to the well-resolved near field
    rng = np.linalg.norm(G[:, :2] - rx[:2], axis=1)
    inside &= rng < cfg["chase_texture_range_m"]
    mapx = np.where(inside, uv[:, 0], -1).reshape(H, W).astype(np.float32)
    mapy = np.where(inside, uv[:, 1], -1).reshape(H, W).astype(np.float32)
    tex = cv2.remap(frame_bgr, mapx, mapy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
    # stylised base: land / reservoir (full pool) / sky
    base = np.zeros((H * W, 3), np.float32)
    sky_t = np.clip((chase.rays_c[:, 1] + 0.4) / 0.6, 0, 1)
    base[:] = (1 - sky_t)[:, None] * np.array([60, 40, 26]) + sky_t[:, None] * np.array([120, 92, 70])   # BGR dusk-blue sky
    land = np.array([46, 52, 58], np.float32); pool = np.array([96, 64, 30], np.float32)
    inpool = np.zeros(H * W, bool)
    if hit.any():
        o = scene.pool_raster
        ix = ((G[:, 0] - o["x0"]) / o["res"]).astype(np.int64); iy = ((G[:, 1] - o["y0"]) / o["res"]).astype(np.int64)
        okr = hit & (ix >= 0) & (ix < o["mask"].shape[1]) & (iy >= 0) & (iy < o["mask"].shape[0])
        inpool[okr] = o["mask"][iy[okr], ix[okr]] > 0
    fog = np.clip(1 - np.where(hit, s, 1e9) / 1400.0, 0.15, 1)[:, None]
    gcol = np.where(inpool[:, None], pool, land) * fog + base * (1 - fog)
    base = np.where(hit[:, None], gcol, base)
    panel = base.reshape(H, W, 3)
    insideI = inside.reshape(H, W)
    # feather the texture edge
    m = cv2.GaussianBlur(insideI.astype(np.float32), (0, 0), 3)[..., None] * insideI[..., None]
    texf = tex.astype(np.float32) * 0.82
    panel = (panel * (1 - m) + texf * m).astype(np.uint8)

    ov = panel.copy()
    # ---- 25 m grid on the plane
    step = 25; cx, cy = np.round(rx[:2] / step) * step; R_ = 400
    for k in np.arange(-R_, R_ + 1, step):
        for a_, b_ in [((cx + k, cy - R_), (cx + k, cy + R_)), ((cx - R_, cy + k), (cx + R_, cy + k))]:
            seg = densify(np.array([[a_[0], a_[1], 0], [b_[0], b_[1], 0]], float), 20)
            u, o_ = chase.project(seg); _poly(ov, u, o_, (150, 140, 120), 1, False)
    panel = cv2.addWeighted(ov, 0.35, panel, 0.65, 0)
    # ---- mapped full-pool shoreline
    u, o_ = chase.project(scene.pool3); _poly(panel, u, o_, (255, 230, 170), 1)
    # ---- GoPro ground footprint (what the main view shows)
    border = np.r_[np.c_[np.linspace(0, gw, 30), np.zeros(30)], np.c_[np.full(16, gw), np.linspace(0, gh, 16)],
                   np.c_[np.linspace(gw, 0, 30), np.full(30, gh)], np.c_[np.zeros(16), np.linspace(gh, 0, 16)]]
    gp, gok = cam.ray_ground(border)
    far = np.linalg.norm(gp[:, :2] - rx[:2], axis=1) > cfg["chase_texture_range_m"]
    rays = cam.lens.unproject(border) @ cam.R.T; hor = rays[:, :2] / np.maximum(np.linalg.norm(rays[:, :2], axis=1)[:, None], 1e-6)
    fb = np.c_[rx[:2] + hor * cfg["chase_texture_range_m"], np.zeros(len(rays))]
    gp = np.where((gok & ~far)[:, None], gp, fb); gp[:, 2] = 0
    u, o_ = chase.project(gp); _poly(panel, u, o_, (235, 235, 235), 1)

    # ---- Fresnel zones: true ground ellipses
    layer = panel.copy(); lines = []
    zones = []
    for s_ in sats:
        if s_["el"] < cfg["elevation_mask_deg"]: continue
        c, ring, _ = fresnel_zone(s_["enu"], rx, scene.wl, 120)
        if np.linalg.norm(c[:2] - rx[:2]) > cfg["chase_zone_range_m"]: continue
        zones.append((s_, c, ring))
    zones.sort(key=lambda z: -np.linalg.norm(z[1] - chase.eye))
    for s_, c, ring in zones:
        col = tuple(int(v) for v in ccol[s_["const"]][::-1])
        u, o_ = chase.project(ring)
        if o_.all():
            cv2.fillPoly(layer, [np.int32(np.clip(u, -3000, 6000))], col)
    panel = cv2.addWeighted(layer, 0.40, panel, 0.60, 0)
    for s_, c, ring in zones:
        col = tuple(int(v) for v in ccol[s_["const"]][::-1]); strong = s_["sid"] in items_ids
        u, o_ = chase.project(ring); _poly(panel, u, o_, col, 2 if strong else 1)
        # incoming ray (satellite -> specular point) and reflected ray (specular point -> drone)
        if strong:
            inc = c[None] + np.linspace(0, cfg.get("chase_ray_m", 90), 2)[:, None] * s_["dir"][None]
            ui, oi = chase.project(inc)
            if oi.all(): cv2.line(panel, tuple(np.int32(ui[1])), tuple(np.int32(ui[0])), col, 2, cv2.LINE_AA)
            ur, orr = chase.project(np.vstack([c, rx]))
            if orr.all(): cv2.line(panel, tuple(np.int32(ur[0])), tuple(np.int32(ur[1])), (230, 230, 230), 1, cv2.LINE_AA)
        uc, oc = chase.project(c[None])
        if oc[0]: cv2.circle(panel, tuple(np.int32(uc[0])), 2, (255, 255, 255), -1, cv2.LINE_AA)
        lines.append((s_, uc[0] if oc[0] else None, strong))
    # ---- drone + nadir line
    ud, od = chase.project(np.vstack([rx, rx * np.array([1, 1, 0])]))
    if od.all():
        cv2.line(panel, tuple(np.int32(ud[0])), tuple(np.int32(ud[1])), (255, 255, 255), 1, cv2.LINE_AA)
        p = tuple(np.int32(ud[0])); cv2.circle(panel, p, 6, (255, 255, 255), -1, cv2.LINE_AA); cv2.circle(panel, p, 10, (255, 255, 255), 1, cv2.LINE_AA)
    return panel, lines, ud[0] if od[0] else None

def pool_raster(pool_xy, res=2.0, pad=50):
    x0, y0 = pool_xy.min(0) - pad; x1, y1 = pool_xy.max(0) + pad
    W = int((x1 - x0) / res) + 1; H = int((y1 - y0) / res) + 1
    m = np.zeros((H, W), np.uint8)
    cv2.fillPoly(m, [np.int32((pool_xy - [x0, y0]) / res)], 1)
    return dict(x0=x0, y0=y0, res=res, mask=m)
