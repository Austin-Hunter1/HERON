"""Per-pixel 'ground patch' rendering of Fresnel zones.

Every pixel near a zone is cast onto the water plane; the exact reflected-path
excess  Δ(q) = |sat-q| + |q-rx| - (specular path)  is evaluated there. The first
Fresnel zone is Δ < λ/2. Everything drawn (fill falloff, rim, inner zone rings,
the metre grid aligned with the satellite azimuth, the long axis) is a function
of ground coordinates, so it foreshortens, converges and thins with distance like
paint on the ground instead of a screen-space sticker. The underlying image is
tinted (not covered), so lakebed and water texture stay visible through it.
"""
import math
import numpy as np, cv2

def smooth_line(dist, width_px):
    """Antialiased line intensity from a signed distance in pixels."""
    return np.clip(1.0 - np.abs(dist) / max(width_px, 1e-3), 0, 1)

def nice_cell(x):
    for v in [0.5, 1, 2, 2.5, 5, 10, 20, 25, 50]:
        if v >= x: return v
    return 50.0

def shade_zone(img, cam, sat_enu, sat_dir, sat_az_deg, center, ring_uv, wl, color_rgb, opts):
    """img: float32 RGB (H,W,3) 0..255, modified in place."""
    h, w = img.shape[:2]
    x0, y0 = np.floor(ring_uv.min(0)); x1, y1 = np.ceil(ring_uv.max(0))
    cx, cy = ring_uv.mean(0); pad = 0.45                       # draw slightly outside zone 1 (zone 2 ring)
    x0 = int(max(0, cx - (cx - x0) * (1 + pad))); x1 = int(min(w - 1, cx + (x1 - cx) * (1 + pad)))
    y0 = int(max(0, cy - (cy - y0) * (1 + pad))); y1 = int(min(h - 1, cy + (y1 - cy) * (1 + pad)))
    if x1 <= x0 + 2 or y1 <= y0 + 2: return
    ys, xs = np.mgrid[y0:y1 + 1, x0:x1 + 1]
    uv = np.c_[xs.ravel(), ys.ravel()].astype(np.float64)
    P, ok = cam.ray_ground(uv)
    rx = cam.C
    base = np.linalg.norm(sat_enu - center) + np.linalg.norm(rx - center)
    delta = np.linalg.norm(sat_enu - P, axis=1) + np.linalg.norm(rx - P, axis=1) - base
    zn = (delta / (wl / 2)).reshape(ys.shape)                 # 1.0 at the first-zone boundary
    zn[~ok.reshape(ys.shape)] = 99
    az = math.radians(sat_az_deg)
    along = np.array([math.sin(az), math.cos(az)]); across = np.array([math.cos(az), -math.sin(az)])
    d2 = P[:, :2] - center[:2]
    a = (d2 @ along).reshape(ys.shape); c = (d2 @ across).reshape(ys.shape)
    # pixel-space gradients for antialiasing (like a shader's fwidth)
    def fwidth(f):
        gy, gx = np.gradient(f); return np.hypot(gx, gy) + 1e-9
    fz = fwidth(zn); fa = fwidth(a); fc = fwidth(c)
    col = np.array(color_rgb, np.float32)
    inside = zn < 1.0
    # --- fill: tint, stronger toward the rim, keeps ground texture
    r = np.sqrt(np.clip(zn, 0, 1))
    alpha = np.where(inside, opts["fill_min"] + (opts["fill_max"] - opts["fill_min"]) * r ** 2, 0.0)
    # --- square metre grid aligned to the satellite azimuth (world units -> foreshortens naturally).
    # Cell size is chosen per zone so the short axis spans ~4 cells; counting cells along the
    # long axis then shows the true ground elongation even though the outline looks round.
    g = opts.get("grid_m") or nice_cell(opts["minor_m"] / 4)
    da = (a / g - np.round(a / g)) * g; dc = (c / g - np.round(c / g)) * g
    grid = np.maximum(smooth_line(da / fa, opts["grid_px"]), smooth_line(dc / fc, opts["grid_px"] * 1.3))
    inner = np.clip(1.15 - zn, 0, 1) ** 0.4 * inside
    tile = np.clip(1 - (zn - 1) / 1.2, 0, 1) * (zn >= 1) * 0.22        # faint grid continuing onto the ground around it
    grid *= np.maximum(inner, tile)
    # --- long axis (toward/away from the satellite), dashed in metres, runs past the rim
    L = opts["axis_ext"]
    axis = smooth_line(c / fc, 1.4) * (np.abs(a) < L) * ((np.floor(a / opts["dash_m"]) % 2) == 0)
    axis *= (zn < 2.2)
    # --- rims: zone 1 bold, zone 2 & 3 faint (true Fresnel-zone structure)
    rim1 = smooth_line((zn - 1.0) / fz, opts["rim_px"])
    rim2 = smooth_line((zn - 2.0) / fz, 1.0) * 0.35
    soft = np.clip(1 - np.abs(zn - 1.0) / 0.35, 0, 1) * 0.25 * (zn > 1)          # outer glow on the ground
    # ripples travelling outward across the ground (water zones only): rings of constant path excess
    rip = np.zeros_like(zn)
    if opts.get("ripple") is not None:
        for k in range(3):
            ph = ((opts["ripple"] + k / 3.0) % 1.0) * 2.4
            rip = np.maximum(rip, smooth_line((zn - ph) / fz, 1.6) * (1 - ph / 2.4) * 0.55)
    sub = img[y0:y1 + 1, x0:x1 + 1]
    if opts.get("style") == "paint":
        # ---- ground paint: everything sized in metres on the ground and lit by the ground itself
        Pg = P.reshape(ys.shape + (3,))
        v1 = Pg - sat_enu; v2 = Pg - rx
        grad = (v1 / np.linalg.norm(v1, axis=-1, keepdims=True) + v2 / np.linalg.norm(v2, axis=-1, keepdims=True))[..., :2]
        gz = np.linalg.norm(grad, axis=-1) / (wl / 2)                 # d(zn)/d(metre) on the ground
        dist_m = (zn - 1.0) / np.maximum(gz, 1e-9)                    # signed metric distance to the zone edge
        band = opts["rim_frac"] * opts["minor_m"]                     # stroke width in METRES, not pixels
        fdm = fwidth(dist_m)
        rim = np.clip(0.5 - (np.abs(dist_m + band / 2) - band / 2) / np.maximum(fdm, 1e-9), 0, 1)
        lum = sub @ np.array([0.299, 0.587, 0.114], np.float32)
        loc = cv2.GaussianBlur(lum, (0, 0), 9) + 1
        shade = np.clip(lum / loc, 0.45, 1.6)[..., None]               # local light/texture of the ground
        paint = np.clip(col[None, None] * shade * opts["paint_gain"], 0, 255)
        fill = np.where(inside, opts["fill_alpha"], 0.0) * (0.75 + 0.25 * np.clip(-dist_m / band, 0, 1))
        gridp = grid * opts["grid_alpha"]
        k = np.clip(np.maximum(fill, np.maximum(rim * opts["rim_alpha"], gridp)) + rip * 0.5, 0, 1)[..., None]
        sub[:] = sub * (1 - k) + (sub * 0.35 + paint * 0.65) * k
        return
    sub = img[y0:y1 + 1, x0:x1 + 1]
    tinted = sub * (1 - opts["tint"]) + (sub * 0.55 + col * 0.45) * opts["tint"]
    sub[:] = sub * (1 - alpha[..., None]) + tinted * alpha[..., None]
    light = np.array([255, 255, 255], np.float32)
    for k, colr in [(rip, col), (grid * opts["grid_alpha"], light), (axis * 0.75, light), (rim1, col)]:
        k = k[..., None]; sub[:] = sub * (1 - k) + colr * k
