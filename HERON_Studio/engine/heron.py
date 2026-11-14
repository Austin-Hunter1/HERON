#!/usr/bin/env python3
"""HERON AR command line.

    python heron.py sync     --config configs/base.json          # 1. clock sync GoPro <-> drone
    python heron.py sfm      --config configs/base.json          # 2. COLMAP on the clip
    python heron.py georef   --config configs/base.json          # 3. SfM -> GPS frame, per-frame poses
    python heron.py preview  --config configs/base.json --t 52,90 # stills (builds the water map once)
    python heron.py render   --config configs/base.json          # 4. full video (+ per-zone CSV)
    python heron.py figures  --config configs/base.json          # pipeline figures -> docs/figures
    python heron.py all      --config configs/base.json          # 1-4 in order

Every config path is relative to the repo root. Put the GoPro clips in video/.
"""
import argparse, json, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

def _work(cfg, a):
    import os
    return Path(a.work) if a.work else Path(os.environ.get("HERON_WORK") or ROOT / "work") / Path(cfg["_path"]).stem

def _solved(cfg):
    from heron_ar.geometry import DATA
    return DATA / cfg["sfm_poses"]

def cmd_sync(cfg, a):
    from heron_ar.geometry import GoProIMU, estimate_sync, DATA
    imu = GoProIMU(DATA / cfg.get("gopro_dir", "gopro1"))
    s = estimate_sync(imu, search=tuple(cfg.get("sync_search", (-120, 0))))
    print(f"mission_t = video_t * (1 {s['rate']:+.7f}) {s['offset']:+.4f} s   "
          f"({s['windows']} windows, mean corr {s['mean_corr']:.2f}, residual {1e3 * s['weighted_residual_s']:.0f} ms)")
    if a.write:
        p = Path(cfg["_path"]); raw = json.loads(p.read_text()); raw["sync"] = dict(rate=s["rate"], offset=s["offset"])
        p.write_text(json.dumps(raw, indent=2)); print("written to", p)

def cmd_sfm(cfg, a):
    from heron_ar.sfm import extract, extracted, select, solve
    from heron_ar.geometry import resolve, DATA, clip_fps
    o = cfg["sfm"]; w = _work(cfg, a); step = a.step or o["frame_step"]
    t0 = a.start if a.start is not None else 0.0
    t1 = a.end if a.end is not None else cfg["clip_duration_s"]
    have = None if a.reextract else extracted(w)
    names = select(have, step) if have and have.get("width") in (None, o["work_width"]) else None
    if names: print(f"reusing {len(names):,} of {len(have['names']):,} extracted frames (every {step})", flush=True)
    else:
        extract(resolve(cfg["clip_path"]), w, t0, t1, step=step, width=o["work_width"], fps=clip_fps(cfg))
        names = extracted(w)["names"]
    solve(w, DATA / cfg["lens_profile"], width=o["work_width"], focal_scale=cfg.get("focal_scale", 1.0),
          max_features=o["max_features"], overlap=o["sequential_overlap"], names=names, step=step,
          mapper=getattr(a, "mapper", None) or o.get("mapper", "global"), gpu=o.get("gpu", "auto"), colmap_exe=o.get("colmap_exe"),
          chunk_size=o.get("chunk_size") or 0, chunk_overlap=o.get("chunk_overlap", 150), chunk_jobs=o.get("chunk_jobs", 3),
          merge=o.get("merge", "cheap"), track_cap=o.get("track_cap"), merge_ba_iters=o.get("merge_ba_iters", 50),
          gps_cfg=cfg["_path"] if o.get("merge_gps", True) else None)

def cmd_georef(cfg, a):
    from heron_ar.georef import main
    main(str(_work(cfg, a) / "sparse"), cfg["_path"], str(_solved(cfg)))

def cmd_merge(cfg, a):
    from heron_ar.merge import merge
    merge(str(_solved(cfg)), a.parts)

def cmd_render(cfg, a):
    from heron_ar.render import run, run_parallel
    jobs = getattr(a, "jobs", None) or cfg.get("render_jobs")          # None/"auto": one per CPU thread, less one
    jobs = None if jobs in (None, "auto") else int(jobs)
    whole = a.frame_start == 0 and a.frame_end is None
    if whole and jobs != 1: run_parallel(cfg["_path"], out=a.out, jobs=jobs)
    else: run(cfg["_path"], out=a.out, frame_start=a.frame_start, frame_end=a.frame_end, preroll=getattr(a, "preroll", 0))

def cmd_preview(cfg, a):
    from heron_ar.render import run
    run(cfg["_path"], preview=a.t)

def cmd_figures(cfg, a):
    from heron_ar.figures import make_all
    make_all(cfg["_path"], ROOT / "docs" / "figures", work=_work(cfg, a), only=a.only)

def cmd_all(cfg, a):
    for f in (cmd_sync, cmd_sfm, cmd_georef, cmd_render):
        print(f"\n=== {f.__name__[4:]} ===", flush=True); t = time.time(); f(cfg, a); print(f"({time.time() - t:.0f} s)")

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    def add(name, fn, help):
        p = sub.add_parser(name, help=help); p.add_argument("--config", required=True); p.add_argument("--work", default=None,
                            help="SfM working folder (default work/<config name>)"); p.set_defaults(fn=fn); return p
    p = add("sync", cmd_sync, "estimate the GoPro->mission clock mapping"); p.add_argument("--write", action="store_true", help="store it in the config")
    p = add("sfm", cmd_sfm, "extract frames and run COLMAP")
    p.add_argument("--start", type=float, default=None, help="clip seconds"); p.add_argument("--end", type=float, default=None)
    p.add_argument("--step", type=int, default=None, help="use every Nth frame (1 = every frame)"); p.add_argument("--reextract", action="store_true")
    p.add_argument("--mapper", choices=("global", "incremental"), default=None, help="override sfm.mapper")
    add("georef", cmd_georef, "fit SfM to the GPS track, write per-keyframe poses")
    p = add("merge", cmd_merge, "merge georeferenced chunk files into the config's sfm_poses"); p.add_argument("parts", nargs="+")
    p = add("render", cmd_render, "render the AR video")
    p.add_argument("--frame-start", type=int, default=0); p.add_argument("--frame-end", type=int, default=None); p.add_argument("--out", default=None)
    p.add_argument("--jobs", type=int, default=None, help="parallel render processes (default: CPU threads - 1; 1 = serial)")
    p.add_argument("--preroll", type=int, default=0, help="frames composed before --frame-start but not written")
    p = add("preview", cmd_preview, "render stills"); p.add_argument("--t", default="52", help="comma-separated clip seconds")
    p = add("figures", cmd_figures, "pipeline figures"); p.add_argument("--only", default=None, help="comma-separated figure numbers")
    p = add("all", cmd_all, "sync -> sfm -> georef -> render")
    p.add_argument("--start", type=float, default=None); p.add_argument("--end", type=float, default=None)
    p.add_argument("--step", type=int, default=None); p.add_argument("--reextract", action="store_true"); p.add_argument("--write", action="store_true")
    p.add_argument("--frame-start", type=int, default=0); p.add_argument("--frame-end", type=int, default=None); p.add_argument("--out", default=None)
    a = ap.parse_args()
    from heron_ar.geometry import load_config
    a.fn(load_config(a.config), a)

if __name__ == "__main__":
    main()
