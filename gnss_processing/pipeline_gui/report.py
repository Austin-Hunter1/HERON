"""
Write the short PDF report of one pipeline run.

The report uses matplotlib only (`PdfPages`).  Why: matplotlib is already in
the notebook environment, so the report adds no package.  The report has:

1. A summary page: the collect, the run settings, the status of each step, and
   the errors.
2. One section for each step: its figures in a grid, then the text that
   `report.excerpts` in the config selects.

The full detail stays in the executed notebooks in the report folder.
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # no window: the report is written from a worker thread
import matplotlib.image as mpimg  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402

from .config import ReportConfig  # noqa: E402
from .runner import StepResult  # noqa: E402

MONO = "DejaVu Sans Mono"  # ships with matplotlib, and has the box-drawing characters
TEXT_SIZE_PT = 7.0
MARGIN_IN = 0.5
HEADER_IN = 0.6


@dataclass(frozen=True)
class ReportInfo:
    """The text of the summary page that does not come from the step results."""

    title: str
    fields: list[tuple[str, str]]


def _chars_per_line(cfg: ReportConfig) -> int:
    """How many monospace characters fit across the page."""
    width_pt = (cfg.page_size_in[0] - 2 * MARGIN_IN) * 72
    return max(40, int(width_pt / (TEXT_SIZE_PT * 0.6)))


def _lines_per_page(cfg: ReportConfig) -> int:
    """How many monospace lines fit down the page, below the header."""
    height_pt = (cfg.page_size_in[1] - 2 * MARGIN_IN - HEADER_IN) * 72
    return max(10, int(height_pt / (TEXT_SIZE_PT * 1.3)))


def _wrap(lines: list[str], width: int) -> list[str]:
    """Wrap long lines; keep empty lines and the indent of table rows."""
    wrapped: list[str] = []
    for line in lines:
        if len(line) <= width:
            wrapped.append(line)
        else:
            wrapped.extend(textwrap.wrap(line, width, subsequent_indent="    ", drop_whitespace=False))
    return wrapped


def _new_page(cfg: ReportConfig, heading: str):
    """A blank page with a heading line and a footer line."""
    fig = plt.figure(figsize=cfg.page_size_in)
    width, height = cfg.page_size_in
    fig.text(MARGIN_IN / width, 1 - MARGIN_IN / height, heading,
             fontsize=13, fontweight="bold", va="top", ha="left")
    fig.text(1 - MARGIN_IN / width, MARGIN_IN / 2 / height,
             f"HERON gnss_processing pipeline report  |  {datetime.now():%Y-%m-%d %H:%M}",
             fontsize=7, color="0.4", ha="right", va="bottom")
    return fig


def _text_pages(pdf: PdfPages, cfg: ReportConfig, heading: str, lines: list[str]) -> None:
    """Write monospace lines, on as many pages as they need."""
    lines = _wrap(lines, _chars_per_line(cfg))
    per_page = _lines_per_page(cfg)
    width, height = cfg.page_size_in
    top = 1 - (MARGIN_IN + HEADER_IN) / height
    for page, first in enumerate(range(0, max(len(lines), 1), per_page)):
        fig = _new_page(cfg, heading if page == 0 else f"{heading} (continued)")
        fig.text(MARGIN_IN / width, top, "\n".join(lines[first:first + per_page]),
                 family=MONO, fontsize=TEXT_SIZE_PT, va="top", ha="left", linespacing=1.3)
        pdf.savefig(fig, dpi=cfg.dpi)
        plt.close(fig)


def _figure_pages(pdf: PdfPages, cfg: ReportConfig, heading: str,
                  figures: list[Path], grid: tuple[int, int]) -> None:
    """Place figure images in a (columns, rows) grid, as many pages as needed."""
    columns, rows = grid
    per_page = columns * rows
    width, height = cfg.page_size_in
    left, right = MARGIN_IN / width, 1 - MARGIN_IN / width
    top, bottom = 1 - (MARGIN_IN + HEADER_IN) / height, MARGIN_IN / height
    cell_w, cell_h = (right - left) / columns, (top - bottom) / rows
    caption_h = 0.25 / height

    for page, first in enumerate(range(0, len(figures), per_page)):
        fig = _new_page(cfg, heading if page == 0 else f"{heading} (continued)")
        for k, path in enumerate(figures[first:first + per_page]):
            col, row = k % columns, k // columns
            x0 = left + col * cell_w
            y0 = top - (row + 1) * cell_h
            ax = fig.add_axes([x0 + 0.01, y0 + caption_h, cell_w - 0.02, cell_h - caption_h - 0.01])
            ax.axis("off")
            try:
                ax.imshow(mpimg.imread(path))
            except Exception as exc:  # a broken image must not stop the report
                ax.text(0.5, 0.5, f"cannot read image:\n{exc}", ha="center", va="center", fontsize=7)
            fig.text(x0 + cell_w / 2, y0 + caption_h * 0.3, path.stem,
                     fontsize=7, ha="center", va="bottom", color="0.3")
        pdf.savefig(fig, dpi=cfg.dpi)
        plt.close(fig)


def split_figures(figures: list[Path], per_satellite_pattern: str) -> tuple[list[Path], list[Path]]:
    """
    Split figures into (general, per-satellite).

    Notebook 01 draws three figures for every satellite.  They go in a denser
    grid, so a ten-satellite run does not fill thirty pages.
    """
    pattern = re.compile(per_satellite_pattern)
    general = [p for p in figures if not pattern.search(p.stem)]
    per_sat = [p for p in figures if pattern.search(p.stem)]
    # Group per-satellite figures by plot type, then by satellite.
    per_sat.sort(key=lambda p: (pattern.sub("", p.stem), p.stem))
    return general, per_sat


def summary_lines(info: ReportInfo, results: list[StepResult]) -> list[str]:
    """The text of the summary page(s)."""
    lines: list[str] = []
    key_width = max((len(k) for k, _ in info.fields), default=0)
    lines += [f"{k.ljust(key_width)}  {v}" for k, v in info.fields]
    lines += ["", "Steps", "-----"]
    lines.append(f"{'step':<14}{'status':<12}{'minutes':>8}{'figures':>9}  note")
    for r in results:
        lines.append(
            f"{r.step.label:<14}{r.status:<12}{r.duration_s / 60:>8.1f}{len(r.figures):>9}  {r.message}"
        )
    failed = [r for r in results if r.errors]
    if failed:
        lines += ["", "Errors", "------"]
        for r in failed:
            lines.append(f"[{r.step.label}]")
            for error in r.errors:
                lines += ["  " + e for e in error.splitlines()]
            lines.append("")
    return lines


def build_report(path: Path, info: ReportInfo, results: list[StepResult], cfg: ReportConfig) -> Path:
    """Write the PDF to `path`, and return `path`."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with PdfPages(path) as pdf:
        _text_pages(pdf, cfg, info.title, summary_lines(info, results))
        for r in results:
            general, per_sat = split_figures(r.figures, cfg.per_satellite_pattern)
            if general:
                _figure_pages(pdf, cfg, r.step.title, general, cfg.figure_grid)
            if per_sat:
                _figure_pages(pdf, cfg, f"{r.step.title}: each satellite", per_sat, cfg.per_satellite_grid)
            for heading, text in r.excerpts.items():
                _text_pages(pdf, cfg, f"{r.step.title}: {heading.lstrip('# ')}", text.splitlines())
        meta = pdf.infodict()
        meta["Title"] = info.title
        meta["Creator"] = "gnss_processing pipeline_gui"
    return path
