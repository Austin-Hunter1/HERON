#!/usr/bin/env python3
"""
Patch a config cell of notebook 00/01/02 with parameter overrides, execute it
with nbconvert, and report text output plus any errors.

The notebooks are meant to be edited by hand and re-run in Jupyter; this script
does the same edit mechanically so it can be driven from the command line (or by
Claude) without opening a UI. It never touches the notebook in `notebooks/` --
it writes a patched copy next to the run output and executes that.

Usage:
    python scripts/run_notebook.py 00 --set EXPERIMENT_INDEX=1 --set CHANNEL_INDEX=0
    python scripts/run_notebook.py 01 --set SIGNAL_ID=GpsL1CA --set EXPERIMENT_INDEX=1 \\
        --set CHANNEL_INDEX=0 --set START_OFFSET_MS=0.0
    python scripts/run_notebook.py 02 --set SIGNAL_ID=GpsL1CA --set EXPERIMENT_INDEX=0 \\
        --set CHANNEL_INDEX=1

BeiDou (SIGNAL_ID is a class name, so pass BdsB2a or BdsB1C without quotes).  B2a is on
the L5 band and B1C is on the L1 band, so the channel index differs.  Run notebook 01
first; notebook 02 reads the tracking file it writes:
    python scripts/run_notebook.py 01 --set SIGNAL_ID=BdsB2a --set EXPERIMENT_INDEX=0 \\
        --set CHANNEL_INDEX=0
    python scripts/run_notebook.py 02 --set SIGNAL_ID=BdsB2a --set EXPERIMENT_INDEX=0 \\
        --set CHANNEL_INDEX=0
    python scripts/run_notebook.py 01 --set SIGNAL_ID=BdsB1C --set EXPERIMENT_INDEX=0 \\
        --set CHANNEL_INDEX=1
    python scripts/run_notebook.py 02 --set SIGNAL_ID=BdsB1C --set EXPERIMENT_INDEX=0 \\
        --set CHANNEL_INDEX=1
The indices above are examples.  Read the right ones from the catalog table that the
notebook prints (run it once with any index to see the table).

Each --set KEY=VALUE overwrites the right-hand side of the first top-level
assignment `KEY = ...` found in any code cell (VALUE is inserted as raw Python
source, so pass GpsL5 not "GpsL5", and 1 not "1"). A trailing inline comment on
that line is preserved.

The interpreter and the kernel default to the ones for the OS that is running
(see PLATFORM_DEFAULTS): the same command works in the Linux boot and in the
Windows boot.  For another environment, give them yourself:
    python scripts/run_notebook.py 01 --python <path to python.exe> --kernel <kernel name> ...

Requires the Jupyter kernel named in PLATFORM_DEFAULTS for this OS (see the
`run-gnss-notebooks` skill for how it is set up) and must run with this repo's
root as the working directory, so `import utils` resolves to this repo's own
utils/ package rather than gps-tracking-example's upstream copy.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
NOTEBOOKS = {
    "00": "00-raw-signal-diagnostics.ipynb",
    "01": "01-acquisition-and-tracking.ipynb",
    "02": "02-navigation.ipynb",
}
# Interpreter (relative to the repo that holds gps-tracking-example) and Jupyter
# kernel name for each OS.  One entry per OS keeps the Linux boot and the Windows
# boot on the same command line.  Change an entry here when an environment moves.
PLATFORM_DEFAULTS = {
    "linux": {
        "python": Path(".venv-linux") / "bin" / "python3",
        "kernel": "gnss_tracking_linux",
    },
    "win32": {
        "python": Path(".venv") / "Scripts" / "python.exe",
        "kernel": "gnss-processing",
    },
}


def platform_defaults(platform: str | None = None) -> tuple[str, str]:
    """
    Return `(python path, kernel name)` for the OS that is running.

    Why: the two boots of this machine have different virtual environments and
    kernel names.  Choosing here means nobody edits the command per OS.  An OS
    that has no entry is a clear error, not a silent guess.
    """
    platform = sys.platform if platform is None else platform
    for prefix, entry in PLATFORM_DEFAULTS.items():
        if platform.startswith(prefix):
            python = REPO_ROOT.parent / "gps-tracking-example" / entry["python"]
            return str(python), entry["kernel"]
    raise SystemExit(
        f"No default interpreter for platform {platform!r}. Pass --python and --kernel."
    )


RUNS_DIR = REPO_ROOT / "local-data" / "notebook_runs"


def inject_sys_path_cell(nb: dict) -> None:
    """
    Force `import utils` (and everything else at repo root) to resolve to this
    repo's own packages, not gps-tracking-example's editable-installed copy --
    which matters for notebooks 01/02, since signal_interfaces.py and
    tracking_supervisor.py genuinely differ between the two (see README.md).

    nbconvert's execute preprocessor runs the kernel with cwd set to the
    *notebook file's* own directory by default, not the repo root, so the
    cwd-shadowing that makes `import utils` work when a human runs these
    notebooks from an editor configured to use the repo root as cwd does not
    happen here. Prepending the repo root to sys.path sidesteps that --
    reliable regardless of nbconvert's cwd behavior or where the patched
    notebook copy happens to live on disk.
    """
    setup_cell = {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": [
            "import sys as _sys\n",
            f"_sys.path.insert(0, {str(REPO_ROOT)!r})\n",
        ],
    }
    nb["cells"].insert(0, setup_cell)


def patch_notebook(nb: dict, overrides: dict[str, str]) -> set[str]:
    applied: set[str] = set()
    for cell in nb["cells"]:
        if cell["cell_type"] != "code":
            continue
        new_lines = []
        for line in cell["source"]:
            for key, value in overrides.items():
                m = re.match(rf"^(\s*{re.escape(key)}\s*=\s*)(.*?)(\s*#.*)?(\n?)$", line)
                if m:
                    prefix, _old_value, comment, newline = m.groups()
                    line = f"{prefix}{value}{comment or ''}{newline}"
                    applied.add(key)
                    break
            new_lines.append(line)
        cell["source"] = new_lines
    return applied


def main() -> int:
    # Windows' console encoding is not UTF-8 by default. Notebook output can
    # contain characters (progress bars, em dashes) it cannot encode, which
    # would otherwise crash this script's own print() calls below.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("notebook", choices=sorted(NOTEBOOKS), help="Which notebook: 00, 01, or 02")
    parser.add_argument(
        "--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE",
        help="Override a top-level variable in the notebook's config cell. Repeatable.",
    )
    parser.add_argument("--timeout", type=int, default=1800, help="Per-cell execution timeout, seconds")
    default_python, default_kernel = platform_defaults()
    parser.add_argument("--kernel", default=default_kernel, help=f"Jupyter kernel name (default: {default_kernel})")
    parser.add_argument(
        "--python", default=default_python,
        help=f"Path to the python executable that runs nbconvert (default: {default_python})",
    )
    parser.add_argument("--out", default=None, help="Output .ipynb path (default: local-data/notebook_runs/<name>_<notebook>.ipynb)")
    args = parser.parse_args()

    overrides = {}
    for item in args.overrides:
        if "=" not in item:
            parser.error(f"--set expects KEY=VALUE, got: {item!r}")
        key, value = item.split("=", 1)
        overrides[key.strip()] = value.strip()

    src_path = REPO_ROOT / "notebooks" / NOTEBOOKS[args.notebook]
    nb = json.loads(src_path.read_text(encoding="utf-8"))

    inject_sys_path_cell(nb)
    applied = patch_notebook(nb, overrides)
    missing = set(overrides) - applied
    if missing:
        print(f"WARNING: these --set keys were not found in any code cell and were not applied: {sorted(missing)}", file=sys.stderr)

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out) if args.out else RUNS_DIR / f"{args.notebook}_last_run.ipynb"
    # Named after the output file, so two runs started at once do not overwrite
    # each other's patched copy (a shared name made parallel runs execute the
    # wrong overrides).
    patched_path = RUNS_DIR / f"{out_path.stem}.patched.ipynb"
    patched_path.write_text(json.dumps(nb, indent=1), encoding="utf-8")

    python = args.python

    # `-m nbconvert`, not `-m jupyter nbconvert`: the `jupyter` command starts a
    # separate launcher program, which some Windows policies block.
    cmd = [
        python, "-m", "nbconvert", "--to", "notebook", "--execute", "--allow-errors",
        f"--ExecutePreprocessor.timeout={args.timeout}",
        f"--ExecutePreprocessor.kernel_name={args.kernel}",
        "--output", str(out_path.resolve()),
        str(patched_path),
    ]
    # cwd=REPO_ROOT so `import utils` picks up this repo's own utils/ package
    # (see the module docstring) rather than the one installed from
    # gps-tracking-example's editable install.
    result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    print(result.stdout)
    print(result.stderr, file=sys.stderr)
    if result.returncode != 0:
        print(f"nbconvert failed (exit {result.returncode})", file=sys.stderr)
        return result.returncode

    out_nb = json.loads(out_path.read_text(encoding="utf-8"))
    error_count = 0
    for i, cell in enumerate(out_nb["cells"]):
        for out in cell.get("outputs", []):
            if out.get("output_type") == "error":
                error_count += 1
                print(f"\n=== ERROR in cell {i} ===", file=sys.stderr)
                print("".join(out.get("traceback", [])), file=sys.stderr)
            elif out.get("output_type") in ("stream", "execute_result") and "text" in out:
                print("".join(out["text"]))

    print(f"\nWrote: {out_path}")
    if error_count:
        print(f"{error_count} cell(s) raised an error.", file=sys.stderr)
        return 1
    print("No errors.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
