"""Wait for bench_run.py to exit, then rerun the no-cap chunk test (it failed on a locked database)."""
import subprocess, sys, time
from datetime import datetime
from pathlib import Path

out = Path(sys.argv[1]); runner_pid = sys.argv[2]; here = Path(__file__).resolve().parent
log = lambda m: open(out / "run.log", "a").write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {m}\n")
while runner_pid in subprocess.run(["tasklist", "/FI", f"PID eq {runner_pid}"], capture_output=True, text=True).stdout:
    time.sleep(60)
for cap in ("default", "tpv500"):
    log(f"start: rerun chunks 1200/150 x3, cap {cap}, cheap + full merge"); t = time.time()
    with open(out / "bench_stdout.log", "a") as f:
        rc = subprocess.run([sys.executable, str(here / "solve_bench.py"), "chunks", "--flight",
                             r"C:\Users\Nathan\HERON Studio\flights\flight-2026-11-10-698ad8", "--step", "4", "--out", str(out),
                             "--chunk", "1200", "--overlap", "150", "--jobs", "3", "--cap", cap, "--full"],
                            stdout=f, stderr=subprocess.STDOUT).returncode
    log(("done" if rc == 0 else f"FAILED ({rc})") + f": rerun chunks cap {cap} in {(time.time() - t) / 60:.0f} min")
log("rerun finished")
