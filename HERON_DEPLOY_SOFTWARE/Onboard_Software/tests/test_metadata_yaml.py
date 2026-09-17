"""Tests for the SDR team's metadata.yml and the rebuild command."""

import json
from pathlib import Path

import yaml
from heron_onboard.capture.metadata import build_metadata, write_metadata
from heron_onboard.capture.metadata_yaml import (
    METADATA_YAML_FILENAME,
    build_metadata_yaml,
    scan_sidecars,
    write_metadata_yaml,
)
from heron_onboard.cli import main

TEAM_KEYS = {"band_configurations", "channel_configurations", "collections"}


def _sidecar(flight_dir: Path, sdr_id: str, ch_id: str, seq: int, prefix: str, **over) -> Path:
    """Write a sidecar like the C++ recorder does."""
    d = flight_dir / sdr_id / ch_id
    d.mkdir(parents=True, exist_ok=True)
    name = f"{prefix}_{seq:05d}.sc8"
    (d / name).write_bytes(b"\x00" * 16)
    data = {
        "sdr_id": sdr_id,
        "channel_id": ch_id,
        "file": name,
        "seq": seq,
        "first_sample_device_time": 1000.0 + 30 * seq,
        "first_sample_host_time": 1000.5 + 30 * seq,
        "num_samples": 660_000_000,
        "sample_rate_hz": 22e6,
        "center_freq_hz": 1176.45e6,
        "gain_db": 45.0,
        "bandwidth_hz": 20e6,
        "antenna": "RX2",
        "cpu_format": "sc8",
        "bytes_per_sample": 2,
        "overflows_in_segment": 0,
        "host_drops_in_segment": 0,
        "complete": True,
    }
    data.update(over)
    path = d / (name + ".json")
    path.write_text(json.dumps(data))
    return path


def test_yaml_matches_team_schema(config, tmp_path: Path):
    flight = tmp_path / "f1"
    _sidecar(flight, "b210_1", "L5_direct", 0, "P")
    _sidecar(flight, "b210_1", "L5_direct", 1, "P", overflows_in_segment=3, complete=False)
    _sidecar(flight, "b200mini_1", "L1_refl", 0, "P", center_freq_hz=1575.42e6, sample_rate_hz=10e6)
    path = write_metadata_yaml(flight, config, "P")
    assert path.name == METADATA_YAML_FILENAME
    doc = yaml.safe_load(path.read_text())
    assert set(doc) == TEAM_KEYS
    assert doc["band_configurations"]["L5"] == {"center_freq": 1176.45e6, "inter_freq": 0.0}
    assert doc["band_configurations"]["L1"]["center_freq"] == 1575.42e6
    ch = doc["channel_configurations"]["b210_1_L5_direct"]
    assert ch["samp_rate"] == 22e6 and ch["bands"] == ["L5"]
    assert ch["sample_format"] == {
        "bit_depth": 8,
        "is_complex": True,
        "is_integer": True,
        "is_signed": True,
        "is_i_lsb": True,
    }
    assert len(doc["collections"]) == 3
    c1 = doc["collections"]["P_b210_1_L5_direct_00001"]
    assert c1["channel_config"] == "b210_1_L5_direct"
    assert c1["filename"] == "b210_1/L5_direct/P_00001.sc8"
    assert "3 overflow indication(s)" in c1["notes"] and "partial" in c1["notes"]
    c0 = doc["collections"]["P_b210_1_L5_direct_00000"]
    assert "overflow" not in c0["notes"] and "30.000 s" in c0["notes"]
    assert "not PPS-aligned" in c0["notes"]  # Test config uses time_source = none.


def test_empty_flight_gives_valid_yaml_without_collections(config, tmp_path: Path):
    flight = tmp_path / "f2"
    flight.mkdir()
    doc = yaml.safe_load(write_metadata_yaml(flight, config, "P").read_text())
    assert doc["collections"] == {} and "b200mini_1_L1_refl" in doc["channel_configurations"]


def test_scan_skips_bad_and_foreign_json(config, tmp_path: Path):
    flight = tmp_path / "f3"
    _sidecar(flight, "b210_1", "L5_direct", 0, "P")
    (flight / "b210_1" / "L5_direct" / "junk.json").write_text("{not json")
    (flight / "b210_1" / "L5_direct" / "other.json").write_text('{"a": 1}')
    assert len(scan_sidecars(flight)) == 1
    doc = build_metadata_yaml(config, "P", scan_sidecars(flight))
    assert len(doc["collections"]) == 1


def test_rebuild_after_power_loss(config, tmp_path: Path):
    """metadata.json without a stop time gets totals from the sidecars."""
    flight = tmp_path / "f4"
    flight.mkdir()
    meta = build_metadata(config, "f4", "P", 1000.0, "v", {"sync_epoch": 0, "start_time": 0}, "t")
    write_metadata(flight, meta)
    _sidecar(flight, "b210_1", "L5_direct", 0, "P", overflows_in_segment=1)
    _sidecar(flight, "b210_1", "L5_direct", 1, "P")
    _sidecar(flight, "b210_1", "L5_refl", 0, "P")
    assert main(["rebuild-metadata", str(flight)]) == 0
    meta = json.loads((flight / "metadata.json").read_text())
    assert meta["rebuilt"] is True and meta["stop_unix"] == 1030.5
    assert meta["totals"]["b210_1"] == {
        "samples": 3 * 660_000_000,
        "overflows": 1,
        "host_drops": 0,
        "segments": 2,
        "fault": None,
    }
    doc = yaml.safe_load((flight / METADATA_YAML_FILENAME).read_text())
    assert len(doc["collections"]) == 3


def test_rebuild_without_metadata_json_fails_clearly(tmp_path: Path):
    assert main(["rebuild-metadata", str(tmp_path)]) == 2
