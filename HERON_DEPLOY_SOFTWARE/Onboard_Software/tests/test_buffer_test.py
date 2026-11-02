"""Tests for the NUC buffer-size test and the wizard write options (D-078)."""

import csv
import json
from pathlib import Path

import pytest
from heron_common.config import ConfigError
from heron_onboard.buffer_test import (
    Trial,
    merge_device_args,
    plan_trials,
    run_buffer_test,
    summarize,
    trial_command,
)
from heron_onboard.capture.recorder_process import build_recorder_command
from heron_onboard.config import validate_onboard_dict
from heron_onboard.testing import minimal_config_dict


def _stopped(**kw) -> str:
    event = {
        "event": "stopped",
        "samples": 220_000_000,
        "overflows": 0,
        "host_drops": 0,
        "timeouts": 0,
        "bytes": 880_000_000,
        "elapsed_s": 10.0,
        "exit_code": 0,
    }
    event.update(kw)
    return json.dumps(event)


def test_capture_defaults_follow_the_wizard(config):
    cap = config.capture
    assert cap.wire_format == "sc8" and cap.wire_peak == 1.0
    assert cap.write_mode == "direct" and cap.spb == 10000
    assert cap.int_n is True and cap.file_buffer_bytes == 0


def test_recorder_command_passes_the_wizard_options(config):
    argv = build_recorder_command(
        Path("rec"), config.sdr[0], config.capture, Path("out"), "p", 0.0, 0.0
    )
    text = " ".join(argv)
    assert "--wire-format sc8 --wire-peak 1.0" in text
    assert "--write-mode direct --spb 10000 --file-buffer-bytes 0 --int-n 1 --fsync 1" in text
    assert "--duration" not in text


def test_recorder_command_duration_and_args_override(config):
    argv = build_recorder_command(
        Path("rec"),
        config.sdr[0],
        config.capture,
        Path("out"),
        "p",
        0.0,
        0.0,
        duration_s=12.5,
        uhd_args="serial=S1,num_recv_frames=64",
    )
    assert argv[argv.index("--args") + 1] == "serial=S1,num_recv_frames=64"
    assert argv[argv.index("--duration") + 1] == "12.5"


def test_merge_device_args_replaces_and_appends():
    base = "serial=S1,recv_buff_size=2e7,num_recv_frames=256"
    out = merge_device_args(base, {"num_recv_frames": 32, "recv_frame_size": 8000})
    assert out == "serial=S1,recv_buff_size=2e7,num_recv_frames=32,recv_frame_size=8000"
    assert merge_device_args(base, {"num_recv_frames": None}) == base


def test_plan_trials_is_the_full_product(config):
    bt = config.buffer_test.model_copy(
        update={
            "write_modes": ["direct", "threaded"],
            "spb": [1000, 2000, 4000],
            "num_recv_frames": [32, 256],
            "recv_frame_size": [],
            "file_buffer_bytes": [0, 4096],
        }
    )
    trials = plan_trials(bt)
    assert len(trials) == 2 * 3 * 2 * 1 * 2
    assert [t.index for t in trials] == list(range(1, len(trials) + 1))
    assert all(t.recv_frame_size is None for t in trials)
    assert trials[0] == Trial(1, "direct", 1000, 32, None, 0)


def test_summarize_reads_the_stopped_line():
    trial = Trial(1, "direct", 10000, None, None, 0)
    lines = ["[INFO] [UHD] text", '{"event":"ready"}', _stopped(overflows=2)]
    r = summarize(trial, 0, lines, 22e6)
    assert r.samples == 220_000_000 and r.expected_samples == 220_000_000
    assert r.overflows == 2 and not r.clean
    assert r.write_mbps == pytest.approx(88.0)
    assert r.missing_fraction == 0.0


def test_summarize_without_stopped_line_is_not_clean():
    trial = Trial(1, "direct", 10000, None, None, 0)
    lines = [json.dumps({"event": "status", "samples": 5, "bytes": 10})]
    r = summarize(trial, -1, lines, 22e6)
    assert r.samples == 5 and not r.clean
    assert "no stopped summary" in r.errors[0]


def test_run_buffer_test_writes_csv_and_removes_files(tmp_path):
    raw = minimal_config_dict(tmp_path)
    raw["buffer_test"] = {
        "sdr_id": "b200_1",
        "duration_s": 10,
        "settle_s": 0,
        "outdir": str(tmp_path / "bt"),
        "results_csv": str(tmp_path / "bt" / "results.csv"),
        "write_modes": ["direct", "threaded"],
        "spb": [10000],
        "num_recv_frames": [32],
        "file_buffer_bytes": [0],
    }
    cfg = validate_onboard_dict(raw)
    seen: list[list[str]] = []

    def fake_runner(argv: list[str], timeout_s: float) -> tuple[int, list[str]]:
        seen.append(argv)
        outdir = Path(argv[argv.index("--outdir") + 1])
        (outdir / "b200_1").mkdir(parents=True)
        threaded = argv[argv.index("--write-mode") + 1] == "threaded"
        # The fake says the threaded mode drops samples, the direct mode does not.
        return 0, [_stopped(samples=100_000_000, host_drops=3 if threaded else 0)]

    results = run_buffer_test(cfg, runner=fake_runner, sleep=lambda s: None, report=lambda m: None)
    assert [r.clean for r in results] == [True, False]
    assert "--args" in seen[0]
    assert seen[0][seen[0].index("--args") + 1] == "serial=S2,num_recv_frames=32"
    assert seen[0][seen[0].index("--duration") + 1] == "10.0"
    rows = list(csv.DictReader((tmp_path / "bt" / "results.csv").open(encoding="utf-8")))
    assert [row["write_mode"] for row in rows] == ["direct", "threaded"]
    assert rows[1]["host_drops"] == "3" and rows[1]["clean"] == "False"
    assert not (tmp_path / "bt" / "trial_001").exists()


def test_trial_command_uses_trial_settings(config):
    trial = Trial(7, "threaded", 50000, 1024, 16000, 4194304)
    argv = trial_command(config, config.sdr[0], trial, Path("t7"))
    text = " ".join(argv)
    assert "--write-mode threaded --spb 50000 --file-buffer-bytes 4194304" in text
    assert "num_recv_frames=1024,recv_frame_size=16000" in text
    assert "--file-prefix buffertest_007" in text


def test_buffer_test_sdr_id_must_exist(tmp_path):
    raw = minimal_config_dict(tmp_path)
    raw["buffer_test"] = {"sdr_id": "nope"}
    with pytest.raises(ConfigError, match="buffer_test.sdr_id"):
        validate_onboard_dict(raw)


def test_example_config_is_valid(config_dir):
    from heron_onboard.config import load_onboard_config

    cfg = load_onboard_config(config_dir / "onboard.example.toml")
    assert cfg.capture.write_mode == "direct" and cfg.capture.wire_format == "sc8"
    assert all(ch.bandwidth_hz == 15e6 for s in cfg.sdr for ch in s.channels)
    assert len(plan_trials(cfg.buffer_test)) == 72
