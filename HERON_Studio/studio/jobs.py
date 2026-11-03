"""Processing jobs: one flight at a time, each step a child process (or in-process for light ones).

Steps write to flights/<id>/job.log; the UI polls /api/flights/<id>/job. A job can be
cancelled (kills the running child) and restarted from any step; finished steps are skipped
unless asked.
"""
import json, os, signal, subprocess, sys, threading, time, traceback
from pathlib import Path
from . import store, ingest, water
from .config import ENGINE_DIR, APP_DIR, FLIGHTS_DIR

STEPS = [  # id, label, weight (rough share of total time), needs
    ("prepare", "Read files", 4, None),
    ("sync", "Sync clocks", 4, None),
    ("solve", "Solve the camera (COLMAP)", 45, None),
    ("georef", "Tie to GPS", 4, None),
    ("water", "Map the water", 14, "water"),
    ("reflections", "Reflections", 5, None),
    ("model3d", "3D model", 3, "model3d"),
    ("render", "Render AR video", 18, "ar_video"),
    ("exports", "Water exports + site package", 3, None),
]
_running = {}           # flight id -> Job
_queue_lock = threading.Lock()


def engine_cmd(*args):
    if getattr(sys, "frozen", False): return [sys.executable, "--engine", *args]
    return [sys.executable, str(ENGINE_DIR / "heron.py"), *args]


def task_cmd(*args):
    if getattr(sys, "frozen", False): return [sys.executable, "--task", *args]
    return [sys.executable, "-m", "studio.engine_tasks", *args]


class Job:
    def __init__(self, fid, start_at=None, only=None):
        self.fid, self.start_at, self.only = fid, start_at, only
        self.proc = None; self.cancelled = False; self.thread = threading.Thread(target=self.run, daemon=True)
        self.dir = FLIGHTS_DIR / fid; self.logf = self.dir / "job.log"

    # -------------------------------------------------------------- helpers
    def log(self, msg):
        with open(self.logf, "a") as f: f.write(msg.rstrip() + "\n")

    def set(self, **kw):
        def upd(f):
            j = f.get("job") or {}; j.update(kw); f["job"] = j
        return store.update(self.fid, upd)

    def env(self):
        e = dict(os.environ, HERON_DATA=str(self.dir / "data"), HERON_CACHE=str(self.dir / "cache"),
                 HERON_OUT=str(self.dir / "out"), HERON_WORK=str(self.dir / "work"), PYTHONUNBUFFERED="1",
                 PYTHONPATH=os.pathsep.join([str(ENGINE_DIR), str(APP_DIR), os.environ.get("PYTHONPATH", "")]))
        envj = self.dir / "engine_env.json"
        if envj.exists():
            t0 = json.loads(envj.read_text()).get("t0_us")
            if t0: e["HERON_T0_US"] = str(int(t0))
        return e

    def sh(self, cmd, step):
        self.log("$ " + " ".join(str(c) for c in cmd[1:]))
        self.proc = subprocess.Popen(cmd, cwd=str(APP_DIR), env=self.env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, bufsize=1, start_new_session=True)
        last = ""
        for line in self.proc.stdout:
            line = line.rstrip()
            if not line: continue
            g = _GLOG.match(line)                     # COLMAP's glog lines: drop the prefix and per-image chatter
            if g:
                line = g.group(1).strip()
                if line.startswith(_GLOG_SKIP): continue
            self.log(line); last = line
            p = _progress(step, line)
            if step == "solve" and "num_reg_frames=" in line:          # COLMAP: frames placed / frames extracted
                import re
                n = int(re.search(r"num_reg_frames=(\d+)", line).group(1))
                tot = sum(len(list(d.glob("*.jpg"))) for d in (self.dir / "work").glob("*/images")) or 1
                p = min(99, 100 * n // tot); line = f"{n:,} of {tot:,} frames placed"
            if p is not None: self.set(step_progress=p, detail=line[-160:])
        rc = self.proc.wait(); self.proc = None
        if self.cancelled: raise Cancelled()
        if rc != 0: raise RuntimeError(f"{step} failed (exit {rc}): {last[-300:]}")
        return last

    # -------------------------------------------------------------- run
    def run(self):
        f = store.load(self.fid); make = f["settings"]["make"]; cfg = str(self.dir / "config.json")
        done = set((f.get("job") or {}).get("done", [])) if self.start_at else set()
        steps = [s for s in STEPS if s[3] is None or make.get(s[3], True)]
        if self.only: steps = [s for s in steps if s[0] in self.only]
        ids = [s[0] for s in steps]
        if self.start_at in ids: done = {i for i in done if i in ids and ids.index(i) < ids.index(self.start_at)}   # rerun from there
        # start_at == "resume": keep every finished step
        total = sum(s[2] for s in steps)
        self.set(state="running", started=store.now(), steps=[dict(id=s[0], label=s[1]) for s in steps], done=sorted(done),
                 error=None, step=None, step_progress=0, finished=None)
        store.update(self.fid, lambda f: f.update(status="processing"))
        self.log(f"=== job started {store.now()}")
        try:
            for sid, label, w, _ in steps:
                if sid in done: continue
                t0 = time.time(); self.set(step=sid, step_progress=0, detail=label,
                                           progress=round(100 * sum(s[2] for s in steps if s[0] in done) / total))
                self.log(f"--- {label}")
                getattr(self, "do_" + sid)(cfg)
                done.add(sid); self.set(done=sorted(done), step_progress=100,
                                         **{f"t_{sid}": round(time.time() - t0, 1)})
            self.set(state="done", step=None, progress=100, finished=store.now())
            store.update(self.fid, lambda f: f.update(status="done"))
            self.log(f"=== done {store.now()}")
        except Cancelled:
            self.set(state="cancelled", finished=store.now()); store.update(self.fid, lambda f: f.update(status="ready"))
            self.log("=== cancelled")
        except Exception as e:
            self.log(traceback.format_exc()); self.set(state="error", error=str(e), finished=store.now())
            store.update(self.fid, lambda f: f.update(status="error"))
        finally:
            _running.pop(self.fid, None)

    def cancel(self):
        self.cancelled = True
        if self.proc:
            try: os.killpg(self.proc.pid, signal.SIGTERM)
            except Exception: self.proc.terminate()

    # -------------------------------------------------------------- steps
    def do_prepare(self, cfg):
        f = store.load(self.fid)
        c = ingest.prepare(f, log=self.log)
        store.update(self.fid, lambda g: g.update(telemetry_info=f.get("telemetry_info"), engine=dict(
            water_elevation_m=c["water_elevation_m"], render=[c["output_width"], c["output_height"]],
            solve_width=c["sfm"]["work_width"], frame_step=c["sfm"]["frame_step"])))
        ingest.proxy(f, log=self.log)

    def do_sync(self, cfg):
        last = self.sh(engine_cmd("sync", "--config", cfg, "--write"), "sync")
        s = json.loads(Path(cfg).read_text()).get("sync")
        store.update(self.fid, lambda f: f["results"].update(sync=dict(s or {}, report=last)))

    def do_solve(self, cfg):
        self.sh(engine_cmd("sfm", "--config", cfg), "solve")

    def do_georef(self, cfg):
        last = self.sh(engine_cmd("georef", "--config", cfg), "georef")
        store.update(self.fid, lambda f: f["results"].update(georef=last))

    def do_water(self, cfg):
        c = json.loads(Path(cfg).read_text()); mid = round(c["clip_duration_s"] / 2, 1)
        self.sh(engine_cmd("preview", "--config", cfg, "--t", str(mid)), "water")

    def do_reflections(self, cfg):
        self.sh(task_cmd("reflections", str(self.dir)), "reflections")

    def do_model3d(self, cfg):
        self.sh(task_cmd("model3d", str(self.dir)), "model3d")

    def do_render(self, cfg):
        self.sh(engine_cmd("render", "--config", cfg, "--out", str(self.dir / "out" / "HERON_AR.mp4")), "render")

    def do_exports(self, cfg):
        f = store.load(self.fid); s = water.export(self.dir, f); pkg = water.site_package(self.dir, f)
        store.update(self.fid, lambda g: g["results"].update(water=s, site_package=pkg.name))
        self.log(f"exports: {', '.join(s['files'])} + {pkg.name}")


class Cancelled(Exception): pass


import re as _re
_GLOG = _re.compile(r"^[IWEF]\d{8} [\d:.]+\s+\d+ \S+:\d+\] (.*)")
_GLOG_SKIP = ("Name:", "Dimensions:", "Camera:", "Focal Length:", "Features:", "Mask:", "=> Image sees", "=> Continued", "=> Merged",
              "=> Filtered", "=> Completed", "=> Changed", "Matching block", "in ")


def _progress(step, line):
    """Pull a 0-100 number out of engine output where it reports one."""
    import re
    m = re.search(r"(\d+)/(\d+)", line)
    if step in ("render", "water") and m and int(m.group(2)) > 0: return min(99, 100 * int(m.group(1)) // int(m.group(2)))
    m = re.search(r"(\d+)%", line)
    if m: return min(99, int(m.group(1)))
    m = re.search(r"[Rr]egistering image #?\d+ \((\d+)\)", line)
    if m: return None
    return None


def start(fid, start_at=None, only=None):
    with _queue_lock:
        if fid in _running: raise RuntimeError("already processing")
        if any(j.thread.is_alive() for j in _running.values()): raise RuntimeError("another flight is processing; wait or cancel it")
        (FLIGHTS_DIR / fid / "job.log").open("a").close()
        j = Job(fid, start_at, only); _running[fid] = j; j.thread.start(); return j


def cancel(fid):
    j = _running.get(fid)
    if j: j.cancel()


def log_tail(fid, n=80):
    p = FLIGHTS_DIR / fid / "job.log"
    if not p.exists(): return []
    with open(p, "rb") as f:
        f.seek(0, 2); size = f.tell(); f.seek(max(0, size - 24000)); return f.read().decode(errors="replace").splitlines()[-n:]


def recover():
    """App restarted mid-job: mark it interrupted so the user can resume."""
    for f in store.all_flights():
        if (f.get("job") or {}).get("state") == "running":
            def upd(g): g["job"]["state"] = "interrupted"; g["status"] = "error"; g["job"]["error"] = "Studio was closed while processing. Resume to carry on from the last finished step."
            store.update(f["id"], upd)
