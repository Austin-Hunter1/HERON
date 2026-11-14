"""Benchmarks for the camera solve: track caps, overlapping chunks, cheap vs full merge.

Runs the COLMAP program (CUDA build) on a flight's existing features database, so nothing is
re-extracted. Each variant is scored three ways:
  * time per mapper stage
  * GPS fit: one similarity (with position latency) from the solved cameras to the EKF track
  * pose difference to a reference solve of the same frames (metres / degrees, after alignment)

  python tools/solve_bench.py caps   --flight <dir> --step 12 --out <bench dir>
  python tools/solve_bench.py chunks --flight <dir> --step 4  --out <bench dir> --chunk 1200 --overlap 150 --jobs 3

Results go to <out>/results.json (appended) and stdout.
"""
import argparse, csv, json, os, re, shutil, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "engine"))


# ------------------------------------------------------------------ COLMAP program
def colmap_exe():
    sys.path.insert(0, str(HERE.parent / "engine"))
    from heron_ar.sfm import find_colmap
    exe = find_colmap(os.environ.get("HERON_COLMAP"))
    if not exe: raise SystemExit("no COLMAP program found")
    exe = Path(exe)
    if exe.suffix.lower() == ".bat": exe = exe.parent / "bin" / "colmap.exe"
    env = dict(os.environ); env["PATH"] = str(exe.parent) + os.pathsep + env.get("PATH", "")
    env["QT_PLUGIN_PATH"] = str(exe.parent.parent / "plugins")
    return exe, env


def run(cmd, log_path, env):
    """Run a COLMAP command, log everything, return (seconds, {stage: seconds})."""
    t0 = time.time(); stages = {}
    with open(log_path, "w", encoding="utf-8", errors="replace") as lf:
        p = subprocess.Popen([str(c) for c in cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             errors="replace", env=env)
        for line in p.stdout:
            lf.write(line)
            m = re.match(r"^(.*?) done in ([0-9.]+) seconds", line.strip())
            if m: stages[m.group(1)] = stages.get(m.group(1), 0) + float(m.group(2))
        rc = p.wait()
    if rc: raise RuntimeError(f"{cmd[1]} failed ({rc}), see {log_path}")
    return time.time() - t0, stages


def global_mapper(exe, env, db, images, names, out, extra=None, tag="map"):
    out = Path(out); shutil.rmtree(out, ignore_errors=True); out.mkdir(parents=True)
    lst = out / "images.txt"; lst.write_text("\n".join(names) + "\n")
    opts = {"image_list_path": lst, "ba_refine_principal_point": 0, "ba_refine_focal_length": 1,
            "ba_refine_extra_params": 1, "ba_ceres_use_gpu": 1}
    opts.update(extra or {})
    cmd = [exe, "global_mapper", "--database_path", db, "--image_path", images, "--output_path", out]
    for k, v in opts.items(): cmd += [f"--GlobalMapper.{k}", v]
    secs, stages = run(cmd, out / f"{tag}.log", env)
    return secs, stages, best_model(out)


def best_model(sparse):
    import pycolmap
    recs = [pycolmap.Reconstruction(str(d)) for d in Path(sparse).iterdir() if d.is_dir() and (d / "images.bin").exists()]
    return max(recs, key=lambda r: r.num_reg_images()) if recs else None


# ------------------------------------------------------------------ geometry
def frame_no(name): return int(re.findall(r"\d+", name)[0])


def poses(rec):
    """{frame: (R_cw, C)} for every posed image."""
    out = {}
    for im in rec.images.values():
        if not im.has_pose: continue
        T = im.cam_from_world(); R = T.rotation.matrix(); out[frame_no(im.name)] = (R, -R.T @ T.translation)
    return out


def umeyama(src, dst):
    ms, md = src.mean(0), dst.mean(0); S, D = src - ms, dst - md
    U, sig, Vt = np.linalg.svd(D.T @ S / len(src)); E = np.eye(3)
    if np.linalg.det(U @ Vt) < 0: E[2, 2] = -1
    R = U @ E @ Vt; s = np.trace(np.diag(sig) @ E) / (S ** 2).sum(1).mean()
    return s, R, md - s * R @ ms


def robust_sim(src, dst, it=5):
    keep = np.ones(len(src), bool)
    for _ in range(it):
        s, R, t = umeyama(src[keep], dst[keep]); e = np.linalg.norm((s * (R @ src.T)).T + t - dst, axis=1)
        keep = e < max(1.0, 3 * np.median(e[keep]))
    return s, R, t, e, keep


class GPS:
    """The flight's EKF track in a flat local ENU frame, on the video clock."""
    def __init__(self, flight):
        flight = Path(flight); cfg = json.loads((flight / "config.json").read_text())
        t0 = json.loads((flight / "engine_env.json").read_text())["t0_us"] / 1e6
        rows = list(csv.DictReader(open(flight / "data" / "POS.csv")))
        self.t = np.array([float(r["TimeUS"]) for r in rows]) / 1e6 - t0
        lat = np.array([float(r["Lat"]) for r in rows]); lon = np.array([float(r["Lng"]) for r in rows])
        alt = np.array([float(r["Alt"]) for r in rows])
        self.enu = np.c_[(lon - lon[0]) * 111320 * np.cos(np.radians(lat[0])), (lat - lat[0]) * 110540, alt - alt[0]]
        self.sync = cfg["sync"]; self.fps = cfg.get("clip_fps") or 23.976; self.vt0 = cfg.get("clip_video_start_s", 0.0)

    def at(self, frames, dt=0.0):
        from heron_ar.geometry import mission_time
        mt = mission_time(self.vt0 + np.asarray(frames) / self.fps, self.sync) + dt
        ok = (mt > self.t[0]) & (mt < self.t[-1])
        return np.c_[[np.interp(mt, self.t, self.enu[:, i]) for i in range(3)]].T, ok

    def fit(self, P):
        """Similarity camera centres -> GPS, latency searched like georef. P: {frame: (R, C)}."""
        fr = np.array(sorted(P)); C = np.array([P[f][1] for f in fr]); best = None
        for dt in np.arange(-1.6, 0.61, 0.1):
            G, ok = self.at(fr, dt)
            s, R, t, e, keep = robust_sim(C[ok], G[ok], it=3)
            if best is None or np.median(e[keep]) < best[0]: best = (np.median(e[keep]), dt)
        G, ok = self.at(fr, best[1]); s, R, t, e, keep = robust_sim(C[ok], G[ok])
        return dict(median=float(np.median(e[keep])), p90=float(np.percentile(e[keep], 90)), latency=float(best[1]),
                    scale=float(s), used=int(keep.sum()), of=int(ok.sum()), sim=(s, R, t))


def to_world(P, sim):
    """Apply a similarity to {frame: (R_cw, C)} -> {frame: (R_wc, C_world)}."""
    s, R, t = sim
    return {f: (R @ Rcw.T, s * R @ C + t) for f, (Rcw, C) in P.items()}


def rot_deg(Ra, Rb):
    c = (np.trace(Ra.T @ Rb) - 1) / 2
    return float(np.degrees(np.arccos(np.clip(c, -1, 1))))


def compare(W, Wref):
    """Pose difference between two world-frame pose sets (both already in metres, ENU). A residual
    similarity is removed first, so this measures shape, not the GPS tie."""
    common = sorted(set(W) & set(Wref))
    if len(common) < 10: return dict(common=len(common))
    A = np.array([W[f][1] for f in common]); B = np.array([Wref[f][1] for f in common])
    s, R, t, e, keep = robust_sim(A, B)
    rd = np.array([rot_deg(R @ W[f][0], Wref[f][0]) for f in common])
    return dict(common=len(common), pos_median_m=float(np.median(e)), pos_p90_m=float(np.percentile(e, 90)),
                pos_max_m=float(e.max()), rot_median_deg=float(np.median(rd)), rot_p90_deg=float(np.percentile(rd, 90)))


# ------------------------------------------------------------------ data set
class Flight:
    def __init__(self, flight, step):
        self.dir = Path(flight); self.work = self.dir / "work" / "config"
        self.db = self.work / f"db_s{step}.db"; self.images = self.work / "images"
        from heron_ar.sfm import extracted, select
        key = json.loads(self.db.with_suffix(".json").read_text())
        self.names = select(extracted(self.work), step)
        assert self.names and len(self.names) == key["n"], (len(self.names or []), key["n"])
        self.gps = GPS(self.dir)
        self.ref = best_model(self.work / "sparse"); self.ref_fit = self.ref_world = None
        if self.ref is not None:          # no reference yet while the flight's own solve is still running
            rp = poses(self.ref); self.ref_fit = self.gps.fit(rp); self.ref_world = to_world(rp, self.ref_fit["sim"])


def score(fl, rec, label, secs, stages, extra=None):
    P = poses(rec); fit = fl.gps.fit(P); W = to_world(P, fit["sim"])
    r = dict(label=label, seconds=round(secs, 1), stages={k: round(v, 1) for k, v in stages.items()},
             placed=len(P), of=len(fl.names), gps=dict((k, round(v, 3) if isinstance(v, float) else v)
                                                       for k, v in fit.items() if k != "sim"),
             vs_reference=compare(W, fl.ref_world), points=rec.num_points3D())
    r.update(extra or {})
    return r, W


def save(out, r):
    p = Path(out) / "results.json"; allr = json.loads(p.read_text()) if p.exists() else []
    allr.append(r); p.write_text(json.dumps(allr, indent=1))
    print(json.dumps(r, indent=1), flush=True)


def private_db(fl, out):
    """A copy of the features database for the bench, so the flight's own one is never touched."""
    dst = Path(out) / fl.db.name
    if not dst.exists() or dst.stat().st_size != fl.db.stat().st_size:
        print(f"copying {fl.db} -> {dst}", flush=True); shutil.copy2(fl.db, dst)
    return dst


# ------------------------------------------------------------------ experiments
CAPS = {
    "default": {},
    "tpv1000": {"track_required_tracks_per_view": 1000},
    "tpv500": {"track_required_tracks_per_view": 500},
    "tpv250": {"track_required_tracks_per_view": 250},
    "tpv125": {"track_required_tracks_per_view": 125},
    "tpv250_noretri": {"track_required_tracks_per_view": 250, "skip_retriangulation": 1},
}


def cmd_caps(a):
    fl = Flight(a.flight, a.step); exe, env = colmap_exe(); db = private_db(fl, a.out)
    names = fl.names[a.first:a.first + a.n] if a.n else fl.names
    print(f"reference: {fl.ref.num_reg_images()} frames, GPS fit {fl.ref_fit['median']:.2f} m", flush=True)
    for label in (a.only.split(",") if a.only else CAPS):
        out = Path(a.out) / f"caps_{label}_{len(names)}"
        secs, stages, rec = global_mapper(exe, env, db, fl.images, names, out, CAPS[label])
        r, _ = score(fl, rec, f"cap {label} ({len(names)} frames, {fl.db.name})", secs, stages, dict(kind="caps", cap=CAPS[label]))
        save(a.out, r)


def chunks_of(names, size, overlap):
    if len(names) <= size: return [names]
    n = int(np.ceil((len(names) - overlap) / (size - overlap)))
    stride = (len(names) - overlap) / n
    starts = [int(round(i * stride)) for i in range(n)]
    return [names[s:(starts[i + 1] + overlap if i + 1 < n else len(names))] for i, s in enumerate(starts)]


def stitch(fl, chunk_recs):
    """Cheap merge: each chunk gets its own similarity to GPS; overlap frames come from the chunk in
    which they sit furthest from the edge. Returns (world poses, per-chunk fits, overlap disagreement)."""
    Ws, fits, spans = [], [], []
    for rec in chunk_recs:
        P = poses(rec); fit = fl.gps.fit(P); Ws.append(to_world(P, fit["sim"])); fits.append(fit)
        fr = sorted(P); spans.append((fr[0], fr[-1]))
    merged, dis_p, dis_r = {}, [], []
    for i, W in enumerate(Ws):
        for f, pose in W.items():
            mid = min(f - spans[i][0], spans[i][1] - f)
            if f in merged:
                j, pose_j, mid_j = merged[f]
                dis_p.append(np.linalg.norm(pose[1] - pose_j[1])); dis_r.append(rot_deg(pose[0], pose_j[0]))
                if mid <= mid_j: continue
            merged[f] = (i, pose, mid)
    W = {f: v[1] for f, v in merged.items()}
    dis = dict(n=len(dis_p), pos_median_m=float(np.median(dis_p)) if dis_p else None,
               pos_p90_m=float(np.percentile(dis_p, 90)) if dis_p else None,
               rot_median_deg=float(np.median(dis_r)) if dis_r else None)
    return W, fits, dis


def full_merge(exe, env, chunk_dirs, out, ba_iters):
    """Full merge: chain model_merger over neighbouring chunks, then one bundle adjustment of the whole."""
    out = Path(out); shutil.rmtree(out, ignore_errors=True); out.mkdir(parents=True)
    cur = chunk_dirs[0]; t_merge = 0.0
    for i, nxt in enumerate(chunk_dirs[1:], 1):
        dst = out / f"merged_{i}"; dst.mkdir()
        s, _ = run([exe, "model_merger", "--input_path1", cur, "--input_path2", nxt, "--output_path", dst],
                   out / f"merge_{i}.log", env); t_merge += s; cur = dst
    ba = out / "ba"; ba.mkdir()
    s, _ = run([exe, "bundle_adjuster", "--input_path", cur, "--output_path", ba,
                "--BundleAdjustment.refine_principal_point", 0, "--BundleAdjustmentCeres.use_gpu", 1,
                "--BundleAdjustmentCeres.max_num_iterations", ba_iters], out / "ba.log", env)
    import pycolmap
    return t_merge, s, pycolmap.Reconstruction(str(cur)), pycolmap.Reconstruction(str(ba))


def cmd_chunks(a):
    fl = Flight(a.flight, a.step); exe, env = colmap_exe(); db = private_db(fl, a.out)
    cap = CAPS[a.cap]; parts = chunks_of(fl.names, a.chunk, a.overlap)
    tag = f"c{a.chunk}_o{a.overlap}_{a.cap}"
    print(f"{len(parts)} chunks: {[len(p) for p in parts]}, {a.jobs} at a time; reference GPS fit "
          f"{fl.ref_fit['median']:.2f} m", flush=True)
    import queue                                  # one database copy per parallel job ("database is locked")
    dbs = queue.Queue(); dbs.put(db)
    for k in range(1, a.jobs):
        c = Path(a.out) / f"db_s{a.step}_{k}.db"
        if not c.exists() or c.stat().st_size != Path(db).stat().st_size: shutil.copyfile(db, c)
        dbs.put(c)
    t0 = time.time()

    def one(i):
        d = Path(a.out) / f"chunks_{tag}" / f"part{i:02d}"; mydb = dbs.get()
        try:
            for attempt in range(2):
                try: s, st, rec = global_mapper(exe, env, mydb, fl.images, parts[i], d, cap); break
                except RuntimeError as e:
                    if attempt == 1: raise
                    print(f"chunk {i}: {e}; retrying", flush=True); shutil.rmtree(d, ignore_errors=True); time.sleep(30)
        finally: dbs.put(mydb)
        import pycolmap
        dirs = [x for x in d.iterdir() if x.is_dir() and (x / "images.bin").exists()]
        bestdir = max(dirs, key=lambda x: pycolmap.Reconstruction(str(x)).num_reg_images())
        print(f"chunk {i}: {rec.num_reg_images()}/{len(parts[i])} frames in {s:.0f} s", flush=True)
        return dict(i=i, seconds=s, stages=st, placed=rec.num_reg_images(), n=len(parts[i]), dir=bestdir, rec=rec)

    with ThreadPoolExecutor(a.jobs) as ex: res = list(ex.map(one, range(len(parts))))
    wall = time.time() - t0
    base = dict(kind="chunks", chunk=a.chunk, overlap=a.overlap, jobs=a.jobs, cap=cap, n_chunks=len(parts),
                chunk_wall_s=round(wall, 1), chunk_cpu_s=round(sum(r["seconds"] for r in res), 1),
                chunk_placed=[r["placed"] for r in res], chunk_sizes=[r["n"] for r in res])

    # cheap merge
    t1 = time.time(); W, fits, dis = stitch(fl, [r["rec"] for r in res]); tc = time.time() - t1
    # score like the other results: latency search and robust fit (the stitched poses are already in GPS metres)
    cf = fl.gps.fit({f: (w[0].T, w[1]) for f, w in W.items()})
    save(a.out, dict(base, label=f"cheap merge {tag}", merge_s=round(tc, 1), placed=len(W), of=len(fl.names),
                     gps={k: v for k, v in cf.items() if k != "sim"},
                     chunk_gps=[round(f["median"], 2) for f in fits], overlap_disagreement=dis,
                     vs_reference=compare(W, fl.ref_world), total_s=round(wall + tc, 1)))

    # full merge
    if a.full:
        tm, tb, merged, rec = full_merge(exe, env, [r["dir"] for r in res], Path(a.out) / f"full_{tag}", a.ba_iters)
        r, _ = score(fl, rec, f"full merge {tag}", tm + tb, {"model_merger": tm, "bundle_adjuster": tb},
                     dict(base, merged_before_ba=merged.num_reg_images(), total_s=round(wall + tm + tb, 1)))
        save(a.out, r)
        P = poses(merged); fit = fl.gps.fit(P)
        save(a.out, dict(base, label=f"merge without BA {tag}", placed=len(P),
                         gps=dict(median=round(fit["median"], 3), p90=round(fit["p90"], 3)),
                         vs_reference=compare(to_world(P, fit["sim"]), fl.ref_world)))


def main():
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    for n in ("caps", "chunks"):
        p = sub.add_parser(n); p.add_argument("--flight", required=True); p.add_argument("--step", type=int, required=True)
        p.add_argument("--out", required=True)
    c = sub.choices["caps"]; c.add_argument("--only"); c.add_argument("--first", type=int, default=0); c.add_argument("--n", type=int, default=0)
    c = sub.choices["chunks"]; c.add_argument("--chunk", type=int, default=1200); c.add_argument("--overlap", type=int, default=150)
    c.add_argument("--jobs", type=int, default=3); c.add_argument("--cap", default="default", choices=list(CAPS))
    c.add_argument("--full", action="store_true"); c.add_argument("--ba-iters", type=int, default=50)
    a = ap.parse_args(); Path(a.out).mkdir(parents=True, exist_ok=True)
    {"caps": cmd_caps, "chunks": cmd_chunks}[a.cmd](a)


if __name__ == "__main__":
    main()
