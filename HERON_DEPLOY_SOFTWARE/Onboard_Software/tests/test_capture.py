"""Tests for the recorder command line, status parsing, metadata, and manager."""

import json
from pathlib import Path

from heron_common.protocol import SdrStatus
from heron_onboard.capture import CaptureManager, FakeCaptureBackend
from heron_onboard.capture.metadata import METADATA_FILENAME, build_metadata, finalize_metadata
from heron_onboard.capture.recorder_process import (
    apply_status,
    build_recorder_command,
    parse_status_line,
)


def test_build_recorder_command_has_every_setting(config):
    sdr = config.sdr[0]
    argv = build_recorder_command(
        Path("/usr/local/bin/heron_recorder"),
        sdr,
        config.capture,
        Path("/data/f1"),
        "20260917T120000Z",
        sync_epoch=1000.0,
        start_time=1003.0,
    )
    text = " ".join(argv)
    assert Path(argv[0]) == Path("/usr/local/bin/heron_recorder")
    assert "--args serial=S1" in text
    assert "--rate 22000000.0" in text
    assert "--clock-source internal --time-source none" in text
    assert "--sync-epoch 1000.0" in text and "--start-time 1003.0" in text
    assert text.count("--channel") == 2
    assert "index=0,id=L5_direct,freq=1176450000.0,gain=45.0,bw=20000000.0,antenna=RX2" in text
    assert Path(argv[argv.index("--outdir") + 1]) == Path("/data/f1")
    assert "--file-prefix 20260917T120000Z" in text
    assert "--sdr-id b210_1" in text


def test_parse_status_line_accepts_json_only():
    assert parse_status_line('{"event":"status","samples":5}\n') == {
        "event": "status",
        "samples": 5,
    }
    assert parse_status_line("OOOOO") is None
    assert parse_status_line("[UHD] info") is None
    assert parse_status_line("{bad json") is None
    assert parse_status_line("[1,2]") is None


def test_apply_status_merges_events():
    s = SdrStatus(id="x")
    s = apply_status(s, {"event": "ready", "ref_locked": True})
    assert s.present and s.ref_locked is True and not s.streaming
    s = apply_status(s, {"event": "started"})
    assert s.streaming
    s = apply_status(
        s,
        {
            "event": "status",
            "streaming": True,
            "overflows": 3,
            "samples": 10,
            "rate_mbps": 44.0,
            "segment": 2,
        },
    )
    assert s.overflows == 3 and s.samples == 10 and s.segment == 2
    s = apply_status(s, {"event": "error", "message": "device lost"})
    assert s.fault == "device lost"
    s = apply_status(s, {"event": "stopped"})
    assert not s.streaming


def test_metadata_build_and_finalize(config):
    meta = build_metadata(
        config,
        "f1",
        "20260917T120000Z",
        1000.0,
        "abc123",
        {"sync_epoch": 1006.0, "start_time": 1009.0},
        "test",
    )
    assert meta["flight_id"] == "f1" and meta["software_version"] == "abc123"
    assert meta["timing"]["sync_epoch_unix"] == 1006.0
    assert [s["serial"] for s in meta["sdrs"]] == ["S1", "S2"]
    assert meta["config"]["capture"]["segment_seconds"] == 30.0
    meta = finalize_metadata(
        meta, 1100.0, [SdrStatus(id="b210_1", samples=99, overflows=1, segment=3)]
    )
    assert meta["stop_unix"] == 1100.0 and meta["totals"]["b210_1"]["samples"] == 99
    json.dumps(meta)  # Must be serialisable.


def test_capture_manager_writes_metadata_and_runs_backend(config, tmp_path: Path):
    backend = FakeCaptureBackend(config.sdr, clock=lambda: 50.0)
    mgr = CaptureManager(config, backend, "v1")
    mgr.start("flight_x", 10.0, "test")
    flight_dir = config.disk.data_root / "flight_x"
    assert (flight_dir / METADATA_FILENAME).exists()
    assert backend.start_calls[0][0] == flight_dir
    statuses = mgr.poll()
    assert all(s.streaming for s in statuses)
    assert mgr.data_rate_mbps(statuses) == (22e6 * 2 * 2 + 10e6 * 2) / 1e6
    mgr.stop(60.0, "test end")
    meta = json.loads((flight_dir / METADATA_FILENAME).read_text())
    assert meta["stop_unix"] == 60.0 and meta["stop_reason"] == "test end"
    assert not mgr.active and backend.stop_calls == 1
