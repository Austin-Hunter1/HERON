"""Continue the full-merge bundle adjustment for more iterations and score each result.

    python tools/bench_ba_more.py <bench dir> <tag> <iterations>
"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import solve_bench as sb

out, tag, iters = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
fl = sb.Flight(r"C:\Users\Nathan\HERON Studio\flights\flight-2026-11-10-698ad8", 4)
exe, env = sb.colmap_exe()
src = out / f"full_c1200_o150_{tag}" / "ba"; dst = out / f"full_c1200_o150_{tag}" / f"ba_plus{iters}"
import shutil; shutil.rmtree(dst, ignore_errors=True); dst.mkdir(parents=True)
t = time.time()
sb.run([exe, "bundle_adjuster", "--input_path", src, "--output_path", dst,
        "--BundleAdjustment.refine_principal_point", 0, "--BundleAdjustmentCeres.use_gpu", 1,
        "--BundleAdjustmentCeres.max_num_iterations", iters], dst.parent / f"ba_plus{iters}.log", env)
s = time.time() - t
import pycolmap
r, _ = sb.score(fl, pycolmap.Reconstruction(str(dst)), f"full merge {tag} + {iters} more BA iterations", s,
                {"bundle_adjuster": s}, dict(kind="ba_more", tag=tag, extra_iters=iters))
sb.save(out, r)
