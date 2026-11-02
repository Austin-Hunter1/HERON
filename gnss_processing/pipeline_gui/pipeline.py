"""
One complete run: make the report folder, run the steps, write the report.

The window and the command line both call `run_and_report`, so the two give
the same result.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .config import REPO_ROOT, PipelineConfig
from .plan import RunRequest, Step, build_steps
from .report import ReportInfo, build_report
from .runner import PASSED, RunContext, StepResult, notebook_figures_dir, run_pipeline

# `import utils` must find this repository's utils/, not gps-tracking-example's
# installed copy (see scripts/run_notebook.py, inject_sys_path_cell).
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@dataclass(frozen=True)
class RunOutcome:
    """Where the run wrote its results, and how each step ended."""

    report_dir: Path
    report_pdf: Path
    results: list[StepResult]

    @property
    def all_passed(self) -> bool:
        return all(r.status == PASSED for r in self.results)


def list_channels(collects_dir: Path):
    """Every channel under `collects_dir`, from `utils.catalog` (imported late, it is slow)."""
    from utils import catalog

    return catalog.list_channels(collects_dir)


def available_signals(names: list[str]) -> set[str]:
    """
    The names that `utils.signal_interfaces` can build on this machine.

    Why: `GpsL1C` is `None` when the pinned gnss-tools cannot make its codes.
    The window shows such a signal as not available instead of failing later
    inside notebook 01.
    """
    from utils import signal_interfaces

    return {name for name in names if getattr(signal_interfaces, name, None) is not None}


def outputs_root(cfg: PipelineConfig) -> Path:
    """The config's `outputs_dir`, or OUTPUTS_PATH from `.env`."""
    if cfg.outputs_dir is not None:
        return cfg.outputs_dir
    from utils import environment_variables

    return environment_variables.get_outputs_path()


def default_collects_dir(cfg: PipelineConfig) -> Path | None:
    """The config's `collects_dir`, or COLLECTS_PATH from `.env`, or None."""
    if cfg.collects_dir is not None:
        return cfg.collects_dir
    from utils import environment_variables

    try:
        return environment_variables.get_collects_path()
    except ValueError:
        return None


def make_report_dir(reports_root: Path, experiment_name: str, collect_id: str, band_id: str) -> Path:
    """A new folder for this run.  The time stamp keeps earlier runs."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = Path(reports_root) / experiment_name / f"{collect_id}_{band_id}_{stamp}"
    folder.mkdir(parents=True, exist_ok=False)
    return folder


def _channel_fields(channel, collects_dir: Path, request: RunRequest) -> list[tuple[str, str]]:
    """The collect and settings lines of the summary page."""
    from utils import catalog

    reference = (
        ", ".join(f"{v:g}" for v in request.reference_geo)
        if request.reference_geo else "notebook 02 default"
    )
    return [
        ("collects folder", str(collects_dir)),
        ("experiment", f"{channel.experiment_name}  (exp {channel.experiment_index})"),
        ("collect / band", f"{channel.collect_id} / {channel.band_id}  (ch {channel.channel_index})"),
        ("file", str(channel.filepath)),
        ("sample rate", f"{channel.samp_rate / 1e6:.3f} Msps"),
        ("format", catalog.sample_format_label(channel.sample_params)),
        ("duration", f"{channel.duration_s:,.1f} s ({channel.size_bytes / 1e9:.2f} GB)"),
        ("notebooks", ", ".join(request.notebooks)),
        ("signals", ", ".join(request.signals) or "-"),
        ("start offset", f"{request.start_offset_ms:,.0f} ms"),
        ("track duration", f"{request.track_duration_ms / 1e3:,.0f} s"),
        ("reference position", reference),
    ]


def run_and_report(
    cfg: PipelineConfig,
    collects_dir: Path,
    channel,
    request: RunRequest,
    log: Callable[[str], None] = print,
    should_stop: Callable[[], bool] = lambda: False,
    on_step: Callable[[int, int, Step], None] = lambda i, n, s: None,
) -> RunOutcome:
    """
    Run the request on `channel` (a `utils.catalog.Channel`), then write the report.

    The report is written also when steps fail or the user stops the run: a
    report that shows the failure is more useful than no report.
    """
    steps = build_steps(request, cfg.navigation_unsupported)
    reports_root = outputs_root(cfg) / cfg.reports_subdir
    report_dir = make_report_dir(reports_root, channel.experiment_name, channel.collect_id, channel.band_id)
    log(f"Report folder: {report_dir}")

    settings = {
        "created": datetime.now().isoformat(timespec="seconds"),
        "collects_dir": str(collects_dir),
        "experiment": channel.experiment_name,
        "collect_id": channel.collect_id,
        "band_id": channel.band_id,
        "request": asdict(request),
        "steps": [{"label": s.label, "notebook": s.notebook, "overrides": s.overrides} for s in steps],
    }
    (report_dir / "run_settings.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")

    ctx = RunContext(
        collects_dir=Path(collects_dir),
        figures_dir=notebook_figures_dir(collects_dir, channel.experiment_name, channel.collect_id),
        report_dir=report_dir,
        timeout_s=cfg.cell_timeout_s,
        excerpt_headings=cfg.report.excerpts,
        max_excerpt_lines=cfg.report.max_excerpt_lines,
        python=cfg.notebook_python,
        kernel=cfg.notebook_kernel,
    )
    results = run_pipeline(
        steps, ctx, log, should_stop, on_step=lambda i, s: on_step(i, len(steps), s)
    )

    info = ReportInfo(
        title=f"{channel.collect_id} / {channel.band_id}",
        fields=_channel_fields(channel, Path(collects_dir), request),
    )
    pdf = build_report(report_dir / "report.pdf", info, results, cfg.report)
    log(f"Report: {pdf}")
    return RunOutcome(report_dir=report_dir, report_pdf=pdf, results=results)
