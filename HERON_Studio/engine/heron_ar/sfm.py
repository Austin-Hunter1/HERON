"""Structure-from-Motion on the GoPro clip with COLMAP (pycolmap).

  1. extract()  - pull frames (every `frame_step`-th) from the clip, downscaled to `work_width`,
                  plus a mask that hides the black EIS border strips and the top edge.
                  Writes extract.json so a later run can reuse the frames: a Quick solve
                  (every 12th) after a Full extract (every frame) just picks a subset.
  2. solve()    - SIFT features -> sequential matching (video order, `sequential_overlap`
                  neighbours) -> mapping. The fisheye camera starts from the Gyroflow lens
                  profile and its focal length and distortion are refined by BA.

Mapping uses COLMAP's global mapper (GLOMAP) by default: it places all frames at once and is
far faster on long clips than the incremental mapper, which adds one frame at a time and
re-runs a global bundle adjustment as the model grows (mostly single-threaded; a Full solve
of a 9-minute clip took days). Set sfm.mapper = "incremental" to get the old behaviour.

The features/matches database is kept between runs (db_s<step>.db, keyed on the settings in
db_s<step>.json), so a re-run or a crash during mapping doesn't redo extraction and matching.

GPU: the pycolmap wheel is CPU-only. If a COLMAP 4.x program is found (sfm.colmap_exe,
HERON_COLMAP, `colmap` on PATH, or the release unzipped to ~/HERON Studio/tools/colmap-<ver>/)
and sfm.gpu isn't "off", feature extraction and matching
run through it with use_gpu=1. A no-CUDA COLMAP build uses OpenGL (SiftGPU), which works on
AMD and Intel GPUs for extraction (its matcher failed on an RX 5700 XT); a CUDA build needs NVIDIA
and also runs matching, global positioning and bundle adjustment on the GPU, so mapping goes
through the program too. Any failure falls back to pycolmap on the CPU. Rotation averaging and
track building are CPU-only in COLMAP whichever build is used.

Long solves can be split into overlapping chunks (sfm.chunk_size frames, sfm.chunk_overlap shared
with the next chunk), mapped by sfm.chunk_jobs COLMAP processes at once. Mapping time grows roughly
with the square of the frame count, so six chunks of 1,100 cost far less than one solve of 5,900.
They are then merged:
  * cheap (default): no joint adjustment. Each chunk is tied to GPS on its own in georef.py and the poses are
    stitched (sparse/chunks.json lists the chunk models). Seconds instead of hours; chunk-to-chunk
    consistency comes from GPS only. Tested best: about 1 m from GPS everywhere on the 19 Oct and
    10 Nov flights, where the full merge bent 6-145 m at the flight ends.
  * full: model_merger joins neighbouring chunks through their shared frames, then one bundle
    adjustment of the whole model (GPS positions as soft priors when sfm.merge_gps is on, see
    gps_ba.py). Result in sparse/0 like a single solve.
sfm.track_cap limits the tracks the global mapper keeps per frame (track_required_tracks_per_view).

Image names are f_<clip frame index>.jpg, which georef.py uses to recover each image's
video time. Result: <work>/sparse/<model id>/ (cameras.bin, images.bin, points3D.bin).
"""
from __future__ import annotations
import json, os, re, shutil, subprocess, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np, cv2

FPS = 24000 / 1001

def _say(msg):
    print(msg, flush=True)

def extract(video, work, t0, t1, step=12, width=960, log=print, fps=FPS):
    work = Path(work); d = work / "images"; m = work / "masks"
    for p in (d, m):                                   # never mix frames from an older extract
        if p.exists(): shutil.rmtree(p)
    d.mkdir(parents=True); m.mkdir()
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened(): raise FileNotFoundError(video)
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)); h = int(round(width * H / W))
    mk = np.full((h, width), 255, np.uint8); e = int(width * 0.05); mk[:, :e] = 0; mk[:, width - e:] = 0; mk[: int(h * 0.075), :] = 0
    f0, f1 = int(t0 * fps), int(t1 * fps); cap.set(cv2.CAP_PROP_POS_FRAMES, f0); n = 0
    for f in range(f0, f1):
        ok, fr = cap.read()
        if not ok: break
        if (f - f0) % step: continue
        name = f"f_{f:06d}.jpg"
        cv2.imwrite(str(d / name), cv2.resize(fr, (width, h), interpolation=cv2.INTER_AREA), [cv2.IMWRITE_JPEG_QUALITY, 93])
        cv2.imwrite(str(m / (name + ".png")), mk); n += 1
    (work / "extract.json").write_text(json.dumps(dict(step=step, width=width, f0=f0, f1=f1, n=n)))
    log(f"extracted {n} frames -> {d}")
    return n

def extracted(work):
    """What's already in <work>/images: dict(step, width, f0, names) or None."""
    work = Path(work); d = work / "images"
    names = sorted(p.name for p in d.glob("f_*.jpg")) if d.exists() else []
    if not names: return None
    meta = work / "extract.json"
    if meta.exists():
        m = json.loads(meta.read_text())
    else:                                              # extracted before extract.json existed
        idx = [int(n[2:8]) for n in names]
        img = cv2.imread(str(d / names[0]), cv2.IMREAD_REDUCED_COLOR_2)
        m = dict(step=int(np.min(np.diff(idx))) if len(idx) > 1 else 1, width=img.shape[1] * 2 if img is not None else None, f0=idx[0])
    m["names"] = names
    return m

def select(meta, step):
    """Every `step`-th clip frame out of an extract made at meta['step'], or None if it can't."""
    if step % meta["step"]: return None
    return [n for n in meta["names"] if (int(n[2:8]) - meta["f0"]) % step == 0]

def initial_camera(lens_json, width, focal_scale=1.0):
    d = json.loads(Path(lens_json).read_text())
    K = np.array(d["fisheye_params"]["camera_matrix"], float); D = d["fisheye_params"]["distortion_coeffs"]
    s = width / d["calib_dimension"]["w"]
    return [K[0, 0] * s * focal_scale, K[1, 1] * s * focal_scale, K[0, 2] * s, K[1, 2] * s, *D]

# ---------------------------------------------------------------- COLMAP program (GPU)
def find_colmap(explicit=None):
    """Path to a COLMAP program, or None. Accepts an exe/bat or the unzipped release folder."""
    home = Path(os.environ.get("HERON_STUDIO_HOME") or Path.home() / "HERON Studio") / "tools"
    # e.g. tools/colmap-4.2.1-cuda/, tools/colmap-4.2.1/: a CUDA build first (needs an NVIDIA GPU, else
    # COLMAP itself falls back to the CPU), then the newest version
    unzipped = sorted(home.glob("colmap*/"), key=lambda p: ("cuda" in p.name.lower(), p.name), reverse=True) if home.exists() else []
    for c in (explicit, os.environ.get("HERON_COLMAP"), shutil.which("colmap"), shutil.which("COLMAP.bat"), *unzipped):
        if not c: continue
        p = Path(c)
        if p.is_dir():
            for sub in ("COLMAP.bat", "bin/colmap.exe", "colmap.exe", "bin/colmap", "colmap"):
                if (p / sub).exists(): return p / sub
        elif p.exists(): return p
    return None

def _flag(help_text, *names):
    for n in names:
        if f"--{n}" in help_text: return f"--{n}"
    return None

class ColmapGPU:
    """Feature extraction + sequential matching through the COLMAP program with use_gpu=1."""
    def __init__(self, exe):
        exe = Path(exe); self.env = dict(os.environ)
        if exe.suffix.lower() == ".bat" and (exe.parent / "bin" / "colmap.exe").exists():
            # Do what the Windows release's COLMAP.bat does, without cmd.exe re-parsing our arguments
            # (commas in camera_params, spaces in paths).
            root = exe.parent; exe = root / "bin" / "colmap.exe"
            self.env["PATH"] = str(root / "bin") + os.pathsep + self.env.get("PATH", "")
            self.env["QT_PLUGIN_PATH"] = str(root / "plugins") + os.pathsep + self.env.get("QT_PLUGIN_PATH", "")
        self.exe = exe
        txt = self._out([exe, "-h"])
        m = re.search(r"COLMAP (\d+)\.(\d+)", txt)
        self.version = (int(m.group(1)), int(m.group(2))) if m else None
        self.cuda = "with CUDA" in txt
        self.fx = self._out([exe, "feature_extractor", "-h"])
        self.mx = self._out([exe, "sequential_matcher", "-h"])

    def _out(self, cmd):
        r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, errors="replace", timeout=60, env=self.env)
        return r.stdout + r.stderr

    def usable(self):
        # pycolmap 4 reads the database next; a COLMAP 3.x database has a different layout.
        return self.version is not None and self.version >= (4, 0) and "--database_path" in self.fx

    def _stream(self, cmd, log=None):
        """Run, echo the output (or write it to `log`), return (exit code, COLMAP error lines). COLMAP can
        log an error and still exit 0. Lines saying a stage is done are always echoed."""
        p = subprocess.Popen([str(c) for c in cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace", env=self.env)
        errs = []; lf = open(log, "w", encoding="utf-8", errors="replace") if log else None
        try:
            for line in p.stdout:
                line = line.rstrip()
                if lf: lf.write(line + "\n")
                if re.match(r"^[EF]\d{8} ", line): errs.append(line)
                if not lf or errs and errs[-1] == line: print(line, flush=True)
            return p.wait(), errs
        finally:
            if lf: lf.close()

    def extract(self, db, images, list_path, masks, cam_params, max_features, n):
        g = _flag(self.fx, "FeatureExtraction.use_gpu", "SiftExtraction.use_gpu")
        mf = _flag(self.fx, "SiftExtraction.max_num_features", "FeatureExtraction.max_num_features")
        cmd = [self.exe, "feature_extractor", "--database_path", db, "--image_path", images, "--image_list_path", list_path,
               "--ImageReader.camera_model", "OPENCV_FISHEYE", "--ImageReader.single_camera", "1",
               "--ImageReader.camera_params", cam_params, "--ImageReader.mask_path", masks]
        if mf: cmd += [mf, str(max_features)]
        if g: cmd += [g, "1"]
        rc, errs = self._stream(cmd)
        return rc == 0 and _db_count(db, "keypoints") >= n

    def match(self, db, overlap):
        # The OpenGL (SiftGPU) matcher fails to allocate on some drivers, e.g. AMD RX 5700 XT on
        # Adrenalin 32.0: "Not enough GPU memory to match N features" at any N, exit code 0.
        g = _flag(self.mx, "FeatureMatching.use_gpu", "SiftMatching.use_gpu")
        cmd = [self.exe, "sequential_matcher", "--database_path", db,
               "--SequentialMatching.overlap", str(overlap), "--SequentialMatching.loop_detection", "0"]
        if g: cmd += [g, "1"]
        rc, errs = self._stream(cmd)
        return rc == 0 and not any("Failed to create feature matcher" in e for e in errs) and _db_count(db, "two_view_geometries") > 0

    def map(self, kind, db, images, out, list_path, extra=None, quiet=False):
        """Mapping in the COLMAP program, for its GPU bundle adjustment (and GPU global positioning for
        the global mapper). Only worth it with a CUDA build. Returns True if a model was written."""
        cmd_name, P = ("global_mapper", "GlobalMapper.") if kind == "global" else ("mapper", "Mapper.")
        h = self._out([self.exe, cmd_name, "-h"])
        opts = {"image_list_path": list_path, "ba_refine_principal_point": 0, "ba_refine_focal_length": 1, "ba_refine_extra_params": 1}
        if kind == "global": opts.update(gp_use_gpu=1, ba_ceres_use_gpu=1)
        else: opts.update(ba_use_gpu=1, ba_global_frames_ratio=1.4, ba_global_points_ratio=1.4, ba_global_max_num_iterations=30)
        opts.update(extra or {})
        cmd = [self.exe, cmd_name, "--database_path", db, "--image_path", images, "--output_path", out]
        for k, v in opts.items():
            f = _flag(h, P + k)
            if f: cmd += [f, str(v)]
            elif k == "image_list_path": return False        # can't limit it to this solve's frames
        rc, errs = self._stream(cmd, log=Path(out) / "mapper.log" if quiet else None)
        return rc == 0 and any((d / "images.bin").exists() for d in Path(out).iterdir() if d.is_dir())

    def run(self, args, log):
        """Any other COLMAP command; output to a log file. True on success."""
        rc, errs = self._stream([self.exe, *args], log=log)
        return rc == 0

def _load_models(sparse):
    import pycolmap
    return {int(d.name): pycolmap.Reconstruction(str(d)) for d in Path(sparse).iterdir()
            if d.is_dir() and d.name.isdigit() and (d / "images.bin").exists()}

def _db_count(db, table):
    """Rows with data in a COLMAP database table (keypoints: images with features; two_view_geometries: matched pairs)."""
    import sqlite3
    try:
        with sqlite3.connect(str(db)) as c: return c.execute(f"SELECT COUNT(*) FROM {table} WHERE rows > 0").fetchone()[0]
    except sqlite3.Error:
        return 0

# ---------------------------------------------------------------- solve
def _db_for(work, step, key):
    db = Path(work) / f"db_s{step}.db"; kf = db.with_suffix(".json")
    old = json.loads(kf.read_text()) if kf.exists() else None
    if db.exists() and old != key:
        _say(f"settings changed since {db.name} was made; starting it over")
        for p in (db, Path(str(db) + "-wal"), Path(str(db) + "-shm")): p.unlink(missing_ok=True)
    kf.write_text(json.dumps(key, indent=1))
    return db

def _clear(d):
    if d.exists(): shutil.rmtree(d)
    d.mkdir(parents=True)

def _incremental_options(pycolmap, names, threads=-1):
    op = pycolmap.IncrementalPipelineOptions()
    op.image_names = list(names); op.num_threads = threads
    op.ba_refine_principal_point = False; op.ba_refine_extra_params = True; op.ba_refine_focal_length = True
    # Fewer global bundle adjustments: by default one runs every time the model grows 10 %,
    # which is what makes a long incremental solve crawl. Local BA still runs every frame.
    op.ba_global_frames_ratio = 1.4; op.ba_global_points_ratio = 1.4
    op.ba_global_max_num_iterations = 30
    return op

def _global_options(pycolmap, names, threads=-1):
    go = pycolmap.GlobalPipelineOptions()
    go.image_names = list(names); go.num_threads = threads
    ba = go.mapper.bundle_adjustment
    ba.refine_principal_point = False; ba.refine_extra_params = True; ba.refine_focal_length = True
    return go

def _write_models(recs, sparse):
    # global_mapping returns the models; write them the same way incremental_mapping does (sparse/<id>/).
    for k, r in recs.items():
        d = sparse / str(k)
        if not (d / "images.bin").exists():
            d.mkdir(parents=True, exist_ok=True); r.write(str(d))

def chunk_names(names, size, overlap):
    """Split frames into overlapping chunks of about `size` (each shares `overlap` frames with the next)."""
    if not size or len(names) <= 1.5 * size: return [list(names)]
    n = int(np.ceil((len(names) - overlap) / (size - overlap))); stride = (len(names) - overlap) / n
    starts = [int(round(i * stride)) for i in range(n)]
    return [list(names[s:(starts[i + 1] + overlap if i + 1 < n else len(names))]) for i, s in enumerate(starts)]

def _largest_dir(d):
    import pycolmap
    best = None
    for x in Path(d).iterdir():
        if x.is_dir() and x.name.isdigit() and (x / "images.bin").exists():
            k = pycolmap.Reconstruction(str(x)).num_reg_images()
            if best is None or k > best[0]: best = (k, x)
    return best

def solve_chunks(prog, db, images, names, work, size, overlap, jobs, extra, merge, log=_say, ba_iters=50, gps_cfg=None):
    """Map overlapping chunks in parallel, then merge them (see the module notes). Returns the sparse dir."""
    import pycolmap
    work = Path(work); sparse = work / "sparse"; cdir = work / "chunks"; _clear(cdir)
    parts = chunk_names(names, size, overlap); jobs = max(1, min(int(jobs or 1), len(parts)))
    if not (prog and prog.cuda) and jobs > 1: jobs = 1            # pycolmap in-process: one at a time
    log(f"{len(names):,} frames in {len(parts)} chunks of ~{len(parts[0]):,} ({overlap} shared), {jobs} at a time, "
        f"{merge} merge" + (f", {extra}" if extra else ""))
    done = []; t0 = time.time()
    # COLMAP processes sharing one SQLite database crash at start ("database is locked") while another
    # is reading it, so each parallel job maps from its own copy. Copies are handed out from a pool.
    import queue
    dbs = queue.Queue(); dbs.put(Path(db))
    for k in range(1, jobs):
        c = cdir / f"db{k}.db"; cdir.mkdir(parents=True, exist_ok=True); shutil.copyfile(db, c); dbs.put(c)

    def one(i):
        out = cdir / f"part{i:02d}"; out.mkdir(parents=True)
        lst = out / "images.txt"; lst.write_text("\n".join(parts[i]) + "\n")
        ok = False; mydb = dbs.get()
        try:
            if prog:
                for attempt in range(2):
                    try: ok = prog.map("global", mydb, images, out, lst, extra=extra, quiet=True)
                    except Exception as e: log(f"chunk {i + 1}: COLMAP failed ({e})")
                    if ok: break
                    for x in out.iterdir():
                        if x.is_dir(): shutil.rmtree(x)
                    if attempt == 0: log(f"chunk {i + 1}: COLMAP failed, retrying"); time.sleep(30)
            if not ok:
                for x in out.iterdir():
                    if x.is_dir(): shutil.rmtree(x)
                recs = pycolmap.global_mapping(str(mydb), str(images), str(out), options=_global_options(pycolmap, parts[i], threads=-1))
                _write_models(recs, out)
        finally: dbs.put(mydb)
        best = _largest_dir(out)
        done.append(i)
        log(f"chunk {i + 1}/{len(parts)}: {best[0] if best else 0:,} of {len(parts[i]):,} frames placed "
            f"({time.time() - t0:.0f} s since start)")
        _say(f"solve {45 + int(45 * len(done) / len(parts))}%")
        return best

    with ThreadPoolExecutor(jobs) as ex: res = list(ex.map(one, range(len(parts))))
    for k in range(1, jobs): (cdir / f"db{k}.db").unlink(missing_ok=True)
    good = [r[1] for r, part in zip(res, parts) if r and r[0] >= 0.3 * len(part)]
    if len(good) < len(parts): log(f"{len(parts) - len(good)} chunk(s) placed under 30 % of their frames and are left out")
    if not good: raise RuntimeError("no chunk of the solve worked")
    _clear(sparse)
    if merge == "full" and prog and len(good) > 1:
        mdir = cdir / "merge"; mdir.mkdir(); cur = good[0]; ok = True
        for i, nxt in enumerate(good[1:], 1):
            dst = mdir / f"m{i:02d}"; dst.mkdir()
            if not prog.run(["model_merger", "--input_path1", cur, "--input_path2", nxt, "--output_path", dst], mdir / f"m{i:02d}.log") \
                    or not (dst / "images.bin").exists():
                log(f"merging chunk {i + 1} failed (see {mdir / f'm{i:02d}.log'}); using the cheap merge instead"); ok = False; break
            cur = dst
        if ok:
            n = pycolmap.Reconstruction(str(cur)).num_reg_images()
            log(f"chunks merged: {n:,} frames; bundle adjustment of the whole model")
            ba = sparse / "0"; ba.mkdir(parents=True); t1 = time.time()
            if gps_cfg:            # GPS track as position priors: keeps the ends of the flight from bending
                try:
                    from .gps_ba import gps_bundle_adjust
                    gps_bundle_adjust(cur, ba, gps_cfg, iters=ba_iters, log=log); return sparse
                except Exception as e:
                    log(f"GPS-constrained bundle adjustment failed ({e}); plain bundle adjustment instead")
                    shutil.rmtree(ba, ignore_errors=True); ba.mkdir(parents=True)
            ok = prog.run(["bundle_adjuster", "--input_path", cur, "--output_path", ba,
                           "--BundleAdjustment.refine_principal_point", "0", "--BundleAdjustmentCeres.use_gpu", "1",
                           "--BundleAdjustmentCeres.max_num_iterations", str(int(ba_iters))], mdir / "ba.log") and (ba / "images.bin").exists()
            if ok:
                log(f"bundle adjustment done in {time.time() - t1:.0f} s"); return sparse
            log("bundle adjustment failed; using the merged model without it")
            shutil.rmtree(ba); shutil.copytree(cur, ba); return sparse
        _clear(sparse)
    elif merge == "full":
        log("full merge needs the COLMAP program; using the cheap merge")
    (sparse / "chunks.json").write_text(json.dumps(dict(chunks=[os.path.relpath(d, sparse) for d in good]), indent=1))
    log(f"cheap merge: {len(good)} chunk models listed in sparse/chunks.json, stitched in Tie to GPS")
    return sparse

def solve(work, lens_json, width=960, focal_scale=1.0, max_features=2500, overlap=12, names=None, step=1,
          mapper="global", gpu="auto", colmap_exe=None, log=_say, chunk_size=0, chunk_overlap=150, chunk_jobs=3,
          merge="full", track_cap=None, merge_ba_iters=50, gps_cfg=None):
    import pycolmap
    work = Path(work); images = work / "images"; sparse = work / "sparse"
    names = list(names) if names else sorted(p.name for p in images.glob("f_*.jpg"))
    if not names: raise RuntimeError(f"no frames in {images}")
    cam = ",".join(map(str, initial_camera(lens_json, width, focal_scale)))
    key = dict(width=width, focal_scale=focal_scale, max_features=max_features, overlap=overlap, lens=Path(lens_json).name,
               step=step, n=len(names), first=names[0], last=names[-1])
    db = _db_for(work, step, key)
    lst = work / "images.txt"; lst.write_text("\n".join(names) + "\n")
    log(f"solving {len(names):,} frames (every {step}{'st' if step == 1 else 'th'}), mapper: {mapper}")

    # -- features + matches: COLMAP program on the GPU if there is one, else pycolmap on the CPU
    prog = None
    if str(gpu).lower() not in ("off", "false", "0", "cpu"):
        exe = find_colmap(colmap_exe)
        if exe:
            try:
                prog = ColmapGPU(exe)
                if not prog.usable():
                    log(f"COLMAP at {exe} is version {prog.version or '?'}; need 4.x for GPU features, using the CPU"); prog = None
                else:
                    log(f"GPU features via {exe} (COLMAP {'.'.join(map(str, prog.version))}, {'CUDA' if prog.cuda else 'OpenGL'})")
            except Exception as e:
                log(f"couldn't run COLMAP at {exe} ({e}); using the CPU"); prog = None
        elif str(gpu).lower() == "on":
            log("sfm.gpu is on but no COLMAP program was found (set HERON_COLMAP); using the CPU")

    _say("solve 1%")
    if not (prog and prog.extract(db, images, lst, work / "masks", cam, max_features, len(names))):
        if prog: log("GPU feature extraction failed; finishing on the CPU")
        ext = pycolmap.FeatureExtractionOptions(); ext.sift.max_num_features = max_features
        rd = pycolmap.ImageReaderOptions(); rd.camera_model = "OPENCV_FISHEYE"; rd.camera_params = cam; rd.mask_path = str(work / "masks")
        pycolmap.extract_features(str(db), str(images), image_names=names, camera_mode=pycolmap.CameraMode.SINGLE,
                                  reader_options=rd, extraction_options=ext)
    _say("solve 25%")
    if not (prog and prog.match(db, overlap)):
        if prog: log("GPU matching failed; finishing on the CPU")
        mo = pycolmap.SequentialPairingOptions(); mo.overlap = overlap; mo.loop_detection = False
        pycolmap.match_sequential(str(db), pairing_options=mo)
    pairs = _db_count(db, "two_view_geometries")
    log(f"{_db_count(db, 'keypoints'):,} frames with features, {pairs:,} matched pairs")
    if not pairs: raise RuntimeError("no frame pairs matched; check the clip, the masks and the lens profile")
    _say("solve 45%")

    # -- mapping: in the COLMAP program on a CUDA build (GPU bundle adjustment), else pycolmap on the CPU
    extra = {"track_required_tracks_per_view": int(track_cap)} if track_cap else {}
    if mapper == "global" and len(chunk_names(names, chunk_size, chunk_overlap)) > 1:
        solve_chunks(prog, db, images, names, work, chunk_size, chunk_overlap, chunk_jobs, extra, merge, log, ba_iters=merge_ba_iters, gps_cfg=gps_cfg)
        _say("solve 99%")
        return sparse

    def run_mapper(kind):
        _clear(sparse)
        if prog and prog.cuda:
            log(f"{kind} mapping with GPU bundle adjustment (CUDA)")
            try:
                if prog.map(kind, db, images, sparse, lst, extra=extra if kind == "global" else None): return _load_models(sparse)
                log("GPU mapping didn't produce a model; mapping on the CPU")
            except Exception as e:
                log(f"GPU mapping failed ({e}); mapping on the CPU")
            _clear(sparse)
        if kind == "global":
            recs = pycolmap.global_mapping(str(db), str(images), str(sparse), options=_global_options(pycolmap, names))
            _write_models(recs, sparse); return recs
        return pycolmap.incremental_mapping(str(db), str(images), str(sparse), options=_incremental_options(pycolmap, names))

    recs = {}
    if mapper == "global":
        try:
            recs = run_mapper("global")
        except Exception as e:
            log(f"global mapper failed: {e}")
        best = max((r.num_reg_images() for r in recs.values()), default=0)
        if best < 0.5 * len(names):
            log(f"global mapper placed {best:,} of {len(names):,} frames; trying the incremental mapper")
            mapper = "incremental"
    if mapper == "incremental":
        recs = run_mapper("incremental")
    _say("solve 99%")
    log("models (id: registered images): " + str({k: r.num_reg_images() for k, r in recs.items()}))
    return sparse
