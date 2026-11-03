"""Extra presentation figures: orthomosaic from the GoPro, OSM vs observed shoreline,
COLMAP->GPS fusion, georeferenced 3D scene, pixel<->ground grid, reflection tracks."""
from __future__ import annotations
import math
from pathlib import Path
import numpy as np, cv2
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

from .figures import (_fig, _save, _img_ax, SURF, INK, INK2, MUTED, GRID, BLUE, ORANGE, AQUA, YELLOW, WATER_FILL,
                      TURN_FILL, CONST, FPS)
from .geometry import fresnel_zone, CACHE

CYAN = "#5fd3f3"; EXPOSED = "#eb6834"

# ------------------------------------------------------------------ orthomosaic (built once, cached)
def orthomosaic(cx, cell=0.5, step_s=0.75, scale=0.35, max_range=320.0, log=print):
    p = CACHE / f"ortho_{Path(cx.cfg['clip_path']).stem}_{cell}.npz"
    if p.exists():
        z = np.load(p); return z["rgb"], float(z["x0"]), float(z["y0"]), float(z["cell"])
    from .render import ground_field
    sc = cx.scene; C = cx.sfm["Cw"]; pad = max_range * 0.75
    x0, x1 = C[:, 0].min() - pad, C[:, 0].max() + pad; y0, y1 = C[:, 1].min() - pad, C[:, 1].max() + pad
    W, H = int((x1 - x0) / cell), int((y1 - y0) / cell)
    best = np.full(H * W, np.inf, np.float32); rgb = np.zeros((H * W, 3), np.uint8)
    cap = cv2.VideoCapture(str(cx.video)); dur = cx.cfg["clip_duration_s"]
    for n, t in enumerate(np.arange(0.5, dur - 0.5, step_s)):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(t * FPS))); ok, fr = cap.read()
        if not ok: continue
        _, cam, _, _ = cx.pose(t)
        G = ground_field(cam, cx.W, cx.H, scale, sc.terrain); sh, sw = G.shape[:2]
        col = cv2.resize(fr, (sw, sh), interpolation=cv2.INTER_AREA)
        r = np.hypot(G[..., 0] - cam.C[0], G[..., 1] - cam.C[1])
        ok = np.isfinite(r) & (r < max_range)
        ix = ((G[..., 0][ok] - x0) / cell).astype(int); iy = ((G[..., 1][ok] - y0) / cell).astype(int)
        g = (ix >= 0) & (ix < W) & (iy >= 0) & (iy < H)
        k = (iy[g] * W + ix[g]); s = r[ok][g].astype(np.float32); c = col[ok][g]
        o = np.argsort(-s); k, s, c = k[o], s[o], c[o]                    # closest written last
        m = s < best[k]; best[k[m]] = s[m]; rgb[k[m]] = c[m]
        if n % 40 == 0: log(f"  ortho frame {n}")
    rgb = rgb.reshape(H, W, 3)[:, :, ::-1]; hole = np.isinf(best).reshape(H, W)
    # fill pinholes between ray footprints far from the camera
    filled = cv2.inpaint(rgb, (hole & (cv2.dilate((~hole).astype(np.uint8), np.ones((7, 7))) > 0)).astype(np.uint8), 3, cv2.INPAINT_TELEA)
    filled[hole & ~(cv2.dilate((~hole).astype(np.uint8), np.ones((7, 7))) > 0)] = 255
    rgba = np.dstack([filled, np.where(hole & ~(cv2.dilate((~hole).astype(np.uint8), np.ones((7, 7))) > 0), 0, 255).astype(np.uint8)])
    np.savez_compressed(p, rgb=rgba, x0=x0, y0=y0, cell=cell)
    return rgba, x0, y0, cell

def _ortho_show(ax, cx, alpha=1.0):
    img, x0, y0, cell = orthomosaic(cx)
    ax.imshow(img, extent=[x0, x0 + img.shape[1] * cell, y0, y0 + img.shape[0] * cell], origin="lower", alpha=alpha, interpolation="bilinear")
    return img, x0, y0, cell

def _water_contours(ax, cx, box, color=CYAN, lw=2.0, label=None):
    wm = cx.watermap(); x0, x1, y0, y1 = box
    j0, j1 = int((x0 - wm.x0) / wm.cell), int((x1 - wm.x0) / wm.cell); i0, i1 = int((y0 - wm.y0) / wm.cell), int((y1 - wm.y0) / wm.cell)
    P = wm.prob[i0:i1, j0:j1]; xs = wm.x0 + (np.arange(j0, j1) + 0.5) * wm.cell; ys = wm.y0 + (np.arange(i0, i1) + 0.5) * wm.cell
    ax.contour(xs, ys, P, levels=[0.5], colors=[color], linewidths=lw)
    return P, xs, ys

# ------------------------------------------------------------------ 12. OSM vs optical shoreline on our own orthomosaic
def fig12_shoreline(cx, out):
    from matplotlib.path import Path as MPath
    sc = cx.scene; img, ox, oy, cell = orthomosaic(cx)
    box = (ox, ox + img.shape[1] * cell, oy, oy + img.shape[0] * cell)
    f = _fig(13, 7.6, "Then vs now · Mapped full pool (OSM) vs the shoreline we saw",
             "Left: an orthomosaic built from the GoPro frames themselves. Every frame is projected onto the solved ground and the "
             "closest view of each 0.5 m cell is kept. Right: where the reservoir outline says water should be, but the camera saw dry lakebed.")
    gs = f.add_gridspec(1, 2, left=0.05, right=0.985, top=0.83, bottom=0.08, wspace=0.12)
    ax = f.add_subplot(gs[0]); ax.set_aspect("equal"); _ortho_show(ax, cx)
    ax.plot(*np.r_[sc.pool, sc.pool[:1]].T, color=YELLOW, lw=2, ls=(0, (4, 2)))
    _water_contours(ax, cx, box)
    ax.plot(cx.sfm["Cw"][:, 0], cx.sfm["Cw"][:, 1], color="white", lw=1, alpha=0.8)
    ax.set_xlim(box[0], box[1]); ax.set_ylim(box[2], box[3]); ax.set_xlabel("east (m)"); ax.set_ylabel("north (m)")
    ax.legend(handles=[Line2D([], [], color=YELLOW, lw=2, ls=(0, (4, 2)), label="OSM reservoir outline (full pool)"),
                       Line2D([], [], color=CYAN, lw=2, label="shoreline seen by the GoPro (Oct 19)"),
                       Line2D([], [], color="white", lw=1, label="flight lines")], loc="lower left", fontsize=8.5, facecolor="#00000066", labelcolor="white", frameon=True, edgecolor="none")
    ax.set_title("A · GoPro orthomosaic, 0.5 m per pixel")
    # B: exposed lakebed
    ax = f.add_subplot(gs[1]); ax.set_aspect("equal"); _ortho_show(ax, cx, alpha=0.55)
    H, W = img.shape[:2]; xs = ox + (np.arange(W) + 0.5) * cell; ys = oy + (np.arange(H) + 0.5) * cell
    XX, YY = np.meshgrid(xs, ys); inpool = MPath(sc.pool).contains_points(np.c_[XX.ravel(), YY.ravel()]).reshape(H, W)
    wet = cx.watermap().sample(XX, YY) > 0.5; seen = img[..., 3] > 0
    exposed = inpool & ~wet & seen
    ov = np.zeros((H, W, 4)); ov[exposed] = matplotlib_rgba(EXPOSED, 0.55); ov[wet & seen] = matplotlib_rgba(BLUE, 0.45)
    ax.imshow(ov, extent=[box[0], box[1], box[2], box[3]], origin="lower", interpolation="nearest")
    ax.plot(*np.r_[sc.pool, sc.pool[:1]].T, color=YELLOW, lw=2, ls=(0, (4, 2)))
    a_exp = exposed.sum() * cell ** 2; a_pool = (inpool & seen).sum() * cell ** 2
    ax.set_xlim(box[0], box[1]); ax.set_ylim(box[2], box[3]); ax.set_xlabel("east (m)")
    ax.legend(handles=[Patch(color=EXPOSED, alpha=0.7, label=f"inside the outline but dry: {a_exp / 1e4:.1f} ha"),
                       Patch(color=BLUE, alpha=0.6, label="water seen"),
                       Line2D([], [], color=YELLOW, lw=2, ls=(0, (4, 2)), label="OSM outline")], loc="lower left", fontsize=8.5)
    ax.set_title(f"B · {100 * a_exp / max(a_pool, 1):.0f}% of the mapped pool in view is dry lakebed")
    return _save(f, out, "fig12_shoreline_then_vs_now.png")

def matplotlib_rgba(hexc, a):
    from matplotlib.colors import to_rgba
    r = list(to_rgba(hexc)); r[3] = a; return r

# ------------------------------------------------------------------ 13. COLMAP -> GPS fusion
def fig13_fusion(cx, out):
    from .georef import umeyama
    sc = cx.scene; z = cx.sfm; rec = cx.model()
    ims = sorted([im for im in rec.images.values() if im.has_pose], key=lambda i: int("".join(ch for ch in i.name if ch.isdigit())))
    Craw = np.array([im.projection_center() for im in ims]); vt = z["vt"]; Cw = z["Cw"]
    G = np.array([sc.tel.position(sc.mission_t(v) - 0.45)[0] for v in vt])
    s, R, t = umeyama(Craw, G); Cfit = (s * (R @ Craw.T)).T + t; res = Cfit - G
    kt = vt - cx.t0
    f = _fig(13, 8.2, "Fusing COLMAP with GPS",
             "COLMAP knows the exact shape of the camera path and the camera angles, but not where, which way or how big. GPS knows where "
             "and how big, but not the angles. One similarity transform (7 numbers) joins them.")
    gs = f.add_gridspec(2, 3, left=0.05, right=0.985, top=0.83, bottom=0.08, wspace=0.28, hspace=0.45, height_ratios=[1.25, 1])
    ax = f.add_subplot(gs[0, 0]); ax.set_aspect("equal")
    ax.plot(Craw[:, 0], Craw[:, 1], color=ORANGE, lw=1.8); ax.scatter(*Craw[0, :2], color=ORANGE, s=25, zorder=3)
    ax.set_title("A · COLMAP path, its own units"); ax.set_xlabel("model x"); ax.set_ylabel("model y")
    ax = f.add_subplot(gs[0, 1]); ax.set_aspect("equal")
    ax.plot(G[:, 0], G[:, 1], color=BLUE, lw=4, alpha=0.5, label="GPS/EKF track")
    ax.plot(Cfit[:, 0], Cfit[:, 1], color=ORANGE, lw=1.2, label="COLMAP after fit")
    ax.set_title("B · × 39.6 m, rotated, shifted"); ax.set_xlabel("east (m)"); ax.set_ylabel("north (m)"); ax.legend(loc="lower right", fontsize=8.5)
    ax = f.add_subplot(gs[0, 2]); i = len(G) // 2; w = 18
    m = (np.abs(G[:, 0] - G[i, 0]) < w) & (np.abs(G[:, 1] - G[i, 1]) < w)
    ax.plot(G[:, 0], G[:, 1], color=BLUE, lw=1.6, label="GPS/EKF"); ax.scatter(Cfit[m, 0], Cfit[m, 1], s=16, color=ORANGE, zorder=3, label="COLMAP keyframe")
    for a_, b_ in zip(G[m], Cfit[m]): ax.plot([a_[0], b_[0]], [a_[1], b_[1]], color=INK2, lw=0.8)
    ax.set_xlim(G[i, 0] - w, G[i, 0] + w); ax.set_ylim(G[i, 1] - w, G[i, 1] + w); ax.set_aspect("equal")
    ax.set_title("C · Zoom: 36 m box, gaps drawn"); ax.set_xlabel("east (m)"); ax.legend(loc="lower right", fontsize=8.5)
    ax = f.add_subplot(gs[1, :])
    for k, (nm, c) in enumerate([("east", BLUE), ("north", ORANGE), ("up", AQUA)]):
        ax.plot(kt, res[:, k], color=c, lw=1.5, label=f"{nm}  (rms {np.sqrt((res[:, k] ** 2).mean()):.2f} m)")
    ax.axhline(0, color=INK2, lw=0.8)
    ax.set_xlabel("clip time (s)"); ax.set_ylabel("COLMAP − GPS (m)"); ax.legend(loc="upper left", ncol=3)
    ax.set_title("D · What is left after the fit: about a meter, the size of GPS noise itself")
    return _save(f, out, "fig13_colmap_gps_fusion.png")

# ------------------------------------------------------------------ 14. 3D georeferenced scene
def _look_at(C, target, up=(0, 0, 1.0)):
    f_ = np.asarray(target, float) - C; f_ /= np.linalg.norm(f_)
    r_ = np.cross(f_, up); r_ /= np.linalg.norm(r_); d_ = np.cross(f_, r_)
    return np.c_[r_, d_, f_]                                   # camera->world (x right, y down, z forward)

def fig14_scene3d(cx, out, t=52.0, W=1800, H=1000):
    """Ray-traced virtual chase view of the solved scene: orthomosaic on the SfM surface, camera path,
    frustums, and the satellite -> specular point -> receiver paths with their Fresnel zones."""
    from .scene import sat_state
    from PIL import Image, ImageDraw, ImageFont
    sc = cx.scene; z = cx.sfm; Cw = z["Cw"] + [0, 0, 1.55]
    mt, cam, _, _ = cx.pose(t); rx = cam.C; _, sats = sat_state(sc, mt, rx)
    img, ox, oy, cell = orthomosaic(cx)
    fw = cam.R[:, 2].copy(); fw[2] = 0; fw /= np.linalg.norm(fw); side = np.array([fw[1], -fw[0], 0])
    target = rx + fw * 90 + np.array([0, 0, -rx[2]]); Cv = rx - fw * 230 - side * 210 + np.array([0, 0, 190])
    R = _look_at(Cv, target); fpx = 1.05 * W
    ys, xs = np.mgrid[0:H, 0:W].astype(np.float64)
    d = np.c_[((xs - W / 2) / fpx).ravel(), ((ys - H / 2) / fpx).ravel(), np.ones(W * H)] @ R.T
    d /= np.linalg.norm(d, axis=1, keepdims=True)
    hit, ok = sc.terrain.ray_hit(Cv, d, iters=5); ok &= d[:, 2] < -0.01
    u = ((hit[:, 0] - ox) / cell - 0.5).reshape(H, W).astype(np.float32); v = ((hit[:, 1] - oy) / cell - 0.5).reshape(H, W).astype(np.float32)
    u[~ok.reshape(H, W)] = -10; v[~ok.reshape(H, W)] = -10
    tex = cv2.remap(img, u, v, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0)).astype(np.float32)
    sky = np.linspace(0, 1, H)[:, None, None] * np.array([252, 252, 251]) + (1 - np.linspace(0, 1, H)[:, None, None]) * np.array([214, 228, 240])
    a = tex[..., 3:4] / 255.0
    # haze with distance
    dist = np.linalg.norm(hit - Cv, axis=1).reshape(H, W, 1); hz = np.clip((dist - 250) / 900, 0, 0.55)
    base = tex[..., :3] * (1 - hz) + np.array([236, 240, 244]) * hz
    out_img = (base * a + sky * (1 - a)).clip(0, 255).astype(np.uint8)
    pil = Image.fromarray(out_img); dr = ImageDraw.Draw(pil, "RGBA")
    def proj(P):
        Pc = (np.atleast_2d(P) - Cv) @ R; okp = Pc[:, 2] > 1
        return np.c_[fpx * Pc[:, 0] / Pc[:, 2] + W / 2, fpx * Pc[:, 1] / Pc[:, 2] + H / 2], okp
    def line(P, col, w, a=255):
        uv, okp = proj(P)
        for k in range(len(uv) - 1):
            if okp[k] and okp[k + 1]: dr.line([tuple(uv[k]), tuple(uv[k + 1])], fill=(*col, a), width=w)
    hexrgb = lambda h: tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))
    # camera path + nadir shadow
    m = np.linalg.norm(Cw[:, :2] - rx[:2], axis=1) < 600
    sh = Cw[m].copy(); sh[:, 2] = sc.terrain.height(sh[:, 0], sh[:, 1]) + 0.3
    line(sh, (0, 0, 0), 2, 60); line(Cw[m], (255, 255, 255), 7, 230); line(Cw[m], (25, 25, 25), 3)
    for i in np.where(m)[0][::45]:
        Cc = Cw[i]; Rr = z["Rwc"][i]; corners = Cc + (np.array([[-1, -0.56, 1], [1, -0.56, 1], [1, 0.56, 1], [-1, 0.56, 1]]) * 9) @ Rr.T
        for q in corners: line(np.r_[[Cc], [q]], (25, 25, 25), 2, 200)
        line(np.r_[corners, corners[:1]], (25, 25, 25), 2, 200)
    # satellite paths + zones
    labels = []
    for s in sorted(sats, key=lambda s: s["el"]):
        if s["el"] < cx.cfg["elevation_mask_deg"]: continue
        col = hexrgb(CONST.get(s["const"], (MUTED,))[0])
        c_, ring, _ = fresnel_zone(s["enu"], rx, sc.wl, 90)
        ring[:, 2] = sc.terrain.height(ring[:, 0], ring[:, 1]) + 0.3; c_[2] = float(sc.terrain.height(c_[0], c_[1]))
        uvr, okr = proj(ring)
        if okr.all(): dr.polygon([tuple(q) for q in uvr], fill=(*col, 90), outline=(*col, 255), width=3)
        line(np.r_[[c_ + s["dir"] * 150], [c_]], col, 2, 230); line(np.r_[[c_], [rx]], col, 2, 160)
        uvc, okc = proj(ring[np.argmax(ring[:, 1] - 0 * ring[:, 0])])
        if okc[0]: labels.append((uvr[np.argmin(uvr[:, 1])] if okr.all() else uvc[0], f"{s['sid']} {s['el']:.0f}°", col))
    uvx, _ = proj(rx); dr.ellipse([uvx[0][0] - 9, uvx[0][1] - 9, uvx[0][0] + 9, uvx[0][1] + 9], fill=(11, 11, 11, 255), outline=(255, 255, 255, 255), width=3)
    try: fnt = ImageFont.truetype(str(Path(__file__).resolve().parent.parent / "fonts" / "JetBrainsMono-Medium.ttf"), 22)
    except OSError: fnt = ImageFont.load_default()
    for (q, txt, col) in labels:
        if 0 < q[0] < W - 120 and 0 < q[1] < H: dr.text((q[0] - 30, q[1] - 30), txt, font=fnt, fill=(20, 20, 20, 255), stroke_width=3, stroke_fill=(255, 255, 255, 230))
    dr.text((uvx[0][0] + 16, uvx[0][1] - 14), "drone / receiver", font=fnt, fill=(20, 20, 20, 255), stroke_width=3, stroke_fill=(255, 255, 255, 230))
    f = _fig(13, 8.2, "The solved scene in 3D, in real meters",
             f"A virtual camera looking at the reconstruction: the GoPro orthomosaic on the SfM surface, the solved camera path with frustums, "
             f"and at t = {t:.0f} s each satellite's ray bouncing off its specular point to the drone, with its first Fresnel zone on the ground.")
    ax = f.add_axes([0.01, 0.01, 0.98, 0.84]); ax.imshow(np.array(pil)); ax.set_axis_off()
    ax.legend(handles=[Line2D([], [], color=CONST[k][0], lw=3, label=CONST[k][1]) for k in "GEC"] +
              [Line2D([], [], color="#191919", lw=3, label="solved camera path + frustums")], loc="lower left", fontsize=10, frameon=True, facecolor="white", edgecolor="none")
    return _save(f, out, "fig14_scene_3d.png")

# ------------------------------------------------------------------ 15. pixel <-> ground
def fig15_pixel_ground(cx, out, t=52.0):
    from .render import ground_field
    sc = cx.scene; fr = cx.frame(t); _, cam, _, _ = cx.pose(t)
    G = ground_field(cam, cx.W, cx.H, 0.5, sc.terrain); sh, sw = G.shape[:2]
    f = _fig(13, 6.2, "Pixel ↔ ground: every pixel knows its spot on Earth",
             "With position, angles and lens solved, each pixel's ray is traced to the surface. Lines are 25 m east/north grid lines and "
             "50 m range rings from the drone's nadir, computed on the ground and drawn through the camera.")
    gs = f.add_gridspec(1, 2, left=0.01, right=0.985, top=0.83, bottom=0.08, wspace=0.08, width_ratios=[1.45, 1])
    ax = f.add_subplot(gs[0]); _img_ax(ax, fr, "A · Ground coordinates seen by the camera")
    xs = (np.arange(sw) + 0.5) / 0.5; ys = (np.arange(sh) + 0.5) / 0.5
    E, N = G[..., 0], G[..., 1]; Rg = np.hypot(E - cam.C[0], N - cam.C[1])
    lv = lambda a: np.arange(np.floor(np.nanmin(a) / 25) * 25, np.nanmax(a), 25)
    far = Rg > 260; E = np.where(far, np.nan, E); N = np.where(far, np.nan, N)
    ax.contour(xs, ys, E, levels=lv(E), colors="white", linewidths=0.8, alpha=0.85, negative_linestyles="solid")
    ax.contour(xs, ys, N, levels=lv(N), colors="white", linewidths=0.8, alpha=0.85, negative_linestyles="solid")
    cs = ax.contour(xs, ys, cv2.GaussianBlur(np.nan_to_num(Rg, nan=999).astype(np.float32), (0, 0), 2), levels=np.arange(50, 251, 50), colors=[YELLOW], linewidths=1.6)
    ax.clabel(cs, fmt=lambda v: f"{v:.0f} m", fontsize=8, colors=YELLOW)
    for (u, v) in [(500, 700), (1400, 820), (960, 330)]:
        e, n = G[int(v * 0.5), int(u * 0.5), :2]
        ax.scatter([u], [v], s=40, color=ORANGE, edgecolor="white", lw=1.2, zorder=4)
        ax.annotate(f"px ({u},{v})\n→ E {e:.0f}, N {n:.0f} m", (u, v), xytext=(8, -4), textcoords="offset points", fontsize=8.5, color="white",
                    fontweight="bold", bbox=dict(fc="#000000aa", ec="none", pad=2))
    ax.set_xlim(0, cx.W); ax.set_ylim(cx.H, 0)
    ax = f.add_subplot(gs[1]); ax.set_aspect("equal"); _ortho_show(ax, cx)
    okg = np.isfinite(Rg) & (Rg < 260); bx = (cam.C[0] - 150, cam.C[0] + 330); by = (cam.C[1] - 360, cam.C[1] + 120)
    Hh, ex, ey = np.histogram2d(G[..., 0][okg], G[..., 1][okg], bins=[240, 240], range=[bx, by])
    cov = cv2.morphologyEx((Hh.T > 0).astype(np.uint8), cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    ov = np.zeros(cov.shape + (4,)); ov[cov > 0] = matplotlib_rgba(YELLOW, 0.28)
    ax.imshow(ov, extent=[bx[0], bx[1], by[0], by[1]], origin="lower", interpolation="nearest")
    ax.contour((ex[:-1] + ex[1:]) / 2, (ey[:-1] + ey[1:]) / 2, cv2.GaussianBlur(cov.astype(np.float32), (0, 0), 1), levels=[0.5], colors=[YELLOW], linewidths=1.8)
    for r in range(50, 251, 50): ax.add_patch(plt.Circle(cam.C[:2], r, fill=False, color=YELLOW, lw=0.8, alpha=0.7))
    ax.scatter(*cam.C[:2], marker="^", s=70, color=ORANGE, edgecolor="white", zorder=4)
    for (u, v) in [(500, 700), (1400, 820), (960, 330)]:
        e, n = G[int(v * 0.5), int(u * 0.5), :2]; ax.scatter([e], [n], s=40, color=ORANGE, edgecolor="white", lw=1.2, zorder=4)
    ax.set_xlim(cam.C[0] - 150, cam.C[0] + 330); ax.set_ylim(cam.C[1] - 360, cam.C[1] + 120)
    ax.set_title("B · From above: ground this frame sees (to 260 m)"); ax.set_xlabel("east (m)"); ax.set_ylabel("north (m)")
    return _save(f, out, "fig15_pixel_to_ground.png")

# ------------------------------------------------------------------ 16. reflection tracks over the clip
def fig16_tracks(cx, out):
    from .scene import sat_state
    sc = cx.scene; wm = cx.watermap(); img, ox, oy, cell = orthomosaic(cx)
    from matplotlib.path import Path as MPath
    pool_path = MPath(sc.pool); tr = {}
    for t in np.arange(0, cx.cfg["clip_duration_s"], 0.5):
        mt, cam, _, _ = cx.pose(t); rx = cam.C; _, sats = sat_state(sc, mt, rx)
        for s in sats:
            if s["el"] < cx.cfg["elevation_mask_deg"]: continue
            c_, _, _ = fresnel_zone(s["enu"], rx, sc.wl, 8)
            tr.setdefault((s["const"], s["sid"]), []).append((t, c_[0], c_[1], float(wm.sample(np.array([c_[0]]), np.array([c_[1]]))[0]),
                                                              float(pool_path.contains_point(c_[:2]))))
    f = _fig(13, 8.0, "Where the reflections went during the survey",
             "Specular-point track of every satellite above 20° over the clip, on the GoPro orthomosaic. Solid where the reflection lands "
             "on water (usable GNSS-R), faint where it lands on land.")
    gs = f.add_gridspec(1, 2, left=0.05, right=0.985, top=0.83, bottom=0.08, wspace=0.15, width_ratios=[1.3, 1])
    ax = f.add_subplot(gs[0]); ax.set_aspect("equal"); _ortho_show(ax, cx, alpha=0.8)
    box = (ox, ox + img.shape[1] * cell, oy, oy + img.shape[0] * cell); _water_contours(ax, cx, box, lw=1.2)
    stats = []
    for (k, sid), rows in sorted(tr.items()):
        a = np.array(rows); col = CONST.get(k, (MUTED, ""))[0]; wet = a[:, 3] > 0.5
        ax.plot(a[:, 1], a[:, 2], color=col, lw=0.8, alpha=0.35)
        ax.scatter(a[wet, 1], a[wet, 2], s=5, color=col, lw=0, zorder=3)
        stats.append((sid, k, wet.mean(), a[:, 4].mean()))
    ax.plot(cx.sfm["Cw"][:, 0], cx.sfm["Cw"][:, 1], color="white", lw=1)
    ax.set_xlim(box[0], box[1]); ax.set_ylim(box[2], box[3]); ax.set_xlabel("east (m)"); ax.set_ylabel("north (m)")
    ax.legend(handles=[Line2D([], [], color=CONST[k][0], lw=3, label=CONST[k][1]) for k in "GEC"] + [Line2D([], [], color=CYAN, lw=1.5, label="shoreline")],
              loc="lower left", fontsize=8.5)
    ax.set_title("A · Specular-point tracks (dots = on water)")
    tot_osm = np.mean([r[3] for r in stats]); tot_w = np.mean([r[2] for r in stats]); nsat = len(stats)
    ax = f.add_subplot(gs[1]); stats.sort(key=lambda r: (-r[3], -r[2])); stats = [r for r in stats if r[3] > 0 or r[2] > 0]
    y = np.arange(len(stats)); hgt = 0.38
    ax.barh(y - hgt / 2, [100 * r[3] for r in stats], height=hgt, color=YELLOW, edgecolor=SURF, lw=1, label="if the lake were at full pool (OSM)")
    ax.barh(y + hgt / 2, [100 * r[2] for r in stats], height=hgt, color=BLUE, edgecolor=SURF, lw=1, label="water actually there (seen)")
    for yi, r in zip(y, stats):
        ax.text(100 * r[3] + 1.5, yi - hgt / 2, f"{100 * r[3]:.0f}%", va="center", fontsize=8, color=INK2)
        ax.text(100 * r[2] + 1.5, yi + hgt / 2, f"{100 * r[2]:.0f}%", va="center", fontsize=8, color=INK2)
    ax.set_yticks(y, [r[0] for r in stats]); ax.invert_yaxis(); ax.set_xlim(0, 105); ax.grid(axis="y", visible=False)
    ax.set_xlabel("share of the clip the reflection lands on water (%)"); ax.legend(loc="lower right", fontsize=8.5)
    ax.set_title(f"B · {nsat} sats: {100 * tot_osm:.0f}% on water at full pool, {100 * tot_w:.0f}% real")
    return _save(f, out, "fig16_reflection_tracks.png")

EXTRA = {12: fig12_shoreline, 13: fig13_fusion, 14: fig14_scene3d, 15: fig15_pixel_ground, 16: fig16_tracks}

# ------------------------------------------------------------------ 17. COLMAP output, georeferenced, as a 3D render
def fig17_pointcloud(cx, out, W=1800, H=1000):
    from PIL import Image, ImageDraw
    sc = cx.scene; z = cx.sfm; rec = cx.model(); s_ = float(z["sim_scale"])
    # all points with colour, mapped into ENU with the same similarity georef used for the cameras
    from .georef import umeyama
    ims = sorted([im for im in rec.images.values() if im.has_pose], key=lambda i: int("".join(ch for ch in i.name if ch.isdigit())))
    Craw = np.array([im.projection_center() for im in ims]); Cw = z["Cw"]
    s, R, t = umeyama(Craw, Cw)
    P = np.array([p.xyz for p in rec.points3D.values()]); col = np.array([p.color for p in rec.points3D.values()])
    err = np.array([p.error for p in rec.points3D.values()]); Pw = (s * (R @ P.T)).T + t + [0, 0, 1.55]
    keep = err < 2; Pw, col = Pw[keep], col[keep]
    mid = Cw.mean(0); fw = Cw[-1] - Cw[0]; fw[2] = 0; fw /= np.linalg.norm(fw); side = np.array([fw[1], -fw[0], 0])
    Cv = mid - fw * 330 + side * 260 + np.array([0, 0, 210]); target = mid + side * -60 + np.array([0, 0, -mid[2]])
    Rv = _look_at(Cv, target); fpx = 1.0 * W
    def proj(Q):
        Qc = (np.atleast_2d(Q) - Cv) @ Rv; return np.c_[fpx * Qc[:, 0] / Qc[:, 2] + W / 2, fpx * Qc[:, 1] / Qc[:, 2] + H / 2], Qc[:, 2]
    uv, dep = proj(Pw); o = np.argsort(-dep); ok = (dep[o] > 1)
    canvas = np.zeros((H, W, 3), np.uint8); canvas[:] = (24, 26, 30)
    for (u, v), c, d in zip(uv[o][ok], col[o][ok], dep[o][ok]):
        if 0 <= u < W and 0 <= v < H:
            r = int(np.clip(900 / d, 1, 4)); cv2.circle(canvas, (int(u), int(v)), r, (int(c[0]), int(c[1]), int(c[2])), -1, cv2.LINE_AA)
    pil = Image.fromarray(canvas); dr = ImageDraw.Draw(pil, "RGBA")
    uvc, dc = proj(Cw + [0, 0, 1.55])
    for k in range(len(uvc) - 1):
        if dc[k] > 1 and dc[k + 1] > 1: dr.line([tuple(uvc[k]), tuple(uvc[k + 1])], fill=(235, 104, 52, 255), width=3)
    for i in range(0, len(Cw), 10):
        Cc = Cw[i] + [0, 0, 1.55]; Rr = z["Rwc"][i]
        corners = Cc + (np.array([[-1, -0.56, 1], [1, -0.56, 1], [1, 0.56, 1], [-1, 0.56, 1]]) * 7) @ Rr.T
        q, dq = proj(np.r_[[Cc], corners])
        if (dq > 1).all():
            for k in range(1, 5): dr.line([tuple(q[0]), tuple(q[k])], fill=(255, 196, 61, 220), width=1)
            dr.polygon([tuple(x) for x in q[1:]], outline=(255, 196, 61, 255), fill=(255, 196, 61, 40))
    f = plt.figure(figsize=(13, 8.0), dpi=150); f.patch.set_facecolor("#181a1e")
    import os
    if not os.environ.get("HERON_FIG_NOTITLE"): f.text(0.012, 0.975, "What COLMAP builds: the sparse 3D model", fontsize=16, fontweight="bold", color="#f0efec", va="top")
    if not os.environ.get("HERON_FIG_NOTITLE"): f.text(0.012, 0.935, f"{int(keep.sum()):,} triangulated points in their photo colour and {len(Cw)} solved camera poses (one frustum every 10th), "
           f"after georeferencing (scale {s_:.1f} m per model unit). The bare beach, the tree line and the water's edge are all recovered from the video alone.",
           fontsize=10.5, color="#c3c2b7", va="top", wrap=True)
    ax = f.add_axes([0.0, 0.0, 1.0, 0.88]); ax.imshow(np.array(pil)); ax.set_axis_off()
    ax.legend(handles=[Line2D([], [], color=ORANGE, lw=3, label="solved camera path"), Line2D([], [], color="#ffc43d", lw=2, label="camera frustums (pointing direction + field of view)")],
              loc="lower left", fontsize=10, facecolor="#00000099", labelcolor="white", frameon=True, edgecolor="none")
    p = Path(out) / "fig17_colmap_pointcloud.png"; f.savefig(p, dpi=150, facecolor=f.get_facecolor()); plt.close(f); print("  wrote", p); return p

EXTRA[17] = fig17_pointcloud
