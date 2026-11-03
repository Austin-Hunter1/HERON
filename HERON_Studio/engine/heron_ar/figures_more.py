"""More explanatory figures: sky plot, lens model, error budget, ground coverage, flight timeline."""
from __future__ import annotations
import math
from pathlib import Path
import numpy as np, cv2
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.lines import Line2D

from .figures import _fig, _save, _img_ax, _shade_turns, SURF, INK, INK2, MUTED, GRID, BLUE, ORANGE, AQUA, YELLOW, CONST, FPS, SEQ
from .geometry import fresnel_zone, DATA


def _sat_series(cx, step=2.0):
    from .scene import sat_state
    sc = cx.scene; out = {}
    ts = np.arange(0, cx.cfg["clip_duration_s"], step)
    for t in ts:
        mt, cam, _, _ = cx.pose(t); _, ss = sat_state(sc, mt, cam.C)
        for s in ss:
            d = out.setdefault(s["sid"], {"c": s["const"], "t": [], "az": [], "el": []})
            d["t"].append(t); d["az"].append(s["az"]); d["el"].append(s["el"])
    return out

# ------------------------------------------------------------------ 18. sky plot
def fig18_skyplot(cx, out, t_mark=52.0):
    S = _sat_series(cx); mask = cx.cfg["elevation_mask_deg"]
    f = _fig(13, 7.6, "Which satellites, where in the sky",
             f"Sky plot over the {cx.cfg['clip_duration_s']:.0f} s survey clip (centre = straight up, edge = horizon). Satellites barely move in 3 minutes, "
             f"so each traces only a short arc; dots mark t = {t_mark:.0f} s. Only those above the {mask:.0f}° mask get a specular point and zone.")
    ax = f.add_axes([0.03, 0.04, 0.55, 0.8], projection="polar"); ax.set_theta_zero_location("N"); ax.set_theta_direction(-1)
    ax.set_facecolor(SURF); ax.set_ylim(0, 90); ax.set_yticks([0, 30, 60, 90 - mask]); ax.set_yticklabels(["90°", "60°", "30°", f"{mask:.0f}° mask"], color=INK2, fontsize=8.5)
    ax.set_xticks(np.radians([0, 90, 180, 270])); ax.set_xticklabels(["N", "E", "S", "W"], color=INK, fontsize=11)
    ax.fill_between(np.linspace(0, 2 * np.pi, 200), 90 - mask, 90, color="#ece9e2", zorder=0)
    ax.grid(color=GRID, lw=0.7)
    rows = []
    for sid, d in sorted(S.items()):
        if max(d["el"]) < 5: continue
        col = CONST.get(d["c"], (MUTED, ""))[0]; az = np.radians(d["az"]); r = 90 - np.array(d["el"])
        up = np.array(d["el"]) >= mask
        ax.plot(az, r, color=col, lw=2.2 if up.any() else 1, alpha=1 if up.any() else 0.35)
        i = int(np.argmin(np.abs(np.array(d["t"]) - t_mark)))
        ax.scatter(az[i], r[i], s=46, color=col, edgecolor=SURF, lw=1.5, zorder=4, alpha=1 if up[i] else 0.35)
        ax.annotate(sid, (az[i], r[i]), xytext=(6, 4), textcoords="offset points", fontsize=8.5, color=INK if up[i] else MUTED)
        rows.append((sid, d["c"], d["el"][i], d["az"][i], up[i]))
    ax.legend(handles=[Line2D([], [], color=CONST[k][0], lw=3, label=CONST[k][1]) for k in "GEC"], loc="lower left", bbox_to_anchor=(-0.05, -0.05), fontsize=9)
    # right: elevation vs zone size at the drone's height
    ax2 = f.add_axes([0.66, 0.12, 0.32, 0.66])
    h = float(cx.pose(t_mark)[1].C[2]); wl = cx.scene.wl
    for sid, c, el, az, up in rows:
        if not up: continue
        e = math.radians(el); sat = np.array([0, 2e7 * math.cos(e), 2e7 * math.sin(e)])
        cz, ring, _ = fresnel_zone(sat, np.array([0, 0, h]), wl, 90)
        L = np.ptp(ring[:, 1]); Wd = np.ptp(ring[:, 0]); rng = cz[1]
        ax2.scatter(el, L, s=40, color=CONST[c][0], edgecolor=SURF, lw=1, zorder=3)
        ax2.annotate(sid, (el, L), xytext=(5, 3), textcoords="offset points", fontsize=8.5, color=INK2)
    els = np.linspace(mask, 85, 40); Ls = []
    for el in els:
        e = math.radians(el); cz, ring, _ = fresnel_zone(np.array([0, 2e7 * math.cos(e), 2e7 * math.sin(e)]), np.array([0, 0, h]), wl, 60); Ls.append(np.ptp(ring[:, 1]))
    ax2.plot(els, Ls, color=MUTED, lw=1.4, ls=(0, (4, 2)), zorder=1)
    ax2.set_xlabel("elevation (°)"); ax2.set_ylabel("zone length on the ground (m)")
    ax2.set_title(f"Low satellites → long zones\n(receiver {h:.0f} m above water, t = {t_mark:.0f} s)", fontsize=11)
    return _save(f, out, "fig18_skyplot.png")

# ------------------------------------------------------------------ 19. lens
def fig19_lens(cx, out, t=52.0):
    import json
    L = cx.lens; prof = json.loads((DATA / cx.cfg["lens_profile"]).read_text()); s = cx.W / prof["calib_dimension"]["w"]
    Kp = np.array(prof["fisheye_params"]["camera_matrix"]); Dp = np.array(prof["fisheye_params"]["distortion_coeffs"])
    th = np.radians(np.linspace(0, 85, 200))
    def rpx(f, D): t2 = th ** 2; return f * th * (1 + D[0] * t2 + D[1] * t2 ** 2 + D[2] * t2 ** 3 + D[3] * t2 ** 4)
    fr = cx.frame(t)
    f = _fig(13, 8.6, "The lens: how a fisheye maps angles to pixels",
             "Each pixel's distance from the image centre encodes its angle off the optical axis. COLMAP refined the focal length and the four "
             "distortion terms from our own footage; undistorting with them straightens the horizon and the shoreline.")
    gs = f.add_gridspec(2, 2, left=0.05, right=0.985, top=0.85, bottom=0.07, wspace=0.12, hspace=0.25, width_ratios=[1, 1.35])
    ax = f.add_subplot(gs[0, 0])
    ax.plot(np.degrees(th), L.fx * np.tan(np.minimum(th, np.radians(80))), color=MUTED, lw=1.4, ls=(0, (4, 2)), label="ordinary (pinhole) lens")
    ax.plot(np.degrees(th), rpx(Kp[0, 0] * s * cx.cfg.get("focal_scale", 1), Dp), color=ORANGE, lw=2, label="starting profile (Gyroflow × 0.936)")
    ax.plot(np.degrees(th), rpx(L.fx, L.D), color=BLUE, lw=2.4, label="refined by COLMAP")
    ax.set_ylim(0, 1300); ax.set_xlabel("angle off the optical axis (°)"); ax.set_ylabel("distance from image centre (px)")
    ax.axhline(cx.W / 2, color=INK2, lw=0.8); ax.text(2, cx.W / 2 + 15, "image edge (half width)", fontsize=8.5, color=INK2)
    ax.legend(loc="upper left", fontsize=8.5); ax.set_title("A · Angle → pixel radius")
    ax = f.add_subplot(gs[1, 0])
    m_ = np.degrees(th) <= 62
    ax.plot(np.degrees(th)[m_], (rpx(L.fx, L.D) - rpx(Kp[0, 0] * s * cx.cfg.get("focal_scale", 1), Dp))[m_], color=BLUE, lw=2)
    ax.axhline(0, color=INK2, lw=0.8); ax.set_xlabel("angle off the optical axis (°)"); ax.set_ylabel("refined − start (px)")
    ax.set_title("B · What the refinement changed (to the image corner, ~62°)")
    # C: frame with equal-angle rings
    ax = f.add_subplot(gs[0, 1]); img = fr.copy()
    for deg in range(10, 81, 10):
        tt = math.radians(deg); t2 = tt * tt; rr = L.fx * tt * (1 + L.D[0] * t2 + L.D[1] * t2 ** 2 + L.D[2] * t2 ** 3 + L.D[3] * t2 ** 4)
        cv2.circle(img, (int(L.cx), int(L.cy)), int(rr), (61, 196, 255), 2, cv2.LINE_AA)
        cv2.putText(img, f"{deg}", (int(L.cx + rr * 0.707) + 4, int(L.cy - rr * 0.707)), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (61, 196, 255), 2, cv2.LINE_AA)
    _img_ax(ax, img, "C · Rings every 10° off-axis, drawn with the refined model")
    ax = f.add_subplot(gs[1, 1])
    K = np.array([[L.fx, 0, L.cx], [0, L.fy, L.cy], [0, 0, 1]]); Kn = K.copy(); Kn[0, 0] *= 0.55; Kn[1, 1] *= 0.55
    und = cv2.fisheye.undistortImage(fr, K, np.array(L.D), Knew=Kn)
    _img_ax(ax, und, "D · Undistorted to a pinhole view")
    return _save(f, out, "fig19_lens.png")

# ------------------------------------------------------------------ 20. error budget
def fig20_error_budget(cx, out):
    H = 53.6; v = 8.0
    dep = np.linspace(8, 89, 300); a = np.radians(dep); D = H / np.tan(a)
    e_ang = H / np.sin(a) ** 2 * math.radians(1.0)              # 1 degree tilt error
    e_ang_s = H / np.sin(a) ** 2 * math.radians(0.06)           # COLMAP-level
    e_h = D * (1.0 / H)                                         # 1 m height error
    e_t = np.full_like(D, v * 0.1)                              # 0.1 s timing error
    f = _fig(13, 7.0, "Error budget: what moves a ground point, and by how much",
             f"Ground position error of the spot a pixel sees, for a camera {H:.0f} m above the water flying {v:.0f} m/s, against how steeply the pixel "
             "looks down. Shallow rays (toward the horizon) amplify angle errors; that is why the gyro alone was not good enough.")
    gs = f.add_gridspec(1, 2, left=0.06, right=0.97, top=0.84, bottom=0.1, wspace=0.62, width_ratios=[1.45, 1])
    ax = f.add_subplot(gs[0]); ax.set_yscale("log")
    ax.plot(dep, e_ang, color=ORANGE, lw=2.2, label="1° angle error (gyro-only level)")
    ax.plot(dep, e_h, color=AQUA, lw=2.2, label="1 m height / water-level error")
    ax.plot(dep, e_t, color=YELLOW, lw=2.2, label="0.1 s timing error")
    ax.plot(dep, e_ang_s, color=BLUE, lw=2.2, label="0.06° angle error (COLMAP level)")
    for x, lab in [(45, "45°: spot 54 m away"), (20, "20°: 147 m away")]:
        ax.axvline(x, color=INK2, lw=0.8, ls=(0, (3, 3))); ax.text(x + 0.8, 120, lab, fontsize=9, color=INK2)
    ax.set_xlim(8, 89); ax.set_ylim(0.01, 300); ax.invert_xaxis()
    ax.set_xlabel("how far below the horizon the pixel looks (°)   ←  steep (near nadir)   ·   shallow (far)  →"); ax.set_ylabel("ground error (m, log scale)")
    ax.legend(loc="upper left", fontsize=9); ax.set_title("A · Error vs viewing angle")
    ax = f.add_subplot(gs[1]); ax.grid(axis="y", visible=False)
    at = [("angle, gyro only (1.6° median)", H / math.sin(math.radians(30)) ** 2 * math.radians(1.6), ORANGE),
          ("angle, COLMAP (0.06°)", H / math.sin(math.radians(30)) ** 2 * math.radians(0.06), BLUE),
          ("water level (±0.75 m guess)", (H / math.tan(math.radians(30))) * 0.75 / H, AQUA),
          ("timing (35 ms sync scatter)", v * 0.035, YELLOW),
          ("GPS position (0.84 m fit)", 0.84, MUTED)]
    y = np.arange(len(at))
    ax.barh(y, [a_[1] for a_ in at], color=[a_[2] for a_ in at], height=0.62, edgecolor=SURF, lw=1.5)
    for yi, a_ in zip(y, at): ax.text(a_[1] + 0.1, yi, f"{a_[1]:.1f} m", va="center", fontsize=9.5, color=INK2)
    ax.set_yticks(y, [a_[0] for a_ in at]); ax.invert_yaxis(); ax.set_xlabel("ground error at 30° below horizon (≈93 m away)"); ax.set_xlim(0, 7.6)
    ax.set_title("B · Our budget for a typical pixel")
    return _save(f, out, "fig20_error_budget.png")

# ------------------------------------------------------------------ 21. coverage
def fig21_coverage(cx, out, cell=2.0, step_s=1.0):
    from .render import ground_field
    from .figures_extra import _ortho_show, orthomosaic
    sc = cx.scene; img, ox, oy, oc = orthomosaic(cx)
    x0, y0 = ox, oy; W, H = int(img.shape[1] * oc / cell), int(img.shape[0] * oc / cell)
    cnt = np.zeros((H, W), np.int32)
    for t in np.arange(0, cx.cfg["clip_duration_s"], step_s):
        _, cam, _, _ = cx.pose(t); G = ground_field(cam, cx.W, cx.H, 0.12, sc.terrain)
        r = np.hypot(G[..., 0] - cam.C[0], G[..., 1] - cam.C[1]); ok = np.isfinite(r) & (r < 600)
        ix = ((G[..., 0][ok] - x0) / cell).astype(int); iy = ((G[..., 1][ok] - y0) / cell).astype(int)
        g = (ix >= 0) & (ix < W) & (iy >= 0) & (iy < H); m = np.zeros((H, W), bool); m[iy[g], ix[g]] = True
        m = cv2.dilate(m.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0; cnt += m
    f = _fig(10, 8.4, "How often each spot on the ground was seen",
             f"Number of 1-second frames whose view covered each {cell:.0f} m cell during the clip. Many views from different angles is what lets "
             "COLMAP triangulate and the water map vote; the far edges were seen only a few times, obliquely.")
    ax = f.add_axes([0.08, 0.07, 0.66, 0.76]); ax.set_aspect("equal"); _ortho_show(ax, cx, alpha=0.6)
    cm = LinearSegmentedColormap.from_list("seq", ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"])
    C = np.ma.masked_where(cnt == 0, cnt)
    im = ax.imshow(C, extent=[x0, x0 + W * cell, y0, y0 + H * cell], origin="lower", cmap=cm, alpha=0.75, interpolation="nearest", vmin=1, vmax=np.percentile(cnt[cnt > 0], 99))
    ax.plot(cx.sfm["Cw"][:, 0], cx.sfm["Cw"][:, 1], color=ORANGE, lw=1.6, label="camera path")
    cax = f.add_axes([0.78, 0.22, 0.022, 0.45]); cb = f.colorbar(im, cax=cax); cb.set_label("frames that saw the cell"); cb.outline.set_visible(False)
    ax.set_xlim(x0, x0 + W * cell); ax.set_ylim(y0, y0 + H * cell); ax.set_xlabel("east (m)"); ax.set_ylabel("north (m)"); ax.legend(loc="lower left")
    f.text(0.78, 0.76, f"max {cnt.max()} views\nmedian {int(np.median(cnt[cnt > 0]))} views", fontsize=10, color=INK2, va="top")
    return _save(f, out, "fig21_coverage.png")

# ------------------------------------------------------------------ 22. timeline
def fig22_timeline(cx, out):
    from .scene import sat_state
    sc = cx.scene; wm = cx.watermap(); mask = cx.cfg["elevation_mask_deg"]
    ts = np.arange(0, cx.cfg["clip_duration_s"], 0.5); hgt, spd, nup, nwet, hd = [], [], [], [], []
    prev = None
    for t in ts:
        mt, cam, _, _ = cx.pose(t); C = cam.C; hgt.append(C[2] - float(sc.terrain.height(C[0], C[1])))
        spd.append(np.nan if prev is None else np.hypot(*(C[:2] - prev[:2])) / 0.5); prev = C
        fw = cam.R[:, 2]; hd.append(math.degrees(math.atan2(fw[0], fw[1])) % 360)
        _, ss = sat_state(sc, mt, C); up = [s for s in ss if s["el"] >= mask]; nup.append(len(up)); k = 0
        for s in up:
            c_, _, _ = fresnel_zone(s["enu"], C, sc.wl, 8); k += float(wm.sample(np.array([c_[0]]), np.array([c_[1]]))[0]) > 0.5
        nwet.append(k)
    mt0 = sc.mission_t(cx.t0); tx = lambda m: (m - mt0) / (1 + sc.sync["rate"])
    f = _fig(13, 8.6, "The survey clip at a glance",
             "Every quantity the overlay uses, against clip time. Shaded bands are the turns; the zone count shows how rarely a reflection "
             "landed on the water that was actually there.")
    gs = f.add_gridspec(4, 1, left=0.07, right=0.985, top=0.86, bottom=0.07, hspace=0.45)
    panels = [("Height above the surface below the drone (m)", np.array(hgt), BLUE),
              ("Ground speed (m/s)", np.array(spd), BLUE),
              ("Camera heading (°)", np.unwrap(np.radians(hd)) * 180 / np.pi, BLUE)]
    for k, (ttl, y, c) in enumerate(panels):
        ax = f.add_subplot(gs[k]); _shade_turns(ax, sc, tx, label=False); ax.plot(ts, y, color=c, lw=1.8); ax.set_xlim(0, ts[-1]); ax.set_title(ttl)
    ax = f.add_subplot(gs[3]); _shade_turns(ax, sc, tx, label=False)
    ax.step(ts, nup, where="post", color=MUTED, lw=1.6, label="satellites above 20°")
    ax.step(ts, nwet, where="post", color=AQUA, lw=2.2, label="zones on water")
    ax.set_xlim(0, ts[-1]); ax.set_ylim(0, max(nup) + 2); ax.set_title("Satellites in view vs reflections on water (count)")
    ax.set_xlabel("clip time (s)"); ax.legend(loc="center right", ncol=2)
    return _save(f, out, "fig22_timeline.png")

MORE = {18: fig18_skyplot, 19: fig19_lens, 20: fig20_error_budget, 21: fig21_coverage, 22: fig22_timeline}
