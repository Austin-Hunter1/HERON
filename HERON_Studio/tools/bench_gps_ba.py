"""Test the GPS-constrained full merge on a merged chunk model and score it like the other results.

    python tools/bench_gps_ba.py <flight dir> <merged model dir> <out dir> [iters] [sigma_h]
"""
import json, os, shutil, sys, time
from pathlib import Path
here = Path(__file__).resolve().parent
flight, merged, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
iters = int(sys.argv[4]) if len(sys.argv) > 4 else 150; sig = float(sys.argv[5]) if len(sys.argv) > 5 else 1.5
os.environ.update(HERON_DATA=str(flight / "data"), HERON_CACHE=str(flight / "cache"), HERON_OUT=str(flight / "out"),
                  HERON_WORK=str(flight / "work"),
                  HERON_T0_US=str(json.loads((flight / "engine_env.json").read_text())["t0_us"]))
sys.path.insert(0, str(here.parent / "engine")); sys.path.insert(0, str(here))
from heron_ar.gps_ba import gps_bundle_adjust
cfg = out / "config.json"; out.mkdir(parents=True, exist_ok=True); shutil.copy(flight / "config.json", cfg)  # a resync would write here
t = time.time(); gps_bundle_adjust(merged, out / "model", cfg, sigma_h=sig, iters=iters); secs = time.time() - t
import solve_bench as sb, pycolmap
step = json.loads((flight / "config.json").read_text())["sfm"]["frame_step"]
fl = sb.Flight(str(flight), step)
r, _ = sb.score(fl, pycolmap.Reconstruction(str(out / "model")), f"GPS-constrained merge {merged.parent.name} s{sig:g} i{iters}",
                secs, {"gps_ba": secs}, dict(kind="gps_ba", sigma_h=sig, iters=iters))
sb.save(out.parent, r)
