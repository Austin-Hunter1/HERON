"""Run the solve benchmarks once the flight currently processing is finished.
  python tools/bench_run.py <bench dir>
Progress: <bench dir>/run.log; results: <bench dir>/results.json."""
import subprocess, sys, time, re
from pathlib import Path

HERE = Path(__file__).resolve().parent
FL = Path.home() / "HERON Studio" / "flights"
OLD, NEW = FL / "flight-2026-11-05-a66811", FL / "flight-2026-11-10-698ad8"
out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
log = open(out / "run.log", "a", buffering=1, encoding="utf-8")


def say(m): log.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {m}\n")


def job_finished():
    txt = (NEW / "job.log").read_text(errors="replace")[-20000:]
    last = txt.rsplit("=== job started", 1)[-1]
    return re.search(r"^=== (done|cancelled)|failed \(exit|Traceback", last, re.M) is not None


say("waiting for the current Studio job to finish")
while not job_finished(): time.sleep(60)
say("job finished; starting benchmarks")

B = [sys.executable, str(HERE / "solve_bench.py")]
RUNS = [
    ("caps, new flight, first 1200 frames at step 4", B + ["caps", "--flight", NEW, "--step", "4", "--out", out, "--n", "1200"]),
    ("caps, old flight, 1062 frames at step 12", B + ["caps", "--flight", OLD, "--step", "12", "--out", out]),
    ("chunks 1200/150 x3, no cap, cheap + full merge", B + ["chunks", "--flight", NEW, "--step", "4", "--out", out,
                                                            "--chunk", "1200", "--overlap", "150", "--jobs", "3", "--full"]),
    ("chunks 1200/150 x3, cap 500, cheap + full merge", B + ["chunks", "--flight", NEW, "--step", "4", "--out", out,
                                                             "--chunk", "1200", "--overlap", "150", "--jobs", "3", "--cap", "tpv500", "--full"]),
]
for label, cmd in RUNS:
    say(f"start: {label}"); t = time.time()
    with open(out / "bench_stdout.log", "a", encoding="utf-8", errors="replace") as so:
        so.write(f"\n##### {label}\n"); so.flush()
        rc = subprocess.call([str(c) for c in cmd], stdout=so, stderr=subprocess.STDOUT)
    say(f"{'done' if rc == 0 else f'FAILED ({rc})'}: {label} in {(time.time() - t) / 60:.0f} min")
say("all benchmarks finished")
