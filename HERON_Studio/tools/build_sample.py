"""Package the 19 Oct 2026 Boulder Reservoir results as Studio's built-in sample flight.

Run from the heron-ar checkout's results (paths below); the output goes to sample/boulder-2026-10-19
and is copied into the user's flights folder on first start.
"""
import json, os, shutil, subprocess, sys
from pathlib import Path
import numpy as np, cv2

AR = Path(os.environ.get("HERON_AR", "/home/claude/heron-ar"))
APP = Path(__file__).resolve().parent.parent
OUT = APP / "sample" / "boulder-2026-10-19"
sys.path.insert(0, str(APP))


def main():
    if OUT.exists(): shutil.rmtree(OUT)
    for s in ("data", "cache", "out", "exports"): (OUT / s).mkdir(parents=True)
    # origin + outline + config
    lines = (AR / "data" / "POS.csv").read_text().splitlines(); (OUT / "data" / "POS.csv").write_text("\n".join(lines[:200]) + "\n")
    shutil.copy2(AR / "data" / "boulder_reservoir.geojson", OUT / "data" / "outline.geojson")
    import csv as _csv
    rows = list(_csv.DictReader(open(AR / "data" / "POS.csv"))); k = max(1, len(rows) // 1500)
    (OUT / "track.json").write_text(json.dumps([[float(r["Lng"]), float(r["Lat"]), float(r["Alt"])] for r in rows[::k]]))
    sys.path.insert(0, str(AR)); from heron_ar.geometry import load_config
    cfg = load_config(AR / "configs" / "survey_1080p.json"); cfg.pop("_path")
    cfg.update(clip_path=str(AR / cfg["clip_path"]), fps=24000 / 1001, aspect=16 / 9, outline_geojson="boulder_reservoir.geojson")
    (OUT / "config.json").write_text(json.dumps(cfg, indent=1))
    # water map (uint8 to keep the download small)
    z = np.load(AR / "cache" / "watermap_HERON_GoPro1_survey_1080p.npz")
    np.savez_compressed(OUT / "cache" / "watermap_survey.npz", x0=z["x0"], y0=z["y0"], cell=z["cell"],
                        prob=np.round(z["prob"] * 255).astype(np.uint8))
    # dense height model, 0.5 m, float16
    d = np.load(AR / "cache" / "dem_dense.npz")
    np.savez_compressed(OUT / "out" / "dem.npz", Z=d["Z"].astype(np.float16), x0=d["x0"], y0=d["y0"], cell=d["cell"], water_z=d["water_z"])
    # reflections: run the Studio engine task against the original data
    env = dict(os.environ, HERON_DATA=str(AR / "data"), HERON_CACHE=str(AR / "cache"), HERON_OUT=str(OUT / "out"),
               PYTHONPATH=f"{AR}{os.pathsep}{APP}", HERON_ENGINE=str(AR))
    subprocess.run([sys.executable, "-m", "studio.engine_tasks", "reflections", str(OUT)], cwd=APP, env=env, check=True)
    # media
    shutil.copy2(AR / "out" / "HERON_AR_passes2-3_share.mp4", OUT / "out" / "HERON_AR.mp4")
    cap = cv2.VideoCapture(str(OUT / "out" / "HERON_AR.mp4")); cap.set(cv2.CAP_PROP_POS_FRAMES, 24 * 60); ok, fr = cap.read()
    cv2.imwrite(str(OUT / "still.jpg"), fr, [cv2.IMWRITE_JPEG_QUALITY, 88])
    cv2.imwrite(str(OUT / "thumb.jpg"), cv2.resize(fr, (960, 540), interpolation=cv2.INTER_AREA), [cv2.IMWRITE_JPEG_QUALITY, 84])
    o = np.load(AR / "cache" / "ortho_HERON_GoPro1_survey_1080p_0.5.npz"); rgba = o["rgb"]   # RGBA, alpha 0 = no photo
    from PIL import Image
    im = Image.fromarray(rgba[::-1]); im.save(OUT / "ortho.png", optimize=True); im.save(OUT / "ortho.webp", quality=82)
    rgb = rgba[..., :3]
    from studio import water
    fr_ = water.origin(OUT); x0, y0, c = float(o["x0"]), float(o["y0"]), float(o["cell"]); H, W = rgb.shape[:2]
    lo, la = fr_.ll(np.array([x0, x0 + W * c]), np.array([y0, y0 + H * c]))
    (OUT / "ortho.json").write_text(json.dumps(dict(en=[x0, y0, x0 + W * c, y0 + H * c], west=lo[0], east=lo[1], south=la[0], north=la[1])))
    # flight record
    rs = json.loads((OUT / "out" / "reflections_summary.json").read_text())
    glint = json.loads((AR / "cache" / "glint_check2.json").read_text()) if (AR / "cache" / "glint_check2.json").exists() else {}
    wind = json.loads((AR / "cache" / "wind.json").read_text()) if (AR / "cache" / "wind.json").exists() else {}
    sig = json.loads((AR / "cache" / "glint_sig2.json").read_text()) if (AR / "cache" / "glint_sig2.json").exists() else {}
    f = dict(id="boulder-reservoir-2026-10-19", name="Boulder Reservoir · test flight", site="Boulder Reservoir", date="2026-10-19",
             sample=True, status="done", created="2026-10-19T17:00:00+00:00", files=[
                 dict(name="[flight].bin", kind="log", linked=False, info=dict(format="ArduPilot DataFlash")),
                 dict(name="GoPro1.MP4 (survey clip)", kind="video", linked=False, info={})],
             video=dict(name="GoPro1 survey clip", width=1920, height=1080, fps=23.976, duration_s=174.7, codec="hevc",
                        resolution_label="1080p", aspect="16:9", camera_model="HERO7 Black"),
             lens=dict(id="HERON/GoPro_HERO7 Black_Wide_HS EIS_4k_16by9_3840x2160-29.97fps.json",
                       name="GoPro_HERO7 Black_Wide_HS EIS_4k_16by9_3840x2160-29.97fps", notes=["Focal length refined by COLMAP (×0.936)."]),
             checklist=dict(video=True, log=True, camera_gyro=True, mission=True, outline=True), ready=True,
             settings=dict(solve="full", render_size="1080p", make=dict(ar_video=True, water=True, model3d=True, report=True)),
             job=dict(state="done", progress=100, steps=[], done=[]),
             results=dict(
                 sync=dict(rate=cfg["sync"]["rate"], offset=cfg["sync"]["offset"], residual_ms=35),
                 solve=dict(frames=2925, frames_total=2925, points=129119, reprojection_px=0.47, gps_fit_m=0.95,
                            note="Every frame solved by COLMAP for passes 2–3 (0–122 s of the clip)."),
                 glint=dict(median_px=2.6, survey_solve_px=4.0),
                 lake=dict(outline_above_water_m=1.1, exposed_lakebed_ha=6.0, ripple_tilt_deg=2.1, wind_ms=1.7, wind_from="NE",
                           sigma2=sig.get("sig2"), water_edge_flatness_m=0.42),
                 reflections=rs,
                 ar_video_note="AR video covers passes 2–3."))
    (OUT / "flight.json").write_text(json.dumps(f, indent=1))
    # exports
    s = water.export(OUT, f); pkg = water.site_package(OUT, f)
    f["results"]["water"] = s; f["results"]["site_package"] = pkg.name
    (OUT / "flight.json").write_text(json.dumps(f, indent=1))
    tot = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file())
    print("sample:", OUT, f"{tot / 1e6:.1f} MB"); print(json.dumps(s, indent=1)[:1500])


if __name__ == "__main__":
    main()
