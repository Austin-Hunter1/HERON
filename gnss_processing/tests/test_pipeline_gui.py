"""
Tests of the notebook pipeline window logic (D-078): the config loader, the step
plan, the figure and notebook readers, and the PDF report.  The window itself
and the real notebook runs are not tested here; they need a display and a kernel.
"""

from __future__ import annotations

import copy
import os
import time
from pathlib import Path

import numpy as np
import pytest
import yaml
from pipeline_gui import config as config_mod
from pipeline_gui.plan import (
    RunRequest,
    build_steps,
    default_track_duration_ms,
    parse_reference_geo,
)
from pipeline_gui.report import ReportInfo, build_report, split_figures, summary_lines
from pipeline_gui.runner import (
    ERROR,
    PASSED,
    SKIPPED,
    RunContext,
    StepResult,
    build_command,
    child_environment,
    new_or_changed_figures,
    notebook_errors,
    notebook_excerpts,
    run_pipeline,
    snapshot_figures,
)


@pytest.fixture
def raw_config() -> dict:
    return yaml.safe_load(config_mod.DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))


# ----- Config ----------------------------------------------------------------


def test_the_shipped_config_loads():
    cfg = config_mod.load_config()
    assert cfg.signals["L5"] == ["GpsL5", "BdsB2a"]
    assert cfg.report.figure_grid == (1, 2)


@pytest.mark.parametrize(
    "path, value, message",
    [
        (("cell_timeout_s",), 0, "cell_timeout_s"),
        (("cell_timeout_s",), "long", "cell_timeout_s"),
        (("signals_off_by_default",), ["GpsL9"], "not in 'signals'"),
        (("report", "figure_grid"), [1], "report.figure_grid"),
        (("report", "per_satellite_pattern"), "([", "regular expression"),
        (("report", "excerpts"), {"03": []}, "03"),
        (("track_duration_s",), -5, "track_duration_s"),
    ],
)
def test_a_bad_value_names_its_key(raw_config, path, value, message):
    data = copy.deepcopy(raw_config)
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(config_mod.ConfigError, match=message.replace("[", r"\[").replace("(", r"\(")):
        config_mod.parse_config(data)


def test_a_missing_key_is_named(raw_config):
    del raw_config["report"]["dpi"]
    with pytest.raises(config_mod.ConfigError, match="report.dpi"):
        config_mod.parse_config(raw_config)


def test_a_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(config_mod.ConfigError, match="not found"):
        config_mod.load_config(tmp_path / "nothing.yml")


# ----- Plan ------------------------------------------------------------------


def _request(**kw) -> RunRequest:
    base = dict(experiment_index=3, channel_index=1, signals=("GpsL5", "BdsB2a"),
                start_offset_ms=0.0, track_duration_ms=60_000)
    base.update(kw)
    return RunRequest(**base)


def test_steps_run_00_once_then_01_and_02_for_each_signal():
    steps = build_steps(_request())
    assert [s.label for s in steps] == ["00_raw", "01_GpsL5", "02_GpsL5", "01_BdsB2a", "02_BdsB2a"]
    assert steps[2].depends_on == "01_GpsL5"


def test_01_and_02_get_the_same_span_and_channel():
    steps = build_steps(_request(start_offset_ms=500.0, track_duration_ms=30_000))
    for s in steps[1:]:
        assert s.overrides["EXPERIMENT_INDEX"] == "3"
        assert s.overrides["CHANNEL_INDEX"] == "1"
        assert s.overrides["START_OFFSET_MS"] == "500.0"
        # An int, because notebook 01 sizes arrays with it.
        assert s.overrides["TRACK_DURATION_MS"] == "30000"
    assert steps[0].overrides == {"EXPERIMENT_INDEX": "3", "CHANNEL_INDEX": "1"}


def test_notebook_02_is_not_planned_for_an_unsupported_signal():
    steps = build_steps(_request(signals=("GpsL1CA", "GpsL1C")), navigation_unsupported=["GpsL1C"])
    assert [s.label for s in steps] == ["00_raw", "01_GpsL1CA", "02_GpsL1CA", "01_GpsL1C"]


def test_02_alone_has_no_dependency():
    steps = build_steps(_request(notebooks=("02",)))
    assert all(s.depends_on is None for s in steps)


def test_the_reference_position_goes_to_notebook_02_only():
    steps = build_steps(_request(reference_geo=(40.0, -105.2, 1600.0)))
    assert steps[2].overrides["RECEIVER_REFERENCE_GEO"] == "(40.0, -105.2, 1600.0)"
    assert "RECEIVER_REFERENCE_GEO" not in steps[1].overrides


def test_overrides_patch_the_real_notebooks():
    """Every key the plan sets must exist in the notebook, or run_notebook.py ignores it."""
    from tests.test_run_notebook import _notebook, runner

    for step in build_steps(_request(reference_geo=(1.0, 2.0, 3.0))):
        applied = runner.patch_notebook(_notebook(step.notebook), dict(step.overrides))
        assert applied == set(step.overrides), step.label


@pytest.mark.parametrize("kw, message", [
    ({"signals": ()}, "signal"),
    ({"notebooks": ()}, "notebook"),
    ({"start_offset_ms": -1.0}, "offset"),
    ({"notebooks": ("03",)}, "unknown"),
])
def test_a_bad_request_is_refused(kw, message):
    with pytest.raises(ValueError, match=message):
        build_steps(_request(**kw))


def test_default_track_duration_is_whole_seconds_to_the_end():
    assert default_track_duration_ms(120.4, 0.0) == 120_000
    assert default_track_duration_ms(120.4, 10_500.0) == 109_000
    with pytest.raises(ValueError):
        default_track_duration_ms(5.0, 4_500.0)


def test_reference_position_text():
    assert parse_reference_geo("") is None
    assert parse_reference_geo("(40.0, -105.2, 1589)") == (40.0, -105.2, 1589.0)
    for bad in ("40, -105", "a, b, c", "95, 0, 0"):
        with pytest.raises(ValueError):
            parse_reference_geo(bad)


# ----- Runner helpers --------------------------------------------------------


def test_only_new_or_changed_figures_are_found(tmp_path):
    (tmp_path / "old.png").write_bytes(b"1")
    (tmp_path / "keep.png").write_bytes(b"1")
    (tmp_path / "notes.txt").write_text("x")
    before = snapshot_figures(tmp_path)
    (tmp_path / "new.png").write_bytes(b"2")
    later = time.time() + 5
    (tmp_path / "old.png").write_bytes(b"22")
    os.utime(tmp_path / "old.png", (later, later))
    assert [p.name for p in new_or_changed_figures(tmp_path, before)] == ["new.png", "old.png"]


def test_a_missing_figures_folder_is_empty(tmp_path):
    assert new_or_changed_figures(tmp_path / "none", {}) == []


def _md(text):
    return {"cell_type": "markdown", "source": [text]}


def _code(*outputs):
    return {"cell_type": "code", "source": [], "outputs": list(outputs)}


def _stdout(text):
    return {"output_type": "stream", "name": "stdout", "text": [text]}


EXECUTED = {
    "cells": [
        _md("## 7. Results"),
        _code(_stdout("before\n")),
        _md("## 8. Position solution"),
        _code(_stdout("fix 1\n"), {"output_type": "stream", "name": "stderr", "text": ["100%|###|\n"]}),
        _md("### 8.1 Detail"),
        _code(_stdout("fix 2\n")),
        _md("## 9. Clock"),
        _code(_stdout("clock\n"),
              {"output_type": "error", "ename": "KeyError", "evalue": "'G05'",
               "traceback": ["\x1b[31mTraceback\x1b[0m", "line a", "KeyError: 'G05'"]}),
    ]
}


def test_excerpt_runs_to_the_next_heading_of_the_same_level():
    out = notebook_excerpts(EXECUTED, ["## 8. Position solution", "## 99. Missing"], max_lines=10)
    assert out == {"## 8. Position solution": "fix 1\nfix 2"}


def test_excerpt_is_cut_at_max_lines():
    nb = {"cells": [_md("## A"), _code(_stdout("".join(f"{i}\n" for i in range(10))))]}
    text = notebook_excerpts(nb, ["## A"], max_lines=3)["## A"]
    assert text.splitlines()[:3] == ["0", "1", "2"] and "7 more lines" in text


def test_errors_name_the_cell_and_drop_colour_codes():
    errors = notebook_errors(EXECUTED)
    assert len(errors) == 1
    assert errors[0].startswith("cell 7: KeyError: 'G05'")
    assert "\x1b" not in errors[0]


def test_the_command_and_environment(tmp_path):
    step = build_steps(_request())[1]
    ctx = RunContext(collects_dir=tmp_path, figures_dir=tmp_path, report_dir=tmp_path,
                     timeout_s=99, excerpt_headings={}, max_excerpt_lines=5, kernel="k")
    cmd = build_command(step, ctx, tmp_path / "x.ipynb")
    assert cmd[2:3] == ["01"] and "SIGNAL_ID=GpsL5" in cmd
    assert cmd[cmd.index("--timeout") + 1] == "99" and cmd[cmd.index("--kernel") + 1] == "k"
    assert "--python" not in cmd
    assert child_environment(tmp_path)["COLLECTS_PATH"] == str(tmp_path)


def test_02_is_skipped_when_01_fails(tmp_path, monkeypatch):
    import pipeline_gui.runner as runner_mod

    def fake_run_step(step, ctx, log, should_stop):
        return StepResult(step, ERROR if step.label == "01_GpsL5" else PASSED)

    monkeypatch.setattr(runner_mod, "run_step", fake_run_step)
    ctx = RunContext(tmp_path, tmp_path, tmp_path, 1, {}, 1)
    results = run_pipeline(build_steps(_request()), ctx, log=lambda s: None)
    assert [r.status for r in results] == [PASSED, ERROR, SKIPPED, PASSED, PASSED]


# ----- Report ----------------------------------------------------------------


def _png(path: Path) -> Path:
    import matplotlib.pyplot as plt

    plt.imsave(path, np.random.default_rng(0).random((20, 30)))
    return path


def test_per_satellite_figures_are_split_and_grouped(tmp_path):
    names = ["01_acquisition_dwell_layout", "03_prompt_components_G05",
             "04_code_delay_doppler_G02", "03_prompt_components_C19"]
    general, per_sat = split_figures([tmp_path / f"{n}.png" for n in names], "_[A-Z][0-9]{2}$")
    assert [p.stem for p in general] == ["01_acquisition_dwell_layout"]
    assert [p.stem for p in per_sat] == [
        "03_prompt_components_C19", "03_prompt_components_G05", "04_code_delay_doppler_G02"]


def test_the_report_is_a_pdf_with_every_part(tmp_path):
    cfg = config_mod.load_config()
    step00, step01, step02 = build_steps(_request(signals=("GpsL5",)))
    figs = [_png(tmp_path / n) for n in ("a.png", "b.png", "03_prompt_components_G05.png")]
    results = [
        StepResult(step00, PASSED, 60.0, figures=figs[:2]),
        StepResult(step01, ERROR, 30.0, figures=figs[2:], errors=["cell 9: ValueError: bad"],
                   message="1 cell(s) raised an error."),
        StepResult(step02, SKIPPED, message="Skipped: 01_GpsL5 did not pass.",
                   excerpts={"## 8. Position solution": "lat 40.0\nlon -105.2"}),
    ]
    info = ReportInfo("collect / L5", [("experiment", "test"), ("file", "x.dat")])
    lines = summary_lines(info, results)
    assert any("01_GpsL5" in line and "cell error" in line for line in lines)
    assert any("ValueError: bad" in line for line in lines)

    pdf = build_report(tmp_path / "report.pdf", info, results, cfg.report)
    data = pdf.read_bytes()
    assert data.startswith(b"%PDF")
    # summary + 00 figures (2 on one page) + 01 per-satellite + 02 excerpt = 4 pages
    assert data.count(b"/Type /Page") - data.count(b"/Type /Pages") == 4
