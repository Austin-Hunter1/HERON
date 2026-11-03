"""
Run the planned notebook steps one after the other, and keep what each made.

Each step calls `scripts/run_notebook.py` in a child process.  That script
already patches the config cell, fixes `sys.path` and runs nbconvert, so this
module does not repeat that logic.  This module adds four things:

1. It points the kernel at the chosen collects folder.  It sets `COLLECTS_PATH`
   in the child environment; a value set in the shell wins over `.env` (see
   `utils/environment_variables.py`).
2. It copies the figures of each step into the report folder.  All three
   notebooks write to the same `Figures/<collect_id>/` folder with fixed names,
   so the next signal would overwrite them.
3. It reads the executed notebook for errors and for text the report copies.
4. It can stop a running step, with all of its child processes.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .config import REPO_ROOT
from .plan import Step

RUN_NOTEBOOK_SCRIPT = REPO_ROOT / "scripts" / "run_notebook.py"
FIGURE_SUFFIXES = (".png", ".jpg", ".jpeg", ".svg")
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# Step status values, as the report shows them.
PASSED, ERROR, FAILED, SKIPPED, STOPPED = "passed", "cell error", "failed", "skipped", "stopped"


@dataclass
class StepResult:
    """What one step did.  The report reads only this."""

    step: Step
    status: str
    duration_s: float = 0.0
    executed_notebook: Path | None = None
    figures: list[Path] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    excerpts: dict[str, str] = field(default_factory=dict)
    message: str = ""


@dataclass(frozen=True)
class RunContext:
    """Where the step reads from and writes to, and how it runs the notebooks."""

    collects_dir: Path
    figures_dir: Path
    """`<collects_dir>/<experiment>/Figures/<collect_id>`: where the notebooks save figures."""
    report_dir: Path
    timeout_s: int
    excerpt_headings: dict[str, list[str]]
    max_excerpt_lines: int
    python: str | None = None
    kernel: str | None = None


def notebook_figures_dir(collects_dir: Path, experiment_name: str, collect_id: str) -> Path:
    """The folder that notebooks 00, 01 and 02 all set as `FIGURES_DIR`."""
    return Path(collects_dir) / experiment_name / "Figures" / collect_id


def strip_ansi(text: str) -> str:
    """Remove the terminal colour codes that IPython puts in a traceback."""
    return _ANSI.sub("", text)


# ----- Figures ---------------------------------------------------------------


def snapshot_figures(folder: Path) -> dict[str, tuple[int, int]]:
    """Name -> (modification time, size) of every figure file in `folder`."""
    if not folder.is_dir():
        return {}
    return {
        p.name: (p.stat().st_mtime_ns, p.stat().st_size)
        for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in FIGURE_SUFFIXES
    }


def new_or_changed_figures(folder: Path, before: dict[str, tuple[int, int]]) -> list[Path]:
    """
    Figure files that a step wrote.

    Why compare with a snapshot: the folder also holds figures from earlier runs,
    and a step that fails part way writes only some figures.  A file that is new,
    or that has a new time or size, came from this step.
    """
    after = snapshot_figures(folder)
    return sorted(folder / name for name, stamp in after.items() if before.get(name) != stamp)


def copy_figures(figures: list[Path], destination: Path) -> list[Path]:
    """Copy the figures into the report folder, and return the new paths."""
    destination.mkdir(parents=True, exist_ok=True)
    copied = []
    for source in figures:
        target = destination / source.name
        shutil.copy2(source, target)
        copied.append(target)
    return copied


# ----- Executed notebook -----------------------------------------------------


def _heading(cell: dict) -> tuple[int, str] | None:
    """(level, text) when a markdown cell starts with a heading line, else None."""
    if cell.get("cell_type") != "markdown":
        return None
    source = "".join(cell.get("source", [])).strip()
    first = source.splitlines()[0].strip() if source else ""
    match = re.match(r"^(#+)\s", first)
    return (len(match.group(1)), first) if match else None


def _cell_text(cell: dict) -> str:
    """
    The standard-output text of a code cell.

    Standard error is left out: it holds progress bars and warnings, which do
    not belong in a short report.
    """
    parts = []
    for out in cell.get("outputs", []):
        kind = out.get("output_type")
        if kind == "stream" and out.get("name") == "stdout":
            parts.append("".join(out.get("text", [])))
        elif kind == "execute_result":
            parts.append("".join(out.get("data", {}).get("text/plain", [])) + "\n")
    return "".join(parts)


def notebook_errors(nb: dict) -> list[str]:
    """One short text for each error output: the cell, the error, and the last frames."""
    errors = []
    for index, cell in enumerate(nb.get("cells", [])):
        for out in cell.get("outputs", []):
            if out.get("output_type") != "error":
                continue
            trace = strip_ansi("\n".join(out.get("traceback", []))).strip().splitlines()
            tail = "\n".join(trace[-6:])
            errors.append(
                f"cell {index}: {out.get('ename', 'Error')}: {strip_ansi(out.get('evalue', ''))}\n{tail}"
            )
    return errors


def notebook_excerpts(nb: dict, headings: list[str], max_lines: int) -> dict[str, str]:
    """
    The printed text of the sections that start at `headings`.

    A section runs from its heading to the next heading of the same or a higher
    level.  Why by heading and not by cell number: a cell number changes when a
    team member adds a cell; a heading changes much less.  A heading that is not
    found gives no entry, so the report stays correct when a notebook changes.
    """
    cells = nb.get("cells", [])
    excerpts: dict[str, str] = {}
    for wanted in headings:
        start = next(
            (i for i, c in enumerate(cells) if (h := _heading(c)) and h[1] == wanted.strip()),
            None,
        )
        if start is None:
            continue
        level = _heading(cells[start])[0]
        text = []
        for cell in cells[start + 1:]:
            h = _heading(cell)
            if h and h[0] <= level:
                break
            if cell.get("cell_type") == "code":
                text.append(_cell_text(cell))
        lines = strip_ansi("".join(text)).rstrip().splitlines()
        if not lines:
            continue
        if len(lines) > max_lines:
            hidden = len(lines) - max_lines
            lines = lines[:max_lines] + [f"... ({hidden} more lines in the saved notebook)"]
        excerpts[wanted] = "\n".join(lines)
    return excerpts


# ----- Child process ---------------------------------------------------------


def build_command(step: Step, ctx: RunContext, out_path: Path) -> list[str]:
    """The `run_notebook.py` command line for one step."""
    cmd = [sys.executable, str(RUN_NOTEBOOK_SCRIPT), step.notebook]
    for key, value in step.overrides.items():
        cmd += ["--set", f"{key}={value}"]
    cmd += ["--timeout", str(ctx.timeout_s), "--out", str(out_path)]
    if step.stop_before:
        cmd += ["--stop-before", step.stop_before]
    if ctx.python:
        cmd += ["--python", ctx.python]
    if ctx.kernel:
        cmd += ["--kernel", ctx.kernel]
    return cmd


def child_environment(collects_dir: Path) -> dict[str, str]:
    """
    The environment of the notebook process.

    `COLLECTS_PATH` makes the kernel read the folder chosen in the window.  The
    kernel inherits it through nbconvert.  UTF-8 output keeps the table
    characters readable in the log on Windows.
    """
    env = dict(os.environ)
    env["COLLECTS_PATH"] = str(collects_dir)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _kill_tree(process: subprocess.Popen) -> None:
    """Stop the process and its children (nbconvert and the kernel)."""
    if process.poll() is not None:
        return
    if sys.platform.startswith("win"):
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True, check=False,
        )
    else:
        import signal

        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def run_step(
    step: Step,
    ctx: RunContext,
    log: Callable[[str], None],
    should_stop: Callable[[], bool] = lambda: False,
) -> StepResult:
    """Run one step to the end (or until `should_stop`), and collect its results."""
    notebooks_dir = ctx.report_dir / "notebooks"
    notebooks_dir.mkdir(parents=True, exist_ok=True)
    out_path = notebooks_dir / f"{step.label}.ipynb"
    before = snapshot_figures(ctx.figures_dir)
    started = time.monotonic()

    cmd = build_command(step, ctx, out_path)
    log(f"$ {' '.join(cmd[1:])}")
    process = subprocess.Popen(
        cmd,
        cwd=REPO_ROOT,
        env=child_environment(ctx.collects_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=not sys.platform.startswith("win"),
    )

    # A reader thread, so the loop below can check for a stop request while
    # the child is silent (run_notebook.py prints only at the end).
    def _read() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            log(line.rstrip("\n"))

    reader = threading.Thread(target=_read, daemon=True)
    reader.start()
    stopped = False
    while process.poll() is None:
        if should_stop():
            stopped = True
            _kill_tree(process)
            break
        time.sleep(0.5)
    process.wait()
    reader.join(timeout=5)

    result = StepResult(step=step, status=PASSED, duration_s=time.monotonic() - started)
    changed = new_or_changed_figures(ctx.figures_dir, before)
    result.figures = copy_figures(changed, ctx.report_dir / "figures" / step.label)

    if out_path.is_file():
        result.executed_notebook = out_path
        nb = json.loads(out_path.read_text(encoding="utf-8"))
        result.errors = notebook_errors(nb)
        result.excerpts = notebook_excerpts(
            nb, ctx.excerpt_headings.get(step.notebook, []), ctx.max_excerpt_lines
        )

    if stopped:
        result.status, result.message = STOPPED, "Stopped by the user."
    elif result.errors:
        result.status = ERROR
        result.message = f"{len(result.errors)} cell(s) raised an error."
    elif process.returncode != 0:
        result.status = FAILED
        result.message = f"run_notebook.py ended with exit code {process.returncode}."
    return result


def run_pipeline(
    steps: list[Step],
    ctx: RunContext,
    log: Callable[[str], None],
    should_stop: Callable[[], bool] = lambda: False,
    on_step: Callable[[int, Step], None] = lambda i, s: None,
) -> list[StepResult]:
    """
    Run all steps in order.

    A step whose `depends_on` step did not pass is skipped: notebook 02 has no
    tracking file to read when notebook 01 failed.  A failed step does not stop
    the other signals, because one bad signal must not hide the good ones.
    """
    results: list[StepResult] = []
    status_by_label: dict[str, str] = {}
    for index, step in enumerate(steps):
        if should_stop():
            results.append(StepResult(step, STOPPED, message="Not started: the run was stopped."))
            continue
        if step.depends_on and status_by_label.get(step.depends_on) != PASSED:
            result = StepResult(step, SKIPPED, message=f"Skipped: {step.depends_on} did not pass.")
            log(f"--- {step.title}: skipped ({step.depends_on} did not pass)")
        else:
            on_step(index, step)
            log(f"=== {step.title}")
            result = run_step(step, ctx, log, should_stop)
            log(f"--- {step.title}: {result.status} in {result.duration_s / 60:.1f} min, "
                f"{len(result.figures)} figure(s)")
            for error in result.errors:
                log(error)
        status_by_label[step.label] = result.status
        results.append(result)
    return results
