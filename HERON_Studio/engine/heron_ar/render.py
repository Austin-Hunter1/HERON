"""HERON AR v2: presentation renderer.

Holographic ground-projected Fresnel zones (per-pixel on the solved terrain/water
surface, emissive with bloom), a world-anchored ground grid with range rings around the
drone's nadir, incident signal beams from each satellite, 3-D pins with callouts,
segmented water with a live shoreline, and a glass HUD (compass tape, telemetry, map,
sky plot, chase view of the true ground ellipses).

    python3 render_v2.py --preview 44.6,75
    python3 render_v2.py [--frame-start N --frame-end M --out file.mp4]
"""
from __future__ import annotations
import argparse, csv, json, math, shutil, subprocess, time
from pathlib import Path
import numpy as np, cv2
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from .scene import (Scene, segment_water, horizon_mask_for, sat_state, clip_poly_front, densify, poly_ok,
                       DATA, ROOT, CCOL, CNAME)
from .geometry import Fisheye, CameraPose, fresnel_zone, sun_enu_dir, resolve, CACHE, OUT, clip_fps
from .chase_view import ChaseCam, render_chase

F_DIR = ROOT / "fonts"
_fc = {}
def F(name, px):
    k = (name, px)
    if k not in _fc:
        try: _fc[k] = ImageFont.truetype(str(F_DIR / name), px)
        except OSError: _fc[k] = ImageFont.load_default()
    return _fc[k]

ACC = np.array([90, 230, 255], np.float32)        # primary HUD accent (RGB)
WATER = (70, 200, 255)
INK = (236, 244, 250); DIM = (150, 168, 184); FAINT = (95, 112, 128)

# ----------------------------------------------------------------- per-pixel ground field
_ray_cache = {}
def cam_rays(lens, w, h, scale):
    k = (id(lens), w, h, scale)
    if k not in _ray_cache:
        sw, sh = int(w * scale), int(h * scale)
        ys, xs = np.mgrid[0:sh, 0:sw].astype(np.float64)
        _ray_cache[k] = (lens.unproject(np.c_[(xs.ravel() + 0.5) / scale, (ys.ravel() + 0.5) / scale]), sw, sh)
    return _ray_cache[k]

def ground_field(cam, w, h, scale, terrain):
    """Ground points for a (h*scale, w*scale) grid of pixels; NaN above the horizon."""
    rc, sw, sh = cam_rays(cam.lens, w, h, scale)
    d = rc @ cam.R.T
    if terrain is not None: P, ok = terrain.ray_hit(cam.C, d, iters=4)
    else:
        s = -cam.C[2] / d[:, 2]; ok = s > 0; P = cam.C + s[:, None] * d
    ok &= d[:, 2] < -0.01
    P[~ok] = np.nan
    return P.reshape(sh, sw, 3)

def fwidth(f):
    gy, gx = np.gradient(f); return np.hypot(gx, gy) + 1e-9

def line_mask(v, period, width_px, fv):
    d = (v / period - np.round(v / period)) * period
    return np.clip(1 - np.abs(d) / (fv * width_px), 0, 1)

def ground_grid(G, rx, t, scale):
    """Holographic grid: 10 m minor / 50 m major lines + range rings around nadir, fading with range."""
    x, y = G[..., 0], G[..., 1]; ok = np.isfinite(x)
    x = np.where(ok, x, 0); y = np.where(ok, y, 0)
    fx, fy = fwidth(x), fwidth(y)
    minor = np.maximum(line_mask(x, 10, 0.9, fx), line_mask(y, 10, 0.9, fy))
    major = np.maximum(line_mask(x, 50, 1.2, fx), line_mask(y, 50, 1.2, fy))
    r = np.hypot(x - rx[0], y - rx[1]); fr = fwidth(r)
    rings = np.zeros_like(r)
    for R0 in (25, 50, 100, 200):
        rings = np.maximum(rings, np.clip(1 - np.abs(r - R0) / (fr * 1.3), 0, 1))
    pulse_r = (t % 3.0) / 3.0 * 260                                         # expanding sonar ring on the ground
    sweep = np.clip(1 - np.abs(r - pulse_r) / (fr * 6 + 1.5), 0, 1) * (1 - pulse_r / 260)
    fade = np.clip(1 - r / 330, 0, 1) ** 1.6 * ok
    alias = np.clip(1.6 - fx * 0.35, 0, 1)                                  # suppress lines where cells shrink < few px
    a = (minor * 0.16 * alias + major * 0.30 + rings * 0.40 + sweep * 0.40) * fade
    return a.astype(np.float32), r

# ----------------------------------------------------------------- holographic Fresnel zone
def holo_zone(img, glow, cam, sat_enu, az_deg, center, ring_uv, wl, color, minor_m, major_m, t, on_water, S, minimal=False, wmap=None, zoff=None):
    h, w = img.shape[:2]
    x0, y0 = np.floor(ring_uv.min(0)); x1, y1 = np.ceil(ring_uv.max(0)); cx, cy = ring_uv.mean(0); pad = 0.6
    x0 = int(max(0, cx - (cx - x0) * (1 + pad))); x1 = int(min(w - 1, cx + (x1 - cx) * (1 + pad)))
    y0 = int(max(0, cy - (cy - y0) * (1 + pad))); y1 = int(min(h - 1, cy + (y1 - cy) * (1 + pad)))
    if x1 <= x0 + 3 or y1 <= y0 + 3: return
    ys, xs = np.mgrid[y0:y1 + 1, x0:x1 + 1]
    uv = np.c_[xs.ravel(), ys.ravel()].astype(np.float64)
    P, ok = cam.ray_ground(uv); rx = cam.C
    base = np.linalg.norm(sat_enu - center) + np.linalg.norm(rx - center)
    v1 = P - sat_enu; v2 = P - rx; n1 = np.linalg.norm(v1, axis=1); n2 = np.linalg.norm(v2, axis=1)
    zn = ((n1 + n2 - base) / (wl / 2)).reshape(ys.shape); zn[~ok.reshape(ys.shape)] = 99
    grad = (v1 / n1[:, None] + v2 / n2[:, None])[:, :2]
    gz = (np.linalg.norm(grad, axis=1) / (wl / 2)).reshape(ys.shape)
    dist = (zn - 1.0) / np.maximum(gz, 1e-9)                                # metres to the zone edge (signed, on ground)
    azr = math.radians(az_deg); al = np.array([math.sin(azr), math.cos(azr)]); ac = np.array([math.cos(azr), -math.sin(azr)])
    d2 = P[:, :2] - center[:2]
    a = (d2 @ al).reshape(ys.shape); c = (d2 @ ac).reshape(ys.shape)
    fd, fa, fc = fwidth(dist), fwidth(a), fwidth(c)
    inside = zn < 1
    band = max(0.35, 0.055 * minor_m)
    rim = np.clip(0.5 - (np.abs(dist + band / 2) - band / 2) / fd, 0, 1)                     # metric-width rim
    halo = np.exp(-np.clip(dist, 0, None) / (0.22 * minor_m)) * (dist > 0) * 0.55            # glow spilling onto the ground
    inner_fill = inside * (0.18 + 0.30 * np.clip(zn, 0, 1) ** 1.5)
    # ground-aligned hatch: lines across the satellite direction every g metres (shows the true elongation)
    g = max(0.5, round(minor_m / 5 * 2) / 2)
    hatch = line_mask(a, g, 1.1, fa) * inside * 0.75
    spine = np.clip(1 - np.abs(c) / (fc * 1.1), 0, 1) * inside * 0.55 * ((np.floor(a / max(g / 2, 0.5)) % 2) == 0)
    # scan: a light bar sweeping along the incident direction across the ground
    L = major_m; ph = ((t / 2.4) % 1.0) * 1.6 - 0.3
    scan = np.exp(-((a / L - (ph - 0.5)) ** 2) / (2 * 0.035 ** 2)) * np.clip(1.2 - zn, 0, 1) * 0.9
    core = np.exp(-(np.hypot(a, c) / max(0.6, 0.07 * minor_m)) ** 2) * 0.9
    ripple = np.zeros_like(zn)
    if on_water:
        for k in range(3):
            q = ((t / 1.8 + k / 3) % 1.0) * 2.2
            ripple = np.maximum(ripple, np.clip(1 - np.abs(zn - q) / (fwidth(zn) * 1.5), 0, 1) * (1 - q / 2.2) * 0.6)
    sub = img[y0:y1 + 1, x0:x1 + 1]; gsub = glow[y0:y1 + 1, x0:x1 + 1]
    lum = sub @ np.array([0.299, 0.587, 0.114], np.float32)
    shade = np.clip(lum / (cv2.GaussianBlur(lum, (0, 0), 7) + 1), 0.5, 1.5)[..., None]       # ground texture modulates the light
    col = np.array(color, np.float32)[None, None]
    # colour-grade the ground inside the zone (paint), then add emissive light on top
    paint = inner_fill[..., None]
    sub[:] = sub * (1 - paint) + (sub * 0.45 + col * shade * 0.55) * paint
    if minimal and wmap is not None:
        # ---- water-aware look: the part of the zone that lies on water lights up cyan/white,
        #      the part on land stays a thin, dim outline. Decided per ground point.
        Pw = (P + (zoff if zoff is not None else 0))
        wpx = wmap.sample(Pw[:, 0], Pw[:, 1]).reshape(ys.shape)
        wpx = np.clip((wpx - 0.35) / 0.3, 0, 1)
        WCOL = np.array([120, 235, 255], np.float32)[None, None]
        # land part: subtle darkening + constellation-coloured thin rim
        land = inside * (1 - wpx)
        sub[:] = sub * (1 - 0.18 * land[..., None])
        sub[:] = np.clip(sub + col * (rim * (1 - wpx) * 0.9)[..., None] + col * (hatch * 0.25 * (1 - wpx))[..., None], 0, 255)
        # water part: bright filled glow, white hatch, bold rim, ripples
        wet = inside * wpx
        fillw = (0.55 + 0.25 * np.clip(zn, 0, 1) ** 2) * wet
        sub[:] = sub * (1 - fillw[..., None]) + (sub * 0.3 + WCOL * 0.7) * fillw[..., None]
        emw = (rim * 1.6 + hatch * 0.9 + ripple * 1.2) * np.maximum(wpx, 0)
        sub[:] = np.clip(sub + (WCOL * 0.55 + col * 0.45) * emw[..., None], 0, 255)
        gsub[:] += WCOL * ((rim * 1.0 + fillw * 0.5 + ripple * 0.6) * wpx)[..., None]
        return
    if minimal: scan = scan * 0; ripple = ripple * 0; spine = spine * 0.6; core = core * 0.6
    emis = (rim * 1.35 + hatch + spine + scan + core + ripple)[..., None]
    sub[:] = np.clip(sub + col * emis * (0.55 + 0.45 * shade) * 0.9, 0, 255)
    gsub[:] += col * (rim * 0.9 + halo + scan * 0.6 + core * 0.8 + ripple * 0.5)[..., None]

# ----------------------------------------------------------------- glass panels
def glass(base_rgb, box, radius, tint=(8, 14, 22), alpha=0.58, blur=14):
    x0, y0, x1, y1 = [int(v) for v in box]; x0, y0 = max(0, x0), max(0, y0); x1, y1 = min(base_rgb.shape[1], x1), min(base_rgb.shape[0], y1)
    crop = base_rgb[y0:y1, x0:x1].astype(np.float32)
    bl = cv2.GaussianBlur(crop, (0, 0), blur)
    m = np.zeros((y1 - y0, x1 - x0), np.uint8); cv2.rectangle(m, (radius, 0), (x1 - x0 - radius, y1 - y0), 255, -1)
    cv2.rectangle(m, (0, radius), (x1 - x0, y1 - y0 - radius), 255, -1)
    for cxy in [(radius, radius), (x1 - x0 - radius - 1, radius), (radius, y1 - y0 - radius - 1), (x1 - x0 - radius - 1, y1 - y0 - radius - 1)]:
        cv2.circle(m, cxy, radius, 255, -1, cv2.LINE_AA)
    a = (m.astype(np.float32) / 255)[..., None]
    out = bl * (1 - alpha) + np.array(tint, np.float32) * alpha
    base_rgb[y0:y1, x0:x1] = (crop * (1 - a) + out * a).astype(np.uint8)

def frame_lines(d, box, S, color=(255, 255, 255), a=46):
    x0, y0, x1, y1 = box; L = 14 * S
    d.rounded_rectangle(box, int(12 * S), outline=(*color, a), width=1)
    for (cx, cy, sx, sy) in [(x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)]:
        d.line([(cx + sx * 4 * S, cy + sy * (4 * S + L)), (cx + sx * 4 * S, cy + sy * 4 * S), (cx + sx * (4 * S + L), cy + sy * 4 * S)],
               fill=(*[int(v) for v in ACC], 200), width=max(1, int(1.6 * S)))

def txt(d, xy, s, f, fill=INK, anchor="la", a=255):
    d.text(xy, s, font=f, fill=(*fill, a), anchor=anchor)

_vg = {}
def vignette(w, h):
    if (w, h) not in _vg:
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        _vg[(w, h)] = (1 - 0.22 * (((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2) ** 1.3)[..., None]
    return _vg[(w, h)]

# ----------------------------------------------------------------- compose
def compose_v2(frame_bgr, scene, vt, lens, fidx, fps, rows):
    cfg = scene.cfg; h, w = frame_bgr.shape[:2]; S = w / 1920.0; t = fidx / fps
    mt, cam, lat, lon = scene.pose(vt, lens)
    rx = cam.C; dt, sats = sat_state(scene, mt, rx)
    hz = horizon_mask_for(cam, w, h)
    gs = 0.4
    G = ground_field(cam, w, h, gs, scene.terrain)
    wmap = getattr(scene, "watermap", None)
    if wmap is not None:
        pw = wmap.sample(G[..., 0], G[..., 1])
        pw = cv2.resize(pw, (w, h), interpolation=cv2.INTER_LINEAR)
        from .water_refine import refine
        pw = refine(frame_bgr, pw, hz > 0)                      # shoreline re-decided from this frame's own pixels
        water_soft = np.clip((pw - 0.35) / 0.3, 0, 1) * (hz > 0)
        water = (water_soft > 0.5).astype(np.uint8)
    else:
        water, prior = segment_water(frame_bgr, cam, scene, hz); water_soft = water.astype(np.float32)
    img = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB).astype(np.float32)
    hz_cache = None
    # slight cinematic grade: cooler shadows, gentle contrast
    if not cfg.get("minimal", False): img = np.clip((img - 128) * 1.04 + 128 + np.array([-3, 0, 5], np.float32), 0, 255)
    glow = np.zeros_like(img)

    # ---- water: soft cyan grade + shimmering shoreline
    wm = cv2.GaussianBlur(water_soft, (0, 0), 1.0)[..., None]
    img = img * (1 - 0.30 * wm) + (img * 0.55 + np.array(WATER, np.float32) * 0.45) * 0.30 * wm
    # smooth anti-aliased shoreline: contour of the mask, lightly smoothed, drawn as a 2 px line
    cnts, _ = cv2.findContours((water_soft > 0.5).astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    shore = np.zeros((h, w), np.float32)
    for cn in cnts:
        if len(cn) < 40: continue
        p_ = cn[:, 0, :].astype(np.float32)
        k_ = 7; p_ = np.stack([np.convolve(np.r_[p_[-k_:, i], p_[:, i], p_[:k_, i]], np.ones(2 * k_ + 1) / (2 * k_ + 1), "valid") for i in range(2)], 1)
        cv2.polylines(shore, [np.int32(p_ * 16)], True, 1.0, max(2, int(2 * S)), cv2.LINE_AA, shift=4)
    shore *= (hz > 0)
    img = np.clip(img * (1 - 0.8 * shore[..., None]) + np.array([215, 245, 255], np.float32) * 0.8 * shore[..., None], 0, 255)
    glow += shore[..., None] * np.array([60, 170, 255], np.float32) * 0.5

    # ---- holographic ground grid (half resolution field)
    ga, rng = ground_grid(G, rx, (t if not cfg.get('minimal') else 1e-3), gs)
    if cfg.get('minimal'): ga = ga * 0.55
    ga = cv2.resize(ga, (w, h), interpolation=cv2.INTER_LINEAR)[..., None]
    img = np.clip(img + ga * ACC * 0.85, 0, 255); glow += ga * ACC * 0.35

    # ---- zones (draped on the local surface)
    items = []
    for s in sats:
        if s["el"] < cfg["elevation_mask_deg"]: continue
        hloc = scene.terrain.water_z if scene.terrain is not None else 0.0
        for _ in range(3 if scene.terrain is not None else 0):
            c_, _, _ = fresnel_zone(s["enu"] - [0, 0, hloc], rx - [0, 0, hloc], scene.wl, 8); hloc = float(scene.terrain.height(c_[0], c_[1]))
        off = np.array([0, 0, hloc]); camh = CameraPose(lens, cam.R, cam.C - off)
        center, ring, _ = fresnel_zone(s["enu"] - off, rx - off, scene.wl, 120)
        uvr, okr = clip_poly_front(camh, ring)
        azr = math.radians(s["az"]); al = np.array([math.sin(azr), math.cos(azr)]); ac = np.array([math.cos(azr), -math.sin(azr)])
        pa = (ring[:, :2] - center[:2]) @ al; pc = (ring[:, :2] - center[:2]) @ ac
        L, Wd = pa.max() - pa.min(), pc.max() - pc.min()
        inview = okr.all() and poly_ok(uvr) and cv2.intersectConvexConvex(cv2.convexHull(uvr.astype(np.float32)),
                     np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float32))[0] > 1
        wf = None
        if inview and wmap is not None:
            # sample the zone interior on the ground (exact, independent of perspective)
            rr = np.sqrt(np.random.default_rng(0).random(400)); th = np.random.default_rng(1).random(400) * 2 * np.pi
            pts = center[:2] + (rr * np.cos(th))[:, None] * (al * L / 2) + (rr * np.sin(th))[:, None] * (ac * Wd / 2)
            wf = float((wmap.sample(pts[:, 0], pts[:, 1]) > 0.5).mean())
        elif inview:
            mk = np.zeros((h, w), np.uint8); cv2.fillPoly(mk, [np.int32(uvr)], 1); vis = mk & hz
            if vis.sum() > 20: wf = float((water & vis).sum() / vis.sum())
        uvc, okc = clip_poly_front(camh, center[None])
        rows.append(dict(video_s=round(vt, 4), mission_s=round(mt, 4), utc=dt.isoformat(), sat=s["sid"], el_deg=round(s["el"], 3), az_deg=round(s["az"], 3),
                         spec_e_m=round(center[0], 3), spec_n_m=round(center[1], 3), surface_z_m=round(hloc, 2),
                         range_m=round(float(np.linalg.norm(center[:2] - rx[:2])), 2), zone_along_m=round(L, 2), zone_across_m=round(Wd, 2),
                         in_view=bool(inview), px=round(float(uvc[0, 0]), 1) if okc[0] else "", py=round(float(uvc[0, 1]), 1) if okc[0] else "",
                         water_fraction_image=round(wf, 3) if wf is not None else ""))
        if inview: items.append(dict(s=s, center=center, off=off, camh=camh, uvr=uvr, uvc=uvc[0] if okc[0] else None, wf=wf, L=L, Wd=Wd,
                                     dist=float(np.linalg.norm(center - rx + off))))
    items.sort(key=lambda it: -it["dist"])
    for it in items:
        holo_zone(img, glow, it["camh"], it["s"]["enu"] - it["off"], it["s"]["az"], it["center"], it["uvr"], scene.wl,
                  CCOL[it["s"]["const"]], it["Wd"], it["L"], t, it["wf"] is not None and it["wf"] > 0.5, S, minimal=cfg.get("minimal", False),
                  wmap=wmap, zoff=it["off"])

    # ---- incident beams (satellite -> specular point) and 3-D pins
    beams = np.zeros((h, w, 3), np.float32); pins = []
    for it in (items if True else []):
        c = np.array(CCOL[it["s"]["const"]], np.float32); P0 = it["center"] + it["off"]
        seg = P0 + np.linspace(0, 0 if cfg.get('minimal') else 32, 40)[:, None] * it["s"]["dir"][None]
        su, so = clip_poly_front(cam, seg)
        for i in range(len(seg) - 1):
            if so[i] and so[i + 1] and poly_ok(su[i:i + 2]):
                k = (1 - i / 39) ** 1.4
                cv2.line(beams, tuple(np.int32(su[i])), tuple(np.int32(su[i + 1])), tuple(float(v) * k for v in c), max(1, int(2.2 * S * (0.4 + k))), cv2.LINE_AA)
        top = P0 + np.array([0, 0, 7.0])                                   # world-vertical pin, 7 m tall
        pu, po = clip_poly_front(cam, np.vstack([P0, top]))
        if po.all() and poly_ok(pu): pins.append((it, pu[0], pu[1]))
    img = np.clip(img + beams * 0.75, 0, 255); glow += beams * 0.8
    for it, p0, p1 in pins:
        c = CCOL[it["s"]["const"]]; n = 18
        for i in range(n):
            a0 = p0 + (p1 - p0) * i / n; a1 = p0 + (p1 - p0) * (i + 1) / n
            cv2.line(img, tuple(np.int32(a0)), tuple(np.int32(a1)), tuple(float(v) * (0.5 + 0.5 * i / n) + 255 * 0.25 for v in c), max(1, int(1.4 * S)), cv2.LINE_AA)

    # ---- sun glint check marker (pure direction)
    sun_d, sun_az, sun_el = sun_enu_dir(dt, lat, lon); gd = sun_d * np.array([1, 1, -1.0])
    gu, gok = cam.project_dir(gd[None]); gpx = gu[0] if gok[0] and (gd @ cam.R)[2] > 0 and 0 <= gu[0, 0] < w and 0 <= gu[0, 1] < h else None

    # ---- bloom
    gl = cv2.resize(np.clip(glow, 0, 255), (w // 2, h // 2), interpolation=cv2.INTER_AREA)
    bloom = cv2.resize(cv2.GaussianBlur(gl, (0, 0), 2.5 * S) * 0.55 + cv2.GaussianBlur(gl, (0, 0), 8 * S) * 0.45, (w, h), interpolation=cv2.INTER_LINEAR)
    if not cfg.get("minimal", False):
        img = 255 - (255 - img) * (255 - np.clip(bloom * 0.9, 0, 255)) / 255                      # screen blend
        img = np.clip(img * vignette(w, h), 0, 255)
    else:
        img = 255 - (255 - img) * (255 - np.clip(bloom * 0.25, 0, 255)) / 255                     # just a hint of glow
    out = img.astype(np.uint8)

    # ================= HUD
    fw = cam.R[:, 2]; hdg = math.degrees(math.atan2(fw[0], fw[1])) % 360; dep = math.degrees(math.asin(-fw[2]))
    nw = sum(1 for it in items if it["wf"] is not None and it["wf"] > 0.5)
    spd = float(np.linalg.norm(scene.tel.velocity(mt)[:2]))
    # chase panel (rendered first so glass sits over the video)
    if cfg.get("minimal", False): return minimal_hud(out, scene, cfg, items, pins, sats, cam, dt, mt, rx, gpx, w, h, S, nw, hdg, spd)
    PW, PH = int(cfg["chase_w"] * S), int(cfg["chase_h"] * S)
    if scene.chase is None or scene.chase.W != PW: scene.chase = ChaseCam(PW, PH, cfg["chase_hfov_deg"])
    panel, plabels, dpos = render_chase(scene.chase, frame_bgr, cam, scene, sats, {it["s"]["sid"] for it in items}, CCOL, cfg)
    cpx, cpy = w - PW - int(24 * S), int(96 * S)
    boxes = dict(title=(24 * S, 24 * S, 560 * S, 182 * S), chase=(cpx - 8 * S, cpy - 40 * S, cpx + PW + 8 * S, cpy + PH + 8 * S),
                 map=(24 * S, h - 300 * S, 300 * S, h - 24 * S), sky=(w - 300 * S, h - 300 * S, w - 24 * S, h - 24 * S),
                 tape=(w / 2 - 380 * S, 18 * S, w / 2 + 380 * S, 70 * S))
    for k, b in boxes.items(): glass(out, b, int(14 * S))
    out[cpy:cpy + PH, cpx:cpx + PW] = cv2.cvtColor(panel, cv2.COLOR_BGR2RGB)
    pil = Image.fromarray(out).convert("RGBA"); hud = Image.new("RGBA", (w, h), (0, 0, 0, 0)); d = ImageDraw.Draw(hud, "RGBA")
    fT, fH, fL = F("SpaceGrotesk-700.ttf", int(25 * S)), F("SpaceGrotesk-500.ttf", int(15 * S)), F("SpaceGrotesk-700.ttf", int(19 * S))
    fM, fMs, fMx = F("JetBrainsMono-Medium.ttf", int(14 * S)), F("JetBrainsMono-Regular.ttf", int(12 * S)), F("JetBrainsMono-Regular.ttf", int(10.5 * S))
    for b in boxes.values(): frame_lines(d, b, S)
    acc = tuple(int(v) for v in ACC)

    # title block
    x0, y0 = 44 * S, 38 * S
    d.ellipse([x0, y0 + 8 * S, x0 + 9 * S, y0 + 17 * S], fill=(*acc, 255))
    txt(d, (x0 + 18 * S, y0), "HERON", fT); txt(d, (x0 + 18 * S + d.textlength("HERON", font=fT) + 10 * S, y0 + 7 * S), "GNSS-R  ·  SPECULAR AR", fH, acc)
    txt(d, (x0, y0 + 44 * S), f"UTC {dt.strftime('%H:%M:%S.%f')[:-4]}   T+{mt:06.1f}s", fM, DIM)
    stats = [("ALT", f"{rx[2]:4.1f} m"), ("GS", f"{spd:3.1f} m/s"), ("HDG", f"{hdg:05.1f}°"), ("TILT", f"{dep:4.1f}°")]
    xx = x0
    for k, v in stats:
        txt(d, (xx, y0 + 74 * S), k, fMx, FAINT); txt(d, (xx, y0 + 88 * S), v, fM, INK); xx += 120 * S
    txt(d, (x0, y0 + 114 * S), f"{len(items)} ZONES IN VIEW", fMs, INK)
    cx_ = x0 + d.textlength(f"{len(items)} ZONES IN VIEW", font=fMs) + 12 * S
    d.rounded_rectangle((cx_, y0 + 112 * S, cx_ + 92 * S, y0 + 130 * S), int(5 * S), fill=(*WATER, 230))
    txt(d, (cx_ + 46 * S, y0 + 114 * S), f"{nw} ON WATER", fMx, (6, 20, 30), anchor="ma")

    # compass tape
    tx0, ty0, tx1, ty1 = boxes["tape"]; span = 110.0; cxm = (tx0 + tx1) / 2
    for deg in range(0, 360, 5):
        dd = ((deg - hdg + 180) % 360) - 180
        if abs(dd) > span / 2: continue
        x = cxm + dd / span * (tx1 - tx0 - 40 * S); big = deg % 30 == 0; a = int(255 * (1 - abs(dd) / (span / 2)) ** 0.6)
        d.line([(x, ty0 + 8 * S), (x, ty0 + (20 if big else 13) * S)], fill=(*INK, a if big else a // 2), width=1)
        if big: txt(d, (x, ty0 + 24 * S), {0: "N", 90: "E", 180: "S", 270: "W"}.get(deg, f"{deg:03d}"), fMx, INK, anchor="ma", a=a)
    for s in sats:
        if s["el"] < cfg["elevation_mask_deg"]: continue
        dd = ((s["az"] - hdg + 180) % 360) - 180
        if abs(dd) > span / 2: continue
        x = cxm + dd / span * (tx1 - tx0 - 40 * S); c = CCOL[s["const"]]
        d.polygon([(x - 4 * S, ty0 + 2 * S), (x + 4 * S, ty0 + 2 * S), (x, ty0 + 8 * S)], fill=(*c, 255))
    d.polygon([(cxm - 6 * S, ty1 - 2 * S), (cxm + 6 * S, ty1 - 2 * S), (cxm, ty1 - 10 * S)], fill=(*acc, 255))

    # zone callouts on 3-D pins (avoid panels and each other)
    placed = [tuple(b) for b in boxes.values()]
    for it, p0, p1 in sorted(pins, key=lambda q: q[0]["dist"]):
        s = it["s"]; c = CCOL[s["const"]]; x, y = p1
        tag = "WATER" if (it["wf"] is not None and it["wf"] > 0.5) else "LAND"
        l1 = s["sid"]; l2 = f"EL {s['el']:4.1f}°   {it['L']:.0f}×{it['Wd']:.0f} m"
        bw = max(d.textlength(l1, font=fL) + d.textlength(tag, font=fMx) + 34 * S, d.textlength(l2, font=fMx) + 20 * S); bh = 46 * S
        best = None
        for dx, dy in [(10, -bh / S - 6), (-10 - bw / S, -bh / S - 6), (14, -bh / S - 40), (-14 - bw / S, -bh / S - 40), (16, 10), (-16 - bw / S, 10)]:
            b = (x + dx * S, y + dy * S, x + dx * S + bw, y + dy * S + bh)
            if b[0] < 8 or b[1] < 8 or b[2] > w - 8 or b[3] > h - 8: continue
            if any(b[0] < q[2] and b[2] > q[0] and b[1] < q[3] and b[3] > q[1] for q in placed): continue
            best = b; break
        if best is None: continue
        placed.append(best); bx, by, ex, ey = best
        glass(out, best, int(8 * S), alpha=0.55, blur=8)
        d.line([(x, y), (bx if bx > x else ex, by + bh / 2)], fill=(*c, 210), width=max(1, int(1.2 * S)))
        d.ellipse([x - 3 * S, y - 3 * S, x + 3 * S, y + 3 * S], outline=(*c, 255), width=max(1, int(1.5 * S)))
        d.rounded_rectangle(best, int(8 * S), outline=(*c, 150), width=1)
        d.rectangle([bx, by + 9 * S, bx + 2.5 * S, ey - 9 * S], fill=(*c, 255))
        txt(d, (bx + 12 * S, by + 5 * S), l1, fL, c)
        tx = bx + 12 * S + d.textlength(l1, font=fL) + 9 * S; tc = WATER if tag == "WATER" else (214, 186, 140)
        d.rounded_rectangle((tx, by + 9 * S, tx + d.textlength(tag, font=fMx) + 10 * S, by + 25 * S), int(4 * S), fill=(*tc, 235))
        txt(d, (tx + 5 * S, by + 10.5 * S), tag, fMx, (8, 18, 28))
        txt(d, (bx + 12 * S, by + 29 * S), l2, fMx, DIM)

    # satellites visible in the sky part of the frame
    for s in sats:
        if s["el"] < 0: continue
        Pc = s["dir"] @ cam.R
        if Pc[2] <= 0: continue
        u, ok = cam.lens.project_cam(Pc[None])
        x, y = u[0]
        if not (ok[0] and 0 <= x < w and 0 <= y < h): continue
        if any(q[0] < x < q[2] and q[1] < y < q[3] for q in boxes.values()): continue
        c = CCOL[s["const"]]; r = 8 * S
        for k_, rr in enumerate([14, 22, 30]):
            d.ellipse([x - rr * S, y - rr * S, x + rr * S, y + rr * S], outline=(*c, 140 - 40 * k_), width=1)
        d.polygon([(x, y - r), (x + r, y), (x, y + r), (x - r, y)], fill=(*c, 220))
        lx = x + 34 * S if x + 200 * S < w else x - 34 * S - 150 * S
        txt(d, (lx, y - 16 * S), s["sid"], fL, c); txt(d, (lx, y + 6 * S), f"EL {s['el']:.0f}°  AZ {s['az']:.0f}°", fMx, INK)

    if gpx is not None:
        x, y = gpx; r = 12 * S
        d.ellipse([x - r, y - r, x + r, y + r], outline=(255, 232, 150, 230), width=max(1, int(1.4 * S)))
        d.line([(x - 2 * r, y), (x - r * 1.3, y)], fill=(255, 232, 150, 230), width=1); d.line([(x + r * 1.3, y), (x + 2 * r, y)], fill=(255, 232, 150, 230), width=1)
        txt(d, (x + 2.3 * r, y - 7 * S), "SUN GLINT · PREDICTED", fMx, (255, 232, 150))

    # chase header + labels
    txt(d, (cpx + 2 * S, cpy - 30 * S), "CHASE CAM", fMs, INK); txt(d, (cpx + 92 * S, cpy - 30 * S), "true ground ellipses · live texture", fMx, DIM)
    for s, uc, strong in plabels:
        if uc is None or not strong: continue
        x, y = cpx + uc[0], cpy + uc[1]
        if cpx + 4 < x < cpx + PW - 40 and cpy + 6 < y < cpy + PH - 16: txt(d, (x + 6 * S, y - 14 * S), s["sid"], fMx, CCOL[s["const"]])
    if dpos is not None and 0 < dpos[0] < PW and 0 < dpos[1] < PH: txt(d, (cpx + dpos[0] + 12 * S, cpy + dpos[1] - 7 * S), "HERON", fMx, INK)

    # mini-map
    mx0, my0, mx1, my1 = boxes["map"]; mm = mx1 - mx0; sc = mm / 340.0
    def m2p(p): return (mx0 + mm / 2 + (p[0] - rx[0]) * sc, my0 + mm / 2 - (p[1] - rx[1]) * sc)
    mp = Image.new("RGBA", (int(mm), int(my1 - my0)), (0, 0, 0, 0)); md = ImageDraw.Draw(mp, "RGBA")
    of = lambda p: (m2p(p)[0] - mx0, m2p(p)[1] - my0)
    md.polygon([of(p) for p in scene.pool], fill=(*WATER, 45), outline=(*WATER, 160))
    for R0 in (50, 100, 200): md.ellipse([mm / 2 - R0 * sc, (my1 - my0) / 2 - R0 * sc, mm / 2 + R0 * sc, (my1 - my0) / 2 + R0 * sc], outline=(*acc, 50))
    md.line([of(p) for p in scene.track], fill=(255, 255, 255, 70), width=1)
    for s in sats:
        if s["el"] < cfg["elevation_mask_deg"]: continue
        c0, rg, _ = fresnel_zone(s["enu"], rx, scene.wl, 36)
        md.polygon([of(p) for p in rg], fill=(*CCOL[s["const"]], 90), outline=(*CCOL[s["const"]], 230))
    md.polygon([(mm / 2 + 7 * S * math.sin(math.radians(hdg + a_)), (my1 - my0) / 2 - 7 * S * math.cos(math.radians(hdg + a_))) for a_ in (0, 140, 220)], fill=(255, 255, 255, 255))
    rm = Image.new("L", mp.size, 0); ImageDraw.Draw(rm).rounded_rectangle((0, 0, mp.size[0] - 1, mp.size[1] - 1), int(14 * S), fill=255)
    hud.paste(mp, (int(mx0), int(my0)), Image.composite(mp, Image.new("RGBA", mp.size, (0, 0, 0, 0)), rm))
    txt(d, (mx0 + 14 * S, my0 + 10 * S), "MAP", fMs, INK); txt(d, (mx0 + 52 * S, my0 + 11 * S), "true zone shapes · N up", fMx, DIM)
    txt(d, (mx0 + 14 * S, my1 - 24 * S), "100 m", fMx, DIM); d.line([(mx0 + 60 * S, my1 - 17 * S), (mx0 + 60 * S + 100 * sc, my1 - 17 * S)], fill=(*INK, 200), width=1)

    # sky plot
    sx0, sy0, sx1, sy1 = boxes["sky"]; cxp, cyp = (sx0 + sx1) / 2, (sy0 + sy1) / 2 + 10 * S; rad = (sx1 - sx0) * 0.38
    for e in (0, 20, 45, 70):
        rr = rad * (90 - e) / 90; d.ellipse([cxp - rr, cyp - rr, cxp + rr, cyp + rr], outline=(*acc, 120 if e == 20 else 45), width=1)
    for a_ in range(0, 360, 30):
        d.line([(cxp, cyp), (cxp + rad * math.sin(math.radians(a_)), cyp - rad * math.cos(math.radians(a_)))], fill=(255, 255, 255, 22), width=1)
    # camera heading wedge
    d.pieslice([cxp - rad, cyp - rad, cxp + rad, cyp + rad], hdg - 90 - 55, hdg - 90 + 55, fill=(*acc, 22))
    inv = {it["s"]["sid"] for it in items}
    for s in sats:
        if s["el"] < 0: continue
        rr = rad * (90 - s["el"]) / 90; x = cxp + rr * math.sin(math.radians(s["az"])); y = cyp - rr * math.cos(math.radians(s["az"]))
        c = CCOL[s["const"]] if s["el"] >= cfg["elevation_mask_deg"] else (90, 100, 110); big = s["sid"] in inv
        q = (5.5 if big else 3.2) * S
        if big: d.ellipse([x - q - 4 * S, y - q - 4 * S, x + q + 4 * S, y + q + 4 * S], outline=(*c, 140))
        d.ellipse([x - q, y - q, x + q, y + q], fill=(*c, 255 if big else 170))
        if big: txt(d, (x + 8 * S, y - 7 * S), s["sid"], fMx, c)
    sxs, sys_ = cxp + rad * (90 - sun_el) / 90 * math.sin(math.radians(sun_az)), cyp - rad * (90 - sun_el) / 90 * math.cos(math.radians(sun_az))
    d.ellipse([sxs - 6 * S, sys_ - 6 * S, sxs + 6 * S, sys_ + 6 * S], outline=(255, 232, 150, 230), width=2)
    txt(d, (sx0 + 14 * S, sy0 + 10 * S), "SKY", fMs, INK); txt(d, (sx0 + 52 * S, sy0 + 11 * S), f"mask {cfg['elevation_mask_deg']:.0f}° · ring = in view", fMx, DIM)
    # legend + honesty line
    lx = w / 2 - 250 * S; ly = h - 38 * S
    for k in cfg["constellations"]:
        d.ellipse([lx, ly + 3 * S, lx + 9 * S, ly + 12 * S], fill=(*CCOL[k], 255)); txt(d, (lx + 15 * S, ly), CNAME[k], fMx, INK); lx += d.textlength(CNAME[k], font=fMx) + 36 * S
    d.rectangle([lx, ly + 3 * S, lx + 12 * S, ly + 12 * S], fill=(*WATER, 120), outline=(*WATER, 255)); txt(d, (lx + 18 * S, ly), "water", fMx, INK)
    txt(d, (w / 2, h - 18 * S), "first Fresnel zones · predicted geometry from flight log, orbits and image-solved camera · not received-signal measurements", fMx, FAINT, anchor="ma")

    pil = Image.alpha_composite(Image.fromarray(out).convert("RGBA"), hud).convert("RGB")
    return cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2BGR), dict(items=len(items), on_water=nw)

_TURNS = None
def flight_phase(mt):
    """PASS n / TURN n from the logged survey turn windows (elapsed = mission + 0.545 s)."""
    global _TURNS
    if _TURNS is None:
        import pandas as pd
        try: T = pd.read_csv(DATA / "turn_intervals.csv"); _TURNS = T[["turn_zone_start_elapsed_s", "turn_zone_end_elapsed_s"]].values - 0.545
        except Exception: _TURNS = np.zeros((0, 2))
    if len(_TURNS) == 0 or mt < _TURNS[0, 0] - 60: return "OUTBOUND" if mt < 84 else "SURVEY"
    for i, (a, b) in enumerate(_TURNS):
        if a <= mt <= b: return f"TURN {i + 1}"
        if mt < a: return f"PASS {i + 1}"
    return "PASS 10" if mt < _TURNS[-1, 1] + 66 else "RETURN"

def minimal_hud(out, scene, cfg, items, pins, sats, cam, dt, mt, rx, gpx, w, h, S, nw, hdg, spd):
    # legibility: soft dark gradient behind the header, nothing else framed
    gh = int(118 * S); g = np.linspace(0.42, 0, gh, dtype=np.float32)[:, None, None]
    out[:gh] = (out[:gh].astype(np.float32) * (1 - g)).astype(np.uint8)
    hud = Image.new("RGBA", (w, h), (0, 0, 0, 0)); d = ImageDraw.Draw(hud, "RGBA")
    fT, fL = F("SpaceGrotesk-700.ttf", int(26 * S)), F("SpaceGrotesk-700.ttf", int(21 * S))
    fM, fMx, fS = F("JetBrainsMono-Medium.ttf", int(15 * S)), F("JetBrainsMono-Medium.ttf", int(14 * S)), F("JetBrainsMono-Regular.ttf", int(13 * S))
    def sh(xy, s_, f, fill, anchor="la", a=255):
        d.text((xy[0] + 1, xy[1] + 1.5), s_, font=f, fill=(0, 0, 0, int(a * 0.55)), anchor=anchor); d.text(xy, s_, font=f, fill=(*fill, a), anchor=anchor)
    # ---- header
    x0, y0 = 32 * S, 26 * S
    sh((x0, y0), "HERON", fT, INK); tx = x0 + d.textlength("HERON", font=fT) + 14 * S
    sh((tx, y0 + 6 * S), "GNSS-R reflection forecast", F("SpaceGrotesk-500.ttf", int(19 * S)), DIM)
    ph = flight_phase(mt); pw_ = d.textlength(ph, font=fMx) + 18 * S
    px = tx + d.textlength("GNSS-R reflection forecast", font=F("SpaceGrotesk-500.ttf", int(19 * S))) + 16 * S
    turn = ph.startswith("TURN")
    d.rounded_rectangle((px, y0 + 5 * S, px + pw_, y0 + 27 * S), int(11 * S), fill=(255, 196, 61, 235) if turn else (255, 255, 255, 40),
                        outline=None if turn else (255, 255, 255, 120), width=1)
    d.text((px + 9 * S, y0 + 9 * S), ph, font=fMx, fill=(20, 16, 6, 255) if turn else (*INK, 255))
    info = f"{dt.strftime('%H:%M:%S')} UTC    {rx[2]:4.1f} m AGL    {spd:3.1f} m/s    {len(items)} zones · {nw} on water"
    sh((x0, y0 + 40 * S), info, fM, DIM)
    # legend (top right)
    lx = w - 32 * S; ly = y0 + 6 * S
    for k in reversed(cfg["constellations"]):
        tw_ = d.textlength(CNAME[k], font=fMx); lx -= tw_
        d.ellipse([lx - 15 * S, ly + 3 * S, lx - 5 * S, ly + 13 * S], fill=(*CCOL[k], 255)); sh((lx, ly), CNAME[k], fMx, INK); lx -= 36 * S
    lx = w - 32 * S; tw_ = d.textlength("on water", font=fMx); lx -= tw_
    d.rounded_rectangle([lx - 26 * S, ly + 34 * S, lx - 6 * S, ly + 48 * S], int(4 * S), fill=(120, 235, 255, 235)); sh((lx, ly + 33 * S), "on water", fMx, INK)
    # ---- zone labels with placement hysteresis (labels don't hop between sides)
    placed = [(0, 0, w * 0.62, 104 * S), (w - 560 * S, 0, w, 90 * S)]
    mem = scene.__dict__.setdefault("label_side", {})
    for it, p0, p1 in sorted(pins, key=lambda q: q[0]["dist"]):
        s_ = it["s"]; c = CCOL[s_["const"]]; x, y = p0
        wf_ = it["wf"] or 0.0; onw = wf_ > 0.5
        tag = f"ON WATER {wf_ * 100:.0f}%" if onw else ("LAND" if wf_ <= 0.05 else f"LAND · {wf_ * 100:.0f}% wet")
        l1 = s_["sid"]; l2 = f"el {s_['el']:.0f}°   {it['L']:.0f} × {it['Wd']:.0f} m"
        tw = d.textlength(tag, font=fS) + 14 * S
        bw = max(d.textlength(l1, font=fL) + tw + 12 * S, d.textlength(l2, font=fS)) + 22 * S; bh = 54 * S
        rpx = float(np.ptp(it["uvr"][:, 0])) / 2 + 12 * S; rpy = float(np.ptp(it["uvr"][:, 1])) / 2 + 10 * S
        cands = [(x + rpx, y - bh / 2, x + rpx + bw, y + bh / 2), (x - rpx - bw, y - bh / 2, x - rpx, y + bh / 2),
                 (x - bw / 2, y - rpy - bh, x + bw / 2, y - rpy), (x - bw / 2, y + rpy, x + bw / 2, y + rpy + bh)]
        order = [mem.get(s_["sid"], 0)] + [i for i in range(4) if i != mem.get(s_["sid"], 0)]
        best = None
        for i in order:
            b_ = cands[i]
            if b_[0] < 10 or b_[1] < 10 or b_[2] > w - 10 or b_[3] > h - 10: continue
            if any(b_[0] < q[2] and b_[2] > q[0] and b_[1] < q[3] and b_[3] > q[1] for q in placed): continue
            best = b_; mem[s_["sid"]] = i; break
        if best is None: continue
        placed.append(best); bx, by, ex, ey = best
        d.rounded_rectangle(best, int(10 * S), fill=(8, 14, 22, 150))
        d.rounded_rectangle((bx, by + 10 * S, bx + 3 * S, ey - 10 * S), int(1.5 * S), fill=(*c, 255))
        sh((bx + 13 * S, by + 7 * S), l1, fL, c)
        tx_ = bx + 13 * S + d.textlength(l1, font=fL) + 10 * S
        if onw:
            d.rounded_rectangle((tx_, by + 10 * S, tx_ + tw, by + 29 * S), int(9 * S), fill=(120, 235, 255, 245))
            d.text((tx_ + 7 * S, by + 12 * S), tag, font=fS, fill=(4, 28, 40, 255))
        else:
            d.rounded_rectangle((tx_, by + 10 * S, tx_ + tw, by + 29 * S), int(9 * S), outline=(214, 200, 176, 200), width=1)
            d.text((tx_ + 7 * S, by + 12 * S), tag, font=fS, fill=(226, 214, 192, 255))
        d.text((bx + 13 * S, by + 33 * S), l2, font=fS, fill=(*DIM, 255))
    # ---- satellites in the sky
    for s_ in sats:
        if s_["el"] < 0: continue
        Pc = s_["dir"] @ cam.R
        if Pc[2] <= 0: continue
        u, ok = cam.lens.project_cam(Pc[None]); x, y = u[0]
        if not (ok[0] and 0 <= x < w and 110 * S <= y < h): continue
        c = CCOL[s_["const"]]; r = 7 * S
        d.ellipse([x - 16 * S, y - 16 * S, x + 16 * S, y + 16 * S], outline=(*c, 110), width=1)
        d.polygon([(x, y - r), (x + r, y), (x, y + r), (x - r, y)], fill=(*c, 230))
        sh((x + 22 * S, y - 9 * S), f"{s_['sid']}  {s_['el']:.0f}°", fMx, c)
    if gpx is not None:
        x, y = gpx; r = 9 * S
        d.ellipse([x - r, y - r, x + r, y + r], outline=(255, 232, 150, 210), width=max(1, int(1.4 * S)))
        sh((x + 14 * S, y - 8 * S), "sun glint · predicted", fS, (255, 232, 150), a=230)
    # footer
    pil = Image.alpha_composite(Image.fromarray(out).convert("RGBA"), hud).convert("RGB")
    return cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2BGR), dict(items=len(items), on_water=nw)

def run(config, preview="", out=None, frame_start=0, frame_end=None):
    """Render a clip (or preview stills at the given clip times, seconds)."""
    from types import SimpleNamespace
    from .geometry import load_config
    a = SimpleNamespace(preview=preview, out=out, frame_start=frame_start, frame_end=frame_end)
    cfg = load_config(config); scene = Scene(cfg)
    cap = cv2.VideoCapture(str(resolve(cfg["clip_path"]))); fps = clip_fps(cfg); n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    W, H = cfg["output_width"], cfg["output_height"]
    lens = Fisheye(DATA / cfg["lens_profile"], W, H, cfg.get("focal_scale", 1.0))
    if getattr(scene, "sfm_cam", None) is not None:
        p = scene.sfm_cam; k = W / 1920.0; lens.fx, lens.fy, lens.cx, lens.cy = p[0] * k, p[1] * k, p[2] * k, p[3] * k; lens.D = np.array(p[4:8])
    # world-anchored water map (built once per clip, cached)
    from .watermap import WaterMap, build as build_wm
    CACHE.mkdir(exist_ok=True); wmp = CACHE / f"watermap_{Path(cfg['clip_path']).stem}{cfg.get('watermap_tag', '')}.npz"
    if wmp.exists(): scene.watermap = WaterMap.load(wmp)
    else:
        nwm = min(n, int(cfg.get("watermap_frames", n)))
        scene.watermap = build_wm(scene, lens, cap, fps, cfg["clip_video_start_s"], nwm, ground_field, step_s=cfg.get("watermap_step_s", 0.75)); scene.watermap.save(wmp)
    rows = []
    if a.preview:
        for ts in [float(x) for x in a.preview.split(",")]:
            fi = int(round(ts * fps)); cap.set(cv2.CAP_PROP_POS_FRAMES, fi); ok, fr = cap.read(); fr = cv2.resize(fr, (W, H), interpolation=cv2.INTER_CUBIC) if fr.shape[1] != W else fr; scene.water_ema = None; scene.chase = None
            t0 = time.time(); img, info = compose_v2(fr, scene, cfg["clip_video_start_s"] + fi / fps, lens, fi, fps, rows)
            OUT.mkdir(exist_ok=True); p = OUT / f"preview_{Path(cfg['clip_path']).stem}_{ts:06.1f}.jpg"; cv2.imwrite(str(p), img, [cv2.IMWRITE_JPEG_QUALITY, 93]); print(p, info, f"{time.time() - t0:.2f}s")
        return
    end = n if a.frame_end is None else min(n, a.frame_end)
    dest = Path(a.out or OUT / cfg.get("output_name", "HERON_AR.mp4")); dest.parent.mkdir(exist_ok=True)
    proc = subprocess.Popen([shutil.which("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
                             "-r", f"{fps:.6f}", "-i", "-", "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
                             "-movflags", "+faststart", str(dest)], stdin=subprocess.PIPE)
    cap.set(cv2.CAP_PROP_POS_FRAMES, a.frame_start); t0 = time.time()
    for i in range(a.frame_start, end):
        ok, fr = cap.read()
        if not ok: break
        if fr.shape[1] != W: fr = cv2.resize(fr, (W, H), interpolation=cv2.INTER_CUBIC)
        img, info = compose_v2(fr, scene, cfg["clip_video_start_s"] + i / fps, lens, i, fps, rows); proc.stdin.write(img.tobytes())
        if i % 240 == 0: print(f"{i}/{end} {time.time() - t0:.0f}s {info}", flush=True)
    proc.stdin.close(); assert proc.wait() == 0
    with dest.with_suffix(".csv").open("w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=rows[0].keys()); wr.writeheader(); wr.writerows(rows)
    print("done", dest)


