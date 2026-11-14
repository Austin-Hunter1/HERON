"""GPS residual along the flight, per chunk, for merged models: where does a merge go wrong?

    python tools/bench_where.py <model dir> [<model dir> ...]
    (flight: BENCH_FLIGHT env var, default the 10 Nov flight; frame step and chunking from its config.json)
"""
import json, os, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import solve_bench as sb, pycolmap

fdir = os.environ.get("BENCH_FLIGHT", r"C:\Users\Nathan\HERON Studio\flights\flight-2026-11-10-698ad8")
sfm = json.loads((Path(fdir) / "config.json").read_text())["sfm"]
fl = sb.Flight(fdir, sfm["frame_step"])
parts = sb.chunks_of(fl.names, sfm.get("chunk_size", 1200), sfm.get("chunk_overlap", 150))
if os.environ.get("BENCH_SYNC"): fl.gps.sync = json.loads(os.environ["BENCH_SYNC"])   # try a clock sync without writing the config
for d in sys.argv[1:]:
    if d.startswith("cheap:"):                    # cheap merge of a chunk run: cheap:<chunks dir>
        cd = Path(d[6:]); recs = [sb.best_model(p) for p in sorted(cd.glob("part*"))]
        W, fits, dis = sb.stitch(fl, recs)
        P = {f: (w[0].T, w[1]) for f, w in W.items()}; fit = fl.gps.fit(P); W = sb.to_world(P, fit["sim"])
    else:
        P = sb.poses(pycolmap.Reconstruction(d)); fit = fl.gps.fit(P); W = sb.to_world(P, fit["sim"])
    frames = sorted(W); G, ok = fl.gps.at(frames, fit["latency"])
    res = {f: float(np.linalg.norm(W[f][1] - g)) for f, g, k in zip(frames, G, ok) if k}
    print(f"\n{d}\n  overall median {np.median(list(res.values())):.2f} m (robust fit {fit['median']:.2f})")
    for i, p in enumerate(parts):
        v = [res[sb.frame_no(f)] for f in p if sb.frame_no(f) in res]
        if v: print(f"  chunk {i}: median {np.median(v):6.2f} m  p90 {np.percentile(v, 90):6.2f} m  ({len(v)} frames)")
