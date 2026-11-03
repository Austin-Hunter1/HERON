"""
Tests of the notebook pipeline window logic (D-084): the config loader, the step
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
    AssistRequest,
    RunRequest,
    assist_request_from_text,
    build_steps,
    default_track_duration_ms,
    parse_p_fa_total,
    parse_reference_geo,
    parse_threshold_db,
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
        (("acquisition_only_stop_heading",), "6. Tracking", "markdown heading"),
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


def test_thresholds_go_to_notebook_01_of_their_signal_only():
    steps = build_steps(_request(acq_p_fa_total={"GpsL5": 1e-3}, acq_threshold_db={"BdsB2a": 8.5}))
    by_label = {s.label: s.overrides for s in steps}
    assert by_label["01_GpsL5"]["ACQ_P_FA_TOTAL"] == "0.001"
    assert "ACQ_THRESHOLD_DB" not in by_label["01_GpsL5"]
    assert by_label["01_BdsB2a"]["ACQ_THRESHOLD_DB"] == "8.5"
    assert "ACQ_P_FA_TOTAL" not in by_label["01_BdsB2a"]
    for label in ("00_raw", "02_GpsL5", "02_BdsB2a"):
        assert not {"ACQ_P_FA_TOTAL", "ACQ_THRESHOLD_DB"} & set(by_label[label])


def test_threshold_field_text():
    assert parse_p_fa_total("") is None and parse_threshold_db("  ") is None
    assert parse_p_fa_total("1e-3") == 1e-3
    assert parse_threshold_db("8.5") == 8.5
    for bad in ("0", "1", "-1e-3", "x"):
        with pytest.raises(ValueError):
            parse_p_fa_total(bad)
    for bad in ("0", "-2", "nan", "x"):
        with pytest.raises(ValueError):
            parse_threshold_db(bad)


def test_overrides_patch_the_real_notebooks():
    """Every key the plan sets must exist in the notebook, or run_notebook.py ignores it."""
    from tests.test_run_notebook import _notebook, runner

    request = _request(reference_geo=(1.0, 2.0, 3.0),
                       acq_p_fa_total={"GpsL5": 1e-3}, acq_threshold_db={"GpsL5": 9.0},
                       assist=AssistRequest(blocked_sectors=((300.0, 60.0, 60.0),)))
    for step in build_steps(request):
        applied = runner.patch_notebook(_notebook(step.notebook), dict(step.overrides))
        assert applied == set(step.overrides), step.label


def test_assisted_values_go_to_notebook_01_only_and_are_valid_python():
    import ast

    assist = AssistRequest(time_utc="2026-10-14 01:17:45", min_elevation_deg=10.0,
                           blocked_sectors=((300.0, 60.0, 60.0),), dwell_multiplier=20)
    steps = build_steps(_request(reference_geo=(40.0, -105.2, 1589.1), assist=assist))
    by_label = {s.label: s.overrides for s in steps}
    o = by_label["01_GpsL5"]
    assert o["ASSISTED_ACQUISITION"] == "True"
    assert ast.literal_eval(o["ASSIST_POSITION_GEO"]) == (40.0, -105.2, 1589.1)
    assert ast.literal_eval(o["ASSIST_TIME_UTC"]) == "2026-10-14 01:17:45"
    assert ast.literal_eval(o["ASSIST_BLOCKED_SECTORS"]) == [(300.0, 60.0, 60.0)]
    assert o["ASSIST_DWELL_MULTIPLIER"] == "20"
    assert not any(k.startswith("ASSIST") for k in by_label["02_GpsL5"])
    assert not any(k.startswith("ASSIST") for k in by_label["00_raw"])
    empty = build_steps(_request(reference_geo=(1.0, 2.0, 3.0), assist=AssistRequest()))[1].overrides
    assert empty["ASSIST_TIME_UTC"] == "None" and empty["ASSIST_BLOCKED_SECTORS"] == "[]"


def test_assisted_acquisition_needs_a_position_and_notebook_01():
    with pytest.raises(ValueError, match="reference position"):
        build_steps(_request(assist=AssistRequest()))
    with pytest.raises(ValueError, match="notebook 01"):
        build_steps(_request(reference_geo=(1.0, 2.0, 3.0), assist=AssistRequest(), notebooks=("00",)))


def test_assisted_field_text():
    fields = dict(time_utc="", min_elevation="0", sectors="", dwell_multiplier="10",
                  stage1_satellites="4", doppler_halfwidth="250")
    a = assist_request_from_text(**fields)
    assert a.time_utc is None and a.blocked_sectors == () and a.dwell_multiplier == 10
    a = assist_request_from_text(**{**fields, "time_utc": "2026-10-14T01:17:45", "sectors": "300-60:60"})
    assert a.time_utc == "2026-10-14 01:17:45" and a.blocked_sectors == ((300.0, 60.0, 60.0),)
    for key, bad in (("time_utc", "noon"), ("min_elevation", "95"), ("sectors", "300-60"),
                     ("dwell_multiplier", "2.5"), ("stage1_satellites", "0"), ("doppler_halfwidth", "-1")):
        with pytest.raises(ValueError):
            assist_request_from_text(**{**fields, key: bad})


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


def test_acquisition_only_stops_01_and_plans_no_02():
    steps = build_steps(_request(acquisition_only=True), acquisition_stop_heading="## 6. Tracking")
    assert [s.label for s in steps] == ["00_raw", "01_GpsL5", "01_BdsB2a"]
    assert steps[0].stop_before is None
    assert all(s.stop_before == "## 6. Tracking" for s in steps[1:])
    assert steps[1].title == "Notebook 01: Acquisition only (GpsL5)"


def test_acquisition_only_needs_notebook_01():
    with pytest.raises(ValueError, match="notebook 01"):
        build_steps(_request(acquisition_only=True, notebooks=("00", "02")))


def test_the_stop_heading_reaches_the_command(tmp_path):
    step = build_steps(_request(acquisition_only=True), acquisition_stop_heading="## 6. Tracking")[1]
    ctx = RunContext(tmp_path, tmp_path, tmp_path, 9, {}, 5)
    cmd = build_command(step, ctx, tmp_path / "x.ipynb")
    assert cmd[cmd.index("--stop-before") + 1] == "## 6. Tracking"
    full = build_steps(_request())[1]
    assert "--stop-before" not in build_command(full, ctx, tmp_path / "x.ipynb")


def test_the_shipped_stop_heading_keeps_acquisition_and_cuts_tracking():
    """The real notebook 01 must have the heading, with the acquisition plots before it."""
    from tests.test_run_notebook import _notebook, runner

    cfg = config_mod.load_config()
    nb = _notebook("01")
    removed = runner.truncate_before(nb, cfg.acquisition_only_stop_heading)
    code = "\n".join("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code")
    assert removed > 0
    assert "run_acquisition(" in code and "02c_acquisition_code_phase_slices_all_prns" in code
    assert "save_tracking_results" not in code and "TrackingLoopParameters" not in code


def test_a_missing_stop_heading_is_an_error():
    from tests.test_run_notebook import _notebook, runner

    with pytest.raises(ValueError, match="not found"):
        runner.truncate_before(_notebook("01"), "## 99. Nothing")


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
